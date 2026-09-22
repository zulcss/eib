from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("eib")


class EibError(RuntimeError):
    """A pipeline step failed; message is already user-facing."""


def run(cmd: list[str], *, dry_run: bool, cwd: Path | None = None) -> None:
    """Run a command, streaming its output, or just print it for --dry-run."""
    printable = " ".join(shlex.quote(c) for c in cmd)
    log.info("+ %s%s", printable, f"   (cwd={cwd})" if cwd else "")
    if dry_run:
        return
    try:
        result = subprocess.run(cmd, cwd=cwd)
    except FileNotFoundError as exc:
        raise EibError(
            f"{cmd[0]}: command not found (is it installed and on PATH?)"
        ) from exc
    if result.returncode != 0:
        raise EibError(f"command failed (exit {result.returncode}): {printable}")


def resolve_entries(
    entries: list, default_dir: Path | None, base_dir: Path, kind: str
) -> list[tuple[str, Path]]:
    """Resolve a 'profiles'/'images' list into (dest_name, source_path) pairs.

    Each entry may be:
      - a plain string naming a profile/image. If default_dir (derived
        from --mkosi-dir) is set, the source is default_dir/name.
        Otherwise the string is itself a path (resolved against base_dir)
        and the source's own directory name is used as the dest name.
      - a mapping {name: ..., dir: ...} giving an explicit source
        directory for that one entry (resolved against base_dir),
        overriding default_dir; 'name' is required and is the dest name.
    """
    resolved: list[tuple[str, Path]] = []
    for entry in entries:
        if isinstance(entry, str):
            if default_dir is not None:
                name = entry
                source = default_dir / name
            else:
                source = Path(entry)
                if not source.is_absolute():
                    source = base_dir / source
                name = source.name
        elif isinstance(entry, dict):
            name = entry.get("name")
            if not name:
                raise EibError(f"{kind} entry missing required 'name': {entry}")
            entry_dir = entry.get("dir")
            if entry_dir:
                source = Path(entry_dir)
                if not source.is_absolute():
                    source = base_dir / source
            elif default_dir is not None:
                source = default_dir / name
            else:
                raise EibError(
                    f"{kind} entry {name!r} has no 'dir', and no "
                    f"--mkosi-dir/{kind} default is available"
                )
        else:
            raise EibError(f"{kind} entry must be a string or mapping: {entry!r}")
        resolved.append((name, source))
    return resolved


def load_config(path: Path) -> dict:
    """Load and validate the YAML workspace config.

    Expected shape (see eib.yaml.example):

        profiles:
          - trixie-bootc             # looked up under --mkosi-dir/profiles
          - common
          - name: extra              # or an explicit per-entry source dir
            dir: /elsewhere/extra
        images:
          - initrd
          - system

    Relative paths (any per-entry 'dir', or a plain entry used as a path
    when --mkosi-dir isn't given) are resolved relative to the config
    file's own directory. The raw 'profiles'/'images' lists are returned
    unresolved; main() resolves them once --mkosi-dir is known.
    """
    try:
        import yaml
    except ImportError as exc:
        raise EibError(
            "reading a YAML config requires PyYAML; install it with "
            "'pip install pyyaml' (or apt install python3-yaml)"
        ) from exc

    if not path.is_file():
        raise EibError(f"config file not found: {path}")

    with path.open() as f:
        data = yaml.safe_load(f) or {}

    base_dir = path.parent

    profiles = data.get("profiles") or []
    images = data.get("images") or []
    if not isinstance(profiles, list):
        raise EibError(f"{path}: 'profiles' must be a list")
    if not isinstance(images, list):
        raise EibError(f"{path}: 'images' must be a list")

    return {
        "profiles_raw": profiles,
        "images_raw": images,
        "base_dir": base_dir,
    }


def copy_into(src: Path, dest_dir: Path, name: str, dry_run: bool) -> None:
    """Copy src (file or directory) into dest_dir/name, replacing any
    existing entry of that name."""
    if not src.exists():
        raise EibError(f"source path does not exist: {src}")

    dest = dest_dir / name
    log.info("copying %s -> %s", src, dest)
    if dry_run:
        return

    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest.is_dir() and not dest.is_symlink():
        shutil.rmtree(dest)
    elif dest.exists():
        dest.unlink()

    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)


def prepare_workspace(
    workspace: Path,
    profiles: list[tuple[str, Path]],
    images: list[tuple[str, Path]],
    dry_run: bool,
) -> Path:
    """Assemble the mkosi workspace by copying the resolved profiles/images
    into it."""
    log.info("creating workspace %s", workspace)
    if not dry_run:
        workspace.mkdir(parents=True, exist_ok=True)

    profiles_dest = workspace / "mkosi.profiles"
    images_dest = workspace / "mkosi.images"

    for name, src in profiles:
        copy_into(src, profiles_dest, name, dry_run)
    for name, src in images:
        copy_into(src, images_dest, name, dry_run)

    return workspace


def step_build(
    workspace: Path, output_dir: Path, dry_run: bool, debug: bool = False
) -> None:
    """Run mkosi in the workspace to produce an OCI image."""
    output_dir.mkdir(parents=True, exist_ok=True) if not dry_run else None
    cmd = ["mkosi", "--directory", str(workspace)]
    if debug:
        cmd.append("--debug")
    cmd += ["--output-dir", str(output_dir), "--format", "oci", "build"]
    run(cmd, dry_run=dry_run)


def find_oci_output(output_dir: Path, since: float) -> Path:
    """Locate the OCI image mkosi just produced in output_dir.

    mkosi's exact output name depends on the project's Output= setting, so
    this looks for whatever showed up (or changed) during the build: an
    OCI-layout directory (has index.json + oci-layout) or an oci-archive
    tarball, preferring the most recently modified match.
    """
    candidates: list[Path] = []
    if not output_dir.exists():
        raise EibError(f"output directory does not exist: {output_dir}")

    for entry in output_dir.iterdir():
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < since:
            continue
        if (
            entry.is_dir()
            and (entry / "index.json").exists()
            and (entry / "oci-layout").exists()
        ):
            candidates.append(entry)
        elif entry.is_file() and entry.suffix in (".tar", ".oci"):
            candidates.append(entry)

    if not candidates:
        raise EibError(
            f"no OCI image found in {output_dir} after build; "
            "check the project's Format=/Output= mkosi settings"
        )

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]
