### process_data.py

import os
import json
import re  # Make sure this import is present
from openai import OpenAI, APIError, APIConnectionError, RateLimitError
from fetch_feeds import fetch_rss_feeds
from dotenv import load_dotenv
from collections import defaultdict
import datetime
import logging
import time
from send_to_slack import send_disaster_alert_block
from bs4 import BeautifulSoup

# Load environment variables from .env file
load_dotenv()

# Load the model name from environment variables
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "gpt-3.5-turbo")  # Use a faster/cheaper model for extraction
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))  # Number of entries to process in a single API call

# Configure logging with both file and console handlers
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("disaster_alert_bot.log"),
        logging.StreamHandler()
    ]
)

# Initialize OpenAI client with the latest SDK (v1)
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

def normalize_date(date_str):
    """
    Normalize various date formats to ISO 8601 (YYYY-MM-DD).
    """
    # Handle None or empty strings
    if not date_str:
        return 'Unknown Date'
        
    # Try various date formats
    for fmt in (
        '%a, %d %b %Y %H:%M:%S %Z',  # RFC 822 format
        '%Y-%m-%dT%H:%M:%SZ',        # ISO 8601 with Z
        '%Y-%m-%dT%H:%M:%S%z',       # ISO 8601 with timezone offset
        '%Y-%m-%d %H:%M:%S UTC',     # Custom UTC format
        '%Y-%m-%d %H:%M:%S',         # Standard datetime
        '%Y-%m-%d',                  # Just date
        '%d %b %Y',                  # 25 Dec 2023
        '%B %d, %Y',                 # December 25, 2023
        '%m/%d/%Y',                  # MM/DD/YYYY
        '%d/%m/%Y',                  # DD/MM/YYYY
    ):
        try:
            return datetime.datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue
            
    # Check for special patterns like "Updated: 2023-12-25"
    date_match = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', date_str)
    if date_match:
        try:
            extracted_date = date_match.group(1).replace('/', '-')
            return normalize_date(extracted_date)  # Recursively normalize this extracted date
        except (ValueError, TypeError):
            pass
            
    # If all attempts fail
    return 'Unknown Date'

