from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
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
    entries: list[tuple[object, Path]], default_dir: Path | None, kind: str
) -> list[tuple[str, Path]]:
    """Resolve a 'profiles'/'images' list into (dest_name, source_path) pairs.

    Each item is (entry, base_dir): base_dir is the directory of whichever
    config file (the top-level one, or one pulled in via 'include')
    contributed that entry, and anchors any relative path in it.

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
    for entry, base_dir in entries:
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


IMAGE_KEYS = ("name", "version", "format")

# Output formats accepted by mkosi's --format.
MKOSI_FORMATS = (
    "addon",
    "confext",
    "cpio",
    "directory",
    "disk",
    "esp",
    "none",
    "oci",
    "portable",
    "sysext",
    "tar",
    "uki",
)
DEFAULT_IMAGE_FORMAT = "disk"

CONTAINER_KEYS = ("tag", "disk", "size", "filesystem")
DEFAULT_CONTAINER_FILESYSTEM = "ext4"


def _validate_section(
    path: Path, section: str, data: object, keys: tuple[str, ...]
) -> dict:
    """Check that a config file's 'image'/'container' mapping has only
    known keys, each with a string (or integer) value."""
    if not isinstance(data, dict):
        raise EibError(f"{path}: '{section}' must be a mapping")
    for key, value in data.items():
        if key not in keys:
            raise EibError(
                f"{path}: unknown '{section}' key {key!r} "
                f"(expected one of: {', '.join(keys)})"
            )
        # YAML reads e.g. 1.10 as the float 1.1, so only accept values
        # that round-trip exactly; anything else must be quoted.
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise EibError(
                f"{path}: '{section}.{key}' must be a string "
                f"(quote it in the YAML): {value!r}"
            )
    return data


def _load_config_file(path: Path, seen: frozenset[Path]) -> tuple[
    list[tuple[object, Path]],
    list[tuple[object, Path]],
    dict[str, str],
    dict[str, str],
]:
    """Recursively load a config file together with any files it 'include's.

    Returns the combined 'profiles'/'images' entries as (entry, base_dir)
    pairs, base_dir being the directory of whichever file (this one, or an
    included one) contributed that entry, plus the merged 'image' and
    'container' settings.
    Included files are loaded depth-first in the order listed, and their
    entries are placed *before* this file's own, so a file's own
    profiles/images are appended after (and so take precedence when a
    name collides, since later copies win). Likewise, each 'image' field
    and 'container' field this file sets overrides the same field from
    any included file.
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

    resolved_path = path.resolve()
    if resolved_path in seen:
        raise EibError(f"circular 'include' detected at {path}")
    seen = seen | {resolved_path}

    with path.open() as f:
        data = yaml.safe_load(f) or {}

    base_dir = path.parent

    own_profiles = data.get("profiles") or []
    own_images = data.get("images") or []
    includes = data.get("include") or []
    own_image = _validate_section(path, "image", data.get("image") or {}, IMAGE_KEYS)
    own_container = _validate_section(
        path, "container", data.get("container") or {}, CONTAINER_KEYS
    )
    if not isinstance(own_profiles, list):
        raise EibError(f"{path}: 'profiles' must be a list")
    if not isinstance(own_images, list):
        raise EibError(f"{path}: 'images' must be a list")
    if not isinstance(includes, list):
        raise EibError(f"{path}: 'include' must be a list")
    if "format" in own_image and own_image["format"] not in MKOSI_FORMATS:
        raise EibError(
            f"{path}: unsupported 'image.format' {own_image['format']!r} "
            f"(expected one of: {', '.join(MKOSI_FORMATS)})"
        )

    profiles = [(entry, base_dir) for entry in own_profiles]
    images = [(entry, base_dir) for entry in own_images]
    image: dict[str, str] = {}
    container: dict[str, str] = {}

    for include in includes:
        if not isinstance(include, str):
            raise EibError(f"{path}: 'include' entries must be strings: {include!r}")
        include_path = Path(include)
        if not include_path.is_absolute():
            include_path = base_dir / include_path
        (
            included_profiles,
            included_images,
            included_image,
            included_container,
        ) = _load_config_file(include_path, seen)
        profiles = included_profiles + profiles
        images = included_images + images
        image.update(included_image)
        container.update(included_container)

    image.update({key: str(value) for key, value in own_image.items()})
    container.update({key: str(value) for key, value in own_container.items()})
    if "disk" in own_container:
        # A relative disk image path is anchored at this file's directory,
        # like any other path in the config.
        container["disk"] = str(base_dir / container["disk"])

    return profiles, images, image, container


