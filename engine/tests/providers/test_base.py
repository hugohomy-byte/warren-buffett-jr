"""Tests for wbj.providers.base: param redaction in logged requests."""

import logging

import httpx

from wbj.config import Settings
from wbj.providers.base import Provider
from wbj.providers.cache import Cache


def _make_provider(tmp_path, handler):
    settings = Settings()
    cache = Cache(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Provider(settings, cache, client=client)


def test_redacts_apikey_token_and_api_key_from_client_error_log(tmp_path, caplog):
    """4xx responses log params; apikey/token/api_key must never appear in
    plaintext in the log output — only the '***' mask."""

    def handler(request):
        return httpx.Response(400, json={"error": "bad request"})

    p = _make_provider(tmp_path, handler)

    with caplog.at_level(logging.WARNING):
        result = p.get_json(
            "https://example.com/thing",
            {
                "apikey": "secret-fmp-key",
                "token": "secret-finnhub-key",
                "api_key": "secret-fred-key",
                "symbol": "NVDA",
            },
            "thing",
            "NVDA",
        )

    assert result is None
    log_text = caplog.text
    assert "secret-fmp-key" not in log_text
    assert "secret-finnhub-key" not in log_text
    assert "secret-fred-key" not in log_text
    assert "NVDA" in log_text


# --- client-error surfacing ------------------------------------------------


def test_quota_exhausted_flagged_on_429(tmp_path):
    """A 429 must be distinguishable from a company genuinely lacking data."""

    def handler(request):
        return httpx.Response(429, json={"Error Message": "Limit Reach"})

    p = _make_provider(tmp_path, handler)

    assert p.get_json("https://example.com/x", {}, "x", "NVDA") is None
    assert p.quota_exhausted is True
    assert p.needs_paid_plan is False


def test_paid_plan_flagged_on_402(tmp_path):
    def handler(request):
        return httpx.Response(402, json={"Error Message": "Payment Required"})

    p = _make_provider(tmp_path, handler)

    assert p.get_json("https://example.com/x", {}, "x", "NVDA") is None
    assert p.needs_paid_plan is True
    assert p.quota_exhausted is False


def test_no_error_flags_on_success(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"ok": True})

    p = _make_provider(tmp_path, handler)

    assert p.get_json("https://example.com/x", {}, "x", "NVDA") == {"ok": True}
    assert p.client_errors == []
    assert p.quota_exhausted is False


def test_paid_endpoint_is_skipped_on_later_calls(tmp_path):
    """402 must be remembered: re-calling would spend quota to be refused."""
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(402, json={"Error Message": "Payment Required"})

    p = _make_provider(tmp_path, handler)

    assert p.get_json("https://example.com/insiders", {}, "insiders", "NVDA") is None
    assert len(calls) == 1

    # Same endpoint, different ticker: still skipped, no second request.
    assert p.get_json("https://example.com/insiders", {}, "insiders", "AAPL") is None
    assert len(calls) == 1
    assert p.needs_paid_plan is True


def test_paywall_note_does_not_block_other_endpoints(tmp_path):
    def handler(request):
        if "insiders" in request.url.path:
            return httpx.Response(402, json={})
        return httpx.Response(200, json={"ok": True})

    p = _make_provider(tmp_path, handler)

    assert p.get_json("https://example.com/insiders", {}, "insiders", "NVDA") is None
    assert p.get_json("https://example.com/profile", {}, "profile", "NVDA") == {"ok": True}


def test_quota_error_is_not_remembered_as_paywall(tmp_path):
    """429 is transient — it must not permanently disable the endpoint."""
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={})

    p = _make_provider(tmp_path, handler)

    p.get_json("https://example.com/x", {}, "x", "NVDA")
    p.get_json("https://example.com/x", {}, "x", "AAPL")
    assert len(calls) == 2
