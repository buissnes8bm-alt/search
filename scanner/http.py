"""One shared HTTP session with retries.

Every external call in this project gets the same retry policy, so a rate limit
or a dropped connection does not kill the daily run. Retries cover connection
errors and the transient status codes only — a 401 or a 400 is a bug in the
request and retrying it just wastes time.
"""

from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import HTTP_TIMEOUT, RETRY_ATTEMPTS, RETRY_WAIT_SECONDS

log = logging.getLogger(__name__)

RETRYABLE_STATUSES = (429, 500, 502, 503, 504)


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=RETRY_ATTEMPTS,
        connect=RETRY_ATTEMPTS,
        read=RETRY_ATTEMPTS,
        status=RETRY_ATTEMPTS,
        backoff_factor=RETRY_WAIT_SECONDS,
        status_forcelist=RETRYABLE_STATUSES,
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def post_json(session: requests.Session, url: str, *, headers: dict, payload: dict) -> dict:
    """POST JSON and return the decoded body, raising with a useful message."""
    response = session.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
    if not response.ok:
        # Truncated: provider error bodies can be enormous, and the first
        # couple of hundred characters always carry the actual reason.
        raise RuntimeError(
            f"{response.status_code} from {url.split('?')[0]}: {response.text[:300]}"
        )
    return response.json()
