"""FinnHub provider.

Wraps a slice of the FinnHub REST API: consensus EPS/revenue estimates,
the earnings calendar, real-time quote, company profile and the
precomputed metric bundle. `FinnhubProvider` is disabled
(`available == False`) when no API key is configured; every public
method then returns `None` immediately without touching the cache or the
network. Requests and caching are delegated to
`wbj.providers.base.Provider.get_json` — this module only builds
URLs/params and picks cache keys / max_age_days per data type.

FinnHub's key is passed as the `token` query param; `base.Provider`
already redacts `token` from logged request params.

Free-tier note: `estimates`/`revenue_estimates` and daily candles are
paid-only (403), but `quote`, `profile` and `metrics` are not — and
`metrics` already carries 52-week high/low and trailing price returns,
which is enough to score technical/valuation without price history.
"""

from __future__ import annotations

from typing import Any

from wbj.providers.base import Provider

BASE_URL = "https://finnhub.io/api/v1"

# max_age_days per cache key, per task brief:
#   quote 1, estimates/revenue_estimates/earnings_calendar 7.
_MAX_AGE_QUOTE = 1
_MAX_AGE_ESTIMATES = 7
_MAX_AGE_CALENDAR = 7
# metrics move with price (52w high, trailing returns) -> refresh daily;
# profile is reference data -> weekly, same as other providers.
_MAX_AGE_METRICS = 1
_MAX_AGE_PROFILE = 7


class FinnhubProvider(Provider):
    """FinnHub data provider."""

    @property
    def available(self) -> bool:
        """True iff a FinnHub API key is configured."""
        return bool(self.settings and getattr(self.settings, "finnhub_api_key", None))

    def _params(self, **extra: Any) -> dict[str, Any]:
        params = {"token": self.settings.finnhub_api_key}
        params.update(extra)
        return params

    def estimates(self, t: str) -> list | dict | None:
        """Consensus EPS estimates (history + forward)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/stock/eps-estimate",
            self._params(symbol=t),
            "fh_estimates",
            t,
            max_age_days=_MAX_AGE_ESTIMATES,
        )

    def revenue_estimates(self, t: str) -> list | dict | None:
        """Consensus revenue estimates (history + forward)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/stock/revenue-estimate",
            self._params(symbol=t),
            "fh_revenue_estimates",
            t,
            max_age_days=_MAX_AGE_ESTIMATES,
        )

    def earnings_calendar(self, t: str) -> list | dict | None:
        """Upcoming/historical earnings calendar entries."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/calendar/earnings",
            self._params(symbol=t),
            "fh_earnings_calendar",
            t,
            max_age_days=_MAX_AGE_CALENDAR,
        )

    def quote(self, t: str) -> list | dict | None:
        """Real-time quote: current price, change, high/low/open, prev close."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/quote",
            self._params(symbol=t),
            "fh_quote",
            t,
            max_age_days=_MAX_AGE_QUOTE,
        )

    def profile(self, t: str) -> list | dict | None:
        """Company profile: name, country, exchange, market cap (in millions)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/stock/profile2",
            self._params(symbol=t),
            "fh_profile",
            t,
            max_age_days=_MAX_AGE_PROFILE,
        )

    def metrics(self, t: str) -> list | dict | None:
        """Precomputed fundamentals/price metrics (`metric=all`).

        Carries 52WeekHigh/Low, trailing price returns (5D..52W), peTTM,
        psTTM, epsTTM, beta and debt/equity — the inputs needed to score
        technical and valuation when daily candles aren't available.
        """
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/stock/metric",
            self._params(symbol=t, metric="all"),
            "fh_metrics",
            t,
            max_age_days=_MAX_AGE_METRICS,
        )
