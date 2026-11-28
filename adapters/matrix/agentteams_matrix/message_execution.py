"""Bind QwenPaw native turns to durable ordinary Matrix work."""
import asyncio
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import json
import logging
import time
from uuid import uuid4

from task_recovery import Recovery, RecoveryConflict, RecoveryUnavailable
from worker_matrix_delivery import confirm_bound, publish_saved

log = logging.getLogger(__name__)

async def publish_nio_reply(channel, room, checkpoint, text, identity, write_checkpoint, send):
    client = channel._client

    async def whoami():
        response = await client.whoami()
        if getattr(response, "user_id", None) != channel.matrix_user_id:
            raise RecoveryConflict("Matrix session no longer belongs to this Worker")
        return response.user_id, getattr(response, "device_id", None)

    async def bind(next_checkpoint):
        saved = await asyncio.to_thread(write_checkpoint, next_checkpoint)
        confirm_bound(saved["execution"], identity, "qwenpaw", next_checkpoint)

    async def find(txn, body, sender):
        from nio import RoomMessageText, RoomMessagesResponse

        token = None
        seen = set()
        while token not in seen:
            seen.add(token)
            # The SDK decrypts /messages before inspecting the marker. A
            # server-side event-type filter would hide encrypted replies.
            page = await client.room_messages(room, start=token, limit=100, message_filter={"senders": [sender]})
            if not isinstance(page, RoomMessagesResponse) or page.room_id != room:
                raise RecoveryUnavailable("Matrix reply history is unavailable")
            for event in page.chunk:
                if not isinstance(event, RoomMessageText) or event.sender != sender:
                    continue
                source = event.source
                content = source["content"]
                if source.get("room_id", room) != room or content.get("body") != body:
                    continue
                marker = content.get("org.agentteams.reply_txn")
                if marker == txn:
                    return event.event_id
                if not checkpoint.get("reply_attempt") and marker is None and (source.get("unsigned") or {}).get("transaction_id") == txn:
                    return event.event_id
            token = page.end
            if not token:
                break
        return None

    return await publish_saved(checkpoint, text, whoami, bind, find, send)


