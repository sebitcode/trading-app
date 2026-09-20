from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from crypto_orchestrator.account_context import account_scope
from crypto_orchestrator.accounts import AccountManager
from crypto_orchestrator.api import create_app
from crypto_orchestrator.config import Settings
from crypto_orchestrator.models import (
    AgentInvestigationInput,
    InvestigationDecision,
    InvestigationPhase,
)
from crypto_orchestrator.service import ConflictError, NotFoundError, TradingService
from crypto_orchestrator.store import SQLiteStore


def _investigation(key: str = "hourly-btc-001") -> AgentInvestigationInput:
    return AgentInvestigationInput(
        idempotency_key=key,
        task_name="sebitcode hourly crypto monitor",
        agent_id="research-agent",
        symbol="BTC/USDT",
        phase=InvestigationPhase.SIMULATION,
        decision=InvestigationDecision.NO_TRADE,
        summary="No valid setup after the scheduled market review.",
        reason="reward_risk_below_minimum",
        signal="long score 0.61",
        balance_quote="1000",
        equity_quote="998.50",
        drawdown_percent="0.15",
        spread_bps="3.2",
        findings={"market_regime": "range", "data_status": "fresh"},
    )


def test_agent_investigation_is_durable_idempotent_and_secret_free(tmp_path) -> None:
    settings = Settings(db_path=tmp_path / "investigations.db")
    store = SQLiteStore(settings.db_path)
    service = TradingService(settings, store)

    saved = service.record_agent_investigation(_investigation())
    repeated = service.record_agent_investigation(_investigation())

    assert repeated.investigation_id == saved.investigation_id
    assert service.list_agent_investigations(task_name="SEBITCODE HOURLY CRYPTO MONITOR")
    assert "secret" not in saved.model_dump_json().lower()

    reopened = TradingService(settings, SQLiteStore(settings.db_path))
    assert reopened.get_agent_investigation(saved.investigation_id).summary == saved.summary

    with pytest.raises(ConflictError):
        service.record_agent_investigation(
            _investigation().model_copy(update={"reason": "different"})
        )


def test_agent_investigation_memory_is_account_scoped(tmp_path) -> None:
    settings = Settings(db_path=tmp_path / "investigations-accounts.db")
    store = SQLiteStore(settings.db_path)
    accounts = AccountManager(settings, store)
    account_a = accounts.create_account("User A")
    account_b = accounts.create_account("User B")
    service = TradingService(settings, store, account_manager=accounts)

    with account_scope(account_a.account_id):
        saved = service.record_agent_investigation(_investigation("account-a-001"))
        assert service.list_agent_investigations()

    with account_scope(account_b.account_id):
        assert service.list_agent_investigations() == []
        with pytest.raises(NotFoundError):
            service.get_agent_investigation(saved.investigation_id)


def test_agent_investigation_rejects_credential_shaped_findings() -> None:
    with pytest.raises(ValidationError, match="credential fields"):
        AgentInvestigationInput.model_validate(
            _investigation().model_dump() | {"findings": {"api_token": "do-not-store"}}
        )


def test_api_exposes_account_investigation_memory(tmp_path) -> None:
    settings = Settings(db_path=tmp_path / "investigations-api.db")

    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/accounts", json={"name": "Investigation API"})
        headers = {"Authorization": f"Bearer {created.json()['access_token']}"}
        saved = client.post(
            "/api/v1/investigations",
            json=_investigation("api-hourly-001").model_dump(mode="json"),
            headers=headers,
        )
        listed = client.get("/api/v1/investigations", headers=headers)

    assert saved.status_code == 201
    assert listed.status_code == 200
    assert listed.json()[0]["task_name"] == "sebitcode hourly crypto monitor"
