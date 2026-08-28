"""Appending rows to Google Sheets with a service account.

A service account, not OAuth. OAuth needs a browser redirect, which does not
exist on a CI runner — you would have to generate a refresh token by hand and
store it. A service account is a key file that just works headlessly. The cost
is one extra step: the sheet must be shared with the service account's email,
the same way you would share it with a colleague.
"""

from __future__ import annotations

import json
import logging
from typing import List, Sequence

import requests
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import service_account

from .config import HTTP_TIMEOUT

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
API_ROOT = "https://sheets.googleapis.com/v4/spreadsheets"

HEADERS = [
    "date",
    "product",
    "score",
    "est_retail_usd",
    "est_landed_usd",
    "reason",
    "risk",
]


class SheetsError(RuntimeError):
    pass


def _credentials(service_account_json: str):
    try:
        info = json.loads(service_account_json)
    except json.JSONDecodeError as error:
        raise SheetsError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the whole key "
            "file contents, braces included."
        ) from error
    try:
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    except (ValueError, KeyError) as error:
        raise SheetsError(f"service account key rejected: {error}") from error


def _token(service_account_json: str) -> str:
    creds = _credentials(service_account_json)
    creds.refresh(GoogleRequest())
    return creds.token


def append_rows(
    session: requests.Session,
    rows: Sequence[Sequence],
    *,
    sheet_id: str,
    tab: str,
    service_account_json: str,
) -> int:
    """Append rows to the sheet. Returns how many were written."""
    if not rows:
        return 0

    token = _token(service_account_json)
    url = f"{API_ROOT}/{sheet_id}/values/{tab}!A:G:append"
    response = session.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        json={"values": [list(r) for r in rows]},
        timeout=HTTP_TIMEOUT,
    )
    if not response.ok:
        hint = ""
        if response.status_code in (403, 404):
            hint = (
                " — check the sheet is shared with the service account's "
                "client_email as an Editor, and that SHEET_ID is the id from "
                "the URL, not the whole URL"
            )
        raise SheetsError(f"sheets {response.status_code}: {response.text[:300]}{hint}")

    log.info("appended %d rows to %s!%s", len(rows), sheet_id, tab)
    return len(rows)


def ensure_headers(
    session: requests.Session,
    *,
    sheet_id: str,
    tab: str,
    service_account_json: str,
) -> bool:
    """Write the header row if the sheet is empty. Returns True if written."""
    token = _token(service_account_json)
    read = session.get(
        f"{API_ROOT}/{sheet_id}/values/{tab}!A1:G1",
        headers={"Authorization": f"Bearer {token}"},
        timeout=HTTP_TIMEOUT,
    )
    if not read.ok:
        raise SheetsError(f"sheets {read.status_code}: {read.text[:300]}")
    if (read.json().get("values") or []):
        return False

    write = session.put(
        f"{API_ROOT}/{sheet_id}/values/{tab}!A1:G1",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"valueInputOption": "RAW"},
        json={"values": [HEADERS]},
        timeout=HTTP_TIMEOUT,
    )
    if not write.ok:
        raise SheetsError(f"sheets {write.status_code}: {write.text[:300]}")
    log.info("wrote header row")
    return True


def rows_from(products: List) -> List[List]:
    return [p.as_row() for p in products]
