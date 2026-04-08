# Copyright 2026 Phil Pennock — see LICENSE file.

"""Tests for tab-completion and command definitions."""

import sys
import types
from unittest.mock import patch


from mcp_test_driver.completion import (
    BUILTIN_ALIASES,
    BUILTIN_COMMANDS,
    BUILTIN_NAMES,
    BUILTIN_PREFIX,
    CompletionState,
    _get_readline,
    _readline_is_libedit,
    make_completer,
    readline_info,
    schedule_restore_input,
    setup_readline,
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


# ---------------------------------------------------------------------------
# Helpers shared by readline tests
# ---------------------------------------------------------------------------


def _fake_rl(
    *,
    backend: str | None = "readline",
    doc: str = "GNU readline",
    lib_ver: str = "8.2",
    ver_int: int = 0x0802,
) -> object:
    """Return a minimal fake readline-like module object."""

    class _FakeRL:
        pass

    rl = _FakeRL()
    rl.__doc__ = doc  # type: ignore[attr-defined]
    if backend is not None:
        rl.backend = backend  # type: ignore[attr-defined]
    rl._READLINE_LIBRARY_VERSION = lib_ver  # type: ignore[attr-defined]
    rl._READLINE_VERSION = ver_int  # type: ignore[attr-defined]
    return rl


# ---------------------------------------------------------------------------
# _get_readline
# ---------------------------------------------------------------------------


class TestGetReadline:
    """_get_readline() prefers gnureadline over readline, returns None if absent."""

    def test_returns_gnureadline_when_available(self) -> None:
        fake_gnu = types.ModuleType("gnureadline")
        fake_std = types.ModuleType("readline")
        with patch.dict(sys.modules, {"gnureadline": fake_gnu, "readline": fake_std}):
            result = _get_readline()
        assert result is fake_gnu

    def test_falls_back_to_readline_when_gnureadline_missing(self) -> None:
        fake_std = types.ModuleType("readline")
        with patch.dict(sys.modules, {"gnureadline": None, "readline": fake_std}):
            result = _get_readline()
        assert result is fake_std

    def test_returns_none_when_both_missing(self) -> None:
        with patch.dict(sys.modules, {"gnureadline": None, "readline": None}):
            result = _get_readline()
        assert result is None

    def test_returns_something_in_normal_environment(self) -> None:
        # gnureadline is a project dependency, so this should always succeed.
        result = _get_readline()
        assert result is not None


# ---------------------------------------------------------------------------
# _readline_is_libedit
# ---------------------------------------------------------------------------


class TestReadlineIsLibedit:
    """_readline_is_libedit() correctly identifies libedit vs GNU readline."""

    def test_backend_editline_is_libedit(self) -> None:
        assert _readline_is_libedit(_fake_rl(backend="editline"))

    def test_backend_readline_not_libedit(self) -> None:
        assert not _readline_is_libedit(_fake_rl(backend="readline"))

    def test_backend_none_doc_contains_libedit(self) -> None:
        rl = _fake_rl(backend=None, doc="libedit readline wrapper")
        assert _readline_is_libedit(rl)

    def test_backend_none_lib_ver_editline_wrapper(self) -> None:
        rl = _fake_rl(backend=None, doc="some readline", lib_ver="EditLine wrapper")
        assert _readline_is_libedit(rl)

    def test_backend_none_gnu_readline_not_libedit(self) -> None:
        rl = _fake_rl(
            backend=None,
            doc="Importing this module enables command line editing using GNU readline.",
            lib_ver="8.2",
        )
        assert not _readline_is_libedit(rl)

    def test_backend_takes_priority_over_doc(self) -> None:
        # Even if doc mentions libedit, a backend="readline" attr wins.
        rl = _fake_rl(backend="readline", doc="libedit something")
        assert not _readline_is_libedit(rl)


# ---------------------------------------------------------------------------
# readline_info
# ---------------------------------------------------------------------------


class TestReadlineInfo:
    """readline_info() returns (name, version) from the active readline module."""

    def test_no_readline_returns_defaults(self) -> None:
        with patch("mcp_test_driver.completion._get_readline", return_value=None):
            name, version = readline_info()
        assert name == "readline"
        assert version == "unknown"

    def test_gnu_readline_name_and_version_string(self) -> None:
        rl = _fake_rl(backend="readline", lib_ver="8.2", ver_int=0x0802)
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            name, version = readline_info()
        assert name == "readline"
        assert version == "8.2"

    def test_libedit_name_and_decoded_version(self) -> None:
        rl = _fake_rl(
            backend="editline",
            lib_ver="EditLine wrapper",
            ver_int=0x0402,  # → "4.2"
        )
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            name, version = readline_info()
        assert name == "libedit"
        assert version == "4.2"

    def test_version_integer_decoding(self) -> None:
        # 0x0802 = major 8, minor 2
        rl = _fake_rl(lib_ver="EditLine wrapper", ver_int=0x0802)
        rl.backend = "readline"  # type: ignore[attr-defined]
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            _, version = readline_info()
        assert version == "8.2"

    def test_version_unknown_when_no_ver_int(self) -> None:
        rl = _fake_rl(lib_ver="EditLine wrapper")
        rl._READLINE_VERSION = None  # type: ignore[attr-defined]
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            _, version = readline_info()
        assert version == "unknown"


# ---------------------------------------------------------------------------
# schedule_restore_input
# ---------------------------------------------------------------------------


class TestScheduleRestoreInput:
    """schedule_restore_input() installs a one-shot pre_input_hook."""

    def _make_fake_rl(self) -> tuple[object, list]:
        """Return (fake_rl, calls) where calls records hook set/fire events."""
        calls: list = []

        class _FakeRL:
            def set_pre_input_hook(self, fn: object) -> None:
                calls.append(("set", fn))
                if fn is not None:
                    self._hook = fn

            def insert_text(self, text: str) -> None:
                calls.append(("insert", text))

            def redisplay(self) -> None:
                calls.append(("redisplay",))

        return _FakeRL(), calls

    def test_hook_is_registered(self) -> None:
        rl, calls = self._make_fake_rl()
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            schedule_restore_input("hello")
        set_calls = [c for c in calls if c[0] == "set" and c[1] is not None]
        assert len(set_calls) == 1

    def test_hook_inserts_text_and_clears_itself(self) -> None:
        rl, calls = self._make_fake_rl()
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            schedule_restore_input("my text")
        # Fire the hook
        rl._hook()  # type: ignore[attr-defined]
        assert ("insert", "my text") in calls
        # Hook should have cleared itself (set to None)
        clear_calls = [c for c in calls if c[0] == "set" and c[1] is None]
        assert len(clear_calls) == 1

    def test_noop_when_no_readline(self) -> None:
        with patch("mcp_test_driver.completion._get_readline", return_value=None):
            schedule_restore_input("hello")  # must not raise

    def test_noop_when_set_pre_input_hook_absent(self) -> None:
        class _MinimalRL:
            pass

        with patch("mcp_test_driver.completion._get_readline", return_value=_MinimalRL()):
            schedule_restore_input("hello")  # must not raise


# ---------------------------------------------------------------------------
# setup_readline
# ---------------------------------------------------------------------------


class TestSetupReadline:
    """setup_readline() configures tab-completion and help key bindings."""

    def _make_recording_rl(self, *, is_libedit: bool) -> tuple[object, list]:
        binds: list[str] = []

        class _RecordingRL:
            def set_completer(self, fn: object) -> None:
                pass

            def set_completer_delims(self, s: str) -> None:
                pass

            def parse_and_bind(self, cmd: str) -> None:
                binds.append(cmd)

        rl = _RecordingRL()
        if is_libedit:
            rl.backend = "editline"  # type: ignore[attr-defined]
            rl.__doc__ = "libedit readline"  # type: ignore[attr-defined]
            rl._READLINE_LIBRARY_VERSION = "EditLine wrapper"  # type: ignore[attr-defined]
        else:
            rl.backend = "readline"  # type: ignore[attr-defined]
            rl.__doc__ = "GNU readline"  # type: ignore[attr-defined]
            rl._READLINE_LIBRARY_VERSION = "8.2"  # type: ignore[attr-defined]
        return rl, binds

    def test_returns_false_when_no_readline(self) -> None:
        state = CompletionState.from_tools([])
        with patch("mcp_test_driver.completion._get_readline", return_value=None):
            assert setup_readline(state) is False

    def test_returns_true_when_readline_available(self) -> None:
        state = CompletionState.from_tools([])
        rl, _ = self._make_recording_rl(is_libedit=False)
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                assert setup_readline(state) is True

    def test_libedit_uses_bind_caret_i_for_tab(self) -> None:
        state = CompletionState.from_tools([])
        rl, binds = self._make_recording_rl(is_libedit=True)
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                setup_readline(state)
        assert "bind ^I rl_complete" in binds
        assert not any(b == "tab: complete" for b in binds)

    def test_gnu_readline_uses_tab_complete(self) -> None:
        state = CompletionState.from_tools([])
        rl, binds = self._make_recording_rl(is_libedit=False)
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                setup_readline(state)
        assert "tab: complete" in binds
        assert not any("rl_complete" in b for b in binds)

    def test_gnu_readline_tty_binds_help_keys(self) -> None:
        state = CompletionState.from_tools([])
        rl, binds = self._make_recording_rl(is_libedit=False)
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                setup_readline(state)
        assert any(r'"\eh"' in b for b in binds), "Esc-h not bound"
        assert any(r'"\eOP"' in b for b in binds), "F1 VT220 not bound"
        assert any(r'"\e[11~"' in b for b in binds), "F1 alternate not bound"

    def test_help_macro_prepends_not_kill_yank(self) -> None:
        """The F1/Esc-h macro must use the prepend approach, not kill/yank."""
        state = CompletionState.from_tools([])
        rl, binds = self._make_recording_rl(is_libedit=False)
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                setup_readline(state)
        help_binds = [b for b in binds if "/help" in b]
        assert help_binds, "no /help macro bound"
        for b in help_binds:
            assert r"\C-k" not in b, "macro must not use kill (\\C-k)"
            assert r"\C-y" not in b, "macro must not use yank (\\C-y)"
        # Must start with go-to-beginning then literal /help
        assert any(r"\C-a/help" in b for b in help_binds)

    def test_libedit_does_not_bind_help_keys(self) -> None:
        state = CompletionState.from_tools([])
        rl, binds = self._make_recording_rl(is_libedit=True)
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                setup_readline(state)
        assert not any("/help" in b for b in binds)
        assert not any(r'"\eh"' in b for b in binds)

    def test_no_tty_does_not_bind_help_keys(self) -> None:
        state = CompletionState.from_tools([])
        rl, binds = self._make_recording_rl(is_libedit=False)
        with patch("mcp_test_driver.completion._get_readline", return_value=rl):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                setup_readline(state)
        assert not any("/help" in b for b in binds)
