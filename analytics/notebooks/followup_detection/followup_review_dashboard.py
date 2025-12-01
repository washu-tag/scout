"""
Interactive review dashboard for follow-up detection results.

Usage in Voila (recommended - instant page load):
    from followup_review_dashboard import create_landing_page

    # Shows landing page immediately with "Launch Dashboard" button
    # User clicks button to load data (fast with Trino) and show dashboard
    create_landing_page(
        table_name="default.latest_reports",
        samples_per_category=50
    )

Usage in Jupyter Notebook:
    from followup_review_dashboard import create_review_dashboard

    # Option 1: Load from Trino automatically with stratified sampling
    create_review_dashboard(
        table_name="default.latest_reports",
        samples_per_category=50,  # Samples per modality/detection/confidence combo
        report_col='report_text'
    )

    # Option 2: Pass pre-loaded Pandas DataFrame
    import pandas as pd
    df = pd.read_csv("reports.csv")  # or load from any source
    create_review_dashboard(df, report_col='report_text')

Note: Trino connection details are read from environment variables:
    TRINO_HOST, TRINO_PORT, TRINO_SCHEME, TRINO_USER, TRINO_CATALOG, TRINO_SCHEMA
"""

import re
import pandas as pd
import ipywidgets as widgets
from IPython.display import display, HTML
import html as html_lib
import os


# Global state
annotations = {}
_normalized_cache = {}


def normalize_text(text):
    """Cached text normalization for matching."""
    if text not in _normalized_cache:
        _normalized_cache[text] = re.sub(r"\s+", " ", text.strip()).lower()
    return _normalized_cache[text]


def get_darker_shade(hex_color):
    """Get darker shade of a color."""
    hex_color = hex_color.lstrip("#")
    r, g, b = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    r, g, b = int(r * 0.6), int(g * 0.6), int(b * 0.6)
    return f"#{r:02x}{g:02x}{b:02x}"


def get_lighter_shade(hex_color):
    """Get lighter shade of a color."""
    hex_color = hex_color.lstrip("#")
    r, g, b = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    r, g, b = (
        min(255, int(r + (255 - r) * 0.3)),
        min(255, int(g + (255 - g) * 0.3)),
        min(255, int(b + (255 - b) * 0.3)),
    )
    return f"#{r:02x}{g:02x}{b:02x}"


def _load_from_trino(table_name, samples_per_category, report_col, status_output=None):
    """
    Load data from Trino Delta table with stratified sampling.

    Parameters
    ----------
    table_name : str
        Delta table name (e.g., "default.latest_reports")
    samples_per_category : int
        Number of samples per modality/detection/confidence category
    report_col : str
        Column name for report text
    status_output : widgets.Output, optional
        Output widget for status messages

    Returns
    -------
    pd.DataFrame
        Stratified sample as Pandas DataFrame
    """
    import trino
    import pandas as pd

    def print_status(msg):
        if status_output:
            with status_output:
                print(msg, flush=True)
        else:
            print(msg, flush=True)

    # Get Trino connection details from environment
    TRINO_HOST = os.environ.get("TRINO_HOST", "trino.trino")
    TRINO_PORT = int(os.environ.get("TRINO_PORT", "8080"))
    TRINO_SCHEME = os.environ.get("TRINO_SCHEME", "http")
    TRINO_USER = os.environ.get("TRINO_USER", "trino")
    TRINO_CATALOG = os.environ.get("TRINO_CATALOG", "delta")
    TRINO_SCHEMA = os.environ.get("TRINO_SCHEMA", "default")

    # Connect to Trino
    conn = trino.dbapi.connect(
        host=TRINO_HOST,
        port=TRINO_PORT,
        http_scheme=TRINO_SCHEME,
        user=TRINO_USER,
        catalog=TRINO_CATALOG,
        schema=TRINO_SCHEMA,
    )

    # Parse table name (handle "catalog.schema.table" or "schema.table" or "table")
    table_parts = table_name.split(".")
    if len(table_parts) == 3:
        catalog, schema, table = table_parts
    elif len(table_parts) == 2:
        catalog = TRINO_CATALOG
        schema, table = table_parts
    else:
        catalog = TRINO_CATALOG
        schema = TRINO_SCHEMA
        table = table_parts[0]

    # Stratified sampling query using Trino SQL
    # Note: Using CTE with ROW_NUMBER for stratified sampling
    query = f"""
    WITH categorized AS (
        SELECT
            obr_3_filler_order_number,
            message_dt,
            modality,
            followup_detected,
            followup_confidence,
            followup_snippet,
            followup_finding,
            {report_col},
            patient_age,
            sex,
            race,
            sending_facility,
            service_name,
            service_identifier,
            diagnoses,
            principal_result_interpreter,
            CONCAT(
                modality, '_',
                CAST(followup_detected AS VARCHAR), '_',
                COALESCE(followup_confidence, 'null')
            ) AS category,
            ROW_NUMBER() OVER (
                PARTITION BY CONCAT(
                    modality, '_',
                    CAST(followup_detected AS VARCHAR), '_',
                    COALESCE(followup_confidence, 'null')
                )
                ORDER BY message_dt DESC, obr_3_filler_order_number
            ) AS row_num
        FROM {catalog}.{schema}.{table}
        WHERE followup_processed_at IS NOT NULL
          AND modality IS NOT NULL
    )
    SELECT
        obr_3_filler_order_number,
        message_dt,
        modality,
        followup_detected,
        followup_confidence,
        followup_snippet,
        followup_finding,
        {report_col},
        patient_age,
        sex,
        race,
        sending_facility,
        service_name,
        service_identifier,
        diagnoses,
        principal_result_interpreter
    FROM categorized
    WHERE row_num <= {samples_per_category}
    """

    # Execute query and load into Pandas
    cursor = conn.cursor()
    cursor.execute(query)

    # Fetch column names and data
    columns = [desc[0] for desc in cursor.description]
    data = cursor.fetchall()

    # Create DataFrame
    df_sample = pd.DataFrame(data, columns=columns)

    cursor.close()
    conn.close()

    return df_sample


def highlight_snippet_in_text(report_text, snippet, color="#FFC107"):
    """
    Highlight snippet in report text, preserving original formatting including newlines.
    """
    if not snippet or len(snippet.strip()) < 5:
        return html_lib.escape(report_text)

    snippet_norm = normalize_text(snippet)
    report_norm = normalize_text(report_text)

    match_pos = report_norm.find(snippet_norm)
    if match_pos == -1:
        return html_lib.escape(report_text)

    # Calculate non-whitespace character positions in normalized text
    chars_before = len(report_norm[:match_pos].replace(" ", ""))
    snippet_char_count = len(snippet_norm.replace(" ", ""))

    # Find corresponding positions in original text
    char_count = 0
    original_start = 0

    for i, c in enumerate(report_text):
        if not c.isspace():
            if char_count == chars_before:
                original_start = i
                break
            char_count += 1

    char_count = 0
    original_end = original_start
    for i in range(original_start, len(report_text)):
        if not report_text[i].isspace():
            char_count += 1
        if char_count >= snippet_char_count:
            original_end = i + 1
            break

    darker = get_darker_shade(color)

    # Build result with escaped HTML and preserved formatting
    before = html_lib.escape(report_text[:original_start])
    matched = html_lib.escape(report_text[original_start:original_end])
    after = html_lib.escape(report_text[original_end:])

    return (
        before
        + f'<mark id="highlighted-snippet" style="background: linear-gradient(135deg, {color} 0%, {get_lighter_shade(color)} 100%); padding: 4px 8px; font-weight: 600; border-left: 4px solid {darker}; margin: 0 2px; border-radius: 3px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">'
        + matched
        + "</mark>"
        + after
    )


