"""SUPPORT_DATA_DIR: set, unset, or empty as in a copied .env.example."""

from pathlib import Path

from supplyguard.workflow.settings import DEFAULT_DATA_DIR, data_dir


def test_empty_or_missing_setting_uses_the_repository_folder(monkeypatch) -> None:
    monkeypatch.setenv("SUPPORT_DATA_DIR", "")
    assert data_dir() == DEFAULT_DATA_DIR
    monkeypatch.delenv("SUPPORT_DATA_DIR")
    assert data_dir() == DEFAULT_DATA_DIR
    assert (DEFAULT_DATA_DIR / "config" / "policies.json").exists()


def test_setting_overrides_the_folder(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SUPPORT_DATA_DIR", str(tmp_path))
    assert data_dir() == Path(tmp_path)
