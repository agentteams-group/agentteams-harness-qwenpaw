"""The moved plugin keeps its host and package contracts."""
import json
import os
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_entry_manifest_and_package_paths():
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    assert manifest["metadata"]["name"] == "qwen-code-bridge"
    assert manifest["runtime"] == "qwenpaw"
    assert manifest["requires"]["qwenpaw"]
    for key in ("backend", "frontend"):
        assert (ROOT / manifest["entry"][key]).is_file()
    assert manifest["package"]["include"]
    for entry in manifest["package"]["include"]:
        assert (ROOT / entry).exists()
    for surface in ("prompts", "skills", "mcp", "hooks"):
        assert surface not in manifest


def test_entry_manifest_against_shared_core_schema():
    core = Path(os.environ.get("AGENTTEAMS_CORE_SOURCE", ROOT.parents[1] / ".deps/AgentTeams"))
    schema = json.loads((core / "plugins/schemas/plugin.schema.json").read_text())
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    jsonschema.Draft202012Validator(schema).validate(manifest)
