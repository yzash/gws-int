"""Workspace activity from the audit logs: Drive, Meet, Chat, Calendar, Classroom, Login,
OAuth tokens, Chrome, Keep, Workspace Studio, Gmail. Same read-only audit scope as Gemini."""
from __future__ import annotations

import re
from collections import Counter

import numpy as np
import pandas as pd

from .common import (Section, active_users_by_window, after_hours_mask, counts, domain,
                     heatmap, ip_matcher, p, pct, per_user_event_counts, truthy)

APP_TITLES = {
    "drive": "Drive, Docs, Sheets, Slides, Forms", "meet": "Google Meet", "chat": "Google Chat",
    "calendar": "Google Calendar", "login": "Sign-in and security", "token": "Third-party app access (OAuth)",
    "classroom": "Google Classroom", "keep": "Google Keep", "workspace_studio": "Workspace Studio (Flows)",
    "chrome": "Chrome browser and ChromeOS", "gmail": "Gmail (sent mail log)",
}


def overview_section(frames: dict, status: dict, users: pd.DataFrame, end: pd.Timestamp) -> Section:
    s = Section("activity_overview", "Workspace activity overview (audit logs)",
                intro="Distinct users with at least one audit event per app, over the last 1, 7 and 30 days "
                      "of the window, plus each person's last activity per app.")
    rows, last = [], {}
    for app, st in status.items():
        df = frames.get(app)
        if df is None:
            rows.append({"app": app, "status": st, "events": 0})
            continue
        au = active_users_by_window(df, end)
        rows.append({"app": app, "status": "ok", "events": len(df), "active_users_1d": au[1],
                     "active_users_7d": au[7], "active_users_30d": au[30],
                     "newest_event": df["time"].max(), "top_events": ", ".join(
                         f"{k} ({v})" for k, v in df["name"].value_counts().head(4).items())})
        if len(df):
            last[app] = df[df["email"] != ""].groupby("email")["time"].max()
    table = pd.DataFrame(rows)
    s.table("Apps", table, csv="activity_apps", limit=20)
    if last:
        lt = pd.DataFrame(last)
        lt.index.name = "user_email"
        lt = users[["user_email", "ou_path"]].merge(lt.reset_index(), on="user_email", how="left")
        lt["last_any_activity"] = lt[[c for c in last]].max(axis=1)
        inactive = lt[lt["last_any_activity"].isna() | (lt["last_any_activity"] < end - pd.Timedelta(days=30))]
        s.tile("Users with no audit activity in 30 days", f"{len(inactive)}", f"of {len(lt)} in directory")
        s.table("Last activity per user per app", lt.sort_values("last_any_activity", ascending=False),
                csv="last_activity_by_app", limit=10)
        s.table("Accounts with no activity for 30+ days", inactive[["user_email", "ou_path", "last_any_activity"]],
                csv="inactive_30d", limit=15)
    if not frames:
        s.status, s.reason = "disabled", "Activity logs module is off (modules.activity_logs)."
    return s


