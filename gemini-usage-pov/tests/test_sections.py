"""Every dashboard section, on synthetic API-shaped records (tests/synthetic_extra.py). No network."""
import json
import socket
from pathlib import Path

import pandas as pd
import pytest

from src import pipeline, report
from src.common import Pseudonymiser
from src.config import DEFAULTS, LICENSING, USAGE, ConfigError, scopes_for, validate_scopes
from src.sections_gemini import classify
from tests.synthetic_extra import build_extra

FIXTURE = Path(__file__).parent / "fixtures" / "sample_cache.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def guard(*a, **k):
        raise RuntimeError("tests must not touch the network")
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket.socket, "connect", guard)


def make_cfg(**modules):
    mods = {"activity_logs": True, "usage_reports": True, "licensing": True, "chrome": True, **modules}
    cfg = {**DEFAULTS, "modules": mods, "activity_window_days": 28, "min_group_size": 2}
    cfg["scopes"] = scopes_for(mods)
    return cfg


@pytest.fixture(scope="module")
def cache():
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def result(cache):
    return pipeline.build(make_cfg(), cache["activities"], cache["users"], cache["start_time"],
                          cache["end_time"], build_extra())


def tiles(sec):
    return {a: b for a, b, _ in sec.tiles}


def table(sec, title):
    return next(t["df"] for t in sec.tables if t["title"] == title)


def test_all_sections_build(result):
    secs = result["sections"]
    for key in ("coverage", "gemini_use_cases", "gemini_time", "groups", "activity_overview", "usage", "drive",
                "meet", "chat", "calendar", "classroom", "login", "token", "external_ai", "licences",
                "chrome_mgmt", "chrome_audit", "keep", "workspace_studio", "context"):
        assert secs[key].status == "ok", (key, secs[key].reason)


def test_use_case_classification():
    assert classify("summarize", "side_panel", "docs") == "Summarising and understanding"
    assert classify("classic_use_case_generate_rubric", "classroom_gemini_education", "classroom") == "Assessment and feedback"
    assert classify("generate_text", "help_me_write", "slides") == "Building presentations"
    assert classify("generate_text", "help_me_write", "gmail") == "Writing and communication"
    assert classify("", "workflows_execution", "workflows") == "Automation and agents"
    assert classify("mystery", "mystery", "mystery") == "Unclassified"
    assert classify("mystery", "", "x", {"mystery": "Meetings"}) == "Meetings"


def test_gemini_sections(result):
    uc = result["sections"]["gemini_use_cases"]
    adoption = table(uc, "Use-case adoption")
    assert adoption["actions"].sum() == 58
    assert len(adoption) == 14
    assert result["sections"]["gemini_time"].heatmaps[0]["grid"].values.sum() == 58


def test_groups_hide_small(cache):
    r = pipeline.build({**make_cfg(), "min_group_size": 3}, cache["activities"], cache["users"],
                       cache["start_time"], cache["end_time"], build_extra())
    by_ou = table(r["sections"]["groups"], "By ou path")
    small = by_ou[by_ou["users"] < 3]
    assert (small["adoption_pct"] == "hidden").all() and len(small)


def test_drive(result):
    d = result["sections"]["drive"]
    per = table(d, "Per user")
    assert per["created"].sum() == 14
    ext = table(d, "External or public sharing")
    assert len(ext) == 2 and "partner@outside.org" in set(ext["target"])
    reuse = table(d, "Resource reuse: most-copied files")
    assert reuse.iloc[0]["source"] == "Lesson Template" and reuse.iloc[0]["copies"] == 3


def test_meet(result):
    m = result["sections"]["meet"]
    t = tiles(m)
    assert t["Meetings"] == "6"
    assert t["Meetings with external guests"] == "1"
    feats = table(m, "Interactive features (polls, Q&A, hand raise, …)").set_index("feature")
    assert feats.loc["poll_created", "events"] == 1


