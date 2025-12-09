"""
Scout Cohort Builder - Core Module

This module provides functionality for building custom patient cohorts from radiology reports
with flexible filtering, negation detection, and manual review capabilities.
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
EXPORT_DIR = "/home/jovyan/cohort_exports"
os.makedirs(EXPORT_DIR, exist_ok=True)


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
# SQL QUERY GENERATION
# ============================================================================


def build_cohort_query(config):
    """
    Build SQL query based on user configuration.

    Args:
        config: Dictionary with filter parameters

    Returns:
        tuple: (sql_query, criteria_summary)
    """
    conditions = []
    criteria_summary = []

    # Build primary search criteria (diagnosis OR report text) - these are ORed together
    primary_conditions = []

    # Diagnosis codes
    if config.get("diagnosis_codes"):
        codes = [c.strip() for c in config["diagnosis_codes"].split(",") if c.strip()]
        if codes:
            codes_str = "', '".join(codes)
            primary_conditions.append(
                f"any_match(diagnoses, e -> e.diagnosis_code IN ('{codes_str}'))"
            )
            criteria_summary.append(f"Diagnosis codes: {', '.join(codes)}")

    # Diagnosis text search
    if config.get("diagnosis_text"):
        text = config["diagnosis_text"].strip()
        if text:
            # Escape special regex characters except spaces
            text_escaped = re.escape(text).replace("\\ ", " ").replace("\\", "\\\\")
            primary_conditions.append(
                f"any_match(diagnoses, e -> REGEXP_LIKE(e.diagnosis_code_text, '(?is){text_escaped}'))"
            )
            criteria_summary.append(f"Diagnosis text contains: '{text}'")

    # Report text search (primary conditions)
    if config.get("report_text_terms"):
        terms = config["report_text_terms"].strip()
        if terms:
            # Support multiple patterns separated by newlines
            patterns = [t.strip() for t in terms.split("\n") if t.strip()]
            for pattern in patterns:
                # Check if user provided proximity pattern (e.g., term1.{0,50}term2)
                if ".{" in pattern:
                    # User-provided regex pattern
                    pattern_escaped = pattern.replace("\\", "\\\\")
                    primary_conditions.append(
                        f"REGEXP_LIKE(report_text, '(?is){pattern_escaped}')"
                    )
                    criteria_summary.append(f"Report text pattern: {pattern}")
                else:
                    # Simple keyword search
                    pattern_escaped = (
                        re.escape(pattern).replace("\\ ", " ").replace("\\", "\\\\")
                    )
                    primary_conditions.append(
                        f"REGEXP_LIKE(report_text, '(?is){pattern_escaped}')"
                    )
                    criteria_summary.append(f"Report text contains: '{pattern}'")

    # Add primary conditions with OR if we have any
    if primary_conditions:
        if len(primary_conditions) == 1:
            conditions.append(primary_conditions[0])
        else:
            conditions.append("(" + " OR ".join(primary_conditions) + ")")

    # Modality
    if config.get("modalities"):
        modalities = config["modalities"]
        if modalities:
            mod_list = "', '".join([m.lower() for m in modalities])
            conditions.append(f"lower(modality) IN ('{mod_list}')")
            criteria_summary.append(f"Modality: {', '.join(modalities)}")

    # Service name (exam description)
    if config.get("service_name_pattern"):
        pattern = config["service_name_pattern"].strip()
        if pattern:
            # Check if it's a comma-separated list
            if "," in pattern:
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
                # Single pattern
                pattern_escaped = (
                    re.escape(pattern).replace("\\ ", " ").replace("\\", "\\\\")
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
    if config.get("date_range_days") and config["date_range_days"] != "all":
        days = int(config["date_range_days"])
        conditions.append(f"requested_dt >= current_date - INTERVAL '{days}' DAY")
        criteria_summary.append(f"Date range: Last {days} days")

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
    FROM {TRINO_CATALOG}.{TRINO_SCHEMA}.reports
    WHERE {where_clause}
    ORDER BY message_dt DESC
    {limit_clause}
    """

    return sql, criteria_summary


# ============================================================================
# NEGATIVE FILTERING (Python-level)
# ============================================================================

# Default negation patterns - centralized here for use by both detection and highlighting
DEFAULT_NEGATION_PATTERNS = [
    r"no\s+(mri?\s+)?evidence",
    r"without\s+(mri?\s+)?evidence",
    r"negative\s+for",
    r"absence\s+of",
    r"ruled?\s+out",
    r"rules?\s+out",
    r"excluding",
    r"excluded",
    r"evaluat(?:e|ion)\s+(for)?",
    r"concern\s+(for)?",
    r"no\s+\w+\s+(suggest|indication|sign)",
    r"no\s+",  # Catches "no brain metastases", "no definite", etc.
]


