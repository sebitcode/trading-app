from __future__ import annotations

import sqlite3

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from crypto_orchestrator.account_context import account_scope
from crypto_orchestrator.accounts import (
    AccountManager,
    BootstrapError,
    UnsupportedCredentialProvider,
)
from crypto_orchestrator.api import create_app
from crypto_orchestrator.config import Settings
from crypto_orchestrator.models import CredentialPayload, TradeProposal
from crypto_orchestrator.service import TradingService
from crypto_orchestrator.store import SQLiteStore


def _create_account(client: TestClient, name: str) -> tuple[str, dict[str, str]]:
    response = client.post("/api/v1/accounts", json={"name": name})
    assert response.status_code == 201
    payload = response.json()
    return payload["account_id"], {"Authorization": f"Bearer {payload['access_token']}"}


def test_accounts_hash_tokens_encrypt_credentials_and_do_not_mix(tmp_path) -> None:
    settings = Settings(
        db_path=tmp_path / "accounts.db",
        credential_encryption_key=Fernet.generate_key().decode(),
    )
    store = SQLiteStore(settings.db_path)
    manager = AccountManager(settings, store)

    account_a = manager.create_account("User A")
    account_b = manager.create_account("User B")

    assert account_a.access_token != account_b.access_token
    assert manager.authenticate(account_a.access_token).account_id == account_a.account_id
    assert manager.authenticate(account_b.access_token).account_id == account_b.account_id

    manager.save_credential(
        account_a.account_id,
        "x",
        CredentialPayload(
            values={"bearer_token": "secret-a", "influencer_usernames": "alice"}
        ),
    )

    assert manager.credentials_for(account_a.account_id) == {
        "x": {"bearer_token": "secret-a", "influencer_usernames": "alice"}
    }
    assert manager.credentials_for(account_b.account_id) == {}
    assert manager.list_credentials(account_b.account_id) == []

    with sqlite3.connect(settings.db_path) as connection:
        token_rows = connection.execute("SELECT token_hash FROM account_tokens").fetchall()
        credential_row = connection.execute(
            "SELECT encrypted_json FROM account_credentials WHERE account_id = ?",
            (account_a.account_id,),
        ).fetchone()

    assert all(account_a.access_token not in row[0] for row in token_rows)
    assert credential_row is not None
    assert "secret-a" not in credential_row[0]

    try:
        manager.save_credential(
            account_b.account_id,
            "github",
            CredentialPayload(values={"token": "not-supported"}),
        )
    except UnsupportedCredentialProvider:
        pass
    else:
        raise AssertionError("unsupported providers must be rejected")


def test_partial_credential_update_preserves_existing_values(tmp_path) -> None:
    settings = Settings(
        db_path=tmp_path / "partial-credentials.db",
        credential_encryption_key=Fernet.generate_key().decode(),
    )
    store = SQLiteStore(settings.db_path)
    manager = AccountManager(settings, store)
    account = manager.create_account("User A")

    manager.save_credential(
        account.account_id,
        "telegram",
        CredentialPayload(values={"bot_token": "token-a", "chat_ids": "chat-a"}),
    )
    manager.save_credential(
        account.account_id,
        "telegram",
        CredentialPayload(values={"chat_ids": "chat-a,chat-b"}),
    )

    assert manager.credentials_for(account.account_id) == {
        "telegram": {"bot_token": "token-a", "chat_ids": "chat-a,chat-b"}
    }


