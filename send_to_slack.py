### send_to_slack.py

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import os
from dotenv import load_dotenv
import logging
import time
import json

# Load environment variables
load_dotenv()

# Slack configuration
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
CHANNEL_NAME = os.getenv('CHANNEL_NAME')

# Initialize Slack client
client = WebClient(token=SLACK_BOT_TOKEN)

def send_disaster_alert_block(blocks, max_retries=3, backoff_factor=2):
    """
    Sends a Block Kit formatted message to Slack with retry logic.
    
    Args:
        blocks (list): List of Slack Block Kit blocks
        max_retries (int): Maximum number of retry attempts
        backoff_factor (int): Factor for exponential backoff
        
    Returns:
        bool: True if message was sent successfully, False otherwise
    """
    # Validate required environment variables
    if not SLACK_BOT_TOKEN:
        logging.error("SLACK_BOT_TOKEN is not set in .env file")
        return False
        
    if not CHANNEL_NAME:
        logging.error("CHANNEL_NAME is not set in .env file")
        return False
        
    # Generate a fallback text from the blocks for notifications
    fallback_text = "Disaster Alerts Summary"
    for block in blocks:
        if block.get("type") == "section" and "text" in block and block["text"].get("type") == "mrkdwn":
            # Extract first line of text for fallback
            text = block["text"].get("text", "")
            first_line = text.split("\n")[0] if text else ""
            if first_line and len(first_line) > len(fallback_text):
                fallback_text = first_line[:100] + "..." if len(first_line) > 100 else first_line
            break
    
    # Attempt to send message with retries
    for attempt in range(1, max_retries + 1):
        try:
            # Log the blocks being sent (at debug level to avoid clutter)
            logging.debug(f"Sending blocks to Slack: {json.dumps(blocks, indent=2)}")
            
            response = client.chat_postMessage(
                channel=CHANNEL_NAME,
                blocks=blocks,
                text=fallback_text
            )
            
            logging.info(f"Message sent successfully to {CHANNEL_NAME} (timestamp: {response['ts']})")
            return True
            
        except SlackApiError as e:
            error_code = e.response.get('error', 'unknown_error')
            
            # Handle specific error types
            if error_code == 'invalid_blocks':
                logging.error(f"Invalid Block Kit format: {e.response['error']}")
                
                # Log the problematic blocks
                logging.error(f"Problematic blocks: {json.dumps(blocks, indent=2)}")
                
                # Try to send a simplified message instead
                try:
                    simplified_message = "⚠️ *Disaster Alert System*: New alerts detected, but there was an error formatting the message."
                    client.chat_postMessage(
                        channel=CHANNEL_NAME,
                        text=simplified_message
                    )
                    logging.info("Sent simplified message after block formatting error")
                    return False  # Still count as failure of the original message
                except SlackApiError:
                    pass  # If this also fails, continue with retry logic
                    
                # Don't retry for invalid blocks as it's likely a formatting issue
                break
                
            elif error_code == 'channel_not_found':
                logging.error(f"Channel not found: {CHANNEL_NAME}")
                break  # Don't retry for non-existent channel
                
            elif error_code == 'not_in_channel':
                logging.error(f"Bot is not in channel: {CHANNEL_NAME}")
                break  # Don't retry if bot isn't in the channel
                
            elif error_code == 'rate_limited':
                retry_after = int(e.response.headers.get('Retry-After', 60))
                logging.warning(f"Rate limited by Slack. Retrying after {retry_after} seconds")
                time.sleep(retry_after)
                continue  # Try again after waiting
                
            else:
                logging.error(f"Attempt {attempt}: Slack API error: {error_code} - {e}")
                
            # For other errors, implement exponential backoff
            if attempt < max_retries:
                sleep_time = backoff_factor ** attempt
                logging.info(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
            else:
                logging.error("Max retries reached. Failed to send message to Slack.")
                
        except Exception as e:
            logging.error(f"Unexpected error sending message to Slack: {e}")
            
            if attempt < max_retries:
                sleep_time = backoff_factor ** attempt
                logging.info(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
            else:
                logging.error("Max retries reached. Failed to send message to Slack.")
    
    return False

# Simple test function for direct testing
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    # Simple test message
    test_blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🧪 Test Alert",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "This is a test of the disaster alert system."
            }
        }
    ]
    
    result = send_disaster_alert_block(test_blocks)
    print(f"Test message sent: {result}")