"""Unit tests for eib's core pipeline helpers."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from eib import (
    EibError,
    copy_into,
    default_image_version,
    find_oci_output,
    load_config,
    prepare_workspace,
    resolve_entries,
    run,
    step_build,
)


class TestRun:
    def test_dry_run_does_not_execute(self, monkeypatch):
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))
        run(["mkosi", "build"], dry_run=True)
        assert calls == []

    def test_success_runs_command(self, monkeypatch):
        calls = []

        def fake_run(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess([], 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        run(["mkosi", "build"], dry_run=False)
        assert calls == [((["mkosi", "build"],), {"cwd": None})]

    def test_nonzero_exit_raises(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1)
        )
        with pytest.raises(EibError, match="exit 1"):
            run(["mkosi", "build"], dry_run=False)

    def test_missing_command_raises(self, monkeypatch):
        def raise_not_found(*args, **kwargs):
            raise FileNotFoundError

        monkeypatch.setattr(subprocess, "run", raise_not_found)
        with pytest.raises(EibError, match="not found"):
            run(["nonexistent-command"], dry_run=False)


class TestResolveEntries:
    def test_plain_name_with_default_dir(self, tmp_path):
        default_dir = tmp_path / "profiles"
        resolved = resolve_entries(
            [("trixie-bootc", tmp_path)], default_dir, "profiles"
        )
        assert resolved == [("trixie-bootc", default_dir / "trixie-bootc")]

    def test_plain_name_without_default_dir_is_a_path(self, tmp_path):
        resolved = resolve_entries([("sub/common", tmp_path)], None, "profiles")
        assert resolved == [("common", tmp_path / "sub" / "common")]

    def test_mapping_with_explicit_dir(self, tmp_path):
        entries = [({"name": "extra", "dir": "elsewhere/extra"}, tmp_path)]
        resolved = resolve_entries(entries, None, "profiles")
        assert resolved == [("extra", tmp_path / "elsewhere" / "extra")]

    def test_mapping_dir_overrides_default_dir(self, tmp_path):
        default_dir = tmp_path / "profiles"
        entries = [({"name": "extra", "dir": "/srv/extra"}, tmp_path)]
        resolved = resolve_entries(entries, default_dir, "profiles")
        assert resolved == [("extra", Path("/srv/extra"))]

    def test_mapping_falls_back_to_default_dir(self, tmp_path):
        default_dir = tmp_path / "profiles"
        entries = [({"name": "common"}, tmp_path)]
        resolved = resolve_entries(entries, default_dir, "profiles")
        assert resolved == [("common", default_dir / "common")]

    def test_mapping_missing_name_raises(self, tmp_path):
        with pytest.raises(EibError, match="missing required 'name'"):
            resolve_entries([({"dir": "x"}, tmp_path)], None, "profiles")

    def test_mapping_without_dir_or_default_raises(self, tmp_path):
        with pytest.raises(EibError, match="no 'dir'"):
            resolve_entries([({"name": "extra"}, tmp_path)], None, "profiles")

    def test_invalid_entry_type_raises(self, tmp_path):
        with pytest.raises(EibError, match="must be a string or mapping"):
            resolve_entries([(123, tmp_path)], None, "profiles")

    def test_entries_use_their_own_base_dir(self, tmp_path):
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        entries = [("common", tmp_path), ("sub/extra", other_dir)]
        resolved = resolve_entries(entries, None, "profiles")
        assert resolved == [
            ("common", tmp_path / "common"),
            ("extra", other_dir / "sub" / "extra"),
        ]


class TestLoadConfig:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(EibError, match="config file not found"):
            load_config(tmp_path / "missing.yml")

    def test_loads_profiles_and_images(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("profiles:\n  - common\nimages:\n  - initrd\n")
        cfg = load_config(config)
        assert cfg["profiles_raw"] == [("common", tmp_path)]
        assert cfg["images_raw"] == [("initrd", tmp_path)]
        assert cfg["base_dir"] == tmp_path

    def test_empty_file_yields_empty_lists(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("")
        cfg = load_config(config)
        assert cfg["profiles_raw"] == []
        assert cfg["images_raw"] == []

    def test_profiles_must_be_a_list(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("profiles: not-a-list\n")
        with pytest.raises(EibError, match="'profiles' must be a list"):
            load_config(config)

    def test_images_must_be_a_list(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("images: not-a-list\n")
        with pytest.raises(EibError, match="'images' must be a list"):
            load_config(config)

    def test_include_must_be_a_list(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("include: not-a-list\n")
        with pytest.raises(EibError, match="'include' must be a list"):
            load_config(config)

    def test_include_entries_must_be_strings(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("include:\n  - 123\n")
        with pytest.raises(EibError, match="'include' entries must be strings"):
            load_config(config)

    def test_include_missing_file_raises(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("include:\n  - missing.yml\n")
        with pytest.raises(EibError, match="config file not found"):
            load_config(config)

    def test_include_merges_profiles_and_images_ahead_of_own(self, tmp_path):
        included = tmp_path / "common.yml"
        included.write_text("profiles:\n  - shared\nimages:\n  - initrd\n")
        config = tmp_path / "eib.yml"
        config.write_text(
            "include:\n  - common.yml\nprofiles:\n  - trixie-bootc\nimages: []\n"
        )
        cfg = load_config(config)
        assert cfg["profiles_raw"] == [
            ("shared", tmp_path),
            ("trixie-bootc", tmp_path),
        ]
        assert cfg["images_raw"] == [("initrd", tmp_path)]

    def test_include_path_is_relative_to_its_own_file(self, tmp_path):
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        (sub_dir / "extra.yml").write_text(
            "profiles:\n  - name: extra\n    dir: here\n"
        )
        config = tmp_path / "eib.yml"
        config.write_text("include:\n  - sub/extra.yml\n")
        cfg = load_config(config)
        assert cfg["profiles_raw"] == [({"name": "extra", "dir": "here"}, sub_dir)]

    def test_nested_include(self, tmp_path):
        grandchild = tmp_path / "grandchild.yml"
        grandchild.write_text("profiles:\n  - base\n")
        child = tmp_path / "child.yml"
        child.write_text("include:\n  - grandchild.yml\nprofiles:\n  - mid\n")
        config = tmp_path / "eib.yml"
        config.write_text("include:\n  - child.yml\nprofiles:\n  - top\n")
        cfg = load_config(config)
        assert cfg["profiles_raw"] == [
            ("base", tmp_path),
            ("mid", tmp_path),
            ("top", tmp_path),
        ]

    def test_circular_include_raises(self, tmp_path):
        a = tmp_path / "a.yml"
        b = tmp_path / "b.yml"
        a.write_text("include:\n  - b.yml\n")
        b.write_text("include:\n  - a.yml\n")
        with pytest.raises(EibError, match="circular 'include'"):
            load_config(a)

    def test_loads_image_name_and_version(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("image:\n  name: elxr-bianca\n  version: '1.10'\n")
        cfg = load_config(config)
        assert cfg["image_name"] == "elxr-bianca"
        assert cfg["image_version"] == "1.10"

    def test_image_is_optional(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("profiles:\n  - common\n")
        cfg = load_config(config)
        assert cfg["image_name"] is None
        assert cfg["image_version"] is None
        assert cfg["image_format"] == "disk"

    def test_loads_image_format(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("image:\n  format: disk\n")
        cfg = load_config(config)
        assert cfg["image_format"] == "disk"

    def test_unsupported_image_format_raises(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("image:\n  format: qcow2\n")
        with pytest.raises(EibError, match="unsupported 'image.format' 'qcow2'"):
            load_config(config)

    def test_including_file_overrides_included_image_format(self, tmp_path):
        common = tmp_path / "common.yml"
        common.write_text("image:\n  format: tar\n")
        config = tmp_path / "eib.yml"
        config.write_text("include:\n  - common.yml\nimage:\n  format: disk\n")
        cfg = load_config(config)
        assert cfg["image_format"] == "disk"

    def test_integer_image_version_is_accepted(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("image:\n  version: 2\n")
        cfg = load_config(config)
        assert cfg["image_version"] == "2"

    def test_float_image_version_raises(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("image:\n  version: 1.10\n")
        with pytest.raises(EibError, match="'image.version' must be a string"):
            load_config(config)

    def test_non_mapping_image_raises(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("image: elxr\n")
        with pytest.raises(EibError, match="'image' must be a mapping"):
            load_config(config)

    def test_unknown_image_key_raises(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("image:\n  nmae: elxr\n")
        with pytest.raises(EibError, match="unknown 'image' key 'nmae'"):
            load_config(config)

    def test_including_file_overrides_included_image_fields(self, tmp_path):
        common = tmp_path / "common.yml"
        common.write_text("image:\n  name: base\n  version: '1.0'\n")
        config = tmp_path / "eib.yml"
        config.write_text("include:\n  - common.yml\nimage:\n  version: '2.0'\n")
        cfg = load_config(config)
        assert cfg["image_name"] == "base"
        assert cfg["image_version"] == "2.0"


class TestCopyInto:
    def test_missing_source_raises(self, tmp_path):
        with pytest.raises(EibError, match="does not exist"):
            copy_into(tmp_path / "missing", tmp_path / "dest", "name", dry_run=False)

    def test_dry_run_does_not_copy(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        dest_dir = tmp_path / "dest"
        copy_into(src, dest_dir, "name", dry_run=True)
        assert not dest_dir.exists()

    def test_copies_directory(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "file.txt").write_text("hello")
        dest_dir = tmp_path / "dest"
        copy_into(src, dest_dir, "name", dry_run=False)
        assert (dest_dir / "name" / "file.txt").read_text() == "hello"

    def test_copies_file(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("hello")
        dest_dir = tmp_path / "dest"
        copy_into(src, dest_dir, "name", dry_run=False)
        assert (dest_dir / "name").read_text() == "hello"

    def test_replaces_existing_directory(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "new.txt").write_text("new")
        dest_dir = tmp_path / "dest"
        existing = dest_dir / "name"
        existing.mkdir(parents=True)
        (existing / "old.txt").write_text("old")

        copy_into(src, dest_dir, "name", dry_run=False)

        assert (existing / "new.txt").read_text() == "new"
        assert not (existing / "old.txt").exists()


class TestPrepareWorkspace:
    def test_copies_profiles_and_images(self, tmp_path):
        profile_src = tmp_path / "profile-a"
        profile_src.mkdir()
        image_src = tmp_path / "image-a"
        image_src.mkdir()
        workspace = tmp_path / "workspace"

        result = prepare_workspace(
            workspace,
            [("profile-a", profile_src)],
            [("image-a", image_src)],
            dry_run=False,
        )

        assert result == workspace
        assert (workspace / "mkosi.profiles" / "profile-a").is_dir()
        assert (workspace / "mkosi.images" / "image-a").is_dir()

    def test_writes_mkosi_conf_template(self, tmp_path):
        workspace = tmp_path / "workspace"
        prepare_workspace(workspace, [], [], dry_run=False)
        assert (workspace / "mkosi.conf").is_file()

    def test_does_not_overwrite_existing_mkosi_conf(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "mkosi.conf").write_text("custom")

        prepare_workspace(workspace, [], [], dry_run=False)

        assert (workspace / "mkosi.conf").read_text() == "custom"

    def test_dry_run_does_not_create_workspace(self, tmp_path):
        workspace = tmp_path / "workspace"
        prepare_workspace(workspace, [], [], dry_run=True)
        assert not workspace.exists()


class TestStepBuild:
    def test_builds_expected_command(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "eib.run", lambda cmd, **kwargs: calls.append((cmd, kwargs))
        )
        monkeypatch.setattr("eib.default_image_version", lambda: "20260922164500")
        workspace = tmp_path / "workspace"
        output_dir = tmp_path / "out"

        step_build(workspace, output_dir, dry_run=False)

        assert output_dir.is_dir()
        assert calls == [
            (
                [
                    "mkosi",
                    "--directory",
                    str(workspace),
                    "--image-version",
                    "20260922164500",
                    "--output-dir",
                    str(output_dir),
                    "--format",
                    "disk",
                    "build",
                ],
                {"dry_run": False},
            )
        ]

    def test_debug_flag_is_forwarded(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "eib.run", lambda cmd, **kwargs: calls.append((cmd, kwargs))
        )
        workspace = tmp_path / "workspace"
        output_dir = tmp_path / "out"

        step_build(workspace, output_dir, dry_run=True, debug=True)

        args = calls[0][0]
        assert "--debug" in args
        assert not output_dir.exists()

    def test_profiles_are_forwarded(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "eib.run", lambda cmd, **kwargs: calls.append((cmd, kwargs))
        )
        monkeypatch.setattr("eib.default_image_version", lambda: "20260922164500")
        workspace = tmp_path / "workspace"
        output_dir = tmp_path / "out"

        step_build(
            workspace,
            output_dir,
            dry_run=True,
            profiles=["trixie-bootc", "vendor-extra"],
        )

        args = calls[0][0]
        assert args == [
            "mkosi",
            "--directory",
            str(workspace),
            "--profile",
            "trixie-bootc",
            "--profile",
            "vendor-extra",
            "--image-version",
            "20260922164500",
            "--output-dir",
            str(output_dir),
            "--format",
            "disk",
            "build",
        ]

    def test_image_name_and_version_are_forwarded(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "eib.run", lambda cmd, **kwargs: calls.append((cmd, kwargs))
        )
        workspace = tmp_path / "workspace"
        output_dir = tmp_path / "out"

        step_build(
            workspace,
            output_dir,
            dry_run=True,
            profiles=["bianca"],
            image_name="elxr-bianca",
            image_version="1.0",
        )

        args = calls[0][0]
        assert args == [
            "mkosi",
            "--directory",
            str(workspace),
            "--profile",
            "bianca",
            "--image-id",
            "elxr-bianca",
            "--image-version",
            "1.0",
            "--output-dir",
            str(output_dir),
            "--format",
            "disk",
            "build",
        ]

    def test_missing_image_version_uses_timestamp(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "eib.run", lambda cmd, **kwargs: calls.append((cmd, kwargs))
        )

        step_build(
            tmp_path / "workspace",
            tmp_path / "out",
            dry_run=True,
            image_name="elxr-bianca",
        )

        args = calls[0][0]
        assert "--auto-bump" not in args
        version = args[args.index("--image-version") + 1]
        assert len(version) == 14 and version.isdigit()

    def test_image_format_is_forwarded(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "eib.run", lambda cmd, **kwargs: calls.append((cmd, kwargs))
        )

        step_build(
            tmp_path / "workspace",
            tmp_path / "out",
            dry_run=True,
            image_format="oci",
        )

        args = calls[0][0]
        assert args[args.index("--format") + 1] == "oci"
        assert "disk" not in args


class TestDefaultImageVersion:
    def test_is_utc_timestamp(self, monkeypatch):
        class FakeDatetime:
            @staticmethod
            def now(tz):
                assert tz is timezone.utc
                return datetime(2026, 9, 22, 16, 45, 0, tzinfo=tz)

        monkeypatch.setattr("eib.datetime", FakeDatetime)
        assert default_image_version() == "20260922164500"


class TestFindOciOutput:
    def test_missing_output_dir_raises(self, tmp_path):
        with pytest.raises(EibError, match="does not exist"):
            find_oci_output(tmp_path / "missing", since=0)

    def test_no_candidates_raises(self, tmp_path):
        with pytest.raises(EibError, match="no OCI image found"):
            find_oci_output(tmp_path, since=0)

    def test_finds_oci_layout_directory(self, tmp_path):
        oci_dir = tmp_path / "image.oci"
        oci_dir.mkdir()
        (oci_dir / "index.json").write_text("{}")
        (oci_dir / "oci-layout").write_text("{}")

        found = find_oci_output(tmp_path, since=0)
        assert found == oci_dir

    def test_finds_archive_file(self, tmp_path):
        archive = tmp_path / "image.tar"
        archive.write_text("data")

        found = find_oci_output(tmp_path, since=0)
        assert found == archive

    def test_ignores_entries_older_than_since(self, tmp_path):
        archive = tmp_path / "image.tar"
        archive.write_text("data")

        with pytest.raises(EibError, match="no OCI image found"):
            find_oci_output(tmp_path, since=time.time() + 1000)

    def test_prefers_most_recently_modified(self, tmp_path):
        older = tmp_path / "older.tar"
        older.write_text("data")
        newer = tmp_path / "newer.tar"
        newer.write_text("data")
        now = time.time()
        os.utime(older, (now, now - 10))
        os.utime(newer, (now, now))

        found = find_oci_output(tmp_path, since=now - 100)
        assert found == newer