def should_filter_entry(entry, entry_details=None):
    """
    Determine if an entry should be filtered out based on severity/magnitude.
    Returns True if entry should be filtered out, False if it should be kept.
    """
    # Guard against None values
    if entry is None:
        logging.warning("Received None entry in should_filter_entry")
        return True
        
    source_type = entry.get("source_type", "").lower() if isinstance(entry, dict) else ""
    title = entry.get("title", "").lower() if isinstance(entry, dict) else ""
    summary = entry.get("summary", "").lower() if isinstance(entry, dict) else ""
    link = entry.get("link", "").lower() if isinstance(entry, dict) else ""
    
    # NOAA SPC specific filtering
    if source_type == "noaa_spc":
        # Filter out Mesoscale Discussions
        if "/md/" in link or title.startswith("spc md"):
            logging.info(f"Filtering out SPC Mesoscale Discussion: {title}")
            return True
            
        # Filter out Outlook reports
        if "/outlook/" in link or "outlook" in title.lower():
            logging.info(f"Filtering out SPC Outlook report: {title}")
            return True
            
    # GDACS specific filtering - CRITICAL: Check different ways to identify GDACS green alerts
    if source_type == "gdacs":
        # 1. Check title - most GDACS alerts start with their level in the title
        if title.startswith("green "):
            logging.info(f"Filtering out GDACS green alert from title start: {title}")
            return True
            
        # 2. Check keyword in title or summary
        if "green alert" in title or "green alert" in summary:
            logging.info(f"Filtering out GDACS green alert from title/summary content: {title}")
            return True
            
        # 3. Check if XML data is available directly - this depends on feedparser structure
        if isinstance(entry, dict):
            # Try to access GDACS namespace elements if available
            for k, v in entry.items():
                if 'alertlevel' in k.lower() and isinstance(v, str) and v.lower() == 'green':
                    logging.info(f"Filtering out GDACS green alert from XML element: {title}")
                    return True

        # 4. Check if entry has raw XML content to parse
        raw_xml = str(entry)
        if 'alertlevel' in raw_xml.lower() and 'green' in raw_xml.lower():
            # Look for patterns like alertlevel>Green or AlertLevel>Green
            if re.search(r'alertlevel\s*>\s*green', raw_xml, re.IGNORECASE):
                logging.info(f"Filtering out GDACS green alert from raw XML content: {title}")
                return True
                
        # 5. Use LLM-extracted details as final check
        if entry_details and isinstance(entry_details, dict):
            alert_level = entry_details.get("alert_level", "").lower()
            if alert_level == "green":
                logging.info(f"Filtering out GDACS green alert from LLM extraction: {title} (Level: {alert_level})")
                return True
                
        # 6. Fallback approach - if title contains "Green earthquake" pattern
        if "green earthquake" in title:
            logging.info(f"Filtering out GDACS green earthquake from title pattern: {title}")
            return True
            
        # 7. Parse for green icon URL
        if isinstance(entry, dict) and 'icon' in entry:
            icon_url = entry.get('icon', '')
            if 'green' in icon_url.lower() and 'eq' in icon_url.lower():
                logging.info(f"Filtering out GDACS green alert from icon URL: {title}")
                return True
            
        # 8. For GDACS earthquakes, also apply magnitude threshold
        if entry_details and isinstance(entry_details, dict):
            disaster_type = entry_details.get("disaster_type", "").lower()
            if disaster_type == "earthquake":
                magnitude = entry_details.get("severity", "")
                if isinstance(magnitude, str) and magnitude.replace('.', '').isdigit():
                    try:
                        magnitude = float(magnitude)
                        if magnitude < 6.0:
                            logging.info(f"Filtering out low magnitude GDACS earthquake: {title} (M{magnitude})")
                            return True
                    except ValueError:
                        pass
    
    # USGS specific filtering for earthquakes
    elif source_type == "usgs":
        # Try to extract magnitude from USGS title (they often start with magnitude)
        magnitude_match = re.search(r'(?:^|\s)(?:m|magnitude)\s*(\d+\.?\d*)', title, re.IGNORECASE)
        if magnitude_match:
            try:
                magnitude = float(magnitude_match.group(1))
                if magnitude < 5.8:
                    logging.info(f"Filtering out low magnitude USGS earthquake from title: {title} (M{magnitude})")
                    return True
            except (ValueError, IndexError):
                pass
            
        # If no match in title, try summary
        if not magnitude_match:
            magnitude_match = re.search(r'(?:^|\s)(?:m|magnitude)\s*(\d+\.?\d*)', summary, re.IGNORECASE)
            if magnitude_match:
                try:
                    magnitude = float(magnitude_match.group(1))
                    if magnitude < 5.8:
                        logging.info(f"Filtering out low magnitude USGS earthquake from summary: {title} (M{magnitude})")
                        return True
                except (ValueError, IndexError):
                    pass
        
        # Use LLM-extracted info for USGS as a backup
        if entry_details and isinstance(entry_details, dict):
            disaster_type = entry_details.get("disaster_type", "").lower()
            if disaster_type == "earthquake":
                magnitude = entry_details.get("severity", "")
                if isinstance(magnitude, str) and magnitude.replace('.', '').isdigit():
                    try:
                        magnitude = float(magnitude)
                        if magnitude < 5.8:
                            logging.info(f"Filtering out low magnitude USGS earthquake from LLM: {title} (M{magnitude})")
                            return True
                    except ValueError:
                        pass
    
    # If we've made it here, the entry shouldn't be filtered
    return False

