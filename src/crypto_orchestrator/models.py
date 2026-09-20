from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_ACCOUNT_ID = "acct_local"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


def _normalize_execution_plan_symbols(values: list[str]) -> list[str]:
    normalized = [value.replace("/", "").replace(" ", "").upper() for value in values]
    if any(not value for value in normalized):
        raise ValueError("execution plan symbols cannot be blank")
    if len(normalized) != len(set(normalized)):
        raise ValueError("execution plan symbols must be unique")
    return normalized


class MarketType(StrEnum):
    SPOT = "spot"
    PERPETUAL = "perpetual"


class TradeSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class SignalTier(StrEnum):
    CORE = "core"
    EXPLORATORY = "exploratory"


class ExecutionMode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class OperationStatus(StrEnum):
    PROPOSED = "proposed"
    PAPER_OPEN = "paper_open"
    CLOSED = "closed"


class OutcomeStatus(StrEnum):
    WIN = "win"
    LOSS = "loss"
    PARTIAL_WIN = "partial_win"
    PARTIAL_LOSS = "partial_loss"
    INCONCLUSIVE = "inconclusive"
    EXECUTION_ERROR = "execution_error"
    DATA_ERROR = "data_error"
    RISK_BLOCKED = "risk_blocked"
    LIQUIDATED = "liquidated"


class PatternRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class PatternStatus(StrEnum):
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"
    MIXED = "mixed"


class WorkflowPhase(StrEnum):
    BEFORE_OPERATION = "before_operation"
    AFTER_OPERATION = "after_operation"


class WorkflowStepType(StrEnum):
    MARKET_SNAPSHOT = "market_snapshot"
    CRYPTO_NEWS = "crypto_news"
    X_POSTS = "x_posts"
    PATTERN_CONTEXT = "pattern_context"
    LESSONS = "lessons"
    AGENT_INSTRUCTION = "agent_instruction"


class WorkflowStepStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowRunStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ExecutionPlanStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ExecutionPlanAction(StrEnum):
    READ_MARKET_SNAPSHOT = "read_market_snapshot"
    READ_CRYPTO_NEWS = "read_crypto_news"
    READ_DERIVATIVES_POSITIONING = "read_derivatives_positioning"
    READ_PATTERN_CONTEXT = "read_pattern_context"
    READ_LESSONS = "read_lessons"
    RUN_BEFORE_WORKFLOW = "run_before_workflow"
    AGENT_DECISION = "agent_decision"
    PROPOSE_PAPER_TRADE = "propose_paper_trade"
    EXECUTE_PAPER_TRADE = "execute_paper_trade"
    MONITOR_PAPER_TRADE = "monitor_paper_trade"
    RECORD_OPERATION_OUTCOME = "record_operation_outcome"
    RECORD_AGENT_POSTMORTEM = "record_agent_postmortem"
    RUN_AFTER_WORKFLOW = "run_after_workflow"
    REVIEW_METRICS = "review_metrics"


class ExecutionPlanRunStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class ExecutionPlanReadType(StrEnum):
    MARKET_SNAPSHOT = "market_snapshot"
    CRYPTO_NEWS = "crypto_news"
    DERIVATIVES_POSITIONING = "derivatives_positioning"
    PATTERN_CONTEXT = "pattern_context"
    LESSONS = "lessons"


class PositionReviewDecision(StrEnum):
    HOLD = "hold"
    ADJUST = "adjust"
    FORCE_CLOSE = "force_close"


class PositionReviewStatus(StrEnum):
    PREPARED = "prepared"
    SUBMITTED = "submitted"
    EXPIRED = "expired"


class RootCauseCategory(StrEnum):
    MARKET_REGIME_MISMATCH = "market_regime_mismatch"
    FALSE_SIGNAL = "false_signal"
    INSUFFICIENT_CONFIRMATION = "insufficient_confirmation"
    DATA_QUALITY = "data_quality"
    EXECUTION = "execution"
    LATENCY = "latency"
    RISK_MANAGEMENT = "risk_management"
    EXTERNAL_EVENT = "external_event"
    AGENT_PROCESS = "agent_process"
    UNKNOWN = "unknown"


