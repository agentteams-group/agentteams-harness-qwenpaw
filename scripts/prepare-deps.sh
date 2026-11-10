#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source versions.env
if [ ! -d .deps/AgentTeams/.git ]; then
    git clone --no-checkout --depth 1 https://github.com/agentscope-ai/AgentTeams.git .deps/AgentTeams
fi
git -C .deps/AgentTeams fetch --depth 1 origin "$AT_REF"
git -C .deps/AgentTeams checkout --detach "$AT_REF"
cd .deps/AgentTeams
OUT_DIR=dist/adapters/qwenpaw ruby plugins/teamharness/adapters/qwenpaw/scripts/build-qwenpaw-plugin.rb plugins/teamharness/plugin.yaml
OUT_DIR=dist/adapters/qwenpaw ruby plugins/workerflow/adapters/qwenpaw/scripts/build-qwenpaw-plugin.rb plugins/workerflow/plugin.yaml
