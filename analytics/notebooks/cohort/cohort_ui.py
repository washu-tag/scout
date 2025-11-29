"""
Scout Cohort Builder - UI Components Module

This module provides the interactive UI components for the cohort builder dashboard.
"""

import re
import html
import pandas as pd
import ipywidgets as widgets
from IPython.display import display, HTML

from cohort_builder import (
    export_cohort,
    PRIMARY_GRADIENT,
    SUCCESS_GRADIENT,
    PURPLE_PRIMARY,
    GREEN_SUCCESS,
    ORANGE_WARNING,
    RED_ERROR,
)


# ============================================================================
# TEXT HIGHLIGHTING
# ============================================================================


def _highlight_report_text(report_text, config):
    """
    Highlight search terms and negation phrases in report text using actual matching logic.

    Args:
        report_text: The report text to highlight
        config: Configuration dict with search terms

    Returns:
        HTML-escaped and highlighted text
    """
    if not report_text or pd.isna(report_text):
        return html.escape("No report text")

    from cohort_builder import has_positive_mention

    # Collect all search patterns from config
    search_patterns = []

    # Report text terms
    if config.get("report_text_terms"):
        terms = config["report_text_terms"].strip()
        if terms:
            patterns = [t.strip() for t in terms.split("\n") if t.strip()]
            search_patterns.extend(patterns)

    # If no search patterns, just escape and return
    if not search_patterns:
        return html.escape(report_text)

    # Find all matches and their negation status using actual logic
    report_lower = report_text.lower()
    all_matches = []

    # Default negation pattern (from cohort_builder.py) - must match cohort_builder.py
    negation_patterns = [
        r"no\s+(mri?\s+)?evidence",
        r"without\s+(mri?\s+)?evidence",
        r"negative\s+for",
        r"absence\s+of",
        r"ruled?\s+out",
        r"rules?\s+out",
        r"excluding",
        r"excluded",
        r"evaluat(?:e|ion)\s+(for)?",  # Updated to match "evaluate" and "evaluation"
        r"concern\s+(for)?",
        r"no\s+\w+\s+(suggest|indication|sign)",
        r"no\s+",  # Catches "no brain metastases", "no definite", etc.
    ]
    negation_regex = re.compile("|".join(negation_patterns), re.IGNORECASE)

    # Find all pattern matches
    for pattern in search_patterns:
        try:
            matches = re.finditer(pattern, report_lower, re.IGNORECASE | re.DOTALL)
            for match in matches:
                # Check 50 chars before and after the match (updated to match cohort_builder.py)
                start_pos = max(0, match.start() - 50)
                end_pos = min(len(report_lower), match.end() + 50)
                context = report_lower[start_pos:end_pos]

                # Check if negation exists in context
                has_negation = bool(negation_regex.search(context))

                all_matches.append(
                    {
                        "start": match.start(),
                        "end": match.end(),
                        "text": report_text[
                            match.start() : match.end()
                        ],  # Original case
                        "has_negation": has_negation,
                    }
                )
        except re.error:
            # Skip invalid regex patterns
            continue

    # Remove overlapping matches - keep the longer one
    unique_matches = []
    for match in sorted(
        all_matches, key=lambda m: (m["start"], -(m["end"] - m["start"]))
    ):
        # Check if this match overlaps with any already added
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
        # Add text after this match (up to the last position)
        result_parts.append(html.escape(report_text[match_info["end"] : last_end]))

        # Add highlighted match
        escaped_match = html.escape(match_info["text"])

        if match_info["has_negation"]:
            # Negated match - red background
            highlighted = f'<span style="background-color: #ffcdd2; font-weight: bold; border: 2px solid red; padding: 1px 3px; border-radius: 3px;" title="Negated match">{escaped_match}</span>'
        else:
            # Positive match - green background
            highlighted = f'<span style="background-color: #a5d6a7; font-weight: bold; border: 2px solid green; padding: 1px 3px; border-radius: 3px;" title="Positive match">{escaped_match}</span>'

        result_parts.append(highlighted)
        last_end = match_info["start"]

    # Add any remaining text before the first match
    result_parts.append(html.escape(report_text[0:last_end]))

    # Reverse and join (since we built from end to start)
    result_parts.reverse()
    return "".join(result_parts)


