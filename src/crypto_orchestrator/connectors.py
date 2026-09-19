from __future__ import annotations

import asyncio
import hashlib
import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from .models import (
    DataSourceError,
    DerivativesPositioningPoint,
    DerivativesPositioningSnapshot,
    ExternalSignal,
    MarketCandle,
    MarketSnapshot,
    MarketType,
    SignalType,
    utc_now,
)
from .ports import DerivativesPositioningPort, MarketDataPort, SocialSignalPort


class ConnectorError(RuntimeError):
    """A normalized failure from an explicitly configured external connector."""

    def __init__(self, message: str, *, category: str = "connector_error", retryable: bool = False):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class _HttpConnector:
    def __init__(
        self, *, timeout_seconds: float = 10.0, http_client: httpx.AsyncClient | None = None
    ):
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    async def _get(
        self, url: str, *, params: dict[str, Any] | None = None, headers=None
    ) -> httpx.Response:
        try:
            if self._http_client is not None:
                response = await self._http_client.get(url, params=params, headers=headers)
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, follow_redirects=True
                ) as client:
                    response = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise ConnectorError(
                f"{self.name} request timed out",
                category="timeout",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ConnectorError(
                f"{self.name} request failed",
                category="network_error",
                retryable=True,
            ) from exc

        if response.status_code == 429:
            raise ConnectorError(
                f"{self.name} rate limit reached",
                category="rate_limited",
                retryable=True,
            )
        if response.status_code >= 500:
            raise ConnectorError(
                f"{self.name} returned HTTP {response.status_code}",
                category="upstream_error",
                retryable=True,
            )
        if response.status_code >= 400:
            raise ConnectorError(
                f"{self.name} returned HTTP {response.status_code}",
                category="upstream_rejected",
            )
        return response

    async def _get_json(
        self, url: str, *, params: dict[str, Any] | None = None, headers=None
    ) -> Any:
        response = await self._get(url, params=params, headers=headers)
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError(
                f"{self.name} returned invalid JSON",
                category="invalid_response",
            ) from exc

    async def _get_text(self, url: str, *, headers=None) -> str:
        response = await self._get(url, headers=headers)
        return response.text


_SYMBOL_RE = re.compile(r"^[A-Z0-9]+(?:/[A-Z0-9]+)?$")
_BINANCE_INTERVALS = {
    "1s",
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
    "1M",
}


def _normalize_symbol(symbol: str) -> tuple[str, str]:
    normalized = symbol.strip().upper()
    if not _SYMBOL_RE.fullmatch(normalized):
        raise ValueError("symbol must contain only letters/numbers with an optional slash")
    return normalized, normalized.replace("/", "")


def _base_asset(symbol: str) -> str:
    normalized, _ = _normalize_symbol(symbol)
    if "/" in normalized:
        return normalized.split("/", 1)[0]
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if normalized.endswith(quote) and len(normalized) > len(quote) + 1:
            return normalized[: -len(quote)]
    return normalized


def _decimal(value: Any, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ConnectorError(
            f"{field} was not numeric in the upstream response",
            category="invalid_response",
        ) from exc


def _required_decimal(value: Any, field: str) -> Decimal:
    parsed = _decimal(value, field)
    if parsed is None:
        raise ConnectorError(
            f"{field} was missing in the upstream response",
            category="invalid_response",
        )
    return parsed


def _timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if value > 100_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, UTC)
    if isinstance(value, str):
        parsed = value.strip().replace("Z", "+00:00")
        result = datetime.fromisoformat(parsed)
        return result.astimezone(UTC) if result.tzinfo else result.replace(tzinfo=UTC)
    raise ValueError("timestamp must be an ISO string or Unix timestamp")


class BinanceMarketDataConnector(_HttpConnector, MarketDataPort):
    name = "binance"

    def __init__(
        self,
        *,
        spot_base_url: str = "https://data-api.binance.vision",
        futures_base_url: str = "https://fapi.binance.com",
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ):
        super().__init__(timeout_seconds=timeout_seconds, http_client=http_client)
        self._spot_base_url = spot_base_url.rstrip("/")
        self._futures_base_url = futures_base_url.rstrip("/")

    async def snapshot(
        self, symbol: str, market_type: MarketType, timeframe: str, limit: int
    ) -> MarketSnapshot:
        normalized_symbol, api_symbol = _normalize_symbol(symbol)
        if timeframe not in _BINANCE_INTERVALS:
            raise ValueError(f"unsupported Binance interval: {timeframe}")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        base_url = (
            self._futures_base_url
            if market_type is MarketType.PERPETUAL
            else self._spot_base_url
        )
        prefix = "/fapi/v1" if market_type is MarketType.PERPETUAL else "/api/v3"
        start = time.perf_counter()
        ticker, raw_klines = await asyncio.gather(
            self._get_json(
                f"{base_url}{prefix}/ticker/24hr",
                params={"symbol": api_symbol},
            ),
            self._get_json(
                f"{base_url}{prefix}/klines",
                params={"symbol": api_symbol, "interval": timeframe, "limit": limit},
            ),
        )

        if isinstance(ticker, list):
            if not ticker:
                raise ConnectorError("Binance returned no ticker", category="empty_response")
            ticker = ticker[0]
        if not isinstance(ticker, dict) or not isinstance(raw_klines, list):
            raise ConnectorError(
                "Binance returned an unexpected response shape", category="invalid_response"
            )

        candles = [self._candle(item) for item in raw_klines]
        return MarketSnapshot(
            source=self.name,
            venue="binance",
            symbol=normalized_symbol,
            market_type=market_type,
            timeframe=timeframe,
            price=_required_decimal(ticker.get("lastPrice"), "lastPrice"),
            bid_price=_decimal(ticker.get("bidPrice"), "bidPrice"),
            ask_price=_decimal(ticker.get("askPrice"), "askPrice"),
            change_24h_percent=_decimal(
                ticker.get("priceChangePercent"), "priceChangePercent"
            ),
            volume_24h=_decimal(ticker.get("volume"), "volume"),
            observed_at=utc_now(),
            latency_ms=max(0, int((time.perf_counter() - start) * 1000)),
            candles=candles,
        )

    @staticmethod
    def _candle(item: Any) -> MarketCandle:
        if not isinstance(item, list) or len(item) < 8:
            raise ConnectorError("Binance returned an invalid candle", category="invalid_response")
        try:
            return MarketCandle(
                open_time=_timestamp(item[0]),
                open_price=_required_decimal(item[1], "open"),
                high_price=_required_decimal(item[2], "high"),
                low_price=_required_decimal(item[3], "low"),
                close_price=_required_decimal(item[4], "close"),
                volume=_required_decimal(item[5], "volume"),
                close_time=_timestamp(item[6]),
                quote_volume=_required_decimal(item[7], "quoteVolume"),
            )
        except (IndexError, TypeError, ValueError) as exc:
            raise ConnectorError(
                "Binance returned an invalid candle", category="invalid_response"
            ) from exc

    async def latest_price(
        self, symbol: str, venue: str, market_type: MarketType
    ) -> Decimal:
        snapshot = await self.snapshot(symbol, market_type, "1m", 1)
        return snapshot.price


_BINANCE_POSITIONING_PERIODS = {
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "12h",
    "1d",
}


class BinanceDerivativesPositioningConnector(
    _HttpConnector, DerivativesPositioningPort
):
    """Read-only aggregate positioning context from Binance USDⓈ-M futures."""

    name = "binance_derivatives"

    def __init__(
        self,
        *,
        futures_base_url: str = "https://fapi.binance.com",
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ):
        super().__init__(timeout_seconds=timeout_seconds, http_client=http_client)
        self._futures_base_url = futures_base_url.rstrip("/")

    async def positioning(
        self, symbol: str, period: str | None = None, limit: int | None = None
    ) -> DerivativesPositioningSnapshot:
        normalized_symbol, api_symbol = _normalize_symbol(symbol)
        requested_period = (period or "5m").strip().lower()
        if requested_period not in _BINANCE_POSITIONING_PERIODS:
            raise ValueError(
                "unsupported Binance positioning period: " f"{requested_period}"
            )
        requested_limit = 30 if limit is None else limit
        if not 1 <= requested_limit <= 500:
            raise ValueError("positioning limit must be between 1 and 500")

        history_params = {
            "symbol": api_symbol,
            "contractType": "PERPETUAL",
            "period": requested_period,
            "limit": requested_limit,
        }
        base_url = self._futures_base_url
        requests = (
            (
                "open_interest",
                f"{base_url}/fapi/v1/openInterest",
                {"symbol": api_symbol},
            ),
            (
                "premium_index",
                f"{base_url}/fapi/v1/premiumIndex",
                {"symbol": api_symbol},
            ),
            (
                "open_interest_history",
                f"{base_url}/futures/data/openInterestHist",
                history_params,
            ),
            (
                "global_account_ratio",
                f"{base_url}/futures/data/globalLongShortAccountRatio",
                history_params,
            ),
            (
                "top_account_ratio",
                f"{base_url}/futures/data/topLongShortAccountRatio",
                history_params,
            ),
            (
                "top_position_ratio",
                f"{base_url}/futures/data/topLongShortPositionRatio",
                history_params,
            ),
            (
                "taker_flow",
                f"{base_url}/futures/data/takerlongshortRatio",
                history_params,
            ),
        )
        results = await asyncio.gather(
            *(
                self._get_json(url, params=params)
                for _, url, params in requests
            ),
            return_exceptions=True,
        )

        source_errors: list[DataSourceError] = []
        successful_sources: set[str] = set()
        current_values: dict[str, Decimal] = {}
        current_times: list[datetime] = []
        historical_values: dict[datetime, dict[str, Decimal]] = {}

        open_interest_result = results[0]
        if isinstance(open_interest_result, Exception):
            self._record_positioning_error(
                source_errors, "open_interest", open_interest_result
            )
        else:
            try:
                record = self._latest_record(open_interest_result, "open_interest")
                current_values["open_interest_contracts"] = _required_decimal(
                    record.get("openInterest"), "openInterest"
                )
                current_times.append(self._record_timestamp(record))
                successful_sources.add("open_interest")
            except (ConnectorError, TypeError, ValueError, ArithmeticError) as exc:
                self._record_positioning_error(source_errors, "open_interest", exc)

        premium_result = results[1]
        if isinstance(premium_result, Exception):
            self._record_positioning_error(
                source_errors, "premium_index", premium_result
            )
        else:
            try:
                record = self._latest_record(premium_result, "premium_index")
                current_values["mark_price"] = _required_decimal(
                    record.get("markPrice"), "markPrice"
                )
                current_values["index_price"] = _required_decimal(
                    record.get("indexPrice"), "indexPrice"
                )
                funding_rate = _decimal(record.get("lastFundingRate"), "lastFundingRate")
                if funding_rate is not None:
                    current_values["funding_rate"] = funding_rate
                current_times.append(self._record_timestamp(record))
                successful_sources.add("premium_index")
            except (ConnectorError, TypeError, ValueError, ArithmeticError) as exc:
                self._record_positioning_error(source_errors, "premium_index", exc)

        history_parsers = {
            "open_interest_history": self._open_interest_values,
            "global_account_ratio": self._global_account_values,
            "top_account_ratio": self._top_account_values,
            "top_position_ratio": self._top_position_values,
            "taker_flow": self._taker_values,
        }
        for index, (label, _, _) in enumerate(requests[2:], start=2):
            result = results[index]
            if isinstance(result, Exception):
                self._record_positioning_error(source_errors, label, result)
                continue
            parser = history_parsers[label]
            try:
                records = self._records(result, label)
                if not records:
                    raise ConnectorError(
                        f"Binance returned no records for {label}",
                        category="empty_response",
                    )
                for record in records:
                    timestamp = self._record_timestamp(record)
                    values = parser(record)
                    historical_values.setdefault(timestamp, {}).update(values)
                successful_sources.add(label)
            except (ConnectorError, TypeError, ValueError, ArithmeticError) as exc:
                self._record_positioning_error(source_errors, label, exc)

        if not successful_sources:
            retryable = any(error.retryable for error in source_errors)
            raise ConnectorError(
                "Binance derivatives positioning returned no usable data",
                category="upstream_unavailable",
                retryable=retryable,
            )

        historical_points = [
            DerivativesPositioningPoint(observed_at=timestamp, **values)
            for timestamp, values in sorted(historical_values.items())
        ]
        if historical_values:
            latest_historical_values: dict[str, Decimal] = {}
            for timestamp in sorted(historical_values):
                latest_historical_values.update(historical_values[timestamp])
            current_values = {
                **latest_historical_values,
                **current_values,
            }
        observation_times = [*current_times, *historical_values]
        observed_at = max(observation_times) if observation_times else utc_now()
        current = DerivativesPositioningPoint(
            observed_at=observed_at,
            **current_values,
        )
        return DerivativesPositioningSnapshot(
            source=self.name,
            venue="binance_usdm",
            symbol=normalized_symbol,
            contract_type="PERPETUAL",
            period=requested_period,
            observed_at=observed_at,
            current=current,
            historical_points=historical_points,
            source_errors=source_errors,
        )

    @staticmethod
    def _records(payload: Any, source: str) -> list[dict[str, Any]]:
        records = [payload] if isinstance(payload, dict) else payload
        if not isinstance(records, list) or not all(
            isinstance(record, dict) for record in records
        ):
            raise ConnectorError(
                f"Binance returned an unexpected response shape for {source}",
                category="invalid_response",
            )
        return records

    @classmethod
    def _latest_record(cls, payload: Any, source: str) -> dict[str, Any]:
        records = cls._records(payload, source)
        if not records:
            raise ConnectorError(
                f"Binance returned no records for {source}",
                category="empty_response",
            )
        return max(records, key=cls._record_timestamp)

    @staticmethod
    def _record_timestamp(record: dict[str, Any]) -> datetime:
        value = record.get("timestamp", record.get("time"))
        if value is None:
            raise ConnectorError(
                "Binance positioning record has no timestamp",
                category="invalid_response",
            )
        try:
            return _timestamp(value)
        except (TypeError, ValueError, OSError) as exc:
            raise ConnectorError(
                "Binance positioning record has an invalid timestamp",
                category="invalid_response",
            ) from exc

    @staticmethod
    def _open_interest_values(record: dict[str, Any]) -> dict[str, Decimal]:
        values = {
            "open_interest_contracts": _required_decimal(
                record.get("sumOpenInterest"), "sumOpenInterest"
            ),
            "open_interest_value_quote": _required_decimal(
                record.get("sumOpenInterestValue"), "sumOpenInterestValue"
            ),
        }
        return values

    @staticmethod
    def _global_account_values(record: dict[str, Any]) -> dict[str, Decimal]:
        return {
            "global_long_short_account_ratio": _required_decimal(
                record.get("longShortRatio"), "longShortRatio"
            ),
            "global_long_account_ratio": _required_decimal(
                record.get("longAccount"), "longAccount"
            ),
            "global_short_account_ratio": _required_decimal(
                record.get("shortAccount"), "shortAccount"
            ),
        }

    @staticmethod
    def _top_account_values(record: dict[str, Any]) -> dict[str, Decimal]:
        return {
            "top_trader_long_short_account_ratio": _required_decimal(
                record.get("longShortRatio"), "longShortRatio"
            ),
            "top_trader_long_account_ratio": _required_decimal(
                record.get("longAccount"), "longAccount"
            ),
            "top_trader_short_account_ratio": _required_decimal(
                record.get("shortAccount"), "shortAccount"
            ),
        }

    @staticmethod
    def _top_position_values(record: dict[str, Any]) -> dict[str, Decimal]:
        return {
            "top_trader_long_short_position_ratio": _required_decimal(
                record.get("longShortRatio"), "longShortRatio"
            ),
            "top_trader_long_position_ratio": _required_decimal(
                record.get("longPosition", record.get("longAccount")),
                "longPosition",
            ),
            "top_trader_short_position_ratio": _required_decimal(
                record.get("shortPosition", record.get("shortAccount")),
                "shortPosition",
            ),
        }

    @staticmethod
    def _taker_values(record: dict[str, Any]) -> dict[str, Decimal]:
        buy_volume = _required_decimal(
            record.get("buyVol", record.get("takerBuyVol")), "buyVol"
        )
        sell_volume = _required_decimal(
            record.get("sellVol", record.get("takerSellVol")), "sellVol"
        )
        values: dict[str, Decimal] = {
            "taker_buy_volume": buy_volume,
            "taker_sell_volume": sell_volume,
        }
        buy_value = _decimal(
            record.get("buyVolValue", record.get("takerBuyVolValue")),
            "buyVolValue",
        )
        sell_value = _decimal(
            record.get("sellVolValue", record.get("takerSellVolValue")),
            "sellVolValue",
        )
        if buy_value is not None:
            values["taker_buy_volume_value_quote"] = buy_value
        if sell_value is not None:
            values["taker_sell_volume_value_quote"] = sell_value
        ratio = _decimal(record.get("buySellRatio"), "buySellRatio")
        if ratio is None and sell_volume:
            ratio = buy_volume / sell_volume
        if ratio is not None:
            values["taker_buy_sell_volume_ratio"] = ratio
        return values

    @staticmethod
    def _record_positioning_error(
        errors: list[DataSourceError], label: str, error: Exception
    ) -> None:
        if isinstance(error, ConnectorError):
            errors.append(
                DataSourceError(
                    source=f"binance_derivatives:{label}",
                    category=error.category,
                    message=str(error)[:500],
                    retryable=error.retryable,
                )
            )
            return
        errors.append(
            DataSourceError(
                source=f"binance_derivatives:{label}",
                category="unexpected_error",
                message=str(error)[:500] or type(error).__name__,
            )
        )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _element_text(element: ET.Element | None) -> str:
    return "" if element is None else " ".join("".join(element.itertext()).split())


def _first_child_text(element: ET.Element, names: set[str]) -> str:
    for child in list(element):
        if _local_name(child.tag) in names:
            value = _element_text(child)
            if value:
                return value
    return ""


def _first_descendant_text(element: ET.Element, names: set[str]) -> str:
    for child in element.iter():
        if child is not element and _local_name(child.tag) in names:
            value = _element_text(child)
            if value:
                return value
    return ""


def _clean_html(value: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(value)).split())


