from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F


def read_hl7(spark: SparkSession, paths: list[str]) -> DataFrame:
    """Read one-message-per-file HL7 files into rows of {message, segments}.

    Drop-in replacement for the smolder data source (spark.read.format("hl7")),
    preserving its parsing semantics:

    - One file produces one row.
    - `message` is the first line of the file, verbatim (the MSH segment),
      taken before any empty-line filtering.
    - `segments` holds each subsequent non-empty line as a struct of `id` (the
      text before the first pipe) and `fields` (the remaining pipe-separated
      values, so fields[0] is HL7 field 1). Trailing empty fields are dropped
      and interior empty fields are kept, matching the Java String.split
      behavior smolder relied on.
    - Segment lines may be terminated by \\r (HL7 standard), \\n, or \\r\\n.

    Known divergences, both on inputs where smolder threw and failed the Spark
    task: a zero-byte file yields no row at all (the text source skips empty
    files, so the file is never marked ingested), and a segment line of only
    pipes yields a segment with an empty id. Neither input is produced by
    hl7log-extractor, which drops all-blank messages before upload.
    """
    raw_lines = F.split(F.col("value"), "\r\n|\r|\n")
    # Split each segment line once into tokens, then build structs from the
    # token arrays; repeating the split inside the struct lambda would
    # re-evaluate it per accessed element.
    segment_tokens = F.transform(
        F.filter(
            F.slice(raw_lines, 2, F.size(raw_lines) - 1),
            lambda line: line != "",
        ),
        # Stripping trailing pipes before the split is what drops trailing
        # empty fields.
        lambda line: F.split(F.regexp_replace(line, "\\|+$", ""), "\\|"),
    )
    return spark.read.text(paths, wholetext=True).select(
        F.get(raw_lines, 0).alias("message"),
        F.transform(
            segment_tokens,
            lambda tokens: F.struct(
                F.get(tokens, 0).alias("id"),
                F.slice(tokens, 2, F.size(tokens) - 1).alias("fields"),
            ),
        ).alias("segments"),
    )
