# FuelCast Greece (Predictive Fuel Lag Engine)

An econometrically grounded fuel price forecasting engine modeling the **"Rockets and Feathers"** asymmetric price transmission across Greek retail fuel markets (Athens metro hubs vs. mainland vs. remote island logistical zones).

## Architectural Structure

```
fuel-predict/
├── fuelcast                   # Executable CLI launcher
├── db/
│   └── schema.sql             # PostgreSQL schema with NUTS-3 geography & tax regimes
└── src/
    └── fuelcast/
        ├── __init__.py
        ├── cli.py             # On-demand manual CLI suite
        ├── engine.py          # Asymmetric lag engine (NARDL impulse formulation)
        ├── tax.py             # Greek fiscal compounding (EFK, VAT, levies)
        ├── data/
        │   └── regions.json   # Greek NUTS-3 topology & turnover tiers
        └── ingestion/
            ├── __init__.py
            ├── upstream.py    # Brent ($/bbl) & EUR/USD spot ingestion
            └── domestic.py    # Ministry regional observatory parser & outlier scrubbing
```

---

## On-Demand CLI Usage

All ingestion, simulation, and predictive pipelines can be executed manually on demand:

### 1. Ingest Upstream Market Series
Fetches Brent Crude (`BZ=F`) and `EURUSD=X` daily spot rates, deriving raw Crude cost in €/L:

```bash
# Query live market feed (falls back gracefully to synthetic if offline)
./fuelcast ingest-upstream --days 30

# Force synthetic simulation with specific market trend
./fuelcast ingest-upstream --days 14 --mock --trend spike
./fuelcast ingest-upstream --days 14 --mock --trend drop

# Dump to JSON
./fuelcast ingest-upstream --days 30 --output upstream.json
```

### 2. Ingest Domestic Regional Observatories
Parses regional Greek pump prices across prefectures (Νομοί), applies statistical outlier rejection ($>2.8\sigma$), and assigns logistical freight tiers:

```bash
./fuelcast ingest-domestic

# Save structured snapshot
./fuelcast ingest-domestic --output domestic.json
```

### 3. Generate Regional Price Forecasts
Feeds upstream shock impulses into the asymmetric decay engine across Greece:

```bash
# Run 3-day (72h) forecast across all regions
./fuelcast forecast --horizon 3

# Filter by specific prefecture (e.g., Cyclades or Attica)
./fuelcast forecast --horizon 3 --region EL422
./fuelcast forecast --horizon 3 --region "Central Athens"
```

### 4. Greek "Double Tax" Transparency Simulator
Decomposes retail pump prices or Euro transactions into Greek Excise Duty (€0.700/L on Unleaded 95), 24% VAT, and Pre-tax Base:

```bash
./fuelcast tax-breakdown --amount 50 --price 1.889 --fuel unleaded_95
```