# ============================================================================
# FILTER CONTROLS
# ============================================================================


def create_filter_controls(state):
    """Create filter dropdown controls."""
    # Show/hide filter based on inclusion in cohort
    inclusion_filter = widgets.Dropdown(
        options=[
            ("All reports", "all"),
            ("Included in cohort", "included_cohort"),
            ("Excluded from cohort", "excluded_cohort"),
        ],
        value="all",
        description="Cohort:",
        layout=widgets.Layout(width="280px"),
        style={"description_width": "60px"},
    )

    # Filter by strong indicator (diagnosis code match)
    strong_filter = widgets.Dropdown(
        options=[
            ("All matches", "all"),
            ("Strong matches only (DX code)", "strong_only"),
            ("Text matches only", "text_only"),
        ],
        value="all",
        description="Match Type:",
        layout=widgets.Layout(width="280px"),
        style={"description_width": "80px"},
    )

    view_filter = widgets.Dropdown(
        options=[
            ("All reports", "all"),
            ("Annotated only", "annotated"),
            ("Unannotated only", "unannotated"),
        ],
        value="all",
        description="View:",
        layout=widgets.Layout(width="250px"),
    )

    annotation_filter = widgets.Dropdown(
        options=[
            ("All", "all"),
            ("Included", "included"),
            ("Excluded", "excluded"),
            ("Uncertain", "uncertain"),
        ],
        value="all",
        description="Annotation:",
        layout=widgets.Layout(width="250px"),
    )

    def apply_filters():
        """Apply filters and update filtered indices."""
        df = state["df"]
        annotations = state["annotations"]
        indices = []

        for idx in range(len(df)):
            # Inclusion filter (based on cohort inclusion status from negation filtering)
            if inclusion_filter.value == "included_cohort":
                if not df.iloc[idx].get("included_in_cohort", False):
                    continue
            elif inclusion_filter.value == "excluded_cohort":
                if df.iloc[idx].get("included_in_cohort", True):
                    continue

            # Strong match filter (based on has_strong_indicator flag)
            if strong_filter.value == "strong_only":
                if not df.iloc[idx].get("has_strong_indicator", False):
                    continue
            elif strong_filter.value == "text_only":
                if df.iloc[idx].get("has_strong_indicator", False):
                    continue

            # View filter (based on manual annotations)
            if view_filter.value == "annotated":
                if idx not in annotations or not annotations[idx].get(
                    "reviewed", False
                ):
                    continue
            elif view_filter.value == "unannotated":
                if idx in annotations and annotations[idx].get("reviewed", False):
                    continue

            # Annotation filter (based on manual annotations)
            if annotation_filter.value != "all":
                if idx not in annotations:
                    continue
                ann = annotations[idx]
                if not ann.get("reviewed", False):
                    continue

                if (
                    annotation_filter.value == "included"
                    and ann.get("included") != True
                ):
                    continue
                elif (
                    annotation_filter.value == "excluded"
                    and ann.get("included") != False
                ):
                    continue
                elif (
                    annotation_filter.value == "uncertain"
                    and ann.get("included") is not None
                ):
                    continue

            indices.append(idx)

        state["filtered_indices"] = indices
        state["current_pos"] = 0

        # Trigger render from state
        if "render_report" in state:
            state["render_report"]()

    inclusion_filter.observe(lambda change: apply_filters(), names="value")
    strong_filter.observe(lambda change: apply_filters(), names="value")
    view_filter.observe(lambda change: apply_filters(), names="value")
    annotation_filter.observe(lambda change: apply_filters(), names="value")

    container = widgets.HBox(
        [inclusion_filter, strong_filter, view_filter, annotation_filter],
        layout=widgets.Layout(margin="0 0 16px 0", gap="16px"),
    )

    return {
        "container": container,
        "inclusion_filter": inclusion_filter,
        "strong_filter": strong_filter,
        "view_filter": view_filter,
        "annotation_filter": annotation_filter,
    }