def test_classroom(result):
    c = result["sections"]["classroom"]
    t = tiles(c)
    assert t["Submissions"] == "15"
    assert t["Late submission rate"] == "20.0%"
    assert t["Median grading turnaround"] == "24.0 h"
    assert t["Active teachers (30d)"] == "3"


def test_login_token_licences_usage_chrome(result):
    s = result["sections"]
    assert tiles(s["login"])["Suspicious sign-in events"] == "1"
    ai = table(s["token"], "AI tools connected to Google accounts")
    assert set(ai["app_name"]) == {"ChatGPT", "Claude"}
    lt = tiles(s["licences"])
    assert lt["Paid AI seats with little use"] == "2"
    act = table(s["usage"], "Active users by app (latest day)")
    assert list(act.columns) == ["app", "1-day", "7-day", "30-day"]
    assert tiles(s["chrome_mgmt"])["AI extensions installed"] == "2"


def test_coverage_statuses(result):
    cov = table(result["sections"]["coverage"], "Metric coverage")
    assert cov["status"].str.startswith("✅").sum() >= 50
    reactions = cov[cov["metric"] == "Reactions"].iloc[0]
    assert reactions["status"].startswith("❌")


def test_disabled_and_blocked_sources(cache):
    extra = build_extra()
    extra["status"]["classroom"] = "unavailable: HTTP 403: Classroom is not enabled"
    cfg = make_cfg(usage_reports=False, licensing=False, chrome=False)
    r = pipeline.build(cfg, cache["activities"], cache["users"], cache["start_time"], cache["end_time"], extra)
    s = r["sections"]
    assert s["classroom"].status == "unavailable" and "403" in s["classroom"].reason
    assert s["usage"].status == "disabled" and s["licences"].status == "disabled"
    cov = table(s["coverage"], "Metric coverage")
    assert cov[cov["metric"].str.startswith("Grading turnaround")].iloc[0]["status"].startswith("⚠️")
    assert cov[cov["metric"] == "Licence holders by SKU"].iloc[0]["status"].startswith("🔒")


def test_gemini_only_config(cache):
    cfg = make_cfg(activity_logs=False, usage_reports=False, licensing=False, chrome=False)
    r = pipeline.build(cfg, cache["activities"], cache["users"], cache["start_time"], cache["end_time"], None)
    assert r["sections"]["activity_overview"].status == "disabled"
    assert r["sections"]["gemini_use_cases"].status == "ok"


def test_render_and_pseudonymise(tmp_path, result):
    secs = pipeline.ordered(result["sections"])
    pseudo = Pseudonymiser()
    details = report.write_detail_csvs(tmp_path, "2026-09-25", secs, pseudo)
    ctx = report.dashboard_context(pseudo.df(result["summary"]), pseudo.df(result["scoped"]), result["org"],
                                   sections=secs, pseudo=pseudo, run_date="2026-09-25")
    html = report.render_dashboard(tmp_path, "2026-09-25", ctx).read_text()
    assert "user1@example.com" not in html and "partner@outside.org" not in html
    assert "<script" not in html
    assert "What can be extracted from this tenant" in html
    for f in details.glob("*.csv"):
        assert "user1@example.com" not in f.read_text(), f.name
    # Unpseudonymised run keeps emails.
    ctx = report.dashboard_context(result["summary"], result["scoped"], result["org"], sections=secs,
                                   run_date="2026-09-25")
    assert "user1@example.com" in report.render_dashboard(tmp_path, "2026-09-25", ctx).read_text()


def test_module_scopes():
    mods = {"activity_logs": True, "usage_reports": True, "licensing": False, "chrome": False}
    scopes = scopes_for(mods)
    assert USAGE in scopes and LICENSING not in scopes
    with pytest.raises(ConfigError):
        validate_scopes(scopes[:2], mods)  # module enabled without its scope
    with pytest.raises(ConfigError):
        validate_scopes(scopes + ["https://www.googleapis.com/auth/gmail.readonly"], mods)
