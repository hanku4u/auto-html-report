"""Writing a report to a directory on the local filesystem.

The default writer, and the one a CI job uses unless it is configured for S3. Two properties matter more
than the code's size:

* **Atomic publication.** The bytes go to a temporary file in the same directory and are then renamed
  into place, so a reader never sees a half-written report and a crash leaves the previous one intact. A
  partially written HTML file is worse than a missing one: it looks like a report.
* **No path derivation.** The writer receives a complete filename and refuses anything that is not a
  plain name. Writers never invent paths, and a filename that could escape the directory is a boundary
  this module does not cross — the naming rule lives in ``docs/06-output-html.md`` §2 and is applied by
  :func:`auto_inform.generate.report_filename`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from auto_inform.domain.errors import OutputError
from auto_inform.domain.report import OutputLocation


class LocalDirectoryWriter:
    """Writes reports into a directory, creating it if needed."""

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)

    def write(self, *, filename: str, content: str) -> OutputLocation:
        """Write ``content`` as ``filename``, atomically, and say where it landed."""
        _check_filename(filename)

        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            target = (self._directory / filename).resolve()
            handle, temporary = tempfile.mkstemp(
                dir=str(self._directory), prefix=".auto-inform-", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
                    stream.write(content)
                    stream.flush()
                    # Durability before publication: if the rename lands but the bytes have not reached
                    # the disk, a power loss can leave an empty file under the real name.
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise OutputError(
                f"could not write {filename!r} into {self._directory}: {exc}"
            ) from exc

        return OutputLocation(kind="file", uri=target.as_uri())


def _check_filename(filename: str) -> None:
    """Refuse anything that is not a plain file name in the writer's own directory."""
    if (
        not filename
        or filename in {".", ".."}
        or "\x00" in filename
        or Path(filename).name != filename
    ):
        raise OutputError(
            f"{filename!r} is not a plain filename. Writers receive a complete name and never derive a "
            "path; see docs/06-output-html.md §2."
        )
