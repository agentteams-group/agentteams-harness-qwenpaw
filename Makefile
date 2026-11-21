include versions.env

IMAGE ?= ghcr.io/agentteams-group/agentteams-harness-qwenpaw
VERSION ?= v0.1.0
PLATFORMS ?= linux/amd64,linux/arm64
AT_SOURCE ?= .deps/AgentTeams
BUILD_ARGS = --build-context shared=$(AT_SOURCE)/shared/lib --build-context agentteams=$(AT_SOURCE)
export PYTHONPATH := $(CURDIR)/src:$(abspath $(AT_SOURCE))/plugins/agentteams-matrix-channel:$(PYTHONPATH)

.PHONY: prepare build push test smoke assistant-protocol-smoke
prepare:
	bash scripts/prepare-deps.sh
build: prepare
	docker build $(BUILD_ARGS) $(DOCKER_BUILD_ARGS) -t $(IMAGE):$(VERSION) .
push: prepare
	docker buildx build --platform $(PLATFORMS) $(BUILD_ARGS) $(DOCKER_BUILD_ARGS) -t $(IMAGE):$(VERSION) --push .
test:
	python3 -m pytest tests -q
	PYTHONPATH=$(abspath $(AT_SOURCE))/shared/lib:$(PYTHONPATH) python3 -m pytest $(AT_SOURCE)/plugins/tests/agentteams-matrix/test_qwenpaw_task_recovery.py -q
smoke:
	docker run --rm --entrypoint /opt/venv/qwenpaw/bin/python $(IMAGE):$(VERSION) -c 'import qwenpaw_worker; from qwenpaw.plugins.loader import PluginLoader'
	docker run --rm --network none -v "$(CURDIR)/tests/native_managed_model_smoke.py:/tmp/native_managed_model_smoke.py:ro" --entrypoint /opt/venv/qwenpaw/bin/python $(IMAGE):$(VERSION) /tmp/native_managed_model_smoke.py
	docker run --rm --entrypoint sh $(IMAGE):$(VERSION) -ec 'for helper in install-worker-skill.sh safe-extract-worker-skill.py push-worker-skills.sh; do test -r "/opt/agentteams/agent/skills/worker-management/scripts/$$helper"; done'

assistant-protocol-smoke:
	docker run --rm --network none -e AT_SOURCE=/at -v "$(abspath $(AT_SOURCE)):/at:ro" -v "$(CURDIR)/tests:/cert:ro" --entrypoint /opt/venv/qwenpaw/bin/python $(IMAGE):$(VERSION) /cert/native_assistant_smoke.py
	docker run --rm --entrypoint agt $(IMAGE):$(VERSION) assistant receipt --help
