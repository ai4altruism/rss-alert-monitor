### fetch_feeds.py

import calendar
import email.utils
import feedparser
import os
import re
import logging
import requests
import platform
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings

# Filter out the XML parsing warning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Load environment variables
load_dotenv()

# List of RSS Feeds
RSS_FEEDS = [
    os.getenv('RSS_FEED_1'),
    os.getenv('RSS_FEED_2'),
    os.getenv('RSS_FEED_3'),
    os.getenv('RSS_FEED_4'),
    os.getenv('RSS_FEED_5'),
    os.getenv('RSS_FEED_6')  # Added NHC feed
]

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("disaster_alert_bot.log"),
        logging.StreamHandler()
    ]
)

def get_user_agent():
    """
    Build a descriptive and ethical user agent string.
    
    Format: DisasterMonitor/1.1.0 (contact@yourorganization.org; https://yourorganization.org/disastermonitor) Python/3.9 Platform/Linux
    """
    version = "1.1.0"
    contact = os.getenv('CONTACT_EMAIL', 'team@ai4altruism.org')
    website = os.getenv('WEBSITE_URL', 'https://ai4altruism.org/disastermonitor')
    python_version = platform.python_version()
    system_platform = platform.system()
    
    return f"DisasterMonitor/{version} ({contact}; {website}) Python/{python_version} Platform/{system_platform}"

def _parse_report_datetime(report):
    """
    Best-effort parse of a report's publication time to an aware UTC
    datetime. Handles every format our sources emit: feedparser's
    *_parsed struct_time (RSS/Atom), ISO 8601 (ReliefWeb date.created),
    RFC 2822 (raw RSS pubDate), and WFIGS's '%Y-%m-%d %H:%M:%S UTC'.

    Returns None when no date is parseable — callers must treat undated
    reports as fresh (never drop a report just because its feed omits
    dates).
    """
    raw = report.get("raw_entry")
    if raw is not None:
        for key in ("published_parsed", "updated_parsed"):
            try:
                parsed = raw.get(key) if hasattr(raw, "get") else None
            except Exception:
                parsed = None
            if parsed:
                try:
                    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
                except (TypeError, ValueError, OverflowError):
                    pass

    published = (report.get("published") or "").strip()
    if not published:
        return None

    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    try:
        dt = email.utils.parsedate_to_datetime(published)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass

    try:
        return datetime.strptime(published, "%Y-%m-%d %H:%M:%S UTC").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def filter_stale_reports(reports, max_age_days=None):
    """
    Drop reports published more than max_age_days ago (default from
    MAX_ALERT_AGE_DAYS env var, or 3; 0 disables). Guards against a
    newly added source delivering its backlog (e.g. ReliefWeb's top-20
    disasters by creation date span weeks) — old events must never
    reach Slack as fresh alerts. Reports without a parseable date are
    kept.
    """
    if max_age_days is None:
        max_age_days = int(os.getenv("MAX_ALERT_AGE_DAYS", "3"))
    if not max_age_days or max_age_days <= 0:
        return reports

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    fresh = []
    stale_count = 0
    for report in reports:
        published = _parse_report_datetime(report)
        if published is not None and published < cutoff:
            stale_count += 1
            logging.info(
                f"Age guard: skipped stale report ({published:%Y-%m-%d}): "
                f"{report.get('title', 'Untitled')}"
            )
            continue
        fresh.append(report)

    if stale_count:
        logging.info(
            f"Age guard: skipped {stale_count} reports older than {max_age_days} days"
        )
    return fresh


