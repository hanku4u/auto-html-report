"""The local writer: atomic publication, and a refusal to derive paths.

The atomicity test is the important one. A half-written HTML file is worse than a missing one, because it
looks like a report, so the writer publishes by renaming a complete temporary file into place.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from auto_inform.domain.errors import OutputError
from auto_inform.infrastructure.writers.local_writer import LocalDirectoryWriter


def test_write_should_create_the_directory_and_the_file(tmp_path: Path) -> None:
    # given a directory that does not exist yet
    target = tmp_path / "reports" / "nested"

    # when a report is written
    location = LocalDirectoryWriter(target).write(filename="report.html", content="<html></html>")

    # then the directory and the file exist, and the writer says where
    assert (target / "report.html").read_text(encoding="utf-8") == "<html></html>"
    assert location.kind == "file"


def test_write_should_return_an_absolute_file_uri(tmp_path: Path) -> None:
    # given a written report
    location = LocalDirectoryWriter(tmp_path).write(filename="report.html", content="x")

    # when the location is inspected
    # then it is an absolute file URI, so a caller can log it or hand it to an event store without
    # knowing the working directory
    assert location.uri.startswith("file:///")
    assert location.uri.endswith("/report.html")


def test_write_should_preserve_non_ascii_content(tmp_path: Path) -> None:
    # given a document with an em dash and an accented name, both of which appear in real reports
    content = "<p>LINE 4 — Daily Health · José</p>"

    # when it is written and read back
    # then it round-trips as UTF-8
    LocalDirectoryWriter(tmp_path).write(filename="report.html", content=content)

    assert (tmp_path / "report.html").read_text(encoding="utf-8") == content


def test_write_should_overwrite_a_file_of_the_same_name(tmp_path: Path) -> None:
    # given an existing report with the same name, as a re-run of a job produces
    writer = LocalDirectoryWriter(tmp_path)
    writer.write(filename="report.html", content="first")

    # when it is written again
    writer.write(filename="report.html", content="second")

    # then the second wins, because writers are idempotent for a filename and names are unique per run
    assert (tmp_path / "report.html").read_text(encoding="utf-8") == "second"


def test_write_should_leave_no_temporary_files_behind(tmp_path: Path) -> None:
    # given a written report
    LocalDirectoryWriter(tmp_path).write(filename="report.html", content="x")

    # when the directory is listed
    # then only the report is there: the temporary file was renamed into place, not abandoned
    assert [path.name for path in tmp_path.iterdir()] == ["report.html"]


@pytest.mark.parametrize("filename", ["../escape.html", "sub/report.html", "..", ".", ""])
def test_write_should_refuse_a_filename_that_is_not_a_plain_name(
    tmp_path: Path, filename: str
) -> None:
    # given a filename that would leave the writer's directory
    # when it is written
    # then it is refused. Writers receive a complete name and never derive a path
    # (docs/06-output-html.md §2)
    with pytest.raises(OutputError, match="plain filename"):
        LocalDirectoryWriter(tmp_path).write(filename=filename, content="x")


def test_write_should_raise_output_error_when_the_directory_cannot_be_created(
    tmp_path: Path,
) -> None:
    # given a *file* where the output directory should be
    obstruction = tmp_path / "reports"
    obstruction.write_text("not a directory", encoding="utf-8")

    # when a report is written
    # then the failure is an OutputError with the underlying cause chained, so the processing job can
    # distinguish "could not write" from "the block failed"
    with pytest.raises(OutputError, match="could not write"):
        LocalDirectoryWriter(obstruction).write(filename="report.html", content="x")


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_write_should_publish_owner_only(tmp_path: Path) -> None:
    # given a written report
    LocalDirectoryWriter(tmp_path).write(filename="report.html", content="x")

    # when its mode is inspected
    # then it is 0600. That is deliberate, not incidental: the temporary file from mkstemp is owner-only
    # and the rename preserves it, and these documents carry production data. A consumer must run as the
    # writing user or copy the file (docs/06-output-html.md §1)
    assert (tmp_path / "report.html").stat().st_mode & 0o777 == 0o600
