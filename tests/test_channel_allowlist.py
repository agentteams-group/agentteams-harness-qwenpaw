"""Exercise the pinned native registry without loading optional chat SDKs."""
import ast
import logging
from pathlib import Path
import subprocess
import sys
import sysconfig
from types import ModuleType, SimpleNamespace

import pytest


@pytest.mark.parametrize("allowlist, expected", [
    ("", ("console", "dingtalk", "feishu")),
    ("console, agentteams_matrix, dingtalk", ("console", "dingtalk")),
    ("feishu", ("feishu",)),
    ("unknown", ("console", "dingtalk", "feishu")),
])
def test_native_allowlist_imports_only_selected_channels(tmp_path, monkeypatch, allowlist, expected):
    native = Path(sysconfig.get_path("purelib")) / "qwenpaw/app/channels/registry.py"
    registry = tmp_path / "registry.py"
    registry.write_text(native.read_text())
    script = Path(__file__).resolve().parents[1] / "scripts/apply-channel-allowlist.py"
    # Reapplication is required after in-place upgrade and rollback.
    for _ in range(2):
        subprocess.run([sys.executable, str(script), str(registry)], check=True)
    tree = ast.parse(registry.read_text())
    loader = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_load_builtin_channels")
    base = type("BaseChannel", (), {})
    channel = type("Channel", (base,), {})
    calls = []

    def import_channel(name, package):
        calls.append(name)
        return SimpleNamespace(Channel=channel)

    constant = ModuleType("qwenpaw.constant")
    constant.EnvVarLoader = SimpleNamespace(get_str=lambda key, default: allowlist)
    monkeypatch.setitem(sys.modules, "qwenpaw.constant", constant)
    namespace = {"__package__": "qwenpaw.app.channels", "BaseChannel": base,
                 "_BUILTIN_SPECS": {key: ("." + key, "Channel") for key in ("console", "dingtalk", "feishu")},
                 "_REQUIRED_CHANNEL_KEYS": frozenset({"console"}),
                 "importlib": SimpleNamespace(import_module=import_channel),
                 "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[loader], type_ignores=[]), "registry", "exec"), namespace)
    assert set(namespace["_load_builtin_channels"]()) == set(expected)
    assert calls == ["." + key for key in expected]


def test_unknown_native_registry_fails_without_rewriting(tmp_path):
    registry = tmp_path / "registry.py"
    registry.write_text("# incompatible future SDK\n")
    script = Path(__file__).resolve().parents[1] / "scripts/apply-channel-allowlist.py"
    result = subprocess.run([sys.executable, str(script), str(registry)], capture_output=True)
    assert result.returncode != 0
    assert registry.read_text() == "# incompatible future SDK\n"
