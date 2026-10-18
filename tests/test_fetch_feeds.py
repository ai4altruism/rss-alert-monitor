import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

import fetch_feeds
from fetch_feeds import fetch_rss_feeds, filter_stale_reports

GDACS = "https://www.gdacs.org/xml/rss.xml"
USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.atom"
SPC = "https://www.spc.noaa.gov/products/spcwwrss.xml"
NHC = "https://www.nhc.noaa.gov/gtwo.xml"
RELIEFWEB = "https://api.reliefweb.int/v2/disasters?appname=test"
WFIGS = "https://services3.arcgis.com/x/arcgis/rest/services/WFIGS/FeatureServer/0/query?f=json"


def test_gdacs_green_item_is_dropped_orange_and_red_kept(http):
    http.route("gdacs.org", http.fixture("gdacs_rss.xml"))
    reports = fetch_rss_feeds([GDACS])
    titles = [r["title"] for r in reports]
    assert len(reports) == 2
    assert not any(t.lower().startswith("green ") for t in titles)
    assert titles[0].startswith("Orange") and titles[1].startswith("Red")
    assert {r["source_type"] for r in reports} == {"gdacs"}
    assert reports[0]["source"] == "GDACS RSS information"


def test_usgs_atom_entries_map_to_reports(http):
    http.route("usgs.gov", http.fixture("usgs_atom.xml"))
    reports = fetch_rss_feeds([USGS])
    assert [r["title"] for r in reports] == [
        "M 6.1 - 45 km SW of Test Town, Peru",
        "M 4.7 - 10 km N of Sample City, Japan",
    ]
    assert reports[0]["link"].endswith("us7000test1")
    assert reports[0]["source_type"] == "usgs"


def test_spc_drops_mesoscale_discussions_and_outlooks_keeps_watch(http):
    http.route("spc.noaa.gov", http.fixture("spc_rss.xml"))
    reports = fetch_rss_feeds([SPC])
    assert [r["title"] for r in reports] == ["SPC Tornado Watch 567"]
    assert reports[0]["source_type"] == "noaa_spc"


def test_nhc_outlook_without_formation_expected_is_filtered(http):
    # The hand-made outlook has no "Formation chance" line, i.e. nothing forming
    http.route("nhc.noaa.gov", http.fixture("nhc_gtwo.xml"))
    assert fetch_rss_feeds([NHC]) == []


def test_nhc_recorded_outlooks_keep_only_basins_with_a_formation_chance(http):
    # Recorded 2026-10-18: Atlantic 7-day low, Eastern Pacific 7-day high,
    # Central Pacific "formation is not expected"
    from pathlib import Path
    http.route("nhc.noaa.gov", Path(__file__).parent / "fixtures" / "recorded-20261018" / "nhc_gtwo.xml")
    reports = fetch_rss_feeds([NHC])
    assert sorted(r["title"] for r in reports) == [
        "Atlantic Tropical Weather Outlook", "Eastern Pacific Tropical Weather Outlook"]
    assert all(r["source_type"] == "nhc" and "gtwo.php" in r["link"] for r in reports)


def test_reliefweb_item_with_url_and_date(http):
    http.route("api.reliefweb.int", http.fixture("reliefweb_v2.json"))
    first = fetch_rss_feeds([RELIEFWEB])[0]
    assert first["link"] == "https://reliefweb.int/disaster/eq-2026-000200-per"
    assert first["published"] == "2026-10-12T08:45:00+00:00"
    assert first["source_type"] == "reliefweb"
    assert "Disaster type: Earthquake." in first["summary"]
    assert "Peru" in first["summary"]


def test_reliefweb_item_without_url_or_date_falls_back(http):
    http.route("api.reliefweb.int", http.fixture("reliefweb_v2.json"))
    second = fetch_rss_feeds([RELIEFWEB])[1]
    # The fallback keeps the disasters page but stays unique per item
    assert second["link"].startswith("https://reliefweb.int/disasters#")
    assert second["published"] == ""


def test_reliefweb_request_asks_for_url_and_date_fields(http, monkeypatch):
    http.route("api.reliefweb.int", http.fixture("reliefweb_v2.json"))
    fetch_rss_feeds([RELIEFWEB])
    _, kw = http.calls_[0]
    wanted = kw["params"]["fields[include][]"]
    assert "url" in wanted and "date.created" in wanted


