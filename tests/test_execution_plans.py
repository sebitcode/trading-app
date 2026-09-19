from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from crypto_orchestrator.account_context import account_scope
from crypto_orchestrator.accounts import AccountManager
from crypto_orchestrator.api import create_app
from crypto_orchestrator.config import Settings
from crypto_orchestrator.intelligence import IntelligenceService
from crypto_orchestrator.models import (
    DerivativesPositioningPoint,
    DerivativesPositioningSnapshot,
    ExecutionPlanCreateRequest,
    ExecutionPlanRunRequest,
    ExecutionPlanStatus,
    ExecutionPlanUpdateRequest,
    MarketSnapshot,
    MarketType,
    TradeProposal,
)
from crypto_orchestrator.service import ConflictError, NotFoundError, RiskRejected, TradingService
from crypto_orchestrator.store import SQLiteStore


def _plan_payload() -> dict[str, object]:
    return {
        "name": "EMA sample collection",
        "description": "Collect a controlled paper-trading sample.",
        "objective": "Collect enough closed paper operations to evaluate the strategy.",
        "strategy_version": "ema_rsi_atr_trend_v1",
        "pattern_id": "ema_rsi_atr_trend_v1",
        "mode": "paper",
        "capital_quote": "1000",
        "max_trade_notional_quote": "1000",
        "risk_per_trade_quote": "10",
        "max_daily_loss_quote": "30",
        "minimum_net_reward_risk_ratio": "0",
        "max_open_operations": 1,
        "max_duration_minutes": 4320,
        "target_operations": 200,
        "symbols": ["BTC/USDT", "ETHUSDT"],
        "market_type": "perpetual",
        "timeframe": "1m",
        "allowed_sides": ["long", "short"],
        "data_sources": ["binance_public", "rss"],
        "entry_rules": ["Require EMA9/EMA21 alignment and RSI confirmation."],
        "exit_rules": ["Use the hard stop, target, and maximum holding time."],
        "risk_rules": ["Risk no more than 10 USDT per operation."],
        "evaluation_metrics": ["net_expectancy", "profit_factor", "max_drawdown"],
        "steps": [
            {
                "step_id": "pstep_market",
                "name": "Read market",
                "action": "read_market_snapshot",
                "instructions": "Read the allowed market snapshot before deciding.",
            },
            {
                "step_id": "pstep_news",
                "name": "Read RSS",
                "action": "read_crypto_news",
                "instructions": "Read configured RSS news as context, never as an order trigger.",
            },
            {
                "step_id": "pstep_decision",
                "name": "Agent decision",
                "action": "agent_decision",
                "instructions": (
                    "Explain the evidence and choose whether a paper proposal is justified."
                ),
            },
            {
                "step_id": "pstep_propose",
                "name": "Propose paper trade",
                "action": "propose_paper_trade",
                "instructions": "Submit a complete paper proposal only when all risk limits pass.",
            },
            {
                "step_id": "pstep_execute",
                "name": "Execute paper trade",
                "action": "execute_paper_trade",
                "instructions": "Execute only the proposal accepted by the server risk engine.",
            },
            {
                "step_id": "pstep_outcome",
                "name": "Record outcome",
                "action": "record_operation_outcome",
                "instructions": (
                    "Close the operation with the observed price and all simulated costs."
                ),
            },
            {
                "step_id": "pstep_postmortem",
                "name": "Record postmortem",
                "action": "record_agent_postmortem",
                "instructions": "Record what worked, what failed, and a candidate lesson.",
            },
        ],
        "status": "active",
    }


def _plan_request() -> ExecutionPlanCreateRequest:
    return ExecutionPlanCreateRequest.model_validate(_plan_payload())


def _provenance_plan_payload() -> dict[str, object]:
    payload = _plan_payload()
    payload.update(
        {
            "name": "EMA provenance plan",
            "max_trade_notional_quote": "300",
            "max_open_operations": 2,
            "target_operations": 10,
        }
    )
    payload["steps"] = [
        {
            "step_id": "pstep_market",
            "name": "Read market",
            "action": "read_market_snapshot",
            "instructions": "Read the exact symbol market snapshot before deciding.",
        },
        {
            "step_id": "pstep_news",
            "name": "Read RSS",
            "action": "read_crypto_news",
            "instructions": "Read exact symbol RSS context before deciding.",
        },
        {
            "step_id": "pstep_positioning",
            "name": "Read positioning",
            "action": "read_derivatives_positioning",
            "instructions": "Read public aggregate positioning for the exact symbol.",
        },
        {
            "step_id": "pstep_pattern",
            "name": "Read pattern context",
            "action": "read_pattern_context",
            "instructions": "Read comparable pattern outcomes for the exact symbol.",
        },
        {
            "step_id": "pstep_lessons",
            "name": "Read lessons",
            "action": "read_lessons",
            "instructions": "Read candidate lessons for the exact symbol.",
        },
        {
            "step_id": "pstep_decision",
            "name": "Agent decision",
            "action": "agent_decision",
            "instructions": "Explain the evidence before proposing a paper trade.",
        },
    ]
    return payload


