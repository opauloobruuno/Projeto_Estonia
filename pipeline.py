import json
from typing import Dict

import numpy as np
import pandas as pd


VALID_EVENT_TYPES = np.array(["page_view", "signup", "purchase", "refund"])


def _load_and_clean(input_csv: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load raw data from CSV and perform cleaning/normalization using
    vectorized Pandas/NumPy operations (no row-by-row Python loops).

    Returns
    -------
    raw_df : DataFrame before cleaning
    clean_df : DataFrame after cleaning
    """
    # Load without parsing dates first (timestamps may contain invalid strings)
    raw_df = pd.read_csv(input_csv)

    df = raw_df.copy()

    # ---- Normalize timestamp column ----
    # Coerce malformed strings to NaT, then filter them out.
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce", utc=True)

    # Drop rows with invalid timestamps
    df = df.dropna(subset=["ts"])

    # Remove timestamps that are in the future relative to "now" (UTC).
    now = pd.Timestamp.utcnow().tz_localize("UTC") if df["ts"].dt.tz is None else pd.Timestamp.utcnow()
    df = df[df["ts"] <= now]

    # Normalize to date column for daily aggregations (kept as separate column)
    df["date"] = df["ts"].dt.date

    # ---- Filter invalid event types ----
    df = df[df["event_type"].isin(VALID_EVENT_TYPES)]

    # ---- Drop rows with null/NaN country ----
    df = df.dropna(subset=["country"])

    # ---- Remove duplicate event_id collisions (keep first occurrence) ----
    if "event_id" in df.columns:
        df = df.drop_duplicates(subset=["event_id"], keep="first")

    return raw_df, df


def _compute_range(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"start": None, "end": None}

    min_date = df["date"].min()
    max_date = df["date"].max()

    return {
        "start": str(min_date),
        "end": str(max_date),
    }


def _compute_counts(raw_df: pd.DataFrame, clean_df: pd.DataFrame) -> dict:
    raw_rows = int(len(raw_df))
    valid_rows = int(len(clean_df))
    return {
        "raw_rows": raw_rows,
        "valid_rows": valid_rows,
        "dropped_rows": raw_rows - valid_rows,
    }


def _compute_dau(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []

    # Vectorized: groupby-agg for daily unique users.
    dau_series = df.groupby("date")["user_id"].nunique()
    dau_df = dau_series.reset_index(name="dau").sort_values("date")

    dau_df["date"] = dau_df["date"].astype(str)
    return dau_df.to_dict(orient="records")


def _compute_funnel(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []

    # Vectorized daily counts per event type.
    counts = (
        df.pivot_table(
            index="date",
            columns="event_type",
            values="event_id",
            aggfunc="count",
            fill_value=0,
        )
        .reindex(columns=["page_view", "signup", "purchase", "refund"], fill_value=0)
        .reset_index()
    )

    # Rename and compute funnel conversion metrics.
    counts = counts.rename(
        columns={
            "page_view": "pv",
            "signup": "signup",
            "purchase": "purchase",
        }
    )

    # Ensure numeric types
    for col in ["pv", "signup", "purchase"]:
        if col in counts.columns:
            counts[col] = counts[col].astype(np.int64)

    # Vectorized ratio computations with safe division.
    pv = counts["pv"].to_numpy()
    signup = counts["signup"].to_numpy()
    purchase = counts["purchase"].to_numpy()

    pv_to_signup = np.where(pv > 0, signup / pv, 0.0)
    signup_to_purchase = np.where(signup > 0, purchase / signup, 0.0)

    counts["pv_to_signup"] = pv_to_signup
    counts["signup_to_purchase"] = signup_to_purchase

    counts["date"] = counts["date"].astype(str)

    # Select only required columns
    funnel_df = counts[
        [
            "date",
            "pv",
            "signup",
            "purchase",
            "pv_to_signup",
            "signup_to_purchase",
        ]
    ].sort_values("date")

    return funnel_df.to_dict(orient="records")


def _compute_revenue_daily(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []

    # Revenue is driven by purchase/refund events; amount is already signed
    # (refunds negative) from the generator.
    rev_series = df.groupby("date")["amount"].sum()
    rev_df = rev_series.reset_index(name="net_revenue").sort_values("date")

    rev_df["date"] = rev_df["date"].astype(str)
    return rev_df.to_dict(orient="records")


def _compute_top_countries(df: pd.DataFrame, top_n: int = 5) -> list[dict]:
    if df.empty:
        return []

    # Aggregate net revenue by country.
    country_rev = (
        df.groupby("country")["amount"]
        .sum()
        .reset_index(name="net_revenue")
        .sort_values("net_revenue", ascending=False)
    )

    top_df = country_rev.head(top_n)
    return top_df.to_dict(orient="records")


def _compute_anomalies(revenue_daily: list[dict]) -> list[dict]:
    if not revenue_daily:
        return []

    rev_df = pd.DataFrame(revenue_daily)
    values = rev_df["net_revenue"].astype(float).to_numpy()

    mean = values.mean()
    std = values.std(ddof=0)

    if std == 0:
        z_scores = np.zeros_like(values, dtype=float)
    else:
        z_scores = (values - mean) / std

    rev_df["z_score"] = z_scores

    # Keep all days with their z-scores; consumer can choose thresholds.
    anomalies_df = rev_df[["date", "net_revenue", "z_score"]]
    return anomalies_df.to_dict(orient="records")


def _compute_retention_d1(df: pd.DataFrame) -> list[dict]:
    """
    Compute D1 retention by signup cohort date:
      - cohort_date: date of first signup for user
      - users: number of users in that cohort
      - retained: number of those users that have ANY event on cohort_date + 1
      - rate: retained / users
    """
    if df.empty:
        return []

    # Consider only users who ever signup; cohorted by date of their first signup.
    signup_df = df[df["event_type"] == "signup"][["user_id", "date"]]
    if signup_df.empty:
        return []

    # First signup date per user (vectorized groupby-agg).
    first_signup = (
        signup_df.groupby("user_id")["date"]
        .min()
        .reset_index()
        .rename(columns={"date": "cohort_date"})
    )

    # Next-day date for each cohort user.
    cohort_dates = pd.to_datetime(first_signup["cohort_date"])
    d1_dates = (cohort_dates + pd.Timedelta(days=1)).dt.date
    first_signup["d1_date"] = d1_dates

    # Build a DataFrame of all (user_id, event_date) pairs from all events.
    user_date_pairs = df[["user_id", "date"]].drop_duplicates()

    # Join to see which cohort users have an event on their d1_date.
    merged = first_signup.merge(
        user_date_pairs,
        left_on=["user_id", "d1_date"],
        right_on=["user_id", "date"],
        how="left",
        indicator=True,
    )

    # Retained if match found (_merge == "both").
    merged["retained_flag"] = (merged["_merge"] == "both").astype(int)

    # Aggregate by cohort_date (vectorized groupby).
    cohort_stats = (
        merged.groupby("cohort_date")["retained_flag"]
        .agg(users="size", retained="sum")
        .reset_index()
    )

    cohort_stats["rate"] = np.where(
        cohort_stats["users"] > 0,
        cohort_stats["retained"] / cohort_stats["users"],
        0.0,
    )

    cohort_stats["cohort_date"] = cohort_stats["cohort_date"].astype(str)

    return cohort_stats.to_dict(orient="records")


def build_report(input_csv: str) -> Dict:
    """
    Loads data, cleans it, calculates metrics, and returns the report dictionary.
    Also saves the dictionary to 'report.json'.
    """
    raw_df, clean_df = _load_and_clean(input_csv)

    report: Dict = {}

    # Range of valid data
    report["range"] = _compute_range(clean_df.assign(date=clean_df["ts"].dt.date if "date" not in clean_df.columns else clean_df["date"]))

    # Counts
    report["counts"] = _compute_counts(raw_df, clean_df)

    # Ensure clean_df has 'date' column for downstream computations
    if "date" not in clean_df.columns:
        clean_df = clean_df.assign(date=clean_df["ts"].dt.date)

    # DAU
    report["dau"] = _compute_dau(clean_df)

    # Funnel metrics
    report["funnel"] = _compute_funnel(clean_df)

    # Revenue daily
    revenue_daily = _compute_revenue_daily(clean_df)
    report["revenue_daily"] = revenue_daily

    # Top countries
    report["top_countries"] = _compute_top_countries(clean_df)

    # Anomalies based on revenue_daily net_revenue z-scores
    report["anomalies"] = _compute_anomalies(revenue_daily)

    # D1 retention
    report["retention_d1"] = _compute_retention_d1(clean_df)

    # Persist to JSON
    with open("report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    return report


if __name__ == "__main__":
    build_report("events.csv")

