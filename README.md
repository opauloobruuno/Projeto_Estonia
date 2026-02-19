<div align="center">

# E-commerce Events Analytics Pipeline

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-API%20Server-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Pandas](https://img.shields.io/badge/Pandas-Data%20Processing-150458?logo=pandas)](https://pandas.pydata.org/)
[![NumPy](https://img.shields.io/badge/NumPy-Numeric%20Computing-013243?logo=numpy)](https://numpy.org/)
[![Pytest](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest)](https://docs.pytest.org/)  

</div>

---

### Project Overview

This project implements an **e-commerce events ingestion and analytics pipeline** (a mini ETL system) that processes user interactions of the following types:

- **page_view**
- **signup**
- **purchase**
- **refund**

The full flow covers:

- **Large-scale synthetic data generation** (`generate_data.py`);
- **Cleaning, normalization, and analytical metrics calculation** (`pipeline.py`);
- **Metrics exposure via HTTP API** using **FastAPI** (`api.py`), with **in-memory cache** based on input file mtime (`events.csv`).

The main output is a `report.json` file consolidating engagement metrics, revenue, conversion funnel, top countries by revenue, anomaly detection, and D1 retention.

---

### Architecture and Data Flow

The system follows a simple 3-stage data flow:

1. **Generation** → `generate_data.py` creates `events.csv` with synthetic events.
2. **Processing / Metrics** → `pipeline.py` loads `events.csv`, cleans the data, and generates `report.json`.
3. **API Service** → `api.py` exposes the metrics via REST endpoints, with in-memory cache.

#### 1. Data Generation (`generate_data.py`)

The `generate_data.py` module generates a synthetic e-commerce events dataset using **NumPy** and **Pandas** with **100% vectorized** operations (no Python `for` loops over rows).

- **Main configuration**:
  - `N_ROWS = 1_000_000` (default number of rows generated).
  - `OUTPUT_CSV = "events.csv"` (output file).
  - `USER_BASE_SIZE = 100_000` (approximate user base size).
  - `EVENT_TYPES = ["page_view", "signup", "purchase", "refund"]`.
  - `COUNTRIES = ["BR", "US", "MX", "CA", "UK", "DE", "FR", "JP", "AU", "IN"]` (multiple markets).
  - `DEVICES = ["ios", "android", "web"]`.

- **Event type distribution** (`EVENT_TYPE_PROBS`):
  - `purchase`: **3%** (within 2–5% range);
  - `refund`: **0.3%** (within 0.1–0.5% range);
  - remainder (**96.7%**) split as:
    - **90%** `page_view`;
    - **6.7%** `signup`.

- **Value distribution (column `amount`)**:
  - For `purchase` and `refund` events, values use a **lognormal distribution** (parameters `mu = log(50)`, `sigma = 0.5`), approximating average prices around 50 monetary units.
  - Purchases (`purchase`) have **positive** `amount`.
  - Refunds (`refund`) have **negative** `amount` (treated as revenue outflow).

- **Timestamps**:
  - Timestamps (`ts`) are **uniformly** distributed over the **last 30 days**, at second resolution, using vectorized operations with `pd.to_timedelta`.

- **Intentional dirty data / anomaly injection** (`inject_dirty_data`):
  - Dirty row fraction: `DIRTY_FRACTION = 0.005` (**0.5%** of rows).
  - Injections done entirely via vectorized operations:
    - **Invalid timestamps**:
      - half of cases replaced with **future** dates (now + 30 days);
      - half with invalid strings (`"not_a_timestamp"`).
    - **Null country**:
      - some records have the `country` column set to `NaN`.
    - **Invalid event types**:
      - `event_type` replaced with `"???"` for some rows.
    - **`event_id` collisions**:
      - subset of rows has `event_id` copied from other rows, creating **duplicate IDs**.

This simulates a **realistic big data** scenario with dirty data, ready for pipeline processing.

#### 2. Processing Pipeline (`pipeline.py`)

The `pipeline.py` module implements a pipeline for **cleaning**, **normalization**, and **metrics calculation** using **Pandas/NumPy** in a **vectorized** way, mainly via the `build_report` function.

- **Loading and cleaning (`_load_and_clean`)**:
  - CSV read **without initial date parsing**, to tolerate invalid strings.
  - Convert `ts` column to datetime with:

    ```python
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)
    ```

  - Invalid timestamps become `NaT` and are **removed**.
  - **Future** timestamps (relative to `now` in UTC) are filtered out.
  - `date` column (date only, no time) is created for daily aggregations.
  - Filter to keep only valid `event_type` (`page_view`, `signup`, `purchase`, `refund`).
  - Remove rows with null `country`.
  - Remove **duplicate `event_id`**, keeping only the first occurrence.

- **Metrics calculation (vectorized functions)**:
  - `_compute_range` → date range of valid data.
  - `_compute_counts` → **raw** vs **valid** row counts and dropped rows.
  - `_compute_dau` → **Daily Active Users** via `groupby("date")["user_id"].nunique()`.
  - `_compute_funnel` → daily event funnel (see metrics section below).
  - `_compute_revenue_daily` → net revenue aggregated by day.
  - `_compute_top_countries` → countries with highest net revenue.
  - `_compute_anomalies` → daily revenue anomalies via Z-score.
  - `_compute_retention_d1` → D1 retention by signup cohorts.

The main `build_report(input_csv)` function orchestrates everything, returning a metrics dictionary and saving the result to `report.json`.

#### 3. API (`api.py` / FastAPI)

The `api.py` module exposes the computed metrics via a **FastAPI** API.

- **Application**:

  ```python
  app = FastAPI(title="Analytics API")
  ```

- **In-memory cache**:
  - Global structure: `results_cache: Dict[str, Tuple[float, Dict[str, Any]]]`.
  - Key: filename (e.g. `"events.csv"`).
  - Value: tuple `(mtime, report)`:
    - `mtime`: CSV file modification timestamp;
    - `report`: precomputed report dictionary.
  - On each request, the pipeline is re-run only if file `mtime` has changed (**mtime-based cache**).

- **Response-time middleware**:
  - HTTP middleware that measures request processing time, logs to console, and injects the `X-Process-Time` header in the response.

- **Available endpoints**:

  - `GET /health`  
    - Returns simple JSON: `{"status": "ok"}` for health checks.

  - `GET /report`  
    - Query param: `file` (optional, default `"events.csv"`).
    - If the file exists, returns the corresponding `report` (reusing cache when possible).
    - On error:
      - 404 if the file does not exist;
      - 500 if the pipeline fails for any other reason.

---

### Calculated Metrics (`report.json`)

The `report.json` file produced by the pipeline contains, among others, the following metric blocks:

- **Daily Active Users (DAU)** (`report["dau"]`)
  - List of objects per day:
    - `date`: date in ISO format (e.g. `"2026-02-10"`).
    - `dau`: number of unique users with any event that day.
  - Calculated in vectorized form with `groupby("date")["user_id"].nunique()`.

- **Conversion Funnel (View → Signup → Purchase)** (`report["funnel"]`)
  - Daily funnel metrics:
    - `date`: date.
    - `pv`: `page_view` count.
    - `signup`: `signup` count.
    - `purchase`: `purchase` count.
    - `pv_to_signup`: page_view-to-signup conversion rate.
    - `signup_to_purchase`: signup-to-purchase conversion rate.
  - Implemented via `pivot_table` plus vectorized divisions with `np.where`, avoiding division by zero.

- **Daily Net Revenue (Purchase − Refund)** (`report["revenue_daily"]`)
  - Daily net revenue:
    - The `amount` column already has:
      - **positive** values for `purchase`;
      - **negative** values for `refund`.
    - Daily revenue is the sum of `amount` per `date`:

      ```python
      rev_series = df.groupby("date")["amount"].sum()
      ```

  - Result: list of `{"date": "...", "net_revenue": ...}`.

- **Top Countries by Revenue** (`report["top_countries"]`)
  - Net revenue aggregated by country:
    - Group `amount` by `country`;
    - Sort by `net_revenue` descending;
    - Return only the **top N** countries (default `top_n=10`).
  - Each item contains:
    - `country`;
    - `net_revenue`.

- **Anomaly Detection (Z-score > 3 sigma)** (`report["anomalies"]`)
  - `_compute_anomalies` receives the `revenue_daily` list and computes:
    - mean and standard deviation of `net_revenue`;
    - `z_score` per day: \((\text{net_revenue} - mean) / std\).
  - Result: list with:
    - `date`;
    - `net_revenue`;
    - `z_score`.
  - Clients can apply thresholds (e.g. |z_score| > 3) to identify anomalous revenue days.

- **D1 Retention** (`report["retention_d1"]`)
  - Retention on the day after signup, by cohort:
    - `cohort_date`: date of **first signup** for each user.
    - `users`: number of users in that cohort.
    - `retained`: how many of those users had **any event** on `cohort_date + 1`.
    - `rate`: fraction `retained / users`.
  - Implemented without loops, using:
    - groupby for first signup date;
    - vectorized `d1_date` calculation;
    - merge with unique `(user_id, date)` pairs from all events.

---

### Installation and Execution Guide

#### 1. Requirements

- **Python 3.11+** (or compatible version).
- `pip` for dependency installation.

#### 2. Clone the repository

```bash
git clone https://github.com/opauloobruuno/Projeto_Stonia.git
cd Projeto_Stonia
```

#### 3. Install dependencies

With an existing `requirements.txt`:

```bash
pip install -r requirements.txt
```

Or install the main packages manually:

```bash
pip install fastapi uvicorn pandas numpy pytest
```

#### 4. Generate synthetic data

By default, the script generates **1,000,000 rows**. Adjust the volume by editing the `N_ROWS` constant in `generate_data.py`.

```bash
python generate_data.py
```

This creates:

- `events.csv` – raw dataset with clean and injected dirty data.

#### 5. Run the pipeline and generate the report

Run the pipeline directly from the command line:

```bash
python pipeline.py
```

Or call `build_report` as a module:

```bash
python -c "from pipeline import build_report; build_report('events.csv')"
```

This produces:

- `report.json` – file consolidating all metrics described above.

#### 6. Start the FastAPI server

Run the server with `uvicorn` pointing to the app in `api.py`:

```bash
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

Or use the `if __name__ == "__main__"` block in `api.py`:

```bash
python api.py
```

#### 7. Test the endpoints

- **Health check**:

  ```bash
  curl http://localhost:8000/health
  ```

  Expected response:

  ```json
  {"status": "ok"}
  ```

- **Get the analytics report**:

  Ensure `events.csv` exists (generate with `generate_data.py` if needed) and run:

  ```bash
  curl "http://localhost:8000/report?file=events.csv"
  ```

  The response will be a large JSON with keys:

  ```json
  {
    "range": {...},
    "counts": {...},
    "dau": [...],
    "funnel": [...],
    "revenue_daily": [...],
    "top_countries": [...],
    "anomalies": [...],
    "retention_d1": [...]
  }
  ```

#### 8. Run the tests

The project includes automated tests (e.g. in `test_pipeline.py`) using **pytest**.

```bash
pytest
```

---

### Technical Decisions

- **Heavy use of vectorized operations (Pandas/NumPy)**:
  - All critical stages (generation, cleaning, metrics) use **vectorized operations**, avoiding row-by-row Python loops.
  - Benefits:
    - Higher **performance** on large datasets (millions of rows);
    - Better use of NumPy/Pandas internal optimizations (C / SIMD).

- **Handling of dirty data and edge cases**:
  - Invalid timestamps are converted to `NaT` and removed.
  - Future timestamps are filtered out to avoid metric distortions.
  - Rows with null `country` are removed before aggregations.
  - Invalid event types (`"???"`) are discarded.
  - `event_id` collisions are resolved with `drop_duplicates`, ensuring uniqueness.

- **Revenue and anomaly modeling**:
  - A **lognormal** distribution for `purchase`/`refund` values better simulates the long tail of e-commerce prices.
  - **Z-score**–based anomaly detection (3-sigma) is simple but effective for highlighting days with revenue far above or below the mean.

- **Cohort-based retention**:
  - D1 retention is computed by **signup cohorts** using vectorized joins, scaling well to large volumes.

- **API with mtime-based in-memory cache**:
  - The cache avoids recomputing the full pipeline on every `/report` request, using file `mtime` as the invalidation trigger.
  - Simple, efficient, and suitable for a **batch + read** environment.

---
