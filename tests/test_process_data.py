import json

import pytest

import process_data
from conftest import disaster, make_api_error, make_bad_request_error
from process_data import (
    extract_details_in_batch,
    group_disasters,
    prepare_entry_for_extraction,
    process_disasters,
    strip_json_fences,
)

L1, L2 = "https://r/1", "https://r/2"


def prepared(*links, published="2026-10-12"):
    return [prepare_entry_for_extraction(disaster(f"Title {l}", l, source_type="reliefweb",
                                                  published=published)) for l in links]


def result(ident, **kw):
    base = {"id": ident, "disaster_type": "Flood", "location": "Chad", "date": "2026-10-12",
            "severity": None, "alert_level": "", "description": "d"}
    base.update(kw)
    return base


def is_fallback(details):
    return details["disaster_type"] == "Unknown Type" and details["location"] == "Unknown Location"


# ---- request construction ----------------------------------------------
@pytest.mark.parametrize("model", ["gpt-5-mini", "gpt-6-luna"])
def test_extraction_request_uses_reasoning_effort_and_no_temperature(fake_llm, monkeypatch, model):
    monkeypatch.setattr(process_data, "EXTRACTION_MODEL", model)
    monkeypatch.setattr(process_data, "REASONING_EFFORT_EXTRACTION", "low")
    fake_llm([json.dumps({"results": []})])
    extract_details_in_batch(prepared(L1))
    kw = fake_llm.calls[0]
    assert kw["model"] == model
    assert kw["reasoning"] == {"effort": "low"}
    assert "temperature" not in kw
    assert L1 in kw["input"] and kw["instructions"]


@pytest.mark.parametrize("model", ["gpt-5-mini", "gpt-6-luna"])
def test_summary_request_uses_reasoning_effort_and_no_temperature(fake_llm, monkeypatch, model):
    monkeypatch.setattr(process_data, "MODEL_NAME", model)
    monkeypatch.setattr(process_data, "EXTRACTION_MODEL", "gpt-5-mini")
    monkeypatch.setattr(process_data, "REASONING_EFFORT_SUMMARY", "medium")
    process_disasters([disaster("Floods", L1, source_type="reliefweb")])
    summary_call = fake_llm.calls[-1]
    assert summary_call["model"] == model
    assert summary_call["reasoning"] == {"effort": "medium"}
    assert "temperature" not in summary_call
    assert "https://" not in summary_call["input"].split("Input groups:")[1]  # URLs never sent


def test_both_calls_in_one_run_use_their_own_models(fake_llm, monkeypatch):
    monkeypatch.setattr(process_data, "MODEL_NAME", "gpt-6-luna")
    monkeypatch.setattr(process_data, "EXTRACTION_MODEL", "gpt-5-mini")
    process_disasters([disaster("Floods", L1, source_type="reliefweb")])
    assert [c["model"] for c in fake_llm.calls] == ["gpt-5-mini", "gpt-6-luna"]


# ---- extraction parsing and failure paths ------------------------------
def test_extraction_parses_clean_json(fake_llm):
    fake_llm([json.dumps({"results": [result(L1, location="Niger")]})])
    out = extract_details_in_batch(prepared(L1))
    assert out[L1]["location"] == "Niger"


def test_extraction_parses_fenced_json(fake_llm):
    fake_llm(["```json\n" + json.dumps({"results": [result(L1, location="Niger")]}) + "\n```"])
    assert extract_details_in_batch(prepared(L1))[L1]["location"] == "Niger"


def test_extraction_accepts_bare_list(fake_llm):
    fake_llm([json.dumps([result(L1, location="Niger")])])
    assert extract_details_in_batch(prepared(L1))[L1]["location"] == "Niger"


def test_extraction_accepts_single_result_dict(fake_llm):
    fake_llm([json.dumps(result(L1, location="Niger"))])
    assert extract_details_in_batch(prepared(L1))[L1]["location"] == "Niger"


