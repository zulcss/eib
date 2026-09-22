"""Unit tests for the 'eib image build' CLI command."""

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
