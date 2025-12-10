"""
Scout RADS Dashboard - Main Dashboard Module

This module provides the main entry point for the RADS dashboard.
All UI logic is contained here - the notebook just calls launch_rads_dashboard().
"""

import time
import traceback
import pandas as pd
import ipywidgets as widgets
from IPython.display import display, HTML
from datetime import datetime, timedelta

from rads_builder import (
    load_rads_data,
    PRIMARY_GRADIENT,
    SUCCESS_GRADIENT,
    PURPLE_PRIMARY,
    GREEN_SUCCESS,
    ORANGE_WARNING,
    RED_ERROR,
)

from rads_ui import (
    create_statistics_panel,
    create_score_distribution_panel,
    create_time_comparison_panel,
    create_demographics_panel,
    create_patient_progression_panel,
    create_report_browser,
    create_export_controls,
)


def launch_rads_dashboard():
    """
    Main entry point for the RADS dashboard.
    Creates and displays the landing page with configuration inputs.
    """
    # Global styles
    display(
        HTML(
            """
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
            body, .widget-html, .widget-label, .widget-text input, .widget-textarea textarea {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
            }
        </style>
    """
        )
    )

    # Header with features - Enhanced design
    header_html = widgets.HTML(
        """
        <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    padding: 40px 24px 32px 24px; text-align: center; color: white; border-radius: 12px 12px 0 0;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);'>
            <div style='max-width: 900px; margin: 0 auto;'>
                <h1 style='margin: 0 0 12px 0; font-size: 36px; font-weight: 700; letter-spacing: -0.5px;
                           text-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);'>
                    Scout RADS Dashboard
                </h1>
                <p style='margin: 0 0 28px 0; font-size: 16px; opacity: 0.95; font-weight: 400; line-height: 1.5;'>
                    Advanced RADS analysis with intelligent score extraction, population analytics,<br>
                    and longitudinal patient tracking
                </p>

                <!-- Feature badges -->
                <div style='display: flex; justify-content: center; gap: 12px; flex-wrap: wrap; margin-bottom: 8px;'>
                    <div style='display: flex; align-items: center; gap: 8px; padding: 10px 18px;
                                background: rgba(255, 255, 255, 0.2); backdrop-filter: blur(10px);
                                border-radius: 24px; border: 1px solid rgba(255, 255, 255, 0.3);
                                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
                                transition: all 0.2s ease;'>
                        <span style='font-size: 20px;'>🔍</span>
                        <span style='font-size: 13px; font-weight: 600; color: white;'>Auto Extraction</span>
                    </div>
                    <div style='display: flex; align-items: center; gap: 8px; padding: 10px 18px;
                                background: rgba(255, 255, 255, 0.2); backdrop-filter: blur(10px);
                                border-radius: 24px; border: 1px solid rgba(255, 255, 255, 0.3);
                                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);'>
                        <span style='font-size: 20px;'>📊</span>
                        <span style='font-size: 13px; font-weight: 600; color: white;'>Demographics</span>
                    </div>
                    <div style='display: flex; align-items: center; gap: 8px; padding: 10px 18px;
                                background: rgba(255, 255, 255, 0.2); backdrop-filter: blur(10px);
                                border-radius: 24px; border: 1px solid rgba(255, 255, 255, 0.3);
                                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);'>
                        <span style='font-size: 20px;'>📈</span>
                        <span style='font-size: 13px; font-weight: 600; color: white;'>Trends</span>
                    </div>
                    <div style='display: flex; align-items: center; gap: 8px; padding: 10px 18px;
                                background: rgba(255, 255, 255, 0.2); backdrop-filter: blur(10px);
                                border-radius: 24px; border: 1px solid rgba(255, 255, 255, 0.3);
                                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);'>
                        <span style='font-size: 20px;'>👤</span>
                        <span style='font-size: 13px; font-weight: 600; color: white;'>Progression</span>
                    </div>
                    <div style='display: flex; align-items: center; gap: 8px; padding: 10px 18px;
                                background: rgba(255, 255, 255, 0.2); backdrop-filter: blur(10px);
                                border-radius: 24px; border: 1px solid rgba(255, 255, 255, 0.3);
                                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);'>
                        <span style='font-size: 20px;'>📄</span>
                        <span style='font-size: 13px; font-weight: 600; color: white;'>Browser</span>
                    </div>
                </div>
            </div>
        </div>
    """
    )

    # Input widgets
    rads_type_select = widgets.Dropdown(
        options=[
            ("BI-RADS (Breast)", "BIRADS"),
            ("LI-RADS (Liver)", "LIRADS"),
            # ('PI-RADS (Prostate)', 'PIRADS')  # Future expansion
        ],
        value="BIRADS",
        description="RADS Type:",
        layout=widgets.Layout(width="98%"),
        style={"description_width": "100px"},
    )

    modality_select = widgets.SelectMultiple(
        value=["MG", "US"],
        options=["MG", "US", "CT", "MR", "MRI", "XR", "NM", "PT", "RF"],
        description="Modality:",
        layout=widgets.Layout(width="98%", height="100px"),
        style={"description_width": "100px"},
    )

    service_name_input = widgets.Text(
        value="",
        placeholder="e.g., breast, mammo (leave empty for all)",
        description="Service Name:",
        layout=widgets.Layout(width="98%"),
        style={"description_width": "100px"},
    )

    facility_select = widgets.SelectMultiple(
        value=(),
        options=[
            "BJH",
            "PWH",
            "BJSPH",
            "MBMC",
            "CH",
            "WUSM",
            "BJWCH",
            "SLCH",
            "PHCF",
            "MBSH",
            "BJCMG",
            "AMH",
            "MHB",
            "MHE",
            "WUCA",
            "CC",
            "HOME CARE SERVICES",
        ],
        description="Facilities:",
        layout=widgets.Layout(width="98%", height="120px"),
        style={"description_width": "100px"},
    )

    # Date range inputs
    today = datetime.now()
    start_2015 = datetime(2015, 1, 1)

    start_date_input = widgets.DatePicker(
        description="Start Date:",
        value=start_2015.date(),
        layout=widgets.Layout(width="48%"),
        style={"description_width": "80px"},
    )

    end_date_input = widgets.DatePicker(
        description="End Date:",
        value=today.date(),
        layout=widgets.Layout(width="48%"),
        style={"description_width": "80px"},
    )

    min_age_input = widgets.IntText(
        value=18,
        description="Min Age:",
        layout=widgets.Layout(width="48%"),
        style={"description_width": "80px"},
    )

    max_age_input = widgets.IntText(
        value=89,
        description="Max Age:",
        layout=widgets.Layout(width="48%"),
        style={"description_width": "80px"},
    )

    sample_limit_input = widgets.IntText(
        value=1000,
        placeholder="Leave empty for no limit",
        description="Sample Limit:",
        layout=widgets.Layout(width="98%"),
        style={"description_width": "100px"},
    )

    # Form container with enhanced visual hierarchy
    left_column = widgets.VBox(
        [
            widgets.HTML(
                """
            <div style='font-weight: 600; font-size: 16px; margin-bottom: 12px; color: #1f2937;
                        padding-bottom: 8px; border-bottom: 3px solid #667eea;
                        display: flex; align-items: center; gap: 8px;'>
                <span>🔬</span> RADS Configuration
            </div>
        """
            ),
            rads_type_select,
            widgets.HTML("<div style='height: 16px;'></div>"),
            widgets.HTML(
                """
            <div style='font-weight: 600; font-size: 16px; margin-bottom: 12px; color: #1f2937;
                        padding-bottom: 8px; border-bottom: 3px solid #667eea;
                        display: flex; align-items: center; gap: 8px;'>
                <span>🏥</span> Exam Filters
            </div>
        """
            ),
            modality_select,
            widgets.HTML(
                """
            <div style='font-size: 12px; color: #6b7280; margin-top: 4px; font-style: italic;'>
                💡 Tip: Hold Ctrl/Cmd to select multiple modalities
            </div>
        """
            ),
            service_name_input,
            facility_select,
            widgets.HTML(
                """
            <div style='font-size: 12px; color: #6b7280; margin-top: 4px; font-style: italic;'>
                💡 No selection = all facilities. Hold Ctrl/Cmd to select multiple.
            </div>
        """
            ),
        ],
        layout=widgets.Layout(width="48%"),
    )

    right_column = widgets.VBox(
        [
            widgets.HTML(
                """
            <div style='font-weight: 600; font-size: 16px; margin-bottom: 12px; color: #1f2937;
                        padding-bottom: 8px; border-bottom: 3px solid #667eea;
                        display: flex; align-items: center; gap: 8px;'>
                <span>📅</span> Date Range
            </div>
        """
            ),
            widgets.HBox(
                [start_date_input, end_date_input],
                layout=widgets.Layout(width="100%", justify_content="space-between"),
            ),
            widgets.HTML("<div style='height: 24px;'></div>"),
            widgets.HTML(
                """
            <div style='font-weight: 600; font-size: 16px; margin-bottom: 12px; color: #1f2937;
                        padding-bottom: 8px; border-bottom: 3px solid #667eea;
                        display: flex; align-items: center; gap: 8px;'>
                <span>👥</span> Patient Filters
            </div>
        """
            ),
            widgets.HBox(
                [min_age_input, max_age_input],
                layout=widgets.Layout(width="100%", justify_content="space-between"),
            ),
            widgets.HTML("<div style='height: 24px;'></div>"),
            widgets.HTML(
                """
            <div style='font-weight: 600; font-size: 16px; margin-bottom: 12px; color: #1f2937;
                        padding-bottom: 8px; border-bottom: 3px solid #667eea;
                        display: flex; align-items: center; gap: 8px;'>
                <span>⚙️</span> Performance Options
            </div>
        """
            ),
            sample_limit_input,
            widgets.HTML(
                """
            <div style='background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
                        padding: 14px; border-radius: 6px; margin-top: 16px;
                        border-left: 4px solid #f59e0b; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);'>
                <div style='font-size: 13px; color: #78350f; line-height: 1.6;'>
                    <strong>💡 Pro Tip:</strong> Start with a sample limit (e.g., 1000) for rapid exploration.
                    Once satisfied with filters, set to 0 or clear for full dataset analysis.
                </div>
            </div>
        """
            ),
        ],
        layout=widgets.Layout(width="48%"),
    )

    form_container = widgets.HBox(
        [left_column, right_column],
        layout=widgets.Layout(padding="20px 24px", justify_content="space-between"),
    )

    # Search button with enhanced styling
    search_button = widgets.Button(
        description="🔍 Search RADS Reports",
        button_style="success",
        layout=widgets.Layout(width="98%", height="56px"),
        style={"button_color": "#10b981", "font_weight": "bold"},
    )

    # Add instructional text above button
    search_instruction = widgets.HTML(
        """
        <div style='text-align: center; margin-bottom: 12px; color: #6b7280; font-size: 14px;'>
            Configure your search criteria above, then click the button below to begin analysis
        </div>
    """
    )

    # Container that will hold either landing page or dashboard
    main_container = widgets.VBox(
        [
            widgets.VBox(
                [
                    header_html,
                    widgets.VBox(
                        [form_container, search_instruction, search_button],
                        layout=widgets.Layout(padding="20px"),
                    ),
                ],
                layout=widgets.Layout(
                    width="85%",
                    max_width="1800px",
                    margin="100px auto",
                    background="white",
                    border_radius="12px",
                    border="2px solid #667eea",
                    box_shadow="0 10px 30px rgba(0, 0, 0, 0.1)",
                ),
            )
        ]
    )

    # Button click handler
    def on_search_click(b):
        # Gather configuration
        config = {
            "rads_type": rads_type_select.value,
            "modalities": list(modality_select.value),
            "service_name_pattern": service_name_input.value,
            "facilities": list(facility_select.value),
            "start_date": start_date_input.value,
            "end_date": end_date_input.value,
            "min_age": min_age_input.value if min_age_input.value else None,
            "max_age": max_age_input.value if max_age_input.value else None,
            "sample_limit": (
                sample_limit_input.value if sample_limit_input.value else None
            ),
        }

        # Clear and show dashboard
        main_container.children = []
        _create_rads_dashboard(config, main_container)

    search_button.on_click(on_search_click)

    # Display the landing page
    display(main_container)


