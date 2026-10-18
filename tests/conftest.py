import asyncio
import pytest
import httpx
from app.config import Settings
from app.errors import ServiceError
from app.main import create_app
from app.model import ModelChunk


class FakeAuth:
    async def verify(self, token):
        if token == 'denied':
            raise ServiceError('forbidden')
        if token not in ('test-token', 'other-token'):
            raise ServiceError('unauthenticated')
        return token


class FakeModel:
    supports_images = False

    def __init__(self):
        self.calls = []
        self.closed = False
        self.fail = None
        self.delay = 0
        self.finish = 'stop'

    async def stream(self, messages, image):
        self.calls.append((messages, image))
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield ModelChunk(text='Hello 한글\n')
            if self.fail:
                raise self.fail
            yield ModelChunk(text='world')
            if self.finish:
                yield ModelChunk(finish_reason=self.finish)
        finally:
            self.closed = True


@pytest.fixture
def cfg():
    return Settings(_env_file=None, model_id='fake-model', ollama_api_key='fake-key',
                    firebase_project_id='fake-project', requests_per_minute=100)


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def api(cfg, model):
    return create_app(cfg, authenticator=FakeAuth(), model=model)


@pytest.fixture
def client(api):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test',
                             headers={'Authorization': 'Bearer test-token'})
