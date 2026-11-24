# Tests

`tests/test_<module>.py` covers `src/mcp_test_driver/<module>.py`.
Cross-cutting suites: `test_security.py` (defences against malicious
servers), `test_extensions.py`, `test_site.py` (the published OAuth
client document).

## Use the fakes

Tests run the real client against fake servers configured by a scenario
dict; do not mock transports
([decision 0011](../docs/decisions/0011-fakes-not-mocks.md)).  To test
new server behaviour, add a scenario key to the fake.

| Fixture (`conftest.py`) | Gives | Scenario keys are documented in |
|---|---|---|
| `fake_stdio(scenario)` | argv for a subprocess server | `fakes/engine.py` docstring |
| `fake_http(scenario)` | a running `FakeHttpServer` (`.url`) | `fakes/http_server.py` docstring, plus the engine's |
| scenario key `oauth` | an authorization server beside the HTTP fake | `fakes/auth_server.py` docstring |
| `fake_proxy(**kw)` | a `FakeProxy`; hosts resolve only through it | `fakes/proxy_server.py` docstring |
| `test_certs` | a CA and server certificate for TLS tests | `conftest.py` |
| `log_path` + `read_log()` | every message the fake received | `conftest.py` |

`fakes/memory.py` is an in-memory `Transport` for unit tests that need no
server process.  For a REPL driven by a fake, copy the `make_repl`
fixture in `test_repl.py`.

When you add a scenario key, document it in that fake's docstring: the
docstrings are the only reference.

## Running

```sh
uv run pytest                      # the gate
uv run pytest tests/test_oauth.py -k step_up
uv run tox                         # every supported Python
```

Opt-in suites, not part of the gate; both need network and Node:

- `task test-integration`: real servers (the official Python SDK and the
  TypeScript "everything" server), in `integration/`, marked
  `integration`, skipped unless `MCPTD_INTEGRATION=1`.
- `task conformance`: the official conformance suite's client scenarios
  against `--conformance-client`.  CI runs it on PRs touching `src/`.

## Tests that guard a rule

- `test_versions.py` fails when `versions.py` and the vendored schemas in
  `docs/specs/` disagree.  Fix the registry, never the schemas.
- `test_security.py::test_http_clients_only_built_in_net` fails when a
  module other than `net.py` builds an HTTP client.
- `test_site.py` fails when `site/oauth/client.json` and `oauth.py`
  disagree on the client ID or redirect URIs.
