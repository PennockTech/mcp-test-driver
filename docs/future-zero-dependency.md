# Future: Dropping urllib3 for Zero Dependencies

Analysis of what urllib3 provides over Python 3.12+ stdlib, and what it
would take to remove our only runtime dependency.

## What We Use from urllib3

Our entire urllib3 usage is in `HttpTransport` (`src/mcp_test_driver/transport.py`):

1. **`PoolManager`** — connection pooling and request dispatch
2. **`Retry(total=3, redirect=0)`** — automatic retries + redirect blocking
3. **`Timeout(connect=10, read=60)`** — split connect/read timeouts
4. **`preload_content=False`** — streaming response for SSE parsing
5. **`cert_reqs="CERT_NONE"`** — optional TLS bypass for testing
6. **Exception hierarchy** — `MaxRetryError`, `SSLError`, `HTTPError`
7. **`resp.release_conn()`** — return connection to pool

## Feature-by-Feature Comparison

### Connection pooling

- **urllib3**: `PoolManager` handles this transparently.
- **stdlib**: `http.client.HTTPSConnection` is a single persistent connection.
  No pool manager, but we talk to exactly one server, so a single reusable
  connection is sufficient.
- **Verdict**: Stdlib is adequate.

### Automatic retries

- **urllib3**: `Retry(total=3)` transparently retries on connection failures.
- **stdlib**: No built-in retry.  A simple loop suffices:

```python
for attempt in range(max_retries):
    try:
        conn.request(...)
        return conn.getresponse()
    except (ConnectionError, OSError):
        if attempt == max_retries - 1:
            raise
        conn = _new_connection()
```

- **Verdict**: ~8 lines to replicate.

### Redirect blocking

- **urllib3**: `Retry(redirect=0)` plus `redirect=False` per-request.
- **stdlib**: `http.client` does **not** follow redirects — it returns the
  3xx response directly.  SSRF protection is inherent.
- **Verdict**: Stdlib is *better* here.  No configuration needed.

### Split connect/read timeouts

- **urllib3**: `Timeout(connect=10, read=60)` — first-class support.
- **stdlib**: `HTTPSConnection(host, timeout=10)` sets a single socket
  timeout.  Split timeouts require manual socket manipulation:

```python
conn = HTTPSConnection(host, port, timeout=10)  # connect timeout
conn.connect()
conn.sock.settimeout(60)  # read timeout
```

- **Verdict**: This is urllib3's strongest value-add.  The stdlib approach
  is fragile and non-obvious.  The `sock.settimeout()` call must happen
  after `connect()` but before any request, and reconnection logic must
  re-apply it.

### Streaming response (SSE)

- **urllib3**: `preload_content=False` returns an iterable response.
- **stdlib**: `http.client.HTTPResponse` is a streaming `io.BufferedIOBase`.
  Iterate with `for line in response:` or `response.readline()`.
- **Verdict**: Equivalent.  Stdlib is arguably cleaner since the response
  is a standard file-like object.

### TLS configuration

- **urllib3**: `cert_reqs="CERT_NONE"` to disable verification.
- **stdlib**: `ssl.create_default_context()` for verification.  Custom
  `SSLContext` with `check_hostname=False` and `verify_mode=ssl.CERT_NONE`
  for bypass.
- **Verdict**: Equivalent.  Stdlib is slightly more verbose.

### Exception hierarchy

- **urllib3**: `MaxRetryError` wrapping `SSLError`, `HTTPError`, etc.
- **stdlib**: `ssl.SSLError` (subclass of `OSError`), `ConnectionError`,
  `http.client.HTTPException`, `socket.timeout`.  Same cases
  distinguishable, but flatter and spread across modules.
- **Verdict**: Adequate but less ergonomic.

## Summary Table

| Feature            | urllib3                | stdlib                    | Effort to replace     |
| ------------------ | ---------------------- | ------------------------- | --------------------- |
| Connection pooling | PoolManager            | Single HTTPSConnection    | Trivial (one host)    |
| Retries            | Retry(total=3)         | Manual loop               | ~8 lines              |
| Redirect blocking  | Retry(redirect=0)      | Inherent in http.client   | Free (simpler)        |
| Split timeouts     | Timeout(connect, read) | Manual sock.settimeout    | ~5 lines, fragile     |
| Streaming          | preload_content=False  | Native file-like response | Equivalent            |
| TLS config         | cert_reqs param        | SSLContext                | ~5 lines more verbose |
| Exceptions         | MaxRetryError etc.     | OSError subclasses        | Adequate but flatter  |

## Migration Approach

If zero-dependency becomes a goal:

1. Replace `PoolManager` with a single `http.client.HTTPSConnection`
   (or `HTTPConnection` for plain HTTP).
2. Add a retry wrapper (~8 lines).
3. Use `ssl.create_default_context()` with an optional unverified context.
4. Post-connect `conn.sock.settimeout(read_timeout)` for read timeout.
   Document the fragility and ensure reconnection logic re-applies it.
5. Replace urllib3 exception types with stdlib equivalents in catch blocks:
   `ssl.SSLError`, `ConnectionError`, `http.client.HTTPException`.
6. SSE parsing: iterate `response.readline()` instead of urllib3's response
   iterator.  Apply the same `MAX_RESPONSE_BYTES` byte counter.

Estimated effort: ~80 lines of new code replacing ~40 lines of urllib3
calls.  The timeout handling is the only part that requires care.

## Recommendation

Not worth doing unless zero-dependency is an explicit goal.  urllib3 is
a well-maintained, widely-used library.  The split timeout support alone
justifies the dependency for production use.
