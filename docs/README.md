# auto_inform specification documents

This directory holds the specification set for `auto_inform`. It is spec-driven: no feature or
nontrivial refactor is implemented before the relevant document here has been reviewed and moved
to `active`.

Start with [01-overview.md](01-overview.md) if you are new, or jump to the document for the area
you are working in.

This set specifies **auto_inform v0.1** — package version `0.1.0`, see
[08-packaging.md](08-packaging.md).

## Conventions

### Naming and status

- Files are named `NN-short-description.md`. `NN` is a two-digit, sequential, unique number. The
  next unused number for a new document is **13**.
- [README.md](README.md) is the index: it is deliberately unnumbered and carries no frontmatter.
  The remaining rules in this section apply to numbered documents.
- Every numbered document opens with YAML frontmatter:

  ```yaml
  ---
  status: proposed          # proposed | active | superseded
  description: One sentence covering what the document is about.
  replaces: docs/SPEC.md §4.2-4.4, §5    # optional; provenance only
  ---
  ```

- `description` is **required**. It is what the index below is generated from, and what a reader
  scanning `docs/` uses to decide whether to open the file.
- `status` is required in practice. A missing `status` defaults to `proposed`, but do not rely on
  that — an explicit status is how a reviewer knows whether a document has been ratified.
- `status: superseded` documents must name their replacements in a `replacement:` field, and those
  documents must exist.
- `replaces` is a local addition to the convention: it records which section of the original
  single-document specification this file was split from, so that a reader can trace provenance.
  It carries no normative weight.

### Open questions

- A document in `proposed` state may carry unanswered questions in an **Open questions** section.
  That is expected, not a defect.
- Once a document moves to `active`, every question in it must be either resolved or explicitly
  marked as accepted-and-deferred with a named default.
- Questions that get answered are reworded as decisions in
  [10-decision-log.md](10-decision-log.md) rather than left dangling. The log is the record of
  *why*; the affected document is the record of *what*.
