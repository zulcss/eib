"""Unit tests for the 'eib image build' and 'eib image import' CLI commands."""

from __future__ import annotations

from click.testing import CliRunner

from eib.cli.commands.images import image


def write_config(tmp_path, profiles=None, images=None):
    config = tmp_path / "eib.yml"
    lines = []
    if profiles is not None:
        lines.append("profiles:")
        lines += [f"  - {p}" for p in profiles]
    if images is not None:
        lines.append("images:")
        lines += [f"  - {i}" for i in images]
    config.write_text("\n".join(lines) + "\n")
    return config


class TestImageBuild:
    def test_dry_run_reports_no_build(self, tmp_path, caplog):
        config = write_config(tmp_path, profiles=[], images=[])
        runner = CliRunner()

        with caplog.at_level("INFO", logger="eib"):
            result = runner.invoke(
                image,
                [
                    "build",
                    "--config",
                    str(config),
                    "--workspace",
                    str(tmp_path / "workspace"),
                    "--dry-run",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "dry run: nothing was built" in caplog.text

    def test_default_disk_format_skips_oci_lookup(self, tmp_path, caplog, monkeypatch):
        config = tmp_path / "eib.yml"
        config.write_text("profiles: []\n")
        calls = []
        monkeypatch.setattr(
            "eib.run", lambda cmd, **kwargs: calls.append((cmd, kwargs))
        )
        runner = CliRunner()

        with caplog.at_level("INFO", logger="eib"):
            result = runner.invoke(
                image,
                [
                    "build",
                    "--config",
                    str(config),
                    "--workspace",
                    str(tmp_path / "workspace"),
                ],
            )

        assert result.exit_code == 0, result.output
        args = calls[0][0]
        assert args[args.index("--format") + 1] == "disk"
        assert "done: built disk image in" in caplog.text

    def test_missing_config_exits_with_error(self, tmp_path, caplog):
        runner = CliRunner()

        with caplog.at_level("INFO", logger="eib"):
            result = runner.invoke(
                image,
                [
                    "build",
                    "--config",
                    str(tmp_path / "missing.yml"),
                    "--workspace",
                    str(tmp_path / "workspace"),
                    "--dry-run",
                ],
            )

        assert result.exit_code == 1
        assert "config file not found" in caplog.text

    def test_mkosi_dir_missing_profiles_exits_with_error(self, tmp_path, caplog):
        config = write_config(tmp_path, profiles=["common"], images=[])
        mkosi_dir = tmp_path / "mkosi-sources"
        mkosi_dir.mkdir()
        runner = CliRunner()

        with caplog.at_level("INFO", logger="eib"):
            result = runner.invoke(
                image,
                [
                    "build",
                    "--config",
                    str(config),
                    "--workspace",
                    str(tmp_path / "workspace"),
                    "--mkosi-dir",
                    str(mkosi_dir),
                    "--dry-run",
                ],
            )

        assert result.exit_code == 1
        assert "'profiles' directory not found" in caplog.text

    def test_dry_run_assembles_workspace_plan_without_writing(self, tmp_path):
        profile_src = tmp_path / "sources" / "common"
        profile_src.mkdir(parents=True)
        config = write_config(tmp_path, profiles=["sources/common"], images=[])
        workspace = tmp_path / "workspace"
        runner = CliRunner()

        result = runner.invoke(
            image,
            [
                "build",
                "--config",
                str(config),
                "--workspace",
                str(workspace),
                "--dry-run",
            ],
        )

        assert result.exit_code == 0, result.output
        assert not workspace.exists()


OCI_CONFIG = "image:\n  format: oci\ncontainer:\n  tag: elxr-bianca:latest\n"


def make_oci_output(workspace):
    oci_dir = workspace / "mkosi.output" / "image.oci"
    oci_dir.mkdir(parents=True)
    (oci_dir / "index.json").write_text("{}")
    (oci_dir / "oci-layout").write_text("{}")
    return oci_dir


class TestImageImport:
    def invoke(self, tmp_path, config, *extra):
        return CliRunner().invoke(
            image,
            [
                "import",
                "--config",
                str(config),
                "--workspace",
                str(tmp_path / "workspace"),
                *extra,
            ],
        )

    def test_imports_with_config_tag(self, tmp_path, caplog, monkeypatch):
        config = tmp_path / "eib.yml"
        config.write_text(OCI_CONFIG)
        oci_dir = make_oci_output(tmp_path / "workspace")
        calls = []
        monkeypatch.setattr(
            "eib.run", lambda cmd, **kwargs: calls.append((cmd, kwargs))
        )

        with caplog.at_level("INFO", logger="eib"):
            result = self.invoke(tmp_path, config)

        assert result.exit_code == 0, result.output
        assert calls[0][0] == [
            "skopeo",
            "copy",
            f"oci:{oci_dir}",
            "containers-storage:localhost/elxr-bianca:latest",
        ]
        assert "done: imported" in caplog.text

    def test_tag_option_overrides_config(self, tmp_path, monkeypatch):
        config = tmp_path / "eib.yml"
        config.write_text(OCI_CONFIG)
        make_oci_output(tmp_path / "workspace")
        calls = []
        monkeypatch.setattr(
            "eib.run", lambda cmd, **kwargs: calls.append((cmd, kwargs))
        )

        result = self.invoke(tmp_path, config, "--tag", "other:1")

        assert result.exit_code == 0, result.output
        assert calls[0][0][-1] == "containers-storage:localhost/other:1"

    def test_dry_run_reports_no_import(self, tmp_path, caplog):
        config = tmp_path / "eib.yml"
        config.write_text(OCI_CONFIG)
        make_oci_output(tmp_path / "workspace")

        with caplog.at_level("INFO", logger="eib"):
            result = self.invoke(tmp_path, config, "--dry-run")

        assert result.exit_code == 0, result.output
        assert "dry run: nothing was imported" in caplog.text

    def test_missing_tag_exits_with_error(self, tmp_path, caplog):
        config = tmp_path / "eib.yml"
        config.write_text("image:\n  format: oci\n")
        make_oci_output(tmp_path / "workspace")

        with caplog.at_level("INFO", logger="eib"):
            result = self.invoke(tmp_path, config)

        assert result.exit_code == 1
        assert "no container tag" in caplog.text

    def test_missing_oci_output_exits_with_error(self, tmp_path, caplog):
        config = tmp_path / "eib.yml"
        config.write_text(OCI_CONFIG)

        with caplog.at_level("INFO", logger="eib"):
            result = self.invoke(tmp_path, config)

        assert result.exit_code == 1
        assert "output directory does not exist" in caplog.text

    def test_non_oci_format_exits_with_error(self, tmp_path, caplog, monkeypatch):
        config = tmp_path / "eib.yml"
        config.write_text("container:\n  tag: elxr-bianca:latest\n")
        make_oci_output(tmp_path / "workspace")
        calls = []
        monkeypatch.setattr(
            "eib.run", lambda cmd, **kwargs: calls.append((cmd, kwargs))
        )

        with caplog.at_level("INFO", logger="eib"):
            result = self.invoke(tmp_path, config)

        assert result.exit_code == 1
        assert "only OCI images can be imported" in caplog.text
        assert "'disk'" in caplog.text
        assert calls == []
