# auto-html-report

Build a report from a list of blocks, and get one self-contained HTML file. It has inline CSS, embedded charts, no
JavaScript and no external requests, so you can email it or archive it and it keeps working.

If several blocks need the same data, the data is fetched once.

```bash
pip install auto-html-report
pip install "auto-html-report[charts]"   # if your blocks draw matplotlib charts
```

## Quick start

A block is one row of the report. It says where its data comes from and how to show it:

```python
from auto_html_report import Block, build_report, html_table


def get_shifts():
    return [("Day", "06:00-14:00"), ("Swing", "14:00-22:00"), ("Night", "22:00-06:00")]


class Shifts(Block):
    title = "Shift calendar"
    gather = get_shifts

    def render(self, data):
        return html_table(["Shift", "Hours"], data)


report = build_report("Daily review", [Shifts()])
print(report.save("reports/"))
```

That prints the path of an HTML file you can open in a browser. The report is a table with three columns: **Item**
shows the block's title, **Data** shows what `render` returned, and **Check** is left empty for a reader to fill in.

## Creating your own blocks

Subclass `Block` and set three things:

| Part | What it does |
| --- | --- |
| `title` | The text in the row's Item column. |
| `gather` | A plain function that fetches the data. Leave it out if the block needs no data; `render` then receives `None`. |
| `render(self, data)` | Takes what `gather` returned, and returns the HTML for the row's Data cell. |

### Pass inputs through the context

The parameter names of a `gather` function say what it needs. You supply the values in `context`, which is a dict that
can hold anything, including IDs, API clients and database settings:

```python
def get_maintenance(tool_id, http):
    return http.get_json(f"/maintenance/{tool_id}", timeout=10)


class MaintenanceTable(Block):
    title = "Maintenance history"
    gather = get_maintenance

    def render(self, data):
        rows = [(record["date"], record["engineer"]) for record in data["records"]]
        return html_table(["Date", "Engineer"], rows)


report = build_report(
    "Tool review",
    [MaintenanceTable()],
    context={"tool_id": "T-104", "http": my_client},
)
```

The package doesn't include an HTTP client, so `my_client` is whichever client you already use. Inside a `gather`
function you can also query a database, read a file or run any other code.

### Share data between blocks

Blocks that use the same `gather` function with the same arguments share one call:

```python
class MaintenanceCount(Block):
    title = "Maintenance count"
    gather = get_maintenance  # the same function as MaintenanceTable

    def render(self, data):
        return str(len(data["records"]))


report = build_report(
    "Tool review",
    [MaintenanceTable(), MaintenanceCount()],  # one API call, two rows
    context={"tool_id": "T-104", "http": my_client},
)
```

> **Define each `gather` function once, and import it wherever it's needed.** Sharing depends on it being the same
> function. Two separate functions that make the same API call both run, and nothing warns you.

Blocks sharing a call receive the same object, so `render` must not change `data`.

### Use the same block with different inputs

Arguments passed to a block override the context for that block. Titles must be unique, so give each copy its own:

```python
report = build_report(
    "Tool comparison",
    [
        MaintenanceTable(title="T-104 history", tool_id="T-104"),
        MaintenanceTable(title="T-112 history", tool_id="T-112"),  # different arguments, so a second call
    ],
    context={"http": my_client},
)
```

If a block always needs a setting of its own, give it a constructor and pass the rest to `super().__init__`:

```python
class RecentMaintenance(Block):
    gather = get_maintenance

    def __init__(self, tool_id, limit=5):
        super().__init__(title=f"Recent work on {tool_id}", tool_id=tool_id)
        self.limit = limit

    def render(self, data):
        rows = [(record["date"], record["engineer"]) for record in data["records"][: self.limit]]
        return html_table(["Date", "Engineer"], rows)
```

### Read from several sources

Set `gather` to a dict, and `render` receives a dict with the same keys. Each source can still be shared with other
blocks:

```python
class ToolSummary(Block):
    title = "Tool summary"
    gather = {"maintenance": get_maintenance, "shifts": get_shifts}

    def render(self, data):
        return html_table(["Events", "Shifts"], [(len(data["maintenance"]["records"]), len(data["shifts"]))])
```

### Keep the output safe

`render` returns HTML that goes into the report as written. Anything from outside your program has to go through
`html_table`, which escapes every value, or `escape`:

```python
from auto_html_report import escape


class ToolNote(Block):
    title = "Note"
    gather = get_maintenance

    def render(self, data):
        return f"<p>Last engineer: {escape(data['records'][0]['engineer'])}</p>"
```

### Draw a chart

Use `matplotlib.figure.Figure` rather than `pyplot`, and embed the result as an image:

```python
from matplotlib.figure import Figure

from auto_html_report import figure_to_data_uri


class WorkPerEngineer(Block):
    title = "Work per engineer"
    gather = get_maintenance

    def render(self, data):
        engineers = [record["engineer"] for record in data["records"]]
        figure = Figure(figsize=(4, 3))
        figure.add_subplot().hist(engineers)
        return f'<img class="ai-figure" src="{figure_to_data_uri(figure)}" alt="Work per engineer">'
```

### Test your blocks

A `gather` function and a `render` method are ordinary Python, so you can test each one without a report or a server:

```python
def test_maintenance_count_shows_the_number_of_records():
    assert MaintenanceCount().render({"records": [{}, {}]}) == "2"
```

## Choosing blocks at runtime

