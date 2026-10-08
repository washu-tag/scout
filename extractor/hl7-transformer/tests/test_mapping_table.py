"""The mapping table's MERGEs write one file each, however many partitions the source
has. An insert-only MERGE otherwise writes one file per source partition."""

from delta.tables import DeltaTable

from hl7scout.hl7extractor.mappingtableextractor import (
    MappingTableExtractor,
    mapping_schema,
)


def test_merge_to_dt_writes_one_file_from_a_multi_partition_source(spark):
    table = "mapping_merge_one_file"
    (
        DeltaTable.createIfNotExists(spark)
        .tableName(table)
        .addColumns(mapping_schema)
        .execute()
    )
    rows = [(f"sp{i}", f"r{i}", f"m{i}", f"e{i}", True) for i in range(64)]
    # The session runs 2 shuffle partitions; a micro-batch source can have hundreds.
    source = spark.createDataFrame(rows, mapping_schema).repartition(8)
    assert source.rdd.getNumPartitions() == 8

    MappingTableExtractor(spark, table).merge_to_dt(source)

    assert spark.sql(f"DESCRIBE DETAIL {table}").first().numFiles == 1
    assert spark.table(table).count() == 64
