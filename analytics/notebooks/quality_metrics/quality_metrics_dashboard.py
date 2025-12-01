"""
Quality Metrics Dashboard for Radiology Reports (Voila-optimized).

Streamlined dashboard using Trino for fast loading in Voila.

Usage in Voila:
    from quality_metrics_dashboard import create_landing_page
    create_landing_page()

Usage in Notebook:
    from quality_metrics_dashboard import create_dashboard
    create_dashboard()
"""

import os
import pandas as pd
import ipywidgets as widgets
from IPython.display import display, HTML, clear_output
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from datetime import datetime, timedelta

# Set seaborn style
sns.set_style("whitegrid")
sns.set_palette("husl")


def _connect_trino():
    """Connect to Trino using environment variables."""
    import trino

    TRINO_HOST = os.environ.get("TRINO_HOST", "trino.trino")
    TRINO_PORT = int(os.environ.get("TRINO_PORT", "8080"))
    TRINO_SCHEME = os.environ.get("TRINO_SCHEME", "http")
    TRINO_USER = os.environ.get("TRINO_USER", "trino")
    TRINO_CATALOG = os.environ.get("TRINO_CATALOG", "delta")
    TRINO_SCHEMA = os.environ.get("TRINO_SCHEMA", "default")

    conn = trino.dbapi.connect(
        host=TRINO_HOST,
        port=TRINO_PORT,
        http_scheme=TRINO_SCHEME,
        user=TRINO_USER,
        catalog=TRINO_CATALOG,
        schema=TRINO_SCHEMA,
    )

    return conn


def _load_quality_data(
    table_name="default.reports", date_range_days=None, limit=None
):
    """
    Load quality metrics data from Trino (optimized for speed).

    Parameters
    ----------
    table_name : str
        Table name (e.g., "default.reports")
    date_range_days : int, optional
        Only load reports from last N days
    limit : int, default=50000
        Limit number of rows for performance (safety valve)

    Returns
    -------
    pd.DataFrame
        Report data with calculated TAT fields
    """
    conn = _connect_trino()

    # Parse table name
    table_parts = table_name.split(".")
    if len(table_parts) == 2:
        schema, table = table_parts
    else:
        schema = "default"
        table = table_parts[0]

    # Build WHERE clause - filter on report_finalized_dt to keep time-based plots in range
    where_clauses = []
    if date_range_days:
        cutoff = (datetime.now() - timedelta(days=date_range_days)).strftime("%Y-%m-%d")
        # Filter on the finalized date to ensure time-based plots stay within range
        where_clauses.append(
            f"COALESCE(results_report_status_change_dt, message_dt) >= TIMESTAMP '{cutoff}'"
        )

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    limit_sql = f"LIMIT {limit}" if limit else ""

    # Simplified query - only essential fields for faster loading
    query = f"""
    SELECT
        obr_3_filler_order_number,
        message_dt,
        requested_dt,
        observation_dt,
        results_report_status_change_dt,
        modality,
        sending_facility,
        principal_result_interpreter AS radiologist,

        -- TAT calculations with fallbacks
        COALESCE(results_report_status_change_dt, message_dt) AS report_finalized_dt,

        -- Exam-to-Report TAT in hours
        CAST(DATE_DIFF('second',
            COALESCE(observation_dt, requested_dt),
            COALESCE(results_report_status_change_dt, message_dt)
        ) AS DOUBLE) / 3600.0 AS exam_to_report_hours,

        -- Quality indicators (simplified)
        LENGTH(report_text) AS report_length,
        CASE WHEN report_section_findings IS NOT NULL AND LENGTH(report_section_findings) > 10 THEN true ELSE false END AS has_findings,
        CASE WHEN report_section_impression IS NOT NULL AND LENGTH(report_section_impression) > 10 THEN true ELSE false END AS has_impression,
        CASE WHEN report_section_addendum IS NOT NULL AND LENGTH(report_section_addendum) > 10 THEN true ELSE false END AS has_addendum

    FROM delta.{schema}.{table}
    {where_sql}
    ORDER BY message_dt DESC
    {limit_sql}
    """

    # Use cursor to avoid SQLAlchemy warning
    cursor = conn.cursor()
    cursor.execute(query)
    columns = [desc[0] for desc in cursor.description]
    data = cursor.fetchall()
    df = pd.DataFrame(data, columns=columns)
    cursor.close()
    conn.close()

    # Filter out extreme TAT outliers (> 30 days = 720 hours)
    df.loc[
        (df["exam_to_report_hours"] <= 0) | (df["exam_to_report_hours"] > 720),
        "exam_to_report_hours",
    ] = None

    # Add time dimensions - fix timezone warning by removing tz info before period conversion
    df["report_date"] = pd.to_datetime(df["report_finalized_dt"]).dt.date
    report_dt = pd.to_datetime(df["report_finalized_dt"])
    # Remove timezone info to avoid warning
    if report_dt.dt.tz is not None:
        report_dt = report_dt.dt.tz_localize(None)
    df["report_week"] = report_dt.dt.to_period("W").dt.start_time

    # Store the date range used for filtering (useful for plot titles)
    df.attrs["date_range_days"] = date_range_days

    return df