# ============================================================================
# METRICS DISPLAY
# ============================================================================


def create_metrics_display(state):
    """Create metrics output widget."""
    # Create a container that will hold the metrics HTML widget
    metrics_container = widgets.VBox()

    def render_metrics():
        """Render annotation progress metrics."""
        annotations = state["annotations"]
        df = state["df"]
        criteria_summary = state.get("criteria_summary", [])
        filtered_indices = state.get("filtered_indices", [])

        # Use filtered_indices to count only the reports being reviewed
        total_reports = len(filtered_indices) if filtered_indices else len(df)

        # Cohort inclusion counts (from SQL + negation filtering) for filtered reports
        if filtered_indices and "included_in_cohort" in df.columns:
            filtered_df = df.iloc[filtered_indices]
            cohort_included_count = int(filtered_df["included_in_cohort"].sum())
        else:
            cohort_included_count = total_reports
        cohort_excluded_count = total_reports - cohort_included_count
        cohort_included_pct = (
            (cohort_included_count / total_reports * 100) if total_reports > 0 else 0
        )

        # Manual annotation counts for filtered reports only
        filtered_annotations = {
            idx: annotations[idx] for idx in filtered_indices if idx in annotations
        }
        annotated_count = sum(
            1 for a in filtered_annotations.values() if a.get("reviewed", False)
        )
        included_count = sum(
            1
            for a in filtered_annotations.values()
            if a.get("reviewed", False) and a.get("included") == True
        )
        excluded_count = sum(
            1
            for a in filtered_annotations.values()
            if a.get("reviewed", False) and a.get("included") == False
        )
        uncertain_count = sum(
            1
            for a in filtered_annotations.values()
            if a.get("reviewed", False) and a.get("included") is None
        )

        progress_pct = (
            (annotated_count / total_reports * 100) if total_reports > 0 else 0
        )

        # Format criteria summary
        criteria_html = "<br>".join([f"• {c}" for c in criteria_summary])

        metrics_html = f"""
        <div style='background: #f8f9fa; padding: 12px; border-radius: 6px; margin-bottom: 12px;'>
            <div style='display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px;'>
                <!-- Cohort Filtering Results -->
                <div style='padding: 10px; background: white; border-radius: 4px; border-left: 3px solid {PURPLE_PRIMARY};'>
                    <div style='font-weight: 600; font-size: 13px; margin-bottom: 6px;'>Automated Filtering</div>
                    <div style='display: flex; flex-direction: column; gap: 4px; font-size: 13px;'>
                        <div>
                            <span style='font-weight: 600;'>SQL:</span> {len(df)}
                        </div>
                        <div>
                            <span style='color: {GREEN_SUCCESS}; font-weight: 600;'>✓</span>
                            Included: {cohort_included_count} ({cohort_included_pct:.1f}%)
                        </div>
                        <div>
                            <span style='color: {RED_ERROR}; font-weight: 600;'>✗</span>
                            Excluded: {cohort_excluded_count} ({100-cohort_included_pct:.1f}%)
                        </div>
                    </div>
                </div>

                <!-- Manual Annotation Progress -->
                <div style='padding: 10px; background: white; border-radius: 4px; border-left: 3px solid {ORANGE_WARNING};'>
                    <div style='font-weight: 600; font-size: 13px; margin-bottom: 6px;'>Manual Review</div>
                    <div style='display: flex; flex-direction: column; gap: 4px; font-size: 13px;'>
                        <div>
                            <span style='font-weight: 600;'>Reviewed:</span> {annotated_count}/{total_reports} ({progress_pct:.1f}%)
                        </div>
                        <div>
                            <span style='color: {GREEN_SUCCESS};'>✓</span> Keep: {included_count}
                            <span style='margin-left: 8px; color: {RED_ERROR};'>✗</span> Discard: {excluded_count}
                            <span style='margin-left: 8px; color: {ORANGE_WARNING};'>?</span> Uncertain: {uncertain_count}
                        </div>
                    </div>
                </div>

                <!-- Criteria Summary -->
                <div style='padding: 8px; background: white; border-radius: 4px; border-left: 3px solid #6366f1;'>
                    <div style='font-weight: 600; font-size: 13px; margin-bottom: 4px;'>Applied Criteria</div>
                    <div style='font-size: 11px; color: #666; max-height: 80px; overflow-y: auto; line-height: 1.3;'>
                        {criteria_html if criteria_html else '<span style="color: #999;">No criteria specified</span>'}
                    </div>
                </div>
            </div>
        </div>
        """

        # Update the container with new HTML widget
        metrics_container.children = [widgets.HTML(metrics_html)]

    # Store render function in state for access by other components
    state["render_metrics"] = render_metrics

    # Initial render
    render_metrics()

    return metrics_container


