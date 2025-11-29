"""
Scout RADS Dashboard - Core Module

This module provides functionality for analyzing LI-RADS (Liver Imaging Reporting and Data System)
scores from radiology reports with population statistics and time-based comparisons.
"""

import os
import re
import json
import pandas as pd
import numpy as np
import ipywidgets as widgets
from IPython.display import display, HTML
from datetime import datetime, timedelta
from tqdm import tqdm
import trino


# ============================================================================
# CONFIGURATION & CONSTANTS
# ============================================================================

# Color palette (consistent with Scout dashboards)
PRIMARY_GRADIENT = "linear-gradient(135deg, #667eea 0%, #764ba2 100%)"
SUCCESS_GRADIENT = "linear-gradient(135deg, #10b981 0%, #059669 100%)"
INFO_GRADIENT = "linear-gradient(135deg, #11998e 0%, #38ef7d 100%)"
PURPLE_PRIMARY = "#667eea"
GREEN_SUCCESS = "#10b981"
ORANGE_WARNING = "#FF9800"
RED_ERROR = "#F44336"

# Trino connection settings
TRINO_HOST = os.environ.get("TRINO_HOST", "trino.trino")
TRINO_PORT = int(os.environ.get("TRINO_PORT", "8080"))
TRINO_SCHEME = os.environ.get("TRINO_SCHEME", "http")
TRINO_USER = os.environ.get("TRINO_USER", "trino")
TRINO_CATALOG = os.environ.get("TRINO_CATALOG", "delta")
TRINO_SCHEMA = os.environ.get("TRINO_SCHEMA", "default")

# Export directory
EXPORT_DIR = "/home/jovyan/rads_exports"
os.makedirs(EXPORT_DIR, exist_ok=True)

# RADS score categories
LIRADS_SCORES = [
    "LR-1",
    "LR-2",
    "LR-3",
    "LR-4",
    "LR-5",
    "LR-M",
    "LR-NC",
    "LR-TIV",
    "LR-TR",
]
BIRADS_SCORES = [
    "BI-RADS-0",
    "BI-RADS-1",
    "BI-RADS-2",
    "BI-RADS-3",
    "BI-RADS-4",
    "BI-RADS-4A",
    "BI-RADS-4B",
    "BI-RADS-4C",
    "BI-RADS-5",
    "BI-RADS-6",
]
PIRADS_SCORES = ["1", "2", "3", "4", "5"]  # PI-RADS for prostate


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def connect_trino():
    """Connect to Trino."""
    conn = trino.dbapi.connect(
        host=TRINO_HOST,
        port=TRINO_PORT,
        http_scheme=TRINO_SCHEME,
        user=TRINO_USER,
        catalog=TRINO_CATALOG,
        schema=TRINO_SCHEMA,
    )
    return conn


def render_section_loading(section_name):
    """Render loading indicator for a section."""
    display(
        HTML(
            f"""
        <div style='background: #f3f4f6; padding: 16px 20px; border-radius: 8px;
                    border-left: 4px solid {PURPLE_PRIMARY}; margin-bottom: 16px;'>
            <div style='display: flex; align-items: center; gap: 12px;'>
                <div style='font-size: 20px;'>⏳</div>
                <div>{section_name}...</div>
            </div>
        </div>
    """
        )
    )


def render_status_card(message, icon="⏳", gradient=PRIMARY_GRADIENT, subtitle=""):
    """Render a status card."""
    subtitle_html = (
        f"<div style='font-size: 14px; margin-top: 8px; opacity: 0.9;'>{subtitle}</div>"
        if subtitle
        else ""
    )
    display(
        HTML(
            f"""
        <div style='background: {gradient}; padding: 24px; border-radius: 12px;
                    color: white; margin: 20px 0; text-align: center;'>
            <div style='font-size: 28px; margin-bottom: 8px;'>{icon}</div>
            <div style='font-size: 18px; font-weight: 600;'>{message}</div>
            {subtitle_html}
        </div>
    """
        )
    )


# ============================================================================
# RADS SCORE EXTRACTION
# ============================================================================

# Comprehensive LI-RADS patterns
LIRADS_PATTERNS = [
    r"LI-?RADS[-\s]*((?:LR-)?(?:1|2|3|4|5|M|NC|TIV|TR))",  # LI-RADS-5, LIRADS 4, LI-RADS LR-5, LI-RADS TR
    r"LR[-\s]*([1-5]|M|NC|TIV|TR)",  # LR-5, LR 4, LR-M, LR-TR
    r"LIRADS[-\s]*([1-5]|M|NC|TIV|TR)",  # LIRADS5, LIRADS 4, LIRADS TR
]

