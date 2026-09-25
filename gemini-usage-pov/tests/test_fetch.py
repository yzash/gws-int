"""Pagination and retry logic in src/fetch.py, using fake API objects (no network)."""
import httplib2
import pytest
from googleapiclient.errors import HttpError

from src import fetch


class FakeRequest:
    def __init__(self, result=None, errors=()):
        self.result, self.errors = result, list(errors)

    def execute(self):
        if self.errors:
            raise self.errors.pop(0)
        return self.result


def http_error(status):
    return HttpError(httplib2.Response({"status": status}), b"{}")


class FakeActivities:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        token = kwargs.get("pageToken")
        idx = 0 if token is None else int(token)
        page = {"items": self.pages[idx]}
        if idx + 1 < len(self.pages):
            page["nextPageToken"] = str(idx + 1)
        return FakeRequest(page)


class FakeReports:
    def __init__(self, pages):
        self._a = FakeActivities(pages)

    def activities(self):
        return self._a


def test_paginates_to_completion():
    rep = FakeReports([[{"n": 1}] * 1000, [{"n": 2}] * 1000, [{"n": 3}] * 5])
    items = fetch.fetch_activities(rep, "s", "e", progress=None)
    assert len(items) == 2005
    calls = rep.activities().calls
    assert len(calls) == 3
    assert calls[0]["applicationName"] == "gemini_in_workspace_apps"
    assert calls[0]["userKey"] == "all" and calls[0]["maxResults"] == 1000
    assert "pageToken" not in calls[0] and calls[2]["pageToken"] == "2"


def test_max_pages():
    rep = FakeReports([[{}] * 1000, [{}] * 1000])
    assert len(fetch.fetch_activities(rep, "s", "e", max_pages=1, progress=None)) == 1000


def test_retries_429_and_5xx():
    slept = []
    req = FakeRequest({"ok": True}, errors=[http_error(429), http_error(503)])
    assert fetch.execute_with_retry(req, sleep=slept.append) == {"ok": True}
    assert len(slept) == 2 and slept[1] > slept[0] - 1


def test_does_not_retry_403():
    req = FakeRequest({"ok": True}, errors=[http_error(403)])
    with pytest.raises(HttpError):
        fetch.execute_with_retry(req, sleep=lambda s: None)


def test_gives_up_eventually():
    req = FakeRequest({"ok": True}, errors=[http_error(500)] * fetch.MAX_ATTEMPTS)
    with pytest.raises(HttpError):
        fetch.execute_with_retry(req, sleep=lambda s: None)


def test_window_is_rfc3339():
    start, end = fetch.window(28)
    assert start.endswith("Z") and end.endswith("Z") and start < end


# ------------------------------------------------------------ optional sources
def http_error_msg(status, message):
    import json
    return HttpError(httplib2.Response({"status": status}), json.dumps({"error": {"message": message}}).encode())


class MultiAppReports:
    """activities().list per application, plus usage report endpoints."""

    def __init__(self):
        self.calls = []

    def activities(self):
        outer = self

        class A:
            def list(self, **kw):
                outer.calls.append(kw)
                app = kw["applicationName"]
                if app == "classroom":
                    return FakeRequest(errors=[http_error_msg(403, "Classroom is not enabled")])
                return FakeRequest({"items": [{"id": {"time": "2026-09-20T10:00:00.000Z"},
                                               "actor": {"email": "a@example.com"},
                                               "events": [{"name": kw.get("eventName") or "x"}]}]})
        return A()

    def customerUsageReports(self):
        class C:
            def get(self, date):
                if date >= "2026-09-22":
                    return FakeRequest(errors=[http_error_msg(400, "Data for dates later than 2026-09-21 is not yet available.")])
                return FakeRequest({"usageReports": [{"date": date, "parameters": [{"name": "gmail:num_1day_active_users", "intValue": "3"}]}]})
        return C()

    def userUsageReport(self):
        class U:
            def get(self, **kw):
                if kw["date"] >= "2026-09-22":
                    return FakeRequest(errors=[http_error_msg(400, "Data for dates later than 2026-09-21 is not yet available.")])
                return FakeRequest({"usageReports": [{"date": kw["date"], "entity": {"userEmail": "A@example.com"},
                                                      "parameters": [{"name": "gmail:num_emails_sent", "intValue": "2"}]}]})
        return U()


def test_fetch_extra_records_status(monkeypatch):
    monkeypatch.setattr(fetch, "build_optional_services", lambda creds, modules: {})
    cfg = {"modules": {"activity_logs": True, "usage_reports": True}, "activity_applications": ["drive", "classroom", "token"],
           "gmail_sent_log": True, "activity_window_days": 28, "window_days": 5, "customer_id": "my_customer"}
    rep = MultiAppReports()
    extra = fetch.fetch_extra(cfg, None, rep, "2026-08-27T12:00:00.000Z", "2026-09-24T12:00:00.000Z", [], progress=None)
    st = extra["status"]
    assert st["drive"].startswith("ok") and st["classroom"].startswith("unavailable") and "403" in st["classroom"]
    # token only fetches grants/revokes, never the huge 'activity' stream
    token_calls = [c for c in rep.calls if c["applicationName"] == "token"]
    assert sorted(c["eventName"] for c in token_calls) == ["authorize", "revoke"]
    # gmail is chunked to <=30-day spans and filtered to sent mail
    gmail_calls = [c for c in rep.calls if c["applicationName"] == "gmail"]
    assert len(gmail_calls) == 1 and gmail_calls[0]["filters"] == "event_info.mail_event_type==1"
    # usage: dates after the available one are skipped, not errors
    assert st["usage_reports"].startswith("ok")
    assert [r["date"] for r in extra["usage_customer"]] == ["2026-09-21", "2026-09-20", "2026-09-19"]
    assert extra["usage_users"][0]["email"] == "A@example.com"


def test_cache_roundtrip(tmp_path):
    p = tmp_path / "raw_2026-09-25.json"
    fetch.save_cache(p, [{"a": 1}], [{"u": 1}], "s", "e", {"status": {"drive": "ok: 0 records"}})
    assert (tmp_path / "raw_extra_2026-09-25.json.gz").exists()
    c = fetch.load_cache(p)
    assert c["extra"]["status"]["drive"] == "ok: 0 records" and c["activities"] == [{"a": 1}]
