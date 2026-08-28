"""Entry point: python -m scanner

Run order matches the original n8n flow:
  queries -> search -> flatten -> score -> parse -> drop repeats -> notify -> log

Memory is saved LAST, only after Telegram and Sheets both succeed. If a send
fails, the products stay unseen and get reported again next run. That is the
safe direction to fail in: you may see a repeat, you never lose a product.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

from .config import Config, ConfigError
from .http import build_session
from .memory import Memory
from .products import ParseError, Product, parse_products
from .queries import queries_for
from .scoring import EmptyResponse, score_blob
from .search import flatten, search_all
from . import sheets, telegram

log = logging.getLogger("scanner")

DEFAULT_STATE = Path("state/seen.json")

# The spec's section 8 sample, for exercising everything downstream of the model
# without spending an API call.
SAMPLE_REPLY = json.dumps(
    [
        {"product": "Electric baby nail trimmer", "score": 8, "reason": "Battery powered, tiny, solves real parent fear, silent demo works", "est_retail_usd": 16, "est_landed_usd": 6, "risk": "Parents hesitate on electric devices near infants"},
        {"product": "Car multi-function storage bag", "score": 8, "reason": "No power needed, near-zero shipping weight, car-heavy country", "est_retail_usd": 13, "est_landed_usd": 4, "risk": "Low novelty, needs visual differentiation"},
        {"product": "Mini electric garlic chopper", "score": 7, "reason": "Lebanese cooking runs on garlic, demo is instantly satisfying", "est_retail_usd": 17, "est_landed_usd": 6, "risk": "Must be USB rechargeable, not mains powered"},
        {"product": "Cooling neck fan", "score": 7, "reason": "Battery power works during outages, heat runs through September", "est_retail_usd": 20, "est_landed_usd": 7, "risk": "Four week runway left, lithium customs friction"},
        {"product": "Pimple patches", "score": 7, "reason": "Weightless shipping, no power, visible before-after, repeat purchase", "est_retail_usd": 10, "est_landed_usd": 2, "risk": "Counterfeits everywhere, one bad batch ends reputation"},
        {"product": "Magnetic phone charger", "score": 6, "reason": "Power cuts make portable charging genuinely useful", "est_retail_usd": 22, "est_landed_usd": 9, "risk": "Saturated market, lithium cells expensive to air freight"},
    ]
)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scanner",
        description="Score US trending products for the Lebanese market.",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="use the built-in sample reply instead of calling Serper and Gemini",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be sent; do not touch Telegram, Sheets or memory",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE,
        help=f"path to the memory file (default: {DEFAULT_STATE})",
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="override today's date, as YYYY-MM-DD (for testing rotation)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def _notify(session, cfg, text: str, dry_run: bool) -> None:
    if dry_run:
        print("--- telegram ---")
        print(text)
        print("--- end ---")
        return
    telegram.send(session, cfg.telegram_token, cfg.telegram_chat_id, text)


def run(args: argparse.Namespace) -> int:
    today = args.date or dt.datetime.now(dt.timezone.utc).date()
    session = build_session()

    # --sample still needs config for the send step, unless we are also dry.
    cfg = None
    if not (args.sample and args.dry_run):
        cfg = Config.from_env()

    # 1-4. search and flatten, or skip straight to the canned reply
    if args.sample:
        log.info("using built-in sample data, no API calls")
        raw = SAMPLE_REPLY
    else:
        queries = queries_for(today)
        log.info("queries for %s: %s", today, queries)
        responses = search_all(session, queries, cfg.serper_api_key)
        blob = flatten(responses)
        if not blob:
            _notify(session, cfg, telegram.failure_message("no search results"), args.dry_run)
            return 0
        # 5. score
        try:
            raw = score_blob(session, blob, cfg.gemini_api_key, cfg.gemini_model)
        except EmptyResponse as error:
            _notify(session, cfg, telegram.failure_message(str(error)), args.dry_run)
            return 1

    # 6. parse
    try:
        products = parse_products(raw, today)
    except ParseError as error:
        text = f"Scan complete. Model response could not be parsed. Raw: {telegram.clean(raw)[:400]}"
        log.error("parse failed: %s", error)
        _notify(session, cfg, text, args.dry_run)
        return 1

    if not products:
        _notify(session, cfg, "Scan complete. Nothing cleared the bar today.", args.dry_run)
        return 0

    # 7. drop anything already reported
    memory = Memory.load(args.state)
    memory.expire(today)
    fresh, repeats = memory.split(products)
    log.info("%d scored, %d new, %d repeats", len(products), len(fresh), len(repeats))

    if not fresh:
        memory.touch(repeats, today)
        _notify(session, cfg, telegram.nothing_new_message(len(products)), args.dry_run)
        if not args.dry_run:
            memory.save()
        return 0

    # 8. tell him, 9. log every new product including the rejects
    message = telegram.build_message(fresh, today.isoformat())
    _notify(session, cfg, message, args.dry_run)

    if args.dry_run:
        print(f"--- {len(fresh)} sheet rows would be appended ---")
        for product in fresh:
            print("   ", product.as_row())
        return 0

    sheets.ensure_headers(
        session,
        sheet_id=cfg.sheet_id,
        tab=cfg.sheet_tab,
        service_account_json=cfg.service_account_json,
    )
    sheets.append_rows(
        session,
        sheets.rows_from(fresh),
        sheet_id=cfg.sheet_id,
        tab=cfg.sheet_tab,
        service_account_json=cfg.service_account_json,
    )

    # Saved only now that everything else has succeeded.
    memory.touch(repeats, today)
    memory.remember(fresh, today)
    memory.save()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return run(args)
    except ConfigError as error:
        log.error("%s", error)
        return 2
    except Exception as error:  # noqa: BLE001 - top level guard, logged and surfaced
        log.exception("run failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
