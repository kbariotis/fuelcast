from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class FuelTaxProfile:
    """Statutory tax structure under Greek law (Law 2960/2001 & amendments)."""
    fuel_type: str
    efk_per_litre: float        # Ειδικός Φόρος Κατανάλωσης (ΕΦΚ)
    regulatory_levy: float      # ΔΕΤΕ (0.5%), ΡΑΑΕΥ, and Special Account levies
    vat_rate: float = 0.24      # ΦΠΑ 24%


GREEK_TAX_DEFAULTS: Dict[str, FuelTaxProfile] = {
    "unleaded_95": FuelTaxProfile(
        fuel_type="unleaded_95",
        efk_per_litre=0.7000,
        regulatory_levy=0.0055,
        vat_rate=0.24,
    ),
    "unleaded_100": FuelTaxProfile(
        fuel_type="unleaded_100",
        efk_per_litre=0.7000,
        regulatory_levy=0.0055,
        vat_rate=0.24,
    ),
    "diesel": FuelTaxProfile(
        fuel_type="diesel",
        efk_per_litre=0.4100,
        regulatory_levy=0.0042,
        vat_rate=0.24,
    ),
    "lpg": FuelTaxProfile(
        fuel_type="lpg",
        efk_per_litre=0.4300,
        regulatory_levy=0.0035,
        vat_rate=0.24,
    ),
}


@dataclass
class PriceDecomposition:
    pump_price: float
    vat_amount: float
    efk_amount: float
    levy_amount: float
    pre_tax_base: float         # Ex-refinery + logistics + gross margin
    tax_wedge_pct: float        # (Total Taxes / Pump Price) * 100


def decompose_pump_price(
    pump_price: float,
    fuel_type: str = "unleaded_95",
    custom_profile: FuelTaxProfile = None,
) -> PriceDecomposition:
    """
    Deconstructs Greek retail pump price according to the compounded tax formula:
    PumpPrice = [PreTaxBase + EFK + Levy] * (1 + VAT)
    """
    profile = custom_profile or GREEK_TAX_DEFAULTS.get(fuel_type)
    if profile is None:
        raise ValueError(f"Unknown fuel type: {fuel_type}")

    price_ex_vat = pump_price / (1.0 + profile.vat_rate)
    vat_amount = pump_price - price_ex_vat
    efk_amount = profile.efk_per_litre
    levy_amount = profile.regulatory_levy

    pre_tax_base = price_ex_vat - efk_amount - levy_amount
    total_tax = vat_amount + efk_amount + levy_amount
    tax_wedge_pct = (total_tax / pump_price) * 100.0

    return PriceDecomposition(
        pump_price=round(pump_price, 3),
        vat_amount=round(vat_amount, 3),
        efk_amount=round(efk_amount, 4),
        levy_amount=round(levy_amount, 4),
        pre_tax_base=round(pre_tax_base, 3),
        tax_wedge_pct=round(tax_wedge_pct, 1),
    )


def project_pump_price(
    pre_tax_base: float,
    fuel_type: str = "unleaded_95",
    custom_profile: FuelTaxProfile = None,
) -> float:
    """
    Computes final pump price from a projected pre-tax cost base:
    PumpPrice = [PreTaxBase + EFK + Levy] * (1 + VAT)
    """
    profile = custom_profile or GREEK_TAX_DEFAULTS.get(fuel_type)
    if profile is None:
        raise ValueError(f"Unknown fuel type: {fuel_type}")

    subtotal = pre_tax_base + profile.efk_per_litre + profile.regulatory_levy
    return round(subtotal * (1.0 + profile.vat_rate), 3)
