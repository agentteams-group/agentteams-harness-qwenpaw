# agentteams-harness-qwenpaw

QwenPaw Worker and Team Leader runtime integration for [AgentTeams](https://github.com/agentscope-ai/AgentTeams).
Migrated from `qwenpaw/` at AgentTeams commit `67c8e807c4d210537b753f8121710181825bdfac` (Apache-2.0).
The runtime remains pinned to **QwenPaw 2.2.2.post1**.

The pinned AgentTeams Matrix channel publishes request-bound Dashboard approval
cards and native approval/rejection/expiry receipts. Updating the channel build
input is required to include the shared ApprovalService bridge; a Dashboard-only
update cannot resume or reject a native QwenPaw approval waiter.

The managed QwenPaw Task adapter and Matrix channel live in `adapters/`.
The image combines these runtime adapters with the shared Task and Matrix assets
from the AgentTeams commit in `versions.env`. Native adapter tests exercise
that same shared Core checkout.

## Scope

`src/qwenpaw_worker/` owns configuration, workspace sync, lifecycle and runtime
upgrades. `scripts/` contains the entrypoint, compatibility patches, plugin
loader smoke and upgrade orchestrator. `tests/` preserves the runtime tests.

Manager startup, Controller/CRDs, Helm, installers, role templates and shared
plugins remain in AgentTeams. The QwenPaw Manager image is still built there.
`versions.env` pins the AgentTeams source supplying shared libraries, Matrix
and upgrade plugins, Qwen Code Bridge, TeamHarness and WorkerFlow bundles.
Dependencies are prepared under the ignored `.deps/AgentTeams` directory.

See [the runtime guide](docs/runtime.md) for lifecycle, configuration, plugin
boundaries and opt-in image integration tests.

Assigned skill sync, malformed frontmatter, and enable failures are non-blocking:
healthy skills continue loading and the Agent stays ready. Affected skills are
named in warning logs and `heartbeat.json` (`details.skillWarnings`), and Worker
heartbeats report `skillWarnings` to supporting Controllers for Dashboard display.
Failed assignments retry on the runtime-config polling interval and clear their
warnings after recovery. Skills with invalid local content are disabled; a
transient storage sync failure retains the previously loaded runtime copy.

## Build and test

WorkerFlow is disabled by default for Workers and Team Leaders. AT TeamHarness
collaboration remains available. To enable Worker-local sub-agent workflows,
set `AGENTTEAMS_QWENPAW_WORKERFLOW_ENABLED=true` and restart the Worker. Enabling
it starts additional MCP processes and increases memory pressure. Managers do
not enable WorkerFlow.

The image loads console, AgentTeams Matrix and DingTalk channels by default.
`QWENPAW_ENABLED_CHANNELS` selects native channels before their SDKs are imported.
If you use other native channels, include them in this comma-separated list;
explicitly set an empty string to restore discovery of all native channels.
External channel plugins, memory, model/tool execution and tracing are retained.

Use Python 3.11–3.13, Ruby, Git, and Docker with BuildKit:

```sh
make prepare
python3 -m pip install -e . 'matrix-nio>=0.25,<0.27' 'pytest>=8.3,<10' 'pytest-asyncio>=0.23,<2'
make test
make build
make smoke
```

Unit tests include compatibility checks against the pinned AgentTeams source.
Shell tests under `tests/integration/` require Docker and MinIO; model tests
also require model credentials and are separate from unit tests.

CI runs tests and builds the image. Version tags (`v*`) publish amd64/arm64
images to `ghcr.io/agentteams-group/agentteams-harness-qwenpaw`.
A normal main-branch push does not publish an image.

AgentTeams consumes this repository as a pinned `qwenpaw/` submodule, passing
its root as the `agentteams` build context and `shared/lib` as `shared`.
The existing environment variables and persisted `.qwenpaw` workspace contract
are unchanged.
