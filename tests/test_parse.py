# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for argument parsing."""

import pytest

from mcp_test_driver.parse import (
    _coerce_scalar,
    _parse_dict_content,
    _parse_list_content,
    _parse_value,
    _split_at_depth0,
    parse_args,
)


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

    def test_json_array_not_parsed_as_json(self) -> None:
        # Arrays don't start with { so they go through key=val parsing.
        # Tokens without = are silently ignored — result is empty dict.
        result = parse_args("[1, 2, 3]")
        assert result == {}

    def test_json_object_starting_with_brace_but_invalid_type(self) -> None:
        # Simulate edge case: technically this is valid JSON starting with {
        # but json.loads would return a dict, so it's fine.
        result = parse_args('{"key": [1, 2]}')
        assert result == {"key": [1, 2]}
        assert isinstance(result, dict)

    def test_list_value_simple(self) -> None:
        result = parse_args("paths=[src/foo.py src/bar.py]")
        assert result == {"paths": ["src/foo.py", "src/bar.py"]}

    def test_list_value_with_commas(self) -> None:
        result = parse_args("tags=[foo,bar,baz]")
        assert result == {"tags": ["foo", "bar", "baz"]}

    def test_list_value_mixed_separators(self) -> None:
        result = parse_args("items=[a, b c, d]")
        assert result == {"items": ["a", "b", "c", "d"]}

    def test_list_value_quoted_items(self) -> None:
        result = parse_args('tags=["hello world", foo, 42]')
        assert result == {"tags": ["hello world", "foo", 42]}

    def test_list_value_bool_coercion(self) -> None:
        result = parse_args("flags=[true false true]")
        assert result == {"flags": [True, False, True]}

    def test_list_value_int_coercion(self) -> None:
        result = parse_args("nums=[1 2 3]")
        assert result == {"nums": [1, 2, 3]}

    def test_list_value_empty(self) -> None:
        result = parse_args("items=[]")
        assert result == {"items": []}

    def test_dict_value_simple(self) -> None:
        result = parse_args("filter={type: file}")
        assert result == {"filter": {"type": "file"}}

    def test_dict_value_multiple_keys(self) -> None:
        result = parse_args("filter={type: file, recursive: true}")
        assert result == {"filter": {"type": "file", "recursive": True}}

    def test_dict_value_int(self) -> None:
        result = parse_args("options={limit: 10}")
        assert result == {"options": {"limit": 10}}

    def test_dict_value_quoted_value(self) -> None:
        result = parse_args('options={label: "my project"}')
        assert result == {"options": {"label": "my project"}}

    def test_dict_value_no_space_after_colon(self) -> None:
        result = parse_args("filter={type:file}")
        assert result == {"filter": {"type": "file"}}

    def test_relaxed_brace_syntax(self) -> None:
        # Top-level {…} that is NOT valid JSON falls through to relaxed parser.
        result = parse_args("{type: file, recursive: true}")
        assert result == {"type": "file", "recursive": True}

    def test_relaxed_brace_unmatched_raises(self) -> None:
        with pytest.raises(ValueError, match="Unmatched"):
            parse_args("{type: file")

    def test_multiple_keys_with_list_and_dict(self) -> None:
        result = parse_args("paths=[a b] opts={limit: 5}")
        assert result == {"paths": ["a", "b"], "opts": {"limit": 5}}

    def test_list_in_dict_value(self) -> None:
        result = parse_args("meta={tags: [a b]}")
        assert result == {"meta": {"tags": ["a", "b"]}}

    def test_dict_in_list_value(self) -> None:
        # Nested dict inside a list
        result = parse_args("items=[{k: v}]")
        assert result == {"items": [{"k": "v"}]}


class TestSplitAtDepth0:
    """Tests for _split_at_depth0()."""

    def test_simple_space(self) -> None:
        assert _split_at_depth0("a b c", " ") == ["a", "b", "c"]

    def test_brackets_not_split(self) -> None:
        result = _split_at_depth0("a [b c] d", " ")
        assert result == ["a", "[b c]", "d"]

    def test_braces_not_split(self) -> None:
        result = _split_at_depth0("a {b c} d", " ")
        assert result == ["a", "{b c}", "d"]

    def test_nested_brackets(self) -> None:
        result = _split_at_depth0("a [[b c] d] e", " ")
        assert result == ["a", "[[b c] d]", "e"]

    def test_quoted_string_not_split(self) -> None:
        result = _split_at_depth0('a "b c" d', " ")
        assert result == ["a", '"b c"', "d"]

    def test_single_quoted_string_not_split(self) -> None:
        result = _split_at_depth0("a 'b c' d", " ")
        assert result == ["a", "'b c'", "d"]

    def test_escaped_quote_inside_double_quoted(self) -> None:
        result = _split_at_depth0(r'a "b\"c" d', " ")
        assert result == ["a", r'"b\"c"', "d"]

    def test_multiple_stop_chars(self) -> None:
        result = _split_at_depth0("a,b c,d", " ,")
        assert result == ["a", "b", "c", "d"]

    def test_empty_string(self) -> None:
        assert _split_at_depth0("", " ") == []

    def test_no_delimiters(self) -> None:
        assert _split_at_depth0("abc", " ") == ["abc"]

    def test_consecutive_delimiters_yield_no_empty_tokens(self) -> None:
        result = _split_at_depth0("a  b", " ")
        assert result == ["a", "b"]