def _provenance_intelligence() -> IntelligenceService:
    market = AsyncMock()
    market.name = "binance"
    market.snapshot.return_value = MarketSnapshot(
        source="binance",
        venue="binance",
        symbol="BTC/USDT",
        market_type=MarketType.PERPETUAL,
        timeframe="1m",
        price="60000",
        latency_ms=1,
    )
    news = AsyncMock()
    news.name = "rss"
    news.signals.return_value = []
    positioning = AsyncMock()
    positioning.name = "binance_derivatives"
    positioning.positioning.return_value = DerivativesPositioningSnapshot(
        source="binance_derivatives",
        venue="binance",
        symbol="BTCUSDT",
        period="5m",
        current=DerivativesPositioningPoint(
            observed_at=datetime.now(UTC),
            open_interest_contracts="1000",
            funding_rate="0.0001",
        ),
    )
    return IntelligenceService(
        market_data=(market,), positioning=(positioning,), news=(news,)
    )


def _bound_proposal(
    proposal_payload: dict[str, object], plan_name: str, run_id: str, symbol: str = "BTC/USDT"
) -> TradeProposal:
    payload = dict(proposal_payload)
    payload.update(
        {
            "idempotency_key": f"provenance-{symbol.replace('/', '').lower()}-001",
            "strategy_version": "ema_rsi_atr_trend_v1",
            "execution_plan_name": plan_name,
            "execution_plan_run_id": run_id,
            "quantity": "0.005",
            "entry_price": "60000",
            "stop_loss_price": "59400",
            "take_profit_price": "61200",
            "max_loss_quote": "8",
        }
    )
    context = dict(payload["context"])
    context.update({"symbol": symbol, "timeframe": "1m", "side": "long"})
    payload["context"] = context
    pattern = dict(payload["patterns"][0])
    pattern["pattern_id"] = "ema_rsi_atr_trend_v1"
    payload["patterns"] = [pattern]
    return TradeProposal.model_validate(payload)


def _record_provenance_reads(service: TradingService, run_id: str, symbol: str) -> None:
    async def read():
        await service.market_snapshot(
            symbol, MarketType.PERPETUAL, "1m", 20, execution_plan_run_id=run_id
        )
        await service.crypto_news(
            symbol,
            datetime.now(UTC) - timedelta(minutes=10),
            20,
            execution_plan_run_id=run_id,
        )
        await service.derivatives_positioning(
            symbol, execution_plan_run_id=run_id
        )

    asyncio.run(read())
    service.pattern_context(
        "ema_rsi_atr_trend_v1",
        symbol=symbol,
        market_type="perpetual",
        side="long",
        execution_plan_run_id=run_id,
    )


def test_execution_plan_requires_capital_for_all_slots() -> None:
    payload = _plan_payload()
    payload.update({"max_trade_notional_quote": "400", "max_open_operations": 3})

    with pytest.raises(ValidationError, match=r"max_trade_notional_quote \* max_open_operations"):
        ExecutionPlanCreateRequest.model_validate(payload)


def test_execution_plan_requires_daily_loss_for_all_slots() -> None:
    payload = _plan_payload()
    payload.update(
        {
            "max_trade_notional_quote": "500",
            "risk_per_trade_quote": "20",
            "max_open_operations": 2,
        }
    )

    with pytest.raises(ValidationError, match=r"risk_per_trade_quote \* max_open_operations"):
        ExecutionPlanCreateRequest.model_validate(payload)


def test_execution_plan_crud_and_versioning(service) -> None:
    created = service.create_execution_plan(_plan_request())

    assert created.plan_id.startswith("plan_")
    assert created.symbols == ["BTCUSDT", "ETHUSDT"]
    assert service.list_execution_plans()[0].name == "EMA sample collection"

    updated_payload = _plan_payload()
    updated_payload["description"] = "Updated controlled paper-trading sample."
    updated_payload["status"] = "paused"
    updated = service.update_execution_plan(
        created.plan_id,
        ExecutionPlanUpdateRequest.model_validate(updated_payload),
    )

    assert updated.version == 2
    assert updated.status is ExecutionPlanStatus.PAUSED
    service.delete_execution_plan(created.plan_id)

    with pytest.raises(NotFoundError):
        service.get_execution_plan(created.plan_id)


