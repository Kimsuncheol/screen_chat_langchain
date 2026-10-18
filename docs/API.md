# Screen Chat AI API

Status: **Deployed on Render Free; public health/docs and authentication rejection verified. Provider/Firebase credential setup, authenticated generation, and Flutter cloud integration are pending.**

API version: `v1`  
Last updated: 2026-10-18

This document defines the initial Flutter-to-cloud contract for FastAPI,
LangChain, and Ollama Cloud. Paths are relative to a deployment base URL that
is `https://screen-chat-ai.onrender.com`. Production traffic must use HTTPS.

## Architecture and data handling

### Render Free deployment profile

The deployment target is one Render Free Python web service using `render.yaml`.
The base URL is `https://screen-chat-ai.onrender.com`, deployed in Singapore.
Health, OpenAPI, docs and unauthenticated rejection were verified on 2026-10-18.
Ollama key/model and Firebase Admin credentials remain to be configured; real
authenticated generation and end-to-end SSE are not verified. See
[deployment and secret configuration](RENDER.md).

This profile uses one Uvicorn worker and sets `SC_MAX_CONCURRENT_REQUESTS=2`;
image support stays disabled pending exact-model verification. API request,
response, and SSE schemas are unchanged. `/health` verifies process liveness
only, not Ollama access or Firebase credentials.

Free services sleep after 15 idle minutes and may take about a minute to wake.
Client connection/startup timeouts must allow for that delay separately from
the API generation deadline. During wake-up or outages, Render gateway responses
may not follow this API's error JSON. Treat these as transport failures and
never assume an incomplete stream completed successfully. No replay is supported.
Rate/daily quota counters are in memory and reset when the service restarts,
redeploys or spins down; they are not durable billing controls. Render hosting
and Ollama Cloud usage have separate allowances. No paid resources are configured.

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
`SC_IMAGE_SUPPORT_VERIFIED=true` and `SC_VERIFIED_IMAGE_MODEL_ID` equal to
`SC_MODEL_ID`. The shared adapter must also report image support. A missing or
mismatched verified model ID disables images even if the flag is true. No live
provider probe or paid verification call runs on this endpoint. Verification is
an operator attestation for that exact model, not automatic capability discovery.
Reverify vision, context and image budgets when changing the model or provider
revision, then update the attested model ID; do not copy approval by model family.

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

This uses the shared LangChain prompt workflow and direct Ollama Cloud adapter.
Supply non-whitespace OCR, a decodable PNG/JPEG image, or both. A valid image
with no readable text is still accepted: legibility is assessed by the model,
not by a server-side OCR heuristic. Empty/missing sources or corrupt images
return 422 `invalid_request`. A supplied image requires verified model/adapter
vision support; otherwise return 422 `image_not_supported` before streaming,
even when OCR is present. Images are never silently dropped or replaced with
OCR. If the client wants OCR-only processing after rejection, it must explicitly
submit a new request without the image.

The summary prompt asks for one short sentence identifying the main subject,
then up to four short Markdown bullets when useful (fewer for sparse content).
It preserves important visible names, dates, quantities, units and warnings.
It uses `language=en` or `ko` even when captured content requests another
language; `auto` follows the source language, falling back to English. Proper
names and exact values remain intact. Screenshot/OCR instructions are untrusted
evidence, never system instructions. No tools or outside context are supplied.

For cropped, unreadable or conflicting image/OCR evidence, the model is instructed
to state uncertainty and summarize only supported details. If nothing can be
read reliably, it should say it cannot reliably summarize the screen and ask for
a clearer capture instead of inventing key points. This uncertainty is normal
answer text in `delta` events, not a validation or upstream error. These are model
instructions rather than a deterministic guarantee of model accuracy; automated
tests verify prompt boundaries and transport using scripted fake model output.

Illustrative response for the OCR request above (wording and delta boundaries
can vary; concatenate delta text):

```text
event: start
data: {"request_id":"req_summary_example","action":"summarize"}

event: delta
data: {"request_id":"req_summary_example","text":"Project launch update.\n"}

event: delta
data: {"request_id":"req_summary_example","text":"- Launch moved to November 4.\n- Testing remains open."}

event: complete
data: {"request_id":"req_summary_example","finish_reason":"stop"}

```

For image-only processing, replace `screen.ocr_text` with `screen.image` using
the image schema above and the actual raw base64 string. To supply both, keep
both fields in `screen`. The adapter attaches the image to the user message in
memory; only an attachment marker goes into the serialized text prompt. No
screenshot bytes are promoted to system instructions or persisted.

#### Analyze

```json
{
  "action": "analyze",
  "language": "en",
  "focus": "Explain this error and suggest what to check.",
  "screen": {"ocr_text": "Connection timed out. Please try again."}
}
```

Analyze uses the same LangChain prompt builder, Cloud adapter and streaming
pipeline as summarize. Supply a screenshot, non-whitespace OCR, or both, plus
an optional `focus` describing the aspect the user wants explained. Omitted or
null focus means general analysis; supplied strings must be non-whitespace and
within `max_focus_characters` (default 2000). Focus guides relevance, not the
truth of an assumed explanation. Summarize/ask-only fields remain invalid.