def test_extraction_missing_ids_get_fallback_details(fake_llm):
    fake_llm([json.dumps({"results": [result(L1)]})])
    out = extract_details_in_batch(prepared(L1, L2))
    assert not is_fallback(out[L1])
    assert is_fallback(out[L2])


@pytest.mark.parametrize("text", [
    "",
    "Here is the JSON you asked for: " + json.dumps({"results": []}),
    "not json at all",
])
def test_extraction_unparseable_output_falls_back_after_retries(fake_llm, text):
    fake_llm([text, text])
    out = extract_details_in_batch(prepared(L1))
    assert is_fallback(out[L1])
    assert len(fake_llm.calls) == 2


def test_extraction_retry_recovers_after_bad_first_output(fake_llm):
    fake_llm(["oops", json.dumps({"results": [result(L1, location="Niger")]})])
    assert extract_details_in_batch(prepared(L1))[L1]["location"] == "Niger"


def test_extraction_non_container_json_falls_back(fake_llm):
    fake_llm(["42"])
    assert is_fallback(extract_details_in_batch(prepared(L1))[L1])


def test_extraction_bad_request_error_falls_back(fake_llm):
    fake_llm([make_bad_request_error(), make_bad_request_error()])
    out = extract_details_in_batch(prepared(L1, L2))
    assert all(is_fallback(v) for v in out.values())
    assert len(fake_llm.calls) == 2


def test_extraction_api_error_falls_back(fake_llm):
    fake_llm([make_api_error(), make_api_error()])
    assert is_fallback(extract_details_in_batch(prepared(L1))[L1])


def test_extraction_fallback_date_comes_from_published(fake_llm):
    fake_llm([make_api_error(), make_api_error()])
    out = extract_details_in_batch(prepared(L1, published="2026-10-09"))
    assert out[L1]["date"] == "2026-10-09"


def test_extraction_normalizes_model_date(fake_llm):
    fake_llm([json.dumps({"results": [result(L1, date="October 5, 2026")]})])
    assert extract_details_in_batch(prepared(L1))[L1]["date"] == "2026-10-05"


def test_empty_batch_makes_no_call(fake_llm):
    assert extract_details_in_batch([]) == {}
    assert fake_llm.calls == []


def test_prepare_entry_strips_html_and_truncates_summary():
    e = disaster("t", L1, summary="<p>Hello <b>world</b></p>" + "x" * 2000)
    p = prepare_entry_for_extraction(e)
    assert p["id"] == L1
    assert p["summary"].startswith("Hello") and "<" not in p["summary"]
    assert len(p["summary"]) <= 1000


def test_strip_json_fences():
    assert strip_json_fences('```json\n{"a":1}\n```') == '{"a":1}'
    assert strip_json_fences('```\n{"a":1}\n```') == '{"a":1}'
    assert strip_json_fences(None) is None


# ---- grouping ------------------------------------------------------------
def test_group_disasters_filters_before_calling_llm(fake_llm):
    groups = group_disasters([disaster("M 5.1 - X", L1, source_type="usgs")])
    assert not groups
    assert fake_llm.calls == []


def test_group_disasters_batches_by_batch_size(fake_llm, monkeypatch):
    monkeypatch.setattr(process_data, "BATCH_SIZE", 2)
    ds = [disaster(f"Flood {i}", f"https://r/{i}", source_type="reliefweb") for i in range(5)]
    group_disasters(ds)
    assert len(fake_llm.calls) == 3


def test_group_key_includes_gdacs_alert_level(fake_llm):
    ds = [disaster("Orange earthquake alert (Magnitude 6.5M) in X", L1, source_type="gdacs")]
    groups = group_disasters(ds)
    assert any("(Orange Alert)" in k for k in groups)


