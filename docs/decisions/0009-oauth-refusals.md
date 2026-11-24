# 0009: OAuth deviations are findings; refuse only to protect credentials

Status: Accepted (2026-11-12)

## Context

Decision 0001 says the tool reports rather than blocks.  Authorization is
where that stops being safe: carrying on after some deviations sends a
token or an authorization code to the wrong party.

## Decision

Diagnose first: every departure from the authorization spec is a
finding, and the flow continues.  It stops in exactly these cases:

- protected-resource metadata whose `resource` is not this server
  (wrong audience, RFC 9728 §3.3);
- an authorization response whose `iss` differs from the issuer, or is
  missing when the AS advertises it (mix-up, RFC 9207);
- authorization-server metadata whose issuer does not match;
- a `state` mismatch.

## Consequences

- A new refusal must name the credential it protects; otherwise make it
  a finding.
- Missing `code_challenge_methods_supported`, deprecated DCR and the
  like are reported, and the user may proceed.
