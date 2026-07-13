# format_message.py

import re

def format_alert_block(summary):
    """
    Format the summary for Slack Block Kit with enhanced disaster-specific formatting.
    
    Includes:
    1. Special emoji icons for different disaster types
    2. Proper formatting for Slack's markdown
    3. Handling of SPC MD messages and other special cases
    """

    # Add emoji based on disaster type in heading
    def heading_replacer(match):
        heading_text = match.group(1).strip()
        
        # Determine appropriate emoji based on disaster type
        emoji = "🌐"  # Default emoji
        
        if re.search(r'earthquake', heading_text, re.IGNORECASE):
            emoji = "🌍"  # Changed from 🌋 to 🌍 for earthquake
        elif re.search(r'flood', heading_text, re.IGNORECASE):
            emoji = "🌊"
        elif re.search(r'fire|wildfire', heading_text, re.IGNORECASE):
            emoji = "🔥"
        elif re.search(r'hurricane|cyclone|typhoon', heading_text, re.IGNORECASE):
            emoji = "🌀"
        elif re.search(r'tornado', heading_text, re.IGNORECASE):
            emoji = "🌪️"
        elif re.search(r'storm|thunder|lightning', heading_text, re.IGNORECASE):
            emoji = "⛈️"
        elif re.search(r'volcano|eruption', heading_text, re.IGNORECASE):
            emoji = "🌋"
        elif re.search(r'snow|blizzard|winter', heading_text, re.IGNORECASE):
            emoji = "❄️"
        elif re.search(r'drought|heat', heading_text, re.IGNORECASE):
            emoji = "☀️"
        elif re.search(r'MD \d+|Discussion', heading_text, re.IGNORECASE):
            emoji = "🌪️"  # SPC Mesoscale Discussions often relate to severe weather
        elif re.search(r'warning|advisory|watch', heading_text, re.IGNORECASE):
            emoji = "⚠️"
            
        return f"*{emoji} {heading_text}*"

    # Special handling for SPC MD messages - add emoji for weather alerts
    summary = re.sub(
        r"^\s*###\s+(SPC MD \d+)", 
        r"### 🌪️ \1", 
        summary,
        flags=re.MULTILINE
    )

    # 2. Turn lines starting with '### ' into bold lines with emoji
    summary = re.sub(
        r'^\s*###\s+(.*)',
        heading_replacer,
        summary,
        flags=re.MULTILINE
    )

    # 3. Convert '**some text**' into '*some text*' for Slack bold
    summary = re.sub(
        r'\*\*(.+?)\*\*',
        r'*\1*',
        summary
    )
    
    # 4. Fix Slack link formatting - ensure proper format <url|text>
    # A link with a missing URL (<|More Info>) is invalid Block Kit and would
    # be rejected by Slack; degrade it to plain text instead.
    summary = re.sub(
        r'<\|(More Info)>',
        r'\1',
        summary
    )

    # Also fix any markdown links [text](url)
    summary = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        r'<\2|\1>',
        summary
    )

    # Create blocks with dividers between sections. Slack limits: 3000 chars
    # of text per section block, 50 blocks per message. Truncate per section
    # (not globally) so a busy day drops detail, not whole disaster groups.
    max_section_len = 2900
    max_blocks = 48  # leave room for the header and footer blocks

    sections = [s.strip() for s in summary.split('---') if s.strip()]
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🚨 Disaster Alerts",
                "emoji": True
            }
        }
    ]

    truncated_groups = 0
    for i, section in enumerate(sections):
        if len(blocks) >= max_blocks:
            truncated_groups = len(sections) - i
            break

        if len(section) > max_section_len:
            section = section[:max_section_len] + "..."

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": section
            }
        })

        # Add a divider after each section except the last one
        if i < len(sections) - 1 and len(blocks) < max_blocks:
            blocks.append({"type": "divider"})

    # Add footer
    footer_text = "Disaster Alert Monitor"
    if truncated_groups:
        footer_text += f" — {truncated_groups} additional group(s) omitted (message limit)"
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": footer_text
            }
        ]
    })

    return blocks