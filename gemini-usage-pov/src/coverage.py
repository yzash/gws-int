"""'What can this tenant's data tell us?' - the capability map for the catalog of metrics."""
from __future__ import annotations

import pandas as pd

from .common import Section

MODULE_OF = {"usage_reports": "usage_reports", "licensing": "licensing", "chrome_mgmt": "chrome"}
AUDIT_APPS = {"drive", "meet", "chat", "calendar", "login", "token", "classroom", "keep",
              "workspace_studio", "chrome", "gmail"}

# (area, metric, sources: "a|b" = any of, "none:<why>" = not exposed, "config:<key>" = needs a setting)
CATALOG = [
    ("Core adoption", "Daily / weekly / monthly active users by app", "usage_reports|drive"),
    ("Core adoption", "Active users by group (OU / school / region)", "gemini|drive"),
    ("Core adoption", "Last sign-in and last activity per app, per user", "usage_reports|login"),
    ("Core adoption", "Accounts with no activity for 30+ days", "usage_reports|login"),
    ("Drive", "Files created, edited, viewed, trashed per user per day", "drive"),
    ("Drive", "Visibility: private, internal, external, anyone-with-link, public", "drive"),
    ("Drive", "Creators vs consumers", "drive"),
    ("Drive", "Shared-drive activity", "drive"),
    ("Classroom", "Active courses and teachers (1 / 7 / 30 days)", "classroom"),
    ("Classroom", "Active classes per teacher; assignments and announcements per week", "classroom"),
    ("Classroom", "Posts and comments by teachers vs students", "classroom"),
    ("Classroom", "Courses created per user", "classroom"),
    ("Classroom", "Submissions per assignment, late submissions", "classroom"),
    ("Meet", "Meet hours per user", "meet"),
    ("Meet", "Minutes by device and internal / external participants", "meet"),
    ("Meet", "Meetings by size, meetings with external guests", "meet"),
    ("Meet", "Polls, Q&A, raised hands", "meet"),
    ("Meet", "Reactions", "none:Not recorded in the Meet audit log"),
    ("Gmail and Chat", "Emails sent and received per user", "usage_reports|gmail"),
    ("Gmail and Chat", "Chat messages, reactions, conversations read, spaces created", "chat"),
    ("Calendar", "Events created or changed per user", "calendar"),
    ("Calendar", "Guest invitations and appointment schedules", "calendar"),
    ("Gemini", "% of users with at least one Gemini action", "gemini"),
    ("Gemini", "High / Medium / Low / Zero usage tiers", "gemini"),
    ("Gemini", "Active AI days per month (median)", "gemini"),
    ("Gemini", "Usage by app (Gmail, Docs, Sheets, Slides, Drive, Meet, Chat, Keep, Vids, Forms, Classroom, Gemini app…)", "gemini"),
    ("Gemini", "13 use-case buckets and share of actions by type", "gemini"),
    ("Gemini", "Decks, images, video and audio generated", "gemini"),
    ("Gemini", "Lesson plans, quizzes, rubrics via Gemini in Classroom", "gemini"),
    ("Gemini", "Average use-case buckets per AI user", "gemini"),
    ("Gemini", "Gemini Notebook / NotebookLM outputs (notebooks, sources, Audio Overviews)",
     "none:No NotebookLM application in the Reports API audit logs"),
    ("Gemini", "Users hitting AI usage limits", "none:Not exposed in the Gemini audit log"),
    ("Teaching practice", "Grading turnaround (turned in → graded)", "classroom"),
    ("Teaching practice", "Late-submission rate by class", "classroom"),
    ("Teaching practice", "Rubric creation and scoring", "classroom"),
    ("Teaching practice", "Private and public comments per submission; feedback events", "classroom"),
    ("Teaching practice", "EdTech add-on use in Classroom", "classroom"),
    ("Teaching practice", "Guardian summaries", "none:Not in the Classroom audit log"),
    ("Collaboration", "Comments created", "drive"),
    ("Collaboration", "Comments resolved / reassigned; suggestions accepted / rejected",
     "none:Google's Drive audit event list documents comment creation only; if resolve/suggestion "
     "events appear in your data they are counted automatically in the Drive per-user table"),
    ("Collaboration", "Colleagues shared with; sharing across OUs / schools", "drive"),
    ("Collaboration", "Most-copied templates and resources", "drive"),
    ("Collaboration", "Permission and link-sharing changes; external / public sharing", "drive"),
    ("Time and context", "Activity by hour and weekday", "gemini|drive|chat"),
    ("Time and context", "After-hours email and Chat", "gmail|chat"),
    ("Time and context", "Term vs holiday / before vs after comparison", "config:periods"),
    ("Time and context", "Device mix", "meet|chrome"),
    ("Time and context", "Office / school network vs home", "config:office_ip_ranges"),
    ("Time and context", "Regional patterns (Meet join locations)", "meet"),
    ("Security", "2-Step Verification enrolment", "directory"),
    ("Security", "Passkey enrolment", "login"),
    ("Security", "Suspicious sign-ins", "login"),
    ("Security", "OAuth app authorisations (incl. AI tools)", "token"),
    ("Organisation", "Breakdown by OU and OU level", "directory"),
    ("Organisation", "Breakdown by subject / grade (custom directory fields)", "config:custom_fields"),
    ("Organisation", "Minimum group size (hide groups under N)", "directory"),
    ("Organisation", "Top 10% of users per group; groups below target reach", "gemini"),
    ("Organisation", "Champions vs non-users", "gemini"),
    ("Organisation", "Run-to-run change (python run.py --compare)", "gemini"),
    ("Licences", "Licence holders by SKU", "licensing"),
    ("Licences", "Paid AI seats with little use; heavy AI users without an AI licence", "licensing"),
    ("Licences", "Licensed seats vs active users", "licensing"),
    ("External AI tools", "AI tools connected via Google sign-in (OAuth)", "token"),
    ("External AI tools", "AI browser extensions installed", "chrome_mgmt"),
    ("External AI tools", "Visits to ChatGPT, Claude, Copilot and other AI sites",
     "none:Not in Google's APIs; needs Chrome Enterprise reporting (URL events) to BigQuery or a SIEM"),
    ("Chromebooks", "Foreground running time per app; device active / idle / locked", "chrome_mgmt"),
]

