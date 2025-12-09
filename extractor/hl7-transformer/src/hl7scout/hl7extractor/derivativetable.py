from dataclasses import field, dataclass
from typing import Callable

from pyspark.sql import DataFrame, SparkSession


@dataclass
class DerivativeTable:
    source_table: str
    table_name: str
    process_source_data: Callable[[DataFrame, SparkSession, str], None]
    children_tables: dict[str, "DerivativeTable"] = field(
        default_factory=dict, init=False
    )
