import json
from datetime import datetime, timedelta

import pytest

from app.services.session_parser import SessionError, check_account, parse_session


def _chatgpt_session(email="user@example.com", plan="free", expires=None, token="token-123") -> str:
    return json.dumps(
        {
            "user": {"id": "user-1", "name": "Demo", "email": email},
            "expires": (expires or (datetime.utcnow() + timedelta(hours=1))).isoformat() + "Z",
            "accessToken": token,
            "account": {"planType": plan},
        }
    )


def test_parse_valid_chatgpt_session():
    info = parse_session(_chatgpt_session())
    assert info.kind == "chatgpt"
    assert info.email == "user@example.com"
    assert info.masked_email == "u***@example.com"
    assert info.plan == "free"
    assert info.user_id == "user-1"


def test_expired_session_rejected():
    raw = _chatgpt_session(expires=datetime.utcnow() - timedelta(minutes=2))
    with pytest.raises(SessionError) as exc:
        parse_session(raw)
    assert exc.value.code == "INVALID_SESSION_TOKEN"


def test_missing_access_token_rejected():
    raw = json.dumps({"user": {"email": "a@b.com"}, "expires": "2030-01-01T00:00:00Z"})
    with pytest.raises(SessionError) as exc:
        parse_session(raw)
    assert exc.value.code == "INVALID_SESSION_TOKEN"


def test_invalid_json_rejected():
    with pytest.raises(SessionError) as exc:
        parse_session("{not-json")
    assert exc.value.code == "INVALID_SESSION_TOKEN"


def test_wrapped_session_supported():
    raw = json.dumps({"session": json.loads(_chatgpt_session())})
    assert parse_session(raw).email == "user@example.com"


def test_plus_plan_blocked_without_locking():
    info = parse_session(_chatgpt_session(plan="plus"))
    check = check_account(info)
    assert check.eligible is False
    assert check.error_code == "ALREADY_SUBSCRIBED"


def test_claude_session_key():
    info = parse_session("sk-ant-sid01-abcdefghijklmnopqrstuvwxyz")
    assert info.kind == "claude"
    assert info.email.endswith("@session.moliply.local")
    assert info.access_token.startswith("sk-ant-")
    assert check_account(info).eligible is True


def test_claude_cookie_header():
    info = parse_session("sessionKey=sk-ant-sid01-abcdef1234567890; other=1")
    assert info.kind == "claude"
    assert info.access_token == "sk-ant-sid01-abcdef1234567890"
