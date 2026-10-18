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
from auto_inform.infrastructure.writers._names import check_filename


class LocalDirectoryWriter:
    """Writes reports into a directory, creating it if needed.

    **Publication is atomic.** The bytes go to a temporary file in the same directory, are flushed and
    ``fsync``'d, and are then renamed into place; the directory entry is flushed after the rename so the
    replacement survives a power loss, not merely a crash. A reader never sees a half-written report, and
    a failure leaves the previous one intact.

    **Reports are published owner-only (mode 0600), deliberately.** ``mkstemp`` creates them that way and
    the rename preserves the mode. These documents carry production yield and maintenance data, so least
    privilege is the right default rather than an accident: a consumer — an artifact uploader, a web
    server — must run as the writing user or copy the file. If a shared mode is genuinely wanted, ``chmod``
    after writing; the writer will not widen it.

    **Filesystem semantics are POSIX.** Windows reserved device names are not handled, and Windows is not
    a supported target for this writer in v1.

    A crash between the temporary file's creation and the rename leaves an ``.auto-inform-*.tmp`` file
    behind. Nothing sweeps those: the prefix makes them identifiable and safe to delete, and a sweeper is
    the operator's job rather than a race with a concurrent writer.
    """

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)

    def write(self, *, filename: str, content: str) -> OutputLocation:
        """Write ``content`` as ``filename``, atomically, and say where it landed."""
        check_filename(filename)

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
                _sync_directory(self._directory)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise OutputError(
                f"could not write {filename!r} into {self._directory}: {exc}"
            ) from exc

        return OutputLocation(kind="file", uri=target.as_uri())


def _sync_directory(directory: Path) -> None:
    """Flush the directory entry so the rename survives a power loss, not just a crash.

    Atomicity is what the rename already provides; durability of the *rename* needs the parent directory
    flushed too. POSIX only, and best-effort: a filesystem that refuses to open a directory for reading
    leaves the atomicity guarantee intact, which is the property that matters to a reader.
    """
    if os.name != "posix":
        return

    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
