from __future__ import annotations

import jwt

from agentic_broker.common import service_clients


class _FakeCredentials:
    valid = False
    token: str | None = None

    def refresh(self, _request: object) -> None:
        self.valid = True
        self.token = jwt.encode(
            {
                "iss": "https://accounts.google.com",
                "aud": "https://payments-abc-uc.a.run.app",
                "email": "relay-buyer@example.iam.gserviceaccount.com",
                "sub": "123",
            },
            "test-only-key-with-at-least-thirty-two-bytes",
            algorithm="HS256",
        )


def test_local_service_url_does_not_request_google_credentials(monkeypatch) -> None:
    def unexpected(_audience: str) -> object:
        raise AssertionError("local calls must not request a Google ID token")

    monkeypatch.setattr(service_clients, "_id_token_credentials", unexpected)
    assert service_clients._cloud_run_auth_headers("http://payments:8081/health") == {}


def test_cloud_run_service_url_gets_audience_bound_bearer(monkeypatch) -> None:
    audiences: list[str] = []

    def credentials(audience: str) -> _FakeCredentials:
        audiences.append(audience)
        return _FakeCredentials()

    monkeypatch.setattr(service_clients, "_id_token_credentials", credentials)
    headers = service_clients._cloud_run_auth_headers(
        "https://payments-abc-uc.a.run.app/verify"
    )

    assert audiences == ["https://payments-abc-uc.a.run.app"]
    assert headers["Authorization"].startswith("Bearer eyJ")
