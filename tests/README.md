# Unit tests

Offline unit tests: no network, no real OpenAI or Slack calls, no changes to production code.

    python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python -m pytest

- `conftest.py` sets dummy env vars before importing the bot (it builds an OpenAI client at import), and provides fakes: `fake_llm` (scripted `responses.create`), `slack` (records `chat_postMessage`), `http` (serves `tests/fixtures/` through `requests.get`), `run_main` (one `main.main()` pass against a temp SQLite DB).
- `tests/fixtures/` holds small hand-made samples in each source's format (GDACS, USGS, SPC, NHC, ReliefWeb v2, WFIGS). They are not recorded from live feeds.
- Known bugs are pinned as `xfail(strict=True, reason="BUG: ...")`. When one is fixed the test XPASSes and fails the run: delete the marker.
- `disaster_bot.py` is deliberately not imported (it posts to Slack).