def _create_rads_dashboard(config, container):
    """
    Create the RADS dashboard with analytics interface.

    Args:
        config: Configuration dictionary from landing page
        container: Widget container to render into
    """
    # Show enhanced loading status with progress indicator
    status_output = widgets.Output()

    # Display improved loading UI
    with status_output:
        display(
            HTML(
                f"""
            <div style='max-width: 600px; margin: 100px auto; text-align: center;'>
                <div style='background: {PRIMARY_GRADIENT}; padding: 40px; border-radius: 12px;
                            color: white; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15);'>
                    <div style='font-size: 48px; margin-bottom: 16px; animation: pulse 2s ease-in-out infinite;'>
                        🔍
                    </div>
                    <h2 style='margin: 0 0 12px 0; font-size: 24px; font-weight: 700;'>
                        Searching for RADS Reports
                    </h2>
                    <p style='margin: 0; font-size: 15px; opacity: 0.95; line-height: 1.6;'>
                        Querying the database and extracting RADS scores...<br>
                        This may take a few moments depending on your filters.
                    </p>
                    <div style='margin-top: 24px; background: rgba(255, 255, 255, 0.2);
                                height: 6px; border-radius: 3px; overflow: hidden;'>
                        <div style='height: 100%; background: white; width: 100%;
                                    animation: loading 1.5s ease-in-out infinite;'></div>
                    </div>
                </div>
            </div>
            <style>
                @keyframes pulse {{
                    0%, 100% {{ transform: scale(1); opacity: 1; }}
                    50% {{ transform: scale(1.1); opacity: 0.8; }}
                }}
                @keyframes loading {{
                    0% {{ transform: translateX(-100%); }}
                    100% {{ transform: translateX(100%); }}
                }}
            </style>
        """
            )
        )

    container.children = [status_output]

    try:
        # Build SQL query first for debugging
        from rads_builder import build_rads_query

        sql, criteria_summary = build_rads_query(config)

        # Load data
        df, criteria_summary = load_rads_data(config, status_output)

        # Check if query returned results
        if df is None or len(df) == 0:
            # Display SQL (truncated if too long)
            sql_display = sql.strip()
            if len(sql_display) > 2000:
                sql_display = sql_display[:2000] + "\n...(truncated)"

            with status_output:
                status_output.clear_output(wait=True)
                display(
                    HTML(
                        f"""
                    <div style='background: {ORANGE_WARNING}; padding: 24px; border-radius: 12px;
                                color: white; margin: 20px auto; text-align: center; max-width: 1000px;'>
                        <div style='font-size: 28px; margin-bottom: 8px;'>⚠️</div>
                        <div style='font-size: 18px; font-weight: 600;'>No RADS Reports Found</div>
                        <div style='font-size: 14px; margin-top: 8px; opacity: 0.9;'>
                            Try adjusting your search criteria or date range
                        </div>
                    </div>
                    <div style='max-width: 1000px; margin: 20px auto; background: white; padding: 24px;
                                border-radius: 12px; border: 2px solid {PURPLE_PRIMARY};'>
                        <h5 style='margin: 0 0 12px 0; color: {PURPLE_PRIMARY};'>Generated SQL Query</h5>
                        <div style='background: #1e1e1e; color: #d4d4d4; padding: 16px; border-radius: 6px;
                                    overflow-x: auto; font-family: monospace; font-size: 13px; line-height: 1.5;'>
                            <pre style='margin: 2px; white-space: pre-wrap;'>{sql_display}</pre>
                        </div>
                    </div>
                """
                    )
                )
            return

        # Continue with dashboard creation
        _build_dashboard_ui(df, criteria_summary, config, container, status_output)

    except Exception as e:
        # Show error message
        with status_output:
            status_output.clear_output(wait=True)
            display(
                HTML(
                    f"""
                <div style='background: {RED_ERROR}; padding: 24px; border-radius: 12px;
                            color: white; margin: 20px auto; text-align: center; max-width: 1000px;'>
                    <div style='font-size: 28px; margin-bottom: 8px;'>❌</div>
                    <div style='font-size: 18px; font-weight: 600;'>Query Failed</div>
                    <div style='font-size: 14px; margin-top: 8px; opacity: 0.9;'>{str(e)}</div>
                </div>
                <div style='max-width: 1000px; margin: 20px auto; background: white; padding: 24px;
                            border-radius: 12px; border: 2px solid {RED_ERROR};'>
                    <h3 style='margin: 0 0 12px 0; color: {RED_ERROR};'>Error Details</h3>
                    <pre style='margin: 0; white-space: pre-wrap; word-wrap: break-word; font-size: 12px;'>
                        {traceback.format_exc()}
                    </pre>
                </div>
            """
                )
            )


