"""CIO dashboard payload and rendering (no network)."""
import json
from pathlib import Path

import pytest

from src import cio, transform
from src.common import Pseudonymiser
from src.config import DEFAULTS

FIXTURE = Path(__file__).parent / "fixtures" / "sample_cache.json"
USAGE = ["feature_utilization"]


@pytest.fixture(scope="module")
def built():
    c = json.loads(FIXTURE.read_text())
    ev = transform.flatten(c["activities"])
    summary = transform.build_user_summary(ev, c["users"], usage_event_names=USAGE)
    scoped = transform.scoped_events(ev, summary, USAGE)
    cfg = {**DEFAULTS, "_start": c["start_time"], "_end": c["end_time"]}
    return summary, scoped, cfg


def test_payload_shape(built):
    summary, scoped, cfg = built
    p = cio.build_payload(summary, scoped, cfg)
    n = len(p["ev"]["u"])
    assert n == len(scoped) == 58
    assert all(len(p["ev"][k]) == n for k in "udhacfxk")
    assert len(p["users"]["e"]) == len(summary)          # zero-usage users included
    assert p["meta"]["end_day"] - p["meta"]["start_day"] == 28
    assert all(p["meta"]["start_day"] <= d <= p["meta"]["end_day"] for d in p["ev"]["d"])
    assert len(p["dict"]["uc_fam"]) == len(p["dict"]["uc"])
    # per-user totals survive the compact encoding
    u1 = p["users"]["e"].index("user1@example.com")
    assert sum(1 for u in p["ev"]["u"] if u == u1) == 31


def test_render(tmp_path, built):
    summary, scoped, cfg = built
    out = cio.render(tmp_path, "2026-09-27", cio.build_payload(summary, scoped, cfg))
    html = out.read_text()
    assert "user1@example.com" in html
    assert 'src="http' not in html and "href=\"http" not in html   # self-contained, no external loads
    assert "{%" not in html and "{{" not in html.split('id="data"')[0]
    assert (tmp_path / "cio_dashboard_2026-09-27.html").exists()


def test_pseudonymised(tmp_path, built):
    summary, scoped, cfg = built
    ps = Pseudonymiser()
    html = cio.render(tmp_path, "d", cio.build_payload(ps.df(summary), ps.df(scoped), cfg, True)).read_text()
    assert "user1@example.com" not in html and "User One" not in html