def drive_section(df: pd.DataFrame, users: pd.DataFrame, cfg: dict) -> Section:
    s = Section("drive", APP_TITLES["drive"])
    if df.empty:
        s.status, s.reason = "empty", "No Drive events in the window."
        return s
    tenant_domains = {domain(e) for e in users["user_email"]}
    df = df.assign(doc_type=p(df, "doc_type", ""), visibility=p(df, "visibility", ""),
                   doc_id=p(df, "doc_id", ""), title=p(df, "doc_title", ""),
                   primary=p(df, "primary_event", True))
    per_user = per_user_event_counts(df, {"created": ["create", "upload"], "edited": ["edit"],
                                          "viewed": ["view", "preview"], "trashed": ["trash", "delete"],
                                          "copied": ["copy"], "downloaded": ["download"],
                                          "comments": [n for n in df["name"].unique() if "comment" in n],
                                          "suggestions": [n for n in df["name"].unique() if "suggest" in n],
                                          "shared": ["change_user_access", "change_document_visibility",
                                                     "change_document_access_scope", "change_acl_editors"]})
    creators = per_user[(per_user["created"] + per_user["edited"]) > 0]
    consumers = per_user[(per_user["created"] + per_user["edited"] == 0) & (per_user["viewed"] > 0)]
    s.tile("Files created", f"{int(per_user['created'].sum()):,}")
    s.tile("Files edited", f"{int(per_user['edited'].sum()):,}")
    s.tile("Creators : consumers", f"{len(creators)} : {len(consumers)}", "users who create/edit vs only view")
    s.tile("Comments", f"{int(per_user['comments'].sum()):,}", "Drive comment events")
    daily = (df[df["name"].isin(["create", "upload", "edit", "view", "trash"])]
             .assign(date=df["time"].dt.tz_convert(cfg["timezone"]).dt.date)
             .pivot_table(index=["date", "email"], columns="name", values="doc_id", aggfunc="size", fill_value=0))
    s.table("Per user", per_user, csv="drive_by_user", limit=10)
    if len(daily):
        s.table("Per user per day", daily.reset_index(), csv="drive_daily_by_user", limit=0)
    created = df[df["name"].isin(["create", "upload"])]
    if len(created):
        s.chart("Files created by type", created["doc_type"].value_counts().head(10), "files")
    with_vis = df[(df["doc_id"] != "") & (df["visibility"].astype(str) != "")]
    latest_vis = with_vis.sort_values("time").groupby("doc_id")["visibility"].last()
    if len(latest_vis):
        s.chart("Visibility of files touched in the window", latest_vis.value_counts(), "files")
    share = df[df["name"].isin(["change_user_access", "change_document_visibility",
                                "change_document_access_scope", "change_acl_editors"])].copy()
    if len(share):
        share["target"] = p(share, "target_user", "")
        share["new_visibility"] = share["visibility"]
        share["old_visibility"] = p(share, "old_visibility", "")
        share["external"] = share["target"].apply(lambda t: bool(t) and domain(t) not in tenant_domains) | \
            share["new_visibility"].isin(["shared_externally", "public_on_the_web", "people_with_link"])
        ext = share[share["external"]]
        s.tile("External / public sharing events", f"{len(ext):,}")
        s.table("External or public sharing", ext[["time", "email", "title", "target", "old_visibility", "new_visibility"]]
                .rename(columns={"email": "user_email"}), csv="drive_external_sharing", limit=10)
        trans = share[share["old_visibility"] != ""].groupby(["old_visibility", "new_visibility"]).size()
        if len(trans):
            s.table("Link-sharing changes (from → to)", trans.rename("events").reset_index(), csv="drive_visibility_changes")
        internal = share[(share["target"] != "") & ~share["external"]]
        if len(internal):
            ou = dict(zip(users["user_email"], users["ou_path"]))
            internal = internal.assign(actor_ou=internal["email"].map(ou), target_ou=internal["target"].str.lower().map(ou))
            collab = internal.groupby("email").agg(colleagues_shared_with=("target", "nunique"),
                                                   cross_group_shares=("target_ou", lambda s_: 0))
            collab["cross_group_shares"] = internal[internal["actor_ou"] != internal["target_ou"]].groupby("email").size()
            collab = collab.fillna(0).astype(int).reset_index().rename(columns={"email": "user_email"})
            s.table("Collaboration: colleagues shared with, sharing across OUs", collab.sort_values(
                "colleagues_shared_with", ascending=False), csv="drive_collaboration", limit=10)
    copies = df[df["name"] == "copy"]
    if len(copies):
        src = copies["title"].str.replace(r"^(Copy of |Copie de |Kopie von )", "", regex=True)
        reuse = (copies.assign(source=src).groupby("source")
                 .agg(copies=("email", "size"), users=("email", "nunique")).sort_values("copies", ascending=False))
        s.table("Resource reuse: most-copied files", reuse.reset_index(), csv="drive_reuse", limit=10)
    sd = df[p(df, "owner_is_shared_drive", False).apply(lambda v: str(v).lower() == "true")]
    s.notes.append(f"Shared-drive activity: {pct(len(sd), len(df))}% of Drive events are on shared-drive files.")
    return s


