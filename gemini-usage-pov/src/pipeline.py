"""Turn a cache (Gemini + directory + optional extra sources) into summaries and sections."""
from __future__ import annotations

import pandas as pd

from . import coverage, sections_admin, sections_gemini, sections_workspace, transform
from .common import Section, flatten_audit, unavailable


def users_frame(users: list[dict], custom_fields: list[str]) -> pd.DataFrame:
    rows = []
    for u in users:
        r = {"user_email": (u.get("primaryEmail") or "").lower(),
             "ou_path": u.get("orgUnitPath") or "/", "suspended": bool(u.get("suspended", False)),
             "isEnrolledIn2Sv": u.get("isEnrolledIn2Sv"), "isEnforcedIn2Sv": u.get("isEnforcedIn2Sv")}
        for f in custom_fields:
            schema, _, field = f.partition(".")
            v = ((u.get("customSchemas") or {}).get(schema) or {}).get(field)
            if isinstance(v, list):
                v = ", ".join(str(x.get("value", x)) if isinstance(x, dict) else str(x) for x in v)
            r[f] = v
        rows.append(r)
    cols = ["user_email", "ou_path", "suspended", "isEnrolledIn2Sv", "isEnforcedIn2Sv"] + list(custom_fields)
    df = pd.DataFrame(rows, columns=cols)
    if df["isEnrolledIn2Sv"].isna().all():
        df = df.drop(columns=["isEnrolledIn2Sv", "isEnforcedIn2Sv"])
    return df


NOT_FETCHED = "Not fetched yet: run `python run.py` (without --from-cache) to collect it."


def _unavail_reason(status: str) -> str:
    return status.split(":", 1)[1].strip() if ":" in status else status


def build(cfg: dict, activities: list[dict], users: list[dict], start: str, end: str,
          extra: dict | None = None) -> dict:
    events = transform.flatten(activities, cfg["param_map"])
    summary = transform.build_user_summary(
        events, users, usage_event_names=cfg["usage_event_names"],
        include_suspended=cfg["include_suspended"], ou_filter=cfg["ou_filter"], tz=cfg["timezone"])
    scoped = transform.scoped_events(events, summary, cfg["usage_event_names"])
    org = transform.build_org_summary(summary, scoped, start_time=start, end_time=end,
                                      window_days=cfg["window_days"])
    udf = users_frame(users, cfg.get("custom_fields") or [])
    in_scope = udf[udf["user_email"].isin(summary["user_email"])]
    end_ts = pd.Timestamp(end)
    extra = extra or {}
    status = extra.get("status", {})

    sections: dict[str, Section] = {}
    sections["gemini_use_cases"] = sections_gemini.use_case_section(scoped, summary, cfg)
    sections["gemini_time"] = sections_gemini.time_section(scoped, cfg)
    sections["groups"] = sections_gemini.groups_section(summary, udf, cfg)

    frames: dict[str, pd.DataFrame] = {}
    if cfg["modules"].get("activity_logs"):
        in_scope_emails = set(summary["user_email"])
        restrict = bool(cfg["ou_filter"]) or not cfg["include_suspended"]
        for app, src in (extra.get("sources") or {}).items():
            f = flatten_audit(src.get("items", []), app)
            if restrict:
                # Keep in-scope people, plus rows not tied to a tenant user (external meeting guests etc.).
                tenant = f["email"].isin(set(udf["user_email"]))
                f = f[f["email"].isin(in_scope_emails) | ~tenant]
            frames[app] = f
        apps = list(cfg["activity_applications"]) + (["gmail"] if cfg.get("gmail_sent_log") else [])
        app_status = {a: status.get(a, "") for a in apps}
        sw = sections_workspace
        empty = flatten_audit([], "")
        sections["activity_overview"] = sw.overview_section(frames, app_status, in_scope, end_ts)
        built = {
            "drive": lambda: sw.drive_section(frames.get("drive", empty), udf, cfg),
            "meet": lambda: sw.meet_section(frames.get("meet", empty), udf, cfg),
            "chat": lambda: sw.chat_section(frames.get("chat", empty), cfg),
            "calendar": lambda: sw.calendar_section(frames.get("calendar", empty), udf),
            "classroom": lambda: sw.classroom_section(frames.get("classroom", empty), scoped, end_ts),
            "login": lambda: sw.login_section(frames.get("login", empty), in_scope),
            "token": lambda: sw.token_section(frames.get("token", empty), cfg),
            "chrome": lambda: sw.chrome_audit_section(frames.get("chrome", empty)),
            "keep": lambda: sw.generic_section("keep", frames.get("keep", empty)),
            "workspace_studio": lambda: sw.generic_section("workspace_studio", frames.get("workspace_studio", empty)),
            "gmail": lambda: sw.gmail_section(frames.get("gmail", empty), cfg),
        }
        for app in apps:
            if app not in built:
                if app in frames:
                    sections[app] = sw.generic_section(app, frames[app])
                continue
            key = "chrome_audit" if app == "chrome" else app
            st = app_status.get(app, "")
            title = sw.APP_TITLES.get(app, app)
            if st.startswith("unavailable"):
                sections[key] = unavailable(key, title, "unavailable", _unavail_reason(st))
            elif not st:
                sections[key] = unavailable(key, title, "unavailable", NOT_FETCHED)
            else:
                sections[key] = built[app]()
        sections["context"] = sw.context_section(frames, scoped, cfg)
    else:
        sections["activity_overview"] = unavailable(
            "activity_overview", "Workspace activity overview (audit logs)", "disabled",
            "Turn on modules.activity_logs in config.yaml (uses the same permission as Gemini).")

    def optional(key, module, status_key, title, fn):
        st = status.get(status_key, "")
        if not cfg["modules"].get(module):
            sections[key] = unavailable(key, title, "disabled",
                                        f"Turn on modules.{module} in config.yaml and re-run setup.py.")
        elif st.startswith("unavailable"):
            sections[key] = unavailable(key, title, "unavailable", _unavail_reason(st))
        elif not st:
            sections[key] = unavailable(key, title, "unavailable", NOT_FETCHED)
        else:
            sections[key] = fn()

    optional("usage", "usage_reports", "usage_reports", "Workspace usage reports (all apps)",
             lambda: sections_admin.usage_section(extra, udf, end_ts))
    optional("licences", "licensing", "licensing", "Licences and licence value",
             lambda: sections_admin.licence_section(extra, summary, cfg))
    optional("chrome_mgmt", "chrome", "chrome_mgmt", "Managed Chrome and ChromeOS devices",
             lambda: sections_admin.chrome_mgmt_section(extra, cfg))
    sections["external_ai"] = sections_admin.external_ai_section(sections)

    states = coverage.source_states(cfg, extra, len(scoped), len(users))
    sections["coverage"] = coverage.coverage_section(cfg, states)
    return {"events": events, "summary": summary, "scoped": scoped, "org": org,
            "sections": sections, "users": udf}


ORDER = ["coverage", "gemini_use_cases", "gemini_time", "groups", "activity_overview", "usage", "drive",
         "meet", "chat", "gmail", "calendar", "classroom", "login", "token", "external_ai", "licences",
         "chrome_mgmt", "chrome_audit", "keep", "workspace_studio", "context"]


def ordered(sections: dict) -> list[Section]:
    return [sections[k] for k in ORDER if k in sections] + [v for k, v in sections.items() if k not in ORDER]
