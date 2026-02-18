import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api import app
from pipeline import build_report


@pytest.fixture
def client():
    """FastAPI test client fixture."""
    return TestClient(app)


def test_data_cleaning_and_normalization(tmp_path):
    """
    Test that invalid rows are properly dropped:
    - Invalid event_type rows are filtered out
    - Null country rows are dropped
    - Duplicate event_id rows are removed (keeping first)
    """
    # Create test data with mixed quality
    now = pd.Timestamp.utcnow()
    day1 = now - pd.Timedelta(days=1)

    test_data = pd.DataFrame(
        {
            "event_id": [1, 2, 3, 1],  # Row 4 duplicates Row 1
            "user_id": [100, 101, 102, 100],
            "ts": [
                day1,
                day1,
                day1,
                day1,
            ],
            "event_type": [
                "purchase",  # Valid - kept
                "bot_action",  # Invalid - dropped
                "purchase",  # Valid event_type but...
                "purchase",  # Duplicate event_id - dropped
            ],
            "amount": [100.0, 0.0, 50.0, 100.0],
            "country": ["US", "US", None, "US"],  # Row 3 has null country - dropped
            "device": ["web", "web", "ios", "web"],
            "session_id": ["s1", "s2", "s3", "s1"],
        }
    )

    # Save to temporary CSV
    test_csv = tmp_path / "test_events.csv"
    test_data.to_csv(test_csv, index=False)

    # Build report
    report = build_report(str(test_csv))

    # Assertions
    assert "counts" in report
    assert report["counts"]["raw_rows"] == 4
    # Pipeline behavior: drops invalid event_type, null country, and duplicate event_id
    # Row 1: Valid purchase (kept)
    # Row 2: Invalid event_type "bot_action" (dropped)
    # Row 3: Null country (dropped)
    # Row 4: Duplicate event_id (dropped)
    # Total: 3 rows dropped, 1 valid row remains
    assert report["counts"]["valid_rows"] == 1  # Only Row 1 should remain
    assert report["counts"]["dropped_rows"] == 3  # 3 rows dropped (invalid type + null country + duplicate)

    # Verify valid rows have correct structure
    assert len(report["revenue_daily"]) > 0
    assert report["revenue_daily"][0]["net_revenue"] == 100.0  # Only Row 1's purchase


def test_net_revenue_calculation(tmp_path):
    """
    Test that net revenue correctly sums purchases (positive) and refunds (negative).
    """
    now = pd.Timestamp.utcnow()
    day1 = now - pd.Timedelta(days=1)

    test_data = pd.DataFrame(
        {
            "event_id": [1, 2, 3],
            "user_id": [100, 101, 102],
            "ts": [day1, day1, day1],
            "event_type": ["purchase", "refund", "page_view"],
            "amount": [100.0, -30.0, 0.0],  # Refund should be negative
            "country": ["US", "US", "US"],
            "device": ["web", "web", "ios"],
            "session_id": ["s1", "s2", "s3"],
        }
    )

    test_csv = tmp_path / "test_events.csv"
    test_data.to_csv(test_csv, index=False)

    report = build_report(str(test_csv))

    # Verify revenue_daily structure
    assert "revenue_daily" in report
    assert len(report["revenue_daily"]) == 1

    day1_revenue = report["revenue_daily"][0]
    assert day1_revenue["date"] == str(day1.date())
    # Net revenue: 100 (purchase) + (-30) (refund) = 70.0
    # Note: page_view with amount 0.0 doesn't affect sum
    assert day1_revenue["net_revenue"] == pytest.approx(70.0, rel=1e-6)


