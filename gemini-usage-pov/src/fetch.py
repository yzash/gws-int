"""Talk to Google: Reports API (audit logs, usage reports), Directory, Licensing, Chrome.

Kept free of any transform logic so tests can run on saved JSON without credentials.
Every optional source fails soft: an unavailable source is recorded with the reason
so the coverage report can show what is and isn't extractable in this tenant.
"""
from __future__ import annotations

import gzip
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

GEMINI_APPLICATION = "gemini_in_workspace_apps"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 7


class SourceUnavailable(Exception):
    """A data source could not be read (not licensed, not enabled, no permission)."""


def build_services(creds):
    from googleapiclient.discovery import build

    reports = build("admin", "reports_v1", credentials=creds, cache_discovery=False)
    directory = build("admin", "directory_v1", credentials=creds, cache_discovery=False)
    return reports, directory


def build_optional_services(creds, modules: dict) -> dict:
    from googleapiclient.discovery import build

    services = {}
    if modules.get("licensing"):
        services["licensing"] = build("licensing", "v1", credentials=creds, cache_discovery=False)
    if modules.get("chrome"):
        services["chrome"] = build("chromemanagement", "v1", credentials=creds, cache_discovery=False)
    return services


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


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def window(days: float, now: datetime | None = None) -> tuple[str, str]:
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return rfc3339(start), rfc3339(end)


def http_reason(e) -> str:
    try:
        body = json.loads(e.content.decode())
        return body.get("error", {}).get("message") or str(e)
    except Exception:
        return str(e)


def fetch_activities(reports, start_time: str, end_time: str,
                     customer_id: str | None = None, max_pages: int | None = None,
                     progress=print, application: str = GEMINI_APPLICATION,
                     event_name: str | None = None, filters: str | None = None) -> list[dict]:
    """All activities for one audit application in the window, following nextPageToken."""
    items: list[dict] = []
    page_token = None
    pages = 0
    while True:
        kwargs = dict(
            userKey="all",
            applicationName=application,
            startTime=start_time,
            endTime=end_time,
            maxResults=1000,
        )
        if event_name:
            kwargs["eventName"] = event_name
        if filters:
            kwargs["filters"] = filters
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


