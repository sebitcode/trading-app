from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

import httpx
from cryptography.fernet import Fernet

from crypto_orchestrator.account_context import account_scope
from crypto_orchestrator.accounts import AccountManager
from crypto_orchestrator.config import Settings
from crypto_orchestrator.models import (
    CredentialPayload,
    OutcomeInput,
    PostmortemInput,
    TradeProposal,
)
from crypto_orchestrator.notifications import (
    NotificationError,
    NotificationService,
    TelegramNotifier,
)
from crypto_orchestrator.service import TradingService
from crypto_orchestrator.store import SQLiteStore


def test_telegram_notifier_sends_plain_text_to_each_chat() -> None:
    requests: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        notifier = TelegramNotifier(
            "123456:bot-secret",
            api_base_url="https://api.telegram.org",
            http_client=client,
        )
        delivered = notifier.send(("100", "@crypto_channel"), "A paper operation opened.")
    finally:
        client.close()

    assert delivered == 2
    assert len(requests) == 2
    assert all(url.endswith("/bot123456:bot-secret/sendMessage") for url, _ in requests)
    assert [payload["chat_id"] for _, payload in requests] == ["100", "@crypto_channel"]
    assert all(payload["text"] == "A paper operation opened." for _, payload in requests)
    assert all("parse_mode" not in payload for _, payload in requests)


def test_notification_lifecycle_is_account_scoped(
    tmp_path, proposal_payload, postmortem_payload
) -> None:
    settings = Settings(
        db_path=tmp_path / "notifications.db",
        credential_encryption_key=Fernet.generate_key().decode(),
        binance_market_data_enabled=False,
        news_rss_feeds=(),
    )
    store = SQLiteStore(settings.db_path)
    accounts = AccountManager(settings, store)
    account_a = accounts.create_account("User A")
    account_b = accounts.create_account("User B")
    accounts.save_credential(
        account_a.account_id,
        "telegram",
        CredentialPayload(values={"bot_token": "token-a", "chat_ids": "chat-a"}),
    )
    accounts.save_credential(
        account_b.account_id,
        "telegram",
        CredentialPayload(values={"bot_token": "token-b", "chat_ids": "chat-b"}),
    )

    deliveries: list[tuple[str, tuple[str, ...], str]] = []

    class RecordingSender:
        def __init__(self, token: str) -> None:
            self.token = token

        def send(self, chat_ids: tuple[str, ...], text: str) -> int:
            deliveries.append((self.token, chat_ids, text))
            return len(chat_ids)

    notifications = NotificationService(
        settings,
        accounts,
        sender_factory=RecordingSender,
    )
    service = TradingService(
        settings,
        store,
        account_manager=accounts,
        notification_service=notifications,
    )
    proposal = TradeProposal.model_validate(proposal_payload)

    with account_scope(account_a.account_id):
        operation = service.create_proposal(proposal)
        service.execute_paper(operation.operation_id)
        service.close_operation(
            operation.operation_id,
            OutcomeInput(exit_price=Decimal("60600"), exit_reason="take_profit"),
        )
        service.record_postmortem(
            operation.operation_id,
            PostmortemInput.model_validate(postmortem_payload),
        )

    with account_scope(account_b.account_id):
        operation_b = service.create_proposal(proposal)
        service.execute_paper(operation_b.operation_id)

    assert len(deliveries) == 4
    assert [delivery[0] for delivery in deliveries] == ["token-a"] * 3 + ["token-b"]
    assert [delivery[1] for delivery in deliveries] == [("chat-a",)] * 3 + [("chat-b",)]
    assert "Thesis:" in deliveries[0][2]
    assert "PnL gross/net:" in deliveries[1][2]
    assert "Candidate lesson:" in deliveries[2][2]


def test_notification_failure_does_not_rollback_operation(tmp_path, proposal_payload) -> None:
    settings = Settings(
        db_path=tmp_path / "notification-failure.db",
        credential_encryption_key=Fernet.generate_key().decode(),
        binance_market_data_enabled=False,
        news_rss_feeds=(),
    )
    store = SQLiteStore(settings.db_path)
    accounts = AccountManager(settings, store)
    account = accounts.create_account("User A")
    accounts.save_credential(
        account.account_id,
        "telegram",
        CredentialPayload(values={"bot_token": "token-a", "chat_ids": "chat-a"}),
    )

    class FailingSender:
        def send(self, chat_ids: tuple[str, ...], text: str) -> int:
            raise NotificationError("upstream unavailable", category="network_error")

    notifications = NotificationService(
        settings,
        accounts,
        sender_factory=lambda token: FailingSender(),
    )
    service = TradingService(
        settings,
        store,
        account_manager=accounts,
        notification_service=notifications,
    )

    with account_scope(account.account_id):
        operation = service.create_proposal(TradeProposal.model_validate(proposal_payload))
        opened = service.execute_paper(operation.operation_id)

    assert opened.status.value == "paper_open"
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1
