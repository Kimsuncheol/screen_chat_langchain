# Screen Chat AI backend

Implemented FastAPI backend for the Android Flutter client at
`../screen_chat`. Summarize, analyze and ask share validation, Firebase identity,
limits, cancellation, and a direct Ollama Cloud adapter. This is implemented
source, not a deployed service. The Flutter client still runs local Gemma and
has Firebase Core only: cloud transport and sign-in are not integrated yet.

## Setup and run

Deployment target: **Render Free**. The root `render.yaml` and
[deployment guide](docs/RENDER.md) define the configuration and secret setup.
No live deployment has been verified. Free instances sleep when idle; quotas
reset with the process. Ollama Cloud usage has separate account limits/costs.

Python 3.11+ is required (verified here with Python 3.14). Use a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# Fill in server credentials, Firebase project and an exact direct-cloud model ID.
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

`requirements.in` records dependency intent; `requirements.txt` pins the tested
resolved environment, including test dependencies. After intentional dependency
updates, run the tests and regenerate pins using `.venv/bin/pip freeze`.
Configuration uses `SC_` variables and `.env`; invalid settings stop startup.
No model is selected implicitly and no local Ollama daemon is used. Missing
model/key makes capabilities report no actions and assistant requests return 503.
Health is process liveness, not an upstream readiness probe.

Set `SC_FIREBASE_PROJECT_ID` to the same project as the intended Flutter build.
Existing Flutter configuration currently names `screenask-e4147`. Configure
Firebase Admin Application Default Credentials on the server, preferably workload
identity; private credential files must remain outside this repository. Verification
checks project, signature, expiry and revocation/disabled account status. An empty
`SC_ALLOWED_UIDS` allows all valid project users; set an explicit UID list to
restrict access. A provider outage is never an authentication bypass.
`FIREBASE_AUTH_EMULATOR_HOST` is rejected to prevent accepting unsigned tokens.

