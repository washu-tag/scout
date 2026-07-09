"""Environment smoke tests: prove the local Spark + Delta + CDF harness works and that
the production modules import. If these fail, the richer derivative tests can't run."""


def test_production_modules_import():
    # Importing these pulls in the full runtime dependency set (delta, temporalio,
    # trino, s3fs, psycopg). A failure here is a harness/deps problem, not a logic bug.
    import hl7scout.hl7extractor.sparkutils  # noqa: F401
    import hl7scout.hl7extractor.dataextraction  # noqa: F401
    import hl7scout.hl7extractor.deltalake  # noqa: F401

    # The activity wrappers must import cleanly with the split signatures (base activity
    # no longer takes createMapping; the derive activity is added).
    from hl7scout.activities.ingesthl7 import (  # noqa: F401
        IngestHl7FilesActivity,
        IngestHl7FilesToDeltaLakeActivityInput,
        DeriveDeltaTablesActivityInput,
    )


def test_delta_change_data_feed_roundtrip(spark):
    df = spark.createDataFrame([(1, "a"), (2, "b")], ["id", "source_file"])
    (
        df.write.format("delta")
        .option("delta.enableChangeDataFeed", "true")
        .mode("overwrite")
        .saveAsTable("default.smoke")
    )

    cdf = (
        spark.read.format("delta")
        .option("readChangeFeed", "true")
        .option("startingVersion", 0)
        .table("default.smoke")
    )

    assert "_change_type" in cdf.columns
    assert cdf.filter(cdf._change_type == "insert").count() == 2
