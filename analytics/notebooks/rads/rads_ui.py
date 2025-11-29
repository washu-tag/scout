"""
Scout RADS Dashboard - UI Components Module

This module provides the interactive UI components for the RADS dashboard.
"""

import re
import html
import pandas as pd
import numpy as np
import ipywidgets as widgets
from IPython.display import display, HTML
import matplotlib.pyplot as plt
from io import BytesIO
import base64

from rads_builder import (
    export_rads_data,
    calculate_score_distribution,
    calculate_demographics_breakdown,
    calculate_patient_progression,
    get_time_period_filter,
    filter_by_date_range,
    extract_rads_scores,
    PRIMARY_GRADIENT,
    SUCCESS_GRADIENT,
    PURPLE_PRIMARY,
    GREEN_SUCCESS,
    ORANGE_WARNING,
    RED_ERROR,
    LIRADS_SCORES,
)


# ============================================================================
# TEXT HIGHLIGHTING
# ============================================================================


def _highlight_rads_text(report_text, rads_type="LIRADS"):
    """
    Highlight RADS mentions in report text.

    Args:
        report_text: The report text to highlight
        rads_type: 'LIRADS' or 'PIRADS'

    Returns:
        HTML-escaped and highlighted text
    """
    if not report_text or pd.isna(report_text):
        return html.escape("No report text")

    # Extract scores to know what to highlight
    from rads_builder import LIRADS_PATTERNS, PIRADS_PATTERNS

    patterns = LIRADS_PATTERNS if rads_type == "LIRADS" else PIRADS_PATTERNS
    all_matches = []

    # Find all pattern matches
    for pattern in patterns:
        try:
            matches = re.finditer(pattern, report_text, re.IGNORECASE)
            for match in matches:
                all_matches.append(
                    {
                        "start": match.start(),
                        "end": match.end(),
                        "text": report_text[match.start() : match.end()],
                    }
                )
        except re.error:
            continue

    # Remove overlapping matches
    unique_matches = []
    for match in sorted(
        all_matches, key=lambda m: (m["start"], -(m["end"] - m["start"]))
    ):
        overlaps = False
        for existing in unique_matches:
            if not (
                match["end"] <= existing["start"] or match["start"] >= existing["end"]
            ):
                overlaps = True
                break
        if not overlaps:
            unique_matches.append(match)

    # Sort matches by position (reverse order for replacement)
    unique_matches.sort(key=lambda m: m["start"], reverse=True)

    # Build result by replacing from end to start
    result_parts = []
    last_end = len(report_text)

    for match_info in unique_matches:
        # Add text after this match
        result_parts.append(html.escape(report_text[match_info["end"] : last_end]))

        # Add highlighted match
        escaped_match = html.escape(match_info["text"])
        highlighted = f'<span style="background-color: #fef3c7; font-weight: bold; border: 2px solid #f59e0b; padding: 1px 3px; border-radius: 3px;" title="RADS mention">{escaped_match}</span>'
        result_parts.append(highlighted)
        last_end = match_info["start"]

    # Add any remaining text before the first match
    result_parts.append(html.escape(report_text[0:last_end]))

    # Reverse and join
    result_parts.reverse()
    return "".join(result_parts)


# ============================================================================
# VISUALIZATION HELPERS
# ============================================================================


def create_score_distribution_chart(distribution_df, title="RADS Score Distribution"):
    """
    Create a bar chart for RADS score distribution.

    Args:
        distribution_df: DataFrame with 'score', 'count', 'percentage' columns
        title: Chart title

    Returns:
        HTML string with embedded chart image
    """
    if len(distribution_df) == 0:
        return (
            "<div style='padding: 20px; text-align: center; color: #999;'>No data</div>"
        )

    # Create figure
    fig, ax = plt.subplots(figsize=(10, 5))

    # Color map for RADS scores (risk-based)
    score_colors = {
        "LR-1": "#10b981",  # Green (benign)
        "LR-2": "#84cc16",  # Light green
        "LR-3": "#eab308",  # Yellow (intermediate)
        "LR-4": "#f97316",  # Orange (probably HCC)
        "LR-TR": "#a855f7",  # Purple (treated)
        "LR-5": "#dc2626",  # Red (definitely HCC)
        "LR-M": "#7c2d12",  # Dark red (non-HCC malignancy)
        "LR-NC": "#9ca3af",  # Gray (non-categorizable)
        "LR-TIV": "#b91c1c",  # Dark red (tumor in vein)
    }

    colors = [score_colors.get(score, "#6366f1") for score in distribution_df["score"]]

    # Create bar chart
    bars = ax.bar(
        distribution_df["score"], distribution_df["count"], color=colors, alpha=0.8
    )

    # Add value labels on bars
    for bar, count, pct in zip(
        bars, distribution_df["count"], distribution_df["percentage"]
    ):
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            height,
            f"{int(count)}\n({pct}%)",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
        )

    ax.set_xlabel("RADS Score", fontsize=12, fontweight="bold")
    ax.set_ylabel("Number of Reports", fontsize=12, fontweight="bold")
    ax.set_title(title, fontsize=14, fontweight="bold", pad=20)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()

    # Convert to base64 image
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)

    return f'<img src="data:image/png;base64,{img_base64}" style="max-width: 100%; height: auto;" />'


def create_trend_chart(df_list, labels, title="RADS Score Trends"):
    """
    Create a line chart showing RADS score distribution over time periods.

    Args:
        df_list: List of DataFrames (one per time period)
        labels: List of labels for each DataFrame
        title: Chart title

    Returns:
        HTML string with embedded chart image
    """
    if not df_list or all(len(df) == 0 for df in df_list):
        return (
            "<div style='padding: 20px; text-align: center; color: #999;'>No data</div>"
        )

    # Create figure
    fig, ax = plt.subplots(figsize=(12, 6))

    # Calculate distributions for each period
    all_scores = set()
    distributions = []

    for df in df_list:
        if len(df) > 0:
            dist = calculate_score_distribution(df)
            distributions.append(dist)
            all_scores.update(dist["score"].tolist())
        else:
            distributions.append(
                pd.DataFrame({"score": [], "count": [], "percentage": []})
            )

    # Sort scores by priority
    priority_order = [
        "LR-1",
        "LR-2",
        "LR-3",
        "LR-4",
        "LR-TR",
        "LR-5",
        "LR-M",
        "LR-NC",
        "LR-TIV",
    ]
    scores = sorted(
        all_scores,
        key=lambda x: priority_order.index(x) if x in priority_order else 999,
    )

    # Plot lines for each score
    x = np.arange(len(labels))

    for score in scores:
        counts = []
        for dist in distributions:
            if len(dist) > 0 and score in dist["score"].values:
                count = dist[dist["score"] == score]["count"].iloc[0]
                counts.append(count)
            else:
                counts.append(0)

        if sum(counts) > 0:  # Only plot if there's data
            ax.plot(x, counts, marker="o", label=score, linewidth=2, markersize=8)

    ax.set_xlabel("Time Period", fontsize=12, fontweight="bold")
    ax.set_ylabel("Number of Reports", fontsize=12, fontweight="bold")
    ax.set_title(title, fontsize=14, fontweight="bold", pad=20)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.legend(title="RADS Score", bbox_to_anchor=(1.05, 1), loc="upper left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    # Convert to base64 image
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)

    return f'<img src="data:image/png;base64,{img_base64}" style="max-width: 100%; height: auto;" />'


# ============================================================================
# STATISTICS DISPLAY
# ============================================================================


