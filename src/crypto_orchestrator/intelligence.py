from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from .config import Settings
from .connectors import (
    BinanceDerivativesPositioningConnector,
    BinanceMarketDataConnector,
    ConnectorError,
    CryptoPanicNewsConnector,
    RssNewsConnector,
    XApiConnector,
)
from .models import (
    DataSourceError,
    DerivativesPositioningResponse,
    DerivativesPositioningSnapshot,
    ExternalSignal,
    MarketDataResponse,
    MarketSnapshot,
    MarketType,
    SignalResponse,
    utc_now,
)
from .ports import DerivativesPositioningPort, MarketDataPort, SocialSignalPort


class IntelligenceService:
    """Aggregate explicitly configured, read-only market and signal connectors."""

    def __init__(
        self,
        *,
        market_data: tuple[MarketDataPort, ...] = (),
        positioning: tuple[DerivativesPositioningPort, ...] = (),
        news: tuple[SocialSignalPort, ...] = (),
        x_posts: tuple[SocialSignalPort, ...] = (),
    ):
        self._market_data = market_data
        self._positioning = positioning
        self._news = news
        self._x_posts = x_posts

    def health(self) -> dict[str, dict[str, str]]:
        return {
            "market_data": self._connector_status(self._market_data, "binance"),
            "derivatives_positioning": self._connector_status(
                self._positioning, "binance_derivatives"
            ),
            "news": self._connector_status(self._news, "rss"),
            "x": self._connector_status(self._x_posts, "x_api"),
        }

    async def market_snapshot(
        self, symbol: str, market_type: MarketType, timeframe: str, limit: int
    ) -> MarketDataResponse:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        requested_at = utc_now()
        results = await asyncio.gather(
            *(
                connector.snapshot(symbol, market_type, timeframe, limit)
                for connector in self._market_data
            ),
            return_exceptions=True,
        )
        snapshots: list[MarketSnapshot] = []
        errors: list[DataSourceError] = []
        if not self._market_data:
            errors.append(
                DataSourceError(
                    source="market_data",
                    category="not_configured",
                    message="No market-data connector is configured.",
                )
            )
        for connector, result in zip(self._market_data, results, strict=True):
            if isinstance(result, Exception):
                errors.append(self._source_error(connector.name, result))
            else:
                snapshots.append(result)
        return MarketDataResponse(
            requested_at=requested_at,
            symbol=symbol,
            market_type=market_type,
            timeframe=timeframe,
            snapshots=snapshots,
            errors=errors,
        )

    async def derivatives_positioning(
        self, symbol: str, period: str, limit: int
    ) -> DerivativesPositioningResponse:
        if not 1 <= limit <= 500:
            raise ValueError("positioning limit must be between 1 and 500")
        requested_period = period.strip().lower()
        if not requested_period:
            raise ValueError("positioning period is required")
        requested_at = utc_now()
        results = await asyncio.gather(
            *(
                connector.positioning(symbol, requested_period, limit)
                for connector in self._positioning
            ),
            return_exceptions=True,
        )
        snapshots: list[DerivativesPositioningSnapshot] = []
        errors: list[DataSourceError] = []
        if not self._positioning:
            errors.append(
                DataSourceError(
                    source="derivatives_positioning",
                    category="not_configured",
                    message="No derivatives-positioning connector is configured.",
                )
            )
        for connector, result in zip(self._positioning, results, strict=True):
            if isinstance(result, Exception):
                errors.append(self._source_error(connector.name, result))
            else:
                snapshots.append(result)
        return DerivativesPositioningResponse(
            requested_at=requested_at,
            symbol=symbol,
            contract_type="PERPETUAL",
            period=requested_period,
            snapshots=snapshots,
            errors=errors,
        )

    async def news(
        self, symbol: str | None, since: datetime, limit: int
    ) -> SignalResponse:
        return await self._signals(self._news, "news", symbol, since, limit)

    async def x_posts(
        self, symbol: str | None, since: datetime, limit: int
    ) -> SignalResponse:
        return await self._signals(self._x_posts, "x", symbol, since, limit)

    async def _signals(
        self,
        connectors: tuple[SocialSignalPort, ...],
        empty_source: str,
        symbol: str | None,
        since: datetime,
        limit: int,
    ) -> SignalResponse:
        if since.tzinfo is None:
            raise ValueError("since must include a timezone")
        if since > utc_now():
            raise ValueError("since cannot be in the future")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        results = await asyncio.gather(
            *(connector.signals(symbol, since, limit) for connector in connectors),
            return_exceptions=True,
        )
        signals: dict[str, ExternalSignal] = {}
        errors: list[DataSourceError] = []
        if not connectors:
            errors.append(
                DataSourceError(
                    source=empty_source,
                    category="not_configured",
                    message="No signal connector is configured.",
                )
            )
        for connector, result in zip(connectors, results, strict=True):
            if isinstance(result, Exception):
                errors.append(self._source_error(connector.name, result))
                continue
            for signal in result:
                signals.setdefault(signal.signal_id, signal)
        ordered = sorted(
            signals.values(),
            key=lambda item: item.published_at or item.observed_at,
            reverse=True,
        )
        return SignalResponse(
            requested_symbol=symbol,
            since=since.astimezone(UTC),
            signals=ordered[:limit],
            errors=errors,
        )

    @staticmethod
    def _connector_status(
        connectors: tuple[object, ...], fallback_name: str
    ) -> dict[str, str]:
        if not connectors:
            return {fallback_name: "not_configured"}
        return {str(connector.name): "configured" for connector in connectors}

    @staticmethod
    def _source_error(source: str, error: Exception) -> DataSourceError:
        if isinstance(error, ConnectorError):
            return DataSourceError(
                source=source,
                category=error.category,
                message=str(error),
                retryable=error.retryable,
            )
        return DataSourceError(
            source=source,
            category="unexpected_error",
            message=str(error)[:500] or type(error).__name__,
        )


