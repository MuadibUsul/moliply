import base64

import pytest

from app.services.code_generator import ALPHABET, generate_code, is_valid_code_format, normalize_code
from app.services.crypto import (
    decrypt_json,
    decrypt_text,
    encrypt_json,
    encrypt_text,
    luhn_valid,
    mask_card_number,
    mask_email,
)


def test_text_roundtrip():
    token = encrypt_text("super-secret-session")
    assert token != "super-secret-session"
    assert decrypt_text(token) == "super-secret-session"
    assert decrypt_text(token) == "super-secret-session"


def test_json_roundtrip():
    payload = {"email": "user@example.com", "sessionKey": "sk-ant-123"}
    token = encrypt_json(payload)
    assert decrypt_json(token) == payload


def test_tampered_ciphertext_fails():
    token = encrypt_text("hello")
    raw = bytearray(base64.b64decode(token))
    raw[-1] ^= 0x01
    with pytest.raises(Exception):
        decrypt_text(base64.b64encode(bytes(raw)).decode("ascii"))


def test_mask_email():
    assert mask_email("user@example.com") == "u***@example.com"
    assert mask_email("u@example.com") == "u***@example.com"
    assert mask_email("") == "***"


def test_mask_card_and_luhn():
    assert mask_card_number("4242424242424242") == "4242 **** **** 4242"
    assert luhn_valid("4242424242424242") is True
    assert luhn_valid("4242424242424241") is False


def test_code_format_and_alphabet():
    code = generate_code()
    assert is_valid_code_format(code)
    assert code.startswith("MOLI-")
    assert len(code) == 19
    assert not any(char in code.replace("MOLI-", "") for char in "0O1I")
    assert set(code.replace("MOLI-", "").replace("-", "")) <= set(ALPHABET)


def test_code_normalization():
    code = generate_code()
    compact = code.replace("-", "").lower()
    assert normalize_code(compact) == code
    assert normalize_code(f"  {code.lower()}  ") == code
    assert is_valid_code_format("MOLI-8F3K-9A2B-7C4D")
