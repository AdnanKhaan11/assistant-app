"""Check that operator instructions stay connected to runnable project entry points."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_readme_documents_existing_setup_and_run_entry_points() -> None:
    """Keep documented setup commands aligned with the checked-in app files."""
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "python -m backend.bootstrap_admin" in readme
    assert "uvicorn backend.main:app --reload --port 8000" in readme
    assert "python -m pip install -r requirements.txt" in readme
    assert (PROJECT_ROOT / ".env.example").is_file()
    assert (PROJECT_ROOT / "backend" / "bootstrap_admin.py").is_file()
    assert (PROJECT_ROOT / "backend" / "main.py").is_file()


def test_readme_documents_optional_observability_and_supported_test_commands() -> None:
    """Keep local observability and both automated test commands discoverable."""
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "docker compose -f deploy/docker-compose.yml up -d" in readme
    assert (PROJECT_ROOT / "deploy" / "docker-compose.yml").is_file()
    assert "python -m pytest -q" in readme
    assert "node --test frontend/tests/*.test.cjs" in readme
    assert (PROJECT_ROOT / "frontend" / "tests").is_dir()


def test_readme_discloses_deferred_account_and_embedding_operations() -> None:
    """Make product limitations visible so setup docs do not promise absent features."""
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "Email-based password reset is not available" in readme
    assert "read-only" in readme
    assert "changing embedding models" in readme
    assert "not exposed through authenticated user routes" in readme
    assert "saving does not activate them, including after a restart" in readme