def _credential_csv(values: dict[str, str], name: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in values.get(name, "").split(",")
        if item.strip()
    )


def build_default_intelligence(
    settings: Settings,
    account_credentials: dict[str, dict[str, str]] | None = None,
) -> IntelligenceService:
    """Build read-only connectors using only the selected account's secrets."""

    market_data: tuple[MarketDataPort, ...] = ()
    if settings.binance_market_data_enabled:
        market_data = (
            BinanceMarketDataConnector(
                spot_base_url=settings.binance_spot_base_url,
                futures_base_url=settings.binance_futures_base_url,
                timeout_seconds=settings.http_timeout_seconds,
            ),
        )

    positioning: tuple[DerivativesPositioningPort, ...] = ()
    if settings.binance_market_data_enabled:
        positioning = (
            BinanceDerivativesPositioningConnector(
                futures_base_url=settings.binance_futures_base_url,
                timeout_seconds=settings.http_timeout_seconds,
            ),
        )

    news_connectors: list[SocialSignalPort] = [
        RssNewsConnector(feed_url, timeout_seconds=settings.http_timeout_seconds)
        for feed_url in settings.news_rss_feeds
    ]
    cryptopanic_token = settings.cryptopanic_auth_token.strip()
    if settings.cryptopanic_api_enabled and cryptopanic_token:
        news_connectors.append(
            CryptoPanicNewsConnector(
                cryptopanic_token,
                api_plan=settings.cryptopanic_api_plan,
                timeout_seconds=settings.http_timeout_seconds,
            )
        )
    news = tuple(news_connectors)
    x_posts: tuple[SocialSignalPort, ...] = ()
    if settings.x_api_enabled:
        if account_credentials is None:
            x_bearer_token = settings.x_bearer_token
            influencer_usernames = settings.x_influencer_usernames
        else:
            x_credentials = account_credentials.get("x", {})
            x_bearer_token = x_credentials.get("bearer_token", "").strip()
            influencer_usernames = _credential_csv(x_credentials, "influencer_usernames")
        if x_bearer_token:
            x_posts = (
                XApiConnector(
                    x_bearer_token,
                    influencer_usernames,
                    api_base_url=settings.x_api_base_url,
                    timeout_seconds=settings.http_timeout_seconds,
                ),
            )
    return IntelligenceService(
        market_data=market_data,
        positioning=positioning,
        news=news,
        x_posts=x_posts,
    )
