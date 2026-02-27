from pathlib import Path

from thepipe import registration


def test_get_thepipe_path_falls_back_to_python_c(monkeypatch):
    monkeypatch.setattr(registration.sys, "executable", "/tmp/fake-python")
    monkeypatch.setattr(registration.os.path, "exists", lambda _: False)

    cmd = registration._get_thepipe_path()

    assert cmd == '/tmp/fake-python -c "from thepipe import main; main()"'


def test_register_antigravity_appends_agents_block_without_crash(tmp_path, monkeypatch):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(
        "# AGENTS.md\n\n## existing-tool\n\nExisting instructions.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(registration, "_get_thepipe_path", lambda: "/abs/thepipe")

    workflow_file, agents_updated = registration.register_antigravity(str(tmp_path))

    assert workflow_file == tmp_path / ".antigravity" / "workflows" / "thepipe.md"
    assert workflow_file.exists()
    assert agents_updated == agents_md

    updated = agents_md.read_text(encoding="utf-8")
    assert "## existing-tool" in updated
    assert "## thepipe" in updated
    assert ".antigravity/workflows/thepipe.md" in updated
    assert ".agent/workflows/thepipe.md" not in updated

    # register_antigravity should not silently hide AGENTS.md from version control.
    gitignore = tmp_path / ".gitignore"
    if gitignore.exists():
        assert "AGENTS.md" not in gitignore.read_text(encoding="utf-8")

    # Allowlist side-effect is still expected for Antigravity registration.
    assert (tmp_path / ".agent" / "config.json").exists()


def test_register_antigravity_creates_agents_block_with_antigravity_path(tmp_path, monkeypatch):
    monkeypatch.setattr(registration, "_get_thepipe_path", lambda: "/abs/thepipe")

    _, agents_updated = registration.register_antigravity(str(tmp_path))

    assert agents_updated == tmp_path / "AGENTS.md"
    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert ".antigravity/workflows/thepipe.md" in content
    assert ".agent/workflows/thepipe.md" not in content
