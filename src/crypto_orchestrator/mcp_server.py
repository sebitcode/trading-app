from __future__ import annotations

import json
from datetime import timedelta

from mcp.server.mcpserver import MCPServer

from .accounts import CredentialVaultError, UnsupportedCredentialProvider
from .models import (
    CredentialPayload,
    ExecutionPlanCreateRequest,
    ExecutionPlanRunRequest,
    ExecutionPlanUpdateRequest,
    MarketType,
    OutcomeInput,
    PositionExitPolicy,
    PositionMonitorStateInput,
    PositionReviewInput,
    PostmortemInput,
    TradeProposal,
    WorkflowCreateRequest,
    WorkflowPhase,
    WorkflowRunRequest,
    WorkflowUpdateRequest,
    jsonable,
    utc_now,
)
from .service import ConflictError, NotFoundError, RiskRejected, TradingService


def build_mcp_server(service: TradingService) -> MCPServer:
    mcp = MCPServer(
        "Crypto Orchestrator",
        description="Market intelligence and paper-trading orchestration tools.",
        instructions=(
            "Use read-only context before proposing paper trades. Every proposal must include "
            "evidence, scope, invalidation conditions, and pattern hypotheses. Candidate lessons "
            "are not validated strategy rules. Live trading is not exposed by this server. "
            "RSS is the primary free news source; optional CryptoPanic news supplements RSS "
            "only when CRYPTOPANIC_API_ENABLED is true and an auth token is configured. "
            "CryptoPanic vote counts are crowd context, not truth or a standalone signal; "
            "X ingestion is disabled by default. "
            "The authenticated account may define named workflows; run their before_operation "
            "phase before proposing and their after_operation phase after recording an outcome. "
            "The authenticated account may also define named execution plans. Read an active "
            "plan and run its preflight before following its ordered paper-only steps. "
            "A plan run returns a durable run_id and covers every symbol in the plan; pass "
            "that run_id to each exact-symbol market, news, positioning, pattern, or lesson "
            "read, using the plan market type/timeframe and pattern id where applicable, "
            "so the server can register fresh provenance and reject mismatched context. "
            "Plan-bound proposals must "
            "include both execution_plan_name and execution_plan_run_id; unbound proposals "
            "remain subject only to global risk checks. Use get_execution_plan_run to inspect "
            "safe run status and receipts. "
            "Use list_operations and get_operation to monitor and evaluate account operations. "
            "Open paper positions are persistent records; a separate supervisor may monitor "
            "their protective exit policies without an AI session. Before any discretionary "
            "adjustment or forced close, call prepare_position_review, analyze its fresh market, "
            "news, fundamental-event, and derivatives context, then submit_position_review. "
            "Only a fresh submitted review authorizes update_position_exit_policy or "
            "force_close_position. Protective supervisor exits never wait for the AI. "
            "Before proposing a paper trade, read get_crypto_news and get_derivatives_positioning. "
            "Positioning data is aggregate context (open interest, funding, long/short ratios, "
            "and taker flow), not individual leverage or a standalone trading signal. "
            "Use evaluate_strategy to score mirrored long/short candidates by regime; use "
            "run_strategy_cycle only for the selected account's active paper plan. "
            "Workflow instructions guide the agent but never bypass server-side risk checks. "
            "Account configuration tools can store encrypted provider credentials and test "
            "Telegram, but never return secret values. "
            "When configured for the authenticated account, Telegram receives outbound updates "
            "for paper execution, outcomes, and postmortem conclusions; it cannot control trades."
        ),
        version="0.1.0",
    )

    @mcp.tool()
    def get_system_status() -> dict[str, object]:
        """Return the current safety and execution status."""

        return service.health()

    @mcp.tool()
    def get_account_configuration() -> dict[str, object]:
        """Return the authenticated account identity and safe configuration metadata."""

        if service.account_manager is None:
            raise RuntimeError("account configuration is unavailable on this MCP transport")
        account = service.account_manager.get_account(service._account_id())
        if account is None:
            return {"account_id": service._account_id(), "account": None}
        return {"account_id": account.account_id, "account": jsonable(account)}

    @mcp.tool()
    def list_configured_credentials() -> list[dict[str, object]]:
        """List configured provider metadata without returning credential values."""

        if service.account_manager is None:
            raise RuntimeError("account configuration is unavailable on this MCP transport")
        return [
            jsonable(metadata)
            for metadata in service.account_manager.list_credentials(service._account_id())
        ]

    @mcp.tool()
    def configure_provider_credentials(
        provider: str, values: dict[str, str]
    ) -> dict[str, object]:
        """Encrypt and save credentials for one provider in the authenticated account."""

        if service.account_manager is None:
            raise RuntimeError("account configuration is unavailable on this MCP transport")
        try:
            metadata = service.account_manager.save_credential(
                service._account_id(),
                provider,
                CredentialPayload(values=values),
            )
        except (CredentialVaultError, UnsupportedCredentialProvider) as exc:
            return {"saved": False, "error": str(exc)}
        return {"saved": True, "credential": jsonable(metadata)}

    @mcp.tool()
    def remove_provider_credentials(provider: str) -> dict[str, object]:
        """Remove one provider credential set from the authenticated account."""

        if service.account_manager is None:
            raise RuntimeError("account configuration is unavailable on this MCP transport")
        return {
            "provider": provider.strip().lower(),
            "removed": service.account_manager.delete_credential(
                service._account_id(), provider
            ),
        }

    @mcp.tool()
    def test_telegram_notifications() -> dict[str, object]:
        """Send a Telegram test notification for the authenticated account."""

        if service.notification_service is None:
            raise RuntimeError("notifications are unavailable on this MCP transport")
        return jsonable(service.notification_service.send_test(service._account_id()))

    @mcp.tool()
    def list_workflows(limit: int = 100) -> list[dict[str, object]]:
        """List named workflows available to the authenticated account."""

        return [jsonable(workflow) for workflow in service.list_workflows(limit)]

    @mcp.tool()
    def get_workflow(workflow_name: str) -> dict[str, object]:
        """Retrieve a named workflow and its before/after agent instructions."""

        return jsonable(service.get_workflow_by_name(workflow_name))

    @mcp.tool()
    def create_workflow(workflow: WorkflowCreateRequest) -> dict[str, object]:
        """Create a named before/after workflow for the authenticated account."""

        try:
            return {"saved": True, "workflow": jsonable(service.create_workflow(workflow))}
        except ConflictError as exc:
            return {"saved": False, "error": str(exc)}

    @mcp.tool()
    def update_workflow(
        workflow_id: str, workflow: WorkflowUpdateRequest
    ) -> dict[str, object]:
        """Update a named workflow and increment its version."""

        try:
            return {
                "saved": True,
                "workflow": jsonable(service.update_workflow(workflow_id, workflow)),
            }
        except (ConflictError, NotFoundError) as exc:
            return {"saved": False, "error": str(exc)}

    @mcp.tool()
    def remove_workflow(workflow_id: str) -> dict[str, object]:
        """Remove a workflow from the authenticated account."""

        try:
            service.delete_workflow(workflow_id)
        except NotFoundError as exc:
            return {"deleted": False, "error": str(exc)}
        return {"deleted": True, "workflow_id": workflow_id}

    @mcp.tool()
    def list_execution_plans(limit: int = 100) -> list[dict[str, object]]:
        """List paper-only execution plans available to the authenticated account."""

        return [jsonable(plan) for plan in service.list_execution_plans(limit)]

    @mcp.tool()
    def get_execution_plan(plan_name: str) -> dict[str, object]:
        """Read a named paper-only execution plan and its ordered agent steps."""

        return jsonable(service.get_execution_plan_by_name(plan_name))

    @mcp.tool()
    def create_execution_plan(plan: ExecutionPlanCreateRequest) -> dict[str, object]:
        """Create an account-scoped paper-only execution plan."""

        try:
            return {"saved": True, "plan": jsonable(service.create_execution_plan(plan))}
        except ConflictError as exc:
            return {"saved": False, "error": str(exc)}

    @mcp.tool()
    def update_execution_plan(
        plan_id: str, plan: ExecutionPlanUpdateRequest
    ) -> dict[str, object]:
        """Update an execution plan and increment its version."""

        try:
            return {
                "saved": True,
                "plan": jsonable(service.update_execution_plan(plan_id, plan)),
            }
        except (ConflictError, NotFoundError) as exc:
            return {"saved": False, "error": str(exc)}

    @mcp.tool()
    def remove_execution_plan(plan_id: str) -> dict[str, object]:
        """Remove an execution plan from the authenticated account."""

        try:
            service.delete_execution_plan(plan_id)
        except NotFoundError as exc:
            return {"deleted": False, "error": str(exc)}
        return {"deleted": True, "plan_id": plan_id}

    @mcp.tool()
    async def run_execution_plan(
        plan_name: str,
        symbol: str | None = None,
        duration_minutes: int | None = None,
        target_operations: int | None = None,
    ) -> dict[str, object]:
        """Run plan preflight and its before-workflow without silently placing a trade."""

        result = await service.run_execution_plan(
            ExecutionPlanRunRequest(
                plan_name=plan_name,
                symbol=symbol,
                duration_minutes=duration_minutes,
                target_operations=target_operations,
            )
        )
        return jsonable(result)

    @mcp.tool()
    def get_execution_plan_run(run_id: str) -> dict[str, object]:
        """Return safe status and read receipts for an account-scoped plan run."""

        return jsonable(service.get_execution_plan_run(run_id))

    @mcp.tool()
    async def run_workflow(
        workflow_name: str,
        phase: WorkflowPhase,
        operation_id: str | None = None,
        symbol: str | None = None,
        pattern_id: str | None = None,
        execution_plan_run_id: str | None = None,
    ) -> dict[str, object]:
        """Run a named workflow phase and return its query outputs and instructions."""

        result = await service.run_workflow(
            WorkflowRunRequest(
                workflow_name=workflow_name,
                phase=phase,
                operation_id=operation_id,
                symbol=symbol,
                pattern_id=pattern_id,
                execution_plan_run_id=execution_plan_run_id,
            )
        )
        return jsonable(result)

    @mcp.tool()
    def get_pattern_context(
        pattern_id: str,
        symbol: str | None = None,
        market_type: str | None = None,
        side: str | None = None,
        limit: int = 50,
        execution_plan_run_id: str | None = None,
    ) -> dict[str, object]:
        """Retrieve comparable operation outcomes and candidate lessons for a pattern."""

        return jsonable(
            service.pattern_context(
                pattern_id,
                symbol=symbol,
                market_type=market_type,
                side=side,
                limit=limit,
                execution_plan_run_id=execution_plan_run_id,
            )
        )

    @mcp.tool()
    def get_lessons(
        pattern_id: str | None = None,
        symbol: str | None = None,
        market_type: str | None = None,
        side: str | None = None,
        limit: int = 100,
        execution_plan_run_id: str | None = None,
    ) -> list[dict[str, object]]:
        """Retrieve account-scoped candidate lessons and register a plan read when bound."""

        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return [
            jsonable(lesson)
            for lesson in service.lessons(
                pattern_id=pattern_id,
                symbol=symbol,
                market_type=market_type,
                side=side,
                limit=limit,
                execution_plan_run_id=execution_plan_run_id,
            )
        ]

    @mcp.tool()
    def list_operations(limit: int = 100) -> list[dict[str, object]]:
        """List account-scoped paper operations for monitoring and evaluation."""

        return [jsonable(operation) for operation in service.list_operations(limit)]

    @mcp.tool()
    def get_operation(operation_id: str) -> dict[str, object]:
        """Read one account-scoped operation, including its plan snapshot and outcome."""

        return jsonable(service.get_operation(operation_id))

    @mcp.tool()
    def record_position_observation(
        operation_id: str, observation: PositionMonitorStateInput
    ) -> dict[str, object]:
        """Persist durable profit-band timing state for an open paper position."""

        try:
            return {
                "saved": True,
                "operation": jsonable(
                    service.record_position_observation(operation_id, observation)
                ),
            }
        except (ConflictError, NotFoundError) as exc:
            return {"saved": False, "error": str(exc)}

    @mcp.tool()
    async def prepare_position_review(operation_id: str) -> dict[str, object]:
        """Fetch fresh context for an AI review without changing the position."""

        try:
            return {
                "prepared": True,
                "review": jsonable(await service.prepare_position_review(operation_id)),
            }
        except (ConflictError, NotFoundError) as exc:
            return {"prepared": False, "error": str(exc)}

    @mcp.tool()
    def get_position_review(review_id: str) -> dict[str, object]:
        """Read a prepared or submitted position review and its evidence."""

        return jsonable(service.get_position_review(review_id))

    @mcp.tool()
    def list_position_reviews(operation_id: str, limit: int = 20) -> list[dict[str, object]]:
        """List the most recent context reviews for one position."""

        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return [
            jsonable(review)
            for review in service.list_position_reviews(operation_id, limit)
        ]

    @mcp.tool()
    def submit_position_review(
        review_id: str, review: PositionReviewInput
    ) -> dict[str, object]:
        """Submit AI analysis after reviewing the fresh position context."""

        try:
            return {
                "submitted": True,
                "review": jsonable(service.submit_position_review(review_id, review)),
            }
        except (ConflictError, NotFoundError, ValueError) as exc:
            return {"submitted": False, "error": str(exc)}

    @mcp.tool()
    def update_position_exit_policy(
        operation_id: str, review_id: str, policy: PositionExitPolicy
    ) -> dict[str, object]:
        """Apply a new exit policy only after a fresh AI adjustment review."""

        try:
            return {
                "updated": True,
                "operation": jsonable(
                    service.update_position_exit_policy(operation_id, review_id, policy)
                ),
            }
        except (ConflictError, NotFoundError, ValueError) as exc:
            return {"updated": False, "error": str(exc)}

    @mcp.tool()
    async def force_close_position(operation_id: str, review_id: str) -> dict[str, object]:
        """Force-close a paper position only after a fresh AI force-close review."""

        try:
            return {
                "closed": True,
                "operation": jsonable(
                    await service.force_close_after_review(operation_id, review_id)
                ),
            }
        except (ConflictError, NotFoundError, ValueError) as exc:
            return {"closed": False, "error": str(exc)}

    @mcp.tool()
    async def get_market_snapshot(
        symbol: str,
        market_type: MarketType = MarketType.SPOT,
        timeframe: str = "1m",
        limit: int = 20,
        execution_plan_run_id: str | None = None,
    ) -> dict[str, object]:
        """Retrieve normalized prices, 24h movement, volume, and recent candles."""

        return jsonable(
            await service.market_snapshot(
                symbol,
                market_type,
                timeframe,
                limit,
                execution_plan_run_id=execution_plan_run_id,
            )
        )

    @mcp.tool()
    async def get_derivatives_positioning(
        symbol: str,
        period: str | None = None,
        limit: int | None = None,
        execution_plan_run_id: str | None = None,
    ) -> dict[str, object]:
        """Retrieve public aggregate futures positioning, leverage proxies, and their trends."""

        if limit is not None and not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return jsonable(
            await service.derivatives_positioning(
                symbol,
                period,
                limit,
                execution_plan_run_id=execution_plan_run_id,
            )
        )

    @mcp.tool()
    async def get_crypto_news(
        symbol: str | None = None,
        lookback_minutes: int = 1_440,
        limit: int = 20,
        execution_plan_run_id: str | None = None,
    ) -> dict[str, object]:
        """Retrieve timestamped news from configured RSS and optional CryptoPanic sources."""

        if not 1 <= lookback_minutes <= 30 * 24 * 60:
            raise ValueError("lookback_minutes must be between 1 and 43200")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        since = utc_now() - timedelta(minutes=lookback_minutes)
        return jsonable(
            await service.crypto_news(
                symbol,
                since,
                limit,
                execution_plan_run_id=execution_plan_run_id,
            )
        )

    @mcp.tool()
    async def get_x_posts(
        symbol: str | None = None,
        lookback_minutes: int = 1_440,
        limit: int = 20,
    ) -> dict[str, object]:
        """Retrieve legacy X posts; unavailable unless X_API_ENABLED is enabled."""

        if not 1 <= lookback_minutes <= 7 * 24 * 60:
            raise ValueError("lookback_minutes must be between 1 and 10080")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        since = utc_now() - timedelta(minutes=lookback_minutes)
        return jsonable(await service.x_posts(symbol, since, limit))

    @mcp.tool()
    async def evaluate_strategy(
        plan_name: str,
        symbol: str,
        execution_plan_run_id: str,
        lookback_minutes: int = 1_440,
        candle_limit: int = 100,
    ) -> dict[str, object]:
        """Score technical, derivatives, sentiment, and event fundamentals for both sides."""

        return jsonable(
            await service.evaluate_strategy(
                plan_name,
                symbol,
                execution_plan_run_id,
                lookback_minutes=lookback_minutes,
                candle_limit=candle_limit,
            )
        )

    @mcp.tool()
    async def run_strategy_cycle(
        plan_name: str,
        symbol: str,
        execution_plan_run_id: str,
    ) -> dict[str, object]:
        """Evaluate and execute at most one risk-checked paper trade for this cycle."""

        return await service.run_strategy_cycle(plan_name, symbol, execution_plan_run_id)

    @mcp.tool()
    def propose_paper_trade(proposal: TradeProposal) -> dict[str, object]:
        """Record a risk-checked paper proposal, optionally bound to a workflow and plan."""

        try:
            return {"accepted": True, "operation": jsonable(service.create_proposal(proposal))}
        except RiskRejected as exc:
            return {"accepted": False, "risk_check": jsonable(exc.check)}
        except (ConflictError, NotFoundError) as exc:
            return {"accepted": False, "error": str(exc)}

    @mcp.tool()
    def execute_paper_trade(operation_id: str) -> dict[str, object]:
        """Execute a previously accepted paper proposal and notify the account's Telegram chats."""

        return jsonable(service.execute_paper(operation_id))

    @mcp.tool()
    def record_operation_outcome(operation_id: str, outcome: OutcomeInput) -> dict[str, object]:
        """Close a paper operation, record outcome facts, and notify Telegram chats."""

        return jsonable(service.close_operation(operation_id, outcome))

    @mcp.tool()
    def record_agent_postmortem(
        operation_id: str, postmortem: PostmortemInput
    ) -> dict[str, object]:
        """Record conclusions and candidate lessons, then notify the account's Telegram chats."""

        return jsonable(service.record_postmortem(operation_id, postmortem))

    @mcp.resource("trading://patterns/{pattern_id}/context")
    def pattern_context_resource(pattern_id: str) -> str:
        """Expose pattern history as a readable MCP resource."""

        context = service.pattern_context(pattern_id)
        return json.dumps(jsonable(context), indent=2)

    @mcp.resource("trading://execution-plans/{plan_name}")
    def execution_plan_resource(plan_name: str) -> str:
        """Expose a named execution plan as a readable MCP resource."""

        plan = service.get_execution_plan_by_name(plan_name)
        return json.dumps(jsonable(plan), indent=2)

    @mcp.prompt()
    def review_operation(operation_id: str) -> str:
        """Create a structured review prompt for a closed operation."""

        operation = service.get_operation(operation_id)
        return (
            "Review this operation without rewriting history. Compare the original thesis, "
            "pattern hypothesis, risk plan, execution facts, and outcome. Separate agent "
            "hypotheses from evidence-backed causes, then propose a candidate lesson.\n\n"
            f"Operation snapshot:\n{json.dumps(jsonable(operation), indent=2)}"
        )

    return mcp
