from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from crypto_orchestrator.models import (
    ExecutionPlanCreateRequest,
    OutcomeInput,
    SignalTier,
    TradeProposal,
)


def _risk_plan_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Risk allocation plan",
        "description": "Test the allocation controls.",
        "objective": "Verify cost-aware plan risk controls in paper mode.",
        "strategy_version": "test-strategy-1",
        "pattern_id": "momentum_breakout",
        "mode": "paper",
        "capital_quote": "1000",
        "max_trade_notional_quote": "300",
        "risk_per_trade_quote": "10",
        "max_daily_loss_quote": "30",
        "minimum_net_reward_risk_ratio": "0",
        "max_open_operations": 3,
        "max_duration_minutes": 60,
        "target_operations": 10,
        "symbols": ["BTCUSDT"],
        "market_type": "perpetual",
        "timeframe": "15m",
        "allowed_sides": ["long"],
        "data_sources": ["binance_public"],
        "entry_rules": ["Use the fixture signal."],
        "exit_rules": ["Use the hard stop and target."],
        "risk_rules": ["Keep each operation inside its slot."],
        "evaluation_metrics": ["net_expectancy"],
        "steps": [
            {
                "step_id": "pstep_decision",
                "name": "Agent decision",
                "action": "agent_decision",
                "instructions": "Explain why the paper proposal is justified.",
            }
        ],
        "status": "active",
    }
    payload.update(overrides)
    return payload


def _plan_compatible_proposal(
    proposal_payload: dict[str, object], *, plan_name: str, **overrides: object
) -> TradeProposal:
    payload = dict(proposal_payload)
    payload.update(
        {
            "idempotency_key": f"{plan_name.lower().replace(' ', '-')}-proposal-001",
            "strategy_version": "test-strategy-1",
            "execution_plan_name": plan_name,
            "quantity": "0.005",
            "entry_price": "60000",
            "stop_loss_price": "59400",
            "take_profit_price": "61200",
            "max_loss_quote": "8",
        }
    )
    context = dict(payload["context"])
    context.update({"symbol": "BTC/USDT", "timeframe": "15m", "side": "long"})
    payload["context"] = context
    pattern = dict(payload["patterns"][0])
    pattern["pattern_id"] = "momentum_breakout"
    payload["patterns"] = [pattern]
    payload.update(overrides)
    return TradeProposal.model_validate(payload)


def test_risk_rejects_short_spot(proposal_payload: dict[str, object], service) -> None:
    payload = dict(proposal_payload)
    payload["idempotency_key"] = "test-spot-short-001"
    context = dict(payload["context"])
    context.update({"market_type": "spot", "side": "short"})
    payload["context"] = context
    payload["stop_loss_price"] = "60600"
    payload["take_profit_price"] = "58800"
    proposal = TradeProposal.model_validate(payload)

    check = service.evaluate_risk(proposal)

    assert not check.allowed
    assert "spot_short_requires_margin_adapter" in check.reasons


def test_risk_rejects_stale_signal(proposal_payload: dict[str, object], service) -> None:
    payload = dict(proposal_payload)
    payload["idempotency_key"] = "test-stale-signal-001"
    payload["signal_observed_at"] = (
        datetime.now(UTC) - timedelta(seconds=service.settings.max_signal_age_seconds + 1)
    ).isoformat()
    proposal = TradeProposal.model_validate(payload)

    check = service.evaluate_risk(proposal)

    assert not check.allowed
    assert "signal_is_stale" in check.reasons


def test_risk_rejects_invalid_long_stop(proposal_payload: dict[str, object], service) -> None:
    payload = dict(proposal_payload)
    payload["idempotency_key"] = "test-invalid-stop-001"
    payload["stop_loss_price"] = "60100"
    payload["max_loss_quote"] = "8"
    proposal = TradeProposal.model_validate(payload)

    check = service.evaluate_risk(proposal)

    assert not check.allowed
    assert "long_stop_must_be_below_entry" in check.reasons


def test_risk_rejects_live_when_paper_only(proposal_payload: dict[str, object], service) -> None:
    payload = dict(proposal_payload)
    payload["idempotency_key"] = "test-live-disabled-001"
    payload["mode"] = "live"
    proposal = TradeProposal.model_validate(payload)

    check = service.evaluate_risk(proposal)

    assert not check.allowed
    assert "live_trading_disabled" in check.reasons


def test_risk_rejects_loss_above_per_operation_limit(
    proposal_payload: dict[str, object], service
) -> None:
    payload = dict(proposal_payload)
    payload["idempotency_key"] = "test-operation-loss-limit"
    payload["stop_loss_price"] = "57000"
    payload["max_loss_quote"] = "100"
    proposal = TradeProposal.model_validate(payload)

    check = service.evaluate_risk(proposal)

    assert not check.allowed
    assert "operation_loss_limit_exceeded" in check.reasons


def test_forced_success_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        OutcomeInput(exit_price="60010", exit_reason="manual", forced_status="win")


def test_plan_rejects_candidate_when_account_open_notional_exceeds_capital(
    proposal_payload: dict[str, object], service
) -> None:
    plan = service.create_execution_plan(
        ExecutionPlanCreateRequest.model_validate(_risk_plan_payload())
    )

    existing_payload = dict(proposal_payload)
    existing_payload.update(
        {
            "idempotency_key": "existing-allocation-001",
            "quantity": "0.0133333333",
            "entry_price": "60000",
            "stop_loss_price": "59900",
            "take_profit_price": "60200",
            "max_loss_quote": "8",
        }
    )
    existing = service.create_proposal(TradeProposal.model_validate(existing_payload))
    service.execute_paper(existing.operation_id)

    candidate = _plan_compatible_proposal(proposal_payload, plan_name=plan.name)
    check = service.evaluate_risk(candidate)

    assert not check.allowed
    assert "execution_plan_capital_allocation_exceeded" in check.reasons


def test_plan_rejects_candidate_below_cost_aware_reward_risk_ratio(
    proposal_payload: dict[str, object], service
) -> None:
    plan = service.create_execution_plan(
        ExecutionPlanCreateRequest.model_validate(
            _risk_plan_payload(minimum_net_reward_risk_ratio="1")
        )
    )
    candidate = _plan_compatible_proposal(
        proposal_payload,
        plan_name=plan.name,
        idempotency_key="low-reward-risk-001",
        take_profit_price="60300",
    )

    check = service.evaluate_risk(candidate)

    assert not check.allowed
    assert check.estimated_net_reward_quote > 0
    assert check.estimated_reward_risk_ratio < 1
    assert "execution_plan_net_reward_risk_ratio_below_minimum" in check.reasons


def test_plan_limits_exploratory_signal_risk(
    proposal_payload: dict[str, object], service
) -> None:
    plan = service.create_execution_plan(
        ExecutionPlanCreateRequest.model_validate(
            _risk_plan_payload(
                minimum_signal_score="0.68",
                exploratory_trades_enabled=True,
                exploratory_minimum_signal_score="0.52",
                exploratory_risk_fraction="0.20",
            )
        )
    )
    payload = _plan_compatible_proposal(
        proposal_payload,
        plan_name=plan.name,
        idempotency_key="exploratory-risk-limit-001",
        signal_tier=SignalTier.EXPLORATORY,
        signal_score="0.60",
    )

    check = service.evaluate_risk(payload)

    assert not check.allowed
    assert "exploratory_risk_limit_exceeded" in check.reasons