# ============================================================================
# NAVIGATION CONTROLS
# ============================================================================


def create_navigation_controls(state):
    """Create navigation controls and report display."""
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
        annotations = state["annotations"]
        filtered_indices = state["filtered_indices"]
        current_pos = state["current_pos"]

        if not filtered_indices:
            with report_output:
                report_output.clear_output(wait=True)
                display(
                    HTML(
                        "<div style='padding: 20px; text-align: center;'>No reports match current filters</div>"
                    )
                )
            position_label.value = "<span style='font-weight: 600;'>No reports</span>"
            prev_btn.disabled = True
            next_btn.disabled = True
            return

        idx = filtered_indices[current_pos]
        row = df.iloc[idx]

        # Update position
        position_label.value = f"<span style='font-weight: 600;'>Report {current_pos + 1} of {len(filtered_indices)}</span>"

        # Update navigation buttons
        prev_btn.disabled = current_pos == 0
        next_btn.disabled = current_pos >= len(filtered_indices) - 1

        # Load annotation controls if they exist
        if "load_annotation" in state:
            state["load_annotation"](idx)

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

        # Apply highlighting to report text (already HTML-escaped by _highlight_report_text)
        highlighted_text = _highlight_report_text(report_text, state["config"])

        # Determine cohort inclusion status
        included_in_cohort = row.get("included_in_cohort", True)
        has_strong_indicator = row.get("has_strong_indicator", False)
        has_positive_mention = row.get("has_positive_mention", True)

        # Build compact cohort status badge
        if included_in_cohort:
            if has_strong_indicator:
                status_badge = f"<span style='background: {GREEN_SUCCESS}; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600;'>✓ INCLUDED (Diagnosis Code)</span>"
            else:
                status_badge = f"<span style='background: {GREEN_SUCCESS}; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600;'>✓ INCLUDED</span>"
        else:
            status_badge = f"<span style='background: {RED_ERROR}; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600;'>✗ EXCLUDED (Negated)</span>"

        # Get diagnoses - using pattern from followup_review_dashboard.py
        import ast

        diagnoses = []
        diagnoses_value = row.get("diagnoses")

        if diagnoses_value is not None:
            try:
                # If it's already a list/tuple (from DataFrame)
                if (
                    isinstance(diagnoses_value, (list, tuple))
                    and len(diagnoses_value) > 0
                ):
                    for d in diagnoses_value:
                        code = None
                        text = None

                        # Handle dict
                        if isinstance(d, dict):
                            code = d.get("diagnosis_code", "")
                            text = d.get("diagnosis_code_text", "")
                        # Handle NamedRowTuple or any object with attributes
                        elif hasattr(d, "diagnosis_code"):
                            code = getattr(d, "diagnosis_code", "")
                            text = getattr(d, "diagnosis_code_text", "")
                        # Handle tuple/list with positional values
                        elif isinstance(d, (list, tuple)) and len(d) >= 2:
                            code = d[0]
                            text = d[1]

                        if code:  # Only add if there's a code
                            diagnoses.append({"code": code, "text": text})

                # If it's a string, try to parse it
                elif isinstance(diagnoses_value, str):
                    s = diagnoses_value.strip()
                    if s and s != "[]":
                        parsed = ast.literal_eval(s)
                        if isinstance(parsed, list):
                            for d in parsed:
                                if isinstance(d, dict):
                                    code = d.get("diagnosis_code", "")
                                    text = d.get("diagnosis_code_text", "")
                                    if code:  # Only add if there's a code
                                        diagnoses.append({"code": code, "text": text})
            except Exception as e:
                # Silently fail and use empty list
                diagnoses = []

        # Check if any diagnosis matches the search criteria
        search_codes = []
        if state.get("config", {}).get("diagnosis_codes"):
            search_codes = [
                c.strip()
                for c in state["config"]["diagnosis_codes"].split(",")
                if c.strip()
            ]

        diagnoses_html_parts = []
        has_matching_diagnosis = False
        for diag in diagnoses[:8]:  # Show up to 8
            code = html.escape(str(diag.get("code", "")))
            text = html.escape(str(diag.get("text", "")))
            if code:
                # Check if this diagnosis code matches search criteria
                is_match = any(code == search_code for search_code in search_codes)
                if is_match:
                    has_matching_diagnosis = True
                    # Highlight matching diagnosis codes
                    diagnoses_html_parts.append(
                        f"<div style='font-size: 11px; padding: 2px 0;'><code style='background: #a5d6a7; padding: 1px 4px; border-radius: 2px; font-weight: 600;'>{code}</code> {text}</div>"
                    )
                else:
                    diagnoses_html_parts.append(
                        f"<div style='font-size: 11px; padding: 2px 0;'><code style='background: #e5e7eb; padding: 1px 4px; border-radius: 2px;'>{code}</code> {text}</div>"
                    )

        # Build diagnoses HTML
        diagnoses_html = (
            "".join(diagnoses_html_parts)
            if diagnoses_html_parts
            else "<div style='font-size: 11px; color: #999;'>None</div>"
        )

        # Determine diagnoses section background color - highlight if has_strong_indicator regardless
        diagnoses_section_bg = "#e8f5e9" if has_strong_indicator else "#f8f9fa"

        with report_output:
            report_output.clear_output(wait=True)
            report_html = f"""
            <div style='background: white; padding: 12px; border-radius: 6px; border: 1px solid #e5e7eb;'>
                <!-- Header with status badge -->
                <div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid #e5e7eb;'>
                    <div>
                        <span style='font-weight: 600; font-size: 14px;'>Report {current_pos + 1} of {len(filtered_indices)}</span>
                        <span style='margin-left: 12px; font-size: 13px; color: #666;'>{accession}</span>
                    </div>
                    {status_badge}
                </div>

                <!-- Two-column layout: Patient info on left, Report text in center -->
                <div style='display: grid; grid-template-columns: 320px 1fr; gap: 16px;'>
                    <!-- Left: Patient Info & Diagnoses -->
                    <div style='display: flex; flex-direction: column; gap: 12px;'>
                        <!-- Patient Demographics -->
                        <div style='background: #f8f9fa; padding: 12px; border-radius: 4px; font-size: 14px;'>
                            <div style='font-weight: 600; margin-bottom: 8px; font-size: 15px;'>Patient</div>
                            <div style='margin-bottom: 4px;'><strong>ID:</strong> {patient_id}</div>
                            <div style='margin-bottom: 4px;'><strong>Age:</strong> {patient_age} • <strong>Sex:</strong> {sex}</div>
                            <div style='margin-bottom: 4px;'><strong>Date:</strong> {requested_dt}</div>
                            <div style='margin-bottom: 4px;'><strong>Modality:</strong> {modality}</div>
                            <div><strong>Service:</strong> {service_name}</div>
                        </div>

                        <!-- Diagnoses -->
                        <div style='background: {diagnoses_section_bg}; padding: 12px; border-radius: 4px; font-size: 13px;{" border: 2px solid #a5d6a7;" if has_strong_indicator else ""}'>
                            <div style='font-weight: 600; margin-bottom: 8px; font-size: 15px;'>Diagnoses{" 💪" if has_strong_indicator else ""}</div>
                            <div style='max-height: 180px; overflow-y: auto;'>
                                {diagnoses_html}
                            </div>
                        </div>
                    </div>

                    <!-- Center: Report Text -->
                    <div>
                        <div style='margin-bottom: 8px; padding: 8px; background: #f3f4f6; border-radius: 4px; font-size: 13px;'>
                            <strong>Legend:</strong>
                            <span style='background-color: #a5d6a7; padding: 2px 6px; margin: 0 6px; border: 1px solid green; border-radius: 2px;'>✅ Match</span>
                            <span style='background-color: #ffcdd2; padding: 2px 6px; margin: 0 6px; border: 1px solid red; border-radius: 2px;'>❌ Negated</span>
                        </div>
                        <div style='background: #f9fafb; padding: 16px; border-radius: 4px; border: 1px solid #e5e7eb;
                                    max-height: 500px; overflow-y: auto; white-space: pre-wrap;
                                    font-family: monospace; font-size: 14px; line-height: 1.6;'>{highlighted_text}</div>
                    </div>
                </div>
            </div>
            """
            display(HTML(report_html))

    def on_prev(b):
        if state["filtered_indices"]:
            # Save current annotation before navigating (if auto_save_annotation exists)
            if "auto_save_annotation" in state:
                state["auto_save_annotation"]()
            state["current_pos"] = max(0, state["current_pos"] - 1)
            render_report()

    def on_next(b):
        if state["filtered_indices"]:
            # Save current annotation before navigating (if auto_save_annotation exists)
            if "auto_save_annotation" in state:
                state["auto_save_annotation"]()
            state["current_pos"] = min(
                len(state["filtered_indices"]) - 1, state["current_pos"] + 1
            )
            render_report()

    prev_btn.on_click(on_prev)
    next_btn.on_click(on_next)

    # Store render function in state
    state["render_report"] = render_report

    nav_bar = widgets.HBox(
        [prev_btn, position_label, next_btn],
        layout=widgets.Layout(justify_content="space-between", margin="0 0 16px 0"),
    )

    # Initial render
    render_report()

    return {
        "nav_bar": nav_bar,
        "prev_btn": prev_btn,
        "next_btn": next_btn,
        "position_label": position_label,
    }, report_output


