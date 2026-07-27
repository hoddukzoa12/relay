#!/usr/bin/env python3
"""One-time interactive DSers OAuth bootstrap into Google Secret Manager.

This script is intentionally human-run on a machine with a browser.  Cloud Run
never performs authorization-code login; it only consumes and safely rotates
the resulting refresh token.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "agents"))

from agentic_broker.common.dsers_auth import (  # noqa: E402
    DSersTokenManager,
    GcpSecretManagerTokenStore,
    OAuthBundle,
)

AUTHORIZATION_ENDPOINT = "https://mcp.dsers.com/oauth/authorize"
REGISTRATION_ENDPOINT = "https://mcp.dsers.com/oauth/register"
TOKEN_ENDPOINT = "https://mcp.dsers.com/oauth/token"
RESOURCE = "https://mcp.dsers.com/dropshipping/mcp"


class CallbackResult:
    code: str = ""
    state: str = ""
    error: str = ""


def _handler(
    expected_path: str, result: CallbackResult
) -> type[BaseHTTPRequestHandler]:
    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path != expected_path:
                self.send_error(404)
                return
            query = parse_qs(parsed.query)
            result.code = query.get("code", [""])[0]
            result.state = query.get("state", [""])[0]
            result.error = query.get("error_description", query.get("error", [""]))[0]
            ok = bool(result.code and not result.error)
            body = (
                "DSers authorization received. Return to the terminal."
                if ok
                else f"DSers authorization failed: {result.error or 'missing code'}"
            )
            encoded = body.encode("utf-8")
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return OAuthCallbackHandler


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _exchange_authorization(
    *, callback_port: int, timeout_seconds: int
) -> OAuthBundle:
    redirect_path = "/oauth/callback"
    redirect_uri = f"http://127.0.0.1:{callback_port}{redirect_path}"
    registration = httpx.post(
        REGISTRATION_ENDPOINT,
        json={
            "client_name": "Relay DSers Cloud Run bootstrap",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "mcp",
        },
        timeout=20,
    )
    registration.raise_for_status()
    client_id = str(registration.json().get("client_id") or "")
    if not client_id:
        raise RuntimeError("DSers dynamic registration omitted client_id")

    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    expected_state = secrets.token_urlsafe(32)
    authorization_url = f"{AUTHORIZATION_ENDPOINT}?{urlencode({
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': 'mcp',
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
        'state': expected_state,
        'resource': RESOURCE,
    })}"

    result = CallbackResult()
    server = HTTPServer(
        ("127.0.0.1", callback_port),
        _handler(redirect_path, result),
    )
    server.timeout = timeout_seconds
    print(f"Opening DSers authorization in your browser:\n{authorization_url}")
    if not webbrowser.open(authorization_url):
        print("Browser did not open automatically; copy the URL above.")
    server.handle_request()
    server.server_close()
    if result.error:
        raise RuntimeError(f"DSers authorization failed: {result.error}")
    if not result.code:
        raise TimeoutError(
            f"No OAuth callback arrived within {timeout_seconds} seconds"
        )
    if not secrets.compare_digest(result.state, expected_state):
        raise RuntimeError("OAuth callback state did not match")

    token = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": result.code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "resource": RESOURCE,
        },
        headers={"Accept": "application/json"},
        timeout=20,
    )
    token.raise_for_status()
    payload = token.json()
    access_token = str(payload.get("access_token") or "")
    refresh_token = str(payload.get("refresh_token") or "")
    expires_in = float(payload.get("expires_in") or 0)
    if not access_token or not refresh_token or expires_in <= 0:
        raise RuntimeError(
            "DSers token response omitted access_token, rotating "
            "refresh_token, or expires_in"
        )
    return OAuthBundle(
        client_id=client_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=time.time() + expires_in,
        token_type=str(payload.get("token_type") or "Bearer"),
        scope=str(payload.get("scope") or ""),
        resource=RESOURCE,
        rotation_id=f"bootstrap-{secrets.token_hex(16)}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Authorize DSers once and store its rotating grant safely"
    )
    parser.add_argument(
        "--project",
        default=os.getenv("DSERS_SECRET_PROJECT_ID")
        or os.getenv("GCP_PROJECT_ID"),
        required=not bool(
            os.getenv("DSERS_SECRET_PROJECT_ID") or os.getenv("GCP_PROJECT_ID")
        ),
    )
    parser.add_argument(
        "--secret",
        default=os.getenv("DSERS_SECRET_ID", "relay-dsers-oauth"),
    )
    parser.add_argument(
        "--alias",
        default=os.getenv("DSERS_SECRET_ALIAS", "relay-active"),
    )
    parser.add_argument("--callback-port", type=int, default=8765)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="intentionally replace an existing active grant",
    )
    parser.add_argument(
        "--verify-rotation",
        action="store_true",
        help="rotate once after bootstrap and verify a fresh manager can read it",
    )
    args = parser.parse_args()

    bundle = _exchange_authorization(
        callback_port=args.callback_port,
        timeout_seconds=args.timeout_seconds,
    )
    store = GcpSecretManagerTokenStore(
        args.project, args.secret, args.alias
    )
    store.write_bootstrap_bundle(bundle, replace_existing=args.replace)
    print(
        f"Stored DSers OAuth in projects/{args.project}/secrets/{args.secret} "
        f"(alias {args.alias}) at {datetime.now(timezone.utc).isoformat()}."
    )

    if args.verify_rotation:
        manager = DSersTokenManager(store, token_url=TOKEN_ENDPOINT)
        manager.access_token(force_refresh=True)
        restarted = DSersTokenManager(
            GcpSecretManagerTokenStore(args.project, args.secret, args.alias),
            token_url=TOKEN_ENDPOINT,
        )
        restarted.access_token()
        print(
            "Rotation verified: a new secret version was promoted and a fresh "
            "manager read the active grant."
        )


if __name__ == "__main__":
    main()
