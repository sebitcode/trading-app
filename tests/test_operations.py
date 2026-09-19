from __future__ import annotations

from decimal import Decimal

import pytest

from crypto_orchestrator.models import OutcomeInput, PostmortemInput, TradeProposal
from crypto_orchestrator.service import ConflictError, RiskRejected


def test_paper_operation_lifecycle_and_learning(
    service, proposal_payload: dict[str, object], postmortem_payload: dict[str, object]
) -> None:
    proposal = TradeProposal.model_validate(proposal_payload)
    operation = service.create_proposal(proposal)
    opened = service.execute_paper(operation.operation_id)

    closed = service.close_operation(
        operation.operation_id,
        OutcomeInput(
            exit_price=Decimal("59400"),
            fees_quote=Decimal("0.60"),
            slippage_quote=Decimal("0.10"),
            funding_quote=Decimal("0.00"),
            exit_reason="stop_loss",
        ),
    )
    result = service.record_postmortem(
        operation.operation_id, PostmortemInput.model_validate(postmortem_payload)
    )
    context = service.pattern_context("momentum_breakout", symbol="BTC/USDT")

    assert opened.status.value == "paper_open"
    assert closed.outcome is not None
    assert closed.outcome.status.value == "loss"
    assert closed.outcome.pnl_net == Decimal("-6.70")
    assert result.postmortem is not None
    assert context.total_cases == 1
    assert context.losses == 1
    assert context.lessons[0].validation_status.value == "candidate"


def test_idempotency_returns_same_proposal(service, proposal_payload: dict[str, object]) -> None:
    proposal = TradeProposal.model_validate(proposal_payload)

    first = service.create_proposal(proposal)
    second = service.create_proposal(proposal)

    assert first.operation_id == second.operation_id
    assert len(service.list_operations()) == 1


def test_idempotency_rejects_different_payload(
    service, proposal_payload: dict[str, object]
) -> None:
    service.create_proposal(TradeProposal.model_validate(proposal_payload))
    conflicting = dict(proposal_payload)
    conflicting["entry_price"] = "60100"

    with pytest.raises(ConflictError):
        service.create_proposal(TradeProposal.model_validate(conflicting))

    assert len(service.list_operations()) == 1


def test_cannot_close_before_paper_execution(service, proposal_payload: dict[str, object]) -> None:
    operation = service.create_proposal(TradeProposal.model_validate(proposal_payload))

    with pytest.raises(ConflictError):
        service.close_operation(
            operation.operation_id,
            OutcomeInput(exit_price=Decimal("60010"), exit_reason="manual"),
        )


def test_risk_rejection_does_not_persist_operation(
    service, proposal_payload: dict[str, object]
) -> None:
    payload = dict(proposal_payload)
    payload["idempotency_key"] = "test-rejected-operation-001"
    payload["max_loss_quote"] = "1"
    proposal = TradeProposal.model_validate(payload)

    with pytest.raises(RiskRejected):
        service.create_proposal(proposal)

    assert service.list_operations() == []


def test_repeated_postmortem_is_idempotent(
    service, proposal_payload: dict[str, object], postmortem_payload: dict[str, object]
) -> None:
    operation = service.create_proposal(TradeProposal.model_validate(proposal_payload))
    service.execute_paper(operation.operation_id)
    service.close_operation(
        operation.operation_id,
        OutcomeInput(exit_price=Decimal("59400"), exit_reason="stop_loss"),
    )
    service.record_postmortem(
        operation.operation_id, PostmortemInput.model_validate(postmortem_payload)
    )
    service.record_postmortem(
        operation.operation_id, PostmortemInput.model_validate(postmortem_payload)
    )

    assert len(service.lessons(pattern_id="momentum_breakout")) == 1