def create_dashboard(table_name="default.reports", date_range_days=360):
    """
    Create and display quality metrics dashboard with progressive rendering.

    Parameters
    ----------
    table_name : str, default="default.reports"
        Delta table to analyze (reports recommended for Voila)
    date_range_days : int, default=360
        Only analyze reports from last N days (default 360 for comprehensive view)
    """

    # Display header
    display(
        HTML(
            """
        <div style='background: white; padding: 20px 28px; border-bottom: 3px solid #667eea; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);'>
            <h1 style='margin: 0; color: #667eea; font-size: 28px; font-weight: 700;'>Quality & Turnaround Time Dashboard</h1>
            <p style='margin: 8px 0 0 0; color: #666;'>Monitor report quality indicators and TAT metrics</p>
        </div>
    """
        )
    )

    # Create status output for loading messages
    status_output = widgets.Output()
    display(status_output)

    with status_output:
        display(
            HTML(
                """
            <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px 24px; border-radius: 8px; color: white; margin-bottom: 16px;'>
                <div style='display: flex; align-items: center; gap: 12px;'>
                    <div style='font-size: 24px;'>⏳</div>
                    <div>
                        <div style='font-size: 16px; font-weight: 600;'>Loading Data</div>
                        <div style='font-size: 13px; opacity: 0.9; margin-top: 4px;'>
                            Connecting to {table}...
                        </div>
                    </div>
                </div>
            </div>
        """.replace(
                    "{table}", table_name
                )
            )
        )

    # Load data
    df = _load_quality_data(table_name, date_range_days)

    with status_output:
        status_output.clear_output(wait=True)
        tat_count = df["exam_to_report_hours"].notna().sum()
        tat_pct = 100.0 * tat_count / len(df) if len(df) > 0 else 0
        display(
            HTML(
                f"""
            <div style='background: linear-gradient(135deg, #10b981 0%, #059669 100%); padding: 20px 24px; border-radius: 8px; color: white; margin-bottom: 16px;'>
                <div style='display: flex; align-items: center; gap: 12px;'>
                    <div style='font-size: 24px;'>✓</div>
                    <div>
                        <div style='font-size: 16px; font-weight: 600;'>Data Loaded Successfully</div>
                        <div style='font-size: 13px; opacity: 0.9; margin-top: 4px;'>
                            {len(df):,} reports • {tat_count:,} with TAT data ({tat_pct:.1f}%)
                        </div>
                    </div>
                </div>
            </div>
        """
            )
        )

    # Clear loading status and show sections progressively
    import time

    time.sleep(1.5)
    status_output.clear_output()

    # Create output widgets for each section
    summary_output = widgets.Output()
    tat_modality_output = widgets.Output()
    tat_trends_output = widgets.Output()
    radiologist_output = widgets.Output()
    quality_output = widgets.Output()

    # Display all section containers
    display(summary_output)
    display(tat_modality_output)
    display(tat_trends_output)
    display(radiologist_output)
    display(quality_output)

    # Render sections progressively
    with summary_output:
        _render_section_loading("Dashboard Summary")

    with summary_output:
        summary_output.clear_output(wait=True)
        _render_summary(df)

    with tat_modality_output:
        _render_section_loading("Turnaround Time by Modality")

    with tat_modality_output:
        tat_modality_output.clear_output(wait=True)
        _render_tat_by_modality(df)

    with tat_trends_output:
        _render_section_loading("TAT Trends Over Time")

    with tat_trends_output:
        tat_trends_output.clear_output(wait=True)
        _render_tat_trends(df)

    with radiologist_output:
        _render_section_loading("Radiologist Performance")

    with radiologist_output:
        radiologist_output.clear_output(wait=True)
        _render_radiologist_metrics(df)

    with quality_output:
        _render_section_loading("Report Quality Indicators")

    with quality_output:
        quality_output.clear_output(wait=True)
        _render_quality_indicators(df)


