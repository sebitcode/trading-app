from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from crypto_orchestrator.connectors import (
    BinanceDerivativesPositioningConnector,
    BinanceMarketDataConnector,
    ConnectorError,
    CryptoPanicNewsConnector,
    RssNewsConnector,
    XApiConnector,
)
from crypto_orchestrator.models import MarketType, SignalType


def test_binance_connector_normalizes_spot_and_perpetual_market_data() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "BTCUSDT"
        if request.url.path.endswith("/ticker/24hr"):
            return httpx.Response(
                200,
                json={
                    "lastPrice": "60000.10",
                    "bidPrice": "60000.00",
                    "askPrice": "60000.20",
                    "priceChangePercent": "1.25",
                    "volume": "123.45",
                },
            )
        if request.url.path.endswith("/klines"):
            return httpx.Response(
                200,
                json=[
                    [
                        1_700_000_000_000,
                        "59000",
                        "60500",
                        "58500",
                        "60000",
                        "100",
                        1_700_000_059_999,
                        "6000000",
                    ]
                ],
            )
        return httpx.Response(404)

    async def run() -> tuple[object, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = BinanceMarketDataConnector(
                spot_base_url="https://spot.test",
                futures_base_url="https://futures.test",
                http_client=client,
            )
            spot = await connector.snapshot("BTC/USDT", MarketType.SPOT, "1m", 1)
            perpetual = await connector.snapshot("BTC/USDT", MarketType.PERPETUAL, "1m", 1)
            return spot, perpetual

    spot, perpetual = asyncio.run(run())

    assert spot.market_type is MarketType.SPOT
    assert spot.price == Decimal("60000.10")
    assert spot.change_24h_percent == Decimal("1.25")
    assert spot.candles[0].close_price == Decimal("60000")
    assert perpetual.market_type is MarketType.PERPETUAL


def test_binance_derivatives_connector_aggregates_positioning_and_trend() -> None:
    timestamps = [1_700_000_000_000, 1_700_000_300_000]

    async def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        path = request.url.path
        assert params["symbol"] == "BTCUSDT"
        if path.endswith("/openInterest"):
            return httpx.Response(
                200,
                json={
                    "symbol": "BTCUSDT",
                    "openInterest": "1000.5",
                    "time": timestamps[-1],
                },
            )
        if path.endswith("/premiumIndex"):
            return httpx.Response(
                200,
                json={
                    "symbol": "BTCUSDT",
                    "markPrice": "60100",
                    "indexPrice": "60090",
                    "lastFundingRate": "0.0001",
                    "time": timestamps[-1],
                },
            )
        assert params["contractType"] == "PERPETUAL"
        assert params["period"] == "5m"
        assert params["limit"] == "2"
        records = {
            "/futures/data/openInterestHist": [
                {
                    "sumOpenInterest": "900",
                    "sumOpenInterestValue": "54000000",
                    "timestamp": timestamps[0],
                },
                {
                    "sumOpenInterest": "950",
                    "sumOpenInterestValue": "57000000",
                    "timestamp": timestamps[1],
                },
            ],
            "/futures/data/globalLongShortAccountRatio": [
                {
                    "longShortRatio": "1.2",
                    "longAccount": "0.5454",
                    "shortAccount": "0.4546",
                    "timestamp": timestamps[1],
                }
            ],
            "/futures/data/topLongShortAccountRatio": [
                {
                    "longShortRatio": "1.5",
                    "longAccount": "0.6",
                    "shortAccount": "0.4",
                    "timestamp": timestamps[1],
                }
            ],
            "/futures/data/topLongShortPositionRatio": [
                {
                    "longShortRatio": "1.1",
                    "longAccount": "0.5238",
                    "shortAccount": "0.4762",
                    "timestamp": timestamps[1],
                }
            ],
            "/futures/data/takerlongshortRatio": [
                {
                    "buyVol": "120",
                    "sellVol": "100",
                    "buySellRatio": "1.2",
                    "timestamp": timestamps[1],
                }
            ],
        }
        for suffix, payload in records.items():
            if path.endswith(suffix):
                return httpx.Response(200, json=payload)
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = BinanceDerivativesPositioningConnector(
                futures_base_url="https://futures.test",
                http_client=client,
            )
            return await connector.positioning("BTC/USDT", "5m", 2)

    snapshot = asyncio.run(run())

    assert snapshot.symbol == "BTC/USDT"
    assert snapshot.contract_type == "PERPETUAL"
    assert snapshot.current.open_interest_contracts == Decimal("1000.5")
    assert snapshot.current.open_interest_value_quote == Decimal("57000000")
    assert snapshot.current.mark_price == Decimal("60100")
    assert snapshot.current.funding_rate == Decimal("0.0001")
    assert snapshot.current.global_long_short_account_ratio == Decimal("1.2")
    assert snapshot.current.top_trader_long_short_position_ratio == Decimal("1.1")
    assert snapshot.current.taker_buy_sell_volume_ratio == Decimal("1.2")
    assert len(snapshot.historical_points) == 2
    assert snapshot.source_errors == []


def test_binance_derivatives_connector_preserves_partial_source_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/openInterest"):
            return httpx.Response(
                200,
                json={"openInterest": "1000", "time": 1_700_000_000_000},
            )
        if request.url.path.endswith("/premiumIndex"):
            return httpx.Response(
                200,
                json={
                    "markPrice": "60000",
                    "indexPrice": "59990",
                    "lastFundingRate": "0",
                    "time": 1_700_000_000_000,
                },
            )
        if request.url.path.endswith("/topLongShortPositionRatio"):
            return httpx.Response(503)
        if request.url.path.endswith("/openInterestHist"):
            return httpx.Response(
                200,
                json=[
                    {
                        "sumOpenInterest": "1000",
                        "sumOpenInterestValue": "60000000",
                        "timestamp": 1_700_000_000_000,
                    }
                ],
            )
        if request.url.path.endswith("/globalLongShortAccountRatio"):
            return httpx.Response(
                200,
                json=[
                    {
                        "longShortRatio": "1",
                        "longAccount": "0.5",
                        "shortAccount": "0.5",
                        "timestamp": 1_700_000_000_000,
                    }
                ],
            )
        if request.url.path.endswith("/topLongShortAccountRatio"):
            return httpx.Response(
                200,
                json=[
                    {
                        "longShortRatio": "1",
                        "longAccount": "0.5",
                        "shortAccount": "0.5",
                        "timestamp": 1_700_000_000_000,
                    }
                ],
            )
        if request.url.path.endswith("/takerlongshortRatio"):
            return httpx.Response(
                200,
                json=[
                    {
                        "buyVol": "100",
                        "sellVol": "100",
                        "buySellRatio": "1",
                        "timestamp": 1_700_000_000_000,
                    }
                ],
            )
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = BinanceDerivativesPositioningConnector(
                futures_base_url="https://futures.test",
                http_client=client,
            )
            return await connector.positioning("BTCUSDT", "5m", 1)

    snapshot = asyncio.run(run())

    assert snapshot.current.mark_price == Decimal("60000")
    assert any(
        error.source.endswith(":top_position_ratio")
        and error.category == "upstream_error"
        and error.retryable
        for error in snapshot.source_errors
    )


def test_rss_connector_filters_by_symbol_and_normalizes_items() -> None:
    body = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item>
        <title>Bitcoin breaks resistance</title>
        <description><![CDATA[BTC market volume increased.]]></description>
        <link>https://news.test/btc</link>
        <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
        <author>Reporter</author>
      </item>
      <item>
        <title>Ethereum upgrade</title>
        <description>ETH network update.</description>
        <link>https://news.test/eth</link>
        <pubDate>Mon, 01 Jan 2024 12:30:00 GMT</pubDate>
      </item>
    </channel></rss>"""

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = RssNewsConnector("https://news.test/feed.xml", http_client=client)
            return await connector.signals(
                "BTC/USDT", datetime(2023, 1, 1, tzinfo=UTC), 20
            )

    signals = asyncio.run(run())

    assert len(signals) == 1
    assert signals[0].signal_type is SignalType.NEWS
    assert signals[0].source == "rss:news.test"
    assert signals[0].title == "Bitcoin breaks resistance"
    assert signals[0].symbols == ["BTC"]
    assert signals[0].author == "Reporter"


def test_cryptopanic_connector_normalizes_posts_and_filters_by_since() -> None:
    now = datetime.now(UTC)
    published_at = now - timedelta(minutes=5)
    old_published_at = now - timedelta(days=2)
    token = "test-cryptopanic-secret"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/growth/v2/posts/"
        assert request.url.params["auth_token"] == token
        assert request.url.params["kind"] == "news"
        assert request.url.params["public"] == "true"
        assert request.url.params["currencies"] == "BTC"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 12345,
                        "title": "Bitcoin market update",
                        "description": "BTC adoption growth is accelerating.",
                        "source": {
                            "title": "Example News",
                            "domain": "example.test",
                        },
                        "domain": "example.test",
                        "original_url": "https://example.test/bitcoin-update",
                        "currencies": [{"code": "BTC", "title": "Bitcoin"}],
                        "published_at": published_at.isoformat(),
                        "votes": {"positive": 7, "negative": 2},
                    },
                    {
                        "id": 67890,
                        "title": "Old Bitcoin report",
                        "published_at": old_published_at.isoformat(),
                    },
                ]
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = CryptoPanicNewsConnector(token, http_client=client)
            return await connector.signals("BTC/USDT", now - timedelta(hours=1), limit=20)

    signals = asyncio.run(run())

    assert len(signals) == 1
    signal = signals[0]
    assert signal.signal_type is SignalType.NEWS
    assert signal.source == "cryptopanic"
    assert signal.reference == "12345"
    assert signal.title == "Bitcoin market update"
    assert signal.text == "BTC adoption growth is accelerating."
    assert signal.url == "https://example.test/bitcoin-update"
    assert signal.symbols == ["BTC"]
    assert signal.published_at == published_at
    assert signal.metadata == {
        "post_id": "12345",
        "domain": "example.test",
        "publisher": "Example News",
        "votes_positive": "7",
        "votes_negative": "2",
    }
    assert token not in repr(signal.model_dump())


def test_cryptopanic_connector_tolerates_optional_post_fields() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"id": "minimal-post", "description": "News summary"}]},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = CryptoPanicNewsConnector("test-token", http_client=client)
            return await connector.signals(None, datetime(2026, 1, 1, tzinfo=UTC), 20)

    signals = asyncio.run(run())

    assert len(signals) == 1
    assert signals[0].title == "News summary"
    assert signals[0].text == "News summary"
    assert signals[0].url is None
    assert signals[0].published_at is None
    assert signals[0].symbols == []
    assert signals[0].metadata == {"post_id": "minimal-post"}


def test_cryptopanic_connector_sanitizes_api_failures() -> None:
    token = "test-cryptopanic-secret"

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": f"invalid token {token}"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = CryptoPanicNewsConnector(token, http_client=client)
            await connector.signals(None, datetime.now(UTC) - timedelta(hours=1), 20)

    with pytest.raises(ConnectorError) as captured:
        asyncio.run(run())

    assert captured.value.category == "upstream_rejected"
    assert token not in str(captured.value)
    assert captured.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "payload",
    [
        {"results": "not-a-list"},
        {"results": [None]},
    ],
)
def test_cryptopanic_connector_rejects_invalid_response_shapes(payload) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = CryptoPanicNewsConnector("test-token", http_client=client)
            await connector.signals(None, datetime.now(UTC) - timedelta(hours=1), 20)

    with pytest.raises(ConnectorError) as captured:
        asyncio.run(run())

    assert captured.value.category == "invalid_response"


def test_x_connector_uses_bearer_auth_and_influencer_filter() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/2/tweets/search/recent"
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.url.params["query"] == (
            "($BTC OR BTC) (from:alice OR from:bob) -is:retweet -is:reply"
        )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "123",
                        "text": "BTC momentum is improving",
                        "created_at": "2026-09-13T12:00:00Z",
                        "author_id": "42",
                        "public_metrics": {"like_count": 10, "repost_count": 2},
                    }
                ],
                "includes": {
                    "users": [
                        {
                            "id": "42",
                            "username": "alice",
                            "name": "Alice",
                            "verified": True,
                        }
                    ]
                },
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = XApiConnector(
                "secret-token",
                ("@Alice", "bob"),
                api_base_url="https://api.x.test",
                http_client=client,
            )
            return await connector.signals(
                "BTC/USDT", datetime.now(UTC) - timedelta(hours=1), 20
            )

    signals = asyncio.run(run())

    assert len(signals) == 1
    assert signals[0].signal_type is SignalType.X_POST
    assert signals[0].author == "@alice"
    assert signals[0].url == "https://x.com/alice/status/123"
    assert signals[0].metadata == {"like_count": "10", "repost_count": "2"}
