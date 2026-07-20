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


def test_402_does_not_disable_endpoint_for_other_tickers(tmp_path):
    """A 402 flags needs_paid_plan but must NOT silently skip later calls.

    FMP sometimes returns 402 for a quota wall, not only for paid endpoints,
    so a 402 must never permanently disable an endpoint — each ticker still
    hits the network. Which endpoints are paid is decided explicitly by the
    provider (fmp_paid_plan), not learned from a 402 at runtime.
    """
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(402, json={"Error Message": "Payment Required"})

    p = _make_provider(tmp_path, handler)

    assert p.get_json("https://example.com/insiders", {}, "insiders", "NVDA") is None
    assert p.get_json("https://example.com/insiders", {}, "insiders", "AAPL") is None
    assert len(calls) == 2  # both hit the network; no silent skipping
    assert p.needs_paid_plan is True