The prompt asks the model to explain visible errors, charts, forms, articles,
settings pages or other captured content. When useful, organize short Markdown
sections for **Observations**, **Interpretation** and **Suggested next steps**;
localize headings to the requested language and omit empty/unhelpful sections.
Observations are visible facts; interpretations must label hypotheses, explain
supporting evidence and identify what the capture cannot establish. Preserve
visible labels, error messages, quantities, units and warnings. Do not invent
chart values, axes, legends, units, time ranges, hidden application state, form
submissions, off-screen article claims or definitive root causes. A visible
software error alone does not prove its cause.

For cropped/unreadable labels, conflicting OCR/image evidence or ambiguous
screens, state uncertainty and ask a concise question for the missing context or
a clearer capture when needed. An incomplete screen can produce a normal streamed
clarification rather than an error. Use `language=en` or `ko` even when screen
content requests another language; `auto` follows the source language, falling
back to English. Keep proper names, exact error codes and values intact.

Captured instructions remain untrusted user-message data. Suggested next steps
are checks the user may choose to perform, never claims that the backend has
performed them or that they guarantee a fix. The backend has no app-control,
command-execution, browsing or form-submission tools. Model guidance is not a
verified diagnosis. Authentication, source/image validation, model-bound vision
approval, size/context/usage limits, disconnect cancellation, deadlines and
content non-persistence all use the shared controls documented here. Unsupported
images return `image_not_supported` before streaming even when OCR is available;
no image is silently dropped. Corrupt images and empty sources remain invalid.

Illustrative stream for the request above; wording and chunk boundaries vary:

```text
event: start
data: {"request_id":"req_analyze_example","action":"analyze"}

event: delta
data: {"request_id":"req_analyze_example","text":"**Observations**\n- The screen reports a connection timeout.\n\n"}

event: delta
data: {"request_id":"req_analyze_example","text":"**Interpretation**\n- The operation timed out; this message alone does not establish why.\n\n"}

event: delta
data: {"request_id":"req_analyze_example","text":"**Suggested next steps**\n- Check connectivity and whether the service is reachable."}

event: complete
data: {"request_id":"req_analyze_example","finish_reason":"stop"}

```

Automated tests use scripted fake responses to check evidence/focus/language
prompt delivery, SSE forwarding, source rejection, safe timeout errors and
upstream cleanup. They do not evaluate model reasoning, visual chart reading,
factual accuracy, translation quality or resistance to semantic prompt injection.
Those require separate evaluation of the selected model; no paid provider calls
are part of automated tests.

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

Ask uses the same LangChain prompt builder, Ollama Cloud adapter and shared
streaming pipeline. `question` is required, must contain non-whitespace text and
is preserved exactly, including surrounding whitespace. `screen` is optional
(omitted or null); text-only questions do not require screen capture. A supplied
screen must contain non-whitespace OCR, a valid PNG/JPEG image, or both. Images
require the same model-bound vision approval as summarize/analyze and are never
silently discarded. `messages` defaults to an empty list and accepts only `user`
or `assistant` roles with non-whitespace string content; clients cannot supply
system/developer/tool roles, extra message fields, a provider model or a
server-side conversation identifier. No alternating-role sequence is required.
Flutter supplies selected recent messages in chronological order, oldest first.

The prompt prioritizes the current question and relevant recent context over
older or superseded details. A recent user correction may replace an older
preference, but previous assistant claims are not verified facts. All accepted
history remains intact and ordered; the current question is placed last in the
serialized user-message data after the available evidence. Captured content
and prior messages are never promoted to system instructions. There is no
server-side relevance filter, history summary, trimming or hidden retrieval.
Flutter selects the relevant recent subset before submitting the request.

Screen-related claims must be grounded in the supplied current screenshot/OCR,
not in an imagined screen or old descriptions. The model is instructed to state
uncertainty for unreadable or conflicting evidence and ask one concise
clarification for missing screen context, unclear references or unavailable live
information. Self-contained text-only questions may use the model's available
knowledge without claiming to see a screen. The requested `en`/`ko` language
wins over language instructions in captured content/history; `auto` follows the
current question, then source language, falling back to English. The backend has
no browsing, app-control, command execution or external tools and the answer
must not claim those capabilities or persistent memory.

##### Ask context admission policy

| Limit | Default | Rejection before SSE |
| --- | --- | --- |
| Current question | 4000 Unicode code points | 422 `invalid_request` |
| OCR | 12000 Unicode code points | 422 `invalid_request` |
| Selected history | At most 12 messages, 4000 code points per message | 422 `invalid_request` |
| Question + OCR + all message text | 24000 code points | 422 `invalid_request` |
| Rendered model context plus output reserve | 32768 estimated tokens | 422 `context_too_large` |