def meet_section(df: pd.DataFrame, users: pd.DataFrame, cfg: dict) -> Section:
    s = Section("meet", APP_TITLES["meet"])
    calls = df[df["name"] == "call_ended"].copy() if len(df) else df
    if df.empty or calls.empty:
        s.status, s.reason = "empty", "No Meet calls in the window."
        return s
    calls["minutes"] = pd.to_numeric(p(calls, "duration_seconds", 0), errors="coerce").fillna(0) / 60
    calls["device"] = p(calls, "device_type", "unknown")
    calls["external"] = truthy(p(calls, "is_external", False))
    calls["conf"] = p(calls, "conference_id", "")
    calls["endpoint"] = p(calls, "endpoint_id", "")
    calls["country"] = p(calls, "location_country", "")
    calls["region"] = p(calls, "location_region", "")
    meetings = calls.groupby("conf").agg(participants=("endpoint", "nunique"), minutes=("minutes", "sum"),
                                         has_external=("external", "any"), start=("time", "min"),
                                         organizer=("params", lambda x: x.iloc[0].get("organizer_email", "")))
    s.tile("Meetings", f"{len(meetings):,}")
    s.tile("Participant hours", f"{calls['minutes'].sum() / 60:,.0f}")
    s.tile("Meetings with external guests", f"{int(meetings['has_external'].sum())}",
           f"{pct(meetings['has_external'].sum(), len(meetings))}%")
    size = pd.cut(meetings["participants"], [0, 2, 5, 10, 25, 50, 10**6],
                  labels=["1–2", "3–5", "6–10", "11–25", "26–50", "51+"]).value_counts().sort_index()
    s.chart("Meetings by size (participants)", size, "meetings")
    s.chart("Participant minutes by device", calls.groupby("device")["minutes"].sum().round().sort_values(ascending=False), "min")
    s.chart("Minutes: internal vs external participants",
            calls.groupby(calls["external"].map({True: "external", False: "internal"}))["minutes"].sum().round(), "min")
    per_user = (calls[calls["email"] != ""].groupby("email")
                .agg(meet_hours=("minutes", lambda m: round(m.sum() / 60, 1)), calls=("conf", "nunique"))
                .sort_values("meet_hours", ascending=False).reset_index().rename(columns={"email": "user_email"}))
    s.table("Meet hours per user", per_user, csv="meet_by_user", limit=10)
    features = ["poll_created", "poll_answered", "question_created", "question_responded", "hand_raised",
                "presentation_started", "closed_captions_started", "send_chat_everyone", "invitation_sent",
                "livestream_watched", "broadcast_activity"]
    feat = df[df["name"].isin(features)].groupby("name").agg(events=("email", "size"), users=("email", "nunique"))
    feat = feat.reindex(features).fillna(0).astype(int)
    s.table("Interactive features (polls, Q&A, hand raise, …)", feat.reset_index().rename(columns={"name": "feature"}),
            csv="meet_features", limit=20)
    s.notes.append("Reactions are not in the Meet audit log.")
    geo = calls[calls["country"] != ""].groupby(["country", "region"])["minutes"].sum().round()
    if len(geo):
        s.table("Where people join from (country / region, from Meet)", geo.rename("minutes").reset_index()
                .sort_values("minutes", ascending=False), csv="meet_locations", limit=10)
    s.table("Meetings", meetings.reset_index().sort_values("start", ascending=False), csv="meet_meetings", limit=0)
    return s


def chat_section(df: pd.DataFrame, cfg: dict) -> Section:
    s = Section("chat", APP_TITLES["chat"])
    if df.empty:
        s.status, s.reason = "empty", "No Chat events in the window."
        return s
    per_user = per_user_event_counts(df, {"messages": ["message_posted"], "reactions": ["reaction_added"],
                                          "conversations_read": ["conversation_read"],
                                          "spaces_created": ["room_created"], "dms_started": ["direct_message_started"],
                                          "apps_invoked": ["app_invoked"], "attachments": ["attachment_upload"]})
    for k in ("messages", "reactions", "conversations_read", "spaces_created"):
        s.tile(k.replace("_", " ").capitalize(), f"{int(per_user[k].sum()):,}")
    msgs = df[df["name"] == "message_posted"]
    if len(msgs):
        s.tile("Messages sent outside working hours",
               f"{pct(after_hours_mask(msgs['time'], cfg['timezone'], cfg['work_hours']).sum(), len(msgs))}%")
        s.heatmaps.append({"title": "Chat messages by weekday and hour", "grid": heatmap(msgs["time"], cfg["timezone"])})
    s.table("Per user", per_user, csv="chat_by_user", limit=10)
    return s