`blocks` is an ordinary list, and rows appear in its order. To let a config file or a user pick the rows, map names to
block classes:

```python
AVAILABLE = {
    "shifts": Shifts,
    "maintenance": MaintenanceTable,
    "count": MaintenanceCount,
}

requested = ["count", "maintenance"]  # from a config file, an API request or command-line arguments

report = build_report(
    "Tool review",
    [AVAILABLE[name]() for name in requested],
    context={"tool_id": "T-104", "http": my_client},
)
```

Only the data those blocks need is fetched. Here that's one API call, and `get_shifts` doesn't run.

## When things fail

| What happens | Result |
| --- | --- |
| A block needs a context key nobody supplied | `MissingContextError` before anything is fetched, listing every missing key |
| A call raises `TransientError`, `TimeoutError` or `ConnectionError` | Retried up to 3 times, after 1, 2 and 4 seconds, then `FetchError` |
| A call raises anything else | `FetchError` straight away |
| `render` raises | That row shows the error, and the rest of the report still renders |

A failed fetch stops the whole report, so you never get one with data missing. To handle these errors, catch them
around `build_report`, and check `report.failed_rows` to find rows that couldn't render:

```python
from auto_html_report import FetchError, MissingContextError

try:
    report = build_report("Tool review", [MaintenanceTable()], context={"http": my_client})
except MissingContextError as error:
    print(error)  # missing context: 'Maintenance history' needs tool_id
except FetchError as error:
    print(error)
else:
    print(report.failed_rows)
```

Client libraries usually have their own exception types. Raise `TransientError` for failures worth retrying, or list
those types in `retry_on`:

```python
from auto_html_report import TransientError


def get_status(http):
    response = http.get_json("/status", timeout=10)
    if response.get("state") == "starting":
        raise TransientError("the service is still starting")
    return response
```

**Set a timeout on every network call inside a `gather` function.** Python can't interrupt a running call, so without
a timeout a stuck request keeps running, and can delay your program's exit, even after `fetch_timeout` ends the report.

## API reference

**`build_report(title, blocks, context=None, ...)`** returns a `Report`. These optional keyword arguments are available:

| Argument | Default | Meaning |
| --- | --- | --- |
| `subtitle` | `None` | A line under the report title. |
| `metadata` | `None` | A dict of labels and values shown in the report header. |
| `retries` | `3` | Retries per call, after the first attempt. |
| `retry_delay` | `1.0` | Seconds before the first retry. It doubles each time. |
| `retry_on` | see above | The exception types worth retrying. |
| `max_workers` | `8` | How many calls run at the same time. |
| `fetch_timeout` | `300.0` | Seconds to wait for all calls before raising `FetchError`. |

**`Report`** has `html`, `title`, `rows`, `failed_rows`, `generated_at` and `save(directory)`. `save` returns the file's
path, and the file is readable only by its owner, because reports often hold internal data.

**`Row`** has `title`, `html` and `error`, which is `None` unless `render` failed.

**Helpers:** `html_table(headers, rows)`, `escape(text)` and `figure_to_data_uri(figure, dpi=100)`.

**Errors:** `MissingContextError` and `FetchError` are both `ReportError`s. `TransientError` is yours to raise.

## Runnable examples

These are in the [GitHub repository](https://github.com/hanku4u/auto-html-report), and run from a clone of it:

```bash
uv run python examples/quick_start.py /tmp/report-demo    # one row, no network
uv run python -m scripts.shared_data_example              # API, database and plain-function sources
uv run python -m examples.manufacturing                   # a table and a chart
uv run python -m examples.plumbing_check                  # retries and failures over real HTTP
```

[`examples/http_client.py`](https://github.com/hanku4u/auto-html-report/blob/main/examples/http_client.py) is a small
HTTP client you can copy.

## Documentation for coding agents

This guide and the design notes are installed with the package, so a coding agent can read them without internet
access. They're in the `docs` folder inside the installed package. To print their location:

```bash
python -c "from importlib.resources import files; print(files('auto_html_report') / 'docs')"
```

Point your agent there, for example from your project's `AGENTS.md` or `CLAUDE.md`.

## Upgrading from 0.1.0

0.2.0 is a rewrite, so existing blocks need changing:

| 0.1.0 | Now |
| --- | --- |
| `collect(self, context)` | A plain `gather` function, with its inputs as parameters |
| `render(self, data, context)` | `render(self, data)` |
| `generate_report(ReportDefinition(...), context=..., writer=...)` | `build_report(title, blocks, context).save(directory)` |
| `new_report_context(settings, http=client)` | A plain dict: `{"http": client}` |
| `html_table(headers=..., rows=...)` | The same, or `html_table(headers, rows)` |
| A failed fetch shows an error in its row | A failed fetch raises `FetchError` |
| `AUTO_HTML_REPORT_*` environment variables | Arguments to `build_report` |

Event discovery and processing, the event stores, S3 output and the built-in semiconductor blocks are gone. They remain
at the [`v0.1.0` tag](https://github.com/hanku4u/auto-html-report/tree/v0.1.0).

## Development

```bash
uv sync --all-extras
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest
```

[`design.md`](https://github.com/hanku4u/auto-html-report/blob/main/src/auto_html_report/docs/design.md) explains how it works and why,
and [`docs/publishing.md`](https://github.com/hanku4u/auto-html-report/blob/main/docs/publishing.md) covers releases.
The tests run every Python example in this README, so keep them working.

## License

Apache-2.0
