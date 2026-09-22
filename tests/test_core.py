"""Unit tests for eib's core pipeline helpers."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from eib import (
    EibError,
    copy_into,
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
        resolved = resolve_entries(["trixie-bootc"], default_dir, tmp_path, "profiles")
        assert resolved == [("trixie-bootc", default_dir / "trixie-bootc")]

    def test_plain_name_without_default_dir_is_a_path(self, tmp_path):
        resolved = resolve_entries(["sub/common"], None, tmp_path, "profiles")
        assert resolved == [("common", tmp_path / "sub" / "common")]

    def test_mapping_with_explicit_dir(self, tmp_path):
        entries = [{"name": "extra", "dir": "elsewhere/extra"}]
        resolved = resolve_entries(entries, None, tmp_path, "profiles")
        assert resolved == [("extra", tmp_path / "elsewhere" / "extra")]

    def test_mapping_dir_overrides_default_dir(self, tmp_path):
        default_dir = tmp_path / "profiles"
        entries = [{"name": "extra", "dir": "/srv/extra"}]
        resolved = resolve_entries(entries, default_dir, tmp_path, "profiles")
        assert resolved == [("extra", Path("/srv/extra"))]

    def test_mapping_falls_back_to_default_dir(self, tmp_path):
        default_dir = tmp_path / "profiles"
        entries = [{"name": "common"}]
        resolved = resolve_entries(entries, default_dir, tmp_path, "profiles")
        assert resolved == [("common", default_dir / "common")]

    def test_mapping_missing_name_raises(self, tmp_path):
        with pytest.raises(EibError, match="missing required 'name'"):
            resolve_entries([{"dir": "x"}], None, tmp_path, "profiles")

    def test_mapping_without_dir_or_default_raises(self, tmp_path):
        with pytest.raises(EibError, match="no 'dir'"):
            resolve_entries([{"name": "extra"}], None, tmp_path, "profiles")

    def test_invalid_entry_type_raises(self, tmp_path):
        with pytest.raises(EibError, match="must be a string or mapping"):
            resolve_entries([123], None, tmp_path, "profiles")


class TestLoadConfig:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(EibError, match="config file not found"):
            load_config(tmp_path / "missing.yml")

    def test_loads_profiles_and_images(self, tmp_path):
        config = tmp_path / "eib.yml"
        config.write_text("profiles:\n  - common\nimages:\n  - initrd\n")
        cfg = load_config(config)
        assert cfg["profiles_raw"] == ["common"]
        assert cfg["images_raw"] == ["initrd"]
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
                    "--output-dir",
                    str(output_dir),
                    "--format",
                    "oci",
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