def create_landing_page(
    table_name="default.latest_reports",
    samples_per_category=50,
    report_col="report_text",
):
    """
    Create a landing page with a button to launch the dashboard.
    Use this in Voila for better UX - shows immediately, user clicks to load data.

    Parameters
    ----------
    table_name : str, default="default.latest_reports"
        Delta table to load from
    samples_per_category : int, default=50
        Number of samples per category
    report_col : str, default='report_text'
        Column with report text
    """
    # Container that will hold either the landing page or the dashboard
    container = widgets.VBox(
        layout=widgets.Layout(padding="0", margin="0", min_height="100vh")
    )

    # Sample size input
    sample_size_input = widgets.IntText(
        value=samples_per_category,
        description="Sample size per category:",
        min=1,
        max=500,
        step=10,
        layout=widgets.Layout(width="400px"),
        style={"description_width": "180px"},
    )

    launch_btn = widgets.Button(
        description="🚀 Launch Dashboard",
        button_style="",
        layout=widgets.Layout(width="280px", height="60px", border="none"),
        style={"button_color": "#667eea", "font_weight": "600", "text_color": "white"},
    )

    def on_launch(b):
        launch_btn.disabled = True
        launch_btn.description = "⏳ Loading..."
        launch_btn.style.button_color = "#9ca3af"
        sample_size_input.disabled = True

        # Get the user-specified sample size
        user_sample_size = sample_size_input.value

        # Create hidden output widget for the new dashboard
        dashboard_output = widgets.Output(layout=widgets.Layout(visibility="hidden"))

        # Add dashboard to container (but hidden)
        container.children = [page_wrapper, dashboard_output]

        # Build dashboard in the hidden output widget
        with dashboard_output:
            create_review_dashboard(
                table_name=table_name,
                samples_per_category=user_sample_size,
                report_col=report_col,
            )

        # Two-step replacement: now that dashboard is ready, hide landing page and show dashboard
        # This ensures smooth transition without flickering
        dashboard_output.layout.visibility = "visible"
        container.children = [dashboard_output]

    launch_btn.on_click(on_launch)

    # Create a properly styled card using only widgets (no mixing HTML and widgets)
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

    # Header section - simple test
    header = widgets.HTML(
        """
        <div style='background: #667eea; padding: 40px; text-align: center; color: white;'>
            <h1 style='margin: 0 0 12px 0; font-size: 36px; font-weight: 700;'>
                Follow-up Detection Review
            </h1>
            <p style='margin: 0; font-size: 18px;'>
                Validate AI predictions and annotate radiology reports
            </p>
        </div>
    """
    )

    # Title section
    title = widgets.HTML(
        """
        <div style='text-align: center;'>
            <h2 style='color: #1f2937; font-size: 24px; font-weight: 700; margin-bottom: 20px;'>
                Ready to Start Reviewing?
            </h2>
        </div>
    """
    )

    sample_help = widgets.HTML(
        """
        <div style='text-align: center; margin-bottom: 20px;'>
            <span style='font-size: 12px; color: #6b7280; line-height: 1;'>
                Number of reports to sample from each modality/detection/confidence combination.<br/>
                <span style='font-weight: 500;'>Higher values</span> = more comprehensive, longer load time.
            </span>
        </div>
    """
    )

    sample_container = widgets.VBox(
        [
            widgets.HBox(
                [sample_size_input], layout=widgets.Layout(justify_content="center")
            ),
            sample_help,
        ],
        layout=widgets.Layout(padding="0", gap="0"),
    )

    # Features section - styled container
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
                        Stratified sampling across modalities and detection results
                    </span>
                </div>
                <div style='display: flex; align-items: flex-start; gap: 12px;'>
                    <span style='color: #10b981; font-weight: 700; font-size: 18px; line-height: 1.5;'>✓</span>
                    <span style='color: #4b5563; font-size: 15px; line-height: 1.6; flex: 1;'>
                        Advanced filtering by modality, facility, radiologist, and findings
                    </span>
                </div>
                <div style='display: flex; align-items: flex-start; gap: 12px;'>
                    <span style='color: #10b981; font-weight: 700; font-size: 18px; line-height: 1.5;'>✓</span>
                    <span style='color: #4b5563; font-size: 15px; line-height: 1.6; flex: 1;'>
                        Interactive annotation with ground truth labeling
                    </span>
                </div>
                <div style='display: flex; align-items: flex-start; gap: 12px;'>
                    <span style='color: #10b981; font-weight: 700; font-size: 18px; line-height: 1.5;'>✓</span>
                    <span style='color: #4b5563; font-size: 15px; line-height: 1.6; flex: 1;'>
                        Model validation metrics and agreement tracking
                    </span>
                </div>
                <div style='display: flex; align-items: flex-start; gap: 12px;'>
                    <span style='color: #10b981; font-weight: 700; font-size: 18px; line-height: 1.5;'>✓</span>
                    <span style='color: #4b5563; font-size: 15px; line-height: 1.6; flex: 1;'>
                        Export annotations to CSV and Delta Lake
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
                <strong style='color: #4b5563;'>Note:</strong> Initial load may take 10-30 seconds, depending on sample size.
            </span>
        </div>
    """
    )

    # Build the card body with proper spacing
    card_body = widgets.VBox(
        [
            title,
            sample_container,
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

    # Wrap everything - use solid color since gradients don't work in widget layouts
    page_wrapper = widgets.VBox(
        [global_styles, card], layout=widgets.Layout(padding="20px", margin="0")
    )

    container.children = [page_wrapper]

    display(container)


def create_review_dashboard(
    df=None, table_name=None, samples_per_category=50, report_col="report_text"
):
    """
    Create interactive review dashboard for follow-up detection results.

    Parameters
    ----------
    df : pd.DataFrame, optional
        Pre-loaded Pandas DataFrame with required columns. If provided, table_name is ignored.
    table_name : str, optional
        Delta table name to load from via Trino (e.g., "default.latest_reports").
        If provided and df is None, loads stratified sample using Trino query.
    samples_per_category : int, default=50
        Number of samples per modality/detection/confidence category.
    report_col : str, default='report_text'
        Column name containing the report text.

    Required columns in df:
        obr_3_filler_order_number (accession), message_dt, modality,
        followup_detected, followup_confidence, followup_snippet, followup_finding,
        report_text (or specified report_col), patient_age, sex, race,
        sending_facility, diagnoses (optional), service_name, service_identifier,
        principal_result_interpreter (optional)

    Features
    --------
    - Stratified sampling across modality/detection/confidence categories (via Trino)
    - Filter by modality, facility, radiologist, finding type, and date range
    - Jump to specific report by accession number
    - Filter by detection status, confidence, and annotation status
    - Display patient demographics and diagnoses
    - Display exam information (modality, service, facility)
    - Show finding type in snippet section
    - Annotate reports with ground truth labels
    - Model validation metrics (agreement between human and model)
    - Export annotations to CSV and Delta Lake
    """
    # Load from Trino if table_name provided
    if df is None and table_name is not None:
        # Load data (when called from landing page, this happens on button click)
        df = _load_from_trino(
            table_name, samples_per_category, report_col, status_output=None
        )

    elif df is None:
        raise ValueError("Either 'df' or 'table_name' must be provided")

    # Validate required columns
    required_cols = [
        "obr_3_filler_order_number",
        "followup_detected",
        "followup_confidence",
        "followup_snippet",
        report_col,
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"DataFrame missing required columns: {missing}")

    # State
    state = {
        "current_pos": 0,
        "filtered_indices": [],
        "df": df.reset_index(drop=True),
        "ground_truth": annotations,  # Reference to global annotations dict
    }

    # Get unique modalities, facilities, and radiologists for filters
    modalities = sorted([m for m in df["modality"].dropna().unique() if m])
    modality_options = [("All", "all")] + [(m, m) for m in modalities]

    facilities = sorted([f for f in df["sending_facility"].dropna().unique() if f])
    facility_options = [("All", "all")] + [(f, f) for f in facilities]

    # Extract radiologist names from principal_result_interpreter (may be array or string)
    radiologists = set()
    if "principal_result_interpreter" in df.columns:
        for val in df["principal_result_interpreter"].dropna():
            if isinstance(val, (list, tuple)) and len(val) > 0:
                # Extract name from first interpreter if array
                radiologists.add(str(val[0]) if val[0] else "")
            elif isinstance(val, str) and val:
                radiologists.add(val)
    radiologists = sorted([r for r in radiologists if r])
    radiologist_options = [("All", "all")] + [(r, r) for r in radiologists]

    # Create filter options
    filter_options = [
        ("All reports", "all"),
        ("━━━━━ BY DETECTION ━━━━━", "sep1"),
        ("Follow-up detected", "followup_yes"),
        ("No follow-up detected", "followup_no"),
        ("━━━━━ BY CONFIDENCE ━━━━━", "sep2"),
        ("High confidence", "high_conf"),
        ("Low confidence", "low_conf"),
        ("High confidence follow-ups", "high_conf_yes"),
        ("Low confidence follow-ups", "low_conf_yes"),
        ("━━━━━ ANNOTATION STATUS ━━━━━", "sep3"),
        ("Reviewed only", "annotated_only"),
        ("Not yet reviewed", "unannotated_only"),
    ]

    # Create widgets
    # Top-level data filters (modality, date, facility, finding)
    modality_filter = widgets.Dropdown(
        options=modality_options,
        value="all",
        description="Modality:",
        layout=widgets.Layout(width="98%"),
    )

    facility_filter = widgets.Dropdown(
        options=facility_options,
        value="all",
        description="Facility:",
        layout=widgets.Layout(width="98%"),
    )

    radiologist_filter = widgets.Dropdown(
        options=radiologist_options,
        value="all",
        description="Radiologist:",
        layout=widgets.Layout(width="98%"),
    )

    finding_search = widgets.Text(
        placeholder="Search findings...",
        description="Finding:",
        layout=widgets.Layout(width="98%"),
    )

    date_filter = widgets.Dropdown(
        options=[
            ("All dates", "all"),
            ("Last 30 days", "30"),
            ("Last 90 days", "90"),
            ("Last 180 days", "180"),
            ("Last year", "365"),
        ],
        value="all",
        description="Date:",
        layout=widgets.Layout(width="98%"),
    )

    accession_jump = widgets.Text(
        placeholder="Accession #", description="", layout=widgets.Layout(width="98%")
    )

    accession_jump_btn = widgets.Button(
        description="Go", button_style="info", layout=widgets.Layout(width="98%")
    )

    # Detection attribute filter
    filter_dropdown = widgets.Dropdown(
        options=filter_options,
        value="unannotated_only",
        layout=widgets.Layout(width="98%"),
    )

    prev_btn = widgets.Button(description="◀ Prev", layout=widgets.Layout(width="auto"))
    next_btn = widgets.Button(
        description="Next ▶",
        button_style="success",
        layout=widgets.Layout(width="auto"),
    )

    # Annotation controls
    followup_yes_check = widgets.Checkbox(
        value=False, description="Follow-up Needed", indent=False
    )
    followup_no_check = widgets.Checkbox(
        value=False, description="No Follow-up", indent=False
    )
    uncertain_check = widgets.Checkbox(
        value=False, description="Uncertain", indent=False
    )
    auto_advance_check = widgets.Checkbox(
        value=True, description="Auto-advance after save", indent=False
    )

    notes_text = widgets.Textarea(
        placeholder="Optional notes...",
        layout=widgets.Layout(width="100%", height="70px"),
    )

    clear_annotation_btn = widgets.Button(
        description="Clear", button_style="warning", layout=widgets.Layout(width="auto")
    )
    export_btn = widgets.Button(
        description="Export Annotations",
        button_style="success",
        layout=widgets.Layout(width="auto"),
    )

    metrics_output = widgets.Output()
    output = widgets.Output()
    annotation_status = widgets.Output()
    status_header = widgets.HTML()

    # Helper functions
    def get_filtered_indices(filter_value):
        """Get filtered indices based on all filters (modality, facility, finding, date, and detection attributes)."""
        if filter_value.startswith("sep"):
            return []

        indices = []
        for i in range(len(state["df"])):
            row = state["df"].iloc[i]

            # Apply modality filter
            if modality_filter.value != "all":
                if (
                    pd.isna(row.get("modality"))
                    or row["modality"] != modality_filter.value
                ):
                    continue

            # Apply facility filter
            if facility_filter.value != "all":
                if (
                    pd.isna(row.get("sending_facility"))
                    or row["sending_facility"] != facility_filter.value
                ):
                    continue

            # Apply radiologist filter
            if radiologist_filter.value != "all":
                radiologist_value = row.get("principal_result_interpreter")
                # Handle array or string
                if pd.isna(radiologist_value):
                    continue
                if isinstance(radiologist_value, (list, tuple)):
                    radiologist_name = (
                        str(radiologist_value[0])
                        if len(radiologist_value) > 0 and radiologist_value[0]
                        else ""
                    )
                else:
                    radiologist_name = str(radiologist_value)
                if radiologist_name != radiologist_filter.value:
                    continue

            # Apply finding search filter (case-insensitive substring match)
            if finding_search.value.strip():
                search_term = finding_search.value.strip().lower()
                finding_value = str(row.get("followup_finding", "")).lower()
                if search_term not in finding_value:
                    continue

            # Apply date filter
            if date_filter.value != "all":
                if pd.isna(row.get("message_dt")):
                    continue
                msg_date = pd.to_datetime(row["message_dt"])
                days_ago = int(date_filter.value)
                cutoff_date = pd.Timestamp.now() - pd.Timedelta(days=days_ago)
                if msg_date < cutoff_date:
                    continue

            # Apply detection attribute filter
            include = False
            if filter_value == "all":
                include = True
            elif filter_value == "followup_yes":
                include = row["followup_detected"] == True
            elif filter_value == "followup_no":
                include = row["followup_detected"] == False
            elif filter_value == "high_conf":
                include = row["followup_confidence"] == "high"
            elif filter_value == "low_conf":
                include = row["followup_confidence"] == "low"
            elif filter_value == "high_conf_yes":
                include = (row["followup_detected"] == True) and (
                    row["followup_confidence"] == "high"
                )
            elif filter_value == "low_conf_yes":
                include = (row["followup_detected"] == True) and (
                    row["followup_confidence"] == "low"
                )
            elif filter_value == "annotated_only":
                include = i in annotations and annotations[i].get("reviewed", False)
            elif filter_value == "unannotated_only":
                include = i not in annotations or not annotations[i].get(
                    "reviewed", False
                )

            if include:
                indices.append(i)

        return indices

    def render_metrics():
        """Render compact metrics display with validation stats."""
        df_data = state["df"]
        total = len(df_data)

        followup_yes = (df_data["followup_detected"] == True).sum()
        high_conf_yes = (
            (df_data["followup_detected"] == True)
            & (df_data["followup_confidence"] == "high")
        ).sum()
        low_conf_yes = (
            (df_data["followup_detected"] == True)
            & (df_data["followup_confidence"] == "low")
        ).sum()

        annotated_count = sum(
            1 for a in annotations.values() if a.get("reviewed", False)
        )
        progress_pct = (annotated_count / total * 100) if total > 0 else 0

        yes_ann = sum(
            1
            for a in annotations.values()
            if a.get("reviewed") and a.get("ground_truth") is True
        )
        no_ann = sum(
            1
            for a in annotations.values()
            if a.get("reviewed") and a.get("ground_truth") is False
        )
        uncertain_ann = sum(
            1
            for a in annotations.values()
            if a.get("reviewed") and a.get("ground_truth") is None
        )

        # Calculate validation stats
        validation_html = ""
        if "ground_truth" in state:
            annotated_indices = [
                i
                for i in range(len(state["df"]))
                if i in annotations
                and annotations[i].get("reviewed", False)
                and annotations[i].get("ground_truth") is not None
            ]

            if annotated_indices:
                agree_count = 0
                disagree_count = 0

                for idx in annotated_indices:
                    row = state["df"].iloc[idx]
                    model_detected = row["followup_detected"]
                    human_detected = annotations[idx]["ground_truth"]

                    if model_detected == human_detected:
                        agree_count += 1
                    else:
                        disagree_count += 1

                total_annotated = len(annotated_indices)
                agreement_rate = (
                    100.0 * agree_count / total_annotated if total_annotated > 0 else 0
                )

                # Color based on agreement rate
                if agreement_rate >= 80:
                    color = "#4CAF50"  # Green
                elif agreement_rate >= 60:
                    color = "#FF9800"  # Orange
                else:
                    color = "#F44336"  # Red

                validation_html = f"""
                <div style='background: {color}; padding: 12px 16px; border-radius: 6px; color: white; box-shadow: 0 2px 8px rgba(0,0,0,0.15); margin-bottom: 16px;'>
                    <div style='display: flex; justify-content: space-between; align-items: center;'>
                        <div style='flex: 1;'>
                            <div style='font-size: 11px; font-weight: 600; text-transform: uppercase; opacity: 0.9;'>Model Validation (current session)</div>
                            <div style='font-size: 16px; margin-top: 2px;'>{agree_count} agree • {disagree_count} disagree</div>
                        </div>
                        <div style='text-align: right;'>
                            <div style='font-size: 24px; font-weight: 700; line-height: 1;'>{agreement_rate:.1f}%</div>
                            <div style='font-size: 10px; opacity: 0.9; margin-top: 2px;'>Agreement</div>
                        </div>
                    </div>
                </div>
                """

        html = f"""
        <div style='display: flex; gap: 16px; margin-bottom: 16px;'>
            <div style='flex: 1; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 12px 16px; border-radius: 6px; color: white; box-shadow: 0 2px 8px rgba(0,0,0,0.15);'>
                <div style='display: flex; justify-content: space-between; align-items: center;'>
                    <div style='flex: 1;'>
                        <div style='font-size: 16px; opacity: 0.9; margin-bottom: 4px;'>
                            {total:,} reports in sample • {len(state['filtered_indices'])} in filtered view
                        </div>
                    </div>
                    <div style='text-align: right;'>
                        <div style='font-size: 24px; font-weight: 700; line-height: 1;'>{annotated_count}/{total}</div>
                        <div style='font-size: 10px; opacity: 0.9; margin-top: 2px;'>Reviewed ({progress_pct:.0f}%)</div>
                    </div>
                </div>
            </div>
            {f'<div style="flex: 1;">{validation_html}</div>' if validation_html else ''}
        </div>
        """

        with metrics_output:
            metrics_output.clear_output(wait=True)
            display(HTML(html))

    def update_status_header():
        """Update the status header with accession and demographics."""
        if state["filtered_indices"] and state["current_pos"] < len(
            state["filtered_indices"]
        ):
            idx = state["filtered_indices"][state["current_pos"]]
            row = state["df"].iloc[idx]

            # Get demographics with safe handling
            accession = row.get("obr_3_filler_order_number", "N/A")
            age = row.get("patient_age", "N/A")
            sex = row.get("sex", "N/A")
            race = row.get("race", "N/A")
            facility = row.get("sending_facility", "N/A")
            modality = row.get("modality", "N/A")

            # Get diagnoses if available
            diagnoses_str = ""
            if "diagnoses" in row and row["diagnoses"]:
                try:
                    if (
                        isinstance(row["diagnoses"], (list, tuple))
                        and len(row["diagnoses"]) > 0
                    ):
                        dx_list = []
                        for d in row["diagnoses"][:3]:
                            if isinstance(d, dict):
                                code = d.get("diagnosis_code", "")
                                text = d.get("diagnosis_code_text", "")
                                dx_list.append(f"{code}: {text}" if text else code)
                            elif hasattr(d, "diagnosis_code"):  # Spark Row object
                                code = getattr(d, "diagnosis_code", "")
                                text = getattr(d, "diagnosis_code_text", "")
                                dx_list.append(f"{code}: {text}" if text else code)
                            else:
                                dx_list.append(str(d))
                        diagnoses_str = " | ".join(dx_list)
                        if len(row["diagnoses"]) > 3:
                            diagnoses_str += f' (+{len(row["diagnoses"])-3} more)'
                except Exception as e:
                    diagnoses_str = ""

            status_header.value = f"""
            <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 12px 16px; border-radius: 6px; color: white; margin-bottom: 12px; box-shadow: 0 3px 8px rgba(0,0,0,0.15);'>
                <div style='font-size: 20px; font-weight: 700; margin-bottom: 8px;'>Report {state['current_pos'] + 1} of {len(state['filtered_indices'])}</div>
                <div style='display: flex; gap: 12px; align-items: center;'>
                    <div style='background: {'#4CAF50' if row['followup_detected'] else '#9E9E9E'}; color: white; padding: 4px 12px; border-radius: 4px; font-weight: 600; font-size: 11px;'>
                        {'FOLLOW-UP' if row['followup_detected'] else 'NO FOLLOW-UP'}
                    </div>
                    <div style='background: {'#FF9800' if row['followup_confidence'] == 'high' else '#2196F3'}; color: white; padding: 4px 10px; border-radius: 3px; font-weight: 600; font-size: 10px;'>
                        {row['followup_confidence'].upper()} CONF
                    </div>
                    <div style='font-size: 11px; opacity: 0.9;'>Acc: {accession}</div>
                </div>
            </div>
            """
        else:
            status_header.value = ""

    def render_report():
        """Render current report."""
        update_status_header()

        if not state["filtered_indices"]:
            with output:
                output.clear_output(wait=True)
                display(
                    HTML(
                        '<div style="text-align: center; padding: 80px; color: #999;"><h2>No reports match this filter</h2><p style="margin-top: 16px;">Try changing the filter above</p></div>'
                    )
                )
            return

        idx = state["filtered_indices"][state["current_pos"]]
        row = state["df"].iloc[idx]

        detected = row["followup_detected"]
        confidence = row["followup_confidence"]
        snippet = row["followup_snippet"] if pd.notna(row["followup_snippet"]) else ""
        finding = (
            row.get("followup_finding", "")
            if pd.notna(row.get("followup_finding"))
            else ""
        )
        report_text = row[report_col].strip()

        # Get patient demographics
        age = row.get("patient_age", "N/A")
        sex = row.get("sex", "N/A")
        race = row.get("race", "N/A")

        # Get exam information
        facility = row.get("sending_facility", "N/A")
        modality = row.get("modality", "N/A")
        service_name = row.get("service_name", "N/A")
        service_identifier = row.get("service_identifier", "N/A")

        # Get diagnoses
        diagnoses_str = ""
        if "diagnoses" in row and row["diagnoses"]:
            try:
                if (
                    isinstance(row["diagnoses"], (list, tuple))
                    and len(row["diagnoses"]) > 0
                ):
                    dx_list = []
                    for d in row["diagnoses"]:
                        if isinstance(d, dict):
                            code = d.get("diagnosis_code", "")
                            text = d.get("diagnosis_code_text", "")
                            dx_list.append(f"{code}: {text}" if text else code)
                        elif hasattr(d, "diagnosis_code"):
                            code = getattr(d, "diagnosis_code", "")
                            text = getattr(d, "diagnosis_code_text", "")
                            dx_list.append(f"{code}: {text}" if text else code)
                        else:
                            dx_list.append(str(d))
                    diagnoses_str = "<br>".join(dx_list)
            except:
                diagnoses_str = ""

        # Highlight snippet with newline preservation
        snippet_color = "#FF9800" if confidence == "high" else "#03A9F4"
        if snippet:
            report_html = highlight_snippet_in_text(report_text, snippet, snippet_color)
        else:
            report_html = html_lib.escape(report_text)

        # Choose header color based on follow-up detection
        header_color = "#4CAF50" if detected else "#5f6368"
        header_gradient = f"linear-gradient(135deg, {header_color} 0%, {get_darker_shade(header_color)} 100%)"

        # Build HTML with 3-column layout
        html = f"""
        <div style='font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; gap: 20px;'>
            <div style='flex: 0 0 700px;'>
        """

        # Build snippet HTML for right sidebar
        snippet_html = ""
        if snippet:
            escaped_snippet = html_lib.escape(snippet)
            snippet_html = f"""
            <div style='background: #FFF9C4; border-left: 4px solid {snippet_color}; padding: 16px; margin-bottom: 16px; border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.06);'>
                <div style='font-size: 11px; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;'>Extracted Snippet</div>
                <div style='font-size: 13px; color: #333; line-height: 1.6; font-style: italic; white-space: pre-wrap;'>"{escaped_snippet}"</div>
                {f"<div style='margin-top: 12px; padding-top: 12px; border-top: 1px solid #E0E0E0;'><div style='font-size: 11px; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px;'>Finding Type</div><div style='font-size: 13px; color: #333; font-weight: 600;'>{finding}</div></div>" if finding else ''}
            </div>
            """

        # Show annotation badge if exists
        annotation_badge = ""
        if idx in annotations and annotations[idx].get("reviewed", False):
            ann = annotations[idx]
            gt = ann.get("ground_truth")
            if gt is True:
                annotation_badge = '<div style="background: #4CAF50; color: white; padding: 8px 16px; border-radius: 4px; font-size: 13px; font-weight: 600; margin-bottom: 12px; display: inline-block;">✓ Your Review: Follow-up Needed</div>'
            elif gt is False:
                annotation_badge = '<div style="background: #757575; color: white; padding: 8px 16px; border-radius: 4px; font-size: 13px; font-weight: 600; margin-bottom: 12px; display: inline-block;">✓ Your Review: No Follow-up</div>'
            else:
                annotation_badge = '<div style="background: #FF9800; color: white; padding: 8px 16px; border-radius: 4px; font-size: 13px; font-weight: 600; margin-bottom: 12px; display: inline-block;">? Your Review: Uncertain</div>'

        # Report text with preserved formatting
        html += f"""
                {annotation_badge}
                """
        html += f"""
                <div style='background: white; border: 2px solid #E0E0E0; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.06); height: calc(100vh - 250px); display: flex; flex-direction: column;'>
                    <div style='background: {header_gradient}; color: white; padding: 14px 20px; flex-shrink: 0;'>
                        <div style='font-weight: 700; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px;'>Radiology Report</div>
                    </div>
                    <div style='padding: 24px; background: #FAFAFA; min-width: 800px; overflow-y: auto; line-height: 1.8; font-size: 14px; color: #333; white-space: pre-wrap; font-family: "SF Mono", Monaco, "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace; flex: 1;'>{report_html}</div>
                </div>
            </div>

            <div style='flex: 1; min-width: 250px;'>
                <div style='position: sticky; top: 0;'>
                    {snippet_html}
                    <div style='background: white; border: 2px solid #E0E0E0; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.06); margin-bottom: 20px;'>
                        <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 12px 16px;'>
                            <div style='font-weight: 700; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;'>Patient Information</div>
                        </div>
                    <div style='padding: 16px; font-size: 13px; line-height: 1.8;'>
                        <div style='margin-bottom: 12px;'>
                            <div style='font-weight: 600; color: #666; font-size: 11px; text-transform: uppercase; margin-bottom: 4px;'>Demographics</div>
                            <div><span style='font-weight: 600; color: #888;'>Age:</span> {int(age) if isinstance(age, (int, float)) and age != 'N/A' else age} years</div>
                            <div><span style='font-weight: 600; color: #888;'>Sex:</span> {sex}</div>
                            <div><span style='font-weight: 600; color: #888;'>Race:</span> {race}</div>
                        </div>
                        {f"<div style='margin-bottom: 12px;'><div style='font-weight: 600; color: #666; font-size: 11px; text-transform: uppercase; margin-bottom: 4px;'>Diagnoses</div><div style='font-size: 12px; line-height: 1.6;'>{diagnoses_str}</div></div>" if diagnoses_str else ''}
                    </div>
                    </div>
                    <div style='background: white; border: 2px solid #E0E0E0; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.06);'>
                        <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 12px 16px;'>
                            <div style='font-weight: 700; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;'>Exam Information</div>
                        </div>
                        <div style='padding: 16px; font-size: 13px; line-height: 1.8;'>
                            <div style='margin-bottom: 12px;'>
                                <div style='font-weight: 600; color: #666; font-size: 11px; text-transform: uppercase; margin-bottom: 4px;'>Modality</div>
                                <div>{modality}</div>
                            </div>
                            <div style='margin-bottom: 12px;'>
                                <div style='font-weight: 600; color: #666; font-size: 11px; text-transform: uppercase; margin-bottom: 4px;'>Service Name</div>
                                <div style='word-wrap: break-word;'>{service_name}</div>
                            </div>
                            <div style='margin-bottom: 12px;'>
                                <div style='font-weight: 600; color: #666; font-size: 11px; text-transform: uppercase; margin-bottom: 4px;'>Service ID</div>
                                <div>{service_identifier}</div>
                            </div>
                            <div>
                                <div style='font-weight: 600; color: #666; font-size: 11px; text-transform: uppercase; margin-bottom: 4px;'>Facility</div>
                                <div style='word-wrap: break-word;'>{facility}</div>
                            </div>
                        </div>
                    </div>
        """

        # Add Model Validation section if human annotation exists
        review_agreement_html = ""
        if (
            "ground_truth" in state
            and idx in state["ground_truth"]
            and state["ground_truth"][idx].get("reviewed", False)
        ):
            ann = state["ground_truth"][idx]
            human_annotation = ann.get("ground_truth")

            # Only show if not uncertain (None means uncertain)
            if human_annotation is not None:
                model_detected = detected
                human_detected = human_annotation

                if model_detected == human_detected:
                    # Agreement
                    agreement_color = "#4CAF50"  # Green
                    agreement_icon = "✓"
                    if model_detected:
                        agreement_text = "Agreement: Follow-up Detected"
                    else:
                        agreement_text = "Agreement: No Follow-up"
                else:
                    # Disagreement
                    agreement_color = "#FF9800"  # Orange
                    agreement_icon = "⚠"
                    if model_detected and not human_detected:
                        agreement_text = (
                            "Disagreement: Model detected, Reviewer did not"
                        )
                    else:
                        agreement_text = (
                            "Disagreement: Model did not detect, Reviewer did"
                        )

                review_agreement_html = f"""
                        <div style='border-top: 2px solid #E0E0E0; margin-top: 24px;'>
                            <div style='background: {agreement_color}; color: white; padding: 12px 16px;'>
                                <div style='font-weight: 700; font-size: 14px; display: flex; align-items: center; gap: 8px;'>
                                    <span style='font-size: 18px;'>{agreement_icon}</span>
                                    <span>Model Validation</span>
                                </div>
                            </div>
                            <div style='padding: 16px; font-size: 13px; line-height: 1.8;'>
                                <div style='font-weight: 600; color: #333;'>{agreement_text}</div>
                                <div style='margin-top: 8px; font-size: 12px; color: #666;'>
                                    <div>Model: {'Follow-up detected' if model_detected else 'No follow-up'} ({confidence} confidence)</div>
                                    <div>Reviewer: {'Follow-up needed' if human_detected else 'No follow-up'}</div>
                                </div>
                            </div>
                        </div>
                """

        html += (
            review_agreement_html
            + """
                    </div>  <!-- Close Patient/Exam info container -->
                </div>  <!-- Close sticky wrapper -->
            </div>  <!-- Close right column -->
        </div>  <!-- Close flex container -->
        """
        )

        # Add JavaScript to auto-scroll to highlighted snippet
        scroll_script = """
        <script>
        (function() {
            // Wait for DOM to be ready
            setTimeout(function() {
                var snippet = document.getElementById('highlighted-snippet');
                if (snippet) {
                    // Scroll the snippet into view smoothly, centered vertically
                    snippet.scrollIntoView({
                        behavior: 'smooth',
                        block: 'center',
                        inline: 'nearest'
                    });
                }
            }, 100);
        })();
        </script>
        """

        with output:
            output.clear_output(wait=True)
            display(HTML(html + scroll_script))

        load_annotation_state(idx)

    def load_annotation_state(idx):
        """Load annotation state for current report."""
        followup_yes_check.unobserve_all()
        followup_no_check.unobserve_all()
        uncertain_check.unobserve_all()
        notes_text.unobserve_all()

        if idx in annotations:
            ann = annotations[idx]
            gt = ann.get("ground_truth")
            followup_yes_check.value = gt is True
            followup_no_check.value = gt is False
            uncertain_check.value = gt is None and ann.get("reviewed", False)
            notes_text.value = ann.get("notes", "")
        else:
            followup_yes_check.value = False
            followup_no_check.value = False
            uncertain_check.value = False
            notes_text.value = ""

        followup_yes_check.observe(on_annotation_change, names="value")
        followup_no_check.observe(on_annotation_change, names="value")
        uncertain_check.observe(on_annotation_change, names="value")
        notes_text.observe(on_notes_change, names="value")

    def auto_save_annotation():
        """Auto-save current annotation."""
        if not state["filtered_indices"] or state["current_pos"] >= len(
            state["filtered_indices"]
        ):
            return

        idx = state["filtered_indices"][state["current_pos"]]

        if followup_yes_check.value:
            ground_truth = True
        elif followup_no_check.value:
            ground_truth = False
        elif uncertain_check.value:
            ground_truth = None
        else:
            if idx in annotations:
                del annotations[idx]
            render_metrics()
            render_report()  # Re-render to hide Model Validation section
            return

        annotations[idx] = {
            "ground_truth": ground_truth,
            "notes": notes_text.value,
            "reviewed": True,
        }

        render_metrics()  # Updates both metrics and validation stats
        render_report()  # Re-render to show Model Validation section

        if (
            auto_advance_check.value
            and state["current_pos"] < len(state["filtered_indices"]) - 1
        ):
            state["current_pos"] += 1
            render_report()

    def on_annotation_change(change):
        """Handle annotation checkbox changes."""
        if not change["new"]:
            auto_save_annotation()
            return

        # Mutual exclusivity
        if change["owner"] == followup_yes_check:
            followup_no_check.unobserve_all()
            uncertain_check.unobserve_all()
            followup_no_check.value = False
            uncertain_check.value = False
            followup_no_check.observe(on_annotation_change, names="value")
            uncertain_check.observe(on_annotation_change, names="value")
        elif change["owner"] == followup_no_check:
            followup_yes_check.unobserve_all()
            uncertain_check.unobserve_all()
            followup_yes_check.value = False
            uncertain_check.value = False
            followup_yes_check.observe(on_annotation_change, names="value")
            uncertain_check.observe(on_annotation_change, names="value")
        elif change["owner"] == uncertain_check:
            followup_yes_check.unobserve_all()
            followup_no_check.unobserve_all()
            followup_yes_check.value = False
            followup_no_check.value = False
            followup_yes_check.observe(on_annotation_change, names="value")
            followup_no_check.observe(on_annotation_change, names="value")

        auto_save_annotation()

    def on_notes_change(change):
        auto_save_annotation()

    def on_prev(b):
        if state["filtered_indices"]:
            state["current_pos"] = max(0, state["current_pos"] - 1)
            render_report()

    def on_next(b):
        if state["filtered_indices"]:
            state["current_pos"] = min(
                len(state["filtered_indices"]) - 1, state["current_pos"] + 1
            )
            render_report()

    def on_filter_change(change):
        state["filtered_indices"] = get_filtered_indices(filter_dropdown.value)
        state["current_pos"] = 0
        render_metrics()
        render_report()

    def on_modality_or_date_change(change):
        """Handle modality or date filter changes."""
        state["filtered_indices"] = get_filtered_indices(filter_dropdown.value)
        state["current_pos"] = 0
        render_metrics()
        render_report()

    def on_accession_jump(b):
        """Jump to report by accession number."""
        target_accession = accession_jump.value.strip()
        if not target_accession:
            return

        # Search for matching accession in filtered indices
        for i, idx in enumerate(state["filtered_indices"]):
            row = state["df"].iloc[idx]
            if (
                str(row.get("obr_3_filler_order_number", "")).strip()
                == target_accession
            ):
                state["current_pos"] = i
                render_report()
                return

        # If not found in filtered view, search entire dataset
        for idx in range(len(state["df"])):
            row = state["df"].iloc[idx]
            if (
                str(row.get("obr_3_filler_order_number", "")).strip()
                == target_accession
            ):
                # Update filters to show this report
                modality_filter.value = (
                    row.get("modality", "all")
                    if row.get("modality") in [opt[1] for opt in modality_options]
                    else "all"
                )
                facility_filter.value = "all"
                radiologist_filter.value = "all"
                finding_search.value = ""
                date_filter.value = "all"
                state["filtered_indices"] = get_filtered_indices(filter_dropdown.value)
                # Find position in new filtered list
                for i, new_idx in enumerate(state["filtered_indices"]):
                    if new_idx == idx:
                        state["current_pos"] = i
                        break
                render_metrics()
                render_report()
                return

        # Not found
        with annotation_status:
            annotation_status.clear_output()
            print(f"⚠️  Accession '{target_accession}' not found")

    def on_clear_annotation(b):
        if state["filtered_indices"] and state["current_pos"] < len(
            state["filtered_indices"]
        ):
            idx = state["filtered_indices"][state["current_pos"]]
            if idx in annotations:
                del annotations[idx]
            load_annotation_state(idx)
            render_metrics()

    def on_export(b):
        """Export annotations to CSV and optionally to Delta table."""
        import time
        from IPython.display import display, clear_output

        # Disable button and show loading state
        export_btn.disabled = True
        export_btn.description = "⏳ Exporting..."
        export_btn.button_style = "warning"

        # Force immediate widget update
        import IPython

        if IPython.get_ipython() is not None:
            IPython.get_ipython().kernel.do_one_iteration()

        try:
            if not annotations:
                with annotation_status:
                    annotation_status.clear_output(wait=True)
                    print("⚠️  No annotations to export")
                return

            with annotation_status:
                annotation_status.clear_output(wait=True)
                print("📦 Preparing annotations for export...")

            # Force display update
            if IPython.get_ipython() is not None:
                IPython.get_ipython().kernel.do_one_iteration()

            # Build annotations DataFrame
            ann_data = []
            for idx, ann in annotations.items():
                if ann.get("reviewed", False):
                    row = state["df"].iloc[idx]
                    ann_data.append(
                        {
                            "obr_3_filler_order_number": row.get(
                                "obr_3_filler_order_number"
                            ),
                            "model_followup_detected": row["followup_detected"],
                            "model_confidence": row["followup_confidence"],
                            "model_snippet": row["followup_snippet"],
                            "human_ground_truth": ann["ground_truth"],
                            "human_notes": ann.get("notes", ""),
                        }
                    )

            if not ann_data:
                with annotation_status:
                    annotation_status.clear_output()
                    print("⚠️  No reviewed annotations to export")
                return

            ann_df = pd.DataFrame(ann_data)

            yes_count = sum(
                1
                for a in annotations.values()
                if a.get("reviewed") and a.get("ground_truth") is True
            )
            no_count = sum(
                1
                for a in annotations.values()
                if a.get("reviewed") and a.get("ground_truth") is False
            )
            uncertain_count = sum(
                1
                for a in annotations.values()
                if a.get("reviewed") and a.get("ground_truth") is None
            )

            # Export to CSV with timestamp
            from datetime import datetime

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"followup_annotations_{timestamp}.csv"

            with annotation_status:
                annotation_status.clear_output(wait=True)
                print(f"📦 Preparing annotations for export...")
                print(f"💾 Saving to CSV: {output_path}...")

            # Force display update
            if IPython.get_ipython() is not None:
                IPython.get_ipython().kernel.do_one_iteration()

            ann_df.to_csv(output_path, index=False)

            # Try to write to Delta table using Spark (required for MERGE operations)
            # Note: Trino doesn't support UPDATE/MERGE on Delta tables, only Spark does
            delta_success = False
            delta_error = None

            with annotation_status:
                annotation_status.clear_output(wait=True)
                print(f"💾 Saved to CSV: {output_path}")
                print(f"🔄 Updating Delta Lake table...")

            # Force display update
            if IPython.get_ipython() is not None:
                IPython.get_ipython().kernel.do_one_iteration()

            try:
                from pyspark.sql import SparkSession
                from pyspark.sql import functions as F

                # Get or create Spark session
                # S3 and Hive Metastore settings come from spark-defaults.conf
                spark = (
                    SparkSession.builder.appName("followup-detection-export")
                    .enableHiveSupport()
                    .getOrCreate()
                )

                # Convert to Spark DataFrame with explicit schema to avoid type issues
                from pyspark.sql.types import (
                    StructType,
                    StructField,
                    StringType,
                    BooleanType,
                )

                schema = StructType(
                    [
                        StructField("obr_3_filler_order_number", StringType(), True),
                        StructField("model_followup_detected", BooleanType(), True),
                        StructField("model_confidence", StringType(), True),
                        StructField("model_snippet", StringType(), True),
                        StructField("human_ground_truth", BooleanType(), True),
                        StructField("human_notes", StringType(), True),
                    ]
                )

                spark_df = spark.createDataFrame(ann_df, schema=schema)

                # Create temp view for MERGE
                spark_df.createOrReplaceTempView("human_annotations")

                # MERGE into reports
                spark.sql(
                    """
                    MERGE INTO reports AS target
                    USING human_annotations AS source
                    ON target.obr_3_filler_order_number = source.obr_3_filler_order_number
                    WHEN MATCHED THEN UPDATE SET
                        target.human_ground_truth = source.human_ground_truth,
                        target.human_notes = source.human_notes,
                        target.human_reviewed_at = current_timestamp()
                """
                )

                # Clean up temp view
                spark.catalog.dropTempView("human_annotations")
                delta_success = True

            except Exception as e:
                delta_error = str(e)

            # Final success message
            with annotation_status:
                annotation_status.clear_output(wait=True)
                print(f"✅ Successfully exported {len(ann_data)} annotations")
                print(f"   📄 CSV file: {output_path}")
                if delta_success:
                    print(f"   💾 Updated Delta Lake table: reports")
                else:
                    print(f"   ⚠️  Delta Lake update failed (Spark required for MERGE)")
                    if delta_error:
                        print(f"       Error: {delta_error}")
                print(f"\n📊 Annotation Summary:")
                print(f"   ✓ {yes_count} Follow-up Needed")
                print(f"   ✗ {no_count} No Follow-up")
                print(f"   ? {uncertain_count} Uncertain")

            # Force final display update
            if IPython.get_ipython() is not None:
                IPython.get_ipython().kernel.do_one_iteration()

        finally:
            # Always re-enable button
            export_btn.disabled = False
            export_btn.description = "Export Annotations"
            export_btn.button_style = "success"

    # Wire up handlers
    prev_btn.on_click(on_prev)
    next_btn.on_click(on_next)
    filter_dropdown.observe(on_filter_change, names="value")
    modality_filter.observe(on_modality_or_date_change, names="value")
    facility_filter.observe(on_modality_or_date_change, names="value")
    radiologist_filter.observe(on_modality_or_date_change, names="value")
    finding_search.observe(on_modality_or_date_change, names="value")
    date_filter.observe(on_modality_or_date_change, names="value")
    accession_jump_btn.on_click(on_accession_jump)
    clear_annotation_btn.on_click(on_clear_annotation)
    export_btn.on_click(on_export)
    followup_yes_check.observe(on_annotation_change, names="value")
    followup_no_check.observe(on_annotation_change, names="value")
    uncertain_check.observe(on_annotation_change, names="value")
    notes_text.observe(on_notes_change, names="value")

    # Initialize
    state["filtered_indices"] = get_filtered_indices(filter_dropdown.value)
    render_metrics()  # Includes validation stats
    update_status_header()
    render_report()

    # Build UI
    control_panel = widgets.VBox(
        [
            status_header,
            widgets.HTML(
                '<div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 12px; border-radius: 8px 8px 0 0; color: white;"><h2 style="margin: 0; font-size: 16px; font-weight: 600;">Control Panel</h2></div>'
            ),
            widgets.VBox(
                [
                    widgets.HTML(
                        '<div style="padding: 12px 12px 6px 12px; font-weight: 600; color: #555; font-size: 11px;">DATA FILTERS</div>'
                    ),
                    widgets.Box(
                        [modality_filter],
                        layout=widgets.Layout(padding="0 12px 4px 12px"),
                    ),
                    widgets.Box(
                        [facility_filter],
                        layout=widgets.Layout(padding="0 12px 4px 12px"),
                    ),
                    widgets.Box(
                        [radiologist_filter],
                        layout=widgets.Layout(padding="0 12px 4px 12px"),
                    ),
                    widgets.Box(
                        [finding_search],
                        layout=widgets.Layout(padding="0 12px 4px 12px"),
                    ),
                    widgets.Box(
                        [date_filter], layout=widgets.Layout(padding="0 12px 6px 12px")
                    ),
                ]
            ),
            widgets.VBox(
                [
                    widgets.HTML(
                        '<div style="padding: 0 12px 6px 12px; font-weight: 600; color: #555; font-size: 11px;">JUMP TO ACCESSION</div>'
                    ),
                    widgets.Box(
                        [accession_jump],
                        layout=widgets.Layout(padding="0 12px 4px 12px"),
                    ),
                    widgets.Box(
                        [accession_jump_btn],
                        layout=widgets.Layout(padding="0 12px 6px 12px"),
                    ),
                ]
            ),
            widgets.HTML(
                '<div style="border-top: 2px solid #E0E0E0; margin: 8px;"></div>'
            ),
            widgets.VBox(
                [
                    widgets.HTML(
                        '<div style="padding: 0 12px 6px 12px; font-weight: 600; color: #555; font-size: 11px;">DETECTION FILTER</div>'
                    ),
                    widgets.Box(
                        [filter_dropdown],
                        layout=widgets.Layout(padding="0 12px 6px 12px"),
                    ),
                ]
            ),
            widgets.HTML(
                '<div style="border-top: 2px solid #E0E0E0; margin: 8px;"></div>'
            ),
            widgets.HBox(
                [prev_btn, next_btn],
                layout=widgets.Layout(padding="0 12px 6px 12px", gap="6px"),
            ),
            widgets.HTML(
                '<div style="border-top: 2px solid #E0E0E0; margin: 8px;"></div>'
            ),
            widgets.VBox(
                [
                    widgets.HTML(
                        '<div style="padding: 0 12px 6px 12px; font-weight: 600; color: #555; font-size: 11px;">ANNOTATION</div>'
                    ),
                    widgets.Box(
                        [followup_yes_check],
                        layout=widgets.Layout(padding="0 12px 2px 12px"),
                    ),
                    widgets.Box(
                        [followup_no_check],
                        layout=widgets.Layout(padding="0 12px 2px 12px"),
                    ),
                    widgets.Box(
                        [uncertain_check],
                        layout=widgets.Layout(padding="0 12px 6px 12px"),
                    ),
                    widgets.Box(
                        [auto_advance_check],
                        layout=widgets.Layout(padding="0 12px 6px 12px"),
                    ),
                ]
            ),
            widgets.VBox(
                [
                    widgets.HTML(
                        '<div style="padding: 0 12px 4px 12px; font-weight: 600; color: #555; font-size: 11px;">NOTES</div>'
                    ),
                    widgets.Box(
                        [notes_text], layout=widgets.Layout(padding="0 12px 6px 12px")
                    ),
                ]
            ),
            widgets.HTML(
                '<div style="border-top: 2px solid #E0E0E0; margin: 8px;"></div>'
            ),
            widgets.HBox(
                [clear_annotation_btn, export_btn],
                layout=widgets.Layout(padding="0 12px 12px 12px", gap="6px"),
            ),
            annotation_status,
        ],
        layout=widgets.Layout(
            border="2px solid #E0E0E0",
            border_radius="8px",
            background_color="white",
            width="380px",
            min_width="380px",
            max_width="380px",
            overflow_y="auto",
            margin="0 20px 0 0",
            box_shadow="0 2px 12px rgba(0,0,0,0.08)",
        ),
    )

    main_content = widgets.VBox(
        [output], layout=widgets.Layout(flex="1", min_width="900px")
    )

    content_area = widgets.HBox(
        [control_panel, main_content],
        layout=widgets.Layout(
            align_items="flex-start", width="100%", max_width="1600px"
        ),
    )

    # Build and display final dashboard UI
    ui = widgets.VBox(
        [
            widgets.HTML(
                """
            <div style='background: white; padding: 20px 28px; border-bottom: 3px solid #667eea; margin-bottom: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);'>
                <h1 style='margin: 0; color: #667eea; font-size: 28px; font-weight: 700;'>Follow-up Detection Review Dashboard</h1>
            </div>
        """
            ),
            metrics_output,
            content_area,
        ],
        layout=widgets.Layout(background_color="#FAFAFA", padding="0", margin="0"),
    )

    display(ui)

    # return {
    #     'ui': ui,
    #     'filter_dropdown': filter_dropdown,
    #     'state': state,
    #     'annotations': annotations
    # }
