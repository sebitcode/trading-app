from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

from .account_context import get_current_account_id
from .accounts import (
    AccountManager,
    BootstrapError,
    CredentialVaultError,
    UnsupportedCredentialProvider,
)
from .auth import AccountMiddleware
from .config import Settings
from .mcp_server import build_mcp_server
from .models import (
    Account,
    AccountCreated,
    AccountCreateRequest,
    CredentialMetadata,
    CredentialPayload,
    DerivativesPositioningResponse,
    ExecutionPlan,
    ExecutionPlanCreateRequest,
    ExecutionPlanRunReceipt,
    ExecutionPlanRunRequest,
    ExecutionPlanRunResult,
    ExecutionPlanUpdateRequest,
    Lesson,
    MarketDataResponse,
    MarketType,
    NotificationResult,
    OperationRecord,
    OutcomeInput,
    PatternContext,
    PostmortemInput,
    RiskCheck,
    SignalResponse,
    StrategyEvaluation,
    TradeProposal,
    Workflow,
    WorkflowCreateRequest,
    WorkflowRunRequest,
    WorkflowRunResult,
    WorkflowUpdateRequest,
)
from .notifications import NotificationService
from .service import ConflictError, NotFoundError, RiskRejected, TradingService
from .store import SQLiteStore

FRONTEND_DIR = Path(__file__).with_name("static")


