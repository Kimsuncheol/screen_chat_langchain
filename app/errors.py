from typing import Literal
from pydantic import BaseModel

ErrorCode = Literal[
    'unauthenticated', 'forbidden', 'payload_too_large', 'unsupported_media_type',
    'invalid_request', 'context_too_large', 'image_not_supported', 'rate_limited',
    'concurrency_limited', 'quota_exceeded', 'upstream_error', 'service_unavailable',
    'upstream_timeout', 'internal_error', 'not_found', 'method_not_allowed',
]
MESSAGES = {
    'unauthenticated': (401, 'A valid Firebase ID token is required.', False),
    'forbidden': (403, 'This account does not have service access.', False),
    'payload_too_large': (413, 'The submitted content exceeds the size limit.', False),
    'unsupported_media_type': (415, 'Use JSON with PNG or JPEG images.', False),
    'invalid_request': (422, 'The request does not match the supported input schema.', False),
    'context_too_large': (422, 'The input exceeds the configured model context budget.', False),
    'image_not_supported': (422, 'Image input is not enabled for this service.', False),
    'rate_limited': (429, 'Too many requests. Please try again later.', True),
    'concurrency_limited': (429, 'The active request limit has been reached.', True),
    'quota_exceeded': (429, 'The daily request allowance has been reached.', False),
    'upstream_error': (502, 'The AI service could not complete the request.', True),
    'service_unavailable': (503, 'The AI service is not available.', True),
    'upstream_timeout': (504, 'The AI service timed out.', True),
    'internal_error': (500, 'The request could not be completed.', True),
    'not_found': (404, 'The endpoint does not exist.', False),
    'method_not_allowed': (405, 'The HTTP method is not supported.', False),
}


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    request_id: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ServiceError(Exception):
    def __init__(self, code: ErrorCode, retry_after: int | None = None):
        self.code = code
        self.status, self.safe_message, self.retryable = MESSAGES[code]
        self.retry_after = retry_after
        super().__init__(code)

    def response(self, request_id: str) -> ErrorResponse:
        return ErrorResponse(error=ErrorDetail(code=self.code, message=self.safe_message,
                                               request_id=request_id, retryable=self.retryable))
