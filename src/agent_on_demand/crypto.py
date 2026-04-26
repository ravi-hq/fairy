import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet() -> Fernet:
    material = getattr(settings, "FIELD_ENCRYPTION_KEY", None) or settings.SECRET_KEY
    key = hashlib.sha256(material.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(plaintext: str) -> bytes:
    return _get_fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    try:
        return _get_fernet().decrypt(ciphertext).decode()
    except InvalidToken as e:
        raise ValueError(
            "Decryption failed: data may be corrupted or the encryption key has changed"
        ) from e