# Breast RADS patterns
BIRADS_PATTERNS = [
    r"BI-?RADS[-\s]*((?:Category\s*)?[0-6](?:[ABC])?)",  # BI-RADS-5, BIRADS 4A, BI-RADS Category 4
    r"BIRADS[-\s]*((?:Category\s*)?[0-6](?:[ABC])?)",  # BIRADS5, BIRADS 4B
    r"(?:Category\s*)?([0-6](?:[ABC])?)\s*(?:BI-?RADS|BIRADS)",  # Category 4 BI-RADS, 5 BIRADS
]

# Prostate RADS patterns (for future expansion)
PIRADS_PATTERNS = [
    r"PI-?RADS[-\s]*([1-5])",  # PI-RADS-5, PIRADS 4
    r"PIRADS[-\s]*([1-5])",  # PIRADS5
]


def extract_rads_scores(report_text, rads_type="LIRADS"):
    """
    Extract RADS scores from report text.

    Args:
        report_text: Report text to search
        rads_type: 'LIRADS', 'BIRADS', or 'PIRADS'

    Returns:
        List of extracted scores (e.g., ['LR-5', 'LR-4'] or ['BI-RADS-4A'])
    """
    if not report_text or pd.isna(report_text):
        return []

    if rads_type == "LIRADS":
        patterns = LIRADS_PATTERNS
    elif rads_type == "BIRADS":
        patterns = BIRADS_PATTERNS
    else:
        patterns = PIRADS_PATTERNS

    scores = []

    for pattern in patterns:
        matches = re.finditer(pattern, report_text, re.IGNORECASE)
        for match in matches:
            score = match.group(1).upper()

            # Normalize LI-RADS scores
            if rads_type == "LIRADS":
                # Add LR- prefix if not present
                if not score.startswith("LR-"):
                    score = f"LR-{score}"
                scores.append(score)
            elif rads_type == "BIRADS":
                # Normalize BI-RADS scores - remove "CATEGORY " prefix if present
                score = re.sub(r"CATEGORY\s*", "", score)
                # Add BI-RADS- prefix if not present
                if not score.startswith("BI-RADS-"):
                    score = f"BI-RADS-{score}"
                scores.append(score)
            else:
                scores.append(score)

    # Return unique scores in order of appearance
    seen = set()
    unique_scores = []
    for score in scores:
        if score not in seen:
            seen.add(score)
            unique_scores.append(score)

    return unique_scores


def get_primary_score(scores):
    """
    Get primary (highest risk) score from a list of scores.

    Args:
        scores: List of RADS scores

    Returns:
        Primary score or None
    """
    if not scores:
        return None

    # Detect score type from first score
    first_score = scores[0]

    if first_score.startswith("LR-"):
        # Priority order for LI-RADS (highest risk first)
        # LR-TR (treated) is placed after LR-4 as it represents a previously treated lesion
        priority = [
            "LR-M",
            "LR-5",
            "LR-TIV",
            "LR-4",
            "LR-TR",
            "LR-3",
            "LR-2",
            "LR-1",
            "LR-NC",
        ]
    elif first_score.startswith("BI-RADS-"):
        # Priority order for BI-RADS (highest risk first)
        # 6 = known malignancy, 5 = highly suggestive of malignancy
        # 4C > 4B > 4A for subcategories
        priority = [
            "BI-RADS-6",
            "BI-RADS-5",
            "BI-RADS-4C",
            "BI-RADS-4B",
            "BI-RADS-4A",
            "BI-RADS-4",
            "BI-RADS-3",
            "BI-RADS-2",
            "BI-RADS-1",
            "BI-RADS-0",
        ]
    else:
        # Default for PI-RADS or unknown
        priority = ["5", "4", "3", "2", "1"]

    for p in priority:
        if p in scores:
            return p

    return scores[0]  # Fallback to first score


# ============================================================================
# SQL QUERY GENERATION
# ============================================================================


