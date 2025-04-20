### fetch_feeds.py

import feedparser
import os
import logging
import requests
import platform
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

def fetch_rss_feeds(feeds):
    """
    Fetches RSS feeds and extracts relevant disaster reports.
    Also performs initial filtering directly on XML data.
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

    logging.info(f"Total disaster reports fetched: {len(disaster_reports)}")
    return disaster_reports

if __name__ == "__main__":
    disasters = fetch_rss_feeds(RSS_FEEDS)
    for disaster in disasters:
        print(f"{disaster['title']} - {disaster['published']}")
        print(f"Summary: {disaster['summary']}")
        print(f"Source: {disaster['source']} ({disaster['source_type']})")
        print(f"More Info: {disaster['link']}\n")