def get_default_negation_regex():
    """
    Get the compiled regex for default negation patterns.
    Used by both negation detection and text highlighting.
    """
    return re.compile("|".join(DEFAULT_NEGATION_PATTERNS), re.IGNORECASE)


def check_negation_before_match(
    report_lower, match_start, negation_regex=None, context_chars=50
):
    """
    Check if there's a negation phrase in the text BEFORE a match.

    Only looks before the match (not after) to prevent false positives where
    negation of a different finding appears after the match.
    e.g., "pneumonia... No evidence of lymphadenopathy" should NOT negate pneumonia.

    Args:
        report_lower: Lowercase report text
        match_start: Start position of the match
        negation_regex: Compiled regex for negation patterns (uses default if None)
        context_chars: How many characters before the match to check (default 50)

    Returns:
        bool: True if negation found before match, False otherwise
    """
    if negation_regex is None:
        negation_regex = get_default_negation_regex()

    start_pos = max(0, match_start - context_chars)
    context_before = report_lower[start_pos:match_start]

    return bool(negation_regex.search(context_before))


def has_positive_mention(report_text, search_patterns, negation_patterns=None):
    """
    Check if report has at least one positive mention of search terms.
    Returns True if there's a match without nearby negation.

    Args:
        report_text: Report text to search
        search_patterns: List of regex patterns to search for
        negation_patterns: Optional list of negation regex patterns (uses DEFAULT_NEGATION_PATTERNS if None)
    """
    if not report_text or pd.isna(report_text):
        return False

    report_lower = report_text.lower()

    # Use default or custom negation patterns
    if negation_patterns is None:
        negation_regex = get_default_negation_regex()
    else:
        negation_regex = re.compile("|".join(negation_patterns), re.IGNORECASE)

    # Find all matches for any search pattern
    matches = []
    for pattern in search_patterns:
        try:
            matches.extend(
                re.finditer(pattern, report_lower, re.IGNORECASE | re.DOTALL)
            )
        except re.error:
            continue

    if not matches:
        return False

    # Check each match for local negation using shared utility
    for match in matches:
        if not check_negation_before_match(report_lower, match.start(), negation_regex):
            return True  # Found a positive mention without preceding negation

    return False  # All matches had negation before them


def filter_negative_reports(df, search_patterns, skip_filter_condition=None):
    """
    Filter out reports where ALL mentions are negated.

    Args:
        df: DataFrame with report_text column
        search_patterns: List of regex patterns to search for
        skip_filter_condition: Optional Series boolean mask for reports to skip filtering

    Returns:
        Filtered DataFrame
    """
    print("\n  Applying context-aware negation filtering...")

    initial_count = len(df)

    # Split dataframe if we have skip condition
    if skip_filter_condition is not None:
        df_skip = df[skip_filter_condition].copy()
        df_filter = df[~skip_filter_condition].copy()
        print(f"  • Reports with strong indicators (skipping filter): {len(df_skip)}")
        print(f"  • Reports needing text-based filtering: {len(df_filter)}")
    else:
        df_skip = pd.DataFrame()
        df_filter = df.copy()

    # Apply filter
    if len(df_filter) > 0:
        tqdm.pandas(desc="  Checking reports", unit="report")
        mask = df_filter["report_text"].progress_apply(
            lambda x: has_positive_mention(x, search_patterns)
        )
        df_filter = df_filter[mask]

    # Combine results
    df_final = (
        pd.concat([df_skip, df_filter], ignore_index=True)
        if len(df_skip) > 0
        else df_filter
    )

    removed_count = initial_count - len(df_final)
    print(f"  • Reports with positive mentions: {len(df_final)}")
    print(f"  • Reports filtered out (all mentions negated): {removed_count}")

    return df_final


# ============================================================================
# DATA LOADING
# ============================================================================


