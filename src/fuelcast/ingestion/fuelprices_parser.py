import json
from dataclasses import dataclass
from typing import List, Dict, Optional
from datetime import datetime

from ..tax import decompose_pump_price

@dataclass
class StationRecord:
    brand: str
    fuel_product: str
    address: str
    price: float
    reported_date: str
    is_stale: bool
    is_company_operated: bool  # Kalypso or Ermis COCO


class FuelPricesParser:
    """
    Parses and analyzes station-level price feeds from fuelprices.gr.
    Derives empirical retail margins through spatial spread against terminal racks
    and fiscal decomposition.
    """

    def __init__(self, vat_rate: float = 0.24):
        self.vat_rate = vat_rate

    @staticmethod
    def identify_coco(address: str, product: str) -> bool:
        """Identifies Company-Owned Company-Operated stations (Kalypso / Ermis)."""
        coco_keywords = ["ΚΑΛΥΨΩ", "ΕΡΜΗΣ", "KALYPSO", "ERMIS"]
        return any(k in address.upper() for k in coco_keywords)

    def parse_kalamaria_sample(self) -> List[StationRecord]:
        """
        Structured ground-truth station observations from fuelprices.gr for Kalamaria.
        """
        raw_records = [
            {"brand": "REVOIL", "product": "Revoil αμόλυβδη 95 xtra4", "address": "ΑΓΟΡΑ ΦΟΙΝΙΚΑ Κ27-Κ28 ΘΕΣΣΑΛΟΝΙΚΗ", "price": 2.117, "date": "2026-10-09 14:05:29"},
            {"brand": "AVIN", "product": "Αμόλυβδη AVIN Best 95", "address": "Λεωφόρος Εθνικής Αντίστασης 94", "price": 2.137, "date": "2026-10-16 00:48:37"},
            {"brand": "EKO", "product": "EKO Kinitron Unleaded 95 Plus", "address": "ΓΕΩΡΓΙΚΗΣ ΣΧΟΛΗΣ 128, ΚΑΛΑΜΑΡΙΑ (ΚΑΛΥΨΩ 13)", "price": 2.138, "date": "2026-10-15 14:37:42"},
            {"brand": "BP", "product": "BP Unleaded 95", "address": "Εθνικής Αντιστάσεως 181- ΚΑΛΑΜΑΡΙΑ (ΚΑΛΥΨΩ No 280)", "price": 2.147, "date": "2026-10-16 11:43:08"},
            {"brand": "BP", "product": "BP Unleaded 95", "address": "ΕΘΝΙΚΗΣ ΑΝΤΙΣΤΑΣΕΩΣ 104 (ΕΚΟ-ΚΑΛΥΨΩ No 117)", "price": 2.147, "date": "2026-10-16 06:33:34"},
            {"brand": "SHELL", "product": "Shell Αμόλυβδη Ενισχυμένη 95", "address": "ΠΟΝΤΟΥ & ΒΡΥΟΥΛΩΝ 44 (ΕΡΜΗΣ ΑΕΜΕΕ)", "price": 2.147, "date": "2026-10-16 06:13:09"},
            {"brand": "SHELL", "product": "Shell Αμόλυβδη Ενισχυμένη 95", "address": "ΕΘΝ.ΑΝΤΙΣΤΑΣΕΩΣ 118 (ΕΡΜΗΣ ΑΕΜΕΕ)", "price": 2.147, "date": "2026-10-16 00:06:30"},
            {"brand": "SHELL", "product": "Shell Αμόλυβδη Ενισχυμένη 95", "address": "ΠΟΝΤΟΥ 13, ΚΑΛΑΜΑΡΙΑ (ΕΡΜΗΣ ΑΕΜΕΕ)", "price": 2.168, "date": "2026-10-16 05:37:10"},
            {"brand": "EKO", "product": "EKO Kinitron Unleaded 95 Plus", "address": "ΕΘΝ.ΑΝΤΙΣΤΑΣΕΩΣ 82 (ΔΕΛΗΓΙΑΝΝΙΔΗΣ)", "price": 2.168, "date": "2026-10-16 08:12:00"}
        ]

        today = "2026-10-16"
        records = []
        for r in raw_records:
            is_stale = not r["date"].startswith(today) and not r["date"].startswith("2026-10-15")
            is_coco = ("ΚΑΛΥΨΩ" in r["address"] or "ΕΡΜΗΣ" in r["address"])
            records.append(
                StationRecord(
                    brand=r["brand"],
                    fuel_product=r["product"],
                    address=r["address"],
                    price=r["price"],
                    reported_date=r["date"],
                    is_stale=is_stale,
                    is_company_operated=is_coco,
                )
            )
        return records

    def analyze_margins(
        self,
        records: List[StationRecord],
        terminal_rack_floor: float = 2.069,
    ) -> Dict:
        """
        Derives empirical station retail margins:
        1. Filters stale reporting outliers.
        2. Computes the spatial spread over Kalochori terminal floor ex-VAT.
        """
        fresh_records = [r for r in records if not r.is_stale]
        prices = [r.price for r in fresh_records]

        modal_price = max(set(prices), key=prices.count)
        mean_price = sum(prices) / len(prices)
        min_price = min(prices)
        max_price = max(prices)

        # Spatial margin over terminal rack floor:
        # ΔPrice_ex_VAT = (P_station - P_terminal) / 1.24
        terminal_floor_ex_vat = terminal_rack_floor / (1.0 + self.vat_rate)
        
        derived_margins = []
        for r in fresh_records:
            station_ex_vat = r.price / (1.0 + self.vat_rate)
            spatial_margin = station_ex_vat - terminal_floor_ex_vat
            derived_margins.append(spatial_margin)

        avg_spatial_margin = sum(derived_margins) / len(derived_margins)

        return {
            "fresh_station_count": len(fresh_records),
            "stale_station_count": len(records) - len(fresh_records),
            "modal_price": round(modal_price, 3),
            "mean_price": round(mean_price, 3),
            "min_price": round(min_price, 3),
            "max_price": round(max_price, 3),
            "terminal_rack_floor": round(terminal_rack_floor, 3),
            "derived_spatial_margin_ex_vat": round(avg_spatial_margin, 4),
            "derived_spatial_margin_inc_vat": round(avg_spatial_margin * (1.0 + self.vat_rate), 3),
        }