# ============================================================================
# ANNOTATION CONTROLS
# ============================================================================


def create_annotation_controls(
    state, nav_widgets, metrics_output, report_output, export_widgets
):
    """Create annotation controls with auto-save."""
    include_check = widgets.Checkbox(
        value=False, description="✅ Keep", style={"description_width": "initial"}
    )
    exclude_check = widgets.Checkbox(
        value=False, description="❌ Discard", style={"description_width": "initial"}
    )
    uncertain_check = widgets.Checkbox(
        value=False, description="❓ Uncertain", style={"description_width": "initial"}
    )
    notes_text = widgets.Textarea(
        placeholder="Optional notes...",
        layout=widgets.Layout(width="268px", height="50px"),
    )
    auto_advance_check = widgets.Checkbox(value=False, description="Auto-advance")

    # XNAT request button
    xnat_button = widgets.Button(
        description="🚀 Request Cohort in XNAT",
        button_style="success",
        layout=widgets.Layout(width="98%"),
    )
    xnat_output = widgets.Output()

    def on_xnat_request(b):
        xnat_button.description = "⏳ Processing..."
        xnat_button.disabled = True

        import time

        time.sleep(2)  # Fake 2s delay

        with xnat_output:
            xnat_output.clear_output()
            display(
                HTML(
                    """
                <div style='background: #d1fae5; color: #065f46; padding: 8px; border-radius: 4px;
                            font-size: 12px; text-align: center; border: 1px solid #10b981;'>
                    ✅ Cohort request submitted successfully!
                </div>
            """
                )
            )

        xnat_button.description = "🚀 Request Cohort in XNAT"
        xnat_button.disabled = False

    xnat_button.on_click(on_xnat_request)

    def auto_save_annotation():
        """Auto-save current annotation."""
        if not state["filtered_indices"]:
            return

        idx = state["filtered_indices"][state["current_pos"]]

        # Determine included status from checkboxes
        if include_check.value:
            included = True
        elif exclude_check.value:
            included = False
        elif uncertain_check.value:
            included = None
        else:
            # No selection - delete annotation
            if idx in state["annotations"]:
                del state["annotations"][idx]
            state["render_metrics"]()
            return

        # Save annotation
        state["annotations"][idx] = {
            "included": included,
            "notes": notes_text.value,
            "reviewed": True,
        }

        state["render_metrics"]()

        # Auto-advance disabled - users must navigate manually
        # if auto_advance_check.value and state['current_pos'] < len(state['filtered_indices']) - 1:
        #     state['current_pos'] += 1
        #     state['render_report']()

    def on_annotation_change(change):
        """Handle annotation checkbox changes with mutual exclusivity."""
        if not change["new"]:
            auto_save_annotation()
            return

        # Ensure only one checkbox is selected
        if change["owner"] == include_check:
            exclude_check.unobserve_all()
            uncertain_check.unobserve_all()
            exclude_check.value = False
            uncertain_check.value = False
            exclude_check.observe(on_annotation_change, names="value")
            uncertain_check.observe(on_annotation_change, names="value")
        elif change["owner"] == exclude_check:
            include_check.unobserve_all()
            uncertain_check.unobserve_all()
            include_check.value = False
            uncertain_check.value = False
            include_check.observe(on_annotation_change, names="value")
            uncertain_check.observe(on_annotation_change, names="value")
        elif change["owner"] == uncertain_check:
            include_check.unobserve_all()
            exclude_check.unobserve_all()
            include_check.value = False
            exclude_check.value = False
            include_check.observe(on_annotation_change, names="value")
            exclude_check.observe(on_annotation_change, names="value")

        auto_save_annotation()

    include_check.observe(on_annotation_change, names="value")
    exclude_check.observe(on_annotation_change, names="value")
    uncertain_check.observe(on_annotation_change, names="value")
    notes_text.observe(lambda change: auto_save_annotation(), names="value")

    def load_annotation(idx):
        """Load annotation for given index."""
        annotations = state["annotations"]
        df = state["df"]

        if idx in annotations:
            ann = annotations[idx]
            include_check.unobserve_all()
            exclude_check.unobserve_all()
            uncertain_check.unobserve_all()

            include_check.value = ann.get("included") == True
            exclude_check.value = ann.get("included") == False
            uncertain_check.value = ann.get("included") is None and ann.get(
                "reviewed", False
            )
            notes_text.unobserve_all()
            notes_text.value = ann.get("notes", "")
            notes_text.observe(lambda change: auto_save_annotation(), names="value")

            include_check.observe(on_annotation_change, names="value")
            exclude_check.observe(on_annotation_change, names="value")
            uncertain_check.observe(on_annotation_change, names="value")
        else:
            # No manual annotation - pre-check based on automated include/exclude
            row = df.iloc[idx]
            # Use bracket notation to access the column value
            included_in_cohort = (
                row["included_in_cohort"]
                if "included_in_cohort" in df.columns
                else True
            )

            include_check.unobserve_all()
            exclude_check.unobserve_all()
            uncertain_check.unobserve_all()

            # Pre-check based on automated decision
            include_check.value = bool(included_in_cohort)
            exclude_check.value = not bool(included_in_cohort)
            uncertain_check.value = False
            notes_text.unobserve_all()
            notes_text.value = ""
            notes_text.observe(lambda change: auto_save_annotation(), names="value")

            include_check.observe(on_annotation_change, names="value")
            exclude_check.observe(on_annotation_change, names="value")
            uncertain_check.observe(on_annotation_change, names="value")

    # Store functions in state
    state["load_annotation"] = load_annotation
    state["auto_save_annotation"] = auto_save_annotation

    # Initialize annotation display for the first report
    if state["filtered_indices"]:
        idx = state["filtered_indices"][state["current_pos"]]
        load_annotation(idx)

    # Create annotation controls as a vertical sidebar with improved styling
    annotation_sidebar = widgets.VBox(
        [
            widgets.HTML(
                """
            <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        color: white; padding: 12px 16px; margin: -16px -16px 16px -16px;
                        border-radius: 6px 6px 0 0; font-weight: 600; font-size: 16px;'>
                📋 Review Decision
            </div>
        """
            ),
            include_check,
            exclude_check,
            uncertain_check,
            widgets.HTML("<div style='height: 20px;'></div>"),
            widgets.HTML(
                """
            <div style='font-weight: 600; font-size: 14px; margin-bottom: 8px;
                        color: #374151; padding-bottom: 6px; border-bottom: 1px solid #e5e7eb;'>
                📝 Notes
            </div>
        """
            ),
            notes_text,
            widgets.HTML("<div style='height: 16px;'></div>"),
            widgets.HTML(
                "<div style='border-top: 1px solid #e5e7eb; padding-top: 12px; margin-top: 4px;'></div>"
            ),
            auto_advance_check,
            widgets.HTML("<div style='height: 16px;'></div>"),
            widgets.HTML(
                """
            <div style='font-weight: 600; font-size: 14px; margin-bottom: 8px;
                        color: #374151; padding-bottom: 6px; border-bottom: 1px solid #e5e7eb;'>
                📤 Export
            </div>
        """
            ),
            export_widgets["include_text_check"],
            export_widgets["export_button"],
            export_widgets["export_output"],
        ],
        layout=widgets.Layout(
            width="300px",
            min_width="300px",
            max_width="300px",
            padding="16px",
            background="white",
            border_radius="8px",
            border="2px solid #667eea",
            box_shadow="0 2px 8px rgba(102, 126, 234, 0.15)",
            overflow="hidden",
        ),
    )

    return {
        "container": annotation_sidebar,
        "include_check": include_check,
        "exclude_check": exclude_check,
        "uncertain_check": uncertain_check,
        "notes_text": notes_text,
        "auto_advance_check": auto_advance_check,
        "xnat_button": xnat_button,
        "xnat_output": xnat_output,
    }


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
        description="📥 Export Cohort CSV",
        button_style="info",
        layout=widgets.Layout(width="auto"),
    )

    export_output = widgets.Output()

    def on_export(b):
        with export_output:
            export_output.clear_output(wait=True)

            try:
                filepath = export_cohort(
                    state["df"], state["annotations"], include_report_text_check.value
                )

                annotated_count = sum(
                    1 for a in state["annotations"].values() if a.get("reviewed", False)
                )

                display(
                    HTML(
                        f"""
                    <div style='background: {SUCCESS_GRADIENT}; padding: 16px; border-radius: 8px;
                                color: white; margin-top: 12px;'>
                        <div style='font-weight: 600; margin-bottom: 8px;'>✓ Export Successful</div>
                        <div style='font-size: 14px; opacity: 0.95;'>
                            Exported {len(state['df'])} reports ({annotated_count} annotated)<br>
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

    container = widgets.VBox(
        [include_report_text_check, export_button, export_output],
        layout=widgets.Layout(
            padding="10px", background="#f8f9fa", border_radius="6px"
        ),
    )

    return {
        "container": container,
        "include_text_check": include_report_text_check,
        "export_button": export_button,
        "export_output": export_output,
    }
