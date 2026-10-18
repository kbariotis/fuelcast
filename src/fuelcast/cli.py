#!/usr/bin/env python3
import argparse
import json
import os
import sys
from typing import Optional

from .core import AsymmetricLagEngine, Signal, decompose_pump_price
from .ingestion import DomesticIngestion, UpstreamIngestion
from .storage import FuelCastStorage


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

    # Persist to embedded SQLite database
    storage = FuelCastStorage()
    saved = storage.save_upstream_quotes(quotes)
    print(f"\n[PERSISTED] {saved} quotes stored in SQLite database ({storage.db_path})")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump([q.__dict__ for q in quotes], f, indent=2)
        print(f"Saved JSON export to: {args.output}")

    # Summary analysis
    first, last = quotes[0], quotes[-1]
    delta_eur = last.crude_eur_per_litre - first.crude_eur_per_litre
    pct_change = (delta_eur / first.crude_eur_per_litre) * 100
    print("-" * 65)
    print(f"Net Window Shift ({first.date} -> {last.date}): Δ €{delta_eur:+.4f}/L ({pct_change:+.1f}%)")


def cmd_ingest_domestic(args):
    """Executes on-demand domestic Greek price observatory ingestion."""
    print("=" * 80)
    print("  FuelCast Greece - Domestic Regional Price Ingestion")
    print("=" * 80)

    ingest = DomesticIngestion()

    if args.live:
        region_target = args.region or "kalamaria"
        print(f"Connecting to live Greek Fuel Price Observatory mirror for: {region_target.upper()}...")
        try:
            snapshot = ingest.fetch_live_market(region_name=region_target, radius_km=args.radius)
            print(f"\n[LIVE QUERY SUCCESS] Timestamp: {snapshot.query_timestamp}")
            print(f"Target Center: {snapshot.center_coords} (Radius: {snapshot.radius_km} km)")
            print(f"Total Stations Observed: {snapshot.total_stations_found} | Fresh: {snapshot.fresh_stations_count} | Stale: {snapshot.stale_stations_count}")
            print(f"Price Metrics: Mean €{snapshot.mean_price:.3f} | Mode €{snapshot.modal_price:.3f} | Range: €{snapshot.min_price:.3f} - €{snapshot.max_price:.3f}")
            print(f"Derived Empirical Spatial Margin (ex-VAT): €{snapshot.derived_spatial_margin_ex_vat:.4f}/L\n")
            
            print("-" * 80)
            print(f"{'Brand':<10} | {'Price (€/L)':<12} | {'Reported At':<19} | {'Address'}")
            print("-" * 80)
            for st in snapshot.stations[:15]:
                stale_tag = " (STALE)" if st.is_stale else ""
                print(f"{st.brand:<10} | €{st.price:<11.3f} | {st.reported_at:<19} | {st.address[:34]}{stale_tag}")
            
            # Persist live stations to SQLite
            storage = FuelCastStorage()
            saved_count = storage.save_station_records(snapshot.stations)
            print(f"[PERSISTED] {saved_count} station observations stored in SQLite database ({storage.db_path})\n")

            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump(snapshot.__dict__, f, indent=2, default=str, ensure_ascii=False)
                print(f"Saved live market snapshot JSON to: {args.output}")
            return
        except Exception as e:
            print(f"Live domestic query failed: {e}. Falling back to cached topology.")

    snapshots = ingest.run(source_file=args.file)

    print(f"\nIngested {len(snapshots)} regional administrative units.")
    print("-" * 80)
    print(f"{'NUTS-3':<7} | {'Prefecture / Dimos (GR)':<26} | {'Tier':<22} | {'Avg (€/L)':<10} | {'Stations'}")
    print("-" * 80)

    for s in snapshots:
        print(f"{s.nuts3_code:<7} | {s.region_name_el:<26} | {s.turnover_tier:<22} | €{s.avg_price:<9.3f} | {s.sample_station_count}")

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

    dom_ingest = DomesticIngestion()

    # 2. Check for live domestic query
    if args.live and args.region:
        try:
            live_snap = dom_ingest.fetch_live_market(region_name=args.region)
            engine = AsymmetricLagEngine()
            fc = engine.predict_regional_price(
                current_pump_price=live_snap.modal_price,
                upstream_eur_series=cost_series,
                fuel_type="unleaded_95",
                turnover_tier="metro_suburban_premium" if "kalamaria" in args.region.lower() else "metro_high",
                horizon_days=args.horizon,
            )
            signal_str = {
                Signal.FILL_NOW: "🔴 FILL NOW",
                Signal.WAIT: "🟢 WAIT",
                Signal.STABLE: "🟡 STABLE",
            }.get(fc.signal, fc.signal.value)

            print(f"\n[LIVE GROUND TRUTH FORECAST: {args.region.upper()}]")
            print(f"Sample: {live_snap.fresh_stations_count} live stations (Mode: €{live_snap.modal_price:.3f}, Mean: €{live_snap.mean_price:.3f})")
            print(f"Signal: {signal_str} | Current: €{fc.current_price:.3f} -> Projected: €{fc.predicted_price:.3f} (Δ {fc.expected_delta:+.3f}) | Conf: {fc.confidence_score*100:.0f}%\n")
            # Persist forecast to SQLite
            storage = FuelCastStorage()
            storage.save_forecast(fc, region_code=args.region, fuel_type="unleaded_95")
            return
        except Exception as e:
            print(f"Live forecast query failed: {e}. Falling back to topology baselines.")

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
    storage = FuelCastStorage()

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

        # Persist each forecast
        storage.save_forecast(fc, region_code=s.nuts3_code, fuel_type="unleaded_95")

        signal_str = {
            Signal.FILL_NOW: "🔴 FILL NOW",
            Signal.WAIT: "🟢 WAIT",
            Signal.STABLE: "🟡 STABLE",
        }.get(fc.signal, fc.signal.value)

        delta_sign = f"{fc.expected_delta:+.3f}"
        print(f"{s.region_name_en:<22} | {s.turnover_tier:<15} | €{fc.current_price:<8.3f} | €{fc.predicted_price:<8.3f} ({delta_sign}) | {signal_str:<12} | {fc.confidence_score*100:.0f}%")

    print("-" * 80)
    print(f"Persisted {len(snapshots)} regional forecasts to SQLite database ({storage.db_path})")
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