def create_app(settings: Settings | None = None, service: TradingService | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if service is None:
        store = SQLiteStore(settings.db_path)
        account_manager = AccountManager(settings, store)
        notification_service = NotificationService(settings, account_manager)
        service = TradingService(
            settings,
            store,
            account_manager=account_manager,
            default_account_id=settings.default_account_id,
            notification_service=notification_service,
        )
    else:
        account_manager = AccountManager(settings, service.store)
        service.attach_account_manager(
            account_manager, default_account_id=settings.default_account_id
        )
        if service.notification_service is None:
            service.attach_notification_service(NotificationService(settings, account_manager))
        notification_service = service.notification_service
    mcp = build_mcp_server(service)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Crypto Orchestrator",
        version="0.1.0",
        description="Safety-first crypto market intelligence and paper-trading API.",
        lifespan=lifespan,
    )

    transport_security = TransportSecuritySettings(
        allowed_hosts=list(settings.mcp_allowed_hosts),
        allowed_origins=list(settings.mcp_allowed_origins),
    )
    app.mount(
        "/mcp",
        mcp.streamable_http_app(
            json_response=True,
            streamable_http_path="/",
            transport_security=transport_security,
        ),
    )
    app.state.service = service
    app.state.account_manager = account_manager
    app.add_middleware(AccountMiddleware, account_manager=account_manager)

    def current_account_id() -> str:
        account_id = get_current_account_id()
        if not account_id:
            raise HTTPException(status_code=401, detail="authentication required")
        return account_id

    @app.post(
        "/api/v1/accounts",
        response_model=AccountCreated,
        status_code=status.HTTP_201_CREATED,
    )
    def create_account(
        payload: AccountCreateRequest,
        x_bootstrap_token: str | None = Header(default=None, alias="X-Bootstrap-Token"),
    ) -> AccountCreated:
        try:
            return account_manager.create_account(payload.name, x_bootstrap_token)
        except BootstrapError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/api/v1/account", response_model=Account)
    def get_account() -> Account:
        account = account_manager.get_account(current_account_id())
        if not account or not account.active:
            raise HTTPException(status_code=401, detail="account is not active")
        return account

    @app.get("/api/v1/account/credentials", response_model=list[CredentialMetadata])
    def list_credentials() -> list[CredentialMetadata]:
        return account_manager.list_credentials(current_account_id())

    @app.put(
        "/api/v1/account/credentials/{provider}",
        response_model=CredentialMetadata,
    )
    def save_credential(provider: str, payload: CredentialPayload) -> CredentialMetadata:
        try:
            return account_manager.save_credential(current_account_id(), provider, payload)
        except UnsupportedCredentialProvider as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except CredentialVaultError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.delete("/api/v1/account/credentials/{provider}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_credential(provider: str) -> Response:
        if not account_manager.delete_credential(current_account_id(), provider):
            raise HTTPException(status_code=404, detail="credential not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/api/v1/account/notifications/telegram/test",
        response_model=NotificationResult,
    )
    def test_telegram_notification() -> NotificationResult:
        return notification_service.send_test(current_account_id())

    @app.get("/api/v1/workflows", response_model=list[Workflow])
    def list_workflows(limit: int = Query(default=100, ge=1, le=100)) -> list[Workflow]:
        return service.list_workflows(limit)

    @app.post(
        "/api/v1/workflows",
        response_model=Workflow,
        status_code=status.HTTP_201_CREATED,
    )
    def create_workflow(payload: WorkflowCreateRequest) -> Workflow:
        try:
            return service.create_workflow(payload)
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/workflows/run", response_model=WorkflowRunResult)
    async def run_workflow(payload: WorkflowRunRequest) -> WorkflowRunResult:
        try:
            return await service.run_workflow(payload)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/workflows/{workflow_id}", response_model=Workflow)
    def get_workflow(workflow_id: str) -> Workflow:
        try:
            return service.get_workflow(workflow_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/v1/workflows/{workflow_id}", response_model=Workflow)
    def update_workflow(workflow_id: str, payload: WorkflowUpdateRequest) -> Workflow:
        try:
            return service.update_workflow(workflow_id, payload)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete(
        "/api/v1/workflows/{workflow_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_workflow(workflow_id: str) -> Response:
        try:
            service.delete_workflow(workflow_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/execution-plans", response_model=list[ExecutionPlan])
    def list_execution_plans(
        limit: int = Query(default=100, ge=1, le=100)
    ) -> list[ExecutionPlan]:
        return service.list_execution_plans(limit)

    @app.post(
        "/api/v1/execution-plans",
        response_model=ExecutionPlan,
        status_code=status.HTTP_201_CREATED,
    )
    def create_execution_plan(payload: ExecutionPlanCreateRequest) -> ExecutionPlan:
        try:
            return service.create_execution_plan(payload)
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/execution-plans/run", response_model=ExecutionPlanRunResult)
    async def run_execution_plan(payload: ExecutionPlanRunRequest) -> ExecutionPlanRunResult:
        try:
            return await service.run_execution_plan(payload)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/v1/execution-plan-runs/{run_id}",
        response_model=ExecutionPlanRunReceipt,
    )
    def get_execution_plan_run(run_id: str) -> ExecutionPlanRunReceipt:
        try:
            return service.get_execution_plan_run(run_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/execution-plans/{plan_id}", response_model=ExecutionPlan)
    def get_execution_plan(plan_id: str) -> ExecutionPlan:
        try:
            return service.get_execution_plan(plan_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/v1/execution-plans/{plan_id}", response_model=ExecutionPlan)
    def update_execution_plan(
        plan_id: str, payload: ExecutionPlanUpdateRequest
    ) -> ExecutionPlan:
        try:
            return service.update_execution_plan(plan_id, payload)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete(
        "/api/v1/execution-plans/{plan_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_execution_plan(plan_id: str) -> Response:
        try:
            service.delete_execution_plan(plan_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/health")
    def health() -> dict[str, object]:
        return service.health()

    @app.post("/api/v1/risk/check", response_model=RiskCheck)
    def check_risk(proposal: TradeProposal) -> RiskCheck:
        try:
            return service.evaluate_risk(proposal)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/market/snapshot", response_model=MarketDataResponse)
    async def market_snapshot(
        symbol: str = Query(min_length=3, max_length=40),
        market_type: MarketType = MarketType.SPOT,
        timeframe: str = Query(default="1m", min_length=1, max_length=20),
        limit: int = Query(default=20, ge=1, le=500),
        execution_plan_run_id: str | None = Query(
            default=None, min_length=10, max_length=100, pattern=r"^epr_[a-z0-9]+$"
        ),
    ) -> MarketDataResponse:
        try:
            return await service.market_snapshot(
                symbol,
                market_type,
                timeframe,
                limit,
                execution_plan_run_id=execution_plan_run_id,
            )
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/market/positioning", response_model=DerivativesPositioningResponse)
    async def derivatives_positioning(
        symbol: str = Query(min_length=3, max_length=40),
        period: str | None = Query(default=None, min_length=2, max_length=3),
        limit: int | None = Query(default=None, ge=1, le=100),
        execution_plan_run_id: str | None = Query(
            default=None, min_length=10, max_length=100, pattern=r"^epr_[a-z0-9]+$"
        ),
    ) -> DerivativesPositioningResponse:
        try:
            return await service.derivatives_positioning(
                symbol,
                period,
                limit,
                execution_plan_run_id=execution_plan_run_id,
            )
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/news", response_model=SignalResponse)
    async def crypto_news(
        symbol: str | None = Query(default=None, min_length=3, max_length=40),
        lookback_minutes: int = Query(default=1_440, ge=1, le=30 * 24 * 60),
        limit: int = Query(default=20, ge=1, le=100),
        execution_plan_run_id: str | None = Query(
            default=None, min_length=10, max_length=100, pattern=r"^epr_[a-z0-9]+$"
        ),
    ) -> SignalResponse:
        since = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
        try:
            return await service.crypto_news(
                symbol,
                since,
                limit,
                execution_plan_run_id=execution_plan_run_id,
            )
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/strategy/evaluate", response_model=StrategyEvaluation)
    async def evaluate_strategy(
        plan_name: str = Query(min_length=1, max_length=120),
        symbol: str = Query(min_length=3, max_length=40),
        execution_plan_run_id: str = Query(
            min_length=10, max_length=100, pattern=r"^epr_[a-z0-9]+$"
        ),
        lookback_minutes: int = Query(default=1_440, ge=1, le=30 * 24 * 60),
        candle_limit: int = Query(default=100, ge=30, le=500),
    ) -> StrategyEvaluation:
        try:
            return await service.evaluate_strategy(
                plan_name,
                symbol,
                execution_plan_run_id,
                lookback_minutes=lookback_minutes,
                candle_limit=candle_limit,
            )
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/strategy/cycle")
    async def run_strategy_cycle(
        plan_name: str = Query(min_length=1, max_length=120),
        symbol: str = Query(min_length=3, max_length=40),
        execution_plan_run_id: str = Query(
            min_length=10, max_length=100, pattern=r"^epr_[a-z0-9]+$"
        ),
    ) -> dict[str, object]:
        return await service.run_strategy_cycle(plan_name, symbol, execution_plan_run_id)

    @app.get("/api/v1/signals/x", response_model=SignalResponse)
    async def x_posts(
        symbol: str | None = Query(default=None, min_length=3, max_length=40),
        lookback_minutes: int = Query(default=1_440, ge=1, le=7 * 24 * 60),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> SignalResponse:
        since = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
        return await service.x_posts(symbol, since, limit)

    @app.post(
        "/api/v1/operations",
        response_model=OperationRecord,
        status_code=status.HTTP_201_CREATED,
    )
    def create_operation(proposal: TradeProposal) -> OperationRecord:
        try:
            return service.create_proposal(proposal)
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RiskRejected as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"message": str(exc), "risk_check": exc.check.model_dump(mode="json")},
            ) from exc

    @app.get("/api/v1/operations", response_model=list[OperationRecord])
    def list_operations(limit: int = Query(default=100, ge=1, le=500)) -> list[OperationRecord]:
        return service.list_operations(limit)

    @app.get("/api/v1/operations/{operation_id}", response_model=OperationRecord)
    def get_operation(operation_id: str) -> OperationRecord:
        try:
            return service.get_operation(operation_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/operations/{operation_id}/execute-paper", response_model=OperationRecord)
    def execute_paper(operation_id: str) -> OperationRecord:
        try:
            return service.execute_paper(operation_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RiskRejected as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "risk_check": exc.check.model_dump(mode="json")},
            ) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/operations/{operation_id}/outcome", response_model=OperationRecord)
    def close_operation(operation_id: str, outcome: OutcomeInput) -> OperationRecord:
        try:
            return service.close_operation(operation_id, outcome)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/operations/{operation_id}/postmortem", response_model=OperationRecord)
    def postmortem(operation_id: str, payload: PostmortemInput) -> OperationRecord:
        try:
            return service.record_postmortem(operation_id, payload)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/patterns/{pattern_id}/context", response_model=PatternContext)
    def pattern_context(
        pattern_id: str,
        symbol: str | None = None,
        market_type: str | None = None,
        side: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        execution_plan_run_id: str | None = Query(
            default=None, min_length=10, max_length=100, pattern=r"^epr_[a-z0-9]+$"
        ),
    ) -> PatternContext:
        try:
            return service.pattern_context(
                pattern_id,
                symbol=symbol,
                market_type=market_type,
                side=side,
                limit=limit,
                execution_plan_run_id=execution_plan_run_id,
            )
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/lessons", response_model=list[Lesson])
    def lessons(
        pattern_id: str | None = None,
        symbol: str | None = None,
        market_type: str | None = None,
        side: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        execution_plan_run_id: str | None = Query(
            default=None, min_length=10, max_length=100, pattern=r"^epr_[a-z0-9]+$"
        ),
    ) -> list[Lesson]:
        try:
            return service.lessons(
                pattern_id=pattern_id,
                symbol=symbol,
                market_type=market_type,
                side=side,
                limit=limit,
                execution_plan_run_id=execution_plan_run_id,
            )
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    return app


app = create_app()
