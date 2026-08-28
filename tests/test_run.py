"""End-to-end runs with the network stubbed out.

These cover the orchestration in __main__ — the ordering and failure behaviour
the unit tests cannot see.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from scanner import __main__ as cli
from scanner import sheets, telegram
from scanner.scoring import EmptyResponse

TODAY = "2026-08-27"

ENV = {
    "SERPER_API_KEY": "serper",
    "GEMINI_API_KEY": "gemini",
    "TELEGRAM_BOT_TOKEN": "token",
    "TELEGRAM_CHAT_ID": "123",
    "SHEET_ID": "sheet",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Stub every outbound call and record what the run tried to do."""
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)

    sent: list[str] = []
    appended: list[list] = []

    monkeypatch.setattr(telegram, "send", lambda s, t, c, text: sent.append(text))
    monkeypatch.setattr(cli.telegram, "send", lambda s, t, c, text: sent.append(text))
    monkeypatch.setattr(sheets, "ensure_headers", lambda *a, **k: False)
    monkeypatch.setattr(cli.sheets, "ensure_headers", lambda *a, **k: False)

    def fake_append(session, rows, **kwargs):
        appended.extend(rows)
        return len(rows)

    monkeypatch.setattr(sheets, "append_rows", fake_append)
    monkeypatch.setattr(cli.sheets, "append_rows", fake_append)

    return {
        "sent": sent,
        "appended": appended,
        "state": tmp_path / "seen.json",
    }


def run(wired, *extra):
    return cli.main(["--sample", "--date", TODAY, "--state", str(wired["state"]), *extra])


def test_first_run_sends_shortlist_and_logs_every_product(wired):
    assert run(wired) == 0
    assert len(wired["sent"]) == 1
    message = wired["sent"][0]
    assert message.startswith("*Product scan — 2026-08-27*")
    # top 5 in the message, all 6 in the sheet
    assert message.count("/10*") == 5
    assert len(wired["appended"]) == 6
    assert wired["state"].exists()


def test_second_identical_run_reports_nothing_new_and_writes_no_rows(wired):
    run(wired)
    wired["sent"].clear()
    wired["appended"].clear()

    assert run(wired) == 0
    assert wired["sent"] == [
        "Scan complete. 6 products scored, all already reported. Nothing new today."
    ]
    assert wired["appended"] == []


def test_third_run_reports_only_the_new_product(wired, monkeypatch):
    run(wired)
    wired["sent"].clear()
    wired["appended"].clear()

    extended = json.loads(cli.SAMPLE_REPLY) + [{
        "product": "Solar camping lantern", "score": 9,
        "reason": "Runs through outages, no mains",
        "est_retail_usd": 14, "est_landed_usd": 5, "risk": "Fragile panel",
    }]
    monkeypatch.setattr(cli, "SAMPLE_REPLY", json.dumps(extended))

    assert run(wired) == 0
    assert "Solar camping lantern" in wired["sent"][0]
    assert "Pimple patches" not in wired["sent"][0]
    assert [row[1] for row in wired["appended"]] == ["Solar camping lantern"]


def test_repeat_sightings_bump_the_counter(wired):
    run(wired)
    run(wired)
    data = json.loads(wired["state"].read_text())
    assert data["count"] == 6
    assert all(e["times_seen"] == 2 for e in data["seen"])


def test_dry_run_touches_nothing(wired, capsys):
    assert run(wired, "--dry-run") == 0
    assert wired["sent"] == []
    assert wired["appended"] == []
    assert not wired["state"].exists()
    assert "Product scan" in capsys.readouterr().out


def test_a_failed_telegram_send_does_not_record_the_products(wired, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("telegram 500")

    monkeypatch.setattr(cli.telegram, "send", boom)
    assert run(wired) == 1
    # nothing recorded, so tomorrow reports them again rather than losing them
    assert not wired["state"].exists()


def test_a_failed_sheet_append_does_not_record_the_products(wired, monkeypatch):
    def boom(*args, **kwargs):
        raise sheets.SheetsError("sheets 403")

    monkeypatch.setattr(cli.sheets, "append_rows", boom)
    assert run(wired) == 1
    assert not wired["state"].exists()


def test_an_unparseable_reply_still_notifies(wired, monkeypatch):
    monkeypatch.setattr(cli, "SAMPLE_REPLY", "I could not find any products.")
    assert run(wired) == 1
    assert "could not be parsed" in wired["sent"][0]
    assert wired["appended"] == []


def test_an_empty_array_sends_the_specs_sentence(wired, monkeypatch):
    monkeypatch.setattr(cli, "SAMPLE_REPLY", "[]")
    assert run(wired) == 0
    assert wired["sent"] == ["Scan complete. Nothing cleared the bar today."]
    assert wired["appended"] == []


def test_all_low_scores_still_log_rejects_but_send_no_shortlist(wired, monkeypatch):
    monkeypatch.setattr(cli, "SAMPLE_REPLY", json.dumps([
        {"product": "Espresso machine", "score": 2, "reason": "heavy",
         "est_retail_usd": 120, "est_landed_usd": 60, "risk": "shipping"},
    ]))
    assert run(wired) == 0
    assert wired["sent"] == ["Scan complete. Nothing cleared the bar today."]
    # the reject is still training data, so it reaches the sheet
    assert [row[1] for row in wired["appended"]] == ["Espresso machine"]


def test_an_empty_model_response_notifies_with_the_reason(wired, monkeypatch):
    def boom(*args, **kwargs):
        raise EmptyResponse("blocked by the model provider (SAFETY)")

    monkeypatch.setattr(cli, "score_blob", boom)
    monkeypatch.setattr(cli, "queries_for", lambda d: ["q"])
    monkeypatch.setattr(cli, "search_all", lambda s, q, k: [{}])
    monkeypatch.setattr(cli, "flatten", lambda r: "some blob")

    code = cli.main(["--date", TODAY, "--state", str(wired["state"])])
    assert code == 1
    assert "SAFETY" in wired["sent"][0]


def test_missing_configuration_exits_two_without_sending(monkeypatch, tmp_path):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["--sample", "--state", str(tmp_path / "s.json")]) == 2


def test_sample_plus_dry_run_needs_no_configuration_at_all(monkeypatch, tmp_path, capsys):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["--sample", "--dry-run", "--date", TODAY,
                     "--state", str(tmp_path / "s.json")]) == 0
    assert "Product scan" in capsys.readouterr().out


def test_date_override_drives_query_rotation():
    args = cli.parse_args(["--date", "2026-09-14"])
    assert args.date == dt.date(2026, 9, 14)