Choose a model with [Ollama's direct Cloud docs](https://docs.ollama.com/cloud)
and `https://ollama.com/api/tags`; use the returned exact name, not a local CLI
`:cloud` alias. The official example currently uses `gemma4:31b`; availability
and account access must be checked at deployment. Keep images disabled until
that exact model's vision capability, context budget and image token reserve
have been verified. Then set both `SC_IMAGE_SUPPORT_VERIFIED=true` and
`SC_VERIFIED_IMAGE_MODEL_ID` to the exact `SC_MODEL_ID`. Model changes invalidate
that approval by an exact-name check. This is operator-attested capability
configuration, not a live provider metadata check. No live provider calls were
made to validate a model here.
The configured context budget is admission policy, not a claim about model size.
See [API contract](docs/API.md) for the conservative context estimate.

## Safe local checks (no sign-in, secrets, screenshots or paid calls)

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q app tests
.venv/bin/pip check
curl -s http://127.0.0.1:8000/health
```

Tests inject `FakeAuth` and `FakeModel` through `create_app`. Transport tests use
HTTPX MockTransport, generated tiny in-memory images and raw ASGI disconnects.
There is no environment flag that disables production authentication. Without
real Firebase sign-in, test the authenticated flow with these fakes rather than
inventing a token or calling the paid provider. `/docs` and `/openapi.json`
expose schemas without secrets. SSE event payload schemas appear in OpenAPI
components; frame order and cancellation are specified in `docs/API.md`.

## Authenticated API example

This example requires an actual Firebase ID token obtained after sign-in in a
compatible development client and a configured server. It can incur provider
usage. A Firebase custom token or service-account access token is not an ID token.

```sh
# Set FIREBASE_ID_TOKEN privately in this terminal; do not paste it into logs.
curl -s http://127.0.0.1:8000/v1/capabilities \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN"
curl -N http://127.0.0.1:8000/v1/assistant/stream \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  --data '{"action":"ask","language":"en","question":"What does this error mean?","screen":{"ocr_text":"Connection timed out."}}'
```

Production requires HTTPS. Cancel by closing the connection. Never retry an
answer automatically after receiving deltas. More examples: [docs/API.md](docs/API.md).

## Summarize

Send `action="summarize"` to the same authenticated streaming endpoint with
`screen.ocr_text`, `screen.image`, or both. The workflow requests a short main
subject sentence and up to four key-point bullets in the requested language,
preserving visible names, dates, quantities and warnings. It asks the model to
state uncertainty for unreadable/cropped/conflicting evidence and request a clearer
capture when nothing can be summarized reliably. Captured instructions stay
untrusted data. The server does not infer missing facts or run screen instructions.
See the [request and example stream](docs/API.md#summarize).

Images require approval for the exact configured model and an adapter reporting
vision support. They are rejected explicitly when unsupported, including when
OCR is available; there is no silent OCR fallback. A valid image with unreadable
content can produce a normal streamed uncertainty response. Generation quality
still depends on the configured model; fake tests do not evaluate live vision
or prove prompt-injection resistance.

Migration for previously enabled vision deployments: reverify the exact model
and set `SC_VERIFIED_IMAGE_MODEL_ID` to `SC_MODEL_ID` alongside the existing flag.
A flag alone no longer enables images. Custom `ModelAdapter` implementations
must provide `supports_images`; the shared Cloud adapter derives it from the
model-bound approval. Request and SSE payload schemas are unchanged.

Run focused fake-only checks with:

```sh
.venv/bin/python -m pytest -q tests/test_summarize.py
```

## Analyze

Send `action="analyze"` through the same streaming endpoint with a screenshot,
OCR text, or both and an optional `focus`. The prompt requests evidence-based
explanation of errors, charts, forms, articles and settings pages. When useful,
it groups visible facts, inferred interpretations and suggested user checks under
localized Markdown headings. It asks for missing context when a screen is
ambiguous and prohibits invented chart values, hidden state and root causes.
Guidance never executes commands, controls other apps or follows screen instructions.

Analyze reuses the shared authentication, vision validation, limits, cancellation,
timeouts and privacy controls. No request/SSE schema changes or client migration
are required. See the [analyze request and stream example](docs/API.md#analyze).

```sh
.venv/bin/python -m pytest -q tests/test_analyze.py
```

These tests use scripted responses and fake Cloud transport to check evidence,
focus and requested-language delivery, SSE, unsupported images, timeouts and
cancellation/cleanup. They are application contract tests, not evaluations of
model answer quality, chart-reading accuracy, translation or semantic injection
resistance. No real screenshots, production identity or paid calls are used.

## Ask

Use `action="ask"` with a non-whitespace `question`, optional screen OCR/image
and up to 12 selected recent `user`/`assistant` messages in chronological order.
Text-only questions are supported. The prompt prioritizes the current question
and relevant recent context, keeps history as untrusted data and asks for a
concise clarification when evidence is insufficient. It does not claim browsing,
app control, external tools or persistent conversation memory.

Every accepted message and the full question are preserved, with the current
question placed last in prompt data. Oversized fields return `invalid_request`;
exceeding the rendered model context budget returns `context_too_large` before
model invocation. The budget includes system instructions, serialized input,
chat framing, image reserve and output reserve. No silent context trimming or
provider retries occur. See [ask context budgets and Flutter examples](docs/API.md#ask).

Flutter owns conversation storage and selects the relevant recent subset before
each explicit request. Same-user requests share no implicit history. Flutter
still needs sign-in/token refresh, cloud SSE/cancellation integration, local
history selection and updated cloud disclosures; its current runtime is local.

```sh
.venv/bin/python -m pytest -q tests/test_ask.py
```

Fake-provider tests cover text-only and screen-grounded input, follow-ups,
roles/limits, exact context admission boundaries, prompt injection placement,
no retry after partial failures, disconnect cleanup, no implicit cross-request
history, guarded local persistence and sensitive-content log/tracing suppression.
These are application tests, not evaluations of live model answers or a guarantee
about retention in external provider/deployment tooling.

## Architecture and operations

- `app/workflows/`: explicit action dispatch and LangChain ChatPromptTemplate
  orchestration; selected history and captured content are serialized as untrusted
  user data, never interpolated into system instructions.
- `app/model.py`: shared HTTPX transport to `https://ollama.com/api/chat`, translating
  LangChain messages to Ollama JSON and NDJSON to typed chunks. This deliberately
  owns the response/client context managers for deterministic cleanup rather than
  relying on private ChatOllama client attributes. No tools, remote fetches or retries.
- `app/auth.py`, `app/limits.py`: injectable authentication and user-limit interfaces.
  Limits are atomic within one process. The default quota is admitted requests per
  UTC day, not token billing. Failures/cancellations still count to prevent abuse.
  Counters hold UIDs and counts only, and expire on subsequent admissions after a
  UTC day boundary. They reset on restart. Use one worker; replace `UserLimits`
  with a shared atomic implementation before horizontal scaling. Enforce durable
  billing quotas externally before production if needed.
- `app/main.py`: bounded body reads, content-free errors, SSE and disconnect handling.
  Body upload, authentication, total generation and upstream idle/connect waits
  have separate deadlines. Cleanup closes upstream connections and releases leases.
  Cancellation is best effort; provider processing/billing may continue.

The backend does not persist screenshots, OCR, questions, selected context or
answers. Images are validated and sent in memory. Conversation history stays on
the client. Content necessarily leaves the device for explicit cloud requests.
Provider processing/retention is governed separately by Ollama's policies; backend
non-persistence does not promise provider deletion.

LangSmith content tracing is disabled in configuration and around prompt execution;
no content callbacks are registered. Operational logs contain only server request
ID, status/outcome, duration and a validated action. No bodies, headers, UID,
queries, prompts or raw exceptions are logged. Run without access logging as above;
disable body/header capture, content tracing and error-local-variable capture in
proxies, APM and deployment tooling as well. Do not add logging middleware that
captures request or response content. No CORS is enabled; the current client is
native Android, not a web application.

Before rollout, supply credentials/model budgets, deploy behind HTTPS with proxy
SSE buffering disabled and an idle timeout above the generation deadline, configure
shared/durable quotas as required, complete Flutter sign-in/SSE/cancellation and
update its user-facing cloud-processing disclosure. Existing Flutter local-only
runtime policy and screens must be revised before enabling cloud calls; this
change updates its engineering guide and README, not runtime client behavior.

## Official references verified on 2026-10-18

- [Ollama Cloud authentication, endpoint and model naming](https://docs.ollama.com/cloud)
- [Ollama chat](https://docs.ollama.com/api/chat), [NDJSON streaming](https://docs.ollama.com/api/streaming),
  [vision inputs](https://docs.ollama.com/capabilities/vision)
- [LangChain Ollama integration](https://docs.langchain.com/oss/python/integrations/chat/ollama),
  [messages](https://docs.langchain.com/oss/python/langchain/messages),
  [tracing controls](https://docs.langchain.com/langsmith/trace-with-langchain)
- [FastAPI StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/)
- [Firebase Admin ID-token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens)