def build_rads_query(config):
    """
    Build SQL query for RADS reports.

    Args:
        config: Dictionary with filter parameters

    Returns:
        tuple: (sql_query, criteria_summary)
    """
    conditions = []
    criteria_summary = []

    # Modality filter (CT/MRI for liver)
    if config.get("modalities"):
        modalities = config["modalities"]
        mod_list = "', '".join([m.lower() for m in modalities])
        conditions.append(f"lower(modality) IN ('{mod_list}')")
        criteria_summary.append(f"Modality: {', '.join(modalities)}")

    # Service name (liver, hepatic, etc.)
    if config.get("service_name_pattern"):
        pattern = config["service_name_pattern"].strip()
        if pattern:
            # Check if it's a comma-separated list or already uses pipe
            if "," in pattern and "|" not in pattern:
                # Split by comma and create OR pattern
                terms = [t.strip() for t in pattern.split(",") if t.strip()]
                escaped_terms = [
                    re.escape(t).replace("\\ ", " ").replace("\\", "\\\\")
                    for t in terms
                ]
                # Join with | for OR in regex
                regex_pattern = "|".join(escaped_terms)
                conditions.append(f"REGEXP_LIKE(service_name, '(?is){regex_pattern}')")
                criteria_summary.append(f"Service name contains: {' OR '.join(terms)}")
            else:
                # Already uses pipe or is a single pattern - use as-is
                pattern_escaped = (
                    re.escape(pattern)
                    .replace("\\ ", " ")
                    .replace("\\|", "|")
                    .replace("\\", "\\\\")
                )
                conditions.append(
                    f"REGEXP_LIKE(service_name, '(?is){pattern_escaped}')"
                )
                criteria_summary.append(f"Service name contains: '{pattern}'")

    # Facility
    if config.get("facilities"):
        facilities = config["facilities"]
        if facilities:
            facilities_list = "', '".join(facilities)
            conditions.append(f"sending_facility IN ('{facilities_list}')")
            criteria_summary.append(f"Facilities: {', '.join(facilities)}")

    # Age range
    if config.get("min_age") is not None:
        conditions.append(f"patient_age >= {config['min_age']}")
        criteria_summary.append(f"Min age: {config['min_age']}")
    if config.get("max_age") is not None:
        conditions.append(f"patient_age <= {config['max_age']}")
        criteria_summary.append(f"Max age: {config['max_age']}")

    # Date range
    if config.get("start_date"):
        conditions.append(f"requested_dt >= DATE '{config['start_date']}'")
        criteria_summary.append(f"Start date: {config['start_date']}")
    if config.get("end_date"):
        conditions.append(f"requested_dt <= DATE '{config['end_date']}'")
        criteria_summary.append(f"End date: {config['end_date']}")

    # RADS mention in report text (broad search)
    rads_type = config.get("rads_type", "LIRADS")
    if rads_type == "LIRADS":
        conditions.append("REGEXP_LIKE(report_text, '(?is)(LI-?RADS|LR-[1-5M])')")
        criteria_summary.append("Report mentions LI-RADS")
    elif rads_type == "BIRADS":
        conditions.append("REGEXP_LIKE(report_text, '(?is)(BI-?RADS|BIRADS)')")
        criteria_summary.append("Report mentions BI-RADS")
    else:
        conditions.append("REGEXP_LIKE(report_text, '(?is)(PI-?RADS)')")
        criteria_summary.append("Report mentions PI-RADS")

    # Ensure we have patient ID
    conditions.append("(epic_mrn IS NOT NULL OR empi_mr IS NOT NULL)")

    # Build WHERE clause
    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # LIMIT clause
    limit_clause = (
        f"LIMIT {config['sample_limit']}" if config.get("sample_limit") else ""
    )
    if config.get("sample_limit"):
        criteria_summary.append(
            f"⚠️ LIMITED to {config['sample_limit']} results for testing"
        )

    # Build full query
    sql = f"""
    SELECT DISTINCT
        obr_3_filler_order_number,
        epic_mrn,
        empi_mr,
        patient_age,
        sex,
        race,
        ethnic_group,
        modality,
        service_name,
        requested_dt,
        observation_dt,
        report_text,
        diagnoses,
        sending_facility,
        message_dt
    FROM {TRINO_CATALOG}.{TRINO_SCHEMA}.latest_reports
    WHERE {where_clause}
    ORDER BY message_dt DESC
    {limit_clause}
    """

    return sql, criteria_summary


# ============================================================================
# DATA LOADING & PROCESSING
# ============================================================================


