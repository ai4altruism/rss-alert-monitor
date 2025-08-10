# Disaster Alert Monitor

A Python-based disaster alert monitoring application that fetches potential disaster alerts from RSS feeds, processes and aggregates the data using OpenAI's GPT-5 series models, and sends formatted reports to a Slack channel. The application is designed to be containerized with Docker for deployment flexibility. This application is currently deployed for operations in Aid Arena.

## 🚀 Features
- Fetches disaster reports from multiple RSS feeds
- **GPT-5-mini integration** with enhanced reasoning capabilities for improved disaster analysis
- Intelligent filtering of alerts based on severity, alert level, and report type
- Processes and groups disaster data by type, location, and date using AI
- Aggregates similar reports to avoid duplication
- Sends alerts to a specified Slack channel using Block Kit formatting
- Retries on errors with exponential backoff for both OpenAI and Slack API integrations
- Maintains a database to prevent duplicate alerts
- **Configurable reasoning effort** for balancing speed vs. accuracy

## 🤖 AI-Powered Processing

This application uses OpenAI's **GPT-5-mini** model with the new Responses API to:
- Extract structured disaster information from raw RSS feeds
- Classify disaster types, severity levels, and geographic impact
- Generate concise, informative summaries for emergency responders
- Filter and prioritize alerts based on significance

### GPT-5 Features
- **Enhanced Reasoning**: GPT-5 models include internal reasoning tokens for improved accuracy
- **Configurable Effort Levels**: 
  - `low` effort for fast data extraction
  - `medium` effort for balanced quality and speed in summarization
- **Large Context Window**: 272,000 input tokens, 128,000 completion tokens

## Alert Filtering
The system implements multi-layered filtering to reduce noise and focus on significant events:
- Filters out GDACS green alerts (lower severity alerts)
- Filters out USGS earthquakes with magnitude < 5.8
- Filters out GDACS earthquakes with magnitude < 6.0
- Filters out SPC Mesoscale Discussions and Outlook reports
- XML-level pre-filtering for improved performance
- AI-powered relevance filtering based on disaster impact

## Prerequisites
- Docker
- Python 3.9 or later (for local development and test)
- Slack workspace with bot token and channel setup
- **OpenAI API key with GPT-5 model access**
- RSS feed URLs

## 📜 Setup

### 1. Clone the Repository
```bash
git clone <repository-url>
cd rss-alert-monitor
```

### 2. Install Dependencies (for local development)
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file in the project root with the following variables:

```env
# OpenAI GPT-5 Configuration
OPENAI_API_KEY=your-openai-api-key
MODEL_NAME=gpt-5-mini
EXTRACTION_MODEL=gpt-5-mini
BATCH_SIZE=10

# GPT-5 Reasoning Settings
REASONING_EFFORT_EXTRACTION=low
REASONING_EFFORT_SUMMARY=medium

# Slack Configuration
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token
CHANNEL_NAME=#your-channel-name

# RSS Feed URLs
RSS_FEED_1=https://gdacs.org/xml/rss_24h.xml
RSS_FEED_2=https://reliefweb.int/disasters/rss.xml
RSS_FEED_3=https://inciweb.wildfire.gov/incidents/rss.xml
RSS_FEED_4=http://www.spc.noaa.gov/products/spcrss.xml
RSS_FEED_5=https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.atom
RSS_FEED_6=https://www.nhc.noaa.gov/gtwo.xml

# Application Settings
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

### Test Individual Components
```bash
# Test disaster processing with GPT-5-mini
python process_data.py

# Test RSS feed fetching
python fetch_feeds.py

# Test Slack integration
python send_to_slack.py
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

## 📁 File Structure
```
project-root/
├── fetch_feeds.py        # Fetches disaster reports from RSS feeds with filtering
├── process_data.py       # GPT-5-mini powered processing and aggregation
├── format_message.py     # Formats messages for Slack with icons and styling
├── send_to_slack.py      # Sends alerts to Slack
├── main.py               # Orchestrates the entire workflow
├── requirements.txt      # Lists Python dependencies
├── .env                  # Stores environment variables
├── .env.example          # Example environment configuration
├── context.md            # GPT-5 features documentation
├── Dockerfile            # Containerization configuration
├── disaster_alert_bot.db # SQLite database (created during runtime)
└── disaster_alert_bot.log # Application logs
```

## 🛠 Configuration

### GPT-5 Reasoning Effort Levels
- **`low`**: Fast processing, suitable for data extraction (default for extraction)
- **`medium`**: Balanced quality and speed (default for summarization)  
- **`high`**: Maximum accuracy, slower processing

### Filtering Thresholds
You can modify these thresholds in the `process_data.py` file:
- GDACS green alerts (lower severity)
- SPC Mesoscale Discussions (MD reports)
- SPC Outlook reports
- USGS earthquakes with magnitude < 5.8
- GDACS earthquakes with magnitude < 6.0

## Key Technologies
- **Python**: Core application logic
- **OpenAI GPT-5-mini**: AI-powered disaster analysis and summarization
- **OpenAI Responses API**: Enhanced reasoning capabilities
- **Docker**: Containerization for deployment
- **Slack SDK**: Sending alerts to Slack
- **Feedparser**: Parsing RSS feeds
- **BeautifulSoup**: XML parsing and pre-filtering
- **SQLite**: Tracking sent disaster entries

## 🔄 Migration from GPT-4o-mini

This application has been upgraded from GPT-4o-mini to GPT-5-mini with the following improvements:

### What Changed
- **Model**: `gpt-4o-mini` → `gpt-5-mini`
- **API**: Chat Completions → Responses API
- **Features**: Added configurable reasoning effort levels
- **Performance**: Enhanced disaster classification accuracy

### Breaking Changes
- Environment variables now include `REASONING_EFFORT_*` parameters
- Removed deprecated `VERBOSITY_LEVEL` parameter
- Updated API calls to use new Responses endpoint

### Migration Benefits
- **Improved Accuracy**: Better disaster type classification and severity assessment
- **Enhanced Reasoning**: Internal reasoning tokens provide more reliable results
- **Configurable Performance**: Balance speed vs. accuracy based on use case
- **Future-Proof**: Built on OpenAI's latest model architecture

## 📊 Monitoring and Logging

The application provides comprehensive logging for monitoring and debugging:
- RSS feed fetch status and filtering statistics
- GPT-5-mini API call success/failure rates
- Disaster classification and grouping results
- Slack delivery confirmations
- Error handling and retry attempts

Log files are stored in `disaster_alert_bot.log` and can be monitored for operational insights.

## Contributing
Feel free to submit issues or pull requests. Contributions are welcome!

When contributing:
1. Follow the existing code structure
2. Maintain compatibility with GPT-5 API requirements
3. Update documentation for any configuration changes
4. Test both local and containerized deployments

## 📜 License

This project is licensed under the [GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0.en.html). Copyright (c) 2025 AI for Altruism Inc.

When using or distributing this software, please attribute as follows:

```
RSS Disaster Alert Monitor
Copyright (c) 2025 AI for Altruism Inc
License: GNU GPL v3.0
```

## Acknowledgments
- OpenAI for GPT-5-mini and the Responses API
- Slack SDK for Python
- Feedparser for RSS parsing
- BeautifulSoup for XML parsing

## 📩 Contact

For issues or questions, please open a GitHub issue or contact:

- **Email**: team@ai4altruism.org
- **Website**: https://ai4altruism.org