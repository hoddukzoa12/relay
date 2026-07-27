from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from threading import Condition, Thread
import time

import httpx
import pytest

from agentic_broker.common.dsers_auth import (
    DSersAuthUnavailable,
    DSersTokenManager,
    GcpSecretManagerTokenStore,
    OAuthBundle,
    RefreshLease,
)
from google.api_core.exceptions import FailedPrecondition


class MemoryTokenStore:
    """A blocking fake that models one cross-instance Secret Manager lease."""

    def __init__(self, bundle: OAuthBundle) -> None:
        self.bundle = bundle
        self.condition = Condition()
        self.owner: str | None = None
        self.version = 1
        self.persisted: list[OAuthBundle] = []
        self.released: list[str] = []

    def read_active(self) -> OAuthBundle:
        with self.condition:
            return self.bundle

    def acquire_refresh_lease(
        self, owner: str, *, ttl_seconds: int, wait_seconds: float
    ) -> RefreshLease:
        deadline = time.monotonic() + wait_seconds
        with self.condition:
            while self.owner is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DSersAuthUnavailable("lease timeout")
                self.condition.wait(remaining)
            self.owner = owner
            return RefreshLease(
                owner=owner,
                etag=f"etag-{self.version}",
                annotations={},
                version_aliases={"relay-active": self.version},
            )

    def persist_rotation(
        self, bundle: OAuthBundle, lease: RefreshLease
    ) -> str:
        with self.condition:
            assert self.owner == lease.owner
            self.version += 1
            self.bundle = bundle
            self.persisted.append(bundle)
            self.owner = None
            self.condition.notify_all()
            return f"etag-{self.version}"

    def release_refresh_lease(self, owner: str) -> None:
        with self.condition:
            if self.owner == owner:
                self.owner = None
                self.released.append(owner)
                self.condition.notify_all()


class FakeSecretManagerClient:
    def __init__(self, initial: OAuthBundle) -> None:
        self.etag = 1
        self.annotations: dict[str, str] = {}
        self.aliases = {"relay-active": 1}
        self.versions = {1: initial.to_bytes()}

    def _secret(self):
        return SimpleNamespace(
            etag=f"etag-{self.etag}",
            annotations=dict(self.annotations),
            version_aliases=dict(self.aliases),
        )

    def get_secret(self, request: dict):
        assert request["name"].endswith("/secrets/dsers")
        return self._secret()

    def update_secret(self, request: dict):
        supplied = request["secret"]
        if supplied["etag"] != f"etag-{self.etag}":
            raise FailedPrecondition("etag changed")
        paths = set(request["update_mask"]["paths"])
        if "annotations" in paths:
            self.annotations = dict(supplied["annotations"])
        if "version_aliases" in paths:
            self.aliases = dict(supplied["version_aliases"])
        self.etag += 1
        return self._secret()

    def add_secret_version(self, request: dict):
        number = max(self.versions) + 1
        self.versions[number] = bytes(request["payload"]["data"])
        return SimpleNamespace(
            name=f"projects/project/secrets/dsers/versions/{number}"
        )

    def access_secret_version(self, request: dict):
        selector = request["name"].rsplit("/", 1)[-1]
        number = self.aliases[selector] if not selector.isdigit() else int(selector)
        return SimpleNamespace(
            name=f"projects/project/secrets/dsers/versions/{number}",
            payload=SimpleNamespace(data=self.versions[number]),
        )

    def list_secret_versions(self, request: dict):
        return [
            SimpleNamespace(
                name=f"projects/project/secrets/dsers/versions/{number}",
                state="ENABLED",
            )
            for number in sorted(self.versions, reverse=True)
        ]


def bundle(*, expires_at: float) -> OAuthBundle:
    return OAuthBundle(
        client_id="client-1",
        access_token="access-old",
        refresh_token="refresh-old",
        expires_at=expires_at,
        scope="mcp",
    )


