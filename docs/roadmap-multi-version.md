<!-- The approved plan for multi-version MCP support, kept for the record.
     See "Status" for what was built and where it differs. -->

# Roadmap: make mcp-test-driver a multi-version MCP diagnostic driver

## Status (2026-11-12): all phases implemented

| Phase | Commit(s) on the feature branch |
|---|---|
| 0 Specs, feature matrix, this roadmap | `4d2aa62` |
| Step A GitHub Pages CIMD document | `e8aa191` |
| 1–2 Core rebuild, negotiation, diagnostics | `033cdf7` |
| 3 Elicitation, sampling, progress | `ed961fa` |
| 4 2026-07-28 stateless protocol, dual-era negotiation | `a813414` |
| 5 HTTP+SSE fallback, OAuth discovery, server cards, `--otel` | `99900d8` |
| 6 OAuth login | `6671a5a` |
| 7 Probing, `--check`, conformance client | `ab3f745` |
| 8 Tasks, MCP Apps, skills, extensions, docs | `6d0ef23` |

Verification: about 510 unit/end-to-end tests against scenario-driven fake
servers; opt-in integration tests against the official Python SDK (both
eras, stdio and HTTP) and the TypeScript "everything" server; and every
client scenario in the official conformance suite's 2025-11-25 and
2026-07-28 requirement lists (50/50).

Where the build differs from the plan below:

- `--era auto` is the default, as planned; the Streamable HTTP GET stream
  is also opened right after `initialize` (as SDK clients do), because the
  conformance suite showed servers sending requests there.
- SSE resumption and the GET stream landed with Phase 4, alongside the
  per-stream reader threads they needed.
- The `validate` extra was not added (it would rewrite `uv.lock`, and CI
  pins an older uv); `structuredContent` is validated when `jsonschema` is
  importable, and its absence is noted.
- `-c/--exec`, `--elicitation-auto` and `--conformance-client` were added
  to make scripted runs and the official client suite possible.
- OAuth refuses to continue on an `iss` mismatch, a mismatched AS
  metadata issuer, or protected-resource metadata naming a different
  resource; everything else is a finding.

Not done / future work:

- OAuth extensions beyond client credentials: enterprise-managed
  authorization (SEP-990), DPoP, `private_key_jwt`, WIF jwt-bearer.
- The CIMD document goes live once `site/` is on `main` and Pages is set
  to "GitHub Actions".
- Skills: `resources/directory/read` is not wrapped (use `/send`).
- `sampling.tools` / `sampling.context` capabilities are not declared
  (a `!json` reply can still carry `tool_use`).
- Automated comparison with the conformance suite's *server* leg (done by
  hand: verdicts agree where scenarios overlap).


## Context

The driver was built against the **2024-11-05** spec. It hard-codes `PROTOCOL_VERSION = "2024-11-05"` in `protocol.py:56`, yet its HTTP transport is the newer 2025-03-26 Streamable HTTP. Since then the spec has moved four times:

- **Released revisions:** `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`, and **`2026-07-28`** (latest, final since 2026-07-28).
- **Draft:** differs from 2026-07-28 by only two items: CIMD draft-03, and `_meta` restored on a few params.
- **Next release:** targeted for 2027-03-30.

2026-07-28 is a break between eras:

- No `initialize` handshake and no sessions.
- Each request carries its version and capabilities in `_meta`.
- A new `server/discover` method.
- Server→client requests are replaced by Multi Round-Trip Requests (MRTR).
- `subscriptions/listen` replaces the GET stream and `resources/subscribe`.
- `ping` and `logging/setLevel` are removed.

The spec defines a **dual-era client** model: probe with `server/discover`, then fall back to `initialize`. The driver should implement exactly that, report what it found at start-up, and flag conformance problems. It should work as a general MCP development and maintenance tool across every released revision.

Decisions made with the user:

