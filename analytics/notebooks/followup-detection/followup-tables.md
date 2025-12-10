Create follow-up detection tables with the following command (after reports have been ingested):

```bash
kubectl exec -n scout-extractor deployment/hl7-transformer -it -- /app/venv/bin/python3 << 'EOF'
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("create-followup-tables").enableHiveSupport().getOrCreate()

# Create latest_reports with all data from reports
spark.sql("""
  CREATE TABLE default.latest_reports
  USING delta
  PARTITIONED BY (year)
  AS SELECT 
	  *,
	  CAST(NULL AS BOOLEAN) AS followup_detected,
	  CAST(NULL AS STRING) AS followup_confidence,
	  CAST(NULL AS STRING) AS followup_snippet,
	  CAST(NULL AS STRING) AS followup_finding,
	  CAST(NULL AS TIMESTAMP) AS followup_processed_at
  FROM default.reports
""")

# Create empty followup_errors table
spark.sql("""
  CREATE TABLE default.followup_errors (
	  message_control_id STRING,
	  error STRING,
	  error_timestamp TIMESTAMP
  )
  USING delta
""")

print(f"✅ latest_reports: {spark.table('default.latest_reports').count():,} rows")
print(f"✅ followup_errors: created (empty)")
EOF
```