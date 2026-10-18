# Unit tests

Offline unit tests: no network, no real OpenAI or Slack calls, no changes to production code.

    python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python -m pytest

- `conftest.py` sets dummy env vars before importing the bot (it builds an OpenAI client at import), and provides fakes: `fake_llm` (scripted `responses.create`), `slack` (records `chat_postMessage`), `http` (serves `tests/fixtures/` through `requests.get`), `run_main` (one `main.main()` pass against a temp SQLite DB).
- `tests/fixtures/` holds small hand-made samples in each source's format (GDACS, USGS, SPC, NHC, ReliefWeb v2, WFIGS). They are not recorded from live feeds.
- `tests/fixtures/recorded-20261018/` holds the live public feeds as fetched on 2026-10-18 (WFIGS with perimeters removed). `test_dedup_and_recorded.py` runs the real fetchers on them and asserts invariants (unique dedup keys, every report linked, dates parse), not particular events. Re-record when a feed changes format.
- Known bugs are pinned as `xfail(strict=True, reason="BUG: ...")`. When one is fixed the test XPASSes and fails the run: delete the marker.
- `disaster_bot.py` is deliberately not imported (it posts to Slack).
