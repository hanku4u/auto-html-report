# Publishing

Releases are tag-driven. `release.yml` runs on a pushed `v*` tag:

```
tag v* ─► verify ─► checks ─► build ─► check wheel ─► TestPyPI ─► (approval) ─► PyPI ─► GitHub Release
```

1. **verify** fails unless the tag equals `v` plus the `pyproject.toml` version, and the commit is on `main`.
2. **checks** reuses `ci.yml`, so a tag can't release what CI would reject.
3. **build** runs `uv build` once. Every later job uses those files, so what was tested is what ships.
4. **check wheel** runs `scripts/check_install.sh` on that wheel.
5. **TestPyPI** publishes in the `TestPyPI` environment.
6. **PyPI** publishes in the `PyPI` environment and waits for approval. Any version other than plain digits and
   dots, such as `0.2.0rc1`, stops after TestPyPI.
7. **GitHub Release** is created with the `dist/` files, and notes from the version's section of `CHANGELOG.md`.

Publishing uses trusted publishing (OIDC), so no PyPI token exists anywhere. Each index has a publisher registered
against the repository name, `release.yml` and the environment name. Don't rename any of those without updating
the publishers. A published version can't be replaced: yank a bad release and cut a new version.

## Cutting a release

1. In a PR, run `uv version <version>`. For a final release, also rename `[Unreleased]` in `CHANGELOG.md` to
   `[<version>]` and start a new empty `[Unreleased]`. The workflow refuses a final release with no notes.
2. Merge it, then tag that commit on `main` and push: `git tag v<version> && git push origin v<version>`.
3. For a final release, approve the `PyPI` deployment once TestPyPI has succeeded.

A failed run can be re-run: `--check-url` makes the publish jobs skip files already on the index.

## Dependency policy

Runtime dependencies are ranges whose floors are tested by CI's `lowest-direct` job, development tools are exact
pins, and `uv.lock` pins everything for this repository. Run `uv lock` in the same commit as any change.
