from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from .config import Settings
from .models import ExecutionMode, MarketType, RiskCheck, TradeProposal, TradeSide


class RiskEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(
        self,
        proposal: TradeProposal,
        *,
        open_operations: int,
        daily_loss: Decimal,
        now: datetime | None = None,
    ) -> RiskCheck:
        now = now or datetime.now(UTC)
        notional = proposal.quantity * proposal.entry_price
        stop_distance = abs(proposal.entry_price - proposal.stop_loss_price)
        modeled_cost_rate = (
            self.settings.default_fee_rate + self.settings.default_slippage_rate
        ) * Decimal("2")
        estimated_cost_buffer = notional * modeled_cost_rate
        estimated_stop_loss = stop_distance * proposal.quantity
        estimated_stop_loss += estimated_cost_buffer
        estimated_net_reward = Decimal("0")
        estimated_reward_risk_ratio = Decimal("0")
        if proposal.take_profit_price is not None:
            if proposal.context.side is TradeSide.LONG:
                reward_distance = proposal.take_profit_price - proposal.entry_price
            else:
                reward_distance = proposal.entry_price - proposal.take_profit_price
            estimated_net_reward = reward_distance * proposal.quantity - estimated_cost_buffer
            if estimated_stop_loss > 0:
                estimated_reward_risk_ratio = estimated_net_reward / estimated_stop_loss
        reasons: list[str] = []

        if proposal.mode is ExecutionMode.LIVE and self.settings.paper_trading_only:
            reasons.append("live_trading_disabled")
        if notional > self.settings.max_order_notional:
            reasons.append("order_notional_limit_exceeded")
        if estimated_stop_loss > self.settings.max_operation_loss:
            reasons.append("operation_loss_limit_exceeded")
        if open_operations >= self.settings.max_open_operations:
            reasons.append("open_operations_limit_exceeded")
        if daily_loss >= self.settings.max_daily_loss:
            reasons.append("daily_loss_limit_exceeded")
        if proposal.leverage > self.settings.max_leverage:
            reasons.append("leverage_limit_exceeded")
        if proposal.context.market_type is MarketType.SPOT:
            if proposal.leverage != Decimal("1"):
                reasons.append("spot_leverage_not_supported")
            if proposal.context.side is TradeSide.SHORT:
                reasons.append("spot_short_requires_margin_adapter")
        if proposal.context.side is TradeSide.LONG:
            if proposal.stop_loss_price >= proposal.entry_price:
                reasons.append("long_stop_must_be_below_entry")
            if (
                proposal.take_profit_price is not None
                and proposal.take_profit_price <= proposal.entry_price
            ):
                reasons.append("long_target_must_be_above_entry")
        if proposal.context.side is TradeSide.SHORT:
            if proposal.stop_loss_price <= proposal.entry_price:
                reasons.append("short_stop_must_be_above_entry")
            if (
                proposal.take_profit_price is not None
                and proposal.take_profit_price >= proposal.entry_price
            ):
                reasons.append("short_target_must_be_below_entry")
        if proposal.max_loss_quote < estimated_stop_loss:
            reasons.append("declared_max_loss_below_estimate")
        age_seconds = (now - proposal.signal_observed_at).total_seconds()
        if age_seconds > self.settings.max_signal_age_seconds:
            reasons.append("signal_is_stale")
        if age_seconds < -5:
            reasons.append("signal_timestamp_is_in_the_future")

        return RiskCheck(
            allowed=not reasons,
            reasons=reasons,
            notional_quote=notional,
            estimated_stop_loss_quote=estimated_stop_loss,
            estimated_net_reward_quote=estimated_net_reward,
            estimated_reward_risk_ratio=estimated_reward_risk_ratio,
        )
