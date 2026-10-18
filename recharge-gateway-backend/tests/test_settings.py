from __future__ import annotations

from app.services import feishu


def test_admin_settings_roundtrip_and_secret_masking(client, admin_headers):
    initial = client.get("/api/v1/admin/settings", headers=admin_headers)
    assert initial.status_code == 200, initial.text
    data = initial.json()
    assert "payment_mode" in data["values"]
    assert "ltzf_key" not in data["values"]
    assert data["feishu_event_options"]
    assert isinstance(data["recent_notifications"], list)

    updated = client.patch(
        "/api/v1/admin/settings",
        headers=admin_headers,
        json={
            "values": {
                "support_wechat": "settings-test-wechat",
                "support_hours": "10:00-23:00",
                "feishu_enabled": False,
                "feishu_events": "order.created,recharge.failed",
                "feishu_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test-placeholder",
            }
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["values"]["support_wechat"] == "settings-test-wechat"
    assert body["secret_configured"]["feishu_webhook_url"] is True
    assert "feishu_webhook_url" not in body["values"]

    brand = client.get("/api/v1/brand").json()
    assert brand["support_wechat"] == "settings-test-wechat"
    assert brand["support_hours"] == "10:00-23:00"

    cleared = client.patch(
        "/api/v1/admin/settings",
        headers=admin_headers,
        json={"values": {}, "clear_secrets": ["feishu_webhook_url"]},
    )
    assert cleared.status_code == 200
    assert cleared.json()["secret_configured"]["feishu_webhook_url"] is False

    # Restore the default used by the rest of the suite.
    client.patch(
        "/api/v1/admin/settings",
        headers=admin_headers,
        json={"values": {"support_wechat": "MOLIPLY-SUPPORT", "support_hours": "每日 09:00–24:00"}},
    )


def test_feishu_event_validation(client, admin_headers):
    bad = client.patch(
        "/api/v1/admin/settings",
        headers=admin_headers,
        json={"values": {"feishu_events": "order.created,not-a-real-event"}},
    )
    assert bad.status_code == 400
    assert "未知飞书事件" in bad.json()["detail"]


def test_feishu_signature_and_card():
    signature = feishu._feishu_signature("1700000000", "test-secret")
    assert len(signature) == 44
    assert feishu._feishu_signature("1700000000", "test-secret") == signature

    card = feishu._build_card(
        "测试通知",
        {"订单号": "MOL123", "金额": "¥158.00"},
        "warning",
        "/admin/orders",
        True,
    )
    assert card["header"]["template"] == "orange"
    assert "MOL123" in card["elements"][0]["text"]["content"]
    assert any("<at id=all></at>" in str(element) for element in card["elements"])


def test_send_feishu_signs_and_posts_card(client, admin_headers, monkeypatch):
    from app.services import feishu

    configured = client.patch(
        "/api/v1/admin/settings",
        headers=admin_headers,
        json={
            "values": {
                "feishu_enabled": True,
                "feishu_events": "order.created",
                "feishu_low_stock_threshold": 5,
                "feishu_mention_all": False,
                "feishu_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
                "feishu_secret": "feishu-test-secret",
            }
        },
    )
    assert configured.status_code == 200, configured.text

    captured = {}

    class FakeResponse:
        status_code = 200
        content = b'{"code":0}'

        def json(self):
            return {"code": 0}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json):
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(feishu.httpx, "Client", FakeClient)
    result = feishu.send_feishu(
        "order.created",
        "MOLIPLY 新订单",
        {"订单号": "MOLTEST123", "金额": "¥158.00"},
        severity="info",
        link_path="/admin/orders?q=MOLTEST123",
    )
    assert result["status"] == "sent"
    payload = captured["payload"]
    assert payload["msg_type"] == "interactive"
    assert payload["sign"]
    assert payload["timestamp"]
    assert "MOLTEST123" in str(payload["card"])
    assert "查看运营后台" in str(payload["card"])
