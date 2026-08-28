"""Gemini call: extraction and scoring in one request.

The system prompt is the spec's, verbatim. `responseSchema` constrains Gemini to
emit exactly the shape below, so there are no code fences, no preamble and no
malformed objects to recover from.
"""

from __future__ import annotations

import logging
from typing import Tuple

import requests

from .config import MAX_OUTPUT_TOKENS
from .http import post_json

log = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

SYSTEM_PROMPT = """You are a product sourcing analyst for the Lebanese consumer market.

You will receive raw search result text about products trending in the US.
Extract every specific physical product mentioned. Ignore categories,
services, apps, subscriptions, software, and anything that cannot be shipped
in a box.

Score each product 1-10 on how likely it is to sell in LEBANON. Lebanon is
not the US. Score against these realities:

PRICE. Buyers pay cash in USD. Above $25 retail is a hard sell. Under $15 is
the sweet spot. If it cannot be sourced cheap, it scores low no matter how
good it is.

ELECTRICITY. Mains power is unreliable and generator hours are limited.
Penalize constant mains power, long charge times, high wattage. Reward
battery, USB, solar, and fully manual products.

SHIPPING. It arrives by air cargo or Aramex. Penalize bulk, weight,
fragility, liquids, aerosols, and heavy lithium cells. If shipping costs more
than the product, it is dead.

VIDEO. It must be obvious in a 15-second Reel with no sound and no
explanation. Reward visible before/after and satisfying motion. Penalize
anything needing a paragraph to understand.

SATURATION. If it is already in every Instagram shop, Souk el Ahad, and Bourj
Hammoud, score it low. Novelty beats quality in this market.

RETURNS. There is no return culture. Penalize products that disappoint on
arrival: sizing, fit, fragile electronics, anything the photo oversells.

LOCAL FIT. Bonus for solving something specifically Lebanese: power cuts,
water storage, generator noise, small apartments, hot summers, long drives,
hosting guests.

Be skeptical. Most US trends fail in Lebanon. A score of 8 or above should be
rare — no more than two or three per run. If everything is scoring 7 or
higher, you are being too generous. Score again, harder.

Never invent sales figures or supplier prices. Price fields are your
estimates and should be treated as such.

Return ONLY a JSON array. No prose, no markdown fences, no preamble. Each
object:
{
  "product": "cleaned up product name",
  "score": <integer 1-10>,
  "reason": "<max 20 words, the deciding factor>",
  "est_retail_usd": <number>,
  "est_landed_usd": <number>,
  "risk": "<the single biggest reason it fails in Lebanon>",
  "source_url": "<url it came from, or null>"
}"""

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "product": {"type": "STRING"},
            "score": {"type": "INTEGER"},
            "reason": {"type": "STRING"},
            "est_retail_usd": {"type": "NUMBER"},
            "est_landed_usd": {"type": "NUMBER"},
            "risk": {"type": "STRING"},
            "source_url": {"type": "STRING", "nullable": True},
        },
        "required": [
            "product",
            "score",
            "reason",
            "est_retail_usd",
            "est_landed_usd",
            "risk",
        ],
    },
}


class EmptyResponse(RuntimeError):
    """Gemini returned no usable text, and we know why."""


def build_payload(blob: str) -> dict:
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": blob}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }


def extract_text(body: dict) -> Tuple[str, str]:
    """Pull the text out of a Gemini response. Returns (text, finish_reason)."""
    candidates = body.get("candidates") or []
    if candidates:
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(str(p.get("text") or "") for p in parts).strip()
        return text, str(candidate.get("finishReason") or "")
    return "", ""


def explain_empty(body: dict, finish_reason: str) -> str:
    blocked = (body.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        return f"blocked by the model provider ({blocked})"
    if finish_reason == "MAX_TOKENS":
        return "cut off before finishing — raise MAX_OUTPUT_TOKENS"
    if finish_reason:
        return f"stopped early ({finish_reason})"
    return "empty response"


def score_blob(session: requests.Session, blob: str, api_key: str, model: str) -> str:
    """Send the blob for scoring and return the raw JSON text of the reply."""
    url = f"{API_ROOT}/{model}:generateContent"
    body = post_json(
        session,
        url,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        payload=build_payload(blob),
    )
    text, finish_reason = extract_text(body)
    if not text:
        raise EmptyResponse(explain_empty(body, finish_reason))
    log.info("model returned %d chars (finishReason=%s)", len(text), finish_reason)
    return text
