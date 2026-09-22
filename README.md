# eib

Minimal CLI that assembles an mkosi build workspace from a list of profile
and image directories, then runs mkosi to produce an OCI image.

Given a YAML config listing profile directories and image directories,
eib:

1. **assemble** &mdash; creates the workspace, then copies each listed
   profile directory into `<workspace>/mkosi.profiles/<name>` and each
   listed image directory into `<workspace>/mkosi.images/<name>`.
2. **build** &mdash; runs `mkosi ... build` with `Format=oci` in that
   workspace, producing an OCI image.

## Requirements

`mkosi` must be installed and on `PATH`, and PyYAML (`pip install pyyaml`)
to parse the config.

## Config

See `eib.yaml.example`:

```yaml
profiles:
  - trixie-bootc              # looked up under --mkosi-dir/profiles
  - common
  - name: vendor-extra         # or specify this entry's own source dir
    dir: /srv/shared-mkosi-profiles/vendor-extra
images:
  - initrd
  - system
```

| Key | Meaning |
|---|---|
| `profiles` | list copied to `<workspace>/mkosi.profiles/<name>` (see below) |
| `images` | list copied to `<workspace>/mkosi.images/<name>` (see below) |

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

## Pointing at a directory of profiles and images

`--mkosi-dir DIR` sets the default source for any plain-name `profiles`/
`images` entry: `DIR/profiles/<name>` and `DIR/images/<name>`.

- `DIR/profiles` is **required** when `--mkosi-dir` is given &mdash; eib
  errors out immediately if it's missing.
- `DIR/images` is **optional** &mdash; if it's missing, eib just proceeds
  without a default for image entries (any `images` entries would then
  need their own `dir`).

```
./eib.py --config eib.yaml --mkosi-dir /srv/mkosi-sources
```

`examples/mkosi-dir/profiles/bianca/` is a bundled placeholder profile
(eLxr Bianca) to try this against, paired with `eib.yaml.example`:

```
./eib.py --config eib.yaml.example --mkosi-dir examples/mkosi-dir --dry-run
```

## Usage

```
./eib.py --config CONFIG.yaml [options]
```

```
./eib.py --config eib.yaml
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
  stale copies; anything else already in the workspace is left alone.
- Requires root/`--privileged` in practice: mkosi needs loop devices and
  sandboxing.
- The OCI artifact is auto-detected in the output directory after the
  build (an OCI-layout directory or an oci-archive tarball, whichever
  mkosi just produced).

## Running in a container

`Containerfile` bundles mkosi and PyYAML so you don't need them on the
host, and `justfile` wraps the exact `podman run` invocation:

```
just image                       # build the eib container image
just run --config eib.yaml       # assemble the workspace and build
just run --config eib.yaml --dry-run   # see what would happen first
just clean                       # remove the built image
```

The container needs `--privileged` and the project directory bind-mounted
at `/workspace` (the `run` recipe does this).
