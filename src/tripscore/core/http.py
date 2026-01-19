"""
HTTP helpers.

This module centralizes the minimal HTTP client logic used by ingestion clients.

Design goals:
- Small surface area (GET JSON, POST form).
- Deterministic defaults (timeout + User-Agent).
- Raise on non-2xx so callers can decide how to fail (often "fail-open" in recommenders).
"""

from __future__ import annotations

from typing import Any

import httpx


DEFAULT_USER_AGENT = "tripscore/0.1.0 (+https://local)"


def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 15,
) -> Any:
    """GET `url` and return the decoded JSON response.

    Raises:
        httpx.HTTPError: On transport errors or non-2xx status codes.
        ValueError: If the response body is not valid JSON.
    """
    request_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        request_headers.update(headers)

    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.get(url, params=params, headers=request_headers)
        resp.raise_for_status()
        return resp.json()


def post_form(
    url: str,
    *,
    data: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 15,
) -> Any:
    """POST `data` as form-encoded body and return the decoded JSON response.

    Used by the TDX OAuth client-credentials flow.

    Raises:
        httpx.HTTPError: On transport errors or non-2xx status codes.
        ValueError: If the response body is not valid JSON.
    """
    request_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        request_headers.update(headers)

    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.post(url, data=data, headers=request_headers)
        resp.raise_for_status()
        return resp.json()
