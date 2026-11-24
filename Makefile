include versions.env

IMAGE ?= ghcr.io/agentteams-group/agentteams-harness-qwenpaw
VERSION ?= v0.1.0
PLATFORMS ?= linux/amd64,linux/arm64
AT_SOURCE ?= .deps/AgentTeams
BUILD_ARGS = --build-context shared=$(AT_SOURCE)/shared/lib --build-context agentteams=$(AT_SOURCE)
export AGENTTEAMS_CORE_SOURCE := $(abspath $(AT_SOURCE))
export PYTHONPATH := $(CURDIR)/src:$(CURDIR)/adapters/matrix:$(PYTHONPATH)

.PHONY: prepare build push test smoke
prepare:
	bash scripts/prepare-deps.sh
build: prepare
	docker build $(BUILD_ARGS) $(DOCKER_BUILD_ARGS) -t $(IMAGE):$(VERSION) .
push: prepare
	docker buildx build --platform $(PLATFORMS) $(BUILD_ARGS) $(DOCKER_BUILD_ARGS) -t $(IMAGE):$(VERSION) --push .
test:
	python3 -m pytest tests -q
smoke:
	docker run --rm --network none -e AGENTTEAMS_CORE_SOURCE=/at -e PYTHONPATH=/candidate/adapters/matrix:/opt/agentteams/scripts/lib -v "$(CURDIR):/candidate:ro" -v "$(abspath $(AT_SOURCE)):/at:ro" --entrypoint /opt/venv/qwenpaw/bin/python $(IMAGE):$(VERSION) /candidate/tests/native_hitl_smoke.py
	docker run --rm --network none -v "$(CURDIR)/scripts/smoke_plugin_loader.py:/tmp/smoke_plugin_loader.py:ro" --entrypoint /opt/venv/qwenpaw/bin/python $(IMAGE):$(VERSION) /tmp/smoke_plugin_loader.py --plugin-dir /opt/agentteams/plugins/agentteams-matrix-channel
	docker run --rm --entrypoint /opt/venv/qwenpaw/bin/python $(IMAGE):$(VERSION) -c 'import qwenpaw_worker; from qwenpaw.plugins.loader import PluginLoader'
	docker run --rm --network none -v "$(CURDIR)/tests/native_managed_model_smoke.py:/tmp/native_managed_model_smoke.py:ro" --entrypoint /opt/venv/qwenpaw/bin/python $(IMAGE):$(VERSION) /tmp/native_managed_model_smoke.py
	docker run --rm --entrypoint sh $(IMAGE):$(VERSION) -ec 'for helper in install-worker-skill.sh safe-extract-worker-skill.py push-worker-skills.sh; do test -r "/opt/agentteams/agent/skills/worker-management/scripts/$$helper"; done'

assistant-protocol-smoke:
	docker run --rm --network none -e AT_SOURCE=/at -v "$(abspath $(AT_SOURCE)):/at:ro" -v "$(CURDIR):/harness:ro" --entrypoint /opt/venv/qwenpaw/bin/python $(IMAGE):$(VERSION) /harness/tests/native_assistant_smoke.py
