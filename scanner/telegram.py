"""Telegram message formatting and delivery."""

from __future__ import annotations

import logging
import re
from typing import List

import requests

from .config import HTTP_TIMEOUT, TOP_N
from .products import Product

log = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4096

# Telegram's legacy Markdown does not honour backslash escapes reliably, and
# MarkdownV2 would need escaping for the - . | ~ already in this format. So the
# safe move is to remove the characters that break a message. The sheet keeps
# the original, unmodified name; only the Telegram copy is sanitised.
_UNSAFE = re.compile(r"[_*`\[\]]")


def clean(value) -> str:
    return " ".join(_UNSAFE.sub(" ", str(value or "")).split())


def _money(value) -> str:
    return "?" if value is None else f"{value:g}"


def build_message(fresh: List[Product], date: str) -> str:
    """The shortlist, or the plain sentence when nothing cleared the bar."""
    shortlist = [p for p in fresh if p.passed][:TOP_N]
    if not shortlist:
        return "Scan complete. Nothing cleared the bar today."

    lines = [f"*Product scan — {date}*", ""]
    for index, product in enumerate(shortlist, start=1):
        lines.append(f"*{index}. {clean(product.product)} — {product.score}/10*")
        lines.append(clean(product.reason))
        lines.append(
            f"Retail ~${_money(product.est_retail_usd)} | "
            f"Landed ~${_money(product.est_landed_usd)}"
        )
        lines.append(f"Risk: {clean(product.risk)}")
        lines.append("")
    lines.append("_Prices are estimates. Verify on AliExpress before buying._")

    message = "\n".join(lines)
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[: MAX_MESSAGE_CHARS - 3] + "..."
    return message


def nothing_new_message(scored_count: int) -> str:
    plural = "" if scored_count == 1 else "s"
    return (
        f"Scan complete. {scored_count} product{plural} scored, "
        "all already reported. Nothing new today."
    )


def failure_message(reason: str) -> str:
    return f"Scan complete. The model returned no usable output: {clean(reason)}."


def send(session: requests.Session, token: str, chat_id: str, text: str) -> None:
    if not text.strip():
        raise ValueError("refusing to send an empty Telegram message")
    response = session.post(
        f"{API_ROOT}/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=HTTP_TIMEOUT,
    )
    if not response.ok:
        raise RuntimeError(f"telegram {response.status_code}: {response.text[:300]}")
    log.info("sent %d chars to chat %s", len(text), chat_id)
