# Crypto Orchestrator

<p align="center">
  <img src="src/crypto_orchestrator/static/logo.svg" alt="Crypto Orchestrator logo" width="144">
</p>

Crypto Orchestrator is a safety-first foundation for a crypto trading platform. It exposes a domain API and an MCP server while keeping execution behind explicit paper-trading and risk controls.

## Current scope

- Spot and perpetual market contexts.
- Long and short thesis modelling.
- Structured evidence, scope, invalidation conditions, and pattern hypotheses.
- Paper execution only by default.
- SQLite operation ledger with immutable operation snapshots.
- Agent postmortems and candidate lessons.
- Pattern context retrieval for future agents.
- Read-only Binance spot/perpetual snapshots with 24h movement, volume, and candles.
- Read-only Binance USDⓈ-M aggregate derivatives positioning: open-interest trend,
  funding, global/top-trader long-short ratios, and taker buy/sell flow.
- Configurable RSS crypto-news ingestion with source and publication timestamps.
- RSS-first free intelligence mode for crypto-news ingestion.
- Partial-source error reporting so one unavailable provider does not hide other results.
- FastAPI HTTP API.
- MCP server over Streamable HTTP at `/mcp` and local stdio through the MCP SDK.
- Account-scoped HTTP/MCP ecosystems with isolated operations, risk state, idempotency, lessons, and provider credentials.
- Hashed opaque account bearer tokens and Fernet-encrypted per-account credential storage.
- Account-scoped outbound Telegram notifications for paper execution, outcomes, and postmortem conclusions.
- Account-scoped named workflows with before-operation and after-operation query phases.
- Account-scoped named execution plans with paper-only budgets, data-source limits, and ordered agent steps.
- Deterministic regime-aware strategy evaluation with mirrored long/short candidates,
  technical/derivatives/news-event scores, and separate core/exploratory paper tiers.
- Durable position exit policies with multiple take-profit limits, profitable price bands,
  dwell timers, and maximum holding durations.
- An independent paper-position supervisor that continues protective exits without the AI
  process, plus fresh multi-source position reviews required before discretionary changes.
- MCP strategy cycles and a local paper runner for repeated evaluation, execution, monitoring,
  outcomes, and postmortems.

Live exchange execution and model promotion remain disabled. Account creation is available in development; non-development deployments require an `ACCOUNT_BOOTSTRAP_TOKEN`.
X ingestion is disabled by default; data tools never accept provider credentials as tool parameters.

## Docker Compose

The Compose deployment exposes the API and UI on port `8000`, persists SQLite in a named volume, and pins the container to paper trading.

1. Create a local Compose environment file:

   ```bash
   cp .env.docker.example .env.compose
   ```

2. Fill `ACCOUNT_BOOTSTRAP_TOKEN` and `CREDENTIAL_ENCRYPTION_KEY` in `.env.compose`.
3. Build and start the service:

   ```bash
   docker compose --env-file .env.compose up --build -d
   ```

4. Verify the deployment:

   ```bash
   curl -fsS http://localhost:8000/health
   ```

Stop the container without deleting its SQLite volume with `docker compose --env-file .env.compose down`.

## Quick start

```bash
uv venv
uv sync --extra dev
cp .env.example .env
uv run uvicorn crypto_orchestrator.api:app --reload
```

The HTTP API is available at `http://127.0.0.1:8000`. OpenAPI is available at `/docs`, and MCP clients connect to `http://127.0.0.1:8000/mcp/`.

Open the account UI at the server root. It creates or opens an account and stores
provider credentials through the authenticated API. The access token is kept in browser
session storage only; it is not placed in local storage or in the URL. The UI supports
Binance and Telegram credentials, shows metadata without reading secrets back, and
can send a protected Telegram test notification.

### Named workflows

Each account can maintain multiple named workflows. A workflow contains ordered,
server-validated steps for before-operation and after-operation phases. Supported steps
are market snapshots, RSS crypto news, pattern context, candidate lessons, and
instructions returned to the agent. Agents select a workflow by name through the MCP
tools list_workflows, get_workflow, create_workflow, update_workflow, remove_workflow,
and run_workflow. Provider credentials and Telegram test notifications are also
configurable through the MCP tools list_configured_credentials,
configure_provider_credentials, remove_provider_credentials, and
test_telegram_notifications.

