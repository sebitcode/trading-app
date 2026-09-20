from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import RLock
from typing import Any
from uuid import uuid4

from .account_context import get_current_account_id
from .accounts import AccountManager
from .config import Settings
from .intelligence import IntelligenceService, build_default_intelligence
from .models import (
    DEFAULT_ACCOUNT_ID,
    DerivativesPositioningResponse,
    ExecutionMode,
    ExecutionPlan,
    ExecutionPlanAction,
    ExecutionPlanCreateRequest,
    ExecutionPlanReadReceipt,
    ExecutionPlanReadType,
    ExecutionPlanRunReceipt,
    ExecutionPlanRunRequest,
    ExecutionPlanRunResult,
    ExecutionPlanRunStatus,
    ExecutionPlanStatus,
    ExecutionPlanSymbolsUpdateRequest,
    ExecutionPlanUpdateRequest,
    Lesson,
    MarketDataResponse,
    MarketType,
    OperationRecord,
    OperationStatus,
    OutcomeInput,
    OutcomeStatus,
    PaperExecution,
    PatternContext,
    PatternHypothesis,
    PatternStatus,
    PositionExitPolicy,
    PositionMonitorState,
    PositionMonitorStateInput,
    PositionReview,
    PositionReviewDecision,
    PositionReviewInput,
    PositionReviewStatus,
    Postmortem,
    PostmortemInput,
    ProfitBand,
    RiskCheck,
    SignalResponse,
    SignalTier,
    StrategyCycleRecord,
    StrategyEvaluation,
    TakeProfitLimit,
    TradeOutcome,
    TradeProposal,
    TradeSide,
    Workflow,
    WorkflowCreateRequest,
    WorkflowPhase,
    WorkflowRunRequest,
    WorkflowRunResult,
    WorkflowRunStatus,
    WorkflowStep,
    WorkflowStepResult,
    WorkflowStepStatus,
    WorkflowStepType,
    WorkflowUpdateRequest,
    jsonable,
    utc_now,
)
from .notifications import NotificationEvent, NotificationService
from .risk import RiskEngine
from .store import SQLiteStore
from .strategy import StrategyPolicy, evaluate_market_strategy


class DomainError(Exception):
    """Base error for expected domain rejections."""


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class RiskRejected(DomainError):
    def __init__(self, check: RiskCheck) -> None:
        self.check = check
        super().__init__("trade proposal rejected by risk engine")


_READ_TYPE_BY_ACTION = {
    ExecutionPlanAction.READ_MARKET_SNAPSHOT: ExecutionPlanReadType.MARKET_SNAPSHOT,
    ExecutionPlanAction.READ_CRYPTO_NEWS: ExecutionPlanReadType.CRYPTO_NEWS,
    ExecutionPlanAction.READ_DERIVATIVES_POSITIONING: (
        ExecutionPlanReadType.DERIVATIVES_POSITIONING
    ),
    ExecutionPlanAction.READ_PATTERN_CONTEXT: ExecutionPlanReadType.PATTERN_CONTEXT,
    ExecutionPlanAction.READ_LESSONS: ExecutionPlanReadType.LESSONS,
}


