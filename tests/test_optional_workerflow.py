from types import SimpleNamespace

from qwenpaw_worker.config import WorkerConfig
from qwenpaw_worker.worker import Worker


def make_worker(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTTEAMS_WORKER_ROLE", raising=False)
    monkeypatch.setenv("AGENTTEAMS_AGENT_ROLE", "worker")
    return Worker(WorkerConfig("probe", "http://storage", "key", "secret", install_dir=tmp_path))


def test_workerflow_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTTEAMS_QWENPAW_WORKERFLOW_ENABLED", raising=False)
    worker = make_worker(tmp_path, monkeypatch)
    assert worker._builtin_plugin_ids() == ("teamharness",)


def test_explicit_workerflow_enable_keeps_plugins_and_warns_about_memory(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("AGENTTEAMS_QWENPAW_WORKERFLOW_ENABLED", "true")
    worker = make_worker(tmp_path, monkeypatch)
    prepared = []
    monkeypatch.setattr(worker, "_prepare_builtin_plugin", lambda name, source: prepared.append(name))
    worker._prepare_default_plugins()
    assert worker._builtin_plugin_ids() == ("teamharness", "workerflow")
    assert "workerflow" in prepared
    assert "memory pressure" in caplog.text


def test_disabled_workerflow_preserves_mcp_credentials_for_reenable(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTEAMS_QWENPAW_WORKERFLOW_ENABLED", "false")
    worker = make_worker(tmp_path, monkeypatch)
    plugin = worker.config.qwenpaw_working_dir / "plugins/workerflow"
    plugin.mkdir(parents=True)
    (plugin / "plugin.py").write_text("# image-owned builtin")
    prepared, deleted, updated = [], [], []
    monkeypatch.setattr(worker, "_prepare_builtin_plugin", lambda name, source: prepared.append(name))
    worker.api_client = SimpleNamespace(
        list_mcp=lambda: [{"key": "workerflow"}, {"key": "teamharness"}],
        delete_mcp=deleted.append,
        update_mcp=lambda name, payload: updated.append((name, payload)),
    )
    worker._prepare_default_plugins()
    worker._configure_builtin_plugin_mcp_clients()
    assert worker._builtin_plugin_ids() == ("teamharness",)
    assert not plugin.exists()
    assert "workerflow" not in prepared
    assert "teamharness" in prepared
    assert "qwen-code-bridge" in prepared
    assert deleted == []
    assert updated[0] == ("workerflow", {"enabled": False})
    assert updated[1][0] == "teamharness"
    monkeypatch.setenv("AGENTTEAMS_QWENPAW_WORKERFLOW_ENABLED", "true")
    worker._configure_builtin_plugin_mcp_clients(force=True)
    assert updated[-1][0] == "workerflow"
    assert updated[-1][1]["enabled"] is True
    assert deleted == []

