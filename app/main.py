import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.openapi.utils import get_openapi
from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from app.auth import Authenticator, FirebaseAuthenticator
from app.config import Settings, disable_content_tracing
from app.errors import ErrorResponse, ServiceError
from app.limits import InMemoryLimits, UserLimits
from app.model import ModelAdapter, OllamaCloudAdapter, check_context
from app.schemas import (AssistantRequest, Capabilities, CompleteEvent, DeltaEvent,
                         Health, InputCapabilities, StartEvent)
from app.validation import validate_input
from app.workflows import WORKFLOWS

log = logging.getLogger('screen_chat.operations')


def error_response(exc: ServiceError, request_id: str):
    headers = {'X-Request-ID': request_id, 'Cache-Control': 'no-store'}
    if exc.retry_after is not None:
        headers['Retry-After'] = str(exc.retry_after)
    return JSONResponse(exc.response(request_id).model_dump(), exc.status, headers=headers)


class SafeBoundary:
    """ASGI boundary: IDs, bounded body reads, safe errors, content-free metadata."""
    def __init__(self, app, cfg: Settings):
        self.app, self.cfg = app, cfg

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request_id = 'req_' + uuid.uuid4().hex
        scope.setdefault('state', {})['request_id'] = request_id
        start, started, received = time.monotonic(), False, 0
        status, outcome = 500, 'internal_error'
        body_deadline = start + self.cfg.body_timeout_seconds

        async def bounded_receive():
            nonlocal received
            if scope.get('state', {}).get('body_read_complete'):
                return await receive()
            try:
                async with asyncio.timeout_at(body_deadline):
                    message = await receive()
            except TimeoutError:
                raise ServiceError('upstream_timeout') from None
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > self.cfg.max_request_bytes:
                    raise ServiceError('payload_too_large')
                if not message.get('more_body', False):
                    scope['state']['body_read_complete'] = True
            return message

        async def safe_send(message):
            nonlocal started, status, outcome
            if message['type'] == 'http.response.start':
                started = True
                status = message['status']
                outcome = 'http_' + str(status)
                headers = [(k, v) for k, v in message.get('headers', [])
                           if k.lower() not in (b'x-request-id', b'cache-control')]
                message['headers'] = headers + [(b'x-request-id', request_id.encode()),
                                                (b'cache-control', b'no-store')]
            await send(message)

        try:
            await self.app(scope, bounded_receive, safe_send)
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        except ClientDisconnect:
            outcome = 'disconnected'
        except Exception as exc:
            if started:
                # Never send a second HTTP response or expose exception text.
                outcome = 'stream_interrupted'
                return
            err = exc if isinstance(exc, ServiceError) else ServiceError('internal_error')
            outcome = err.code
            await error_response(err, request_id)(scope, receive, safe_send)
        finally:
            # No URL/query, UID, token, body, prompt, answer or exception text.
            log.info('request_id=%s status=%s outcome=%s duration_ms=%d action=%s',
                     request_id, status, scope['state'].get('outcome', outcome),
                     int((time.monotonic() - start) * 1000), scope['state'].get('action', '-'))


def frame(event: str, payload) -> bytes:
    return f'event: {event}\ndata: {payload.model_dump_json()}\n\n'.encode('utf-8')


