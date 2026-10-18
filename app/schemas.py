from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Action = Literal['summarize', 'analyze', 'ask']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ImageInput(StrictModel):
    media_type: Literal['image/png', 'image/jpeg']
    data_base64: str = Field(min_length=1)


class Screen(StrictModel):
    ocr_text: str | None = None
    image: ImageInput | None = None

    @model_validator(mode='after')
    def not_empty(self):
        if not (self.ocr_text and self.ocr_text.strip()) and self.image is None:
            raise ValueError('Empty screen')
        return self


class Message(StrictModel):
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1)

    @model_validator(mode='after')
    def not_blank(self):
        if not self.content.strip():
            raise ValueError('Empty message')
        return self


class AssistantRequest(StrictModel):
    action: Action
    language: Literal['auto', 'en', 'ko'] = 'auto'
    question: str | None = None
    focus: str | None = None
    screen: Screen | None = None
    messages: list[Message] = Field(default_factory=list)

    @model_validator(mode='after')
    def action_fields(self):
        supplied = self.model_fields_set
        if self.action == 'ask':
            if not self.question or not self.question.strip() or 'focus' in supplied:
                raise ValueError('Invalid ask fields')
        else:
            if self.screen is None or 'question' in supplied or 'messages' in supplied:
                raise ValueError('Invalid screen action fields')
            if self.action == 'summarize' and 'focus' in supplied:
                raise ValueError('Invalid summarize fields')
        if self.focus is not None and not self.focus.strip():
            raise ValueError('Empty focus')
        return self


class Health(BaseModel):
    status: Literal['ok'] = 'ok'
    api_version: Literal['v1'] = 'v1'


class InputCapabilities(BaseModel):
    text: bool
    image: bool


class Capabilities(BaseModel):
    api_version: Literal['v1'] = 'v1'
    actions: list[Action]
    inputs: InputCapabilities
    languages: list[Literal['auto', 'en', 'ko']] = ['auto', 'en', 'ko']
    limits: dict[str, int | float]


class StartEvent(BaseModel):
    request_id: str
    action: Action


class DeltaEvent(BaseModel):
    request_id: str
    text: str


class CompleteEvent(BaseModel):
    request_id: str
    finish_reason: Literal['stop', 'length']
