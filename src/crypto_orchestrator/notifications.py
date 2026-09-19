from __future__ import annotations

import logging
import re
from collections.abc import Callable
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlparse

import httpx

from .accounts import AccountManager
from .config import Settings
from .models import (
    NotificationResult,
    NotificationStatus,
    OperationRecord,
)

logger = logging.getLogger(__name__)
_TELEGRAM_MESSAGE_LIMIT = 4_096
_TELEGRAM_BOT_PATH = re.compile(r"(/bot)[^/\s\"']+")


def _redact_telegram_path(value: str) -> str:
    return _TELEGRAM_BOT_PATH.sub(r"\1[redacted]", value)


class _HttpLogRedactor(logging.Filter):
    """Prevent Bot API path tokens from being emitted by HTTP client loggers."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = _redact_telegram_path(record.getMessage())
        if message != record.getMessage():
            record.msg = message
            record.args = ()
        return True


def _install_http_log_redactor() -> None:
    redactor = _HttpLogRedactor
    for logger_name in ("httpx", "httpcore"):
        http_logger = logging.getLogger(logger_name)
        if not any(isinstance(item, redactor) for item in http_logger.filters):
            http_logger.addFilter(redactor())


class NotificationEvent(StrEnum):
    PAPER_OPENED = "paper_opened"
    OUTCOME_RECORDED = "outcome_recorded"
    POSTMORTEM_RECORDED = "postmortem_recorded"


class NotificationError(RuntimeError):
    """A normalized failure from an outbound notification provider."""

    def __init__(self, message: str, *, category: str, retryable: bool = False):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class MessageSender(Protocol):
    def send(self, chat_ids: tuple[str, ...], text: str) -> int:
        """Send text to each chat and return the number of completed chats."""


class TelegramNotifier(MessageSender):
    """Send plain-text messages through the Telegram Bot API."""

    def __init__(
        self,
        bot_token: str,
        *,
        api_base_url: str = "https://api.telegram.org",
        timeout_seconds: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not bot_token.strip():
            raise ValueError("Telegram bot token is required")
        parsed = urlparse(api_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Telegram API base URL must be an absolute HTTP(S) URL")
        self._bot_token = bot_token.strip()
        self._send_url = f"{api_base_url.rstrip('/')}/bot{self._bot_token}/sendMessage"
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client
        _install_http_log_redactor()

    def send(self, chat_ids: tuple[str, ...], text: str) -> int:
        if not text.strip():
            raise ValueError("Telegram message cannot be empty")
        if not chat_ids:
            return 0

        chunks = _message_chunks(text)
        if self._http_client is not None:
            return self._send_with_client(self._http_client, chat_ids, chunks)
        with httpx.Client(timeout=self._timeout_seconds) as client:
            return self._send_with_client(client, chat_ids, chunks)

    def _send_with_client(
        self, client: httpx.Client, chat_ids: tuple[str, ...], chunks: tuple[str, ...]
    ) -> int:
        delivered = 0
        for chat_id in chat_ids:
            for chunk in chunks:
                try:
                    response = client.post(
                        self._send_url,
                        json={"chat_id": chat_id, "text": chunk},
                        timeout=self._timeout_seconds,
                    )
                except httpx.TimeoutException:
                    raise NotificationError(
                        "Telegram request timed out", category="timeout", retryable=True
                    ) from None
                except httpx.RequestError:
                    raise NotificationError(
                        "Telegram request failed", category="network_error", retryable=True
                    ) from None

                if response.status_code == 429:
                    raise NotificationError(
                        "Telegram rate limit reached", category="rate_limited", retryable=True
                    )
                if response.status_code >= 400:
                    raise NotificationError(
                        "Telegram rejected the message", category="upstream_rejected"
                    )
                try:
                    payload = response.json()
                except ValueError:
                    raise NotificationError(
                        "Telegram returned an invalid response", category="invalid_response"
                    ) from None
                if not isinstance(payload, dict) or payload.get("ok") is not True:
                    raise NotificationError(
                        "Telegram rejected the message", category="upstream_rejected"
                    )
            delivered += 1
        return delivered


def _message_chunks(text: str) -> tuple[str, ...]:
    return tuple(
        text[index : index + _TELEGRAM_MESSAGE_LIMIT]
        for index in range(0, len(text), _TELEGRAM_MESSAGE_LIMIT)
    )


def _safe_text(value: object, limit: int = 800) -> str:
    return str(value).replace("\x00", "").strip()[:limit]


def _list_text(values: list[str]) -> str:
    return "\n".join(f"- {_safe_text(value, 400)}" for value in values) or "- none"


def _scope_text(scope: dict[str, str]) -> str:
    values = (f"{key}={_safe_text(value, 120)}" for key, value in sorted(scope.items()))
    return ", ".join(values) or "none"


def _format_operation(operation: OperationRecord, event: NotificationEvent) -> str:
    proposal = operation.proposal
    context = proposal.context
    patterns = ", ".join(
        f"{pattern.pattern_id} (v{pattern.pattern_version}, {pattern.role.value})"
        for pattern in proposal.patterns
    )
    lines = [
        f"Crypto Orchestrator | {event.replace('_', ' ').title()}",
        f"Operation: {_safe_text(operation.operation_id, 120)}",
        f"Mode/status: {proposal.mode.value} / {operation.status.value}",
        (
            f"Market: {context.symbol} | {context.market_type.value} | "
            f"{context.side.value} | {context.timeframe} | {context.market_regime}"
        ),
        f"Patterns: {patterns}",
    ]

    if event is NotificationEvent.PAPER_OPENED:
        lines.extend(
            [
                (
                    f"Entry: {proposal.entry_price} | Quantity: {proposal.quantity} | "
                    f"Leverage: {proposal.leverage}"
                ),
                (
                    f"Stop loss: {proposal.stop_loss_price} | Take profit: "
                    f"{proposal.take_profit_price or 'not set'}"
                ),
                f"Thesis: {_safe_text(proposal.thesis.summary, 1_200)}",
                f"Scope: {_scope_text(proposal.thesis.scope)}",
                "Invalidation conditions:",
                _list_text(proposal.thesis.invalidation_conditions),
            ]
        )
    elif event is NotificationEvent.OUTCOME_RECORDED and operation.outcome:
        outcome = operation.outcome
        lines.extend(
            [
                (
                    f"Outcome: {outcome.status.value} | Exit: {outcome.exit_price} | "
                    f"Reason: {_safe_text(outcome.exit_reason, 300)}"
                ),
                f"PnL gross/net: {outcome.pnl_gross} / {outcome.pnl_net} quote",
                (
                    f"Costs — fees: {outcome.fees_quote}, slippage: {outcome.slippage_quote}, "
                    f"funding: {outcome.funding_quote}"
                ),
            ]
        )
    elif event is NotificationEvent.POSTMORTEM_RECORDED and operation.postmortem:
        postmortem = operation.postmortem
        lines.extend(
            [
                f"Outcome: {operation.outcome.status.value if operation.outcome else 'unknown'}",
                f"Failure category: {postmortem.failure_category.value}",
                "What worked:",
                _list_text(postmortem.what_worked),
                "What failed:",
                _list_text(postmortem.what_failed),
                f"Why: {_safe_text(postmortem.failure_explanation, 1_000)}",
                f"Counterfactual: {_safe_text(postmortem.counterfactual, 1_000)}",
                f"Candidate lesson: {_safe_text(postmortem.proposed_lesson, 1_200)}",
            ]
        )
        if postmortem.pattern_verdicts:
            lines.append("Pattern conclusions:")
            lines.extend(
                (
                    f"- {verdict.pattern_id}: {verdict.status.value} — "
                    f"{_safe_text(verdict.explanation, 500)}"
                )
                for verdict in postmortem.pattern_verdicts
            )
    return "\n".join(lines)


class NotificationService:
    """Resolve Telegram settings per account and deliver best-effort notifications."""

    def __init__(
        self,
        settings: Settings,
        account_manager: AccountManager,
        sender_factory: Callable[[str], MessageSender] | None = None,
    ) -> None:
        self.settings = settings
        self.account_manager = account_manager
        self._sender_factory = sender_factory or self._default_sender

    def _default_sender(self, bot_token: str) -> TelegramNotifier:
        return TelegramNotifier(
            bot_token,
            api_base_url=self.settings.telegram_api_base_url,
            timeout_seconds=self.settings.http_timeout_seconds,
        )

    def _telegram_config(self, account_id: str) -> tuple[str, tuple[str, ...]]:
        values = self.account_manager.credentials_for(account_id).get("telegram", {})
        bot_token = values.get("bot_token", "").strip()
        raw_chat_ids = values.get("chat_ids", values.get("chat_id", ""))
        chat_ids = tuple(
            dict.fromkeys(item.strip() for item in raw_chat_ids.split(",") if item.strip())
        )
        return bot_token, chat_ids

    def health(self, account_id: str) -> dict[str, str]:
        try:
            bot_token, chat_ids = self._telegram_config(account_id)
        except Exception as exc:
            logger.warning(
                "Telegram configuration could not be read for account %s (%s)",
                account_id,
                type(exc).__name__,
            )
            return {"telegram": "configuration_error"}
        return {"telegram": "configured" if bot_token and chat_ids else "not_configured"}

    def notify_operation(
        self, account_id: str, event: NotificationEvent, operation: OperationRecord
    ) -> NotificationResult:
        return self.notify_message(account_id, _format_operation(operation, event))

    def send_test(self, account_id: str) -> NotificationResult:
        return self.notify_message(
            account_id,
            "Crypto Orchestrator | Telegram test\nThe account notification channel is working.",
        )

    def notify_message(self, account_id: str, message: str) -> NotificationResult:
        try:
            bot_token, chat_ids = self._telegram_config(account_id)
            if not bot_token or not chat_ids:
                return NotificationResult(
                    status=NotificationStatus.NOT_CONFIGURED,
                    delivered_chats=0,
                )
            delivered = self._sender_factory(bot_token).send(chat_ids, message)
            return NotificationResult(
                status=NotificationStatus.SENT,
                delivered_chats=delivered,
            )
        except NotificationError as exc:
            logger.warning(
                "Telegram notification failed for account %s (%s)",
                account_id,
                exc.category,
            )
            return NotificationResult(
                status=NotificationStatus.FAILED,
                delivered_chats=0,
                error=exc.category,
            )
        except Exception as exc:
            logger.warning(
                "Telegram notification failed for account %s (%s)",
                account_id,
                type(exc).__name__,
            )
            return NotificationResult(
                status=NotificationStatus.FAILED,
                delivered_chats=0,
                error="unexpected_error",
            )
