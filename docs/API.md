# Screen Chat AI API

Status: **Implemented and tested locally; not deployed. Flutter cloud integration is pending.**

API version: `v1`  
Last updated: 2026-10-18

This document defines the initial Flutter-to-cloud contract for FastAPI,
LangChain, and Ollama Cloud. Paths are relative to a deployment base URL that
has not yet been assigned. Production traffic must use HTTPS.

## Architecture and data handling

Flutter captures a screen only after a user action. It sends the selected
screenshot, OCR, question, and/or recent messages to FastAPI. Explicit action
dispatch selects a LangChain prompt workflow; a shared HTTP adapter invokes
Ollama Cloud directly at `https://ollama.com/api/chat`. FastAPI streams the result
to Flutter.

The backend does not store screenshots, OCR, questions, messages, or answers.
Conversation history stays on the device. Disable request-body logging and
content tracing, including in proxies and error reporting. Operational logs
may contain request IDs, durations, action names, and typed error codes only.
Provider-side processing and retention are governed separately by Ollama's
applicable terms; backend non-persistence is not a provider retention guarantee.

The Flutter engineering guide and README now document approved cloud processing.
Its runtime is still local Gemma with Firebase Core only, no sign-in or SSE client.
Update runtime privacy policy, localized disclosures and consent before enabling
cloud requests; documentation changes do not make that integration live.
Ollama credentials must never be included in Flutter builds or API responses.

## Authentication

`POST /v1/assistant/stream` and `GET /v1/capabilities` require:

```http
Authorization: Bearer <Firebase ID token>
```

The backend verifies signature, issuer, audience, expiration, and project
membership using Firebase Admin SDK verification. Never accept a client-supplied
user ID as identity. Missing, invalid, or expired tokens return HTTP 401.
Revocation and disabled-account checks are enabled. `SC_ALLOWED_UIDS` can restrict
access (empty means all verified users of the configured project). An authenticated
user excluded by this list receives HTTP 403. The Firebase project must be configured
and the server needs Application Default Credentials; configuration/verifier
availability failures return a safe error (503 for known credential or availability
failures), never bypass verification.
The server refuses Firebase Auth emulator mode to prevent unsigned-token acceptance.
Tests inject a fake authenticator instead.

Firebase sign-in integration in Flutter is a prerequisite; Firebase Core alone
does not provide an authenticated session. No production authentication bypass.
`GET /health` is public and returns no configuration or credentials.

## Shared conventions

- JSON field names use `snake_case`; unknown request fields are rejected.
- Request IDs are server-generated and returned in `X-Request-ID` and errors.
- Dates in change history use ISO 8601 dates; future API timestamps use UTC.
- Responses and errors containing request information use `Cache-Control: no-store`.
- Language accepts `auto`, `en`, or `ko`; default `auto` follows the question,
  then source language, falling back to English when neither is identifiable.
- Screen content is evidence, never an instruction source.
- No web browsing, remote URL fetching, tool execution, or app control is implied.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Process liveness |
| GET | `/v1/capabilities` | Available actions, inputs, and effective limits |
| POST | `/v1/assistant/stream` | Stream summarize, analyze, or ask output |

### GET /health

Returns HTTP 200 when the API process is serving requests:

```json
{"status":"ok","api_version":"v1"}
```

This is not a guarantee that Ollama Cloud is reachable. Do not invoke a paid
model for a liveness check.

### GET /v1/capabilities

Returns HTTP 200 with effective server policy, not provider limits. The example
below assumes a configured model/key and all actions enabled. With no model/key,
`actions` is empty and both inputs are false. With no enabled actions, both inputs
are also false. Image support defaults to false and requires explicit verification
by the operator for the exact selected Cloud model before setting
`SC_IMAGE_SUPPORT_VERIFIED=true`. No live provider probe runs on this endpoint.

