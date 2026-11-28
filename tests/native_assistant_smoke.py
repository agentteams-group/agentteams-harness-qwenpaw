"""Installed QwenPaw SDK channel admission; no model or private Console API."""
import asyncio
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

core = Path(os.environ.get("AT_SOURCE") or os.environ.get("AGENTTEAMS_CORE_SOURCE") or Path(__file__).resolve().parents[1] / ".deps/AgentTeams")
harness = Path(__file__).resolve().parents[1]
os.environ.setdefault("AGENTTEAMS_CORE_SOURCE", str(core))
sys.path[:0] = [str(core / "tests/lib"), str(core / "shared/lib"),
    str(harness / "adapters/matrix"), str(harness / "adapters")]
from assistant_fixture import AssistantFixture
from agentteams_matrix.channel import AgentTeamsMatrixChannel
from qwenpaw.app.channels.base import BaseChannel
from nio import RoomMessageNotice
from agentteams_matrix.managed_sync import ManagedNioSync


async def main():
    with AssistantFixture() as f:
        channel = object.__new__(AgentTeamsMatrixChannel)
        channel._user_id = f.own
        channel._task_execution = channel._session_receipts = None
        channel._workspace_dir = Path(f.directory.name)
        channel._is_dm_room = AsyncMock(return_value=True)
        channel._is_channel_disabled = Mock(return_value=False)
        channel._announce_session_receipts = AsyncMock()
        channel._send_read_receipt = channel._send_typing = AsyncMock()
        channel._session_source_ids = lambda *_: ["$assistant"]
        channel._get_display_name = lambda *_: "Leader"
        channel._client = None
        channel._record_history = Mock()
        admitted = []
        channel._enqueue = admitted.append
        channel._enqueue_matrix_input = lambda payload, _turn: admitted.append(payload)
        channel._handle_direct_control = AsyncMock(return_value=False)
        room = SimpleNamespace(room_id=f.room, users={}, user_name=lambda _: "Leader")
        event = RoomMessageNotice.from_dict({"event_id": "$assistant", "sender": f.own,
            "origin_server_ts": 1, "type": "m.room.message", "content": f.content()})
        managed = object.__new__(ManagedNioSync)
        managed.channel = channel
        handler = managed.handler(event)
        assert handler is not None
        await handler(room, event)
        assert len(admitted) == 1
        assert admitted[0]["content_parts"][0].text == f.text
        assert admitted[0]["meta"]["teamharness_coordination_session"] == f.control["session_id"]
        assert not admitted[0]["meta"].get("task_id")
        # All background assistant output is submitted through Controller.
        # A skipped cycle must not leak native progress, reasoning or final text.
        meta = admitted[0]["meta"]
        # Run the installed SDK's real lifecycle hooks: a scheduled reminder
        # must not consume first-Human bootstrap or mark onboarding complete.
        from agentscope.message import Msg
        from qwenpaw.hooks.bootstrap.bootstrap_hook import BootstrapHook
        from qwenpaw.hooks.cron.cron_hook import CronContextHook
        from teamharness.plugin import _register_coordination_hook
        hooks = []
        _register_coordination_hook(SimpleNamespace(register_runtime_hook=hooks.append))
        scheduled = next((hook for hook in hooks if hook.name == "teamharness_scheduled_source"), None)
        workspace = Path(f.directory.name) / "bootstrap-fixture"
        workspace.mkdir()
        (workspace / "BOOTSTRAP.md").write_text("Only the first Human request starts onboarding")
        request = SimpleNamespace(channel_meta=meta, session_source="matrix")
        ctx = SimpleNamespace(request=request, extras={}, workspace_dir=workspace,
            input_msgs=[Msg(name="system", content=[{"type":"text","text":"Scheduled assistant reminder"}], role="user")], agent_config=None)
        original_message = ctx.input_msgs[0].model_dump()
        if scheduled is not None:
            assert scheduled.priority < CronContextHook.priority
            await scheduled.run(ctx)
        await CronContextHook().run(ctx)
        await BootstrapHook().run(ctx)
        assert not (workspace / ".bootstrap_completed").exists(), "background reminder consumed Human bootstrap"
        assert ctx.input_msgs[0].model_dump() == original_message
        human = SimpleNamespace(request=SimpleNamespace(channel_meta={}, session_source="matrix"),
            extras={}, workspace_dir=workspace,
            input_msgs=[Msg(name="user", content=[{"type":"text","text":"ASSISTANT_DIGEST_DUE is just ordinary Human text"}], role="user")], agent_config=None)
        if scheduled is not None:
            await scheduled.run(human)
        await CronContextHook().run(human)
        await BootstrapHook().run(human)
        assert not human.extras.get("is_cron")
        assert (workspace / ".bootstrap_completed").exists(), "real Human bootstrap was suppressed"
        channel._client = Mock()
        channel._client.room_send = AsyncMock()
        channel._ensure_thread_root = AsyncMock(side_effect=AssertionError("assistant created visible progress"))
        await channel.send(f.room, "No new information", meta)
        assert await channel.on_event_content(None, f.room, None, meta) is True
        await channel.on_event_message_completed(None, f.room, None, meta)
        await channel.on_streaming_start(None, f.room, None, meta, "message")
        await channel.on_streaming_end(None, f.room, None, meta, "message", "NO_REPLY")
        await channel._on_process_completed(None, f.room, meta)
        channel._client.room_send.assert_not_awaited()
        async def stream(_self, _payload):
            yield "native event"
        channel._publish_direct_execution = AsyncMock(side_effect=AssertionError("background check published direct status"))
        with patch.object(BaseChannel, "_stream_with_tracker", stream):
            output = [item async for item in channel._stream_matrix_payload({"meta": {**meta, "is_dm":True, "room_id":f.room, "event_id":"$assistant"}})]
        assert output == ["native event"]
        channel._publish_direct_execution.assert_not_awaited()
        ordinary = RoomMessageNotice.from_dict({"event_id": "$ordinary", "sender": f.own,
            "origin_server_ts": 2, "type": "m.room.message",
            "content": {"msgtype": "m.notice", "body": "Unrelated notice"}})
        await channel._on_coordination_notice(room, ordinary)
        assert managed.handler(ordinary) is None
        assert len(admitted) == 1
        f.context["owner"] = "replaced"
        await channel._on_coordination_notice(room, event)
        assert len(admitted) == 1
        print("QwenPaw native Teams reminder admission: PASS")


if __name__ == "__main__":
    asyncio.run(main())
