# eib

Minimal CLI that assembles an mkosi build workspace from a list of profile
and image directories, then runs mkosi to produce an image (a disk image by default).

Given a YAML config listing profile directories and image directories,
eib:

1. **assemble** &mdash; creates the workspace, seeds it with a starter
   `<workspace>/mkosi.conf` (if one isn't already there), then copies
   each listed profile directory into `<workspace>/mkosi.profiles/<name>`
   and each listed image directory into `<workspace>/mkosi.images/<name>`.
2. **build** &mdash; runs `mkosi ... build` in that workspace,
   producing a disk image by default (see `image.format`). Each listed profile name is also
   passed to mkosi as `--profile <name>`, so the profile's
   `mkosi.profiles/<name>/` content is actually activated rather than
   just being present on disk. The configured `image.name` and
   `image.version`, if set, are passed as `--image-id` and
   `--image-version`. Without an `image.version`, the UTC build time
   (`YYYYMMDDHHMMSS`, e.g. `20260922164500`) is used as the version.
   `image.format` (default `disk`) is passed as `--format`.

## Requirements

`mkosi` must be installed and on `PATH`. eib itself needs PyYAML to parse
the config; either install it directly (`pip install pyyaml`), or install
eib as a package, which pulls it in as a dependency and gives you an `eib`
command:

```
pip install .
```

## Config

See `config.yml`:

```yaml
profiles:
  - trixie-bootc              # looked up under --mkosi-dir/profiles
  - common
  - name: vendor-extra         # or specify this entry's own source dir
    dir: /srv/shared-mkosi-profiles/vendor-extra
images:
  - initrd
  - system
image:
  name: elxr-bianca            # mkosi --image-id
  version: "1.0"               # mkosi --image-version
  format: disk                 # mkosi --format (default: disk)
```

| Key | Meaning |
|---|---|
| `include` | list of other config files to merge in first (see below) |
| `profiles` | list copied to `<workspace>/mkosi.profiles/<name>` (see below) |
| `images` | list copied to `<workspace>/mkosi.images/<name>` (see below) |
| `image` | mapping with optional `name`, `version` and `format` of the built image, passed to mkosi as `--image-id`/`--image-version`/`--format` (no `version` &rarr; UTC build timestamp; no `format` &rarr; `disk`) |

`image.version` must be a string (or an integer): quote it, since YAML
reads an unquoted `1.10` as the number `1.1`. `image.format` must be one
of mkosi's output formats: `addon`, `confext`, `cpio`, `directory`,
`disk`, `esp`, `none`, `oci`, `portable`, `sysext`, `tar` or `uki`.
Unknown `image` keys are an error.

Each `profiles`/`images` entry is either:

- a **plain name**, e.g. `trixie-bootc` &mdash; its source directory is
  `--mkosi-dir/profiles/trixie-bootc` (or `--mkosi-dir/images/<name>`).
  Without `--mkosi-dir`, the string is instead treated as a path to copy
  directly, and its own directory name becomes the dest name.
- a **mapping** `{name: ..., dir: ...}` &mdash; copies from `dir`
  explicitly, overriding `--mkosi-dir` for that one entry. Useful when
  most profiles live under `--mkosi-dir` but a couple come from
  elsewhere.

All paths (any per-entry `dir`) are resolved relative to the config
file's own location, not the current directory.

## Splitting a config into multiple files

`include` pulls in the `profiles`/`images` of other config files, so
shared setup can be factored out of a config that's otherwise specific to
one image:

```yaml
# common.yml
profiles:
  - common
images:
  - initrd
```

```yaml
# config.yml
include:
  - common.yml
profiles:
  - trixie-bootc
```

Included files use the same shape as any other config, and may
themselves `include` further files (nested/transitive includes are
followed; an include cycle is an error). An included file's `profiles`/
`images` are merged in *before* the including file's own entries, so a
name listed in both is copied last (and wins) from the including file.
Each `include` path, like any relative `dir`, resolves against the
directory of the file it's written in &mdash; not the top-level config
&mdash; so an included file's own relative paths keep working regardless
of where it's included from.

`image` is merged field by field instead: an included file can set a
default `name`/`version`/`format`, and the including file overrides whichever
fields it sets itself.

## Pointing at a directory of profiles and images

`--mkosi-dir DIR` sets the default source for any plain-name `profiles`/
`images` entry: `DIR/profiles/<name>` and `DIR/images/<name>`.

- `DIR/profiles` is **required** when `--mkosi-dir` is given &mdash; eib
  errors out immediately if it's missing.
- `DIR/images` is **optional** &mdash; if it's missing, eib just proceeds
  without a default for image entries (any `images` entries would then
  need their own `dir`).

```
./eib.py --config config.yml --mkosi-dir /srv/mkosi-sources
```

`examples/mkosi-dir/profiles/bianca/` is a bundled placeholder profile
(eLxr Bianca) to try this against, paired with `config.yml`:

```
./eib.py --config config.yml --mkosi-dir examples/mkosi-dir --dry-run
```

## Usage

```
./eib.py --config CONFIG.yaml [options]
```

```
./eib.py --config config.yml
```

| Flag | Meaning |
|---|---|
| `--config FILE` | YAML config listing the profiles and images to assemble (required) |
| `--workspace DIR` | directory to assemble and build in (default: `/var/tmp/build/eib-workspace`) |
| `--mkosi-dir DIR` | directory with `profiles`/`images` subdirectories, default source for plain-name entries |
| `-O, --output-dir DIR` | mkosi output directory (default: `<workspace>/mkosi.output`) |
| `--dry-run` | print what would be copied and run, without doing either |
| `-v, --verbose` | verbose logging |
| `--debug` | pass `--debug` to mkosi for more detailed build output |

## Notes

- Re-running against an existing workspace replaces each named
  `mkosi.profiles/`/`mkosi.images/` entry rather than erroring or leaving
  stale copies; anything else already in the workspace is left alone,
  including an existing `mkosi.conf` (the template is only written once,
  so local edits to it survive a re-run).
- Requires root/`--privileged` in practice: mkosi needs loop devices and
  sandboxing.
- With `image.format: oci`, the OCI artifact is auto-detected in the
  output directory after the build (an OCI-layout directory or an oci-archive tarball, whichever
  mkosi just produced).

## Running in a container

`Containerfile` bundles mkosi and PyYAML so you don't need them on the
host, and `justfile` wraps the exact `podman run` invocation:

```
just image                       # build the eib container image
just run --config config.yml       # assemble the workspace and build
just run --config config.yml --dry-run   # see what would happen first
just clean                       # remove the built image
```

The container needs `--privileged` and the project directory bind-mounted
at `/workspace` (the `run` recipe does this).
