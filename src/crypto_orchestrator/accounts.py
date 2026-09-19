from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from .config import Settings
from .models import (
    Account,
    AccountCreated,
    CredentialMetadata,
    CredentialPayload,
    utc_now,
)
from .store import SQLiteStore

SUPPORTED_CREDENTIAL_PROVIDERS = frozenset({"x", "binance", "telegram"})


class AccountError(Exception):
    """Base class for expected account and credential errors."""


class BootstrapError(AccountError):
    pass


class CredentialVaultError(AccountError):
    pass


class UnsupportedCredentialProvider(AccountError):
    pass


class CredentialVault:
    """Encrypt account provider secrets using an application-managed Fernet key."""

    def __init__(self, encryption_key: str):
        if not encryption_key:
            raise CredentialVaultError(
                "CREDENTIAL_ENCRYPTION_KEY is required to store provider credentials"
            )
        try:
            self._fernet = Fernet(encryption_key.encode())
        except (TypeError, ValueError) as exc:
            raise CredentialVaultError(
                "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key"
            ) from exc

    def encrypt(self, values: dict[str, str]) -> str:
        payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        return self._fernet.encrypt(payload).decode()

    def decrypt(self, encrypted: str) -> dict[str, str]:
        try:
            payload = json.loads(self._fernet.decrypt(encrypted.encode()).decode())
        except (InvalidToken, TypeError, ValueError, UnicodeDecodeError) as exc:
            raise CredentialVaultError(
                "stored provider credentials could not be decrypted"
            ) from exc
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
        ):
            raise CredentialVaultError("stored provider credentials have an invalid shape")
        return payload


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AccountManager:
    def __init__(self, settings: Settings, store: SQLiteStore):
        self.settings = settings
        self.store = store
        self._vault: CredentialVault | None = None
        if settings.credential_encryption_key:
            self._vault = CredentialVault(settings.credential_encryption_key)

    def create_account(self, name: str, bootstrap_token: str | None = None) -> AccountCreated:
        expected = self.settings.account_bootstrap_token
        if expected:
            if not bootstrap_token or not hmac.compare_digest(bootstrap_token, expected):
                raise BootstrapError("invalid account bootstrap token")
        elif self.settings.app_env.lower() != "development":
            raise BootstrapError(
                "ACCOUNT_BOOTSTRAP_TOKEN is required outside development"
            )

        account = Account(
            account_id=f"acct_{uuid4().hex}",
            name=name,
            created_at=utc_now(),
        )
        access_token = f"coa_{secrets.token_urlsafe(32)}"
        self.store.create_account(account, _token_digest(access_token))
        return AccountCreated(
            **account.model_dump(),
            access_token=access_token,
        )

    def authenticate(self, access_token: str | None) -> Account | None:
        if not access_token:
            return None
        account = self.store.get_account_by_token_digest(_token_digest(access_token))
        return account if account and account.active else None

    def get_account(self, account_id: str) -> Account | None:
        return self.store.get_account(account_id)

    def save_credential(
        self, account_id: str, provider: str, payload: CredentialPayload
    ) -> CredentialMetadata:
        provider = provider.strip().lower()
        if provider not in SUPPORTED_CREDENTIAL_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_CREDENTIAL_PROVIDERS))
            raise UnsupportedCredentialProvider(
                f"unsupported credential provider; supported providers: {supported}"
            )
        if self._vault is None:
            raise CredentialVaultError(
                "CREDENTIAL_ENCRYPTION_KEY is required to store provider credentials"
            )
        existing_values = self.credentials_for(account_id).get(provider, {})
        values = {**existing_values, **payload.values}
        updated_at = utc_now()
        self.store.save_credential(
            account_id,
            provider,
            self._vault.encrypt(values),
            updated_at,
        )
        return CredentialMetadata(provider=provider, updated_at=updated_at)

    def list_credentials(self, account_id: str) -> list[CredentialMetadata]:
        return self.store.list_credential_metadata(account_id)

    def delete_credential(self, account_id: str, provider: str) -> bool:
        provider = provider.strip().lower()
        if provider not in SUPPORTED_CREDENTIAL_PROVIDERS:
            return False
        return self.store.delete_credential(account_id, provider)

    def credentials_for(self, account_id: str) -> dict[str, dict[str, str]]:
        encrypted = self.store.get_credentials(account_id)
        if not encrypted:
            return {}
        if self._vault is None:
            raise CredentialVaultError(
                "CREDENTIAL_ENCRYPTION_KEY is required to read provider credentials"
            )
        return {
            provider: self._vault.decrypt(value)
            for provider, value in encrypted.items()
        }
