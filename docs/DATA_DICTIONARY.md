# FuelCast Greece: Data Dictionary & Econometric Parameters

This document specifies the exact economic, statistical, and geographic fields configured across [`src/fuelcast/data/regions.json`](../src/fuelcast/data/regions.json) and [`src/fuelcast/data/municipalities.json`](../src/fuelcast/data/municipalities.json).

---

## 1. Regional Topology (`regions.json`)

Defines the macro administrative units of Greece at the Eurostat **NUTS Level 3** (Nomenclature of Territorial Units for Statistics) tier.

| Field | Type | Description | Econometric / Mathematical Role |
| :--- | :--- | :--- | :--- |
| `nuts3` | `string` | Official Eurostat NUTS-3 geographic classification code. | Primary join key across European and Greek administrative datasets (e.g., `EL303` for Central Athens, `EL522` for Thessaloniki, `EL422` for Cyclades). |
| `name_el` | `string` | Official Greek administrative name (*Περιφερειακή Ενότητα*). | Used for display and matching domestic Ministry filings (`fuelprices.gr`). |
| `name_en` | `string` | Standardized English translation. | CLI filtering and international reporting. |
| `turnover_tier` | `enum` | Macro turnover velocity tier: `metro_high`, `mainland_standard`, `island_remote`. | Selects the baseline decay kernel family governing inventory replenishment frequency. |
| `lag_up` ($t_{1/2}^+$) | `float` (days) | **Upward Shock Half-Life** ("The Rocket"). | The time (in days) required for **50% of an upstream cost spike** to transmit into retail pump prices. Metro hubs exhibit $t_{1/2}^+ \approx 1.8 - 2.0\text{d}$; remote islands exhibit $t_{1/2}^+ \approx 4.0 - 4.5\text{d}$. |
| `lag_down` ($t_{1/2}^-$) | `float` (days) | **Downward Shock Half-Life** ("The Feather"). | The time (in days) required for **50% of an upstream cost drop** to be reflected at the pump. Captures the physical and psychological stickiness of expensive wholesale inventory liquidation ($t_{1/2}^- \approx 7.0 - 14.0\text{d}$). |

---

## 2. Municipal Micro-Markets (`municipalities.json`)

Captures intra-metropolitan variance, local station competitive density, and spatial retail margin spreads within major urban agglomerations (Thessaloniki and Attica).

| Field | Type | Description | Econometric / Mathematical Role |
| :--- | :--- | :--- | :--- |
| `municipality_id` | `string` | Unique slug identifier (e.g., `thess_kalamaria`). | Foreign key referencing the micro-market entity. |
| `nuts3` | `string` | Parent NUTS-3 code. | Links the municipality to its parent macroeconomic baseline (e.g., `EL522` for Kalamaria, `EL304` for Glyfada). |
| `name_el` / `name_en`| `string` | Official Greek and English municipal names. | User search queries (`--region kalamaria`). |
| `parent_region` | `string` | Broad metropolitan label. | Grouping and regional hierarchy. |
| `turnover_tier` | `enum` | Micro-market classification: `metro_suburban_premium`, `metro_industrial_discount`, `metro_high`. | Adjusts dealer elasticity based on competitive topology. |
| `margin_offset_eur` ($\Delta M_{\text{spatial}}$) | `float` (€/L) | **Spatial Retail Margin Offset (ex-VAT)**. | The persistent structural spread in Euro per litre relative to the metropolitan wholesale rack floor: $$\Delta M = \frac{P_{\text{muni}} - P_{\text{rack\_floor}}}{1 + \text{VAT}_{24\%}}$$ Accounts for local commercial ground leases, affluent consumer demand elasticity, and absence of independent discounters (e.g., $+€0.065/\text{L}$ in Kalamaria vs. $-€0.035/\text{L}$ in Kalochori depot corridor). |
| `station_count` | `integer` | Active retail station density. | Used as statistical sample weight and degrees of freedom in regional pooling. |
| `typical_lag_up` | `float` (days) | Local upward transmission half-life. | Overrides macro regional prior with municipality-specific delivery frequency. |
| `typical_lag_down` | `float` (days) | Local downward transmission half-life. | Captures local dealer margin retention duration. |
| `context` | `string` | Qualitative market rationale. | Explains the structural economics (refinery COCO presence, brand saturation, discount presence). |

---

## 3. Mathematical Formulation of the Decay Kernels

The asymmetric lag priors parameterize the exponential decay weighting applied to upstream shocks $\Delta c_k$:

$$\Delta c_k^+ = \max(\Delta c_k, 0), \quad \Delta c_k^- = \min(\Delta c_k, 0)$$

For an observation $k$ occurring $\Delta t$ days in the past:

$$\lambda^+ = \frac{\ln(2)}{t_{1/2}^+}, \quad w_k^+ = \exp(-\lambda^+ \cdot \Delta t)$$
$$\lambda^- = \frac{\ln(2)}{t_{1/2}^-}, \quad w_k^- = \exp(-\lambda^- \cdot \Delta t)$$

Because $\lambda^+ > \lambda^-$, immediate price spikes dominate the forward expectation window, while cost reductions decay slowly over an extended moving average, mathematically producing the **asymmetric rocket-and-feather curve**.