def _render_section_loading(section_name):
    """Render loading indicator for a section."""
    display(
        HTML(
            f"""
        <div style='background: #f3f4f6; padding: 16px 20px; border-radius: 8px; border-left: 4px solid #667eea; margin-bottom: 16px;'>
            <div style='display: flex; align-items: center; gap: 12px;'>
                <div style='font-size: 20px;'>⏳</div>
                <div>
                    <div style='font-size: 15px; font-weight: 600; color: #374151;'>Loading {section_name}...</div>
                </div>
            </div>
        </div>
    """
        )
    )


def _render_summary(df):
    """Render summary statistics."""
    total = len(df)
    radiologists = df["radiologist"].nunique()
    facilities = df["sending_facility"].nunique()

    # TAT statistics
    mean_tat = df["exam_to_report_hours"].mean()
    median_tat = df["exam_to_report_hours"].median()
    p90_tat = (
        df["exam_to_report_hours"].quantile(0.9)
        if df["exam_to_report_hours"].notna().sum() > 0
        else None
    )

    # Format with N/A for missing
    mean_tat_str = f"{mean_tat:.1f}h" if pd.notna(mean_tat) else "N/A"
    median_tat_str = f"{median_tat:.1f}h" if pd.notna(median_tat) else "N/A"
    p90_tat_str = f"{p90_tat:.1f}h" if pd.notna(p90_tat) else "N/A"

    addendum_rate = 100.0 * df["has_addendum"].sum() / total if total > 0 else 0
    findings_rate = 100.0 * df["has_findings"].sum() / total if total > 0 else 0
    impression_rate = 100.0 * df["has_impression"].sum() / total if total > 0 else 0

    # Get date range info
    date_range_days = df.attrs.get("date_range_days", None)
    date_range_str = f" (Last {date_range_days} days)" if date_range_days else ""

    display(
        HTML(
            f"""
    <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 24px; border-radius: 12px; color: white; margin-bottom: 20px;'>
        <h2 style='margin: 0 0 20px 0; font-size: 24px;'>Dashboard Summary{date_range_str}</h2>
        <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;'>
            <div style='background: rgba(255,255,255,0.15); padding: 16px; border-radius: 8px;'>
                <div style='font-size: 28px; font-weight: 700;'>{total:,}</div>
                <div style='font-size: 13px; opacity: 0.9;'>Total Reports</div>
            </div>
            <div style='background: rgba(255,255,255,0.15); padding: 16px; border-radius: 8px;'>
                <div style='font-size: 28px; font-weight: 700;'>{radiologists}</div>
                <div style='font-size: 13px; opacity: 0.9;'>Radiologists</div>
            </div>
            <div style='background: rgba(255,255,255,0.15); padding: 16px; border-radius: 8px;'>
                <div style='font-size: 28px; font-weight: 700;'>{facilities}</div>
                <div style='font-size: 13px; opacity: 0.9;'>Facilities</div>
            </div>
        </div>
        <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 16px;'>
            <div style='background: rgba(255,255,255,0.15); padding: 16px; border-radius: 8px;'>
                <div style='font-size: 28px; font-weight: 700;'>{median_tat_str}</div>
                <div style='font-size: 13px; opacity: 0.9;'>Median Exam-to-Report TAT</div>
                <div style='font-size: 11px; opacity: 0.8; margin-top: 4px;'>Mean: {mean_tat_str} | P90: {p90_tat_str}</div>
            </div>
            <div style='background: rgba(255,255,255,0.15); padding: 16px; border-radius: 8px;'>
                <div style='font-size: 28px; font-weight: 700;'>{addendum_rate:.1f}%</div>
                <div style='font-size: 13px; opacity: 0.9;'>Addendum Rate</div>
            </div>
            <div style='background: rgba(255,255,255,0.15); padding: 16px; border-radius: 8px;'>
                <div style='font-size: 28px; font-weight: 700;'>{findings_rate:.0f}% / {impression_rate:.0f}%</div>
                <div style='font-size: 13px; opacity: 0.9;'>Findings / Impression</div>
            </div>
        </div>
    </div>
    """
        )
    )


