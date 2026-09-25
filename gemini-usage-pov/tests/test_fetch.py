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