def prepare_entry_for_extraction(entry):
    """
    Prepare an entry for extraction by cleaning its fields.
    """
    # Extract text from HTML content if needed
    title = entry.get('title', '')
    raw_summary = entry.get('summary', '')
    
    if isinstance(raw_summary, str) and (raw_summary.startswith('<') or '<' in raw_summary):
        try:
            summary = BeautifulSoup(raw_summary, 'html.parser').get_text(separator=' ')
        except Exception as e:
            logging.warning(f"Error parsing HTML summary: {e}")
            summary = raw_summary
    else:
        summary = raw_summary
        
    source = entry.get('source', '')
    source_type = entry.get('source_type', '')
    published = entry.get('published', '')
    
    return {
        "id": entry.get('link', ''),  # Use link as a unique identifier
        "title": title,
        "summary": summary[:1000] if summary else "",  # Truncate long summaries
        "source": source,
        "source_type": source_type,
        "published": published
    }

def extract_details_in_batch(entries, max_retries=2, backoff_factor=2):
    """
    Extract structured disaster information for multiple entries using a single LLM call.
    
    Args:
        entries: List of prepared entry dictionaries
        
    Returns:
        Dictionary mapping entry IDs to their extracted details
    """
    if not entries:
        return {}
        
    # Create a batch prompt
    batch_prompt = """
    Extract detailed information from each of the following disaster alerts.
    
    For each alert, extract these fields:
    - disaster_type: The specific type of disaster (earthquake, hurricane, wildfire, flood, tornado, etc.)
    - location: The affected location or region
    - date: The date of the event (YYYY-MM-DD format if possible)
    - severity: Any severity information (magnitude, category, intensity, etc.)
    - alert_level: For GDACS alerts, specifically extract if it's a green, orange, or red alert
    - description: A brief description of what happened
    
    If you cannot find some information, use null for that field.
    
    IMPORTANT INSTRUCTIONS:
    1. For GDACS alerts, always look for and extract the alert level (Green, Orange, Red).
       Green alerts from GDACS should have "Green" in the alert_level field.
    
    2. For earthquakes, especially from USGS and GDACS sources:
       - Always extract the exact magnitude value in the severity field
       - If the magnitude is mentioned as "M5.7" or "magnitude 5.7", extract "5.7" as the severity
       - Look for magnitude information in both the title and summary
    
    IMPORTANT: Return your response as a JSON object with this EXACT structure:
    {
      "results": [
        {
          "id": "alert_id_1",
          "disaster_type": "type",
          "location": "location",
          "date": "date",
          "severity": "severity",
          "alert_level": "level",
          "description": "description"
        },
        {
          "id": "alert_id_2",
          ...
        }
      ]
    }
    
    Here are the alerts to process:
    """
    
    # Add each entry to the prompt
    for i, entry in enumerate(entries):
        batch_prompt += f"\n--- ALERT {i+1} (ID: {entry['id']}) ---\n"
        batch_prompt += f"Source: {entry['source']}\n"
        batch_prompt += f"Source Type: {entry['source_type']}\n"
        batch_prompt += f"Title: {entry['title']}\n"
        batch_prompt += f"Summary: {entry['summary']}\n"
        batch_prompt += f"Published Date: {entry['published']}\n"
        
        # Add source-specific guidance
        if entry['source_type'] == "noaa_spc":
            batch_prompt += "Note: This is from NOAA SPC. Look for severe weather details, affected regions, and MD numbers.\n"
        elif entry['source_type'] == "nhc":
            batch_prompt += "Note: This is from NHC. Look for tropical cyclone information, basin, and formation probability.\n"
        elif entry['source_type'] == "usgs":
            batch_prompt += "Note: This is from USGS. Extract the exact magnitude and location from earthquake reports. The magnitude is critical for filtering.\n"
        elif entry['source_type'] == "gdacs":
            batch_prompt += "Note: This is from GDACS. Carefully extract the alert level (Green, Orange, Red) and include it in the alert_level field. Also extract the magnitude for earthquakes.\n"
    
    # Call the API with retries
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=EXTRACTION_MODEL,
                messages=[{"role": "user", "content": batch_prompt}],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=4000  # Increased for batch processing
            )
            
            result_text = response.choices[0].message.content
            
            try:
                # Parse the JSON response
                parsed_results = json.loads(result_text)
                
                # The response could be in different formats - handle them appropriately
                if isinstance(parsed_results, dict):
                    if 'results' in parsed_results:
                        # Format: {"results": [...]}
                        results_array = parsed_results['results']
                    else:
                        # Format: Single result as dict (shouldn't happen but handle it)
                        logging.warning("Received single result dict instead of array")
                        results_array = [parsed_results] if 'id' in parsed_results else []
                elif isinstance(parsed_results, list):
                    # Format: Direct array of results
                    results_array = parsed_results
                else:
                    # Unexpected format
                    logging.warning(f"Unexpected response structure from LLM: {type(parsed_results)}")
                    results_array = []
                    
                # Log the structure for debugging
                logging.debug(f"Parsed result structure: {type(parsed_results)}")
                if not results_array:
                    logging.warning(f"Empty results array after parsing. Raw response: {result_text[:500]}...")
                
                # Create a dictionary mapping entry IDs to their details
                details_map = {}
                
                # Process each result
                for result in results_array:
                    entry_id = result.get('id')
                    if entry_id:
                        # Normalize the date
                        if result.get("date"):
                            result["date"] = normalize_date(result["date"])
                        
                        # Store the details
                        details_map[entry_id] = {
                            "disaster_type": result.get("disaster_type", "Unknown Type"),
                            "location": result.get("location", "Unknown Location"),
                            "date": result.get("date", "Unknown Date"),
                            "severity": result.get("severity"),
                            "alert_level": result.get("alert_level", ""),  # Added alert_level field
                            "description": result.get("description", "")
                        }
                
                # Create fallback entries for any missing IDs
                for entry in entries:
                    if entry['id'] not in details_map:
                        details_map[entry['id']] = {
                            "disaster_type": "Unknown Type",
                            "location": "Unknown Location",
                            "date": normalize_date(entry['published']),
                            "severity": None,
                            "alert_level": "",  # Added alert_level field
                            "description": entry['summary'][:100] + "..." if len(entry['summary']) > 100 else entry['summary']
                        }
                
                logging.info(f"Successfully extracted details for {len(details_map)} entries in batch")
                return details_map
                
            except json.JSONDecodeError as e:
                logging.error(f"Error parsing LLM JSON response: {e}")
                logging.debug(f"Raw response: {result_text}")
                
        except (APIError, APIConnectionError, RateLimitError) as e:
            logging.error(f"OpenAI API error on attempt {attempt}: {e}")
            
        except Exception as e:
            logging.error(f"Unexpected error extracting details: {e}")
        
        if attempt < max_retries:
            sleep_time = backoff_factor ** attempt
            time.sleep(sleep_time)
    
    # If all retries fail, create fallback details for all entries
    fallback_details = {}
    for entry in entries:
        fallback_details[entry['id']] = {
            "disaster_type": "Unknown Type",
            "location": "Unknown Location",
            "date": normalize_date(entry['published']),
            "severity": None,
            "alert_level": "",  # Added alert_level field
            "description": entry['summary'][:100] + "..." if len(entry['summary']) > 100 else entry['summary']
        }
    
    return fallback_details

