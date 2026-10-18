import sqlite3

import pytest

import main as main_mod
import process_data
from conftest import disaster, make_bad_request_error, saved_links, slack_error, echo_llm

USGS_LINK = "https://earthquake.usgs.gov/earthquakes/eventpage/us7000abc"
GDACS_LINK = "https://www.gdacs.org/report.aspx?eventtype=EQ&eventid=1500002"


def posted_text(slack):
    return "\n".join(
        b["text"]["text"] for p in slack.block_posts for b in p["blocks"] if b["type"] == "section")


# ---- db helpers ----------------------------------------------------------
def test_initialize_db_creates_tables_and_version(db):
    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sent_entries", "metadata"} <= names
    assert conn.execute("SELECT value FROM metadata WHERE key='version'").fetchone()[0] == "1.1.0"


def test_save_and_load_sent_entries_roundtrip_and_ignore_duplicates(db):
    main_mod.save_sent_entries(["a", "b"])
    main_mod.save_sent_entries(["b", "c"])
    assert main_mod.load_sent_entries() == {"a", "b", "c"}


def test_cleanup_removes_only_entries_older_than_30_days(db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO sent_entries VALUES ('old', datetime('now','-31 day'))")
    conn.execute("INSERT INTO sent_entries VALUES ('new', datetime('now','-1 day'))")
    conn.commit(); conn.close()
    main_mod.cleanup_old_entries()
    assert saved_links(db) == {"new"}


# ---- delivery gating -------------------------------------------------------
def test_successful_delivery_records_links(run_main, slack, db):
    run_main([disaster("Floods in Chad", "https://r/1", source_type="reliefweb")])
    assert len(slack.block_posts) == 1
    assert saved_links(db) == {"https://r/1"}


def test_slack_failure_records_nothing(run_main, slack, db):
    slack.failures = [slack_error("channel_not_found")]
    run_main([disaster("Floods in Chad", "https://r/1", source_type="reliefweb")])
    assert saved_links(db) == set()


def test_slack_failure_is_retried_on_next_run(run_main, slack, db):
    ds = [disaster("Floods in Chad", "https://r/1", source_type="reliefweb")]
    slack.failures = [slack_error("channel_not_found")]
    run_main(ds)
    run_main(ds)
    assert len(slack.block_posts) == 2
    assert saved_links(db) == {"https://r/1"}


def test_already_sent_links_are_not_reposted(run_main, slack, db):
    ds = [disaster("Floods in Chad", "https://r/1", source_type="reliefweb")]
    run_main(ds)
    run_main(ds)
    assert len(slack.block_posts) == 1


def test_llm_total_failure_still_delivers_fallback_alert(run_main, slack, db, fake_llm):
    err = make_bad_request_error()
    fake_llm([err, err, err, err, err])
    run_main([disaster("Floods in Chad", "https://r/1", source_type="reliefweb")])
    assert len(slack.block_posts) == 1
    assert saved_links(db) == {"https://r/1"}


def test_all_filtered_records_links_without_posting(run_main, slack, db):
    run_main([disaster("M 5.0 - Quiet place", USGS_LINK, source_type="usgs")])
    assert slack.posts == []
    assert saved_links(db) == {USGS_LINK}


def test_job_swallows_exceptions(monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(main_mod, "main", boom)
    main_mod.job()  # must not raise


# ---- dedup-key bugs ----------------------------------------------------------
@pytest.mark.xfail(strict=True, reason="BUG: entries dropped by should_filter_entry are saved as sent, so a USGS event revised from M5.6 to M6.1 under the same link never alerts")
def test_usgs_event_filtered_then_revised_upward_alerts(run_main, slack):
    run_main([disaster("M 5.6 - 40 km W of Somewhere", USGS_LINK, source_type="usgs")])
    assert slack.posts == []
    run_main([disaster("M 6.1 - 40 km W of Somewhere", USGS_LINK, source_type="usgs")])
    assert len(slack.block_posts) == 1


@pytest.mark.xfail(strict=True, reason="BUG: dedup key is the link only, so a GDACS escalation orange->red under a stable link never re-alerts")
def test_gdacs_escalation_under_stable_link_realerts(run_main, slack):
    run_main([disaster("Orange earthquake alert (Magnitude 6.5M) in Chile", GDACS_LINK, source_type="gdacs")])
    run_main([disaster("Red earthquake alert (Magnitude 7.1M) in Chile", GDACS_LINK, source_type="gdacs")])
    assert len(slack.block_posts) == 2


def test_gdacs_same_level_same_link_does_not_realert(run_main, slack):
    ds = [disaster("Orange earthquake alert (Magnitude 6.5M) in Chile", GDACS_LINK, source_type="gdacs")]
    run_main(ds)
    run_main(ds)
    assert len(slack.block_posts) == 1


# ---- block truncation vs saved links ---------------------------------------
def _many(n):
    ds = [disaster(f"Flood {i}", f"https://r/{i}", source_type="reliefweb") for i in range(n)]
    groups = [{"heading": f"Group {i}", "summary": "S.",
               "items": [{"title": d["title"], "link": d["link"], "published": "", "source": "s"}]}
              for i, d in enumerate(ds)]
    return ds, groups


@pytest.fixture
def many_groups(monkeypatch):
    ds, groups = _many(80)
    monkeypatch.setattr(main_mod, "process_disasters", lambda d: (groups, "ok"))
    return ds


def test_every_link_that_was_posted_is_recorded(run_main, slack, db, many_groups):
    run_main(many_groups)
    shown = {d["link"] for d in many_groups if d["link"] in posted_text(slack)}
    assert shown and shown <= saved_links(db)


@pytest.mark.xfail(strict=True, reason="BUG: groups omitted by the 50-block cap are still saved as sent, so they are never delivered")
def test_links_of_groups_omitted_by_block_cap_are_not_recorded(run_main, slack, db, many_groups):
    run_main(many_groups)
    shown = {d["link"] for d in many_groups if f"<{d['link']}|" in posted_text(slack)}
    assert 0 < len(shown) < len(many_groups)
    assert saved_links(db) == shown


# ---- invalid_blocks poison pill -------------------------------------------------
def _invalid_blocks(slack):
    slack.fail_when = lambda kw: slack_error("invalid_blocks") if kw.get("blocks") else None


def test_invalid_blocks_records_nothing_current_behavior(run_main, slack, db):
    _invalid_blocks(slack)
    run_main([disaster("Floods", "https://r/1", source_type="reliefweb")])
    assert saved_links(db) == set()


@pytest.mark.xfail(strict=True, reason="BUG: invalid_blocks is never recorded, so the same rejected alert is re-attempted (and a generic public error text re-posted) on every run")
def test_invalid_blocks_alert_is_not_repeated_forever(run_main, slack):
    _invalid_blocks(slack)
    ds = [disaster("Floods", "https://r/1", source_type="reliefweb")]
    run_main(ds)
    attempts_after_first = len(slack.posts)
    run_main(ds)
    assert len(slack.posts) == attempts_after_first
