from __future__ import annotations

import argparse
import asyncio
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .runner import _call, _close_operation, _datetime, _decimal


def _now() -> datetime:
    return datetime.now(UTC)


def _text(value: Any, fallback: str = "") -> str:
    return fallback if value is None else str(value)


async def _open_operations(session: ClientSession) -> list[dict[str, Any]]:
    operations = await _call(session, "list_operations", {"limit": 500})
    return [
        operation
        for operation in operations
        if isinstance(operation, dict) and operation.get("status") == "paper_open"
    ]


def _policy(operation: dict[str, Any]) -> dict[str, Any]:
    proposal = operation.get("proposal") or {}
    policy = proposal.get("exit_policy")
    if isinstance(policy, dict):
        return policy
    limits = []
    if proposal.get("take_profit_price") is not None:
        limits.append({"label": "initial_target", "price": proposal["take_profit_price"]})
    return {
        "stop_loss_price": proposal["stop_loss_price"],
        "take_profit_limits": limits,
        "profit_bands": [],
        "max_duration_seconds": 24 * 60 * 60,
    }


def _net_pnl(
    operation: dict[str, Any],
    exit_price: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
) -> Decimal:
    proposal = operation.get("proposal") or {}
    execution = operation.get("execution") or {}
    entry = _decimal(execution.get("entry_price") or proposal["entry_price"])
    quantity = _decimal(execution.get("quantity") or proposal["quantity"])
    side = _text((proposal.get("context") or {}).get("side"))
    gross = (
        (exit_price - entry) * quantity
        if side == "long"
        else (entry - exit_price) * quantity
    )
    notional = (entry + exit_price) * quantity
    return gross - notional * (fee_rate + slippage_rate)


async def _market_price(
    session: ClientSession, operation: dict[str, Any]
) -> Decimal | None:
    proposal = operation.get("proposal") or {}
    context = proposal.get("context") or {}
    response = await _call(
        session,
        "get_market_snapshot",
        {
            "symbol": context["symbol"],
            "market_type": context["market_type"],
            "timeframe": context["timeframe"],
            "limit": 1,
        },
    )
    snapshots = response.get("snapshots") or []
    if response.get("errors") or not snapshots:
        return None
    return _decimal(snapshots[0]["price"])


async def _save_band_state(
    session: ClientSession,
    operation: dict[str, Any],
    band_entered_at: dict[str, str],
) -> None:
    monitor_state = operation.get("monitor_state") or {}
    result = await _call(
        session,
        "record_position_observation",
        {
            "operation_id": operation["operation_id"],
            "observation": {
                "band_entered_at": band_entered_at,
                "expected_version": monitor_state.get("version", 0),
            },
        },
    )
    if not result.get("saved"):
        print(
            f"SUPERVISOR_STATE_ERROR operation={operation['operation_id']} "
            f"error={result.get('error')}",
            flush=True,
        )


async def _evaluate_exit(
    session: ClientSession,
    operation: dict[str, Any],
    *,
    fee_rate: Decimal,
    slippage_rate: Decimal,
) -> str | None:
    price = await _market_price(session, operation)
    if price is None:
        return None
    proposal = operation.get("proposal") or {}
    context = proposal.get("context") or {}
    side = _text(context.get("side"))
    policy = _policy(operation)
    stop = _decimal(policy["stop_loss_price"])
    if (side == "long" and price <= stop) or (side == "short" and price >= stop):
        return "stop_loss"

    for limit in policy.get("take_profit_limits") or []:
        target = _decimal(limit["price"])
        if (side == "long" and price >= target) or (side == "short" and price <= target):
            return f"take_profit_level:{_text(limit.get('label'), 'unnamed')}"

    state = operation.get("monitor_state") or {}
    entered = {
        str(label): str(timestamp)
        for label, timestamp in (state.get("band_entered_at") or {}).items()
    }
    next_entered = dict(entered)
    for band in policy.get("profit_bands") or []:
        label = _text(band.get("label"), "unnamed")
        lower = _decimal(band["lower_price"])
        upper = _decimal(band["upper_price"])
        if lower <= price <= upper:
            next_entered.setdefault(label, _now().isoformat())
            if label in entered:
                dwell = (_now() - _datetime(entered[label])).total_seconds()
                profitable = _net_pnl(operation, price, fee_rate, slippage_rate) > 0
                if dwell >= int(band["dwell_seconds"]) and (
                    not band.get("require_net_profit", True) or profitable
                ):
                    if next_entered != entered:
                        await _save_band_state(session, operation, next_entered)
                    return f"profit_band:{label}"
        else:
            next_entered.pop(label, None)
    if next_entered != entered:
        await _save_band_state(session, operation, next_entered)

    max_duration = policy.get("max_duration_seconds")
    opened_at = (operation.get("execution") or {}).get("opened_at")
    if max_duration and opened_at:
        if (_now() - _datetime(opened_at)).total_seconds() >= int(max_duration):
            return "max_duration"
    return None


async def _supervise_once(
    session: ClientSession,
    *,
    fee_rate: Decimal,
    slippage_rate: Decimal,
) -> tuple[int, int]:
    closed = 0
    operations = await _open_operations(session)
    for operation in operations:
        try:
            reason = await _evaluate_exit(
                session,
                operation,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )
            if reason is None:
                continue
            price = await _market_price(session, operation)
            if price is None:
                continue
            await _close_operation(
                session,
                operation,
                price,
                reason,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )
            closed += 1
        except Exception as exc:  # noqa: BLE001 - one position must not stop supervision
            print(
                f"SUPERVISOR_ERROR operation={operation.get('operation_id')} error={exc}",
                flush=True,
            )
    return len(operations), closed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Persistently supervise paper-position exit policies through MCP."
    )
    parser.add_argument("--account-id", default=os.getenv("DEFAULT_ACCOUNT_ID", "acct_local"))
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--duration-minutes", type=int, default=0)
    parser.add_argument("--once", action="store_true")
    return parser


async def _run(arguments: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["DEFAULT_ACCOUNT_ID"] = arguments.account_id
    settings = StdioServerParameters(
        command="uv",
        args=["run", "crypto-orchestrator-mcp"],
        cwd=root,
        env=environment,
    )
    fee_rate = _decimal(os.getenv("DEFAULT_FEE_RATE", "0.001"))
    slippage_rate = _decimal(os.getenv("DEFAULT_SLIPPAGE_RATE", "0.0002"))
    deadline = (
        time.monotonic() + arguments.duration_minutes * 60
        if arguments.duration_minutes
        else None
    )
    async with stdio_client(settings) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            status = await _call(session, "get_system_status", {})
            if status.get("paper_trading_only") is not True:
                raise RuntimeError("paper_trading_only must be true")
            print(
                f"SUPERVISOR_STARTED server={initialized.server_info.name} "
                f"account={arguments.account_id} paper_only=true detached_from_ai=true",
                flush=True,
            )
            while deadline is None or time.monotonic() < deadline:
                open_count, closed = await _supervise_once(
                    session,
                    fee_rate=fee_rate,
                    slippage_rate=slippage_rate,
                )
                print(
                    f"SUPERVISOR_CYCLE open={open_count} closed={closed}",
                    flush=True,
                )
                if arguments.once:
                    break
                await asyncio.sleep(max(1.0, arguments.poll_seconds))
            print("SUPERVISOR_STOPPED positions_left_open_until_next_supervisor=true", flush=True)
    return 0


def run() -> None:
    arguments = _parser().parse_args()
    raise SystemExit(asyncio.run(_run(arguments)))


if __name__ == "__main__":
    run()
