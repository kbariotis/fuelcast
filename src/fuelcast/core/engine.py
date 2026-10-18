import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Sequence, Tuple, Union

from .tax import decompose_pump_price, project_pump_price


BARREL_TO_LITRES = 158.9873


class Signal(str, Enum):
    FILL_NOW = "FILL_NOW"
    WAIT = "WAIT"
    STABLE = "STABLE"


@dataclass
class ForecastResult:
    current_price: float
    predicted_price: float
    expected_delta: float
    signal: Signal
    confidence_score: float
    horizon_days: int
    regional_turnover_tier: str


class AsymmetricLagEngine:
    """
    Econometrically grounded asymmetric pass-through engine for retail fuel pricing.
    
    Rather than naive moving averages that accumulate structural drift, this engine
    models the 'rockets and feathers' transmission mechanism via positive/negative
    shock decomposition (NARDL/AECM paradigm) combined with Greek VAT compounding.
    """

    def __init__(
        self,
        vat_rate: float = 0.24,
        neutral_threshold_eur: float = 0.008,
    ):
        self.vat_rate = vat_rate
        self.neutral_threshold = neutral_threshold_eur

    @staticmethod
    def calculate_upstream_cost_eur_per_litre(
        brent_usd: float,
        eur_usd_rate: float,
    ) -> float:
        """Converts crude benchmark ($/bbl) to EUR per litre."""
        if eur_usd_rate <= 0:
            raise ValueError("EUR/USD exchange rate must be strictly positive.")
        return (brent_usd / BARREL_TO_LITRES) / eur_usd_rate

    def compute_asymmetric_impulse(
        self,
        upstream_series: Sequence[float],
        turnover_tier: str = "metro_high",
    ) -> Tuple[float, float]:
        """
        Calculates cumulative upstream shocks partitioned into positive (rocket)
        and negative (feather) components using exponential decay kernels parameterized
        by regional inventory turnover.
        
        Returns:
            (cumulative_positive_impulse, cumulative_negative_impulse)
        """
        values = list(upstream_series)
        if len(values) < 2:
            return 0.0, 0.0

        deltas = [values[i] - values[i - 1] for i in range(1, len(values))]

        # Regional calibration based on empirical Greek fuel market dynamics:
        # Metro hubs (Attica/Thessaloniki) turn inventory in 2-3 days.
        # Remote island/rural zones operate on 7-14 day tank replenishment schedules.
        if turnover_tier == "metro_high":
            half_life_up = 2.0
            half_life_down = 8.0
        elif turnover_tier == "island_remote":
            half_life_up = 4.0
            half_life_down = 14.0
        else:  # mainland_standard
            half_life_up = 3.0
            half_life_down = 10.0

        decay_rate_up = math.log(2.0) / half_life_up
        decay_rate_down = math.log(2.0) / half_life_down

        n = len(deltas)
        # Weights decay backwards from the most recent observation (distance from t_now)
        weights_up = [math.exp(-decay_rate_up * (n - 1 - i)) for i in range(n)]
        weights_down = [math.exp(-decay_rate_down * (n - 1 - i)) for i in range(n)]

        sum_w_up = sum(weights_up)
        sum_w_down = sum(weights_down)

        pos_shocks = [max(d, 0.0) for d in deltas]
        neg_shocks = [min(d, 0.0) for d in deltas]

        # Normalized weighted transmission
        weighted_pos = sum(p * w for p, w in zip(pos_shocks, weights_up))
        weighted_neg = sum(m * w for m, w in zip(neg_shocks, weights_down))

        impulse_pos = (weighted_pos / sum_w_up * half_life_up) if sum_w_up > 0 else 0.0
        impulse_neg = (weighted_neg / sum_w_down * half_life_down) if sum_w_down > 0 else 0.0

        return impulse_pos, impulse_neg

    def predict_regional_price(
        self,
        current_pump_price: float,
        upstream_eur_series: Sequence[float],
        fuel_type: str = "unleaded_95",
        turnover_tier: str = "metro_high",
        horizon_days: int = 3,
        historical_variance: float = 0.0004,
    ) -> ForecastResult:
        """
        Projects local retail pump price over target horizon H (default 72h / 3 days).
        
        Greek wholesale pricing passes ex-refinery changes directly into the pre-tax base,
        which is then inflated by statutory VAT (24%).
        """
        values = list(upstream_eur_series)
        decomp = decompose_pump_price(current_pump_price, fuel_type=fuel_type)
        pos_impulse, neg_impulse = self.compute_asymmetric_impulse(
            values, turnover_tier=turnover_tier
        )

        # Asymmetric elasticity of transmission:
        # Upward price adjustments reflect rapid, complete pass-through (~0.90)
        # Downward adjustments suffer structural friction and margin retention (~0.55)
        elasticity_up = 0.90
        elasticity_down = 0.55

        # Net pre-tax delta expected at the wholesale/retail boundary
        delta_pre_tax = (pos_impulse * elasticity_up) + (neg_impulse * elasticity_down)

        # Compound with Greek VAT (VAT applies to wholesale shifts)
        delta_pump = delta_pre_tax * (1.0 + self.vat_rate)

        # Time-horizon scaling factor (sigmoid saturation towards the horizon)
        decay_param = 2.5 if delta_pump > 0 else 7.0
        saturation = 1.0 - math.exp(-horizon_days / decay_param)
        projected_delta = delta_pump * saturation

        predicted_price = round(current_pump_price + projected_delta, 3)
        net_delta = round(predicted_price - current_pump_price, 3)

        # Signal Classification
        if net_delta >= self.neutral_threshold:
            signal = Signal.FILL_NOW
        elif net_delta <= -self.neutral_threshold:
            signal = Signal.WAIT
        else:
            signal = Signal.STABLE

        # Confidence Estimation: inversely proportional to shock dispersion & residual variance
        if len(values) >= 3:
            recent_deltas = [values[i] - values[i - 1] for i in range(max(1, len(values) - 10), len(values))]
            mean_d = sum(recent_deltas) / len(recent_deltas)
            var_d = sum((x - mean_d) ** 2 for x in recent_deltas) / len(recent_deltas)
            shock_volatility = math.sqrt(var_d)
        else:
            shock_volatility = 0.01

        conf = 1.0 / (1.0 + math.sqrt(shock_volatility**2 + historical_variance) * 50.0)
        confidence_score = round(min(max(conf, 0.50), 0.96), 3)

        return ForecastResult(
            current_price=current_pump_price,
            predicted_price=predicted_price,
            expected_delta=net_delta,
            signal=signal,
            confidence_score=confidence_score,
            horizon_days=horizon_days,
            regional_turnover_tier=turnover_tier,
        )
