from __future__ import annotations

from pathlib import Path

import pytest

from crypto_orchestrator.config import Settings
from crypto_orchestrator.service import TradingService
from crypto_orchestrator.store import SQLiteStore


@pytest.fixture()
def service(tmp_path: Path) -> TradingService:
    settings = Settings(db_path=tmp_path / "test.db")
    return TradingService(settings, SQLiteStore(settings.db_path))


@pytest.fixture()
def proposal_payload() -> dict[str, object]:
    return {
        "idempotency_key": "test-btc-breakout-001",
        "mode": "paper",
        "agent_id": "test-agent",
        "model_version": "test-model-1",
        "strategy_version": "test-strategy-1",
        "context": {
            "venue": "paper",
            "symbol": "BTC/USDT",
            "market_type": "perpetual",
            "timeframe": "15m",
            "side": "long",
            "market_regime": "high_volatility",
        },
        "thesis": {
            "summary": "Price reclaimed resistance with volume confirmation.",
            "evidence": [
                {
                    "source": "market",
                    "reference": "fixture-001",
                    "feature": "volume_ratio",
                    "value": "2.1",
                    "weight": "0.8",
                }
            ],
            "scope": {"asset": "BTC", "timeframe": "15m"},
            "assumptions": ["Liquidity remains available."],
            "invalidation_conditions": ["Close below reclaimed resistance."],
            "expected_horizon_minutes": 180,
            "confidence": "0.64",
        },
        "patterns": [
            {
                "pattern_id": "momentum_breakout",
                "pattern_version": 1,
                "role": "primary",
                "confidence": "0.72",
                "evidence_refs": ["fixture-001"],
            }
        ],
        "quantity": "0.01",
        "entry_price": "60000",
        "stop_loss_price": "59400",
        "take_profit_price": "61200",
        "leverage": "1",
        "max_loss_quote": "8",
    }


@pytest.fixture()
def postmortem_payload() -> dict[str, object]:
    return {
        "what_worked": ["The volume confirmation was present."],
        "what_failed": ["The breakout did not hold."],
        "failure_category": "false_signal",
        "failure_explanation": "The reclaim lacked follow-through after the initial impulse.",
        "counterfactual": "Wait for a close and retest before entering.",
        "proposed_lesson": (
            "Require a confirmed close and retest before treating this breakout as actionable."
        ),
        "confidence": "0.71",
        "pattern_verdicts": [
            {
                "pattern_id": "momentum_breakout",
                "status": "uncertain",
                "explanation": "The setup was present but not confirmed by follow-through.",
            }
        ],
    }