def load_rads_data(config, status_output):
    """
    Load RADS data and extract scores.

    Args:
        config: Dictionary with filter parameters
        status_output: Widget output for status messages

    Returns:
        tuple: (df, criteria_summary)
    """
    # Build query
    with status_output:
        status_output.clear_output(wait=True)
        render_status_card(
            "Building SQL Query",
            icon="⏳",
            gradient=PRIMARY_GRADIENT,
            subtitle="Searching for RADS reports...",
        )

    sql, criteria_summary = build_rads_query(config)

    # Execute query
    with status_output:
        status_output.clear_output(wait=True)
        render_status_card(
            "Executing Query",
            icon="⏳",
            gradient=PRIMARY_GRADIENT,
            subtitle="This may take 30-90 seconds...",
        )

    conn = connect_trino()
    cursor = conn.cursor()
    cursor.execute(sql)
    columns = [desc[0] for desc in cursor.description]
    data = cursor.fetchall()
    df = pd.DataFrame(data, columns=columns)
    cursor.close()
    conn.close()

    print(f"\n✓ Query returned {len(df)} reports")

    # Extract RADS scores
    with status_output:
        status_output.clear_output(wait=True)
        render_status_card(
            "Extracting RADS Scores",
            icon="⏳",
            gradient=PRIMARY_GRADIENT,
            subtitle="Analyzing report text...",
        )

    rads_type = config.get("rads_type", "LIRADS")
    tqdm.pandas(desc="  Extracting scores", unit="report")
    df["rads_scores"] = df["report_text"].progress_apply(
        lambda x: extract_rads_scores(x, rads_type)
    )
    df["primary_rads_score"] = df["rads_scores"].apply(get_primary_score)

    # Filter to only reports with valid scores
    df_with_scores = df[df["primary_rads_score"].notna()].copy()

    print(f"\n  RADS score extraction:")
    print(f"    • Total reports queried: {len(df)}")
    if len(df) > 0:
        print(
            f"    • Reports with RADS scores: {len(df_with_scores)} ({len(df_with_scores)/len(df)*100:.1f}%)"
        )
    else:
        print(f"    • Reports with RADS scores: 0 (0.0%)")

    # Success message
    with status_output:
        status_output.clear_output(wait=True)
        render_status_card(
            f"Data Loaded Successfully",
            icon="✓",
            gradient=SUCCESS_GRADIENT,
            subtitle=f"{len(df_with_scores)} reports with RADS scores",
        )

    return df_with_scores, criteria_summary


# ============================================================================
# TIME PERIOD HELPERS
# ============================================================================


def get_time_period_filter(period_type):
    """
    Get date filter for predefined time periods.

    Args:
        period_type: 'current_month', 'last_month', 'current_quarter',
                     'last_quarter', 'current_year', '5_years_ago'

    Returns:
        tuple: (start_date, end_date, label)
    """
    today = datetime.now()

    if period_type == "current_month":
        start = today.replace(day=1)
        end = today
        label = start.strftime("%B %Y")

    elif period_type == "last_month":
        # First day of last month
        first_of_month = today.replace(day=1)
        end = first_of_month - timedelta(days=1)
        start = end.replace(day=1)
        label = start.strftime("%B %Y")

    elif period_type == "current_quarter":
        quarter = (today.month - 1) // 3
        start = datetime(today.year, quarter * 3 + 1, 1)
        end = today
        label = f"Q{quarter + 1} {today.year}"

    elif period_type == "last_quarter":
        quarter = (today.month - 1) // 3
        if quarter == 0:
            quarter = 3
            year = today.year - 1
        else:
            quarter -= 1
            year = today.year
        start = datetime(year, quarter * 3 + 1, 1)
        # Last day of that quarter
        if quarter == 3:
            end = datetime(year, 12, 31)
        else:
            end = datetime(year, (quarter + 1) * 3 + 1, 1) - timedelta(days=1)
        label = f"Q{quarter + 1} {year}"

    elif period_type == "current_year":
        start = datetime(today.year, 1, 1)
        end = today
        label = str(today.year)

    elif period_type == "5_years_ago":
        year = today.year - 5
        start = datetime(year, 1, 1)
        end = datetime(year, 12, 31)
        label = str(year)

    else:
        return None, None, None

    return start.date(), end.date(), label


