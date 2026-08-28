"""Cross-run memory of products already reported.

Stored as a JSON file the workflow commits back to the repo. That gives you git
history of every product ever seen, and it doubles as repo activity, which stops
GitHub disabling the schedule on a repo that gets no commits for 60 days.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple

from .config import MAX_MEMORY, RETENTION_DAYS
from .products import Product

log = logging.getLogger(__name__)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def tokenize(name: str) -> Set[str]:
    """Reduce a product name to its set of significant words.

    Case, punctuation and word order are all noise: "LED Strip Lights",
    "led strip lights" and "Strip Lights LED" are one product.
    """
    words = _NON_ALNUM.sub(" ", str(name or "").lower()).split()
    return {w for w in words if len(w) > 1}


def same_product(a: Set[str], b: Set[str]) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # "Portable cooling neck fan" is the same thing as a previously seen
    # "Neck fan". The two-word floor stops a one-word name like "Fan" from
    # swallowing every fan-adjacent product.
    if len(a) >= 2 and len(b) >= 2 and (a <= b or b <= a):
        return True
    return False


@dataclass
class Entry:
    product: str
    score: int
    first_seen: str
    last_seen: str
    times_seen: int = 1

    def to_dict(self) -> Dict:
        return {
            "product": self.product,
            "score": self.score,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "times_seen": self.times_seen,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Entry":
        return cls(
            product=str(data.get("product") or ""),
            score=int(data.get("score") or 0),
            first_seen=str(data.get("first_seen") or ""),
            last_seen=str(data.get("last_seen") or data.get("first_seen") or ""),
            times_seen=int(data.get("times_seen") or 1),
        )


@dataclass
class Memory:
    path: Path
    entries: List[Entry] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Memory":
        if not path.exists():
            log.info("no memory file at %s, starting empty", path)
            return cls(path=path, entries=[])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            # A corrupt memory file must not stop the run. Worst case you get
            # products reported a second time.
            log.warning("could not read memory (%s), starting empty", error)
            return cls(path=path, entries=[])
        raw = data.get("seen") if isinstance(data, dict) else data
        entries = [Entry.from_dict(e) for e in (raw or []) if isinstance(e, dict)]
        return cls(path=path, entries=[e for e in entries if e.product])

    def expire(self, today: dt.date) -> None:
        cutoff = (today - dt.timedelta(days=RETENTION_DAYS)).isoformat()
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.first_seen >= cutoff]
        if before != len(self.entries):
            log.info("expired %d entries older than %s", before - len(self.entries), cutoff)

    def split(self, products: List[Product]) -> Tuple[List[Product], List[Product]]:
        """Partition into (never reported before, already reported)."""
        known = [(e, tokenize(e.product)) for e in self.entries]
        fresh: List[Product] = []
        repeats: List[Product] = []
        for product in products:
            tokens = tokenize(product.product)
            match = next((e for e, t in known if same_product(tokens, t)), None)
            if match is None:
                fresh.append(product)
                # Provisionally track it so two near-identical names inside one
                # run do not both count as new.
                placeholder = Entry(product.product, product.score, "", "", 0)
                known.append((placeholder, tokens))
            else:
                repeats.append(product)
        return fresh, repeats

    def remember(self, products: List[Product], today: dt.date) -> None:
        stamp = today.isoformat()
        for product in products:
            self.entries.append(
                Entry(
                    product=product.product,
                    score=product.score,
                    first_seen=stamp,
                    last_seen=stamp,
                )
            )
        if len(self.entries) > MAX_MEMORY:
            self.entries = self.entries[-MAX_MEMORY:]

    def touch(self, products: List[Product], today: dt.date) -> None:
        """Record that these products were seen again, without adding rows."""
        stamp = today.isoformat()
        known = [(e, tokenize(e.product)) for e in self.entries]
        for product in products:
            tokens = tokenize(product.product)
            for entry, entry_tokens in known:
                if same_product(tokens, entry_tokens):
                    entry.times_seen += 1
                    entry.last_seen = stamp
                    break

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "count": len(self.entries),
            "seen": [e.to_dict() for e in self.entries],
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log.info("saved %d entries to %s", len(self.entries), self.path)