Workflow query parameters may use the context variables $context.symbol,
$context.pattern_id, and $context.operation_id. The server executes only the
allowlisted provider queries; instructions are guidance for the agent and cannot run
arbitrary code, call arbitrary URLs, or bypass risk checks. A proposal may include a
workflow_name so the operation stores the selected workflow snapshot for later review.
The initial account bootstrap still requires the public account-provisioning flow; all
configuration after authentication is account-scoped and available through MCP.

### Execution plans

Execution plans are reusable, account-scoped runbooks for other agents. Each plan stores
the strategy and pattern version, paper capital and risk limits, allowed symbols, market
type, timeframe, long/short sides, free data sources, evaluation metrics, and an ordered
list of safe MCP actions. Plans are paper-only and can reference existing before/after
workflows.

Allocation is explicit and enforced by the server: `max_trade_notional_quote *
max_open_operations` must not exceed `capital_quote`. The difference is an implicit
reserve, so a 1000 USDT plan can use three 300 USDT slots while retaining 100 USDT
uncommitted. The account-wide open notional is checked again before proposal and
execution, preventing another plan from consuming the same virtual capital. Plans can
also set `minimum_net_reward_risk_ratio`; the server calculates reward and stop risk
after two-sided fees and slippage before accepting a proposal.

Agents use the MCP tools `list_execution_plans`, `get_execution_plan`,
`create_execution_plan`, `update_execution_plan`, `remove_execution_plan`, and
`run_execution_plan`. The run tool performs account and data-source preflight and an
optional before-workflow; it does not silently place a trade. A ready run creates a durable
receipt, covers every symbol in the plan, and returns a `run_id`. The agent must pass that
`execution_plan_run_id` to each exact-symbol market/news/positioning/pattern/lesson read,
using the plan market type/timeframe and pattern id where applicable; the server rejects
mismatched context, records a read receipt only when the response has no source errors, and
uses its own record time for freshness. `get_execution_plan_run` exposes only safe run metadata and
receipts. When the result is ready, the agent follows the returned steps using the explicit
market/news/positioning, pattern/lesson, paper-proposal, execution, operation-monitoring,
outcome, postmortem, and after-workflow tools. A plan-bound proposal must include both
`execution_plan_name` and `execution_plan_run_id`; the server rechecks the run, plan version,
symbol, and fresh receipts both at proposal time and again at paper execution. Proposals with
neither field retain the legacy global-risk-only behavior. `list_operations` and `get_operation`
let an agent monitor open trades and compute sample metrics. The selected plan is snapshotted
on accepted operations for later review. The same plan is also available as the MCP resource
`trading://execution-plans/{plan_name}`.

Plans that set `minimum_signal_score` can also enable `exploratory_trades_enabled`.
Exploratory signals have their own minimum score and consume only
`exploratory_risk_fraction` of the plan's per-operation risk. Core and exploratory outcomes
must be evaluated separately; this raises sample frequency without pretending that both tiers
have the same precision.

The MCP tools `evaluate_strategy` and `run_strategy_cycle` use fresh market, aggregate
derivatives, and RSS context. The evaluator classifies trend, range, and squeeze regimes,
mirrors long/short rules, and sizes stop, target, and quantity after modeled costs. A cycle
executes at most one paper operation and never bypasses server-side risk or provenance checks.
Missing data or negative economics still result in no trade.

### Persistent paper positions

Paper operations remain open in SQLite after the AI or MCP client disconnects. Start the
independent supervisor from the project root to enforce each position's stop, take-profit
limits, profitable-band dwell timers, and maximum duration:

```bash
DEFAULT_ACCOUNT_ID=acct_<account-id> \
  uv run crypto-orchestrator-supervisor \
  --poll-seconds 15
```

The supervisor is paper-only and leaves positions open between cycles; it closes a position
only when an exit policy is triggered. It is separate from the AI strategy process and can be
run as a service with a process manager. The supervisor currently closes the full paper
position when a limit or band condition fires; it does not simulate partial fills.

When the AI returns, it must call `prepare_position_review` for every open position. That MCP
tool fetches fresh market candles, aggregate derivatives positioning, RSS news, and a
fundamental-event proxy. The AI must analyze those four areas and submit them with evidence
through `submit_position_review`. Only a fresh submitted `ADJUST` review can call
`update_position_exit_policy`, and only a fresh `FORCE_CLOSE` review can call
`force_close_position`. A `HOLD` review leaves the position and its supervisor policy intact.

