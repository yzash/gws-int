"""Shared helpers for the analytics sections."""
from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass, field
from datetime import timedelta

import pandas as pd

from .transform import param_value

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class Section:
    """One block of the dashboard plus the detail CSVs behind it."""
    key: str
    title: str
    status: str = "ok"            # ok | empty | unavailable | disabled
    reason: str = ""
    intro: str = ""
    tiles: list = field(default_factory=list)      # (label, value, sub)
    charts: list = field(default_factory=list)     # {"title", "bars": [(label, value)], "unit"}
    heatmaps: list = field(default_factory=list)   # {"title", "grid": DataFrame 7x24}
    sparks: list = field(default_factory=list)     # {"title", "series": [(label, [values], latest)]}
    tables: list = field(default_factory=list)     # {"title", "df", "limit", "csv", "note"}
    notes: list = field(default_factory=list)

    def table(self, title, df, csv=None, limit=15, note=""):
        self.tables.append({"title": title, "df": df, "csv": csv, "limit": limit, "note": note})

    def chart(self, title, series: pd.Series | list, unit=""):
        if isinstance(series, pd.Series):
            bars = [(str(k), float(v)) for k, v in series.items()]
        else:
            bars = [(str(k), float(v)) for k, v in series]
        self.charts.append({"title": title, "bars": bars, "unit": unit})

    def tile(self, label, value, sub=""):
        self.tiles.append((label, value, sub))


def unavailable(key, title, status, reason) -> Section:
    return Section(key=key, title=title, status=status, reason=reason)


def flatten_audit(items: list[dict], app: str) -> pd.DataFrame:
    """One row per event: time, email, ip, event_type, name, params (dict)."""
    rows = []
    for act in items or []:
        actor = act.get("actor") or {}
        email = (actor.get("email") or "").strip().lower()
        ts = (act.get("id") or {}).get("time")
        ip = act.get("ipAddress", "")
        for ev in act.get("events") or []:
            params = {p.get("name"): param_value(p) for p in ev.get("parameters") or []}
            e = email
            if not e and str(params.get("identifier_type", "")) == "email_address":
                e = str(params.get("identifier", "")).lower()
            rows.append({"time": ts, "email": e, "ip": ip, "app": app,
                         "event_type": ev.get("type", ""), "name": ev.get("name", ""),
                         "params": params})
    df = pd.DataFrame(rows, columns=["time", "email", "ip", "app", "event_type", "name", "params"])
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    return df


def p(df: pd.DataFrame, name: str, default=None) -> pd.Series:
    """Extract one parameter from the params column."""
    return df["params"].apply(lambda d: d.get(name, default))


def truthy(s: pd.Series) -> pd.Series:
    return s.apply(lambda v: str(v).lower() in ("true", "1", "yes"))


def local(ts: pd.Series, tz: str) -> pd.Series:
    return ts.dt.tz_convert(tz)


def heatmap(ts: pd.Series, tz: str) -> pd.DataFrame:
    t = local(ts, tz)
    grid = pd.crosstab(t.dt.weekday, t.dt.hour).reindex(index=range(7), columns=range(24), fill_value=0)
    grid.index = WEEKDAYS
    return grid


def after_hours_mask(ts: pd.Series, tz: str, work_hours: dict) -> pd.Series:
    t = local(ts, tz)
    days = set(work_hours.get("days", [0, 1, 2, 3, 4]))
    start, end = int(work_hours.get("start", 8)), int(work_hours.get("end", 18))
    in_hours = t.dt.weekday.isin(days) & (t.dt.hour >= start) & (t.dt.hour < end)
    return ~in_hours


def active_users_by_window(df: pd.DataFrame, end: pd.Timestamp, col="email") -> dict:
    out = {}
    for d in (1, 7, 30):
        sub = df[df["time"] > end - timedelta(days=d)]
        out[d] = int(sub[col][sub[col] != ""].nunique())
    return out


def pct(a, b) -> float:
    return round(100.0 * a / b, 1) if b else 0.0


def domain(email: str) -> str:
    return email.split("@", 1)[1].lower() if isinstance(email, str) and "@" in email else ""


def ip_matcher(ranges: list[str]):
    nets = []
    for r in ranges or []:
        try:
            nets.append(ipaddress.ip_network(r, strict=False))
        except ValueError:
            pass

    def match(ip) -> bool:
        try:
            a = ipaddress.ip_address(str(ip))
        except ValueError:
            return False
        return any(a in n for n in nets)
    return match, bool(nets)


def counts(series: pd.Series, name="count") -> pd.DataFrame:
    vc = series.value_counts()
    return vc.rename_axis("value").reset_index(name=name)


def per_user_event_counts(df: pd.DataFrame, events: dict[str, list[str]]) -> pd.DataFrame:
    """Columns = labels, counting rows whose event name is in the list."""
    base = df[df["email"] != ""]
    out = pd.DataFrame(index=sorted(base["email"].unique()))
    for label, names in events.items():
        out[label] = base[base["name"].isin(names)].groupby("email").size()
    out = out.fillna(0).astype(int)
    out["last_activity"] = base.groupby("email")["time"].max()
    out.index.name = "user_email"
    return out.reset_index().sort_values(list(events)[0], ascending=False)


# ------------------------------------------------------------ pseudonymisation
class Pseudonymiser:
    """Stable replacement of emails / names / titles for shareable output."""

    def __init__(self, salt: str = "gemini-usage"):
        self.salt = salt

    def _h(self, v: str, prefix: str) -> str:
        d = hashlib.sha256((self.salt + v.lower()).encode()).hexdigest()[:8]
        return f"{prefix}-{d}"

    def email(self, v):
        if isinstance(v, str) and "@" in v:
            return self._h(v, "user") + "@" + v.split("@", 1)[1]
        return v

    def df(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        out = df.copy()
        for c in out.columns:
            lc = str(c).lower()
            if not (out[c].dtype == object or pd.api.types.is_string_dtype(out[c])):
                continue
            person_name = lc in ("name", "display_name", "full_name") or (
                lc.endswith("_name") and not any(k in lc for k in ("app", "sku", "product", "event", "feature", "room", "add_on")))
            if person_name:
                out[c] = out[c].apply(lambda v: "" if isinstance(v, str) else v)
            elif "title" in lc:
                out[c] = out[c].apply(lambda v: self._h(v, "title") if isinstance(v, str) and v else v)
            elif lc in ("ip", "ip_address"):
                out[c] = ""
            else:
                out[c] = out[c].apply(self._text)
        if out.index.dtype == object or pd.api.types.is_string_dtype(out.index):
            out.index = [self._text(i) for i in out.index]
        return out

    def _text(self, v):
        if isinstance(v, str) and "@" in v:
            parts = [self.email(t) if "@" in t else t for t in v.replace(",", " , ").split(" ")]
            return " ".join(parts).replace(" , ", ", ")
        return v


def json_small(d) -> str:
    return json.dumps(d, sort_keys=True, default=str)
