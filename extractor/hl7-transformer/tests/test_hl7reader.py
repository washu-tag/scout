from pathlib import Path

import pytest
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from hl7scout.hl7extractor.hl7reader import read_hl7


@pytest.fixture(scope="session")
def spark():
    spark = (
        SparkSession.builder.master("local[1]")
        .appName("test-hl7reader")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .getOrCreate()
    )
    yield spark
    spark.stop()


MSH = r"MSH|^~\&|SOMERIS|ABCHOSP|SOMEAPP|ABC_HOSP_DEPT_X|20140713130856|TBD|ORU^R01|2.25.705|P|2.7"


def write_hl7(
    tmp_path: Path, name: str, lines: list[str], terminator: str = "\r"
) -> str:
    """Write segment lines the way hl7log-extractor does: UTF-8, each line
    followed by a terminator."""
    path = tmp_path / name
    path.write_bytes("".join(line + terminator for line in lines).encode("utf-8"))
    return str(path)


def read_one(spark, path: str):
    rows = read_hl7(spark, [path]).collect()
    assert len(rows) == 1
    return rows[0]


def segments_as_dict(row) -> dict[str, list[str]]:
    return {segment.id: list(segment.fields) for segment in row.segments}


def test_basic_message(spark, tmp_path):
    path = write_hl7(
        tmp_path,
        "basic.hl7",
        [MSH, "PID|1||ABC451104^^^ABC^MR||PARK^SEO-HYEON", "OBR|1|placer|filler"],
    )
    row = read_one(spark, path)

    assert row.message == MSH
    segments = segments_as_dict(row)
    # fields[0] is HL7 field 1: the segment id is not part of fields
    assert segments["PID"][0] == "1"
    assert segments["PID"][1] == ""
    assert segments["PID"][2] == "ABC451104^^^ABC^MR"
    assert segments["OBR"] == ["1", "placer", "filler"]


def test_trailing_empty_fields_dropped_interior_kept(spark, tmp_path):
    # Java's String.split drops trailing empty strings but keeps interior
    # ones; smolder inherited that and downstream null-vs-empty checks
    # depend on it.
    path = write_hl7(tmp_path, "trailing.hl7", [MSH, "PID|123||", "ORC||x"])
    segments = segments_as_dict(read_one(spark, path))

    assert segments["PID"] == ["123"]
    assert segments["ORC"] == ["", "x"]


def test_empty_lines_skipped_in_segments(spark, tmp_path):
    path = write_hl7(tmp_path, "blanks.hl7", [MSH, "", "PID|1", ""])
    row = read_one(spark, path)

    assert row.message == MSH
    assert [segment.id for segment in row.segments] == ["PID"]


@pytest.mark.parametrize("terminator", ["\r", "\n", "\r\n"])
def test_line_terminators(spark, tmp_path, terminator):
    path = write_hl7(
        tmp_path,
        f"term_{len(terminator)}_{ord(terminator[0])}.hl7",
        [MSH, "PID|1"],
        terminator=terminator,
    )
    row = read_one(spark, path)

    assert row.message == MSH
    assert segments_as_dict(row) == {"PID": ["1"]}


def test_segment_without_pipes(spark, tmp_path):
    path = write_hl7(tmp_path, "nopipes.hl7", [MSH, "NTE"])
    segments = segments_as_dict(read_one(spark, path))

    assert segments == {"NTE": []}


def test_empty_file_yields_no_row(spark, tmp_path):
    # Divergence from smolder, which threw on empty input and failed the
    # whole Spark task: the text source skips zero-byte files entirely.
    # hl7log-extractor never produces them (all-blank messages are dropped
    # before upload).
    path = tmp_path / "empty.hl7"
    path.write_bytes(b"")

    assert read_hl7(spark, [str(path)]).collect() == []


def test_blank_first_line_is_message(spark, tmp_path):
    # smolder took the message from the first line before filtering empty
    # lines, so a leading blank line means the MSH line lands in segments
    # and the file fails downstream MSH parsing. Preserve that.
    path = write_hl7(tmp_path, "leading_blank.hl7", ["", MSH, "PID|1"])
    row = read_one(spark, path)

    assert row.message == ""
    assert [segment.id for segment in row.segments] == ["MSH", "PID"]


def test_non_ascii_utf8(spark, tmp_path):
    # hl7log-extractor decodes logs as ISO-8859-1 and writes the .hl7 files
    # as UTF-8, so non-ASCII input reaches the reader as valid UTF-8.
    path = write_hl7(tmp_path, "utf8.hl7", [MSH, "PID|1||X||MUÑOZ^JOSÉ"])
    segments = segments_as_dict(read_one(spark, path))

    assert segments["PID"][4] == "MUÑOZ^JOSÉ"


def test_one_row_per_file_with_input_file_name(spark, tmp_path):
    # deltalake.py joins on input_file_name() to map rows back to S3 paths.
    paths = [write_hl7(tmp_path, f"msg_{i}.hl7", [MSH, f"PID|{i}"]) for i in range(3)]
    df = read_hl7(spark, paths).withColumn("file", F.input_file_name())
    rows = df.collect()

    assert len(rows) == 3
    pid_by_file = {
        Path(row.file.removeprefix("file://")).name: row.segments[0].fields[0]
        for row in rows
    }
    assert pid_by_file == {f"msg_{i}.hl7": str(i) for i in range(3)}


def test_downstream_extraction_pattern(spark, tmp_path):
    # The exact expressions deltalake.py runs against the reader's output.
    path = write_hl7(
        tmp_path,
        "downstream.hl7",
        [MSH, "PID|1|MPI123|ABC451104^^^ABC^MR", "OBX|1|TX|GDT|1|Report text line"],
    )
    df = read_hl7(spark, [path]).select(
        F.split("message", "\\|").alias("msh"),
        F.expr("filter(segments, x -> x.id = 'PID')[0].fields").alias("pid"),
        F.expr("filter(segments, x -> x.id = 'OBX')").alias("obx_lines"),
    )
    row = df.collect()[0]

    assert row.msh[3] == "ABCHOSP"  # MSH-4 sending facility
    assert row.msh[9] == "2.25.705"  # MSH-10 message control id
    assert row.pid[1] == "MPI123"  # PID-2
    assert row.obx_lines[0].fields[4] == "Report text line"  # OBX-5


def test_repeated_segments_keep_file_order(spark, tmp_path):
    # deltalake.py posexplodes the OBX segments and assembles report_text in
    # array order, so the reader must keep repeated segments in file order.
    path = write_hl7(
        tmp_path,
        "multi_obx.hl7",
        [MSH, "OBX|1|TX|||LINE-A", "OBX|2|TX|||LINE-B", "OBX|3|TX|||LINE-C"],
    )
    obx = (
        read_hl7(spark, [path])
        .select(F.expr("filter(segments, x -> x.id = 'OBX')").alias("obx"))
        .collect()[0]
        .obx
    )

    assert [segment.fields[4] for segment in obx] == ["LINE-A", "LINE-B", "LINE-C"]


def test_segment_of_only_pipes_yields_empty_id(spark, tmp_path):
    # Documented divergence from smolder, which threw on an empty field array
    # and failed the whole task. A pipes-only line yields a segment with an
    # empty id and no fields, which matches no downstream id filter and is
    # harmless. hl7log-extractor never produces this (segment lines start with
    # an id).
    path = write_hl7(tmp_path, "pipes_only.hl7", [MSH, "|||"])
    row = read_one(spark, path)

    assert [segment.id for segment in row.segments] == [""]
    assert list(row.segments[0].fields) == []
