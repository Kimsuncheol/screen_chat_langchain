"""Shared direct Ollama Cloud transport for LangChain workflow messages.

Explicit HTTP stream ownership makes cancellation/closure independent of integration
internals. No local daemon, callbacks, retries, tools or response accumulation.
"""
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol
import httpx
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from app.config import Settings
from app.errors import ServiceError


@dataclass
class ModelChunk:
    text: str = ''
    finish_reason: Literal['stop', 'length'] | None = None


class ModelAdapter(Protocol):
    def stream(self, messages: list[BaseMessage], image: str | None) -> AsyncIterator[ModelChunk]: ...


class ProviderMessage(BaseModel):
    content: str = ''
    tool_calls: list = Field(default_factory=list)


class ProviderChunk(BaseModel):
    model_config = ConfigDict(strict=True)
    message: ProviderMessage = Field(default_factory=ProviderMessage)
    done: bool = False
    done_reason: str | None = None
    error: str | None = None


def check_context(messages: list[BaseMessage], image: str | None, cfg: Settings):
    # Conservative UTF-8 byte estimate, not a claim of exact provider tokenization.
    # Includes serialized JSON/history, system prompt, chat framing and image reserve.
    estimate = sum(len(str(m.content).encode('utf-8')) + 64 for m in messages)
    estimate += cfg.image_token_reserve if image else 0
    if estimate + cfg.max_output_tokens > cfg.context_tokens:
        raise ServiceError('context_too_large')


class OllamaCloudAdapter:
    def __init__(self, cfg: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg, self.transport = cfg, transport

    async def stream(self, messages: list[BaseMessage], image: str | None):
        payload_messages = [{'role': {'system': 'system', 'human': 'user', 'ai': 'assistant'}[m.type],
                             'content': m.content} for m in messages]
        if image:
            payload_messages[-1]['images'] = [image]
        timeout = httpx.Timeout(self.cfg.upstream_idle_timeout_seconds,
                               connect=self.cfg.connect_timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport,
                                         trust_env=False, follow_redirects=False) as client:
                async with client.stream(
                    'POST', 'https://ollama.com/api/chat',
                    headers={'Authorization': 'Bearer ' + self.cfg.ollama_api_key.get_secret_value()},
                    json={'model': self.cfg.model_id, 'messages': payload_messages, 'stream': True,
                          'options': {'num_predict': self.cfg.max_output_tokens, 'temperature': 0}},
                ) as response:
                    if response.status_code != 200:
                        raise ServiceError('upstream_error')
                    # Bound each NDJSON line, including malformed streams without newlines.
                    pending = b''
                    async for block in response.aiter_bytes():
                        pending += block
                        while b'\n' in pending:
                            line, pending = pending.split(b'\n', 1)
                            if len(line) > 262144:
                                raise ServiceError('upstream_error')
                            if not line.strip():
                                continue
                            part = self.parse(line)
                            if part.text:
                                yield ModelChunk(text=part.text)
                            if part.finish_reason:
                                yield ModelChunk(finish_reason=part.finish_reason)
                                return
                        if len(pending) > 262144:
                            raise ServiceError('upstream_error')
                    if pending.strip():
                        part = self.parse(pending)
                        if part.text:
                            yield ModelChunk(text=part.text)
                        if part.finish_reason:
                            yield ModelChunk(finish_reason=part.finish_reason)
                            return
                    # EOF without provider completion must never become SSE success.
                    raise ServiceError('upstream_error')
        except httpx.TimeoutException:
            raise ServiceError('upstream_timeout') from None
        except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError):
            raise ServiceError('upstream_error') from None

    @staticmethod
    def parse(line: bytes) -> ModelChunk:
        part = ProviderChunk.model_validate_json(line)
        if part.error or part.message.tool_calls:
            raise ServiceError('upstream_error')
        if part.done and part.done_reason not in ('stop', 'length'):
            raise ServiceError('upstream_error')
        return ModelChunk(text=part.message.content,
                          finish_reason=part.done_reason if part.done else None)