def filter_by_date_range(df, start_date, end_date):
    """Filter DataFrame by date range."""
    # Convert to pandas Timestamp and localize to UTC if needed
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)

    # Check if the datetime column is timezone-aware
    if (
        hasattr(df["requested_dt"].dtype, "tz")
        and df["requested_dt"].dtype.tz is not None
    ):
        # Localize the comparison timestamps to UTC
        start_ts = start_ts.tz_localize("UTC")
        end_ts = end_ts.tz_localize("UTC")

    mask = (df["requested_dt"] >= start_ts) & (df["requested_dt"] <= end_ts)
    return df[mask]


# ============================================================================
# STATISTICS & ANALYSIS
# ============================================================================


def calculate_score_distribution(df, by_patients=False):
    """
    Calculate RADS score distribution counting ALL detected scores.

    Args:
        df: DataFrame with RADS data
        by_patients: If True, count unique patients per score. If False, count all score mentions.

    Returns:
        DataFrame with score counts and percentages
    """
    if len(df) == 0:
        return pd.DataFrame(
            {"score": [], "count": [], "percentage": [], "unique_patients": []}
        )

    if by_patients:
        # Create patient_id column
        df_copy = df.copy()
        df_copy["patient_id"] = df_copy.apply(
            lambda row: (
                row["epic_mrn"] if pd.notna(row["epic_mrn"]) else row["empi_mr"]
            ),
            axis=1,
        )

        # Explode rads_scores to count all detected scores per patient
        # Create a row for each score in the rads_scores list
        score_records = []
        for _, row in df_copy.iterrows():
            rads_scores = row.get("rads_scores", [])
            if rads_scores and len(rads_scores) > 0:
                for score in rads_scores:
                    score_records.append(
                        {"score": score, "patient_id": row["patient_id"]}
                    )

        if not score_records:
            return pd.DataFrame({"score": [], "count": [], "percentage": []})

        scores_df = pd.DataFrame(score_records)

        # Count unique patients per score
        dist = scores_df.groupby("score")["patient_id"].nunique().reset_index()
        dist.columns = ["score", "count"]
        total = df_copy["patient_id"].nunique()
        dist["percentage"] = (dist["count"] / total * 100).round(1)
    else:
        # Count all detected scores (not just primary)
        score_records = []
        for _, row in df.iterrows():
            rads_scores = row.get("rads_scores", [])
            if rads_scores and len(rads_scores) > 0:
                for score in rads_scores:
                    score_records.append({"score": score})

        if not score_records:
            return pd.DataFrame({"score": [], "count": [], "percentage": []})

        scores_df = pd.DataFrame(score_records)
        dist = scores_df["score"].value_counts().reset_index()
        dist.columns = ["score", "count"]

        # Calculate percentage based on total mentions (not total reports)
        total_mentions = dist["count"].sum()
        dist["percentage"] = (dist["count"] / total_mentions * 100).round(1)

    # Sort by score priority - detect RADS type from scores
    lirads_priority = [
        "LR-M",
        "LR-5",
        "LR-TIV",
        "LR-4",
        "LR-TR",
        "LR-3",
        "LR-2",
        "LR-1",
        "LR-NC",
    ]
    birads_priority = [
        "BI-RADS-6",
        "BI-RADS-5",
        "BI-RADS-4C",
        "BI-RADS-4B",
        "BI-RADS-4A",
        "BI-RADS-4",
        "BI-RADS-3",
        "BI-RADS-2",
        "BI-RADS-1",
        "BI-RADS-0",
    ]

    # Detect which priority order to use based on scores present
    if len(dist) > 0 and dist["score"].iloc[0].startswith("BI-RADS-"):
        priority_order = birads_priority
    else:
        priority_order = lirads_priority

    dist["sort_key"] = dist["score"].apply(
        lambda x: priority_order.index(x) if x in priority_order else 999
    )
    dist = dist.sort_values("sort_key").drop("sort_key", axis=1)

    return dist