def test_http_account_isolation_covers_operations_and_lessons(
    tmp_path, proposal_payload, postmortem_payload
) -> None:
    settings = Settings(
        db_path=tmp_path / "api-accounts.db",
        credential_encryption_key=Fernet.generate_key().decode(),
        binance_market_data_enabled=False,
        news_rss_feeds=(),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        account_a, headers_a = _create_account(client, "User A")
        account_b, headers_b = _create_account(client, "User B")

        unauthenticated = client.get("/api/v1/operations")
        invalid = client.get(
            "/api/v1/operations", headers={"Authorization": "Bearer invalid-token"}
        )
        assert unauthenticated.status_code == 401
        assert invalid.status_code == 401
        assert invalid.headers["www-authenticate"] == "Bearer"
        assert client.get("/api/v1/account", headers=headers_a).json()["account_id"] == account_a
        assert client.get("/api/v1/account", headers=headers_b).json()["account_id"] == account_b

        first = client.post(
            "/api/v1/operations", json=proposal_payload, headers=headers_a
        )
        second = client.post(
            "/api/v1/operations", json=proposal_payload, headers=headers_b
        )
        assert first.status_code == 201
        assert second.status_code == 201
        first_id = first.json()["operation_id"]
        second_id = second.json()["operation_id"]
        assert first_id != second_id
        assert first.json()["account_id"] == account_a
        assert second.json()["account_id"] == account_b

        assert len(client.get("/api/v1/operations", headers=headers_a).json()) == 1
        assert len(client.get("/api/v1/operations", headers=headers_b).json()) == 1
        assert client.get(f"/api/v1/operations/{second_id}", headers=headers_a).status_code == 404

        assert client.post(
            f"/api/v1/operations/{first_id}/execute-paper", headers=headers_a
        ).status_code == 200
        assert client.post(
            f"/api/v1/operations/{first_id}/outcome",
            json={"exit_price": "60600", "exit_reason": "take_profit"},
            headers=headers_a,
        ).status_code == 200
        assert client.post(
            f"/api/v1/operations/{first_id}/postmortem",
            json=postmortem_payload,
            headers=headers_a,
        ).status_code == 200

        context_a = client.get(
            "/api/v1/patterns/momentum_breakout/context", headers=headers_a
        )
        context_b = client.get(
            "/api/v1/patterns/momentum_breakout/context", headers=headers_b
        )
        assert context_a.json()["total_cases"] == 1
        assert context_b.json()["total_cases"] == 0

        saved = client.put(
            "/api/v1/account/credentials/x",
            json={
                "values": {
                    "bearer_token": "secret-a",
                    "influencer_usernames": "alice,bob",
                }
            },
            headers=headers_a,
        )
        assert saved.status_code == 200
        assert "secret-a" not in saved.text
        updated = client.put(
            "/api/v1/account/credentials/x",
            json={"values": {"influencer_usernames": "alice,bob,charlie"}},
            headers=headers_a,
        )
        assert updated.status_code == 200
        assert app.state.account_manager.credentials_for(account_a) == {
            "x": {
                "bearer_token": "secret-a",
                "influencer_usernames": "alice,bob,charlie",
            }
        }
        assert client.get(
            "/api/v1/account/credentials", headers=headers_a
        ).json()[0]["provider"] == "x"
        assert client.get("/api/v1/account/credentials", headers=headers_b).json() == []


def test_production_account_creation_requires_bootstrap_token(tmp_path) -> None:
    settings = Settings(
        app_env="production",
        account_bootstrap_token="bootstrap-secret",
        db_path=tmp_path / "production-accounts.db",
    )
    manager = AccountManager(settings, SQLiteStore(settings.db_path))

    with pytest.raises(BootstrapError):
        manager.create_account("User A")
    with pytest.raises(BootstrapError):
        manager.create_account("User A", "wrong-secret")

    account = manager.create_account("User A", "bootstrap-secret")
    assert manager.authenticate(account.access_token).account_id == account.account_id


def test_legacy_sqlite_schema_migrates_to_account_scoped_indexes(
    tmp_path, proposal_payload
) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE operations (
                operation_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            );
            CREATE TABLE lessons (
                lesson_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                pattern_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            );
            """
        )

    settings = Settings(db_path=path)
    store = SQLiteStore(path)
    manager = AccountManager(settings, store)
    account_a = manager.create_account("User A")
    account_b = manager.create_account("User B")
    service = TradingService(settings, store, account_manager=manager)
    proposal = TradeProposal.model_validate(proposal_payload)

    with account_scope(account_a.account_id):
        operation_a = service.create_proposal(proposal)
    with account_scope(account_b.account_id):
        operation_b = service.create_proposal(proposal)

    assert operation_a.operation_id != operation_b.operation_id
    with sqlite3.connect(path) as connection:
        operation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(operations)")
        }
        lesson_columns = {row[1] for row in connection.execute("PRAGMA table_info(lessons)")}
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(operations)")
        }

    assert "account_id" in operation_columns
    assert "account_id" in lesson_columns
    assert "idx_operations_account_idempotency" in indexes
