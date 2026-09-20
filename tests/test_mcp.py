from __future__ import annotations

import asyncio

from cryptography.fernet import Fernet

from crypto_orchestrator.account_context import account_scope
from crypto_orchestrator.accounts import AccountManager
from crypto_orchestrator.config import Settings
from crypto_orchestrator.mcp_server import build_mcp_server
from crypto_orchestrator.service import TradingService
from crypto_orchestrator.store import SQLiteStore


def test_mcp_catalog_contains_safe_tools_and_resources(service) -> None:
    mcp = build_mcp_server(service)

    async def list_features():
        return (
            await mcp.list_tools(),
            await mcp.list_resource_templates(),
            await mcp.list_prompts(),
        )

    tools, resources, prompts = asyncio.run(list_features())
    tool_names = {tool.name for tool in tools}
    resource_templates = {resource.uri_template for resource in resources}
    prompt_names = {prompt.name for prompt in prompts}

    assert "get_pattern_context" in tool_names
    assert "get_market_snapshot" in tool_names
    assert "get_derivatives_positioning" in tool_names
    assert "get_crypto_news" in tool_names
    assert "evaluate_strategy" in tool_names
    assert "run_strategy_cycle" in tool_names
    assert "get_x_posts" in tool_names
    assert "list_workflows" in tool_names
    assert "get_workflow" in tool_names
    assert "run_workflow" in tool_names
    assert "list_execution_plans" in tool_names
    assert "get_execution_plan_symbols" in tool_names
    assert "update_execution_plan_symbols" in tool_names
    assert "get_execution_plan" in tool_names
    assert "create_execution_plan" in tool_names
    assert "update_execution_plan" in tool_names
    assert "remove_execution_plan" in tool_names
    assert "run_execution_plan" in tool_names
    assert "get_execution_plan_run" in tool_names
    assert "list_operations" in tool_names
    assert "get_operation" in tool_names
    assert "record_agent_investigation" in tool_names
    assert "list_agent_investigations" in tool_names
    assert "get_agent_investigation" in tool_names
    assert "record_position_observation" in tool_names
    assert "prepare_position_review" in tool_names
    assert "get_position_review" in tool_names
    assert "list_position_reviews" in tool_names
    assert "submit_position_review" in tool_names
    assert "update_position_exit_policy" in tool_names
    assert "force_close_position" in tool_names
    assert "propose_paper_trade" in tool_names
    assert "get_lessons" in tool_names
    assert "record_agent_postmortem" in tool_names
    assert "place_live_order" not in tool_names
    assert "trading://patterns/{pattern_id}/context" in resource_templates
    assert "trading://execution-plans/{plan_name}" in resource_templates
    assert "review_operation" in prompt_names


