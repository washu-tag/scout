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

import warnings
warnings.filterwarnings("ignore")

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


def _load_data(table_name="default.reports", date_range_days=None, limit=None):
    """
    Load data from Trino.

    Parameters
    ----------
    table_name : str
        Table name (e.g., "default.reports")
    date_range_days : int, optional
        Only load reports from last N days
    limit : int, optional
        Limit number of rows for performance

    Returns
    -------
    pd.DataFrame
        Report data
    """
    conn = _connect_trino()

    # Parse table name
    table_parts = table_name.split(".")
    if len(table_parts) == 2:
        schema, table = table_parts
    else:
        schema = "default"
        table = table_parts[0]

    # Build WHERE clause
    where_clauses = []
    if date_range_days:
        cutoff = (datetime.now() - timedelta(days=date_range_days)).strftime("%Y-%m-%d")
        where_clauses.append(f"message_dt >= TIMESTAMP '{cutoff}'")

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    limit_sql = f"LIMIT {limit}" if limit else ""

    # Build your query here
    query = f"""
    SELECT
        -- Add your columns here
        message_dt,
        modality,
        sending_facility
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

    # Store the date range used for filtering
    df.attrs["date_range_days"] = date_range_days

    return df


def create_dashboard(table_name="default.reports", date_range_days=360):
    """
    Create and display dashboard with progressive rendering.

    Parameters
    ----------
    table_name : str, default="default.reports"
        Delta table to analyze
    date_range_days : int, default=360
        Only analyze reports from last N days
    """

    # Display header
    display(
        HTML(
            """
        <div style='background: white; padding: 20px 28px; border-bottom: 3px solid #667eea; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);'>
            <h1 style='margin: 0; color: #667eea; font-size: 28px; font-weight: 700;'>{TITLE}</h1>
            <p style='margin: 8px 0 0 0; color: #666;'>{SUBTITLE}</p>
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
                f"""
            <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px 24px; border-radius: 8px; color: white; margin-bottom: 16px;'>
                <div style='display: flex; align-items: center; gap: 12px;'>
                    <div style='font-size: 24px;'>⏳</div>
                    <div>
                        <div style='font-size: 16px; font-weight: 600;'>Loading Data</div>
                        <div style='font-size: 13px; opacity: 0.9; margin-top: 4px;'>
                            Connecting to {table_name}...
                        </div>
                    </div>
                </div>
            </div>
        """
            )
        )

    # Load data
    df = _load_data(table_name, date_range_days)

    with status_output:
        status_output.clear_output(wait=True)
        display(
            HTML(
                f"""
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
        """
            )
        )

    import time
    time.sleep(1.5)
    status_output.clear_output()

    # Create output widgets for each section
    summary_output = widgets.Output()
    # Add more section outputs as needed

    display(summary_output)

    # Render sections progressively
    with summary_output:
        _render_summary(df)


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
    # Add your summary calculations here

    display(
        HTML(
            f"""
    <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 24px; border-radius: 12px; color: white; margin-bottom: 20px;'>
        <h2 style='margin: 0 0 20px 0; font-size: 24px;'>Dashboard Summary</h2>
        <div style='display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;'>
            <div style='background: rgba(255,255,255,0.15); padding: 16px; border-radius: 8px;'>
                <div style='font-size: 28px; font-weight: 700;'>{total:,}</div>
                <div style='font-size: 13px; opacity: 0.9;'>Total Reports</div>
            </div>
            <!-- Add more summary cards -->
        </div>
    </div>
    """
        )
    )


def create_landing_page(table_name="default.reports", date_range_days=360):
    """
    Create landing page with "Launch Dashboard" button for Voila.

    This provides instant page load - data is only loaded when user clicks the button.
    """
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

    # Header section - customize the color and title
    header = widgets.HTML(
        """
        <div style='background: #667eea; padding: 40px; text-align: center; color: white;'>
            <h1 style='margin: 0 0 12px 0; font-size: 36px; font-weight: 700;'>
                {TITLE}
            </h1>
            <p style='margin: 0; font-size: 18px;'>
                {SUBTITLE}
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

    # Launch button - customize color
    launch_btn = widgets.Button(
        description="🚀 Launch Dashboard",
        button_style="",
        layout=widgets.Layout(width="280px", height="60px", border="none"),
        style={"button_color": "#667eea", "font_weight": "600", "text_color": "white"},
    )

    # Features section - customize features list
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
                        Feature 1 description
                    </span>
                </div>
                <div style='display: flex; align-items: flex-start; gap: 12px;'>
                    <span style='color: #10b981; font-weight: 700; font-size: 18px; line-height: 1.5;'>✓</span>
                    <span style='color: #4b5563; font-size: 15px; line-height: 1.6; flex: 1;'>
                        Feature 2 description
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

        user_date_range = date_range_input.value

        dashboard_output = widgets.Output(layout=widgets.Layout(visibility="hidden"))
        container.children = [page_wrapper, dashboard_output]

        with dashboard_output:
            create_dashboard(table_name, user_date_range)

        dashboard_output.layout.visibility = "visible"
        container.children = [dashboard_output]

    launch_btn.on_click(on_launch)

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

    page_wrapper = widgets.VBox(
        [global_styles, card], layout=widgets.Layout(padding="20px", margin="0")
    )

    container.children = [page_wrapper]

    display(container)
```
