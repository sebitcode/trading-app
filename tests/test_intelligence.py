from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_orchestrator.config import Settings
from crypto_orchestrator.connectors import ConnectorError
from crypto_orchestrator.intelligence import IntelligenceService, build_default_intelligence
from crypto_orchestrator.models import (
    DerivativesPositioningPoint,
    DerivativesPositioningSnapshot,
    ExternalSignal,
    MarketSnapshot,
    MarketType,
    SignalType,
)


class FakeMarketConnector:
    name = "fake-market"

    async def snapshot(
        self, symbol: str, market_type: MarketType, timeframe: str, limit: int
    ) -> MarketSnapshot:
        return MarketSnapshot(
            source=self.name,
            venue="fake",
            symbol=symbol,
            market_type=market_type,
            timeframe=timeframe,
            price=Decimal("60000"),
            latency_ms=3,
        )


class FakeNewsConnector:
    name = "fake-news"

    async def signals(self, symbol: str | None, since: datetime, limit: int):
        return [
            ExternalSignal(
                signal_id="news-1",
                signal_type=SignalType.NEWS,
                source=self.name,
                reference="https://news.test/1",
                title="Bitcoin market update",
                text="BTC market update",
                symbols=["BTC"],
                published_at=since + timedelta(minutes=1),
            )
        ]


class FailingXConnector:
    name = "x"

    async def signals(self, symbol: str | None, since: datetime, limit: int):
        raise ConnectorError("X token is invalid", category="upstream_rejected")


class FakePositioningConnector:
    name = "fake-derivatives"

    async def positioning(self, symbol: str, period: str, limit: int):
        point = DerivativesPositioningPoint(
            observed_at=datetime.now(UTC),
            open_interest_contracts=Decimal("1000"),
            funding_rate=Decimal("0.0001"),
            global_long_short_account_ratio=Decimal("1.1"),
        )
        return DerivativesPositioningSnapshot(
            source=self.name,
            venue="fake",
            symbol=symbol,
            period=period,
            current=point,
        )


def test_intelligence_aggregates_data_and_preserves_source_errors() -> None:
    service = IntelligenceService(
        market_data=(FakeMarketConnector(),),
        news=(FakeNewsConnector(),),
        x_posts=(FailingXConnector(),),
    )
    since = datetime.now(UTC) - timedelta(hours=1)

    async def run():
        return (
            await service.market_snapshot("BTC/USDT", MarketType.SPOT, "1m", 2),
            await service.news("BTC/USDT", since, 10),
            await service.x_posts("BTC/USDT", since, 10),
        )

    market, news, x_posts = asyncio.run(run())

    assert market.snapshots[0].source == "fake-market"
    assert news.signals[0].signal_id == "news-1"
    assert x_posts.signals == []
    assert x_posts.errors[0].category == "upstream_rejected"


def test_intelligence_aggregates_derivatives_positioning() -> None:
    service = IntelligenceService(positioning=(FakePositioningConnector(),))

    result = asyncio.run(service.derivatives_positioning("BTC/USDT", "5m", 10))

    assert result.snapshots[0].current.open_interest_contracts == Decimal("1000")
    assert result.snapshots[0].current.funding_rate == Decimal("0.0001")
    assert result.errors == []
    assert service.health()["derivatives_positioning"] == {"fake-derivatives": "configured"}


def test_intelligence_reports_unconfigured_source() -> None:
    service = IntelligenceService()
    since = datetime.now(UTC) - timedelta(hours=1)

    result = asyncio.run(service.x_posts(None, since, 10))

    assert result.signals == []
    assert result.errors[0].source == "x"
    assert result.errors[0].category == "not_configured"
    assert service.health()["x"] == {"x_api": "not_configured"}
    positioning = asyncio.run(service.derivatives_positioning("BTC/USDT", "5m", 10))
    assert positioning.snapshots == []
    assert positioning.errors[0].category == "not_configured"
    assert service.health()["derivatives_positioning"] == {
        "binance_derivatives": "not_configured"
    }


def test_account_credentials_override_global_x_configuration() -> None:
    settings = Settings(
        x_api_enabled=True,
        x_bearer_token="global-token",
        x_influencer_usernames=("global",),
    )

    account_without_x = build_default_intelligence(settings, account_credentials={})
    account_with_x = build_default_intelligence(
        settings,
        account_credentials={
            "x": {
                "bearer_token": "account-token",
                "influencer_usernames": "alice,bob",
            }
        },
    )

    assert account_without_x.health()["x"] == {"x_api": "not_configured"}
    assert account_with_x.health()["x"] == {"x": "configured"}


def test_x_is_disabled_by_default_even_with_account_credentials() -> None:
    settings = Settings(
        x_bearer_token="global-token",
        x_influencer_usernames=("global",),
    )

    intelligence = build_default_intelligence(
        settings,
        account_credentials={
            "x": {
                "bearer_token": "account-token",
                "influencer_usernames": "alice,bob",
            }
        },
    )

    assert intelligence.health()["x"] == {"x_api": "not_configured"}


def test_cryptopanic_settings_default_to_disabled_and_load_from_environment(
    monkeypatch,
) -> None:
    env_names = (
        "CRYPTOPANIC_API_ENABLED",
        "CRYPTOPANIC_AUTH_TOKEN",
        "CRYPTOPANIC_API_PLAN",
    )
    for name in env_names:
        monkeypatch.delenv(name, raising=False)

    defaults = Settings.from_env()

    assert defaults.cryptopanic_api_enabled is False
    assert defaults.cryptopanic_auth_token == ""
    assert defaults.cryptopanic_api_plan == "growth"

    monkeypatch.setenv("CRYPTOPANIC_API_ENABLED", "true")
    monkeypatch.setenv("CRYPTOPANIC_AUTH_TOKEN", "  test-token  ")
    monkeypatch.setenv("CRYPTOPANIC_API_PLAN", " PRO ")
    configured = Settings.from_env()

    assert configured.cryptopanic_api_enabled is True
    assert configured.cryptopanic_auth_token == "test-token"
    assert configured.cryptopanic_api_plan == "pro"


def test_cryptopanic_is_only_added_when_explicitly_enabled_with_token() -> None:
    disabled_with_token = build_default_intelligence(
        Settings(news_rss_feeds=(), cryptopanic_auth_token="test-token")
    )
    enabled_without_token = build_default_intelligence(
        Settings(news_rss_feeds=(), cryptopanic_api_enabled=True)
    )
    configured = build_default_intelligence(
        Settings(
            news_rss_feeds=("https://news.test/feed.xml",),
            cryptopanic_api_enabled=True,
            cryptopanic_auth_token="test-token",
        )
    )

    assert disabled_with_token.health()["news"] == {"rss": "not_configured"}
    assert enabled_without_token.health()["news"] == {"rss": "not_configured"}
    assert configured.health()["news"] == {
        "rss:news.test": "configured",
        "cryptopanic": "configured",
    }
    assert "test-token" not in repr(configured.health())