class MessageExecution:
    def __init__(self, channel, execution):
        self.channel = channel
        self.execution = execution
        self.matrix = execution.matrix
        self.sessions = execution.sessions
        tracker = channel._workspace.task_tracker
        if not hasattr(tracker, "_agentteams_message_turns"):
            tracker._agentteams_message_turns = {}
            tracker._agentteams_message_admission_lock = asyncio.Lock()
        self.turns = tracker._agentteams_message_turns
        self.admission_lock = tracker._agentteams_message_admission_lock

    async def admit(self, key):
        if key in self.turns:
            return None
        entry = await asyncio.to_thread(self.matrix.message, key)
        if entry["recovery"] in {"backfill", "verify"}:
            return None
        claim = await asyncio.to_thread(self.matrix.message_claim, key)
        mode = claim["recovery"]
        if mode == "deliver":
            await self.deliver(claim)
            return None
        if mode != "resume":
            return None
        identity = claim["identity"]
        # A crash after Task submission can leave the supplementary message
        # pending. Its Controller-stored native receipt identifies the Task;
        # finish canonical delivery instead of sampling another chat turn.
        native_ref = (claim["input"].get("native_commit") or {}).get("native_ref", "")
        if native_ref.startswith("matrix:task:"):
            snapshot = await asyncio.to_thread(self.execution.recovery.discover, include_waiting=True)
            for entry in snapshot["tasks"]:
                task, team = entry["task"], entry["team_id"]
                prefix = "matrix:task:" + self.execution.key(team, task["task_id"]) + ":"
                if (task["room_id"] != claim["input"]["room_id"] or not native_ref.startswith(prefix)
                        or task["status"] not in {"submitted", "completed"}):
                    continue
                await self.execution.recover(only_task=task["task_id"])
                current = await asyncio.to_thread(self.execution.recovery.current, task["task_id"], team)
                if current and ((current.get("execution") or {}).get("checkpoint") or {}).get("delivery") == "delivered":
                    await asyncio.to_thread(self.matrix.message_cancel, identity)
                return None
        work = {"kind": "message", "event_key": key}
        view = await asyncio.to_thread(self.sessions.load, work)
        if (view["generation"] != identity["generation"]
                or view["checkpoint_digest"] != claim["checkpoint_digest"]):
            raise RecoveryConflict("Message changed before native session selection")
        prefix = "matrix:message:" + key + ":"
        session_id = (view.get("cache") or {}).get("native_ref", "")
        if session_id.startswith(prefix):
            from qwenpaw.exceptions import AgentStateError
            try:
                native = await self.native_state(session_id, claim["input"]["room_id"])
            except (AgentStateError, OSError, ValueError, KeyError):
                native = None
            if not native or not native.get("agent") or not self.sessions.usable(view, session_id, Recovery.digest(native)):
                session_id = ""
        else:
            session_id = ""
        task_context = None
        import os
        from pathlib import Path
        import yaml
        from runtime_control import task_room_assignment
        runtime_path = os.getenv('TEAMHARNESS_RUNTIME_CONFIG')
        if runtime_path:
            config = yaml.safe_load(Path(runtime_path).read_text())
            snapshot = await asyncio.to_thread(self.execution.recovery.discover)
            selected = task_room_assignment(config, snapshot, claim['input']['room_id'])
            if selected:
                task = selected['task']
                ex = task['execution']
                task_context = {'team_id': selected['team_id'], 'task_id': task['task_id'],
                    **{field: ex[field] for field in ('generation', 'instance_id', 'process_id')}}
                admission = (task_context['team_id'], task_context['task_id'], task_context['generation'])
                if admission in self.execution.native_active:
                    return None  # The active writer accepts supplements before ordinary admission.
                await asyncio.to_thread(self.execution.verify, task_context)
                session_id = await self.execution.select_session(task_context['team_id'], task)
        await asyncio.to_thread(self.matrix.message_current, identity)
        return {"identity": identity, "task_context": task_context,
                "session_id": session_id or prefix + uuid4().hex, "rebuild": not session_id,
                "resume_text": claim["resume_text"] if claim["execution"].get("checkpoint") else "",
                "room": claim["input"]["room_id"], "lock": asyncio.Lock()}

    async def native_state(self, session, room):
        return await self.channel._workspace.session.get_session_state_dict(
            session_id=session, user_id=room, channel=self.channel.channel, allow_not_exist=True)

    async def deliver(self, claim):
        identity = claim["identity"]
        cp = claim["execution"]["checkpoint"]
        source, text = claim["input"], claim["saved_result"]
        event_id = await publish_nio_reply(self.channel, source["room_id"], cp, text, identity,
            lambda next_cp: self.matrix.message_checkpoint(identity, next_cp),
            lambda txn: self.channel.send_message_reply(source, text, txn))
        return await asyncio.to_thread(self.matrix.message_delivered, identity, event_id)

    async def finish(self, identity, text):
        turn = self.turns[identity["event_key"]]
        task_context = turn.get("task_context")
        if task_context:
            task = await asyncio.to_thread(self.execution.verify, task_context, finished=True)
            if task["status"] in {"submitted", "completed"}:
                # The formal report belongs to the Task, even when a normal @
                # message resumed its native session. Never publish it twice.
                await self.execution.finish(task_context, turn["session_id"], text)
                await asyncio.to_thread(self.matrix.message_cancel, identity)
                return
        if not text.strip():
            raise RecoveryConflict("Ordinary Matrix turn has no final result")
        if text.strip() == "NO_REPLY":
            await asyncio.to_thread(self.matrix.message_cancel, identity)
            return
        saved = await asyncio.to_thread(self.matrix.message_save_reply, identity, text)
        delivered = await self.deliver(saved)
        self.turns[identity["event_key"]]["checkpoint_digest"] = delivered["checkpoint_digest"]

    async def stream(self, turn, native):
        identity = turn["identity"]
        key = identity["event_key"]
        try:
            await asyncio.to_thread(self.matrix.message_current, identity)
            async for item in native:
                yield item
        finally:
            try:
                await native.aclose()
                try:
                    await self.remember(turn)
                except (RecoveryConflict, RecoveryUnavailable, OSError, ValueError):
                    log.warning("QwenPaw could not publish the optional native message cache", exc_info=True)
            finally:
                if self.turns.get(key) is turn:
                    del self.turns[key]

    async def remember(self, turn):
        task_context = turn.get('task_context')
        if task_context:
            current = await asyncio.to_thread(self.execution.verify, task_context, finished=True)
            task_context['checkpoint_digest'] = Recovery.digest(current['execution'].get('checkpoint'))
            await self.execution.remember(task_context, turn['session_id'])
        identity = turn["identity"]
        digest = turn.get("checkpoint_digest")
        if not digest:
            return
        work = {"kind": "message", "event_key": identity["event_key"]}
        view = await asyncio.to_thread(self.sessions.load, work)
        if view["checkpoint_digest"] != digest:
            return
        native = await self.native_state(turn["session_id"], turn["room"])
        if native and native.get("agent"):
            await asyncio.to_thread(self.sessions.remember, work, view, identity, turn["session_id"],
                                    Recovery.digest(native), int(time.time()) + 86400)


