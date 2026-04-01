"""CLI integration and smoke tests."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from polyphony.cli.main import cli
from polyphony.db import connect, fetchone


def test_cli_help_smoke():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "project" in result.output
    assert "practice" in result.output


def test_practice_lists_domains():
    runner = CliRunner()
    result = runner.invoke(cli, ["practice", "--list-domains"])

    assert result.exit_code == 0
    assert "housing" in result.output
    assert "healthcare" in result.output


def test_practice_offline_creates_sandbox(tmp_path):
    projects_root = tmp_path / "projects"
    env = {"POLYPHONY_PROJECTS_DIR": str(projects_root)}

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            [
                "practice",
                "--segments",
                "6",
                "--domain",
                "housing",
                "--overwrite",
            ],
            env=env,
        )

        assert result.exit_code == 0, result.output

        marker = Path(".polyphony_project")
        assert marker.exists()

        project_dir = Path(marker.read_text(encoding="utf-8").strip())
        assert project_dir.parent == projects_root.resolve()

        db_path = project_dir / "project.db"
        assert db_path.exists()

        conn = connect(db_path)
        try:
            project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
            assert project is not None
            assert project["slug"] == "practice-sandbox"

            project_id = project["id"]

            n_docs = fetchone(
                conn,
                "SELECT COUNT(*) AS n FROM document WHERE project_id = ?",
                (project_id,),
            )
            n_segments = fetchone(
                conn,
                "SELECT COUNT(*) AS n FROM segment WHERE project_id = ?",
                (project_id,),
            )
            assert n_docs is not None
            assert n_segments is not None
            n_docs = n_docs["n"]
            n_segments = n_segments["n"]
        finally:
            conn.close()

        assert n_docs == 6
        assert n_segments == 6


def test_practice_topic_mode_uses_llm_generator(monkeypatch, tmp_path):
    projects_root = tmp_path / "projects"
    env = {"POLYPHONY_PROJECTS_DIR": str(projects_root)}

    calls = {"count": 0}

    def fake_generate_llm_data(topic, n_segments, model, seed):
        calls["count"] += 1
        assert topic == "workplace burnout"
        assert n_segments == 2
        assert model == "llama3.2"
        assert seed == 7
        return {
            "segments": [
                {
                    "text": "I feel exhausted before my day even starts.",
                    "metadata": {"participant": "Ari", "generated": True},
                },
                {
                    "text": "My manager keeps shifting priorities every hour.",
                    "metadata": {"participant": "Rin", "generated": True},
                },
            ],
            "codes": [
                {
                    "name": "BURNOUT",
                    "description": "Signs of exhaustion and depersonalization.",
                }
            ],
        }

    monkeypatch.setattr("polyphony.cli.cmd_practice.generate_llm_data", fake_generate_llm_data)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            [
                "practice",
                "--topic",
                "workplace burnout",
                "--segments",
                "2",
                "--seed",
                "7",
                "--overwrite",
            ],
            env=env,
        )

        assert result.exit_code == 0, result.output
        assert calls["count"] == 1

        db_path = projects_root / "practice-sandbox" / "project.db"
        conn = connect(db_path)
        try:
            project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
            assert project is not None
            n_docs = fetchone(
                conn,
                "SELECT COUNT(*) AS n FROM document WHERE project_id = ?",
                (project["id"],),
            )
            assert n_docs is not None
            n_docs = n_docs["n"]
        finally:
            conn.close()

        assert n_docs == 2


def test_practice_default_mode_does_not_call_llm_generator(monkeypatch, tmp_path):
    """Negative control: default practice path must stay offline/template-based."""
    projects_root = tmp_path / "projects"
    env = {"POLYPHONY_PROJECTS_DIR": str(projects_root)}

    def fail_if_called(*args, **kwargs):
        raise AssertionError("LLM generator should not be called in default practice mode")

    monkeypatch.setattr("polyphony.cli.cmd_practice.generate_llm_data", fail_if_called)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            [
                "practice",
                "--domain",
                "housing",
                "--segments",
                "3",
                "--overwrite",
            ],
            env=env,
        )

        assert result.exit_code == 0, result.output


def test_practice_source_file_mode_skips_generators_and_respects_no_open(monkeypatch, tmp_path):
    """Practice with real data should not invoke synthetic generators."""
    projects_root = tmp_path / "projects"
    env = {"POLYPHONY_PROJECTS_DIR": str(projects_root)}

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Synthetic generator should not be called in --source-file mode")

    monkeypatch.setattr("polyphony.cli.cmd_practice.generate_template_data", fail_if_called)
    monkeypatch.setattr("polyphony.cli.cmd_practice.generate_llm_data", fail_if_called)

    runner = CliRunner()
    with runner.isolated_filesystem():
        source = Path("real_data.txt")
        source.write_text(
            "This is a sufficiently long training transcript excerpt for testing the import path.",
            encoding="utf-8",
        )

        result = runner.invoke(
            cli,
            [
                "practice",
                "--source-file",
                str(source),
                "--no-open",
                "--overwrite",
            ],
            env=env,
        )

        assert result.exit_code == 0, result.output
        assert not Path(".polyphony_project").exists()


def test_practice_rejects_topic_with_source_file(tmp_path):
    projects_root = tmp_path / "projects"
    env = {"POLYPHONY_PROJECTS_DIR": str(projects_root)}

    runner = CliRunner()
    with runner.isolated_filesystem():
        source = Path("input.txt")
        source.write_text(
            "This file exists so the option parser accepts --source-file.",
            encoding="utf-8",
        )

        result = runner.invoke(
            cli,
            [
                "practice",
                "--source-file",
                str(source),
                "--topic",
                "burnout",
            ],
            env=env,
        )

        assert result.exit_code != 0
        assert "Choose either --source-file or --topic" in result.output
