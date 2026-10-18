"""Replace with a shared atomic store before deploying multiple workers/replicas."""
import asyncio
import math
import time
from dataclasses import dataclass
from typing import Protocol
from app.config import Settings
from app.errors import ServiceError


class LimitLease(Protocol):
    async def release(self) -> None: ...


class UserLimits(Protocol):
    async def acquire(self, uid: str) -> LimitLease: ...


@dataclass
class Counters:
    minute: int
    day: int
    minute_count: int = 0
    day_count: int = 0
    active: int = 0


class MemoryLease:
    def __init__(self, store: 'InMemoryLimits', uid: str):
        self.store, self.uid, self.released = store, uid, False

    async def release(self):
        async with self.store.lock:
            if not self.released:
                self.store.users[self.uid].active -= 1
                self.store.active -= 1
                self.released = True


class InMemoryLimits:
    def __init__(self, cfg: Settings, clock=time.time):
        self.cfg, self.clock = cfg, clock
        self.users: dict[str, Counters] = {}
        self.active = 0
        self.lock = asyncio.Lock()

    async def acquire(self, uid: str) -> LimitLease:
        async with self.lock:
            now = self.clock()
            minute, day = int(now // 60), int(now // 86400)
            # Bounded retention: discard inactive identities from previous UTC days.
            self.users = {u: c for u, c in self.users.items() if c.active or c.day == day}
            c = self.users.setdefault(uid, Counters(minute, day))
            if c.minute != minute:
                c.minute, c.minute_count = minute, 0
            if c.day != day:
                c.day, c.day_count = day, 0
            if c.active >= self.cfg.max_concurrent_requests_per_user or self.active >= self.cfg.max_concurrent_requests:
                raise ServiceError('concurrency_limited')
            if c.minute_count >= self.cfg.requests_per_minute:
                raise ServiceError('rate_limited', math.ceil((minute + 1) * 60 - now))
            if c.day_count >= self.cfg.requests_per_day:
                raise ServiceError('quota_exceeded', math.ceil((day + 1) * 86400 - now))
            c.active += 1
            self.active += 1
            c.minute_count += 1
            c.day_count += 1
            return MemoryLease(self, uid)
