"""Dedup keys, the deploy-safe legacy match, and real recorded feeds.

The recorded-20261018 fixtures are the live public feeds as fetched on
2026-10-18 (WFIGS with fire perimeters removed). They pin the formats the
hand-written fixtures only approximate; tests on them assert invariants
rather than particular events, since the next recording will differ.
"""
from pathlib import Path

import pytest

import main as main_mod
from conftest import disaster, saved_links
from fetch_feeds import _parse_report_datetime, fetch_rss_feeds
from main import already_sent, dedup_key

RECORDED = Path(__file__).parent / "fixtures" / "recorded-20261018"
USGS = "https://earthquake.usgs.gov/earthquakes/eventpage/us7000abc"
GDACS = "https://www.gdacs.org/report.aspx?eventtype=EQ&eventid=1"


# ---- dedup_key -------------------------------------------------------------
@pytest.mark.parametrize("report, key", [
    (disaster("M 5.6 - Somewhere", USGS, source_type="usgs"), USGS + "#M5.6"),
    (disaster("M 6.1 - Somewhere", USGS, source_type="usgs"), USGS + "#M6.1"),
    (disaster("Orange earthquake alert in X", GDACS, source_type="gdacs"), GDACS + "#orange"),
    (disaster("Red notification for tropical cyclone Y", GDACS, source_type="gdacs"), GDACS + "#red"),
    # SPC titles carry a timestamp; whole-title keys would re-post them hourly
    (disaster("SPC - No watches are valid as of Sun Oct 18 17:01:02 UTC 2026",
              "https://www.spc.noaa.gov/products/watch/", source_type="noaa_spc"),
     "https://www.spc.noaa.gov/products/watch/"),
    (disaster("Stubble Fire (US-OR)", "https://maps/x", source_type="inciweb"), "https://maps/x"),
    # unparseable severity falls back to the link
    (disaster("Earthquake somewhere", USGS, source_type="usgs"), USGS),
    (disaster("Notification for event", GDACS, source_type="gdacs"), GDACS),
])
def test_dedup_key(report, key):
    assert dedup_key(report) == key


def test_legacy_bare_link_row_still_counts_as_sent():
    # Rows written before dedup_key hold the bare link: a deploy must not
    # re-post every USGS/GDACS item still in the feeds.
    report = disaster("M 5.6 - Somewhere", USGS, source_type="usgs")
    assert already_sent(report, {USGS})
    assert already_sent(report, {USGS + "#M5.6"})
    assert not already_sent(report, {USGS + "#M5.4"})


def test_same_usgs_magnitude_is_not_reposted(run_main, slack, db, fake_llm):
    run_main([disaster("M 6.1 - Somewhere", USGS, source_type="usgs")])
    first = len(slack.posts)
    run_main([disaster("M 6.1 - Somewhere", USGS, source_type="usgs")])
    assert len(slack.posts) == first


# ---- block cap, mid-group cut -----------------------------------------------
def test_items_cut_mid_group_are_not_recorded(run_main, slack, db, monkeypatch):
    # One group far larger than the message can hold: the cap is hit inside
    # the group, so its later lines are cut without a footer count.
    ds = [disaster(f"Fire {i} " + "x" * 400, f"https://maps/{i}", source_type="inciweb")
          for i in range(400)]
    group = {"heading": "Wildfires", "summary": "", "items": [
        {"title": d["title"], "link": d["link"], "published": d["published"], "source": "WFIGS"}
        for d in ds]}
    monkeypatch.setattr(main_mod, "process_disasters", lambda d: ([group], "ok"))
    run_main(ds)
    posted = " ".join(b["text"]["text"] for p in slack.posts for b in p.get("blocks", [])
                      if b.get("type") == "section")
    shown = {d["link"] for d in ds if f"<{d['link']}|" in posted}
    assert 0 < len(shown) < len(ds)
    assert saved_links(db) == shown


# ---- SPC noise ------------------------------------------------------------------
def test_spc_no_watches_notice_is_filtered(http):
    http.route("spc.noaa.gov", RECORDED / "spc_rss.xml")
    titles = [r["title"] for r in fetch_rss_feeds(["https://www.spc.noaa.gov/products/spcrss.xml"])]
    assert not any("no watches are valid" in t.lower() for t in titles)


# ---- recorded real feeds ---------------------------------------------------------
FEEDS = {
    "gdacs.org": ("https://gdacs.org/xml/rss_24h.xml", "gdacs_rss.xml"),
    "api.reliefweb.int": ("https://api.reliefweb.int/v2/disasters?appname=test", "reliefweb_v2.json"),
    "arcgis.com": ("https://services3.arcgis.com/x/query?f=json", "wfigs.json"),
    "spc.noaa.gov": ("https://www.spc.noaa.gov/products/spcrss.xml", "spc_rss.xml"),
    "earthquake.usgs.gov": ("https://earthquake.usgs.gov/feed/4.5_day.atom", "usgs_atom.xml"),
    "nhc.noaa.gov": ("https://www.nhc.noaa.gov/gtwo.xml", "nhc_gtwo.xml"),
}


@pytest.fixture
def recorded(http):
    for host, (_, fname) in FEEDS.items():
        http.route(host, RECORDED / fname)
    return fetch_rss_feeds([url for url, _ in FEEDS.values()])


def test_recorded_feeds_yield_reports_from_every_structured_source(recorded):
    sources = {r.get("source_type") for r in recorded}
    assert {"usgs", "reliefweb", "inciweb"} <= sources


def test_recorded_reports_have_title_and_link(recorded):
    assert recorded
    assert all(r.get("title") and r.get("link") for r in recorded)


def test_recorded_dedup_keys_are_unique(recorded):
    keys = [dedup_key(r) for r in recorded]
    assert len(keys) == len(set(keys))


def test_recorded_dates_parse(recorded):
    unparsed = [r["published"] for r in recorded
                if r.get("published") and _parse_report_datetime(r) is None]
    assert unparsed == []
