import asyncio
import json
import httpx
import pytest
from langchain_core.messages import HumanMessage
from app.model import OllamaCloudAdapter
from app.errors import ServiceError


class Wire(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data, self.closed = data, False

    async def __aiter__(self):
        for block in self.data:
            yield block

    async def aclose(self):
        self.closed = True


async def test_cloud_request_and_cleanup(cfg):
    wire = Wire([b'{"message":{"content":"hello"},"done":false}\n',
                 b'{"done":true,"done_reason":"stop"}\n'])
    def handler(request):
        assert str(request.url) == 'https://ollama.com/api/chat'
        assert request.headers['authorization'] == 'Bearer fake-key'
        body = json.loads(request.content)
        assert body['model'] == 'fake-model' and body['stream'] is True
        assert body['messages'][-1]['images'] == ['base64-in-memory']
        assert body['options']['num_predict'] == cfg.max_output_tokens
        assert 'tools' not in body
        return httpx.Response(200, stream=wire)
    adapter = OllamaCloudAdapter(cfg, httpx.MockTransport(handler))
    chunks = [x async for x in adapter.stream([HumanMessage('question')], 'base64-in-memory')]
    assert chunks[0].text == 'hello' and chunks[-1].finish_reason == 'stop'
    assert wire.closed


@pytest.mark.parametrize('data', [b'bad\n', b'{"error":"SECRET"}\n', b'{"done":false}\n',
    b'{"done":true,"done_reason":"unknown"}\n', b'x' * 270000,
    b'{"message":{"tool_calls":[{}]},"done":true,"done_reason":"stop"}\n'])
async def test_bad_provider_stream_closes(cfg, data):
    wire = Wire([data])
    adapter = OllamaCloudAdapter(cfg, httpx.MockTransport(lambda r: httpx.Response(200, stream=wire)))
    with pytest.raises(ServiceError, match='upstream_error'):
        _ = [x async for x in adapter.stream([HumanMessage('q')], None)]
    assert wire.closed


@pytest.mark.parametrize('failure,code', [(401, 'upstream_error'), (429, 'upstream_error'),
                                        (httpx.ReadTimeout('SECRET'), 'upstream_timeout')])
async def test_provider_status_and_timeout(cfg, failure, code):
    def handler(request):
        if isinstance(failure, Exception):
            raise failure
        return httpx.Response(failure, text='SECRET')
    adapter = OllamaCloudAdapter(cfg, httpx.MockTransport(handler))
    with pytest.raises(ServiceError, match=code):
        _ = [x async for x in adapter.stream([HumanMessage('q')], None)]


async def test_real_asgi_disconnect_cancels_idle_upstream(api, model):
    model.delay = 60
    incoming = asyncio.Queue()
    await incoming.put({'type': 'http.request', 'body': json.dumps({'action': 'ask', 'question': 'q'}).encode(),
                        'more_body': False})
    started = asyncio.Event()
    sent = []
    async def send(message):
        sent.append(message)
        if message['type'] == 'http.response.body' and b'event: start' in message.get('body', b''):
            started.set()
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'method': 'POST',
             'path': '/v1/assistant/stream', 'raw_path': b'/v1/assistant/stream', 'query_string': b'',
             'scheme': 'http', 'http_version': '1.1', 'server': ('t', 80), 'client': ('t', 1),
             'headers': [(b'content-type', b'application/json'), (b'authorization', b'Bearer test-token')]}
    task = asyncio.create_task(api(scope, incoming.get, send))
    await asyncio.wait_for(started.wait(), 1)
    # Let the producer enter the fake model's long wait.
    while not model.calls:
        await asyncio.sleep(0)
    await incoming.put({'type': 'http.disconnect'})
    await asyncio.wait_for(task, 1)
    assert model.closed and api.state.limits.active == 0
    output = b''.join(m.get('body', b'') for m in sent)
    assert b'event: complete' not in output


async def test_small_provider_delta_is_not_buffered(cfg):
    reached = asyncio.Event()
    closed = asyncio.Event()
    class SlowWire(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"message":{"content":"small"},"done":false}\n'
            reached.set()
            await asyncio.sleep(60)
        async def aclose(self):
            closed.set()
    adapter = OllamaCloudAdapter(cfg, httpx.MockTransport(lambda r: httpx.Response(200, stream=SlowWire())))
    upstream = adapter.stream([HumanMessage('q')], None)
    chunk = await asyncio.wait_for(anext(upstream), .5)
    assert chunk.text == 'small'
    assert not reached.is_set()
    await upstream.aclose()
    assert closed.is_set()


async def test_response_start_failure_does_not_leak_lease(api):
    incoming = asyncio.Queue()
    await incoming.put({'type': 'http.request', 'body': b'{"action":"ask","question":"q"}', 'more_body': False})
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'method': 'POST',
             'path': '/v1/assistant/stream', 'raw_path': b'/v1/assistant/stream', 'query_string': b'',
             'scheme': 'http', 'http_version': '1.1', 'server': ('t', 80), 'client': ('t', 1),
             'headers': [(b'content-type', b'application/json'), (b'authorization', b'Bearer test-token')]}
    async def failed_send(message):
        raise OSError('socket closed')
    await asyncio.wait_for(api(scope, incoming.get, failed_send), 1)
    assert api.state.limits.active == 0
