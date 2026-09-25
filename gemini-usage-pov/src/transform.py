"""Flatten Gemini audit events, join with the directory, and build summaries.

Pure functions over plain Python data / pandas - no network, no credentials.
"""
from __future__ import annotations

import json
import logging
from collections import Counter

import pandas as pd

log = logging.getLogger("gemini_usage")

APPS = ["gmail", "docs", "sheets", "slides", "drive", "chat", "meet", "calendar", "keep", "vids",
        "forms", "classroom", "workflows", "gemini_app"]
APP_COLUMNS = APPS + ["other"]

# Candidate parameter names for each output field, tried in order. Parameter
# names are not guaranteed stable across apps, so these can be overridden with
# `param_map` in config.yaml, and anything not listed is reported by
# `parameter_report` so the admin can see the real shape of their data.
DEFAULT_PARAM_MAP = {
    "app": ["app_name", "application_name", "app"],
    "feature": ["feature_source", "feature_name", "feature"],
    "action": ["action", "action_type", "action_name"],
}
# Seen in Google's documentation for this log; recognised but not mapped to a column.
RECOGNISED_EXTRA = {"event_category"}

APP_ALIASES = {
    "gmail": "gmail", "mail": "gmail",
    "docs": "docs", "doc": "docs", "document": "docs", "documents": "docs", "kix": "docs",
    "sheets": "sheets", "sheet": "sheets", "spreadsheet": "sheets", "spreadsheets": "sheets", "trix": "sheets",
    "slides": "slides", "slide": "slides", "presentation": "slides", "presentations": "slides", "punch": "slides",
    "drive": "drive",
    "chat": "chat", "dynamite": "chat", "hangouts_chat": "chat",
    "meet": "meet", "meetings": "meet",
    "calendar": "calendar", "cal": "calendar",
    "keep": "keep", "vids": "vids", "forms": "forms", "classroom": "classroom",
    "workflows": "workflows", "workflow": "workflows", "flows": "workflows",
    "gemini_app": "gemini_app", "gemini": "gemini_app", "gemini_web": "gemini_app",
    "gemini_web_app": "gemini_app", "bard": "gemini_app", "gemini_app_web": "gemini_app",
}

RAW_COLUMNS = ["timestamp", "user_email", "app", "feature", "action",
               "event_name", "ip", "event_type", "other_params"]

USER_COLUMNS = (["user_email", "name", "ou_path", "suspended", "total_actions"]
                + APP_COLUMNS
                + ["distinct_features", "active_days", "first_used", "last_used", "tier"])

TIERS = ["Zero", "Low", "Medium", "High"]


def tier_for(total: int) -> str:
    """Mirrors the Admin console's Gemini report buckets."""
    if total <= 0:
        return "Zero"
    if total <= 4:
        return "Low"
    if total <= 19:
        return "Medium"
    return "High"


def normalise_app(value: str | None) -> str:
    if not value:
        return "other"
    key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    for prefix in ("google_", "gemini_in_"):
        if key.startswith(prefix) and key[len(prefix):] in APP_ALIASES:
            key = key[len(prefix):]
    return APP_ALIASES.get(key, "other")


def param_value(p: dict):
    for key in ("value", "intValue", "boolValue"):
        if key in p:
            return p[key]
    for key in ("multiValue", "multiIntValue"):
        if key in p:
            return "|".join(str(v) for v in p[key])
    if "messageValue" in p:
        return json.dumps(p["messageValue"], sort_keys=True)
    if "multiMessageValue" in p:
        return json.dumps(p["multiMessageValue"], sort_keys=True)
    return None


def _pick(params: dict, names: list[str]):
    for n in names:
        if params.get(n) not in (None, ""):
            return params[n]
    return None