async def canonical_registration(agent, call, server_path):
    """Bind delegation to the registered native MCP session and builtin file."""
    from pathlib import Path
    import sys
    from agentscope.mcp import MCPClient
    from agentscope.mcp._config import StdioMCPConfig
    from agentscope.tool import MCPTool

    toolkit = getattr(agent, 'toolkit', None)
    if toolkit is None:
        return None
    tool = await toolkit.get_tool(call.name)
    if type(tool) is not MCPTool:
        # QwenPaw 2.2 exposes builtin MCP through the native Driver adapter,
        # rather than AgentScope's legacy MCPTool/tool_groups.mcps registry.
        from qwenpaw.drivers.adapters.agentscope_tool import DriverCapabilityTool
        from qwenpaw.drivers.capabilities import parse_capability_id
        from qwenpaw.drivers.handlers.mcp import MCPDriverHandler
        from qwenpaw.drivers.handlers.mcp_stateful_client import StdIOStatefulClient
        from qwenpaw.drivers.manager import DriverManager

        if type(tool) is not DriverCapabilityTool:
            return None
        invoker = tool._invoker
        manager = getattr(invoker, '__self__', None)
        if (type(manager) is not DriverManager
                or getattr(invoker, '__func__', None) is not DriverManager.invoke_capability):
            return None
        protocol, driver, kind, action, name = parse_capability_id(tool._capability.capability_id)
        if (protocol, kind, action, name) != ('mcp', 'tool', 'invoke', 'task'):
            return None
        handler = manager._handlers.get(driver)
        if type(handler) is not MCPDriverHandler:
            return None
        client = handler._client
        if (type(client) is not StdIOStatefulClient or not client.is_connected
                or client.session is None or not handler.card.enabled):
            return None
        params = client.server_params
        endpoint = handler.card.endpoint
        if (endpoint.get('transport', 'stdio') != 'stdio'
                or endpoint.get('command') != params.command
                or endpoint.get('args', []) != params.args
                or params.command not in {'python', 'python3', sys.executable,
                    '/opt/venv/qwenpaw/bin/python', '/opt/venv/qwenpaw/bin/python3'}
                or len(params.args) != 1
                or Path(params.args[0]).resolve() != Path(server_path).resolve()):
            return None
        return {'origin': 'agentteams.common.task_native',
                'entrypoint': '/opt/agentteams/plugins/teamharness/mcp/common/task_native.py'}
    if getattr(tool._tool, 'name', None) != 'task':
        return None
    for group in toolkit.tool_groups:
        for client in group.mcps:
            if not isinstance(client, MCPClient) or not client.is_stateful:
                continue
            config = client.mcp_config
            args = getattr(config, 'args', None)
            if (getattr(config, 'command', None) not in {'python', 'python3', sys.executable,
                    '/opt/venv/qwenpaw/bin/python', '/opt/venv/qwenpaw/bin/python3'}
                    or not isinstance(config, StdioMCPConfig) or not isinstance(args, list)
                    or len(args) != 1 or Path(args[0]).resolve() != Path(server_path).resolve()
                    or client._session is None or tool._session is not client._session):
                continue
            return {'origin': 'agentteams.common.task_native',
                    'entrypoint': '/opt/agentteams/plugins/teamharness/mcp/common/task_native.py'}
    return None


