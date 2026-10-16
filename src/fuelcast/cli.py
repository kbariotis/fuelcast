#!/usr/bin/env python3
import argparse
import json
import os
import sys
from typing import Optional

from .engine import AsymmetricLagEngine, Signal
from .ingestion import DomesticIngestion, UpstreamIngestion
from .tax import decompose_pump_price


def cmd_ingest_upstream(args):
    """Executes on-demand upstream market ingestion."""
    print("=" * 65)
    print("  FuelCast Greece - Upstream Market Ingestion")
    print("=" * 65)

    ingest = UpstreamIngestion()
    quotes = ingest.run(force_mock=args.mock, days=args.days, trend=args.trend)

    print(f"\nRetrieved {len(quotes)} daily observation points.")
    print("-" * 65)
    print(f"{'Date':<12} | {'Brent ($/bbl)':<15} | {'EUR/USD':<10} | {'Crude (€/L)':<12}")
    print("-" * 65)

    # Show the last 7 observations
    display_sample = quotes[-7:] if len(quotes) >= 7 else quotes
    for q in display_sample:
        print(f"{q.date:<12} | ${q.brent_usd:<14.2f} | {q.eur_usd:<10.4f} | €{q.crude_eur_per_litre:<11.4f}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump([q.__dict__ for q in quotes], f, indent=2)
        print(f"\nSaved full timeseries to: {args.output}")

    # Summary analysis
    first, last = quotes[0], quotes[-1]
    delta_eur = last.crude_eur_per_litre - first.crude_eur_per_litre
    pct_change = (delta_eur / first.crude_eur_per_litre) * 100
    print("-" * 65)
    print(f"Net Window Shift ({first.date} -> {last.date}): Δ €{delta_eur:+.4f}/L ({pct_change:+.1f}%)")


def cmd_ingest_domestic(args):
    """Executes on-demand domestic Greek price observatory ingestion."""
    print("=" * 75)
    print("  FuelCast Greece - Domestic Regional Price Ingestion")
    print("=" * 75)

    ingest = DomesticIngestion()
    snapshots = ingest.run(source_file=args.file)

    print(f"\nIngested {len(snapshots)} regional administrative units.")
    print("-" * 75)
    print(f"{'NUTS-3':<7} | {'Prefecture (GR)':<20} | {'Tier':<16} | {'Avg (€/L)':<10} | {'Stations'}")
    print("-" * 75)

    for s in snapshots:
        print(f"{s.nuts3_code:<7} | {s.region_name_el:<20} | {s.turnover_tier:<16} | €{s.avg_price:<9.3f} | {s.sample_station_count}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump([s.__dict__ for s in snapshots], f, indent=2, ensure_ascii=False)
        print(f"\nSaved regional snapshots to: {args.output}")


def cmd_forecast(args):
    """Runs the asymmetric predictive engine across Greek regions."""
    print("=" * 80)
    print(f"  FuelCast Greece - Predictive Lag Forecast ({args.horizon}-Day Horizon)")
    print("=" * 80)

    # 1. Fetch upstream timeseries
    up_ingest = UpstreamIngestion()
    quotes = up_ingest.run(force_mock=args.mock, days=args.days, trend=args.trend)
    cost_series = [q.crude_eur_per_litre for q in quotes]

    # 2. Fetch domestic regional baselines
    dom_ingest = DomesticIngestion()
    snapshots = dom_ingest.run()

    if args.region:
        reg_query = args.region.lower()
        snapshots = [
            s for s in snapshots 
            if s.nuts3_code.lower() == reg_query 
            or reg_query in s.region_name_en.lower() 
            or reg_query in s.region_name_el.lower()
        ]
        if not snapshots:
            print(f"Error: Region '{args.region}' not found.")
            sys.exit(1)

    engine = AsymmetricLagEngine()

    print(f"{'Region':<22} | {'Tier':<15} | {'Current':<9} | {'Forecast':<9} | {'Signal':<12} | {'Conf'}")
    print("-" * 80)

    for s in snapshots:
        fc = engine.predict_regional_price(
            current_pump_price=s.avg_price,
            upstream_eur_series=cost_series,
            fuel_type="unleaded_95",
            turnover_tier=s.turnover_tier,
            horizon_days=args.horizon,
        )

        signal_str = {
            Signal.FILL_NOW: "🔴 FILL NOW",
            Signal.WAIT: "🟢 WAIT",
            Signal.STABLE: "🟡 STABLE",
        }.get(fc.signal, fc.signal.value)

        delta_sign = f"{fc.expected_delta:+.3f}"
        print(f"{s.region_name_en:<22} | {s.turnover_tier:<15} | €{fc.current_price:<8.3f} | €{fc.predicted_price:<8.3f} ({delta_sign}) | {signal_str:<12} | {fc.confidence_score*100:.0f}%")

    print("-" * 80)
    print("Note: Signal threshold requires |Δ| >= €0.008/L to trigger FILL_NOW or WAIT.")


def cmd_tax_breakdown(args):
    """Executes the Double Tax transparency simulator."""
    decomp = decompose_pump_price(args.price, fuel_type=args.fuel)

    total_litres = args.amount / args.price
    spent_vat = total_litres * decomp.vat_amount
    spent_efk = total_litres * decomp.efk_amount
    spent_levy = total_litres * decomp.levy_amount
    spent_pretax = total_litres * decomp.pre_tax_base
    total_tax_paid = spent_vat + spent_efk + spent_levy

    print("=" * 65)
    print(f"  Greek 'Double Tax' Simulator: €{args.amount:.2f} at €{args.price:.3f}/L ({args.fuel})")
    print("=" * 65)
    print(f"Total Fuel Dispensed: {total_litres:.2f} Litres\n")
    print(f"{'Component':<35} | {'Per Litre':<10} | {'Total Paid':<11} | {'Share'}")
    print("-" * 65)
    print(f"{'Fixed Excise Duty (ΕΦΚ)':<35} | €{decomp.efk_amount:<9.4f} | €{spent_efk:<10.2f} | {(spent_efk/args.amount)*100:4.1f}%")
    print(f"{'Compounded VAT (ΦΠΑ 24%)':<35} | €{decomp.vat_amount:<9.3f} | €{spent_vat:<10.2f} | {(spent_vat/args.amount)*100:4.1f}%")
    print(f"{'Regulatory Levies (ΔΕΤΕ/ΡΑΑΕΥ)':<35} | €{decomp.levy_amount:<9.4f} | €{spent_levy:<10.2f} | {(spent_levy/args.amount)*100:4.1f}%")
    print(f"{'Pre-Tax Base (Crude + Refining + Margin)':<35} | €{decomp.pre_tax_base:<9.3f} | €{spent_pretax:<10.2f} | {(spent_pretax/args.amount)*100:4.1f}%")
    print("-" * 65)
    print(f"{'TOTAL TAX PAID TO STATE':<35} | €{decomp.vat_amount + decomp.efk_amount + decomp.levy_amount:<9.3f} | €{total_tax_paid:<10.2f} | {decomp.tax_wedge_pct:4.1f}%")
    print(f"{'ACTUAL PRODUCT & STATION VALUE':<35} | €{decomp.pre_tax_base:<9.3f} | €{spent_pretax:<10.2f} | {(100.0 - decomp.tax_wedge_pct):4.1f}%")
    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(
        prog="fuelcast",
        description="FuelCast Greece: Asymmetric fuel price forecasting engine & fiscal simulator.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: ingest-upstream
    up_p = subparsers.add_parser("ingest-upstream", help="Ingest global crude oil & FX rates on-demand.")
    up_p.add_argument("--days", type=int, default=30, help="Window length in days (default: 30)")
    up_p.add_argument("--mock", action="store_true", help="Force synthetic quote stream for offline test.")
    up_p.add_argument("--trend", choices=["spike", "drop", "stable"], default="spike", help="Trend pattern if mocked.")
    up_p.add_argument("--output", type=str, help="Destination JSON file path.")
    up_p.set_defaults(func=cmd_ingest_upstream)

    # Subcommand: ingest-domestic
    dom_p = subparsers.add_parser("ingest-domestic", help="Ingest Greek regional pump price bulletins.")
    dom_p.add_argument("--file", type=str, help="Optional local JSON/bulletin file to parse.")
    dom_p.add_argument("--output", type=str, help="Destination JSON file path.")
    dom_p.set_defaults(func=cmd_ingest_domestic)

    # Subcommand: forecast
    fc_p = subparsers.add_parser("forecast", help="Run asymmetric price forecast over target horizon.")
    fc_p.add_argument("--horizon", type=int, default=3, help="Forecast horizon in days (default: 3 / 72h)")
    fc_p.add_argument("--region", type=str, help="Optional NUTS-3 code (e.g. EL301, EL422) or name filter.")
    fc_p.add_argument("--mock", action="store_true", help="Force synthetic upstream inputs.")
    fc_p.add_argument("--trend", choices=["spike", "drop", "stable"], default="spike")
    fc_p.add_argument("--days", type=int, default=30)
    fc_p.set_defaults(func=cmd_forecast)

    # Subcommand: tax-breakdown
    tax_p = subparsers.add_parser("tax-breakdown", help="Simulate Greek fuel tax wedge and cost breakdown.")
    tax_p.add_argument("--amount", type=float, default=50.0, help="Total transaction amount in EUR (default: €50)")
    tax_p.add_argument("--price", type=float, default=1.889, help="Retail pump price in EUR/L (default: €1.889)")
    tax_p.add_argument("--fuel", choices=["unleaded_95", "unleaded_100", "diesel", "lpg"], default="unleaded_95")
    tax_p.set_defaults(func=cmd_tax_breakdown)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
