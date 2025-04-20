# main.py

from fetch_feeds import fetch_rss_feeds, RSS_FEEDS
from process_data import process_disasters
from format_message import format_alert_block
from send_to_slack import send_disaster_alert_block
import os
from dotenv import load_dotenv
import schedule
import time
import sqlite3
import logging
import json

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

# Database path
DB_PATH = 'disaster_alert_bot.db'

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
    new_disasters = [d for d in disasters if d['link'] not in sent_entries]
    
    if not new_disasters:
        logging.info("No new disaster reports to process.")
        return
    
    logging.info(f"Processing {len(new_disasters)} new disaster reports.")
    
    # Process and summarize the disasters
    summary = process_disasters(new_disasters)
    
    if summary:
        # Format and send the alert to Slack
        formatted_blocks = format_alert_block(summary)
        logging.debug("Formatted blocks to be sent to Slack:")
        logging.debug(json.dumps(formatted_blocks, indent=2))
        
        # Send the alert to Slack
        send_disaster_alert_block(formatted_blocks)
        
        # Update sent entries in the database
        new_links = [d['link'] for d in new_disasters]
        save_sent_entries(new_links)
        
        logging.info("Disaster alert sent to Slack successfully.")
    else:
        logging.warning("No summary generated - no alert sent.")

def job():
    """
    Wrapper function to run the main function and handle any unexpected exceptions.
    """
    try:
        main()
    except Exception as e:
        error_msg = f"An unexpected error occurred in the scheduled job: {str(e)}"
        logging.error(error_msg, exc_info=True)
        
        # Try to send error notification to Slack
        try:
            # Create a formatted error message with traceback
            import traceback
            tb_str = traceback.format_exc()
            slack_error_message = f"⚠️ *System Alert:* The disaster monitoring system encountered an error:\n```\n{error_msg}\n\nTraceback:\n{tb_str[:800]}...\n```"
            
            send_disaster_alert_block([
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": slack_error_message
                    }
                }
            ])
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