def flatten(activities: list[dict], param_map: dict | None = None) -> pd.DataFrame:
    """One row per event (an activity can hold several events)."""
    pmap = {**DEFAULT_PARAM_MAP, **(param_map or {})}
    mapped = {n for names in pmap.values() for n in names}
    rows = []
    for act in activities:
        email = ((act.get("actor") or {}).get("email") or "").strip().lower()
        ts = (act.get("id") or {}).get("time")
        ip = act.get("ipAddress", "")
        for ev in act.get("events") or []:
            params = {p.get("name"): param_value(p) for p in ev.get("parameters") or []}
            raw_app = _pick(params, pmap["app"])
            feature = _pick(params, pmap["feature"])
            action = _pick(params, pmap["action"])
            other = {k: v for k, v in params.items() if k not in mapped}
            rows.append({
                "timestamp": ts,
                "user_email": email,
                "app": normalise_app(raw_app),
                "app_raw": raw_app or "",
                "feature": str(feature) if feature is not None else "",
                "action": str(action) if action is not None else "",
                "event_name": ev.get("name", ""),
                "ip": ip,
                "event_type": ev.get("type", ""),
                "other_params": json.dumps(other, sort_keys=True, default=str) if other else "",
            })
    df = pd.DataFrame(rows, columns=RAW_COLUMNS + ["app_raw"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
    return df.sort_values(["timestamp", "user_email"], kind="stable").reset_index(drop=True)


def parameter_report(activities: list[dict], param_map: dict | None = None) -> dict:
    """What the data actually looks like: event names/types, parameter names, raw app values."""
    pmap = {**DEFAULT_PARAM_MAP, **(param_map or {})}
    known = {n for names in pmap.values() for n in names} | RECOGNISED_EXTRA
    param_names: Counter = Counter()
    event_names: Counter = Counter()
    event_types: Counter = Counter()
    raw_apps: Counter = Counter()
    for act in activities:
        for ev in act.get("events") or []:
            event_names[ev.get("name", "")] += 1
            event_types[ev.get("type", "")] += 1
            params = {p.get("name"): param_value(p) for p in ev.get("parameters") or []}
            param_names.update(params.keys())
            raw_app = _pick(params, pmap["app"])
            if raw_app:
                raw_apps[str(raw_app)] += 1
    unknown = sorted(n for n in param_names if n not in known)
    unmapped_apps = sorted(a for a in raw_apps if normalise_app(a) == "other")
    return {
        "event_names": dict(event_names),
        "event_types": dict(event_types),
        "parameter_names": dict(param_names),
        "unknown_parameters": unknown,
        "raw_app_values": dict(raw_apps),
        "unmapped_app_values": unmapped_apps,
    }


def log_parameter_report(report: dict, usage_event_names: list[str]) -> None:
    log.info("Event names seen: %s", report["event_names"])
    log.info("Parameter names seen: %s", sorted(report["parameter_names"]))
    if report["unknown_parameters"]:
        log.warning("Unrecognised parameter names (kept in raw_events.csv other_params): %s",
                    report["unknown_parameters"])
    if report["unmapped_app_values"]:
        log.warning("App values not mapped to a known app (counted as 'other'): %s",
                    report["unmapped_app_values"])
    if usage_event_names:
        excluded = {k: v for k, v in report["event_names"].items() if k not in usage_event_names}
        if excluded:
            log.warning("Events excluded from summaries because their name is not in "
                        "usage_event_names %s: %s. Add them in config.yaml to count them.",
                        usage_event_names, excluded)


def usage_events(events: pd.DataFrame, usage_event_names: list[str]) -> pd.DataFrame:
    if not usage_event_names:
        return events
    return events[events["event_name"].isin(usage_event_names)]


def directory_frame(users: list[dict]) -> pd.DataFrame:
    rows = [{
        "user_email": (u.get("primaryEmail") or "").strip().lower(),
        "name": (u.get("name") or {}).get("fullName", ""),
        "ou_path": u.get("orgUnitPath") or "/",
        "suspended": bool(u.get("suspended", False)),
    } for u in users]
    return pd.DataFrame(rows, columns=["user_email", "name", "ou_path", "suspended"])


def _in_ou(path: str, prefixes: list[str]) -> bool:
    for p in prefixes:
        p = "/" + p.strip("/") if p.strip("/") else "/"
        if p == "/" or path == p or path.startswith(p + "/"):
            return True
    return False


def build_user_summary(events: pd.DataFrame, users: list[dict], *,
                       usage_event_names: list[str] | None = None,
                       include_suspended: bool = False,
                       ou_filter: list[str] | None = None,
                       tz: str = "UTC") -> pd.DataFrame:
    """One row per user, including directory users with zero usage."""
    ev = usage_events(events, usage_event_names or [])
    ev = ev[ev["user_email"] != ""]
    dirdf = directory_frame(users)

    if len(ev):
        local_day = ev["timestamp"].dt.tz_convert(tz).dt.date
        agg = ev.assign(_day=local_day).groupby("user_email").agg(
            total_actions=("event_name", "size"),
            distinct_features=("feature", lambda s: s[s != ""].nunique()),
            active_days=("_day", "nunique"),
            first_used=("timestamp", "min"),
            last_used=("timestamp", "max"),
        )
        per_app = (ev.pivot_table(index="user_email", columns="app", values="event_name",
                                  aggfunc="size", fill_value=0)
                   .reindex(columns=APP_COLUMNS, fill_value=0))
        agg = agg.join(per_app).reset_index()
    else:
        agg = pd.DataFrame(columns=["user_email", "total_actions", "distinct_features",
                                    "active_days", "first_used", "last_used"] + APP_COLUMNS)

    df = dirdf.merge(agg, on="user_email", how="outer", indicator=True)
    not_in_dir = df["_merge"] == "right_only"
    df.loc[not_in_dir, "name"] = ""
    df.loc[not_in_dir, "ou_path"] = "(not in directory)"
    df["suspended"] = df["suspended"].astype("boolean").fillna(False).astype(bool)
    df = df.drop(columns="_merge")

    for col in ["total_actions", "distinct_features", "active_days"] + APP_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    if not include_suspended:
        df = df[~df["suspended"]]
    if ou_filter:
        df = df[df["ou_path"].apply(lambda p: _in_ou(p, ou_filter))]

    df["tier"] = df["total_actions"].apply(tier_for)
    for col in ("first_used", "last_used"):
        df[col] = pd.to_datetime(df[col], utc=True)
    df = df.sort_values(["total_actions", "user_email"], ascending=[False, True])
    return df[USER_COLUMNS].reset_index(drop=True)


def scoped_events(events: pd.DataFrame, user_summary: pd.DataFrame,
                  usage_event_names: list[str] | None = None) -> pd.DataFrame:
    """Usage events limited to the users that made it into the summary."""
    ev = usage_events(events, usage_event_names or [])
    return ev[ev["user_email"].isin(set(user_summary["user_email"]))]


def top_features(events: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    ev = events[events["feature"] != ""]
    if ev.empty:
        return pd.DataFrame(columns=["feature", "actions", "users"])
    out = (ev.groupby("feature")
             .agg(actions=("event_name", "size"), users=("user_email", "nunique"))
             .reset_index()
             .sort_values(["actions", "feature"], ascending=[False, True]))
    return out.head(n).reset_index(drop=True)


def adoption_by_ou(user_summary: pd.DataFrame) -> pd.DataFrame:
    g = user_summary.groupby("ou_path").agg(
        users=("user_email", "size"),
        active_users=("total_actions", lambda s: int((s > 0).sum())),
    ).reset_index()
    g["adoption_pct"] = (100 * g["active_users"] / g["users"]).round(1)
    return g.sort_values("ou_path").reset_index(drop=True)


def build_org_summary(user_summary: pd.DataFrame, events: pd.DataFrame, *,
                      start_time: str, end_time: str, window_days: int) -> pd.DataFrame:
    """One row per metric. `events` should already be scoped via scoped_events()."""
    total_users = len(user_summary)
    active_users = int((user_summary["total_actions"] > 0).sum())
    rows = [
        ("window_start", start_time),
        ("window_end", end_time),
        ("window_days", window_days),
        ("total_users", total_users),
        ("active_users", active_users),
        ("adoption_pct", round(100 * active_users / total_users, 1) if total_users else 0.0),
        ("total_actions", int(user_summary["total_actions"].sum())),
    ]
    for app in APP_COLUMNS:
        rows.append((f"actions_{app}", int(user_summary[app].sum())))
    for app in APP_COLUMNS:
        rows.append((f"active_users_{app}", int((user_summary[app] > 0).sum())))
    for t in TIERS:
        rows.append((f"users_tier_{t.lower()}", int((user_summary["tier"] == t).sum())))
    for i, r in top_features(events, 10).iterrows():
        rows.append((f"top_feature_{i + 1}", f"{r['feature']} ({r['actions']} actions, {r['users']} users)"))
    for _, r in adoption_by_ou(user_summary).iterrows():
        rows.append((f"adoption_pct_ou:{r['ou_path']}",
                     f"{r['adoption_pct']} ({r['active_users']}/{r['users']})"))
    return pd.DataFrame(rows, columns=["metric", "value"])


COMPARE_METRICS = ["total_users", "active_users", "adoption_pct", "total_actions"] + \
    [f"actions_{a}" for a in APP_COLUMNS]


def compare_org_summaries(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    """F11: change in headline metrics between two org_summary CSVs."""
    prev = dict(zip(previous["metric"], previous["value"]))
    cur = dict(zip(current["metric"], current["value"]))
    rows = []
    for m in COMPARE_METRICS:
        if m not in prev or m not in cur:
            continue
        p, c = float(prev[m]), float(cur[m])
        rows.append({"metric": m, "previous": p, "current": c, "change": round(c - p, 1)})
    return pd.DataFrame(rows, columns=["metric", "previous", "current", "change"])
