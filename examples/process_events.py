"""Processing job: build and write one report per pending event.

The second half of the pipeline (``docs/05`` §2). Importing the manufacturing family is the deployment
step that registers the ``WAFER_FAILURE`` factory; without it every event would be skipped and the run
would exit ``2``.

    uv run python -m examples.discover_wafers
    uv run python -m examples.process_events

``-m`` rather than the path, so the shared fixtures in ``examples.demo_api`` import (see
``discover_wafers``). ``main()`` takes an optional ``Settings`` for the test suite; a job script passes
none.
"""

from __future__ import annotations

import sys

import auto_inform.reports.manufacturing  # noqa: F401 - importing registers the factory
from auto_inform import (
    ExitCode,
    Settings,
    build_output_writer,
    configure_logging,
    exit_code_for,
    load_settings,
    new_report_context,
    run_processing,
)
from auto_inform.domain.errors import ConfigurationError, OutputError, StoreError
from examples.demo_api import demo_http
from examples.store import open_store


def main(settings: Settings | None = None) -> int:
    try:
        resolved = load_settings() if settings is None else settings
        configure_logging(resolved.log_level, resolved.log_format)
        writer = build_output_writer(resolved)
        with open_store(resolved) as store:
            run = run_processing(
                store=store,
                # The demo fixtures stand in for the yield and maintenance APIs.
                context=new_report_context(resolved, http=demo_http()),
                writer=writer,
            )
    except ConfigurationError:
        return ExitCode.CONFIGURATION_ERROR
    except StoreError:
        return ExitCode.STORE_ERROR
    except OutputError:
        return ExitCode.OUTPUT_ERROR
    return exit_code_for(run)


if __name__ == "__main__":
    sys.exit(main())
