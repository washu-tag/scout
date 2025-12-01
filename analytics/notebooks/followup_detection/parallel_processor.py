"""
Parallel processing module for follow-up detection.

This module provides a simple ThreadPoolExecutor-based approach for processing
reports in parallel, avoiding the complexity of Spark's mapPartitions.

Usage:
    from parallel_processor import process_reports_in_batches

    process_reports_in_batches(
        spark=spark,
        classify_func=classify_report,
        dest_table="default.reports",
        error_table="default.followup_errors",
        ollama_url="http://ollama.chat:11434",
        model="gpt-oss:20b",
        total_workers=16,
        batch_size=50000
    )
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm.notebook import tqdm
import requests
from pyspark.sql import functions as F
from pyspark.sql.types import StructType, StructField, StringType, BooleanType, TimestampType


def process_reports_in_batches(
        spark,
        classify_func,
        dest_table,
        error_table,
        ollama_url,
        model,
        total_workers=16,
        batch_size=50000,
        priority_filter=None
):
    """
    Process unprocessed reports in batches using simple parallel ThreadPoolExecutor.

    Parameters
    ----------
    spark : SparkSession
        Active Spark session
    classify_func : callable
        Function that takes a row and returns classification result dict
    dest_table : str
        Name of destination table (e.g., "default.latest_reports")
    error_table : str
        Name of error logging table (e.g., "default.followup_errors")
    ollama_url : str
        Base URL for Ollama API
    model : str
        Model name to use
    total_workers : int
        Number of parallel workers (default: 16)
    batch_size : int
        Number of reports to process per batch (default: 50000)
    priority_filter : str, optional
        SQL WHERE clause to prioritize certain reports (e.g., "lower(report_text) RLIKE 'follow.*up|recommend'")

    Returns
    -------
    dict
        Summary statistics with keys: total_processed, total_errors, elapsed_time
    """

    # Load model into memory before processing
    print("📥 Loading model into memory...")
    resp = requests.post(f"{ollama_url}/api/generate", json={"model": model, "keep_alive": -1})
    if resp.status_code == 200:
        print("✅ Model loaded into memory")
    else:
        print(f"⚠️  Warning: Failed to load model: {resp.text}")

    # Get unprocessed reports count
    df_to_process = spark.table(dest_table).filter(F.col("followup_processed_at").isNull())
    total_reports = df_to_process.count()

    if total_reports == 0:
        print("✅ No reports to process!")
        return {"total_processed": 0, "total_errors": 0, "elapsed_time": 0}

    # Check priority filter coverage
    if priority_filter:
        priority_reports = df_to_process.filter(priority_filter).count()
        non_priority_reports = total_reports - priority_reports
        print(f"\n🎯 Priority Filter Statistics:")
        print(f"  Priority reports (with keywords): {priority_reports:,} ({100*priority_reports/total_reports:.1f}%)")
        print(f"  Non-priority reports: {non_priority_reports:,} ({100*non_priority_reports/total_reports:.1f}%)")

    num_batches = (total_reports + batch_size - 1) // batch_size

    print(f"\n📊 Processing Summary:")
    print(f"  Total reports: {total_reports:,}")
    print(f"  Batch size: {batch_size:,}")
    print(f"  Batches: {num_batches}")
    print(f"  Workers: {total_workers}")
    print(f"  Estimated time: {(total_reports * 0.5 / total_workers / 3600):.1f} hours")
    print()

    # Result schema
    result_schema = StructType([
        StructField("message_control_id", StringType(), True),
        StructField("followup_detected", BooleanType(), True),
        StructField("followup_confidence", StringType(), True),
        StructField("followup_snippet", StringType(), True),
        StructField("followup_finding", StringType(), True),
        StructField("error", StringType(), True)
    ])

    # Track overall stats
    overall_start = time.time()
    total_processed = 0
    total_errors = 0

    # Process batches
    for batch_num in tqdm(range(num_batches), desc="Batches"):
        # Get batch as Pandas (re-query to get updated unprocessed reports)
        # Apply priority filter if specified
        df_query = spark.table(dest_table).filter(F.col("followup_processed_at").isNull())

        if priority_filter:
            # Try priority filter first
            df_batch_pandas = (df_query
                               .filter("modality = 'CT'")
                               .filter("lower(service_name) RLIKE 'chest|thorax|lung'")
                               .filter(priority_filter)
                               .orderBy(F.col("message_dt").desc())
                               .limit(batch_size)
                               .select("message_control_id", "report_text")
                               .toPandas())

            # If no priority matches remain, fall back to all unprocessed
            if len(df_batch_pandas) == 0:
                print("  ✅ All priority reports processed, moving to remaining reports...")
                df_batch_pandas = (df_query
                                   .limit(batch_size)
                                   .orderBy(F.col("message_dt").desc())
                                   .select("message_control_id", "report_text")
                                   .toPandas())
        else:
            # No priority filter, process in order
            df_batch_pandas = (df_query
                               .limit(batch_size)
                               .orderBy(F.col("message_dt").desc())
                               .select("message_control_id", "report_text")
                               .toPandas())

        if len(df_batch_pandas) == 0:
            print("✅ No more reports to process")
            break

        # Track batch timing
        batch_start = time.time()

        # Process with simple ThreadPoolExecutor
        results = []
        with ThreadPoolExecutor(max_workers=total_workers) as executor:
            futures = {
                executor.submit(classify_func, row): idx
                for idx, row in df_batch_pandas.iterrows()
            }

            # Nested progress bar for reports within batch
            with tqdm(total=len(df_batch_pandas), desc=f"  Batch {batch_num+1} reports", leave=False) as pbar:
                for future in as_completed(futures):
                    results.append(future.result())
                    pbar.update(1)

        batch_elapsed = time.time() - batch_start

        # Convert results to DataFrame
        print(f"  💾 Converting {len(results)} results to Spark DataFrame...")
        df_results = spark.createDataFrame(results, schema=result_schema)

        # Separate successes and failures
        df_success = df_results.filter(F.col("error").isNull()).drop("error")
        df_errors = df_results.filter(F.col("error").isNotNull())

        # Log errors
        print(f"  📊 Counting successes and errors...")
        error_count = df_errors.count()
        success_count = df_success.count()
        print(f"     ✓ {success_count} successful, {error_count} errors")

        total_processed += success_count
        total_errors += error_count

        if error_count > 0:
            print(f"  ⚠️  Writing {error_count} errors to {error_table}...")
            (df_errors
             .select(
                F.col("message_control_id"),
                F.col("error"),
                F.current_timestamp().alias("error_timestamp")
            )
             .write
             .format("delta")
             .mode("append")
             .saveAsTable(error_table))
            print(f"     ✓ Errors logged")

        # MERGE results back to latest_reports
        if success_count > 0:
            print(f"  🔄 Merging {success_count} results into {dest_table}...")
            df_success.createOrReplaceTempView("results")
            spark.sql(f"""
                MERGE INTO {dest_table} AS target
                USING results AS source
                ON target.message_control_id = source.message_control_id
                WHEN MATCHED THEN UPDATE SET
                    target.followup_detected = source.followup_detected,
                    target.followup_confidence = source.followup_confidence,
                    target.followup_snippet = source.followup_snippet,
                    target.followup_finding = source.followup_finding,
                    target.followup_processed_at = current_timestamp()
            """)
            print(f"     ✓ Merge complete")

            # Clean up temp view to prevent memory accumulation
            spark.catalog.dropTempView("results")

        # Progress update
        print(f"  📈 Calculating progress...")
        remaining = spark.table(dest_table).filter(F.col("followup_processed_at").isNull()).count()
        processed_count = total_reports - remaining
        pct = 100 * processed_count / total_reports if total_reports > 0 else 0
        throughput = len(df_batch_pandas) / batch_elapsed

        print(f"  📈 Batch {batch_num+1}/{num_batches}: "
              f"{processed_count:,} processed ({pct:.1f}%), "
              f"{remaining:,} remaining | "
              f"{throughput:.2f} reports/sec, "
              f"{error_count} errors")

        # Explicitly clean up DataFrames to help GC
        del df_batch_pandas, results, df_results, df_success, df_errors

        # Force Spark to clean up execution plans every 10 batches
        if (batch_num + 1) % 10 == 0:
            spark.catalog.clearCache()

    overall_elapsed = time.time() - overall_start

    # Unload model from memory
    #print("\n📤 Unloading model from memory...")
    #requests.post(f"{ollama_url}/api/generate", json={"model": model, "keep_alive": 0})

    print("\n✅ Processing complete!")
    print(f"\n📊 Final Statistics:")
    print(f"  Total processed: {total_processed:,}")
    print(f"  Total errors: {total_errors:,}")
    print(f"  Error rate: {100*total_errors/(total_processed+total_errors):.2f}%")
    print(f"  Total time: {overall_elapsed/3600:.2f} hours")
    print(f"  Overall throughput: {(total_processed+total_errors)/overall_elapsed:.2f} reports/sec")

    return {
        "total_processed": total_processed,
        "total_errors": total_errors,
        "elapsed_time": overall_elapsed
    }