def _parse_feed_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(UTC)
    except (TypeError, ValueError, IndexError):
        try:
            return _timestamp(value)
        except (TypeError, ValueError, OSError):
            return None


def _matches_symbol(symbol: str | None, text: str) -> bool:
    if not symbol:
        return True
    asset = _base_asset(symbol)
    upper_text = text.upper()
    if f"${asset}" in upper_text:
        return True
    return bool(re.search(rf"(?<![A-Z0-9]){re.escape(asset)}(?![A-Z0-9])", upper_text))


class RssNewsConnector(_HttpConnector, SocialSignalPort):
    def __init__(
        self,
        feed_url: str,
        *,
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ):
        super().__init__(timeout_seconds=timeout_seconds, http_client=http_client)
        parsed = urlparse(feed_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("RSS feed URL must be an absolute HTTP(S) URL")
        self.feed_url = feed_url
        host = parsed.hostname or "rss"
        self.name = f"rss:{host}"

    async def signals(
        self, symbol: str | None, since: datetime, limit: int
    ) -> list[ExternalSignal]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        body = await self._get_text(
            self.feed_url,
            headers={"Accept": "application/rss+xml, application/atom+xml, application/xml"},
        )
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise ConnectorError(
                "RSS feed returned invalid XML", category="invalid_response"
            ) from exc

        observed_at = utc_now()
        signals: list[ExternalSignal] = []
        entries = [
            element
            for element in root.iter()
            if _local_name(element.tag) in {"item", "entry"}
        ]
        for entry in entries:
            title = _clean_html(
                _first_child_text(entry, {"title"})
                or _first_descendant_text(entry, {"title"})
            )
            description = _clean_html(
                _first_child_text(entry, {"description", "summary", "content"})
                or _first_descendant_text(entry, {"description", "summary", "content"})
            )
            link = self._link(entry)
            reference = link or _first_child_text(entry, {"guid", "id"}) or title
            published = _parse_feed_timestamp(
                _first_child_text(entry, {"pubdate", "published", "updated", "date"})
                or _first_descendant_text(entry, {"pubdate", "published", "updated", "date"})
            )
            if published and published < since:
                continue
            text = description or title
            if not title or not _matches_symbol(symbol, f"{title} {text}"):
                continue
            author = _clean_html(
                _first_child_text(entry, {"author", "creator", "name"})
                or _first_descendant_text(entry, {"author", "creator", "name"})
            ) or None
            identity = f"{self.feed_url}|{reference}|{published or title}"
            signal_id = f"news_{hashlib.sha256(identity.encode()).hexdigest()[:32]}"
            signals.append(
                ExternalSignal(
                    signal_id=signal_id,
                    signal_type=SignalType.NEWS,
                    source=self.name,
                    reference=reference,
                    title=title,
                    text=text,
                    url=link or None,
                    author=author,
                    symbols=[_base_asset(symbol)] if symbol else [],
                    published_at=published,
                    observed_at=observed_at,
                    metadata={"feed_url": self.feed_url},
                )
            )
        signals.sort(key=lambda item: item.published_at or item.observed_at, reverse=True)
        return signals[:limit]

    @staticmethod
    def _link(entry: ET.Element) -> str:
        for child in list(entry):
            if _local_name(child.tag) != "link":
                continue
            href = child.attrib.get("href")
            if href:
                return href.strip()
            value = _element_text(child)
            if value:
                return value
        return ""


class CryptoPanicNewsConnector(_HttpConnector, SocialSignalPort):
    name = "cryptopanic"
    _VOTE_FIELDS = (
        "positive",
        "negative",
        "important",
        "liked",
        "disliked",
        "lol",
        "toxic",
        "saved",
        "comments",
    )

    def __init__(
        self,
        auth_token: str,
        *,
        api_plan: str = "growth",
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ):
        super().__init__(timeout_seconds=timeout_seconds, http_client=http_client)
        token = auth_token.strip()
        if not token:
            raise ValueError("CryptoPanic auth token is required")
        plan = api_plan.strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]+", plan):
            raise ValueError("CryptoPanic API plan must be a path segment")
        self._auth_token = token
        self._api_url = f"https://cryptopanic.com/api/{plan}/v2/posts/"

    async def signals(
        self, symbol: str | None, since: datetime, limit: int
    ) -> list[ExternalSignal]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if since.tzinfo is None:
            raise ValueError("since must include a timezone")

        params = {
            "auth_token": self._auth_token,
            "kind": "news",
            "public": "true",
        }
        asset = _base_asset(symbol) if symbol else None
        if asset:
            params["currencies"] = asset
        try:
            payload = await self._get_json(self._api_url, params=params)
        except ConnectorError as exc:
            # The auth token is a query parameter; suppress lower-level request details.
            raise ConnectorError(
                str(exc), category=exc.category, retryable=exc.retryable
            ) from None
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ConnectorError(
                "CryptoPanic returned an unexpected response shape",
                category="invalid_response",
            )

        since_utc = since.astimezone(UTC)
        observed_at = utc_now()
        signals: list[ExternalSignal] = []
        for post in payload["results"]:
            if not isinstance(post, dict):
                raise ConnectorError(
                    "CryptoPanic returned an invalid post shape",
                    category="invalid_response",
                )
            signal = self._normalize_post(post, asset, since_utc, observed_at)
            if signal is not None:
                signals.append(signal)
        signals.sort(
            key=lambda item: item.published_at or item.observed_at,
            reverse=True,
        )
        return signals[:limit]

    def _normalize_post(
        self,
        post: dict[str, Any],
        requested_asset: str | None,
        since: datetime,
        observed_at: datetime,
    ) -> ExternalSignal | None:
        title = self._optional_text(post.get("title"), "title")
        description = self._optional_text(post.get("description"), "description")
        if not title:
            title = description
        if not title:
            return None
        title = title[:500]
        text = (description or title)[:5_000]

        article_url = self._optional_text(post.get("original_url"), "original_url")
        if not article_url:
            article_url = self._optional_text(post.get("url"), "url")
        post_id = post.get("id")
        if post_id is not None and (
            isinstance(post_id, bool) or not isinstance(post_id, (str, int))
        ):
            raise ConnectorError(
                "CryptoPanic returned an invalid post identifier",
                category="invalid_response",
            )
        normalized_post_id = str(post_id).strip() if post_id is not None else ""
        identity = "|".join((normalized_post_id, article_url or "", title))
        signal_id = f"cryptopanic_{hashlib.sha256(identity.encode()).hexdigest()[:32]}"
        reference = (normalized_post_id or article_url or signal_id)[:300]

        published_at = self._published_at(post.get("published_at"))
        if published_at and published_at < since:
            return None

        publisher, source_domain = self._source_details(post.get("source"))
        domain = self._optional_text(post.get("domain"), "domain") or source_domain
        currencies = self._currency_codes(post.get("currencies"))
        metadata: dict[str, str] = {}
        if normalized_post_id:
            metadata["post_id"] = normalized_post_id
        if domain:
            metadata["domain"] = domain[:253]
        if publisher:
            metadata["publisher"] = publisher[:160]
        metadata.update(self._vote_metadata(post.get("votes")))

        return ExternalSignal(
            signal_id=signal_id,
            signal_type=SignalType.NEWS,
            source=self.name,
            reference=reference,
            title=title,
            text=text,
            url=article_url[:2_000] if article_url else None,
            symbols=currencies[:20] or ([requested_asset] if requested_asset else []),
            published_at=published_at,
            observed_at=observed_at,
            metadata=metadata,
        )

    @staticmethod
    def _optional_text(value: Any, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ConnectorError(
                f"CryptoPanic returned an invalid {field} field",
                category="invalid_response",
            )
        normalized = _clean_html(value).strip()
        return normalized or None

    @classmethod
    def _source_details(cls, value: Any) -> tuple[str | None, str | None]:
        if value is None:
            return None, None
        if isinstance(value, str):
            return cls._optional_text(value, "source"), None
        if not isinstance(value, dict):
            raise ConnectorError(
                "CryptoPanic returned an invalid source field",
                category="invalid_response",
            )
        publisher = cls._optional_text(value.get("title") or value.get("name"), "source title")
        domain = cls._optional_text(value.get("domain"), "source domain")
        return publisher, domain

    @classmethod
    def _currency_codes(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ConnectorError(
                "CryptoPanic returned an invalid currencies field",
                category="invalid_response",
            )
        codes: list[str] = []
        for currency in value:
            if isinstance(currency, str):
                code = currency
            elif isinstance(currency, dict):
                code = currency.get("code")
            else:
                raise ConnectorError(
                    "CryptoPanic returned an invalid currency entry",
                    category="invalid_response",
                )
            if code is None:
                continue
            if not isinstance(code, str):
                raise ConnectorError(
                    "CryptoPanic returned an invalid currency code",
                    category="invalid_response",
                )
            normalized = code.strip().upper()
            if normalized and normalized not in codes:
                codes.append(normalized)
        return codes

    @classmethod
    def _vote_metadata(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ConnectorError(
                "CryptoPanic returned an invalid votes field",
                category="invalid_response",
            )
        metadata: dict[str, str] = {}
        for field in cls._VOTE_FIELDS:
            count = value.get(field)
            if count is None:
                continue
            if isinstance(count, bool) or not isinstance(count, int):
                raise ConnectorError(
                    "CryptoPanic returned an invalid vote count",
                    category="invalid_response",
                )
            metadata[f"votes_{field}"] = str(count)
        return metadata

    @staticmethod
    def _published_at(value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ConnectorError(
                "CryptoPanic returned an invalid publish time",
                category="invalid_response",
            )
        try:
            published_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConnectorError(
                "CryptoPanic returned an invalid publish time",
                category="invalid_response",
            ) from exc
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        return published_at.astimezone(UTC)


class XApiConnector(_HttpConnector, SocialSignalPort):
    name = "x"

    def __init__(
        self,
        bearer_token: str,
        influencer_usernames: tuple[str, ...] = (),
        *,
        api_base_url: str = "https://api.x.com",
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ):
        super().__init__(timeout_seconds=timeout_seconds, http_client=http_client)
        if not bearer_token.strip():
            raise ValueError("X bearer token is required")
        self._bearer_token = bearer_token.strip()
        self._influencer_usernames = tuple(
            username.strip().lstrip("@").lower()
            for username in influencer_usernames
            if username.strip()
        )
        self._api_base_url = api_base_url.rstrip("/")

    async def signals(
        self, symbol: str | None, since: datetime, limit: int
    ) -> list[ExternalSignal]:
        now = utc_now()
        if since < now - timedelta(days=7):
            raise ConnectorError(
                "X recent search supports a maximum lookback of seven days",
                category="lookback_not_supported",
            )
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        query = self._query(symbol)
        payload = await self._get_json(
            f"{self._api_base_url}/2/tweets/search/recent",
            params={
                "query": query,
                "start_time": since.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "max_results": max(10, limit),
                "tweet.fields": "id,text,created_at,author_id,lang,public_metrics",
                "expansions": "author_id",
                "user.fields": "id,name,username,verified,public_metrics",
            },
            headers={"Authorization": f"Bearer {self._bearer_token}"},
        )
        if not isinstance(payload, dict):
            raise ConnectorError(
                "X returned an unexpected response shape", category="invalid_response"
            )

        users = {
            str(user.get("id")): user
            for user in payload.get("includes", {}).get("users", [])
            if isinstance(user, dict) and user.get("id") is not None
        }
        asset = _base_asset(symbol) if symbol else None
        observed_at = utc_now()
        signals: list[ExternalSignal] = []
        for post in payload.get("data", []):
            if not isinstance(post, dict) or not post.get("id") or not post.get("text"):
                continue
            user = users.get(str(post.get("author_id")), {})
            username = str(user.get("username", "")).strip() or None
            post_id = str(post["id"])
            published = None
            if post.get("created_at"):
                try:
                    published = _timestamp(post["created_at"])
                except (TypeError, ValueError, OSError):
                    pass
            metrics = post.get("public_metrics", {})
            metadata = (
                {str(key): str(value) for key, value in metrics.items()}
                if isinstance(metrics, dict)
                else {}
            )
            reference = (
                f"https://x.com/{username}/status/{post_id}"
                if username
                else f"https://x.com/i/web/status/{post_id}"
            )
            signals.append(
                ExternalSignal(
                    signal_id=f"x_{post_id}",
                    signal_type=SignalType.X_POST,
                    source=self.name,
                    reference=reference,
                    title=f"X post by @{username}" if username else "X post",
                    text=str(post["text"]),
                    url=reference,
                    author=f"@{username}" if username else None,
                    symbols=[asset] if asset else [],
                    published_at=published,
                    observed_at=observed_at,
                    metadata=metadata,
                )
            )
        signals.sort(key=lambda item: item.published_at or item.observed_at, reverse=True)
        return signals[:limit]

    def _query(self, symbol: str | None) -> str:
        terms: list[str] = []
        if symbol:
            asset = _base_asset(symbol)
            terms.extend([f"${asset}", asset])
        else:
            terms.append("crypto")
        query = f"({' OR '.join(terms)})"
        if self._influencer_usernames:
            accounts = " OR ".join(f"from:{username}" for username in self._influencer_usernames)
            query = f"{query} ({accounts})"
        return f"{query} -is:retweet -is:reply"