def test_wfigs_with_coordinates_links_to_map_pin(http):
    http.route("arcgis.com", http.fixture("wfigs.json"))
    first = fetch_rss_feeds([WFIGS])[0]
    assert first["link"] == "https://www.google.com/maps/search/?api=1&query=44.8123,-117.5678"
    assert first["title"] == "Anthony Fire (US-OR)"
    assert first["published"] == "2026-10-12 10:00:00 UTC"
    assert first["note"] == "~12,346 acres, 20% contained, Baker County, US-OR"


def test_wfigs_without_coordinates_uses_inciweb_link_unique_per_fire(http):
    http.route("arcgis.com", http.fixture("wfigs.json"))
    second = fetch_rss_feeds([WFIGS])[1]
    assert second["link"].startswith("https://inciweb.wildfire.gov/#")
    assert second["title"] == "Ridge Complex (US-ID)"


def test_wfigs_error_payload_is_swallowed_and_yields_no_reports(http):
    http.route("arcgis.com", {"error": {"code": 400, "message": "bad"}})
    assert fetch_rss_feeds([WFIGS]) == []


def test_one_failing_feed_does_not_block_others(http):
    http.route("usgs.gov", http.fixture("usgs_atom.xml"))
    # GDACS has no route -> ConnectionError inside requests.get
    reports = fetch_rss_feeds([GDACS, USGS])
    assert len(reports) == 2


def test_empty_feed_urls_are_skipped(http):
    assert fetch_rss_feeds([None, ""]) == []


def _wfigs_payload(*names):
    return {"features": [{"attributes": {"attr_IncidentName": n, "attr_POOState": "US-CA"}} for n in names]}


def test_wfigs_fires_without_coordinates_get_distinct_dedup_keys(http):
    http.route("arcgis.com", _wfigs_payload("Alpha", "Bravo"))
    links = [r["link"] for r in fetch_rss_feeds([WFIGS])]
    assert len(links) == 2 and len(set(links)) == 2


def test_reliefweb_disasters_without_url_get_distinct_dedup_keys(http):
    http.route("api.reliefweb.int", {"data": [
        {"fields": {"name": "Flood A"}}, {"fields": {"name": "Flood B"}}]})
    links = [r["link"] for r in fetch_rss_feeds([RELIEFWEB])]
    assert len(links) == 2 and len(set(links)) == 2


# ---- age guard ----------------------------------------------------------
def _report(published, title="r", **kw):
    return {"title": title, "published": published, **kw}


NOW = lambda: datetime.now(timezone.utc)


def test_stale_reliefweb_iso_report_is_dropped_and_fresh_kept():
    old = (NOW() - timedelta(days=4)).isoformat()
    fresh = (NOW() - timedelta(hours=2)).isoformat()
    kept = filter_stale_reports([_report(old, "old"), _report(fresh, "new")], max_age_days=3)
    assert [r["title"] for r in kept] == ["new"]


def test_undated_reports_are_kept():
    reports = [_report("", "a"), _report("No Published Date", "b"), {"title": "c"}]
    assert filter_stale_reports(reports, max_age_days=3) == reports


def test_wfigs_utc_format_parses_for_age_guard():
    old = (NOW() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S UTC")
    fresh = (NOW() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S UTC")
    kept = filter_stale_reports([_report(old, "old"), _report(fresh, "new")], max_age_days=3)
    assert [r["title"] for r in kept] == ["new"]


def test_rfc2822_format_parses_for_age_guard():
    old = format_datetime(NOW() - timedelta(days=5))
    fresh = format_datetime(NOW() - timedelta(hours=1))
    kept = filter_stale_reports([_report(old, "old"), _report(fresh, "new")], max_age_days=3)
    assert [r["title"] for r in kept] == ["new"]


def test_feedparser_struct_time_is_used_when_present():
    old = (NOW() - timedelta(days=9)).timetuple()
    report = {"title": "x", "published": "garbage", "raw_entry": {"published_parsed": old}}
    assert filter_stale_reports([report], max_age_days=3) == []


@pytest.mark.parametrize("disabled", ["0", "-1"])
def test_max_age_zero_or_negative_disables_guard(monkeypatch, disabled):
    monkeypatch.setenv("MAX_ALERT_AGE_DAYS", disabled)
    old = (NOW() - timedelta(days=400)).isoformat()
    assert len(filter_stale_reports([_report(old)])) == 1


def test_default_age_comes_from_env(monkeypatch):
    monkeypatch.setenv("MAX_ALERT_AGE_DAYS", "1")
    two_days = (NOW() - timedelta(days=2)).isoformat()
    assert filter_stale_reports([_report(two_days)]) == []


def test_user_agent_uses_env_contact(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "ops@example.org")
    assert "ops@example.org" in fetch_feeds.get_user_agent()
