import logging
import time
from pathlib import Path

import click

from eib import (
    EibError,
    find_oci_output,
    load_config,
    log,
    prepare_workspace,
    resolve_entries,
    step_build,
    step_import,
    step_install,
)


@click.group(help="Image Commands")
def image():
    pass


@image.command(
    "build",
    context_settings={"help_option_names": ["-h", "--help"]},
    help="eib image builder",
)
@click.option(
    "--config",
    type=click.Path(path_type=Path),
    required=True,
    help="YAML config listing the profiles and images to assemble " "(see config.yml)",
)
@click.option(
    "--workspace",
    type=click.Path(path_type=Path),
    default=Path("/var/tmp/build/eib-workspace"),
    show_default=True,
    help="directory to assemble and build in",
)
@click.option(
    "--mkosi-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="directory with 'profiles' and 'images' subdirectories, used "
    "as the default source for plain-name profiles/images config "
    "entries. 'profiles' is required if this is given; 'images' is "
    "optional.",
)
@click.option(
    "-O",
    "--output-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="mkosi output directory (default: <workspace>/mkosi.output)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="print what would be copied and run, without doing either",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="verbose logging",
)
@click.option(
    "--debug",
    is_flag=True,
    help="pass --debug to mkosi for more detailed build output",
)
def image_create(
    config: Path,
    workspace: Path,
    mkosi_dir: Path | None,
    output_dir: Path | None,
    dry_run: bool,
    verbose: bool,
    debug: bool,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
    )

    try:
        cfg = load_config(config)

        profiles_dir = images_dir = None
        if mkosi_dir is not None:
            profiles_dir = mkosi_dir / "profiles"
            if not profiles_dir.is_dir():
                raise EibError(
                    f"'profiles' directory not found under --mkosi-dir: {profiles_dir}"
                )
            images_dir = mkosi_dir / "images"
            if not images_dir.is_dir():
                log.info(
                    "no 'images' directory under --mkosi-dir (%s); "
                    "plain-name image entries will need their own 'dir'",
                    images_dir,
                )
                images_dir = None

        profiles = resolve_entries(cfg["profiles_raw"], profiles_dir, "profiles")
        images = resolve_entries(cfg["images_raw"], images_dir, "images")

        workspace = prepare_workspace(workspace, profiles, images, dry_run)
        output_dir = output_dir or (workspace / "mkosi.output")

        started = time.time()
        step_build(
            workspace,
            output_dir,
            dry_run,
            debug=debug,
            profiles=[name for name, _ in profiles],
            image_name=cfg["image_name"],
            image_version=cfg["image_version"],
            image_format=cfg["image_format"],
        )

        if dry_run:
            log.info("dry run: nothing was built")
        elif cfg["image_format"] == "oci":
            oci_path = find_oci_output(output_dir, since=started)
            log.info("done: built %s", oci_path)
        else:
            log.info("done: built %s image in %s", cfg["image_format"], output_dir)

    except EibError as exc:
        log.error("error: %s", exc)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        log.error("interrupted")
        raise SystemExit(130) from None


@image.command(
    "import",
    context_settings={"help_option_names": ["-h", "--help"]},
    help="import the most recently built OCI image into local container "
    "storage, tagged with the config's container.tag",
)
@click.option(
    "--config",
    type=click.Path(path_type=Path),
    required=True,
    help="YAML config giving the container tag (see config.yml)",
)
@click.option(
    "--workspace",
    type=click.Path(path_type=Path),
    default=Path("/var/tmp/build/eib-workspace"),
    show_default=True,
    help="directory the image was built in",
)
@click.option(
    "-O",
    "--output-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="mkosi output directory (default: <workspace>/mkosi.output)",
)
@click.option(
    "--tag",
    default=None,
    help="tag to import the image as (overrides the config's container.tag)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="print the import command without running it",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="verbose logging",
)
def image_import(
    config: Path,
    workspace: Path,
    output_dir: Path | None,
    tag: str | None,
    dry_run: bool,
    verbose: bool,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
    )

    try:
        cfg = load_config(config)
        if cfg["image_format"] != "oci":
            raise EibError(
                f"only OCI images can be imported, but {config} has "
                f"'image.format' {cfg['image_format']!r} (set it to oci)"
            )
        tag = tag or cfg["container_tag"]
        if not tag:
            raise EibError(
                f"no container tag: set 'container.tag' in {config} or pass --tag"
            )

        output_dir = output_dir or (workspace / "mkosi.output")
        oci_path = find_oci_output(output_dir, since=0)
        step_import(oci_path, tag, dry_run)

        if dry_run:
            log.info("dry run: nothing was imported")
        else:
            log.info("done: imported %s as %s", oci_path, tag)

    except EibError as exc:
        log.error("error: %s", exc)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        log.error("interrupted")
        raise SystemExit(130) from None


@image.command(
    "install",
    context_settings={"help_option_names": ["-h", "--help"]},
    help="install the imported bootc image onto a new disk image with "
    "'bootc install to-disk', using the config's container.tag, "
    "container.disk, container.size and container.filesystem",
)
@click.option(
    "--config",
    type=click.Path(path_type=Path),
    required=True,
    help="YAML config giving the container tag, disk and size (see config.yml)",
)
@click.option(
    "--tag",
    default=None,
    help="image to install (overrides the config's container.tag)",
)
@click.option(
    "--disk",
    type=click.Path(path_type=Path),
    default=None,
    help="disk image file to create (overrides the config's container.disk)",
)
@click.option(
    "--size",
    default=None,
    help="size of the disk image, e.g. 10G (overrides the config's " "container.size)",
)
@click.option(
    "--filesystem",
    default=None,
    help="root filesystem of the disk image, e.g. ext4 (overrides the "
    "config's container.filesystem)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="print the install commands without running them",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="verbose logging",
)
def image_install(
    config: Path,
    tag: str | None,
    disk: Path | None,
    size: str | None,
    filesystem: str | None,
    dry_run: bool,
    verbose: bool,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
    )

    try:
        cfg = load_config(config)
        tag = tag or cfg["container_tag"]
        disk = disk or cfg["container_disk"]
        size = size or cfg["container_size"]
        filesystem = filesystem or cfg["container_filesystem"]
        for value, key in ((tag, "tag"), (disk, "disk"), (size, "size")):
            if not value:
                raise EibError(
                    f"no container {key}: set 'container.{key}' in {config} "
                    f"or pass --{key}"
                )

        step_install(tag, disk, size, dry_run, filesystem=filesystem)

        if dry_run:
            log.info("dry run: nothing was installed")
        else:
            log.info("done: installed %s to %s", tag, disk)

    except EibError as exc:
        log.error("error: %s", exc)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        log.error("interrupted")
        raise SystemExit(130) from None
