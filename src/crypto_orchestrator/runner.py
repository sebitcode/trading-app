from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _now() -> datetime:
    return datetime.now(UTC)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _text(value: Any, fallback: str = "") -> str:
    return fallback if value is None else str(value)


def _decode_tool_result(result: Any, tool_name: str) -> Any:
    if getattr(result, "is_error", False):
        raise RuntimeError(f"{tool_name}: MCP returned an error")
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    if structured is not None:
        return structured
    decoded_blocks: list[Any] = []
    for block in getattr(result, "content", []) or []:
        if getattr(block, "type", None) != "text":
            continue
        try:
            decoded_blocks.append(json.loads(block.text))
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{tool_name}: invalid MCP response") from exc
    if decoded_blocks:
        return decoded_blocks[0] if len(decoded_blocks) == 1 else decoded_blocks
    raise RuntimeError(f"{tool_name}: MCP returned no content")


async def _call(session: ClientSession, name: str, arguments: dict[str, Any]) -> Any:
    result = await session.call_tool(name, arguments, read_timeout_seconds=30)
    return _decode_tool_result(result, name)


def _operation_belongs_to_run(operation: dict[str, Any], run_id: str) -> bool:
    proposal = operation.get("proposal") or {}
    return operation.get("status") == "paper_open" and proposal.get(
        "execution_plan_run_id"
    ) == run_id


async def _open_operations(
    session: ClientSession, run_id: str
) -> list[dict[str, Any]]:
    operations = await _call(session, "list_operations", {"limit": 500})
    return [
        operation
        for operation in operations
        if isinstance(operation, dict) and _operation_belongs_to_run(operation, run_id)
    ]


def _costs(
    operation: dict[str, Any], exit_price: Decimal, fee_rate: Decimal, slippage_rate: Decimal
) -> tuple[Decimal, Decimal]:
    proposal = operation.get("proposal") or {}
    execution = operation.get("execution") or {}
    entry_price = _decimal(execution.get("entry_price") or proposal["entry_price"])
    quantity = _decimal(execution.get("quantity") or proposal["quantity"])
    notional = (entry_price + exit_price) * quantity
    return notional * fee_rate, notional * slippage_rate


async def _close_operation(
    session: ClientSession,
    operation: dict[str, Any],
    exit_price: Decimal,
    reason: str,
    *,
    fee_rate: Decimal,
    slippage_rate: Decimal,
) -> dict[str, Any]:
    operation_id = _text(operation.get("operation_id"))
    proposal = operation.get("proposal") or {}
    fees, slippage = _costs(operation, exit_price, fee_rate, slippage_rate)
    closed = await _call(
        session,
        "record_operation_outcome",
        {
            "operation_id": operation_id,
            "outcome": {
                "exit_price": str(exit_price),
                "fees_quote": str(fees),
                "slippage_quote": str(slippage),
                "funding_quote": "0",
                "exit_reason": reason,
                "closed_at": _now().isoformat(),
            },
        },
    )
    outcome = (closed.get("outcome") or {}) if isinstance(closed, dict) else {}
    status = _text(outcome.get("status"), "inconclusive")
    failure_category = "false_signal" if status in {"loss", "partial_loss"} else "unknown"
    await _call(
        session,
        "record_agent_postmortem",
        {
            "operation_id": operation_id,
            "postmortem": {
                "what_worked": [
                    "The deterministic strategy and server risk engine controlled "
                    "the paper operation."
                ],
                "what_failed": [] if failure_category == "unknown" else [reason],
                "failure_category": failure_category,
                "failure_explanation": (
                    f"Paper outcome {status} after {reason}; one observation cannot "
                    "validate the strategy."
                ),
                "counterfactual": (
                    "Keep the recorded evidence and do not rewrite the original decision."
                ),
                "proposed_lesson": (
                    "Keep paper-only execution, cost-aware exits, regime scoring, "
                    "and separate tier metrics."
                ),
                "confidence": "0.55",
                "pattern_verdicts": [
                    {
                        "pattern_id": (proposal.get("patterns") or [{}])[0].get(
                            "pattern_id", "regime_score_v1"
                        ),
                        "status": "uncertain",
                        "explanation": (
                            "A single paper outcome is insufficient to validate the pattern."
                        ),
                    }
                ],
            },
        },
    )
    print(
        f"CLOSED operation={operation_id} symbol="
        f"{(proposal.get('context') or {}).get('symbol')} side="
        f"{(proposal.get('context') or {}).get('side')} status={status} reason={reason}",
        flush=True,
    )
    return closed if isinstance(closed, dict) else {}


