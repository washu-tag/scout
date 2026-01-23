# Notebook Template

## Notebook File Structure

The notebook should have a single cell that imports and calls the landing page function:

```python
from {module_name} import create_landing_page

# Launch dashboard with instant page load
# Data loads only when "Launch Dashboard" button is clicked
create_landing_page(
    table_name="default.reports",
    date_range_days=360
)
```

## Dashboard Module Structure

Use `sample_dashboard.py` in this skill directory as the canonical reference for code patterns. Key patterns:

```python
"""
{Title} Dashboard for Radiology Reports (Voila-optimized).

Streamlined dashboard using Trino for fast loading in Voila.

Usage in Voila:
    from {module_name} import create_landing_page
    create_landing_page()

Usage in Notebook:
    from {module_name} import create_dashboard
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
```

## Key Patterns

### 1. Data Loading Function

Load all needed fields in one query, derive additional columns in Python:

```python
def _load_data(table_name="default.reports", date_range_days=None, limit=None):
    """Load data from Trino (optimized for speed)."""
    conn = _connect_trino()

    # Parse table name
    table_parts = table_name.split(".")
    if len(table_parts) == 2:
        schema, table = table_parts
    else:
        schema = "default"
        table = table_parts[0]

    # Build WHERE clause - ALWAYS include radiologist filter
    where_clauses = [
        # REQUIRED: Exclude reports with blank radiologist names
        "principal_result_interpreter IS NOT NULL",
        "TRIM(principal_result_interpreter) <> ''",
    ]
    if date_range_days:
        cutoff = (datetime.now() - timedelta(days=date_range_days)).strftime("%Y-%m-%d")
        where_clauses.append(
            f"COALESCE(results_report_status_change_dt, message_dt) >= TIMESTAMP '{cutoff}'"
        )

    where_sql = f"WHERE {' AND '.join(where_clauses)}"
    limit_sql = f"LIMIT {limit}" if limit else ""

    # Simplified query - only essential fields for faster loading
    query = f"""
    SELECT
        message_dt,
        requested_dt,
        observation_dt,
        results_report_status_change_dt,
        modality,
        sending_facility,
        principal_result_interpreter AS radiologist,

        -- Derived fields calculated in SQL for efficiency
        COALESCE(results_report_status_change_dt, message_dt) AS report_finalized_dt,

        -- REQUIRED: TAT calculation with fallback (requested_dt preferred, observation_dt fallback)
        -- Without this fallback pattern, TAT will be NULL for most reports
        CAST(DATE_DIFF('second',
            COALESCE(requested_dt, observation_dt),
            COALESCE(results_report_status_change_dt, message_dt)
        ) AS DOUBLE) / 3600.0 AS order_to_report_hours,

        -- Quality indicators
        LENGTH(report_text) AS report_length,
        CASE WHEN report_section_findings IS NOT NULL AND LENGTH(report_section_findings) > 10 THEN true ELSE false END AS has_findings,
        CASE WHEN report_section_impression IS NOT NULL AND LENGTH(report_section_impression) > 10 THEN true ELSE false END AS has_impression

    FROM delta.{schema}.{table}
    {where_sql}
    ORDER BY message_dt DESC
    {limit_sql}
    """

    cursor = conn.cursor()
    cursor.execute(query)
    columns = [desc[0] for desc in cursor.description]
    data = cursor.fetchall()
    df = pd.DataFrame(data, columns=columns)
    cursor.close()
    conn.close()

    # Filter outliers in Python
    df.loc[
        (df["order_to_report_hours"] <= 0) | (df["order_to_report_hours"] > 720),
        "order_to_report_hours",
    ] = None

    # Add time dimensions - handle timezone
    df["report_date"] = pd.to_datetime(df["report_finalized_dt"]).dt.date
    report_dt = pd.to_datetime(df["report_finalized_dt"])
    if report_dt.dt.tz is not None:
        report_dt = report_dt.dt.tz_localize(None)
    df["report_week"] = report_dt.dt.to_period("W").dt.start_time

    # Store metadata
    df.attrs["date_range_days"] = date_range_days

    return df
```

### 2. Dashboard with Progressive Section Loading

