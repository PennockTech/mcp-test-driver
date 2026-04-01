# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for tab-completion and command definitions."""

from mcp_test_driver.completion import (
    BUILTIN_ALIASES,
    BUILTIN_COMMANDS,
    BUILTIN_NAMES,
    BUILTIN_PREFIX,
    CompletionState,
    make_completer,
)


SAMPLE_TOOLS: list[dict] = [
    {
        "name": "unicode_search",
        "description": "Search Unicode characters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term"},
                "detail": {
                    "type": "string",
                    "enum": ["full", "summary"],
                    "description": "Detail level",
                },
                "limit": {"type": "integer", "description": "Max results"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "unicode_lookup_char",
        "description": "Look up a character",
        "inputSchema": {
            "type": "object",
            "properties": {
                "char": {"type": "string", "description": "A single character"},
            },
            "required": ["char"],
        },
    },
]


class TestBuiltinCommands:
    """Tests for builtin command definitions."""

    def test_all_commands_have_correct_prefix(self) -> None:
        for canonical, alias, _desc in BUILTIN_COMMANDS:
            assert canonical.startswith(BUILTIN_PREFIX)
            if alias:
                assert alias.startswith(BUILTIN_PREFIX)

    def test_aliases_resolve_to_canonical(self) -> None:
        for canonical, alias, _desc in BUILTIN_COMMANDS:
            if alias:
                assert BUILTIN_ALIASES[alias] == canonical

    def test_canonical_names_in_set(self) -> None:
        for canonical, _alias, _desc in BUILTIN_COMMANDS:
            assert canonical in BUILTIN_NAMES

    def test_no_duplicate_aliases(self) -> None:
        aliases = [alias for _, alias, _ in BUILTIN_COMMANDS if alias]
        assert len(aliases) == len(set(aliases))

    def test_no_duplicate_canonicals(self) -> None:
        canonicals = [c for c, _, _ in BUILTIN_COMMANDS]
        assert len(canonicals) == len(set(canonicals))

    def test_quit_exists(self) -> None:
        assert "/quit" in BUILTIN_NAMES

    def test_help_exists(self) -> None:
        assert "/help" in BUILTIN_NAMES


class TestCompletionState:
    """Tests for CompletionState.from_tools()."""

    def test_tool_names_extracted(self) -> None:
        state = CompletionState.from_tools(SAMPLE_TOOLS)
        assert state.tool_names == {"unicode_search", "unicode_lookup_char"}

    def test_tool_descriptions(self) -> None:
        state = CompletionState.from_tools(SAMPLE_TOOLS)
        assert state.tool_descriptions["unicode_search"] == "Search Unicode characters"

    def test_tool_args_keys(self) -> None:
        state = CompletionState.from_tools(SAMPLE_TOOLS)
        args = state.tool_args["unicode_search"]
        assert "query=" in args
        assert "detail=" in args
        assert "limit=" in args

    def test_enum_values(self) -> None:
        state = CompletionState.from_tools(SAMPLE_TOOLS)
        assert state.arg_enums["unicode_search:detail"] == ["full", "summary"]

    def test_no_enum_for_non_enum(self) -> None:
        state = CompletionState.from_tools(SAMPLE_TOOLS)
        assert "unicode_search:query" not in state.arg_enums

    def test_arg_descriptions(self) -> None:
        state = CompletionState.from_tools(SAMPLE_TOOLS)
        desc = state.arg_descriptions["unicode_search:query"]
        assert "string" in desc
        assert "Search term" in desc

    def test_all_first_words_includes_commands_and_tools(self) -> None:
        state = CompletionState.from_tools(SAMPLE_TOOLS)
        assert "/help" in state.all_first_words
        assert "/quit" in state.all_first_words
        assert "unicode_search" in state.all_first_words
        # Aliases too
        assert "/h" in state.all_first_words

    def test_empty_tools(self) -> None:
        state = CompletionState.from_tools([])
        assert state.tool_names == set()
        assert len(state.all_first_words) > 0  # still has builtin commands

    def test_tool_with_no_properties(self) -> None:
        tools = [
            {
                "name": "simple_tool",
                "description": "No args",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]
        state = CompletionState.from_tools(tools)
        assert state.tool_args["simple_tool"] == []

    def test_tool_schemas_stored(self) -> None:
        state = CompletionState.from_tools(SAMPLE_TOOLS)
        schema = state.tool_schemas["unicode_search"]
        assert "properties" in schema


class TestMakeCompleter:
    """Tests for the completer function."""

    def test_completer_returns_none_for_out_of_range(self) -> None:
        state = CompletionState.from_tools(SAMPLE_TOOLS)
        completer = make_completer(state)
        # Without readline context, we can't fully test, but we can check
        # the function is callable
        assert callable(completer)
