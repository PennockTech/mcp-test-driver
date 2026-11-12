# MCP Feature Matrix

Which Model Context Protocol features exist in which spec revision, and
what mcp-test-driver does with each today.  This is the reference for the
multi-version work described in [roadmap-multi-version.md](roadmap-multi-version.md).

## Revisions

| Revision | Status | Spec | Schema (vendored) |
|---|---|---|---|
| `2024-11-05` | released | [spec](https://modelcontextprotocol.io/specification/2024-11-05) | [`specs/2024-11-05/`](specs/2024-11-05/) |
| `2025-03-26` | released | [spec](https://modelcontextprotocol.io/specification/2025-03-26) | [`specs/2025-03-26/`](specs/2025-03-26/) |
| `2025-06-18` | released | [spec](https://modelcontextprotocol.io/specification/2025-06-18) | [`specs/2025-06-18/`](specs/2025-06-18/) |
| `2025-11-25` | released | [spec](https://modelcontextprotocol.io/specification/2025-11-25) | [`specs/2025-11-25/`](specs/2025-11-25/) |
| `2026-07-28` | **latest** released | [spec](https://modelcontextprotocol.io/specification/2026-07-28) | [`specs/2026-07-28/`](specs/2026-07-28/) |
| `draft` | unreleased | [spec](https://modelcontextprotocol.io/specification/draft) | [`specs/draft/`](specs/draft/) |

Notes:

- The draft still declares `LATEST_PROTOCOL_VERSION = "2026-07-28"`.  As
  of the vendored snapshot it differs from 2026-07-28 in only two ways:
  - the OAuth Client ID Metadata Document reference moves to draft-03
  - optional `_meta` is restored on `CreateMessageRequestParams`,
    `ElicitRequestFormParams`, `ElicitRequestURLParams`, `ElicitResult`
    and `ListRootsResult`
- The next dated revision is targeted for **2027-03-30** (SEP-3398).
- The TypeScript SDK still lists `2024-10-07` as supported.  It is a
  pre-release string that predates 2024-11-05.
- See [`specs/SOURCES`](specs/SOURCES) for the upstream commits.
  Refresh with `scripts/fetch-specs.sh`.

### Two eras

The protocol has two eras, and 2026-07-28 is a clean break between them:

- **Handshake era** (2024-11-05 through 2025-11-25):
  - `initialize` / `notifications/initialized` negotiates one version for the connection.
  - Over HTTP, an `Mcp-Session-Id` carries that state.
  - The server may send JSON-RPC requests to the client.
- **Modern (stateless) era** (2026-07-28 onwards):
  - There is no handshake and no session.
  - Every request carries `io.modelcontextprotocol/protocolVersion` and
    `io.modelcontextprotocol/clientCapabilities` in `params._meta`.
  - `server/discover` advertises the server's versions and capabilities.
  - Server-to-client requests are carried inside results, using Multi
    Round-Trip Requests (MRTR).

A **dual-era client** works with both kinds of server:

- **stdio:** probe with `server/discover`.
  - A `DiscoverResult`, or a recognized modern error such as `-32022`, means the server is modern.
  - Any other error, or a timeout, means it is legacy: fall back to `initialize`.
- **HTTP:** send a modern request.
  - If it returns a 4xx without a recognized modern error body, fall back to a legacy `initialize`.
  - If that fails too, fall back to the deprecated HTTP+SSE transport.
- The fallback must never be keyed to one specific error code.
- Clients should cache the era per server process (stdio) or origin (HTTP).

Source: `2026-07-28/basic/versioning` (backward compatibility),
`basic/transports/stdio` and `basic/transports/streamable-http`.

## Legend

| Mark | Meaning |
|---|---|
| ✓ | present |
| — | absent |
| **new** | introduced in this revision |
| ✗ | removed |
| D | deprecated (still works; slated for removal under SEP-2596) |

The **Driver** column says what mcp-test-driver does with each feature; "—" means not (yet) supported.

## JSON-RPC methods and notifications

Generated from the vendored `schema.json` files.  Direction: C→S is
client to server, S→C is server to client.

| Method | Dir | 2024-11-05 | 2025-03-26 | 2025-06-18 | 2025-11-25 | 2026-07-28 | Driver |
|---|---|---|---|---|---|---|---|
| `initialize` | C→S | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ offers 2025-11-25 by default (`--protocol-version`) |
| `notifications/initialized` | C→S | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `server/discover` | C→S | — | — | — | — | **new** | ✓ start-up probe (`--era auto`); `/discover` |
| `ping` | both | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ both directions |
| `notifications/cancelled` | both | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ sent on Ctrl-C and timeout; displayed |
| `notifications/progress` | both | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ displayed; `/progress` attaches tokens |
| `tools/list` | C→S | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `tools/call` | C→S | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `notifications/tools/list_changed` | S→C | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ refreshes the cache |
| `resources/list` | C→S | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `resources/templates/list` | C→S | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `resources/read` | C→S | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `resources/subscribe` | C→S | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `resources/unsubscribe` | C→S | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `notifications/resources/list_changed` | S→C | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ refreshes the cache |
| `notifications/resources/updated` | S→C | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ displayed |
| `prompts/list` | C→S | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `prompts/get` | C→S | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `notifications/prompts/list_changed` | S→C | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ refreshes the cache |
| `completion/complete` | C→S | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ `/complete` |
| `logging/setLevel` | C→S | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ `/loglevel` (`--rpc` forces it in 2026-07-28) |
| `notifications/message` | S→C | ✓ | ✓ | ✓ | ✓ | ✓ (D) | ✓ displayed by level |
| `subscriptions/listen` | C→S | — | — | — | — | **new** | ✓ `/listen` |
| `notifications/subscriptions/acknowledged` | S→C | — | — | — | — | **new** | ✓ checked against the request |
| `roots/list` | S→C | ✓ | ✓ | ✓ | ✓ | ✓ in MRTR (D) | ✓ requests and MRTR |
| `notifications/roots/list_changed` | C→S | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ `/roots add` / `/roots remove` |
| `sampling/createMessage` | S→C | ✓ | ✓ | ✓ | ✓ | ✓ in MRTR (D) | ✓ human-in-the-loop (`--sampling`) |
| `elicitation/create` | S→C | — | — | **new** | ✓ | ✓ in MRTR | ✓ interactive (`--elicitation`) |
| `notifications/elicitation/complete` | S→C | — | — | — | **new** | ✗ | ✓ displayed |
| `tasks/get` | both | — | — | — | **new** | moved to extension | ✓ `/tasks get`, polling |
| `tasks/result` | both | — | — | — | **new** | ✗ | ✓ `/tasks result`, after polling |
| `tasks/list` | both | — | — | — | **new** | ✗ | ✓ `/tasks list` |
| `tasks/cancel` | both | — | — | — | **new** | moved to extension | ✓ `/tasks cancel` |
| `notifications/tasks/status` | both | — | — | — | **new** | moved to extension | ✓ displayed |

"In MRTR" means that in 2026-07-28 these request shapes still exist, but
the server no longer sends them as JSON-RPC requests.  Instead it embeds
them in an `InputRequiredResult.inputRequests` map.  The client answers by
retrying its original request with `inputResponses` and the opaque
`requestState`.  Only `tools/call`, `prompts/get` and `resources/read` may
return `InputRequiredResult`.

The tasks extension (`io.modelcontextprotocol/tasks`, SEP-2663) defines
`tasks/get`, the new `tasks/update` and `tasks/cancel`, and lets servers
return unsolicited task handles.

## Lifecycle and envelope

| Feature | 2024-11-05 | 2025-03-26 | 2025-06-18 | 2025-11-25 | 2026-07-28 | Driver |
|---|---|---|---|---|---|---|
| Version negotiation | `initialize` | `initialize` | `initialize`; lifecycle rules become MUST | `initialize` | per request, in `_meta`; `server/discover`; `UnsupportedProtocolVersion` error lists the supported versions | ✓ dual-era: `server/discover` probe, then `initialize`; counter-offers, aliases, `--era` |
| Per-request `_meta` keys `io.modelcontextprotocol/{protocolVersion, clientCapabilities}` (required) and `{clientInfo, logLevel}` (optional) | — | — | — | — | **new** | ✓ |
| `io.modelcontextprotocol/serverInfo` in result `_meta` | — | — | — | — | **new** (SHOULD) | ✓ read; absence noted |
| `resultType` (`complete` / `input_required`) on every result | — | — | — | — | **new**; treat absent as `complete` | ✓ checked; MRTR loop on `input_required` |
| JSON-RPC batching | — | **new** (MUST receive) | ✗ | ✗ | ✗ | ✓ received (stdio, HTTP); `/raw` sends; flagged outside 2025-03-26 |
| `_meta` key naming rules (reserved `*.modelcontextprotocol/` and `*.mcp/` prefixes) | — | — | **new** | ✓ | ✓ | `/meta` sets any key |
| OpenTelemetry `traceparent` / `tracestate` / `baggage` in `_meta` (SEP-414) | — | — | — | — | **new** | ✓ `--otel`, `/otel`; `/meta` |
| `CacheableResult` (`ttlMs`, `cacheScope`) on list and read results | — | — | — | — | **new** (required) | ✓ shown and checked |
| `Implementation.title` | — | — | **new** | ✓ | ✓ | ✓ shown |
| `Implementation.icons`, `description`, `websiteUrl` | — | — | — | **new** | ✓ | ✓ shown |
| `instructions` | ✓ | ✓ | ✓ | ✓ | in `DiscoverResult` | ✓ start-up report |
| `capabilities.extensions` | — | — | — | — | **new** | ✓ shown; `--extension`, `/extension` |
| `progressToken`; progress `message` | token | + `message` | ✓ | ✓ | ✓ | ✓ `/progress`, `--progress` |
| `notifications/cancelled.requestId` | required | required | required | optional | ✓ | ✓ always sent |
| Pagination (`cursor` / `nextCursor`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (100-page cap) |

### Error codes

| Code | Meaning | Revisions |
|---|---|---|
| -32700, -32600, -32601, -32602, -32603 | Standard JSON-RPC codes | all |
| -32002 | Resource not found | 2024-11-05 to 2025-11-25. Replaced by -32602 in 2026-07-28; clients SHOULD still accept it. |
| -32042 | URL elicitation required | 2025-11-25 |
| -32020 | `HeaderMismatch` | 2026-07-28 |
| -32021 | `MissingRequiredClientCapability` | 2026-07-28 |
| -32022 | `UnsupportedProtocolVersion` | 2026-07-28 |
| -32000 to -32019 | Implementation-defined | 2026-07-28 policy |
| -32020 to -32099 | Reserved for the spec | 2026-07-28 policy |

## Capabilities

| Capability | Side | 2024-11-05 | 2025-03-26 | 2025-06-18 | 2025-11-25 | 2026-07-28 |
|---|---|---|---|---|---|---|
| `experimental` | both | ✓ | ✓ | ✓ | ✓ | ✓ |
| `extensions` | both | — | — | — | — | **new** |
| `roots.listChanged` | client | ✓ | ✓ | ✓ | ✓ | `roots` D |
| `sampling` | client | `{}` | `{}` | `{}` | `{context?, tools?}` | D |
| `elicitation` | client | — | — | **new** `{}` | `{form?, url?}` | ✓ |
| `tasks` | both | — | — | — | **new** (`list`, `cancel`, `requests.*`) | ✗ (extension) |
| `logging` | server | ✓ | ✓ | ✓ | ✓ | D |
| `completions` | server | — | **new** | ✓ | ✓ | ✓ |
| `prompts.listChanged` | server | ✓ | ✓ | ✓ | ✓ | ✓ |
| `resources.subscribe`, `resources.listChanged` | server | ✓ | ✓ | ✓ | ✓ | ✓ |
| `tools.listChanged` | server | ✓ | ✓ | ✓ | ✓ | ✓ |

## Server features

| Feature | 2024-11-05 | 2025-03-26 | 2025-06-18 | 2025-11-25 | 2026-07-28 | Driver |
|---|---|---|---|---|---|---|
| Tool `annotations` (readOnly, destructive, idempotent and openWorld hints, `title`) | — | **new** | ✓ | ✓ | ✓ | ✓ `/list`, `/describe` |
| Tool `title`, `outputSchema`; result `structuredContent` | — | — | **new** | ✓ | ✓; any JSON Schema 2020-12 keyword / any JSON value | ✓ shown |
| `_meta` on Tool, Resource, Prompt and content | — | — | **new** | ✓ | ✓ | ✓ shown |
| Tool name format (1–128 characters, `[A-Za-z0-9_.-]`, SEP-986) | — | — | — | **new** (SHOULD) | ✓ | ✓ checked; real names kept |
| Input validation errors returned as tool errors (`isError`, SEP-1303) | — | — | — | **new** | ✓ | ✓ shown |
| Tool / Resource / Prompt `icons` | — | — | — | **new** | ✓ | ✓ counted |
| Tool `execution.taskSupport` | — | — | — | **new** | ✗ (extension) | ✓ `/describe`; `/task` calls |
| Default JSON Schema dialect 2020-12 (SEP-1613) | — | — | — | **new** | ✓ | n/a |
| `x-mcp-header` tool params mapped to HTTP headers (SEP-2243) | — | — | — | — | **new** | ✓ sent; invalid annotations flagged (tool kept) |
| Deterministic `tools/list` order | — | — | — | — | **new** (SHOULD) | n/a |
| Annotations: `audience`, `priority` | ✓ | ✓ | + `lastModified` | ✓ | ✓ | ✓ shown |
| `completion/complete` | ✓ | ✓ (+ capability) | + `context.arguments` | ✓ | ✓ | ✓ `/complete` |
| Log level | `logging/setLevel` | ✓ | ✓ | ✓ | per request, `_meta` `logLevel` | ✓ `/loglevel` |
| Change notifications | unsolicited | unsolicited | unsolicited | unsolicited | opt in via `subscriptions/listen` | ✓ cache refresh; `/listen` |

### Content block types

| Type | 2024-11-05 | 2025-03-26 | 2025-06-18 | 2025-11-25 | 2026-07-28 | Driver |
|---|---|---|---|---|---|---|
| `text` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (sanitized; JSON pretty-printed) |
| `image` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ summarised (type, size) |
| `resource` (embedded) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `audio` | — | **new** | ✓ | ✓ | ✓ | ✓ summarised (type, size) |
| `resource_link` | — | — | **new** | ✓ | ✓ | ✓ |
| `tool_use` / `tool_result` (sampling only) | — | — | — | **new** | ✓ | ✓ shown in sampling |

## Client features (server to client)

| Feature | 2024-11-05 | 2025-03-26 | 2025-06-18 | 2025-11-25 | 2026-07-28 | Driver |
|---|---|---|---|---|---|---|
| Delivery | JSON-RPC request | request | request | request | MRTR (`InputRequiredResult`) | ✓ requests (all transports) and MRTR |
| Roots | ✓ | ✓ | ✓ | ✓ | D | ✓ |
| Sampling | ✓ | + audio | ✓ | + `tools`, `toolChoice`; `includeContext` values `thisServer` and `allServers` soft-D | D | ✓ human-in-the-loop |
| Elicitation, form mode | — | — | **new** | + defaults, titled and multi-select enums (SEP-1330, SEP-1034) | ✓ | ✓ every field type and enum style |
| Elicitation, URL mode | — | — | — | **new** (SEP-1036) | ✓, without `elicitationId` or the complete notification | ✓ consent prompt; never opens URLs |

## Transports

| Feature | 2024-11-05 | 2025-03-26 | 2025-06-18 | 2025-11-25 | 2026-07-28 | Driver |
|---|---|---|---|---|---|---|
| stdio | ✓ | ✓ + batches | ✓ | stderr may carry any logging | server MUST NOT send requests | ✓ |
| HTTP+SSE (GET → `endpoint` event, then POST) | ✓ | D | D | D | D (SEP-2596) | ✓ auto-fallback or `--transport sse`; same-origin endpoint only |
| Streamable HTTP POST (JSON or SSE response; 202 for notifications) | — | **new** | ✓ | ✓ | ✓ | ✓ every SSE event routed |
| `Mcp-Session-Id`; 404 means re-initialize; DELETE ends the session | — | ✓ | ✓ | ✓ | ✗ | ✓ |
| GET listening stream (405 if unsupported) | — | ✓ | ✓ | ✓ | ✗ (`subscriptions/listen`) | ✓ `/listen` |
| SSE resumability (`id`, `Last-Event-ID`) | — | ✓ | ✓ | + priming event, `retry`, polling (SEP-1699) | ✗; re-issue the request | ✓ resumes, honouring `retry` |
| `MCP-Protocol-Version` header | — | only during auth discovery | **new** (required) | ✓ | required; must match `_meta` | ✓ from 2025-06-18 |
| `Mcp-Method`, `Mcp-Name` headers (`=?base64?…?=` sentinel) | — | — | — | — | **new** (required) | ✓ |
| Invalid `Origin` | SHOULD validate | SHOULD | MUST validate | 403 | ✓ | n/a (server-side) |

## Authorization (HTTP transports)

| Feature | 2024-11-05 | 2025-03-26 | 2025-06-18 | 2025-11-25 | 2026-07-28 | Driver |
|---|---|---|---|---|---|---|
| OAuth 2.1 + PKCE | — | **new** | ✓ | ✓; S256 must be advertised | ✓ | ✓ `--oauth`, `/auth login`: code + PKCE, client credentials, device grant |
| AS metadata (RFC 8414) | — | **new**; falls back to `/authorize`, `/token`, `/register` | ✓ | + OIDC discovery | ✓ | ✓ fetched and checked, incl. 2025-03-26 defaults |
| Protected Resource Metadata (RFC 9728) | — | — | **new**; `WWW-Authenticate resource_metadata` | + well-known fallback (path-inserted, then root) | ✓ | ✓ header, then well-known, as specified |
| Resource indicators (RFC 8707) | — | — | **new** | ✓ | ✓ | ✓ on every authorization and token request |
| Scope challenge and step-up on 403 `insufficient_scope` | — | — | — | **new** (SEP-835) | ✓ | ✓ scope precedence; step-up with retry limit |
| Client ID Metadata Documents | — | — | — | **new** (SEP-991) | ✓ | ✓ ours by default (GitHub Pages), or `--client-metadata-url` |
| Dynamic Client Registration (RFC 7591) | — | SHOULD | SHOULD | MAY | D; + `application_type` | ✓ fallback, with `application_type: native`; flagged deprecated |
| `iss` validation (RFC 9207) | — | — | — | — | **new** (SEP-2468) | ✓ refused on mismatch or when advertised but missing |
| Credentials keyed by issuer | — | — | — | — | **new** (SEP-2352) | ✓ re-registers when the issuer changes; `--token-cache` |
| Static bearer token / custom headers | — | — | — | — | — | ✓ `--bearer-env`, `--header`, `/header` |

## Extensions and discovery outside the dated core

| Item | Status | Notes / driver support |
|---|---|---|
| Extensions mechanism (SEP-2133, SEP-3392) | Final | `capabilities.extensions["vendor/name"]`; off by default.  Driver: `--extension`, `/extension`, listed in the report |
| Tasks extension `io.modelcontextprotocol/tasks` (SEP-2663) | Final | Replaces the 2025-11-25 core tasks.  Driver: `/task`, task handles followed, `tasks/update` with input |
| MCP Apps `io.modelcontextprotocol/ui` (SEP-1865) | Final | `text/html;profile=mcp-app` resources referenced from tool `_meta.ui.resourceUri`.  Driver: `/app` lists and saves (never renders) |
| Skills extension (SEP-2640) | Final | `skills/list`, `skills/get`, `skill://` resources with digests.  Driver: `/skills`, digest-checked reads |
| OAuth client credentials (SEP-1046, ext-auth) | Final | Driver: `--auth-flow client-credentials`.  Also in ext-auth (not implemented): enterprise-managed authorization (SEP-990), DPoP, WIF |
| Server Cards (SEP-2127) | Final | `<mcp-url>/server-card`; AI Catalog at `/.well-known/ai-catalog.json`. **No official `/.well-known/mcp`.**  Driver: `/server-card`, compared with the live server |

## Conformance suite

[`modelcontextprotocol/conformance`](https://github.com/modelcontextprotocol/conformance)
publishes a frozen list of required scenarios for each revision.  They are
vendored under [`specs/conformance/`](specs/conformance/).

- **Server scenarios** include:
  - `tools-*`, `resources-*` and `prompts-*`
  - `completion-complete`
  - `server-stateless`
  - `caching`
  - `sep-2164-resource-not-found`
  - `dns-rebinding-protection`
  - the `input-required-result-*` family (MRTR)
- **Client scenarios** include:
  - `request-metadata`
  - `http-standard-headers`
  - `http-custom-headers`
  - `sep-2322-client-request-state`
  - about 25 `auth/*` scenarios
- Scenarios that apply to both eras must be run once under each.

mcp-test-driver passes every client scenario in both requirement lists
(`scripts/conformance-client.sh`), and its own `--check` IDs reuse the
suite's scenario names where they test the same behaviour.