RANK = {"extracted": 4, "no_data": 3, "blocked": 2, "needs_module": 1, "needs_config": 1, "not_available": 0}
LABEL = {"extracted": "✅ Extracted", "no_data": "⚪ Supported · no data in window",
         "blocked": "⚠️ Blocked", "needs_module": "🔒 Needs module", "needs_config": "⚙️ Needs setting",
         "not_available": "❌ Not available via API"}


def source_states(cfg: dict, extra: dict, gemini_events: int, users: int) -> dict:
    """source key -> (state, detail, records)."""
    st = {"gemini": ("extracted" if gemini_events else "no_data", f"{gemini_events} events", gemini_events),
          "directory": ("extracted" if users else "no_data", f"{users} users", users)}
    status = (extra or {}).get("status", {})
    for app in AUDIT_APPS:
        if not cfg["modules"].get("activity_logs") or (app not in cfg["activity_applications"]
                                                        and not (app == "gmail" and cfg.get("gmail_sent_log"))):
            need = "gmail_sent_log: true" if app == "gmail" else ("modules.activity_logs" if not cfg["modules"].get("activity_logs")
                                                                  else f"add '{app}' to activity_applications")
            st[app] = ("needs_module", need, 0)
            continue
        s = status.get(app, "")
        n = len(((extra or {}).get("sources", {}).get(app) or {}).get("items", []))
        st[app] = _from_status(s, n)
    for key, module in MODULE_OF.items():
        if not cfg["modules"].get(module):
            st[key] = ("needs_module", f"modules.{module}", 0)
            continue
        s = status.get(key if key != "chrome_mgmt" else "chrome_mgmt", status.get(module, ""))
        if key == "usage_reports":
            n = len((extra or {}).get("usage_customer", [])) + len((extra or {}).get("usage_users", []))
        elif key == "licensing":
            n = len(((extra or {}).get("licences") or {}).get("assignments", []))
        else:
            ch = (extra or {}).get("chrome_mgmt") or {}
            n = len(ch.get("installed_apps", [])) + len(ch.get("telemetry_users", []))
        st[key] = _from_status(s, n)
    return st


def _from_status(s: str, n: int):
    if s.startswith("unavailable"):
        return ("blocked", s.split(":", 1)[1].strip(), 0)
    if not s:
        return ("blocked", "not fetched (run without --from-cache, or cache is from an older run)", 0)
    return ("extracted" if n else "no_data", s, n)


def coverage_section(cfg: dict, states: dict) -> Section:
    rows = []
    for area, metric, src in CATALOG:
        if src.startswith("none:"):
            state, detail, via = "not_available", src[5:], "—"
        elif src.startswith("config:"):
            key = src[7:]
            ok = bool(cfg.get(key))
            state, detail, via = ("extracted" if ok else "needs_config"), (f"{key} is set" if ok else f"set {key} in config.yaml"), "config"
        else:
            best = None
            for s in src.split("|"):
                cand = states.get(s, ("blocked", "unknown source", 0)) + (s,)
                if best is None or RANK[cand[0]] > RANK[best[0]]:
                    best = cand
            state, detail, _, via = best
        rows.append({"area": area, "metric": metric, "status": LABEL[state], "source": via, "detail": detail, "_state": state})
    df = pd.DataFrame(rows)
    s = Section("coverage", "What can be extracted from this tenant",
                intro="Every metric in the catalog, checked against this tenant's live data. "
                      "✅ extracted · ⚪ supported but nothing in this window · 🔒/⚙️ needs a module or setting · "
                      "⚠️ blocked by Google (edition, API or permission) · ❌ not exposed by Google's APIs.")
    tally = df["_state"].value_counts()
    for k in ("extracted", "no_data", "needs_module", "blocked", "not_available"):
        s.tile(LABEL[k].split(" ", 1)[1].split(" ·")[0].capitalize(), f"{int(tally.get(k, 0))}", LABEL[k].split(" ", 1)[0])
    s.table("Metric coverage", df.drop(columns="_state"), csv="coverage", limit=None)
    src_rows = [{"source": k, "status": LABEL[v[0]], "records": v[2], "detail": v[1]} for k, v in sorted(states.items())]
    s.table("Data sources", pd.DataFrame(src_rows), csv="coverage_sources", limit=None)
    return s
