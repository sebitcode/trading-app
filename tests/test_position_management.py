from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from crypto_orchestrator.config import Settings
from crypto_orchestrator.intelligence import IntelligenceService
from crypto_orchestrator.models import (
    DerivativesPositioningPoint,
    DerivativesPositioningSnapshot,
    Evidence,
    MarketSnapshot,
    MarketType,
    PositionExitPolicy,
    PositionMonitorStateInput,
    PositionReviewDecision,
    PositionReviewInput,
    ProfitBand,
    TakeProfitLimit,
    TradeProposal,
)
from crypto_orchestrator.service import ConflictError, TradingService
from crypto_orchestrator.store import SQLiteStore


def _intelligence() -> IntelligenceService:
    market = AsyncMock()
    market.name = "binance"
    market.snapshot.return_value = MarketSnapshot(
        source="binance",
        venue="binance",
        symbol="BTC/USDT",
        market_type=MarketType.PERPETUAL,
        timeframe="15m",
        price="60000",
        latency_ms=1,
    )
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
    news = AsyncMock()
    news.name = "rss"
    news.signals.return_value = []
    return IntelligenceService(
        market_data=(market,), positioning=(positioning,), news=(news,)
    )


def _policy() -> PositionExitPolicy:
    return PositionExitPolicy(
        stop_loss_price="59600",
        take_profit_limits=[TakeProfitLimit(label="target_one", price="61000")],
        profit_bands=[
            ProfitBand(
                label="target_stall",
                lower_price="60400",
                upper_price="60800",
                dwell_seconds=60,
            )
        ],
        max_duration_seconds=3600,
    )


def test_position_exit_policy_is_persisted_and_monitor_state_is_versioned(
    service, proposal_payload: dict[str, object]
) -> None:
    payload = dict(proposal_payload)
    payload["exit_policy"] = _policy()
    operation = service.create_proposal(TradeProposal.model_validate(payload))
    opened = service.execute_paper(operation.operation_id)
    entered_at = datetime.now(UTC) - timedelta(seconds=30)

    observed = service.record_position_observation(
        opened.operation_id,
        PositionMonitorStateInput(
            band_entered_at={"target_stall": entered_at},
            expected_version=0,
        ),
    )

    assert observed.monitor_state.version == 1
    assert observed.proposal.exit_policy == _policy()
    assert observed.monitor_state.band_entered_at["target_stall"] == entered_at

    with pytest.raises(ConflictError, match="version_conflict"):
        service.record_position_observation(
            opened.operation_id,
            PositionMonitorStateInput(
                band_entered_at={},
                expected_version=0,
            ),
        )


def test_position_review_requires_fresh_context_and_exact_adjustment_policy(
    tmp_path, proposal_payload: dict[str, object]
) -> None:
    settings = Settings(db_path=tmp_path / "position-review.db")
    service = TradingService(
        settings,
        SQLiteStore(settings.db_path),
        intelligence=_intelligence(),
    )
    operation = service.create_proposal(TradeProposal.model_validate(proposal_payload))
    service.execute_paper(operation.operation_id)

    prepared = asyncio.run(service.prepare_position_review(operation.operation_id))
    assert prepared.status.value == "prepared"
    assert set(prepared.source_data) == {"market", "derivatives", "news", "fundamental"}
    assert prepared.source_data["fundamental"]["type"] == "rss_event_proxy"

    policy = _policy()
    submitted = service.submit_position_review(
        prepared.review_id,
        PositionReviewInput(
            decision=PositionReviewDecision.ADJUST,
            news_analysis="No fresh negative event was returned by the configured RSS source.",
            fundamental_analysis="RSS event context does not indicate a fundamental change.",
            technical_analysis=(
                "Price remains above the proposed protective stop and structure is intact."
            ),
            derivatives_analysis=(
                "Aggregate funding and open interest remain usable as positioning proxies."
            ),
            evidence=[
                Evidence(
                    source="market",
                    reference="review-market",
                    feature="price",
                    value="60000",
                )
            ],
            proposed_exit_policy=policy,
        ),
    )
    updated = service.update_position_exit_policy(
        operation.operation_id, prepared.review_id, policy
    )

    assert submitted.status.value == "submitted"
    assert updated.proposal.exit_policy == policy

    with pytest.raises(ConflictError, match="does_not_match_review"):
        service.update_position_exit_policy(
            operation.operation_id,
            prepared.review_id,
            policy.model_copy(update={"stop_loss_price": Decimal("59700")}),
        )


def test_discretionary_close_requires_force_close_review(
    tmp_path, proposal_payload: dict[str, object]
) -> None:
    settings = Settings(db_path=tmp_path / "position-close-review.db")
    service = TradingService(
        settings,
        SQLiteStore(settings.db_path),
        intelligence=_intelligence(),
    )
    operation = service.create_proposal(TradeProposal.model_validate(proposal_payload))
    service.execute_paper(operation.operation_id)

    from crypto_orchestrator.models import OutcomeInput

    with pytest.raises(ConflictError, match="fresh_position_review_required"):
        service.close_operation(
            operation.operation_id,
            OutcomeInput(exit_price="60000", exit_reason="manual_close"),
        )
