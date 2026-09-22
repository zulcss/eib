# AGENT.md

## What this is

`eib` ("elxr image builder") is a minimal Python CLI that assembles an
mkosi build workspace from a YAML config (lists of profile/image
directories to copy in), then runs `mkosi ... build` to produce an
image (a disk image by default, or any mkosi format via `image.format`). See `README.md` for full user-facing docs on the config format
and flags.

## Layout

- `src/eib/__init__.py` — all core logic (config loading, entry
  resolution, workspace assembly, running mkosi, locating the OCI
  output when building OCI, importing it with skopeo). Pure functions, no CLI concerns.
- `src/eib/cli/commands/shell.py` — root `click` group (`cli`).
- `src/eib/cli/commands/images.py` — `image build` and `image import`
  subcommands; wire CLI flags to the core functions in `eib/__init__.py`.
- `src/eib/__main__.py` — entry point (`eib = eib.__main__:main`).
- `config/config.yml` — example workspace config.
- `tests/test_core.py`, `tests/test_cli.py` — pytest suites for the two
  layers above.
- `Containerfile` — builds an eLxr-based image bundling mkosi + eib.
- `Justfile` — wraps `podman build`/`podman run` for the container.

## Commands

Run everything through `tox` (see `tox.ini`):

```
tox -e black      # check formatting
tox -e format     # reformat with black
tox -e pyflakes   # lint
tox -e test       # pytest
```

CI (`.gitlab-ci.yml`) runs `black`, `pyflakes`, and `test` on every push.
Run all three locally before considering a change done.

Invoke the CLI itself as `eib image build --config CONFIG [options]`
(after `pip install .`), or via the container: `just build-image image
build --config config/config.yml`.

## Conventions

- Formatted with `black`; linted with `pyflakes` — both enforced in CI,
  not just suggested.
- Core logic (`eib/__init__.py`) stays free of `click`/CLI concerns;
  CLI commands are thin wrappers that parse flags and call into it.
- Pipeline failures raise `EibError` with an already user-facing
  message; the CLI layer catches it and exits non-zero rather than
  printing a traceback.
- `--dry-run` must stay honest: every mutating step (`run`, `copy_into`,
  `prepare_workspace`, `step_build`, `step_import`) takes a `dry_run` flag and only
  logs what it would do.
