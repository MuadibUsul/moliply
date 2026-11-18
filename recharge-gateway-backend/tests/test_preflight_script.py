"""The preflight script must report, not crash, and must judge secrets properly."""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "preflight_check.py"


def _load(monkeypatch):
    spec = importlib.util.spec_from_file_location("preflight_check_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.results.clear()
    return module


def test_shipped_defaults_are_rejected(monkeypatch):
    from app.config import settings

    module = _load(monkeypatch)
    monkeypatch.setattr(settings, "admin_password", "change-me", raising=False)
    monkeypatch.setattr(settings, "admin_jwt_secret", "dev-admin-jwt-secret-change-me", raising=False)
    monkeypatch.setattr(settings, "master_aes_key", "", raising=False)
    monkeypatch.setattr(settings, "admin_totp_secret", "", raising=False)

    module.check_secrets()
    verdicts = {(status, check) for status, _section, check, _detail in module.results}
    assert ("FAIL", "ADMIN_PASSWORD 已脱离默认值") in verdicts
    assert ("FAIL", "ADMIN_JWT_SECRET 已脱离默认值") in verdicts
    assert ("FAIL", "MASTER_AES_KEY 已配置") in verdicts


def test_change_me_placeholders_are_rejected(monkeypatch):
    from app.config import settings

    module = _load(monkeypatch)
    monkeypatch.setattr(settings, "admin_password", "CHANGE_ME_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(settings, "admin_jwt_secret", "CHANGE_ME_RANDOM_JWT_SECRET", raising=False)
    monkeypatch.setattr(settings, "master_aes_key", base64.b64encode(b"k" * 32).decode(), raising=False)
    monkeypatch.setattr(settings, "admin_totp_secret", "JBSWY3DPEHPK3PXP", raising=False)

    module.check_secrets()
    failed = {check for status, _s, check, _d in module.results if status == "FAIL"}
    assert "ADMIN_PASSWORD 已脱离默认值" in failed
    assert "ADMIN_JWT_SECRET 已脱离默认值" in failed


def test_strong_secrets_pass(monkeypatch):
    from app.config import settings

    module = _load(monkeypatch)
    monkeypatch.setattr(settings, "admin_password", "a-very-long-admin-pass", raising=False)
    monkeypatch.setattr(settings, "admin_jwt_secret", "x" * 48, raising=False)
    monkeypatch.setattr(settings, "master_aes_key", base64.b64encode(b"k" * 32).decode(), raising=False)
    monkeypatch.setattr(settings, "admin_totp_secret", "JBSWY3DPEHPK3PXP", raising=False)

    module.check_secrets()
    statuses = [status for status, *_ in module.results]
    assert statuses and all(status == "PASS" for status in statuses)


def test_short_secret_warns_rather_than_fails(monkeypatch):
    from app.config import settings

    module = _load(monkeypatch)
    monkeypatch.setattr(settings, "admin_password", "short-pass", raising=False)
    monkeypatch.setattr(settings, "admin_jwt_secret", "x" * 48, raising=False)
    monkeypatch.setattr(settings, "master_aes_key", base64.b64encode(b"k" * 32).decode(), raising=False)
    monkeypatch.setattr(settings, "admin_totp_secret", "JBSWY3DPEHPK3PXP", raising=False)

    module.check_secrets()
    assert ("WARN", "ADMIN_PASSWORD 长度足够") in {
        (status, check) for status, _section, check, _detail in module.results
    }


def test_desensitized_subject_passes_and_keeps_the_order_number(monkeypatch):
    module = _load(monkeypatch)
    module.check_desensitized_subject()
    statuses = [status for status, *_ in module.results]
    assert statuses == ["PASS"]


def test_every_check_is_exercised_even_when_one_raises(monkeypatch):
    module = _load(monkeypatch)
    calls: list[str] = []

    def boom():
        calls.append("boom")
        raise RuntimeError("nope")

    def fine():
        calls.append("fine")

    monkeypatch.setattr(module, "SECTIONS", (("坏", boom), ("好", fine)))
    monkeypatch.setattr(module.sys, "argv", ["preflight"])
    assert module.main() == 1
    assert calls == ["boom", "fine"]
    assert any(status == "FAIL" and check == "boom" for status, _s, check, _d in module.results)


def test_main_returns_zero_without_failures(monkeypatch):
    module = _load(monkeypatch)
    monkeypatch.setattr(module, "SECTIONS", (("好", lambda: module.record(module.PASS, "好", "ok")),))
    monkeypatch.setattr(module.sys, "argv", ["preflight"])
    assert module.main() == 0


def test_strict_mode_fails_on_warnings(monkeypatch):
    module = _load(monkeypatch)
    monkeypatch.setattr(module, "SECTIONS", (("警告", lambda: module.record(module.WARN, "警告", "meh")),))
    monkeypatch.setattr(module.sys, "argv", ["preflight", "--strict"])
    assert module.main() == 1


@pytest.mark.parametrize(
    "value,expected",
    [
        (base64.b64encode(b"k" * 32).decode(), True),
        (base64.b64encode(b"k" * 16).decode(), False),
        ("", False),
        ("not-base64!!", False),
    ],
)
def test_aes_key_shape_detection(monkeypatch, value, expected):
    module = _load(monkeypatch)
    assert module.key_is_valid_base64_aes(value) is expected
