"""
Scout Cohort Builder - Main Dashboard Module

This module provides the main entry point for the cohort builder dashboard.
All UI logic is contained here - the notebook just calls launch_cohort_builder().
"""

import time
import traceback
import pandas as pd
import ipywidgets as widgets
from IPython.display import display, HTML
import requests
import json

from cohort_builder import (
    load_cohort_data,
    PRIMARY_GRADIENT,
    SUCCESS_GRADIENT,
    PURPLE_PRIMARY,
    GREEN_SUCCESS,
    ORANGE_WARNING,
    RED_ERROR,
)

from cohort_ui import (
    create_filter_controls,
    create_metrics_display,
    create_navigation_controls,
    create_annotation_controls,
    create_export_controls,
)


def generate_regex_fake(user_query, return_prompt=False):
    """
    Fake regex pattern generator for testing (returns brain mets patterns after 1s delay).

    Args:
        user_query: Natural language description (ignored in fake version)
        return_prompt: If True, return what the prompt would be

    Returns:
        Generated regex patterns for brain mets matching
    """
    prompt = f"[FAKE] Would generate patterns for: {user_query}"

    if return_prompt:
        return prompt

    # Simulate API delay
    time.sleep(1)

    # Return brain mets-matching regex patterns
    return "\n".join(
        [
            r"(?:metasta(?:sis|ses|tic)?|mets).{0,50}(?:brain|cerebr(?:al|um)|intracranial)",
            r"(?:brain|cerebr(?:al|um)|intracranial).{0,50}(?:metasta(?:sis|ses|tic)?|mets)",
            r"(?:brain|cerebral|intracranial).{0,30}(?:lesion|mass|tumor).{0,30}(?:metasta|secondary)",
        ]
    )


# def generate_regex_with_ollama(
#     user_query,
#     ollama_url="http://ollama.chatbot:11434",
#     model="gpt-oss-120b-long:latest",
#     return_prompt=False,
# ):
#     """
#     Generate regex patterns for radiology report text search using Ollama.
#
#     Args:
#         user_query: Natural language description of what to search for (e.g., "brain mets")
#         ollama_url: Ollama API endpoint
#         model: Model name to use
#         return_prompt: If True, return the prompt instead of calling Ollama
#
#     Returns:
#         Generated regex patterns (one per line) or error message, or prompt if return_prompt=True
#     """
#     prompt = f"""Generate regex patterns to search radiology reports for: {user_query}
#
# Requirements:
# - Generate 2-4 regex patterns
# - Use case-insensitive matching ((?i) flag assumed)
# - Use flexible spacing: .{{0,50}} to allow words within 50 characters
# - Use non-capturing groups: (?:word1|word2|word3)
# - Include medical terminology variations and synonyms
# - Consider both forward and reverse word orders
#
# Examples:
# For "brain mets":
# - Pattern 1: (?:metasta(?:sis|ses|tic)?|mets).{{0,50}}(?:brain|cerebr(?:al|um)|intracranial)
# - Pattern 2: (?:brain|cerebr(?:al|um)|intracranial).{{0,50}}(?:metasta(?:sis|ses|tic)?|mets)
#
# Return ONLY valid JSON, nothing else:
# {{"patterns": ["pattern1", "pattern2", "pattern3"]}}
#
# Search term: {user_query}
#
# JSON:"""
#
#     if return_prompt:
#         return prompt
#
#     try:
#         response = requests.post(
#             f"{ollama_url}/api/generate",
#             json={
#                 "model": model,
#                 "prompt": prompt,
#                 "temperature": 0,
#                 "stream": False,
#                 "thinking": "low",
#             },
#             timeout=60,
#         )
#         response.raise_for_status()
#         result = response.json()
#
#         # Extract generated text
#         resp_text = result["response"].strip()
#
#         # Check if response is empty
#         if not resp_text:
#             return f"Error: Empty response from model. Available fields: {list(result.keys())}"
#
#         # Extract JSON object, ignoring any text before or after
#
#         # Remove code block fencing if present
#         if resp_text.startswith("```json"):
#             resp_text = resp_text[7:]  # Remove ```json
#         if resp_text.startswith("```"):
#             resp_text = resp_text[3:]  # Remove ```
#
#         # Remove closing fence
#         if "```" in resp_text:
#             resp_text = resp_text.split("```")[0]
#
#         resp_text = resp_text.strip()
#
#         # Check again after cleanup
#         if not resp_text:
#             return "Error: Empty response after cleanup"
#
#         # Find first JSON object (handles text before/after)
#         import re
#
#         json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", resp_text)
#         if json_match:
#             json_str = json_match.group()
#         else:
#             # Fallback: try to parse the whole thing
#             json_str = resp_text.strip()
#
#         # Final check before parsing
#         if not json_str or not json_str.strip():
#             return f"Error: No JSON found in response: {resp_text[:300]}"
#
#         # Parse JSON
#         try:
#             obj = json.loads(json_str)
#
#             if not isinstance(obj, dict):
#                 return "Error: Response is not a JSON object"
#
#             patterns = obj.get("patterns", [])
#
#             if not patterns:
#                 return f"Error: No patterns in response. Got: {str(obj)[:200]}"
#
#             # Ensure patterns are strings and join with newlines
#             # Each pattern should be on its own line
#             # Also replace any escaped newlines (\n as text) with actual newlines
#             pattern_strings = []
#             for p in patterns:
#                 if p:
#                     p_str = str(p)
#                     # Replace literal \n (backslash-n) with actual newline if present
#                     p_str = p_str.replace("\\n", "\n")
#                     pattern_strings.append(p_str)
#
#             return "\n".join(pattern_strings)
#
#         except json.JSONDecodeError as je:
#             return f"Error: Could not parse JSON. Response: {resp_text[:300]}"
#
#     except requests.exceptions.RequestException as e:
#         return f"Error connecting to Ollama: {str(e)}"
#     except Exception as e:
#         return f"Error generating regex: {str(e)}"