1. Vendor each version's official `schema.json` under `docs/specs/<ver>/`, and write a feature-matrix doc.
2. **Support every dated revision, not just two:** 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25 and 2026-07-28.
   - Track `draft` through its vendored schema.
   - Accept `2024-10-07` (still in the TypeScript SDK's supported list) as a pre-release alias of 2024-11-05, with an info finding.
   - The registry is data-driven, so adding 2027-03-30 means one table entry plus re-fetching the schemas.
3. Support the legacy 2024-11-05 HTTP+SSE transport, with automatic fallback.
4. **Auth (revised):**
   - Discovery diagnostics and static tokens come first.
   - **A real OAuth login follows:** authorization code + PKCE with a loopback (127.0.0.1) callback, plus the other flows listed under "Authorization flows" below.

### Guiding principle: exercise everything, hide nothing, report conformance

The negotiated or claimed version sets only **defaults**:

- the wire profile: handshake or `_meta`, which headers to send, and which version we offer
- how findings are annotated

It **never hides or blocks a feature.**

- Every command and every method is always available.
  - If the negotiated version doesn't define a method, we send it anyway and record an info finding: "`tasks/list` is not in negotiated 2025-06-18; sent anyway".
  - Commands for methods the negotiated version lacks, such as `/ping` under 2026-07-28, warn and then send.
- Everything the server returns is rendered in full, including fields from newer versions and unknown fields.
- Each finding has one of three kinds:
  - **violation**: breaks a MUST of the claimed version, or the server contradicts itself
  - **beyond-claimed**: a feature from a later version or an extension, observed or probed while the server claims an older version
  - **deprecated**: the server uses a feature that is deprecated at its claimed version
- **Partial-feature discovery**, i.e. finding newer features on a server that claims an older version:
  - **Passive:** check every response, capability key, notification and content block against the registry for every version, not just the negotiated one. Tag each item with the earliest version that defines it.
  - **Active:** `/probe` and `--probe` send targeted requests and tabulate the results. Examples:
    - `server/discover` on a legacy session
    - `MCP-Protocol-Version` header variants
    - a batch request
    - a GET stream
    - `subscriptions/listen`
    - `tasks/list`
    - `completion/complete` without the `completions` capability
    - `resources/subscribe` without `subscribe: true`
    - `--probe-versions`: re-initialize offering each known version, and probe the modern era as well, which also detects dual-era servers
  - The result is a **per-server feature matrix**: the same rows as [mcp-feature-matrix.md](mcp-feature-matrix.md), with columns *claimed* and *observed*.
- **Raw access for negative and edge testing:**
  - `/send <method> [json]`, `/notify <method> [json]`, and `/raw <json-or-array>` (malformed or batch input allowed)
  - `/meta k=v` and `/header K: V` for sticky per-request overrides
  - `--wire-version V`, to speak one version's wire format while the server claims another

Sources:

- Everything here comes from the spec repo, `modelcontextprotocol/modelcontextprotocol`: the schemas and the `docs/specification/<ver>/` pages that modelcontextprotocol.io is built from.
- The schemas and changelogs are vendored under [specs/](specs/); [specs/SOURCES](specs/SOURCES) pins the commit.
- The 2026-07-28 claims were checked against these primary pages: the changelog, `basic/patterns/mrtr`, the backward-compatibility sections of `basic/versioning` and both transport pages, and `DiscoverResult` in the schema.

---

## Spec context

The per-revision feature matrix, which includes what the driver supports today, lives in
[mcp-feature-matrix.md](mcp-feature-matrix.md).  It is generated from the vendored
schemas in [specs/](specs/).

### SEPs and extensions beyond the dated core

The SEP index has 44 SEPs at Final and one Accepted (SEP-3398, the release cycle). Most Final SEPs are already part of a dated revision, and the matrix above covers them. These are the ones the driver should handle as their own items:

| SEP | What it is | Driver support |
|---|---|---|
| 2133 / 3392 | Extensions mechanism (`capabilities.extensions`), off by default | Show both sides' extensions. `--extension ID[=json]` opts in. Unknown IDs are listed, never dropped. |
| 2663 | Tasks extension `io.modelcontextprotocol/tasks` (get / update / cancel, unsolicited task handles) | `/tasks`, and polling a task handle a tool returns |
| 1686 | Tasks, experimental core in 2025-11-25 | `/tasks list\|get\|result\|cancel` and `--task` on calls |
| 1865 | MCP Apps `io.modelcontextprotocol/ui` (`text/html;profile=mcp-app` resources linked from tool `_meta`) | List app resources per tool; `/app save` writes the HTML to a file for viewing in a browser. Not rendered in the terminal. |
| 2640 | Skills extension | Inspect and list. Details come from reading the SEP during that phase. |
| 2127 | Server Cards | `/server-card`, which cross-checks the card against `server/discover` |
| 414 | OpenTelemetry `traceparent` / `tracestate` / `baggage` in `_meta` | `--otel`: send a fresh `traceparent` on each request and print it, so server-side traces can be correlated |
| 2243 | HTTP headers `Mcp-Method` / `Mcp-Name` / `x-mcp-header` | Part of the modern wire profile; probes check that the server validates them |
| 2260 | Server requests must be associated with a client request | Diagnostic: flag server requests that arrive outside any in-flight client request |
| 1046, 990, ext-auth | OAuth client credentials, enterprise-managed authorization (ID-JAG), DPoP, WIF jwt-bearer | Client credentials is in scope. The others are stretch goals that get discovery and diagnostics only. |
| 2207 / 2468 / 2352 / 991 / 985 / 835 | Refresh tokens, `iss` validation, credentials keyed by issuer, CIMD, PRM alignment, scope step-up | Part of the OAuth work |

### Official conformance suite (`modelcontextprotocol/conformance`)

- It has frozen per-revision requirement files (`requirements/2025-11-25.yaml`, `2026-07-28.yaml`). Each lists **server** scenarios and **client** scenarios.
  - Server examples: `tools-call-*`, `resources-*`, `prompts-*`, `completion-complete`, `server-stateless`, `caching`, `sep-2164-resource-not-found`, `dns-rebinding-protection`, `input-required-result-*`.
  - Client examples: `request-metadata`, `http-standard-headers`, `http-custom-headers`, `sep-2322-client-request-state`, and about 25 `auth/*` scenarios such as metadata variants, scope selection and step-up, `iss-*`, CIMD, pre-registration and token-endpoint auth methods.
- Plan for using it:
  - Vendor those YAML files next to the schemas.
  - Name our check IDs after its scenarios wherever they correspond.
  - Its server scenarios assume its own reference fixture. Ours are generic checks that work against any server, including stdio servers, which the official runner drives only as a client.
  - Use its **client** leg in an optional CI job (needs Node) to test this driver's own client conformance, the OAuth flows included.

---

## Architecture changes

New modules in `src/mcp_test_driver/`:

- **`versions.py`**: the feature matrix in code.
  - `KNOWN_VERSIONS` in order, plus `HANDSHAKE_VERSIONS` and `MODERN_VERSIONS`.
  - `LATEST_HANDSHAKE = "2025-11-25"` and `LATEST_MODERN = "2026-07-28"`.
  - A frozen `SpecVersion` dataclass per version, holding:
    - method and notification sets by direction
    - capability keys
    - content types
    - feature flags: `batching`, `sessions`, `protocol_version_header`, `get_stream`, `sse_resume`, `mrtr`, `method_headers`, `elicitation_url`, `tasks_core`, and so on
    - the deprecated set
  - Wire-profile defaults and finding annotations look up this registry instead of comparing version strings inline. Nothing uses it to block a feature.
  - `earliest_version(feature)` tags observed items as "beyond-claimed".
- **`negotiate.py`**: era and version selection. It produces a `NegotiatedProtocol` dataclass with:
  - `era`, `version`, `offered`, `server_supported`
  - `server_info`, `capabilities`, `instructions`
  - `transport_kind` and `how_chosen` (the probe log)
- **`diagnostics.py`**:
  - `Finding(level, code, message, spec_ref)` and a `DiagnosticLog` collected during the session.
  - Checks on negotiation results, capabilities against the version, tool lint, messages that don't belong to the negotiated version, and use of deprecated features.
  - Renders the start-up report and `/info`.
- **`router.py`** (or a section of `transport.py`): one function that classifies an incoming message as response, notification or server request. It also expands batch arrays. Stdio, the Streamable HTTP SSE parser, and the new legacy-SSE transport all share it, which replaces the stdio-only loop in `transport.py:195-218`.
- **`auth_diag.py`**:
  - Parse `WWW-Authenticate`.
  - Fetch PRM: the header URL first, then the path-inserted and root well-known locations.
  - Then fetch AS metadata in RFC 8414 → OIDC probe order.
  - Fetches are GET-only, without redirects, size-capped (reuse `MAX_RESPONSE_BYTES`) and HTTPS-only (localhost excepted).
  - Every printed value goes through `sanitize`.
- **`oauth.py`**: the OAuth client, using only the standard library (`http.server`, `secrets`, `hashlib`, `webbrowser`) plus the existing urllib3. Details are under "Authorization flows" below.
- **`probe.py`**: active feature probes and the conformance check suite.
  - Each check is `Check(id, versions, kind, run)`. IDs match the official conformance scenario names where possible.
  - It produces the per-server claimed/observed matrix.
  - The REPL (`/probe`, `/check`) and `--check` / `--probe` share it.
- **`render.py`**: content-block and result rendering, moved out of `repl._print_result`.
  - Handles text, image, audio, resource, `resource_link`, `structuredContent`, `isError` and `resultType`.
  - Binary data is summarized as mime type plus size, never dumped.
  - **Every** string goes through `sanitize`. Today `repl.py:90,93,118,124` print server text raw, which breaks the AGENTS.md terminal-injection rule.

Existing modules that change:

- **`protocol.py`**:
  - `McpSession` takes a `NegotiatedProtocol`.
  - `_request` adds modern `_meta` fields when `era == modern`, and runs the MRTR loop: up to 8 rounds, a new id each round, and `inputResponses` plus `requestState` sent back.
  - `call_tool` goes through `_request` instead of duplicating it.
  - Fix `complete()`: send `{type:"ref/resource", uri}`, and add `context.arguments`.
  - New: `discover()`, `listen()`, `cancel()`, and `tasks_*`.
  - No hard gating. `ping` and `set_log_level` are always callable; a finding is recorded when the negotiated version doesn't define them. In the modern era, `/loglevel` by default stores a level that goes into `_meta`, and `/loglevel --rpc` sends `logging/setLevel` anyway.
  - A generic `send(method, params, *, wire=None)` underlies every typed method, and the `/send` and `/raw` commands use it too.
- **`transport.py`**:
  - The `Transport` Protocol gains `set_protocol(negotiated)` so HTTP can add `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` (with the `=?base64?…?=` sentinel when needed) and drop `Mcp-Session-Id` in the modern era.
  - A per-call `timeout` for probes.
  - `HttpTransport`:
    - Route every SSE event, not just the last.
    - Honour priming events and `retry`.
    - Resume via GET + `Last-Event-ID` when the stream ends before the response (handshake era ≥2025-03-26 only).
    - Raise a typed `AuthRequiredError(status, www_authenticate)` on 401/403.
    - Accept `extra_headers` from the CLI. Reject CR/LF in header names and values.
  - New `LegacySseTransport`:
    - GET the URL with `Accept: text/event-stream` and wait for the `endpoint` event.
    - Resolve the endpoint against the base URL and **require the same origin** (SSRF).
    - POST each message there.
    - A daemon reader thread puts messages on a `queue.Queue`. The main thread drains the queue and does all printing, so output never collides with the readline prompt.
  - Stdio:
    - Handle batch arrays.
    - Make the read timeout configurable; the probe uses a short timeout.
    - If the server exits during the probe, relaunch it once and record a finding.
- **`handlers.py`**: new `ElicitationHandler` and `SamplingHandler` (human in the loop). Each is opt-in and contributes to `capabilities()`.
  - **Elicitation:** prompts the user for each field in `requestedSchema` (types, enums, defaults), or accept / decline / cancel. In URL mode it prints the sanitized URL and never opens a browser.
  - **Sampling:** shows the messages and lets the user type the assistant reply, or decline.
  - **Shared path:** the same handlers serve legacy server requests and modern MRTR `inputRequests`.
  - Register `notifications/roots/list_changed` sending when `/roots` changes in the handshake era, which fixes the false `listChanged` claim.
- **`cli.py`**: move to `argparse`, using `nargs=REMAINDER` for the server command, so AGENTS.md's description of it becomes true. New flags:
  - `--protocol-version V`
  - `--era {auto,legacy,modern}`
  - `--transport {auto,stdio,http,sse}`
  - `--probe-timeout S`
  - `--header 'K: V'` (repeatable)
  - `--bearer-env VAR` (keeps the token out of argv)
  - `--insecure` (`verify_tls` exists today but has no flag)
  - `--elicitation[=form,url]`
  - `--sampling`
  - `--wire-version V`, `--extension ID[=json]`, `--otel`
  - Auth: `--client-id`, `--client-secret-env`, `--client-metadata-url`, `--browser`, `--token-cache PATH`, `--auth-flow {auto,code,client-credentials,device,none}`
  - `--check`, `--probe`, `--probe-versions`, `--json`
  - `-q/--quiet` to suppress the report
- **`repl.py` / `completion.py`**:
  - New commands: `/info`, `/complete`, `/listen [secs]` (GET stream in the handshake era, `subscriptions/listen` in the modern era), `/discover`, `/tasks …`, `/auth …`, `/server-card`, `/progress on|off`, `/send`, `/notify`, `/raw`, `/meta`, `/header`, `/probe`, `/check`, `/matrix` (the per-server claimed/observed matrix), `/app`.
  - `list_changed` notifications mark the cache stale; it refreshes before the next prompt.
  - `/list` and `/describe` show title, annotation tags, output schema and task support.
  - Ctrl-C during a request sends `notifications/cancelled` and returns to the prompt.

### Authorization flows (`oauth.py`, `auth_diag.py`)

**Discovery and diagnostics**, every step printed and turned into findings:

- PRM comes from `WWW-Authenticate` `resource_metadata`; otherwise from the path-inserted well-known URI, then the root one.
- AS metadata comes from RFC 8414, then OIDC, in the spec's probe order.
- Checks performed:
  - `authorization_servers` is present
  - `resource` matches the MCP URL
  - S256 appears in `code_challenge_methods_supported`
  - the issuer matches its metadata URL
  - whether CIMD and DCR are advertised

**Flows:**

1. **Authorization code + PKCE (S256)** with an RFC 8252 loopback redirect: listen on `127.0.0.1` on an ephemeral port, accept a single request, time out after 5 min, and validate `state`.
   - Validate `iss` (RFC 9207).
   - Send `resource=` (RFC 8707) on both the authorization and token requests.
   - Choose scope per spec: the challenged scope, else `scopes_supported`, else omit it.
   - Step up on 403 `insufficient_scope`, with a retry limit.
   - Handle `offline_access` and refresh tokens.
   - Print the authorization URL; `--browser` opens it.
   - **Paste-back mode:** when the browser runs on another machine (SSH or a cloud shell), the user pastes the final redirect URL into the prompt instead of relying on the callback.
2. **Client registration**, in spec priority order:
   - pre-registered: `--client-id`, `--client-secret-env`
   - CIMD: `--client-metadata-url`
   - DCR: deprecated but widely deployed, so still supported; sends `application_type: native`
3. **Token endpoint auth methods:** `none`, `client_secret_basic`, `client_secret_post`. These match the conformance scenarios.
4. **Client credentials** (SEP-1046 / ext-auth) for machine-to-machine servers, with a client secret. `private_key_jwt` needs a crypto library, so it ships as an optional extra later.
5. **Device authorization grant (RFC 8628)**, only when the AS advertises it. It isn't MCP-specified, but it helps on headless machines. Labelled non-spec.
6. **Stretch (diagnose only for now):** enterprise-managed authorization (SEP-990 / ID-JAG), DPoP, WIF jwt-bearer.

**Token handling:**

- Tokens stay in memory by default.
- `--token-cache PATH` persists them with mode 0600, keyed by issuer + resource, as 2026-07-28 requires.
- Trace output and the report always redact `Authorization` and token values.
- `/auth status|login|refresh|logout` commands.

**CIMD hosting (decided: GitHub Pages from this repo):**

- `client_id` is `https://pennocktech.github.io/mcp-test-driver/oauth/client.json`. It is a constant in `oauth.py`; `--client-metadata-url` overrides it.
- **Ports:** the CIMD draft defers to RFC 9700 §4.1.3, which lets loopback redirects use any port. Some authorization servers still compare redirects exactly. To serve both kinds, `redirect_uris` lists:
  - `http://127.0.0.1/callback` and `http://[::1]/callback`, portless, for ASes that allow any loopback port
  - `http://127.0.0.1:33418/callback`, for ASes that compare exactly
- The driver binds port 33418 first and falls back to an ephemeral port. `--callback-port` overrides this.
- **Document fields:** `client_id`, `client_name`, `client_uri` (the repo), `redirect_uris`, `grant_types` (`authorization_code`, `refresh_token`), `response_types` (`code`), `token_endpoint_auth_method: "none"`, `application_type: "native"`. Per the draft, it carries no secret.
- **Moving off github.io later is possible but costs something:** the `client_id` changes, and once a CNAME is set, github.io redirects to the custom domain. ASes that refuse redirects would then reject the old ID, which older released versions still use. So any custom domain should be settled before the first release that ships OAuth. `--client-metadata-url` is always available as an escape hatch.

### Era and version negotiation (in `negotiate.py`, following the spec's dual-era rules)

- **stdio, `--era auto`:**
  1. Send `server/discover` with the modern `_meta` and a short timeout (default 5 s).
  2. A `DiscoverResult` means modern: pick the highest mutually supported version.
  3. Error `-32022` means modern: retry with a version from `data.supported`.
  4. Any other error, a timeout, or EOF (relaunch first) means legacy: send `initialize` offering `--protocol-version`, default `2025-11-25`.
- **HTTP:**
  1. POST `server/discover` with the modern headers.
  2. A success, or a 400 whose body is a recognized modern error, means modern.
  3. Any other 4xx means legacy: Streamable HTTP `initialize`.
  4. If that also returns 400/404/405, GET for an `endpoint` event, which means legacy HTTP+SSE.
- **Legacy counter-offers:** accept the server's version if we know it. Otherwise record a finding and continue best-effort.
- **Record keeping:** every step goes into `how_chosen` for the report.

### Start-up report (stderr; `/info` re-prints it; `--check --json` emits it)

- Transport, and how it was chosen.
- Era, offered version, negotiated version, and the server's supported list.
- Server identity: name, title, version, description, website, and icon count.
- Instructions, truncated.
- Capability table:
  - every known capability and sub-flag for the negotiated version: present or absent
  - unknown, experimental and `extensions` keys listed
- Findings by severity. Examples:
  - a capability that the negotiated version doesn't define (`completions` under 2024-11-05, `tasks` under 2025-06-18)
  - a session id in the modern era
  - a missing `resultType`, `ttlMs` or `serverInfo` from a modern server
  - a server request on modern stdio
  - tool-name rule violations or duplicate names
  - an invalid or absent `inputSchema`
  - `structuredContent` without `outputSchema`, or the reverse
  - deprecated features in use (HTTP+SSE, roots, sampling, logging)
  - the legacy -32002 code from a modern server

---

## Phases (each must pass `uv run ruff check`, `uv run ty check`, `uv run pytest`)

0. **Specs and docs, no code changes. (Done: `4d2aa62`.)**
   - Add `scripts/fetch-specs.sh`, which pulls `schema/<ver>/schema.json` (and `schema.ts`) for every released version, plus `draft`, from `raw.githubusercontent.com`. It records the upstream commit SHA and date in `docs/specs/README.md`.
   - The same script also pulls the conformance suite's `requirements/*.yaml` into `docs/specs/conformance/`.
   - Commit the files under `docs/specs/<ver>/`.
   - Add `docs/mcp-feature-matrix.md`, with spec links.
   - Rewrite `docs/mcp-spec-notes.md` to the current state, fixing the `.well-known/mcp` claims and adding the era model.
   - Update the "Not implemented" section of `docs/security-model.md` as later phases land.
1. **Foundations and bug fixes.**
   - The cross-cutting refactors listed under "Execution" below.
   - `versions.py` covering all five revisions plus `draft`, with tests that cross-check its method and capability sets against the vendored `schema.json` files.
   - The generic `send()` with `/send`, `/notify` and `/raw`.
   - The shared message router (SSE no longer drops messages; batch support).
   - `render.py`, with the sanitize gaps fixed and every content type handled.
   - The `complete()` `ref/resource` fix and the `/complete` command.
   - The argparse CLI with `--header`, `--bearer-env` and `--insecure`.
   - Tool display: title, annotations, `outputSchema`.
2. **Handshake-era negotiation and diagnostics.**
   - Offer `2025-11-25` by default; add `--protocol-version`; handle counter-offers.
   - Send `MCP-Protocol-Version` from 2025-06-18 on.
   - Findings annotated by version (violation, beyond-claimed, deprecated), never gating.
   - `diagnostics.py` with the start-up report and `/info`, including passive beyond-claimed detection.
   - `list_changed` cache refresh, and only listing features the server advertises.
   - Progress tokens, and Ctrl-C cancellation.
3. **Client features.** Elicitation (form and URL, every enum variant, -32042 handling), human-in-the-loop sampling (including `tool_use` / `tool_result` display), and roots `list_changed`.
4. **Modern era (2026-07-28).**
   - `server/discover` probing on stdio and HTTP, and the era fallback.
   - Per-request `_meta`.
   - `Mcp-Method` / `Mcp-Name` / `x-mcp-header` headers.
   - The MRTR loop, which reuses the Phase 3 handlers.
   - `resultType` handling.
   - `/listen` → `subscriptions/listen`.
   - `/loglevel` → `_meta`.
   - `/ping` reports that ping was removed.
   - The new error codes, `CacheableResult` display, and the `extensions` capability display.
5. **HTTP completeness.**
   - `LegacySseTransport` with auto-fallback.
   - SSE resumption, priming and `retry`.
   - `/listen` over the GET stream.
   - `auth_diag.py`, plus `/auth` for discovery: on 401/403, print the discovery chain and the scopes.
   - `/server-card`, on demand only.
   - `--otel`.
6. **OAuth.** Authorization code + PKCE with a loopback callback and paste-back mode; pre-registration, CIMD and DCR; token-endpoint auth methods; refresh and step-up; client credentials; device grant if advertised; the token cache.
   - Fake authorization server fixtures cover every `auth/*` conformance client scenario we target.
   - The default CIMD identity is the GitHub Pages `client.json` from Step A.
7. **Probing and conformance.**
   - `probe.py`: `/probe`, `--probe`, `--probe-versions`, dual-era detection, and `/matrix`.
   - `/check` and `--check`: a generic server conformance suite keyed to the official scenario IDs; `--json` output; non-zero exit when there are violations.
   - An optional CI job that runs the official conformance **client** leg against this driver.
8. **Extensions.**
   - Tasks: 2025-11-25 core (`--task`, `/tasks list|get|result|cancel`) and the SEP-2663 extension (`get|update|cancel`, unsolicited handles).
   - MCP Apps (`/app`), Skills, and the `--extension` opt-in.
   - README and AGENTS.md updates. (README and the security-model doc are also updated as each phase lands.)

## Execution (approved 2026-11-12: implement every phase)

Phase 0 is done (commit `4d2aa62`).

**Branch and commits:**

- Each phase lands as one or more commits.
- Every commit passes `uv run ruff check`, `uv run ty check` and `uv run pytest`.
- Transport and protocol phases also run `uv run tox`.

### Step A: GitHub Pages CIMD site (small, first, so Pages can be enabled early)

- `site/index.html`: a one-paragraph landing page linking to the repo.
- `site/oauth/client.json`: as specified above.
- `.github/workflows/pages.yaml`:
  - Runs on push to `main` when `site/**` changes, and on `workflow_dispatch`.
  - `permissions: {}` at the top level. The build job gets `contents: read`; the deploy job gets `pages: write` and `id-token: write`, plus `environment: github-pages`.
  - Uses `actions/checkout` (`persist-credentials: false`), `actions/configure-pages`, `actions/upload-pages-artifact` (path `site`) and `actions/deploy-pages`.
  - Every action is SHA-pinned in the house style (`@<sha> # vX.Y.Z`). Current tags: configure-pages v6.0.0 `45bfe019…`, upload-pages-artifact v5.0.0 `fc324d35…`, deploy-pages v5.0.1 `368f8252…`. Re-verify these with `git ls-remote` when writing the file.
  - Same header comments as the other workflows. Audit with `uvx zizmor .github/workflows/`.
- `tests/test_site.py`: `client.json` parses; its `client_id` equals the constant in `oauth.py` (until that module exists, a constant in the test) and is HTTPS with a path; it has no `client_secret` and no shared-secret auth methods; its `redirect_uris` match what `oauth.py` binds.
- **User action:** in repo Settings → Pages → Source, choose "GitHub Actions". The site goes live only once this reaches `main`.
- The same commit syncs `docs/roadmap-multi-version.md` with this updated plan (the CIMD decision and this Execution section).

### Cross-cutting refactors (Phase 1, before features pile on)

- **`sanitize.py`:** move `sanitize` out of `transport.py`. Keep a re-export so existing imports keep working, then switch internal imports over.
- **`transport/` package:**
  - `base.py`: Transport Protocol and errors
  - `sse.py`: one SSE event parser (`id`, `event`, `data`, `retry`; byte cap), shared by Streamable HTTP and legacy SSE
  - `stdio.py`, `http.py`, and later `legacy_sse.py`
  - `__init__.py` re-exports today's public names
- **`messages.py`:** `classify()`, which sorts a message into response, notification or server request, plus batch expansion.
  - **A dispatcher owned by `McpSession`** receives everything that isn't the awaited response. It handles display, diagnostics, cache invalidation and server-request handlers.
  - Transports no longer print notifications or own the handler registry, which removes the duplicated `_show_notification` / `_handle_server_request` logic.
  - IDs of requests abandoned by Ctrl-C go in a set, and late responses to them are dropped with a dim note.
  - `Transport.drain_pending()` polls without blocking, before each prompt, so idle-time stdio notifications show up.
- **`commands.py`:** one `Command` registry (name, alias, help, handler name, argument-completion kind). It replaces the `BUILTIN_COMMANDS` tuple list in `completion.py` and the `if`/`elif` chain in `repl._dispatch_builtin`. Help, completion and dispatch all derive from it.
- **Threads:** background readers (the legacy SSE GET stream, `/listen`) only enqueue messages. The main thread drains the queue and does all printing. Interactive prompts for elicitation and sampling turn the readline completer off and back on afterwards.
- **Version dedup:** `McpSession.CLIENT_VERSION` comes from `importlib.metadata.version("mcp-test-driver")`, falling back to `__init__.__version__`.
- **CI:** enable the commented-out `ruff check` / `ty check` steps in `ci.yaml`. They pass now, which resolves the FIXME.
- **Optional dependency:** a `validate` extra (`jsonschema`). When installed, it validates `structuredContent` against `outputSchema` and checks `inputSchema` itself; otherwise those checks report "skipped". The extra is added through `uv add --optional`, so `uv.lock` stays managed by uv.

### Per-phase docs

- The feature matrix's "Today" column becomes "Driver", updated as features land.
- README options and commands, and `docs/security-model.md` (new network fetches, OAuth listener, token cache), are updated in the same commit as the feature.
- The final phase updates the AGENTS.md layout and marks the roadmap complete.

### Test infrastructure

- **`tests/fakes/stdio_server.py`:** a scenario-driven fake, configured by a JSON file passed as an argument and run under `-I`. It can act as a legacy server at version X, a modern server, a dual-era server, a server that ignores or crashes on unknown methods, or one that sends notifications, server requests or MRTR results.
- **`tests/fakes/http_server.py`:** a threaded `http.server` with pluggable route handlers. It covers Streamable HTTP, priming and resume, 404 session expiry, modern 400 error bodies, legacy HTTP+SSE (including a cross-origin `endpoint`), and 401/403 challenges.
- **`tests/fakes/auth_server.py`:** PRM and AS metadata variants, an authorize endpoint (auto-approves, then redirects to the loopback), a token endpoint with each client auth method, DCR, the CIMD fetch, `iss` variants, and step-up. One test per targeted conformance `auth/*` scenario.
- **Real servers:** `tests/integration/`, marked `integration` and skipped unless `MCPTD_INTEGRATION=1`, with a `task test-integration` shortcut. They drive:
  - Python SDK `mcp==2.3.0` (dual-era) through `uv run --with`
  - `@modelcontextprotocol/server-everything@2.3.0` through `npx`
  - an old `mcp` 1.x HTTP+SSE server
  - Run during development (Node 22, PyPI and npm are reachable here), not in default CI.
- **Conformance:** `.github/workflows/conformance.yaml` runs a pinned `@modelcontextprotocol/conformance` client leg against the driver's `--check` mode on `workflow_dispatch` and PRs, with `continue-on-error` at first. Read the harness contract (how it passes the server URL to the `--command`) from its README when implementing.

## Testing approach

- **Unit tests** go in the existing per-module files, with new `tests/test_versions.py`, `test_negotiate.py`, `test_diagnostics.py`, `test_render.py` and `test_auth_diag.py`.
- **Schema cross-check:** `test_versions.py` loads `docs/specs/*/schema.json` and asserts that every method `const` and capability property in each schema matches `versions.py`. This catches drift when specs are re-fetched.
- **Fake servers** in `tests/fakes/`:
  - A scripted stdio server (a Python script run with `-I`), configurable as legacy-version-X, modern, dual-era, a server that is silent on unknown methods, or a server that crashes on unknown methods.
  - An `http.server` fake in a thread, covering:
    - Streamable HTTP with JSON or SSE replies
    - priming and resume
    - 404 session expiry
    - the modern 400 error body
    - legacy HTTP+SSE with a cross-origin `endpoint` (must be rejected)
    - a 401 with `WWW-Authenticate`
  - These drive the era-detection matrix end to end.

## Verification

1. `uv sync --group dev`, then `uv run ruff check`, `uv run ty check` and `uv run pytest` at the end of every phase. For transport, protocol and completion changes, also run `uv run tox`.
2. Run by hand against real servers, if the network allows:
   - Python SDK v2, which is dual-era: `uv run --with 'mcp>=2' …` example server, over stdio and HTTP. Expect modern 2026-07-28 to be negotiated, and `--era legacy` to give 2025-11-25.
   - TypeScript `@modelcontextprotocol/server-everything` via `npx`, for legacy 2025-11-25 coverage: elicitation, sampling, progress, resources and `list_changed`.
   - An old pinned SDK server (`mcp==1.3.*` with SSE transport) to exercise the HTTP+SSE fallback.
   - `https://unicode.mcp.pennock.tech/mcp` from the README.
3. Check every start-up report for correct era, version and capabilities, and that `--check --json` returns a non-zero exit code for a deliberately broken fake server.
4. Run the official conformance **client** leg against the driver: `npx @modelcontextprotocol/conformance client --command "<driver invocation>" --suite …`. Target the 2026-07-28 client requirements, including `request-metadata`, `http-standard-headers`, `sep-2322-client-request-state` and the `auth/*` scenarios.
5. Compare our `--check` against the official **server** leg (`conformance server --url …`) on the same HTTP server. Where scenario IDs match, the verdicts should agree.
