"""Shared fixtures for the unit-test harness.

Everything here runs offline: OpenAI, Slack and HTTP are replaced by fakes.
Environment variables and the working directory are set BEFORE any production
module is imported, because those modules read env, build an OpenAI client and
open a log file at import time.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

_SCRATCH = tempfile.mkdtemp(prefix="alertmon-tests-")
os.environ.update({
    "OPENAI_API_KEY": "sk-test-dummy",
    "SLACK_BOT_TOKEN": "xoxb-test-dummy",
    "CHANNEL_NAME": "C_TEST_ALERTS",
    "DB_PATH": os.path.join(_SCRATCH, "import_time.db"),
    "MODEL_NAME": "gpt-5-mini",
    "EXTRACTION_MODEL": "gpt-5-mini",
    "REASONING_EFFORT_EXTRACTION": "low",
    "REASONING_EFFORT_SUMMARY": "medium",
    "BATCH_SIZE": "10",
    "MAX_ALERT_AGE_DAYS": "3",
})
for _k in ("ERROR_CHANNEL_NAME", "RSS_FEED_1", "RSS_FEED_2", "RSS_FEED_3",
           "RSS_FEED_4", "RSS_FEED_5", "RSS_FEED_6"):
    os.environ.pop(_k, None)

# Production modules attach a FileHandler("disaster_alert_bot.log") relative to
# the cwd at import; keep that out of the repo.
_ORIG_CWD = os.getcwd()
os.chdir(_SCRATCH)
sys.path.insert(0, str(REPO_ROOT))

try:  # the openai SDK's HTTP library name differs across releases
    import httpx2 as httpx  # noqa: E402
except ImportError:  # pragma: no cover
    import httpx  # noqa: E402
import openai  # noqa: E402
import pytest  # noqa: E402
import requests  # noqa: E402
from slack_sdk.errors import SlackApiError  # noqa: E402

import fetch_feeds  # noqa: E402
import main as main_mod  # noqa: E402
import process_data  # noqa: E402
import send_to_slack  # noqa: E402

os.chdir(_ORIG_CWD)  # log FileHandlers hold absolute paths now; restore cwd for pytest


# --------------------------------------------------------------------------
# Fake OpenAI
# --------------------------------------------------------------------------
class _Resp:
    def __init__(self, text):
        self.output_text = text


def make_bad_request_error(message="bad request"):
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    resp = httpx.Response(400, request=req)
    return openai.BadRequestError(message, response=resp, body={"error": message})


def make_api_error(message="api error"):
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return openai.APIError(message, req, body=None)


def echo_llm(kw):
    """Default well-behaved model.

    Extraction calls: one result per ALERT, id echoed, details derived from the
    title (alert level, magnitude). Grouping calls: one group per input key.
    """
    prompt = kw["input"]
    if "Here are the alerts to process:" in prompt:
        results = []
        for block in prompt.split("--- ALERT ")[1:]:
            ident = re.search(r"\(ID: (.*?)\) ---", block).group(1)
            title = re.search(r"Title: (.*)", block).group(1)
            level = re.match(r"(green|orange|red)\b", title, re.I)
            mag = re.search(r"M(?:agnitude)? ?(\d+\.\d+)", title)
            dtype = "Earthquake" if re.search(r"earthquake|^M \d", title, re.I) else "Event"
            results.append({
                "id": ident,
                "disaster_type": dtype,
                "location": "Testland",
                "date": "2026-10-12",
                "severity": mag.group(1) if mag else None,
                "alert_level": level.group(1).capitalize() if level else "",
                "description": title,
            })
        return json.dumps({"results": results})
    payload = json.loads(prompt.split("Input groups:\n", 1)[1])
    return json.dumps({"groups": [
        {"heading": p["key"], "summary": "Summary.", "member_keys": [p["key"]]}
        for p in payload
    ]})


class FakeResponses:
    def __init__(self, script=None):
        self.calls = []
        self.script = list(script) if script is not None else None

    def create(self, **kw):
        self.calls.append(kw)
        step = echo_llm if self.script is None else (
            self.script.pop(0) if self.script else echo_llm)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            step = step(kw)
        if isinstance(step, BaseException):
            raise step
        return _Resp(step)


class FakeOpenAI:
    def __init__(self, script=None):
        self.responses = FakeResponses(script)


@pytest.fixture
def fake_llm(monkeypatch):
    """Install a FakeOpenAI as process_data.client. Call fake_llm(script) to
    reinstall with a script (list of str | Exception | callable(kw))."""
    state = {}

    def install(script=None):
        fake = FakeOpenAI(script)
        monkeypatch.setattr(process_data, "client", fake)
        state["fake"] = fake
        return fake

    fake = install()

    class Handle:
        def __call__(self, script=None):
            return install(script)

        @property
        def calls(self):
            return state["fake"].responses.calls

    return Handle()


# --------------------------------------------------------------------------
# Fake Slack
# --------------------------------------------------------------------------
class _SlackResp(dict):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}


def slack_error(code, headers=None):
    return SlackApiError(f"The request failed: {code}", _SlackResp({"ok": False, "error": code}, headers))


class FakeSlackClient:
    """Records chat_postMessage calls. `failures` is a list consumed one per
    call: an Exception to raise or None to succeed. `fail_when` is an optional
    predicate(kwargs) -> Exception|None evaluated first."""

    def __init__(self):
        self.posts = []
        self.failures = []
        self.fail_when = None

    def chat_postMessage(self, **kw):
        self.posts.append(kw)
        if self.fail_when:
            err = self.fail_when(kw)
            if err:
                raise err
        if self.failures:
            err = self.failures.pop(0)
            if err:
                raise err
        return {"ok": True, "ts": "1700000000.000100"}

    @property
    def block_posts(self):
        return [p for p in self.posts if p.get("blocks")]


@pytest.fixture
def slack(monkeypatch):
    fake = FakeSlackClient()
    monkeypatch.setattr(send_to_slack, "client", fake)
    return fake


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _no_error_channel(monkeypatch):
    monkeypatch.setattr(send_to_slack, "ERROR_CHANNEL_NAME", None)


# --------------------------------------------------------------------------
# Fake HTTP for fetch_feeds
# --------------------------------------------------------------------------
def make_response(body, status=200):
    r = requests.Response()
    r.status_code = status
    r.encoding = "utf-8"
    r._content = body if isinstance(body, bytes) else body.encode("utf-8")
    r.url = "https://example.invalid/"
    return r


@pytest.fixture
def http(monkeypatch):
    """Serve canned bodies from requests.get. Register with http.route(substr,
    body_or_path). Unregistered URLs raise so tests can never reach the net."""
    routes = []
    calls = []

    class Http:
        def route(self, substr, body, status=200):
            if isinstance(body, Path):
                body = body.read_bytes()
            elif isinstance(body, (dict, list)):
                body = json.dumps(body)
            routes.append((substr, body, status))

        fixture = staticmethod(lambda name: FIXTURE_DIR / name)
        calls_ = calls

    def fake_get(url, *a, **kw):
        calls.append((url, kw))
        for substr, body, status in routes:
            if substr in url:
                return make_response(body, status)
        raise requests.exceptions.ConnectionError(f"no route for {url} (network disabled in tests)")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setenv("MAX_ALERT_AGE_DAYS", "0")  # fixtures are dated; age guard tested directly
    return Http()


# --------------------------------------------------------------------------
# main.py helpers
# --------------------------------------------------------------------------
@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "bot.db")
    monkeypatch.setattr(main_mod, "DB_PATH", path)
    main_mod.initialize_db()
    return path


def saved_links(path):
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT link FROM sent_entries")}
    finally:
        conn.close()


@pytest.fixture
def run_main(monkeypatch, db, slack, fake_llm):
    """run_main(disasters) runs main.main() once with fetch stubbed."""
    def _run(disasters):
        monkeypatch.setattr(main_mod, "fetch_rss_feeds", lambda feeds: [dict(d) for d in disasters])
        main_mod.main()
    return _run


def disaster(title, link, source_type="usgs", published="Mon, 12 Oct 2026 08:00:00 GMT",
             summary="", source="Test Source"):
    return {"title": title, "summary": summary or title, "link": link,
            "published": published, "source": source, "source_type": source_type}