def extract_details_with_llm(entry, max_retries=2, backoff_factor=2):
    """
    Extract structured disaster information using an LLM.
    This is now a wrapper around the batch function, for backward compatibility.
    """
    prepared_entry = prepare_entry_for_extraction(entry)
    batch_results = extract_details_in_batch([prepared_entry], max_retries, backoff_factor)
    
    # Return the details for this entry, or a fallback if not found
    return batch_results.get(prepared_entry['id'], {
        "disaster_type": "Unknown Type",
        "location": "Unknown Location",
        "date": normalize_date(entry.get('published', '')),
        "severity": None,
        "alert_level": "",  # Added alert_level field
        "description": prepared_entry['summary'][:100] + "..." if len(prepared_entry['summary']) > 100 else prepared_entry['summary']
    })

def group_disasters(disasters):
    """
    Group disasters by information extracted via LLM, using batch processing.
    """
    grouped = defaultdict(list)
    
    # Filter out obvious non-matching entries first (before API calls)
    initial_filtered = [d for d in disasters if not should_filter_entry(d)]
    
    if not initial_filtered:
        return grouped
    
    logging.info(f"Processing {len(initial_filtered)} entries after initial filtering")
    
    # Prepare entries for batch processing
    prepared_entries = [prepare_entry_for_extraction(entry) for entry in initial_filtered]
    
    # Process in batches to avoid token limits
    all_details = {}
    
    for i in range(0, len(prepared_entries), BATCH_SIZE):
        batch = prepared_entries[i:i+BATCH_SIZE]
        logging.info(f"Processing batch {i//BATCH_SIZE + 1} with {len(batch)} entries")
        
        batch_details = extract_details_in_batch(batch)
        all_details.update(batch_details)
    
    # Match the extracted details back to the original entries and group them
    for disaster in initial_filtered:
        if not isinstance(disaster, dict):
            logging.warning(f"Unexpected entry type: {type(disaster)}")
            continue
            
        entry_id = disaster.get('link', '')
        details = all_details.get(entry_id)
        
        if not details:
            logging.warning(f"No details found for entry {entry_id}")
            continue
            
        # Skip filtered entries (based on extracted details)
        try:
            if should_filter_entry(disaster, details):
                continue
        except Exception as e:
            logging.error(f"Error in should_filter_entry: {e}")
            # Continue processing this entry despite the filter error
            # This ensures we don't lose alerts due to filtering issues
            
        # Use extracted information for grouping
        disaster_type = details.get("disaster_type", "Unknown Type")
        location = details.get("location", "Unknown Location")
        date = details.get("date", "Unknown Date")
        severity = details.get("severity")
        
        # Include alert level in the key for GDACS entries
        if disaster.get("source_type") == "gdacs" and details.get("alert_level"):
            alert_level = details.get("alert_level", "").capitalize()
            key = f"{disaster_type} ({alert_level} Alert) in {location} on {date}"
        # Include severity in the key if available
        elif severity:
            key = f"{disaster_type} ({severity}) in {location} on {date}"
        else:
            key = f"{disaster_type} in {location} on {date}"
            
        # Handle special case for SPC Mesoscale Discussions
        if disaster.get("source_type") == "noaa_spc" and "spc md" in disaster.get("title", "").lower():
            # Try to extract the MD number
            md_match = re.search(r'md\s+(\d+)', disaster.get("title", "").lower())
            if md_match:
                md_number = md_match.group(1)
                key = f"Severe Weather Discussion (MD {md_number})"
        
        # Store the original disaster data along with the extracted details
        grouped[key].append({
            "title": disaster["title"],
            "summary": disaster.get("summary", ""),
            "link": disaster["link"],
            "published": disaster["published"],
            "source": disaster["source"],
            "details": details
        })
    
    return grouped

