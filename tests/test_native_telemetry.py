"""Harness-owned native tracing boundary checks against shared Core telemetry."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("AT_SOURCE", ROOT / ".deps/AgentTeams"))
NATIVE = ROOT / "telemetry"
sys.path.insert(0, str(SOURCE / "shared/lib"))
sys.path.insert(0, str(SOURCE / "shared/lib/telemetry"))
sys.path.insert(0, str(NATIVE))
from opentelemetry import context, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import at_telemetry as telemetry
import at_runtime_adapters as common
import at_trace_transport as transport


def test_qwenpaw_normalizes_dictionary_requests_before_trace_identity(monkeypatch):
    import at_native_telemetry as adapters
    provider = TracerProvider()
    monkeypatch.setattr(trace, "get_tracer", lambda *args, **kwargs: provider.get_tracer("test"))
    monkeypatch.setattr(common, "task_resolver", lambda: None)

    class Runtime:
        workspace = SimpleNamespace(workspace_dir=None)

        def _normalize(self, request):
            return SimpleNamespace(**request) if isinstance(request, dict) else request

        async def run(self, request):
            yield dict(telemetry._correlation.get())

    adapters.qwenpaw_runtime(SimpleNamespace(Runtime=Runtime))

    async def run():
        return [row async for row in Runtime().run({"session_id": "real-session", "user_id": "user", "channel_meta": {"room_id": "!task:matrix"}})]

    fields = asyncio.run(run())[0]
    assert fields["agentteams.runtime.session_id"] == "real-session"
    assert fields["agentteams.room.id"] == "!task:matrix"


def test_qwenpaw_refreshes_task_identity_after_runtime_hooks_sync_files(monkeypatch):
    import types
    import at_native_telemetry as adapters
    monkeypatch.setenv("AGENTTEAMS_WORKER_NAME", "worker")
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(telemetry.IdentityProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr(trace, "get_tracer", lambda *args, **kwargs: tracer)
    middleware = types.ModuleType("agentscope.middleware")
    middleware.TracingMiddleware = type("TracingMiddleware", (), {})
    monkeypatch.setitem(sys.modules, "agentscope.middleware", middleware)
    monkeypatch.setattr(common, "task_resolver", lambda: lambda *args, **kwargs: {"project_id": "project", "task": {"task_id": "work", "notifyTxn": "dispatch-one"}})

    class Agent:
        workspace_dir = "/tmp/workspace"
        _request_context = {"channel_meta": {"room_id": "!task:matrix"}}

        def __init__(self, **kwargs):
            pass

        async def reply(self):
            with tracer.start_as_current_span("model"):
                return dict(telemetry._correlation.get())

    adapters.agentscope(SimpleNamespace(Agent=Agent))

    async def run():
        root = tracer.start_span("worker.turn")
        async def execute():
            yield await Agent().reply()
        return [row async for row in telemetry.trace_stream(execute(), root, {"agentteams.turn.id": "turn", "agentteams.room.id": "!task:matrix"})]

    assert asyncio.run(run())[0]["agentteams.task.id"] == "work"
    root = next(span for span in exporter.get_finished_spans() if span.name == "worker.turn")
    assert root.attributes["agentteams.task.id"] == "work"
    assert "task:work" in root.attributes["langfuse.trace.tags"]
    assert telemetry._correlation.get() == {}


def test_qwenpaw_api_turn_uses_coordination_metadata(monkeypatch):
    from types import SimpleNamespace
    import at_native_telemetry as adapters
    callbacks = []
    captured = []
    monkeypatch.setattr(adapters.wrapt, 'wrap_function_wrapper', lambda module, name, callback: callbacks.append(callback))
    monkeypatch.setattr(adapters, 'start_turn', lambda fields, request: captured.append(fields) or object())
    monkeypatch.setattr(adapters, 'enrich_task', lambda fields, workspace: fields)
    monkeypatch.setitem(sys.modules, 'at_trace_transport', SimpleNamespace(incoming=lambda content: {}, remember_reply=lambda *a: None))
    async def stream(iterator, span, fields):
        async for item in iterator:
            yield item
    monkeypatch.setattr(telemetry, 'trace_stream', stream)
    adapters.qwenpaw_runtime(object())
    meta = {'room_id': '!task:test', 'task_id': 'atomic-1', 'project_id': 'request-1', 'execution_generation': 2}
    request = SimpleNamespace(session_id='coordination-task:atomic-1:dispatch-1', user_id='@requester:test',
        channel_meta={}, request_context={'teamharness_coordination': meta})
    runtime = SimpleNamespace(_normalize=lambda value: value, workspace=SimpleNamespace(workspace_dir=None))
    async def wrapped(*args, **kwargs):
        yield 'ok'
    async def run():
        return [item async for item in callbacks[0](wrapped, runtime, (request,), {})]
    assert asyncio.run(run()) == ['ok']
    assert captured[0]['agentteams.task.id'] == 'atomic-1'
    assert captured[0]['agentteams.room.id'] == '!task:test'
    assert captured[0]['agentteams.project.id'] == 'request-1'
    assert captured[0]['agentteams.execution.generation'] == 2


def test_agentscope_middleware_import_order_does_not_break_worker_startup(tmp_path):
    package = tmp_path / 'agentscope'
    (package / 'middleware').mkdir(parents=True)
    (package / 'agent').mkdir()
    (package / '__init__.py').write_text('from . import middleware\n')
    (package / 'middleware/__init__.py').write_text('from ._tracing import TracingMiddleware\n')
    (package / 'middleware/_tracing.py').write_text('from ..agent._agent import Agent\nclass TracingMiddleware: pass\n')
    (package / 'agent/__init__.py').write_text('from ._agent import Agent\n')
    (package / 'agent/_agent.py').write_text('class Agent:\n    def __init__(self, middlewares=None): self.middlewares = middlewares\n    async def reply(self): pass\n')
    environment = {**os.environ, 'AGENTTEAMS_OTEL_ENABLED': 'true', 'AGENTTEAMS_OTEL_RUNTIME': 'qwenpaw',
                   'PYTHONPATH': str(SOURCE / 'shared/lib/telemetry') + os.pathsep + str(NATIVE) + os.pathsep + str(tmp_path)}
    result = subprocess.run([sys.executable, '-c', 'from agentscope.agent import Agent; from agentscope.middleware import TracingMiddleware; assert isinstance(Agent().middlewares[0], TracingMiddleware); agent = Agent(middlewares=[TracingMiddleware()]); assert len(agent.middlewares) == 1; assert type(agent.middlewares[0]) is not TracingMiddleware'],
                            env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_task_identity_arriving_after_turn_start_reaches_children_and_carrier(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import at_native_telemetry as adapters
    from at_trace_transport import carrier, incoming
    sys.path.insert(0, str(ROOT / "adapters/teamharness"))
    from task_trace import find_task_trace_context
    monkeypatch.setattr(common, "task_resolver", lambda: find_task_trace_context)
    monkeypatch.setenv("AGENTTEAMS_WORKER_NAME", "alice")
    provider = TracerProvider()
    provider.add_span_processor(telemetry.IdentityProcessor())
    tracer = provider.get_tracer("test")
    fields = {"agentteams.room.id": "!room:test", "agentteams.turn.id": "turn"}
    correlation = telemetry._correlation.set(fields)
    root = tracer.start_span("worker.turn")
    active = telemetry._active_turn.set(root)
    agent = SimpleNamespace(workspace_dir=tmp_path)
    try:
        adapters.refresh_task_span(agent)
        assert "agentteams.task.id" not in root.attributes
        task_dir = tmp_path / "shared/tasks/one"
        task_dir.mkdir(parents=True)
        (task_dir / "meta.json").write_text(json.dumps({"task_id": "one", "project_id": "project", "room_id": "!room:test", "assigned_to": "@alice:test", "status": "in_progress"}))
        adapters.refresh_task_span(agent)
        assert root.attributes["agentteams.task.id"] == "one"
        assert root.attributes["agentteams.project.id"] == "project"
        with trace.use_span(root, end_on_exit=False):
            with tracer.start_as_current_span("model") as child:
                assert child.attributes["agentteams.task.id"] == "one"
                received = incoming({"com.agentteams.trace": carrier()}, room_id="!other:test")
                assert received["agentteams.upstream.task_id"] == "one"
                assert received["agentteams.project.id"] == "project"
        # An unrelated trace must not pick up the cached task from this turn.
        with tracer.start_as_current_span("unrelated") as unrelated:
            assert "agentteams.task.id" not in unrelated.attributes
    finally:
        telemetry._active_turn.reset(active)
        telemetry._correlation.reset(correlation)
        root.end()
        provider.shutdown()



@pytest.mark.parametrize('prepared,task_id,main_id', [
    ({'planning': ['main-one']}, 'main-one', 'main-one'),
    ({'execution': 'exec-one', 'context': {'task': {'id': 'exec-one', 'mainTaskId': 'main-one'}}}, 'exec-one', 'main-one'),
    ({'completion': 'main-one'}, 'main-one', 'main-one'),
    ({'context': {'task': {'id': 'main-one', 'kind': 'main'}}}, 'main-one', 'main-one'),
])
def test_verified_task_admission_tags_root_and_children_without_legacy_project(prepared, task_id, main_id):
    import at_native_telemetry as adapters
    provider = TracerProvider()
    provider.add_span_processor(telemetry.IdentityProcessor())
    tracer = provider.get_tracer('fixture')
    root = tracer.start_span('worker.turn')
    token = telemetry._active_turn.set(root)
    try:
        adapters.bind_task_context(prepared)
        assert root.attributes['agentteams.task.id'] == task_id
        assert root.attributes['agentteams.main_task.id'] == main_id
        with trace.use_span(root, end_on_exit=False):
            with tracer.start_as_current_span('tool') as child:
                assert child.attributes['agentteams.main_task.id'] == main_id
    finally:
        telemetry._active_turn.reset(token)
        root.end()
        provider.shutdown()
