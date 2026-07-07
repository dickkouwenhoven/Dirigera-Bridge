#!/usr/bin/env python3
"""
scripts/generate_dirigera_token.py

One-time interactive helper to obtain a Dirigera hub access token via
the hub's local pairing flow, and — only with explicit consent — save
it into .env as DIRIGERA_TOKEN.

Why this is a STANDALONE script, run directly on the host, never
inside Docker:
    1. docker-compose.yml uses `env_file: .env`, which only reads
       .env once at container CREATION time, on the host side. The
       file itself is never mounted into the running container's
       filesystem — nothing running inside the dirigera-bridge
       container can see or write the real .env at all.
    2. Real pairing requires a live human to physically press the
       Action button on the bottom of the hub within ~60 seconds of
       the request being made. That is a poor fit for a long-running
       service under `restart: always` — every reference
       implementation checked (the official Python 'dirigera' PyPI
       package, the JS/TS 'lpgera/dirigera' client, a Java client,
       and an openHAB binding) implements this as a separate,
       explicit, interactive step, never as part of a service's
       normal startup path.

Run it directly with the project's existing venv:
    source .venv/bin/activate
    python scripts/generate_dirigera_token.py
    # or: python scripts/generate_dirigera_token.py --ip 192.168.1.50

CONFIRMED vs inferred — this was checked against real source code
(lpgera/dirigera, a maintained open-source TypeScript client with a
working implementation of this exact flow —
https://github.com/lpgera/dirigera/blob/main/src/index.ts), not
guessed:

    CONFIRMED (seen directly in that library's source):
        GET  /oauth/authorize?audience=homesmart.local
             &response_type=code
             &code_challenge={challenge}
             &code_challenge_method={method}
             → { "code": "..." }
        (wait for the physical Action button press)
        POST /oauth/token
             form: code={code}&name={hostname}
                   &grant_type=authorization_code
                   &code_verifier={verifier}
             → { "access_token": "..." }
        - The token exchange is retried in a loop until the button
          press happens or a ~60 second window elapses.
        - 'name' is the requesting device's hostname.

    ASSUMED (standard/highly-likely, but not independently verified
    against Dirigera specifically):
        - code_challenge_method is 'S256' (RFC 7636 PKCE) — this is
          the universal industry-standard choice and consistent with
          how every PKCE implementation works, but the literal string
          wasn't visible in what was checked.
        - The '/v1' path prefix and port 8443 — these match the
          convention this codebase already uses for every other
          Dirigera endpoint (see websocket_client.py, rest_client.py),
          so they are used here for consistency, not independently
          re-confirmed for the oauth endpoints specifically.

    If pairing fails against a real hub, these are the first two
    things to double check.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from dotenv import dotenv_values, set_key

_HUB_PORT = 8443
_API_PREFIX = "/v1"
_AUDIENCE = "homesmart.local"
_CODE_CHALLENGE_METHOD = "S256"
_BUTTON_PRESS_WINDOW_SECONDS = 60
_POLL_INTERVAL_SECONDS = 2

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


# ── SSL ────────────────────────────────────────────────────────────────────


def _build_ssl_context() -> ssl.SSLContext:
    """
    Dirigera uses a self-signed TLS certificate. Matches the same
    approach already used in app/dirigera/websocket_client.py's
    _build_ssl_context() for the identical reason — intentional and
    safe for local hub communication.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ── PKCE ───────────────────────────────────────────────────────────────────


