import json
import logging
import os
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .core.engine import ForecastResult
from .ingestion.domestic import StationRecord
from .ingestion.upstream import MarketQuote

logger = logging.getLogger("fuelcast.storage")


class FuelCastStorage:
    """
    Embedded SQLite persistence layer for FuelCast Greece.
    
    Persists:
    - Administrative NUTS-3 and municipal topologies
    - Daily upstream market quotes (Brent Crude & EUR/USD)
    - Live domestic retail station price observations
    - Model forecasts and signal audit trails
    """

    DEFAULT_DB_REL_PATH = os.path.join("data", "fuelcast.db")

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Base directory is workspace root
            root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            db_path = os.path.join(root_dir, self.DEFAULT_DB_REL_PATH)

        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()
        self._seed_topology_if_empty()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executescript(
                """
                PRAGMA foreign_keys = ON;

                -- 1. Regions and Municipalities
                CREATE TABLE IF NOT EXISTS regions (
                    code TEXT PRIMARY KEY,
                    nuts3_code TEXT NOT NULL,
                    name_el TEXT NOT NULL,
                    name_en TEXT NOT NULL,
                    turnover_tier TEXT NOT NULL,
                    lag_up REAL NOT NULL,
                    lag_down REAL NOT NULL,
                    margin_offset REAL DEFAULT 0.0,
                    is_municipality INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                -- 2. Upstream Market Series (Crude & FX)
                CREATE TABLE IF NOT EXISTS global_market_feed (
                    observation_date TEXT PRIMARY KEY,
                    brent_usd REAL NOT NULL,
                    eur_usd REAL NOT NULL,
                    crude_eur_per_litre REAL NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                -- 3. Domestic Station Price Observations
                CREATE TABLE IF NOT EXISTS station_observations (
                    station_id TEXT NOT NULL,
                    observation_date TEXT NOT NULL,
                    brand TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    pump_price REAL NOT NULL,
                    address TEXT,
                    municipality TEXT,
                    latitude REAL,
                    longitude REAL,
                    reported_at TEXT,
                    is_stale INTEGER DEFAULT 0,
                    is_company_operated INTEGER DEFAULT 0,
                    inserted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (station_id, product_name, observation_date)
                );

                CREATE INDEX IF NOT EXISTS idx_station_obs_date 
                    ON station_observations(observation_date, product_name);
                CREATE INDEX IF NOT EXISTS idx_station_obs_mun 
                    ON station_observations(municipality);

                -- 4. Regional Forecasts & Signals
                CREATE TABLE IF NOT EXISTS regional_forecasts (
                    id TEXT PRIMARY KEY,
                    region_code TEXT NOT NULL,
                    fuel_type TEXT NOT NULL,
                    forecast_date TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    current_price REAL NOT NULL,
                    predicted_price REAL NOT NULL,
                    expected_delta REAL NOT NULL,
                    signal TEXT NOT NULL,
                    confidence_score REAL NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (region_code, fuel_type, forecast_date, horizon_days)
                );

                CREATE INDEX IF NOT EXISTS idx_forecasts_lookup 
                    ON regional_forecasts(region_code, forecast_date);
                """
            )
            conn.commit()

    def _seed_topology_if_empty(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM regions")
            if cursor.fetchone()[0] > 0:
                return

            base_dir = os.path.dirname(os.path.abspath(__file__))
            regions_path = os.path.join(base_dir, "data", "regions.json")
            muni_path = os.path.join(base_dir, "data", "municipalities.json")

            if os.path.exists(regions_path):
                with open(regions_path, "r", encoding="utf-8") as f:
                    for r in json.load(f):
                        cursor.execute(
                            """
                            INSERT OR REPLACE INTO regions 
                            (code, nuts3_code, name_el, name_en, turnover_tier, lag_up, lag_down, margin_offset, is_municipality)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, 0)
                            """,
                            (
                                r["nuts3"],
                                r["nuts3"],
                                r["name_el"],
                                r["name_en"],
                                r["turnover_tier"],
                                r["lag_up"],
                                r["lag_down"],
                            ),
                        )

            if os.path.exists(muni_path):
                with open(muni_path, "r", encoding="utf-8") as f:
                    for m in json.load(f):
                        cursor.execute(
                            """
                            INSERT OR REPLACE INTO regions 
                            (code, nuts3_code, name_el, name_en, turnover_tier, lag_up, lag_down, margin_offset, is_municipality)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                            """,
                            (
                                m["municipality_id"],
                                m["nuts3"],
                                m["name_el"],
                                m["name_en"],
                                m["turnover_tier"],
                                m["typical_lag_up"],
                                m["typical_lag_down"],
                                m.get("margin_offset_eur", 0.0),
                            ),
                        )

            conn.commit()

    def save_upstream_quotes(self, quotes: List[MarketQuote]) -> int:
        """Upserts upstream daily quotes into global_market_feed."""
        if not quotes:
            return 0

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO global_market_feed 
                (observation_date, brent_usd, eur_usd, crude_eur_per_litre)
                VALUES (:date, :brent_usd, :eur_usd, :crude_eur_per_litre)
                ON CONFLICT(observation_date) DO UPDATE SET
                    brent_usd = excluded.brent_usd,
                    eur_usd = excluded.eur_usd,
                    crude_eur_per_litre = excluded.crude_eur_per_litre
                """,
                [asdict(q) for q in quotes],
            )
            conn.commit()
            return len(quotes)

    def get_upstream_series(self, limit_days: int = 30) -> List[MarketQuote]:
        """Retrieves chronological upstream quotes for the lag engine."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT observation_date, brent_usd, eur_usd, crude_eur_per_litre
                FROM global_market_feed
                ORDER BY observation_date ASC
                LIMIT ?
                """,
                (limit_days,),
            )
            rows = cursor.fetchall()
            return [
                MarketQuote(
                    date=r["observation_date"],
                    brent_usd=r["brent_usd"],
                    eur_usd=r["eur_usd"],
                    crude_eur_per_litre=r["crude_eur_per_litre"],
                )
                for r in rows
            ]

    def save_station_records(
        self,
        records: List[StationRecord],
        observation_date: Optional[str] = None,
    ) -> int:
        """Persists station-level price observations."""
        if not records:
            return 0

        if observation_date is None:
            observation_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        entries = []
        for r in records:
            entries.append(
                (
                    r.station_id,
                    observation_date,
                    r.brand,
                    r.product_name,
                    r.price,
                    r.address,
                    r.municipality,
                    r.latitude,
                    r.longitude,
                    r.reported_at,
                    1 if r.is_stale else 0,
                    1 if r.is_company_operated else 0,
                )
            )

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO station_observations (
                    station_id, observation_date, brand, product_name, pump_price,
                    address, municipality, latitude, longitude, reported_at,
                    is_stale, is_company_operated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(station_id, product_name, observation_date) DO UPDATE SET
                    pump_price = excluded.pump_price,
                    reported_at = excluded.reported_at,
                    is_stale = excluded.is_stale
                """,
                entries,
            )
            conn.commit()
            return len(entries)

    def save_forecast(
        self,
        result: ForecastResult,
        region_code: str,
        fuel_type: str = "unleaded_95",
        forecast_date: Optional[str] = None,
    ) -> str:
        """Persists a generated forecast signal into regional_forecasts."""
        if forecast_date is None:
            forecast_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        record_id = str(uuid.uuid4())
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO regional_forecasts (
                    id, region_code, fuel_type, forecast_date, horizon_days,
                    current_price, predicted_price, expected_delta, signal, confidence_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(region_code, fuel_type, forecast_date, horizon_days) DO UPDATE SET
                    current_price = excluded.current_price,
                    predicted_price = excluded.predicted_price,
                    expected_delta = excluded.expected_delta,
                    signal = excluded.signal,
                    confidence_score = excluded.confidence_score,
                    created_at = CURRENT_TIMESTAMP
                """,
                (
                    record_id,
                    region_code,
                    fuel_type,
                    forecast_date,
                    result.horizon_days,
                    result.current_price,
                    result.predicted_price,
                    result.expected_delta,
                    result.signal.value,
                    result.confidence_score,
                ),
            )
            conn.commit()
            return record_id

    def get_db_stats(self) -> Dict[str, int]:
        """Returns row counts across tables."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            stats = {}
            for tbl in ["regions", "global_market_feed", "station_observations", "regional_forecasts"]:
                cursor.execute(f"SELECT COUNT(*) FROM {tbl}")
                stats[tbl] = cursor.fetchone()[0]
            return stats
