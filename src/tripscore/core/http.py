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
    request_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        request_headers.update(headers)

    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.post(url, data=data, headers=request_headers)
        resp.raise_for_status()
        return resp.json()
