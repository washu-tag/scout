---
name: create-scout-playbook
description: Create and publish interactive Scout playbook dashboards that appear on the Launchpad. Use this when the user wants to create a new analytics dashboard, visualization, or playbook for Scout.
argument-hint: "[description of the dashboard to create]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion
---

# /create-scout-playbook

Create and publish interactive Scout playbook dashboards that appear on the Launchpad.

## Workflow

### Step 1: Gather Requirements (BEFORE creating any files)

**IMPORTANT:** Always ask clarifying questions FIRST using AskUserQuestion before writing any code. Do not skip this step even if the user provides a description.

Use AskUserQuestion to ask about (combine into 1-2 calls of up to 4 questions each):

**First question set - Data & Purpose:**

1. **Data scope** - Which modalities or study types should be included?
   - All modalities
   - Specific modality (CT, MRI, XR, US, NM, etc.)
   - Specific body region (brain, chest, abdomen, MSK)
   - Studies matching specific criteria (e.g., contains certain text)

2. **Primary use case** - What's the main workflow this supports?
   - Operational reporting (volumes, TAT, utilization)
   - Clinical review (patient timelines, case finding)
   - Quality assurance (report completeness, compliance)
   - Research/exploration (cohort building, trend analysis)

3. **Time range default** - What's the appropriate lookback period?
   - 30 days (recent activity)
   - 90 days (quarterly view)
   - 1 year (annual trends)
   - 2+ years (longitudinal tracking)

**Second question set - Visualizations & Features:**

4. **Chart types** - What visualizations do you want? (multi-select)
   - Time series / trend lines (volume over time)
   - Summary cards with key metrics
   - Bar charts (comparisons by category)
   - Pie/donut charts (distribution breakdown)
   - Data tables with sorting/filtering
   - Scatter plots (performance comparisons)

5. **Grouping dimensions** - How should data be broken down?
   - By modality
   - By facility/location
   - By radiologist
   - By time period (day/week/month)
   - By patient demographics

6. **Launchpad appearance** - Icon for the playbook card on Launchpad
   - Icons: `users`, `chart`, `sparkles`, `clipboard`, `document`, `beaker`

### Step 2: Create Playbook Files

After gathering requirements, create a new playbook directory in `analytics/notebooks/{playbook-id}/` with:

1. **Main notebook** (`{PlaybookName}.ipynb`) - Single cell that imports and calls the dashboard module
2. **Dashboard module** (`{module_name}.py`) - Python module following the canonical pattern

**IMPORTANT:** Read `sample_dashboard.py` in this skill directory as the canonical reference for code patterns before creating new dashboards.

References in this skill directory:
- `sample_dashboard.py` - **Canonical code template** showing all required patterns (Trino connection, data loading, progressive rendering, landing page, etc.). Use this as your structural reference regardless of what type of dashboard you're building.
- `notebook-template.md` - Key patterns and code snippets
- `trino-pattern.md` - Trino connection and query patterns
- `scout-context.md` - Scout data schema reference

### Step 3: Publish

Run the publish script to deploy to the cluster:

```bash
./scripts/scout-publish.sh analytics/notebooks/{playbook-id} \
  --title "Title" \
  --description "Description" \
  --icon "icon" \
  --color "amber"
```

## Key Patterns

### Canonical Reference
Always read `sample_dashboard.py` in this skill directory before creating a new dashboard. This file demonstrates all required code patterns (Trino connection, data loading, progressive section rendering, landing page with launch button, styling conventions). Adapt the structure and patterns to your specific dashboard requirements.

### Notebook Structure
```python
from {module_name} import create_landing_page

# Launch dashboard with instant page load
# Data loads only when "Launch Dashboard" button is clicked
create_landing_page(
    table_name="default.reports",
    date_range_days=360
)
```

