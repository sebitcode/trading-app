from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from .models import (
    DerivativesPositioningSnapshot,
    Evidence,
    ExternalSignal,
    MarketCandle,
    MarketSnapshot,
    SignalTier,
    StrategyCandidate,
    StrategyEvaluation,
    TradeSide,
    utc_now,
)

CORE_SIGNAL_SCORE = Decimal("0.68")
EXPLORATORY_SIGNAL_SCORE = Decimal("0.52")
EXPLORATORY_TRANSITION_MOMENTUM = 0.0005


@dataclass(frozen=True, slots=True)
class StrategyPolicy:
    """Risk and frequency policy used by the deterministic signal evaluator."""

    core_minimum_score: Decimal = CORE_SIGNAL_SCORE
    exploratory_enabled: bool = False
    exploratory_minimum_score: Decimal = EXPLORATORY_SIGNAL_SCORE
    exploratory_risk_fraction: Decimal = Decimal("0.20")
    minimum_net_reward_risk_ratio: Decimal = Decimal("0")
    max_trade_notional_quote: Decimal = Decimal("300")
    risk_per_trade_quote: Decimal = Decimal("5")
    fee_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0.0002")
    max_spread_bps: Decimal = Decimal("20")


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _decimal(value: float, places: int = 8) -> Decimal:
    return Decimal(str(round(value, places)))


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [
        current - previous for previous, current in zip(values, values[1:], strict=False)
    ]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:], strict=True):
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
    if average_loss == 0:
        return 100.0 if average_gain else 50.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def _atr(candles: list[MarketCandle], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None
    true_ranges: list[float] = []
    for index, candle in enumerate(candles):
        high = float(candle.high_price)
        low = float(candle.low_price)
        if index == 0:
            true_ranges.append(high - low)
            continue
        previous_close = float(candles[index - 1].close_price)
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(true_ranges[-period:]) / period


def _vwap(candles: Iterable[MarketCandle]) -> float | None:
    value = 0.0
    volume = 0.0
    for candle in candles:
        typical_price = (
            float(candle.high_price) + float(candle.low_price) + float(candle.close_price)
        ) / 3
        candle_volume = float(candle.volume)
        value += typical_price * candle_volume
        volume += candle_volume
    return value / volume if volume else None


def _closed_candles(snapshot: MarketSnapshot) -> list[MarketCandle]:
    observed_at = snapshot.observed_at.astimezone(UTC)
    return [candle for candle in snapshot.candles if candle.close_time <= observed_at]


def _spread_bps(snapshot: MarketSnapshot) -> float | None:
    if snapshot.bid_price is None or snapshot.ask_price is None:
        return None
    mid = (float(snapshot.bid_price) + float(snapshot.ask_price)) / 2
    if mid <= 0 or snapshot.ask_price < snapshot.bid_price:
        return None
    return (float(snapshot.ask_price) - float(snapshot.bid_price)) / mid * 10_000


def _technical_score(
    side: TradeSide,
    regime: str,
    price: float,
    ema_fast: float,
    ema_slow: float,
    rsi: float,
    vwap: float,
    momentum: float,
    volume_ratio: float,
    spread_bps: float | None,
) -> tuple[float, bool, list[str]]:
    is_long = side is TradeSide.LONG
    direction_ok = price > ema_fast > ema_slow if is_long else price < ema_fast < ema_slow
    momentum_ok = momentum > 0.001 if is_long else momentum < -0.001
    vwap_ok = price >= vwap if is_long else price <= vwap
    score = 0.50
    reasons: list[str] = []

    if direction_ok:
        score += 0.22
        reasons.append("ema_alignment_confirmed")
    else:
        score -= 0.18
        reasons.append("ema_alignment_conflict")
    if momentum_ok:
        score += 0.12
        reasons.append("momentum_confirmed")
    else:
        score -= 0.10
        reasons.append("momentum_conflict")
    if vwap_ok:
        score += 0.08
        reasons.append("vwap_confirmation")
    else:
        score -= 0.06
        reasons.append("vwap_conflict")
    if volume_ratio >= 1.05:
        score += 0.05
        reasons.append("volume_confirmation")
    else:
        reasons.append("volume_neutral")
    if spread_bps is None or spread_bps <= 20:
        score += 0.03
        reasons.append("spread_acceptable")
    else:
        score -= 0.12
        reasons.append("spread_too_wide")

    if regime == "range":
        reversal_ok = rsi <= 40 and price <= vwap if is_long else rsi >= 60 and price >= vwap
        if reversal_ok:
            score += 0.20
            reasons.append("range_reversal_confirmed")
        else:
            score -= 0.12
            reasons.append("range_extreme_not_confirmed")
        eligible = reversal_ok
    elif regime == "squeeze":
        eligible = (momentum_ok and vwap_ok) or (direction_ok and abs(momentum) >= 0.002)
        if eligible:
            score += 0.10
            reasons.append("squeeze_direction_confirmed")
        else:
            reasons.append("squeeze_direction_not_confirmed")
    else:
        eligible = direction_ok and momentum_ok

    if regime == "trend":
        rsi_ok = 50 <= rsi <= 72 if is_long else 28 <= rsi <= 50
        if rsi_ok:
            score += 0.10
            reasons.append("trend_rsi_confirmed")
        else:
            score -= 0.08
            reasons.append("trend_rsi_extended")
    elif regime == "squeeze":
        rsi_ok = rsi < 78 if is_long else rsi > 22
        if rsi_ok:
            score += 0.06
            reasons.append("squeeze_rsi_not_exhausted")

    return _clamp(score), eligible, reasons


def _positioning_features(
    positioning: DerivativesPositioningSnapshot,
) -> tuple[float | None, float | None, float | None, float | None, list[str]]:
    points = sorted(
        positioning.historical_points,
        key=lambda point: point.observed_at,
    )
    current = positioning.current
    oi_points = [point for point in points if point.open_interest_contracts is not None]
    oi_change: float | None = None
    if len(oi_points) >= 2 and oi_points[0].open_interest_contracts:
        oi_change = (
            float(oi_points[-1].open_interest_contracts - oi_points[0].open_interest_contracts)
            / float(oi_points[0].open_interest_contracts)
        )

    taker_flow: float | None = None
    buy = current.taker_buy_volume_value_quote or current.taker_buy_volume
    sell = current.taker_sell_volume_value_quote or current.taker_sell_volume
    if buy is not None and sell is not None and buy + sell > 0:
        taker_flow = float((buy - sell) / (buy + sell))

    long_short_ratio = current.global_long_short_account_ratio
    if long_short_ratio is None:
        long_short_ratio = current.top_trader_long_short_account_ratio
    ratio = float(long_short_ratio) if long_short_ratio is not None else None
    funding = float(current.funding_rate) if current.funding_rate is not None else None
    warnings = ["positioning_is_aggregate_context"]
    return oi_change, taker_flow, ratio, funding, warnings


def _derivatives_score(
    side: TradeSide,
    regime: str,
    price_momentum: float,
    positioning: DerivativesPositioningSnapshot | None,
) -> tuple[float, list[str], list[str]]:
    if positioning is None:
        return 0.50, ["derivatives_data_missing"], ["derivatives_data_missing"]

    oi_change, taker_flow, ratio, funding, warnings = _positioning_features(positioning)
    is_long = side is TradeSide.LONG
    directional_price = price_momentum > 0 if is_long else price_momentum < 0
    score = 0.50
    reasons: list[str] = []

    oi_rising = oi_change is not None and oi_change >= 0.002
    oi_falling = oi_change is not None and oi_change <= -0.002
    crowd_long = ratio is not None and ratio >= 1.10
    crowd_short = ratio is not None and ratio <= 0.90
    flow_aligned = taker_flow is not None and (
        taker_flow >= 0.03 if is_long else taker_flow <= -0.03
    )
    funding_crowds_side = funding is not None and (
        funding >= 0.0002 if is_long else funding <= -0.0002
    )

    if directional_price:
        score += 0.10
        reasons.append("positioning_price_direction_aligned")
    else:
        score -= 0.08
        reasons.append("positioning_price_direction_conflict")

    if regime == "trend":
        if oi_rising:
            score += 0.16
            reasons.append("open_interest_expansion_confirms_trend")
        elif oi_falling:
            score -= 0.06
            reasons.append("open_interest_deleveraging_risk")
        else:
            reasons.append("open_interest_neutral")
        if flow_aligned:
            score += 0.14
            reasons.append("taker_flow_confirms_trend")
        elif taker_flow is not None:
            score -= 0.06
            reasons.append("taker_flow_conflict")
    elif regime == "squeeze":
        if oi_falling:
            score += 0.14
            reasons.append("open_interest_deleveraging_supports_squeeze")
        if (is_long and crowd_short) or (not is_long and crowd_long):
            score += 0.16
            reasons.append("crowding_is_opposite_to_candidate")
        if flow_aligned:
            score += 0.10
            reasons.append("taker_flow_confirms_squeeze_direction")
    else:
        if (is_long and crowd_short) or (not is_long and crowd_long):
            score += 0.14
            reasons.append("range_crowding_supports_reversal")
        if oi_change is not None and abs(oi_change) < 0.005:
            score += 0.05
            reasons.append("range_open_interest_is_stable")

    if funding is not None:
        if funding_crowds_side:
            score -= 0.08
            reasons.append("funding_crowds_candidate_side")
        elif (is_long and funding <= -0.0002) or (not is_long and funding >= 0.0002):
            score += 0.06
            reasons.append("funding_supports_candidate_side")

    return _clamp(score), reasons, warnings


_POSITIVE_NEWS_TERMS = {
    "adoption",
    "approve",
    "approval",
    "bullish",
    "etf",
    "growth",
    "inflow",
    "launch",
    "partnership",
    "record",
    "upgrade",
}
_NEGATIVE_NEWS_TERMS = {
    "ban",
    "bearish",
    "delay",
    "exploit",
    "fraud",
    "hack",
    "lawsuit",
    "liquidation",
    "outflow",
    "reject",
    "shutdown",
    "unlock",
}
_FUNDAMENTAL_TERMS = {
    "adoption",
    "approval",
    "etf",
    "fees",
    "growth",
    "hack",
    "inflow",
    "launch",
    "lawsuit",
    "partnership",
    "regulatory",
    "tvl",
    "unlock",
    "upgrade",
}


def _news_scores(signals: list[ExternalSignal]) -> tuple[float, float, list[str]]:
    if not signals:
        return 0.50, 0.50, ["news_data_missing_or_neutral", "fundamental_proxy_not_available"]

    sentiment_values: list[float] = []
    fundamental_values: list[float] = []
    for signal in signals:
        text = f"{signal.title} {signal.text}".casefold()
        positive = sum(term in text for term in _POSITIVE_NEWS_TERMS)
        negative = sum(term in text for term in _NEGATIVE_NEWS_TERMS)
        total = positive + negative
        sentiment_values.append((positive - negative) / max(total, 1))
        fundamental_positive = sum(
            term in text for term in _FUNDAMENTAL_TERMS & _POSITIVE_NEWS_TERMS
        )
        fundamental_negative = sum(
            term in text for term in _FUNDAMENTAL_TERMS & _NEGATIVE_NEWS_TERMS
        )
        fundamental_total = fundamental_positive + fundamental_negative
        fundamental_values.append(
            (fundamental_positive - fundamental_negative) / max(fundamental_total, 1)
        )
    return (
        _clamp(0.50 + sum(sentiment_values) / len(sentiment_values) * 0.50),
        _clamp(0.50 + sum(fundamental_values) / len(fundamental_values) * 0.50),
        ["news_is_deterministic_text_proxy", "fundamental_score_is_event_proxy"],
    )


def _evidence(
    symbol: str,
    timeframe: str,
    observed_at: datetime,
    feature: str,
    value: str,
    weight: float,
) -> Evidence:
    return Evidence(
        source="strategy_engine",
        reference=f"{symbol}:{timeframe}:{observed_at.isoformat()}",
        feature=feature,
        value=value,
        weight=_decimal(_clamp(weight, -1, 1)),
        observed_at=observed_at,
    )


def _build_candidate(
    snapshot: MarketSnapshot,
    side: TradeSide,
    regime: str,
    signal_score: float,
    technical_score: float,
    derivatives_score: float,
    sentiment_score: float,
    fundamental_score: float,
    technical_reasons: list[str],
    derivatives_reasons: list[str],
    news_reasons: list[str],
    atr: float,
    policy: StrategyPolicy,
    observed_at: datetime,
    exploratory_override: bool = False,
) -> StrategyCandidate | None:
    price = snapshot.price
    modeled_cost_rate = (policy.fee_rate + policy.slippage_rate) * Decimal("2")
    cost_per_unit = price * modeled_cost_rate
    volatility_stop = _decimal(atr * 1.5)
    cost_floor = price * modeled_cost_rate * Decimal("2.1")
    minimum_stop = price * Decimal("0.001")
    stop_distance = max(volatility_stop, cost_floor, minimum_stop)
    exploratory = exploratory_override or signal_score < float(policy.core_minimum_score)
    risk_budget = policy.risk_per_trade_quote * (
        policy.exploratory_risk_fraction if exploratory else Decimal("1")
    )
    quantity_by_risk = risk_budget / (stop_distance + cost_per_unit)
    quantity_by_notional = policy.max_trade_notional_quote / price
    quantity = min(quantity_by_risk, quantity_by_notional)
    if quantity <= 0:
        return None

    target_distance = max(
        stop_distance * Decimal("2"),
        policy.minimum_net_reward_risk_ratio * (stop_distance + cost_per_unit) + cost_per_unit,
    )
    if side is TradeSide.LONG:
        stop_loss = price - stop_distance
        take_profit = price + target_distance
    else:
        stop_loss = price + stop_distance
        take_profit = price - target_distance
    tier = SignalTier.EXPLORATORY if exploratory else SignalTier.CORE
    reasons = [*technical_reasons, *derivatives_reasons, *news_reasons]
    warnings = [
        "positioning_is_aggregate_context",
        "fundamental_score_requires_external_event_validation",
    ]
    evidence = [
        _evidence(
            snapshot.symbol,
            snapshot.timeframe,
            observed_at,
            "technical_score",
            f"{technical_score:.4f}",
            technical_score,
        ),
        _evidence(
            snapshot.symbol,
            snapshot.timeframe,
            observed_at,
            "derivatives_score",
            f"{derivatives_score:.4f}",
            derivatives_score,
        ),
        _evidence(
            snapshot.symbol,
            snapshot.timeframe,
            observed_at,
            "sentiment_score",
            f"{sentiment_score:.4f}",
            sentiment_score,
        ),
        _evidence(
            snapshot.symbol,
            snapshot.timeframe,
            observed_at,
            "fundamental_score",
            f"{fundamental_score:.4f}",
            fundamental_score,
        ),
        _evidence(
            snapshot.symbol,
            snapshot.timeframe,
            observed_at,
            "composite_score",
            f"{signal_score:.4f}",
            signal_score,
        ),
    ]
    return StrategyCandidate(
        symbol=snapshot.symbol,
        side=side,
        market_regime=regime,
        signal_tier=tier,
        signal_score=_decimal(signal_score),
        technical_score=_decimal(technical_score),
        derivatives_score=_decimal(derivatives_score),
        sentiment_score=_decimal(sentiment_score),
        fundamental_score=_decimal(fundamental_score),
        quantity=quantity,
        entry_price=price,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        risk_budget_quote=risk_budget,
        reasons=reasons[:20],
        warnings=warnings,
        evidence=evidence,
        observed_at=observed_at,
    )


def _exploratory_transition_confirmed(
    side: TradeSide,
    price: float,
    vwap: float,
    momentum: float,
    derivatives_score: float,
    sentiment_score: float,
    fundamental_score: float,
) -> bool:
    """Allow a reduced-risk entry before EMA alignment only with independent confirmation."""

    directional_market = (
        price >= vwap and momentum >= EXPLORATORY_TRANSITION_MOMENTUM
        if side is TradeSide.LONG
        else price <= vwap and momentum <= -EXPLORATORY_TRANSITION_MOMENTUM
    )
    derivatives_confirmed = derivatives_score >= 0.55
    event_confirmed = max(sentiment_score, fundamental_score) >= 0.55
    return directional_market and derivatives_confirmed and event_confirmed


def evaluate_market_strategy(
    snapshot: MarketSnapshot,
    positioning: DerivativesPositioningSnapshot | None,
    signals: list[ExternalSignal],
    *,
    policy: StrategyPolicy,
    observed_at: datetime | None = None,
) -> StrategyEvaluation:
    """Evaluate mirrored long/short setups without placing an order."""

    decision_time = (observed_at or utc_now()).astimezone(UTC)
    candles = _closed_candles(snapshot)
    data_quality: list[str] = []
    if positioning is None:
        data_quality.append("derivatives_data_missing")
    if not signals:
        data_quality.append("news_data_missing_or_neutral")
    if len(candles) < 30:
        return StrategyEvaluation(
            symbol=snapshot.symbol,
            market_type=snapshot.market_type,
            timeframe=snapshot.timeframe,
            price=snapshot.price,
            market_regime="insufficient_data",
            metrics={"closed_candles": str(len(candles))},
            rejection_reasons=["insufficient_closed_candles"],
            data_quality=data_quality,
            observed_at=decision_time,
        )

    closes = [float(candle.close_price) for candle in candles]
    price = float(snapshot.price)
    ema_fast = _ema(closes, 9)
    ema_slow = _ema(closes, 21)
    rsi = _rsi(closes)
    atr = _atr(candles)
    vwap = _vwap(candles)
    if None in {ema_fast, ema_slow, rsi, atr, vwap}:
        return StrategyEvaluation(
            symbol=snapshot.symbol,
            market_type=snapshot.market_type,
            timeframe=snapshot.timeframe,
            price=snapshot.price,
            market_regime="insufficient_data",
            metrics={"closed_candles": str(len(candles))},
            rejection_reasons=["indicators_unavailable"],
            data_quality=[*data_quality, "technical_indicators_unavailable"],
            observed_at=decision_time,
        )

    momentum = price / closes[-5] - 1 if closes[-5] else 0.0
    average_volume = sum(float(candle.volume) for candle in candles[-21:-1]) / 20
    volume_ratio = float(candles[-1].volume) / average_volume if average_volume else 1.0
    spread_bps = _spread_bps(snapshot)
    ema_spread_bps = abs(ema_fast - ema_slow) / price * 10_000 if price else 0.0
    atr_percent = atr / price if price else 0.0
    trend = price > ema_fast > ema_slow or price < ema_fast < ema_slow
    if trend and ema_spread_bps >= 4:
        regime = "trend"
    elif abs(momentum) >= 0.0025 and atr_percent >= 0.0015:
        regime = "squeeze"
    else:
        regime = "range"

    sentiment_base, fundamental_base, news_reasons = _news_scores(signals)
    candidates: list[StrategyCandidate] = []
    rejection_reasons: list[str] = []
    for side in (TradeSide.LONG, TradeSide.SHORT):
        technical_score, technical_eligible, technical_reasons = _technical_score(
            side,
            regime,
            price,
            ema_fast,
            ema_slow,
            rsi,
            vwap,
            momentum,
            volume_ratio,
            spread_bps,
        )
        derivatives_score, derivatives_reasons, derivatives_warnings = _derivatives_score(
            side, regime, momentum, positioning
        )
        sentiment_score = sentiment_base if side is TradeSide.LONG else 1 - sentiment_base
        fundamental_score = fundamental_base if side is TradeSide.LONG else 1 - fundamental_base
        weights = [(technical_score, 0.45), (derivatives_score, 0.30)]
        if signals:
            weights.extend(((sentiment_score, 0.15), (fundamental_score, 0.10)))
        composite_score = sum(score * weight for score, weight in weights) / sum(
            weight for _, weight in weights
        )
        min_exploratory = float(policy.exploratory_minimum_score)
        transition_confirmed = False
        if not technical_eligible:
            transition_confirmed = policy.exploratory_enabled and (
                regime in {"range", "trend"}
                and _exploratory_transition_confirmed(
                    side,
                    price,
                    vwap,
                    momentum,
                    derivatives_score,
                    sentiment_score,
                    fundamental_score,
                )
            )
            if transition_confirmed:
                technical_reasons.append("exploratory_transition_confirmed")
            else:
                rejection_reasons.append(f"{side.value}_technical_setup_not_eligible")
                continue
        if spread_bps is not None and spread_bps > float(policy.max_spread_bps):
            rejection_reasons.append("spread_above_limit")
            continue
        if composite_score < min_exploratory:
            rejection_reasons.append(f"{side.value}_score_below_exploratory_threshold")
            continue
        if composite_score < float(policy.core_minimum_score) and not policy.exploratory_enabled:
            rejection_reasons.append("exploratory_trades_disabled")
            continue
        candidate = _build_candidate(
            snapshot,
            side,
            regime,
            composite_score,
            technical_score,
            derivatives_score,
            sentiment_score,
            fundamental_score,
            technical_reasons,
            derivatives_reasons,
            news_reasons,
            atr,
            policy,
            decision_time,
            exploratory_override=transition_confirmed,
        )
        if candidate is not None:
            candidate = candidate.model_copy(
                update={"warnings": [*candidate.warnings, *derivatives_warnings]}
            )
            candidates.append(candidate)

    candidates.sort(key=lambda candidate: candidate.signal_score, reverse=True)
    return StrategyEvaluation(
        symbol=snapshot.symbol,
        market_type=snapshot.market_type,
        timeframe=snapshot.timeframe,
        price=snapshot.price,
        market_regime=regime,
        candidates=candidates[:2],
        metrics={
            "closed_candles": str(len(candles)),
            "ema9": f"{ema_fast:.8f}",
            "ema21": f"{ema_slow:.8f}",
            "rsi14": f"{rsi:.4f}",
            "atr14": f"{atr:.8f}",
            "atr_percent": f"{atr_percent:.6f}",
            "vwap": f"{vwap:.8f}",
            "momentum_5_candles": f"{momentum:.6f}",
            "volume_ratio": f"{volume_ratio:.4f}",
            "spread_bps": "unavailable" if spread_bps is None else f"{spread_bps:.4f}",
            "ema_spread_bps": f"{ema_spread_bps:.4f}",
        },
        rejection_reasons=rejection_reasons,
        data_quality=data_quality,
        observed_at=decision_time,
    )
