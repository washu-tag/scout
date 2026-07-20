"""End-to-end unit tests for the base-ingest activity (`import_hl7_files_to_deltalake`).

These drive the real activity against local files: the manifest is a local text file
(the s3:// -> s3a:// replaces are no-ops on local paths), the "S3" zip download is a
local copy, and the Postgres status writers are mocked. Everything in between — HL7
parsing, the report DataFrame build, table creation, and the MERGE — is the production
code path.

Written red-first for the re-ingest efficiency work (issue #482): re-ingesting an
unchanged manifest must produce no new base-table commit, no change-data-feed rows,
and no bumped `updated` timestamps, while still reporting every file as a success.
"""

import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

import pytest
from temporalio.testing import ActivityEnvironment

from hl7scout.hl7extractor.deltalake import import_hl7_files_to_deltalake
from testutils import (
    cdf_rows_since,
    patched_session,
    table_version,
    updated_by_source_file,
)


@pytest.fixture(autouse=True)
def _resolved_tempdir(monkeypatch):
    """Pin tempfile's default dir to its realpath. On macOS the default temp dir lives
    under the /var -> /private/var symlink; the activity joins
    concat("file://", temp_path) against input_file_name(), which reports the resolved
    path — a symlinked temp dir would silently null out every source_file."""
    monkeypatch.setattr(tempfile, "tempdir", os.path.realpath(tempfile.gettempdir()))


class _LocalFS:
    """S3FileSystem stand-in: 'download' is a local copy."""

    def download(self, src, dst):
        shutil.copy(src, dst)


def _hl7_lines(i: int, obx_text: str = "Report text") -> list[str]:
    return [
        r"MSH|^~\&|SOMERIS|ABCHOSP|SOMEAPP|DEPT|20240102030405|TBD|ORU^R01"
        + f"|MCID{i}|P|2.7",
        f"PID|1||PAT{i}^^^ABC^MR||DOE^JANE||19800101|F",
        "OBR|1|PLC1|FIL1|XRCHEST^CHEST XRAY^L|||20240102030405",
        f"OBX|1|TX|GDT|1|{obx_text} {i}",
    ]


def _write_zip(zip_path: Path, messages: dict[str, list[str]]) -> None:
    with zipfile.ZipFile(zip_path, "w") as z:
        for member, lines in messages.items():
            z.writestr(member, "".join(line + "\r" for line in lines))


def _make_batch(tmp_path: Path, n: int = 3) -> tuple[str, Path, list[str]]:
    """Create one zip of n HL7 messages plus a manifest file listing them the way
    hl7log-extractor does (<zip_path>/<member> per line). Returns
    (manifest_path, zip_path, expected source_files)."""
    zip_path = tmp_path / "log.zip"
    messages = {f"msg_{i}.hl7": _hl7_lines(i) for i in range(n)}
    _write_zip(zip_path, messages)
    source_files = [f"{zip_path}/{member}" for member in messages]
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("\n".join(source_files) + "\n")
    return str(manifest), zip_path, source_files


def _run_activity(spark, manifest: str, modality_csv: str, table: str, health: Path):
    """Run the real activity with local-file stand-ins for S3 and the status DB.
    Returns (return_value, write_successes mock, write_errors mock)."""
    with patched_session(spark), mock.patch(
        "hl7scout.hl7extractor.deltalake.S3FileSystem", _LocalFS
    ), mock.patch(
        "hl7scout.hl7extractor.deltalake.write_successes"
    ) as successes, mock.patch(
        "hl7scout.hl7extractor.deltalake.write_errors"
    ) as errors:
        result = ActivityEnvironment().run(
            import_hl7_files_to_deltalake, manifest, modality_csv, table, health
        )
    return result, successes, errors


@pytest.fixture
def modality_csv(tmp_path):
    path = tmp_path / "modality.csv"
    path.write_text("Exam Code,Modality\nXRCHEST,XR\n")
    return str(path)


def _success_paths(successes_mock) -> set[str]:
    assert successes_mock.call_count == 1
    return set(successes_mock.call_args[0][0])


def test_first_run_creates_and_inserts(spark, tmp_path, modality_csv):
    """Baseline pin: a first ingest creates the table and inserts one row per file."""
    table = "reports_e2e_first"
    manifest, _, source_files = _make_batch(tmp_path)

    result, successes, errors = _run_activity(
        spark, manifest, modality_csv, table, tmp_path / "health"
    )

    assert result == len(source_files)
    rows = spark.table(f"default.{table}").collect()
    assert len(rows) == len(source_files)
    # A NULL source_file here means the temp-path join broke (see _resolved_tempdir).
    assert {r.source_file for r in rows} == set(source_files)
    assert {r.modality for r in rows} == {"XR"}
    assert _success_paths(successes) == set(source_files)
    errors.assert_not_called()


