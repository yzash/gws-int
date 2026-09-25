"""Test-only: synthetic records shaped like Google's API responses for every optional source.

Used by tests/test_sections.py so each dashboard section can be exercised offline.
The users match tests/fixtures/sample_cache.json (user1..user10@example.com).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

END = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
USERS = [f"user{i}@example.com" for i in range(1, 11)]
TEACHERS = USERS[:3]
STUDENTS = USERS[3:8]


def _t(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class _Log:
    def __init__(self, app):
        self.app, self.items, self.n = app, [], 0

    def add(self, email, name, when, params=None, etype="", ip="203.0.113.10"):
        self.n += 1
        ps = []
        for k, v in (params or {}).items():
            if isinstance(v, bool):
                ps.append({"name": k, "boolValue": v})
            elif isinstance(v, int):
                ps.append({"name": k, "intValue": str(v)})
            elif isinstance(v, list):
                ps.append({"name": k, "multiValue": v})
            else:
                ps.append({"name": k, "value": v})
        act = {"id": {"time": _t(when), "uniqueQualifier": str(self.n), "applicationName": self.app,
                      "customerId": "C0example"},
               "actor": {"email": email} if email else {},
               "ipAddress": ip, "events": [{"type": etype or self.app, "name": name, "parameters": ps}]}
        self.items.append(act)


def build_extra(seed: int = 7) -> dict:
    rng = random.Random(seed)
    when = lambda: END - timedelta(days=rng.randrange(0, 27), hours=rng.randrange(0, 23))  # noqa: E731
    logs = {a: _Log(a) for a in ("drive", "meet", "chat", "calendar", "login", "token", "classroom",
                                 "keep", "workspace_studio", "chrome")}

    d = logs["drive"]
    for i in range(80):
        u = USERS[i % 8]
        doc = f"doc{i % 25}"
        d.add(u, ["create", "edit", "view", "view", "edit", "trash"][i % 6], when(),
              {"doc_id": doc, "doc_title": f"Lesson {i % 25}", "doc_type": ["document", "spreadsheet", "presentation"][i % 3],
               "visibility": ["private", "shared_internally", "people_with_link"][i % 3], "owner": u,
               "owner_is_shared_drive": i % 4 == 0, "primary_event": True})
    d.add("user1@example.com", "change_user_access", when(), {"doc_id": "doc1", "doc_title": "Lesson 1",
          "target_user": "user2@example.com", "visibility": "shared_internally"})
    d.add("user1@example.com", "change_user_access", when(), {"doc_id": "doc2", "doc_title": "Lesson 2",
          "target_user": "partner@outside.org", "visibility": "shared_externally"})
    d.add("user2@example.com", "change_document_visibility", when(), {"doc_id": "doc3", "doc_title": "Lesson 3",
          "old_visibility": "private", "visibility": "people_with_link"})
    for u in ("user3@example.com", "user4@example.com", "user5@example.com"):
        d.add(u, "copy", when(), {"doc_id": "copy-" + u, "doc_title": "Copy of Lesson Template", "doc_type": "document"})
    d.add("user2@example.com", "create_comment", when(), {"doc_id": "doc4", "doc_title": "Lesson 4"})

    m = logs["meet"]
    for c in range(6):
        size = [2, 3, 6, 12, 4, 2][c]
        for k in range(size):
            email = USERS[k % 10] if k < 8 else ""
            params = {"conference_id": f"conf{c}", "meeting_code": f"abc-{c}", "duration_seconds": 1200 + 60 * k,
                      "device_type": ["web", "android", "ios", "meet_hardware"][k % 4], "endpoint_id": f"ep{c}-{k}",
                      "is_external": k >= 8, "organizer_email": USERS[c % 3], "location_country": "SG",
                      "location_region": "Singapore", "identifier_type": "email_address" if email else "phone_number",
                      "identifier": email or "+6500000000"}
            m.add(email, "call_ended", END - timedelta(days=c + 1), params, "call")
    for ev in ("poll_created", "poll_answered", "question_created", "hand_raised", "presentation_started"):
        m.add("user1@example.com", ev, when(), {"conference_id": "conf1", "meeting_code": "abc-1"}, "conference_action")

    ch = logs["chat"]
    for i in range(40):
        ch.add(USERS[i % 6], ["message_posted", "message_posted", "reaction_added", "conversation_read"][i % 4], when(),
               {"room_id": f"room{i % 3}", "conversation_type": "SPACE"})
    ch.add("user1@example.com", "room_created", when(), {"room_id": "room9"})

    ca = logs["calendar"]
    for i in range(15):
        ca.add(USERS[i % 5], ["create_event", "change_event_title", "add_event_guest"][i % 3], when(),
               {"event_id": f"e{i}", "event_guest": "guest@outside.org" if i % 2 else "user2@example.com"})
    ca.add("user2@example.com", "create_appointment_schedule", when(), {"appointment_schedule_title": "Office hours"})

    lo = logs["login"]
    for i, u in enumerate(USERS[:8]):
        lo.add(u, "login_success", END - timedelta(days=i), {"login_type": "google_password"}, "login")
    lo.add("user3@example.com", "login_failure", when(), {"login_failure_type": "login_failure_invalid_password"}, "login")
    lo.add("user4@example.com", "suspicious_login", when(), {"is_suspicious": True}, "account_warning")
    lo.add("user5@example.com", "passkey_enrolled", when(), {}, "account_warning")
    lo.add("user2@example.com", "login_challenge", when(), {"login_challenge_method": ["passkey"]}, "login")

    tk = logs["token"]
    for u, app in (("user1@example.com", "ChatGPT"), ("user2@example.com", "Claude"),
                   ("user3@example.com", "Zoom"), ("user1@example.com", "Zoom")):
        tk.add(u, "authorize", when(), {"app_name": app, "client_id": app.lower(), "client_type": "WEB",
                                        "scope": ["https://www.googleapis.com/auth/drive.readonly", "openid"]}, "auth")
    tk.add("user1@example.com", "revoke", when(), {"app_name": "Zoom", "client_id": "zoom"}, "auth")

    cl = logs["classroom"]
    t0 = END - timedelta(days=10)
    for ci, teacher in enumerate(TEACHERS):
        course = {"course_id": f"c{ci}", "course_title": f"Class {ci}"}
        cl.add(teacher, "created_course", t0 - timedelta(days=5), course)
        cl.add(teacher, "published_announcement", t0, {**course, "post_id": f"a{ci}"})
        post = {**course, "post_id": f"w{ci}", "course_work_title": f"Essay {ci}", "course_work_type": "ASSIGNMENT"}
        cl.add(teacher, "published_course_work", t0, post)
        cl.add(teacher, "created_rubric_for_course_work", t0, post)
        for si, s in enumerate(STUDENTS):
            late = si == 0
            cl.add(s, "changed_submission_state", t0 + timedelta(hours=10 + si),
                   {**post, "submission_state": "TURNED_IN", "is_late": late, "impacted_users": s})
            cl.add(teacher, "set_grade", t0 + timedelta(hours=34 + si), {**post, "grade": "90", "impacted_users": s})
            if si < 2:
                cl.add(teacher, "commented_submission_private", t0 + timedelta(hours=35), {**post, "impacted_users": s})
                cl.add(s, "commented_course_work", t0 + timedelta(hours=36), post)
        cl.add(teacher, "created_add_on_attachment", t0, {**post, "add_on_title": "Quizlet"})

    logs["keep"].add("user1@example.com", "created_note", when(), {"note_name": "n1"})
    logs["workspace_studio"].add("user2@example.com", "flow_created", when(), {"flow_id": "f1"})
    logs["chrome"].add("user3@example.com", "CHROME_OS_LOGIN_EVENT", when(), {"DEVICE_PLATFORM": "ChromeOS"})
    logs["chrome"].add("user4@example.com", "PASSWORD_REUSE", when(), {"DEVICE_PLATFORM": "Windows", "URL": "https://phish.example"})

    usage_customer, usage_users = [], []
    for k in range(1, 22):
        date = (END - timedelta(days=k + 2)).date().isoformat()
        params = [{"name": "accounts:num_users", "intValue": "10"},
                  {"name": "accounts:num_30day_logins", "intValue": "9"},
                  {"name": "accounts:num_users_2sv_enrolled", "intValue": "6"}]
        for app, base in (("gmail", 8), ("drive", 7), ("calendar", 6), ("classroom", 4)):
            for n, mult in (("1", 1), ("7", 1.2), ("30", 1.3)):
                params.append({"name": f"{app}:num_{n}day_active_users", "intValue": str(min(10, int(base * mult) - k % 3))})
        usage_customer.append({"date": date, "parameters": params})
        for i, u in enumerate(USERS):
            usage_users.append({"date": date, "email": u, "parameters": [
                {"name": "accounts:last_login_time", "datetimeValue": _t(END - timedelta(days=(i * 5)))},
                {"name": "gmail:last_interaction_time", "datetimeValue": _t(END - timedelta(days=(i * 4)))},
                {"name": "gmail:num_emails_sent", "intValue": str(i + 1)},
                {"name": "gmail:num_emails_received", "intValue": str(3 * i + 2)},
                {"name": "drive:num_items_created", "intValue": str(i % 3)},
                {"name": "accounts:is_2sv_enrolled", "boolValue": i % 2 == 0}]})

    licences = {"assignments": [{"userId": u, "productId": "Google-Apps", "productName": "Google Workspace",
                                 "skuId": "1010020020", "skuName": "Google Workspace Business Standard"} for u in USERS]
                + [{"userId": u, "productId": "101047", "productName": "Gemini", "skuId": "1010470003",
                    "skuName": "Gemini Business"} for u in ("user5@example.com", "user6@example.com")],
                "skipped_products": {"101031": "HTTP 400: Invalid productId"}}
    chrome_mgmt = {"installed_apps": [
        {"appId": "ext1", "displayName": "ChatGPT for Chrome", "appType": "EXTENSION", "browserDeviceCount": 4, "osUserCount": 3},
        {"appId": "ext2", "displayName": "Grammarly", "appType": "EXTENSION", "browserDeviceCount": 6, "osUserCount": 5},
        {"appId": "ext3", "displayName": "Google Docs Offline", "appType": "EXTENSION", "browserDeviceCount": 9, "osUserCount": 9}],
        "telemetry_users": [{"userEmail": "user3@example.com", "userDevice": [{
            "deviceId": "dev1",
            "appReport": [{"reportTime": _t(END), "usageData": [{"appId": "chrome", "appType": "BROWSER", "runningDuration": "7200s"},
                                                               {"appId": "docs", "appType": "WEB", "runningDuration": "3600s"}]}],
            "deviceActivityReport": [{"reportTime": _t(END), "deviceActivityState": s} for s in ("ACTIVE", "ACTIVE", "IDLE", "LOCKED")]}]}],
        "errors": {}}

    sources = {a: {"items": l.items, "start": _t(END - timedelta(days=28)), "end": _t(END)} for a, l in logs.items()}
    status = {a: f"ok: {len(l.items)} records" for a, l in logs.items()}
    status.update({"usage_reports": "ok: 21 days", "licensing": "ok: 12 assignments", "chrome_mgmt": "ok"})
    return {"sources": sources, "status": status, "usage_customer": usage_customer, "usage_users": usage_users,
            "licences": licences, "chrome_mgmt": chrome_mgmt}