```python
def create_dashboard(table_name="default.reports", date_range_days=360):
    """Create and display dashboard with progressive rendering."""

    # Display header - always use #667eea purple
    display(HTML("""
        <div style='background: white; padding: 20px 28px; border-bottom: 3px solid #667eea; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);'>
            <h1 style='margin: 0; color: #667eea; font-size: 28px; font-weight: 700;'>{TITLE}</h1>
            <p style='margin: 8px 0 0 0; color: #666;'>{SUBTITLE}</p>
        </div>
    """))

    # Create status output for loading messages
    status_output = widgets.Output()
    display(status_output)

    with status_output:
        display(HTML("""
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
        """.replace("{table}", table_name)))

    # Load data
    df = _load_data(table_name, date_range_days)

    with status_output:
        status_output.clear_output(wait=True)
        display(HTML(f"""
            <div style='background: linear-gradient(135deg, #10b981 0%, #059669 100%); padding: 20px 24px; border-radius: 8px; color: white; margin-bottom: 16px;'>
                <div style='display: flex; align-items: center; gap: 12px;'>
                    <div style='font-size: 24px;'>✓</div>
                    <div>
                        <div style='font-size: 16px; font-weight: 600;'>Data Loaded Successfully</div>
                        <div style='font-size: 13px; opacity: 0.9; margin-top: 4px;'>
                            {len(df):,} reports loaded
                        </div>
                    </div>
                </div>
            </div>
        """))

    import time
    time.sleep(1.5)
    status_output.clear_output()

    # Create output widgets for each section
    section1_output = widgets.Output()
    section2_output = widgets.Output()
    # ... add more sections as needed

    # Display all section containers
    display(section1_output)
    display(section2_output)

    # Render sections progressively with loading indicators
    with section1_output:
        _render_section_loading("Section 1 Name")

    with section1_output:
        section1_output.clear_output(wait=True)
        _render_section1(df)

    with section2_output:
        _render_section_loading("Section 2 Name")

    with section2_output:
        section2_output.clear_output(wait=True)
        _render_section2(df)


def _render_section_loading(section_name):
    """Render loading indicator for a section."""
    display(HTML(f"""
        <div style='background: #f3f4f6; padding: 16px 20px; border-radius: 8px; border-left: 4px solid #667eea; margin-bottom: 16px;'>
            <div style='display: flex; align-items: center; gap: 12px;'>
                <div style='font-size: 20px;'>⏳</div>
                <div>
                    <div style='font-size: 15px; font-weight: 600; color: #374151;'>Loading {section_name}...</div>
                </div>
            </div>
        </div>
    """))
```

### 3. Summary Card Pattern

```python
def _render_summary(df):
    """Render summary statistics."""
    total = len(df)
    # Calculate your metrics here

    # Get date range info for title
    date_range_days = df.attrs.get("date_range_days", None)
    date_range_str = f" (Last {date_range_days} days)" if date_range_days else ""

    display(HTML(f"""
    <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 24px; border-radius: 12px; color: white; margin-bottom: 20px;'>
        <h2 style='margin: 0 0 20px 0; font-size: 24px;'>Dashboard Summary{date_range_str}</h2>
        <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;'>
            <div style='background: rgba(255,255,255,0.15); padding: 16px; border-radius: 8px;'>
                <div style='font-size: 28px; font-weight: 700;'>{total:,}</div>
                <div style='font-size: 13px; opacity: 0.9;'>Total Reports</div>
            </div>
            <!-- Add more cards -->
        </div>
    </div>
    """))
```

### 4. Chart Section Pattern

```python
def _render_chart_section(df):
    """Render a chart section."""
    display(HTML("<h2 style='margin-top: 40px;'>Section Title</h2>"))

    # Aggregate data
    aggregated = df.groupby("category").agg(
        count=("field", "size"),
        median=("field", "median"),
    ).reset_index()

    # Filter to meaningful data
    aggregated = aggregated[aggregated["count"] >= 10].sort_values("count", ascending=False)

    if len(aggregated) == 0:
        display(HTML("<p>No data available</p>"))
        return

    # Create plots
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Plot 1
    ax1.bar(aggregated["category"], aggregated["median"], color="#667eea", alpha=0.8)
    ax1.set_xlabel("Category", fontsize=11)
    ax1.set_ylabel("Value", fontsize=11)
    ax1.set_title("Chart 1 Title", fontsize=12, fontweight="bold")
    ax1.tick_params(axis="x", rotation=45)

    # Plot 2
    ax2.bar(aggregated["category"], aggregated["count"], color="#764ba2", alpha=0.8)
    ax2.set_xlabel("Category", fontsize=11)
    ax2.set_ylabel("Count", fontsize=11)
    ax2.set_title("Chart 2 Title", fontsize=12, fontweight="bold")
    ax2.tick_params(axis="x", rotation=45)

    plt.suptitle("Overall Title", fontsize=14, fontweight="bold", y=1.02)
    plt.tight_layout()
    plt.show()

    # Display table using pandas
    display(HTML("<h3>Detailed Statistics</h3>"))
    display(aggregated.round(1))
```

### 5. Adaptive Time Aggregation

For time series, use daily aggregation for short ranges, weekly for longer:

