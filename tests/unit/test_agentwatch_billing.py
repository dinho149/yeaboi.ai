"""Tests for src/yeaboi/agentwatch/billing.py — subscription vs API framing."""

import json

from yeaboi.agentwatch import billing


def _write(tmp_path, payload):
    path = tmp_path / "claude.json"
    path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload, encoding="utf-8")
    return path


class TestDetect:
    def test_a_subscription_account(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            billing,
            "_claude_config_path",
            lambda: _write(
                tmp_path, {"oauthAccount": {"billingType": "stripe_subscription", "subscriptionType": "max"}}
            ),
        )
        ctx = billing.detect_billing()
        assert ctx.kind == billing.KIND_SUBSCRIPTION
        assert ctx.plan_label == "max"
        assert "not a bill" in ctx.total_label

    def test_an_api_account(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            billing, "_claude_config_path", lambda: _write(tmp_path, {"oauthAccount": {"billingType": "prepaid"}})
        )
        ctx = billing.detect_billing()
        assert ctx.kind == billing.KIND_API
        assert "API rates" in ctx.total_label

    def test_missing_or_broken_config_is_unknown(self, tmp_path, monkeypatch):
        monkeypatch.setattr(billing, "_claude_config_path", lambda: tmp_path / "nope.json")
        assert billing.detect_billing() == billing.BillingContext()
        monkeypatch.setattr(billing, "_claude_config_path", lambda: _write(tmp_path, "{not json"))
        assert billing.detect_billing().kind == billing.KIND_UNKNOWN

    def test_label_for_a_stored_kind(self):
        assert "subscription" in billing.label_for("subscription")
        assert "estimated" in billing.label_for("")