def test_execution_plan_is_account_scoped_and_attached_to_operation(
    tmp_path, proposal_payload
) -> None:
    settings = Settings(db_path=tmp_path / "plans-accounts.db")
    store = SQLiteStore(settings.db_path)
    accounts = AccountManager(settings, store)
    account_a = accounts.create_account("User A")
    account_b = accounts.create_account("User B")
    market = AsyncMock()
    market.name = "binance"
    market.snapshot.return_value = MarketSnapshot(
        source="binance",
        venue="binance",
        symbol="BTC/USDT",
        market_type="perpetual",
        timeframe="1m",
        price="60000",
        latency_ms=1,
    )
    news = AsyncMock()
    news.name = "rss"
    news.signals.return_value = []
    intelligence = IntelligenceService(market_data=(market,), news=(news,))
    service = TradingService(
        settings, store, intelligence=intelligence, account_manager=accounts
    )

    with account_scope(account_a.account_id):
        plan = service.create_execution_plan(_plan_request())
        payload = dict(proposal_payload)
        payload["strategy_version"] = "ema_rsi_atr_trend_v1"
        payload["execution_plan_name"] = plan.name
        context = dict(payload["context"])
        context["symbol"] = "BTC/USDT"
        context["timeframe"] = "1m"
        payload["context"] = context
        patterns = dict(payload["patterns"][0])
        patterns["pattern_id"] = "ema_rsi_atr_trend_v1"
        payload["patterns"] = [patterns]
        run = asyncio.run(
            service.run_execution_plan(
                ExecutionPlanRunRequest(
                    plan_name=plan.name,
                    symbol="BTC/USDT",
                    duration_minutes=60,
                    target_operations=5,
                )
            )
        )
        asyncio.run(
            service.market_snapshot(
                "BTC/USDT",
                "perpetual",
                "1m",
                20,
                execution_plan_run_id=run.run_id,
            )
        )
        asyncio.run(
            service.crypto_news(
                "BTC/USDT",
                datetime.now(UTC) - timedelta(minutes=10),
                20,
                execution_plan_run_id=run.run_id,
            )
        )
        payload["execution_plan_run_id"] = run.run_id
        operation = service.create_proposal(TradeProposal.model_validate(payload))

        assert operation.account_id == account_a.account_id
        assert operation.execution_plan is not None
        assert operation.execution_plan.plan_id == plan.plan_id

    with account_scope(account_b.account_id):
        with pytest.raises(NotFoundError):
            service.get_execution_plan_by_name(plan.name)
        with pytest.raises(NotFoundError):
            service.get_execution_plan_run(run.run_id)


def test_run_execution_plan_returns_ready_preflight(tmp_path) -> None:
    market = AsyncMock()
    market.name = "binance"
    news = AsyncMock()
    news.name = "rss:cointelegraph.com"
    settings = Settings(db_path=tmp_path / "plans-run.db")
    intelligence = IntelligenceService(market_data=(market,), news=(news,))
    service = TradingService(settings, SQLiteStore(settings.db_path), intelligence)
    service.create_execution_plan(_plan_request())

    result = asyncio.run(
        service.run_execution_plan(
            ExecutionPlanRunRequest(
                plan_name="EMA sample collection",
                symbol="BTC/USDT",
                duration_minutes=60,
                target_operations=5,
            )
        )
    )

    assert result.status.value == "ready"
    assert result.selected_symbol == "BTCUSDT"
    assert result.duration_minutes == 60
    assert result.target_operations == 5
    assert result.blockers == []
    assert "slot_capacity=1000" in result.preflight
    assert "implicit_reserve=0" in result.preflight
    assert "open_notional=0" in result.preflight
    assert any(step.action.value == "agent_decision" for step in result.next_steps)


def test_plan_run_covers_all_symbols_and_records_exact_symbol_reads(
    tmp_path,
) -> None:
    settings = Settings(db_path=tmp_path / "provenance-run.db")
    service = TradingService(
        settings,
        SQLiteStore(settings.db_path),
        intelligence=_provenance_intelligence(),
    )
    plan = service.create_execution_plan(
        ExecutionPlanCreateRequest.model_validate(_provenance_plan_payload())
    )

    result = asyncio.run(
        service.run_execution_plan(
            ExecutionPlanRunRequest(
                plan_name=plan.name,
                symbol="BTCUSDT",
                duration_minutes=60,
                target_operations=2,
            )
        )
    )

    assert result.status.value == "ready"
    assert result.selected_symbol == "BTCUSDT"
    assert result.covered_symbols == ["BTCUSDT", "ETHUSDT"]
    assert result.required_reads == [
        "market_snapshot",
        "crypto_news",
        "derivatives_positioning",
        "pattern_context",
        "lessons",
    ]
    _record_provenance_reads(service, result.run_id, "ETH/USDT")

    receipt = service.get_execution_plan_run(result.run_id)
    assert receipt.status.value == "ready"
    assert {item.symbol for item in receipt.receipts} == {"ETHUSDT"}
    assert {item.read_type.value for item in receipt.receipts} == {
        "market_snapshot",
        "crypto_news",
        "derivatives_positioning",
        "pattern_context",
        "lessons",
    }


