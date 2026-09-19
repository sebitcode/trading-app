from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_orchestrator.config import Settings
from crypto_orchestrator.connectors import ConnectorError
from crypto_orchestrator.intelligence import IntelligenceService
from crypto_orchestrator.models import (
    DerivativesPositioningPoint,
    DerivativesPositioningSnapshot,
    ExecutionPlanCreateRequest,
    ExecutionPlanRunRequest,
    ExternalSignal,
    MarketCandle,
    MarketSnapshot,
    MarketType,
    SignalTier,
    SignalType,
    TradeSide,
)
from crypto_orchestrator.service import TradingService
from crypto_orchestrator.store import SQLiteStore
from crypto_orchestrator.strategy import StrategyPolicy, evaluate_market_strategy


def _trend_snapshot() -> MarketSnapshot:
    now = datetime.now(UTC)
    candles: list[MarketCandle] = []
    for index in range(60):
        close = Decimal("50000") + Decimal(index * 40)
        close_time = now - timedelta(minutes=60 - index)
        candles.append(
            MarketCandle(
                open_time=close_time - timedelta(minutes=1),
                close_time=close_time,
                open_price=close - Decimal("10"),
                high_price=close + Decimal("20"),
                low_price=close - Decimal("20"),
                close_price=close,
                volume=Decimal(100 + index),
                quote_volume=Decimal(100 + index) * close,
            )
        )
    price = Decimal("52400")
    return MarketSnapshot(
        source="binance",
        venue="binance",
        symbol="BTCUSDT",
        market_type=MarketType.PERPETUAL,
        timeframe="5m",
        price=price,
        bid_price=price - Decimal("1"),
        ask_price=price + Decimal("1"),
        latency_ms=1,
        candles=candles,
    )


def _positioning() -> DerivativesPositioningSnapshot:
    now = datetime.now(UTC)
    return DerivativesPositioningSnapshot(
        source="binance_derivatives",
        venue="binance_usdm",
        symbol="BTCUSDT",
        period="5m",
        current=DerivativesPositioningPoint(
            observed_at=now,
            open_interest_contracts="1100",
            funding_rate="0.00005",
            global_long_short_account_ratio="0.98",
            taker_buy_volume_value_quote="1000",
            taker_sell_volume_value_quote="300",
        ),
        historical_points=[
            DerivativesPositioningPoint(
                observed_at=now - timedelta(minutes=10),
                open_interest_contracts="1000",
            ),
            DerivativesPositioningPoint(
                observed_at=now - timedelta(minutes=5),
                open_interest_contracts="1080",
            ),
        ],
    )


def _range_transition_snapshot() -> MarketSnapshot:
    now = datetime.now(UTC)
    closes = [Decimal("50000")] * 55 + [
        Decimal("50010"),
        Decimal("50020"),
        Decimal("50030"),
        Decimal("50040"),
    ]
    candles = []
    for index, close in enumerate(closes):
        close_time = now - timedelta(minutes=60 - index)
        candles.append(
            MarketCandle(
                open_time=close_time - timedelta(minutes=1),
                close_time=close_time,
                open_price=close - Decimal("5"),
                high_price=close + Decimal("10"),
                low_price=close - Decimal("10"),
                close_price=close,
                volume=Decimal("100"),
                quote_volume=Decimal("100") * close,
            )
        )
    return MarketSnapshot(
        source="binance",
        venue="binance",
        symbol="BTCUSDT",
        market_type=MarketType.PERPETUAL,
        timeframe="5m",
        price=Decimal("50050"),
        bid_price=Decimal("50049"),
        ask_price=Decimal("50051"),
        latency_ms=1,
        candles=candles,
    )


def test_strategy_scores_long_trend_and_builds_cost_aware_prices() -> None:
    evaluation = evaluate_market_strategy(
        _trend_snapshot(),
        _positioning(),
        [
            ExternalSignal(
                signal_id="news-1",
                signal_type=SignalType.NEWS,
                source="rss",
                reference="https://news.test/1",
                title="ETF approval and adoption growth",
                text="Institutional inflow and partnership support adoption.",
                symbols=["BTC"],
            )
        ],
        policy=StrategyPolicy(
            exploratory_enabled=True,
            minimum_net_reward_risk_ratio=Decimal("1"),
        ),
    )

    assert evaluation.market_regime == "trend"
    assert evaluation.candidates
    candidate = evaluation.candidates[0]
    assert candidate.side is TradeSide.LONG
    assert candidate.signal_tier is SignalTier.CORE
    assert candidate.stop_loss_price < candidate.entry_price
    assert candidate.take_profit_price > candidate.entry_price
    assert candidate.signal_score >= Decimal("0.68")


def test_exploratory_transition_requires_nontechnical_confirmation() -> None:
    evaluation = evaluate_market_strategy(
        _range_transition_snapshot(),
        _positioning(),
        [
            ExternalSignal(
                signal_id="news-transition",
                signal_type=SignalType.NEWS,
                source="rss",
                reference="https://news.test/transition",
                title="ETF approval supports adoption growth",
                text="Institutional inflow and partnership support adoption.",
                symbols=["BTC"],
            )
        ],
        policy=StrategyPolicy(
            exploratory_enabled=True,
            exploratory_minimum_score=Decimal("0.52"),
        ),
    )

    assert evaluation.market_regime == "range"
    assert evaluation.candidates
    candidate = evaluation.candidates[0]
    assert candidate.side is TradeSide.LONG
    assert candidate.signal_tier is SignalTier.EXPLORATORY
    assert "exploratory_transition_confirmed" in candidate.reasons
    assert candidate.risk_budget_quote == Decimal("1.00")


