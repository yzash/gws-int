"""Interactive CIO dashboard: Gemini usage only, sliced client-side in the browser.

The page is one self-contained HTML file. Every Gemini event in scope is embedded as
compact columnar arrays; all filtering, rolling windows and per-user drill-downs run
in the browser, so nothing is sent anywhere.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .sections_gemini import BUCKETS, FAMILY, add_buckets
from .transform import APP_COLUMNS

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
APP_LABELS = {
    "gmail": "Gmail", "docs": "Docs", "sheets": "Sheets", "slides": "Slides", "drive": "Drive",
    "chat": "Chat", "meet": "Meet", "calendar": "Calendar", "keep": "Keep", "vids": "Vids",
    "forms": "Forms", "classroom": "Classroom", "workflows": "Workflows", "gemini_app": "Gemini app",
    "other": "Other",
}
FAMILIES = ["create", "assess", "differentiate", "summarise", "communicate", "converse", "automate", "unclassified"]


def _index(values: list) -> tuple[list, dict]:
    uniq = sorted({v for v in values if v is not None})
    return uniq, {v: i for i, v in enumerate(uniq)}


def build_payload(summary: pd.DataFrame, events: pd.DataFrame, cfg: dict, pseudonymised: bool = False) -> dict:
    """summary = per-user summary (all users in scope); events = scoped Gemini usage events."""
    tz = cfg["timezone"]
    users = summary[["user_email", "name", "ou_path", "suspended"]].reset_index(drop=True)
    uidx = {e: i for i, e in enumerate(users["user_email"])}
    ev = add_buckets(events, cfg.get("use_case_map")) if len(events) else events.assign(use_case=[], family=[])
    ev = ev[ev["user_email"].isin(uidx)]
    local = ev["timestamp"].dt.tz_convert(tz) if len(ev) else ev["timestamp"]
    epoch = pd.Timestamp("1970-01-01")
    day = ((local.dt.tz_localize(None).dt.normalize() - epoch).dt.days) if len(ev) else pd.Series(dtype=int)

    apps = [a for a in APP_COLUMNS if a in set(ev["app"])] or ["other"]
    buckets = BUCKETS + ["Unclassified"]
    feats, fidx = _index(list(ev["feature"].replace("", "(none)")))
    acts, aidx = _index(list(ev["action"].replace("", "(none)")))
    ous, oidx = _index(list(users["ou_path"]))
    cats = ev["other_params"].apply(_category) if len(ev) else pd.Series(dtype=str)
    catl, cidx = _index(list(cats))

    start = pd.Timestamp(cfg.get("_start")).tz_convert(tz) if cfg.get("_start") else None
    end = pd.Timestamp(cfg.get("_end")).tz_convert(tz) if cfg.get("_end") else None
    day_of = lambda t: int((t.tz_localize(None).normalize() - epoch).days)  # noqa: E731
    d_min = day_of(start) if start is not None else (int(day.min()) if len(day) else 0)
    d_max = day_of(end) if end is not None else (int(day.max()) if len(day) else 0)

    return {
        "meta": {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "tz": tz, "start_day": d_min, "end_day": d_max, "pseudonymised": pseudonymised,
            "target": float(cfg.get("target_adoption_pct", 50)), "min_group": int(cfg.get("min_group_size", 10)),
            "work_hours": cfg.get("work_hours", {"start": 8, "end": 18, "days": [0, 1, 2, 3, 4]}),
        },
        "dict": {
            "apps": [APP_LABELS.get(a, a) for a in apps], "uc": buckets,
            "fam": FAMILIES, "uc_fam": [FAMILIES.index(FAMILY.get(b, "unclassified")) for b in buckets],
            "feat": feats, "act": acts, "ou": ous, "cat": catl,
        },
        "users": {
            "e": list(users["user_email"]), "n": [n if isinstance(n, str) else "" for n in users["name"]],
            "ou": [oidx[o] for o in users["ou_path"]], "s": [bool(s) for s in users["suspended"]],
        },
        "ev": {
            "u": [uidx[e] for e in ev["user_email"]],
            "d": [int(x) for x in day],
            "h": [int(x) for x in local.dt.hour] if len(ev) else [],
            "a": [apps.index(a) if a in apps else 0 for a in ev["app"]],
            "c": [buckets.index(b) for b in ev["use_case"]],
            "f": [fidx[f] for f in ev["feature"].replace("", "(none)")],
            "x": [aidx[a] for a in ev["action"].replace("", "(none)")],
            "k": [cidx[c] for c in cats],
        },
    }


def _category(other: str) -> str:
    try:
        return json.loads(other).get("event_category", "not reported") if other else "not reported"
    except Exception:
        return "not reported"


def render(out_dir: Path, run_date: str, payload: dict, title: str = "Gemini adoption") -> Path:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html", "j2"]))
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = env.get_template("cio.html.j2").render(data_json=data, title=title, meta=payload["meta"])
    out_dir.mkdir(parents=True, exist_ok=True)
    dated = out_dir / f"cio_dashboard_{run_date}.html"
    dated.write_text(html, encoding="utf-8")
    latest = out_dir / "cio_dashboard.html"
    shutil.copyfile(dated, latest)
    return latest