### Dashboard Module Structure
```python
"""
{Title} Dashboard for Radiology Reports (Voila-optimized).
"""
import os
import pandas as pd
import ipywidgets as widgets
from IPython.display import display, HTML
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, timedelta

def _connect_trino():
    """Connect to Trino using environment variables."""
    # See trino-pattern.md

def _load_data(table_name, date_range_days):
    """Load data from Trino with derived fields."""
    # Single query with all needed fields
    # Derive time dimensions in Python (report_date, report_week)
    # Filter outliers in Python
    # Store date_range_days in df.attrs

def create_dashboard(table_name, date_range_days):
    """Main dashboard with progressive rendering."""
    # Display header (always #667eea purple)
    # Create status output for loading
    # Load data
    # Create output widgets for each section
    # Render sections progressively with loading indicators

def create_landing_page(table_name, date_range_days):
    """Voila-optimized landing page with Launch button."""
    # Simple landing card with date range input only
    # Features list describing the dashboard
    # Launch button that calls create_dashboard()
```

### Important Principles
1. **Trino for data access** - Not PySpark, it's faster for Voila
2. **Landing page pattern** - Simple landing with just date range input (no extra filters)
3. **Progressive rendering** - Show loading indicator, then clear and render each section
4. **Consistent styling** - Always use `#667eea` purple for headers, borders, buttons, accents
5. **Pandas display for tables** - Use `display(df.round(1))` not custom HTML tables
6. **Adaptive time aggregation** - Daily for <60 days, weekly for longer ranges
7. **matplotlib/seaborn** - For all visualizations with `#667eea` and `#764ba2` colors
8. **Exclude blank radiologists** - Always filter out reports where radiologist is NULL or empty
9. **TAT calculation with fallback** - ALWAYS use `COALESCE(requested_dt, observation_dt)` for TAT start time. Without this fallback, TAT will be NULL for most reports since `requested_dt` is often missing. This is REQUIRED, not optional.

### Required SQL Patterns (DO NOT DEVIATE)

**TAT Calculation:**
```sql
-- REQUIRED: Always use COALESCE with fallback for TAT start time
CAST(DATE_DIFF('second',
    COALESCE(requested_dt, observation_dt),  -- MUST have fallback
    COALESCE(results_report_status_change_dt, message_dt)
) AS DOUBLE) / 3600.0 AS order_to_report_hours
```

**Radiologist Filter:**
```sql
-- REQUIRED: Always exclude blank radiologists in WHERE clause
WHERE principal_result_interpreter IS NOT NULL
  AND TRIM(principal_result_interpreter) <> ''
```

### Dashboard Sections Pattern
Typical dashboard has these sections (adapt based on requirements):
1. **Summary** - Key metrics in purple gradient card with date range in title
2. **Breakdown by Category** - Bar charts + pandas data table
3. **Trends Over Time** - Line charts with adaptive daily/weekly aggregation
4. **Performance Comparison** - Scatter plots or grouped bar charts
5. **Quality Indicators** - Green gradient card with completeness metrics

## Example Usage

```
User: Create a CT utilization dashboard showing scan volumes over time

Claude:
1. FIRST asks clarifying questions:
   - "What modalities to include?" (All, specific)
   - "What time range default?" (30 days, 90 days, 1 year)
   - "What chart types?" (trend lines, summary cards, bar charts, tables)
   - "What grouping dimensions?" (by facility, by scanner, by exam type)
   - "What icon for Launchpad?" (chart, clipboard, etc.)

2. Reads sample_dashboard.py for code patterns and structure

3. Creates analytics/notebooks/ct-utilization/
   - CTUtilization.ipynb
   - ct_utilization_dashboard.py

4. Runs ./scripts/scout-publish.sh to deploy
```

## Files Created

For a playbook with ID `my-dashboard`:
```
analytics/notebooks/my-dashboard/
├── MyDashboard.ipynb           # Notebook entry point
└── my_dashboard.py             # Dashboard module
```

## Available Icons
- `users` - User/people related dashboards
- `chart` - Charts/graphs/analytics
- `sparkles` - AI/ML or special features
- `clipboard` - Quality/compliance metrics
- `document` - Documentation/reports
- `beaker` - Research/experimental

## Styling
All dashboards use consistent purple styling (`#667eea`) for the dashboard itself. The Launchpad card color defaults to amber but can be customized in the publish command.