async def _record_investigation(
    session: ClientSession,
    *,
    plan_name: str,
    run_id: str | None,
    symbol: str,
    result: dict[str, Any],
    open_positions: int,
    event: str = "cycle",
) -> None:
    """Persist the runner's structured evidence without ever carrying credentials."""

    evaluation = result.get("evaluation") or {}
    candidates = evaluation.get("candidates") or []
    candidate = candidates[0] if candidates else {}
    operation = result.get("operation") or {}
    proposal = operation.get("proposal") or {}
    context = proposal.get("context") or {}
    reason = _text(result.get("reason"), "strategy_cycle_completed")
    data_quality = evaluation.get("data_quality") or []
    executed = bool(result.get("executed"))
    decision = "paper_trade" if executed else "no_trade"
    if reason in {"risk_rejected", "execution_plan_run_not_active"}:
        decision = "risk_blocked"
    if reason == "strategy_halted":
        decision = "halted"

    findings: dict[str, str] = {}
    for key in ("market_regime", "rejection_reasons", "data_quality", "metrics"):
        value = evaluation.get(key)
        if value:
            findings[key] = json.dumps(value, sort_keys=True)
    risk_check = result.get("risk_check") or {}
    risk_reasons = result.get("risk_reasons") or risk_check.get("reasons") or []
    spread = (evaluation.get("metrics") or {}).get("spread_bps")
    run_binding = {"execution_plan_run_id": run_id} if run_id else {}
    idempotency_subject = operation.get("operation_id") or (
        f"{run_id or 'local'}-{symbol}-{int(_now().timestamp())}"
    )
    investigation: dict[str, Any] = {
        "idempotency_key": f"{event}-{idempotency_subject}",
        "task_name": plan_name,
        "plan_name": plan_name,
        "agent_id": "crypto-orchestrator-strategy-runner",
        "symbol": symbol,
        "operation_id": operation.get("operation_id"),
        "phase": "simulation",
        "decision": decision,
        "summary": f"Periodic strategy investigation for {symbol}.",
        "reason": reason,
        "signal": (
            f"score={candidate.get('signal_score')} reasons={candidate.get('reasons')}"
            if candidate
            else None
        ),
        "fees_quote": str((operation.get("outcome") or {}).get("fees_quote", "0")),
        "funding_quote": str((operation.get("outcome") or {}).get("funding_quote", "0")),
        "open_positions": open_positions,
        "data_fresh": not data_quality and "stale" not in json.dumps(evaluation).lower(),
        "risk_reasons": risk_reasons,
        "findings": findings,
        **run_binding,
    }
    trade_snapshot = candidate or proposal
    if trade_snapshot:
        investigation.update(
            {
                "direction": trade_snapshot.get("side") or context.get("side"),
                "entry_price": trade_snapshot.get("entry_price"),
                "stop_loss_price": trade_snapshot.get("stop_loss_price"),
                "take_profit_price": trade_snapshot.get("take_profit_price"),
            }
        )
    if spread not in (None, "unavailable"):
        investigation["spread_bps"] = spread
    outcome = operation.get("outcome") or {}
    if outcome.get("pnl_net") is not None:
        investigation["hypothetical_pnl_quote"] = outcome["pnl_net"]

    try:
        saved = await _call(session, "record_agent_investigation", {"investigation": investigation})
    except RuntimeError as exc:
        print(f"INVESTIGATION_SAVE_FAILED symbol={symbol} reason={exc}", flush=True)
    else:
        if isinstance(saved, dict) and saved.get("saved") is not True:
            print(
                f"INVESTIGATION_SAVE_FAILED symbol={symbol} reason={saved.get('error')}",
                flush=True,
            )


