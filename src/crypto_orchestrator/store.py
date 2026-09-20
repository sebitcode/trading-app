from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock

from .models import (
    DEFAULT_ACCOUNT_ID,
    Account,
    AgentInvestigationRecord,
    CredentialMetadata,
    ExecutionPlan,
    ExecutionPlanReadReceipt,
    ExecutionPlanRunReceipt,
    Lesson,
    OperationRecord,
    OperationStatus,
    PositionReview,
    StrategyCycleRecord,
    Workflow,
    utc_now,
)


class SQLiteStore:
    """Durable, account-scoped storage for operations, learning, and secrets."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS account_tokens (
                    token_hash TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE INDEX IF NOT EXISTS idx_account_tokens_account
                    ON account_tokens(account_id);
                CREATE TABLE IF NOT EXISTS account_credentials (
                    account_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    encrypted_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, provider),
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_workflows_account_name
                    ON workflows(account_id, name COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_workflows_account
                    ON workflows(account_id);
                CREATE TABLE IF NOT EXISTS execution_plans (
                    plan_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_plans_account_name
                    ON execution_plans(account_id, name COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_execution_plans_account
                    ON execution_plans(account_id);
                """
            )
            self._migrate_operations(connection)
            self._migrate_lessons(connection)
            self._migrate_execution_plan_provenance(connection)
            self._migrate_position_reviews(connection)
            self._migrate_strategy_cycles(connection)
            self._migrate_agent_investigations(connection)

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }

    @staticmethod
    def _create_operations_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE operations (
                operation_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL DEFAULT 'acct_local',
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                FOREIGN KEY(account_id) REFERENCES accounts(account_id)
            )
            """
        )

    @staticmethod
    def _has_global_idempotency_unique_index(connection: sqlite3.Connection) -> bool:
        indexes = connection.execute("PRAGMA index_list(operations)").fetchall()
        for index in indexes:
            if not index["unique"]:
                continue
            columns = connection.execute(
                f"PRAGMA index_info({index['name']})"
            ).fetchall()
            if [row["name"] for row in columns] == ["idempotency_key"]:
                return True
        return False

    def _migrate_operations(self, connection: sqlite3.Connection) -> None:
        if not self._columns(connection, "operations"):
            self._create_operations_table(connection)
        else:
            columns = self._columns(connection, "operations")
            if "account_id" not in columns:
                connection.execute(
                    "ALTER TABLE operations ADD COLUMN account_id TEXT NOT NULL "
                    f"DEFAULT '{DEFAULT_ACCOUNT_ID}'",
                )
            if self._has_global_idempotency_unique_index(connection):
                connection.execute("ALTER TABLE operations RENAME TO operations_legacy")
                self._create_operations_table(connection)
                connection.execute(
                    """
                    INSERT INTO operations(
                        operation_id, account_id, idempotency_key, status, created_at, snapshot_json
                    )
                    SELECT operation_id, account_id, idempotency_key, status,
                           created_at, snapshot_json
                    FROM operations_legacy
                    """
                )
                connection.execute("DROP TABLE operations_legacy")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_operations_account_idempotency
                ON operations(account_id, idempotency_key)
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_operations_account ON operations(account_id)"
        )

    def _migrate_lessons(self, connection: sqlite3.Connection) -> None:
        if not self._columns(connection, "lessons"):
            connection.execute(
                """
                CREATE TABLE lessons (
                    lesson_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT 'acct_local',
                    operation_id TEXT NOT NULL,
                    pattern_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
                )
                """
            )
        elif "account_id" not in self._columns(connection, "lessons"):
            connection.execute(
                "ALTER TABLE lessons ADD COLUMN account_id TEXT NOT NULL "
                f"DEFAULT '{DEFAULT_ACCOUNT_ID}'",
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_pattern ON lessons(pattern_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_account ON lessons(account_id)"
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_lessons_operation_pattern
                ON lessons(operation_id, pattern_id)
            """
        )

    @staticmethod
    def _migrate_execution_plan_provenance(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_plan_runs (
                run_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                plan_name TEXT NOT NULL,
                plan_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                selected_symbol TEXT NOT NULL,
                covered_symbols_json TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                target_operations INTEGER NOT NULL,
                required_reads_json TEXT NOT NULL,
                FOREIGN KEY(account_id) REFERENCES accounts(account_id)
            );
            CREATE INDEX IF NOT EXISTS idx_execution_plan_runs_account
                ON execution_plan_runs(account_id, started_at DESC);
            CREATE TABLE IF NOT EXISTS execution_plan_read_receipts (
                receipt_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                plan_version INTEGER NOT NULL,
                read_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY(account_id) REFERENCES accounts(account_id),
                FOREIGN KEY(run_id) REFERENCES execution_plan_runs(run_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_plan_receipt_identity
                ON execution_plan_read_receipts(account_id, run_id, read_type, symbol);
            CREATE INDEX IF NOT EXISTS idx_execution_plan_receipts_lookup
                ON execution_plan_read_receipts(account_id, run_id, symbol, recorded_at DESC);
            """
        )

    @staticmethod
    def _migrate_position_reviews(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS position_reviews (
                review_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                prepared_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                FOREIGN KEY(account_id) REFERENCES accounts(account_id),
                FOREIGN KEY(operation_id) REFERENCES operations(operation_id)
            );
            CREATE INDEX IF NOT EXISTS idx_position_reviews_operation
                ON position_reviews(account_id, operation_id, prepared_at DESC);
            """
        )

    @staticmethod
    def _migrate_strategy_cycles(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_cycles (
                cycle_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                plan_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                execution_plan_run_id TEXT,
                executed INTEGER NOT NULL,
                reason TEXT NOT NULL,
                operation_id TEXT,
                created_at TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                FOREIGN KEY(account_id) REFERENCES accounts(account_id)
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_cycles_account
                ON strategy_cycles(account_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_strategy_cycles_run
                ON strategy_cycles(account_id, execution_plan_run_id, created_at DESC);
            """
        )

    @staticmethod
    def _migrate_agent_investigations(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_investigations (
                investigation_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                task_name TEXT NOT NULL,
                plan_name TEXT,
                recorded_at TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                FOREIGN KEY(account_id) REFERENCES accounts(account_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_investigations_idempotency
                ON agent_investigations(account_id, idempotency_key);
            CREATE INDEX IF NOT EXISTS idx_agent_investigations_account
                ON agent_investigations(account_id, recorded_at DESC);
            CREATE INDEX IF NOT EXISTS idx_agent_investigations_task
                ON agent_investigations(account_id, task_name, recorded_at DESC);
            """
        )

    def create_account(self, account: Account, token_hash: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO accounts(account_id, name, created_at, active) VALUES (?, ?, ?, ?)",
                (
                    account.account_id,
                    account.name,
                    account.created_at.isoformat(),
                    int(account.active),
                ),
            )
            connection.execute(
                "INSERT INTO account_tokens(token_hash, account_id, created_at) VALUES (?, ?, ?)",
                (token_hash, account.account_id, account.created_at.isoformat()),
            )

    def count_accounts(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()
        return int(row["count"])

    def get_account(self, account_id: str) -> Account | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT account_id, name, created_at, active FROM accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return self._account_from_row(row) if row else None

    def get_account_by_token_digest(self, token_hash: str) -> Account | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT a.account_id, a.name, a.created_at, a.active
                FROM accounts AS a
                JOIN account_tokens AS t ON t.account_id = a.account_id
                WHERE t.token_hash = ? AND t.revoked_at IS NULL
                """,
                (token_hash,),
            ).fetchone()
        return self._account_from_row(row) if row else None

    @staticmethod
    def _account_from_row(row: sqlite3.Row) -> Account:
        return Account(
            account_id=row["account_id"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            active=bool(row["active"]),
        )

    def save_operation(self, operation: OperationRecord) -> None:
        snapshot = operation.model_dump_json()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO operations(
                    operation_id, account_id, idempotency_key, status, created_at, snapshot_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    status = excluded.status,
                    snapshot_json = excluded.snapshot_json
                """,
                (
                    operation.operation_id,
                    operation.account_id,
                    operation.proposal.idempotency_key,
                    operation.status.value,
                    operation.created_at.isoformat(),
                    snapshot,
                ),
            )

    def get_operation(
        self, operation_id: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> OperationRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM operations
                WHERE operation_id = ? AND account_id = ?
                """,
                (operation_id, account_id),
            ).fetchone()
        return OperationRecord.model_validate_json(row["snapshot_json"]) if row else None

    def find_by_idempotency_key(
        self, key: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> OperationRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM operations
                WHERE idempotency_key = ? AND account_id = ?
                """,
                (key, account_id),
            ).fetchone()
        return OperationRecord.model_validate_json(row["snapshot_json"]) if row else None

    def list_operations(
        self, limit: int = 100, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> list[OperationRecord]:
        limit = max(1, min(limit, 500))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshot_json FROM operations
                WHERE account_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
        return [OperationRecord.model_validate_json(row["snapshot_json"]) for row in rows]

    def save_strategy_cycle(self, cycle: StrategyCycleRecord) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_cycles(
                    cycle_id, account_id, plan_name, symbol, execution_plan_run_id,
                    executed, reason, operation_id, created_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cycle_id) DO UPDATE SET
                    plan_name = excluded.plan_name,
                    symbol = excluded.symbol,
                    execution_plan_run_id = excluded.execution_plan_run_id,
                    executed = excluded.executed,
                    reason = excluded.reason,
                    operation_id = excluded.operation_id,
                    created_at = excluded.created_at,
                    snapshot_json = excluded.snapshot_json
                """,
                (
                    cycle.cycle_id,
                    cycle.account_id,
                    cycle.plan_name,
                    cycle.symbol,
                    cycle.execution_plan_run_id,
                    int(cycle.executed),
                    cycle.reason,
                    cycle.operation_id,
                    cycle.created_at.isoformat(),
                    cycle.model_dump_json(),
                ),
            )

    def list_strategy_cycles(
        self,
        limit: int = 100,
        *,
        account_id: str = DEFAULT_ACCOUNT_ID,
        plan_name: str | None = None,
        execution_plan_run_id: str | None = None,
    ) -> list[StrategyCycleRecord]:
        limit = max(1, min(limit, 500))
        clauses = ["account_id = ?"]
        params: list[str | int] = [account_id]
        if plan_name:
            clauses.append("plan_name = ? COLLATE NOCASE")
            params.append(plan_name)
        if execution_plan_run_id:
            clauses.append("execution_plan_run_id = ?")
            params.append(execution_plan_run_id)
        params.append(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshot_json FROM strategy_cycles
                WHERE """
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [StrategyCycleRecord.model_validate_json(row["snapshot_json"]) for row in rows]

    def get_strategy_cycle(
        self, cycle_id: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> StrategyCycleRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM strategy_cycles
                WHERE cycle_id = ? AND account_id = ?
                """,
                (cycle_id, account_id),
            ).fetchone()
        return StrategyCycleRecord.model_validate_json(row["snapshot_json"]) if row else None

    def save_agent_investigation(self, investigation: AgentInvestigationRecord) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_investigations(
                    investigation_id, account_id, idempotency_key, task_name,
                    plan_name, recorded_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                    idempotency_key = excluded.idempotency_key,
                    task_name = excluded.task_name,
                    plan_name = excluded.plan_name,
                    recorded_at = excluded.recorded_at,
                    snapshot_json = excluded.snapshot_json
                """,
                (
                    investigation.investigation_id,
                    investigation.account_id,
                    investigation.idempotency_key,
                    investigation.task_name,
                    investigation.plan_name,
                    investigation.recorded_at.isoformat(),
                    investigation.model_dump_json(),
                ),
            )

    def get_agent_investigation(
        self, investigation_id: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> AgentInvestigationRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM agent_investigations
                WHERE investigation_id = ? AND account_id = ?
                """,
                (investigation_id, account_id),
            ).fetchone()
        return (
            AgentInvestigationRecord.model_validate_json(row["snapshot_json"])
            if row
            else None
        )

    def find_agent_investigation_by_idempotency_key(
        self, key: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> AgentInvestigationRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM agent_investigations
                WHERE idempotency_key = ? AND account_id = ?
                """,
                (key, account_id),
            ).fetchone()
        return (
            AgentInvestigationRecord.model_validate_json(row["snapshot_json"])
            if row
            else None
        )

    def list_agent_investigations(
        self,
        limit: int = 100,
        *,
        account_id: str = DEFAULT_ACCOUNT_ID,
        task_name: str | None = None,
        plan_name: str | None = None,
    ) -> list[AgentInvestigationRecord]:
        limit = max(1, min(limit, 500))
        clauses = ["account_id = ?"]
        params: list[str | int] = [account_id]
        if task_name:
            clauses.append("task_name = ? COLLATE NOCASE")
            params.append(task_name)
        if plan_name:
            clauses.append("plan_name = ? COLLATE NOCASE")
            params.append(plan_name)
        params.append(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshot_json FROM agent_investigations
                WHERE """
                + " AND ".join(clauses)
                + " ORDER BY recorded_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [
            AgentInvestigationRecord.model_validate_json(row["snapshot_json"])
            for row in rows
        ]

    def count_open_operations(self, account_id: str = DEFAULT_ACCOUNT_ID) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM operations
                WHERE account_id = ? AND status = 'paper_open'
                """,
                (account_id,),
            ).fetchone()
        return int(row["count"])

    def open_notional_quote(self, account_id: str = DEFAULT_ACCOUNT_ID) -> Decimal:
        operations = self.list_operations(account_id=account_id, limit=500)
        return sum(
            (
                operation.execution.entry_price * operation.execution.quantity
                if operation.execution
                else operation.proposal.entry_price * operation.proposal.quantity
                for operation in operations
                if operation.status is OperationStatus.PAPER_OPEN
            ),
            Decimal("0"),
        )

    def daily_loss(self, day: str, account_id: str = DEFAULT_ACCOUNT_ID) -> str:
        """Return conservative loss total for an account and UTC calendar day."""

        operations = self.list_operations(account_id=account_id, limit=500)
        total = sum(
            max(-operation.outcome.pnl_net, 0)
            for operation in operations
            if operation.outcome
            and operation.outcome.closed_at.date().isoformat() == day
        )
        return str(total)

    def save_position_review(self, review: PositionReview) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO position_reviews(
                    review_id, account_id, operation_id, status,
                    prepared_at, expires_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_id) DO UPDATE SET
                    status = excluded.status,
                    snapshot_json = excluded.snapshot_json
                """,
                (
                    review.review_id,
                    review.account_id,
                    review.operation_id,
                    review.status.value,
                    review.prepared_at.isoformat(),
                    review.expires_at.isoformat(),
                    review.model_dump_json(),
                ),
            )

    def get_position_review(
        self, review_id: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> PositionReview | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM position_reviews
                WHERE review_id = ? AND account_id = ?
                """,
                (review_id, account_id),
            ).fetchone()
        return PositionReview.model_validate_json(row["snapshot_json"]) if row else None

    def list_position_reviews(
        self,
        operation_id: str,
        *,
        account_id: str = DEFAULT_ACCOUNT_ID,
        limit: int = 20,
    ) -> list[PositionReview]:
        limit = max(1, min(limit, 100))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshot_json FROM position_reviews
                WHERE operation_id = ? AND account_id = ?
                ORDER BY prepared_at DESC LIMIT ?
                """,
                (operation_id, account_id, limit),
            ).fetchall()
        return [PositionReview.model_validate_json(row["snapshot_json"]) for row in rows]

    def save_lesson(self, lesson: Lesson) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO lessons(
                    lesson_id, account_id, operation_id, pattern_id, created_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id, pattern_id) DO UPDATE SET
                    lesson_id = excluded.lesson_id,
                    created_at = excluded.created_at,
                    snapshot_json = excluded.snapshot_json
                """,
                (
                    lesson.lesson_id,
                    lesson.account_id,
                    lesson.operation_id,
                    lesson.pattern_id,
                    lesson.created_at.isoformat(),
                    lesson.model_dump_json(),
                ),
            )

    def list_lessons(
        self,
        *,
        account_id: str = DEFAULT_ACCOUNT_ID,
        pattern_id: str | None = None,
        symbol: str | None = None,
        market_type: str | None = None,
        side: str | None = None,
        limit: int = 100,
    ) -> list[Lesson]:
        limit = max(1, min(limit, 500))
        query = "SELECT snapshot_json FROM lessons"
        params: list[str | int] = [account_id]
        clauses: list[str] = ["account_id = ?"]
        if pattern_id:
            clauses.append("pattern_id = ?")
            params.append(pattern_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        lessons = [Lesson.model_validate_json(row["snapshot_json"]) for row in rows]
        return [
            lesson
            for lesson in lessons
            if (symbol is None or lesson.symbol == symbol)
            and (market_type is None or lesson.market_type.value == market_type)
            and (side is None or lesson.side.value == side)
        ]

    def save_credential(
        self, account_id: str, provider: str, encrypted_json: str, updated_at: datetime
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO account_credentials(account_id, provider, encrypted_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, provider) DO UPDATE SET
                    encrypted_json = excluded.encrypted_json,
                    updated_at = excluded.updated_at
                """,
                (account_id, provider, encrypted_json, updated_at.isoformat()),
            )

    def get_credentials(self, account_id: str) -> dict[str, str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT provider, encrypted_json FROM account_credentials
                WHERE account_id = ?
                """,
                (account_id,),
            ).fetchall()
        return {str(row["provider"]): str(row["encrypted_json"]) for row in rows}

    def list_credential_metadata(self, account_id: str) -> list[CredentialMetadata]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT provider, updated_at FROM account_credentials
                WHERE account_id = ? ORDER BY provider
                """,
                (account_id,),
            ).fetchall()
        return [
            CredentialMetadata(
                provider=str(row["provider"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    def delete_credential(self, account_id: str, provider: str) -> bool:
        with self._lock, self._connect() as connection:
            result = connection.execute(
                "DELETE FROM account_credentials WHERE account_id = ? AND provider = ?",
                (account_id, provider),
            )
        return result.rowcount > 0

    def save_workflow(self, workflow: Workflow) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workflows(
                    workflow_id, account_id, name, version, created_at, updated_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id) DO UPDATE SET
                    name = excluded.name,
                    version = excluded.version,
                    updated_at = excluded.updated_at,
                    snapshot_json = excluded.snapshot_json
                """,
                (
                    workflow.workflow_id,
                    workflow.account_id,
                    workflow.name,
                    workflow.version,
                    workflow.created_at.isoformat(),
                    workflow.updated_at.isoformat(),
                    workflow.model_dump_json(),
                ),
            )

    def get_workflow(
        self, workflow_id: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> Workflow | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM workflows
                WHERE workflow_id = ? AND account_id = ?
                """,
                (workflow_id, account_id),
            ).fetchone()
        return Workflow.model_validate_json(row["snapshot_json"]) if row else None

    def get_workflow_by_name(
        self, name: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> Workflow | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM workflows
                WHERE account_id = ? AND name = ? COLLATE NOCASE
                """,
                (account_id, name),
            ).fetchone()
        return Workflow.model_validate_json(row["snapshot_json"]) if row else None

    def list_workflows(self, account_id: str = DEFAULT_ACCOUNT_ID) -> list[Workflow]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshot_json FROM workflows
                WHERE account_id = ?
                ORDER BY updated_at DESC
                """,
                (account_id,),
            ).fetchall()
        return [Workflow.model_validate_json(row["snapshot_json"]) for row in rows]

    def delete_workflow(
        self, workflow_id: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> bool:
        with self._lock, self._connect() as connection:
            result = connection.execute(
                "DELETE FROM workflows WHERE workflow_id = ? AND account_id = ?",
                (workflow_id, account_id),
            )
        return result.rowcount > 0

    def save_execution_plan(self, plan: ExecutionPlan) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO execution_plans(
                    plan_id, account_id, name, version, created_at, updated_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    name = excluded.name,
                    version = excluded.version,
                    updated_at = excluded.updated_at,
                    snapshot_json = excluded.snapshot_json
                """,
                (
                    plan.plan_id,
                    plan.account_id,
                    plan.name,
                    plan.version,
                    plan.created_at.isoformat(),
                    plan.updated_at.isoformat(),
                    plan.model_dump_json(),
                ),
            )

    def get_execution_plan(
        self, plan_id: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> ExecutionPlan | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM execution_plans
                WHERE plan_id = ? AND account_id = ?
                """,
                (plan_id, account_id),
            ).fetchone()
        return ExecutionPlan.model_validate_json(row["snapshot_json"]) if row else None

    def get_execution_plan_by_name(
        self, name: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> ExecutionPlan | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM execution_plans
                WHERE account_id = ? AND name = ? COLLATE NOCASE
                """,
                (account_id, name),
            ).fetchone()
        return ExecutionPlan.model_validate_json(row["snapshot_json"]) if row else None

    def list_execution_plans(
        self, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> list[ExecutionPlan]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshot_json FROM execution_plans
                WHERE account_id = ?
                ORDER BY updated_at DESC
                """,
                (account_id,),
            ).fetchall()
        return [ExecutionPlan.model_validate_json(row["snapshot_json"]) for row in rows]

    def delete_execution_plan(
        self, plan_id: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> bool:
        with self._lock, self._connect() as connection:
            result = connection.execute(
                "DELETE FROM execution_plans WHERE plan_id = ? AND account_id = ?",
                (plan_id, account_id),
            )
        return result.rowcount > 0

    def save_execution_plan_run(self, run: ExecutionPlanRunReceipt) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO execution_plan_runs(
                    run_id, account_id, plan_id, plan_name, plan_version, status,
                    started_at, expires_at, selected_symbol, covered_symbols_json,
                    duration_minutes, target_operations, required_reads_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    plan_id = excluded.plan_id,
                    plan_name = excluded.plan_name,
                    plan_version = excluded.plan_version,
                    started_at = excluded.started_at,
                    expires_at = excluded.expires_at,
                    selected_symbol = excluded.selected_symbol,
                    covered_symbols_json = excluded.covered_symbols_json,
                    duration_minutes = excluded.duration_minutes,
                    target_operations = excluded.target_operations,
                    required_reads_json = excluded.required_reads_json
                """,
                (
                    run.run_id,
                    run.account_id,
                    run.plan_id,
                    run.plan_name,
                    run.plan_version,
                    run.status.value,
                    run.started_at.isoformat(),
                    run.expires_at.isoformat(),
                    run.selected_symbol,
                    json.dumps(run.covered_symbols),
                    run.duration_minutes,
                    run.target_operations,
                    json.dumps([read.value for read in run.required_reads]),
                ),
            )

    def get_execution_plan_run(
        self, run_id: str, account_id: str = DEFAULT_ACCOUNT_ID
    ) -> ExecutionPlanRunReceipt | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT run_id, account_id, plan_id, plan_name, plan_version, status,
                       started_at, expires_at, selected_symbol, covered_symbols_json,
                       duration_minutes, target_operations, required_reads_json
                FROM execution_plan_runs
                WHERE run_id = ? AND account_id = ?
                """,
                (run_id, account_id),
            ).fetchone()
            if not row:
                return None
            run = ExecutionPlanRunReceipt(
                run_id=row["run_id"],
                account_id=row["account_id"],
                plan_id=row["plan_id"],
                plan_name=row["plan_name"],
                plan_version=row["plan_version"],
                status=row["status"],
                started_at=datetime.fromisoformat(row["started_at"]),
                expires_at=datetime.fromisoformat(row["expires_at"]),
                selected_symbol=row["selected_symbol"],
                covered_symbols=json.loads(row["covered_symbols_json"]),
                duration_minutes=row["duration_minutes"],
                target_operations=row["target_operations"],
                required_reads=json.loads(row["required_reads_json"]),
                receipts=[],
            )
            receipts = self._execution_plan_read_receipts(connection, run_id, account_id)
        return run.model_copy(update={"receipts": receipts})

    @staticmethod
    def _execution_plan_read_receipts(
        connection: sqlite3.Connection, run_id: str, account_id: str
    ) -> list[ExecutionPlanReadReceipt]:
        rows = connection.execute(
            """
            SELECT receipt_id, account_id, run_id, plan_id, plan_version,
                   read_type, symbol, recorded_at
            FROM execution_plan_read_receipts
            WHERE run_id = ? AND account_id = ?
            ORDER BY recorded_at DESC
            """,
            (run_id, account_id),
        ).fetchall()
        return [
            ExecutionPlanReadReceipt(
                receipt_id=row["receipt_id"],
                account_id=row["account_id"],
                run_id=row["run_id"],
                plan_id=row["plan_id"],
                plan_version=row["plan_version"],
                read_type=row["read_type"],
                symbol=row["symbol"],
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
            )
            for row in rows
        ]

    def save_execution_plan_read_receipt(
        self, receipt: ExecutionPlanReadReceipt
    ) -> ExecutionPlanReadReceipt:
        server_recorded_at = utc_now()
        server_receipt = receipt.model_copy(update={"recorded_at": server_recorded_at})
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO execution_plan_read_receipts(
                    receipt_id, account_id, run_id, plan_id, plan_version,
                    read_type, symbol, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, run_id, read_type, symbol) DO UPDATE SET
                    receipt_id = excluded.receipt_id,
                    plan_id = excluded.plan_id,
                    plan_version = excluded.plan_version,
                    recorded_at = excluded.recorded_at
                """,
                (
                    server_receipt.receipt_id,
                    server_receipt.account_id,
                    server_receipt.run_id,
                    server_receipt.plan_id,
                    server_receipt.plan_version,
                    server_receipt.read_type.value,
                    server_receipt.symbol,
                    server_recorded_at.isoformat(),
                ),
            )
        return server_receipt
