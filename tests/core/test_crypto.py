"""민감 필드 암·복호화 (T18, 계획서 4.2)."""

import base64
import os

import pytest

from app.core.crypto import (
    CryptoError,
    decrypt,
    decrypt_json,
    encrypt,
    encrypt_json,
    field_aad,
)

KEY = base64.b64encode(bytes(range(32))).decode()


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", KEY)


def test_round_trip():
    assert decrypt(encrypt("충청남도 논산시")) == "충청남도 논산시"


def test_json_round_trip():
    aad = field_aad(3, "special_flags")
    data = encrypt_json(["다자녀", "보훈"], aad=aad)
    assert decrypt_json(data, aad=aad) == ["다자녀", "보훈"]
    assert decrypt_json(encrypt_json(8)) == 8


def test_nonce_is_new_every_time():
    a, b = encrypt("같은 값"), encrypt("같은 값")
    assert a != b
    assert a[1:13] != b[1:13]


def test_ciphertext_does_not_contain_plaintext():
    assert "논산".encode() not in encrypt("충청남도 논산시")


@pytest.mark.parametrize("position", [0, 5, 20, -1])
def test_tampering_is_detected(position):
    data = bytearray(encrypt("소득분위 3"))
    data[position] ^= 0x01
    with pytest.raises(CryptoError):
        decrypt(bytes(data))


def test_truncated_data_is_rejected():
    with pytest.raises(CryptoError):
        decrypt(encrypt("값")[:20])


def test_aad_binds_row_and_field():
    data = encrypt("8", aad=field_aad(1, "income_bracket"))
    with pytest.raises(CryptoError):
        decrypt(data, aad=field_aad(2, "income_bracket"))  # 다른 사용자 행에 붙여 넣음
    with pytest.raises(CryptoError):
        decrypt(data, aad=field_aad(1, "self_region"))  # 다른 필드


def test_wrong_key_fails(monkeypatch):
    data = encrypt("값")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    from app.core.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(CryptoError):
        decrypt(data)


@pytest.mark.parametrize("bad_key", ["not-base64!!", base64.b64encode(b"short").decode(), None])
def test_invalid_or_missing_key(monkeypatch, bad_key):
    from app.core.config import get_settings

    if bad_key is None:
        monkeypatch.delenv("FIELD_ENCRYPTION_KEY")
    else:
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", bad_key)
    get_settings.cache_clear()
    with pytest.raises(CryptoError) as error:
        encrypt("값")
    if bad_key:
        assert bad_key not in str(error.value)  # 키 값을 메시지에 넣지 않음


def test_error_message_has_no_plaintext():
    data = bytearray(encrypt("비밀 주소"))
    data[-1] ^= 0x01
    with pytest.raises(CryptoError) as error:
        decrypt(bytes(data))
    assert "비밀 주소" not in str(error.value)
