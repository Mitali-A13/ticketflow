"""API-key auth. API routes want the X-API-Key header. Browser pages also accept
the cookie set by the review login page. Both then go through the rate limiter."""

import hmac
import os
from typing import Annotated

from fastapi import Header, HTTPException, Request

COOKIE_NAME = "ticketflow_key"


class LoginRequired(Exception):
    """Raised for browser pages without a valid key; main.py redirects to the login page."""


def valid_key(candidate: str | None) -> bool:
    expected = os.environ.get("TICKETFLOW_API_KEY", "").strip()
    if not expected or not candidate:  # no configured key -> reject everything
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())  # constant time


def _check_rate(request: Request, key: str) -> None:
    allowed, retry_after = request.app.state.limiter.check(key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


def api_auth(
    request: Request, x_api_key: Annotated[str | None, Header()] = None
) -> str:
    if not valid_key(x_api_key):
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    _check_rate(request, x_api_key)
    return x_api_key


def browser_auth(request: Request) -> str:
    key = request.headers.get("x-api-key") or request.cookies.get(COOKIE_NAME)
    if not valid_key(key):
        raise LoginRequired()
    _check_rate(request, key)
    return key
