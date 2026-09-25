#!/usr/bin/env python3
"""Generate tests/fixtures/sample_cache.json: synthetic records in the Reports API shape.

Deterministic, so the committed fixture and the test expectations stay in sync.
To test against your own tenant's data shape instead, use tools/anonymise_cache.py.
"""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sample_cache.json"

APPS = ["gmail", "docs", "sheets", "slides", "drive", "chat", "meet", "gemini_app"]
FEATURES = ["side_panel", "help_me_write", "summarize", "help_me_organize",
            "help_me_create", "take_notes", "chat_prompt", "smart_compose"]
ACTIONS = ["generate", "summarize", "refine", "ask", "insert"]

# (email, name, ou, suspended, events)
USERS = [
    ("user1@example.com", "User One", "/Sales", False, 30),
    ("user2@example.com", "User Two", "/Sales", False, 12),
    ("user3@example.com", "User Three", "/Sales/APAC", False, 6),
    ("user4@example.com", "User Four", "/Engineering", False, 4),
    ("user5@example.com", "User Five", "/Engineering", False, 1),
    ("user6@example.com", "User Six", "/Engineering", False, 0),
    ("user7@example.com", "User Seven", "/Sales/APAC", False, 0),
    ("user8@example.com", "User Eight", "/", False, 0),
    ("user9@example.com", "User Nine", "/Sales", True, 3),     # suspended, has usage
    ("user10@example.com", "User Ten", "/Engineering", True, 0),  # suspended, no usage
]
NOT_IN_DIRECTORY = ("user11@example.com", 2)
END = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def main():
    rng = random.Random(42)
    activities = []
    seq = 0

    def add(email, app, feature, action, when, name="feature_utilization", extra=None):
        nonlocal seq
        seq += 1
        params = [
            {"name": "app_name", "value": app},
            {"name": "action", "value": action},
            {"name": "feature_source", "value": feature},
            {"name": "event_category", "value": "generative_ai"},
        ]
        params += extra or []
        activities.append({
            "kind": "admin#reports#activity",
            "id": {"time": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                   "uniqueQualifier": str(1000 + seq),
                   "applicationName": "gemini_in_workspace_apps",
                   "customerId": "C0example"},
            "etag": f"\"etag{seq}\"",
            "actor": {"email": email, "profileId": str(9000 + seq)},
            "ipAddress": f"203.0.113.{seq % 250}",
            "events": [{"type": "ai_usage_event", "name": name, "parameters": params}],
        })

    for email, _, _, _, n in USERS:
        for i in range(n):
            app = APPS[(i + len(email)) % len(APPS)]
            add(email, app, rng.choice(FEATURES), rng.choice(ACTIONS),
                END - timedelta(days=rng.randrange(0, 27), hours=rng.randrange(0, 23)))
    for i in range(NOT_IN_DIRECTORY[1]):
        add(NOT_IN_DIRECTORY[0], "docs", "summarize", "summarize", END - timedelta(days=i + 1))

    # Shape quirks the transform must tolerate.
    add("user2@example.com", "GMAIL", "help_me_write", "generate", END - timedelta(hours=2),
        extra=[{"name": "surface_id", "value": "compose"},
               {"name": "tags", "multiValue": ["a", "b"]}])                     # unknown params
    add("user4@example.com", "some_new_app", "chat_prompt", "ask", END - timedelta(hours=3))
    add("user1@example.com", "gmail", "help_me_write", "generate", END - timedelta(hours=4),
        name="feature_feedback")                                               # not a usage event
    # One activity carrying two events.
    activities[0]["events"].append({"type": "ai_usage_event", "name": "feature_utilization",
                                    "parameters": [{"name": "app_name", "value": "docs"},
                                                   {"name": "feature_source", "value": "summarize"},
                                                   {"name": "action", "value": "summarize"}]})

    activities.sort(key=lambda a: a["id"]["time"], reverse=True)  # API returns newest first
    users = [{"primaryEmail": e, "name": {"fullName": nm}, "orgUnitPath": ou, "suspended": s}
             for e, nm, ou, s, _ in USERS]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "fetched_at": END.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "start_time": (END - timedelta(days=28)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "end_time": END.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "activities": activities,
        "users": users,
    }, indent=1))
    print(f"wrote {OUT} ({len(activities)} activities)")


if __name__ == "__main__":
    main()