def cmd_db_status(args):
    """Inspects the local SQLite database state and row counts."""
    storage = FuelCastStorage()
    stats = storage.get_db_stats()
    print("=" * 65)
    print("  FuelCast Greece - Embedded SQLite Database Status")
    print("=" * 65)
    print(f"Database File: {storage.db_path}\n")
    print(f"{'Table Name':<28} | {'Persisted Rows':<14}")
    print("-" * 45)
    for tbl, count in stats.items():
        print(f"{tbl:<28} | {count:<14}")
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
    dom_p.add_argument("--live", action="store_true", help="Execute real-time query against Fuel Price Observatory mirror.")
    dom_p.add_argument("--region", type=str, default="kalamaria", help="Target region/municipality for live query (default: kalamaria).")
    dom_p.add_argument("--radius", type=int, default=10, help="Search radius in km for live query (default: 10)")
    dom_p.set_defaults(func=cmd_ingest_domestic)

    # Subcommand: forecast
    fc_p = subparsers.add_parser("forecast", help="Run asymmetric price forecast over target horizon.")
    fc_p.add_argument("--horizon", type=int, default=3, help="Forecast horizon in days (default: 3 / 72h)")
    fc_p.add_argument("--region", type=str, help="Optional NUTS-3 code (e.g. EL301, EL422) or name filter.")
    fc_p.add_argument("--live", action="store_true", help="Fetch live real-time ground truth retail prices for forecast.")
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

    # Subcommand: db-status
    db_p = subparsers.add_parser("db-status", help="Inspect local SQLite database status and table metrics.")
    db_p.set_defaults(func=cmd_db_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

