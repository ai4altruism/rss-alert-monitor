"""Deterministic details for structured feeds, checked on the recorded feeds."""
from pathlib import Path

import pytest

import process_data
from conftest import disaster
from fetch_feeds import fetch_rss_feeds
from structured_details import parse_details

RECORDED = Path(__file__).parent / "fixtures" / "recorded-20261018"
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


def of(reports, source_type):
    return [r for r in reports if r.get("source_type") == source_type]


def test_usgs_title_gives_magnitude_and_place():
    d = parse_details(disaster("M 6.1 - 20 km SW of Sandy Point Town, Saint Kitts and Nevis",
                               "https://e/1", source_type="usgs", published="2026-10-18T16:21:24.252Z"))
    assert d["disaster_type"] == "earthquake"
    assert d["severity"] == "6.1"
    assert d["location"] == "20 km SW of Sandy Point Town, Saint Kitts and Nevis"


def test_usgs_title_without_pattern_falls_back_to_model():
    assert parse_details(disaster("Earthquake somewhere", "https://e/1", source_type="usgs")) is None


def test_recorded_usgs_reports_all_parse(recorded):
    usgs = of(recorded, "usgs")
    assert usgs and all(parse_details(r)["severity"] for r in usgs)


def test_recorded_gdacs_cyclone_carries_level_name_and_wind(recorded):
    tc = [r for r in of(recorded, "gdacs") if "CHAMPI-26" in r["title"]]
    assert tc
    d = parse_details(tc[0])
    assert d["disaster_type"] == "tropical cyclone"
    assert d["alert_level"] == "red"
    assert d["location"].startswith("CHAMPI-26, Guam")
    assert d["severity"] == "259 km/h"


def test_gdacs_earthquake_severity_is_a_bare_magnitude():
    raw = {"gdacs_eventtype": "EQ", "gdacs_alertlevel": "Orange", "gdacs_country": "Chile",
           "gdacs_severity": {"unit": "M", "value": "6.4"}, "gdacs_fromdate": "Sun, 18 Oct 2026 15:35:47 GMT"}
    report = disaster("Orange earthquake (Magnitude 6.4M) in Chile", "https://g/1", source_type="gdacs")
    report["raw_entry"] = raw
    d = parse_details(report)
    assert (d["severity"], d["alert_level"], d["location"]) == ("6.4", "orange", "Chile")


def test_gdacs_low_magnitude_earthquake_still_filtered():
    # should_filter_entry reads the parsed severity as a number (< 6.0 drops)
    raw = {"gdacs_eventtype": "EQ", "gdacs_alertlevel": "Orange", "gdacs_country": "Chile",
           "gdacs_severity": {"unit": "M", "value": "5.7"}}
    report = disaster("Orange earthquake (Magnitude 5.7M) in Chile", "https://g/1", source_type="gdacs")
    report["raw_entry"] = raw
    assert process_data.should_filter_entry(report, parse_details(report)) is True


def test_recorded_wfigs_reports_carry_acreage(recorded):
    fires = of(recorded, "inciweb")
    assert fires
    details = [parse_details(r) for r in fires]
    assert all(d["disaster_type"] == "wildfire" for d in details)
    assert all("acres" in (d["severity"] or "") for d in details)


def test_recorded_reliefweb_reports_carry_type_and_country(recorded):
    rw = of(recorded, "reliefweb")
    panama = next(r for r in rw if r["title"].startswith("Panama: Earthquake"))
    d = parse_details(panama)
    assert (d["disaster_type"], d["location"]) == ("earthquake", "Panama")


@pytest.mark.parametrize("source_type", ["noaa_spc", "nhc"])
def test_prose_sources_go_to_the_model(source_type):
    assert parse_details(disaster("Something", "https://n/1", source_type=source_type)) is None


def test_only_prose_reports_reach_the_extraction_model(recorded, fake_llm):
    # Default fake output is unusable JSON, so groups fall back to deterministic rendering;
    # what matters here is which titles were sent for extraction.
    process_data.group_disasters(recorded)
    sent = " ".join(str(c.get("input", "")) for c in fake_llm.calls)
    structured = of(recorded, "usgs") + of(recorded, "gdacs") + of(recorded, "inciweb") + of(recorded, "reliefweb")
    leaked = [r["title"] for r in structured if r["title"] in sent and not process_data.should_filter_entry(r)]
    assert leaked == []


def test_parsed_groups_have_no_unknown_placeholders(recorded, fake_llm):
    grouped = process_data.group_disasters(of(recorded, "usgs") + of(recorded, "inciweb") + of(recorded, "reliefweb"))
    assert grouped
    assert not any("None" in k or "Unknown Type" in k for k in grouped)
