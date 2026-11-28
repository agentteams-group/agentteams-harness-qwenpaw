"""Native SDK tracing adapters owned by this Harness."""
import importlib
import wrapt
from opentelemetry import trace
import at_telemetry as telemetry
from at_runtime_adapters import bind, enrich_task, start_turn, register_hook

PACKAGES = ("agentscope", "qwenpaw", "openai")
INSTRUMENT_OPENAI = False


def qwenpaw_runtime(module):
    async def run(wrapped, instance, args, kwargs):
        request = args[0] if args else kwargs["request"]
        request = instance._normalize(request)
        if args:
            args = (request, *args[1:])
        else:
            kwargs = {**kwargs, "request": request}
        metadata = dict(getattr(request, "channel_meta", {}) or {})
        coordination = (getattr(request, "request_context", {}) or {}).get("teamharness_coordination")
        if isinstance(coordination, dict):
            metadata.update(coordination)
        fields = telemetry.turn_fields(getattr(request, "session_id", "") or "unassigned",
                                      user_id=getattr(request, "user_id", ""),
                                      metadata=metadata)
        from at_trace_transport import incoming
        fields.update(incoming({"com.agentteams.trace": metadata.get("otel_carrier", {})}))
        fields = enrich_task(fields, getattr(instance.workspace, "workspace_dir", None))
        span = start_turn(fields, request)
        from at_trace_transport import remember_reply
        remember_reply(span, fields, metadata)
        async for item in telemetry.trace_stream(wrapped(*args, **kwargs), span, fields):
            yield item

    wrapt.wrap_function_wrapper(module, "Runtime.run", run)


def bind_task_context(prepared):
    """Enrich the live root from the public Task adapter's verified receipt."""
    root = telemetry._active_turn.get()
    if root is None or not root.is_recording():
        return
    task = (prepared.get('context') or {}).get('task') or {}
    planning = prepared.get('planning') or []
    main = prepared.get('completion') or (planning[0] if len(planning) == 1 else None)
    task_id = prepared.get('execution') or (prepared.get('review') or [''])[0] or main or task.get('id')
    main_task_id = task.get('mainTaskId') or (task.get('id') if task.get('kind') == 'main' else main)
    fields = {**telemetry._correlation.get(), **{
        key: value for key, value in (('agentteams.task.id', task_id), ('agentteams.main_task.id', main_task_id)) if value
    }}
    with bind(fields):
        telemetry.IdentityProcessor().on_start(root)


def refresh_task_span(agent):
    root = telemetry._active_turn.get()
    if root is None or not root.is_recording() or root.attributes.get("agentteams.task.id"):
        return
    rc = getattr(agent, "_request_context", {}) or {}
    fields = enrich_task(telemetry._correlation.get(), rc.get("workspace_dir") or getattr(agent, "workspace_dir", None))
    with bind(fields):
        telemetry.IdentityProcessor().on_start(root)


def agentscope(module):
    from agentscope.middleware import TracingMiddleware

    class WorkerTracingMiddleware(TracingMiddleware):
        async def on_acting(self, agent, input_kwargs, next_handler):
            call = input_kwargs.get("tool_call")
            fields = dict(telemetry._correlation.get())
            span = trace.get_tracer("agentteams.worker").start_span(f"tool {getattr(call, 'name', 'unknown')}", attributes={
                "gen_ai.operation.name": "execute_tool", "langfuse.observation.type": "tool",
                "gen_ai.tool.name": getattr(call, "name", "unknown"),
                "gen_ai.tool.call.id": getattr(call, "id", ""),
            })
            telemetry.set_io(span, input=getattr(call, "input", None))
            async for item in telemetry.trace_stream(next_handler(**input_kwargs), span, fields):
                yield item
            refresh_task_span(agent)

        async def on_check_permission(self, agent, input_kwargs, next_handler):
            with telemetry.operation("tool.permission") as span:
                telemetry.set_io(span, input=input_kwargs.get("tool_call"))
                result = await next_handler(**input_kwargs)
                telemetry.set_io(span, output=result)
                if str(getattr(result, "behavior", "")).lower() == "deny":
                    telemetry.outcome(span, error=RuntimeError("permission denied"))
                return result

        async def on_compress_context(self, agent, input_kwargs, next_handler):
            refresh_task_span(agent)
            with telemetry.operation("context.compress") as span:
                telemetry.set_io(span, input=input_kwargs)
                result = await next_handler(**input_kwargs)
                telemetry.set_io(span, output=result)
                return result

    def initialize(wrapped, instance, args, kwargs):
        # AgentScope's public middleware API captures model context, tools,
        # tool-call IDs, usage, agent state and errors without SDK double counting.
        middlewares = list((args[4] if len(args) > 4 else kwargs.get("middlewares")) or [])
        # QwenPaw already supplies the standard middleware. Replace that
        # instance so our outcome/task hooks run without double instrumentation.
        middlewares = [WorkerTracingMiddleware() if type(m) is TracingMiddleware else m for m in middlewares]
        if not any(isinstance(m, TracingMiddleware) for m in middlewares):
            middlewares.insert(0, WorkerTracingMiddleware())
        if len(args) > 4:
            args = (*args[:4], middlewares, *args[5:])
        else:
            kwargs["middlewares"] = middlewares
        return wrapped(*args, **kwargs)

    async def reply(wrapped, instance, args, kwargs):
        rc = getattr(instance, "_request_context", {}) or {}
        if telemetry._correlation.get().get("agentteams.turn.id"):
            # Runtime hooks may have synced the task files after run() began.
            fields = enrich_task(telemetry._correlation.get(), rc.get("workspace_dir") or getattr(instance, "workspace_dir", None))
            with bind(fields):
                root = telemetry._active_turn.get()
                if root is not None and root.is_recording():
                    telemetry.IdentityProcessor().on_start(root)
                    from at_trace_transport import remember_reply
                    remember_reply(root, fields, rc.get("channel_meta"))
                return await wrapped(*args, **kwargs)
        session_id = rc.get("session_id") or getattr(instance.state, "session_id", "")
        fields = telemetry.turn_fields(session_id, user_id=rc.get("user_id", ""), metadata=rc.get("channel_meta", {}))
        fields = enrich_task(fields, rc.get("workspace_dir") or getattr(instance, "workspace_dir", None))
        with bind(fields):
            with trace.use_span(start_turn(fields, args or kwargs), end_on_exit=True) as span:
                try:
                    result = await wrapped(*args, **kwargs)
                    telemetry.set_io(span, output=result)
                    telemetry.outcome(span, result)
                    return result
                except BaseException as exc:
                    telemetry.outcome(span, error=exc)
                    raise

    wrapt.wrap_function_wrapper(module, "Agent.__init__", initialize)
    wrapt.wrap_function_wrapper(module, "Agent.reply", reply)


def agentscope_ready(_module):
    # Middleware 2.0.10 imports Agent while it is still initializing. Wait
    # for the package to finish before importing and installing middleware.
    agentscope(importlib.import_module("agentscope.agent._agent"))


def install():
    register_hook(agentscope_ready, "agentscope")
    register_hook(qwenpaw_runtime, "qwenpaw.runtime.runtime")
