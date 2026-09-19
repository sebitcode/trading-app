from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from crypto_orchestrator.accounts import AccountManager
from crypto_orchestrator.api import create_app
from crypto_orchestrator.config import Settings
from crypto_orchestrator.intelligence import IntelligenceService
from crypto_orchestrator.models import (
    DerivativesPositioningPoint,
    DerivativesPositioningSnapshot,
    ExternalSignal,
    MarketSnapshot,
    SignalType,
)
from crypto_orchestrator.notifications import NotificationService
from crypto_orchestrator.service import TradingService
from crypto_orchestrator.store import SQLiteStore


def _account_headers(client: TestClient, name: str = "Test account") -> dict[str, str]:
    response = client.post("/api/v1/accounts", json={"name": name})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_api_exposes_health_and_paper_flow(tmp_path, proposal_payload, postmortem_payload) -> None:
    settings = Settings(db_path=tmp_path / "api.db")
    service = TradingService(settings, SQLiteStore(settings.db_path))
    app = create_app(settings, service)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["paper_trading_only"] is True
        headers = _account_headers(client)

        created = client.post("/api/v1/operations", json=proposal_payload, headers=headers)
        assert created.status_code == 201
        operation_id = created.json()["operation_id"]

        opened = client.post(
            f"/api/v1/operations/{operation_id}/execute-paper", headers=headers
        )
        assert opened.status_code == 200

        outcome = client.post(
            f"/api/v1/operations/{operation_id}/outcome",
            json={"exit_price": "60600", "exit_reason": "take_profit"},
            headers=headers,
        )
        assert outcome.status_code == 200
        assert outcome.json()["outcome"]["status"] == "win"

        postmortem = client.post(
            f"/api/v1/operations/{operation_id}/postmortem",
            json=postmortem_payload,
            headers=headers,
        )
        assert postmortem.status_code == 200

        context = client.get("/api/v1/patterns/momentum_breakout/context", headers=headers)
        assert context.status_code == 200
        assert context.json()["total_cases"] == 1


def test_api_rejects_live_proposal(tmp_path, proposal_payload) -> None:
    settings = Settings(db_path=tmp_path / "api-live.db")
    app = create_app(settings)
    payload = dict(proposal_payload)
    payload["idempotency_key"] = "test-api-live-disabled"
    payload["mode"] = "live"

    with TestClient(app) as client:
        headers = _account_headers(client)
        response = client.post("/api/v1/operations", json=payload, headers=headers)

    assert response.status_code == 422
    assert "live_trading_disabled" in response.json()["detail"]["risk_check"]["reasons"]


def test_api_exposes_read_only_intelligence(tmp_path) -> None:
    market = AsyncMock()
    market.name = "test-market"
    market.snapshot.return_value = MarketSnapshot(
        source="test-market",
        venue="test",
        symbol="BTC/USDT",
        market_type="spot",
        timeframe="1m",
        price=Decimal("60000"),
        latency_ms=1,
    )
    news = AsyncMock()
    news.name = "test-news"
    news.signals.return_value = [
        ExternalSignal(
            signal_id="news-1",
            signal_type=SignalType.NEWS,
            source="test-news",
            reference="https://news.test/1",
            title="Bitcoin update",
            text="BTC update",
            published_at=datetime.now(UTC),
        )
    ]
    x_posts = AsyncMock()
    x_posts.name = "test-x"
    x_posts.signals.return_value = []
    positioning = AsyncMock()
    positioning.name = "test-derivatives"
    positioning.positioning.return_value = DerivativesPositioningSnapshot(
        source="test-derivatives",
        venue="test",
        symbol="BTC/USDT",
        period="5m",
        current=DerivativesPositioningPoint(
            observed_at=datetime.now(UTC),
            open_interest_contracts=Decimal("1000"),
        ),
    )
    intelligence = IntelligenceService(
        market_data=(market,),
        positioning=(positioning,),
        news=(news,),
        x_posts=(x_posts,),
    )
    settings = Settings(db_path=tmp_path / "api-intelligence.db")
    service = TradingService(settings, SQLiteStore(settings.db_path), intelligence)
    app = create_app(settings, service)

    with TestClient(app) as client:
        headers = _account_headers(client)
        market_response = client.get(
            "/api/v1/market/snapshot",
            params={"symbol": "BTC/USDT", "market_type": "spot"},
            headers=headers,
        )
        news_response = client.get(
            "/api/v1/news",
            params={"symbol": "BTC/USDT", "lookback_minutes": 60},
            headers=headers,
        )
        x_response = client.get(
            "/api/v1/signals/x",
            params={"symbol": "BTC/USDT", "lookback_minutes": 60},
            headers=headers,
        )
        positioning_response = client.get(
            "/api/v1/market/positioning",
            params={"symbol": "BTC/USDT", "period": "5m", "limit": 10},
            headers=headers,
        )

    assert market_response.status_code == 200
    assert market_response.json()["snapshots"][0]["price"] == "60000"
    assert news_response.status_code == 200
    assert news_response.json()["signals"][0]["signal_id"] == "news-1"
    assert x_response.status_code == 200
    assert x_response.json()["signals"] == []
    assert positioning_response.status_code == 200
    assert (
        positioning_response.json()["snapshots"][0]["current"]["open_interest_contracts"]
        == "1000"
    )


def test_api_can_test_account_telegram_channel(tmp_path) -> None:
    settings = Settings(
        db_path=tmp_path / "api-telegram.db",
        credential_encryption_key=Fernet.generate_key().decode(),
        binance_market_data_enabled=False,
        news_rss_feeds=(),
    )
    store = SQLiteStore(settings.db_path)
    accounts = AccountManager(settings, store)
    messages: list[tuple[str, tuple[str, ...], str]] = []

    class RecordingSender:
        def __init__(self, token: str) -> None:
            self.token = token

        def send(self, chat_ids: tuple[str, ...], text: str) -> int:
            messages.append((self.token, chat_ids, text))
            return len(chat_ids)

    notifications = NotificationService(
        settings,
        accounts,
        sender_factory=RecordingSender,
    )
    service = TradingService(
        settings,
        store,
        notification_service=notifications,
    )
    app = create_app(settings, service)

    with TestClient(app) as client:
        headers = _account_headers(client)
        saved = client.put(
            "/api/v1/account/credentials/telegram",
            json={"values": {"bot_token": "token-a", "chat_ids": "chat-a"}},
            headers=headers,
        )
        test_message = client.post(
            "/api/v1/account/notifications/telegram/test", headers=headers
        )

    assert saved.status_code == 200
    assert test_message.status_code == 200
    assert test_message.json() == {
        "provider": "telegram",
        "status": "sent",
        "delivered_chats": 1,
        "error": None,
    }
    assert messages == [
        (
            "token-a",
            ("chat-a",),
            "Crypto Orchestrator | Telegram test\n"
            "The account notification channel is working.",
        )
    ]
