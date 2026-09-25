"""Usage Reports, Licensing, Chrome management and external AI tools."""
from __future__ import annotations

import re

import pandas as pd

from .common import Section, pct

ACTIVE_RE = re.compile(r"^(?P<app>[a-z_]+):num_(?P<n>1|7|30)day_(?:active_)?users$")


def _pval(p: dict):
    for k in ("intValue", "stringValue", "datetimeValue", "boolValue"):
        if k in p:
            v = p[k]
            return int(v) if k == "intValue" else v
    if "msgValue" in p:
        return str(p["msgValue"])
    return None


def usage_frames(extra: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    cust = pd.DataFrame([{"date": r["date"], **{x["name"]: _pval(x) for x in r["parameters"]}}
                         for r in extra.get("usage_customer", [])])
    users = pd.DataFrame([{"date": r["date"], "user_email": r["email"].lower(),
                           **{x["name"]: _pval(x) for x in r["parameters"]}}
                          for r in extra.get("usage_users", [])])
    return cust, users


def usage_section(extra: dict, users_dir: pd.DataFrame, end: pd.Timestamp) -> Section:
    s = Section("usage", "Workspace usage reports (all apps)",
                intro="Google's own usage reports: daily, weekly and monthly active users per app, "
                      "email and Drive volumes, last sign-in and interaction times. They run 2–5 days behind.")
    cust, uu = usage_frames(extra)
    if cust.empty and uu.empty:
        s.status, s.reason = "empty", "Usage reports returned no data for this window."
        return s
    if len(cust):
        cust = cust.sort_values("date")
        latest = cust.iloc[-1]
        s.tile("Latest report date", str(latest["date"]))
        act = []
        for col in cust.columns:
            m = ACTIVE_RE.match(col)
            if m:
                act.append({"app": m["app"], "window": f"{m['n']}-day", "active_users": latest[col]})
        if act:
            at = pd.DataFrame(act).pivot_table(index="app", columns="window", values="active_users", aggfunc="first")
            at = at.reindex(columns=[c for c in ("1-day", "7-day", "30-day") if c in at.columns])
            s.table("Active users by app (latest day)", at.reset_index(), csv="usage_active_users", limit=30)
            series = []
            for col in sorted(c for c in cust.columns if c.endswith("num_1day_active_users") or c.endswith("num_1day_users")):
                vals = pd.to_numeric(cust[col], errors="coerce").fillna(0).tolist()
                series.append((col.split(":")[0] + " daily active", vals, int(vals[-1]) if vals else 0))
            if series:
                s.sparks.append({"title": "Daily active users per app", "series": series})
        for col, label in (("accounts:num_users", "Accounts"), ("accounts:num_30day_logins", "30-day sign-ins")):
            if col in cust.columns:
                s.tile(label, f"{int(latest[col] or 0):,}")
        for col in cust.columns:
            if "2sv" in col or "passkey" in col:
                s.tile(col.split(":", 1)[1].replace("_", " "), str(latest[col]))
        latest_t = latest.drop(labels=["date"]).rename("value").rename_axis("parameter").reset_index()
        s.table("Every domain-level parameter available (latest day)", latest_t, csv="usage_customer_latest", limit=15)
        s.table("Domain usage by day", cust, csv="usage_customer_daily", limit=0)
    if len(uu):
        num_cols = [c for c in uu.columns if ":num_" in c]
        last_cols = [c for c in uu.columns if ":last_" in c and c.endswith("_time")]
        flag_cols = [c for c in uu.columns if ":is_" in c or c.endswith("_enrolled") or "2sv" in c]
        latest_day = uu.sort_values("date").groupby("user_email").last()
        agg = uu.groupby("user_email")[num_cols].apply(lambda g: g.apply(pd.to_numeric, errors="coerce").sum()) if num_cols else pd.DataFrame(index=latest_day.index)
        per_user = pd.concat([latest_day[last_cols + flag_cols], agg], axis=1)
        for c in last_cols:
            per_user[c] = pd.to_datetime(per_user[c], utc=True, errors="coerce")
        if last_cols:
            per_user["last_any_interaction"] = per_user[last_cols].max(axis=1)
        per_user = per_user.reset_index()
        for col, label in (("gmail:num_emails_sent", "Emails sent"), ("gmail:num_emails_received", "Emails received"),
                           ("drive:num_items_created", "Drive items created"), ("drive:num_items_edited", "Drive items edited")):
            if col in per_user.columns:
                s.tile(label, f"{int(per_user[col].sum()):,}", "window total")
        if "last_any_interaction" in per_user.columns:
            active_dir = users_dir[~users_dir["suspended"]]["user_email"]
            j = pd.DataFrame({"user_email": active_dir}).merge(per_user[["user_email", "last_any_interaction"] +
                                                                       [c for c in ("accounts:last_login_time",) if c in per_user.columns]],
                                                              on="user_email", how="left")
            dormant = j[j["last_any_interaction"].isna() | (j["last_any_interaction"] < end - pd.Timedelta(days=30))]
            s.tile("Accounts with no activity for 30+ days", f"{len(dormant)}", f"of {len(j)} active accounts")
            s.table("Accounts with no activity for 30+ days", dormant, csv="usage_dormant_accounts", limit=15)
        s.table("Per-user usage (window totals, latest last-activity times)", per_user, csv="usage_by_user", limit=10)
        s.table("Every per-user parameter available", pd.DataFrame({"parameter": sorted(c for c in uu.columns if ":" in c)}),
                csv="usage_user_parameters", limit=0)
    return s


AI_LICENCE_DEFAULT = ["gemini", "ai "]


def licence_section(extra: dict, summary: pd.DataFrame, cfg: dict) -> Section:
    s = Section("licences", "Licences and licence value")
    lic = extra.get("licences") or {}
    items = lic.get("assignments") or []
    if not items:
        s.status = "empty"
        s.reason = "No licence assignments returned." + (
            f" Skipped products: {', '.join(lic.get('skipped_products', {}))}." if lic.get("skipped_products") else "")
        return s
    df = pd.DataFrame([{"user_email": (i.get("userId") or "").lower(), "product": i.get("productName", i.get("productId")),
                        "sku": i.get("skuName", i.get("skuId")), "sku_id": i.get("skuId")} for i in items])
    kw = [k.lower() for k in cfg.get("ai_licence_keywords") or AI_LICENCE_DEFAULT]
    df["ai_sku"] = df["sku"].str.lower().apply(lambda n: any(k in n for k in kw))
    s.chart("Seats by SKU", df["sku"].value_counts(), "seats")
    per_user = df.groupby("user_email").agg(skus=("sku", lambda x: " + ".join(sorted(set(x)))), has_ai_sku=("ai_sku", "any"))
    j = summary[["user_email", "ou_path", "total_actions", "tier", "last_used"]].merge(per_user.reset_index(), on="user_email", how="left")
    j["has_ai_sku"] = j["has_ai_sku"].fillna(False).astype(bool)
    j["skus"] = j["skus"].fillna("(none)")
    licensed = j[j["skus"] != "(none)"]
    ai_seats = j[j["has_ai_sku"]]
    s.tile("Licensed users", f"{len(licensed)}")
    s.tile("Licensed users active in Gemini", f"{int((licensed['total_actions'] > 0).sum())}",
           f"{pct((licensed['total_actions'] > 0).sum(), len(licensed))}% of licensed")
    idle = ai_seats[ai_seats["tier"].isin(["Zero", "Low"])]
    heavy = j[(j["tier"] == "High") & ~j["has_ai_sku"]]
    if len(ai_seats):
        s.tile("Paid AI seats with little use", f"{len(idle)}", f"of {len(ai_seats)} AI seats (Zero/Low tier)")
    s.tile("Heavy AI users without an AI licence", f"{len(heavy)}" if df["ai_sku"].any() else "n/a",
           "" if df["ai_sku"].any() else "no separate AI SKU in this tenant")
    s.table("Paid AI seats with little use", idle, csv="licence_idle_ai_seats", limit=10)
    if df["ai_sku"].any():
        s.table("Heavy AI users without an AI licence", heavy, csv="licence_heavy_unlicensed", limit=10)
    s.table("Licences per user with Gemini usage", j, csv="licences_by_user", limit=10)
    if lic.get("skipped_products"):
        s.notes.append("Products not present in this tenant: " + ", ".join(lic["skipped_products"]))
    return s


def _secs(v) -> float:
    try:
        return float(str(v).rstrip("s"))
    except ValueError:
        return 0.0


def chrome_mgmt_section(extra: dict, cfg: dict) -> Section:
    s = Section("chrome_mgmt", "Managed Chrome and ChromeOS devices")
    ch = extra.get("chrome_mgmt") or {}
    apps, tusers = ch.get("installed_apps") or [], ch.get("telemetry_users") or []
    for k, v in (ch.get("errors") or {}).items():
        s.notes.append(f"{k}: {v}")
    if not apps and not tusers:
        s.status, s.reason = "empty", "No managed Chrome data (needs managed browsers / ChromeOS devices)." + (
            " " + "; ".join(ch.get("errors", {}).values()) if ch.get("errors") else "")
        return s
    kw = [k.lower() for k in cfg["ai_app_keywords"]]
    if apps:
        a = pd.DataFrame([{"app": x.get("displayName", x.get("appId")), "type": x.get("appType"),
                           "install_type": x.get("appInstallType"), "browsers": int(x.get("browserDeviceCount", 0) or 0),
                           "os_users": int(x.get("osUserCount", 0) or 0), "app_id": x.get("appId")} for x in apps])
        a["ai_tool"] = a["app"].astype(str).str.lower().apply(lambda n: any(k in n for k in kw))
        s.tile("Browser apps / extensions installed", f"{len(a)}")
        s.tile("AI extensions installed", f"{int(a['ai_tool'].sum())}")
        s.table("AI extensions and apps installed", a[a["ai_tool"]].sort_values("browsers", ascending=False), csv="chrome_ai_extensions")
        s.table("All installed apps and extensions", a.sort_values("browsers", ascending=False), csv="chrome_installed_apps", limit=10)
    if tusers:
        run, states, devs = [], [], []
        for u in tusers:
            email = (u.get("userEmail") or "").lower()
            for d in u.get("userDevice", []) or []:
                devs.append({"user_email": email, "device_id": d.get("deviceId")})
                for rep in d.get("appReport", []) or []:
                    for ud in rep.get("usageData", []) or []:
                        run.append({"user_email": email, "app_id": ud.get("appId"), "app_type": ud.get("appType"),
                                    "hours": _secs(ud.get("runningDuration")) / 3600})
                for rep in d.get("deviceActivityReport", []) or []:
                    states.append({"user_email": email, "state": rep.get("deviceActivityState"), "time": rep.get("reportTime")})
        s.tile("ChromeOS users reporting", f"{len(tusers)}")
        s.tile("Devices", f"{len({d['device_id'] for d in devs})}")
        if run:
            r = pd.DataFrame(run)
            s.table("Foreground running time by app", r.groupby(["app_id", "app_type"])["hours"].sum().round(1)
                    .rename("hours").reset_index().sort_values("hours", ascending=False), csv="chromeos_app_time", limit=10)
            s.table("Foreground running time per user", r.groupby("user_email")["hours"].sum().round(1).rename("hours")
                    .reset_index().sort_values("hours", ascending=False), csv="chromeos_user_time", limit=10)
        if states:
            st = pd.DataFrame(states)
            s.chart("Device activity reports (active / idle / locked)", st["state"].value_counts(), "reports")
            s.table("Device states per user", st.pivot_table(index="user_email", columns="state", values="time",
                                                             aggfunc="size", fill_value=0).reset_index(), csv="chromeos_states")
    return s


def external_ai_section(sections: dict) -> Section:
    s = Section("external_ai", "External AI tools",
                intro="What can be seen about AI tools outside Google, from signals the APIs expose.")
    tok, ch = sections.get("token"), sections.get("chrome_mgmt")
    found = False
    for sec, title in ((tok, "AI tools connected to Google accounts"), (ch, "AI extensions and apps installed")):
        if sec and sec.status == "ok":
            for t in sec.tables:
                if t["title"] == title:
                    s.table(title, t["df"], limit=15)
                    found = True
    s.notes.append("Visits to ChatGPT, Claude, Copilot and other AI sites (first/last visit, visit counts) are not "
                   "available through Google's APIs. They need Chrome Enterprise reporting (e.g. the Chrome Enterprise "
                   "Premium reporting connector sending URL events to BigQuery or a SIEM).")
    if not found:
        s.status, s.reason = "empty", "Turn on the token audit log (activity_logs) or the chrome module to see AI tool signals."
    return s
