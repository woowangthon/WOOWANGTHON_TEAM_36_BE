"""민감 필드 암·복호화 (계획서 4.2). AES-256-GCM, 키는 `FIELD_ENCRYPTION_KEY`(base64 32바이트).

저장 형식: `버전(1바이트) + nonce(12바이트) + 암호문·태그`. nonce는 암호화할 때마다 새로 만듭니다.
`aad`(추가 인증 데이터)에 사용자 ID와 필드 이름을 넣으면, 암호문을 다른 행·필드로 옮겨 붙였을 때
복호화가 실패합니다.

복호화한 값과 키는 로그·예외 메시지에 넣지 않습니다.
"""

import base64
import binascii
import json
import os
from functools import lru_cache
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

FORMAT_VERSION = 1
NONCE_SIZE = 12
KEY_SIZE = 32


class CryptoError(Exception):
    """키가 없거나 잘못됐거나, 암호문이 변조·손상됐을 때. 메시지에 값을 넣지 않습니다."""


@lru_cache
def _cipher(encoded_key: str) -> AESGCM:
    try:
        key = base64.b64decode(encoded_key, validate=True)
    except (binascii.Error, ValueError):
        raise CryptoError("FIELD_ENCRYPTION_KEY가 base64가 아닙니다") from None
    if len(key) != KEY_SIZE:
        raise CryptoError("FIELD_ENCRYPTION_KEY는 32바이트(AES-256)여야 합니다")
    return AESGCM(key)


def _current_cipher() -> AESGCM:
    secret = get_settings().field_encryption_key
    if secret is None:
        raise CryptoError("FIELD_ENCRYPTION_KEY가 설정되지 않았습니다")
    return _cipher(secret.get_secret_value())


def field_aad(user_id: int, field: str) -> bytes:
    """행·필드에 묶는 추가 인증 데이터. 예: `field_aad(3, "income_bracket")`."""
    return f"user:{user_id}:{field}".encode()


def encrypt(plaintext: str, *, aad: bytes = b"") -> bytes:
    nonce = os.urandom(NONCE_SIZE)
    sealed = _current_cipher().encrypt(nonce, plaintext.encode(), aad)
    return bytes([FORMAT_VERSION]) + nonce + sealed


def decrypt(data: bytes, *, aad: bytes = b"") -> str:
    if len(data) < 1 + NONCE_SIZE + 16 or data[0] != FORMAT_VERSION:
        raise CryptoError("암호문 형식이 올바르지 않습니다")
    nonce, sealed = data[1 : 1 + NONCE_SIZE], data[1 + NONCE_SIZE :]
    try:
        return _current_cipher().decrypt(nonce, sealed, aad).decode()
    except InvalidTag:
        raise CryptoError("복호화 실패: 키가 다르거나 암호문이 변조됐습니다") from None


def encrypt_json(value: Any, *, aad: bytes = b"") -> bytes:
    """숫자·목록 같은 값을 JSON으로 바꿔 암호화합니다(소득분위, 특수자격 목록 등)."""
    return encrypt(json.dumps(value, ensure_ascii=False), aad=aad)


def decrypt_json(data: bytes, *, aad: bytes = b"") -> Any:
    return json.loads(decrypt(data, aad=aad))