def process_disasters(disasters, max_retries=3, backoff_factor=2):
    """
    Process disasters by grouping and preparing summaries.
    Implements retries with exponential backoff for OpenAI API calls.
    """
    grouped = group_disasters(disasters)

    # If no disasters after filtering, return None
    if not grouped:
        logging.info("No disasters to process after filtering")
        return None

    # Prepare the prompt for the LLM
    prompt = """
    You are an assistant that processes disaster reports and formats them for a Slack workspace. 
    De-duplicate and aggregate by disaster type. For each disaster, provide a summary including key details and links.
    
    IMPORTANT FORMAT REQUIREMENTS:
    1. For each disaster group, print a line starting with '### ' followed by the group name.
    2. Then provide a brief 1-2 sentence summary that includes:
       - What happened (disaster type)
       - Where it happened (specific location)
       - When it happened (date)
       - How severe it was (magnitude, category, etc. if available)
       - For GDACS alerts, include the alert level (Orange or Red)
    3. Then, for each item in that group, print bullet lines that start with '- **Title:**' followed by the item title. Then new lines for:
       - **Published:**
       - **Source:**
       - **Link:**
    4. Separate each group with a line that only contains three dashes: '---'.
    5. Do NOT add extra headings or emojis outside of each group. End after listing all groups.
    
    <Here is the grouped data...>
    """

    for group, reports in grouped.items():
        prompt += f"\n### {group}\n"
        
        # Include extracted details in the prompt
        if reports and "details" in reports[0]:
            details = reports[0]["details"]
            prompt += "Extracted details:\n"
            for key, value in details.items():
                if value:  # Only include non-empty values
                    prompt += f"- {key}: {value}\n"
            prompt += "\n"
        
        for report in reports:
            # Format with Slack-style links using <URL|Link Text>
            link_text = "More Info"
            # Ensure the link is properly formatted with no pipe character in URL
            url = report.get('link', '#')
            # Double check the URL is valid (not empty and does not already contain a pipe)
            if not url or url == '#':
                url = 'https://example.com/no-link-available'
            url = url.replace('%7C', '%7c').replace('|', '%7c')  # Replace any pipe chars with encoded version
            
            formatted_link = f"<{url}|{link_text}>"
            prompt += (f"- **Title:** {report.get('title', 'No Title')}\n"
                      f"  - **Published:** {report.get('published', 'No Published Date')}\n"
                      f"  - **Source:** {report.get('source', 'Unknown Source')}\n"
                      f"  - **Link:** {formatted_link}\n")
        prompt += "---\n"
    prompt += "\nAggregated Summary:"

    # Log the prompt for debugging
    logging.debug(f"Prompt sent to OpenAI:\n{prompt}")

    # Call OpenAI's API with retries
    for attempt in range(1, max_retries + 1):
        try:
            chat_completion = client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "You process disaster alert data and format it for concise, informative Slack messages. Focus on providing clear what/where/when information."
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                model=MODEL_NAME,
            )
            # Access the response using attributes
            summary = chat_completion.choices[0].message.content.strip()

            logging.info("Successfully obtained summary from OpenAI.")
            
            # Log the summary for debugging
            logging.debug(f"Raw LLM summary:\n{summary}")
            
            return summary
        except RateLimitError as e:
            logging.error(f"Attempt {attempt}: OpenAI API request exceeded rate limit: {e}")
            # Notify via Slack
            send_disaster_alert_block([
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "⚠️ *Alert:* OpenAI API request exceeded rate limit. Please check your quota."
                    }
                }
            ])
        except APIConnectionError as e:
            logging.error(f"Attempt {attempt}: Failed to connect to OpenAI API: {e}")
            # Notify via Slack
            send_disaster_alert_block([
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "⚠️ *Alert:* Failed to connect to OpenAI API. Please check your network connection."
                    }
                }
            ])
        except APIError as e:
            logging.error(f"Attempt {attempt}: OpenAI API returned an API Error: {e}")
            # Notify via Slack
            send_disaster_alert_block([
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"⚠️ *Alert:* OpenAI API returned an error: {e}"
                        }
                    }
                ])
        except Exception as e:
            logging.error(f"Attempt {attempt}: Unexpected error: {e}")
            # Notify via Slack
            send_disaster_alert_block([
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "⚠️ *Alert:* An unexpected error occurred while processing disaster reports."
                    }
                }
            ])

        if attempt < max_retries:
            sleep_time = backoff_factor ** attempt
            logging.info(f"Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)
        else:
            logging.error("Max retries reached. Failed to obtain summary from OpenAI.")
            # Notify via Slack
            send_disaster_alert_block([
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "⚠️ *Alert:* Failed to obtain summary from OpenAI after multiple attempts."
                    }
                }
            ])
            return None

if __name__ == "__main__":
    from fetch_feeds import RSS_FEEDS
    disasters = fetch_rss_feeds(RSS_FEEDS)
    summary = process_disasters(disasters)
    if summary:
        print(summary)
    else:
        print("No summary generated.")