def _render_tat_by_modality(df):
    """Render TAT by modality charts."""
    display(HTML("<h2 style='margin-top: 40px;'>Turnaround Time by Modality</h2>"))

    # Aggregate by modality
    tat_by_mod = (
        df[df["exam_to_report_hours"].notna()]
        .groupby("modality")
        .agg(
            count=("exam_to_report_hours", "size"),
            median_hours=("exam_to_report_hours", "median"),
            mean_hours=("exam_to_report_hours", "mean"),
            p90_hours=("exam_to_report_hours", lambda x: x.quantile(0.9)),
        )
        .reset_index()
    )

    tat_by_mod = tat_by_mod[tat_by_mod["count"] >= 10].sort_values(
        "count", ascending=False
    )

    if len(tat_by_mod) == 0:
        display(HTML("<p>No TAT data available by modality</p>"))
        return

    # Create plots
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Median TAT
    bars1 = ax1.bar(
        tat_by_mod["modality"], tat_by_mod["median_hours"], color="#667eea", alpha=0.8
    )
    ax1.set_xlabel("Modality", fontsize=11)
    ax1.set_ylabel("Hours", fontsize=11)
    ax1.set_title("Median TAT by Modality", fontsize=12, fontweight="bold")
    ax1.tick_params(axis="x", rotation=45)
    for bar in bars1:
        height = bar.get_height()
        ax1.text(
            bar.get_x() + bar.get_width() / 2.0,
            height,
            f"{height:.1f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    # Volume
    bars2 = ax2.bar(
        tat_by_mod["modality"], tat_by_mod["count"], color="#764ba2", alpha=0.8
    )
    ax2.set_xlabel("Modality", fontsize=11)
    ax2.set_ylabel("Report Count", fontsize=11)
    ax2.set_title("Volume by Modality", fontsize=12, fontweight="bold")
    ax2.tick_params(axis="x", rotation=45)
    for bar in bars2:
        height = bar.get_height()
        ax2.text(
            bar.get_x() + bar.get_width() / 2.0,
            height,
            f"{int(height):,}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    plt.suptitle(
        "Turnaround Time & Volume by Modality", fontsize=14, fontweight="bold", y=1.02
    )
    plt.tight_layout()
    plt.show()

    # Display table
    display(HTML("<h3>Detailed Statistics</h3>"))
    display(tat_by_mod.round(1))


def _render_tat_trends(df):
    """Render TAT trends over time (daily for <60 days, weekly for longer)."""
    display(HTML("<h2 style='margin-top: 40px;'>TAT Trends Over Time</h2>"))

    df_with_tat = df[df["exam_to_report_hours"].notna()].copy()
    if len(df_with_tat) == 0:
        display(HTML("<p>No TAT trend data available</p>"))
        return

    # Determine date range to choose aggregation level
    date_range = (
        df_with_tat["report_date"].max() - df_with_tat["report_date"].min()
    ).days

    # Use daily aggregation for shorter ranges, weekly for longer
    if date_range <= 60:
        # Daily aggregation
        trends = (
            df_with_tat.groupby("report_date")
            .agg(
                count=("exam_to_report_hours", "size"),
                median_hours=("exam_to_report_hours", "median"),
                p90_hours=("exam_to_report_hours", lambda x: x.quantile(0.9)),
            )
            .reset_index()
            .sort_values("report_date")
        )
        x_label = "Date"
        period_label = "Daily"
        trends["x_axis"] = pd.to_datetime(trends["report_date"])
    else:
        # Weekly aggregation
        trends = (
            df_with_tat.groupby("report_week")
            .agg(
                count=("exam_to_report_hours", "size"),
                median_hours=("exam_to_report_hours", "median"),
                p90_hours=("exam_to_report_hours", lambda x: x.quantile(0.9)),
            )
            .reset_index()
            .sort_values("report_week")
        )
        x_label = "Week"
        period_label = "Weekly"
        trends["x_axis"] = trends["report_week"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), height_ratios=[0.6, 0.4])

    # TAT trends
    ax1.plot(
        trends["x_axis"],
        trends["median_hours"],
        color="#667eea",
        linewidth=3,
        marker="o",
        markersize=5,
        label="Median TAT",
    )
    ax1.plot(
        trends["x_axis"],
        trends["p90_hours"],
        color="#ff6b6b",
        linewidth=2,
        linestyle="--",
        marker="s",
        markersize=4,
        label="P90 TAT",
    )
    ax1.set_ylabel("Hours", fontsize=11)
    ax1.set_title(f"TAT Trends ({period_label})", fontsize=12, fontweight="bold")
    ax1.legend(loc="best")
    ax1.grid(True, alpha=0.3)

    # Volume
    ax2.bar(trends["x_axis"], trends["count"], color="#764ba2", alpha=0.7)
    ax2.set_xlabel(x_label, fontsize=11)
    ax2.set_ylabel("Report Count", fontsize=11)
    ax2.set_title(f"{period_label} Volume", fontsize=12, fontweight="bold")
    ax2.grid(True, alpha=0.3, axis="y")

    for ax in [ax1, ax2]:
        ax.tick_params(axis="x", rotation=45)

    plt.suptitle(
        f"Turnaround Time Trends ({date_range} days)",
        fontsize=14,
        fontweight="bold",
        y=0.995,
    )
    plt.tight_layout()
    plt.show()


