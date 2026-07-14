# format_message.py

import re

# Slack limits: 3000 chars of text per section block, 50 blocks per message.
MAX_SECTION_LEN = 2900
MAX_BLOCKS = 48  # leave room for the header and footer blocks


def _pick_emoji(heading_text):
    """Choose an emoji for a group heading based on disaster type keywords."""
    checks = [
        (r'earthquake', "🌍"),
        (r'flood', "🌊"),
        (r'fire|wildfire', "🔥"),
        (r'hurricane|cyclone|typhoon', "🌀"),
        (r'tornado', "🌪️"),
        (r'storm|thunder|lightning', "⛈️"),
        (r'volcano|eruption', "🌋"),
        (r'snow|blizzard|winter', "❄️"),
        (r'drought|heat', "☀️"),
        (r'MD \d+|Discussion', "🌪️"),
        (r'warning|advisory|watch', "⚠️"),
    ]
    for pattern, emoji in checks:
        if re.search(pattern, heading_text, re.IGNORECASE):
            return emoji
    return "🌐"


def _escape_mrkdwn(text):
    """Escape Slack mrkdwn control characters in display text."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _sanitize_url(url):
    """Encode characters that would break Slack's <url|text> link syntax."""
    return (url or "").replace("|", "%7C").replace(">", "%3E").replace(" ", "%20")


def _item_line(item):
    """
    Render one report as a single mrkdwn line. The link comes straight from
    the source data — never from LLM output.
    """
    title = _escape_mrkdwn(item.get("title", "No Title"))
    url = _sanitize_url(item.get("link", ""))
    source = _escape_mrkdwn(item.get("source", ""))
    published = _escape_mrkdwn(item.get("published", ""))

    line = f"• <{url}|{title}>" if url else f"• {title}"
    meta = " — ".join(p for p in (published, source) if p)
    if meta:
        line += f"  ({meta})"
    return line


def _section(text):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def format_alert_block(groups):
    """
    Format structured alert groups into Slack Block Kit blocks.

    Args:
        groups: list of {"heading": str, "summary": str, "items": [
                    {"title", "link", "published", "source"}, ...]}
                as produced by process_disasters().

    Text is packed into section blocks at line granularity — a line (and
    therefore a link) is never split mid-way. Groups that do not fit within
    Slack's 50-block limit are omitted and counted in the footer.
    """
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
    for gi, group in enumerate(groups):
        if len(blocks) >= MAX_BLOCKS:
            truncated_groups = len(groups) - gi
            break

        heading = group.get("heading") or "Alerts"
        emoji = _pick_emoji(heading)
        lines = [f"*{emoji} {_escape_mrkdwn(heading)}*"]
        summary = (group.get("summary") or "").strip()
        if summary:
            lines.append(_escape_mrkdwn(summary))
        lines.extend(_item_line(item) for item in group.get("items", []))

        # Pack whole lines into section blocks (an over-long single line is
        # truncated on its own, which can shorten displayed text but never
        # produces a dangling half-link followed by other content).
        current = ""
        for line in lines:
            if len(line) > MAX_SECTION_LEN:
                line = line[:MAX_SECTION_LEN] + "…"
            if current and len(current) + 1 + len(line) > MAX_SECTION_LEN:
                if len(blocks) >= MAX_BLOCKS:
                    break
                blocks.append(_section(current))
                current = line
            else:
                current = f"{current}\n{line}" if current else line
        if current and len(blocks) < MAX_BLOCKS:
            blocks.append(_section(current))

        if gi < len(groups) - 1 and len(blocks) < MAX_BLOCKS:
            blocks.append({"type": "divider"})

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
