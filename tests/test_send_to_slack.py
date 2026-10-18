import pytest

import send_to_slack
from conftest import slack_error
from send_to_slack import send_disaster_alert_block, send_error_notice

BLOCKS = [{"type": "section", "text": {"type": "mrkdwn", "text": "*Heading line*\nbody"}}]


def test_success_posts_blocks_to_configured_channel(slack):
    assert send_disaster_alert_block(BLOCKS) is True
    (post,) = slack.posts
    assert post["channel"] == "C_TEST_ALERTS"
    assert post["blocks"] == BLOCKS
    assert post["text"] != ""  # notification fallback text present


def test_fallback_text_is_first_line_of_first_section(slack):
    long_first = "*" + "H" * 40 + "*"
    send_disaster_alert_block([{"type": "section", "text": {"type": "mrkdwn", "text": long_first + "\nbody"}}])
    assert slack.posts[0]["text"] == long_first


def test_fallback_text_keeps_default_when_first_line_is_short(slack):
    send_disaster_alert_block(BLOCKS)
    assert slack.posts[0]["text"] == "Disaster Alerts Summary"


def test_missing_token_or_channel_returns_false_without_posting(slack, monkeypatch):
    monkeypatch.setattr(send_to_slack, "SLACK_BOT_TOKEN", None)
    assert send_disaster_alert_block(BLOCKS) is False
    monkeypatch.undo()
    monkeypatch.setattr(send_to_slack, "CHANNEL_NAME", None)
    assert send_disaster_alert_block(BLOCKS) is False
    assert slack.posts == []


@pytest.mark.parametrize("code", ["channel_not_found", "not_in_channel"])
def test_permanent_errors_are_not_retried(slack, code):
    slack.failures = [slack_error(code)]
    assert send_disaster_alert_block(BLOCKS) is False
    assert len(slack.posts) == 1


def test_transient_error_is_retried_then_succeeds(slack):
    slack.failures = [slack_error("internal_error"), None]
    assert send_disaster_alert_block(BLOCKS) is True
    assert len(slack.posts) == 2


def test_gives_up_after_max_retries(slack):
    slack.failures = [slack_error("internal_error")] * 5
    assert send_disaster_alert_block(BLOCKS, max_retries=3) is False
    assert len(slack.posts) == 3


def test_rate_limited_waits_then_retries(slack):
    slack.failures = [slack_error("rate_limited", {"Retry-After": "1"}), None]
    assert send_disaster_alert_block(BLOCKS) is True


def test_non_slack_exception_is_retried(slack):
    slack.failures = [RuntimeError("boom"), None]
    assert send_disaster_alert_block(BLOCKS) is True


def test_error_notice_without_error_channel_logs_only(slack):
    assert send_error_notice("oops") is False
    assert slack.posts == []


def test_error_notice_posts_to_error_channel(slack, monkeypatch):
    monkeypatch.setattr(send_to_slack, "ERROR_CHANNEL_NAME", "C_OPS")
    assert send_error_notice("oops") is True
    assert slack.posts[0]["channel"] == "C_OPS"


def test_invalid_blocks_returns_false(slack):
    slack.fail_when = lambda kw: slack_error("invalid_blocks") if kw.get("blocks") else None
    assert send_disaster_alert_block(BLOCKS) is False


@pytest.mark.xfail(strict=True, reason="BUG: on invalid_blocks a generic 'error formatting the message' text is posted to the public alerts channel (and again every run)")
def test_invalid_blocks_does_not_post_generic_text_to_public_channel(slack):
    slack.fail_when = lambda kw: slack_error("invalid_blocks") if kw.get("blocks") else None
    send_disaster_alert_block(BLOCKS)
    generic = [p for p in slack.posts if not p.get("blocks")]
    assert generic == []
