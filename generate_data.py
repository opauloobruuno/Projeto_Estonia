import pandas as pd
import numpy as np
from datetime import timedelta


# Configuration: adjust as needed
N_ROWS = 1_000_000  # Total number of rows to generate
RANDOM_SEED = 42    # Seed for reproducibility
OUTPUT_CSV = "events.csv"

# Vectorized constants / distributions
USER_BASE_SIZE = 100_000
EVENT_TYPES = np.array(["page_view", "signup", "purchase", "refund"])

# Fixed probabilities within the specified ranges:
# - purchase: 3% (between 2–5%)
# - refund: 0.3% (between 0.1–0.5%)
# - remaining 96.7% split as: 90% page_view, 6.7% signup (mostly page_views)
EVENT_TYPE_PROBS = np.array([0.90, 0.067, 0.03, 0.003])

COUNTRIES = np.array(["BR", "US", "MX", "CA", "UK", "DE", "FR", "JP", "AU", "IN"])
DEVICES = np.array(["ios", "android", "web"])

# Dirty data fractions
DIRTY_FRACTION = 0.005  # 0.5%


def generate_events(num_rows: int) -> pd.DataFrame:
    """
    Generate a high-volume synthetic events dataset using NumPy/Pandas
    vectorized operations only (no Python row-by-row loops).
    """
    np.random.seed(RANDOM_SEED)

    # ---- event_id (unique) ----
    # Vectorized: simple arange guarantees uniqueness, later we introduce collisions.
    event_id = np.arange(1, num_rows + 1, dtype=np.int64)

    # ---- user_id ----
    # Vectorized random integers over a ~100k user base.
    user_id = np.random.randint(1, USER_BASE_SIZE + 1, size=num_rows, dtype=np.int32)

    # ---- timestamps over last 30 days (UTC, second resolution) ----
    # All operations are NumPy-based; no loops.
    now = pd.Timestamp.now("UTC").floor("s")
    start_time = now - pd.Timedelta(days=30)
    total_seconds = int((now - start_time).total_seconds())

    # Uniformly sample seconds offset within the 30-day window.
    seconds_offset = np.random.randint(0, total_seconds + 1, size=num_rows, dtype=np.int64)
    ts = start_time + pd.to_timedelta(seconds_offset, unit="s")

    # ---- event_type ----
    # Vectorized multinomial-style draw using np.random.choice.
    event_type = np.random.choice(EVENT_TYPES, size=num_rows, p=EVENT_TYPE_PROBS)

    # ---- amount ----
    # For purchase and refund: lognormal distribution with parameters chosen
    # to simulate realistic pricing around ~50.
    #
    # Vectorization: we create a mask and fill an array in bulk.
    amount = np.zeros(num_rows, dtype=np.float64)
    purchase_mask = event_type == "purchase"
    refund_mask = event_type == "refund"
    price_mask = purchase_mask | refund_mask

    # Parameters for lognormal: mean ~ log(50), sigma=0.5
    mu = np.log(50.0)
    sigma = 0.5
    num_price_rows = price_mask.sum()
    if num_price_rows > 0:
        prices = np.random.lognormal(mean=mu, sigma=sigma, size=num_price_rows)

        # Apply positive prices for purchases and negative for refunds in one shot.
        amount[price_mask] = prices
        # Turn refund amounts negative (refund outflow)
        amount[refund_mask] *= -1.0

    # ---- country ----
    country = np.random.choice(COUNTRIES, size=num_rows)

    # ---- device ----
    device = np.random.choice(DEVICES, size=num_rows)

    # ---- session_id ----
    # Use random 64-bit integers cast to string as session identifiers.
    # Fully vectorized; avoids per-row UUID generation.
    session_id_int = np.random.randint(
        low=0, high=np.iinfo(np.int64).max, size=num_rows, dtype=np.int64
    )
    session_id = session_id_int.astype(str)

    # Build DataFrame in a single vectorized step.
    df = pd.DataFrame(
        {
            "event_id": event_id,
            "user_id": user_id,
            "ts": ts.tz_localize(None),
            "event_type": event_type,
            "amount": amount,
            "country": country,
            "device": device,
            "session_id": session_id,
        }
    )

    return df


def inject_dirty_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Injects dirty data into the DataFrame using vectorized masking operations.
    No row-by-row loops are used.
    """
    num_rows = len(df)
    if num_rows == 0:
        return df

    # Pre-generate indices for dirty injections.
    n_dirty = max(1, int(num_rows * DIRTY_FRACTION))

    # Use numpy permutation to avoid explicit Python loops and to randomize indices.
    dirty_indices = np.random.permutation(num_rows)

    # ---- Invalid timestamps (0.5%) ----
    ts_idx = dirty_indices[:n_dirty]

    # CORREÇÃO 1: Converter a coluna para 'object' para aceitar strings misturadas com datas
    df["ts"] = df["ts"].astype(object)

    # Build malformed/future timestamps vectorized.
    # Half future timestamps, half malformed strings (approx).
    half = n_dirty // 2
    
    # CORREÇÃO 2: Atualização do método depreciado (aviso que apareceu no seu log)
    # De: pd.Timestamp.utcnow() -> Para: pd.Timestamp.now("UTC")
    future_time = pd.Timestamp.now("UTC") + pd.Timedelta(days=30)

    invalid_ts_values = np.empty(n_dirty, dtype=object)
    invalid_ts_values[:half] = future_time.isoformat()
    invalid_ts_values[half:] = "not_a_timestamp"

    # Agora esta linha vai funcionar pois a coluna aceita objetos variados
    df.loc[ts_idx, "ts"] = invalid_ts_values

    # ---- Null countries (0.5%) ----
    country_idx = dirty_indices[n_dirty : 2 * n_dirty]
    df.loc[country_idx, "country"] = np.nan

    # ---- Invalid event types (0.5%) ----
    event_type_idx = dirty_indices[2 * n_dirty : 3 * n_dirty]
    df.loc[event_type_idx, "event_type"] = "???"

    # ---- Duplicate event_ids (0.5%) ----
    dup_idx = dirty_indices[3 * n_dirty : 4 * n_dirty]
    if len(dup_idx) > 0:
        # Create collisions by copying event_id values from random other rows.
        # All done via vectorized indexing; no Python loops.
        source_idx = np.random.randint(0, num_rows, size=len(dup_idx))
        df.loc[dup_idx, "event_id"] = df["event_id"].to_numpy()[source_idx]

    return df


def main():
    # Generate clean dataset
    df = generate_events(N_ROWS)

    # Inject dirty data
    df = inject_dirty_data(df)

    # Write to CSV efficiently without index
    df.to_csv(OUTPUT_CSV, index=False)


if __name__ == "__main__":
    main()