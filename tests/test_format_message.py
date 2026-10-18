import re

import format_message
from format_message import MAX_BLOCKS, format_alert_block


def group(i, n_items=1, title=None):
    return {
        "heading": f"Group {i}",
        "summary": f"Summary {i}.",
        "items": [{"title": title or f"Item {i}-{j}", "link": f"https://x/{i}/{j}",
                   "published": "2026-10-12", "source": "Src"} for j in range(n_items)],
    }


def section_texts(blocks):
    return [b["text"]["text"] for b in blocks if b["type"] == "section"]


def test_blocks_have_header_and_footer():
    blocks = format_alert_block([group(1)])
    assert blocks[0]["type"] == "header"
    assert blocks[-1]["type"] == "context"
    assert blocks[-1]["elements"][0]["text"] == "Disaster Alert Monitor"


def test_item_line_contains_link_title_and_meta():
    text = "\n".join(section_texts(format_alert_block([group(1)])))
    assert "<https://x/1/0|Item 1-0>" in text
    assert "(2026-10-12 — Src)" in text


def test_less_than_in_title_is_escaped():
    blocks = format_alert_block([group(1, title="Quake <b>big</b> & bad")])
    text = "\n".join(section_texts(blocks))
    assert "&lt;b&gt;big&lt;/b&gt; &amp; bad" in text
    assert "<b>" not in text


def test_pipe_and_space_in_url_do_not_break_link_syntax():
    g = group(1)
    g["items"][0]["link"] = "https://x/a b|c>d"
    text = "\n".join(section_texts(format_alert_block([g])))
    assert "<https://x/a%20b%7Cc%3Ed|" in text


def test_many_groups_stay_within_50_blocks_and_footer_counts_omitted():
    groups = [group(i) for i in range(80)]
    blocks = format_alert_block(groups)
    assert len(blocks) <= 50
    rendered = {int(m) for t in section_texts(blocks) for m in re.findall(r"\*\S* Group (\d+)\*", t)}
    omitted = 80 - len(rendered)
    assert omitted > 0
    assert f"{omitted} additional group(s) omitted" in blocks[-1]["elements"][0]["text"]


def test_no_omission_note_when_everything_fits():
    blocks = format_alert_block([group(i) for i in range(5)])
    assert "omitted" not in blocks[-1]["elements"][0]["text"]


def test_every_section_is_at_most_3000_chars():
    big = [group(i, n_items=40, title="T" * 200) for i in range(3)]
    for t in section_texts(format_alert_block(big)):
        assert len(t) <= 3000


def test_single_overlong_line_is_truncated_not_dropped():
    texts = section_texts(format_alert_block([group(1, title="Z" * 5000)]))
    item_text = next(t for t in texts if "ZZZ" in t)
    assert len(item_text) <= 3000
    assert item_text.endswith("…")


def test_long_group_splits_into_multiple_sections_without_splitting_links():
    blocks = format_alert_block([group(1, n_items=60, title="T" * 100)])
    texts = section_texts(blocks)
    assert len(texts) > 1
    for t in texts:
        assert t.count("<https://") == t.count("|")  # each opened link has its pipe
        assert not t.rstrip().endswith("<")


def test_emoji_picked_from_heading():
    g = group(1)
    g["heading"] = "Earthquake (M6.4) — Peru"
    assert section_texts(format_alert_block([g]))[0].startswith("*🌍 ")


def test_empty_groups_list_is_header_and_footer_only():
    blocks = format_alert_block([])
    assert [b["type"] for b in blocks] == ["header", "context"]
