# Trino Connection Patterns

## Standard Connection

```python
import os
import trino

def _connect_trino():
    """Connect to Trino using environment variables."""
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

## Query Execution Pattern

```python
def execute_query(query):
    """Execute a query and return a DataFrame."""
    conn = _connect_trino()
    cursor = conn.cursor()
    cursor.execute(query)
    columns = [desc[0] for desc in cursor.description]
    data = cursor.fetchall()
    df = pd.DataFrame(data, columns=columns)
    cursor.close()
    conn.close()
    return df
```

## Common Query Patterns

### Date Filtering
```sql
-- Filter by message_dt for recent reports
WHERE message_dt >= TIMESTAMP '{cutoff_date}'

-- Or use results_report_status_change_dt for finalized reports
WHERE COALESCE(results_report_status_change_dt, message_dt) >= TIMESTAMP '{cutoff_date}'
```

### Radiologist Filtering (REQUIRED)
```sql
-- ALWAYS exclude reports with blank radiologist names
WHERE principal_result_interpreter IS NOT NULL
  AND TRIM(principal_result_interpreter) <> ''
```

### Modality Filtering
```sql
WHERE modality = 'CT'
WHERE modality IN ('CT', 'MRI', 'XR')
```

### Aggregations
```sql
-- Count by modality
SELECT modality, COUNT(*) as count
FROM delta.default.reports
GROUP BY modality

-- Count by time period (week)
SELECT
    DATE_TRUNC('week', message_dt) as week,
    COUNT(*) as count
FROM delta.default.reports
GROUP BY DATE_TRUNC('week', message_dt)
```

### TAT Calculations
```sql
-- REQUIRED: Order-to-Report TAT in hours
-- ALWAYS use COALESCE(requested_dt, observation_dt) - without fallback, TAT will be NULL for most reports
CAST(DATE_DIFF('second',
    COALESCE(requested_dt, observation_dt),
    COALESCE(results_report_status_change_dt, message_dt)
) AS DOUBLE) / 3600.0 AS order_to_report_hours
```

### Text Analysis
```sql
-- Check for section presence
CASE WHEN report_section_findings IS NOT NULL
     AND LENGTH(report_section_findings) > 10
     THEN true ELSE false END AS has_findings

-- Report length
LENGTH(report_text) AS report_length
```

## Example: Volume by Modality Query

```python
def _load_volume_data(table_name="default.reports", date_range_days=360):
    """Load volume data by modality."""
    conn = _connect_trino()

    table_parts = table_name.split(".")
    schema = table_parts[0] if len(table_parts) == 2 else "default"
    table = table_parts[1] if len(table_parts) == 2 else table_parts[0]

    cutoff = (datetime.now() - timedelta(days=date_range_days)).strftime("%Y-%m-%d")

    query = f"""
    SELECT
        modality,
        DATE_TRUNC('week', message_dt) as week,
        COUNT(*) as count
    FROM delta.{schema}.{table}
    WHERE message_dt >= TIMESTAMP '{cutoff}'
      AND modality IS NOT NULL
    GROUP BY modality, DATE_TRUNC('week', message_dt)
    ORDER BY week, modality
    """

    cursor = conn.cursor()
    cursor.execute(query)
    columns = [desc[0] for desc in cursor.description]
    data = cursor.fetchall()
    df = pd.DataFrame(data, columns=columns)
    cursor.close()
    conn.close()

    return df
```

## Tips

1. **REQUIRED: TAT with fallback** - Always use `COALESCE(requested_dt, observation_dt)` for TAT start time. Without this, TAT will be NULL for most reports.
2. **REQUIRED: Filter blank radiologists** - Always exclude NULL/empty `principal_result_interpreter` in WHERE clause.
3. **Filter outliers** - TAT values can have extreme outliers (filter > 720 hours)
4. **Use DATE_TRUNC for time series** - Aggregating by week/month is common
5. **Limit results for testing** - Use LIMIT during development
6. **Close connections** - Always close cursor and connection after use