To keep scanning for new paper entries while handing all open-position exits to the supervisor,
run the bounded strategy process with `--leave-open`:

```bash
DEFAULT_ACCOUNT_ID=acct_<account-id> \
  uv run crypto-orchestrator-strategy \
  --plan "Regime-aware sandbox v5" \
  --duration-minutes 120 \
  --target-operations 12 \
  --leave-open
```

Without `--leave-open`, the strategy runner is a bounded sampling utility and closes its
run-owned positions at the end. With it, the independent supervisor is the only automatic
exit worker.

The equivalent HTTP routes are `/api/v1/execution-plans` and
`/api/v1/execution-plans/run`. Plan data sources are allowlisted to public Binance and
configured RSS; agents cannot inject arbitrary upstream URLs or code.

### Accounts and credentials

Create each ecosystem once and keep the returned `access_token` secret; it is shown only in the creation response:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/accounts \
  -H 'content-type: application/json' \
  -d '{"name":"User A"}'
```

In non-development environments, send the provisioning secret as `X-Bootstrap-Token`. Set `CREDENTIAL_ENCRYPTION_KEY` before storing provider credentials. Generate a key with:

```bash
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Use the account token on every protected HTTP route and as the `Authorization: Bearer <access_token>` header for MCP Streamable HTTP. Agents cannot select an `account_id`; the server derives it from the authenticated token. Operations, daily risk limits, idempotency keys, postmortems, lessons, and pattern context are account-scoped.

Provider credentials are written through the account endpoint and only metadata is returned.
Telegram and Binance credentials can be configured per account; X credentials are retained
only for backwards-compatible cleanup and are ignored while `X_API_ENABLED=false`.

To receive operation updates, store a Telegram bot token and one or more comma-separated chat IDs for the same account:

Create the bot with Telegram's BotFather, add it to the target chat, and use that chat's ID. Keep the bot token out of source control.

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/account/credentials/telegram \
  -H "Authorization: Bearer $ACCOUNT_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"values":{"bot_token":"<telegram-bot-token>","chat_ids":"<chat-id-1>,<chat-id-2>"}}'
```

The endpoint `POST /api/v1/account/notifications/telegram/test` sends a test message. After that, the account's bot receives notifications when a paper operation opens, when its outcome is recorded, and when an agent records a postmortem with conclusions and a candidate lesson. Delivery failures do not roll back the operation; they are logged without exposing the bot token.

Binance market data remains public read-only data. Binance credentials can be stored per account for the future private execution adapter, but live exchange execution is not implemented in this version.

For a local MCP host that launches subprocesses:

```bash
uv run crypto-orchestrator-mcp
```

## Read-only intelligence

The MCP server exposes these data tools:

- `get_market_snapshot`: normalized Binance spot/perpetual price, bid/ask, 24h change, volume, and recent candles.
- `get_crypto_news`: timestamped stories from the configured RSS feeds, optionally filtered by symbol.
- `get_derivatives_positioning`: aggregate Binance USDⓈ-M futures context including open interest,
  funding/mark price, global and top-trader long-short ratios, and taker buy/sell flow.
- `evaluate_strategy`: deterministic regime-aware technical, derivatives, sentiment, and news-event
  evaluation for both long and short sides.
- `run_strategy_cycle`: evaluate one symbol and execute at most one accepted paper operation.
- `get_pattern_context` and `get_lessons`: account-scoped candidate history; a pattern-context
  read also satisfies the lesson receipt when the active plan requires both.
- `get_x_posts`: legacy compatibility tool; returns `not_configured` while X is disabled.

The equivalent HTTP routes are `/api/v1/market/snapshot`, `/api/v1/market/positioning`,
and `/api/v1/news`.
Every response includes source information and an `errors` array for unavailable or rate-limited providers.

News and positioning are decision context, not order triggers. Strategies should compare
the market structure with news, open-interest changes, funding crowding, long-short ratios,
and taker flow; disagreement lowers confidence and must not bypass the plan's risk gates.
The public data is aggregated: it does not identify individual traders or expose each
trader's exact leverage or position, so it must never be described as a complete view of
where every participant is leveraged.

### Provider configuration

Copy `.env.example` to `.env` and adjust these values when needed:

```dotenv
BINANCE_MARKET_DATA_ENABLED=true
NEWS_RSS_FEEDS=https://cointelegraph.com/rss
CRYPTOPANIC_API_ENABLED=false
CRYPTOPANIC_AUTH_TOKEN=
CRYPTOPANIC_API_PLAN=growth
X_API_ENABLED=false
```

Binance market data is public read-only data. RSS feeds are the primary free news source.
CryptoPanic optionally supplements RSS when `CRYPTOPANIC_API_ENABLED=true` and a valid
`CRYPTOPANIC_AUTH_TOKEN` is configured. Its API plan defaults to `growth`; the integration is
disabled by default to avoid accidental quota usage. CryptoPanic vote counts are crowd context,
not truth or a standalone trading signal. Keep the token in server environment configuration;
it is never returned to agents. RSS feed URLs are server configuration; agents cannot supply
arbitrary upstream URLs.
X can be explicitly re-enabled for legacy deployments with `X_API_ENABLED=true` and a valid
account-scoped Bearer Token, but it is outside the default free mode.
`EXECUTION_PLAN_READ_FRESHNESS_SECONDS` controls the default five-minute provenance freshness
window for plan-bound proposals.

### Repeated paper sessions

After creating an active paper plan, the local MCP runner can repeat strategy cycles, monitor
stop/target/time exits, record outcomes, and write postmortems:

```bash
DEFAULT_ACCOUNT_ID=acct_<account-id> \
  uv run crypto-orchestrator-strategy \
  --plan "Regime-aware sandbox v5" \
  --duration-minutes 120 \
  --target-operations 10