class TestCoerceScalar:
    """Tests for _coerce_scalar()."""

    def test_true(self) -> None:
        assert _coerce_scalar("true") is True
        assert _coerce_scalar("True") is True
        assert _coerce_scalar("TRUE") is True

    def test_false(self) -> None:
        assert _coerce_scalar("false") is False

    def test_integer(self) -> None:
        assert _coerce_scalar("42") == 42
        assert isinstance(_coerce_scalar("42"), int)

    def test_negative_integer(self) -> None:
        assert _coerce_scalar("-5") == -5

    def test_string_passthrough(self) -> None:
        assert _coerce_scalar("hello") == "hello"
        assert _coerce_scalar("3.14") == "3.14"  # float not coerced


class TestParseValue:
    """Tests for _parse_value()."""

    def test_list(self) -> None:
        assert _parse_value("[a b]", 0) == ["a", "b"]

    def test_dict(self) -> None:
        assert _parse_value("{k: v}", 0) == {"k": "v"}

    def test_double_quoted(self) -> None:
        assert _parse_value('"hello world"', 0) == "hello world"

    def test_single_quoted(self) -> None:
        assert _parse_value("'hello world'", 0) == "hello world"

    def test_scalar_int(self) -> None:
        assert _parse_value("42", 0) == 42

    def test_scalar_bool(self) -> None:
        assert _parse_value("true", 0) is True

    def test_scalar_str(self) -> None:
        assert _parse_value("hello", 0) == "hello"

    def test_depth_limit_returns_raw(self) -> None:
        # At max depth, returns the raw string rather than recursing
        from mcp_test_driver.parse import _MAX_DEPTH
        assert _parse_value("[a b]", _MAX_DEPTH + 1) == "[a b]"

    def test_unmatched_bracket_raises(self) -> None:
        with pytest.raises(ValueError, match="Unmatched"):
            _parse_value("[a b", 0)

    def test_unmatched_brace_raises(self) -> None:
        with pytest.raises(ValueError, match="Unmatched"):
            _parse_value("{k: v", 0)

    def test_empty_string(self) -> None:
        assert _parse_value("", 0) == ""


class TestParseListContent:
    """Tests for _parse_list_content()."""

    def test_simple(self) -> None:
        assert _parse_list_content("a b c", 0) == ["a", "b", "c"]

    def test_comma_separated(self) -> None:
        assert _parse_list_content("a,b,c", 0) == ["a", "b", "c"]

    def test_trailing_comma(self) -> None:
        assert _parse_list_content("a, b,", 0) == ["a", "b"]

    def test_empty(self) -> None:
        assert _parse_list_content("", 0) == []

    def test_coercion(self) -> None:
        result = _parse_list_content("true 42 hello", 0)
        assert result == [True, 42, "hello"]

    def test_depth_limit_returns_empty(self) -> None:
        from mcp_test_driver.parse import _MAX_DEPTH
        assert _parse_list_content("a b", _MAX_DEPTH + 1) == []

    def test_nested_list(self) -> None:
        result = _parse_list_content("[a b] c", 0)
        assert result == [["a", "b"], "c"]


class TestParseDictContent:
    """Tests for _parse_dict_content()."""

    def test_simple(self) -> None:
        assert _parse_dict_content("k: v", 0) == {"k": "v"}

    def test_multiple_pairs(self) -> None:
        result = _parse_dict_content("k1: v1, k2: v2", 0)
        assert result == {"k1": "v1", "k2": "v2"}

    def test_no_space_after_colon(self) -> None:
        assert _parse_dict_content("k:v", 0) == {"k": "v"}

    def test_coercion(self) -> None:
        result = _parse_dict_content("flag: true, count: 5", 0)
        assert result == {"flag": True, "count": 5}

    def test_quoted_value(self) -> None:
        result = _parse_dict_content('label: "my project"', 0)
        assert result == {"label": "my project"}

    def test_trailing_comma(self) -> None:
        result = _parse_dict_content("k: v,", 0)
        assert result == {"k": "v"}

    def test_empty(self) -> None:
        assert _parse_dict_content("", 0) == {}

    def test_depth_limit_returns_empty(self) -> None:
        from mcp_test_driver.parse import _MAX_DEPTH
        assert _parse_dict_content("k: v", _MAX_DEPTH + 1) == {}

    def test_value_is_list(self) -> None:
        result = _parse_dict_content("tags: [a b]", 0)
        assert result == {"tags": ["a", "b"]}

    def test_value_is_nested_dict(self) -> None:
        result = _parse_dict_content("inner: {x: 1}", 0)
        assert result == {"inner": {"x": 1}}

    def test_key_with_no_value_at_end(self) -> None:
        # Key with colon but nothing after it
        result = _parse_dict_content("k:", 0)
        assert result == {"k": ""}
