import pytest

from process_data import normalize_date, should_filter_entry


def usgs(title, summary=""):
    return {"source_type": "usgs", "title": title, "summary": summary, "link": "https://u/1"}


def gdacs(title, summary="", **extra):
    return {"source_type": "gdacs", "title": title, "summary": summary, "link": "https://g/1", **extra}


@pytest.mark.parametrize("title,dropped", [
    ("M 5.8 - 10 km S of X", False),
    ("M 5.79 - 10 km S of X", True),
    ("M 5.7 - 10 km S of X", True),
    ("M 6.1 - 10 km S of X", False),
    ("M 4.6 - 10 km S of X", True),
])
def test_usgs_magnitude_threshold(title, dropped):
    assert should_filter_entry(usgs(title)) is dropped


def test_usgs_magnitude_falls_back_to_summary():
    assert should_filter_entry(usgs("Earthquake near X", summary="Magnitude 5.2 at depth")) is True


def test_usgs_llm_severity_backup_filters_low_magnitude():
    details = {"disaster_type": "Earthquake", "severity": "5.1", "alert_level": ""}
    assert should_filter_entry(usgs("Quake near X"), details) is True


@pytest.mark.parametrize("title,dropped", [
    ("Green earthquake alert (Magnitude 5.1M) in X", True),
    ("Orange earthquake alert (Magnitude 6.5M) in X", False),
    ("Red tropical cyclone alert in X", False),
])
def test_gdacs_alert_level_from_title(title, dropped):
    assert should_filter_entry(gdacs(title)) is dropped


def test_gdacs_green_detected_from_llm_alert_level():
    details = {"disaster_type": "Flood", "alert_level": "Green"}
    assert should_filter_entry(gdacs("Flood in X"), details) is True


def test_gdacs_low_magnitude_earthquake_filtered_via_llm_details():
    details = {"disaster_type": "Earthquake", "severity": "5.5", "alert_level": "Orange"}
    assert should_filter_entry(gdacs("Orange earthquake in X"), details) is True


@pytest.mark.xfail(strict=True, reason="BUG: GDACS orange item whose summary merely mentions 'green alert' is dropped by the substring check")
def test_gdacs_orange_item_mentioning_green_alert_in_summary_is_kept():
    entry = gdacs("Orange flood alert in X",
                  summary="Upgraded from a green alert issued yesterday.")
    assert should_filter_entry(entry) is False


def test_spc_mesoscale_discussion_and_outlook_filtered_watch_kept():
    md = {"source_type": "noaa_spc", "title": "SPC MD 1234", "link": "https://s/products/md/md1234.html"}
    outlook = {"source_type": "noaa_spc", "title": "Day 1 Convective Outlook", "link": "https://s/x"}
    watch = {"source_type": "noaa_spc", "title": "SPC Tornado Watch 567", "link": "https://s/products/watch/ww0567.html"}
    assert should_filter_entry(md) is True
    assert should_filter_entry(outlook) is True
    assert should_filter_entry(watch) is False


def test_none_entry_is_filtered():
    assert should_filter_entry(None) is True


def test_unknown_source_type_is_kept():
    assert should_filter_entry({"source_type": "nhc", "title": "x", "link": "l"}) is False


@pytest.mark.xfail(strict=True, reason="BUG: JSON null alert_level makes should_filter_entry raise AttributeError (.lower on None) instead of being handled")
def test_gdacs_null_alert_level_does_not_raise():
    should_filter_entry(gdacs("Orange flood alert in X"),
                        {"disaster_type": "Flood", "alert_level": None})


@pytest.mark.xfail(strict=True, reason="BUG: JSON null disaster_type makes should_filter_entry raise AttributeError for USGS entries")
def test_usgs_null_disaster_type_does_not_raise():
    should_filter_entry(usgs("M 6.2 - X"), {"disaster_type": None, "severity": None})


# ---- normalize_date -----------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("2026-10-05", "2026-10-05"),
    ("2026-10-05T12:00:00Z", "2026-10-05"),
    ("2026-10-05T12:00:00+0000", "2026-10-05"),
    ("2026-10-05T12:00:00+00:00", "2026-10-05"),
    ("2026-10-05 12:00:00 UTC", "2026-10-05"),
    ("Mon, 05 Oct 2026 12:00:00 GMT", "2026-10-05"),
    ("05 Oct 2026", "2026-10-05"),
    ("October 5, 2026", "2026-10-05"),
    ("Updated: 2026-10-05", "2026-10-05"),
    ("", "Unknown Date"),
    (None, "Unknown Date"),
    ("not a date", "Unknown Date"),
])
def test_normalize_date_formats(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.xfail(strict=True, reason="BUG: GDACS dd/mm/yyyy '05/10/2026' is parsed as mm/dd (2026-05-10) because %m/%d/%Y is tried first")
def test_normalize_date_gdacs_day_first_slash_date():
    assert normalize_date("05/10/2026") == "2026-10-05"


@pytest.mark.xfail(strict=True, reason="BUG: RFC 2822 date with numeric '+0000' offset (common RSS pubDate) normalizes to 'Unknown Date'")
def test_normalize_date_rfc2822_numeric_offset():
    assert normalize_date("Mon, 05 Oct 2026 12:00:00 +0000") == "2026-10-05"