def _build_dashboard_ui(df, criteria_summary, config, container, status_output):
    """
    Build the dashboard UI after data is loaded.

    Args:
        df: Loaded DataFrame
        criteria_summary: List of applied criteria
        config: Configuration dictionary
        container: Widget container
        status_output: Output widget for status messages
    """
    # Clear status after brief delay
    time.sleep(1.5)

    try:
        # State management
        state = {
            "df": df,  # Current view (can be filtered)
            "full_df": df,  # Full dataset for time comparisons
            "criteria_summary": criteria_summary,
            "config": config,
            "current_pos": 0,
            "rads_type": config.get("rads_type", "LIRADS"),
        }

        # Calculate summary stats
        unique_patients = df.apply(
            lambda row: (
                row["epic_mrn"] if pd.notna(row["epic_mrn"]) else row["empi_mr"]
            ),
            axis=1,
        ).nunique()

        # Header
        header_widget = widgets.HTML(
            f"""
            <div style='background: {PRIMARY_GRADIENT}; padding: 24px 20px; border-radius: 8px;
                        color: white; margin-bottom: 0; text-align: center;'>
                <div style='font-size: 28px; font-weight: 700; margin-bottom: 6px; letter-spacing: -0.5px;'>
                    Scout RADS Dashboard
                </div>
                <div style='font-size: 14px; opacity: 0.95; margin-bottom: 4px;'>
                    Analyzing {len(df):,} reports from {unique_patients:,} patients with ML predictions and advanced visualizations
                </div>
            </div>
        """
        )

        # Create UI components
        statistics_panel = create_statistics_panel(state)
        score_distribution_panel = create_score_distribution_panel(state)
        time_comparison_panel = create_time_comparison_panel(state)
        demographics_panel = create_demographics_panel(state)
        progression_panel = create_patient_progression_panel(state)
        nav_bar, report_output = create_report_browser(state)

        # Create tabs for different views
        tab_contents = [
            widgets.VBox([statistics_panel, score_distribution_panel]),
            time_comparison_panel,
            demographics_panel,
            progression_panel,
            widgets.VBox([nav_bar, report_output]),
        ]

        tabs = widgets.Tab(children=tab_contents)
        tabs.set_title(0, "📊 Overview")
        tabs.set_title(1, "📈 Time Comparison")
        tabs.set_title(2, "👥 Demographics")
        tabs.set_title(3, "👤 Patient Progression")
        tabs.set_title(4, "📄 Browse Reports")

        # Build dashboard layout
        dashboard = widgets.VBox(
            [header_widget, tabs],
            layout=widgets.Layout(
                width="98%", max_width="2000px", margin="0 auto", padding="15px"
            ),
        )

        status_output.clear_output()
        container.children = [dashboard]

    except Exception as e:
        # Show error message
        with status_output:
            status_output.clear_output(wait=True)
            display(
                HTML(
                    f"""
                <div style='background: {RED_ERROR}; padding: 24px; border-radius: 12px;
                            color: white; margin: 20px; text-align: center;'>
                    <div style='font-size: 28px; margin-bottom: 8px;'>⚠️</div>
                    <div style='font-size: 18px; font-weight: 600;'>Dashboard Error</div>
                    <div style='font-size: 14px; margin-top: 8px; opacity: 0.9;'>
                        {str(e)}
                    </div>
                    <pre style='text-align: left; margin-top: 12px; background: rgba(0,0,0,0.2); padding: 12px; border-radius: 4px; font-size: 12px; overflow-x: auto;'>
                        {traceback.format_exc()}
                    </pre>
                </div>
            """
                )
            )
