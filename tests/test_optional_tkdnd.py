from types import SimpleNamespace

import pytest

import main


@pytest.mark.parametrize("plugin_works", [True, False])
def test_file_picker_launcher_survives_missing_tkdnd(monkeypatch, capsys, plugin_works):
    root = SimpleNamespace()
    monkeypatch.setattr(main, "AppRoot", lambda: root)

    def attach(actual):
        assert actual is root
        if not plugin_works:
            raise RuntimeError("Tcl extension ABI mismatch")
        return "2.9.5"

    monkeypatch.setattr(main.TkinterDnD, "require", attach, raising=False)
    assert main.create_app_root() is root
    assert root.TkdndVersion == ("2.9.5" if plugin_works else None)
    if not plugin_works:
        assert "Drag and drop disabled:" in capsys.readouterr().err
        # No drop registration should be attempted on a fallback root.
        main.Launcher._register_drop_targets(SimpleNamespace(root=root))
