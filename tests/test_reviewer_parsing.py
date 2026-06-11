import pytest

from content_engine.agents.parsing import JSONExtractionError, extract_json
from content_engine.agents.reviewer import parse_review


def test_extract_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_fenced_json():
    text = "Here is the result:\n```json\n{\"approved\": true}\n```\nThanks!"
    assert extract_json(text) == {"approved": True}


def test_extract_json_with_prose_around_object():
    text = 'Sure! {"score": 8.5, "nested": {"x": [1,2,3]}} done.'
    assert extract_json(text) == {"score": 8.5, "nested": {"x": [1, 2, 3]}}


def test_extract_handles_braces_in_strings():
    text = '{"text": "a } b { c", "ok": true}'
    assert extract_json(text) == {"text": "a } b { c", "ok": True}


def test_extract_raises_on_garbage():
    with pytest.raises(JSONExtractionError):
        extract_json("no json here at all")


def test_parse_review_full():
    data = {
        "approved": False,
        "overall_score": 6.5,
        "severity": "high",
        "issues": [
            {"type": "unsupported_claim", "severity": "high", "text": "x",
             "problem": "p", "suggested_fix": "f"},
        ],
        "recommended_action": "revise",
        "notes": "n",
    }
    review = parse_review(data)
    assert review.approved is False
    assert review.overall_score == 6.5
    assert len(review.issues) == 1
    assert len(review.high_severity_issues) == 1
    assert review.issues[0].type == "unsupported_claim"


def test_parse_review_tolerates_missing_fields():
    review = parse_review({"overall_score": "7"})
    assert review.overall_score == 7.0
    assert review.issues == []
    assert review.recommended_action == "revise"


def test_parse_review_handles_bad_score():
    review = parse_review({"overall_score": "not-a-number"})
    assert review.overall_score == 0.0


def test_parse_review_clamps_score_to_0_10():
    assert parse_review({"overall_score": 15}).overall_score == 10.0
    assert parse_review({"overall_score": -3}).overall_score == 0.0