def create_statistics_panel(state):
    """Create overall statistics panel."""
    stats_container = widgets.VBox()

    def render_statistics():
        """Render population statistics."""
        df = state["df"]

        if len(df) == 0:
            stats_container.children = [
                widgets.HTML(
                    "<div style='padding: 20px; text-align: center;'>No data</div>"
                )
            ]
            return

        # Calculate statistics
        total_reports = len(df)

        # Create patient_id column for unique patient identification
        df_copy = df.copy()
        df_copy["patient_id"] = df_copy.apply(
            lambda row: (
                row["epic_mrn"] if pd.notna(row["epic_mrn"]) else row["empi_mr"]
            ),
            axis=1,
        )
        unique_patients = df_copy["patient_id"].nunique()

        # Get one row per patient (most recent report)
        df_patients = df_copy.sort_values(
            "requested_dt", ascending=False
        ).drop_duplicates("patient_id", keep="first")

        # Age statistics (based on unique patients)
        age_mean = df_patients["patient_age"].mean()
        age_median = df_patients["patient_age"].median()
        age_std = df_patients["patient_age"].std()

        # Sex distribution (based on unique patients)
        sex_dist = df_patients["sex"].value_counts()
        sex_html = "<br>".join(
            [
                f"{sex}: {count} ({count/unique_patients*100:.1f}%)"
                for sex, count in sex_dist.items()
            ]
        )

        # Race distribution (top 5, based on unique patients)
        race_dist = df_patients["race"].value_counts().head(5)
        race_html = "<br>".join(
            [
                f"{race}: {count} ({count/unique_patients*100:.1f}%)"
                for race, count in race_dist.items()
            ]
        )

        # Date range
        date_min = df["requested_dt"].min()
        date_max = df["requested_dt"].max()

        stats_html = f"""
        <div style='background: white; padding: 28px; border-radius: 12px; border: 1px solid #e5e7eb;
                    margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);'>
            <!-- Section header -->
            <div style='display: flex; align-items: center; gap: 10px; margin-bottom: 24px;
                        padding-bottom: 12px; border-bottom: 2px solid #f3f4f6;'>
                <span style='font-size: 24px;'>📊</span>
                <h3 style='margin: 0; font-size: 20px; font-weight: 700; color: #1f2937;'>Population Statistics</h3>
            </div>

            <!-- Key metrics -->
            <div style='display: flex; justify-content: space-between; gap: 20px; margin-bottom: 32px; flex-wrap: wrap;'>
                <div style='text-align: center; flex: 1; min-width: 140px; padding: 20px;
                            background: linear-gradient(135deg, #f9fafb 0%, #ffffff 100%);
                            border-radius: 8px; border: 2px solid #e5e7eb;'>
                    <div style='font-size: 36px; font-weight: 700; color: {PURPLE_PRIMARY}; margin-bottom: 6px;'>{total_reports:,}</div>
                    <div style='font-size: 13px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;'>Total Reports</div>
                </div>
                <div style='text-align: center; flex: 1; min-width: 140px; padding: 20px;
                            background: linear-gradient(135deg, #f0fdf4 0%, #ffffff 100%);
                            border-radius: 8px; border: 2px solid #86efac;'>
                    <div style='font-size: 36px; font-weight: 700; color: {GREEN_SUCCESS}; margin-bottom: 6px;'>{unique_patients:,}</div>
                    <div style='font-size: 13px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;'>Unique Patients</div>
                </div>
                <div style='text-align: center; flex: 1; min-width: 140px; padding: 20px;
                            background: linear-gradient(135deg, #f9fafb 0%, #ffffff 100%);
                            border-radius: 8px; border: 2px solid #e5e7eb;'>
                    <div style='font-size: 36px; font-weight: 700; color: {PURPLE_PRIMARY}; margin-bottom: 6px;'>{age_median:.0f}</div>
                    <div style='font-size: 13px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;'>Median Age</div>
                </div>
            </div>

            <!-- Details -->
            <div style='display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px;'>
                <div style='background: #f9fafb; padding: 16px; border-radius: 6px;'>
                    <div style='font-size: 14px; font-weight: 600; margin-bottom: 12px; color: #374151;'>Sex Distribution</div>
                    <div style='font-size: 13px; line-height: 1.8; color: #6b7280;'>
                        {sex_html}
                    </div>
                </div>

                <div style='background: #f9fafb; padding: 16px; border-radius: 6px;'>
                    <div style='font-size: 14px; font-weight: 600; margin-bottom: 12px; color: #374151;'>Race Distribution (Top 5)</div>
                    <div style='font-size: 13px; line-height: 1.8; color: #6b7280;'>
                        {race_html}
                    </div>
                </div>

                <div style='background: #f9fafb; padding: 16px; border-radius: 6px;'>
                    <div style='font-size: 14px; font-weight: 600; margin-bottom: 12px; color: #374151;'>Date Range</div>
                    <div style='font-size: 13px; line-height: 1.8; color: #6b7280;'>
                        <div><strong>From:</strong> {date_min.strftime('%Y-%m-%d')}</div>
                        <div><strong>To:</strong> {date_max.strftime('%Y-%m-%d')}</div>
                    </div>
                </div>
            </div>
        </div>
        """

        stats_container.children = [widgets.HTML(stats_html)]

    # Store render function
    state["render_statistics"] = render_statistics

    # Initial render
    render_statistics()

    return stats_container


# ============================================================================
# SCORE DISTRIBUTION DISPLAY
# ============================================================================