class ValidationStatus(StrEnum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    REJECTED = "rejected"
    EXPIRED = "expired"


class NotificationStatus(StrEnum):
    SENT = "sent"
    NOT_CONFIGURED = "not_configured"
    FAILED = "failed"


class SignalType(StrEnum):
    NEWS = "news"
    X_POST = "x_post"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=80)
    reference: str = Field(min_length=1, max_length=300)
    feature: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=500)
    weight: Decimal = Field(default=Decimal("1"), ge=-1, le=1)
    observed_at: datetime | None = None

    @field_validator("observed_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value else value


class StrategyCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=3, max_length=40)
    side: TradeSide
    market_regime: str = Field(min_length=1, max_length=80)
    signal_tier: SignalTier
    signal_score: Decimal = Field(ge=0, le=1)
    technical_score: Decimal = Field(ge=0, le=1)
    derivatives_score: Decimal = Field(ge=0, le=1)
    sentiment_score: Decimal = Field(ge=0, le=1)
    fundamental_score: Decimal = Field(ge=0, le=1)
    quantity: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    stop_loss_price: Decimal = Field(gt=0)
    take_profit_price: Decimal = Field(gt=0)
    risk_budget_quote: Decimal = Field(gt=0)
    reasons: list[str] = Field(min_length=1, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    evidence: list[Evidence] = Field(default_factory=list, max_length=30)
    observed_at: datetime = Field(default_factory=utc_now)

    @field_validator("observed_at")
    @classmethod
    def normalize_candidate_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class StrategyEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=3, max_length=40)
    market_type: MarketType
    timeframe: str = Field(min_length=1, max_length=20)
    price: Decimal = Field(gt=0)
    market_regime: str = Field(min_length=1, max_length=80)
    candidates: list[StrategyCandidate] = Field(default_factory=list, max_length=2)
    metrics: dict[str, str] = Field(default_factory=dict, max_length=30)
    rejection_reasons: list[str] = Field(default_factory=list, max_length=30)
    data_quality: list[str] = Field(default_factory=list, max_length=20)
    observed_at: datetime = Field(default_factory=utc_now)

    @field_validator("observed_at")
    @classmethod
    def normalize_evaluation_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class TakeProfitLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    price: Decimal = Field(gt=0)

    @field_validator("label")
    @classmethod
    def normalize_limit_label(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("take-profit limit label cannot be blank")
        return normalized


class ProfitBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    lower_price: Decimal = Field(gt=0)
    upper_price: Decimal = Field(gt=0)
    dwell_seconds: int = Field(gt=0, le=30 * 24 * 60 * 60)
    require_net_profit: bool = True

    @field_validator("label")
    @classmethod
    def normalize_band_label(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("profit-band label cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_band_range(self) -> ProfitBand:
        if self.lower_price >= self.upper_price:
            raise ValueError("profit-band lower_price must be below upper_price")
        return self


class PositionExitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_loss_price: Decimal = Field(gt=0)
    take_profit_limits: list[TakeProfitLimit] = Field(default_factory=list, max_length=10)
    profit_bands: list[ProfitBand] = Field(default_factory=list, max_length=5)
    max_duration_seconds: int | None = Field(default=24 * 60 * 60, gt=0, le=30 * 24 * 60 * 60)

    @model_validator(mode="after")
    def validate_unique_labels(self) -> PositionExitPolicy:
        labels = [limit.label for limit in self.take_profit_limits]
        labels.extend(band.label for band in self.profit_bands)
        if len(labels) != len(set(labels)):
            raise ValueError("exit policy labels must be unique")
        return self


class PositionMonitorState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=0, ge=0)
    band_entered_at: dict[str, datetime] = Field(default_factory=dict, max_length=5)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("band_entered_at")
    @classmethod
    def normalize_band_timestamps(cls, values: dict[str, datetime]) -> dict[str, datetime]:
        return {label: _utc(value) for label, value in values.items()}

    @field_validator("updated_at")
    @classmethod
    def normalize_monitor_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class PositionMonitorStateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    band_entered_at: dict[str, datetime] = Field(default_factory=dict, max_length=5)
    expected_version: int = Field(default=0, ge=0)

    @field_validator("band_entered_at")
    @classmethod
    def normalize_input_band_timestamps(cls, values: dict[str, datetime]) -> dict[str, datetime]:
        return {label: _utc(value) for label, value in values.items()}


class PositionReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: PositionReviewDecision
    news_analysis: str = Field(min_length=3, max_length=4_000)
    fundamental_analysis: str = Field(min_length=3, max_length=4_000)
    technical_analysis: str = Field(min_length=3, max_length=4_000)
    derivatives_analysis: str = Field(min_length=3, max_length=4_000)
    evidence: list[Evidence] = Field(min_length=1, max_length=30)
    proposed_exit_policy: PositionExitPolicy | None = None

    @model_validator(mode="after")
    def validate_decision_payload(self) -> PositionReviewInput:
        if self.decision is PositionReviewDecision.ADJUST and self.proposed_exit_policy is None:
            raise ValueError("adjust reviews must include proposed_exit_policy")
        return self


class PositionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(
        default_factory=lambda: f"prv_{uuid4().hex}",
        min_length=10,
        max_length=100,
        pattern=r"^prv_[a-z0-9]+$",
    )
    account_id: str = Field(
        default=DEFAULT_ACCOUNT_ID,
        min_length=6,
        max_length=80,
        pattern=r"^acct_[a-z0-9]+$",
    )
    operation_id: str = Field(min_length=8, max_length=100)
    symbol: str = Field(min_length=3, max_length=40)
    market_type: MarketType
    timeframe: str = Field(min_length=1, max_length=20)
    status: PositionReviewStatus = PositionReviewStatus.PREPARED
    prepared_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    source_data: dict[str, Any] = Field(default_factory=dict)
    data_quality: list[str] = Field(default_factory=list, max_length=30)
    decision: PositionReviewDecision | None = None
    news_analysis: str | None = Field(default=None, max_length=4_000)
    fundamental_analysis: str | None = Field(default=None, max_length=4_000)
    technical_analysis: str | None = Field(default=None, max_length=4_000)
    derivatives_analysis: str | None = Field(default=None, max_length=4_000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=30)
    proposed_exit_policy: PositionExitPolicy | None = None
    reviewed_at: datetime | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_review_symbol(cls, value: str) -> str:
        normalized = value.replace("/", "").replace(" ", "").upper()
        if not normalized:
            raise ValueError("review symbol cannot be blank")
        return normalized

    @field_validator("prepared_at", "expires_at", "reviewed_at")
    @classmethod
    def normalize_review_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value else value

    @model_validator(mode="after")
    def validate_submitted_review(self) -> PositionReview:
        if self.status is PositionReviewStatus.SUBMITTED:
            required = (
                self.decision,
                self.news_analysis,
                self.fundamental_analysis,
                self.technical_analysis,
                self.derivatives_analysis,
                self.reviewed_at,
            )
            if any(value is None for value in required) or not self.evidence:
                raise ValueError("submitted reviews require complete analysis and evidence")
        return self


class MarketContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue: str = Field(min_length=1, max_length=80)
    symbol: str = Field(min_length=3, max_length=40)
    market_type: MarketType
    timeframe: str = Field(min_length=1, max_length=20)
    side: TradeSide
    market_regime: str = Field(min_length=1, max_length=80)


class TradeThesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=10, max_length=2_000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=30)
    scope: dict[str, str] = Field(default_factory=dict, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    invalidation_conditions: list[str] = Field(min_length=1, max_length=20)
    expected_horizon_minutes: int = Field(gt=0, le=30 * 24 * 60)
    confidence: Decimal = Field(ge=0, le=1)


class PatternHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(
        min_length=3,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
    )
    pattern_version: int = Field(default=1, ge=1)
    role: PatternRole = PatternRole.PRIMARY
    confidence: Decimal = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(
        default_factory=lambda: f"step_{uuid4().hex}",
        min_length=8,
        max_length=100,
        pattern=r"^step_[a-z0-9]+$",
    )
    name: str = Field(min_length=1, max_length=120)
    type: WorkflowStepType
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=20)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("workflow step names cannot be blank")
        return normalized

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key, item in value.items():
            if not key or len(key) > 80:
                raise ValueError("workflow parameter names must be between 1 and 80 characters")
            if item is None or isinstance(item, (dict, list, tuple, set)):
                raise ValueError("workflow parameters must be scalar values")
            if isinstance(item, str) and len(item) > 4_000:
                raise ValueError("workflow parameter values must not exceed 4000 characters")
        return value

    @model_validator(mode="after")
    def validate_type_parameters(self) -> WorkflowStep:
        allowed = {
            WorkflowStepType.MARKET_SNAPSHOT: {"symbol", "market_type", "timeframe", "limit"},
            WorkflowStepType.CRYPTO_NEWS: {"symbol", "lookback_minutes", "limit"},
            WorkflowStepType.X_POSTS: {"symbol", "lookback_minutes", "limit"},
            WorkflowStepType.PATTERN_CONTEXT: {
                "pattern_id",
                "symbol",
                "market_type",
                "side",
                "limit",
            },
            WorkflowStepType.LESSONS: {
                "pattern_id",
                "symbol",
                "market_type",
                "side",
                "limit",
            },
            WorkflowStepType.AGENT_INSTRUCTION: {"instruction"},
        }[self.type]
        unsupported = sorted(set(self.parameters) - allowed)
        if unsupported:
            raise ValueError(
                f"workflow step type {self.type.value} does not support: {', '.join(unsupported)}"
            )
        required = {WorkflowStepType.AGENT_INSTRUCTION: {"instruction"}}.get(
            self.type, set()
        )
        missing = sorted(key for key in required if key not in self.parameters)
        if missing:
            raise ValueError(
                f"workflow step type {self.type.value} requires: {', '.join(missing)}"
            )
        return self


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    before_steps: list[WorkflowStep] = Field(default_factory=list, max_length=20)
    after_steps: list[WorkflowStep] = Field(default_factory=list, max_length=20)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("workflow names cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_steps(self) -> WorkflowDefinition:
        steps = [*self.before_steps, *self.after_steps]
        if not steps:
            raise ValueError("workflow must contain at least one step")
        step_ids = [step.step_id for step in steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("workflow step IDs must be unique")
        return self


class WorkflowCreateRequest(WorkflowDefinition):
    pass


class WorkflowUpdateRequest(WorkflowDefinition):
    pass


class Workflow(WorkflowDefinition):
    workflow_id: str = Field(
        min_length=8,
        max_length=100,
        pattern=r"^wf_[a-z0-9]+$",
    )
    account_id: str = Field(
        default=DEFAULT_ACCOUNT_ID,
        min_length=6,
        max_length=80,
        pattern=r"^acct_[a-z0-9]+$",
    )
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_workflow_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class ExecutionPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(
        default_factory=lambda: f"pstep_{uuid4().hex}",
        min_length=10,
        max_length=100,
        pattern=r"^pstep_[a-z0-9]+$",
    )
    name: str = Field(min_length=1, max_length=120)
    action: ExecutionPlanAction
    instructions: str = Field(min_length=10, max_length=2_000)
    required: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("execution plan step names cannot be blank")
        return normalized


class ExecutionPlanDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    objective: str = Field(min_length=10, max_length=2_000)
    strategy_version: str = Field(min_length=1, max_length=120)
    pattern_id: str = Field(
        min_length=3,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
    )
    mode: ExecutionMode = ExecutionMode.PAPER
    capital_quote: Decimal = Field(gt=0)
    max_trade_notional_quote: Decimal = Field(gt=0)
    risk_per_trade_quote: Decimal = Field(gt=0)
    max_daily_loss_quote: Decimal = Field(gt=0)
    minimum_net_reward_risk_ratio: Decimal = Field(default=Decimal("0"), ge=0)
    minimum_signal_score: Decimal | None = Field(default=None, ge=0, le=1)
    exploratory_trades_enabled: bool = False
    exploratory_minimum_signal_score: Decimal = Field(
        default=Decimal("0.52"), ge=0, le=1
    )
    exploratory_risk_fraction: Decimal = Field(default=Decimal("0.20"), gt=0, le=1)
    max_open_operations: int = Field(ge=1, le=100)
    max_duration_minutes: int = Field(gt=0, le=7 * 24 * 60)
    target_operations: int = Field(ge=1, le=10_000)
    symbols: list[str] = Field(min_length=1, max_length=20)
    market_type: MarketType
    timeframe: str = Field(min_length=1, max_length=20)
    allowed_sides: list[TradeSide] = Field(min_length=1, max_length=2)
    data_sources: list[str] = Field(min_length=1, max_length=5)
    before_workflow_name: str | None = Field(default=None, max_length=120)
    after_workflow_name: str | None = Field(default=None, max_length=120)
    entry_rules: list[str] = Field(min_length=1, max_length=30)
    exit_rules: list[str] = Field(min_length=1, max_length=30)
    risk_rules: list[str] = Field(min_length=1, max_length=30)
    evaluation_metrics: list[str] = Field(min_length=1, max_length=20)
    steps: list[ExecutionPlanStep] = Field(min_length=1, max_length=20)
    status: ExecutionPlanStatus = ExecutionPlanStatus.DRAFT

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("execution plan names cannot be blank")
        return normalized

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return _normalize_execution_plan_symbols(values)

    @field_validator("allowed_sides")
    @classmethod
    def validate_sides(cls, values: list[TradeSide]) -> list[TradeSide]:
        if len(values) != len(set(values)):
            raise ValueError("execution plan sides must be unique")
        return values

    @field_validator("data_sources")
    @classmethod
    def validate_data_sources(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().lower() for value in values]
        allowed = {"binance_public", "binance_derivatives", "rss"}
        unsupported = sorted(set(normalized) - allowed)
        if unsupported:
            raise ValueError(
                f"execution plan data sources must be one of: {', '.join(sorted(allowed))}"
            )
        if len(normalized) != len(set(normalized)):
            raise ValueError("execution plan data sources must be unique")
        return normalized

    @field_validator("before_workflow_name", "after_workflow_name")
    @classmethod
    def normalize_workflow_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def validate_safety_contract(self) -> ExecutionPlanDefinition:
        if self.mode is not ExecutionMode.PAPER:
            raise ValueError("execution plans are paper-only")
        if self.max_trade_notional_quote > self.capital_quote:
            raise ValueError("max_trade_notional_quote cannot exceed capital_quote")
        if self.risk_per_trade_quote > self.max_daily_loss_quote:
            raise ValueError("risk_per_trade_quote cannot exceed max_daily_loss_quote")
        if self.max_trade_notional_quote * self.max_open_operations > self.capital_quote:
            raise ValueError(
                "max_trade_notional_quote * max_open_operations cannot exceed capital_quote"
            )
        if self.risk_per_trade_quote * self.max_open_operations > self.max_daily_loss_quote:
            raise ValueError(
                "risk_per_trade_quote * max_open_operations cannot exceed max_daily_loss_quote"
            )
        if (
            self.minimum_signal_score is not None
            and self.exploratory_minimum_signal_score > self.minimum_signal_score
        ):
            raise ValueError(
                "exploratory_minimum_signal_score cannot exceed minimum_signal_score"
            )
        if self.exploratory_trades_enabled and self.minimum_signal_score is None:
            raise ValueError(
                "exploratory_trades_enabled requires minimum_signal_score"
            )
        if not any(step.action is ExecutionPlanAction.AGENT_DECISION for step in self.steps):
            raise ValueError("execution plan must contain an agent_decision step")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("execution plan step IDs must be unique")
        return self


class ExecutionPlanCreateRequest(ExecutionPlanDefinition):
    pass


class ExecutionPlanUpdateRequest(ExecutionPlanDefinition):
    pass


class ExecutionPlanSymbolsUpdateRequest(BaseModel):
    """The user-selected symbol universe for an existing execution plan."""

    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(min_length=1, max_length=20)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return _normalize_execution_plan_symbols(values)


class ExecutionPlan(ExecutionPlanDefinition):
    plan_id: str = Field(
        min_length=10,
        max_length=100,
        pattern=r"^plan_[a-z0-9]+$",
    )
    account_id: str = Field(
        default=DEFAULT_ACCOUNT_ID,
        min_length=6,
        max_length=80,
        pattern=r"^acct_[a-z0-9]+$",
    )
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_plan_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class WorkflowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_name: str = Field(min_length=1, max_length=120)
    phase: WorkflowPhase
    operation_id: str | None = Field(default=None, min_length=8, max_length=100)
    symbol: str | None = Field(default=None, min_length=3, max_length=40)
    pattern_id: str | None = Field(default=None, min_length=3, max_length=100)
    execution_plan_run_id: str | None = Field(
        default=None,
        min_length=10,
        max_length=100,
        pattern=r"^epr_[a-z0-9]+$",
    )

    @field_validator("workflow_name")
    @classmethod
    def normalize_workflow_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("workflow_name cannot be blank")
        return normalized


class WorkflowStepResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    name: str
    type: WorkflowStepType
    status: WorkflowStepStatus
    data: Any = None
    error: str | None = Field(default=None, max_length=500)


class WorkflowRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(
        min_length=9,
        max_length=100,
        pattern=r"^wfr_[a-z0-9]+$",
    )
    account_id: str = Field(
        min_length=6,
        max_length=80,
        pattern=r"^acct_[a-z0-9]+$",
    )
    workflow_id: str
    workflow_name: str
    workflow_version: int
    phase: WorkflowPhase
    operation_id: str | None = None
    status: WorkflowRunStatus
    executed_at: datetime = Field(default_factory=utc_now)
    steps: list[WorkflowStepResult] = Field(max_length=20)

    @field_validator("executed_at")
    @classmethod
    def normalize_run_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class ExecutionPlanRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_name: str = Field(min_length=1, max_length=120)
    symbol: str | None = Field(default=None, min_length=3, max_length=40)
    duration_minutes: int | None = Field(default=None, gt=0, le=7 * 24 * 60)
    target_operations: int | None = Field(default=None, ge=1, le=10_000)

    @field_validator("plan_name")
    @classmethod
    def normalize_plan_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("plan_name cannot be blank")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("/", "").replace(" ", "").upper()
        return normalized or None


class ExecutionPlanRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=10, max_length=100, pattern=r"^epr_[a-z0-9]+$")
    account_id: str = Field(
        min_length=6,
        max_length=80,
        pattern=r"^acct_[a-z0-9]+$",
    )
    plan: ExecutionPlan
    status: ExecutionPlanRunStatus
    started_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    selected_symbol: str
    covered_symbols: list[str] = Field(default_factory=list, max_length=20)
    required_reads: list[ExecutionPlanReadType] = Field(default_factory=list, max_length=10)
    duration_minutes: int = Field(gt=0)
    target_operations: int = Field(ge=1)
    preflight: list[str] = Field(default_factory=list, max_length=20)
    blockers: list[str] = Field(default_factory=list, max_length=20)
    next_steps: list[ExecutionPlanStep] = Field(default_factory=list, max_length=20)
    before_workflow: WorkflowRunResult | None = None

    @field_validator("started_at", "expires_at")
    @classmethod
    def normalize_run_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class ExecutionPlanReadReceipt(BaseModel):
    """Safe, account-scoped evidence that one read completed for a plan run."""

    model_config = ConfigDict(extra="forbid")

    receipt_id: str = Field(
        min_length=10,
        max_length=100,
        pattern=r"^eprr_[a-z0-9]+$",
    )
    account_id: str = Field(
        default=DEFAULT_ACCOUNT_ID,
        min_length=6,
        max_length=80,
        pattern=r"^acct_[a-z0-9]+$",
    )
    run_id: str = Field(
        min_length=10,
        max_length=100,
        pattern=r"^epr_[a-z0-9]+$",
    )
    plan_id: str = Field(
        min_length=10,
        max_length=100,
        pattern=r"^plan_[a-z0-9]+$",
    )
    plan_version: int = Field(ge=1)
    read_type: ExecutionPlanReadType
    symbol: str = Field(min_length=3, max_length=40)
    recorded_at: datetime = Field(default_factory=utc_now)

    @field_validator("symbol")
    @classmethod
    def normalize_receipt_symbol(cls, value: str) -> str:
        normalized = value.replace("/", "").replace(" ", "").upper()
        if not normalized:
            raise ValueError("receipt symbol cannot be blank")
        return normalized

    @field_validator("recorded_at")
    @classmethod
    def normalize_receipt_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class ExecutionPlanRunReceipt(BaseModel):
    """Durable, safe status and provenance view for an execution-plan run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(
        min_length=10,
        max_length=100,
        pattern=r"^epr_[a-z0-9]+$",
    )
    account_id: str = Field(
        default=DEFAULT_ACCOUNT_ID,
        min_length=6,
        max_length=80,
        pattern=r"^acct_[a-z0-9]+$",
    )
    plan_id: str = Field(
        min_length=10,
        max_length=100,
        pattern=r"^plan_[a-z0-9]+$",
    )
    plan_name: str = Field(min_length=1, max_length=120)
    plan_version: int = Field(ge=1)
    status: ExecutionPlanRunStatus
    started_at: datetime
    expires_at: datetime
    selected_symbol: str = Field(min_length=3, max_length=40)
    covered_symbols: list[str] = Field(min_length=1, max_length=20)
    duration_minutes: int = Field(gt=0)
    target_operations: int = Field(ge=1)
    required_reads: list[ExecutionPlanReadType] = Field(default_factory=list, max_length=10)
    receipts: list[ExecutionPlanReadReceipt] = Field(default_factory=list, max_length=200)

    @field_validator("plan_name")
    @classmethod
    def normalize_receipt_plan_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("receipt plan name cannot be blank")
        return normalized

    @field_validator("selected_symbol")
    @classmethod
    def normalize_selected_symbol(cls, value: str) -> str:
        normalized = value.replace("/", "").replace(" ", "").upper()
        if not normalized:
            raise ValueError("selected symbol cannot be blank")
        return normalized

    @field_validator("covered_symbols")
    @classmethod
    def normalize_covered_symbols(cls, values: list[str]) -> list[str]:
        normalized = [value.replace("/", "").replace(" ", "").upper() for value in values]
        if not normalized or any(not value for value in normalized):
            raise ValueError("covered symbols cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("covered symbols must be unique")
        return normalized

    @field_validator("started_at", "expires_at")
    @classmethod
    def normalize_receipt_run_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class TradeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=120)
    mode: ExecutionMode = ExecutionMode.PAPER
    agent_id: str = Field(min_length=1, max_length=120)
    model_version: str = Field(min_length=1, max_length=120)
    strategy_version: str = Field(min_length=1, max_length=120)
    signal_tier: SignalTier = SignalTier.CORE
    signal_score: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    workflow_name: str | None = Field(default=None, min_length=1, max_length=120)
    execution_plan_name: str | None = Field(default=None, min_length=1, max_length=120)
    execution_plan_version: int | None = Field(default=None, ge=1)
    execution_plan_run_id: str | None = Field(
        default=None,
        min_length=10,
        max_length=100,
        pattern=r"^epr_[a-z0-9]+$",
    )
    context: MarketContext
    thesis: TradeThesis
    patterns: list[PatternHypothesis] = Field(min_length=1, max_length=5)
    quantity: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    stop_loss_price: Decimal = Field(gt=0)
    take_profit_price: Decimal | None = Field(default=None, gt=0)
    exit_policy: PositionExitPolicy | None = None
    leverage: Decimal = Field(default=Decimal("1"), ge=1)
    max_loss_quote: Decimal = Field(gt=0)
    signal_observed_at: datetime = Field(default_factory=utc_now)

    @field_validator("signal_observed_at")
    @classmethod
    def normalize_signal_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @field_validator("workflow_name")
    @classmethod
    def normalize_workflow_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("workflow_name cannot be blank")
        return normalized

    @field_validator("execution_plan_name")
    @classmethod
    def normalize_execution_plan_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("execution_plan_name cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_pattern_hypotheses(self) -> TradeProposal:
        pattern_ids = [pattern.pattern_id for pattern in self.patterns]
        if len(pattern_ids) != len(set(pattern_ids)):
            raise ValueError("patterns must not contain duplicate pattern_id values")
        if not any(pattern.role is PatternRole.PRIMARY for pattern in self.patterns):
            raise ValueError("patterns must include one primary pattern")
        if self.exit_policy is None:
            limits = (
                [TakeProfitLimit(label="initial_target", price=self.take_profit_price)]
                if self.take_profit_price is not None
                else []
            )
            self.exit_policy = PositionExitPolicy(
                stop_loss_price=self.stop_loss_price,
                take_profit_limits=limits,
            )
        return self


class PaperExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    entry_price: Decimal
    quantity: Decimal
    opened_at: datetime

    @field_validator("opened_at")
    @classmethod
    def normalize_open_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class OutcomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exit_price: Decimal = Field(gt=0)
    fees_quote: Decimal = Field(default=Decimal("0"), ge=0)
    slippage_quote: Decimal = Field(default=Decimal("0"), ge=0)
    funding_quote: Decimal = Decimal("0")
    exit_reason: str = Field(min_length=1, max_length=120)
    review_id: str | None = Field(
        default=None,
        min_length=10,
        max_length=100,
        pattern=r"^prv_[a-z0-9]+$",
    )
    forced_status: OutcomeStatus | None = None
    closed_at: datetime = Field(default_factory=utc_now)

    @field_validator("forced_status")
    @classmethod
    def validate_forced_status(cls, value: OutcomeStatus | None) -> OutcomeStatus | None:
        allowed = {
            OutcomeStatus.LIQUIDATED,
            OutcomeStatus.EXECUTION_ERROR,
            OutcomeStatus.DATA_ERROR,
            OutcomeStatus.RISK_BLOCKED,
        }
        if value is not None and value not in allowed:
            raise ValueError("forced_status may only describe operational or data outcomes")
        return value

    @field_validator("closed_at")
    @classmethod
    def normalize_close_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class TradeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OutcomeStatus
    exit_price: Decimal
    pnl_gross: Decimal
    pnl_net: Decimal
    fees_quote: Decimal
    slippage_quote: Decimal
    funding_quote: Decimal
    exit_reason: str
    closed_at: datetime


class PatternVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern_id: str = Field(
        min_length=3,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$",
    )
    status: PatternStatus
    explanation: str = Field(min_length=3, max_length=1_000)


class PostmortemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    what_worked: list[str] = Field(default_factory=list, max_length=20)
    what_failed: list[str] = Field(default_factory=list, max_length=20)
    failure_category: RootCauseCategory = RootCauseCategory.UNKNOWN
    failure_explanation: str = Field(min_length=3, max_length=2_000)
    counterfactual: str = Field(min_length=3, max_length=2_000)
    proposed_lesson: str = Field(min_length=10, max_length=2_000)
    confidence: Decimal = Field(ge=0, le=1)
    pattern_verdicts: list[PatternVerdict] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_pattern_verdicts(self) -> PostmortemInput:
        pattern_ids = [verdict.pattern_id for verdict in self.pattern_verdicts]
        if len(pattern_ids) != len(set(pattern_ids)):
            raise ValueError("pattern_verdicts must not contain duplicate pattern_id values")
        return self


class Postmortem(PostmortemInput):
    recorded_at: datetime = Field(default_factory=utc_now)

    @field_validator("recorded_at")
    @classmethod
    def normalize_recorded_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class OperationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    status: OperationStatus
    created_at: datetime
    proposal: TradeProposal
    account_id: str = Field(
        default=DEFAULT_ACCOUNT_ID,
        min_length=6,
        max_length=80,
        pattern=r"^acct_[a-z0-9]+$",
    )
    workflow: Workflow | None = None
    execution_plan: ExecutionPlan | None = None
    execution: PaperExecution | None = None
    monitor_state: PositionMonitorState = Field(default_factory=PositionMonitorState)
    outcome: TradeOutcome | None = None
    postmortem: Postmortem | None = None


class Lesson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lesson_id: str
    operation_id: str
    account_id: str = Field(
        default=DEFAULT_ACCOUNT_ID,
        min_length=6,
        max_length=80,
        pattern=r"^acct_[a-z0-9]+$",
    )
    pattern_id: str
    pattern_version: int
    symbol: str
    market_type: MarketType
    side: TradeSide
    timeframe: str
    market_regime: str
    outcome_status: OutcomeStatus
    lesson: str
    failure_category: RootCauseCategory
    pattern_status: PatternStatus
    confidence: Decimal
    validation_status: ValidationStatus = ValidationStatus.CANDIDATE
    evidence_count: int = 1
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def normalize_lesson_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class RiskCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    notional_quote: Decimal
    estimated_stop_loss_quote: Decimal
    estimated_net_reward_quote: Decimal = Decimal("0")
    estimated_reward_risk_ratio: Decimal = Decimal("0")


class StrategyCycleRecord(BaseModel):
    """Durable decision evidence for one strategy-cycle attempt."""

    model_config = ConfigDict(extra="forbid")

    cycle_id: str = Field(
        min_length=10,
        max_length=100,
        pattern=r"^sc_[a-z0-9]+$",
    )
    account_id: str = Field(
        default=DEFAULT_ACCOUNT_ID,
        min_length=6,
        max_length=80,
        pattern=r"^acct_[a-z0-9]+$",
    )
    plan_name: str = Field(min_length=1, max_length=120)
    plan_version: int | None = Field(default=None, ge=1)
    symbol: str = Field(min_length=3, max_length=40)
    execution_plan_run_id: str | None = Field(
        default=None,
        min_length=10,
        max_length=100,
        pattern=r"^epr_[a-z0-9]+$",
    )
    executed: bool
    reason: str = Field(min_length=1, max_length=240)
    operation_id: str | None = Field(default=None, min_length=8, max_length=100)
    evaluation: StrategyEvaluation | None = None
    risk_check: RiskCheck | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def normalize_cycle_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @field_validator("plan_name")
    @classmethod
    def normalize_cycle_plan_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("cycle plan name cannot be blank")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_cycle_symbol(cls, value: str) -> str:
        normalized = value.replace("/", "").replace(" ", "").upper()
        if not normalized:
            raise ValueError("cycle symbol cannot be blank")
        return normalized


class PatternContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern_id: str
    symbol: str | None
    market_type: MarketType | None
    side: TradeSide | None
    total_cases: int
    wins: int
    losses: int
    net_pnl_quote: Decimal
    lessons: list[Lesson]
    disclaimer: str = "Candidate lessons are observations, not trading rules."


class DataSourceError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class MarketCandle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_time: datetime
    close_time: datetime
    open_price: Decimal = Field(gt=0)
    high_price: Decimal = Field(gt=0)
    low_price: Decimal = Field(gt=0)
    close_price: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    quote_volume: Decimal = Field(ge=0)

    @field_validator("open_time", "close_time")
    @classmethod
    def normalize_candle_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=80)
    venue: str = Field(min_length=1, max_length=80)
    symbol: str = Field(min_length=3, max_length=40)
    market_type: MarketType
    timeframe: str = Field(min_length=1, max_length=20)
    price: Decimal = Field(gt=0)
    bid_price: Decimal | None = Field(default=None, gt=0)
    ask_price: Decimal | None = Field(default=None, gt=0)
    change_24h_percent: Decimal | None = None
    volume_24h: Decimal | None = Field(default=None, ge=0)
    observed_at: datetime = Field(default_factory=utc_now)
    latency_ms: int = Field(ge=0)
    candles: list[MarketCandle] = Field(default_factory=list, max_length=500)

    @field_validator("observed_at")
    @classmethod
    def normalize_snapshot_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class MarketDataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_at: datetime = Field(default_factory=utc_now)
    symbol: str = Field(min_length=3, max_length=40)
    market_type: MarketType
    timeframe: str = Field(min_length=1, max_length=20)
    snapshots: list[MarketSnapshot] = Field(default_factory=list, max_length=20)
    errors: list[DataSourceError] = Field(default_factory=list, max_length=20)

    @field_validator("requested_at")
    @classmethod
    def normalize_requested_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


DERIVATIVES_POSITIONING_DISCLAIMER = (
    "Read-only public aggregate positioning proxies from Binance. Public data cannot "
    "identify individual accounts, their exact leverage, or individual positions; "
    "this context is not a trading command."
)


class DerivativesPositioningPoint(BaseModel):
    """One timestamped aggregate derivatives-positioning observation."""

    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    open_interest_contracts: Decimal | None = Field(default=None, ge=0)
    open_interest_value_quote: Decimal | None = Field(default=None, ge=0)
    mark_price: Decimal | None = Field(default=None, gt=0)
    index_price: Decimal | None = Field(default=None, gt=0)
    funding_rate: Decimal | None = None
    global_long_short_account_ratio: Decimal | None = Field(default=None, ge=0)
    global_long_account_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    global_short_account_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    top_trader_long_short_account_ratio: Decimal | None = Field(default=None, ge=0)
    top_trader_long_account_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    top_trader_short_account_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    top_trader_long_short_position_ratio: Decimal | None = Field(default=None, ge=0)
    top_trader_long_position_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    top_trader_short_position_ratio: Decimal | None = Field(default=None, ge=0, le=1)
    taker_buy_volume: Decimal | None = Field(default=None, ge=0)
    taker_sell_volume: Decimal | None = Field(default=None, ge=0)
    taker_buy_volume_value_quote: Decimal | None = Field(default=None, ge=0)
    taker_sell_volume_value_quote: Decimal | None = Field(default=None, ge=0)
    taker_buy_sell_volume_ratio: Decimal | None = Field(default=None, ge=0)

    @field_validator("observed_at")
    @classmethod
    def normalize_positioning_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class DerivativesPositioningSnapshot(BaseModel):
    """Current aggregate positioning plus a bounded historical trend series."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=80)
    venue: str = Field(min_length=1, max_length=80)
    symbol: str = Field(min_length=3, max_length=40)
    contract_type: str = Field(default="PERPETUAL", min_length=1, max_length=40)
    period: str = Field(min_length=1, max_length=20)
    observed_at: datetime = Field(default_factory=utc_now)
    current: DerivativesPositioningPoint
    historical_points: list[DerivativesPositioningPoint] = Field(
        default_factory=list, max_length=500
    )
    source_errors: list[DataSourceError] = Field(default_factory=list, max_length=20)
    disclaimer: str = Field(
        default=DERIVATIVES_POSITIONING_DISCLAIMER, min_length=1, max_length=1_000
    )

    @field_validator("observed_at")
    @classmethod
    def normalize_positioning_snapshot_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class DerivativesPositioningResponse(BaseModel):
    """Account-independent positioning context returned by intelligence connectors."""

    model_config = ConfigDict(extra="forbid")

    requested_at: datetime = Field(default_factory=utc_now)
    symbol: str = Field(min_length=3, max_length=40)
    contract_type: str = Field(default="PERPETUAL", min_length=1, max_length=40)
    period: str = Field(min_length=1, max_length=20)
    snapshots: list[DerivativesPositioningSnapshot] = Field(default_factory=list, max_length=20)
    errors: list[DataSourceError] = Field(default_factory=list, max_length=20)
    disclaimer: str = Field(
        default=DERIVATIVES_POSITIONING_DISCLAIMER, min_length=1, max_length=1_000
    )

    @field_validator("requested_at")
    @classmethod
    def normalize_positioning_response_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class ExternalSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(min_length=1, max_length=160)
    signal_type: SignalType
    source: str = Field(min_length=1, max_length=80)
    reference: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=5_000)
    url: str | None = Field(default=None, max_length=2_000)
    author: str | None = Field(default=None, max_length=160)
    symbols: list[str] = Field(default_factory=list, max_length=20)
    published_at: datetime | None = None
    observed_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("published_at", "observed_at")
    @classmethod
    def normalize_signal_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value else value


class SignalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_symbol: str | None = Field(default=None, min_length=3, max_length=40)
    since: datetime
    signals: list[ExternalSignal] = Field(default_factory=list, max_length=500)
    errors: list[DataSourceError] = Field(default_factory=list, max_length=20)

    @field_validator("since")
    @classmethod
    def normalize_since_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(
        min_length=6,
        max_length=80,
        pattern=r"^acct_[a-z0-9]+$",
    )
    name: str = Field(min_length=1, max_length=120)
    created_at: datetime = Field(default_factory=utc_now)
    active: bool = True

    @field_validator("created_at")
    @classmethod
    def normalize_account_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class AccountCreated(Account):
    access_token: str = Field(min_length=20, max_length=200)
    token_type: str = "bearer"


class AccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class CredentialPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, str] = Field(min_length=1, max_length=20)

    @field_validator("values")
    @classmethod
    def validate_credential_values(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not key or len(key) > 80:
                raise ValueError("credential names must be between 1 and 80 characters")
            if len(item) > 4_000:
                raise ValueError("credential values must not exceed 4000 characters")
        return value


class CredentialMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=80)
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def normalize_credential_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class NotificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "telegram"
    status: NotificationStatus
    delivered_chats: int = Field(ge=0)
    error: str | None = Field(default=None, max_length=80)


def jsonable(value: Any) -> Any:
    """Return JSON-safe data for APIs and MCP structured output."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value