def test_plan_bound_proposal_requires_provenance_and_paper_recheck(
    tmp_path, proposal_payload: dict[str, object]
) -> None:
    settings = Settings(db_path=tmp_path / "provenance-gate.db")
    service = TradingService(
        settings,
        SQLiteStore(settings.db_path),
        intelligence=_provenance_intelligence(),
    )
    plan = service.create_execution_plan(
        ExecutionPlanCreateRequest.model_validate(_provenance_plan_payload())
    )
    run = asyncio.run(
        service.run_execution_plan(
            ExecutionPlanRunRequest(plan_name=plan.name, duration_minutes=60)
        )
    )
    candidate = _bound_proposal(proposal_payload, plan.name, run.run_id, "ETH/USDT")

    blocked = service.evaluate_risk(candidate)
    assert not blocked.allowed
    assert "execution_plan_read_market_snapshot_missing_or_stale" in blocked.reasons

    with pytest.raises(ConflictError, match="execution_plan_read_market_type_mismatch"):
        asyncio.run(
            service.market_snapshot(
                "ETH/USDT",
                MarketType.SPOT,
                "1m",
                20,
                execution_plan_run_id=run.run_id,
            )
        )

    with pytest.raises(ConflictError, match="execution_plan_read_pattern_id_mismatch"):
        service.pattern_context(
            "other_pattern",
            symbol="ETH/USDT",
            market_type="perpetual",
            side="long",
            execution_plan_run_id=run.run_id,
        )

    with pytest.raises(ConflictError, match="execution_plan_read_pattern_id_required"):
        service.lessons(symbol="ETH/USDT", execution_plan_run_id=run.run_id)

    _record_provenance_reads(service, run.run_id, "BTC/USDT")
    still_blocked = service.evaluate_risk(candidate)
    assert "symbol_not_covered_by_execution_plan_run" not in still_blocked.reasons
    assert "execution_plan_read_market_snapshot_missing_or_stale" in still_blocked.reasons

    _record_provenance_reads(service, run.run_id, "ETH/USDT")
    accepted = service.create_proposal(candidate)
    assert accepted.execution_plan is not None

    updated_payload = _provenance_plan_payload()
    updated_payload["status"] = "paused"
    service.update_execution_plan(
        plan.plan_id,
        ExecutionPlanUpdateRequest.model_validate(updated_payload),
    )
    with pytest.raises(RiskRejected) as error:
        service.execute_paper(accepted.operation_id)
    assert "execution_plan_run_plan_version_mismatch" in error.value.check.reasons


def test_unbound_legacy_proposal_remains_global_risk_only(
    service, proposal_payload: dict[str, object]
) -> None:
    proposal = TradeProposal.model_validate(proposal_payload)

    check = service.evaluate_risk(proposal)

    assert check.allowed
    assert not any(reason.startswith("execution_plan_") for reason in check.reasons)


def test_api_exposes_execution_plan_crud_and_blocked_preflight(tmp_path) -> None:
    settings = Settings(
        db_path=tmp_path / "plans-api.db",
        binance_market_data_enabled=False,
        news_rss_feeds=(),
    )
    app = create_app(settings)

    with TestClient(app) as client:
        account = client.post("/api/v1/accounts", json={"name": "Plan account"})
        headers = {"Authorization": f"Bearer {account.json()['access_token']}"}
        created = client.post(
            "/api/v1/execution-plans",
            json=_plan_payload(),
            headers=headers,
        )
        listed = client.get("/api/v1/execution-plans", headers=headers)
        run = client.post(
            "/api/v1/execution-plans/run",
            json={"plan_name": "EMA sample collection", "symbol": "BTCUSDT"},
            headers=headers,
        )
        receipt = client.get(
            f"/api/v1/execution-plan-runs/{run.json()['run_id']}",
            headers=headers,
        )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "EMA sample collection"
    assert run.status_code == 200
    assert run.json()["status"] == "blocked"
    assert "data_source_binance_public_unavailable" in run.json()["blockers"]
    assert receipt.status_code == 200
    assert receipt.json()["status"] == "blocked"
    assert receipt.json()["covered_symbols"] == ["BTCUSDT", "ETHUSDT"]