def fetch_reliefweb_api(feed_url, headers):
    """
    Fetch disasters from the ReliefWeb API (v2) and map them to the standard
    entry format.

    ReliefWeb's RSS endpoint is WAF-blocked for datacenter IPs and its API v1
    was decommissioned; v2 requires a registered appname (embed it in the URL:
    https://apidoc.reliefweb.int/parameters#appname).

    Args:
        feed_url: Full API query URL, including appname and any field params.
        headers:  HTTP headers (User-Agent etc.).

    Returns:
        list of entry dicts (title/summary/link/published/source/source_type).
    """
    reports = []
    # The API returns ONLY requested fields (default: just `name`), so
    # explicitly request every field this parser reads. Without this,
    # `url` and `date.created` are absent: items then link to the raw
    # API href (HTTP 400 for users — no appname) and carry no date for
    # the age guard.
    field_params = {
        "fields[include][]": [
            "name", "url", "status", "date.created",
            "country.name", "type.name", "profile.overview",
        ]
    }
    response = requests.get(feed_url, headers=headers, params=field_params, timeout=15)
    response.raise_for_status()
    payload = response.json()

    for item in payload.get("data", []):
        fields = item.get("fields", {}) or {}
        title = fields.get("name") or "Unnamed disaster"
        # Never fall back to item['href']: that is the API JSON endpoint,
        # which returns 400 without an appname — useless as a Slack link.
        link = fields.get("url") or "https://reliefweb.int/disasters"

        countries = ", ".join(
            c.get("name", "") for c in fields.get("country", []) if c.get("name")
        )
        dtypes = ", ".join(
            t.get("name", "") for t in fields.get("type", []) if t.get("name")
        )
        status = fields.get("status", "")
        overview = (fields.get("profile", {}) or {}).get("overview", "") or ""

        summary_parts = []
        if dtypes:
            summary_parts.append(f"Disaster type: {dtypes}.")
        if countries:
            summary_parts.append(f"Affected country/countries: {countries}.")
        if status:
            summary_parts.append(f"Status: {status}.")
        if overview:
            summary_parts.append(overview[:800])
        summary = " ".join(summary_parts) or title

        published = (fields.get("date", {}) or {}).get("created", "")

        reports.append({
            "title": title,
            "summary": summary,
            "link": link,
            "published": published,
            "source": "ReliefWeb - Disasters",
            "source_type": "reliefweb",
            "raw_entry": item,
        })

    logging.info(f"Successfully fetched {len(reports)} entries from ReliefWeb API")
    return reports


def _wfigs_map_link(lat, lon):
    """
    Link a WFIGS incident to a map pin at its discovery coordinates.

    Constructed InciWeb URLs are NOT viable: WFIGS tracks every incident but
    InciWeb only publishes pages for team-managed ones (the rest render as
    empty stubs), and even published incidents may live at unpredictable slug
    variants (e.g. 'orwwf-anthony-fire' for incident 'Anthony'). A coordinate
    pin always resolves. Coordinates are rounded to 4 decimals so the link is
    deterministic run-to-run (it is also the dedup key).
    """
    if lat is None or lon is None:
        return "https://inciweb.wildfire.gov/"
    return f"https://www.google.com/maps/search/?api=1&query={lat:.4f},{lon:.4f}"


