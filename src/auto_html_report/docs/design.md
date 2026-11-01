# Design

The package does one job: turn a list of blocks into one self-contained HTML file, fetching data each block needs
without repeating a call. Keep it that small. Event discovery and processing were removed and can be restored from
the `v0.1.0` tag if they're needed again.

## How a report is built

`build_report` in `report.py` is the whole pipeline:

1. **Check.** A gather function's parameter names are its requirements. Each is filled from the block's constructor
   arguments, then the context. Every missing key is reported together in `MissingContextError`.
2. **Plan.** List each block's `(function, arguments)` calls and drop duplicates.
3. **Fetch.** Run the remaining calls in a thread pool, with retries.
4. **Render.** Call each block's `render` in order, on the calling thread.
5. **Return** a `Report`, which `save()` can write.

## Decisions, and why

- **Gather functions live outside blocks.** Python can't tell that two different methods make the same call, so a
  duplicate has to mean the same function object with equal arguments. Two separate functions making the same API
  call both run, so each one should be defined once and imported wherever it's needed.
- **`Block` is the public way to create blocks.** Subclassing it is the only way, so there's one pattern to learn. A
  `gather` method taking `self` is refused, because it couldn't be shared.
- **The context is a plain dict.** Reports need very different inputs, and a function's signature already says what
  it needs. Values and clients both go in it.
- **Fetching fails fast.** A report with missing data isn't worth producing. Other calls' retries stop at once, but
  a call already running can't be interrupted, so gather functions must set their own timeouts.
- **Rendering is contained.** One broken display shouldn't lose the data already fetched, so a `render` failure
  becomes an error in that row.
- **Errors name keys, never values,** because the context can hold credentials.
- **Blocks sharing a call share the result object,** so `render` must not modify it. Copying large responses per
  block is a poor default.
- **Rendering is sequential,** which is why charts need no lock. Use `matplotlib.figure.Figure` rather than `pyplot`,
  which has process-wide state.
- **Autoescaping is on,** and `row.html | safe` in the template is the only exception. A block owns escaping what it
  returns, which `escape` and `html_table` make easy.
- **Reports are self-contained:** no JavaScript and no external requests. `tests/test_rendering.py` audits this.
