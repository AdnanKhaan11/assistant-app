from __future__ import annotations

import pytest

from backend.utils.json_utils import parse_json_object


def test_parse_plain_json_object() -> None:
    assert parse_json_object(' {"category": "network", "ok": true} ') == {
        "category": "network",
        "ok": True,
    }


def test_parse_json_fence() -> None:
    assert parse_json_object('```json\n{"answer": "ready"}\n```') == {"answer": "ready"}


def test_extract_object_from_chatter_and_keep_nested_values() -> None:
    result = parse_json_object('Here is the result:\n{"outer": {"inner": 3}}\nThanks')

    assert result == {"outer": {"inner": 3}}


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", '"scalar"', "```json\n{bad}\n```"])
def test_rejects_non_object_or_invalid_json(text: str) -> None:
    with pytest.raises(ValueError):
        parse_json_object(text)


def test_rejects_non_text_input() -> None:
    with pytest.raises(ValueError, match="must be text"):
        parse_json_object(None)  # type: ignore[arg-type]