def fetch_wfigs_incidents(feed_url, headers):
    """
    Fetch active wildfires from NIFC's WFIGS ArcGIS feature service and map
    them to the standard entry format.

    InciWeb's RSS is WAF-blocked for datacenter IPs; WFIGS is the authoritative
    interagency dataset behind it and its ArcGIS endpoint is open. The full
    query URL lives in the env var, so the significance threshold (e.g.
    poly_GISAcres >= 1000) is tunable without a code change.

    Args:
        feed_url: Full ArcGIS query URL (f=json).
        headers:  HTTP headers (User-Agent etc.).

    Returns:
        list of entry dicts (title/summary/link/published/source/source_type).
    """
    reports = []
    response = requests.get(feed_url, headers=headers, timeout=20)
    response.raise_for_status()
    payload = response.json()

    if "error" in payload:
        raise RuntimeError(f"WFIGS query error: {payload['error']}")

    for feature in payload.get("features", []):
        a = feature.get("attributes", {}) or {}
        name = a.get("attr_IncidentName") or a.get("IncidentName") or "Unnamed incident"
        state = a.get("attr_POOState") or a.get("POOState") or ""
        county = a.get("attr_POOCounty") or a.get("POOCounty") or ""
        def attr(*names):
            for n in names:
                if a.get(n) is not None:
                    return a[n]
            return None

        acres = attr("poly_GISAcres", "DailyAcres")
        contained = attr("attr_PercentContained", "PercentContained")
        lat = attr("attr_InitialLatitude", "InitialLatitude")
        lon = attr("attr_InitialLongitude", "InitialLongitude")

        title = f"{name} Fire" if not name.lower().endswith(("fire", "complex")) else name
        if state:
            title += f" ({state})"

        # Compact stats shown on the item line itself — the link is only a
        # map pin, so the substance must be in the message.
        note_parts = []
        if acres:
            note_parts.append(f"~{round(acres):,} acres")
        if contained is not None:
            note_parts.append(f"{round(contained)}% contained")
        loc = ", ".join(p for p in (county and f"{county} County", state) if p)
        if loc:
            note_parts.append(loc)
        note = ", ".join(note_parts)

        summary = "Active wildfire." + (f" {note}." if note else "")

        # WFIGS timestamps are epoch milliseconds
        published = ""
        ts = a.get("attr_ModifiedOnDateTime_dt") or a.get("attr_FireDiscoveryDateTime")
        if ts:
            published = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )

        reports.append({
            "title": title,
            "summary": summary,
            "note": note,
            "link": _wfigs_map_link(lat, lon),
            "published": published,
            "source": "NIFC WFIGS",
            "source_type": "inciweb",
            "raw_entry": a,
        })

    logging.info(f"Successfully fetched {len(reports)} entries from NIFC WFIGS")
    return reports


