# Disaster Alert Monitor

A Python-based disaster alert monitoring application that fetches potential disaster alerts from RSS feeds, processes and aggregates the data, and sends formatted reports to a Slack channel. The application is designed to be containerized with Docker for deployment flexibility. This application is currently deployed for operations in Aid Arena.

## Features
- Fetches disaster reports from multiple RSS feeds.
- Intelligent filtering of alerts based on severity, alert level, and report type.
- Processes and groups disaster data by type, location, and date.
- Aggregates similar reports to avoid duplication.
- Sends alerts to a specified Slack channel using Block Kit formatting.
- Retries on errors with exponential backoff for both OpenAI and Slack API integrations.
- Maintains a database to prevent duplicate alerts.

## Alert Filtering
The system implements multi-layered filtering to reduce noise and focus on significant events:
- Filters out GDACS green alerts (lower severity alerts)
- Filters out USGS earthquakes with magnitude < 5.8
- Filters out GDACS earthquakes with magnitude < 6.0
- Filters out SPC Mesoscale Discussions and Outlook reports
- XML-level pre-filtering for improved performance

## Prerequisites
- Docker
- Python 3.9 or later (for local development and test)
- Slack workspace with bot token and channel setup
- OpenAI API key
- RSS feed URLs

## Setup

### 1. Clone the Repository
```bash
git clone <repository-url>
cd disaster-monitoring-bot
```

### 2. Install Dependencies (for local development)
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file in the project root with the following variables:
```env
RSS_FEED_1=https://gdacs.org/xml/rss_24h.xml
RSS_FEED_2=https://reliefweb.int/disasters/rss.xml
RSS_FEED_3=https://inciweb.wildfire.gov/incidents/rss.xml
RSS_FEED_4=http://www.spc.noaa.gov/products/spcrss.xml
RSS_FEED_5=https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.atom
RSS_FEED_6=https://www.nhc.noaa.gov/gtwo.xml
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token
CHANNEL_NAME=#your-channel-name
MODEL_NAME=gpt-4o-mini
EXTRACTION_MODEL=gpt-4o-mini
BATCH_SIZE=10
OPENAI_API_KEY=your-openai-api-key
JOB_INTERVAL_MINUTES=30
CONTACT_EMAIL=your-contact-email
WEBSITE_URL=your-website-url
```

### 4. Initialize the Database
For local development, the application uses an SQLite database to track sent entries. Run the application once to initialize the database:
```bash
python main.py
```

## Usage

### Local Development
Run the application locally:
```bash
python main.py
```

### Containerized Deployment

#### 1. Build Docker Image
```bash
docker build -t disaster-monitor-bot .
```

#### 2. Run Docker Container
```bash
docker run --env-file .env -d --name disaster-monitor-bot disaster-monitor-bot
```

#### 3. Verify Logs
View container logs to confirm the application is running:
```bash
docker logs disaster-monitor-bot
```

## File Structure
```
project-root/
├── fetch_feeds.py        # Fetches disaster reports from RSS feeds with filtering
├── process_data.py       # Processes and aggregates disaster data 
├── format_message.py     # Formats messages for Slack with icons and styling
├── send_to_slack.py      # Sends alerts to Slack
├── main.py               # Orchestrates the entire workflow
├── requirements.txt      # Lists Python dependencies
├── .env                  # Stores environment variables
├── Dockerfile            # Containerization configuration
├── disaster_alert_bot.db # SQLite database (created during runtime)
```

## Key Technologies
- **Python**: Core application logic
- **Docker**: Containerization for deployment
- **Slack SDK**: Sending alerts to Slack
- **Feedparser**: Parsing RSS feeds
- **BeautifulSoup**: XML parsing and pre-filtering
- **SQLite**: Tracking sent disaster entries
- **OpenAI API**: Summarizing and processing disaster reports

## Filtering Configuration
The system is configured to filter:
- GDACS green alerts (lower severity)
- SPC Mesoscale Discussions (MD reports)
- SPC Outlook reports
- USGS earthquakes with magnitude < 5.8
- GDACS earthquakes with magnitude < 6.0

You can modify these thresholds in the `process_data.py` file.

## Contributing
Feel free to submit issues or pull requests. Contributions are welcome!

## License
This project is licensed under the GNU General Public License v3.0.

Copyright (C) 2025

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

## Acknowledgments
- Slack SDK for Python
- OpenAI GPT API
- Feedparser for RSS parsing
- BeautifulSoup for XML parsing