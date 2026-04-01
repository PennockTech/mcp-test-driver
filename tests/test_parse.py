# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for argument parsing."""

import pytest

from mcp_test_driver.parse import parse_args


class TestParseArgs:
    """Tests for parse_args()."""

    def test_empty_string(self) -> None:
        assert parse_args("") == {}

    def test_whitespace_only(self) -> None:
        assert parse_args("   ") == {}

    def test_json_object(self) -> None:
        result = parse_args('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_json_nested(self) -> None:
        result = parse_args('{"a": {"b": "c"}}')
        assert result == {"a": {"b": "c"}}

    def test_key_value_string(self) -> None:
        result = parse_args("name=hello")
        assert result == {"name": "hello"}

    def test_key_value_multiple(self) -> None:
        result = parse_args("name=hello exact=true")
        assert result == {"name": "hello", "exact": True}

    def test_key_value_bool_true(self) -> None:
        assert parse_args("flag=true") == {"flag": True}
        assert parse_args("flag=True") == {"flag": True}
        assert parse_args("flag=TRUE") == {"flag": True}

    def test_key_value_bool_false(self) -> None:
        assert parse_args("flag=false") == {"flag": False}
        assert parse_args("flag=False") == {"flag": False}

    def test_key_value_integer(self) -> None:
        assert parse_args("count=42") == {"count": 42}
        assert parse_args("count=0") == {"count": 0}
        assert parse_args("count=-5") == {"count": -5}

    def test_key_value_string_not_int(self) -> None:
        result = parse_args("name=hello123world")
        assert result == {"name": "hello123world"}
        assert isinstance(result["name"], str)

    def test_key_value_with_spaces(self) -> None:
        result = parse_args('name="hello world"')
        assert result == {"name": "hello world"}

    def test_token_without_equals_ignored(self) -> None:
        result = parse_args("key=val standalone other=two")
        assert result == {"key": "val", "other": "two"}

    def test_empty_value(self) -> None:
        result = parse_args("key=")
        assert result == {"key": ""}

    def test_value_with_equals(self) -> None:
        result = parse_args("expr=a=b")
        assert result == {"expr": "a=b"}

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(Exception):
            parse_args("{not valid json")

    def test_unicode_value(self) -> None:
        result = parse_args("char=✓")
        assert result == {"char": "✓"}