class TradingService:
    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore | None = None,
        intelligence: IntelligenceService | None = None,
        account_manager: AccountManager | None = None,
        default_account_id: str | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or SQLiteStore(settings.db_path)
        self.risk = RiskEngine(settings)
        self.intelligence = intelligence or build_default_intelligence(settings)
        self._custom_intelligence = intelligence is not None
        self.account_manager = account_manager
        self.default_account_id = default_account_id or settings.default_account_id
        self.notification_service = notification_service
        self._lock = RLock()

    def attach_account_manager(
        self, account_manager: AccountManager, default_account_id: str | None = None
    ) -> None:
        """Attach the account boundary used by HTTP and MCP transports."""

        self.account_manager = account_manager
        if default_account_id:
            self.default_account_id = default_account_id

    def attach_notification_service(self, notification_service: NotificationService) -> None:
        """Attach account-scoped outbound notifications used by the transports."""

        self.notification_service = notification_service

    def create_workflow(self, payload: WorkflowCreateRequest) -> Workflow:
        account_id = self._account_id()
        if self.store.get_workflow_by_name(payload.name, account_id=account_id):
            raise ConflictError(f"workflow name already exists: {payload.name}")
        now = utc_now()
        workflow = Workflow(
            workflow_id=f"wf_{uuid4().hex}",
            account_id=account_id,
            version=1,
            created_at=now,
            updated_at=now,
            **payload.model_dump(),
        )
        self.store.save_workflow(workflow)
        return workflow

    def get_workflow(self, workflow_id: str) -> Workflow:
        workflow = self.store.get_workflow(workflow_id, account_id=self._account_id())
        if not workflow:
            raise NotFoundError(f"workflow not found: {workflow_id}")
        return workflow

    def get_workflow_by_name(self, name: str) -> Workflow:
        workflow = self.store.get_workflow_by_name(name, account_id=self._account_id())
        if not workflow:
            raise NotFoundError(f"workflow not found: {name}")
        return workflow

    def list_workflows(self, limit: int = 100) -> list[Workflow]:
        return self.store.list_workflows(account_id=self._account_id())[: max(1, min(limit, 100))]

    def update_workflow(
        self, workflow_id: str, payload: WorkflowUpdateRequest
    ) -> Workflow:
        current = self.get_workflow(workflow_id)
        duplicate = self.store.get_workflow_by_name(
            payload.name, account_id=self._account_id()
        )
        if duplicate and duplicate.workflow_id != workflow_id:
            raise ConflictError(f"workflow name already exists: {payload.name}")
        workflow = Workflow(
            workflow_id=current.workflow_id,
            account_id=current.account_id,
            version=current.version + 1,
            created_at=current.created_at,
            updated_at=utc_now(),
            **payload.model_dump(),
        )
        self.store.save_workflow(workflow)
        return workflow

    def delete_workflow(self, workflow_id: str) -> None:
        if not self.store.delete_workflow(workflow_id, account_id=self._account_id()):
            raise NotFoundError(f"workflow not found: {workflow_id}")

    def create_execution_plan(self, payload: ExecutionPlanCreateRequest) -> ExecutionPlan:
        account_id = self._account_id()
        if self.store.get_execution_plan_by_name(payload.name, account_id=account_id):
            raise ConflictError(f"execution plan name already exists: {payload.name}")
        self._validate_plan_workflow_references(payload, account_id)
        now = utc_now()
        plan = ExecutionPlan(
            plan_id=f"plan_{uuid4().hex}",
            account_id=account_id,
            version=1,
            created_at=now,
            updated_at=now,
            **payload.model_dump(),
        )
        self.store.save_execution_plan(plan)
        return plan

    def get_execution_plan(self, plan_id: str) -> ExecutionPlan:
        plan = self.store.get_execution_plan(plan_id, account_id=self._account_id())
        if not plan:
            raise NotFoundError(f"execution plan not found: {plan_id}")
        return plan

    def get_execution_plan_by_name(self, name: str) -> ExecutionPlan:
        plan = self.store.get_execution_plan_by_name(name, account_id=self._account_id())
        if not plan:
            raise NotFoundError(f"execution plan not found: {name}")
        return plan

    def list_execution_plans(self, limit: int = 100) -> list[ExecutionPlan]:
        return self.store.list_execution_plans(self._account_id())[: max(1, min(limit, 100))]

    def get_execution_plan_run(self, run_id: str) -> ExecutionPlanRunReceipt:
        run = self.store.get_execution_plan_run(run_id, account_id=self._account_id())
        if not run:
            raise NotFoundError(f"execution plan run not found: {run_id}")
        return run

    def update_execution_plan(
        self, plan_id: str, payload: ExecutionPlanUpdateRequest
    ) -> ExecutionPlan:
        current = self.get_execution_plan(plan_id)
        duplicate = self.store.get_execution_plan_by_name(
            payload.name, account_id=self._account_id()
        )
        if duplicate and duplicate.plan_id != plan_id:
            raise ConflictError(f"execution plan name already exists: {payload.name}")
        self._validate_plan_workflow_references(payload, self._account_id())
        plan = ExecutionPlan(
            plan_id=current.plan_id,
            account_id=current.account_id,
            version=current.version + 1,
            created_at=current.created_at,
            updated_at=utc_now(),
            **payload.model_dump(),
        )
        self.store.save_execution_plan(plan)
        return plan

    def update_execution_plan_symbols(
        self, plan_name: str, symbols: list[str]
    ) -> ExecutionPlan:
        """Update only the user-selected symbol universe and create a new plan version."""

        current = self.get_execution_plan_by_name(plan_name)
        selection = ExecutionPlanSymbolsUpdateRequest.model_validate({"symbols": symbols})
        payload = ExecutionPlanUpdateRequest.model_validate(
            {
                **current.model_dump(
                    exclude={"plan_id", "account_id", "version", "created_at", "updated_at"}
                ),
                "symbols": selection.symbols,
            }
        )
        return self.update_execution_plan(current.plan_id, payload)

    def delete_execution_plan(self, plan_id: str) -> None:
        if not self.store.delete_execution_plan(plan_id, account_id=self._account_id()):
            raise NotFoundError(f"execution plan not found: {plan_id}")

    def _validate_plan_workflow_references(
        self, payload: ExecutionPlanCreateRequest | ExecutionPlanUpdateRequest, account_id: str
    ) -> None:
        for workflow_name in (payload.before_workflow_name, payload.after_workflow_name):
            if workflow_name and not self.store.get_workflow_by_name(
                workflow_name, account_id=account_id
            ):
                raise ConflictError(f"workflow not found: {workflow_name}")

    @staticmethod
    def _canonical_symbol(symbol: str) -> str:
        return symbol.replace("/", "").replace(" ", "").upper()

    async def run_execution_plan(
        self, request: ExecutionPlanRunRequest
    ) -> ExecutionPlanRunResult:
        plan = self.get_execution_plan_by_name(request.plan_name)
        started_at = utc_now()
        run_id = f"epr_{uuid4().hex}"
        duration_minutes = request.duration_minutes or plan.max_duration_minutes
        target_operations = request.target_operations or plan.target_operations
        selected_symbol = request.symbol or plan.symbols[0]
        selected_symbol = self._canonical_symbol(selected_symbol)
        covered_symbols = [*plan.symbols]
        required_reads = self._required_execution_plan_reads(plan)
        preflight: list[str] = []
        blockers: list[str] = []

        if plan.status is ExecutionPlanStatus.ACTIVE:
            preflight.append("plan_status=active")
        else:
            blockers.append(f"plan_status_{plan.status.value}")
        if self.settings.paper_trading_only and plan.mode is ExecutionMode.PAPER:
            preflight.append("paper_trading_only=true")
        else:
            blockers.append("paper_trading_required")
        if selected_symbol in plan.symbols:
            preflight.append(f"symbol_allowed={selected_symbol}")
        else:
            blockers.append("symbol_not_allowed_by_plan")
        if duration_minutes <= plan.max_duration_minutes:
            preflight.append(f"duration_limit_ok={duration_minutes}m")
        else:
            blockers.append("duration_limit_exceeded")
        if target_operations <= plan.target_operations:
            preflight.append(f"target_operations_limit_ok={target_operations}")
        else:
            blockers.append("target_operations_limit_exceeded")
        if plan.max_trade_notional_quote <= self.settings.max_order_notional:
            preflight.append("plan_notional_within_server_limit=true")
        else:
            blockers.append("plan_notional_exceeds_server_limit")
        if plan.risk_per_trade_quote <= self.settings.max_operation_loss:
            preflight.append("plan_risk_within_server_limit=true")
        else:
            blockers.append("plan_risk_exceeds_server_limit")

        health = self._intelligence_for_account().health()
        if "binance_public" in plan.data_sources:
            if health.get("market_data", {}).get("binance") == "configured":
                preflight.append("data_source_binance_public=configured")
            else:
                blockers.append("data_source_binance_public_unavailable")
        if "rss" in plan.data_sources:
            if any(value == "configured" for value in health.get("news", {}).values()):
                preflight.append("data_source_rss=configured")
            else:
                blockers.append("data_source_rss_unavailable")
        if "binance_derivatives" in plan.data_sources:
            if health.get("derivatives_positioning", {}).get("binance_derivatives") == "configured":
                preflight.append("data_source_binance_derivatives=configured")
            else:
                blockers.append("data_source_binance_derivatives_unavailable")

        open_operations = self.store.count_open_operations(account_id=self._account_id())
        if open_operations < plan.max_open_operations:
            preflight.append(f"open_operations={open_operations}")
        else:
            blockers.append("plan_open_operations_limit_reached")
        slot_capacity = plan.max_trade_notional_quote * plan.max_open_operations
        implicit_reserve = plan.capital_quote - slot_capacity
        open_notional = self.store.open_notional_quote(account_id=self._account_id())
        preflight.extend(
            [
                f"slot_capacity={slot_capacity}",
                f"implicit_reserve={implicit_reserve}",
                f"open_notional={open_notional}",
            ]
        )
        if open_notional >= plan.capital_quote:
            blockers.append("plan_capital_allocation_exceeded")
        daily_loss = self._daily_loss()
        if daily_loss < plan.max_daily_loss_quote:
            preflight.append(f"daily_loss_below_plan_limit={daily_loss}")
        else:
            blockers.append("plan_daily_loss_limit_reached")

        status = ExecutionPlanRunStatus.READY if not blockers else ExecutionPlanRunStatus.BLOCKED
        run_receipt = ExecutionPlanRunReceipt(
            run_id=run_id,
            account_id=self._account_id(),
            plan_id=plan.plan_id,
            plan_name=plan.name,
            plan_version=plan.version,
            status=status,
            started_at=started_at,
            expires_at=started_at + timedelta(minutes=duration_minutes),
            selected_symbol=selected_symbol,
            covered_symbols=covered_symbols,
            duration_minutes=duration_minutes,
            target_operations=target_operations,
            required_reads=required_reads,
        )
        self.store.save_execution_plan_run(run_receipt)

        before_workflow = None
        if status is ExecutionPlanRunStatus.READY and plan.before_workflow_name:
            try:
                before_workflow = await self.run_workflow(
                    WorkflowRunRequest(
                        workflow_name=plan.before_workflow_name,
                        phase=WorkflowPhase.BEFORE_OPERATION,
                        symbol=selected_symbol,
                        pattern_id=plan.pattern_id,
                        execution_plan_run_id=run_id,
                    )
                )
            except Exception as exc:
                blockers.append(f"before_workflow_error={str(exc)[:120]}")
            else:
                if before_workflow.status is not WorkflowRunStatus.COMPLETED:
                    blockers.append("before_workflow_not_completed")

        if blockers and status is ExecutionPlanRunStatus.READY:
            status = ExecutionPlanRunStatus.BLOCKED
            self.store.save_execution_plan_run(run_receipt.model_copy(update={"status": status}))

        return ExecutionPlanRunResult(
            run_id=run_id,
            account_id=self._account_id(),
            plan=plan,
            status=status,
            started_at=started_at,
            expires_at=started_at + timedelta(minutes=duration_minutes),
            selected_symbol=selected_symbol,
            covered_symbols=covered_symbols,
            required_reads=required_reads,
            duration_minutes=duration_minutes,
            target_operations=target_operations,
            preflight=preflight,
            blockers=blockers,
            next_steps=plan.steps if status is ExecutionPlanRunStatus.READY else [],
            before_workflow=before_workflow,
        )

    @staticmethod
    def _required_execution_plan_reads(plan: ExecutionPlan) -> list[ExecutionPlanReadType]:
        required: list[ExecutionPlanReadType] = []
        for step in plan.steps:
            if not step.required:
                continue
            read_type = _READ_TYPE_BY_ACTION.get(step.action)
            if read_type is not None and read_type not in required:
                required.append(read_type)
        return required

    def _execution_plan_run_for_read(
        self, run_id: str, symbol: str
    ) -> ExecutionPlanRunReceipt:
        account_id = self._account_id()
        run = self.store.get_execution_plan_run(run_id, account_id=account_id)
        if not run:
            raise ConflictError("execution_plan_run_not_found")
        now = utc_now()
        if run.status is not ExecutionPlanRunStatus.READY:
            raise ConflictError("execution_plan_run_not_active")
        if now < run.started_at or now >= run.expires_at:
            raise ConflictError("execution_plan_run_expired")
        plan = self.store.get_execution_plan(run.plan_id, account_id=account_id)
        if (
            not plan
            or plan.name.casefold() != run.plan_name.casefold()
            or plan.version != run.plan_version
        ):
            raise ConflictError("execution_plan_run_plan_version_mismatch")
        if plan.status is not ExecutionPlanStatus.ACTIVE:
            raise ConflictError("execution_plan_run_plan_not_active")
        canonical_symbol = self._canonical_symbol(symbol)
        if canonical_symbol not in run.covered_symbols:
            raise ConflictError("symbol_not_covered_by_execution_plan_run")
        if canonical_symbol not in plan.symbols:
            raise ConflictError("symbol_not_allowed_by_execution_plan")
        return run

    def _prepare_execution_plan_read(
        self,
        execution_plan_run_id: str | None,
        symbol: str | None,
        *,
        market_type: MarketType | str | None = None,
        timeframe: str | None = None,
        pattern_id: str | None = None,
        require_pattern_id: bool = False,
        side: str | None = None,
    ) -> None:
        if execution_plan_run_id is None:
            return
        if not symbol:
            raise ConflictError("execution_plan_read_symbol_required")
        run = self._execution_plan_run_for_read(execution_plan_run_id, symbol)
        plan = self.store.get_execution_plan(run.plan_id, account_id=self._account_id())
        if not plan:
            raise ConflictError("execution_plan_run_plan_version_mismatch")
        if market_type is not None and MarketType(market_type) is not plan.market_type:
            raise ConflictError("execution_plan_read_market_type_mismatch")
        if timeframe is not None and timeframe != plan.timeframe:
            raise ConflictError("execution_plan_read_timeframe_mismatch")
        if require_pattern_id and pattern_id is None:
            raise ConflictError("execution_plan_read_pattern_id_required")
        if pattern_id is not None and pattern_id != plan.pattern_id:
            raise ConflictError("execution_plan_read_pattern_id_mismatch")
        if side is not None and TradeSide(side) not in plan.allowed_sides:
            raise ConflictError("execution_plan_read_side_not_allowed")

    def _record_execution_plan_read(
        self,
        execution_plan_run_id: str | None,
        read_types: tuple[ExecutionPlanReadType, ...],
        symbol: str | None,
        successful: bool,
    ) -> None:
        if execution_plan_run_id is None or not successful:
            return
        if not symbol:
            raise ConflictError("execution_plan_read_symbol_required")
        run = self._execution_plan_run_for_read(execution_plan_run_id, symbol)
        canonical_symbol = self._canonical_symbol(symbol)
        for read_type in dict.fromkeys(read_types):
            self.store.save_execution_plan_read_receipt(
                ExecutionPlanReadReceipt(
                    receipt_id=f"eprr_{uuid4().hex}",
                    account_id=self._account_id(),
                    run_id=run.run_id,
                    plan_id=run.plan_id,
                    plan_version=run.plan_version,
                    read_type=read_type,
                    symbol=canonical_symbol,
                )
            )

    def _execution_plan_provenance_reasons(
        self, proposal: TradeProposal, plan: ExecutionPlan
    ) -> list[str]:
        reasons: list[str] = []
        if (
            proposal.execution_plan_version is not None
            and proposal.execution_plan_version != plan.version
        ):
            reasons.append("execution_plan_version_mismatch")
        if not proposal.execution_plan_run_id:
            reasons.append("execution_plan_run_id_required")
            return reasons

        run = self.store.get_execution_plan_run(
            proposal.execution_plan_run_id, account_id=self._account_id()
        )
        if not run:
            reasons.append("execution_plan_run_not_found")
            return reasons

        now = utc_now()
        if run.status is not ExecutionPlanRunStatus.READY:
            reasons.append("execution_plan_run_not_active")
        if now < run.started_at or now >= run.expires_at:
            reasons.append("execution_plan_run_expired")
        if run.plan_id != plan.plan_id or run.plan_name.casefold() != plan.name.casefold():
            reasons.append("execution_plan_run_plan_mismatch")
        if run.plan_version != plan.version:
            reasons.append("execution_plan_run_plan_version_mismatch")
        if (
            proposal.execution_plan_version is not None
            and proposal.execution_plan_version != run.plan_version
        ):
            reasons.append("execution_plan_run_version_mismatch")

        symbol = self._canonical_symbol(proposal.context.symbol)
        if symbol not in run.covered_symbols:
            reasons.append("symbol_not_covered_by_execution_plan_run")

        freshness_cutoff = max(
            run.started_at,
            now - timedelta(seconds=self.settings.execution_plan_read_freshness_seconds),
        )
        for read_type in run.required_reads:
            has_fresh_receipt = any(
                receipt.read_type is read_type
                and receipt.symbol == symbol
                and receipt.plan_id == run.plan_id
                and receipt.plan_version == run.plan_version
                and freshness_cutoff <= receipt.recorded_at <= now
                for receipt in run.receipts
            )
            if not has_fresh_receipt:
                reasons.append(
                    f"execution_plan_read_{read_type.value}_missing_or_stale"
                )
        return reasons

    def _workflow_for_name(self, name: str | None) -> Workflow | None:
        if not name:
            return None
        workflow = self.get_workflow_by_name(name)
        if not workflow.enabled:
            raise ConflictError(f"workflow is disabled: {workflow.name}")
        return workflow

    async def run_workflow(self, request: WorkflowRunRequest) -> WorkflowRunResult:
        operation = self.get_operation(request.operation_id) if request.operation_id else None
        workflow = None
        if operation and operation.workflow:
            if operation.workflow.name.casefold() != request.workflow_name.casefold():
                raise ConflictError("workflow does not match the operation snapshot")
            workflow = operation.workflow
        if workflow is None:
            workflow = self.get_workflow_by_name(request.workflow_name)
        if not workflow.enabled:
            raise ConflictError(f"workflow is disabled: {workflow.name}")
        if request.phase is WorkflowPhase.AFTER_OPERATION and operation is None:
            raise ConflictError("after_operation workflows require operation_id")

        context = {
            "operation_id": request.operation_id,
            "symbol": request.symbol
            or (operation.proposal.context.symbol if operation else None),
            "pattern_id": request.pattern_id
            or (
                operation.proposal.patterns[0].pattern_id
                if operation and operation.proposal.patterns
                else None
            ),
            "execution_plan_run_id": request.execution_plan_run_id,
        }
        steps = (
            workflow.before_steps
            if request.phase is WorkflowPhase.BEFORE_OPERATION
            else workflow.after_steps
        )
        results: list[WorkflowStepResult] = []
        for step in steps:
            try:
                data = await self._execute_workflow_step(step, context)
            except Exception as exc:
                message = str(exc).strip() or exc.__class__.__name__
                results.append(
                    WorkflowStepResult(
                        step_id=step.step_id,
                        name=step.name,
                        type=step.type,
                        status=WorkflowStepStatus.FAILED,
                        error=message[:500],
                    )
                )
            else:
                results.append(
                    WorkflowStepResult(
                        step_id=step.step_id,
                        name=step.name,
                        type=step.type,
                        status=WorkflowStepStatus.COMPLETED,
                        data=data,
                    )
                )

        completed = sum(
            result.status is WorkflowStepStatus.COMPLETED for result in results
        )
        if completed == len(results):
            run_status = WorkflowRunStatus.COMPLETED
        elif completed:
            run_status = WorkflowRunStatus.PARTIAL
        else:
            run_status = WorkflowRunStatus.FAILED
        return WorkflowRunResult(
            run_id=f"wfr_{uuid4().hex}",
            account_id=self._account_id(),
            workflow_id=workflow.workflow_id,
            workflow_name=workflow.name,
            workflow_version=workflow.version,
            phase=request.phase,
            operation_id=request.operation_id,
            status=run_status,
            steps=results,
        )

    async def _execute_workflow_step(
        self, step: WorkflowStep, context: dict[str, Any]
    ) -> Any:
        if step.type is WorkflowStepType.MARKET_SNAPSHOT:
            symbol = self._workflow_text(step, "symbol", context, context.get("symbol"))
            if not symbol:
                raise ValueError("workflow parameter is required: symbol or context.symbol")
            market_type = self._workflow_choice(
                step, "market_type", context, MarketType.SPOT.value, {"spot", "perpetual"}
            )
            timeframe = self._workflow_text(step, "timeframe", context, "1m")
            limit = self._workflow_int(step, "limit", context, 20, 1, 500)
            return jsonable(
                await self.market_snapshot(
                    symbol,
                    MarketType(market_type),
                    timeframe,
                    limit,
                    execution_plan_run_id=context.get("execution_plan_run_id"),
                )
            )

        if step.type in {WorkflowStepType.CRYPTO_NEWS, WorkflowStepType.X_POSTS}:
            symbol = self._workflow_optional_text(step, "symbol", context) or context.get(
                "symbol"
            )
            maximum_lookback = (
                7 * 24 * 60
                if step.type is WorkflowStepType.X_POSTS
                else 30 * 24 * 60
            )
            lookback = self._workflow_int(
                step, "lookback_minutes", context, 1_440, 1, maximum_lookback
            )
            limit = self._workflow_int(step, "limit", context, 20, 1, 100)
            since = utc_now() - timedelta(minutes=lookback)
            response = (
                await self.crypto_news(
                    symbol,
                    since,
                    limit,
                    execution_plan_run_id=context.get("execution_plan_run_id"),
                )
                if step.type is WorkflowStepType.CRYPTO_NEWS
                else await self.x_posts(symbol, since, limit)
            )
            return jsonable(response)

        if step.type is WorkflowStepType.PATTERN_CONTEXT:
            pattern_id = self._workflow_text(
                step, "pattern_id", context, context.get("pattern_id")
            )
            if not pattern_id:
                raise ValueError(
                    "workflow parameter is required: pattern_id or context.pattern_id"
                )
            market_type = self._workflow_optional_choice(
                step, "market_type", context, {"spot", "perpetual"}
            )
            side = self._workflow_optional_choice(step, "side", context, {"long", "short"})
            symbol = self._workflow_optional_text(step, "symbol", context) or context.get(
                "symbol"
            )
            limit = self._workflow_int(step, "limit", context, 50, 1, 500)
            return jsonable(
                self.pattern_context(
                    pattern_id,
                    symbol=symbol,
                    market_type=market_type,
                    side=side,
                    limit=limit,
                    execution_plan_run_id=context.get("execution_plan_run_id"),
                )
            )

        if step.type is WorkflowStepType.LESSONS:
            pattern_id = self._workflow_optional_text(step, "pattern_id", context)
            market_type = self._workflow_optional_choice(
                step, "market_type", context, {"spot", "perpetual"}
            )
            side = self._workflow_optional_choice(step, "side", context, {"long", "short"})
            symbol = self._workflow_optional_text(step, "symbol", context) or context.get(
                "symbol"
            )
            limit = self._workflow_int(step, "limit", context, 100, 1, 500)
            return [
                jsonable(lesson)
                for lesson in self.lessons(
                    pattern_id=pattern_id,
                    symbol=symbol,
                    market_type=market_type,
                    side=side,
                    limit=limit,
                    execution_plan_run_id=context.get("execution_plan_run_id"),
                )
            ]

        if step.type is WorkflowStepType.AGENT_INSTRUCTION:
            instruction = self._required_workflow_parameter(step, "instruction", context)
            return {
                "instruction": instruction,
                "execution": "agent_must_apply_instruction_to_step_outputs",
            }

        raise ValueError(f"unsupported workflow step type: {step.type.value}")

    def _workflow_parameter(
        self, step: WorkflowStep, name: str, context: dict[str, Any], default: Any = None
    ) -> Any:
        value = step.parameters.get(name, default)
        if isinstance(value, str) and value.startswith("$context."):
            context_name = value.removeprefix("$context.")
            if context_name not in context:
                raise ValueError(f"unknown workflow context variable: {context_name}")
            return context[context_name]
        return value

    def _workflow_text(
        self, step: WorkflowStep, name: str, context: dict[str, Any], default: str | None = None
    ) -> str | None:
        value = self._workflow_parameter(step, name, context, default)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _required_workflow_parameter(
        self, step: WorkflowStep, name: str, context: dict[str, Any]
    ) -> str:
        value = self._workflow_text(step, name, context)
        if not value:
            raise ValueError(f"workflow parameter is required: {name}")
        return value

    def _workflow_optional_text(
        self, step: WorkflowStep, name: str, context: dict[str, Any]
    ) -> str | None:
        return self._workflow_text(step, name, context)

    def _workflow_int(
        self,
        step: WorkflowStep,
        name: str,
        context: dict[str, Any],
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        value = self._workflow_parameter(step, name, context, default)
        try:
            number = int(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"workflow parameter must be an integer: {name}") from exc
        if not minimum <= number <= maximum:
            raise ValueError(
                f"workflow parameter {name} must be between {minimum} and {maximum}"
            )
        return number

    def _workflow_choice(
        self,
        step: WorkflowStep,
        name: str,
        context: dict[str, Any],
        default: str,
        allowed: set[str],
    ) -> str:
        value = self._workflow_text(step, name, context, default)
        if value not in allowed:
            raise ValueError(
                f"workflow parameter {name} must be one of: {', '.join(sorted(allowed))}"
            )
        return value

    def _workflow_optional_choice(
        self, step: WorkflowStep, name: str, context: dict[str, Any], allowed: set[str]
    ) -> str | None:
        value = self._workflow_text(step, name, context)
        if value is not None and value not in allowed:
            raise ValueError(
                f"workflow parameter {name} must be one of: {', '.join(sorted(allowed))}"
            )
        return value

    def _account_id(self) -> str:
        return get_current_account_id() or self.default_account_id or DEFAULT_ACCOUNT_ID

    def _intelligence_for_account(self) -> IntelligenceService:
        if self._custom_intelligence or self.account_manager is None:
            return self.intelligence
        credentials = self.account_manager.credentials_for(self._account_id())
        return build_default_intelligence(self.settings, account_credentials=credentials)

    def _notify(self, event: NotificationEvent, operation: OperationRecord) -> None:
        if self.notification_service is not None:
            self.notification_service.notify_operation(operation.account_id, event, operation)

    def health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "environment": self.settings.app_env,
            "paper_trading_only": self.settings.paper_trading_only,
            "connectors": {
                "paper_exchange": "enabled",
                "live_exchanges": "not_configured",
                **self._intelligence_for_account().health(),
                "notifications": (
                    self.notification_service.health(self._account_id())
                    if self.notification_service is not None
                    else {"telegram": "not_configured"}
                ),
            },
        }

    async def market_snapshot(
        self,
        symbol: str,
        market_type: MarketType,
        timeframe: str,
        limit: int,
        execution_plan_run_id: str | None = None,
    ) -> MarketDataResponse:
        self._prepare_execution_plan_read(
            execution_plan_run_id,
            symbol,
            market_type=market_type,
            timeframe=timeframe,
        )
        response = await self._intelligence_for_account().market_snapshot(
            symbol, market_type, timeframe, limit
        )
        self._record_execution_plan_read(
            execution_plan_run_id,
            (ExecutionPlanReadType.MARKET_SNAPSHOT,),
            symbol,
            not response.errors,
        )
        return response

    async def derivatives_positioning(
        self,
        symbol: str,
        period: str | None = None,
        limit: int | None = None,
        execution_plan_run_id: str | None = None,
    ) -> DerivativesPositioningResponse:
        requested_period = (
            period.strip().lower()
            if period is not None
            else self.settings.binance_positioning_period
        )
        requested_limit = (
            limit if limit is not None else self.settings.binance_positioning_limit
        )
        self._prepare_execution_plan_read(execution_plan_run_id, symbol)
        response = await self._intelligence_for_account().derivatives_positioning(
            symbol, requested_period, requested_limit
        )
        self._record_execution_plan_read(
            execution_plan_run_id,
            (ExecutionPlanReadType.DERIVATIVES_POSITIONING,),
            symbol,
            not response.errors,
        )
        return response

    async def crypto_news(
        self,
        symbol: str | None,
        since: datetime,
        limit: int,
        execution_plan_run_id: str | None = None,
    ) -> SignalResponse:
        self._prepare_execution_plan_read(execution_plan_run_id, symbol)
        response = await self._intelligence_for_account().news(symbol, since, limit)
        self._record_execution_plan_read(
            execution_plan_run_id,
            (ExecutionPlanReadType.CRYPTO_NEWS,),
            symbol,
            not response.errors,
        )
        return response

    async def evaluate_strategy(
        self,
        plan_name: str,
        symbol: str,
        execution_plan_run_id: str | None,
        *,
        lookback_minutes: int = 1_440,
        candle_limit: int = 100,
    ) -> StrategyEvaluation:
        """Evaluate the active paper plan using fresh, provenance-bound intelligence."""

        if not execution_plan_run_id:
            raise ConflictError("execution_plan_run_id_required")
        if not 1 <= lookback_minutes <= 30 * 24 * 60:
            raise ValueError("lookback_minutes must be between 1 and 43200")
        if not 30 <= candle_limit <= 500:
            raise ValueError("candle_limit must be between 30 and 500")

        plan = self.get_execution_plan_by_name(plan_name)
        canonical_symbol = self._canonical_symbol(symbol)
        if canonical_symbol not in plan.symbols:
            raise ConflictError("symbol_not_allowed_by_execution_plan")

        market_response = await self.market_snapshot(
            canonical_symbol,
            plan.market_type,
            plan.timeframe,
            candle_limit,
            execution_plan_run_id=execution_plan_run_id,
        )
        if not market_response.snapshots:
            raise ConflictError("strategy_market_data_unavailable")

        positioning = None
        positioning_errors: list[str] = []
        if plan.market_type is MarketType.PERPETUAL:
            positioning_response = await self.derivatives_positioning(
                canonical_symbol,
                execution_plan_run_id=execution_plan_run_id,
            )
            positioning = (
                positioning_response.snapshots[0]
                if positioning_response.snapshots
                else None
            )
            positioning_errors = [
                f"{error.source}:{error.category}" for error in positioning_response.errors
            ]

        news_response = await self.crypto_news(
            canonical_symbol,
            utc_now() - timedelta(minutes=lookback_minutes),
            100,
            execution_plan_run_id=execution_plan_run_id,
        )
        policy = StrategyPolicy(
            core_minimum_score=(
                plan.minimum_signal_score
                if plan.minimum_signal_score is not None
                else Decimal("0.68")
            ),
            exploratory_enabled=plan.exploratory_trades_enabled,
            exploratory_minimum_score=plan.exploratory_minimum_signal_score,
            exploratory_risk_fraction=plan.exploratory_risk_fraction,
            minimum_net_reward_risk_ratio=plan.minimum_net_reward_risk_ratio,
            max_trade_notional_quote=plan.max_trade_notional_quote,
            risk_per_trade_quote=plan.risk_per_trade_quote,
            fee_rate=self.settings.default_fee_rate,
            slippage_rate=self.settings.default_slippage_rate,
        )
        evaluation = evaluate_market_strategy(
            market_response.snapshots[0],
            positioning,
            news_response.signals,
            policy=policy,
        )
        source_quality = [
            *evaluation.data_quality,
            *positioning_errors,
            *[f"{error.source}:{error.category}" for error in market_response.errors],
            *[f"{error.source}:{error.category}" for error in news_response.errors],
        ]
        return evaluation.model_copy(update={"data_quality": list(dict.fromkeys(source_quality))})

    def _record_strategy_cycle(
        self,
        plan_name: str,
        symbol: str,
        execution_plan_run_id: str | None,
        *,
        executed: bool,
        reason: str,
        plan_version: int | None = None,
        operation_id: str | None = None,
        evaluation: StrategyEvaluation | None = None,
        risk_check: RiskCheck | None = None,
    ) -> StrategyCycleRecord:
        record = StrategyCycleRecord(
            cycle_id=f"sc_{uuid4().hex}",
            account_id=self._account_id(),
            plan_name=plan_name,
            plan_version=plan_version,
            symbol=symbol,
            execution_plan_run_id=execution_plan_run_id,
            executed=executed,
            reason=reason[:240] or "strategy_cycle_completed",
            operation_id=operation_id,
            evaluation=evaluation,
            risk_check=risk_check,
        )
        self.store.save_strategy_cycle(record)
        return record

    async def run_strategy_cycle(
        self,
        plan_name: str,
        symbol: str,
        execution_plan_run_id: str | None,
    ) -> dict[str, object]:
        """Evaluate once and execute only the best accepted paper candidate."""

        try:
            evaluation = await self.evaluate_strategy(
                plan_name,
                symbol,
                execution_plan_run_id,
            )
        except (ConflictError, NotFoundError, ValueError) as exc:
            result = {"executed": False, "reason": str(exc)}
            self._record_strategy_cycle(
                plan_name,
                symbol,
                execution_plan_run_id,
                executed=False,
                reason=str(exc),
            )
            return result

        if not evaluation.candidates:
            result = {
                "executed": False,
                "reason": "no_eligible_candidate",
                "evaluation": jsonable(evaluation),
            }
            self._record_strategy_cycle(
                plan_name,
                symbol,
                execution_plan_run_id,
                executed=False,
                reason="no_eligible_candidate",
                evaluation=evaluation,
            )
            return result

        plan = self.get_execution_plan_by_name(plan_name)
        candidate = evaluation.candidates[0]
        if execution_plan_run_id is None:
            result = {"executed": False, "reason": "execution_plan_run_id_required"}
            self._record_strategy_cycle(
                plan_name,
                symbol,
                execution_plan_run_id,
                executed=False,
                reason="execution_plan_run_id_required",
                plan_version=plan.version,
                evaluation=evaluation,
            )
            return result
        try:
            self.pattern_context(
                plan.pattern_id,
                symbol=candidate.symbol,
                market_type=plan.market_type.value,
                side=candidate.side.value,
                execution_plan_run_id=execution_plan_run_id,
            )
            self.lessons(
                pattern_id=plan.pattern_id,
                symbol=candidate.symbol,
                market_type=plan.market_type.value,
                side=candidate.side.value,
                execution_plan_run_id=execution_plan_run_id,
            )
            target = candidate.take_profit_price
            entry = candidate.entry_price
            band_anchor = entry + (target - entry) * Decimal("0.80")
            band_lower = min(band_anchor, target)
            band_upper = max(band_anchor, target)
            proposal = TradeProposal(
                idempotency_key=(
                    f"{plan.pattern_id}-{candidate.symbol.lower()}-"
                    f"{candidate.side.value}-{candidate.observed_at.strftime('%Y%m%d%H%M%S%f')}"
                ),
                mode=ExecutionMode.PAPER,
                agent_id="deterministic-strategy-engine",
                model_version="regime-score-v1",
                strategy_version=plan.strategy_version,
                signal_tier=candidate.signal_tier,
                signal_score=candidate.signal_score,
                workflow_name=plan.before_workflow_name,
                execution_plan_name=plan.name,
                execution_plan_version=plan.version,
                execution_plan_run_id=execution_plan_run_id,
                context={
                    "venue": "binance",
                    "symbol": candidate.symbol,
                    "market_type": plan.market_type,
                    "timeframe": plan.timeframe,
                    "side": candidate.side,
                    "market_regime": candidate.market_regime,
                },
                thesis={
                    "summary": (
                        f"{candidate.signal_tier.value} {candidate.side.value} setup "
                        f"with composite score {candidate.signal_score}."
                    ),
                    "evidence": candidate.evidence,
                    "scope": {
                        "symbol": candidate.symbol,
                        "timeframe": plan.timeframe,
                        "regime": candidate.market_regime,
                        "signal_tier": candidate.signal_tier.value,
                    },
                    "assumptions": [
                        "Paper execution only.",
                        "Aggregate derivatives data is a proxy, not individual leverage.",
                    ],
                    "invalidation_conditions": [
                        "The hard stop is reached.",
                        "The technical regime changes before execution.",
                    ],
                    "expected_horizon_minutes": min(plan.max_duration_minutes, 180),
                    "confidence": candidate.signal_score,
                },
                patterns=[
                    PatternHypothesis(
                        pattern_id=plan.pattern_id,
                        pattern_version=1,
                        confidence=candidate.signal_score,
                        evidence_refs=[evidence.reference for evidence in candidate.evidence],
                    )
                ],
                quantity=candidate.quantity,
                entry_price=candidate.entry_price,
                stop_loss_price=candidate.stop_loss_price,
                take_profit_price=candidate.take_profit_price,
                exit_policy=PositionExitPolicy(
                    stop_loss_price=candidate.stop_loss_price,
                    take_profit_limits=[
                        TakeProfitLimit(label="strategy_target", price=candidate.take_profit_price)
                    ],
                    profit_bands=[
                        ProfitBand(
                            label="target_stall_band",
                            lower_price=band_lower,
                            upper_price=band_upper,
                            dwell_seconds=180,
                        )
                    ],
                    max_duration_seconds=plan.max_duration_minutes * 60,
                ),
                leverage=Decimal("1"),
                max_loss_quote=candidate.risk_budget_quote,
                signal_observed_at=candidate.observed_at,
            )
            operation = self.create_proposal(proposal)
            operation = self.execute_paper(operation.operation_id)
        except RiskRejected as exc:
            result = {
                "executed": False,
                "reason": "risk_rejected",
                "risk_check": jsonable(exc.check),
                "evaluation": jsonable(evaluation),
            }
            self._record_strategy_cycle(
                plan_name,
                symbol,
                execution_plan_run_id,
                executed=False,
                reason="risk_rejected",
                plan_version=plan.version,
                evaluation=evaluation,
                risk_check=exc.check,
            )
            return result
        except (ConflictError, NotFoundError) as exc:
            result = {
                "executed": False,
                "reason": str(exc),
                "evaluation": jsonable(evaluation),
            }
            self._record_strategy_cycle(
                plan_name,
                symbol,
                execution_plan_run_id,
                executed=False,
                reason=str(exc),
                plan_version=plan.version,
                evaluation=evaluation,
            )
            return result
        self._record_strategy_cycle(
            plan_name,
            symbol,
            execution_plan_run_id,
            executed=True,
            reason="operation_opened",
            plan_version=plan.version,
            operation_id=operation.operation_id,
            evaluation=evaluation,
        )
        return {
            "executed": True,
            "operation": jsonable(operation),
            "evaluation": jsonable(evaluation),
        }

    async def x_posts(
        self, symbol: str | None, since: datetime, limit: int
    ) -> SignalResponse:
        return await self._intelligence_for_account().x_posts(symbol, since, limit)

    def _daily_loss(self) -> Decimal:
        today = datetime.now(UTC).date().isoformat()
        return Decimal(self.store.daily_loss(today, account_id=self._account_id()))

    def evaluate_risk(self, proposal: TradeProposal) -> RiskCheck:
        with self._lock:
            account_id = self._account_id()
            open_operations = self.store.count_open_operations(account_id=account_id)
            daily_loss = self._daily_loss()
            check = self.risk.evaluate(
                proposal,
                open_operations=open_operations,
                daily_loss=daily_loss,
            )
            if not proposal.execution_plan_name:
                if proposal.execution_plan_run_id or proposal.execution_plan_version is not None:
                    return check.model_copy(
                        update={
                            "allowed": False,
                            "reasons": [
                                *check.reasons,
                                "execution_plan_name_required_for_run",
                            ],
                        }
                    )
                return check
            plan = self.get_execution_plan_by_name(proposal.execution_plan_name)
            reasons = [*check.reasons]
            if plan.status is not ExecutionPlanStatus.ACTIVE:
                reasons.append(f"execution_plan_{plan.status.value}")
            if proposal.mode is not plan.mode:
                reasons.append("execution_plan_mode_mismatch")
            if self._canonical_symbol(proposal.context.symbol) not in plan.symbols:
                reasons.append("symbol_not_allowed_by_execution_plan")
            if proposal.context.market_type is not plan.market_type:
                reasons.append("market_type_not_allowed_by_execution_plan")
            if proposal.context.timeframe != plan.timeframe:
                reasons.append("timeframe_not_allowed_by_execution_plan")
            if proposal.context.side not in plan.allowed_sides:
                reasons.append("side_not_allowed_by_execution_plan")
            if proposal.strategy_version != plan.strategy_version:
                reasons.append("strategy_version_mismatch")
            if plan.pattern_id not in {pattern.pattern_id for pattern in proposal.patterns}:
                reasons.append("pattern_id_mismatch")
            if check.notional_quote > plan.max_trade_notional_quote:
                reasons.append("execution_plan_notional_limit_exceeded")
            if check.estimated_stop_loss_quote > plan.risk_per_trade_quote:
                reasons.append("execution_plan_risk_limit_exceeded")
            if daily_loss >= plan.max_daily_loss_quote:
                reasons.append("execution_plan_daily_loss_limit_exceeded")
            if open_operations >= plan.max_open_operations:
                reasons.append("execution_plan_open_operations_limit_exceeded")
            open_notional = self.store.open_notional_quote(account_id=account_id)
            if open_notional + check.notional_quote > plan.capital_quote:
                reasons.append("execution_plan_capital_allocation_exceeded")
            if (
                check.estimated_reward_risk_ratio
                < plan.minimum_net_reward_risk_ratio
            ):
                reasons.append("execution_plan_net_reward_risk_ratio_below_minimum")
            if plan.minimum_signal_score is not None:
                if proposal.signal_tier is SignalTier.CORE:
                    if proposal.signal_score < plan.minimum_signal_score:
                        reasons.append("core_signal_score_below_plan_minimum")
                elif not plan.exploratory_trades_enabled:
                    reasons.append("exploratory_trades_disabled")
                else:
                    if proposal.signal_score < plan.exploratory_minimum_signal_score:
                        reasons.append("exploratory_signal_score_below_plan_minimum")
                    exploratory_loss_limit = (
                        plan.risk_per_trade_quote * plan.exploratory_risk_fraction
                    )
                    if check.estimated_stop_loss_quote > exploratory_loss_limit:
                        reasons.append("exploratory_risk_limit_exceeded")
            reasons.extend(self._execution_plan_provenance_reasons(proposal, plan))
            return RiskCheck(
                allowed=not reasons,
                reasons=reasons,
                notional_quote=check.notional_quote,
                estimated_stop_loss_quote=check.estimated_stop_loss_quote,
                estimated_net_reward_quote=check.estimated_net_reward_quote,
                estimated_reward_risk_ratio=check.estimated_reward_risk_ratio,
            )

    def create_proposal(self, proposal: TradeProposal) -> OperationRecord:
        with self._lock:
            account_id = self._account_id()
            existing = self.store.find_by_idempotency_key(
                proposal.idempotency_key, account_id=account_id
            )
            if existing:
                if existing.proposal != proposal:
                    raise ConflictError("idempotency key already belongs to another proposal")
                return existing
            workflow = self._workflow_for_name(proposal.workflow_name)
            execution_plan = (
                self.get_execution_plan_by_name(proposal.execution_plan_name)
                if proposal.execution_plan_name
                else None
            )
            check = self.evaluate_risk(proposal)
            if not check.allowed:
                raise RiskRejected(check)
            now = utc_now()
            operation = OperationRecord(
                operation_id=f"op_{uuid4().hex}",
                status=OperationStatus.PROPOSED,
                created_at=now,
                proposal=proposal,
                account_id=account_id,
                workflow=workflow,
                execution_plan=execution_plan,
            )
            self._validate_exit_policy(operation, proposal.exit_policy)
            self.store.save_operation(operation)
            return operation

    def get_operation(self, operation_id: str) -> OperationRecord:
        operation = self.store.get_operation(operation_id, account_id=self._account_id())
        if not operation:
            raise NotFoundError(f"operation not found: {operation_id}")
        return operation

    def list_operations(self, limit: int = 100) -> list[OperationRecord]:
        return self.store.list_operations(limit, account_id=self._account_id())

    def list_strategy_cycles(
        self,
        limit: int = 100,
        *,
        plan_name: str | None = None,
        execution_plan_run_id: str | None = None,
    ) -> list[StrategyCycleRecord]:
        return self.store.list_strategy_cycles(
            limit,
            account_id=self._account_id(),
            plan_name=plan_name,
            execution_plan_run_id=execution_plan_run_id,
        )

    @staticmethod
    def _effective_exit_policy(operation: OperationRecord) -> PositionExitPolicy:
        if operation.proposal.exit_policy is not None:
            return operation.proposal.exit_policy
        return PositionExitPolicy(
            stop_loss_price=operation.proposal.stop_loss_price,
            take_profit_limits=(
                [
                    TakeProfitLimit(
                        label="initial_target",
                        price=operation.proposal.take_profit_price,
                    )
                ]
                if operation.proposal.take_profit_price is not None
                else []
            ),
        )

    def _validate_exit_policy(
        self,
        operation: OperationRecord,
        policy: PositionExitPolicy,
        *,
        preserve_original_stop: bool = True,
    ) -> None:
        entry = (
            operation.execution.entry_price
            if operation.execution
            else operation.proposal.entry_price
        )
        side = operation.proposal.context.side
        original_stop = operation.proposal.stop_loss_price
        if side is TradeSide.LONG:
            if policy.stop_loss_price >= entry:
                raise ConflictError("long_exit_stop_must_be_below_entry")
            if preserve_original_stop and policy.stop_loss_price < original_stop:
                raise ConflictError("exit_stop_cannot_increase_position_risk")
            if any(limit.price <= entry for limit in policy.take_profit_limits):
                raise ConflictError("long_take_profit_limit_must_be_above_entry")
            if any(
                band.require_net_profit and band.lower_price <= entry
                for band in policy.profit_bands
            ):
                raise ConflictError("long_profit_band_must_be_above_entry")
        else:
            if policy.stop_loss_price <= entry:
                raise ConflictError("short_exit_stop_must_be_above_entry")
            if preserve_original_stop and policy.stop_loss_price > original_stop:
                raise ConflictError("exit_stop_cannot_increase_position_risk")
            if any(limit.price >= entry for limit in policy.take_profit_limits):
                raise ConflictError("short_take_profit_limit_must_be_below_entry")
            if any(
                band.require_net_profit and band.upper_price >= entry
                for band in policy.profit_bands
            ):
                raise ConflictError("short_profit_band_must_be_below_entry")

    def record_position_observation(
        self, operation_id: str, observation: PositionMonitorStateInput
    ) -> OperationRecord:
        with self._lock:
            operation = self.get_operation(operation_id)
            if operation.status is not OperationStatus.PAPER_OPEN:
                raise ConflictError("only open paper operations can record observations")
            current = operation.monitor_state
            if observation.expected_version != current.version:
                raise ConflictError("position_monitor_state_version_conflict")
            policy = self._effective_exit_policy(operation)
            valid_labels = {band.label for band in policy.profit_bands}
            if set(observation.band_entered_at) - valid_labels:
                raise ConflictError("unknown_profit_band_label")
            operation.monitor_state = PositionMonitorState(
                version=current.version + 1,
                band_entered_at=observation.band_entered_at,
                updated_at=utc_now(),
            )
            self.store.save_operation(operation)
            return operation

    async def prepare_position_review(self, operation_id: str) -> PositionReview:
        """Fetch fresh context for an AI review without changing the position."""

        operation = self.get_operation(operation_id)
        if operation.status is not OperationStatus.PAPER_OPEN:
            raise ConflictError("only open paper operations can be reviewed")
        context = operation.proposal.context
        now = utc_now()
        market = await self.market_snapshot(
            context.symbol,
            context.market_type,
            context.timeframe,
            100,
        )
        if not market.snapshots:
            raise ConflictError("position_review_market_data_unavailable")
        derivatives: DerivativesPositioningResponse | None = None
        if context.market_type is MarketType.PERPETUAL:
            derivatives = await self.derivatives_positioning(context.symbol)
        news = await self.crypto_news(
            context.symbol,
            now - timedelta(minutes=24 * 60),
            100,
        )
        quality = [
            *[f"{error.source}:{error.category}" for error in market.errors],
            *(
                [f"{error.source}:{error.category}" for error in derivatives.errors]
                if derivatives
                else []
            ),
            *[f"{error.source}:{error.category}" for error in news.errors],
        ]
        review = PositionReview(
            account_id=self._account_id(),
            operation_id=operation.operation_id,
            symbol=context.symbol,
            market_type=context.market_type,
            timeframe=context.timeframe,
            prepared_at=now,
            expires_at=now
            + timedelta(
                seconds=max(
                    60,
                    min(15 * 60, self.settings.execution_plan_read_freshness_seconds),
                )
            ),
            source_data={
                "market": jsonable(market),
                "derivatives": jsonable(derivatives) if derivatives else None,
                "news": jsonable(news),
                "fundamental": {
                    "type": "rss_event_proxy",
                    "note": (
                        "Fundamental context is an event proxy from timestamped crypto news; "
                        "the AI must provide the fundamental interpretation in its submission."
                    ),
                },
            },
            data_quality=list(dict.fromkeys(quality)),
        )
        self.store.save_position_review(review)
        return review

    def get_position_review(self, review_id: str) -> PositionReview:
        review = self.store.get_position_review(review_id, account_id=self._account_id())
        if not review:
            raise NotFoundError(f"position review not found: {review_id}")
        return review

    def list_position_reviews(self, operation_id: str, limit: int = 20) -> list[PositionReview]:
        self.get_operation(operation_id)
        return self.store.list_position_reviews(
            operation_id,
            account_id=self._account_id(),
            limit=limit,
        )

    def submit_position_review(
        self, review_id: str, review_input: PositionReviewInput
    ) -> PositionReview:
        with self._lock:
            review = self.get_position_review(review_id)
            operation = self.get_operation(review.operation_id)
            if operation.status is not OperationStatus.PAPER_OPEN:
                raise ConflictError("only open paper operations can accept a review")
            if review.status is not PositionReviewStatus.PREPARED:
                raise ConflictError("position review is no longer pending")
            if utc_now() >= review.expires_at:
                expired = review.model_copy(update={"status": PositionReviewStatus.EXPIRED})
                self.store.save_position_review(expired)
                raise ConflictError("position review expired")
            if review.symbol != self._canonical_symbol(operation.proposal.context.symbol):
                raise ConflictError("position_review_symbol_mismatch")
            submitted = review.model_copy(
                update={
                    "status": PositionReviewStatus.SUBMITTED,
                    "decision": review_input.decision,
                    "news_analysis": review_input.news_analysis,
                    "fundamental_analysis": review_input.fundamental_analysis,
                    "technical_analysis": review_input.technical_analysis,
                    "derivatives_analysis": review_input.derivatives_analysis,
                    "evidence": review_input.evidence,
                    "proposed_exit_policy": review_input.proposed_exit_policy,
                    "reviewed_at": utc_now(),
                }
            )
            self.store.save_position_review(submitted)
            return submitted

    def _require_review(
        self,
        operation_id: str,
        review_id: str,
        decision: PositionReviewDecision,
    ) -> PositionReview:
        review = self.get_position_review(review_id)
        if review.operation_id != operation_id:
            raise ConflictError("position_review_operation_mismatch")
        if review.status is not PositionReviewStatus.SUBMITTED:
            raise ConflictError("submitted_position_review_required")
        if review.decision is not decision:
            raise ConflictError(f"position_review_decision_mismatch_{decision.value}")
        if review.reviewed_at is None or utc_now() >= review.expires_at:
            raise ConflictError("position_review_expired")
        operation = self.get_operation(operation_id)
        if operation.status is not OperationStatus.PAPER_OPEN:
            raise ConflictError("only open paper operations can use a review")
        return review

    def update_position_exit_policy(
        self,
        operation_id: str,
        review_id: str,
        policy: PositionExitPolicy,
    ) -> OperationRecord:
        with self._lock:
            review = self._require_review(
                operation_id, review_id, PositionReviewDecision.ADJUST
            )
            if review.proposed_exit_policy != policy:
                raise ConflictError("exit_policy_does_not_match_review")
            operation = self.get_operation(operation_id)
            self._validate_exit_policy(operation, policy)
            first_target = (
                policy.take_profit_limits[0].price
                if policy.take_profit_limits
                else None
            )
            operation.proposal = operation.proposal.model_copy(
                update={
                    "stop_loss_price": policy.stop_loss_price,
                    "take_profit_price": first_target,
                    "exit_policy": policy,
                }
            )
            self.store.save_operation(operation)
            return operation

    async def force_close_after_review(
        self, operation_id: str, review_id: str
    ) -> OperationRecord:
        self._require_review(operation_id, review_id, PositionReviewDecision.FORCE_CLOSE)
        operation = self.get_operation(operation_id)
        context = operation.proposal.context
        market = await self.market_snapshot(
            context.symbol, context.market_type, context.timeframe, 1
        )
        if not market.snapshots:
            raise ConflictError("force_close_market_data_unavailable")
        exit_price = market.snapshots[0].price
        if not operation.execution:
            raise ConflictError("paper_execution_missing")
        notional = (operation.execution.entry_price + exit_price) * operation.execution.quantity
        outcome = OutcomeInput(
            exit_price=exit_price,
            fees_quote=notional * self.settings.default_fee_rate,
            slippage_quote=notional * self.settings.default_slippage_rate,
            funding_quote=Decimal("0"),
            exit_reason="ai_force_close",
            review_id=review_id,
        )
        return self.close_operation(operation_id, outcome)

    def execute_paper(self, operation_id: str) -> OperationRecord:
        with self._lock:
            operation = self.get_operation(operation_id)
            if operation.status is not OperationStatus.PROPOSED:
                raise ConflictError(f"operation cannot be executed from {operation.status.value}")
            if operation.proposal.mode is not ExecutionMode.PAPER:
                raise ConflictError("only paper proposals can use the paper executor")
            check = self.evaluate_risk(operation.proposal)
            if not check.allowed:
                raise RiskRejected(check)
            operation.status = OperationStatus.PAPER_OPEN
            operation.execution = PaperExecution(
                order_id=f"paper_{uuid4().hex}",
                entry_price=operation.proposal.entry_price,
                quantity=operation.proposal.quantity,
                opened_at=utc_now(),
            )
            self.store.save_operation(operation)
        self._notify(NotificationEvent.PAPER_OPENED, operation)
        return operation

    def close_operation(self, operation_id: str, outcome_input: OutcomeInput) -> OperationRecord:
        with self._lock:
            operation = self.get_operation(operation_id)
            if operation.status is not OperationStatus.PAPER_OPEN or not operation.execution:
                raise ConflictError("only open paper operations can be closed")
            autonomous_reasons = (
                "stop_loss",
                "take_profit",
                "take_profit_level:",
                "profit_band:",
                "max_duration",
                "max_holding_time",
            )
            if not any(
                outcome_input.exit_reason == reason
                or outcome_input.exit_reason.startswith(reason)
                for reason in autonomous_reasons
            ):
                if not outcome_input.review_id:
                    raise ConflictError("fresh_position_review_required")
                self._require_review(
                    operation_id,
                    outcome_input.review_id,
                    PositionReviewDecision.FORCE_CLOSE,
                )
            entry = operation.execution.entry_price
            quantity = operation.execution.quantity
            if operation.proposal.context.side.value == "long":
                gross = (outcome_input.exit_price - entry) * quantity
            else:
                gross = (entry - outcome_input.exit_price) * quantity
            net = (
                gross
                - outcome_input.fees_quote
                - outcome_input.slippage_quote
                - outcome_input.funding_quote
            )
            status = outcome_input.forced_status or self._classify_pnl(net)
            operation.status = OperationStatus.CLOSED
            operation.outcome = TradeOutcome(
                status=status,
                exit_price=outcome_input.exit_price,
                pnl_gross=gross,
                pnl_net=net,
                fees_quote=outcome_input.fees_quote,
                slippage_quote=outcome_input.slippage_quote,
                funding_quote=outcome_input.funding_quote,
                exit_reason=outcome_input.exit_reason,
                closed_at=outcome_input.closed_at,
            )
            self.store.save_operation(operation)
        self._notify(NotificationEvent.OUTCOME_RECORDED, operation)
        return operation

    @staticmethod
    def _classify_pnl(net: Decimal) -> OutcomeStatus:
        if net > 0:
            return OutcomeStatus.WIN
        if net < 0:
            return OutcomeStatus.LOSS
        return OutcomeStatus.INCONCLUSIVE

    def record_postmortem(
        self, operation_id: str, postmortem_input: PostmortemInput
    ) -> OperationRecord:
        with self._lock:
            operation = self.get_operation(operation_id)
            if operation.status is not OperationStatus.CLOSED or not operation.outcome:
                raise ConflictError("postmortems require a closed operation")
            previous_postmortem = operation.postmortem
            postmortem = Postmortem(**postmortem_input.model_dump())
            pattern_ids = {pattern.pattern_id for pattern in operation.proposal.patterns}
            unknown_verdicts = {
                verdict.pattern_id
                for verdict in postmortem.pattern_verdicts
                if verdict.pattern_id not in pattern_ids
            }
            if unknown_verdicts:
                raise ConflictError(
                    "postmortem contains a verdict for an unknown operation pattern"
                )
            operation.postmortem = postmortem
            self.store.save_operation(operation)
            verdicts = {item.pattern_id: item for item in postmortem.pattern_verdicts}
            for pattern in operation.proposal.patterns:
                verdict = verdicts.get(pattern.pattern_id)
                pattern_status = verdict.status if verdict else PatternStatus.UNCERTAIN
                lesson = Lesson(
                    lesson_id=f"lesson_{uuid4().hex}",
                    operation_id=operation.operation_id,
                    account_id=operation.account_id,
                    pattern_id=pattern.pattern_id,
                    pattern_version=pattern.pattern_version,
                    symbol=operation.proposal.context.symbol,
                    market_type=operation.proposal.context.market_type,
                    side=operation.proposal.context.side,
                    timeframe=operation.proposal.context.timeframe,
                    market_regime=operation.proposal.context.market_regime,
                    outcome_status=operation.outcome.status,
                    lesson=postmortem.proposed_lesson,
                    failure_category=postmortem.failure_category,
                    pattern_status=pattern_status,
                    confidence=postmortem.confidence,
                )
                self.store.save_lesson(lesson)
        if (
            previous_postmortem is None
            or previous_postmortem.model_dump(exclude={"recorded_at"})
            != postmortem.model_dump(exclude={"recorded_at"})
        ):
            self._notify(NotificationEvent.POSTMORTEM_RECORDED, operation)
        return operation

    def pattern_context(
        self,
        pattern_id: str,
        *,
        symbol: str | None = None,
        market_type: str | None = None,
        side: str | None = None,
        limit: int = 50,
        execution_plan_run_id: str | None = None,
    ) -> PatternContext:
        self._prepare_execution_plan_read(
            execution_plan_run_id,
            symbol,
            market_type=market_type,
            pattern_id=pattern_id,
            side=side,
        )
        account_id = self._account_id()
        lessons = self.store.list_lessons(
            account_id=account_id,
            pattern_id=pattern_id,
            symbol=symbol,
            market_type=market_type,
            side=side,
            limit=limit,
        )
        operations = [
            self.store.get_operation(lesson.operation_id, account_id=account_id)
            for lesson in lessons
        ]
        outcomes = [
            operation.outcome for operation in operations if operation and operation.outcome
        ]
        wins = sum(
            1
            for outcome in outcomes
            if outcome.status in {OutcomeStatus.WIN, OutcomeStatus.PARTIAL_WIN}
        )
        losses = sum(
            1
            for outcome in outcomes
            if outcome.status in {OutcomeStatus.LOSS, OutcomeStatus.PARTIAL_LOSS}
        )
        net_pnl = sum((outcome.pnl_net for outcome in outcomes), Decimal("0"))
        context = PatternContext(
            pattern_id=pattern_id,
            symbol=symbol,
            market_type=market_type,
            side=side,
            total_cases=len(outcomes),
            wins=wins,
            losses=losses,
            net_pnl_quote=net_pnl,
            lessons=lessons,
        )
        read_types = (ExecutionPlanReadType.PATTERN_CONTEXT,)
        if execution_plan_run_id:
            run = self.store.get_execution_plan_run(
                execution_plan_run_id, account_id=self._account_id()
            )
            if run and ExecutionPlanReadType.LESSONS in run.required_reads:
                read_types = (*read_types, ExecutionPlanReadType.LESSONS)
        self._record_execution_plan_read(execution_plan_run_id, read_types, symbol, True)
        return context

    def lessons(
        self,
        *,
        pattern_id: str | None = None,
        symbol: str | None = None,
        market_type: str | None = None,
        side: str | None = None,
        limit: int = 100,
        execution_plan_run_id: str | None = None,
    ) -> list[Lesson]:
        self._prepare_execution_plan_read(
            execution_plan_run_id,
            symbol,
            market_type=market_type,
            pattern_id=pattern_id,
            require_pattern_id=True,
            side=side,
        )
        lessons = self.store.list_lessons(
            account_id=self._account_id(),
            pattern_id=pattern_id,
            symbol=symbol,
            market_type=market_type,
            side=side,
            limit=limit,
        )
        self._record_execution_plan_read(
            execution_plan_run_id,
            (ExecutionPlanReadType.LESSONS,),
            symbol,
            True,
        )
        return lessons