def test_multi_obx_lines_assemble_in_file_order(spark, tmp_path, modality_csv):
    """report_text and the report sections must assemble OBX lines in file order.
    The old extraction ran collect_list on the post-shuffle rows without sorting by
    pos, so multi-OBX messages could reorder between runs — observed on dev03 as
    64/9988 rows spuriously re-hashing on an identical re-ingest, every one a pure
    line reordering. This pins the deterministic, pos-sorted assembly."""
    table = "reports_e2e_obx_order"
    zip_path = tmp_path / "log.zip"
    message = [
        r"MSH|^~\&|SOMERIS|ABCHOSP|SOMEAPP|DEPT|20240102030405|TBD|ORU^R01"
        + "|MCID9|P|2.7",
        "PID|1||PAT9^^^ABC^MR||DOE^JANE||19800101|F",
        "OBR|1|PLC1|FIL1|XRCHEST^CHEST XRAY^L|||20240102030405",
        "OBX|1|TX|&GDT|1|First findings line",
        "OBX|2|TX|&GDT|2|Second findings line",
        "OBX|3|TX|&IMP|1|The impression",
        "OBX|4|TX|&GDT|3|Third findings line",
    ]
    _write_zip(zip_path, {"msg_9.hl7": message})
    manifest = tmp_path / "manifest.txt"
    manifest.write_text(f"{zip_path}/msg_9.hl7\n")

    _run_activity(spark, str(manifest), modality_csv, table, tmp_path / "health")

    row = spark.table(f"default.{table}").collect()[0]
    assert row.report_text == (
        "First findings line\n"
        "Second findings line\n"
        "The impression\n"
        "Third findings line"
    )
    assert row.report_section_findings == (
        "First findings line\nSecond findings line\nThird findings line"
    )
    assert row.report_section_impression == "The impression"


def test_second_identical_run_is_full_noop(spark, tmp_path, modality_csv):
    """Re-ingesting an unchanged manifest must not touch the base table at all: no new
    Delta commit, no CDF rows, no bumped `updated` — while the activity still reports
    every file as a success (so ingest-DB statuses don't stick at 'staged') and returns
    the full count."""
    table = "reports_e2e_noop"
    manifest, _, source_files = _make_batch(tmp_path)

    _run_activity(spark, manifest, modality_csv, table, tmp_path / "health")
    version_after_first = table_version(spark, table)
    updated_after_first = updated_by_source_file(spark, table)

    result, successes, _ = _run_activity(
        spark, manifest, modality_csv, table, tmp_path / "health"
    )

    assert table_version(spark, table) == version_after_first
    assert updated_by_source_file(spark, table) == updated_after_first
    assert result == len(source_files)
    assert _success_paths(successes) == set(source_files)


def test_changed_file_same_source_file_updates(spark, tmp_path, modality_csv):
    """A file whose content changed at the same source_file must still update — and
    ONLY that row: exactly one CDF update, the other rows' `updated` untouched."""
    table = "reports_e2e_changed"
    manifest, zip_path, source_files = _make_batch(tmp_path)

    _run_activity(spark, manifest, modality_csv, table, tmp_path / "health")
    version_after_first = table_version(spark, table)
    updated_after_first = updated_by_source_file(spark, table)

    # Same zip path and member names (so identical source_files), one message changed.
    messages = {f"msg_{i}.hl7": _hl7_lines(i) for i in range(len(source_files))}
    messages["msg_0.hl7"] = _hl7_lines(0, obx_text="Amended report text")
    _write_zip(zip_path, messages)

    _run_activity(spark, manifest, modality_csv, table, tmp_path / "health")

    changed = f"{zip_path}/msg_0.hl7"
    updates = (
        cdf_rows_since(spark, table, version_after_first + 1)
        .filter("_change_type = 'update_postimage'")
        .collect()
    )
    assert {r.source_file for r in updates} == {changed}

    updated_after_second = updated_by_source_file(spark, table)
    assert updated_after_second[changed] > updated_after_first[changed]
    for source_file in source_files:
        if source_file != changed:
            assert updated_after_second[source_file] == updated_after_first[source_file]

    report_text = {
        r.source_file: r.report_text
        for r in spark.table(f"default.{table}")
        .select("source_file", "report_text")
        .collect()
    }
    assert report_text[changed] == "Amended report text 0"
