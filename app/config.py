import os
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', env_prefix='SC_')
    ollama_api_key: SecretStr = SecretStr('')
    model_id: str = ''  # Deliberately no implicit model or local-daemon fallback.
    firebase_project_id: str = ''
    enabled_actions: list[str] = ['summarize', 'analyze', 'ask']
    image_support_verified: bool = False
    verified_image_model_id: str = ''
    context_tokens: int = Field(32768, ge=1024)
    max_output_tokens: int = Field(2048, ge=1)
    image_token_reserve: int = Field(8192, ge=1)
    max_request_bytes: int = Field(8388608, ge=1)
    max_image_bytes: int = Field(5242880, ge=1)
    max_image_pixels: int = Field(16000000, ge=1, le=16000000)
    max_question_characters: int = Field(4000, ge=1)
    max_ocr_characters: int = Field(12000, ge=1)
    max_focus_characters: int = Field(2000, ge=1)
    max_messages: int = Field(12, ge=0)
    max_message_characters: int = Field(4000, ge=1)
    max_total_text_characters: int = Field(24000, ge=1)
    max_concurrent_requests_per_user: int = Field(1, ge=1)
    max_concurrent_requests: int = Field(8, ge=1)
    requests_per_minute: int = Field(10, ge=1)
    requests_per_day: int = Field(100, ge=1)
    request_timeout_seconds: float = Field(120, gt=0)
    body_timeout_seconds: float = Field(15, gt=0)
    connect_timeout_seconds: float = Field(10, gt=0)
    upstream_idle_timeout_seconds: float = Field(30, gt=0)
    allowed_uids: list[str] = []  # Empty: all verified project users.

    @model_validator(mode='after')
    def coherent(self):
        if not set(self.enabled_actions) <= {'summarize', 'analyze', 'ask'}:
            raise ValueError('Unknown enabled action')
        if self.max_output_tokens >= self.context_tokens:
            raise ValueError('Output budget must be smaller than context')
        return self

    @property
    def images_enabled(self) -> bool:
        # Operator verification is tied to the exact direct-cloud model name.
        # Changing model_id invalidates prior vision approval; never guess by family.
        return (self.model_ready and self.image_support_verified
                and self.verified_image_model_id == self.model_id)

    @property
    def model_ready(self):
        return bool(self.model_id and self.ollama_api_key.get_secret_value())


def disable_content_tracing():
    for name in ('LANGCHAIN_TRACING', 'LANGCHAIN_TRACING_V2', 'LANGSMITH_TRACING'):
        os.environ[name] = 'false'
