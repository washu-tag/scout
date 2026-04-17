from .derivativetable import DerivativeTable
from .mappingtableextractor import MappingTableExtractor


def mapping_table(base_report_table_name: str) -> DerivativeTable:
    source_table = f"{base_report_table_name}_curated"

    def process_table(batch_df, spark, table_name):
        MappingTableExtractor(spark, table_name).extract(batch_df)

    return DerivativeTable(
        source_table=source_table,
        table_name=f"{base_report_table_name}_report_patient_mapping",
        process_source_data=process_table,
    )