def calculate_demographics_breakdown(df):
    """
    Calculate demographics breakdown by RADS score (counting unique patients).

    Returns:
        Dictionary with demographics DataFrames
    """
    if len(df) == 0:
        return {}

    # Create patient_id and get one row per patient (most recent report)
    df_copy = df.copy()
    df_copy["patient_id"] = df_copy.apply(
        lambda row: row["epic_mrn"] if pd.notna(row["epic_mrn"]) else row["empi_mr"],
        axis=1,
    )

    # For demographics, we take the most recent report per patient
    # This ensures we count each patient only once
    df_patients = df_copy.sort_values("requested_dt", ascending=False).drop_duplicates(
        "patient_id", keep="first"
    )

    # Age groups
    df_patients["age_group"] = pd.cut(
        df_patients["patient_age"],
        bins=[0, 40, 50, 60, 70, 80, 200],
        labels=["<40", "40-49", "50-59", "60-69", "70-79", "80+"],
    )

    age_breakdown = pd.crosstab(
        df_patients["primary_rads_score"], df_patients["age_group"], margins=True
    )

    # Sex breakdown
    sex_breakdown = pd.crosstab(
        df_patients["primary_rads_score"], df_patients["sex"], margins=True
    )

    # Race breakdown
    race_breakdown = pd.crosstab(
        df_patients["primary_rads_score"], df_patients["race"], margins=True
    )

    return {"age": age_breakdown, "sex": sex_breakdown, "race": race_breakdown}


def calculate_patient_progression(df):
    """
    Calculate patient progression (same patient, multiple reports over time).

    Returns:
        DataFrame with patient progression info
    """
    if len(df) == 0:
        return pd.DataFrame()

    # Group by patient
    df_sorted = df.sort_values(["epic_mrn", "empi_mr", "requested_dt"])

    # Get patients with multiple reports
    patient_id = df_sorted.apply(
        lambda row: row["epic_mrn"] if pd.notna(row["epic_mrn"]) else row["empi_mr"],
        axis=1,
    )

    df_sorted["patient_id"] = patient_id

    # Count reports per patient
    report_counts = df_sorted.groupby("patient_id").size()
    multi_report_patients = report_counts[report_counts > 1].index

    # Filter to multi-report patients
    progression_df = df_sorted[
        df_sorted["patient_id"].isin(multi_report_patients)
    ].copy()

    if len(progression_df) == 0:
        return pd.DataFrame()

    # Add report sequence number per patient
    progression_df["report_sequence"] = (
        progression_df.groupby("patient_id").cumcount() + 1
    )

    # Add previous score
    progression_df["previous_score"] = progression_df.groupby("patient_id")[
        "primary_rads_score"
    ].shift(1)

    # Calculate score change
    def score_to_numeric(score):
        """Convert score to numeric for comparison."""
        if pd.isna(score):
            return None
        mapping = {
            "LR-1": 1,
            "LR-2": 2,
            "LR-3": 3,
            "LR-4": 4,
            "LR-5": 5,
            "LR-M": 6,
            "LR-NC": 0,
            "LR-TIV": 7,
        }
        return mapping.get(score, 0)

    progression_df["score_numeric"] = progression_df["primary_rads_score"].apply(
        score_to_numeric
    )
    progression_df["previous_score_numeric"] = progression_df["previous_score"].apply(
        score_to_numeric
    )
    progression_df["score_change"] = (
        progression_df["score_numeric"] - progression_df["previous_score_numeric"]
    )

    return progression_df[
        [
            "patient_id",
            "obr_3_filler_order_number",
            "requested_dt",
            "primary_rads_score",
            "previous_score",
            "score_change",
            "report_sequence",
        ]
    ].copy()


# ============================================================================
# EXPORT FUNCTIONALITY
# ============================================================================


def export_rads_data(df, include_report_text=False):
    """
    Export RADS data to CSV.

    Args:
        df: DataFrame with RADS data
        include_report_text: Whether to include full report text

    Returns:
        Path to exported file
    """
    # Build export dataframe
    export_df = df[
        [
            "obr_3_filler_order_number",
            "epic_mrn",
            "empi_mr",
            "patient_age",
            "sex",
            "race",
            "ethnic_group",
            "modality",
            "service_name",
            "requested_dt",
            "observation_dt",
            "sending_facility",
            "primary_rads_score",
        ]
    ].copy()

    # Add all detected scores as comma-separated
    export_df["all_rads_scores"] = df["rads_scores"].apply(
        lambda x: ", ".join(x) if x else ""
    )

    # Optionally include report text
    if include_report_text:
        export_df["report_text"] = df["report_text"]

    # Generate filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"rads_data_{timestamp}.csv"
    filepath = os.path.join(EXPORT_DIR, filename)

    # Save
    export_df.to_csv(filepath, index=False)

    return filepath