async def read_only_registration(agent, call):
    """Read effect metadata from the installed SDK binding, never arguments."""
    from agentscope.tool import FunctionTool
    from qwenpaw.agents.tools.file_io import read_file
    from qwenpaw.governance.tool_adapter import _policy_tool_call

    toolkit = getattr(agent, 'toolkit', None)
    tool = await toolkit.get_tool(call.name) if toolkit else None
    from qwenpaw.drivers.adapters.agentscope_tool import DriverCapabilityTool
    from qwenpaw.drivers.manager import DriverManager
    from qwenpaw.drivers.handlers.mcp import MCPDriverHandler

    if type(tool) is DriverCapabilityTool:
        invoker = tool._invoker
        manager = getattr(invoker, '__self__', None)
        capability = tool._capability
        if (type(manager) is not DriverManager
                or getattr(invoker, '__func__', None) is not DriverManager.invoke_capability
                or (capability.protocol, capability.kind, capability.action) != ('mcp', 'tool', 'invoke')):
            return False
        handler = manager._handlers.get(capability.driver_name)
        if type(handler) is not MCPDriverHandler or not handler.card.enabled:
            return False
        try:
            tools = await handler.list_tools()
        except Exception:
            return False
        for registered in tools:
            raw = getattr(registered, '_tool', registered)
            if getattr(raw, 'name', None) == capability.name:
                return getattr(getattr(raw, 'annotations', None), 'readOnlyHint', False) is True
        return False
    return (isinstance(tool, FunctionTool) and type(tool).call is FunctionTool.call
            and type(tool).__call__ in {FunctionTool.__call__, _policy_tool_call}
            and tool._func is read_file)


async def driver_registration(agent, call):
    """Identify the installed SDK driver wrapper, not tool-result text."""
    from qwenpaw.drivers.adapters.agentscope_tool import DriverCapabilityTool
    tool = await agent.toolkit.get_tool(call.name)
    if type(tool) is DriverCapabilityTool and tool._capability.protocol == 'mcp':
        return tool._capability.driver_name
    return None


async def native_shell_registration(agent, call):
    from agentscope.tool import FunctionTool
    from qwenpaw.agents.tools.shell import execute_shell_command
    from qwenpaw.governance.tool_adapter import _policy_tool_call

    tool = await agent.toolkit.get_tool(call.name)
    return (isinstance(tool, FunctionTool) and type(tool).call is FunctionTool.call
            and type(tool).__call__ in {FunctionTool.__call__, _policy_tool_call}
            and tool._func is execute_shell_command)