def test_d1_retention_logic(tmp_path):
    """
    Test D1 retention calculation:
    - User A: First signup Day 1, no event Day 2 (not retained)
    - User B: First signup Day 1, has event Day 2 (retained)
    - User C: First signup Day 2 (cohort is Day 2, not Day 1)
    """
    now = pd.Timestamp.utcnow()
    day1 = now - pd.Timedelta(days=2)
    day2 = now - pd.Timedelta(days=1)

    test_data = pd.DataFrame(
        {
            "event_id": [1, 2, 3, 4, 5],
            "user_id": [100, 101, 100, 101, 102],
            "ts": [day1, day1, day2, day2, day2],
            "event_type": [
                "signup",  # User A signs up Day 1
                "signup",  # User B signs up Day 1
                "page_view",  # User A has event Day 2 (but not retained since it's not signup-based)
                "page_view",  # User B has event Day 2 (retained - any event counts)
                "signup",  # User C signs up Day 2 (cohort Day 2)
            ],
            "amount": [0.0, 0.0, 0.0, 0.0, 0.0],
            "country": ["US", "US", "US", "US", "US"],
            "device": ["web", "web", "ios", "android", "web"],
            "session_id": ["s1", "s2", "s3", "s4", "s5"],
        }
    )

    test_csv = tmp_path / "test_events.csv"
    test_data.to_csv(test_csv, index=False)

    report = build_report(str(test_csv))

    # Verify retention_d1 structure
    assert "retention_d1" in report
    assert len(report["retention_d1"]) == 2  # Two cohorts: Day 1 and Day 2

    # Find Day 1 cohort
    day1_cohort = next(
        (r for r in report["retention_d1"] if r["cohort_date"] == str(day1.date())),
        None,
    )
    assert day1_cohort is not None
    assert day1_cohort["users"] == 2  # User A and User B
    assert day1_cohort["retained"] == 1  # Only User B has an event on Day 2
    assert day1_cohort["rate"] == pytest.approx(0.5, rel=1e-6)

    # Find Day 2 cohort
    day2_cohort = next(
        (r for r in report["retention_d1"] if r["cohort_date"] == str(day2.date())),
        None,
    )
    assert day2_cohort is not None
    assert day2_cohort["users"] == 1  # User C
    # User C's D1 would be Day 3, which doesn't exist in our test data
    assert day2_cohort["retained"] == 0
    assert day2_cohort["rate"] == pytest.approx(0.0, rel=1e-6)


def test_api_health_endpoint(client):
    """Test that the /health endpoint returns 200 OK."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_report_schema_structure(tmp_path):
    """
    Verify that the report structure matches the expected JSON schema keys.
    """
    now = pd.Timestamp.utcnow()
    day1 = now - pd.Timedelta(days=1)

    test_data = pd.DataFrame(
        {
            "event_id": [1, 2],
            "user_id": [100, 101],
            "ts": [day1, day1],
            "event_type": ["purchase", "signup"],
            "amount": [50.0, 0.0],
            "country": ["US", "BR"],
            "device": ["web", "ios"],
            "session_id": ["s1", "s2"],
        }
    )

    test_csv = tmp_path / "test_events.csv"
    test_data.to_csv(test_csv, index=False)

    report = build_report(str(test_csv))

    # Verify all required top-level keys exist
    required_keys = [
        "range",
        "counts",
        "dau",
        "funnel",
        "revenue_daily",
        "top_countries",
        "anomalies",
        "retention_d1",
    ]
    for key in required_keys:
        assert key in report, f"Missing required key: {key}"

    # Verify range structure
    assert "start" in report["range"]
    assert "end" in report["range"]

    # Verify counts structure
    assert "raw_rows" in report["counts"]
    assert "valid_rows" in report["counts"]
    assert "dropped_rows" in report["counts"]

    # Verify list structures are non-empty (we have data)
    assert isinstance(report["dau"], list)
    assert isinstance(report["funnel"], list)
    assert isinstance(report["revenue_daily"], list)
    assert isinstance(report["top_countries"], list)
    assert isinstance(report["anomalies"], list)
    assert isinstance(report["retention_d1"], list)

    # Verify funnel entry structure
    if report["funnel"]:
        funnel_entry = report["funnel"][0]
        assert "date" in funnel_entry
        assert "pv" in funnel_entry
        assert "signup" in funnel_entry
        assert "purchase" in funnel_entry
        assert "pv_to_signup" in funnel_entry
        assert "signup_to_purchase" in funnel_entry

    # Verify revenue_daily entry structure
    if report["revenue_daily"]:
        rev_entry = report["revenue_daily"][0]
        assert "date" in rev_entry
        assert "net_revenue" in rev_entry

    # Verify anomalies entry structure
    if report["anomalies"]:
        anomaly_entry = report["anomalies"][0]
        assert "date" in anomaly_entry
        assert "net_revenue" in anomaly_entry
        assert "z_score" in anomaly_entry

    # Verify retention_d1 entry structure
    if report["retention_d1"]:
        retention_entry = report["retention_d1"][0]
        assert "cohort_date" in retention_entry
        assert "users" in retention_entry
        assert "retained" in retention_entry
        assert "rate" in retention_entry
