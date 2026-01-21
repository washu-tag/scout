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
   - Patient-level timeline view

5. **Grouping dimensions** - How should data be broken down?
   - By modality
   - By facility/location
   - By radiologist
   - By time period (day/week/month)
   - By patient demographics

6. **Interactive features** - What user interactions are needed? (multi-select)
   - Patient lookup by MRN/accession
   - Filter by radiologist or facility
   - Adjustable date range
   - Drill-down from summary to detail
   - Export to CSV

**Final question set - Appearance:**

7. **Launchpad appearance** - Icon and color for the playbook card
   - Icons: `users`, `chart`, `sparkles`, `clipboard`, `document`, `beaker`
   - Colors: `violet`, `rose`, `cyan`, `emerald`, `amber` (recommended), `blue`, `indigo`, `pink`

### Step 2: Create Playbook Files

After gathering requirements, create a new playbook directory in `analytics/notebooks/{playbook-id}/` with:

1. **Main notebook** (`{PlaybookName}.ipynb`) - Single cell that imports and calls the dashboard module
2. **Dashboard module** (`{module_name}.py`) - Python module following the Quality Metrics pattern

Use the templates in this skill directory:
- `notebook-template.md` - Notebook structure pattern
- `trino-pattern.md` - Trino connection and query patterns
- `scout-context.md` - Scout data schema reference

### Step 3: Publish
Run the publish script to deploy to the cluster:

```bash
./scripts/scout-publish.sh analytics/notebooks/{playbook-id} \
  --title "Title" \
  --description "Description" \
  --icon "icon" \
  --color "color"
```

## Key Patterns

### Notebook Structure
```python
from {module_name} import create_landing_page

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
    """Load data from Trino."""
    # Query implementation

def create_dashboard(table_name, date_range_days):
    """Main dashboard with progressive rendering."""
    # Display header
    # Create status output
    # Load data
    # Render sections progressively

def create_landing_page(table_name, date_range_days):
    """Voila-optimized landing page with Launch button."""
    # Landing card with features list
    # Date range input
    # Launch button that calls create_dashboard()
```

### Important Principles
1. **Trino for data access** - Not PySpark, it's faster for Voila
2. **Landing page pattern** - "Launch Dashboard" button for instant page load
3. **Progressive rendering** - Sections load one at a time with status indicators
4. **HTML/CSS styled cards** - For summary statistics
5. **matplotlib/seaborn** - For visualizations

## Example Usage

```
User: Create a CT utilization dashboard showing scan volumes over time

Claude:
1. FIRST asks clarifying questions:
   - "What time range should be the default?" (30 days, 90 days, 1 year)
   - "What chart types do you want?" (trend lines, summary cards, bar charts, tables)
   - "What grouping dimensions?" (by facility, by scanner, by exam type)
   - "What interactive features?" (date picker, facility filter, export)
   - "What icon and color for Launchpad?" (chart icon, amber color)

2. After user answers, creates analytics/notebooks/ct-utilization/
   - CTUtilization.ipynb
   - ct_utilization_dashboard.py

3. Runs ./scripts/scout-publish.sh to deploy
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

## Available Colors
- `violet` - Purple tones
- `rose` - Pink/rose tones
- `cyan` - Blue-green tones
- `emerald` - Green tones
- `amber` - Yellow/orange tones, recommended
- `blue` - Blue tones
- `indigo` - Deep blue/purple
- `pink` - Bright pink