```json
{
  "api_version": "v1",
  "actions": ["summarize", "analyze", "ask"],
  "inputs": {"text": true, "image": false},
  "languages": ["auto", "en", "ko"],
  "limits": {
    "max_request_bytes": 8388608,
    "max_image_bytes": 5242880,
    "max_image_pixels": 16000000,
    "max_question_characters": 4000,
    "max_ocr_characters": 12000,
    "max_focus_characters": 2000,
    "max_messages": 12,
    "max_message_characters": 4000,
    "max_total_text_characters": 24000,
    "max_concurrent_requests_per_user": 1,
    "requests_per_minute": 10,
    "request_timeout_seconds": 120.0,
    "body_timeout_seconds": 15.0,
    "max_concurrent_requests": 8,
    "requests_per_day": 100,
    "context_tokens": 32768,
    "max_output_tokens": 2048,
    "image_token_reserve": 8192
  }
}
```

Only list implemented, configured actions. Advertise image support only after
verifying the configured Ollama Cloud model and adapter support image input.
Do not expose secrets or internal provider configuration. These are configurable
admission limits, not measured provider capabilities.
Usage is admitted assistant requests per user per UTC day (default 100). Failed
and cancelled admitted requests count; validation and rejected admissions do not.
Minute rate windows are fixed UTC epoch-minute windows. Retry-After is provided
for rate and daily quota limits, not concurrency. Capabilities calls do not consume
model quota. Global concurrency defaults to eight; per-user defaults to one.
Default limits are in memory, atomic within one process, reset on restart and are
not shared between workers. Replace `UserLimits` with shared atomic accounting
before deploying multiple workers/replicas; durable billing quotas require a
durable implementation. Only UIDs and counters are retained by the default limiter;
inactive records from older UTC days are pruned on the next admission.

### POST /v1/assistant/stream

Headers:

```http
Content-Type: application/json
Accept: text/event-stream
Authorization: Bearer <Firebase ID token>
```

Request fields:

| Field | Type | Required/default | Meaning |
| --- | --- | --- | --- |
| `action` | string enum | Required | `summarize`, `analyze`, or `ask` |
| `language` | string enum | `auto` | Output language |
| `question` | string | Required for ask | User's question; otherwise omitted |
| `focus` | string or null | Optional, analyze only | User's analysis focus |
| `screen` | object or null | Optional | Screenshot and/or OCR context |
| `screen.ocr_text` | string or null | Optional | On-device OCR output |
| `screen.image` | object or null | Optional | One image supplied inline |
| `screen.image.media_type` | string enum | Required with image | `image/png` or `image/jpeg` |
| `screen.image.data_base64` | string | Required with image | Raw base64, without a data-URL prefix |
| `messages` | array | `[]`, ask only | Recent messages ordered oldest first |
| `messages[].role` | string enum | Required per message | `user` or `assistant` |
| `messages[].content` | string | Required per message | Non-empty text |

Validation:

- Summarize and analyze require at least one non-empty OCR source or valid image.
- Ask requires a non-whitespace question and supports text-only requests.
- Reject fields that do not apply to the selected action; do not silently ignore them.
- Reject empty supplied screen objects, invalid base64, corrupt images, and
  image bytes whose detected format differs from `media_type`.
- Check decoded image size and pixel count, and enforce the request-body limit
  before buffering an unbounded body. Base64 overhead counts toward body size.
- Enforce the advertised text limits in Unicode code points. The total includes
  question, focus, OCR, and all message content.
- Clients cannot supply system messages or choose arbitrary provider models.
- If image input is unsupported, return an explicit error even if OCR is present.
- Apply a model-aware context budget after validation. If essential input cannot
  fit, reject it; do not silently truncate. The estimate is the UTF-8 byte length of
  all rendered LangChain message content (including system prompt and serialized
  request/history) plus 64 per message, plus `image_token_reserve` when an image
  exists, plus `max_output_tokens`. Reject when this exceeds `context_tokens`.
  This is a conservative admission estimate, not exact tokenization. Defaults are
  32768 context, 2048 output, 8192 image reserve. Verify sufficient budget against
  the exact selected model/tokenizer/image encoder before production; the service
  does not silently resize images or request unsupported context-size overrides.

