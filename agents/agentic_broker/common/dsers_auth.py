"""Durable OAuth for the hosted DSers MCP server.

DSers refresh tokens rotate every time they are used.  A process-local mutex is
not enough on Cloud Run, so the active Secret Manager secret carries a short
ETag-protected lease.  Only the lease owner may send the refresh request; it
then adds a new immutable secret version and atomically advances an alias while
releasing the lease.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Protocol
from uuid import uuid4

import httpx

from .config import settings

_LOG = logging.getLogger(__name__)
_LOCK_ANNOTATION = "relay-dsers-refresh-lock"
_ACTIVE_VERSION_ANNOTATION = "relay-dsers-active-version"
_ACTIVE_ROTATION_ANNOTATION = "relay-dsers-active-rotation"
_DEFAULT_RESOURCE = "https://mcp.dsers.com/dropshipping/mcp"


class DSersAuthUnavailable(RuntimeError):
    """DSers cannot authenticate; callers must keep the Relay fallback alive."""


@dataclass(frozen=True)
class OAuthBundle:
    client_id: str
    access_token: str
    refresh_token: str
    expires_at: float
    token_type: str = "Bearer"
    scope: str = ""
    resource: str = _DEFAULT_RESOURCE
    rotation_id: str = ""

    @classmethod
    def from_bytes(cls, payload: bytes) -> "OAuthBundle":
        try:
            value = json.loads(payload.decode("utf-8"))
            bundle = cls(
                client_id=str(value["client_id"]),
                access_token=str(value["access_token"]),
                refresh_token=str(value["refresh_token"]),
                expires_at=float(value["expires_at"]),
                token_type=str(value.get("token_type") or "Bearer"),
                scope=str(value.get("scope") or ""),
                resource=str(value.get("resource") or _DEFAULT_RESOURCE),
                rotation_id=str(value.get("rotation_id") or ""),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DSersAuthUnavailable(
                "DSers OAuth secret is missing required token fields"
            ) from exc
        if not bundle.client_id or not bundle.access_token or not bundle.refresh_token:
            raise DSersAuthUnavailable("DSers OAuth secret contains empty credentials")
        return bundle

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "client_id": self.client_id,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "token_type": self.token_type,
                "scope": self.scope,
                "resource": self.resource,
                "rotation_id": self.rotation_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def access_is_fresh(self, *, now: float, leeway_seconds: int = 90) -> bool:
        return bool(self.access_token) and self.expires_at - leeway_seconds > now


@dataclass(frozen=True)
class RefreshLease:
    owner: str
    etag: str
    annotations: dict[str, str]
    version_aliases: dict[str, int]


class TokenStore(Protocol):
    def read_active(self) -> OAuthBundle: ...

    def acquire_refresh_lease(
        self, owner: str, *, ttl_seconds: int, wait_seconds: float
    ) -> RefreshLease: ...

    def persist_rotation(
        self, bundle: OAuthBundle, lease: RefreshLease
    ) -> str: ...

    def release_refresh_lease(self, owner: str) -> None: ...


def _lock_value(owner: str, expires_at: float) -> str:
    return json.dumps(
        {"owner": owner, "expires_at": expires_at},
        separators=(",", ":"),
        sort_keys=True,
    )


def _parse_lock(value: str | None) -> tuple[str, float] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return str(parsed["owner"]), float(parsed["expires_at"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        # An unreadable lock is treated as expired and replaced via ETag CAS.
        return None


class GcpSecretManagerTokenStore:
    """Secret Manager implementation with cross-instance refresh exclusion."""

    def __init__(
        self,
        project_id: str,
        secret_id: str,
        alias: str,
        *,
        client: Any | None = None,
    ) -> None:
        if not project_id or not secret_id or not alias:
            raise DSersAuthUnavailable(
                "DSERS_SECRET_PROJECT_ID, DSERS_SECRET_ID, and "
                "DSERS_SECRET_ALIAS are required"
            )
        if alias.lower() in {"latest", "new"}:
            raise DSersAuthUnavailable("DSers secret alias must not be latest or new")
        if client is None:
            try:
                from google.cloud import secretmanager
            except ImportError as exc:
                raise DSersAuthUnavailable(
                    "google-cloud-secret-manager is not installed"
                ) from exc
            client = secretmanager.SecretManagerServiceClient()
        self.client = client
        self.project_id = project_id
        self.secret_id = secret_id
        self.alias = alias
        self.name = f"projects/{project_id}/secrets/{secret_id}"

    def write_bootstrap_bundle(
        self, bundle: OAuthBundle, *, replace_existing: bool
    ) -> str:
        """Create/promote the human-authorized first version.

        Re-bootstrap is deliberately explicit because advancing the alias
        invalidates the credential currently used by every Cloud Run instance.
        """
        from google.api_core.exceptions import AlreadyExists, NotFound

        try:
            secret = self.client.get_secret(request={"name": self.name})
        except NotFound:
            try:
                self.client.create_secret(
                    request={
                        "parent": f"projects/{self.project_id}",
                        "secret_id": self.secret_id,
                        "secret": {"replication": {"automatic": {}}},
                    }
                )
            except AlreadyExists:
                pass
            secret = self.client.get_secret(request={"name": self.name})

        aliases = dict(secret.version_aliases)
        if self.alias in aliases and not replace_existing:
            raise DSersAuthUnavailable(
                f"secret alias {self.alias!r} already exists; pass --replace "
                "only when intentionally re-bootstrapping a broken grant"
            )
        if not bundle.rotation_id:
            bundle = replace(bundle, rotation_id=f"bootstrap-{uuid4()}")
        version = self.client.add_secret_version(
            request={
                "parent": self.name,
                "payload": {"data": bundle.to_bytes()},
            }
        )
        version_text = str(version.name).rsplit("/", 1)[-1]
        if not version_text.isdigit():
            raise DSersAuthUnavailable(
                f"Secret Manager returned an invalid version name {version.name}"
            )
        aliases[self.alias] = int(version_text)
        annotations = dict(secret.annotations)
        annotations[_ACTIVE_VERSION_ANNOTATION] = version_text
        annotations[_ACTIVE_ROTATION_ANNOTATION] = bundle.rotation_id
        updated = self.client.update_secret(
            request={
                "secret": {
                    "name": self.name,
                    "annotations": annotations,
                    "version_aliases": aliases,
                    "etag": secret.etag,
                },
                "update_mask": {
                    "paths": ["annotations", "version_aliases"]
                },
            }
        )
        return str(updated.etag)

    def read_active(self) -> OAuthBundle:
        try:
            secret = self.client.get_secret(request={"name": self.name})
            annotations = dict(secret.annotations)
            # The numeric version annotation is promoted in the same ETag-CAS
            # as the friendly alias. Reading it by number is strongly
            # consistent and makes an accidental alias-only rollback harmless.
            selector = annotations.get(
                _ACTIVE_VERSION_ANNOTATION, self.alias
            )
            if selector != self.alias and not selector.isdigit():
                raise DSersAuthUnavailable(
                    "DSers active-version annotation is invalid"
                )
            response = self.client.access_secret_version(
                request={"name": f"{self.name}/versions/{selector}"}
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DSersAuthUnavailable):
                raise
            raise DSersAuthUnavailable(
                f"DSers OAuth secret alias {self.alias!r} is unavailable: {exc}"
            ) from exc
        bundle = OAuthBundle.from_bytes(bytes(response.payload.data))
        expected_rotation = annotations.get(_ACTIVE_ROTATION_ANNOTATION)
        if expected_rotation and bundle.rotation_id != expected_rotation:
            raise DSersAuthUnavailable(
                "DSers active token rotation ID does not match secret metadata; "
                "refusing a possible rollback"
            )
        return bundle

    def acquire_refresh_lease(
        self,
        owner: str,
        *,
        ttl_seconds: int = 120,
        wait_seconds: float = 8,
    ) -> RefreshLease:
        from google.api_core.exceptions import FailedPrecondition

        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                secret = self.client.get_secret(request={"name": self.name})
            except Exception as exc:  # noqa: BLE001
                raise DSersAuthUnavailable(
                    f"cannot inspect DSers OAuth secret metadata: {exc}"
                ) from exc
            annotations = dict(secret.annotations)
            existing = _parse_lock(annotations.get(_LOCK_ANNOTATION))
            now = time.time()
            if existing and existing[0] != owner and existing[1] > now:
                if time.monotonic() >= deadline:
                    raise DSersAuthUnavailable(
                        "another Relay instance is rotating the DSers refresh token"
                    )
                time.sleep(0.1)
                continue

            annotations[_LOCK_ANNOTATION] = _lock_value(
                owner, now + ttl_seconds
            )
            try:
                updated = self.client.update_secret(
                    request={
                        "secret": {
                            "name": self.name,
                            "annotations": annotations,
                            "etag": secret.etag,
                        },
                        "update_mask": {"paths": ["annotations"]},
                    }
                )
            except FailedPrecondition:
                if time.monotonic() >= deadline:
                    raise DSersAuthUnavailable(
                        "lost the ETag race for the DSers refresh lease"
                    )
                continue
            except Exception as exc:  # noqa: BLE001
                raise DSersAuthUnavailable(
                    f"cannot acquire the DSers refresh lease: {exc}"
                ) from exc
            return RefreshLease(
                owner=owner,
                etag=str(updated.etag),
                annotations=dict(updated.annotations),
                version_aliases=dict(updated.version_aliases),
            )

    def _rotation_version(self, rotation_id: str) -> int | None:
        """Reconcile an ambiguous addVersion response without adding twice."""
        try:
            versions = self.client.list_secret_versions(
                request={"parent": self.name, "page_size": 20}
            )
            for version in versions:
                number = str(version.name).rsplit("/", 1)[-1]
                if not number.isdigit() or str(version.state).endswith("DESTROYED"):
                    continue
                try:
                    candidate = self.client.access_secret_version(
                        request={"name": version.name}
                    )
                    bundle = OAuthBundle.from_bytes(
                        bytes(candidate.payload.data)
                    )
                except Exception:  # noqa: BLE001
                    continue
                if bundle.rotation_id == rotation_id:
                    return int(number)
        except Exception:  # noqa: BLE001
            return None
        return None

    def persist_rotation(
        self, bundle: OAuthBundle, lease: RefreshLease
    ) -> str:
        if not bundle.rotation_id:
            bundle = replace(bundle, rotation_id=str(uuid4()))
        try:
            version = self.client.add_secret_version(
                request={
                    "parent": self.name,
                    "payload": {"data": bundle.to_bytes()},
                }
            )
            number_text = str(version.name).rsplit("/", 1)[-1]
            if not number_text.isdigit():
                raise DSersAuthUnavailable(
                    f"Secret Manager returned an invalid version name {version.name}"
                )
            version_number = int(number_text)
        except Exception as exc:  # noqa: BLE001
            version_number = self._rotation_version(bundle.rotation_id) or 0
            if not version_number:
                raise DSersAuthUnavailable(
                    "rotated DSers token could not be durably reconciled after "
                    f"Secret Manager addVersion failed: {exc}"
                ) from exc

        # Alias advance + lease release is one ETag-guarded metadata mutation.
        # If the response is ambiguous, inspect state before attempting it again.
        etag = lease.etag
        annotations = dict(lease.annotations)
        aliases = dict(lease.version_aliases)
        aliases[self.alias] = version_number
        annotations.pop(_LOCK_ANNOTATION, None)
        annotations[_ACTIVE_VERSION_ANNOTATION] = str(version_number)
        annotations[_ACTIVE_ROTATION_ANNOTATION] = bundle.rotation_id
        for attempt in range(3):
            try:
                updated = self.client.update_secret(
                    request={
                        "secret": {
                            "name": self.name,
                            "annotations": annotations,
                            "version_aliases": aliases,
                            "etag": etag,
                        },
                        "update_mask": {
                            "paths": ["annotations", "version_aliases"]
                        },
                    }
                )
                return str(updated.etag)
            except Exception as exc:  # noqa: BLE001
                current = self.client.get_secret(request={"name": self.name})
                current_aliases = dict(current.version_aliases)
                if current_aliases.get(self.alias) == version_number:
                    return str(current.etag)
                current_lock = _parse_lock(
                    dict(current.annotations).get(_LOCK_ANNOTATION)
                )
                if not current_lock or current_lock[0] != lease.owner:
                    raise DSersAuthUnavailable(
                        "lost ownership while promoting the rotated DSers token; "
                        f"new version is {version_number}, active alias was not changed"
                    ) from exc
                if attempt == 2:
                    raise DSersAuthUnavailable(
                        "could not atomically promote the rotated DSers token "
                        f"version {version_number}: {exc}"
                    ) from exc
                etag = str(current.etag)
                annotations = dict(current.annotations)
                annotations.pop(_LOCK_ANNOTATION, None)
                annotations[_ACTIVE_VERSION_ANNOTATION] = str(
                    version_number
                )
                annotations[_ACTIVE_ROTATION_ANNOTATION] = bundle.rotation_id
                aliases = current_aliases
                aliases[self.alias] = version_number
        raise AssertionError("unreachable")

    def release_refresh_lease(self, owner: str) -> None:
        for _attempt in range(3):
            try:
                secret = self.client.get_secret(request={"name": self.name})
                annotations = dict(secret.annotations)
                current = _parse_lock(annotations.get(_LOCK_ANNOTATION))
                if not current or current[0] != owner:
                    return
                annotations.pop(_LOCK_ANNOTATION, None)
                self.client.update_secret(
                    request={
                        "secret": {
                            "name": self.name,
                            "annotations": annotations,
                            "etag": secret.etag,
                        },
                        "update_mask": {"paths": ["annotations"]},
                    }
                )
                return
            except Exception:  # noqa: BLE001
                continue
        _LOG.error(
            "[dsers-auth] failed to release refresh lease owner=%s; "
            "the bounded lease will expire",
            owner,
        )


class DSersTokenManager:
    def __init__(
        self,
        store: TokenStore,
        *,
        token_url: str,
        http_client: httpx.Client | None = None,
        clock: Any = time.time,
    ) -> None:
        self.store = store
        self.token_url = token_url
        self.http = http_client or httpx.Client(timeout=20)
        self.clock = clock

    def access_token(
        self,
        *,
        force_refresh: bool = False,
        rejected_access_token: str | None = None,
    ) -> str:
        bundle = self.store.read_active()
        now = float(self.clock())
        another_instance_refreshed = bool(
            rejected_access_token
            and bundle.access_token != rejected_access_token
        )
        if (
            another_instance_refreshed
            or (not force_refresh and bundle.access_is_fresh(now=now))
        ):
            return bundle.access_token

        owner = str(uuid4())
        lease = self.store.acquire_refresh_lease(
            owner, ttl_seconds=120, wait_seconds=8
        )
        try:
            # A waiter may have observed an expired token just before another
            # instance completed rotation. Re-read only after owning the lease.
            current = self.store.read_active()
            now = float(self.clock())
            another_instance_refreshed = bool(
                rejected_access_token
                and current.access_token != rejected_access_token
            )
            if (
                another_instance_refreshed
                or (not force_refresh and current.access_is_fresh(now=now))
            ):
                self.store.release_refresh_lease(owner)
                return current.access_token

            response = self.http.post(
                self.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": current.refresh_token,
                    "client_id": current.client_id,
                    "resource": current.resource,
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
            access_token = str(payload.get("access_token") or "")
            refresh_token = str(payload.get("refresh_token") or "")
            expires_in = float(payload.get("expires_in") or 0)
            if not access_token or not refresh_token or expires_in <= 0:
                raise DSersAuthUnavailable(
                    "DSers refresh response omitted a rotated refresh token, "
                    "access token, or expiry"
                )
            rotated = OAuthBundle(
                client_id=current.client_id,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=now + expires_in,
                token_type=str(payload.get("token_type") or "Bearer"),
                scope=str(payload.get("scope") or current.scope),
                resource=current.resource,
                rotation_id=str(uuid4()),
            )
            self.store.persist_rotation(rotated, lease)
            _LOG.info(
                "[dsers-auth] refresh token rotated and promoted in Secret Manager "
                "at %s",
                datetime.now(timezone.utc).isoformat(),
            )
            return rotated.access_token
        except Exception as exc:  # noqa: BLE001
            self.store.release_refresh_lease(owner)
            if isinstance(exc, DSersAuthUnavailable):
                raise
            raise DSersAuthUnavailable(
                f"DSers token refresh failed; autonomous sourcing disabled: {exc}"
            ) from exc


def token_manager_from_settings() -> DSersTokenManager:
    if not settings.dsers_secret_id:
        raise DSersAuthUnavailable(
            "DSERS_SECRET_ID is not configured; existing catalog remains available"
        )
    store = GcpSecretManagerTokenStore(
        settings.dsers_secret_project_id,
        settings.dsers_secret_id,
        settings.dsers_secret_alias,
    )
    return DSersTokenManager(
        store, token_url=settings.dsers_oauth_token_url
    )
