"""CSV writers and the static HTML dashboard."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .transform import APP_COLUMNS, RAW_COLUMNS, TIERS, adoption_by_ou, top_features

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

APP_LABELS = {
    "gmail": "Gmail", "docs": "Docs", "sheets": "Sheets", "slides": "Slides",
    "drive": "Drive", "chat": "Chat", "meet": "Meet", "calendar": "Calendar", "workflows": "Workflows", "gemini_app": "Gemini app",
    "other": "Other",
}


def _fmt_ts(s: pd.Series) -> pd.Series:
    return s.apply(lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ") if pd.notna(t) else "")


def write_csvs(out_dir: Path, run_date: str, events: pd.DataFrame,
               user_summary: pd.DataFrame, org_summary: pd.DataFrame) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "raw_events": out_dir / f"raw_events_{run_date}.csv",
        "user_summary": out_dir / f"user_summary_{run_date}.csv",
        "org_summary": out_dir / f"org_summary_{run_date}.csv",
    }
    raw = events[RAW_COLUMNS].copy()
    raw["timestamp"] = _fmt_ts(raw["timestamp"])
    raw.to_csv(paths["raw_events"], index=False)

    us = user_summary.copy()
    us["first_used"] = _fmt_ts(us["first_used"])
    us["last_used"] = _fmt_ts(us["last_used"])
    us.to_csv(paths["user_summary"], index=False)

    org_summary.to_csv(paths["org_summary"], index=False)
    return paths


def _bars(items: list[tuple[str, int]]) -> list[dict]:
    peak = max([v for _, v in items] + [1])
    return [{"label": k, "value": v, "pct": round(100 * v / peak, 2)} for k, v in items]


def dashboard_context(user_summary: pd.DataFrame, events: pd.DataFrame, org: pd.DataFrame,
                      comparison: pd.DataFrame | None = None, tenant_label: str = "") -> dict:
    m = dict(zip(org["metric"], org["value"]))
    start = str(m["window_start"])[:10]
    end = str(m["window_end"])[:10]

    by_app = [(APP_LABELS[a], int(user_summary[a].sum())) for a in APP_COLUMNS]
    by_app = [x for x in by_app if not (x[0] == "Other" and x[1] == 0)]
    by_app.sort(key=lambda x: -x[1])
    by_tier = [(t, int((user_summary["tier"] == t).sum())) for t in TIERS]

    top_users = user_summary[user_summary["total_actions"] > 0].head(20)
    top_users_rows = [{
        "email": r.user_email, "name": r.name, "ou": r.ou_path,
        "actions": int(r.total_actions), "tier": r.tier,
        "apps": ", ".join(f"{APP_LABELS[a]} {int(getattr(r, a))}"
                          for a in APP_COLUMNS if int(getattr(r, a)) > 0),
        "last_used": r.last_used.strftime("%Y-%m-%d") if pd.notna(r.last_used) else "",
    } for r in top_users.itertuples()]

    zero = user_summary[user_summary["total_actions"] == 0]
    zero_by_ou = [{
        "ou": ou,
        "users": [{"email": r.user_email, "name": r.name} for r in grp.itertuples()],
    } for ou, grp in zero.sort_values(["ou_path", "user_email"]).groupby("ou_path", sort=True)]

    ou_rows = adoption_by_ou(user_summary).to_dict("records")

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "tenant_label": tenant_label,
        "window": f"{start} to {end}",
        "window_days": m["window_days"],
        "tiles": [
            {"label": "Users in scope", "value": f"{int(m['total_users']):,}"},
            {"label": "Active Gemini users", "value": f"{int(m['active_users']):,}"},
            {"label": "Adoption", "value": f"{float(m['adoption_pct']):.1f}%"},
            {"label": "Total Gemini actions", "value": f"{int(m['total_actions']):,}"},
            {"label": "Window", "value": f"{m['window_days']} days", "sub": f"{start} → {end}"},
        ],
        "by_app": _bars(by_app),
        "by_tier": _bars(by_tier),
        "top_users": top_users_rows,
        "top_features": top_features(events, 10).to_dict("records"),
        "zero_by_ou": zero_by_ou,
        "zero_count": len(zero),
        "ou_rows": ou_rows,
        "comparison": comparison.to_dict("records") if comparison is not None and len(comparison) else [],
    }


def render_dashboard(out_dir: Path, run_date: str, context: dict) -> Path:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)),
                      autoescape=select_autoescape(["html", "j2"]))
    html = env.get_template("dashboard.html.j2").render(**context)
    dated = out_dir / f"dashboard_{run_date}.html"
    dated.write_text(html, encoding="utf-8")
    latest = out_dir / "dashboard.html"
    shutil.copyfile(dated, latest)
    return latest