def _render_radiologist_metrics(df):
    """Render radiologist performance metrics."""
    display(HTML("<h2 style='margin-top: 40px;'>Radiologist Performance</h2>"))

    # Aggregate by radiologist
    rad_metrics = (
        df[df["radiologist"].notna() & df["exam_to_report_hours"].notna()]
        .groupby("radiologist")
        .agg(
            total_reports=("exam_to_report_hours", "size"),
            median_tat=("exam_to_report_hours", "median"),
            mean_tat=("exam_to_report_hours", "mean"),
            addendum_rate=("has_addendum", lambda x: 100.0 * x.sum() / len(x)),
            avg_length=("report_length", "mean"),
        )
        .reset_index()
    )

    rad_metrics = (
        rad_metrics[rad_metrics["total_reports"] >= 50]
        .sort_values("total_reports", ascending=False)
        .head(20)
    )

    if len(rad_metrics) == 0:
        display(HTML("<p>No radiologist data available (min 50 reports required)</p>"))
        return

    display(HTML("<h3>Top 20 Radiologists by Volume (min 50 reports)</h3>"))
    display(rad_metrics.round(1))

    # Scatter plot
    fig, ax = plt.subplots(figsize=(12, 6))
    scatter = ax.scatter(
        rad_metrics["total_reports"],
        rad_metrics["median_tat"],
        s=rad_metrics["total_reports"] / 5,
        c=rad_metrics["addendum_rate"],
        cmap="RdYlGn_r",
        alpha=0.6,
        edgecolors="black",
        linewidth=0.5,
    )
    ax.set_xlabel("Total Reports", fontsize=12)
    ax.set_ylabel("Median TAT (hours)", fontsize=12)
    ax.set_title(
        "Radiologist Performance: Volume vs TAT", fontsize=14, fontweight="bold"
    )
    ax.grid(True, alpha=0.3)
    cbar = plt.colorbar(scatter, ax=ax)
    cbar.set_label("Addendum Rate (%)", fontsize=11)
    plt.tight_layout()
    plt.show()


