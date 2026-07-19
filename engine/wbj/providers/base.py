"""Resilient HTTP provider base: cache-first fetch with retry/backoff.

`Provider.get_json` never raises for network/HTTP failures — it returns
`None` on exhaustion, and callers are expected to map that to
`wbj.core.nullstates.NullState.MISSING`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from wbj.providers.cache import Cache

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
_REDACTED_PARAMS = frozenset({"apikey", "token", "api_key"})

# Paid-tier refusals (402) are remembered per endpoint, not per ticker, so
# the note lives under a pseudo-ticker. It expires after a week so an
# upgraded plan starts working without clearing the cache by hand.
_PAYWALL_TICKER = "_ENDPOINTS"
_PAYWALL_TTL_DAYS = 7.0


def _redact_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Copy `params` with sensitive values masked, safe to put in log text."""
    if not params:
        return {}
    return {
        k: ("***" if k.lower() in _REDACTED_PARAMS else v) for k, v in params.items()
    }


class Provider:
    """Base class for wbj data providers.

    Subclasses build request URLs/params and call `get_json`, which
    handles cache-first serving and resilient retries uniformly.
    """

    def __init__(
        self,
        settings: Any,
        cache: Cache,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.client = client if client is not None else httpx.Client()
        # Client-error statuses seen this run. `get_json` swallows failures
        # and returns None, which downstream reads as "no data" — but a
        # quota wall (429) or a paid-tier endpoint (402) is not the same as
        # a company genuinely lacking data, and the user needs to be told
        # which one they hit.
        self.client_errors: list[int] = []

    @property
    def quota_exhausted(self) -> bool:
        """True if the API refused a request for rate/quota reasons."""
        return 429 in self.client_errors

    @property
    def needs_paid_plan(self) -> bool:
        """True if an endpoint required a paid plan."""
        return 402 in self.client_errors

    def _sleep(self, seconds: float) -> None:
        """Sleep for `seconds`. Isolated so tests can monkeypatch it out."""
        time.sleep(seconds)

    def _paywall_key(self, cache_key: str) -> str:
        return f"_paywalled_{cache_key}"

    def _is_paywalled(self, cache_key: str) -> bool:
        """True if this endpoint answered 402 recently for any ticker.

        A paid-tier endpoint answers 402 forever on a free key, but the
        request still counts against the daily quota. Remembering the
        refusal keeps those calls from burning quota that the scorable
        endpoints need. The note expires so a plan upgrade is picked up.
        """
        age = self.cache.age_days(_PAYWALL_TICKER, self._paywall_key(cache_key))
        return age is not None and age <= _PAYWALL_TTL_DAYS

    def get_json(
        self,
        url: str,
        params: dict[str, Any],
        cache_key: str,
        ticker: str,
        max_age_days: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict | None:
        """Fetch JSON, cache-first, with retry/backoff on transient failures.

        If a cache entry exists for (ticker, cache_key) and is fresh enough
        (age <= max_age_days, or max_age_days is None), it is returned
        without touching the network. Otherwise up to 3 attempts are made
        against `url`, backing off 0.5s/1s/2s between attempts on 5xx
        responses or httpx transport errors (including timeouts). 4xx
        responses are treated as non-retryable client errors. Returns None
        (never raises) if the fetch ultimately fails; a successful response
        is written to cache before being returned.

        `headers`, if given, is passed through to the underlying request
        (e.g. a required `User-Agent` per SEC EDGAR's fair-access policy).
        Existing callers that don't pass `headers` are unaffected.
        """
        age = self.cache.age_days(ticker, cache_key)
        if age is not None and (max_age_days is None or age <= max_age_days):
            return self.cache.get(ticker, cache_key)

        # Skip endpoints already known to need a paid plan: the call would
        # spend quota only to be refused again.
        if self._is_paywalled(cache_key):
            logger.debug(
                "wbj provider skipping paid-tier endpoint key=%s ticker=%s",
                cache_key,
                ticker,
            )
            self.client_errors.append(402)
            return None

        safe_params = _redact_params(params)

        for attempt in range(_MAX_ATTEMPTS):
            is_last_attempt = attempt == _MAX_ATTEMPTS - 1
            try:
                response = self.client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                logger.warning(
                    "wbj provider request failed (attempt %d/%d) url=%s "
                    "params=%s error=%s",
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    url,
                    safe_params,
                    exc,
                )
                if not is_last_attempt:
                    self._sleep(_BACKOFF_SECONDS[attempt])
                continue

            if response.status_code < 400:
                try:
                    payload = response.json()
                except ValueError:
                    logger.warning(
                        "wbj provider returned malformed JSON status=%d url=%s "
                        "params=%s",
                        response.status_code,
                        url,
                        safe_params,
                    )
                    return None
                self.cache.put(ticker, cache_key, payload)
                return payload

            if response.status_code < 500:
                self.client_errors.append(response.status_code)
                if response.status_code == 402:
                    # Remember the refusal so later runs don't re-spend quota
                    # rediscovering that this endpoint is paid-only.
                    self.cache.put(
                        _PAYWALL_TICKER, self._paywall_key(cache_key), {"status": 402}
                    )
                logger.warning(
                    "wbj provider client error status=%d url=%s params=%s",
                    response.status_code,
                    url,
                    safe_params,
                )
                return None

            logger.warning(
                "wbj provider server error (attempt %d/%d) status=%d url=%s "
                "params=%s",
                attempt + 1,
                _MAX_ATTEMPTS,
                response.status_code,
                url,
                safe_params,
            )
            if not is_last_attempt:
                self._sleep(_BACKOFF_SECONDS[attempt])

        return None