def _strategy_plan_payload() -> dict[str, object]:
    return {
        "name": "Regime score v5",
        "description": "Paper plan using regime-aware mirrored long and short signals.",
        "objective": "Generate a controlled sample without forcing low-quality entries.",
        "strategy_version": "regime_score_v1",
        "pattern_id": "regime_score_v1",
        "mode": "paper",
        "capital_quote": "1000",
        "max_trade_notional_quote": "300",
        "risk_per_trade_quote": "10",
        "max_daily_loss_quote": "30",
        "minimum_net_reward_risk_ratio": "1",
        "minimum_signal_score": "0.68",
        "exploratory_trades_enabled": True,
        "exploratory_minimum_signal_score": "0.52",
        "exploratory_risk_fraction": "0.20",
        "max_open_operations": 1,
        "max_duration_minutes": 60,
        "target_operations": 10,
        "symbols": ["BTCUSDT"],
        "market_type": "perpetual",
        "timeframe": "5m",
        "allowed_sides": ["long", "short"],
        "data_sources": ["binance_public", "binance_derivatives", "rss"],
        "entry_rules": ["Use the regime-aware deterministic score."],
        "exit_rules": ["Use the cost-aware hard stop and target."],
        "risk_rules": ["Exploratory signals use only the configured risk fraction."],
        "evaluation_metrics": ["precision_by_tier", "net_expectancy", "trade_frequency"],
        "steps": [
            {
                "step_id": "pstep_market",
                "name": "Read market",
                "action": "read_market_snapshot",
                "instructions": "Read the closed-candle market snapshot before deciding.",
            },
            {
                "step_id": "pstep_positioning",
                "name": "Read positioning",
                "action": "read_derivatives_positioning",
                "instructions": "Read aggregate positioning as context, not individual leverage.",
            },
            {
                "step_id": "pstep_news",
                "name": "Read news",
                "action": "read_crypto_news",
                "instructions": "Read timestamped RSS signals as event context.",
            },
            {
                "step_id": "pstep_pattern",
                "name": "Read pattern",
                "action": "read_pattern_context",
                "instructions": "Read comparable outcomes for this pattern and symbol.",
            },
            {
                "step_id": "pstep_lessons",
                "name": "Read lessons",
                "action": "read_lessons",
                "instructions": "Read candidate lessons without treating them as rules.",
            },
            {
                "step_id": "pstep_decision",
                "name": "Agent decision",
                "action": "agent_decision",
                "instructions": "Select only the best eligible side and signal tier.",
            },
        ],
        "status": "active",
    }


class FakeMarketConnector:
    name = "binance"

    async def snapshot(self, symbol, market_type, timeframe, limit):
        return _trend_snapshot()


class FakePositioningConnector:
    name = "binance_derivatives"

    async def positioning(self, symbol, period, limit):
        return _positioning()


class FakeNewsConnector:
    name = "rss"

    async def signals(self, symbol, since, limit):
        return []


def test_strategy_cycle_executes_one_risk_checked_paper_operation(tmp_path) -> None:
    settings = Settings(db_path=tmp_path / "strategy-cycle.db")
    intelligence = IntelligenceService(
        market_data=(FakeMarketConnector(),),
        positioning=(FakePositioningConnector(),),
        news=(FakeNewsConnector(),),
    )
    service = TradingService(settings, SQLiteStore(settings.db_path), intelligence=intelligence)
    plan = service.create_execution_plan(
        ExecutionPlanCreateRequest.model_validate(_strategy_plan_payload())
    )
    run = asyncio.run(
        service.run_execution_plan(
            ExecutionPlanRunRequest(plan_name=plan.name, symbol="BTCUSDT")
        )
    )

    result = asyncio.run(service.run_strategy_cycle(plan.name, "BTCUSDT", run.run_id))

    assert result["executed"] is True
    operation = result["operation"]
    assert operation["status"] == "paper_open"
    assert operation["proposal"]["context"]["side"] == "long"
    assert operation["proposal"]["signal_tier"] == "core"


def test_strategy_cycle_returns_reason_when_plan_is_missing(tmp_path) -> None:
    settings = Settings(db_path=tmp_path / "strategy-empty.db")
    failing_market = FakeMarketConnector()
    failing_market.snapshot = _failing_snapshot
    intelligence = IntelligenceService(market_data=(failing_market,))
    service = TradingService(settings, SQLiteStore(settings.db_path), intelligence=intelligence)

    result = asyncio.run(service.run_strategy_cycle("missing-plan", "BTCUSDT", "epr_missing"))

    assert result == {"executed": False, "reason": "execution plan not found: missing-plan"}


async def _failing_snapshot(*args, **kwargs):
    raise ConnectorError("market unavailable", category="upstream_unavailable")