def calendar_section(df: pd.DataFrame, users: pd.DataFrame) -> Section:
    s = Section("calendar", APP_TITLES["calendar"])
    if df.empty:
        s.status, s.reason = "empty", "No Calendar events in the window."
        return s
    changed = [n for n in df["name"].unique() if n.startswith("change_event")]
    per_user = per_user_event_counts(df, {"events_created": ["create_event"], "events_changed": changed,
                                          "events_deleted": ["delete_event"], "guests_invited": ["add_event_guest"],
                                          "appointment_schedules": ["create_appointment_schedule",
                                                                    "change_appointment_schedule"]})
    for k in ("events_created", "events_changed", "guests_invited", "appointment_schedules"):
        s.tile(k.replace("_", " ").capitalize(), f"{int(per_user[k].sum()):,}")
    guests = df[df["name"] == "add_event_guest"]
    if len(guests):
        tenant = {domain(e) for e in users["user_email"]}
        ext = p(guests, "event_guest", "").apply(lambda g: bool(g) and domain(g) not in tenant)
        s.tile("External guest invitations", f"{int(ext.sum()):,}")
    s.table("Per user", per_user, csv="calendar_by_user", limit=10)
    return s


def classroom_section(df: pd.DataFrame, gemini_events: pd.DataFrame, end: pd.Timestamp) -> Section:
    s = Section("classroom", APP_TITLES["classroom"])
    if df.empty:
        s.status, s.reason = "empty", "No Classroom events in the window (Classroom may not be used in this tenant)."
        return s
    df = df.assign(course=p(df, "course_id", ""), course_title=p(df, "course_title", ""), post=p(df, "post_id", ""),
                   impacted=p(df, "impacted_users", ""))
    teacher_events = ["published_course_work", "published_announcement", "set_grade", "created_rubric_for_course_work",
                      "scored_rubric", "created_course", "set_draft_grade", "updated_course_work"]
    teachers = set(df[df["name"].isin(teacher_events)]["email"]) - {""}
    t_df = df[df["email"].isin(teachers)]
    for d in (1, 7, 30):
        sub = df[df["time"] > end - pd.Timedelta(days=d)]
        s.tile(f"Active courses ({d}d)", f"{sub['course'][sub['course'] != ''].nunique()}")
    for d in (1, 7, 30):
        sub = t_df[t_df["time"] > end - pd.Timedelta(days=d)]
        s.tile(f"Active teachers ({d}d)", f"{sub['email'].nunique()}")
    classes = t_df.groupby("email")["course"].nunique()
    s.tile("Active classes per teacher", f"{classes.mean():.1f}" if len(classes) else "0")
    posts = df[df["name"].isin(["published_course_work", "published_announcement"])]
    if len(posts):
        week = posts["time"].dt.tz_convert(None).dt.to_period("W").astype(str)
        weekly = posts.groupby([week.rename("week"), posts["name"]]).size().unstack(fill_value=0)
        s.table("Assignments and announcements per week", weekly.reset_index(),
                csv="classroom_weekly", limit=0)
    comments = df[df["name"].str.startswith("commented_")]
    if len(comments):
        role = comments["email"].isin(teachers).map({True: "teacher", False: "student"})
        by = comments.groupby([role.rename("role"), comments["name"].rename("type")]).size()
        s.chart("Comments by role and type",
                [(f"{r} · {t.replace('commented_', '').replace('_', ' ')}", v) for (r, t), v in by.items()], "comments")
    subs = df[df["name"] == "changed_submission_state"].copy()
    subs["state"] = p(subs, "submission_state", "")
    subs["late"] = truthy(p(subs, "is_late", False))
    turned = subs[subs["state"].str.upper().str.contains("TURNED_IN", na=False)]
    if len(turned):
        s.tile("Submissions", f"{len(turned):,}")
        s.tile("Late submission rate", f"{pct(turned['late'].sum(), len(turned))}%")
        per_assign = turned.groupby(["course_title", "post"]).agg(submissions=("email", "nunique"),
                                                                  late=("late", "sum")).reset_index()
        s.table("Submissions per assignment", per_assign.sort_values("submissions", ascending=False),
                csv="classroom_submissions", limit=10)
        by_course = turned.groupby("course_title").agg(submissions=("email", "size"), late_pct=("late", lambda x: pct(x.sum(), len(x))))
        s.table("Late-submission rate by class", by_course.reset_index().sort_values("submissions", ascending=False),
                csv="classroom_late_by_course", limit=10)
        grades = df[df["name"].isin(["set_grade"]) | ((df["name"] == "changed_submission_state") &
                                                      p(df, "submission_state", "").astype(str).str.upper().str.contains("RETURNED"))]
        tt = []
        for _, g in grades.iterrows():
            for student in str(g["impacted"]).replace(",", " ").replace("|", " ").split():
                tt.append({"post": g["post"], "student": student.lower(), "graded": g["time"]})
        if tt:
            gdf = pd.DataFrame(tt).groupby(["post", "student"])["graded"].min().reset_index()
            tin = turned.groupby(["post", "email"])["time"].min().reset_index().rename(columns={"email": "student", "time": "turned_in"})
            j = tin.merge(gdf, on=["post", "student"])
            j = j[j["graded"] >= j["turned_in"]]
            if len(j):
                hours = (j["graded"] - j["turned_in"]).dt.total_seconds() / 3600
                s.tile("Median grading turnaround", f"{hours.median():.1f} h", f"{len(j)} graded submissions")
    rub = df[df["name"].isin(["created_rubric_for_course_work", "scored_rubric"])].groupby("name").size()
    for k, v in rub.items():
        s.tile(k.replace("_", " ").capitalize(), f"{v:,}")
    priv = (df["name"] == "commented_submission_private").sum()
    pub = (df["name"] == "commented_submission_public").sum()
    if len(turned):
        s.tile("Comments per submission", f"{(priv + pub) / max(len(turned), 1):.2f}", f"{priv} private · {pub} public")
    feedback = df["name"].isin(["set_grade", "scored_rubric", "commented_submission_private", "commented_submission_public"]).sum()
    s.tile("Feedback events", f"{feedback:,}")
    addons = df[df["name"].str.contains("add_on", na=False)]
    if len(addons):
        s.table("EdTech add-on use", p(addons, "add_on_title", "").value_counts().rename_axis("add_on")
                .reset_index(name="events"), csv="classroom_addons")
    created = df[df["name"] == "created_course"].groupby("email").size()
    per_teacher = pd.DataFrame({"courses_created": created, "classes_active": classes,
                                "posts": posts.groupby("email").size()}).fillna(0).astype(int)
    per_teacher.index.name = "user_email"
    s.table("Per teacher", per_teacher.reset_index().sort_values("classes_active", ascending=False),
            csv="classroom_by_teacher", limit=10)
    if len(gemini_events):
        gc = gemini_events[gemini_events["app"] == "classroom"]
        s.tile("Gemini in Classroom actions", f"{len(gc):,}", f"{gc['user_email'].nunique()} users")
    s.notes.append("Guardian summaries are not in the Classroom audit log.")
    return s