async def _monitor(
    session: ClientSession,
    run_id: str,
    plan: dict[str, Any],
    operations: list[dict[str, Any]],
    *,
    max_hold_seconds: int,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    use_plan_receipt: bool = True,
) -> int:
    closed = 0
    for operation in operations:
        proposal = operation.get("proposal") or {}
        context = proposal.get("context") or {}
        symbol = _text(context.get("symbol"))
        market_arguments = {
            "symbol": symbol,
            "market_type": plan["market_type"],
            "timeframe": plan["timeframe"],
            "limit": 30,
        }
        if use_plan_receipt:
            market_arguments["execution_plan_run_id"] = run_id
        market = await _call(session, "get_market_snapshot", market_arguments)
        snapshots = market.get("snapshots") or []
        if market.get("errors") or not snapshots:
            continue
        price = _decimal(snapshots[0]["price"])
        side = _text(context.get("side"))
        stop = _decimal(proposal["stop_loss_price"])
        target = _decimal(proposal["take_profit_price"])
        reason: str | None = None
        if side == "long" and price <= stop or side == "short" and price >= stop:
            reason = "stop_loss"
        elif side == "long" and price >= target or side == "short" and price <= target:
            reason = "take_profit"
        else:
            opened_at = (operation.get("execution") or {}).get("opened_at")
            if opened_at and (_now() - _datetime(opened_at)).total_seconds() >= max_hold_seconds:
                reason = "max_holding_time"
        if reason:
            closed_operation = await _close_operation(
                session,
                operation,
                price,
                reason,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )
            await _record_investigation(
                session,
                plan_name=plan["name"],
                run_id=run_id if use_plan_receipt else None,
                symbol=symbol,
                result={
                    "executed": True,
                    "reason": f"paper_outcome:{reason}",
                    "operation": closed_operation.get("operation") or closed_operation,
                },
                open_positions=max(0, len(operations) - closed - 1),
                event="outcome",
            )
            closed += 1
    return closed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic paper strategy through MCP."
    )
    parser.add_argument("--plan", required=True, help="Active paper execution plan name")
    parser.add_argument("--account-id", default=os.getenv("DEFAULT_ACCOUNT_ID", "acct_local"))
    parser.add_argument(
        "--symbols", default="", help="Comma-separated symbols; defaults to plan symbols"
    )
    parser.add_argument("--duration-minutes", type=int, default=None)
    parser.add_argument("--target-operations", type=int, default=None)
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=None,
        help="Override the plan interval; otherwise use schedule_interval_minutes.",
    )
    parser.add_argument("--max-hold-seconds", type=int, default=900)
    parser.add_argument(
        "--leave-open",
        action="store_true",
        help="Leave open positions for the independent supervisor instead of closing them here",
    )
    return parser


