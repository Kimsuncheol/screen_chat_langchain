import base64
import binascii
import io
import warnings
from PIL import Image, UnidentifiedImageError
from app.config import Settings
from app.errors import ServiceError
from app.schemas import AssistantRequest


def validate_input(req: AssistantRequest, cfg: Settings) -> None:
    fields = [(req.question, cfg.max_question_characters), (req.focus, cfg.max_focus_characters),
              (req.screen.ocr_text if req.screen else None, cfg.max_ocr_characters)]
    fields += [(m.content, cfg.max_message_characters) for m in req.messages]
    if len(req.messages) > cfg.max_messages or any(len(v) > limit for v, limit in fields if v):
        raise ServiceError('invalid_request')
    if sum(len(v) for v, _ in fields if v) > cfg.max_total_text_characters:
        raise ServiceError('invalid_request')
    image = req.screen.image if req.screen else None
    if image is None:
        return
    if not cfg.images_enabled:
        raise ServiceError('image_not_supported')
    if len(image.data_base64) > 4 * ((cfg.max_image_bytes + 2) // 3):
        raise ServiceError('payload_too_large')
    try:
        raw = base64.b64decode(image.data_base64, validate=True)
        if len(raw) > cfg.max_image_bytes:
            raise ServiceError('payload_too_large')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as img:
                expected = {'image/png': 'PNG', 'image/jpeg': 'JPEG'}[image.media_type]
                if img.format != expected:
                    raise ServiceError('invalid_request')
                if img.width * img.height > cfg.max_image_pixels:
                    raise ServiceError('payload_too_large')
                img.verify()
            # Verify structure and force decoding, entirely in memory.
            with Image.open(io.BytesIO(raw)) as img:
                img.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ServiceError('payload_too_large') from None
    except (ValueError, binascii.Error, UnidentifiedImageError, OSError, SyntaxError):
        raise ServiceError('invalid_request') from None