@contextmanager
def native_driver_receipt(*, native_shell=False):
    """Observe the real SDK result before it flattens server and transport errors."""
    from mcp.types import CallToolResult
    import qwenpaw.drivers.adapters.agentscope_tool as native

    observer = getattr(native, '_agentteams_result_observer', None)
    if observer is None:
        observer = ContextVar('agentteams_driver_result', default=None)
        from agentteams_matrix.mcp_http_receipts import install
        install(observer)
        render = native._tool_chunk_from_driver_result
        def observed(result):
            current = observer.get()
            if current is not None and result.ok and isinstance(result.value, CallToolResult):
                current['received'] = True
            return render(result)
        native._tool_chunk_from_driver_result = observed
        native._agentteams_result_observer = observer
    if native_shell:
        from qwenpaw.agents.tools import shell
        if not getattr(shell, '_agentteams_guard_observed', False):
            check = shell._is_dangerous_self_kill
            def observed_guard(command):
                denied = check(command)
                current = observer.get()
                if denied and current is not None and current.get('native_shell'):
                    current['process_guard_denied'] = True
                return denied
            shell._is_dangerous_self_kill = observed_guard
            shell._agentteams_guard_observed = True
    receipt = {'native_shell': native_shell}
    token = observer.set(receipt)
    try:
        yield receipt
    finally:
        observer.reset(token)


async def guarded_tool(owner, turn, call, next_handler, kwargs, *, registration=None, read_only=False, driver_name=None, native_shell=False):
    """Save the terminal response of the exact admitted SDK call."""
    from agentscope.tool import ToolResponse
    from task_operation import tool_policy

    identity = turn["identity"]
    async with turn["lock"]:
        if tool_policy(registration)['mode'] == 'delegate':
            # The public Task bridge owns its transaction and unknown fence.
            # A generic started operation would block its own submit call.
            async for item in next_handler(**kwargs):
                yield item
            return
        call_id = call.id
        arguments = json.loads(call.input) if isinstance(call.input, str) else call.input
        message = 'event_key' in identity
        begin = owner.message_begin_tools if message else owner.begin_tools
        finish = owner.message_finish_tools if message else owner.finish_tools
        request = {"id": call_id, "name": call.name, "arguments": arguments}
        if driver_name:
            request['pre_effect_error_codes'] = ['DRIVER_POLICY_DENIED']
        if native_shell:
            request.setdefault('pre_effect_error_codes', []).append('NATIVE_PROCESS_GUARD_DENIED')
        if read_only:
            request["read_only"] = True
        operation = await asyncio.to_thread(begin, identity, [request])
        final = None
        with (native_driver_receipt(native_shell=native_shell) if driver_name or native_shell else nullcontext({})) as native_mcp:
            async for item in next_handler(**kwargs):
                if final is not None:
                    raise RecoveryConflict("Native tool emitted output after its final result")
                if isinstance(item, ToolResponse):
                    driver_denied = (driver_name and item.metadata.get('ok') is False
                              and item.metadata.get('type') == 'driver_policy_denied'
                              and item.metadata.get('driver_id') == driver_name
                              and item.metadata.get('operation') == 'invoke')
                    shell_denied = native_shell and native_mcp.get('process_guard_denied') is True
                    denied = driver_denied or shell_denied
                    if item.state != "success" and not (item.state == "error" and (read_only or denied or (driver_name and native_mcp.get('received')))):
                        raise RecoveryConflict("Native tool outcome requires verification")
                    final = item
                else:
                    yield item
        if final is None:
            raise RecoveryConflict("Native tool closed without a confirmed result")
        result = {"id": call_id, "content": final.model_dump(mode="json")["content"]}
        if final.state == "error":
            result["status"] = "error"
            if read_only or denied:
                result["effect"] = "none"
            if denied:
                result['error_code'] = 'NATIVE_PROCESS_GUARD_DENIED' if shell_denied else 'DRIVER_POLICY_DENIED'
        saved = await asyncio.to_thread(finish, identity, operation, [result])
        turn["checkpoint_digest"] = (saved["checkpoint_digest"] if message else
                                      Recovery.digest(saved['execution'].get('checkpoint')))
        yield final