def create_score_distribution_panel(state):
    """Create RADS score distribution panel with chart."""
    dist_container = widgets.VBox()

    def render_distribution():
        """Render score distribution."""
        df = state["df"]

        if len(df) == 0:
            dist_container.children = [
                widgets.HTML(
                    "<div style='padding: 20px; text-align: center;'>No data</div>"
                )
            ]
            return

        # Calculate distributions - both by reports and by unique patients
        dist_df_reports = calculate_score_distribution(df, by_patients=False)
        dist_df_patients = calculate_score_distribution(df, by_patients=True)

        # Merge the two distributions
        dist_merged = dist_df_reports.merge(
            dist_df_patients,
            on="score",
            how="outer",
            suffixes=("_reports", "_patients"),
        )
        dist_merged = dist_merged.fillna(0)

        # Sort by LI-RADS severity (highest risk first)
        priority_order = [
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
        dist_merged["sort_key"] = dist_merged["score"].apply(
            lambda x: priority_order.index(x) if x in priority_order else 999
        )
        dist_merged = dist_merged.sort_values("sort_key").drop("sort_key", axis=1)

        # Create chart (using all detected scores count)
        chart_html = create_score_distribution_chart(
            dist_df_reports, "LI-RADS Score Distribution (All Detected Scores)"
        )

        # Create table with both counts
        table_rows = []
        for _, row in dist_merged.iterrows():
            table_rows.append(
                f"""
                <tr>
                    <td style='padding: 6px; border-bottom: 1px solid #e5e7eb;'><strong>{row['score']}</strong></td>
                    <td style='padding: 6px; border-bottom: 1px solid #e5e7eb; text-align: right;'>{int(row['count_reports'])}</td>
                    <td style='padding: 6px; border-bottom: 1px solid #e5e7eb; text-align: right;'>{row['percentage_reports']:.1f}%</td>
                    <td style='padding: 6px; border-bottom: 1px solid #e5e7eb; text-align: right;'>{int(row['count_patients'])}</td>
                    <td style='padding: 6px; border-bottom: 1px solid #e5e7eb; text-align: right;'>{row['percentage_patients']:.1f}%</td>
                </tr>
            """
            )

        dist_html = f"""
        <div style='background: white; padding: 28px; border-radius: 12px; border: 1px solid #e5e7eb;
                    margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);'>
            <!-- Section header -->
            <div style='display: flex; align-items: center; gap: 10px; margin-bottom: 12px;'>
                <span style='font-size: 24px;'>📈</span>
                <h2 style='margin: 0; font-size: 22px; color: #1f2937; font-weight: 700;'>RADS Score Distribution</h2>
            </div>
            <p style='margin: 0 0 24px 0; font-size: 14px; color: #6b7280; line-height: 1.6;'>
                📌 <strong>Note:</strong> Counts ALL detected scores. A single report may mention multiple scores (e.g., different lesions).
            </p>

            <!-- Chart and Table Side by Side -->
            <div style='display: flex; gap: 24px; align-items: start;'>
                <!-- Chart on the left -->
                <div style='flex: 1; min-width: 0;'>
                    {chart_html}
                </div>

                <!-- Table on the right -->
                <div style='flex: 0 0 auto; min-width: 400px;'>
                    <table style='width: 100%; border-collapse: collapse;'>
                        <thead>
                            <tr style='background: #f3f4f6;'>
                                <th style='padding: 8px; text-align: left; border-bottom: 2px solid {PURPLE_PRIMARY};'>Score</th>
                                <th colspan='2' style='padding: 8px; text-align: center; border-bottom: 2px solid {PURPLE_PRIMARY};'>Total Mentions</th>
                                <th colspan='2' style='padding: 8px; text-align: center; border-bottom: 2px solid {PURPLE_PRIMARY};'>Unique Patients</th>
                            </tr>
                            <tr style='background: #f3f4f6;'>
                                <th style='padding: 8px; text-align: left; border-bottom: 2px solid {PURPLE_PRIMARY};'></th>
                                <th style='padding: 8px; text-align: right; border-bottom: 2px solid {PURPLE_PRIMARY};'>Count</th>
                                <th style='padding: 8px; text-align: right; border-bottom: 2px solid {PURPLE_PRIMARY};'>%</th>
                                <th style='padding: 8px; text-align: right; border-bottom: 2px solid {PURPLE_PRIMARY};'>Count</th>
                                <th style='padding: 8px; text-align: right; border-bottom: 2px solid {PURPLE_PRIMARY};'>%</th>
                            </tr>
                        </thead>
                        <tbody>
                            {''.join(table_rows)}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        """

        dist_container.children = [widgets.HTML(dist_html)]

    # Store render function
    state["render_distribution"] = render_distribution

    # Initial render
    render_distribution()

    return dist_container


# ============================================================================
# TIME COMPARISON PANEL
# ============================================================================


def create_time_trend_chart(df, granularity="month"):
    """
    Create a time-based trend chart showing RADS score distribution over time.

    Args:
        df: DataFrame with RADS data
        granularity: 'day', 'week', or 'month'

    Returns:
        HTML string with embedded chart image
    """
    if len(df) == 0:
        return (
            "<div style='padding: 20px; text-align: center; color: #999;'>No data</div>"
        )

    df_copy = df.copy()

    # Group by time period (remove timezone to avoid warning)
    if granularity == "day":
        df_copy["period"] = (
            pd.to_datetime(df_copy["requested_dt"]).dt.tz_localize(None).dt.date
        )
        date_format = "%Y-%m-%d"
        xlabel = "Date"
    elif granularity == "week":
        # Remove timezone before converting to period
        df_copy["period"] = (
            pd.to_datetime(df_copy["requested_dt"])
            .dt.tz_localize(None)
            .dt.to_period("W")
            .apply(lambda r: r.start_time)
        )
        date_format = "%Y-%m-%d"
        xlabel = "Week Starting"
    else:  # month
        # Remove timezone before converting to period
        df_copy["period"] = (
            pd.to_datetime(df_copy["requested_dt"])
            .dt.tz_localize(None)
            .dt.to_period("M")
            .apply(lambda r: r.start_time)
        )
        date_format = "%Y-%m"
        xlabel = "Month"

    # Get all unique periods
    periods = sorted(df_copy["period"].unique())

    if len(periods) == 0:
        return (
            "<div style='padding: 20px; text-align: center; color: #999;'>No data</div>"
        )

    # Count scores by period
    score_counts = {}
    for period in periods:
        period_df = df_copy[df_copy["period"] == period]
        dist = calculate_score_distribution(period_df)
        score_counts[period] = {
            row["score"]: row["count"] for _, row in dist.iterrows()
        }

    # Get all unique scores
    all_scores = set()
    for counts in score_counts.values():
        all_scores.update(counts.keys())

    # Sort scores by priority
    priority_order = [
        "LR-1",
        "LR-2",
        "LR-3",
        "LR-4",
        "LR-TR",
        "LR-5",
        "LR-M",
        "LR-NC",
        "LR-TIV",
    ]
    scores = sorted(
        all_scores,
        key=lambda x: priority_order.index(x) if x in priority_order else 999,
    )

    # Color map for scores
    score_colors = {
        "LR-1": "#10b981",  # Green
        "LR-2": "#84cc16",  # Light green
        "LR-3": "#eab308",  # Yellow
        "LR-4": "#f97316",  # Orange
        "LR-5": "#dc2626",  # Red
        "LR-M": "#7c2d12",  # Dark red
        "LR-NC": "#9ca3af",  # Gray
        "LR-TIV": "#b91c1c",  # Dark red
    }

    # Create stacked area chart
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [2, 1]}
    )

    # Prepare data for stacked area chart
    x_indices = np.arange(len(periods))
    x_labels = [
        p.strftime(date_format) if hasattr(p, "strftime") else str(p) for p in periods
    ]

    # Top chart: Stacked area showing absolute counts
    bottom = np.zeros(len(periods))
    for score in scores:
        counts = [score_counts[p].get(score, 0) for p in periods]
        ax1.fill_between(
            x_indices,
            bottom,
            bottom + counts,
            label=score,
            alpha=0.8,
            color=score_colors.get(score, "#6366f1"),
        )
        bottom += counts

    ax1.set_xlabel(xlabel, fontsize=12, fontweight="bold")
    ax1.set_ylabel("Number of Reports", fontsize=12, fontweight="bold")
    ax1.set_title(
        "RADS Score Distribution Over Time (Stacked)",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )
    ax1.set_xticks(x_indices[:: max(1, len(x_indices) // 12)])  # Show max 12 labels
    ax1.set_xticklabels(
        x_labels[:: max(1, len(x_indices) // 12)], rotation=45, ha="right"
    )
    ax1.legend(title="RADS Score", bbox_to_anchor=(1.05, 1), loc="upper left")
    ax1.grid(axis="y", alpha=0.3)

    # Bottom chart: Individual score trends (lines)
    for score in scores:
        counts = [score_counts[p].get(score, 0) for p in periods]
        if sum(counts) > 0:  # Only plot if there's data
            ax2.plot(
                x_indices,
                counts,
                marker="o",
                label=score,
                linewidth=2,
                markersize=6,
                color=score_colors.get(score, "#6366f1"),
            )

    ax2.set_xlabel(xlabel, fontsize=12, fontweight="bold")
    ax2.set_ylabel("Number of Reports", fontsize=12, fontweight="bold")
    ax2.set_title(
        "Individual RADS Score Trends", fontsize=14, fontweight="bold", pad=20
    )
    ax2.set_xticks(x_indices[:: max(1, len(x_indices) // 12)])
    ax2.set_xticklabels(
        x_labels[:: max(1, len(x_indices) // 12)], rotation=45, ha="right"
    )
    ax2.legend(title="RADS Score", bbox_to_anchor=(1.05, 1), loc="upper left")
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()

    # Convert to base64 image
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)

    return f'<img src="data:image/png;base64,{img_base64}" style="max-width: 100%; height: auto;" />'


def create_time_comparison_panel(state):
    """Create time comparison panel."""
    comparison_container = widgets.VBox()

    # Analysis type selector
    analysis_type = widgets.Dropdown(
        options=[
            ("Trend Analysis (Over Time)", "trend"),
            ("Period Comparison (Side-by-Side)", "comparison"),
        ],
        value="trend",
        description="Analysis:",
        layout=widgets.Layout(width="400px"),
        style={"description_width": "80px"},
    )

    # Granularity selector for trend analysis
    granularity_selector = widgets.Dropdown(
        options=[("By Month", "month"), ("By Week", "week"), ("By Day", "day")],
        value="month",
        description="Granularity:",
        layout=widgets.Layout(width="300px"),
        style={"description_width": "80px"},
    )

    # Time period selector for comparison
    period_selector = widgets.Dropdown(
        options=[
            ("Current Month vs Last Month", "month"),
            ("Current Quarter vs Last Quarter", "quarter"),
            ("This Year vs 5 Years Ago", "year"),
        ],
        value="month",
        description="Compare:",
        layout=widgets.Layout(width="400px"),
        style={"description_width": "80px"},
    )

    comparison_output = widgets.Output()
    controls_container = widgets.HBox()

    def update_controls():
        """Update visible controls based on analysis type."""
        if analysis_type.value == "trend":
            controls_container.children = [granularity_selector]
        else:
            controls_container.children = [period_selector]

    def on_analysis_type_change(change):
        """Handle analysis type change."""
        update_controls()
        render_analysis()

    def render_analysis():
        """Render the selected analysis."""
        with comparison_output:
            comparison_output.clear_output(wait=True)

            # Show progress indicator
            display(
                HTML(
                    f"""
                <div style='background: {PRIMARY_GRADIENT}; padding: 24px; border-radius: 12px;
                            color: white; margin: 20px 0; text-align: center;'>
                    <div style='font-size: 28px; margin-bottom: 8px;'>⏳</div>
                    <div style='font-size: 18px; font-weight: 600;'>Generating Visualization...</div>
                    <div style='font-size: 14px; margin-top: 8px; opacity: 0.9;'>This may take a few moments</div>
                </div>
            """
                )
            )

        # Process in background
        import time

        time.sleep(0.1)  # Small delay to ensure progress indicator displays

        with comparison_output:
            comparison_output.clear_output(wait=True)

            full_df = state["full_df"]

            if analysis_type.value == "trend":
                # Trend analysis
                granularity = granularity_selector.value

                # Create trend chart
                chart_html = create_time_trend_chart(full_df, granularity)

                # Summary statistics
                total_reports = len(full_df)
                date_range = f"{full_df['requested_dt'].min().strftime('%Y-%m-%d')} to {full_df['requested_dt'].max().strftime('%Y-%m-%d')}"

                trend_html = f"""
                <div style='background: white; padding: 16px; border-radius: 6px; border: 1px solid #e5e7eb; margin-top: 16px;'>
                    <h3 style='margin: 0 0 16px 0; font-size: 16px; color: {PURPLE_PRIMARY};'>Trend Analysis</h3>

                    <div style='background: #f3f4f6; padding: 12px; border-radius: 4px; margin-bottom: 16px;'>
                        <div style='font-size: 13px;'>
                            <strong>Total Reports:</strong> {total_reports:,}<br>
                            <strong>Date Range:</strong> {date_range}<br>
                            <strong>Granularity:</strong> {granularity.capitalize()}
                        </div>
                    </div>

                    <!-- Trend Chart -->
                    <div style='margin-bottom: 20px;'>
                        {chart_html}
                    </div>

                    <div style='background: #fef3c7; padding: 12px; border-radius: 4px; border-left: 3px solid #f59e0b;'>
                        <div style='font-size: 12px; color: #78350f;'>
                            <strong>💡 Interpretation:</strong><br>
                            • <strong>Top chart (Stacked):</strong> Shows total volume and composition over time<br>
                            • <strong>Bottom chart (Lines):</strong> Shows individual score trends - useful for spotting changes in specific categories
                        </div>
                    </div>
                </div>
                """

                display(HTML(trend_html))

            else:
                # Period comparison
                on_period_comparison_change()

    def on_period_comparison_change():
        """Handle period comparison."""
        full_df = state["full_df"]  # Use full dataset, not filtered
        period_value = period_selector.value

        if period_value == "month":
            start1, end1, label1 = get_time_period_filter("current_month")
            start2, end2, label2 = get_time_period_filter("last_month")
        elif period_value == "quarter":
            start1, end1, label1 = get_time_period_filter("current_quarter")
            start2, end2, label2 = get_time_period_filter("last_quarter")
        elif period_value == "year":
            start1, end1, label1 = get_time_period_filter("current_year")
            start2, end2, label2 = get_time_period_filter("5_years_ago")
        else:
            return

        # Filter data
        df1 = filter_by_date_range(full_df, start1, end1)
        df2 = filter_by_date_range(full_df, start2, end2)

        # Calculate distributions
        dist1 = calculate_score_distribution(df1)
        dist2 = calculate_score_distribution(df2)

        # Create comparison chart
        chart_html = create_trend_chart(
            [df2, df1], [label2, label1], f"RADS Score Comparison: {label2} vs {label1}"
        )

        # Create side-by-side tables
        def dist_to_table_html(dist, df_len, label):
            if len(dist) == 0:
                return f"<div style='padding: 20px; text-align: center; color: #999;'>No data for {label}</div>"

            rows = []
            for _, row in dist.iterrows():
                rows.append(
                    f"""
                    <tr>
                        <td style='padding: 6px; border-bottom: 1px solid #e5e7eb;'><strong>{row['score']}</strong></td>
                        <td style='padding: 6px; border-bottom: 1px solid #e5e7eb; text-align: right;'>{int(row['count'])}</td>
                        <td style='padding: 6px; border-bottom: 1px solid #e5e7eb; text-align: right;'>{row['percentage']}%</td>
                    </tr>
                """
                )

            return f"""
            <div style='flex: 1; min-width: 300px;'>
                <h4 style='margin: 0 0 12px 0; font-size: 14px; color: {PURPLE_PRIMARY};'>{label}</h4>
                <div style='font-size: 12px; margin-bottom: 8px;'>Total: {df_len} reports</div>
                <table style='width: 100%; border-collapse: collapse;'>
                    <thead>
                        <tr style='background: #f3f4f6;'>
                            <th style='padding: 8px; text-align: left; border-bottom: 2px solid {PURPLE_PRIMARY};'>Score</th>
                            <th style='padding: 8px; text-align: right; border-bottom: 2px solid {PURPLE_PRIMARY};'>Count</th>
                            <th style='padding: 8px; text-align: right; border-bottom: 2px solid {PURPLE_PRIMARY};'>%</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(rows)}
                    </tbody>
                </table>
            </div>
            """

        comparison_html = f"""
        <div style='background: white; padding: 16px; border-radius: 6px; border: 1px solid #e5e7eb; margin-top: 16px;'>
            <h3 style='margin: 0 0 16px 0; font-size: 16px; color: {PURPLE_PRIMARY};'>Time Period Comparison</h3>

            <!-- Trend Chart -->
            <div style='margin-bottom: 24px;'>
                {chart_html}
            </div>

            <!-- Side-by-side tables -->
            <div style='display: flex; gap: 24px; flex-wrap: wrap;'>
                {dist_to_table_html(dist2, len(df2), label2)}
                {dist_to_table_html(dist1, len(df1), label1)}
            </div>
        </div>
        """

        display(HTML(comparison_html))

    # Set up event handlers
    analysis_type.observe(on_analysis_type_change, names="value")
    granularity_selector.observe(lambda change: render_analysis(), names="value")
    period_selector.observe(lambda change: on_period_comparison_change(), names="value")

    # Initial setup
    update_controls()

    # Build container
    comparison_container.children = [
        widgets.HTML(
            f"""
            <div style='background: white; padding: 28px; border-radius: 12px; border: 1px solid #e5e7eb;
                        margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);'>
                <!-- Section header -->
                <div style='display: flex; align-items: center; gap: 10px; margin-bottom: 12px;'>
                    <span style='font-size: 24px;'>⏱️</span>
                    <h2 style='margin: 0; font-size: 22px; color: #1f2937; font-weight: 700;'>Time-Based Analysis</h2>
                </div>
                <p style='margin: 0 0 20px 0; font-size: 14px; color: #6b7280; line-height: 1.6;'>
                    📊 Visualize RADS score trends over time or compare distributions between specific periods
                </p>
            </div>
        """
        ),
        analysis_type,
        controls_container,
        comparison_output,
    ]

    # Trigger initial render of trend analysis
    render_analysis()

    return comparison_container


# ============================================================================
# DEMOGRAPHICS PANEL
# ============================================================================


def create_demographics_charts(df):
    """
    Create demographic visualization charts (counting unique patients).

    Returns:
        Tuple of (age_chart_html, sex_chart_html, race_chart_html)
    """
    if len(df) == 0:
        empty_msg = (
            "<div style='padding: 20px; text-align: center; color: #999;'>No data</div>"
        )
        return empty_msg, empty_msg, empty_msg

    # Score colors (demographics charts)
    score_colors = {
        "LR-1": "#10b981",  # Benign
        "LR-2": "#84cc16",  # Probably benign
        "LR-3": "#eab308",  # Intermediate
        "LR-4": "#f97316",  # Probably HCC
        "LR-TR": "#a855f7",  # Treated
        "LR-5": "#dc2626",  # Definitely HCC
        "LR-M": "#7c2d12",  # Non-HCC malignancy
        "LR-NC": "#9ca3af",  # Non-categorizable
        "LR-TIV": "#b91c1c",  # Tumor in vein
    }

    # Prepare data - count unique patients only
    df_copy = df.copy()
    df_copy["patient_id"] = df_copy.apply(
        lambda row: row["epic_mrn"] if pd.notna(row["epic_mrn"]) else row["empi_mr"],
        axis=1,
    )

    # Get one row per patient (most recent report)
    df_patients = df_copy.sort_values("requested_dt", ascending=False).drop_duplicates(
        "patient_id", keep="first"
    )

    df_patients["age_group"] = pd.cut(
        df_patients["patient_age"],
        bins=[0, 40, 50, 60, 70, 80, 200],
        labels=["<40", "40-49", "50-59", "60-69", "70-79", "80+"],
    )

    # 1. AGE GROUP STACKED BAR CHART
    fig, ax = plt.subplots(figsize=(12, 6))

    age_crosstab = pd.crosstab(
        df_patients["age_group"], df_patients["primary_rads_score"]
    )

    # Sort columns by score priority
    priority_order = [
        "LR-1",
        "LR-2",
        "LR-3",
        "LR-4",
        "LR-TR",
        "LR-5",
        "LR-M",
        "LR-NC",
        "LR-TIV",
    ]
    cols_sorted = [c for c in priority_order if c in age_crosstab.columns]
    age_crosstab = age_crosstab[cols_sorted]

    age_crosstab.plot(
        kind="bar",
        stacked=True,
        ax=ax,
        color=[score_colors.get(c, "#6366f1") for c in cols_sorted],
        alpha=0.8,
        width=0.7,
    )

    ax.set_xlabel("Age Group", fontsize=12, fontweight="bold")
    ax.set_ylabel("Number of Unique Patients", fontsize=12, fontweight="bold")
    ax.set_title(
        "RADS Score Distribution by Age Group (Unique Patients)",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )
    ax.legend(title="RADS Score", bbox_to_anchor=(1.05, 1), loc="upper left")
    ax.grid(axis="y", alpha=0.3)
    plt.xticks(rotation=0)
    plt.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    age_chart = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)

    age_chart_html = f'<img src="data:image/png;base64,{age_chart}" style="max-width: 100%; height: auto;" />'

    # 2. SEX PIE CHARTS (one per RADS score)
    fig, axes = plt.subplots(2, 4, figsize=(16, 8))
    axes = axes.flatten()

    sex_crosstab = pd.crosstab(df_patients["primary_rads_score"], df_patients["sex"])

    for idx, score in enumerate(cols_sorted[:8]):  # Max 8 scores
        ax = axes[idx]
        if score in sex_crosstab.index:
            data = sex_crosstab.loc[score]
            if data.sum() > 0:
                ax.pie(
                    data,
                    labels=data.index,
                    autopct="%1.1f%%",
                    startangle=90,
                    colors=["#3b82f6", "#ec4899", "#8b5cf6"][: len(data)],
                )
                ax.set_title(f"{score}\n(n={data.sum()})", fontweight="bold")
            else:
                ax.text(
                    0.5,
                    0.5,
                    "No data",
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                )
                ax.set_title(score, fontweight="bold")
        else:
            ax.text(
                0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes
            )
            ax.set_title(score, fontweight="bold")
        ax.axis("equal")

    # Hide unused subplots
    for idx in range(len(cols_sorted), 8):
        axes[idx].axis("off")

    plt.suptitle(
        "Sex Distribution by RADS Score (Unique Patients)",
        fontsize=14,
        fontweight="bold",
        y=1.02,
    )
    plt.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    sex_chart = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)

    sex_chart_html = f'<img src="data:image/png;base64,{sex_chart}" style="max-width: 100%; height: auto;" />'

    # 3. RACE HORIZONTAL BAR CHART (top 10 races)
    fig, ax = plt.subplots(figsize=(12, 8))

    race_crosstab = pd.crosstab(df_patients["race"], df_patients["primary_rads_score"])

    # Get top 10 races by total count
    race_totals = race_crosstab.sum(axis=1).sort_values(ascending=False).head(10)
    race_crosstab_top = race_crosstab.loc[race_totals.index]

    # Sort columns
    cols_sorted_race = [c for c in priority_order if c in race_crosstab_top.columns]
    race_crosstab_top = race_crosstab_top[cols_sorted_race]

    race_crosstab_top.plot(
        kind="barh",
        stacked=True,
        ax=ax,
        color=[score_colors.get(c, "#6366f1") for c in cols_sorted_race],
        alpha=0.8,
    )

    ax.set_xlabel("Number of Unique Patients", fontsize=12, fontweight="bold")
    ax.set_ylabel("Race", fontsize=12, fontweight="bold")
    ax.set_title(
        "RADS Score Distribution by Race - Top 10 (Unique Patients)",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )
    ax.legend(title="RADS Score", bbox_to_anchor=(1.05, 1), loc="upper left")
    ax.grid(axis="x", alpha=0.3)
    plt.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    race_chart = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)

    race_chart_html = f'<img src="data:image/png;base64,{race_chart}" style="max-width: 100%; height: auto;" />'

    return age_chart_html, sex_chart_html, race_chart_html


def create_demographics_panel(state):
    """Create demographics breakdown panel with graphical visualizations."""
    demo_container = widgets.VBox()

    def render_demographics():
        """Render demographics breakdown with charts."""
        df = state["df"]

        if len(df) == 0:
            demo_container.children = [
                widgets.HTML(
                    "<div style='padding: 20px; text-align: center;'>No data</div>"
                )
            ]
            return

        # Show progress
        demo_container.children = [
            widgets.HTML(
                f"""
            <div style='background: {PRIMARY_GRADIENT}; padding: 24px; border-radius: 12px;
                        color: white; margin: 20px 0; text-align: center;'>
                <div style='font-size: 28px; margin-bottom: 8px;'>⏳</div>
                <div style='font-size: 18px; font-weight: 600;'>Generating Demographics Visualizations...</div>
            </div>
        """
            )
        ]

        # Generate charts
        age_chart_html, sex_chart_html, race_chart_html = create_demographics_charts(df)

        # Also get the crosstab tables for reference
        demographics = calculate_demographics_breakdown(df)

        # Build comprehensive HTML
        demo_html = f"""
        <div style='background: white; padding: 28px; border-radius: 12px; border: 1px solid #e5e7eb;
                    margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);'>
            <!-- Section header -->
            <div style='display: flex; align-items: center; gap: 10px; margin-bottom: 12px;'>
                <span style='font-size: 24px;'>👥</span>
                <h2 style='margin: 0; font-size: 22px; color: #1f2937; font-weight: 700;'>Demographics Analysis by RADS Score</h2>
            </div>
            <p style='margin: 0 0 24px 0; font-size: 14px; color: #6b7280; line-height: 1.6;'>
                📌 <strong>Note:</strong> All demographics counts are based on unique patients (using most recent report).
                Each patient is counted only once, even if they have multiple reports.
            </p>

            <!-- Age Group Chart -->
            <div style='margin-bottom: 32px;'>
                <h3 style='margin: 0 0 16px 0; font-size: 18px; color: #374151; font-weight: 600;'>Age Distribution</h3>
                {age_chart_html}
            </div>

            <!-- Sex Distribution Charts -->
            <div style='margin-bottom: 32px;'>
                <h3 style='margin: 0 0 16px 0; font-size: 18px; color: #374151; font-weight: 600;'>Sex Distribution by RADS Score</h3>
                {sex_chart_html}
            </div>

            <!-- Race Distribution Chart -->
            <div style='margin-bottom: 32px;'>
                <h3 style='margin: 0 0 16px 0; font-size: 18px; color: #374151; font-weight: 600;'>Race Distribution</h3>
                {race_chart_html}
            </div>

            <!-- Data Tables (collapsible) -->
            <details style='margin-top: 24px;'>
                <summary style='cursor: pointer; font-weight: 600; font-size: 14px; color: {PURPLE_PRIMARY}; padding: 8px; background: #f3f4f6; border-radius: 4px;'>
                    📊 View Detailed Tables
                </summary>
                <div style='margin-top: 16px; padding: 16px; background: #f9fafb; border-radius: 4px;'>
                    <style>
                        .demographics-table {{
                            width: 100%;
                            border-collapse: collapse;
                            font-size: 12px;
                            margin-bottom: 20px;
                        }}
                        .demographics-table th {{
                            background: #f3f4f6;
                            padding: 8px;
                            text-align: left;
                            border-bottom: 2px solid {PURPLE_PRIMARY};
                        }}
                        .demographics-table td {{
                            padding: 6px 8px;
                            border-bottom: 1px solid #e5e7eb;
                        }}
                        .demographics-table tbody tr:hover {{
                            background: #f9fafb;
                        }}
                    </style>

                    <h5 style='margin: 0 0 8px 0; font-size: 13px; color: #374151;'>Age Group Cross-tabulation</h5>
                    <div style='overflow-x: auto; margin-bottom: 20px;'>
                        {demographics.get('age', pd.DataFrame()).to_html(classes='demographics-table', border=0)}
                    </div>

                    <h5 style='margin: 0 0 8px 0; font-size: 13px; color: #374151;'>Sex Cross-tabulation</h5>
                    <div style='overflow-x: auto; margin-bottom: 20px;'>
                        {demographics.get('sex', pd.DataFrame()).to_html(classes='demographics-table', border=0)}
                    </div>

                    <h5 style='margin: 0 0 8px 0; font-size: 13px; color: #374151;'>Race Cross-tabulation</h5>
                    <div style='overflow-x: auto;'>
                        {demographics.get('race', pd.DataFrame()).to_html(classes='demographics-table', border=0)}
                    </div>
                </div>
            </details>
        </div>
        """

        demo_container.children = [widgets.HTML(demo_html)]

    # Store render function
    state["render_demographics"] = render_demographics

    # Initial render
    render_demographics()

    return demo_container


# ============================================================================
# PATIENT PROGRESSION PANEL
# ============================================================================


def create_patient_progression_panel(state):
    """Create patient progression panel (longitudinal analysis) with per-patient browsing."""
    prog_container = widgets.VBox()

    def render_progression():
        """Render patient progression with statistics and per-patient view."""
        df = state["df"]

        if len(df) == 0:
            prog_container.children = [
                widgets.HTML(
                    "<div style='padding: 20px; text-align: center;'>No data</div>"
                )
            ]
            return

        progression_df = calculate_patient_progression(df)

        if len(progression_df) == 0:
            prog_container.children = [
                widgets.HTML(
                    """
                <div style='background: white; padding: 16px; border-radius: 6px; border: 1px solid #e5e7eb;'>
                    <h3 style='margin: 0 0 12px 0; font-size: 16px; color: #667eea;'>Patient Progression</h3>
                    <div style='padding: 20px; text-align: center; color: #999;'>
                        No patients with multiple reports in the current dataset
                    </div>
                </div>
            """
                )
            ]
            return

        # Calculate overall progression statistics
        unique_patients = progression_df["patient_id"].nunique()
        total_reports = len(progression_df)
        avg_reports_per_patient = (
            total_reports / unique_patients if unique_patients > 0 else 0
        )

        # Score changes
        score_changes = progression_df[progression_df["score_change"].notna()]
        increased = (score_changes["score_change"] > 0).sum()
        decreased = (score_changes["score_change"] < 0).sum()
        stable = (score_changes["score_change"] == 0).sum()

        # Create overall statistics panel - enhanced design
        stats_html = f"""
        <div style='background: white; padding: 28px; border-radius: 12px; border: 1px solid #e5e7eb;
                    margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);'>
            <!-- Section header -->
            <div style='display: flex; align-items: center; gap: 10px; margin-bottom: 20px;
                        padding-bottom: 12px; border-bottom: 2px solid #f3f4f6;'>
                <span style='font-size: 24px;'>📊</span>
                <h3 style='margin: 0; font-size: 20px; font-weight: 700; color: #1f2937;'>Progression Summary</h3>
            </div>

            <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px;'>
                <div style='text-align: center; flex: 1; min-width: 140px; padding: 16px;
                            background: linear-gradient(135deg, #f9fafb 0%, #ffffff 100%);
                            border-radius: 8px; border: 2px solid #e5e7eb;'>
                    <div style='font-size: 32px; font-weight: 700; color: {PURPLE_PRIMARY}; margin-bottom: 4px;'>{unique_patients}</div>
                    <div style='font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;'>Patients Tracked</div>
                </div>
                <div style='text-align: center; flex: 1; min-width: 140px; padding: 16px;
                            background: linear-gradient(135deg, #f0fdf4 0%, #ffffff 100%);
                            border-radius: 8px; border: 2px solid #86efac;'>
                    <div style='font-size: 32px; font-weight: 700; color: {GREEN_SUCCESS}; margin-bottom: 4px;'>{avg_reports_per_patient:.1f}</div>
                    <div style='font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;'>Avg Reports</div>
                </div>
                <div style='text-align: center; flex: 1; min-width: 140px; padding: 16px;
                            background: linear-gradient(135deg, #fef3c7 0%, #ffffff 100%);
                            border-radius: 8px; border: 2px solid #fbbf24;'>
                    <div style='font-size: 32px; font-weight: 700; color: {ORANGE_WARNING}; margin-bottom: 4px;'>{increased}</div>
                    <div style='font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;'>Progressed ⬆️</div>
                </div>
                <div style='text-align: center; flex: 1; min-width: 140px; padding: 16px;
                            background: linear-gradient(135deg, #f0fdf4 0%, #ffffff 100%);
                            border-radius: 8px; border: 2px solid #86efac;'>
                    <div style='font-size: 32px; font-weight: 700; color: {GREEN_SUCCESS}; margin-bottom: 4px;'>{decreased}</div>
                    <div style='font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;'>Improved ⬇️</div>
                </div>
                <div style='text-align: center; flex: 1; min-width: 140px; padding: 16px;
                            background: linear-gradient(135deg, #f9fafb 0%, #ffffff 100%);
                            border-radius: 8px; border: 2px solid #e5e7eb;'>
                    <div style='font-size: 32px; font-weight: 700; color: #6b7280; margin-bottom: 4px;'>{stable}</div>
                    <div style='font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;'>Stable →</div>
                </div>
            </div>
        </div>
        """

        # Get unique patients for dropdown
        patient_ids = sorted(progression_df["patient_id"].unique())

        # Patient selector
        patient_selector = widgets.Dropdown(
            options=[(f"Patient: {pid}", pid) for pid in patient_ids],
            description="Select Patient:",
            layout=widgets.Layout(width="400px"),
            style={"description_width": "120px"},
        )

        # Navigation buttons
        prev_patient_btn = widgets.Button(
            description="◀ Previous Patient",
            layout=widgets.Layout(width="auto"),
            button_style="",
        )

        next_patient_btn = widgets.Button(
            description="Next Patient ▶",
            layout=widgets.Layout(width="auto"),
            button_style="success",
        )

        patient_position_label = widgets.HTML()

        # Patient report output
        patient_output = widgets.Output()

        def render_patient_timeline(patient_id):
            """Render timeline for selected patient."""
            with patient_output:
                patient_output.clear_output(wait=True)

                # Get all reports for this patient
                patient_reports = progression_df[
                    progression_df["patient_id"] == patient_id
                ].sort_values("requested_dt")

                # Get original report data
                original_df = df[
                    df.apply(
                        lambda r: (
                            r["epic_mrn"] if pd.notna(r["epic_mrn"]) else r["empi_mr"]
                        )
                        == patient_id,
                        axis=1,
                    )
                ].sort_values("requested_dt")

                # Collect all unique scores this patient has had across all reports
                all_patient_scores = set()
                for _, report in original_df.iterrows():
                    rads_scores = report.get("rads_scores")
                    if rads_scores is not None and len(rads_scores) > 0:
                        all_patient_scores.update(rads_scores)

                # Score color mapping
                score_colors = {
                    "LR-1": "#10b981",
                    "LR-2": "#84cc16",
                    "LR-3": "#eab308",
                    "LR-4": "#f97316",
                    "LR-TR": "#a855f7",
                    "LR-5": "#dc2626",
                    "LR-M": "#7c2d12",
                    "LR-NC": "#9ca3af",
                    "LR-TIV": "#b91c1c",
                }

                # Build timeline visualization
                timeline_rows = []
                for idx, (_, row) in enumerate(patient_reports.iterrows()):
                    requested_dt = (
                        row["requested_dt"].strftime("%Y-%m-%d")
                        if pd.notna(row["requested_dt"])
                        else "N/A"
                    )

                    # Get full report text and all RADS scores from original dataframe
                    accession = row["obr_3_filler_order_number"]
                    report_data = original_df[
                        original_df["obr_3_filler_order_number"] == accession
                    ]

                    if len(report_data) > 0:
                        report_text = (
                            str(report_data.iloc[0]["report_text"])
                            if pd.notna(report_data.iloc[0]["report_text"])
                            else "No report text available"
                        )
                        # Highlight RADS mentions
                        highlighted_text = _highlight_rads_text(
                            report_text, state.get("rads_type", "LIRADS")
                        )

                        # Get all RADS scores from this report
                        all_scores_in_report = report_data.iloc[0].get(
                            "rads_scores", []
                        )
                        if all_scores_in_report and len(all_scores_in_report) > 0:
                            # Sort scores by severity
                            priority_order = [
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
                            sorted_scores = sorted(
                                all_scores_in_report,
                                key=lambda x: (
                                    priority_order.index(x)
                                    if x in priority_order
                                    else 999
                                ),
                            )
                            primary_score = sorted_scores[0]  # Most severe
                            scores_display = ", ".join(sorted_scores)
                            lesion_count = len(all_scores_in_report)
                            dot_color = score_colors.get(primary_score, "#6b7280")
                        else:
                            primary_score = "N/A"
                            scores_display = "None detected"
                            lesion_count = 0
                            dot_color = "#9ca3af"
                    else:
                        highlighted_text = "Report text not available"
                        primary_score = "N/A"
                        scores_display = "N/A"
                        lesion_count = 0
                        dot_color = "#9ca3af"

                    # Clinical description helper
                    descriptions = {
                        "LR-1": "Definitely benign",
                        "LR-2": "Probably benign",
                        "LR-3": "Intermediate probability",
                        "LR-4": "Probably HCC",
                        "LR-5": "Definitely HCC",
                        "LR-M": "Non-HCC malignancy",
                        "LR-TR": "Post-treatment",
                        "LR-TIV": "Tumor in vein",
                        "LR-NC": "Non-categorizable",
                    }

                    timeline_rows.append(
                        f"""
                    <div style='display: flex; gap: 20px; padding: 16px 0; border-left: 3px solid #e5e7eb; position: relative; margin-left: 12px;'>
                        <!-- Timeline dot -->
                        <div style='position: absolute; left: -13px; top: 20px; width: 24px; height: 24px;
                                    background: {dot_color}; border-radius: 50%; border: 4px solid white;
                                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);'></div>

                        <!-- Date column -->
                        <div style='flex-shrink: 0; width: 90px; padding-left: 28px; color: #6b7280; font-size: 13px; font-weight: 500;'>
                            {requested_dt}
                        </div>

                        <!-- Content column -->
                        <div style='flex-grow: 1; padding-bottom: 12px;'>
                            <div style='font-weight: 700; font-size: 16px; margin-bottom: 4px; color: #1f2937;'>
                                {primary_score} - {descriptions.get(primary_score, 'Unknown')}
                            </div>
                            <div style='font-size: 14px; color: #6b7280; margin-bottom: 8px;'>
                                {scores_display if lesion_count > 1 else descriptions.get(primary_score, 'No findings')}
                            </div>
                            <div style='font-size: 12px; color: #9ca3af; margin-bottom: 8px;'>
                                Lesions: {lesion_count} • Accession: {html.escape(str(accession))}
                            </div>

                            <!-- Expandable report text -->
                            <details style='margin-top: 8px;'>
                                <summary style='cursor: pointer; font-size: 12px; color: {PURPLE_PRIMARY}; padding: 6px 8px; background: #f3f4f6; border-radius: 4px; font-weight: 600;'>
                                    📄 View Full Report Text
                                </summary>
                                <div style='margin-top: 12px; padding: 12px; background: #f9fafb; border-radius: 4px; border: 1px solid #e5e7eb;'>
                                    <div style='margin-bottom: 8px; padding: 6px; background: #fef3c7; border-radius: 4px; font-size: 11px;'>
                                        <strong>Legend:</strong>
                                        <span style='background-color: #fef3c7; padding: 2px 6px; margin: 0 6px; border: 1px solid #f59e0b; border-radius: 2px;'>RADS Mention</span>
                                    </div>
                                    <div style='white-space: pre-wrap; font-family: monospace; font-size: 12px; line-height: 1.6; max-height: 400px; overflow-y: auto;'>
                                        {highlighted_text}
                                    </div>
                                </div>
                            </details>
                        </div>
                    </div>
                    """
                    )

                # Format all unique scores
                if all_patient_scores:
                    # Sort scores by priority
                    priority_order = [
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
                    sorted_scores = sorted(
                        all_patient_scores,
                        key=lambda x: (
                            priority_order.index(x) if x in priority_order else 999
                        ),
                    )
                    all_scores_summary = ", ".join(sorted_scores)
                    scores_summary_html = f"""
                    <div style='background: #fef3c7; padding: 10px; border-radius: 4px; margin-bottom: 16px; border-left: 3px solid #f59e0b;'>
                        <div style='font-size: 12px; color: #78350f;'>
                            <strong>📊 All RADS Scores for this Patient:</strong> {html.escape(all_scores_summary)}
                        </div>
                    </div>
                    """
                else:
                    scores_summary_html = ""

                # Get patient demographics from first report
                first_report = original_df.iloc[0]
                patient_age = first_report.get("patient_age", "Unknown")
                patient_sex = first_report.get("sex", "Unknown")
                total_reports = len(patient_reports)
                date_span = (
                    patient_reports["requested_dt"].max()
                    - patient_reports["requested_dt"].min()
                ).days
                months_span = max(1, date_span // 30)

                patient_html = f"""
                <div style='background: white; padding: 24px; border-radius: 8px; border: 1px solid #e5e7eb;'>
                    <!-- Title -->
                    <h2 style='margin: 0 0 12px 0; font-size: 24px; color: #1f2937; font-weight: 700;'>
                        Patient Progression Tracking
                    </h2>

                    <!-- Patient info -->
                    <div style='font-size: 15px; color: #374151; margin-bottom: 20px; font-weight: 500;'>
                        Patient: {html.escape(str(patient_id))} | Age: {patient_age} | Sex: {patient_sex}
                        <div style='margin-top: 4px; font-size: 13px; color: #6b7280; font-weight: 400;'>
                            Total Reports: {total_reports} | Span: {months_span} months
                        </div>
                    </div>

                    <!-- Timeline -->
                    <div style='margin-top: 24px;'>
                        {''.join(timeline_rows)}
                    </div>
                </div>
                """

                display(HTML(patient_html))

        def update_navigation():
            """Update navigation buttons and position label."""
            current_idx = patient_ids.index(patient_selector.value)
            total = len(patient_ids)

            # Update position label
            patient_position_label.value = f"<span style='font-weight: 600;'>Patient {current_idx + 1} of {total}</span>"

            # Update button states
            prev_patient_btn.disabled = current_idx == 0
            next_patient_btn.disabled = current_idx >= total - 1

        def on_patient_change(change):
            render_patient_timeline(change["new"])
            update_navigation()

        def on_prev_patient(b):
            current_idx = patient_ids.index(patient_selector.value)
            if current_idx > 0:
                patient_selector.value = patient_ids[current_idx - 1]

        def on_next_patient(b):
            current_idx = patient_ids.index(patient_selector.value)
            if current_idx < len(patient_ids) - 1:
                patient_selector.value = patient_ids[current_idx + 1]

        patient_selector.observe(on_patient_change, names="value")
        prev_patient_btn.on_click(on_prev_patient)
        next_patient_btn.on_click(on_next_patient)

        # Render first patient by default
        if len(patient_ids) > 0:
            render_patient_timeline(patient_ids[0])
            update_navigation()

        # Navigation bar
        nav_bar = widgets.HBox(
            [prev_patient_btn, patient_position_label, next_patient_btn],
            layout=widgets.Layout(justify_content="space-between", margin="8px 0"),
        )

        # Build complete panel
        prog_container.children = [
            widgets.HTML(stats_html),
            patient_selector,
            nav_bar,
            patient_output,
        ]

    # Store render function
    state["render_progression"] = render_progression

    # Initial render
    render_progression()

    return prog_container


# ============================================================================
# REPORT BROWSER
# ============================================================================


def create_report_browser(state):
    """Create report browsing interface."""
    prev_btn = widgets.Button(
        description="◀ Previous", layout=widgets.Layout(width="auto")
    )
    next_btn = widgets.Button(
        description="Next ▶",
        button_style="success",
        layout=widgets.Layout(width="auto"),
    )
    position_label = widgets.HTML()
    report_output = widgets.Output(
        layout=widgets.Layout(flex="1 1 auto", max_width="1200px")
    )

    def render_report():
        """Render current report."""
        df = state["df"]
        current_pos = state["current_pos"]

        if len(df) == 0:
            with report_output:
                report_output.clear_output(wait=True)
                display(
                    HTML(
                        "<div style='padding: 20px; text-align: center;'>No reports</div>"
                    )
                )
            position_label.value = "<span style='font-weight: 600;'>No reports</span>"
            prev_btn.disabled = True
            next_btn.disabled = True
            return

        row = df.iloc[current_pos]

        # Update position
        position_label.value = f"<span style='font-weight: 600;'>Report {current_pos + 1} of {len(df)}</span>"

        # Update navigation buttons
        prev_btn.disabled = current_pos == 0
        next_btn.disabled = current_pos >= len(df) - 1

        # Render report
        patient_id = html.escape(
            str(row["epic_mrn"] if pd.notna(row["epic_mrn"]) else row["empi_mr"])
        )
        requested_dt = (
            row["requested_dt"].strftime("%Y-%m-%d")
            if pd.notna(row["requested_dt"])
            else "N/A"
        )
        accession = html.escape(str(row.get("obr_3_filler_order_number", "Unknown")))
        patient_age = html.escape(str(row.get("patient_age", "Unknown")))
        sex = html.escape(str(row.get("sex", "Unknown")))
        modality = html.escape(str(row.get("modality", "Unknown")))
        service_name = html.escape(str(row.get("service_name", "Unknown")))

        report_text = (
            str(row["report_text"])
            if pd.notna(row["report_text"])
            else "No report text"
        )
        highlighted_text = _highlight_rads_text(
            report_text, state.get("rads_type", "LIRADS")
        )

        # RADS score display
        all_scores = row.get("rads_scores", [])
        if all_scores and len(all_scores) > 0:
            # Sort scores by severity
            priority_order = [
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
            sorted_scores = sorted(
                all_scores,
                key=lambda x: priority_order.index(x) if x in priority_order else 999,
            )
            all_scores_display = ", ".join(sorted_scores)
            lesion_count = len(all_scores)
        else:
            all_scores_display = "None detected"
            lesion_count = 0

        with report_output:
            report_output.clear_output(wait=True)
            report_html = f"""
            <div style='background: white; padding: 20px; border-radius: 8px; border: 1px solid #e5e7eb;'>
                <!-- Header -->
                <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 2px solid #f3f4f6;'>
                    <div>
                        <h3 style='margin: 0; font-size: 18px; font-weight: 700; color: #1f2937;'>Report {current_pos + 1} of {len(df)}</h3>
                        <div style='margin-top: 4px; font-size: 13px; color: #6b7280;'>Accession: {accession}</div>
                    </div>
                    <div style='background: {PRIMARY_GRADIENT}; color: white; padding: 10px 20px; border-radius: 8px; font-size: 15px; font-weight: 600; box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
                        {all_scores_display}
                    </div>
                </div>

                <!-- Two-column layout -->
                <div style='display: grid; grid-template-columns: 320px 1fr; gap: 20px;'>
                    <!-- Left: Patient Info -->
                    <div style='display: flex; flex-direction: column; gap: 16px;'>
                        <div style='background: #f9fafb; padding: 16px; border-radius: 6px; font-size: 14px;'>
                            <div style='font-weight: 600; margin-bottom: 12px; font-size: 15px; color: #374151;'>Patient Information</div>
                            <div style='margin-bottom: 6px; color: #6b7280;'><strong style='color: #374151;'>ID:</strong> {patient_id}</div>
                            <div style='margin-bottom: 6px; color: #6b7280;'><strong style='color: #374151;'>Age:</strong> {patient_age} • <strong style='color: #374151;'>Sex:</strong> {sex}</div>
                            <div style='margin-bottom: 6px; color: #6b7280;'><strong style='color: #374151;'>Date:</strong> {requested_dt}</div>
                            <div style='margin-bottom: 6px; color: #6b7280;'><strong style='color: #374151;'>Modality:</strong> {modality}</div>
                            <div style='color: #6b7280;'><strong style='color: #374151;'>Service:</strong> {service_name}</div>
                        </div>

                        <div style='background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); padding: 16px; border-radius: 6px; font-size: 14px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);'>
                            <div style='font-weight: 600; margin-bottom: 12px; font-size: 15px; color: #78350f;'>RADS Findings</div>
                            <div style='margin-bottom: 6px; color: #78350f;'><strong>Scores:</strong> {all_scores_display}</div>
                            <div style='color: #78350f;'><strong>Lesion Count:</strong> {lesion_count}</div>
                        </div>
                    </div>

                    <!-- Right: Report Text -->
                    <div>
                        <div style='margin-bottom: 12px; padding: 10px 12px; background: #f3f4f6; border-radius: 4px; font-size: 13px; color: #374151;'>
                            <strong>Legend:</strong>
                            <span style='background-color: #fef3c7; padding: 3px 8px; margin: 0 8px; border: 1px solid #f59e0b; border-radius: 3px;'>RADS Mention</span>
                        </div>
                        <div style='background: #f9fafb; padding: 20px; border-radius: 6px; border: 1px solid #e5e7eb;
                                    max-height: 500px; overflow-y: auto; white-space: pre-wrap;
                                    font-family: monospace; font-size: 14px; line-height: 1.7; color: #374151;'>{highlighted_text}</div>
                    </div>
                </div>
            </div>
            """
            display(HTML(report_html))

    def on_prev(b):
        state["current_pos"] = max(0, state["current_pos"] - 1)
        render_report()

    def on_next(b):
        state["current_pos"] = min(len(state["df"]) - 1, state["current_pos"] + 1)
        render_report()

    prev_btn.on_click(on_prev)
    next_btn.on_click(on_next)

    # Store render function
    state["render_report"] = render_report

    nav_bar = widgets.HBox(
        [prev_btn, position_label, next_btn],
        layout=widgets.Layout(justify_content="space-between", margin="0 0 16px 0"),
    )

    # Initial render
    render_report()

    return nav_bar, report_output


# ============================================================================
# EXPORT CONTROLS
# ============================================================================


def create_export_controls(state):
    """Create export controls."""
    include_report_text_check = widgets.Checkbox(
        value=False,
        description="Include full report text",
        style={"description_width": "initial"},
    )

    export_button = widgets.Button(
        description="📥 Export RADS Data CSV",
        button_style="info",
        layout=widgets.Layout(width="auto"),
    )

    export_output = widgets.Output()

    def on_export(b):
        with export_output:
            export_output.clear_output(wait=True)

            try:
                filepath = export_rads_data(
                    state["df"], include_report_text_check.value
                )

                display(
                    HTML(
                        f"""
                    <div style='background: {SUCCESS_GRADIENT}; padding: 16px; border-radius: 8px;
                                color: white; margin-top: 12px;'>
                        <div style='font-weight: 600; margin-bottom: 8px;'>✓ Export Successful</div>
                        <div style='font-size: 14px; opacity: 0.95;'>
                            Exported {len(state['df'])} reports<br>
                            File: <code style='background: rgba(255,255,255,0.2); padding: 2px 6px;
                                               border-radius: 4px;'>{filepath}</code>
                        </div>
                    </div>
                """
                    )
                )
            except Exception as e:
                display(
                    HTML(
                        f"""
                    <div style='background: {RED_ERROR}; padding: 16px; border-radius: 8px;
                                color: white; margin-top: 12px;'>
                        <div style='font-weight: 600; margin-bottom: 8px;'>✗ Export Failed</div>
                        <div style='font-size: 14px; opacity: 0.95;'>Error: {str(e)}</div>
                    </div>
                """
                    )
                )

    export_button.on_click(on_export)

    container = widgets.HBox(
        [include_report_text_check, export_button, export_output],
        layout=widgets.Layout(gap="16px", align_items="center"),
    )

    return container