async def _run(arguments: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["DEFAULT_ACCOUNT_ID"] = arguments.account_id
    settings = StdioServerParameters(
        command=sys.executable,
        args=["-m", "crypto_orchestrator.stdio"],
        cwd=root,
        env=environment,
    )
    async with stdio_client(settings) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            status = await _call(session, "get_system_status", {})
            if status.get("paper_trading_only") is not True:
                raise RuntimeError("paper_trading_only must be true")
            plan = await _call(session, "get_execution_plan", {"plan_name": arguments.plan})
            symbols = tuple(
                symbol.strip().upper().replace("/", "")
                for symbol in (
                    arguments.symbols.split(",") if arguments.symbols else plan["symbols"]
                )
                if symbol.strip()
            )
            duration_minutes = (
                arguments.duration_minutes
                if arguments.duration_minutes is not None
                else min(
                    int(plan.get("simulation_duration_minutes") or plan["max_duration_minutes"]),
                    int(plan["max_duration_minutes"]),
                )
            )
            target_operations = (
                arguments.target_operations
                if arguments.target_operations is not None
                else int(plan["target_operations"])
            )
            run = await _call(
                session,
                "run_execution_plan",
                {
                    "plan_name": arguments.plan,
                    "duration_minutes": duration_minutes,
                    "target_operations": target_operations,
                },
            )
            if run.get("status") != "ready":
                raise RuntimeError(f"plan preflight blocked: {run.get('blockers')}")
            run_id = run["run_id"]
            deadline = _datetime(run["expires_at"])
            interval_seconds = (
                arguments.interval_seconds
                if arguments.interval_seconds is not None
                else int(plan.get("schedule_interval_minutes", 60)) * 60
            )
            if interval_seconds <= 0:
                raise RuntimeError("interval must be greater than zero")
            fee_rate = _decimal(os.getenv("DEFAULT_FEE_RATE", "0.001"))
            slippage_rate = _decimal(os.getenv("DEFAULT_SLIPPAGE_RATE", "0.0002"))
            created = 0
            closed = 0
            print(
                f"RUNNER_STARTED server={initialized.server_info.name} "
                f"account={arguments.account_id} plan={arguments.plan} run_id={run_id} "
                f"expires_at={run['expires_at']} paper_only=true",
                flush=True,
            )
            while _now() < deadline and created < target_operations:
                cycle_started = time.monotonic()
                operations = await _open_operations(session, run_id)
                if not arguments.leave_open:
                    closed += await _monitor(
                        session,
                        run_id,
                        plan,
                        operations,
                        max_hold_seconds=arguments.max_hold_seconds,
                        fee_rate=fee_rate,
                        slippage_rate=slippage_rate,
                    )
                operations = await _open_operations(session, run_id)
                capacity = max(0, int(plan["max_open_operations"]) - len(operations))
                for symbol in symbols:
                    if capacity <= 0 or created >= target_operations:
                        break
                    result = await _call(
                        session,
                        "run_strategy_cycle",
                        {
                            "plan_name": arguments.plan,
                            "symbol": symbol,
                            "execution_plan_run_id": run_id,
                        },
                    )
                    current_open = len(await _open_operations(session, run_id))
                    if isinstance(result, dict):
                        await _record_investigation(
                            session,
                            plan_name=arguments.plan,
                            run_id=run_id,
                            symbol=symbol,
                            result=result,
                            open_positions=current_open,
                        )
                    if result.get("executed"):
                        created += 1
                        capacity -= 1
                        operation = result.get("operation") or {}
                        operation_proposal = operation.get("proposal") or {}
                        operation_context = operation_proposal.get("context") or {}
                        operation_side = operation_context.get("side")
                        operation_tier = operation_proposal.get("signal_tier")
                        print(
                            f"EXECUTED cycle={created} operation={operation.get('operation_id')} "
                            f"symbol={symbol} side={operation_side} tier={operation_tier}",
                            flush=True,
                        )
                    else:
                        evaluation = result.get("evaluation") or {}
                        print(
                            f"NO_TRADE symbol={symbol} reason={result.get('reason')} "
                            f"regime={evaluation.get('market_regime')} "
                            f"rejections={evaluation.get('rejection_reasons')}",
                            flush=True,
                        )
                elapsed = time.monotonic() - cycle_started
                current_open = len(await _open_operations(session, run_id))
                remaining_seconds = max(0, int((deadline - _now()).total_seconds()))
                print(
                    f"CYCLE created={created} closed={closed} open={current_open} "
                    f"elapsed={elapsed:.1f}s remaining={remaining_seconds}s",
                    flush=True,
                )
                await asyncio.sleep(max(0.5, interval_seconds - elapsed))

            remaining = await _open_operations(session, run_id)
            if not arguments.leave_open:
                closed += await _monitor(
                    session,
                    run_id,
                    plan,
                    remaining,
                    max_hold_seconds=0,
                    fee_rate=fee_rate,
                    slippage_rate=slippage_rate,
                    use_plan_receipt=False,
                )
            print(
                f"RUNNER_FINISHED run_id={run_id} created={created} closed={closed} "
                f"remaining_open={len(await _open_operations(session, run_id))} "
                f"leave_open={arguments.leave_open}",
                flush=True,
            )
    return 0


def run() -> None:
    arguments = _parser().parse_args()
    raise SystemExit(asyncio.run(_run(arguments)))


if __name__ == "__main__":
    run()