def test_mcp_can_configure_credentials_and_named_workflows(tmp_path) -> None:
    settings = Settings(
        db_path=tmp_path / "mcp-configuration.db",
        credential_encryption_key=Fernet.generate_key().decode(),
    )
    store = SQLiteStore(settings.db_path)
    account_manager = AccountManager(settings, store)
    account = account_manager.create_account("MCP account")
    service = TradingService(settings, store, account_manager=account_manager)
    mcp = build_mcp_server(service)

    async def configure():
        with account_scope(account.account_id):
            credential = await mcp.call_tool(
                "configure_provider_credentials",
                {
                    "provider": "x",
                    "values": {
                        "bearer_token": "secret-x",
                        "influencer_usernames": "alice,bob",
                    },
                },
            )
            credentials = await mcp.call_tool("list_configured_credentials", {})
            workflow = await mcp.call_tool(
                "create_workflow",
                {
                    "workflow": {
                        "name": "MCP review",
                        "description": "Named agent playbook",
                        "before_steps": [
                            {
                                "name": "Agent rules",
                                "type": "agent_instruction",
                                "parameters": {
                                    "instruction": "Compare evidence before proposing."
                                },
                            }
                        ],
                        "after_steps": [],
                        "enabled": True,
                    }
                },
            )
            workflows = await mcp.call_tool("list_workflows", {})
            plan = await mcp.call_tool(
                "create_execution_plan",
                {
                    "plan": {
                        "name": "MCP paper plan",
                        "description": "Account-scoped paper execution plan.",
                        "objective": "Collect controlled operations for later evaluation.",
                        "strategy_version": "ema_rsi_atr_trend_v1",
                        "pattern_id": "ema_rsi_atr_trend_v1",
                        "mode": "paper",
                        "capital_quote": "1000",
                        "max_trade_notional_quote": "1000",
                        "risk_per_trade_quote": "10",
                        "max_daily_loss_quote": "30",
                        "max_open_operations": 1,
                        "max_duration_minutes": 60,
                        "target_operations": 5,
                        "symbols": ["BTCUSDT", "ZECUSDT"],
                        "market_type": "perpetual",
                        "timeframe": "1m",
                        "allowed_sides": ["long", "short"],
                        "data_sources": ["binance_public", "rss"],
                        "entry_rules": ["Require the configured strategy confirmation."],
                        "exit_rules": ["Respect stop, target, and time limits."],
                        "risk_rules": ["Never exceed the plan risk budget."],
                        "evaluation_metrics": ["net_expectancy"],
                        "steps": [
                            {
                                "step_id": "pstep_decision",
                                "name": "Agent decision",
                                "action": "agent_decision",
                                "instructions": "Explain the evidence before proposing a trade.",
                            }
                        ],
                        "status": "active",
                    }
                },
            )
            plans = await mcp.call_tool("list_execution_plans", {})
            read_plan = await mcp.call_tool(
                "get_execution_plan", {"plan_name": "MCP paper plan"}
            )
            selected_symbols = await mcp.call_tool(
                "get_execution_plan_symbols", {"plan_name": "MCP paper plan"}
            )
            updated_symbols = await mcp.call_tool(
                "update_execution_plan_symbols",
                {
                    "plan_name": "MCP paper plan",
                    "symbols": ["BTC/USDT", "ZEC/USDT"],
                },
            )
            plan_run = await mcp.call_tool(
                "run_execution_plan",
                {"plan_name": "MCP paper plan", "symbol": "BTC/USDT"},
            )
            removed = await mcp.call_tool(
                "remove_provider_credentials", {"provider": "x"}
            )
        return (
            credential,
            credentials,
            workflow,
            workflows,
            plan,
            plans,
            read_plan,
            selected_symbols,
            updated_symbols,
            plan_run,
            removed,
        )

    (
        credential,
        credentials,
        workflow,
        workflows,
        plan,
        plans,
        read_plan,
        selected_symbols,
        updated_symbols,
        plan_run,
        removed,
    ) = asyncio.run(configure())

    assert credential.structured_content["saved"] is True
    assert "secret-x" not in str(credential.structured_content)
    assert len(credentials.structured_content["result"]) == 1
    assert credentials.structured_content["result"][0]["provider"] == "x"
    assert workflow.structured_content["saved"] is True
    assert workflow.structured_content["workflow"]["name"] == "MCP review"
    assert workflows.structured_content["result"][0]["name"] == "MCP review"
    assert plan.structured_content["saved"] is True
    assert plan.structured_content["plan"]["status"] == "active"
    assert plans.structured_content["result"][0]["name"] == "MCP paper plan"
    assert read_plan.structured_content["name"] == "MCP paper plan"
    assert selected_symbols.structured_content == {
        "plan_name": "MCP paper plan",
        "plan_version": 1,
        "status": "active",
        "symbols": ["BTCUSDT", "ZECUSDT"],
        "market_type": "perpetual",
        "timeframe": "1m",
        "selection_source": "user_execution_plan",
    }
    assert updated_symbols.structured_content["saved"] is True
    assert updated_symbols.structured_content["plan"]["version"] == 2
    assert updated_symbols.structured_content["plan"]["symbols"] == ["BTCUSDT", "ZECUSDT"]
    assert plan_run.structured_content["status"] == "ready"
    assert removed.structured_content == {"provider": "x", "removed": True}


def test_mcp_can_persist_periodic_investigation(tmp_path) -> None:
    settings = Settings(db_path=tmp_path / "mcp-investigation.db")
    store = SQLiteStore(settings.db_path)
    account_manager = AccountManager(settings, store)
    account = account_manager.create_account("MCP investigation account")
    service = TradingService(settings, store, account_manager=account_manager)
    mcp = build_mcp_server(service)

    async def record_and_read():
        with account_scope(account.account_id):
            saved = await mcp.call_tool(
                "record_agent_investigation",
                {
                    "investigation": {
                        "idempotency_key": "mcp-hourly-001",
                        "task_name": "Hourly crypto monitor",
                        "agent_id": "mcp-agent",
                        "symbol": "BTC/USDT",
                        "decision": "no_trade",
                        "summary": "The setup did not meet the configured threshold.",
                        "reason": "no_eligible_candidate",
                        "findings": {"market_regime": "range"},
                    }
                },
            )
            listed = await mcp.call_tool("list_agent_investigations", {"limit": 10})
        return saved, listed

    saved, listed = asyncio.run(record_and_read())

    assert saved.structured_content["saved"] is True
    assert saved.structured_content["investigation"]["task_name"] == "Hourly crypto monitor"
    assert "secret" not in str(saved.structured_content).lower()
    assert len(listed.structured_content["result"]) == 1
