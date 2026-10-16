-- FuelCast Greece: PostgreSQL Schema

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Administrative & Geographic Taxonomy
CREATE TABLE IF NOT EXISTS regions (
    id SERIAL PRIMARY KEY,
    nuts2_code VARCHAR(10) NOT NULL,            -- e.g., EL30 (Attica), EL52 (Central Macedonia)
    nuts3_code VARCHAR(10) NOT NULL,            -- Prefecture level (Perifereiaki Enotita)
    name_el VARCHAR(100) NOT NULL,
    name_en VARCHAR(100) NOT NULL,
    turnover_tier VARCHAR(20) NOT NULL CHECK (turnover_tier IN ('metro_high', 'mainland_standard', 'island_remote')),
    typical_lag_days_up NUMERIC(3,1) DEFAULT 2.0,
    typical_lag_days_down NUMERIC(3,1) DEFAULT 10.0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_regions_nuts3 ON regions(nuts3_code);

-- 2. Retail Stations (Ministry Registry)
CREATE TABLE IF NOT EXISTS stations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    registry_id VARCHAR(50) UNIQUE NOT NULL,    -- fuelprices.gr internal station ID
    brand VARCHAR(50) NOT NULL,                 -- e.g., EKO, BP, Shell, AVIN, Independent
    region_id INT REFERENCES regions(id),
    name VARCHAR(255),
    address VARCHAR(255),
    latitude NUMERIC(9,6) NOT NULL,
    longitude NUMERIC(9,6) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_stations_region ON stations(region_id);
CREATE INDEX idx_stations_coords ON stations(latitude, longitude);

-- 3. Fiscal Framework (Excise Duties & Taxes)
-- Allows historical tracking when government adjustments occur (e.g., changes to EFK or VAT)
CREATE TABLE IF NOT EXISTS tax_regimes (
    id SERIAL PRIMARY KEY,
    fuel_type VARCHAR(20) NOT NULL CHECK (fuel_type IN ('unleaded_95', 'unleaded_100', 'diesel', 'lpg')),
    efk_per_litre NUMERIC(6,4) NOT NULL,       -- e.g., 0.7000 for Unleaded 95, 0.4100 for Diesel
    regulatory_levy NUMERIC(6,4) NOT NULL,      -- Special levies (Deteli, RAE, etc. ~0.005)
    vat_rate NUMERIC(4,3) NOT NULL,             -- 0.24 (24%)
    effective_from DATE NOT NULL,
    effective_to DATE,
    CONSTRAINT check_tax_dates CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE INDEX idx_tax_regimes_lookup ON tax_regimes(fuel_type, effective_from);

-- 4. Global Market Feeds (Upstream Input Series)
CREATE TABLE IF NOT EXISTS global_market_feed (
    observation_date DATE PRIMARY KEY,
    brent_usd NUMERIC(8,4) NOT NULL,           -- Brent Crude Close ($/bbl)
    platts_cif_med_usd NUMERIC(8,4),           -- Platts CIF Med Cargo quote ($/MT) if accessible
    eur_usd_rate NUMERIC(6,4) NOT NULL,        -- ECB / FX Spot Rate
    crude_eur_per_litre NUMERIC(8,5) GENERATED ALWAYS AS (
        (brent_usd / 158.987) / eur_usd_rate
    ) STORED,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 5. Domestic Price Observations (Daily station-level scrape)
CREATE TABLE IF NOT EXISTS station_daily_prices (
    station_id UUID REFERENCES stations(id) ON DELETE CASCADE,
    fuel_type VARCHAR(20) NOT NULL,
    observation_date DATE NOT NULL,
    pump_price NUMERIC(5,3) NOT NULL,          -- e.g., 1.849
    scraped_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (station_id, fuel_type, observation_date)
);

CREATE INDEX idx_station_prices_date ON station_daily_prices(observation_date, fuel_type);

-- 6. Aggregated Regional Metrics & Model Forecasts
CREATE TABLE IF NOT EXISTS regional_forecasts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    region_id INT REFERENCES regions(id),
    fuel_type VARCHAR(20) NOT NULL,
    forecast_date DATE NOT NULL,               -- Date forecast was issued
    target_horizon_days INT NOT NULL,          -- +1, +3, +5 days
    current_avg_price NUMERIC(5,3) NOT NULL,
    predicted_avg_price NUMERIC(5,3) NOT NULL,
    expected_delta NUMERIC(5,3) GENERATED ALWAYS AS (predicted_avg_price - current_avg_price) STORED,
    signal VARCHAR(10) NOT NULL CHECK (signal IN ('FILL_NOW', 'WAIT', 'STABLE')),
    confidence_score NUMERIC(4,3) NOT NULL,    -- 0.000 to 1.000
    model_version VARCHAR(30) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_region_forecast UNIQUE (region_id, fuel_type, forecast_date, target_horizon_days)
);

CREATE INDEX idx_forecasts_lookup ON regional_forecasts(region_id, fuel_type, forecast_date);