def load_cohort_data(config, status_output, approval_callback=None):
    """
    Load cohort data based on configuration.

    Args:
        config: Dictionary with filter parameters
        status_output: Widget output for status messages
        approval_callback: Optional callback function to get user approval (receives sql, criteria_summary)
                          Should return True to proceed, False to cancel

    Returns:
        tuple: (df, criteria_summary) or (None, None) if cancelled
    """
    # Build query
    with status_output:
        status_output.clear_output(wait=True)
        render_status_card(
            "Building SQL Query",
            icon="⏳",
            gradient=PRIMARY_GRADIENT,
            subtitle="Constructing search criteria...",
        )

    sql, criteria_summary = build_cohort_query(config)

    # Get user approval if callback provided
    if approval_callback:
        approved = approval_callback(sql, criteria_summary)
        if not approved:
            return None, None

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

    # Apply negative filtering if text search was used
    # BUT: Keep all reports in the dataframe with inclusion status
    if config.get("report_text_terms") and config.get("apply_negation_filter", True):
        with status_output:
            status_output.clear_output(wait=True)
            render_status_card(
                "Applying Negation Filters",
                icon="⏳",
                gradient=PRIMARY_GRADIENT,
                subtitle="Analyzing reports for negated mentions...",
            )

        # Extract search patterns for negation check
        terms = config["report_text_terms"].strip()
        patterns = [t.strip() for t in terms.split("\n") if t.strip()]

        # Determine which reports have strong indicators (skip filtering)
        has_strong_indicator = pd.Series([False] * len(df), index=df.index)
        if config.get("diagnosis_codes"):
            codes = [
                c.strip() for c in config["diagnosis_codes"].split(",") if c.strip()
            ]
            if codes:

                def has_diagnosis_code(diagnoses):
                    if not diagnoses:
                        return False
                    try:
                        for diag in diagnoses:
                            code = (
                                diag.get("diagnosis_code")
                                if isinstance(diag, dict)
                                else getattr(diag, "diagnosis_code", None)
                            )
                            if code in codes:
                                return True
                    except (TypeError, AttributeError):
                        pass
                    return False

                has_strong_indicator = df["diagnoses"].apply(has_diagnosis_code)

        # Check each report for positive mentions
        print("\n  Analyzing negation status for each report...")
        df["has_strong_indicator"] = has_strong_indicator

        # Initialize has_positive_mention based on strong indicator
        # Reports with strong indicators (DX codes) are always considered positive
        df["has_positive_mention"] = has_strong_indicator.copy()

        # Only check reports without strong indicators for text-based positive mentions
        text_based_mask = ~has_strong_indicator
        if text_based_mask.any():
            from tqdm import tqdm

            tqdm.pandas(desc="  Checking reports", unit="report")
            df.loc[text_based_mask, "has_positive_mention"] = df.loc[
                text_based_mask, "report_text"
            ].progress_apply(lambda x: has_positive_mention(x, patterns))

        # Calculate inclusion status
        df["included_in_cohort"] = (
            df["has_strong_indicator"] | df["has_positive_mention"]
        )

        # Print stats
        total = len(df)
        strong_count = has_strong_indicator.sum()
        included_count = df["included_in_cohort"].sum()
        excluded_count = total - included_count

        print(f"\n  Negation filtering results:")
        print(f"    • Total reports from SQL: {total}")
        print(f"    • Strong indicators (always included): {strong_count}")
        print(
            f"    • Included after negation check: {included_count} ({included_count/total*100:.1f}%)"
        )
        print(
            f"    • Excluded (all mentions negated): {excluded_count} ({excluded_count/total*100:.1f}%)"
        )
    else:
        # No negation filtering - include all
        df["has_strong_indicator"] = False
        df["has_positive_mention"] = True
        df["included_in_cohort"] = True

    # Success message
    with status_output:
        status_output.clear_output(wait=True)
        render_status_card(
            f"Cohort Built Successfully",
            icon="✓",
            gradient=SUCCESS_GRADIENT,
            subtitle=f"{len(df)} reports loaded",
        )

    return df, criteria_summary


# ============================================================================
# EXPORT FUNCTIONALITY
# ============================================================================


def export_cohort(df, annotations, include_report_text=False):
    """
    Export cohort to CSV with annotations.

    Args:
        df: DataFrame with cohort data
        annotations: Dictionary of annotations (index -> {included, notes})
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
        ]
    ].copy()

    # Add annotations
    export_df["manually_included"] = None
    export_df["manual_review_notes"] = ""

    for idx, ann in annotations.items():
        if idx < len(export_df) and ann.get("reviewed", False):
            export_df.at[idx, "manually_included"] = ann.get("included")
            export_df.at[idx, "manual_review_notes"] = ann.get("notes", "")

    # Optionally include report text
    if include_report_text:
        export_df["report_text"] = df["report_text"]

    # Generate filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"cohort_{timestamp}.csv"
    filepath = os.path.join(EXPORT_DIR, filename)

    # Save
    export_df.to_csv(filepath, index=False)

    return filepath