def _create_search_form(container, config=None):
    """
    Create the search form with optional pre-populated values.

    Args:
        container: Widget container to render the form into
        config: Optional configuration dictionary to pre-populate form values
    """
    # Use empty config if none provided
    if config is None:
        config = {}

    # Header - more compact
    header_html = widgets.HTML(
        """
        <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    padding: 20px; text-align: center; color: white; border-radius: 12px 12px 0 0;'>
            <h1 style='margin: 0 0 6px 0; font-size: 28px; font-weight: 700;'>
                Scout Cohort Builder
            </h1>
            <p style='margin: 0; font-size: 14px; opacity: 0.9;'>
                Build custom patient cohorts with flexible filtering and manual review
            </p>
        </div>
    """
    )

    # Features list - redesigned
    features_html = widgets.HTML(
        """
        <div style='display: flex; justify-content: center; gap: 24px; margin-bottom: 24px; flex-wrap: wrap;'>
            <div style='display: flex; align-items: center; gap: 8px; padding: 8px 16px; background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
                        border-radius: 20px; border: 1px solid #bae6fd;'>
                <span style='font-size: 18px;'>🔍</span>
                <span style='font-size: 13px; font-weight: 500; color: #0c4a6e;'>ICD-10 & Text Search</span>
            </div>
            <div style='display: flex; align-items: center; gap: 8px; padding: 8px 16px; background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%);
                        border-radius: 20px; border: 1px solid #bbf7d0;'>
                <span style='font-size: 18px;'>🎯</span>
                <span style='font-size: 13px; font-weight: 500; color: #14532d;'>Negation Filtering</span>
            </div>
            <div style='display: flex; align-items: center; gap: 8px; padding: 8px 16px; background: linear-gradient(135deg, #faf5ff 0%, #f3e8ff 100%);
                        border-radius: 20px; border: 1px solid #e9d5ff;'>
                <span style='font-size: 18px;'>✏️</span>
                <span style='font-size: 13px; font-weight: 500; color: #581c87;'>Manual Review</span>
            </div>
            <div style='display: flex; align-items: center; gap: 8px; padding: 8px 16px; background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
                        border-radius: 20px; border: 1px solid #fcd34d;'>
                <span style='font-size: 18px;'>📊</span>
                <span style='font-size: 13px; font-weight: 500; color: #78350f;'>CSV Export</span>
            </div>
        </div>
    """
    )

    # Input widgets - populate from config if provided
    diagnosis_codes_input = widgets.Textarea(
        value=config.get("diagnosis_codes", ""),
        placeholder="e.g., C79.31, C79.32",
        description="ICD-10 Codes:",
        layout=widgets.Layout(width="98%", height="60px"),
        style={"description_width": "100px"},
    )

    diagnosis_text_input = widgets.Text(
        value=config.get("diagnosis_text", ""),
        placeholder="e.g., brain metastases",
        description="ICD-10 Text:",
        layout=widgets.Layout(width="98%"),
        style={"description_width": "100px"},
    )

    # AI regex generation input and button
    ai_query_input = widgets.Text(
        placeholder="e.g., brain mets, pulmonary nodules",
        description="Describe finding:",
        layout=widgets.Layout(width="70%"),
        style={"description_width": "140px"},
    )

    generate_regex_button = widgets.Button(
        description="✨ Generate Patterns",
        button_style="info",
        tooltip="Use AI to generate search patterns from natural language",
        layout=widgets.Layout(width="28%"),
    )

    ai_status_output = widgets.Output(
        layout=widgets.Layout(width="98%", max_height="60px")
    )

    # Debug toggle
    show_debug = widgets.Checkbox(
        value=False, description="Show debug info", layout=widgets.Layout(width="auto")
    )

    debug_output = widgets.Output(
        layout=widgets.Layout(width="98%", max_height="200px", overflow="auto")
    )

    report_text_input = widgets.Textarea(
        value=config.get("report_text_terms", ""),
        placeholder="Enter search terms or regex patterns (one per line)\ne.g., metasta(?:sis|ses|tic).{0,50}brain",
        description="Text to match:",
        layout=widgets.Layout(width="98%", height="200px"),
        style={"description_width": "100px"},
    )

    # AI regex generation handler
    def on_generate_regex(b):
        query = ai_query_input.value.strip()
        if not query:
            with ai_status_output:
                ai_status_output.clear_output()
                display(
                    HTML(
                        "<div style='color: #dc2626; font-size: 12px; padding: 4px;'>⚠️ Please enter a query</div>"
                    )
                )
            return

        # Show loading state
        generate_regex_button.description = "⏳ Generating..."
        generate_regex_button.disabled = True

        with ai_status_output:
            ai_status_output.clear_output()
            display(
                HTML(
                    "<div style='color: #667eea; font-size: 12px; padding: 4px;'>⏳ Generating search patterns...</div>"
                )
            )

        # Show debug info if enabled
        if show_debug.value:
            import html as html_module

            # Get the actual prompt that will be sent
            prompt_text = generate_regex_fake(query, return_prompt=True)
            prompt_escaped = html_module.escape(prompt_text)

            with debug_output:
                debug_output.clear_output()
                display(
                    HTML(
                        f"""
                    <div style='background: #f3f4f6; padding: 8px; border-radius: 4px; font-size: 11px; font-family: monospace;'>
                        <div style='font-weight: 600; margin-bottom: 4px;'>Request Details:</div>
                        <div><b>Mode:</b> FAKE (returns brain mets patterns after 1s delay)</div>
                        <div><b>Query:</b> {query}</div>
                        <div style='margin-top: 8px;'><b>Prompt:</b></div>
                        <pre style='background: white; padding: 4px; border-radius: 2px; overflow-x: auto; max-height: 200px; font-size: 10px;'>{prompt_escaped}</pre>
                    </div>
                """
                    )
                )

        try:
            # Generate regex using fake function (Ollama call commented out)
            result = generate_regex_fake(query)

            # Show full response in debug if enabled
            if show_debug.value:
                with debug_output:
                    display(
                        HTML(
                            f"""
                        <div style='background: #fef3c7; padding: 8px; border-radius: 4px; font-size: 11px; font-family: monospace; margin-top: 8px;'>
                            <div style='font-weight: 600; margin-bottom: 4px;'>Response:</div>
                            <pre style='margin: 0; white-space: pre-wrap; word-wrap: break-word;'>{result[:500]}</pre>
                        </div>
                    """
                        )
                    )

            with ai_status_output:
                ai_status_output.clear_output()

                # Check if it's an error message
                if result.startswith("Error"):
                    display(
                        HTML(
                            f"<div style='color: #dc2626; font-size: 12px; padding: 4px;'>❌ {result}</div>"
                        )
                    )
                else:
                    # Update report text input with generated patterns
                    report_text_input.value = result

                    # Show simple success message
                    num_patterns = len(result.split("\n"))
                    display(
                        HTML(
                            f"<div style='color: #10b981; font-size: 12px; padding: 4px;'>✅ Generated {num_patterns} search patterns</div>"
                        )
                    )

        except Exception as e:
            with ai_status_output:
                ai_status_output.clear_output()
                display(
                    HTML(
                        f"<div style='color: #dc2626; font-size: 12px; padding: 4px;'>❌ Error: {str(e)}</div>"
                    )
                )

        finally:
            generate_regex_button.description = "✨Generate Patterns"
            generate_regex_button.disabled = False

    generate_regex_button.on_click(on_generate_regex)

    modality_select = widgets.SelectMultiple(
        value=tuple(config.get("modalities", [])),
        options=["CT", "MR", "US", "XR", "NM", "PT", "RF", "MG"],
        description="Modality:",
        layout=widgets.Layout(width="98%", height="120px"),
        style={"description_width": "100px"},
    )

    service_name_input = widgets.Text(
        value=config.get("service_name_pattern", ""),
        placeholder="e.g., brain, chest, abdomen",
        description="Exam Description:",
        layout=widgets.Layout(width="98%"),
        style={"description_width": "100px"},
    )

    facility_select = widgets.SelectMultiple(
        value=tuple(config.get("facilities", [])),
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

    min_age_input = widgets.IntText(
        value=config.get("min_age") if config.get("min_age") is not None else 18,
        description="Min Age:",
        layout=widgets.Layout(width="48%"),
        style={"description_width": "80px"},
    )

    max_age_input = widgets.IntText(
        value=config.get("max_age") if config.get("max_age") is not None else 89,
        description="Max Age:",
        layout=widgets.Layout(width="48%"),
        style={"description_width": "80px"},
    )

    date_range_select = widgets.Dropdown(
        options=[
            ("All dates", "all"),
            ("Last 30 days", "30"),
            ("Last 90 days", "90"),
            ("Last 180 days", "180"),
            ("Last 365 days", "365"),
        ],
        value=config.get("date_range_days", "all"),
        description="Date Range:",
        layout=widgets.Layout(width="98%"),
        style={"description_width": "100px"},
    )

    sample_limit_input = widgets.IntText(
        value=(
            config.get("sample_limit")
            if config.get("sample_limit") is not None
            else 100
        ),
        placeholder="Leave empty for no limit",
        description="Sample Limit:",
        layout=widgets.Layout(width="98%"),
        style={"description_width": "100px"},
    )

    apply_negation_filter = widgets.Checkbox(
        value=config.get("apply_negation_filter", True),
        description="Exclude negated matches (recommended, you can still review and manually include)",
        layout=widgets.Layout(width="98%"),
        style={"description_width": "0px"},
    )

    # Form container - two column layout with explicit widths
    left_column = widgets.VBox(
        [
            widgets.HTML(
                "<div style='font-weight: 600; font-size: 15px; margin-bottom: 10px; color: #1f2937; padding-bottom: 6px; border-bottom: 2px solid #e5e7eb;'>Search Criteria</div>"
            ),
            widgets.HTML(
                "<div style='background: #fef3c7; padding: 8px; border-radius: 4px; border-left: 3px solid #f59e0b; margin-bottom: 10px;'><strong style='color: #92400e;'>💪 Auto-include Filters</strong><div style='font-size: 11px; color: #78350f; margin-top: 2px;'>Cases matching these criteria will be in the cohort</div></div>"
            ),
            diagnosis_codes_input,
            diagnosis_text_input,
            widgets.HTML("<div style='height: 16px;'></div>"),
            widgets.HTML(
                "<div style='background: #dbeafe; padding: 8px; border-radius: 4px; border-left: 3px solid #3b82f6; margin-bottom: 10px;'><strong style='color: #1e3a8a;'>📝 Search Terms</strong><div style='font-size: 11px; color: #1e40af; margin-top: 2px;'>Negated matches can be excluded</div></div>"
            ),
            report_text_input,
            widgets.HTML(
                "<div style='font-size: 11px; color: #6b7280; font-style: italic; margin: 8px 0 4px 0;'>💡 Optional: Use AI to generate search patterns from natural language</div>"
            ),
            widgets.HBox(
                [ai_query_input, generate_regex_button],
                layout=widgets.Layout(width="100%", justify_content="space-between"),
            ),
            ai_status_output,
            widgets.HTML(
                "<details style='margin: 4px 0;'><summary style='font-size: 11px; color: #6b7280; cursor: pointer;'>Debug info</summary><div id='debug-content'></div></details>"
            ),
            debug_output,
            widgets.HTML("<div style='height: 12px;'></div>"),
            widgets.HTML(
                "<div style='font-weight: 600; font-size: 15px; margin-bottom: 10px; color: #1f2937; padding-bottom: 6px; border-bottom: 2px solid #e5e7eb;'>Isolate Positive Findings</div>"
            ),
            apply_negation_filter,
        ],
        layout=widgets.Layout(width="45%"),
    )

    # Fake upload button for patient identifiers
    upload_ids_button = widgets.Button(
        description="📤 Upload Patient IDs CSV",
        button_style="info",
        tooltip="Upload a CSV file with patient identifiers to filter",
        layout=widgets.Layout(width="50%"),
    )

    right_column = widgets.VBox(
        [
            widgets.HTML(
                "<div style='font-weight: 600; font-size: 15px; margin-bottom: 10px; color: #1f2937; padding-bottom: 6px; border-bottom: 2px solid #e5e7eb;'>Filters</div>"
            ),
            widgets.HTML(
                "<div style='font-weight: 600; font-size: 13px; margin: 12px 0 8px 0; color: #374151;'>Exam Filters</div>"
            ),
            modality_select,
            service_name_input,
            facility_select,
            widgets.HTML(
                """
            <div style='font-size: 12px; color: #6b7280; margin-top: 4px; font-style: italic;'>
                💡 No selection = all facilities
            </div>
        """
            ),
            date_range_select,
            widgets.HTML("<div style='height: 16px;'></div>"),
            widgets.HTML(
                "<div style='font-weight: 600; font-size: 13px; margin-bottom: 8px; color: #374151;'>Demographic Filters</div>"
            ),
            widgets.HBox(
                [min_age_input, max_age_input],
                layout=widgets.Layout(width="100%", justify_content="space-between"),
            ),
            widgets.HTML("<div style='height: 20px;'></div>"),
            widgets.HTML(
                "<div style='font-weight: 600; font-size: 15px; margin-bottom: 10px; color: #1f2937; padding-bottom: 6px; border-bottom: 2px solid #e5e7eb;'>Options</div>"
            ),
            sample_limit_input,
            widgets.HTML("<div style='height: 20px;'></div>"),
            widgets.HTML(
                "<div style='font-weight: 600; font-size: 15px; margin-bottom: 10px; color: #1f2937; padding-bottom: 6px; border-bottom: 2px solid #e5e7eb;'>Patient List <span style='font-weight: 400; font-style: italic; color: #6b7280;'>(optional)</span></div>"
            ),
            upload_ids_button,
        ],
        layout=widgets.Layout(width="45%"),
    )

    form_container = widgets.HBox(
        [left_column, right_column],
        layout=widgets.Layout(padding="20px 24px", justify_content="space-between"),
    )

    # Build button
    build_button = widgets.Button(
        description="🔍 Build Cohort",
        button_style="success",
        layout=widgets.Layout(width="98%", height="50px"),
    )

    # Button click handler
    def on_build_click(b):
        # Gather configuration
        new_config = {
            "diagnosis_codes": diagnosis_codes_input.value,
            "diagnosis_text": diagnosis_text_input.value,
            "report_text_terms": report_text_input.value,
            "modalities": list(modality_select.value),
            "service_name_pattern": service_name_input.value,
            "facilities": list(facility_select.value),
            "min_age": min_age_input.value if min_age_input.value else None,
            "max_age": max_age_input.value if max_age_input.value else None,
            "date_range_days": date_range_select.value,
            "sample_limit": (
                sample_limit_input.value if sample_limit_input.value else None
            ),
            "apply_negation_filter": apply_negation_filter.value,
        }

        # Clear and show dashboard
        container.children = []
        _create_review_dashboard(new_config, container)

    build_button.on_click(on_build_click)

    # Build the landing page layout
    landing_page = widgets.VBox(
        [
            header_html,
            widgets.VBox(
                [features_html, form_container, build_button],
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

    # Update container with the landing page
    container.children = [landing_page]


def launch_cohort_builder():
    """
    Main entry point for the cohort builder dashboard.
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

    # Create main container that will hold either landing page or dashboard
    main_container = widgets.VBox([])

    # Create and display the search form
    _create_search_form(main_container, config=None)

    # Display the container
    display(main_container)


def _create_review_dashboard(config, container):
    """
    Create the review dashboard with annotation interface.

    Args:
        config: Configuration dictionary from landing page
        container: Widget container to render into
    """
    # Show loading status
    status_output = widgets.Output()
    container.children = [status_output]

    # Build SQL and load data directly
    from cohort_builder import build_cohort_query

    sql, criteria_summary = build_cohort_query(config)

    try:
        # Load data
        df, _ = load_cohort_data(config, status_output, approval_callback=None)

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
                        <div style='font-size: 18px; font-weight: 600;'>No Results Found</div>
                        <div style='font-size: 14px; margin-top: 8px; opacity: 0.9;'>Try adjusting your search criteria</div>
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
        _build_dashboard_ui(df, criteria_summary, config, sql, container, status_output)

    except Exception as e:
        # Display SQL on error
        sql_display = sql.strip()
        if len(sql_display) > 2000:
            sql_display = sql_display[:2000] + "\n...(truncated)"

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
                    <h3 style='margin: 0 0 12px 0; color: {RED_ERROR};'>Generated SQL Query</h3>
                    <div style='background: #1e1e1e; color: #d4d4d4; padding: 16px; border-radius: 6px;
                                overflow-x: auto; font-family: monospace; font-size: 13px; line-height: 1.5;'>
                        <pre style='margin: 0; white-space: pre-wrap;'>{sql_display}</pre>
                    </div>
                </div>
            """
                )
            )


def _build_dashboard_ui(df, criteria_summary, config, sql, container, status_output):
    """
    Build the dashboard UI after data is loaded.

    Args:
        df: Loaded DataFrame
        criteria_summary: List of applied criteria
        config: Configuration dictionary
        sql: The SQL query that was executed
        container: Widget container
        status_output: Output widget for status messages
    """
    # Clear status after brief delay
    time.sleep(1.5)

    try:
        # State management
        state = {
            "df": df,
            "criteria_summary": criteria_summary,
            "config": config,
            "annotations": {},  # idx -> {included: bool/None, notes: str, reviewed: bool}
            "filtered_indices": list(range(len(df))),
            "current_pos": 0,
        }

        # Summary section - compact header showing only included count
        unique_patients = (
            df[["epic_mrn", "empi_mr"]]
            .apply(
                lambda row: (
                    row["epic_mrn"] if pd.notna(row["epic_mrn"]) else row["empi_mr"]
                ),
                axis=1,
            )
            .nunique()
        )

        # Get included count
        cohort_included_count = (
            int(df["included_in_cohort"].sum())
            if "included_in_cohort" in df.columns
            else len(df)
        )

        # Create back button
        back_button = widgets.Button(
            description="← Back to Search",
            button_style="",
            layout=widgets.Layout(width="auto", height="auto"),
            style={"button_color": "rgba(255, 255, 255, 0.2)"},
        )

        # Back button handler
        def on_back_click(b):
            # Check if there are any annotations
            has_annotations = any(
                ann.get("reviewed", False) or ann.get("notes", "").strip()
                for ann in state["annotations"].values()
            )

            if has_annotations:
                # Show confirmation message
                confirm_output = widgets.Output()
                confirm_yes = widgets.Button(
                    description="Yes, go back",
                    button_style="warning",
                    layout=widgets.Layout(width="auto"),
                )
                confirm_no = widgets.Button(
                    description="Cancel",
                    button_style="",
                    layout=widgets.Layout(width="auto"),
                )

                def on_confirm_yes(b2):
                    container.children = []
                    _create_search_form(container, state["config"])

                def on_confirm_no(b2):
                    container.children = [dashboard]

                confirm_yes.on_click(on_confirm_yes)
                confirm_no.on_click(on_confirm_no)

                confirmation = widgets.VBox(
                    [
                        widgets.HTML(
                            f"""
                            <div style='background: {ORANGE_WARNING}; padding: 24px; border-radius: 12px;
                                        color: white; margin: 20px auto; text-align: center; max-width: 600px;'>
                                <div style='font-size: 28px; margin-bottom: 8px;'>⚠️</div>
                                <div style='font-size: 18px; font-weight: 600;'>Unsaved Annotations</div>
                                <div style='font-size: 14px; margin-top: 8px; opacity: 0.9;'>
                                    You have reviewed reports. Going back will not save these annotations.
                                    Are you sure you want to continue?
                                </div>
                            </div>
                        """
                        ),
                        widgets.HBox(
                            [confirm_yes, confirm_no],
                            layout=widgets.Layout(justify_content="center", gap="12px"),
                        ),
                        confirm_output,
                    ],
                    layout=widgets.Layout(
                        width="98%", max_width="2000px", margin="0 auto", padding="15px"
                    ),
                )
                container.children = [confirmation]
            else:
                # No annotations, go back immediately
                container.children = []
                _create_search_form(container, state["config"])

        back_button.on_click(on_back_click)

        # Summary section with back button
        summary_html = widgets.HTML(
            f"""
            <div style='background: {PRIMARY_GRADIENT}; padding: 12px 20px; border-radius: 8px;
                        color: white; margin-bottom: 12px;'>
                <span style='font-size: 18px; font-weight: 700;'>Scout Cohort Builder</span>
                <span style='font-size: 14px; opacity: 0.9; margin-left: 16px;'>
                    {cohort_included_count:,} included reports • {unique_patients:,} patients
                </span>
            </div>
        """
        )

        summary_widget = widgets.HBox(
            [back_button, summary_html],
            layout=widgets.Layout(
                justify_content="flex-start", align_items="center", gap="12px"
            ),
        )

        # Create UI components
        filter_widgets = create_filter_controls(state)
        metrics_output = create_metrics_display(state)
        nav_widgets, report_output = create_navigation_controls(state)
        export_widgets = create_export_controls(state)
        annotation_widgets = create_annotation_controls(
            state, nav_widgets, metrics_output, report_output, export_widgets
        )

        # Create "Show SQL" button
        sql_output = widgets.Output()
        show_sql_btn = widgets.Button(
            description="📋 Show SQL Query",
            button_style="info",
            layout=widgets.Layout(width="auto"),
        )

        def on_show_sql(b):
            with sql_output:
                if sql_output.outputs:
                    # Toggle - clear if already showing
                    sql_output.clear_output()
                    show_sql_btn.description = "📋 Show SQL Query"
                else:
                    # Show SQL
                    sql_display = sql.strip()
                    if len(sql_display) > 2000:
                        sql_display = sql_display[:2000] + "\n...(truncated)"

                    display(
                        HTML(
                            f"""
                        <div style='background: linear-gradient(to bottom, #f8f9fa, #ffffff);
                                    padding: 20px; border-radius: 8px;
                                    border: 2px solid {PURPLE_PRIMARY};
                                    margin: 16px 0;
                                    box-shadow: 0 2px 8px rgba(102, 126, 234, 0.1);'>
                            <div style='display: flex; align-items: center; margin-bottom: 12px;'>
                                <span style='font-size: 20px; margin-right: 8px;'>🔍</span>
                                <h4 style='margin: 0; color: {PURPLE_PRIMARY}; font-size: 16px; font-weight: 600;'>
                                    Generated SQL Query
                                </h4>
                            </div>
                            <div style='background: #1e1e1e; color: #d4d4d4;
                                        padding: 16px; border-radius: 6px;
                                        overflow-x: auto;
                                        font-family: "SF Mono", Monaco, "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace;
                                        font-size: 13px; line-height: 1.6;
                                        border: 1px solid #374151;
                                        box-shadow: inset 0 2px 4px rgba(0,0,0,0.3);'>
                                <pre style='margin: 0; white-space: pre-wrap; word-wrap: break-word;'>{sql_display}</pre>
                            </div>
                        </div>
                    """
                        )
                    )
                    show_sql_btn.description = "📋 Hide SQL Query"

        show_sql_btn.on_click(on_show_sql)

        # Create horizontal layout for report and annotation controls
        report_and_annotation = widgets.HBox(
            [report_output, annotation_widgets["container"]],
            layout=widgets.Layout(gap="16px"),
        )

        # Create XNAT request section at bottom
        xnat_section = widgets.VBox(
            [
                widgets.HTML("<div style='height: 24px;'></div>"),
                widgets.HBox(
                    [annotation_widgets["xnat_button"]],
                    layout=widgets.Layout(justify_content="center", margin="12px 0"),
                ),
                annotation_widgets["xnat_output"],
            ]
        )

        # Build dashboard layout
        dashboard = widgets.VBox(
            [
                summary_widget,
                filter_widgets["container"],
                metrics_output,
                show_sql_btn,
                sql_output,
                nav_widgets["nav_bar"],
                report_and_annotation,
                xnat_section,
            ],
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