def test_null_extraction_fields_never_produce_None_in_group_keys(fake_llm):
    fake_llm([json.dumps({"results": [result(L1, disaster_type=None, location=None, date=None)]})])
    groups = group_disasters([disaster("Floods", L1, source_type="reliefweb")])
    assert groups and not any("None" in k for k in groups)


@pytest.mark.xfail(strict=True, reason="BUG: swapped ids in a batch response are trusted, so details are cross-attributed (needs index ids to fix)")
def test_swapped_ids_do_not_cross_attribute_details(fake_llm):
    a = disaster("Earthquake rocks Chile", L1, source_type="reliefweb")
    b = disaster("Wildfire burns Oregon", L2, source_type="reliefweb")
    # The model describes A's content but labels it with B's id, and vice versa.
    fake_llm([json.dumps({"results": [
        result(L2, disaster_type="Earthquake", location="Chile"),
        result(L1, disaster_type="Wildfire", location="Oregon"),
    ]})])
    groups = group_disasters([a, b])
    key_for_a = next(k for k, items in groups.items() if any(i["link"] == L1 for i in items))
    assert "Earthquake" in key_for_a


# ---- process_disasters --------------------------------------------------
def test_process_disasters_empty_status_when_everything_filtered(fake_llm):
    groups, status = process_disasters([disaster("M 5.0 - X", L1, source_type="usgs")])
    assert (groups, status) == (None, "empty")


def test_process_disasters_attaches_links_from_source_not_llm(fake_llm):
    groups, status = process_disasters([disaster("Floods", L1, source_type="reliefweb")])
    assert status == "ok"
    assert groups[0]["items"][0]["link"] == L1


def test_process_disasters_merges_groups_per_llm(fake_llm):
    ds = [disaster("Flood A", L1, source_type="reliefweb"),
          disaster("Flood B", L2, source_type="reliefweb")]

    def merge(kw):
        keys = [p["key"] for p in json.loads(kw["input"].split("Input groups:\n", 1)[1])]
        return json.dumps({"groups": [{"heading": "Merged", "summary": "S.", "member_keys": keys}]})

    from conftest import echo_llm
    fake_llm([echo_llm, merge])
    groups, status = process_disasters(ds)
    assert status == "ok" and len(groups) == 1
    assert {i["link"] for i in groups[0]["items"]} == {L1, L2}


def test_process_disasters_llm_omitting_a_group_renders_it_deterministically(fake_llm):
    ds = [disaster("Flood A", L1, source_type="reliefweb"),
          disaster("Flood B", L2, source_type="reliefweb")]
    from conftest import echo_llm

    def only_first(kw):
        keys = [p["key"] for p in json.loads(kw["input"].split("Input groups:\n", 1)[1])]
        return json.dumps({"groups": [{"heading": "One", "summary": "S.", "member_keys": keys[:1]}]})

    fake_llm([echo_llm, only_first])
    groups, status = process_disasters(ds)
    assert status == "ok"
    assert {i["link"] for g in groups for i in g["items"]} == {L1, L2}


def test_process_disasters_summary_failure_degrades_to_fallback_and_notifies(fake_llm, monkeypatch):
    notices = []
    monkeypatch.setattr(process_data, "send_error_notice", lambda text: notices.append(text))
    from conftest import echo_llm
    err = make_bad_request_error()
    fake_llm([echo_llm, err, err, err])
    groups, status = process_disasters([disaster("Flood A", L1, source_type="reliefweb")])
    assert status == "ok"
    assert groups[0]["items"][0]["link"] == L1
    assert len(notices) == 1 and "summarization failed" in notices[0]


@pytest.mark.parametrize("bad", ["", "[]", '{"groups": []}', "not json"])
def test_process_disasters_unusable_summary_output_still_sends_fallback(fake_llm, bad):
    from conftest import echo_llm
    fake_llm([echo_llm, bad, bad, bad])
    groups, status = process_disasters([disaster("Flood A", L1, source_type="reliefweb")])
    assert status == "ok" and groups[0]["items"][0]["link"] == L1
