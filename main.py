# main.py

from fetch_feeds import fetch_rss_feeds, RSS_FEEDS
from process_data import process_disasters
from format_message import format_alert_block
from send_to_slack import send_disaster_alert_block, send_error_notice
import os
from dotenv import load_dotenv
import schedule
import time
import sqlite3
import logging
import json
import re

# Load environment variables
load_dotenv()

# Configure logging with both file and console handlers
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("disaster_alert_bot.log"),
        logging.StreamHandler()
    ]
)

# Database path (override with DB_PATH, e.g. /data/disaster_alert_bot.db when
# running in Docker with a persistent volume — otherwise sent-entry history is
# lost on container recreation and previously posted alerts repeat)
DB_PATH = os.getenv('DB_PATH', 'disaster_alert_bot.db')

def initialize_db():
    """
    Initialize the SQLite database and create tables if they don't exist.
    Adds a timestamp column for tracking entry creation.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sent_entries (
                link TEXT PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Add index for faster queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_timestamp ON sent_entries(timestamp)
        ''')
        
        # Create a table for metadata (including version info)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Set version info if not exists
        cursor.execute('INSERT OR IGNORE INTO metadata (key, value) VALUES (?, ?)', 
                      ('version', '1.1.0'))
        
        conn.commit()
        conn.close()
        logging.info("Database initialized successfully.")
    except Exception as e:
        logging.error(f"Error initializing database: {e}")

def cleanup_old_entries():
    """
    Clean up entries older than 30 days to prevent database bloat.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM sent_entries
            WHERE timestamp < datetime('now', '-30 day')
        ''')
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted_count > 0:
            logging.info(f"Cleaned up {deleted_count} old entries from the database.")
    except Exception as e:
        logging.error(f"Error cleaning up old entries: {e}")

def load_sent_entries():
    """
    Load sent entries from the database to avoid duplicates.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT link FROM sent_entries')
        rows = cursor.fetchall()
        conn.close()
        sent_entries = set(row[0] for row in rows)
        logging.info(f"Loaded {len(sent_entries)} sent entries from the database.")
        return sent_entries
    except Exception as e:
        logging.error(f"Error loading sent entries: {e}")
        return set()

def save_sent_entries(new_links):
    """
    Save new sent entries to the database.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.executemany('INSERT OR IGNORE INTO sent_entries (link, timestamp) VALUES (?, CURRENT_TIMESTAMP)', 
                          [(link,) for link in new_links])
        conn.commit()
        conn.close()
        logging.info(f"Saved {len(new_links)} new sent entries to the database.")
    except Exception as e:
        logging.error(f"Error saving sent entries: {e}")

from fetch_feeds import NHC_LEVELS, nhc_formation_level

_USGS_MAG = re.compile(r"\s*M\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_GDACS_LEVELS = {"green": 0, "orange": 1, "red": 2}


def severity(report):
    """(label, rank) for sources whose single link carries a changing level,
    else None. USGS: magnitude. GDACS: alert color. NHC: highest 7-day
    formation chance in the basin's outlook (the link is one per basin)."""
    source_type = (report.get("source_type") or "").lower()
    title = report.get("title") or ""
    if source_type == "usgs":
        m = _USGS_MAG.match(title)
        if m:
            return f"M{m.group(1)}", float(m.group(1))
    elif source_type == "gdacs":
        level = title.split(" ", 1)[0].lower()
        if level in _GDACS_LEVELS:
            return level, _GDACS_LEVELS[level]
    elif source_type == "nhc":
        rank = nhc_formation_level(report.get("summary"))
        label = {v: k for k, v in NHC_LEVELS.items()}.get(rank, "none")
        return label, rank
    return None


def _rank_of(source_type, label):
    try:
        if source_type == "usgs":
            return float(label.lstrip("Mm"))
        if source_type == "gdacs":
            return _GDACS_LEVELS[label]
        if source_type == "nhc":
            return NHC_LEVELS.get(label, 0)
    except (KeyError, ValueError):
        pass
    return None


def dedup_key(report):
    """
    The key a report is recorded under in sent_entries: the link, plus the
    severity label for sources that re-use one link while the level changes
    (see severity). Titles are not used whole, because some sources (SPC)
    put a timestamp in every title.
    """
    link = report.get("link", "") or ""
    sev = severity(report)
    return f"{link}#{sev[0]}" if sev else link


def already_sent(report, sent_entries):
    """
    True unless the report is new or an escalation.

    For USGS, GDACS and NHC a report under an already-recorded link alerts
    again only when its level is higher than every level recorded for that
    link: M5.6 to M6.1, orange to red, low to high formation chance. A
    downgrade or a same-level update stays quiet.

    Rows written before dedup_key existed hold the bare link; matching it
    keeps a deploy from re-posting everything still in the feeds. Such a row
    expires with the normal 30-day cleanup.
    """
    link = report.get("link", "") or ""
    if dedup_key(report) in sent_entries or link in sent_entries:
        return True
    sev = severity(report)
    if not sev:
        return False
    source_type = (report.get("source_type") or "").lower()
    prefix = link + "#"
    for entry in sent_entries:
        if entry.startswith(prefix):
            rank = _rank_of(source_type, entry[len(prefix):])
            if rank is not None and rank >= sev[1]:
                return True
    return False


def main():
    """
    Main function to fetch, process, and send disaster alerts.
    """
    logging.info("Starting disaster report processing...")
    
    # Clean up old entries periodically
    cleanup_old_entries()
    
    # Load previously sent entries
    sent_entries = load_sent_entries()
    
    # Fetch disaster reports from RSS feeds
    disasters = fetch_rss_feeds(RSS_FEEDS)
    logging.info(f"Fetched {len(disasters)} disaster reports from RSS feeds.")
    
    # Filter out already sent entries
    new_disasters = [d for d in disasters if not already_sent(d, sent_entries)]
    
    if not new_disasters:
        logging.info("No new disaster reports to process.")
        return
    
    logging.info(f"Processing {len(new_disasters)} new disaster reports.")
    
    # Process and summarize the disasters into structured groups
    groups, status = process_disasters(new_disasters)
    new_keys = [dedup_key(d) for d in new_disasters]

    if status == "ok" and groups:
        # Format and send the alert to Slack
        rendered = set()
        formatted_blocks = format_alert_block(groups, rendered_links=rendered)
        logging.debug("Formatted blocks to be sent to Slack:")
        logging.debug(json.dumps(formatted_blocks, indent=2))

        # Items that reached a group but were cut by the block cap stay
        # unrecorded so the next run delivers them. Entries that never
        # reached a group were filtered out and are recorded as processed.
        grouped = {item.get("link") for g in groups for item in g.get("items", [])}
        omitted = grouped - rendered
        keys_to_save = [dedup_key(d) for d in new_disasters if d.get("link") not in omitted]
        if omitted:
            logging.warning(f"{len(omitted)} alert(s) cut by the Slack block limit; left for the next run.")

        # Send the alert to Slack; only record entries as sent if delivery
        # succeeded, so a failed post is retried next run instead of lost.
        if send_disaster_alert_block(formatted_blocks):
            save_sent_entries(keys_to_save)
            logging.info("Disaster alert sent to Slack successfully.")
        else:
            logging.error(
                "Failed to send alert to Slack. Entries NOT marked as sent; will retry next run."
            )
    elif status == "empty":
        # Everything was filtered out (green alerts, low magnitude, etc.).
        # Record the entries so they are not re-processed on every run.
        save_sent_entries(new_keys)
        logging.info("All new entries filtered out - recorded as processed, no alert sent.")
    else:
        # LLM summarization failed - leave entries unrecorded so they retry.
        logging.warning("No summary generated - no alert sent; entries will be retried next run.")

def job():
    """
    Wrapper function to run the main function and handle any unexpected exceptions.
    """
    try:
        main()
    except Exception as e:
        error_msg = f"An unexpected error occurred in the scheduled job: {str(e)}"
        logging.error(error_msg, exc_info=True)

        # Notify via the error channel (or log only when none is configured) —
        # tracebacks never go to the public alerts channel.
        try:
            import traceback
            tb_str = traceback.format_exc()
            send_error_notice(
                f"⚠️ *System Alert:* The disaster monitoring system encountered an error:\n"
                f"```\n{error_msg}\n\nTraceback:\n{tb_str[:800]}...\n```"
            )
        except Exception as slack_error:
            logging.error(f"Failed to send error notification to Slack: {slack_error}")

if __name__ == "__main__":
    # Initialize the database
    initialize_db()
    
    # Load the job interval from the environment variable
    try:
        job_interval_minutes = int(os.getenv('JOB_INTERVAL_MINUTES', '10'))
        if job_interval_minutes <= 0:
            raise ValueError("JOB_INTERVAL_MINUTES must be a positive integer.")
    except ValueError as ve:
        logging.error(f"Invalid JOB_INTERVAL_MINUTES value: {ve}. Using default interval of 10 minutes.")
        job_interval_minutes = 10
    
    # Run the job immediately on startup
    job()
    
    # Schedule the job based on the loaded interval
    schedule.every(job_interval_minutes).minutes.do(job)

    logging.info(f"Disaster Alert Bot is running... Scheduled to run every {job_interval_minutes} minutes.")
    
    while True:
        schedule.run_pending()
        time.sleep(1)