def _render_quality_indicators(df):
    """Render quality indicators."""
    display(HTML("<h2 style='margin-top: 40px;'>Report Quality Indicators</h2>"))

    total = len(df)
    findings_pct = 100.0 * df["has_findings"].sum() / total if total > 0 else 0
    impression_pct = 100.0 * df["has_impression"].sum() / total if total > 0 else 0
    addendum_pct = 100.0 * df["has_addendum"].sum() / total if total > 0 else 0
    avg_length = df["report_length"].mean()

    display(
        HTML(
            f"""
    <div style='background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); padding: 24px; border-radius: 12px; color: white; margin-bottom: 20px;'>
        <h3 style='margin: 0 0 16px 0;'>Report Completeness Indicators</h3>
        <div style='display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;'>
            <div style='background: rgba(255,255,255,0.2); padding: 12px; border-radius: 6px;'>
                <div style='font-size: 24px; font-weight: 700;'>{findings_pct:.1f}%</div>
                <div style='font-size: 12px;'>Has Findings</div>
            </div>
            <div style='background: rgba(255,255,255,0.2); padding: 12px; border-radius: 6px;'>
                <div style='font-size: 24px; font-weight: 700;'>{impression_pct:.1f}%</div>
                <div style='font-size: 12px;'>Has Impression</div>
            </div>
            <div style='background: rgba(255,255,255,0.2); padding: 12px; border-radius: 6px;'>
                <div style='font-size: 24px; font-weight: 700;'>{addendum_pct:.1f}%</div>
                <div style='font-size: 12px;'>Has Addendum</div>
            </div>
            <div style='background: rgba(255,255,255,0.2); padding: 12px; border-radius: 6px;'>
                <div style='font-size: 24px; font-weight: 700;'>{avg_length:.0f}</div>
                <div style='font-size: 12px;'>Avg Length (chars)</div>
            </div>
        </div>
    </div>
    """
        )
    )


