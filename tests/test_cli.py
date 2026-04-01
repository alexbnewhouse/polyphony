"""CLI integration and smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from polyphony.cli.main import cli
from polyphony.db import connect, fetchone, insert, json_col


def _seed_cli_project(db_path: Path, slug: str = "audio-test") -> None:
    conn = connect(db_path)
    try:
        project_id = insert(
            conn,
            "project",
            {
                "name": "CLI Test Project",
                "slug": slug,
                "description": "Project for CLI integration tests",
                "methodology": "grounded_theory",
                "research_questions": json_col(["RQ1"]),
                "status": "setup",
                "config": "{}",
            },
        )
        insert(
            conn,
            "agent",
            {
                "project_id": project_id,
                "role": "supervisor",
                "agent_type": "human",
                "model_name": "human",
                "model_version": "human",
                "temperature": 0.0,
                "seed": 0,
            },
        )
        insert(
            conn,
            "agent",
            {
                "project_id": project_id,
                "role": "coder_a",
                "agent_type": "llm",
                "model_name": "stub-a",
                "model_version": "stub",
                "temperature": 0.1,
                "seed": 42,
            },
        )
        insert(
            conn,
            "agent",
            {
                "project_id": project_id,
                "role": "coder_b",
                "agent_type": "llm",
                "model_name": "stub-b",
                "model_version": "stub",
                "temperature": 0.1,
                "seed": 99,
            },
        )
        conn.commit()
    finally:
        conn.close()


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


def test_data_transcribe_imports_transcript_with_provenance(monkeypatch, tmp_path):
    projects_root = tmp_path / "projects"
    slug = "audio-test"
    db_path = projects_root / slug / "project.db"
    _seed_cli_project(db_path, slug=slug)

    env = {"POLYPHONY_PROJECTS_DIR": str(projects_root)}

    transcript_text = (
        "This is the first long transcript paragraph about housing insecurity and stress.\n\n"
        "This is the second long transcript paragraph about coping and social support."
    )

    monkeypatch.setattr(
        "polyphony.cli.cmd_data.transcribe_audio_file",
        lambda *args, **kwargs: {
            "text": transcript_text,
            "metadata": {
                "source_type": "audio_transcription",
                "source_audio_path": str(projects_root / slug / "audio" / "a.wav"),
                "transcription_provider": "local_whisper",
                "transcription_model": "small",
            },
            "stored_audio_path": str(projects_root / slug / "audio" / "a.wav"),
            "segments": [],
        },
    )

    runner = CliRunner()
    with runner.isolated_filesystem():
        audio = Path("sample.wav")
        audio.write_bytes(b"RIFFstub")

        result = runner.invoke(
            cli,
            ["--project", slug, "data", "transcribe", str(audio)],
            env=env,
        )

        assert result.exit_code == 0, result.output

    conn = connect(db_path)
    try:
        project = fetchone(conn, "SELECT * FROM project ORDER BY id LIMIT 1")
        assert project is not None

        doc = fetchone(
            conn,
            "SELECT * FROM document WHERE project_id = ? ORDER BY id DESC LIMIT 1",
            (project["id"],),
        )
        assert doc is not None
        metadata = json.loads(doc["metadata"])
        assert metadata["source_type"] == "audio_transcription"
        assert metadata["transcription_provider"] == "local_whisper"

        seg_count = fetchone(
            conn,
            "SELECT COUNT(*) AS n FROM segment WHERE project_id = ?",
            (project["id"],),
        )
        assert seg_count is not None
        assert seg_count["n"] == 2
    finally:
        conn.close()


def test_data_transcribe_auto_code_requires_codebook(monkeypatch, tmp_path):
    projects_root = tmp_path / "projects"
    slug = "audio-test"
    db_path = projects_root / slug / "project.db"
    _seed_cli_project(db_path, slug=slug)

    env = {"POLYPHONY_PROJECTS_DIR": str(projects_root)}

    class StubAgent:
        def __init__(self, agent_id):
            self.agent_id = agent_id
            self.model_name = "stub"

        def is_available(self):
            return True

    monkeypatch.setattr(
        "polyphony.cli.cmd_data.build_agent_objects",
        lambda *args, **kwargs: (StubAgent(1), StubAgent(2), None),
    )
    monkeypatch.setattr(
        "polyphony.cli.cmd_data.transcribe_audio_file",
        lambda *args, **kwargs: {
            "text": "This transcript paragraph is long enough to import safely.",
            "metadata": {"source_type": "audio_transcription"},
            "stored_audio_path": "ignored",
            "segments": [],
        },
    )

    runner = CliRunner()
    with runner.isolated_filesystem():
        audio = Path("sample.wav")
        audio.write_bytes(b"RIFFstub")

        result = runner.invoke(
            cli,
            ["--project", slug, "data", "transcribe", str(audio), "--auto-code"],
            env=env,
        )

        assert result.exit_code != 0
        assert "No active codebook" in result.output


def test_data_transcribe_auto_induce_and_auto_code_orchestration(monkeypatch, tmp_path):
    projects_root = tmp_path / "projects"
    slug = "audio-test"
    db_path = projects_root / slug / "project.db"
    _seed_cli_project(db_path, slug=slug)

    env = {"POLYPHONY_PROJECTS_DIR": str(projects_root)}

    calls: dict = {"induction": None, "coding": 0}

    class StubAgent:
        def __init__(self, agent_id, model_name):
            self.agent_id = agent_id
            self.model_name = model_name
            self.role = model_name

        def is_available(self):
            return True

    monkeypatch.setattr(
        "polyphony.cli.cmd_data.build_agent_objects",
        lambda *args, **kwargs: (StubAgent(1, "a"), StubAgent(2, "b"), None),
    )
    monkeypatch.setattr(
        "polyphony.cli.cmd_data.transcribe_audio_file",
        lambda *args, **kwargs: {
            "text": "This transcript paragraph is long enough to import safely.",
            "metadata": {"source_type": "audio_transcription"},
            "stored_audio_path": "ignored",
            "segments": [],
        },
    )

    def fake_run_induction(**kwargs):
        calls["induction"] = kwargs
        conn = kwargs["conn"]
        project = kwargs["project"]
        cb_id = insert(
            conn,
            "codebook_version",
            {
                "project_id": project["id"],
                "version": 1,
                "stage": "draft",
                "rationale": "test",
            },
        )
        insert(
            conn,
            "code",
            {
                "project_id": project["id"],
                "codebook_version_id": cb_id,
                "name": "TEST_CODE",
                "description": "test",
                "level": "open",
                "is_active": 1,
                "sort_order": 0,
                "example_quotes": "[]",
            },
        )
        conn.commit()
        return cb_id

    def fake_run_coding_session(**kwargs):
        calls["coding"] += 1
        return 1

    monkeypatch.setattr("polyphony.pipeline.induction.run_induction", fake_run_induction)
    monkeypatch.setattr("polyphony.pipeline.coding.run_coding_session", fake_run_coding_session)

    runner = CliRunner()
    with runner.isolated_filesystem():
        audio = Path("sample.wav")
        audio.write_bytes(b"RIFFstub")

        result = runner.invoke(
            cli,
            [
                "--project",
                slug,
                "data",
                "transcribe",
                str(audio),
                "--auto-induce",
                "--auto-approve-codes",
                "--auto-code",
            ],
            env=env,
        )

        assert result.exit_code == 0, result.output

    assert calls["induction"] is not None
    assert calls["induction"]["auto_accept_all"] is True
    assert calls["coding"] == 2
