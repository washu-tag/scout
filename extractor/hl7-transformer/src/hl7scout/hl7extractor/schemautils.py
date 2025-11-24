from typing import Callable

from pyspark.sql.types import StructField, StringType, StructType, ArrayType
from pyspark.sql import functions as F, Column

shared_name_components = [
    StructField("family_name", StringType()),
    StructField("given_name", StringType()),
    StructField("second_and_further_names", StringType()),
    StructField("suffix", StringType()),
    StructField("prefix", StringType()),
    StructField("degree", StringType()),
    StructField("name_type_code", StringType()),
]

name_with_id_components = (
    [StructField("id_number", StringType())]
    + shared_name_components
    + [
        StructField("assigning_authority", StringType()),
        StructField("identifier_type_code", StringType()),
        StructField("assigning_facility", StringType()),
    ]
)

person_name_schema = StructType(shared_name_components)
person_name_and_id_schema = StructType(name_with_id_components)


def empty_column(column_name: str) -> Column:
    return F.lit(None).cast(StringType()).alias(column_name)


def null_if_empty(column):
    return F.when((column != "") & column.isNotNull(), column).otherwise(None)


def map_xpn_to_struct(parts):
    return F.struct(
        *[
            null_if_empty(parts[i]).alias(field.name)
            for i, field in enumerate(shared_name_components)
        ]
    )


def map_xcn_to_struct(parts):
    return F.struct(
        null_if_empty(parts[0]).alias("id_number"),
        *[
            null_if_empty(parts[i + 1]).alias(field.name)
            for i, field in enumerate(shared_name_components)
            if i < 6
        ],  # wrong spot for name_type_code
        null_if_empty(parts[9]).alias("name_type_code"),
        null_if_empty(parts[8]).alias("assigning_authority"),
        null_if_empty(parts[12]).alias("identifier_type_code"),
        null_if_empty(parts[13]).alias("assigning_facility")
    )


def map_cnn_to_struct(parts):
    return F.struct(
        null_if_empty(parts[0]).alias("id_number"),
        *[
            null_if_empty(parts[i + 1]).alias(field.name)
            for i, field in enumerate(shared_name_components)
            if i < 6
        ],  # wrong spot for name_type_code
        empty_column("name_type_code"),
        null_if_empty(parts[8]).alias("assigning_authority"),
        empty_column("identifier_type_code"),
        empty_column("assigning_facility")
    )


def read_first_struct_name_friendly(source_column: str) -> Column:
    return F.concat_ws(
        " ", F.col(source_column)[0].given_name, F.col(source_column)[0].family_name
    )


def read_struct_of_names_friendly(source_column: str) -> Column:
    return F.array_distinct(
        F.transform(
            source_column,
            lambda name: F.concat_ws(" ", name.given_name, name.family_name),
        )
    )


def split_and_transform_repeated_field(
    column: str,
    component_lambda: Callable[[Column], Column],
    object_filter: Callable[[Column], Column] = None,
) -> Column:
    transformed = F.transform(
        F.transform(
            F.split(F.col(column), "~"),  # split repetition
            lambda component: F.split(component, "\\^"),  # and then split by component
        ),
        component_lambda,
    )
    return F.filter(transformed, object_filter) if object_filter else transformed


def extract_person_names_from_xpn(
    column: str, object_filter: Callable[[Column], Column] = None
) -> Column:
    return split_and_transform_repeated_field(
        column, map_xpn_to_struct, object_filter
    ).cast(ArrayType(person_name_schema))


def extract_person_names_from_xcn(
    column: str, object_filter: Callable[[Column], Column] = None
) -> Column:
    return split_and_transform_repeated_field(
        column, map_xcn_to_struct, object_filter
    ).cast(ArrayType(person_name_and_id_schema))


def extract_person_names_from_cnn(
    column: str, object_filter: Callable[[Column], Column] = None
) -> Column:
    return split_and_transform_repeated_field(
        column, map_cnn_to_struct, object_filter
    ).cast(ArrayType(person_name_and_id_schema))