```python
def _render_trends(df):
    """Render trends over time with adaptive aggregation."""
    df_filtered = df[df["metric"].notna()].copy()

    # Determine date range
    date_range = (df_filtered["report_date"].max() - df_filtered["report_date"].min()).days

    # Use daily for <60 days, weekly for longer
    if date_range <= 60:
        trends = df_filtered.groupby("report_date").agg(
            count=("metric", "size"),
            median=("metric", "median"),
        ).reset_index().sort_values("report_date")
        x_label = "Date"
        period_label = "Daily"
        trends["x_axis"] = pd.to_datetime(trends["report_date"])
    else:
        trends = df_filtered.groupby("report_week").agg(
            count=("metric", "size"),
            median=("metric", "median"),
        ).reset_index().sort_values("report_week")
        x_label = "Week"
        period_label = "Weekly"
        trends["x_axis"] = trends["report_week"]

    # Plot with period_label in title
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(trends["x_axis"], trends["median"], color="#667eea", linewidth=3, marker="o", markersize=5)
    ax.set_title(f"Trends ({period_label})", fontsize=12, fontweight="bold")
    # ...
```

### 6. Landing Page Pattern

Keep landing page simple with just date range input (no additional filters):

```python
def create_landing_page(table_name="default.reports", date_range_days=360):
    """Create landing page with Launch Dashboard button for Voila."""
    container = widgets.VBox(
        layout=widgets.Layout(padding="0", margin="0", min_height="100vh")
    )

    # Global styles
    global_styles = widgets.HTML("""
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
            body, .widget-html, .widget-label {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
            }
        </style>
    """)

    # Header - always use #667eea purple
    header = widgets.HTML("""
        <div style='background: #667eea; padding: 40px; text-align: center; color: white;'>
            <h1 style='margin: 0 0 12px 0; font-size: 36px; font-weight: 700;'>
                {TITLE}
            </h1>
            <p style='margin: 0; font-size: 18px;'>
                {SUBTITLE}
            </p>
        </div>
    """)

    # Date range input only (no other filters on landing page)
    date_range_input = widgets.IntText(
        value=date_range_days,
        description="Days to analyze:",
        min=1,
        max=3650,
        step=30,
        layout=widgets.Layout(width="400px"),
        style={"description_width": "140px"},
    )

    date_help = widgets.HTML("""
        <div style='text-align: center; margin-bottom: 20px;'>
            <span style='font-size: 12px; color: #6b7280; line-height: 1;'>
                Number of days of reports to analyze from the most recent data.<br/>
                <span style='font-weight: 500;'>Lower values</span> = faster load time.
            </span>
        </div>
    """)

    # Launch button - always #667eea
    launch_btn = widgets.Button(
        description="🚀 Launch Dashboard",
        button_style="",
        layout=widgets.Layout(width="280px", height="60px", border="none"),
        style={"button_color": "#667eea", "font_weight": "600", "text_color": "white"},
    )

    # Features list - customize per dashboard
    features_container = widgets.VBox(
        layout=widgets.Layout(background="#f8f9fa", border_radius="12px", padding="28px 32px")
    )
    features_container.children = [widgets.HTML("""
        <div style='margin-bottom: 20px;'>
            <h3 style='font-size: 14px; font-weight: 700; color: #667eea; text-transform: uppercase; letter-spacing: 0.5px; margin: 0;'>
                Dashboard Features
            </h3>
        </div>
        <div style='display: grid; gap: 10px;'>
            <div style='display: flex; align-items: flex-start; gap: 12px;'>
                <span style='color: #10b981; font-weight: 700; font-size: 18px; line-height: 1.5;'>✓</span>
                <span style='color: #4b5563; font-size: 15px; line-height: 1.6; flex: 1;'>
                    Feature 1 description
                </span>
            </div>
            <!-- Add more features -->
        </div>
    """)]

    def on_launch(b):
        launch_btn.disabled = True
        launch_btn.description = "⏳ Loading..."
        launch_btn.style.button_color = "#9ca3af"
        date_range_input.disabled = True

        user_date_range = date_range_input.value

        # Create hidden output, build dashboard, then swap
        dashboard_output = widgets.Output(layout=widgets.Layout(visibility="hidden"))
        container.children = [page_wrapper, dashboard_output]

        with dashboard_output:
            create_dashboard(table_name, user_date_range)

        dashboard_output.layout.visibility = "visible"
        container.children = [dashboard_output]

    launch_btn.on_click(on_launch)

    # Build card structure
    # ... (see full example in git version)
```

## Styling Guidelines

1. **Primary color**: Always use `#667eea` (purple) for headers, borders, buttons, accents
2. **Secondary color**: Use `#764ba2` for gradients with primary
3. **Success color**: Use `#10b981` (green) for success states, checkmarks
4. **Charts**: Use `#667eea` and `#764ba2` for bar/line colors
5. **Tables**: Use pandas `display()` for data tables, not custom HTML
6. **Section headers**: Use `<h2 style='margin-top: 40px;'>` for section titles

## Common Sections

Typical dashboard sections (adapt based on requirements):
1. **Summary** - Key metrics in gradient card
2. **Breakdown by Category** - Bar charts + data table
3. **Trends Over Time** - Line charts with adaptive daily/weekly aggregation
4. **Performance Comparison** - Scatter plots or grouped bars
5. **Quality Indicators** - Green gradient card with completeness metrics
