import asyncio
import os
from typing import Protocol
from google.auth.exceptions import DefaultCredentialsError, TransportError
from firebase_admin import auth, get_app, initialize_app
from firebase_admin import exceptions as firebase_errors
from app.errors import ServiceError
from app.config import Settings


class Authenticator(Protocol):
    async def verify(self, token: str) -> str: ...


class FirebaseAuthenticator:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.app = None
        # Emulator verification accepts unsigned tokens. Never permit it in this server.
        if os.environ.get('FIREBASE_AUTH_EMULATOR_HOST'):
            raise RuntimeError('Firebase emulator mode is not permitted; use injected fakes in tests')

    async def verify(self, token: str) -> str:
        if not self.cfg.firebase_project_id:
            raise ServiceError('service_unavailable')
        try:
            if self.app is None:
                name = 'screen-chat-' + self.cfg.firebase_project_id
                try:
                    self.app = get_app(name)
                except ValueError:
                    self.app = initialize_app(options={'projectId': self.cfg.firebase_project_id,
                                                       'httpTimeout': 10}, name=name)
            claims = await asyncio.to_thread(auth.verify_id_token, token, app=self.app,
                                             check_revoked=True)
            uid = claims['uid']
        except (auth.CertificateFetchError, firebase_errors.UnavailableError,
                DefaultCredentialsError, TransportError):
            raise ServiceError('service_unavailable') from None
        except (ValueError, KeyError, firebase_errors.FirebaseError):
            raise ServiceError('unauthenticated') from None
        if self.cfg.allowed_uids and uid not in self.cfg.allowed_uids:
            raise ServiceError('forbidden')
        return uid