```

The runner is a bounded sampling process and closes its remaining run-owned positions when
its duration ends. Use `crypto-orchestrator-supervisor` instead when positions must survive
the AI session and remain active until their durable exit policy triggers.

The runner is paper-only. It does not fabricate a trade to satisfy a quota: a configured
exploratory tier increases the chance of a non-zero sample, while data-quality failures,
stale evidence, risk limits, and cost-aware exits remain hard stops.

## Paper-trading flow

1. Create a trade proposal with a thesis, evidence, scope, invalidation conditions, and pattern hypothesis.
2. Execute it through the paper-only endpoint.
3. Close the operation with an exit price and all simulated costs.
4. Record an agent postmortem.
5. Query pattern context and candidate lessons before future proposals.

Example proposal:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/operations \
  -H 'content-type: application/json' \
  -H "Authorization: Bearer $ACCOUNT_TOKEN" \
  -d @- <<'JSON'
{
  "idempotency_key": "demo-btc-breakout-001",
  "mode": "paper",
  "agent_id": "demo-agent",
  "model_version": "baseline-1",
  "strategy_version": "breakout-1",
  "context": {
    "venue": "paper",
    "symbol": "BTC/USDT",
    "market_type": "perpetual",
    "timeframe": "15m",
    "side": "long",
    "market_regime": "high_volatility"
  },
  "thesis": {
    "summary": "Price reclaimed resistance with volume confirmation.",
    "evidence": [
      {"source": "market", "reference": "btc-15m-001", "feature": "volume_ratio", "value": "2.1", "weight": 0.8}
    ],
    "scope": {"asset": "BTC", "timeframe": "15m", "regime": "high_volatility"},
    "assumptions": ["Liquidity remains available above resistance."],
    "invalidation_conditions": ["Close below reclaimed resistance."],
    "expected_horizon_minutes": 180,
    "confidence": 0.64
  },
  "patterns": [
    {"pattern_id": "momentum_breakout", "pattern_version": 1, "role": "primary", "confidence": 0.72}
  ],
  "quantity": "0.01",
  "entry_price": "60000",
  "stop_loss_price": "59400",
  "take_profit_price": "61200",
  "leverage": "1",
  "max_loss_quote": "8"
}
JSON
```

## Safety boundary

- `PAPER_TRADING_ONLY=true` rejects live proposals.
- Spot short proposals are rejected until a margin adapter is implemented.
- Risk checks run on the server, not in the agent prompt.
- The agent's explanation is stored separately from validated root cause.
- Candidate lessons are not strategy rules and are never auto-promoted.
- Data connectors are explicit and read-only; no arbitrary upstream HTTP passthrough exists.
- External data is context only and never executes an order by itself.
- Telegram is outbound-only in this version; it cannot create, execute, close, or modify operations.

## Verification

```bash
uv run pytest
uv run ruff check .
```
