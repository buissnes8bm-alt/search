"""Serper.dev search, and flattening the results into one text blob."""

from __future__ import annotations

import logging
from typing import Iterable, List

import requests

from .config import BLOB_CHAR_CAP, RESULTS_PER_QUERY
from .http import post_json

log = logging.getLogger(__name__)

SERPER_URL = "https://google.serper.dev/search"


def search(session: requests.Session, query: str, api_key: str) -> dict:
    return post_json(
        session,
        SERPER_URL,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        payload={"q": query, "num": RESULTS_PER_QUERY},
    )


def search_all(session: requests.Session, queries: Iterable[str], api_key: str) -> List[dict]:
    responses = []
    for query in queries:
        log.info("searching: %s", query)
        responses.append(search(session, query, api_key))
    return responses


def flatten(responses: Iterable[dict], cap: int = BLOB_CHAR_CAP) -> str:
    """Merge every organic result into one blob, deduped and capped.

    The URL is included because the scoring prompt asks for a source_url, which
    the model cannot fill from a title and snippet alone.
    """
    lines: List[str] = []
    seen = set()

    for response in responses:
        for result in response.get("organic") or []:
            title = " ".join(str(result.get("title") or "").split())
            snippet = " ".join(str(result.get("snippet") or "").split())
            link = str(result.get("link") or "").strip()
            if not title and not snippet:
                continue
            key = f"{title}|{snippet}".lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"{title} — {snippet} [{link}]" if link else f"{title} — {snippet}")

    # Cap on a whole-result boundary so the model never reads a half entry.
    blob, used = [], 0
    for line in lines:
        if used + len(line) + 1 > cap:
            break
        blob.append(line)
        used += len(line) + 1

    log.info("flattened %d results into %d chars", len(blob), used)
    return "\n".join(blob)