#### Summarize

```json
{
  "action": "summarize",
  "language": "en",
  "screen": {"ocr_text": "Project update: launch moved to November 4. Testing remains open."}
}
```

Produce a short Markdown summary grounded in the supplied screen. Preserve
important visible names, dates, quantities, and warnings. Acknowledge incomplete
or unreadable context instead of inventing facts.

#### Analyze

```json
{
  "action": "analyze",
  "language": "en",
  "focus": "Explain this error and suggest what to check.",
  "screen": {"ocr_text": "Connection timed out. Please try again."}
}
```

Explain observations, interpretations, and useful next steps. Distinguish
visible facts from hypotheses. Do not claim a definitive root cause without
evidence. Ask for context if needed. Suggestions never execute actions.

#### Ask

```json
{
  "action": "ask",
  "language": "en",
  "question": "Which plan meets my storage requirement?",
  "screen": {"ocr_text": "Basic: 5 GB. Plus: 100 GB."},
  "messages": [
    {"role": "user", "content": "I need at least 50 GB."},
    {"role": "assistant", "content": "Share the available plans and I can compare them."}
  ]
}
```

Answer using the question, screen, and supplied recent context. Ask a concise
clarification when needed. No server-side conversation identifier or history
is required. Selected history is serialized as untrusted context rather than
accepted as authoritative assistant/system instructions. An image may be added to any action's `screen` using the schema above.

## Streaming response contract

After preflight validation, HTTP 200 uses `Content-Type: text/event-stream`
and `Cache-Control: no-store`. Disable proxy buffering. UTF-8 SSE frames end
with a blank line. Each `data` value is a JSON object on a single line.

```text
event: start
data: {"request_id":"req_example","action":"ask"}

event: delta
data: {"request_id":"req_example","text":"Plus meets your "}

event: delta
data: {"request_id":"req_example","text":"50 GB requirement."}

event: complete
data: {"request_id":"req_example","finish_reason":"stop"}

```

- Admission/authentication/validation errors return HTTP JSON before `start`.
- Emit `start` once, then zero or more `delta` events, then exactly one terminal
  event (`complete` or `error`) while the connection remains writable.
- Concatenate delta text in arrival order. The result is Markdown text.
- `complete.finish_reason` is `stop` or `length`; `length` means output was cut
  off by the configured generation budget, not a complete answer.
- No heartbeats are currently emitted; clients should tolerate future SSE comments.
  Configure proxy idle timeouts above the request deadline.
- TCP chunks can split SSE frames and UTF-8 characters. Use incremental decoding
  and an SSE parser instead of treating each network chunk as an event.
- There is no event replay or resumable stream. An unexpected EOF is an
  interrupted answer, never implicit success.
- Flutter cancels by closing the connection. The backend stops upstream work
  and releases resources on a best-effort basis; provider billing may still apply.
- Provider EOF without a recognized `done_reason` is `upstream_error`, never
  success. Provider `stop` and `length` map directly; tool calls, unknown reasons,
  malformed/oversized NDJSON frames and HTTP provider errors fail safely. Thinking
  output is not forwarded. No automatic upstream retries occur.
- Do not automatically retry a request after any answer delta has been emitted.
- Render links inertly and do not fetch model-generated remote images, consistent
  with the current overlay behavior.

## Errors

