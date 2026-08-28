"""Covers the same ground the n8n build's JS suite did, plus the Python edges."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from scanner import memory as memory_mod
from scanner import scoring, sheets, telegram
from scanner.config import SCORE_THRESHOLD, TOP_N
from scanner.memory import Memory, same_product, tokenize
from scanner.products import ParseError, Product, parse_products, survivors
from scanner.queries import POOL_TEMPLATES, queries_for
from scanner.search import flatten

TODAY = dt.date(2026, 8, 27)

SAMPLE = [
    {"product": "Electric baby nail trimmer", "score": 8, "reason": "Battery powered, tiny, solves real parent fear, silent demo works", "est_retail_usd": 16, "est_landed_usd": 6, "risk": "Parents hesitate on electric devices near infants"},
    {"product": "Car multi-function storage bag", "score": 8, "reason": "No power needed, near-zero shipping weight, car-heavy country", "est_retail_usd": 13, "est_landed_usd": 4, "risk": "Low novelty, needs visual differentiation"},
    {"product": "Mini electric garlic chopper", "score": 7, "reason": "Lebanese cooking runs on garlic, demo is instantly satisfying", "est_retail_usd": 17, "est_landed_usd": 6, "risk": "Must be USB rechargeable, not mains powered"},
    {"product": "Cooling neck fan", "score": 7, "reason": "Battery power works during outages, heat runs through September", "est_retail_usd": 20, "est_landed_usd": 7, "risk": "Four week runway left, lithium customs friction"},
    {"product": "Pimple patches", "score": 7, "reason": "Weightless shipping, no power, visible before-after, repeat purchase", "est_retail_usd": 10, "est_landed_usd": 2, "risk": "Counterfeits everywhere, one bad batch ends reputation"},
    {"product": "Magnetic phone charger", "score": 6, "reason": "Power cuts make portable charging genuinely useful", "est_retail_usd": 22, "est_landed_usd": 9, "risk": "Saturated market, lithium cells expensive to air freight"},
]


def product(name: str, score: int = 7) -> Product:
    return Product(
        product=name, score=score, reason="r", est_retail_usd=9,
        est_landed_usd=3, risk="x", date=TODAY.isoformat(),
    )


# --------------------------------------------------------------- queries ----

def test_four_queries_per_run():
    assert len(queries_for(TODAY)) == 4


def test_month_and_year_interpolated():
    assert all("August 2026" in q for q in queries_for(TODAY)[:1])
    september = queries_for(dt.date(2026, 9, 14))
    assert any("September 2026" in q for q in september)


def test_no_month_hardcoded_in_templates():
    joined = " ".join(POOL_TEMPLATES)
    for month in ["January", "August", "December"]:
        assert month not in joined


def test_consecutive_days_never_repeat():
    days = [queries_for(TODAY + dt.timedelta(days=i)) for i in range(14)]
    for earlier, later in zip(days, days[1:]):
        assert earlier != later


def test_rotation_covers_whole_pool_within_pool_length():
    # Start early in the month so the window does not cross into September and
    # change the interpolated strings out from under the comparison.
    start = dt.date(2026, 8, 1)
    seen = set()
    for i in range(len(POOL_TEMPLATES)):
        seen.update(queries_for(start + dt.timedelta(days=i)))
    assert len(seen) == len(POOL_TEMPLATES)


def test_rotation_is_deterministic():
    assert queries_for(TODAY) == queries_for(TODAY)


def test_pool_size_stays_coprime_with_the_step():
    from math import gcd
    from scanner.config import ROTATION_STEP
    assert gcd(len(POOL_TEMPLATES), ROTATION_STEP) == 1


def test_original_spec_queries_survive():
    pool = set()
    for i in range(len(POOL_TEMPLATES)):
        pool.update(queries_for(TODAY + dt.timedelta(days=i)))
    assert "trending products August 2026 tiktok" in pool
    assert "tiktok shop trending products this week" in pool


# --------------------------------------------------------------- flatten ----

def serper(n: int, pad: str = "") -> dict:
    return {
        "organic": [
            {"title": f"Product {n}-{i}", "snippet": f"Snippet {n}-{i}. {pad}",
             "link": f"https://example.com/{n}/{i}"}
            for i in range(10)
        ]
    }


def test_flatten_merges_all_responses():
    blob = flatten([serper(1), serper(2), serper(3), serper(4)])
    assert blob.count("\n") == 39


def test_flatten_dedupes_across_queries():
    assert flatten([serper(1), serper(1)]).count("\n") == 9


def test_flatten_caps_at_12k_on_a_result_boundary():
    blob = flatten([serper(i, "x" * 4000) for i in range(4)])
    assert len(blob) <= 12_000
    assert blob.splitlines()[-1].endswith("]")


def test_flatten_survives_a_response_with_no_organic_key():
    assert flatten([{"message": "rate limited"}]) == ""


def test_flatten_skips_entries_with_neither_title_nor_snippet():
    assert flatten([{"organic": [{"link": "https://x.test"}]}]) == ""


# ----------------------------------------------------------------- parse ----

def test_parses_the_sample_data():
    products = parse_products(json.dumps(SAMPLE), TODAY)
    assert len(products) == 6
    assert all(p.date == "2026-08-27" for p in products)


def test_sorted_descending_by_score():
    scores = [p.score for p in parse_products(json.dumps(SAMPLE), TODAY)]
    assert scores == sorted(scores, reverse=True)


def test_threshold_filter():
    mixed = SAMPLE + [{"product": "Espresso machine", "score": 2, "reason": "heavy",
                       "est_retail_usd": 120, "est_landed_usd": 60, "risk": "shipping"}]
    products = parse_products(json.dumps(mixed), TODAY)
    assert len(products) == 7
    assert len(survivors(products)) == 6
    assert all(p.score >= SCORE_THRESHOLD for p in survivors(products))


@pytest.mark.parametrize("wrapper", [
    "```json\n{body}\n```",
    "```\n{body}\n```",
    "Here you go:\n{body}",
    "{body}",
])
def test_recovers_from_fences_and_preamble(wrapper):
    raw = wrapper.format(body=json.dumps(SAMPLE))
    assert len(parse_products(raw, TODAY)) == 6


def test_unparseable_reply_raises_parse_error():
    with pytest.raises(ParseError):
        parse_products("I could not find any products, sorry.", TODAY)


def test_non_array_json_raises_parse_error():
    with pytest.raises(ParseError):
        parse_products('{"product": "just one"}', TODAY)


def test_empty_array_yields_no_products():
    assert parse_products("[]", TODAY) == []


def test_non_numeric_score_does_not_crash_and_fails_the_filter():
    raw = json.dumps([{"product": "Weird", "score": "high", "reason": "x",
                       "est_retail_usd": "n/a", "est_landed_usd": None, "risk": "y"}])
    products = parse_products(raw, TODAY)
    assert products[0].score == 0
    assert not products[0].passed
    assert products[0].est_retail_usd is None


def test_score_is_clamped_to_ten():
    raw = json.dumps([{"product": "Hype", "score": 99, "reason": "x",
                       "est_retail_usd": 1, "est_landed_usd": 1, "risk": "y"}])
    assert parse_products(raw, TODAY)[0].score == 10


def test_entries_without_a_name_are_dropped():
    raw = json.dumps([{"product": "  ", "score": 9, "reason": "x",
                       "est_retail_usd": 1, "est_landed_usd": 1, "risk": "y"}])
    assert parse_products(raw, TODAY) == []


def test_row_matches_the_seven_spec_columns():
    row = parse_products(json.dumps(SAMPLE), TODAY)[0].as_row()
    assert row == ["2026-08-27", "Electric baby nail trimmer", 8, 16.0, 6.0,
                   "Battery powered, tiny, solves real parent fear, silent demo works",
                   "Parents hesitate on electric devices near infants"]
    assert len(row) == len(sheets.HEADERS)


# ---------------------------------------------------------------- gemini ----

def gemini_reply(text: str, finish: str = "STOP") -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}


def test_extract_text_from_gemini():
    text, finish = scoring.extract_text(gemini_reply("hello"))
    assert (text, finish) == ("hello", "STOP")


def test_extract_joins_multipart_responses():
    body = {"candidates": [{"content": {"parts": [{"text": "ab"}, {"text": "cd"}]}}]}
    assert scoring.extract_text(body)[0] == "abcd"


def test_extract_handles_an_empty_body():
    assert scoring.extract_text({}) == ("", "")


@pytest.mark.parametrize("body,finish,expected", [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "", "SAFETY"),
    ({}, "MAX_TOKENS", "MAX_OUTPUT_TOKENS"),
    ({}, "RECITATION", "RECITATION"),
    ({}, "", "empty response"),
])
def test_empty_responses_explain_themselves(body, finish, expected):
    assert expected in scoring.explain_empty(body, finish)


def test_payload_is_json_serialisable_and_carries_the_prompt():
    payload = scoring.build_payload('a "quoted" blob\nwith\tcontrol chars')
    round_tripped = json.loads(json.dumps(payload))
    assert round_tripped["contents"][0]["parts"][0]["text"].startswith('a "quoted"')
    assert round_tripped["generationConfig"]["responseMimeType"] == "application/json"
    assert round_tripped["systemInstruction"]["parts"][0]["text"].startswith(
        "You are a product sourcing analyst")


def test_system_prompt_keeps_the_specs_scoring_criteria():
    for heading in ["PRICE.", "ELECTRICITY.", "SHIPPING.", "VIDEO.",
                    "SATURATION.", "RETURNS.", "LOCAL FIT."]:
        assert heading in scoring.SYSTEM_PROMPT
    assert "Score again, harder." in scoring.SYSTEM_PROMPT


def test_response_schema_pins_every_field_the_sheet_needs():
    props = scoring.RESPONSE_SCHEMA["items"]["properties"]
    for field in ["product", "score", "reason", "est_retail_usd",
                  "est_landed_usd", "risk", "source_url"]:
        assert field in props
    assert props["score"]["type"] == "INTEGER"
    assert props["source_url"]["nullable"] is True
    assert "source_url" not in scoring.RESPONSE_SCHEMA["items"]["required"]


# -------------------------------------------------------------- telegram ----

def test_message_matches_the_spec_format():
    products = parse_products(json.dumps(SAMPLE), TODAY)
    message = telegram.build_message(products, TODAY.isoformat())
    assert message.startswith("*Product scan — 2026-08-27*")
    assert "*1. Electric baby nail trimmer — 8/10*" in message
    assert "Retail ~$16 | Landed ~$6" in message
    assert "Risk: Parents hesitate on electric devices near infants" in message
    assert message.endswith("_Prices are estimates. Verify on AliExpress before buying._")


def test_message_caps_at_top_n():
    products = parse_products(json.dumps(SAMPLE), TODAY)
    message = telegram.build_message(products, TODAY.isoformat())
    assert sum(1 for line in message.splitlines() if line.startswith("*") and "/10*" in line) == TOP_N
    assert "Magnetic phone charger" not in message


def test_message_stays_within_telegram_limits():
    many = [product(f"Widget number {i} alpha", 9) for i in range(50)]
    message = telegram.build_message(many, TODAY.isoformat())
    assert len(message) <= telegram.MAX_MESSAGE_CHARS


def test_markdown_breaking_names_are_neutralised():
    p = product("LED_strip *pro* [2026]", 9)
    message = telegram.build_message([p], TODAY.isoformat())
    assert "LED_strip" not in message
    assert "[2026]" not in message
    assert message.count("*") % 2 == 0
    # the sheet keeps the original
    assert p.as_row()[1] == "LED_strip *pro* [2026]"


def test_no_survivors_sends_the_specs_sentence():
    assert telegram.build_message([product("Junk", 2)], TODAY.isoformat()) == (
        "Scan complete. Nothing cleared the bar today.")


def test_missing_prices_render_without_crashing():
    p = Product("Mystery", 9, "r", None, None, "x", date=TODAY.isoformat())
    assert "Retail ~$? | Landed ~$?" in telegram.build_message([p], TODAY.isoformat())


def test_nothing_new_message_pluralises():
    assert "1 product scored" in telegram.nothing_new_message(1)
    assert "6 products scored" in telegram.nothing_new_message(6)


def test_send_refuses_an_empty_message():
    with pytest.raises(ValueError):
        telegram.send(None, "t", "c", "   ")


# ---------------------------------------------------------------- memory ----

def test_tokenize_ignores_case_punctuation_and_short_words():
    assert tokenize("LED Strip-Lights!") == {"led", "strip", "lights"}


@pytest.mark.parametrize("a,b", [
    ("LED Strip Lights", "led strip lights"),
    ("LED Strip Lights", "Strip Lights LED"),
    ("LED Strip Lights", "LED  Strip-Lights"),
    ("Neck fan", "Portable cooling neck fan"),
])
def test_matcher_treats_variants_as_one_product(a, b):
    assert same_product(tokenize(a), tokenize(b))


@pytest.mark.parametrize("a,b", [
    ("Pimple patches", "Acne patches"),
    ("Cooling neck fan", "Car storage bag"),
    ("Fan", "Cooling neck fan"),
    ("Fan", "Fanny pack"),
])
def test_matcher_keeps_different_products_apart(a, b):
    assert not same_product(tokenize(a), tokenize(b))


def test_matcher_ignores_empty_names():
    assert not same_product(set(), tokenize("Anything"))


def make_memory(tmp_path):
    return Memory.load(tmp_path / "seen.json")


def test_first_run_reports_everything(tmp_path):
    mem = make_memory(tmp_path)
    products = parse_products(json.dumps(SAMPLE), TODAY)
    fresh, repeats = mem.split(products)
    assert len(fresh) == 6 and repeats == []


def test_second_identical_run_reports_nothing(tmp_path):
    mem = make_memory(tmp_path)
    products = parse_products(json.dumps(SAMPLE), TODAY)
    fresh, _ = mem.split(products)
    mem.remember(fresh, TODAY)
    mem.save()

    reloaded = Memory.load(tmp_path / "seen.json")
    fresh2, repeats2 = reloaded.split(parse_products(json.dumps(SAMPLE), TODAY))
    assert fresh2 == []
    assert len(repeats2) == 6


def test_only_genuinely_new_products_are_reported(tmp_path):
    mem = make_memory(tmp_path)
    mem.remember(parse_products(json.dumps(SAMPLE), TODAY), TODAY)
    extended = SAMPLE + [{"product": "Solar camping lantern", "score": 9,
                          "reason": "Runs through outages", "est_retail_usd": 14,
                          "est_landed_usd": 5, "risk": "Fragile panel"}]
    fresh, repeats = mem.split(parse_products(json.dumps(extended), TODAY))
    assert [p.product for p in fresh] == ["Solar camping lantern"]
    assert len(repeats) == 6


def test_near_duplicates_within_one_run_count_once(tmp_path):
    mem = make_memory(tmp_path)
    raw = json.dumps([
        {"product": "Cooling neck fan", "score": 8, "reason": "x", "est_retail_usd": 1, "est_landed_usd": 1, "risk": "y"},
        {"product": "Portable cooling neck fan", "score": 7, "reason": "x", "est_retail_usd": 1, "est_landed_usd": 1, "risk": "y"},
    ])
    fresh, repeats = mem.split(parse_products(raw, TODAY))
    assert len(fresh) == 1
    assert len(repeats) == 1


def test_touch_bumps_the_counter_without_adding_rows(tmp_path):
    mem = make_memory(tmp_path)
    products = parse_products(json.dumps(SAMPLE), TODAY)
    mem.remember(products, TODAY)
    later = TODAY + dt.timedelta(days=1)
    mem.touch(products, later)
    assert len(mem.entries) == 6
    assert all(e.times_seen == 2 for e in mem.entries)
    assert all(e.last_seen == later.isoformat() for e in mem.entries)


def test_memory_expires_after_the_retention_window(tmp_path):
    mem = make_memory(tmp_path)
    mem.remember([product("Cooling neck fan")], dt.date(2026, 1, 1))
    mem.expire(TODAY)
    assert mem.entries == []
    fresh, _ = mem.split([product("Cooling neck fan")])
    assert len(fresh) == 1


def test_recent_entries_survive_expiry(tmp_path):
    mem = make_memory(tmp_path)
    mem.remember([product("Cooling neck fan")], TODAY - dt.timedelta(days=5))
    mem.expire(TODAY)
    assert len(mem.entries) == 1


def test_memory_is_capped(tmp_path):
    mem = make_memory(tmp_path)
    for batch in range(7):
        mem.remember([product(f"Widget model {batch}{i} alpha") for i in range(100)], TODAY)
    assert len(mem.entries) <= memory_mod.MAX_MEMORY


def test_memory_round_trips_through_disk(tmp_path):
    mem = make_memory(tmp_path)
    mem.remember([product("Solar lantern", 9)], TODAY)
    mem.save()
    reloaded = Memory.load(tmp_path / "seen.json")
    assert len(reloaded.entries) == 1
    assert reloaded.entries[0].product == "Solar lantern"
    assert reloaded.entries[0].times_seen == 1


def test_saved_file_is_readable_json_with_a_timestamp(tmp_path):
    mem = make_memory(tmp_path)
    mem.remember([product("Solar lantern")], TODAY)
    mem.save()
    data = json.loads((tmp_path / "seen.json").read_text())
    assert data["count"] == 1
    assert "updated" in data
    assert data["seen"][0]["product"] == "Solar lantern"


def test_corrupt_memory_file_does_not_stop_the_run(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{not json at all")
    mem = Memory.load(path)
    assert mem.entries == []


def test_missing_memory_file_starts_empty(tmp_path):
    assert Memory.load(tmp_path / "nope.json").entries == []


# ---------------------------------------------------------------- sheets ----

def test_sheet_headers_match_the_spec_columns():
    assert sheets.HEADERS == ["date", "product", "score", "est_retail_usd",
                              "est_landed_usd", "reason", "risk"]


def test_bad_service_account_json_gives_a_useful_error():
    with pytest.raises(sheets.SheetsError, match="not valid JSON"):
        sheets._credentials("this is not json")


def test_append_rows_is_a_noop_with_nothing_to_write():
    assert sheets.append_rows(None, [], sheet_id="x", tab="Sheet1", service_account_json="{}") == 0