def _generate_pkce_pair() -> Tuple[str, str]:
    """
    Generate a PKCE code_verifier and its S256 code_challenge, per
    RFC 7636 — the same mechanism the real lpgera/dirigera client
    uses for this exact flow.

    Returns:
        (code_verifier, code_challenge)
    """
    verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    )
    challenge_digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(challenge_digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ── Hub requests ─────────────────────────────────────────────────────────────


def _request_authorization_code(
    ip: str,
    code_challenge: str,
    ctx: ssl.SSLContext,
) -> str:
    """
    Step 1: ask the hub to start pairing.

    This is what makes the hub's Action button start accepting a
    press — nothing happens on the hub itself until this request is
    made.

    Raises:
        RuntimeError: if the hub's response has no 'code' field.
        urllib.error.URLError / HTTPError: on network/HTTP failure.
    """
    query = urllib.parse.urlencode(
        {
            "audience": _AUDIENCE,
            "response_type": "code",
            "code_challenge": code_challenge,
            "code_challenge_method": _CODE_CHALLENGE_METHOD,
        }
    )
    url = f"https://{ip}:{_HUB_PORT}{_API_PREFIX}/oauth/authorize?{query}"

    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
        data = json.loads(resp.read())

    code = data.get("code")
    if not code:
        raise RuntimeError(f"Hub did not return an authorization code: {data!r}")
    return code


def _exchange_code_for_token(
    ip: str,
    code: str,
    code_verifier: str,
    ctx: ssl.SSLContext,
) -> str:
    """
    Step 2: exchange the authorization code for an access token.

    Only succeeds once the physical Action button has actually been
    pressed on the hub — until then the hub rejects the exchange, so
    this retries on a short interval for up to
    _BUTTON_PRESS_WINDOW_SECONDS, matching the real client library's
    own retry behaviour for this step.

    Raises:
        TimeoutError: if no button press is detected in time.
        urllib.error.HTTPError: on an unexpected (non-pending) failure.
    """
    url = f"https://{ip}:{_HUB_PORT}{_API_PREFIX}/oauth/token"
    body = urllib.parse.urlencode(
        {
            "code": code,
            "name": socket.gethostname(),
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
    ).encode("ascii")

    deadline = time.monotonic() + _BUTTON_PRESS_WINDOW_SECONDS

    while time.monotonic() < deadline:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read())
            token = data.get("access_token")
            if token:
                return token
        except urllib.error.HTTPError as exc:
            # Expected while waiting for the button press — the hub
            # rejects the exchange until the physical press happens.
            if exc.code not in (400, 403):
                raise

        time.sleep(_POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"No button press detected within {_BUTTON_PRESS_WINDOW_SECONDS} "
        "seconds. Run the script again and press the Action button "
        "promptly after it starts."
    )


# ── .env handling ────────────────────────────────────────────────────────────


def _read_existing_dirigera_ip() -> Optional[str]:
    """Look for an existing DIRIGERA_IP in .env so the user isn't asked
    to retype something already configured."""
    if not _ENV_PATH.exists():
        return None
    return dotenv_values(_ENV_PATH).get("DIRIGERA_IP")


def _save_token_to_env(token: str) -> None:
    """
    Update (or add) DIRIGERA_TOKEN in .env in place, leaving every
    other line untouched. Only ever called after explicit user
    confirmation — never silently.

    Uses python-dotenv's set_key(), already a project dependency, so
    quoting/formatting/ordering is handled correctly rather than by
    hand-rolled line parsing.
    """
    _ENV_PATH.touch(exist_ok=True)
    set_key(str(_ENV_PATH), "DIRIGERA_TOKEN", token)


# ── Entry point ────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Obtain a Dirigera hub access token via the hub's pairing flow."
    )
    parser.add_argument("--ip", help="Dirigera hub IP address", default=None)
    args = parser.parse_args()

    ip = args.ip or _read_existing_dirigera_ip()
    if not ip:
        ip = input("Dirigera hub IP address: ").strip()

    if not ip:
        print("No IP address given — aborting.", file=sys.stderr)
        return 1

    ctx = _build_ssl_context()
    verifier, challenge = _generate_pkce_pair()

    print(f"Requesting pairing from {ip} ...")
    try:
        code = _request_authorization_code(ip, challenge, ctx)
    except Exception as exc:
        print(f"Failed to start pairing: {exc}", file=sys.stderr)
        return 1

    print()
    print("Press the Action button on the bottom of your Dirigera hub now.")
    print(f"You have {_BUTTON_PRESS_WINDOW_SECONDS} seconds.")
    print()

    try:
        token = _exchange_code_for_token(ip, code, verifier, ctx)
    except Exception as exc:
        print(f"Pairing failed: {exc}", file=sys.stderr)
        return 1

    print("Pairing successful.")
    print(f"Access token: {token}")
    print()
    print(
        "Treat this token as a secret — never share it, log it, or "
        "commit it to version control."
    )
    print()

    answer = (
        input(f"Save this token to {_ENV_PATH} as DIRIGERA_TOKEN? [y/N] ")
        .strip()
        .lower()
    )
    if answer == "y":
        _save_token_to_env(token)
        print("Saved.")
        print("Restart the bridge to pick it up:")
        print("  docker compose up -d --build")
    else:
        print("Not saved. Copy the token above into .env manually if needed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
