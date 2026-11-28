"""Startup discovery must not include providers configured after readiness."""
import importlib.util
from pathlib import Path


def test_startup_catalog_snapshot_precedes_readiness_and_is_idempotent():
    script = Path(__file__).resolve().parents[1] / "scripts/patch-qwenpaw-startup-discovery.py"
    spec = importlib.util.spec_from_file_location("startup_patch", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = ("            def _mark_core_agents_ready(_results: dict[str, bool]) -> None:\n"
              "                app.state.startup_ready.set()\n"
              "            await start_agents(on_core_ready=_mark_core_agents_ready)\n"
              "            startup_provider_ids = provider_manager.startup_sync_provider_ids()\n"
              "            sync(startup_provider_ids)\n")
    patched = module.apply_patch(source)
    assert patched.index("startup_provider_ids =") < patched.index("app.state.startup_ready.set()")
    assert patched.count("startup_provider_ids =") == 1
    assert "sync(startup_provider_ids)" in patched
    assert module.apply_patch(patched) == patched