def login_section(df: pd.DataFrame, users: pd.DataFrame) -> Section:
    s = Section("login", APP_TITLES["login"])
    n = len(users)
    if "isEnrolledIn2Sv" in users.columns:
        s.tile("2-Step Verification enrolled", f"{pct(users['isEnrolledIn2Sv'].fillna(False).astype(bool).sum(), n)}%",
               f"{int(users['isEnrolledIn2Sv'].fillna(False).astype(bool).sum())} of {n} (directory)")
        s.tile("2SV enforced", f"{pct(users['isEnforcedIn2Sv'].fillna(False).astype(bool).sum(), n)}%")
    if df.empty:
        s.status = "empty" if "isEnrolledIn2Sv" not in users.columns else "ok"
        s.reason = "No sign-in events in the window."
        return s
    suspicious = [x for x in df["name"].unique() if "suspicious" in x or "attack" in x or "account_disabled" in x
                  or "risky" in x or "password_leak" in x]
    per_user = per_user_event_counts(df, {"logins": ["login_success"], "failures": ["login_failure"],
                                          "challenges": ["login_challenge"], "suspicious": suspicious,
                                          "passkey_enrolled": ["passkey_enrolled"], "2sv_enrolled": ["2sv_enroll"],
                                          "2sv_disabled": ["2sv_disable"]})
    last_login = df[df["name"] == "login_success"].groupby("email")["time"].max()
    per_user["last_login"] = per_user["user_email"].map(last_login)
    s.tile("Suspicious sign-in events", f"{int(per_user['suspicious'].sum())}")
    s.tile("Passkeys enrolled (window)", f"{int(per_user['passkey_enrolled'].sum())}")
    s.tile("Failed sign-ins", f"{int(per_user['failures'].sum())}")
    lt = p(df[df["name"] == "login_success"], "login_type", "")
    if len(lt):
        s.chart("Sign-in method", lt.value_counts(), "sign-ins")
    ch = p(df[df["name"] == "login_challenge"], "login_challenge_method", "")
    if len(ch):
        s.chart("Challenge method", ch.apply(lambda v: str(v)).value_counts().head(8), "challenges")
    s.table("Per user", per_user, csv="login_by_user", limit=10)
    if suspicious:
        s.table("Suspicious / risky events", df[df["name"].isin(suspicious)][["time", "email", "name"]]
                .rename(columns={"email": "user_email"}), csv="login_suspicious", limit=10)
    return s