def fetch_users(directory, customer_id: str = "my_customer", progress=print,
                custom_fields: list[str] | None = None) -> list[dict]:
    """Every user in the tenant (email, name, OU, suspended, 2SV, custom fields)."""
    users: list[dict] = []
    page_token = None
    fields = ("nextPageToken,users(primaryEmail,name/fullName,orgUnitPath,suspended,archived,"
              "isAdmin,customerId,isEnrolledIn2Sv,isEnforcedIn2Sv,creationTime,lastLoginTime"
              + (",customSchemas" if custom_fields else "") + ")")
    while True:
        kwargs = dict(customer=customer_id or "my_customer", maxResults=500,
                      orderBy="email", fields=fields)
        if custom_fields:
            kwargs["projection"] = "custom"
            kwargs["customFieldMask"] = ",".join(sorted({f.split(".")[0] for f in custom_fields}))
        else:
            kwargs["projection"] = "basic"
        if page_token:
            kwargs["pageToken"] = page_token
        resp = execute_with_retry(directory.users().list(**kwargs))
        users.extend(resp.get("users", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            if progress:
                progress(f"  {len(users)} users in directory")
            return users


# ------------------------------------------------------------ audit logs
def _chunks(start_time: str, end_time: str, max_days: int):
    s, e = parse_time(start_time), parse_time(end_time)
    while s < e:
        n = min(e, s + timedelta(days=max_days))
        yield rfc3339(s), rfc3339(n)
        s = n


def fetch_audit_app(reports, app: str, start_time: str, end_time: str,
                    customer_id: str | None = None, progress=print) -> list[dict]:
    """Audit events for one Workspace application (drive, meet, chat, ...)."""
    from googleapiclient.errors import HttpError

    def quiet(msg):  # only report every 25th page for big logs
        n = msg.strip().split()[1].rstrip(":") if msg.strip().startswith("page") else ""
        if progress and n.isdigit() and int(n) % 25 == 0:
            progress(f"  {app}: {msg.strip()}")

    try:
        if app == "token":
            # 'activity' events are every API call and can be enormous; grants are what matter.
            items = []
            for ev in ("authorize", "revoke"):
                items += fetch_activities(reports, start_time, end_time, customer_id,
                                          progress=quiet, application=app, event_name=ev)
            return items
        if app == "gmail":
            # Gmail log requests may span at most 30 days; only 'message sent' events are kept.
            items = []
            for s, e in _chunks(start_time, end_time, 29):
                items += fetch_activities(reports, s, e, customer_id, progress=quiet,
                                          application=app, filters="event_info.mail_event_type==1")
            return items
        return fetch_activities(reports, start_time, end_time, customer_id,
                                progress=quiet, application=app)
    except HttpError as e:
        raise SourceUnavailable(f"HTTP {e.resp.status}: {http_reason(e)}") from e


# ------------------------------------------------------------ usage reports
def _usage_dates(end_time: str, days: int) -> list[str]:
    end = parse_time(end_time).date()
    return [(end - timedelta(days=i)).isoformat() for i in range(1, days + 1)]


def _not_yet_available(e) -> bool:
    text = http_reason(e).lower()
    return "not yet available" in text or "later than" in text or "data for dates" in text


def fetch_customer_usage(reports, end_time: str, days: int, progress=print) -> list[dict]:
    """Domain-level usage report (active users per app, logins, ...) for each day."""
    from googleapiclient.errors import HttpError

    out = []
    for d in _usage_dates(end_time, days):
        try:
            resp = execute_with_retry(reports.customerUsageReports().get(date=d))
        except HttpError as e:
            if _not_yet_available(e):
                continue
            raise SourceUnavailable(f"HTTP {e.resp.status}: {http_reason(e)}") from e
        for r in resp.get("usageReports", []):
            if r.get("parameters"):
                out.append({"date": r.get("date", d), "parameters": r["parameters"]})
    if progress:
        progress(f"  usage reports: {len(out)} days of domain data")
    return out


def fetch_user_usage(reports, end_time: str, days: int, progress=print) -> list[dict]:
    """Per-user usage report rows for each day in the window."""
    from googleapiclient.errors import HttpError

    out = []
    for d in _usage_dates(end_time, days):
        token = None
        while True:
            kwargs = dict(userKey="all", date=d, maxResults=1000)
            if token:
                kwargs["pageToken"] = token
            try:
                resp = execute_with_retry(reports.userUsageReport().get(**kwargs))
            except HttpError as e:
                if _not_yet_available(e):
                    break
                raise SourceUnavailable(f"HTTP {e.resp.status}: {http_reason(e)}") from e
            for r in resp.get("usageReports", []):
                out.append({"date": r.get("date", d),
                            "email": (r.get("entity") or {}).get("userEmail", ""),
                            "parameters": r.get("parameters", [])})
            token = resp.get("nextPageToken")
            if not token:
                break
    if progress:
        progress(f"  usage reports: {len(out)} user-day rows")
    return out


# ------------------------------------------------------------ licensing / chrome
def fetch_licences(licensing, customer_id: str, products: list[str], progress=print) -> dict:
    """License assignments per product. Products the tenant doesn't have are skipped."""
    from googleapiclient.errors import HttpError

    out, skipped = [], {}
    for product in products:
        token = None
        try:
            while True:
                kwargs = dict(productId=product, customerId=customer_id, maxResults=1000)
                if token:
                    kwargs["pageToken"] = token
                resp = execute_with_retry(licensing.licenseAssignments().listForProduct(**kwargs))
                out += resp.get("items", [])
                token = resp.get("nextPageToken")
                if not token:
                    break
        except HttpError as e:
            skipped[product] = f"HTTP {e.resp.status}: {http_reason(e)}"
    if progress:
        progress(f"  licences: {len(out)} assignments")
    return {"assignments": out, "skipped_products": skipped}


def fetch_chrome(chrome, progress=print) -> dict:
    """Installed browser apps/extensions and ChromeOS per-user telemetry."""
    from googleapiclient.errors import HttpError

    result, errors = {"installed_apps": [], "telemetry_users": []}, {}
    calls = {
        "installed_apps": (lambda **k: chrome.customers().reports().countInstalledApps(**k),
                           dict(customer="customers/my_customer", pageSize=100), "installedApps"),
        "telemetry_users": (lambda **k: chrome.customers().telemetry().users().list(**k),
                            dict(parent="customers/my_customer", pageSize=100,
                                 readMask="name,orgUnitId,userEmail,userId,userDevice"),
                            "telemetryUsers"),
    }
    for key, (call, base, field) in calls.items():
        try:
            token = None
            while True:
                kwargs = dict(base)
                if token:
                    kwargs["pageToken"] = token
                resp = execute_with_retry(call(**kwargs))
                result[key] += resp.get(field, [])
                token = resp.get("nextPageToken")
                if not token:
                    break
        except HttpError as e:
            errors[key] = f"HTTP {e.resp.status}: {http_reason(e)}"
    result["errors"] = errors
    if progress:
        progress(f"  chrome: {len(result['installed_apps'])} installed apps, "
                 f"{len(result['telemetry_users'])} ChromeOS users")
    return result


def fetch_extra(cfg: dict, creds, reports, start_time: str, end_time: str,
                users: list[dict], progress=print) -> dict:
    """Everything beyond Gemini + directory, according to cfg['modules']."""
    modules = cfg["modules"]
    extra: dict = {"sources": {}, "status": {}}
    a_start, _ = window(cfg["activity_window_days"], parse_time(end_time))

    if modules.get("activity_logs"):
        apps = list(cfg["activity_applications"])
        if cfg.get("gmail_sent_log") and "gmail" not in apps:
            apps.append("gmail")
        for app in apps:
            if progress:
                progress(f"Fetching {app} audit log ...")
            try:
                items = fetch_audit_app(reports, app, a_start, end_time, cfg["customer_id"], progress)
                extra["sources"][app] = {"items": items, "start": a_start, "end": end_time}
                extra["status"][app] = f"ok: {len(items)} records"
                if progress:
                    progress(f"  {app}: {len(items)} records")
            except SourceUnavailable as e:
                extra["status"][app] = f"unavailable: {e}"
                if progress:
                    progress(f"  {app}: skipped ({e})")

    if modules.get("usage_reports"):
        if progress:
            progress("Fetching usage reports ...")
        try:
            extra["usage_customer"] = fetch_customer_usage(reports, end_time, cfg["window_days"], progress)
            extra["usage_users"] = fetch_user_usage(reports, end_time, cfg["window_days"], progress)
            extra["status"]["usage_reports"] = f"ok: {len(extra['usage_customer'])} days"
        except SourceUnavailable as e:
            extra["status"]["usage_reports"] = f"unavailable: {e}"

    try:
        services = build_optional_services(creds, modules)
    except Exception as e:  # discovery failure (API not enabled etc.)
        services = {}
        for m in ("licensing", "chrome"):
            if modules.get(m):
                extra["status"][m] = f"unavailable: {e}"
    if "licensing" in services:
        if progress:
            progress("Fetching licence assignments ...")
        cid = cfg["customer_id"]
        if cid in ("", "my_customer"):
            cid = next((u.get("customerId") for u in users if u.get("customerId")), cid)
        lic = fetch_licences(services["licensing"], cid, cfg["licence_products"], progress)
        extra["licences"] = lic
        extra["status"]["licensing"] = (f"ok: {len(lic['assignments'])} assignments"
                                        if lic["assignments"] or not lic["skipped_products"]
                                        else f"unavailable: {next(iter(lic['skipped_products'].values()))}")
    if "chrome" in services:
        if progress:
            progress("Fetching Chrome management data ...")
        ch = fetch_chrome(services["chrome"], progress)
        extra["chrome_mgmt"] = ch
        extra["status"]["chrome_mgmt"] = ("ok" if not ch["errors"] else
                                          ("partial: " if len(ch["errors"]) < 2 else "unavailable: ")
                                          + "; ".join(f"{k}: {v}" for k, v in ch["errors"].items()))
    return extra


# ------------------------------------------------------------ cache
def extra_cache_path(path: Path) -> Path:
    path = Path(path)
    return path.with_name(path.name.replace("raw_", "raw_extra_", 1) + ".gz")


def save_cache(path: Path, activities: list[dict], users: list[dict],
               start_time: str, end_time: str, extra: dict | None = None) -> None:
    """Gemini + directory go in raw_DATE.json; everything else in raw_extra_DATE.json.gz."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": rfc3339(datetime.now(timezone.utc)),
        "start_time": start_time,
        "end_time": end_time,
        "activities": activities,
        "users": users,
    }
    path.write_text(json.dumps(payload, indent=1))
    if extra:
        with gzip.open(extra_cache_path(path), "wt", encoding="utf-8") as f:
            json.dump(extra, f)


def load_cache(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    extra = extra_cache_path(path)
    if extra.exists():
        with gzip.open(extra, "rt", encoding="utf-8") as f:
            data["extra"] = json.load(f)
    return data
