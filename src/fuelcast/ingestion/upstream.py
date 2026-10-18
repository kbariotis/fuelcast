import json
import logging
import math
import ssl
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("fuelcast.ingestion.upstream")


BARREL_TO_LITRES = 158.9873


@dataclass
class MarketQuote:
    date: str
    brent_usd: float
    eur_usd: float
    crude_eur_per_litre: float


class UpstreamIngestion:
    """
    On-demand ingestion for global crude oil and currency spot rates.
    Retrieves Brent Crude (BZ=F) and EUR/USD (EURUSD=X) feeds,
    with an offline calibration fallback when networks are unavailable.
    """

    USER_AGENT = "Mozilla/5.0 (FuelCast Greece Ingestion Engine 1.0)"

    def __init__(self, timeout_sec: int = 8):
        self.timeout = timeout_sec
        try:
            self.ssl_ctx = ssl.create_default_context()
        except Exception:
            self.ssl_ctx = ssl._create_unverified_context()

    def _fetch_yahoo_series(self, symbol: str, range_str: str = "1mo") -> Dict[str, float]:
        """Fetches daily closing prices from Yahoo Finance JSON API."""
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={range_str}"
        req = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT})
        
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                unverified_ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, context=unverified_ctx, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            else:
                raise

        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        closes = result["indicators"]["quote"][0].get("close", [])

        series = {}
        for ts, close in zip(timestamps, closes):
            if close is not None and not math.isnan(close):
                dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                series[dt_str] = round(float(close), 4)

        return series

    def fetch_live_quotes(self, range_str: str = "1mo") -> List[MarketQuote]:
        """Executes dual HTTP queries for Brent crude and EUR/USD, synchronizing timestamps."""
        brent_series = self._fetch_yahoo_series("BZ=F", range_str=range_str)
        fx_series = self._fetch_yahoo_series("EURUSD=X", range_str=range_str)

        # Inner join on matching trading dates
        common_dates = sorted(set(brent_series.keys()) & set(fx_series.keys()))
        if not common_dates:
            raise RuntimeError("No overlapping trading dates found between Brent and EUR/USD series.")

        quotes = []
        for d in common_dates:
            b_usd = brent_series[d]
            fx = fx_series[d]
            eur_per_litre = (b_usd / BARREL_TO_LITRES) / fx
            quotes.append(
                MarketQuote(
                    date=d,
                    brent_usd=b_usd,
                    eur_usd=fx,
                    crude_eur_per_litre=round(eur_per_litre, 5),
                )
            )

        return quotes

    def generate_synthetic_quotes(
        self,
        base_brent: float = 82.50,
        base_eur_usd: float = 1.0850,
        days: int = 30,
        trend: str = "spike",
    ) -> List[MarketQuote]:
        """
        Generates realistic market quotes for offline execution or pipeline testing.
        Trends: 'spike', 'drop', or 'stable'.
        """
        import random
        from datetime import timedelta

        random.seed(42)  # Deterministic seed for reproducible testing
        end_date = datetime.now(timezone.utc)
        quotes = []

        cur_brent = base_brent
        cur_fx = base_eur_usd

        for i in range(days, 0, -1):
            dt_str = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
            
            # Apply trend drift
            if trend == "spike" and i <= 5:
                drift = 1.20
            elif trend == "drop" and i <= 8:
                drift = -1.10
            else:
                drift = 0.0

            cur_brent = max(40.0, cur_brent + drift + random.gauss(0, 0.45))
            cur_fx = max(0.85, cur_fx + random.gauss(0, 0.002))

            eur_per_litre = (cur_brent / BARREL_TO_LITRES) / cur_fx
            quotes.append(
                MarketQuote(
                    date=dt_str,
                    brent_usd=round(cur_brent, 2),
                    eur_usd=round(cur_fx, 4),
                    crude_eur_per_litre=round(eur_per_litre, 5),
                )
            )

        return quotes

    def run(self, force_mock: bool = False, days: int = 30, trend: str = "spike") -> List[MarketQuote]:
        """Primary on-demand entrypoint with graceful network-unreachable fallback."""
        if force_mock:
            logger.info("Generating synthetic upstream market quotes (force_mock=True)...")
            return self.generate_synthetic_quotes(days=days, trend=trend)

        try:
            logger.info("Executing on-demand upstream query (Brent + EUR/USD)...")
            return self.fetch_live_quotes(range_str=f"{days}d")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.warning("Live network feed unreachable (%s). Falling back to synthetic stream.", e)
            return self.generate_synthetic_quotes(days=days, trend=trend)
