### structured_details.py
"""
Deterministic disaster details for the feeds that publish structured data.

USGS, GDACS, NIFC WFIGS and ReliefWeb already state the type, place, date and
severity of each event in fixed fields or a fixed title pattern. Reading them
in code means the numbers in an alert are the source's numbers: no model can
misread a magnitude, invent an acreage, or attribute one alert's details to
another. Only SPC and NHC, whose products are prose, still go to the model.

parse_details returns the same dict shape the model extraction produces
(disaster_type, location, date, severity, alert_level, description), or None
when the report is not from a structured source or lacks the fields.
"""

import re

# USGS atom titles: "M 4.7 - 20 km SW of Sandy Point Town, Saint Kitts and Nevis"
_USGS_TITLE = re.compile(r"^\s*M\s*([0-9]+(?:\.[0-9]+)?)\s*-\s*(.+?)\s*$", re.IGNORECASE)

_GDACS_TYPES = {
    "EQ": "earthquake",
    "TC": "tropical cyclone",
    "FL": "flood",
    "VO": "volcano",
    "DR": "drought",
    "WF": "wildfire",
    "TS": "tsunami",
}


def _details(disaster_type, location, date, severity=None, alert_level=""):
    return {
        "disaster_type": disaster_type,
        "location": location or "Unknown Location",
        "date": date or "Unknown Date",
        "severity": severity,
        "alert_level": alert_level or "",
        "description": "",
    }


def _get(raw, key):
    """Field from a feedparser entry or a plain dict."""
    if raw is None:
        return None
    try:
        return raw.get(key)
    except AttributeError:
        return getattr(raw, key, None)


def _usgs(report):
    m = _USGS_TITLE.match(report.get("title") or "")
    if not m:
        return None
    # Severity is the bare magnitude: should_filter_entry compares it as a
    # number against the 5.8 / 6.0 thresholds.
    return _details("earthquake", m.group(2), report.get("published"), m.group(1))


def _gdacs(report):
    raw = report.get("raw_entry")
    event_type = (_get(raw, "gdacs_eventtype") or "").upper()
    if event_type not in _GDACS_TYPES:
        return None
    severity = None
    sev = _get(raw, "gdacs_severity")
    value = sev.get("value") if isinstance(sev, dict) else None
    unit = (sev.get("unit") if isinstance(sev, dict) else "") or ""
    if value:
        try:
            number = float(value)
        except ValueError:
            number = None
        if event_type == "EQ" and number is not None:
            severity = f"{number:g}"  # bare magnitude, as for USGS
        elif number is not None and unit:
            severity = f"{round(number):,} {unit}"
    location = _get(raw, "gdacs_country") or ""
    name = _get(raw, "gdacs_eventname") or ""
    if name and event_type in ("TC", "VO"):
        location = f"{name}, {location}" if location else name
    return _details(
        _GDACS_TYPES[event_type],
        location,
        _get(raw, "gdacs_fromdate") or report.get("published"),
        severity,
        (_get(raw, "gdacs_alertlevel") or "").lower(),
    )


def _wfigs(report):
    raw = report.get("raw_entry") or {}
    state = raw.get("attr_POOState") or raw.get("POOState") or ""
    county = raw.get("attr_POOCounty") or raw.get("POOCounty") or ""
    location = ", ".join(p for p in (county and f"{county} County", state.replace("US-", "")) if p)
    # The fetcher's note already reads "~12,345 acres, 40% contained, X County, US-OR"
    note = report.get("note") or ""
    severity = ", ".join(p for p in note.split(", ") if "acres" in p or "contained" in p) or None
    return _details("wildfire", location, report.get("published"), severity)


def _reliefweb(report):
    raw = report.get("raw_entry") or {}
    fields = raw.get("fields", {}) or {}
    types = [t.get("name") for t in fields.get("type", []) if t.get("name")]
    countries = [c.get("name") for c in fields.get("country", []) if c.get("name")]
    if not types and not countries:
        return None
    return _details(
        (types[0] if types else "disaster").lower(),
        ", ".join(countries),
        (fields.get("date", {}) or {}).get("created") or report.get("published"),
    )


_PARSERS = {
    "usgs": _usgs,
    "gdacs": _gdacs,
    "inciweb": _wfigs,
    "reliefweb": _reliefweb,
}


def parse_details(report):
    """Details read from a structured source's own fields, or None."""
    parser = _PARSERS.get((report.get("source_type") or "").lower())
    return parser(report) if parser else None
