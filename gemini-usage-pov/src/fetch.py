"""Talk to the Admin SDK: Reports API (Gemini audit events) and Directory API (users).

Kept free of any transform logic so tests can run on saved JSON without credentials.
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

GEMINI_APPLICATION = "gemini_in_workspace_apps"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 7


def build_services(creds):
    from googleapiclient.discovery import build

    reports = build("admin", "reports_v1", credentials=creds, cache_discovery=False)
    directory = build("admin", "directory_v1", credentials=creds, cache_discovery=False)
    return reports, directory


def execute_with_retry(request, sleep=time.sleep):
    """Execute a googleapiclient request, backing off on 429 / 5xx."""
    from googleapiclient.errors import HttpError

    for attempt in range(MAX_ATTEMPTS):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            status = int(status) if status is not None else None
            if status not in RETRY_STATUSES or attempt == MAX_ATTEMPTS - 1:
                raise
            delay = min(2 ** attempt, 60) + random.random()
            print(f"  API returned {status}; retrying in {delay:.1f}s...")
            sleep(delay)
        except (ConnectionError, TimeoutError, OSError):
            if attempt == MAX_ATTEMPTS - 1:
                raise
            sleep(min(2 ** attempt, 60) + random.random())


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def window(days: float, now: datetime | None = None) -> tuple[str, str]:
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return rfc3339(start), rfc3339(end)


def fetch_activities(reports, start_time: str, end_time: str,
                     customer_id: str | None = None, max_pages: int | None = None,
                     progress=print) -> list[dict]:
    """All gemini_in_workspace_apps activities in the window, following nextPageToken."""
    items: list[dict] = []
    page_token = None
    pages = 0
    while True:
        kwargs = dict(
            userKey="all",
            applicationName=GEMINI_APPLICATION,
            startTime=start_time,
            endTime=end_time,
            maxResults=1000,
        )
        if customer_id and customer_id != "my_customer":
            kwargs["customerId"] = customer_id
        if page_token:
            kwargs["pageToken"] = page_token
        resp = execute_with_retry(reports.activities().list(**kwargs))
        batch = resp.get("items", [])
        items.extend(batch)
        pages += 1
        if progress:
            progress(f"  page {pages}: {len(batch)} records (total {len(items)})")
        page_token = resp.get("nextPageToken")
        if not page_token or (max_pages and pages >= max_pages):
            return items


def fetch_users(directory, customer_id: str = "my_customer", progress=print) -> list[dict]:
    """Every user in the tenant (email, name, OU, suspended)."""
    users: list[dict] = []
    page_token = None
    fields = ("nextPageToken,users(primaryEmail,name/fullName,orgUnitPath,"
              "suspended,archived,isAdmin)")
    while True:
        kwargs = dict(customer=customer_id or "my_customer", maxResults=500,
                      orderBy="email", projection="basic", fields=fields)
        if page_token:
            kwargs["pageToken"] = page_token
        resp = execute_with_retry(directory.users().list(**kwargs))
        users.extend(resp.get("users", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            if progress:
                progress(f"  {len(users)} users in directory")
            return users


def save_cache(path: Path, activities: list[dict], users: list[dict],
               start_time: str, end_time: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": rfc3339(datetime.now(timezone.utc)),
        "start_time": start_time,
        "end_time": end_time,
        "activities": activities,
        "users": users,
    }
    path.write_text(json.dumps(payload, indent=1))


def load_cache(path: Path) -> dict:
    return json.loads(Path(path).read_text())