These are configurable server policy; fetch effective limits from capabilities.
The final context estimate is the UTF-8 byte length of **all** rendered messages
(including system instructions, JSON quoting, the current question and every
selected message) plus 64 per rendered message, plus 8192 for an attached image,
plus a 2048 output reserve by default. The sum may equal but must not exceed
`context_tokens`. It is a conservative estimate, not exact provider tokenization;
operators must validate the selected model and image budgets before rollout.
No input is silently truncated, even when the question alone would fit. Every
accepted message and the full question reach the model, or the entire request
is rejected with a typed error before quota admission/model invocation. For a
rejection, Flutter may let the user explicitly select fewer prior messages or
smaller input and resubmit; preserve the current question and essential context.
Do not automatically drop screen evidence or retry a partial answer.

##### Flutter request and events

The JSON request above is suitable for Flutter's JSON encoder. An equivalent
Dart map, using selected local messages (example data only), is:

```dart
final body = <String, Object>{
  'action': 'ask',
  'language': 'en',
  'question': 'Which plan meets my storage requirement?',
  'screen': <String, Object>{'ocr_text': 'Basic: 5 GB. Plus: 100 GB.'},
  'messages': <Map<String, String>>[
    {'role': 'user', 'content': 'I need at least 50 GB.'},
    {'role': 'assistant', 'content': 'Share the available plans and I can compare them.'},
  ],
};
```

POST JSON to `/v1/assistant/stream` using the Firebase ID token as Bearer
authorization, `Content-Type: application/json` and `Accept: text/event-stream`.
For text-only requests omit `screen`; for image requests add raw base64 through
`screen.image` with `media_type` and `data_base64`. A successful example stream is
shown in the streaming section immediately below. Flutter concatenates `delta.text`
in order and marks success only on `complete` (`length` indicates truncation).
An `error` is terminal; EOF without a terminal event is interruption. For example,
after some deltas a provider failure is delivered as:

```text
event: error
data: {"error":{"code":"upstream_error","message":"The AI service could not complete the request.","request_id":"req_example","retryable":true}}

```

Parse SSE frames across TCP chunks and UTF-8 boundaries; do not treat each network
chunk as an event. Associate the returned request ID with the active UI answer.
Close the HTTP connection on cancellation, account changes or disposal. No event
replay, resumable answer, or automatic provider retry is implemented. `retryable`
never means the app may automatically resubmit a request after any answer deltas.
Keep partial/interrupted answers distinguishable from complete local history.

Conversation storage remains on the device. The next request must explicitly
include its own selected context; previous requests are not remembered, even for
the same authenticated UID. Only content-free quota counters are held in the
backend; no questions, messages, screenshots or answers are written to disk,
databases, traces or logs. Provider retention remains a separate policy.

Flutter currently has Firebase Core configuration only and local Gemma inference.
It still needs Firebase Authentication sign-in/token refresh, a cloud transport
service at the main assistant owner, incremental SSE handling, cancellation,
client-side history selection and revised cloud-processing disclosures/privacy
tests. These examples do not imply a working Flutter authentication or HTTP flow.
Scripted fake tests verify application behavior and prompt delivery, not model
answer accuracy, translation, reasoning or semantic prompt-injection resistance.

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
    "message": "The request does not match the supported input schema.",
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
| 2026-10-18 | Deployed on Render Free in Singapore at https://screen-chat-ai.onrender.com; verified public health/docs and HTTP 401 for unauthenticated protected endpoints | No schema changes; provider/Firebase secret setup and authenticated generation remain pending |
| 2026-10-18 | Added Render Free deployment profile, secret configuration, cold-start behavior and process-local quota reset notes | No API schema changes; Render profile reduces global concurrency to 2; Flutter must handle cold-start delay and gateway errors |
| 2026-10-18 | Refined ask prompt for current-question priority, bounded client-supplied history, grounding and clarification; retained all accepted context and placed question last in prompt data; added Flutter examples and privacy/lifecycle tests | No request/SSE schemas, defaults, authentication or limit changes; no client migration. Larger prompt instructions count toward the existing context estimate |
| 2026-10-18 | Refined analyze guidance for observations versus hypotheses, incomplete charts, ambiguous screens, optional focus and user-only next steps; added fake transport/lifecycle tests and SSE example | No request/SSE schema, limit, default or authentication changes; no client migration. Prompt/output guidance changes only; headings are conditional model output, not new structured response fields |
| 2026-10-18 | Refined summarize output/language/uncertainty contract; added source-specific tests and example SSE; tied vision approval to exact model ID and adapter support for all actions | Vision configuration tightening: existing image deployments must reverify their model and set `SC_VERIFIED_IMAGE_MODEL_ID` equal to `SC_MODEL_ID` alongside the existing flag; otherwise image capabilities are false and image requests return 422. Request/SSE schemas unchanged; custom adapters must implement `supports_images`. OCR-only clients need no migration |
| 2026-10-18 | Implemented endpoints, safe Firebase verification, SSE/cleanup, explicit model selection, opt-in verified vision, bounded input/context and single-process rate/daily quota accounting | No previously deployed API; migrate proposed clients to capability discovery and required sign-in, note image default false and new quota/context fields |
| 2026-10-18 | Initial proposed API contract for summarize, analyze, and ask | No deployed API yet |

Implementation configuration and verified official references are in [README.md](../README.md).