def token_section(df: pd.DataFrame, cfg: dict) -> Section:
    s = Section("token", APP_TITLES["token"],
                intro="Apps that people granted access to their Google account. AI tools are flagged by name.")
    if df.empty:
        s.status, s.reason = "empty", "No OAuth grants in the window."
        return s
    df = df.assign(app_name=p(df, "app_name", "").astype(str), client_type=p(df, "client_type", ""),
                   scope=p(df, "scope", ""))
    kw = [k.lower() for k in cfg["ai_app_keywords"]]
    df["is_ai"] = df["app_name"].str.lower().apply(lambda n: any(k in n for k in kw))
    grants = df[df["name"] == "authorize"]
    apps = grants.groupby("app_name").agg(users=("email", "nunique"), grants=("email", "size"),
                                          first_seen=("time", "min"), last_seen=("time", "max"),
                                          ai_tool=("is_ai", "first"), client_type=("client_type", "first"))
    apps["revokes"] = df[df["name"] == "revoke"].groupby("app_name").size()
    apps = apps.fillna({"revokes": 0}).sort_values("users", ascending=False).reset_index()
    ai = apps[apps["ai_tool"]]
    s.tile("Apps granted access", f"{len(apps)}")
    s.tile("AI tools connected", f"{len(ai)}", f"{grants[grants['is_ai']]['email'].nunique()} users")
    s.table("AI tools connected to Google accounts", ai, csv="oauth_ai_apps", limit=15)
    s.table("All third-party apps", apps, csv="oauth_apps", limit=15)
    ai_users = (grants[grants["is_ai"]].groupby(["email", "app_name"])["time"].agg(["min", "max"])
                .rename(columns={"min": "first_grant", "max": "last_grant"}).reset_index().rename(columns={"email": "user_email"}))
    s.table("Who connected AI tools", ai_users, csv="oauth_ai_users", limit=10)
    sc = Counter()
    for v in grants["scope"]:
        for x in str(v).split("|"):
            if x:
                sc[x] += 1
    if sc:
        s.table("Most-granted scopes", pd.DataFrame(sc.most_common(), columns=["scope", "grants"]),
                csv="oauth_scopes", limit=10)
    return s


def chrome_audit_section(df: pd.DataFrame) -> Section:
    s = Section("chrome_audit", APP_TITLES["chrome"])
    if df.empty:
        s.status, s.reason = "empty", "No Chrome audit events (requires managed Chrome browsers / ChromeOS devices)."
        return s
    plat = p(df, "DEVICE_PLATFORM", "")
    if (plat != "").any():
        s.chart("Device platform (Chrome events)", plat[plat != ""].value_counts().head(8), "events")
    s.chart("Chrome events", df["name"].value_counts().head(10), "events")
    logins = df[df["name"].isin(["CHROME_OS_LOGIN_EVENT", "CHROME_OS_LOGIN_LOGOUT_EVENT"])]
    if len(logins):
        s.table("ChromeOS sign-ins per user", logins.groupby("email").agg(sign_ins=("name", "size"), last=("time", "max"))
                .reset_index().rename(columns={"email": "user_email"}).sort_values("sign_ins", ascending=False),
                csv="chrome_signins", limit=10)
    reuse = df[df["name"] == "PASSWORD_REUSE"]
    if len(reuse):
        s.table("Password reuse on sites", reuse.assign(url=p(reuse, "URL", ""))[["time", "email", "url"]]
                .rename(columns={"email": "user_email"}), csv="chrome_password_reuse", limit=10)
    s.notes.append("The Chrome audit log does not record general site visits, so visits to ChatGPT, Claude etc. "
                   "cannot be read through this API (see External AI tools).")
    return s