def fetch_rss_feeds(feeds):
    """
    Fetches RSS feeds and extracts relevant disaster reports.
    Also performs initial filtering directly on XML data.
    JSON API sources (ReliefWeb API, NIFC WFIGS) are dispatched by URL.
    """
    disaster_reports = []
    
    # Create a descriptive and respectful user agent
    user_agent = get_user_agent()
    headers = {"User-Agent": user_agent}
    
    logging.info(f"Using User-Agent: {user_agent}")
    
    for feed_url in feeds:
        if not feed_url:
            logging.warning("Skipping empty feed URL.")
            continue

        logging.info(f"Fetching feed: {feed_url}")

        # JSON API sources — not RSS, handled by dedicated fetchers
        if "api.reliefweb.int" in feed_url.lower():
            try:
                disaster_reports.extend(fetch_reliefweb_api(feed_url, headers))
            except Exception as e:
                logging.error(f"Error fetching ReliefWeb API {feed_url}: {e}")
            continue

        if "arcgis.com" in feed_url.lower():
            try:
                disaster_reports.extend(fetch_wfigs_incidents(feed_url, headers))
            except Exception as e:
                logging.error(f"Error fetching WFIGS {feed_url}: {e}")
            continue

        try:
            # Fetch the feed content manually to avoid parsing errors
            response = requests.get(feed_url, headers=headers, timeout=15)
            response.raise_for_status()
            
            # Pre-filter for GDACS green alerts directly in XML when possible
            if "gdacs.org" in feed_url.lower():
                try:
                    # Check if content is actually XML
                    xml_content = response.text
                    
                    # Skip if empty content
                    if not xml_content.strip():
                        logging.warning(f"Empty content received from GDACS feed")
                    else:
                        # Try parsing with BeautifulSoup which is more forgiving with malformed XML
                        # Try different parsers in order of preference
                        try:
                            # Try lxml-xml parser first (proper XML parser)
                            soup = BeautifulSoup(xml_content, 'lxml-xml')
                        except:
                            try:
                                # Try lxml (HTML parser) next
                                soup = BeautifulSoup(xml_content, 'lxml')
                            except:
                                try:
                                    # Try html5lib if available
                                    soup = BeautifulSoup(xml_content, 'html5lib')
                                except:
                                    # Fallback to built-in html.parser
                                    soup = BeautifulSoup(xml_content, 'html.parser')
                        
                        # Find all items - first try with standard RSS path
                        items = soup.find_all('item')
                        
                        if not items:
                            # If no items found, try with namespace prefix
                            items = soup.select('rss channel item')
                        
                        filtered_count = 0
                        for item in items:
                            # Look for alert level in various ways
                            is_green_alert = False
                            
                            # Check for gdacs:alertlevel element directly
                            alert_elem = item.find(lambda tag: tag.name.endswith('alertlevel'))
                            if alert_elem and alert_elem.text.lower() == 'green':
                                is_green_alert = True
                            
                            # Also check title as fallback
                            title_elem = item.find('title')
                            if title_elem and title_elem.text and title_elem.text.lower().startswith('green '):
                                is_green_alert = True
                            
                            if is_green_alert:
                                title = title_elem.text if title_elem else "Unknown title"
                                logging.info(f"Pre-filtered GDACS green alert in XML parsing: {title}")
                                filtered_count += 1
                                
                                # Remove this item from the XML
                                item.decompose()
                        
                        if filtered_count > 0:
                            logging.info(f"Pre-filtered {filtered_count} GDACS green alerts in XML parsing")
                            
                            # Convert modified XML back to string for feedparser
                            modified_xml = str(soup)
                            response._content = modified_xml.encode('utf-8')
                            
                except Exception as e:
                    logging.warning(f"Error in GDACS XML pre-filtering: {str(e)}")
                    # Fall back to regular feedparser if XML parsing fails
            
            # Pre-filter for SPC Mesoscale Discussions and Outlooks
            if "spc.noaa.gov" in feed_url.lower():
                try:
                    # Check if content is actually XML
                    xml_content = response.text
                    
                    # Skip if empty content
                    if not xml_content.strip():
                        logging.warning(f"Empty content received from SPC feed")
                    else:
                        # Try parsing with BeautifulSoup which is more forgiving with malformed XML
                        # Try different parsers in order of preference
                        try:
                            # Try lxml-xml parser first (proper XML parser)
                            soup = BeautifulSoup(xml_content, 'lxml-xml')
                        except:
                            try:
                                # Try lxml (HTML parser) next
                                soup = BeautifulSoup(xml_content, 'lxml')
                            except:
                                try:
                                    # Try html5lib if available
                                    soup = BeautifulSoup(xml_content, 'html5lib')
                                except:
                                    # Fallback to built-in html.parser
                                    soup = BeautifulSoup(xml_content, 'html.parser')
                        
                        # Find all items
                        items = soup.find_all('item')
                        
                        if not items:
                            # If no items found, try with namespace prefix
                            items = soup.select('rss channel item')
                        
                        total_items = len(items)
                        filtered_count = 0
                        items_to_remove = []
                        
                        for item in items:
                            # Get link and title
                            link_elem = item.find('link')
                            title_elem = item.find('title')
                            
                            link = link_elem.text if link_elem else ""
                            title = title_elem.text if title_elem else ""
                            
                            # Skip Mesoscale Discussions and Outlooks
                            if (link and ("/md/" in link.lower() or "/outlook/" in link.lower())) or \
                               (title and (title.lower().startswith("spc md") or "outlook" in title.lower())):
                                logging.info(f"Pre-filtered SPC report in XML parsing: {title}")
                                filtered_count += 1
                                items_to_remove.append(item)
                        
                        # Remove filtered items after iterating
                        for item in items_to_remove:
                            item.decompose()
                        
                        if filtered_count > 0:
                            logging.info(f"Pre-filtered {filtered_count} of {total_items} SPC reports in XML parsing")
                            
                            # Convert modified XML back to string for feedparser
                            modified_xml = str(soup)
                            response._content = modified_xml.encode('utf-8')
                            
                except Exception as e:
                    logging.warning(f"Error in SPC XML pre-filtering: {str(e)}")
                    # Fall back to regular feedparser if XML parsing fails
            
            # Parse feed with feedparser
            feed = feedparser.parse(response.text)
            
            # Identify feed source for later use in extraction
            feed_source_url = feed_url.lower()
            feed_source_type = None
            
            if "gdacs.org" in feed_source_url:
                feed_source_type = "gdacs"
            elif "reliefweb.int" in feed_source_url:
                feed_source_type = "reliefweb"
            elif "wildfire.gov" in feed_source_url:
                feed_source_type = "inciweb"
            elif "spc.noaa.gov" in feed_source_url:
                feed_source_type = "noaa_spc"
            elif "usgs.gov" in feed_source_url:
                feed_source_type = "usgs"
            elif "nhc.noaa.gov" in feed_source_url:
                feed_source_type = "nhc"
            
            # Determine feed title
            feed_title = "Unknown Source"
            
            # First try to get from feed object
            if 'feed' in feed and 'title' in feed.feed:
                feed_title = feed.feed.get('title', 'Unknown Source')
            
            # Set default feed titles based on source URL if title not found
            if feed_title == "Unknown Source":
                if "gdacs.org" in feed_source_url:
                    feed_title = "GDACS RSS information"
                elif "reliefweb.int" in feed_source_url:
                    feed_title = "ReliefWeb - Disasters"
                elif "wildfire.gov" in feed_source_url:
                    feed_title = "InciWeb"
                elif "spc.noaa.gov" in feed_source_url:
                    feed_title = "SPC Forecast Products"
                elif "usgs.gov" in feed_source_url:
                    feed_title = "USGS Magnitude 4.5+ Earthquakes"
                elif "nhc.noaa.gov" in feed_source_url:
                    feed_title = "National Hurricane Center"

            # Log successful fetch with entry count
            entry_count = len(feed.entries) if hasattr(feed, 'entries') else 0
            logging.info(f"Successfully fetched {entry_count} entries from {feed_title}")
            
            for entry in feed.entries:
                # Extract common fields with fallbacks
                title = entry.get('title', 'No Title')
                summary = entry.get('summary', entry.get('description', 'No Summary'))
                link = entry.get('link', 'No Link')
                published = entry.get('published', entry.get('updated', 'No Published Date'))
                
                # Final GDACS green alert filtering
                if feed_source_type == "gdacs":
                    title_lower = title.lower()
                    if (title_lower.startswith("green ") or 
                        "green alert" in title_lower or 
                        "gdacs:alertlevel>green" in str(entry).lower()):
                        logging.info(f"Filtered GDACS green alert in feedparser stage: {title}")
                        continue
                
                # Final SPC report filtering
                if feed_source_type == "noaa_spc":
                    link_lower = link.lower()
                    title_lower = title.lower()
                    if ("/md/" in link_lower or 
                        title_lower.startswith("spc md") or
                        "/outlook/" in link_lower or 
                        "outlook" in title_lower):
                        logging.info(f"Filtered SPC report in feedparser stage: {title}")
                        continue
                
                # Create a disaster report with feed source type
                disaster_reports.append({
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": published,
                    "source": feed_title,
                    "source_type": feed_source_type,  # Add source type for specialized processing
                    "raw_entry": entry  # Store the raw entry for additional parsing if needed
                })
        
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching feed {feed_url}: {e}")
        except Exception as e:
            logging.error(f"Unexpected error processing feed {feed_url}: {e}")

    disaster_reports = filter_stale_reports(disaster_reports)

    logging.info(f"Total disaster reports fetched: {len(disaster_reports)}")
    return disaster_reports

if __name__ == "__main__":
    disasters = fetch_rss_feeds(RSS_FEEDS)
    for disaster in disasters:
        print(f"{disaster['title']} - {disaster['published']}")
        print(f"Summary: {disaster['summary']}")
        print(f"Source: {disaster['source']} ({disaster['source_type']})")
        print(f"More Info: {disaster['link']}\n")