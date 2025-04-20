# this is just a simple script to test sending a message to a slack channel
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import os
from dotenv import load_dotenv

load_dotenv()
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
CHANNEL_NAME = "#disaster-alerts"

# Initialize the Slack client
client = WebClient(token=SLACK_BOT_TOKEN)

def send_disaster_alert(message):
    try:
        # Send a message to the channel
        response = client.chat_postMessage(
            channel=CHANNEL_NAME,
            text=message
        )
        print(f"Message sent: {response['message']['text']}")
    except SlackApiError as e:
        print(f"Error sending message: {e.response['error']}")

# Example Usage
alert_message = """
🚨 *Exercise Disaster Alert* 🚨
- **Type**: Earthquake
- **Location**: Somewhere, CA
- **Magnitude**: 6.5
- **Time**: 2024-12-31 14:00 UTC

For more details, visit: https://example.com/disaster-info
"""
send_disaster_alert(alert_message)
