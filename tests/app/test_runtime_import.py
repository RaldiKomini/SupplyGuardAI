import importlib


def test_chainlit_entry_point_imports_with_combined_dependencies(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHAINLIT_APP_ROOT", str(tmp_path))

    module = importlib.import_module("supplyguard.app.chainlit_app")

    assert callable(module.start)
    assert callable(module.on_message)