def generic_section(app: str, df: pd.DataFrame) -> Section:
    s = Section(app, APP_TITLES.get(app, app))
    if df.empty:
        s.status, s.reason = "empty", f"No {app} events in the window."
        return s
    s.tile("Events", f"{len(df):,}")
    s.tile("Users", f"{df['email'][df['email'] != ''].nunique()}")
    s.chart("Events by type", df["name"].value_counts().head(12), "events")
    pu = df[df["email"] != ""].pivot_table(index="email", columns="name", values="time", aggfunc="size", fill_value=0)
    pu.insert(0, "total", pu.sum(axis=1))
    s.table("Per user", pu.reset_index().rename(columns={"email": "user_email"}).sort_values("total", ascending=False),
            csv=f"{app}_by_user", limit=10)
    return s


def gmail_section(df: pd.DataFrame, cfg: dict) -> Section:
    s = Section("gmail", APP_TITLES["gmail"])
    if df.empty:
        s.status, s.reason = "empty", "No sent-mail events (enable gmail_sent_log; needs an edition with the Gmail log)."
        return s
    s.tile("Emails sent", f"{len(df):,}")
    s.tile("Sent outside working hours", f"{pct(after_hours_mask(df['time'], cfg['timezone'], cfg['work_hours']).sum(), len(df))}%")
    s.heatmaps.append({"title": "Emails sent by weekday and hour", "grid": heatmap(df["time"], cfg["timezone"])})
    per = df.groupby("email").agg(sent=("name", "size"),
                                  after_hours=("time", lambda t: int(after_hours_mask(t, cfg["timezone"], cfg["work_hours"]).sum())))
    s.table("Per user", per.reset_index().rename(columns={"email": "user_email"}).sort_values("sent", ascending=False),
            csv="gmail_sent_by_user", limit=10)
    return s


def context_section(frames: dict, gemini: pd.DataFrame, cfg: dict) -> Section:
    s = Section("context", "Time, device and network patterns")
    all_ts = [f["time"] for f in frames.values() if len(f)]
    if len(gemini):
        all_ts.append(gemini["timestamp"])
    if not all_ts:
        s.status, s.reason = "empty", "No activity data."
        return s
    ts = pd.concat(all_ts)
    s.heatmaps.append({"title": "All Workspace activity by weekday and hour", "grid": heatmap(ts, cfg["timezone"])})
    rows = []
    for app, f in list(frames.items()) + ([("gemini", gemini.rename(columns={"timestamp": "time"}))] if len(gemini) else []):
        if len(f):
            rows.append({"app": app, "events": len(f),
                         "outside_working_hours_pct": pct(after_hours_mask(f["time"], cfg["timezone"], cfg["work_hours"]).sum(), len(f))})
    s.table("After-hours share by app", pd.DataFrame(rows), csv="after_hours_by_app")
    match, has_ranges = ip_matcher(cfg.get("office_ip_ranges"))
    if has_ranges:
        rows = []
        for app, f in frames.items():
            ips = f["ip"][f["ip"] != ""]
            if len(ips):
                rows.append({"app": app, "events_with_ip": len(ips), "from_office_network_pct": pct(ips.apply(match).sum(), len(ips))})
        s.table("Activity from office / school networks", pd.DataFrame(rows), csv="network_share")
    else:
        s.notes.append("Set office_ip_ranges (CIDR list) in config.yaml to measure office/school network vs home.")
    dev = []
    if "meet" in frames and len(frames["meet"]):
        m = frames["meet"][frames["meet"]["name"] == "call_ended"]
        dev += [("Meet", d) for d in p(m, "device_type", "unknown")]
    if "chrome" in frames and len(frames["chrome"]):
        dev += [("Chrome", d) for d in p(frames["chrome"], "DEVICE_PLATFORM", "") if d]
    if dev:
        dd = pd.DataFrame(dev, columns=["source", "device"]).value_counts().rename("events").reset_index()
        s.table("Device mix", dd, csv="device_mix")
    s.notes.append("Regional patterns come from Meet join locations; IP geolocation is not done "
                   "(it would need an external lookup service).")
    return s