def load_config(path: Path) -> dict:
    """Load and validate the YAML workspace config.

    Expected shape (see config.yml):

        include:
          - common.yml                # pull in more profiles/images
        profiles:
          - trixie-bootc               # looked up under --mkosi-dir/profiles
          - common
          - name: extra                # or an explicit per-entry source dir
            dir: /elsewhere/extra
        images:
          - initrd
          - system
        image:
          name: elxr-bianca            # passed to mkosi as --image-id
          version: "1.0"               # passed to mkosi as --image-version
                                       # (omit to use a build timestamp)
          format: disk                 # passed to mkosi as --format
                                       # (default: disk)
        container:
          tag: elxr-bianca:latest      # container image tag
          disk: elxr-bianca.raw        # disk image path
          size: 10G                    # disk image size
          filesystem: ext4             # root filesystem (default: ext4)

    'include' lists other config files (same shape, and itself may
    include further files) whose 'profiles'/'images' are merged in ahead
    of this file's own entries. Relative paths (an 'include' entry, any
    per-entry 'dir', or a plain profiles/images entry used as a path when
    --mkosi-dir isn't given) are resolved relative to the directory of
    whichever config file they appear in, not necessarily the top-level
    one. The raw 'profiles'/'images' lists are returned unresolved; main()
    resolves them once --mkosi-dir is known. 'image' (all keys optional)
    names, versions and sets the output format of the built image; a field
    set in the including file overrides the same field from an included
    one. 'container' (its 'tag', 'disk', 'size' and 'filesystem' optional,
    the filesystem defaulting to ext4) is merged the same way; a relative 'container.disk' path is resolved against the
    directory of the config file that sets it.
    """
    profiles, images, image, container = _load_config_file(path, seen=frozenset())

    return {
        "profiles_raw": profiles,
        "images_raw": images,
        "image_name": image.get("name"),
        "image_version": image.get("version"),
        "image_format": image.get("format", DEFAULT_IMAGE_FORMAT),
        "container_tag": container.get("tag"),
        "container_disk": (Path(container["disk"]) if "disk" in container else None),
        "container_size": container.get("size"),
        "container_filesystem": container.get(
            "filesystem", DEFAULT_CONTAINER_FILESYSTEM
        ),
        "base_dir": path.parent,
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


MKOSI_CONF_TEMPLATE = """\
# Top-level mkosi config for this workspace.
#
# Settings here apply to every image under mkosi.images/, layered
# beneath whatever the selected profile(s) (mkosi.profiles/) provide.
# See mkosi's docs for the full list of settings:
# https://github.com/systemd/mkosi/blob/main/mkosi/resources/mkosi.md
"""


def write_mkosi_conf(workspace: Path, dry_run: bool) -> None:
    """Seed workspace/mkosi.conf with a starter template, unless a config
    already exists there (so re-running never clobbers local edits)."""
    conf = workspace / "mkosi.conf"
    if conf.exists():
        return
    log.info("writing %s", conf)
    if dry_run:
        return
    conf.write_text(MKOSI_CONF_TEMPLATE)


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

    write_mkosi_conf(workspace, dry_run)

    profiles_dest = workspace / "mkosi.profiles"
    images_dest = workspace / "mkosi.images"

    for name, src in profiles:
        copy_into(src, profiles_dest, name, dry_run)
    for name, src in images:
        copy_into(src, images_dest, name, dry_run)

    return workspace


def default_image_version() -> str:
    """Timestamp version (UTC, YYYYMMDDHHMMSS) for builds whose config
    doesn't set 'image.version'. All digits, so later builds always
    compare as newer versions."""
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def step_build(
    workspace: Path,
    output_dir: Path,
    dry_run: bool,
    debug: bool = False,
    profiles: list[str] | None = None,
    image_name: str | None = None,
    image_version: str | None = None,
    image_format: str = DEFAULT_IMAGE_FORMAT,
) -> None:
    """Run mkosi in the workspace to produce an image in image_format
    (one of MKOSI_FORMATS; a disk image by default).

    Without an explicit image_version, a UTC build timestamp is used
    instead (see default_image_version), so each build gets a new,
    increasing version.
    """
    output_dir.mkdir(parents=True, exist_ok=True) if not dry_run else None
    cmd = ["mkosi", "--directory", str(workspace)]
    if debug:
        cmd.append("--debug")
    for name in profiles or []:
        cmd += ["--profile", name]
    if image_name:
        cmd += ["--image-id", image_name]
    cmd += ["--image-version", image_version or default_image_version()]
    cmd += ["--output-dir", str(output_dir), "--format", image_format, "build"]
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


def local_image_ref(tag: str) -> str:
    """Qualify a tag without a registry (e.g. elxr-bianca:latest) as
    localhost/<tag>, matching what 'podman build' does; skopeo and podman
    would otherwise look it up under docker.io/library/."""
    registry = tag.split("/")[0]
    if "/" not in tag or not (
        "." in registry or ":" in registry or registry == "localhost"
    ):
        return f"localhost/{tag}"
    return tag


def step_import(oci_path: Path, tag: str, dry_run: bool) -> None:
    """Copy an OCI image (an OCI-layout directory or an oci-archive
    tarball, as found by find_oci_output) into local container storage
    as tag, so bootc can install it.

    A tag without a registry is stored under localhost/ (see
    local_image_ref).
    """
    tag = local_image_ref(tag)
    transport = "oci" if oci_path.is_dir() else "oci-archive"
    run(
        ["skopeo", "copy", f"{transport}:{oci_path}", f"containers-storage:{tag}"],
        dry_run=dry_run,
    )


def step_install(
    tag: str,
    disk: Path,
    size: str,
    dry_run: bool,
    filesystem: str = DEFAULT_CONTAINER_FILESYSTEM,
) -> None:
    """Install the bootc container image tag (from local container
    storage) onto a new disk image file at disk, of the given size (any
    size 'truncate -s' accepts, e.g. 10G), with a root filesystem of the
    given type (passed to bootc as --filesystem, e.g. ext4).

    The file is created sparse with truncate, then the host's bootc
    writes the image to it via 'bootc install to-disk --via-loopback',
    reading the image from containers-storage with --source-imgref. An
    existing disk is never overwritten.
    """
    if disk.exists():
        raise EibError(f"disk image already exists: {disk} (remove it first)")
    disk = disk.absolute()
    log.info("creating directory %s", disk.parent)
    if not dry_run:
        disk.parent.mkdir(parents=True, exist_ok=True)
    run(["truncate", "-s", size, str(disk)], dry_run=dry_run)
    run(
        [
            "bootc",
            "install",
            "to-disk",
            "--source-imgref",
            f"containers-storage:{local_image_ref(tag)}",
            "--composefs-backend",
            "--wipe",
            "--filesystem",
            filesystem,
            "--via-loopback",
            str(disk),
        ],
        dry_run=dry_run,
    )


# Platforms 'eib image export' converts to: platform -> (qemu-img output
# format, file extension).
EXPORT_PLATFORMS = {
    "qcow2": ("qcow2", ".qcow2"),
    "vmdk": ("vmdk", ".vmdk"),
    "vhdx": ("vhdx", ".vhdx"),
    "vhd": ("vpc", ".vhd"),
    "vdi": ("vdi", ".vdi"),
}
DEFAULT_EXPORT_PLATFORM = "qcow2"


def find_disk_output(output_dir: Path) -> Path:
    """Locate the most recently built raw disk image in output_dir.

    mkosi names it after the image id and version (e.g.
    elxr-bianca_1.0.raw) and adds an unversioned symlink to it; symlinks
    are skipped so the real file is returned.
    """
    if not output_dir.exists():
        raise EibError(f"output directory does not exist: {output_dir}")

    candidates = [
        entry
        for entry in output_dir.iterdir()
        if entry.suffix == ".raw" and entry.is_file() and not entry.is_symlink()
    ]
    if not candidates:
        raise EibError(
            f"no disk image (*.raw) found in {output_dir}; "
            "build one with 'image.format: disk' first"
        )

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def export_path(disk: Path, platform: str) -> Path:
    """Default path of disk exported to platform: disk with its extension
    swapped for the platform's (e.g. elxr-bianca.raw -> elxr-bianca.qcow2)."""
    return disk.with_suffix(EXPORT_PLATFORMS[platform][1])


def step_export(disk: Path, output: Path, platform: str, dry_run: bool) -> None:
    """Convert the raw disk image disk to platform's image format (one of
    EXPORT_PLATFORMS) at output, with 'qemu-img convert'. An existing
    output is never overwritten.
    """
    if platform not in EXPORT_PLATFORMS:
        raise EibError(
            f"unsupported platform {platform!r} "
            f"(expected one of: {', '.join(EXPORT_PLATFORMS)})"
        )
    if not disk.is_file():
        raise EibError(f"disk image not found: {disk}")
    if output.exists():
        raise EibError(f"exported image already exists: {output} (remove it first)")
    log.info("creating directory %s", output.parent)
    if not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "qemu-img",
            "convert",
            "-f",
            "raw",
            "-O",
            EXPORT_PLATFORMS[platform][0],
            str(disk),
            str(output),
        ],
        dry_run=dry_run,
    )
