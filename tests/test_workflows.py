from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from crypto_orchestrator.api import create_app
from crypto_orchestrator.config import Settings
from crypto_orchestrator.intelligence import IntelligenceService
from crypto_orchestrator.models import ExternalSignal, MarketSnapshot, SignalType
from crypto_orchestrator.service import TradingService
from crypto_orchestrator.store import SQLiteStore


def _headers(client: TestClient, name: str) -> dict[str, str]:
    response = client.post("/api/v1/accounts", json={"name": name})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _workflow_payload() -> dict[str, object]:
    return {
        "name": "BTC decision desk",
        "description": "Gather market context and review the result.",
        "before_steps": [
            {
                "name": "Market snapshot",
                "type": "market_snapshot",
                "parameters": {
                    "symbol": "$context.symbol",
                    "market_type": "spot",
                    "timeframe": "15m",
                    "limit": "5",
                },
            },
            {
                "name": "Decision rules",
                "type": "agent_instruction",
                "parameters": {
                    "instruction": "Compare all outputs before writing the thesis."
                },
            },
        ],
        "after_steps": [
            {
                "name": "Pattern review",
                "type": "pattern_context",
                "parameters": {"pattern_id": "$context.pattern_id", "limit": "10"},
            }
        ],
        "enabled": True,
    }


def test_workflows_are_named_and_isolated_per_account(tmp_path) -> None:
    app = create_app(Settings(db_path=tmp_path / "workflows.db"))

    with TestClient(app) as client:
        headers_a = _headers(client, "User A")
        headers_b = _headers(client, "User B")
        created = client.post(
            "/api/v1/workflows", json=_workflow_payload(), headers=headers_a
        )
        duplicate = client.post(
            "/api/v1/workflows",
            json=_workflow_payload() | {"name": "btc DECISION desk"},
            headers=headers_a,
        )
        account_a_workflows = client.get("/api/v1/workflows", headers=headers_a)
        account_b_workflows = client.get("/api/v1/workflows", headers=headers_b)

        assert created.status_code == 201
        assert duplicate.status_code == 409
        workflow = created.json()
        assert workflow["name"] == "BTC decision desk"
        assert workflow["version"] == 1
        assert len(account_a_workflows.json()) == 1
        assert account_b_workflows.json() == []

        workflow_id = workflow["workflow_id"]
        cross_account = client.get(
            f"/api/v1/workflows/{workflow_id}", headers=headers_b
        )
        updated = client.put(
            f"/api/v1/workflows/{workflow_id}",
            json=_workflow_payload() | {"description": "Updated review"},
            headers=headers_a,
        )

    assert cross_account.status_code == 404
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["description"] == "Updated review"


def test_workflow_runner_executes_queries_and_binds_operation_snapshot(
    tmp_path, proposal_payload
) -> None:
    market = AsyncMock()
    market.name = "test-market"
    market.snapshot.return_value = MarketSnapshot(
        source="test-market",
        venue="test",
        symbol="BTC/USDT",
        market_type="spot",
        timeframe="15m",
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
    intelligence = IntelligenceService(
        market_data=(market,), news=(news,), x_posts=(x_posts,)
    )
    settings = Settings(
        db_path=tmp_path / "workflow-runner.db",
        binance_market_data_enabled=False,
        news_rss_feeds=(),
    )
    service = TradingService(
        settings,
        SQLiteStore(settings.db_path),
        intelligence=intelligence,
    )
    app = create_app(settings, service)

    with TestClient(app) as client:
        headers = _headers(client, "Workflow account")
        created = client.post(
            "/api/v1/workflows", json=_workflow_payload(), headers=headers
        )
        before = client.post(
            "/api/v1/workflows/run",
            json={
                "workflow_name": "BTC decision desk",
                "phase": "before_operation",
                "symbol": "BTC/USDT",
            },
            headers=headers,
        )
        proposal = dict(proposal_payload)
        proposal["idempotency_key"] = "workflow-bound-proposal-001"
        proposal["workflow_name"] = "BTC decision desk"
        operation = client.post("/api/v1/operations", json=proposal, headers=headers)
        after = client.post(
            "/api/v1/workflows/run",
            json={
                "workflow_name": "btc decision desk",
                "phase": "after_operation",
                "operation_id": operation.json()["operation_id"],
            },
            headers=headers,
        )

    assert created.status_code == 201
    assert before.status_code == 200
    assert before.json()["status"] == "completed"
    assert [step["status"] for step in before.json()["steps"]] == [
        "completed",
        "completed",
    ]
    assert operation.status_code == 201
    assert operation.json()["workflow"]["name"] == "BTC decision desk"
    assert after.status_code == 200
    assert after.json()["status"] == "completed"
    assert after.json()["workflow_version"] == 1
    market.snapshot.assert_awaited_once()
