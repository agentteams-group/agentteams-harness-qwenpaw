"""Load the shared Core test dependencies before collecting native adapters."""
import os
from pathlib import Path
import sys

source = Path(os.environ.get("AGENTTEAMS_CORE_SOURCE", os.environ.get("AT_SOURCE", Path(__file__).resolve().parents[1] / ".deps/AgentTeams")))
sys.path.insert(0, str(source / "shared/lib"))
sys.path.insert(0, str(source / "shared/lib/telemetry"))

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime_config_environment(monkeypatch):
    # Worker setup exports this path; do not leak a deleted fixture to the next test.
    monkeypatch.setenv("TEAMHARNESS_RUNTIME_CONFIG", "")
