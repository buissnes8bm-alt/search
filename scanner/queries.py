"""Search queries, rotated daily.

Running the same four strings every day returns the same Google results all
month. The pool size (12) and the daily step (5) are coprime, so the starting
offset visits every position in the pool before repeating and no two consecutive
days run the same four queries.
"""

from __future__ import annotations

import datetime as dt
from typing import List

from .config import QUERIES_PER_RUN, ROTATION_STEP

# Keep this coprime with ROTATION_STEP (i.e. avoid multiples of 5) or the
# rotation will only ever visit some of the pool.
POOL_TEMPLATES = [
    # the original four
    "trending products {month} {year} tiktok",
    "viral products {month} {year} amazon",
    "best selling dropshipping products {month} {year}",
    "tiktok shop trending products this week",
    # different sources, to get off the same listicles every day
    "tiktok made me buy it {month} {year}",
    "amazon movers and shakers {month} {year}",
    "viral gadgets {month} {year} reddit",
    "winning products to sell online {month} {year}",
    # category sweeps, which surface products the general queries never reach
    "trending kitchen gadgets {month} {year}",
    "trending car accessories {month} {year}",
    "viral home organization products {month} {year}",
    "best selling beauty tools {month} {year}",
]


def build_pool(today: dt.date) -> List[str]:
    """The full pool with the current month and year filled in."""
    month = today.strftime("%B")
    year = str(today.year)
    return [t.format(month=month, year=year) for t in POOL_TEMPLATES]


def queries_for(today: dt.date) -> List[str]:
    """The four queries this day's run should use."""
    pool = build_pool(today)
    start = (today.toordinal() * ROTATION_STEP) % len(pool)
    return [pool[(start + i) % len(pool)] for i in range(QUERIES_PER_RUN)]