def test_fresh_access_token_does_not_rotate() -> None:
    store = MemoryTokenStore(bundle(expires_at=20_000))
    manager = DSersTokenManager(
        store,
        token_url="https://mcp.dsers.test/oauth/token",
        clock=lambda: 10_000,
    )

    assert manager.access_token() == "access-old"
    assert store.persisted == []


def test_rotated_token_is_persisted_and_survives_a_new_manager() -> None:
    requests: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(
            200,
            json={
                "access_token": "access-new",
                "refresh_token": "refresh-new",
                "expires_in": 3600,
                "scope": "mcp",
            },
        )

    store = MemoryTokenStore(bundle(expires_at=1))
    manager = DSersTokenManager(
        store,
        token_url="https://mcp.dsers.test/oauth/token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: 1_000,
    )

    assert manager.access_token() == "access-new"
    assert requests == [
        {
            "grant_type": "refresh_token",
            "refresh_token": "refresh-old",
            "client_id": "client-1",
            "resource": "https://mcp.dsers.com/dropshipping/mcp",
        }
    ]
    assert store.persisted[0].refresh_token == "refresh-new"

    restarted = DSersTokenManager(
        store,
        token_url="https://mcp.dsers.test/oauth/token",
        clock=lambda: 1_001,
    )
    assert restarted.access_token() == "access-new"


def test_two_instances_refresh_a_rotating_token_only_once() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return httpx.Response(
            200,
            json={
                "access_token": "access-new",
                "refresh_token": "refresh-new",
                "expires_in": 3600,
            },
        )

    store = MemoryTokenStore(bundle(expires_at=1))
    results: list[str] = []

    def refresh() -> None:
        manager = DSersTokenManager(
            store,
            token_url="https://mcp.dsers.test/oauth/token",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            clock=lambda: 1_000,
        )
        results.append(manager.access_token())

    threads = [Thread(target=refresh), Thread(target=refresh)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == 1
    assert results == ["access-new", "access-new"]
    assert len(store.persisted) == 1


def test_invalid_rotation_response_releases_lease_and_logs_clear_failure() -> None:
    store = MemoryTokenStore(bundle(expires_at=1))
    manager = DSersTokenManager(
        store,
        token_url="https://mcp.dsers.test/oauth/token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "access_token": "access-new",
                        # A missing rotated refresh token must never overwrite.
                        "expires_in": 3600,
                    },
                )
            )
        ),
        clock=lambda: 1_000,
    )

    with pytest.raises(DSersAuthUnavailable, match="rotated refresh token"):
        manager.access_token()
    assert store.owner is None
    assert len(store.released) == 1
    assert store.bundle.refresh_token == "refresh-old"


def test_secret_manager_lease_uses_etag_and_alias_promotion_is_atomic() -> None:
    client = FakeSecretManagerClient(bundle(expires_at=1))
    first = GcpSecretManagerTokenStore(
        "project", "dsers", "relay-active", client=client
    )
    second = GcpSecretManagerTokenStore(
        "project", "dsers", "relay-active", client=client
    )
    lease = first.acquire_refresh_lease(
        "instance-a", ttl_seconds=120, wait_seconds=0
    )
    with pytest.raises(DSersAuthUnavailable, match="another Relay instance"):
        second.acquire_refresh_lease(
            "instance-b", ttl_seconds=120, wait_seconds=0
        )

    rotated = replace(
        bundle(expires_at=5_000),
        access_token="access-new",
        refresh_token="refresh-new",
        rotation_id="rotation-1",
    )
    first.persist_rotation(rotated, lease)

    assert client.aliases["relay-active"] == 2
    assert client.annotations == {
        "relay-dsers-active-version": "2",
        "relay-dsers-active-rotation": "rotation-1",
    }
    assert second.read_active().refresh_token == "refresh-new"

    # Alias-only rollback cannot make a restarted process consume the invalid
    # rotated-away token; the atomically promoted numeric annotation wins.
    client.aliases["relay-active"] = 1
    assert second.read_active().refresh_token == "refresh-new"