def create_app(cfg: Settings | None = None, *, authenticator: Authenticator | None = None,
               model: ModelAdapter | None = None, limits: UserLimits | None = None) -> FastAPI:
    disable_content_tracing()
    cfg = cfg or Settings()
    # No HTTP debug logs; operational logger above is an explicit allowlist.
    for name in ('httpx', 'httpcore', 'firebase_admin', 'google.auth'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    api = FastAPI(title='Screen Chat AI', version='1.0.0', debug=False)
    api.add_middleware(SafeBoundary, cfg=cfg)
    authn = authenticator or FirebaseAuthenticator(cfg)
    adapter = model or OllamaCloudAdapter(cfg)
    limiter = limits or InMemoryLimits(cfg)
    api.state.limits = limiter

    @api.exception_handler(ServiceError)
    async def service_error(request, exc):
        return error_response(exc, request.state.request_id)

    @api.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error_response(ServiceError('invalid_request'), request.state.request_id)

    @api.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = {404: 'not_found', 405: 'method_not_allowed'}.get(exc.status_code, 'invalid_request')
        return error_response(ServiceError(code), request.state.request_id)

    bearer = HTTPBearer(auto_error=False, description='Firebase ID token from client sign-in')

    async def identity(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        token = credentials.credentials if credentials else ''
        if not token or len(token) > 16384:
            raise ServiceError('unauthenticated')
        try:
            async with asyncio.timeout(cfg.connect_timeout_seconds):
                return await authn.verify(token)
        except TimeoutError:
            raise ServiceError('service_unavailable') from None

    errors = {status: {'model': ErrorResponse} for status in
              (401, 403, 413, 415, 422, 429, 500, 502, 503, 504)}

    @api.get('/health', response_model=Health)
    async def health():
        return Health()

    @api.get('/v1/capabilities', response_model=Capabilities, responses=errors)
    async def capabilities(uid: str = Depends(identity)):
        names = ('max_request_bytes', 'max_image_bytes', 'max_image_pixels',
                 'max_question_characters', 'max_ocr_characters', 'max_focus_characters',
                 'max_messages', 'max_message_characters', 'max_total_text_characters',
                 'max_concurrent_requests_per_user', 'max_concurrent_requests',
                 'requests_per_minute', 'requests_per_day', 'request_timeout_seconds',
                 'body_timeout_seconds', 'context_tokens', 'max_output_tokens', 'image_token_reserve')
        ready = cfg.model_ready
        return Capabilities(actions=cfg.enabled_actions if ready else [],
                            inputs=InputCapabilities(text=ready and bool(cfg.enabled_actions),
                                                     image=ready and bool(cfg.enabled_actions)
                                                     and cfg.image_support_verified),
                            limits={name: getattr(cfg, name) for name in names})

    request_schema = AssistantRequest.model_json_schema(ref_template='#/components/schemas/{model}')
    request_definitions = request_schema.pop('$defs', {})

    @api.post('/v1/assistant/stream', response_class=StreamingResponse,
              responses={**errors, 200: {'content': {'text/event-stream': {'schema': {'type': 'string'}}},
                                        'description': 'SSE start/delta/complete/error; see docs/API.md'}},
              openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {
                  'schema': request_schema}}}})
    async def assistant(request: Request, uid: str = Depends(identity)):
        # Read incrementally under the ASGI byte/deadline guard, after authentication.
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise ServiceError('unsupported_media_type')
        try:
            body = await request.body()
            req = AssistantRequest.model_validate_json(body)
        except ValidationError as exc:
            unsupported_image = any(e['loc'] == ('screen', 'image', 'media_type')
                                    and e['type'] == 'literal_error' for e in exc.errors())
            raise ServiceError('unsupported_media_type' if unsupported_image else 'invalid_request') from None
        except ValueError:
            raise ServiceError('invalid_request') from None
        request.state.action = req.action
        if not cfg.model_ready or req.action not in cfg.enabled_actions:
            raise ServiceError('service_unavailable')
        validate_input(req, cfg)
        messages = WORKFLOWS[req.action].messages(req)
        image = req.screen.image.data_base64 if req.screen and req.screen.image else None
        check_context(messages, image, cfg)
        # Reserve before headers for JSON 429 responses; the response-level finally
        # also releases if cancellation happens before the generator first runs.
        lease = await limiter.acquire(uid)
        request_id = request.state.request_id

        async def events() -> AsyncIterator[bytes]:
            upstream = None
            try:
                yield frame('start', StartEvent(request_id=request_id, action=req.action))
                async with asyncio.timeout(cfg.request_timeout_seconds):
                    upstream = adapter.stream(messages, image)
                    async for chunk in upstream:
                        if chunk.text:
                            yield frame('delta', DeltaEvent(request_id=request_id, text=chunk.text))
                        if chunk.finish_reason:
                            request.state.outcome = 'complete_' + chunk.finish_reason
                            yield frame('complete', CompleteEvent(request_id=request_id,
                                                                   finish_reason=chunk.finish_reason))
                            return
                    raise ServiceError('upstream_error')
            except asyncio.CancelledError:
                request.state.outcome = 'cancelled'
                raise
            except Exception as exc:
                err = (ServiceError('upstream_timeout') if isinstance(exc, TimeoutError)
                       else exc if isinstance(exc, ServiceError) else ServiceError('internal_error'))
                request.state.outcome = err.code
                yield frame('error', err.response(request_id))
            finally:
                # Shield cleanup from Starlette's disconnect cancel scope.
                import anyio
                with anyio.CancelScope(shield=True):
                    try:
                        if upstream is not None:
                            with contextlib.suppress(Exception):
                                await upstream.aclose()
                    finally:
                        await lease.release()

        # Force the ASGI 2.0 disconnect listener on all servers. StreamingResponse
        # cancels the producer even while it is blocked waiting on upstream data.
        class DisconnectResponse(StreamingResponse):
            async def __call__(self, scope, receive, send):
                scope = dict(scope, asgi={**scope.get('asgi', {}), 'spec_version': '2.0'})
                try:
                    await super().__call__(scope, receive, send)
                finally:
                    import anyio
                    with anyio.CancelScope(shield=True):
                        await self.body_iterator.aclose()
                        await lease.release()

        return DisconnectResponse(events(), media_type='text/event-stream',
                                  headers={'X-Accel-Buffering': 'no'})

    def openapi():
        if api.openapi_schema is None:
            schema = get_openapi(title=api.title, version=api.version, routes=api.routes)
            components = schema.setdefault('components', {}).setdefault('schemas', {})
            components.update(request_definitions)
            for event_model in (StartEvent, DeltaEvent, CompleteEvent):
                components[event_model.__name__] = event_model.model_json_schema()
            api.openapi_schema = schema
        return api.openapi_schema

    api.openapi = openapi
    return api


app = create_app()
