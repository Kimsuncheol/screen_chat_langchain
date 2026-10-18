import asyncio
import pytest
from app.auth import FirebaseAuthenticator
from app.config import Settings
from app.errors import ServiceError
from app.limits import InMemoryLimits


async def test_atomic_concurrency_and_idempotent_release(cfg):
    store = InMemoryLimits(cfg)
    async def attempt():
        try:
            return await store.acquire('u')
        except ServiceError as exc:
            return exc.code
    results = await asyncio.gather(*[attempt() for _ in range(8)])
    leases = [x for x in results if x != 'concurrency_limited']
    assert len(leases) == 1
    await leases[0].release()
    await leases[0].release()
    assert store.active == 0


async def test_rate_quota_reset_and_global(cfg):
    now = [100.0]
    cfg.requests_per_minute = 1
    cfg.requests_per_day = 2
    cfg.max_concurrent_requests = 1
    store = InMemoryLimits(cfg, clock=lambda: now[0])
    lease = await store.acquire('u')
    with pytest.raises(ServiceError, match='concurrency_limited'):
        await store.acquire('other')
    await lease.release()
    with pytest.raises(ServiceError) as err:
        await store.acquire('u')
    assert err.value.code == 'rate_limited' and err.value.retry_after == 20
    now[0] += 60
    await (await store.acquire('u')).release()
    now[0] += 60
    with pytest.raises(ServiceError, match='quota_exceeded'):
        await store.acquire('u')
    now[0] += 86400
    await (await store.acquire('u')).release()


async def test_rate_http(client, cfg):
    cfg.requests_per_minute = 1
    body = {'action': 'ask', 'question': 'hello'}
    assert (await client.post('/v1/assistant/stream', json=body)).status_code == 200
    r = await client.post('/v1/assistant/stream', json=body)
    assert r.status_code == 429 and int(r.headers['retry-after']) > 0


async def test_firebase_verification_and_access(cfg, monkeypatch):
    import app.auth as module
    calls = []
    monkeypatch.setattr(module, 'get_app', lambda name: 'fake-app')
    def verify(token, **kwargs):
        calls.append((token, kwargs))
        if token == 'bad':
            raise ValueError('SECRET_TOKEN')
        return {'uid': 'verified-uid'}
    monkeypatch.setattr(module.auth, 'verify_id_token', verify)
    service = FirebaseAuthenticator(cfg)
    assert await service.verify('signed') == 'verified-uid'
    assert calls[-1] == ('signed', {'app': 'fake-app', 'check_revoked': True})
    with pytest.raises(ServiceError, match='unauthenticated'):
        await service.verify('bad')
    cfg.allowed_uids = ['other']
    with pytest.raises(ServiceError, match='forbidden'):
        await service.verify('signed')


def test_no_emulator_bypass(cfg, monkeypatch):
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST', 'localhost:9099')
    with pytest.raises(RuntimeError, match='emulator'):
        FirebaseAuthenticator(cfg)