- The consolidated list of currently open questions is in [§ Open questions index](#open-questions-index)
  at the bottom of this file.

### Dev plans

- Any document whose specification exceeds 250 lines requires a **Dev plan** section: either its own
  staged plan, or a short section naming the stages of
  [11-implementation-plan.md](11-implementation-plan.md) that implement it. Duplicating the staging
  across documents is not useful, so the second form is normal here.
- The rule exists so that no document is large enough to be un-reviewable and un-sequencable at once.
  It is a prompt to write a plan, not a filing requirement to be argued past.
- **Exempt:** append-only reference documents and the plan itself. Today that is
  [10-decision-log.md](10-decision-log.md) and [11-implementation-plan.md](11-implementation-plan.md) — a
  decision log grows without bound and has nothing to stage, and requiring the implementation plan to
  contain a plan is circular. Once the decision log passes a few hundred lines, split it into
  `docs/adr/NNNN-short-title.md` files rather than bolting a plan onto it.

### Dependencies

- This project uses [uv](https://docs.astral.sh/uv/) for dependency management. Direct dependencies
  are declared in `pyproject.toml` with exact `==` pins; the resolved transitive closure lives in the
  committed `uv.lock`. There is no `requirements.txt`.
- Documents that introduce or alter a third-party dependency must document it in a
  **Dependency changes** section, listing the exact pin, consistent with `pyproject.toml`. Floating
  version ranges are not acceptable in this project; see [08-packaging.md](08-packaging.md).

### Acceptance criteria

- An **Acceptance criteria** section is optional. When it is absent, criteria are inferred from the
  document's **Testing** section — verify that section is strong enough to carry that weight before
  omitting the criteria.

## The document set

**Ratified 2026-10-18.** Documents 01 to 11 are `active`: the v1 implementation they describe is
complete, and every acceptance criterion in [09 §3](09-testing-and-acceptance.md#3-acceptance-criteria)
has a test. `active` is a statement about the specification and the code agreeing, not a claim of
production use — the HTTP client is still the stub described in [07 §2](07-configuration-and-logging.md#2-the-http-stub).
Document 12 is `proposed`: it describes the planned move to PyPI, not the code as it stands.

| # | Document | Status | Description |
| --- | --- | --- | --- |
| — | [README.md](README.md) | — | This index, plus the conventions above. |
| 01 | [01-overview.md](01-overview.md) | active | What `auto_inform` is, v1 scope and non-goals, shared vocabulary, and the architectural shape of the framework. |
| 02 | [02-block-authoring.md](02-block-authoring.md) | active | The `Block` contract, its injected context, the HTTP port and its error mapping, and the per-block execution rules. |
| 03 | [03-report-api.md](03-report-api.md) | active | Report data types, the injected contexts, the three public entry points, the block and report registries, and how predefined and dynamic reports are expressed. |
| 04 | [04-events-and-stores.md](04-events-and-stores.md) | active | The `Event` value object, the `EventStore` port, the shared schema, and the SQLite and PostgreSQL implementations. |
| 05 | [05-discovery-and-processing.md](05-discovery-and-processing.md) | active | The two scheduled jobs: the `Discoverer` port, `run_discovery`, `run_processing`, throughput policy, and CI exit codes. |
| 06 | [06-output-html.md](06-output-html.md) | active | Output writers, file naming, the HTML document contract, the escaping trust boundary, and figure embedding. |
| 07 | [07-configuration-and-logging.md](07-configuration-and-logging.md) | active | Environment variables and their `Settings` attributes, the HTTP stub, and the structured logging contract. |
| 08 | [08-packaging.md](08-packaging.md) | active | Distribution name, exact dependency pins, optional extras, package data, and install instructions. |
| 09 | [09-testing-and-acceptance.md](09-testing-and-acceptance.md) | active | The testing strategy per layer, hermeticity rules, and the v1 acceptance criteria. |
| 10 | [10-decision-log.md](10-decision-log.md) | active | ADR-0001 to ADR-0035: each ratified decision, its context, and the alternatives rejected. |
| 11 | [11-implementation-plan.md](11-implementation-plan.md) | active | The staged build order for v1, with review boundaries and a definition of done per stage. |
| 12 | [12-publishing.md](12-publishing.md) | proposed | The move to the public PyPI package `auto-html-report`: identity, dependency policy, public-surface changes, and the release pipeline. |

## Reading order by role

| Role | Read in this order |
| --- | --- |
| Implementing the framework | 01 → 03 → 02 → 04 → 05 → 06 → 07 → 08 → 09 → 11 |
| Writing a new block | 02 → 06 → 07 |
| Writing or scheduling a job | 05 → 07 → 04 |
| Reviewing a design change | 10 first — the alternatives that were already rejected are the ones most likely to be proposed again |

## Open questions index

Every unanswered question across the spec set, with where it lives. Each has a named v1 default that the
code implements, so each is an accepted deferral to v0.2 rather than a gap in v1.

| Where | Question |
| --- | --- |
| [02 § Open questions](02-block-authoring.md#open-questions) | Should a block be able to declare per-attempt timeouts that differ from its overall budget? |
| [03 § Open questions](03-report-api.md#open-questions) | Does an ad-hoc report need its own event-store identity, or is it always written and forgotten? |
| [04 § Open questions](04-events-and-stores.md#open-questions) | Is a single shared events table sufficient for all report families, or do some families need isolation? |
| [05 § Open questions](05-discovery-and-processing.md#open-questions) | Should the processing job emit a machine-readable run manifest in addition to the log? |
| [06 § Open questions](06-output-html.md#open-questions) | Can a report ever be larger than a single file is sensible for? |
| [06 § Open questions](06-output-html.md#open-questions) | S3 object lifecycle, signed URLs, or public-read for browser viewing? |
| [07 § Open questions](07-configuration-and-logging.md#open-questions) | Should S3 server-side encryption default to on rather than opt-in? |

Resolved questions from the original specification, with the decisions that replaced them, are
recorded in [10-decision-log.md](10-decision-log.md) — in particular ADR-0019 (event attempt
retirement), ADR-0020 (report-level throughput), and the fixes to the HTTP error mapping in
[02-block-authoring.md](02-block-authoring.md).
