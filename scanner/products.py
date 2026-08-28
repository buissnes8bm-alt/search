"""Parsing the model's reply into products, and filtering them."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .config import SCORE_THRESHOLD

log = logging.getLogger(__name__)

_FENCE_OPEN = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"```\s*$")


class ParseError(ValueError):
    """The reply could not be read as a JSON array of products."""


@dataclass
class Product:
    product: str
    score: int
    reason: str
    est_retail_usd: Optional[float]
    est_landed_usd: Optional[float]
    risk: str
    source_url: Optional[str] = None
    date: str = field(default="")

    @property
    def passed(self) -> bool:
        return self.score >= SCORE_THRESHOLD

    def as_row(self) -> List[Any]:
        """The seven sheet columns, in the spec's order."""
        return [
            self.date,
            self.product,
            self.score,
            "" if self.est_retail_usd is None else self.est_retail_usd,
            "" if self.est_landed_usd is None else self.est_landed_usd,
            self.reason,
            self.risk,
        ]


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return round(result, 2)


def _score(value: Any) -> int:
    try:
        return max(0, min(10, round(float(value))))
    except (TypeError, ValueError):
        # A non-numeric score fails the threshold rather than crashing the run.
        return 0


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    text = _FENCE_OPEN.sub("", text)
    text = _FENCE_CLOSE.sub("", text)
    return text.strip()


def _slice_array(text: str) -> Optional[str]:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + 1]


def parse_products(raw: str, today: dt.date) -> List[Product]:
    """Parse the reply into products, sorted by score descending.

    Structured output means fences and preambles should never appear. They are
    still handled, because the fallback costs three lines and a silent failure
    on a day the model ignores the schema costs a run.
    """
    text = strip_fences(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as first_error:
        recovered = _slice_array(text)
        if recovered is None:
            raise ParseError(str(first_error)) from first_error
        try:
            data = json.loads(recovered)
        except json.JSONDecodeError as second_error:
            raise ParseError(str(second_error)) from second_error

    if not isinstance(data, list):
        raise ParseError("expected a JSON array")

    stamp = today.isoformat()
    products: List[Product] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("product"))
        if not name:
            continue
        products.append(
            Product(
                product=name,
                score=_score(entry.get("score")),
                reason=_text(entry.get("reason")),
                est_retail_usd=_number(entry.get("est_retail_usd")),
                est_landed_usd=_number(entry.get("est_landed_usd")),
                risk=_text(entry.get("risk")),
                source_url=entry.get("source_url") or None,
                date=stamp,
            )
        )

    products.sort(key=lambda p: p.score, reverse=True)
    log.info("parsed %d products", len(products))
    return products


def survivors(products: List[Product]) -> List[Product]:
    return [p for p in products if p.passed]