def create_landing_page(table_name="default.reports", date_range_days=360):
    """
    Create landing page with "Launch Dashboard" button for Voila.

    This provides instant page load - data is only loaded when user clicks the button.

    Parameters
    ----------
    table_name : str, default="default.reports"
        Delta table to analyze
    date_range_days : int, default=360
        Only analyze reports from last N days (default 360 for comprehensive view)
    """
    # Container that will hold either the landing page or the dashboard
    container = widgets.VBox(
        layout=widgets.Layout(padding="0", margin="0", min_height="100vh")
    )

    # Global styles
    global_styles = widgets.HTML(
        """
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
            body, .widget-html, .widget-label {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
            }
        </style>
    """
    )

    # Header section
    header = widgets.HTML(
        """
        <div style='background: #667eea; padding: 40px; text-align: center; color: white;'>
            <h1 style='margin: 0 0 12px 0; font-size: 36px; font-weight: 700;'>
                Quality & Turnaround Time Dashboard
            </h1>
            <p style='margin: 0; font-size: 18px;'>
                Monitor report quality indicators and TAT metrics
            </p>
        </div>
    """
    )

    # Title section
    title = widgets.HTML(
        """
        <div style='text-align: center;'>
            <h2 style='color: #1f2937; font-size: 24px; font-weight: 700; margin-bottom: 20px;'>
                Ready to Analyze Your Reports?
            </h2>
        </div>
    """
    )

    # Date range input
    date_range_input = widgets.IntText(
        value=date_range_days,
        description="Days to analyze:",
        min=1,
        max=3650,
        step=30,
        layout=widgets.Layout(width="400px"),
        style={"description_width": "140px"},
    )

    date_help = widgets.HTML(
        """
        <div style='text-align: center; margin-bottom: 20px;'>
            <span style='font-size: 12px; color: #6b7280; line-height: 1;'>
                Number of days of reports to analyze from the most recent data.<br/>
                <span style='font-weight: 500;'>Lower values</span> = faster load time.
            </span>
        </div>
    """
    )

    date_container = widgets.VBox(
        [
            widgets.HBox(
                [date_range_input], layout=widgets.Layout(justify_content="center")
            ),
            date_help,
        ],
        layout=widgets.Layout(padding="0", gap="0"),
    )

    # Launch button
    launch_btn = widgets.Button(
        description="🚀 Launch Dashboard",
        button_style="",
        layout=widgets.Layout(width="280px", height="60px", border="none"),
        style={"button_color": "#667eea", "font_weight": "600", "text_color": "white"},
    )

    # Features section
    features_container = widgets.VBox(
        layout=widgets.Layout(
            background="#f8f9fa", border_radius="12px", padding="28px 32px"
        )
    )

    features_container.children = [
        widgets.HTML(
            """
            <div style='margin-bottom: 20px;'>
                <h3 style='font-size: 14px; font-weight: 700; color: #667eea; text-transform: uppercase; letter-spacing: 0.5px; margin: 0;'>
                    Dashboard Features
                </h3>
            </div>
            <div style='display: grid; gap: 10px;'>
                <div style='display: flex; align-items: flex-start; gap: 12px;'>
                    <span style='color: #10b981; font-weight: 700; font-size: 18px; line-height: 1.5;'>✓</span>
                    <span style='color: #4b5563; font-size: 15px; line-height: 1.6; flex: 1;'>
                        Summary statistics with TAT and quality metrics
                    </span>
                </div>
                <div style='display: flex; align-items: flex-start; gap: 12px;'>
                    <span style='color: #10b981; font-weight: 700; font-size: 18px; line-height: 1.5;'>✓</span>
                    <span style='color: #4b5563; font-size: 15px; line-height: 1.6; flex: 1;'>
                        Turnaround time analysis by modality and trends over time
                    </span>
                </div>
                <div style='display: flex; align-items: flex-start; gap: 12px;'>
                    <span style='color: #10b981; font-weight: 700; font-size: 18px; line-height: 1.5;'>✓</span>
                    <span style='color: #4b5563; font-size: 15px; line-height: 1.6; flex: 1;'>
                        Radiologist performance metrics and volume analysis
                    </span>
                </div>
                <div style='display: flex; align-items: flex-start; gap: 12px;'>
                    <span style='color: #10b981; font-weight: 700; font-size: 18px; line-height: 1.5;'>✓</span>
                    <span style='color: #4b5563; font-size: 15px; line-height: 1.6; flex: 1;'>
                        Report completeness indicators (findings, impression, addendums)
                    </span>
                </div>
                <div style='display: flex; align-items: flex-start; gap: 12px;'>
                    <span style='color: #10b981; font-weight: 700; font-size: 18px; line-height: 1.5;'>✓</span>
                    <span style='color: #4b5563; font-size: 15px; line-height: 1.6; flex: 1;'>
                        Progressive loading with real-time status updates
                    </span>
                </div>
            </div>
        """
        )
    ]

    # Footer note
    footer_note = widgets.HTML(
        """
        <div style='text-align: center;'>
            <span style='color: #6b7280; font-size: 14px; line-height: 1.6;'>
                <strong style='color: #4b5563;'>Note:</strong> Load time depends on date range. Reduce days for faster load.
            </span>
        </div>
    """
    )

    def on_launch(b):
        launch_btn.disabled = True
        launch_btn.description = "⏳ Loading..."
        launch_btn.style.button_color = "#9ca3af"
        date_range_input.disabled = True

        # Get the user-specified date range
        user_date_range = date_range_input.value

        # Create hidden output widget for the new dashboard
        dashboard_output = widgets.Output(layout=widgets.Layout(visibility="hidden"))

        # Add dashboard to container (but hidden)
        container.children = [page_wrapper, dashboard_output]

        # Build dashboard in the hidden output widget
        with dashboard_output:
            create_dashboard(table_name, user_date_range)

        # Two-step replacement: now that dashboard is ready, hide landing page and show dashboard
        # This ensures smooth transition without flickering
        dashboard_output.layout.visibility = "visible"
        container.children = [dashboard_output]

    launch_btn.on_click(on_launch)

    # Build the card body with proper spacing
    card_body = widgets.VBox(
        [
            title,
            date_container,
            widgets.HBox([launch_btn], layout=widgets.Layout(justify_content="center")),
            features_container,
            footer_note,
        ],
        layout=widgets.Layout(padding="24px 40px 40px 40px", gap="20px"),
    )

    # Build the complete card
    card = widgets.VBox(
        [header, card_body],
        layout=widgets.Layout(
            max_width="700px",
            margin="40px auto",
            background="white",
            border_radius="16px",
            border="3px solid #667eea",
            box_shadow="0 20px 60px rgba(0, 0, 0, 0.15)",
            overflow="hidden",
        ),
    )

    # Wrap everything
    page_wrapper = widgets.VBox(
        [global_styles, card], layout=widgets.Layout(padding="20px", margin="0")
    )

    container.children = [page_wrapper]

    display(container)