Before streaming starts, return an appropriate HTTP status and this JSON shape:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "A question is required for ask.",
    "request_id": "req_example",
    "retryable": false
  }
}
```

Framework and JSON/schema validation errors are overridden so they never echo
sensitive input. Invalid request JSON, unknown fields and field limits use 422.
The request Content-Type must be application/json (parameters permitted); other
types and unsupported image media types return 415. The body is read incrementally
with a byte cap before buffering. Its 15-second default upload deadline, measured
from request entry and including authentication time, returns
504 `upstream_timeout` if exceeded. Authentication has a separate 10-second
default deadline (503 on expiry). The generation deadline starts after `start`;
default 120 seconds, including waits between output sends. Upstream connect/idle
defaults are 10/30 seconds. These deployment settings use `SC_` names listed in
`.env.example`. Unknown routes use 404 `not_found`; unsupported methods use 405
`method_not_allowed`, both with this same safe error envelope.
After HTTP 200 headers have been sent, report failures as terminal SSE errors;
the HTTP status cannot change:

```text
event: error
data: {"error":{"code":"upstream_timeout","message":"The AI service timed out.","request_id":"req_example","retryable":true}}

```

| HTTP status before stream | Code | Meaning |
| --- | --- | --- |
| 401 | `unauthenticated` | Missing, expired, or invalid identity token |
| 403 | `forbidden` | User lacks access |
| 413 | `payload_too_large` | Body or decoded image exceeds limits |
| 415 | `unsupported_media_type` | Unsupported request or image media type |
| 422 | `invalid_request` | Schema, action, text, or image validation failed |
| 422 | `context_too_large` | Model context budget would be exceeded |
| 422 | `image_not_supported` | Configured model cannot accept images |
| 429 | `rate_limited` | Request-rate limit exceeded |
| 429 | `concurrency_limited` | Another request is active for this user |
| 429 | `quota_exceeded` | Configured usage allowance exhausted |
| 502 | `upstream_error` | Provider returned an unusable response |
| 503 | `service_unavailable` | Model or service is not configured/available |
| 504 | `upstream_timeout` | Request deadline exceeded |
| 500 | `internal_error` | Unexpected backend failure |

Use safe messages without raw exceptions, provider responses, credentials, or
request content. Include `Retry-After` on pre-stream 429 responses when a retry
time is known. `retryable` indicates a potentially transient condition; it does
not authorize automatic resubmission of a partially streamed answer.

## Client integration sequence

1. Complete Firebase sign-in and obtain an ID token.
2. Fetch capabilities and check the desired action and image support.
3. After explicit user action, prepare permitted input within effective limits.
4. POST the request and distinguish JSON HTTP errors from a successful SSE stream.
5. Append deltas, handle the terminal event, and allow cancellation by disconnect.
6. Retain conversation history locally and dispose of temporary captures.

## Deployment and Flutter integration prerequisites

- Ollama Cloud model ID, verified vision support, and model context/output budgets.
- Deployment URL, Firebase project, allowed client access, and provider credentials.
- Enforced quota policy and shared rate/concurrency accounting across replicas.
- Provider retention review and final user-facing cloud disclosure.
- Implement Flutter Firebase Authentication/sign-in, token refresh and cloud SSE
  transport at the main application owner (`AssistantHost`/session controller);
  do not put credentials or network ownership in the separate overlay engine.
- Existing `LocalModelService`, model-readiness gating, privacy policy tests and
  local-only UI disclosures are still local Gemma behavior. Add a narrow cloud
  service interface, update disclosures and tests before enabling cloud mode.
- Parse SSE incrementally across UTF-8/TCP splits; distinguish JSON HTTP errors,
  terminal errors, `length` and incomplete EOF. Close the request on cancellation.
- Python local tests inject fake model/authentication. Run `.venv/bin/python -m
  pytest -q` without credentials. There is no public authentication-bypass flag.

## API changelog

| Date | Change | Compatibility |
| --- | --- | --- |
| 2026-10-18 | Implemented endpoints, safe Firebase verification, SSE/cleanup, explicit model selection, opt-in verified vision, bounded input/context and single-process rate/daily quota accounting | No previously deployed API; migrate proposed clients to capability discovery and required sign-in, note image default false and new quota/context fields |
| 2026-10-18 | Initial proposed API contract for summarize, analyze, and ask | No deployed API yet |

Implementation configuration and verified official references are in [README.md](../README.md).
