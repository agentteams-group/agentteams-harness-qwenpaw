"""Native outgoing progress is marked at its producer, not filtered heuristically."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agentteams_matrix.channel import AgentTeamsMatrixChannel, _MATRIX_OWN_THREAD_ROOT_KEY


def test_tool_thinking_and_placeholder_are_progress_but_business_text_is_not():
    channel = AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
    channel._client = object()
    channel._active_thread_roots = {}
    channel._write_attachment_context = lambda *args: None
    channel._room_send_with_retry = AsyncMock(return_value=SimpleNamespace(event_id='$progress'))
    channel.send_content_parts = AsyncMock()
    async def run():
        meta = {}
        await channel._ensure_thread_root('!task:test', meta)
        assert channel._room_send_with_retry.call_args.args[2]['org.agentteams.progress_only'] is True
        await channel._send_or_queue_thread_parts('!task:test', ['actual-tool-event'], meta)
        forwarded = channel.send_content_parts.call_args.args[2]
        assert forwarded['org.agentteams.progress_only'] is True
        tool = channel._matrix_text_content('!task:test', 'Tool input/output', forwarded, 'm.text')
        assert tool['org.agentteams.progress_only'] is True
        assert tool['m.mentions']['user_ids'] == []
        await channel._send_streaming_thread_text('!task:test', meta, 'Thinking')
        assert channel._room_send_with_retry.call_args.args[2]['org.agentteams.progress_only'] is True
        final = channel._matrix_text_content('!task:test', 'Business result', meta, 'm.text')
        assert 'org.agentteams.progress_only' not in final
        assert meta[_MATRIX_OWN_THREAD_ROOT_KEY] == '$progress'
    asyncio.run(run())


def test_internal_handoff_notice_reaches_the_verified_ingress():
    channel = AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
    channel._on_room_event = AsyncMock()
    event = SimpleNamespace(source={'content': {'org.agentteams.coordination': {
        'channel_meta': {'teamharness_coordination': {'kind': 'handoff'}}}}})
    room = SimpleNamespace(room_id='!task:test')
    asyncio.run(channel._on_coordination_notice(room, event))
    channel._on_room_event.assert_awaited_once_with(room, event)
    event.source['content']['org.agentteams.coordination']['channel_meta']['teamharness_coordination']['kind'] = 'forged'
    asyncio.run(channel._on_coordination_notice(room, event))
    channel._on_room_event.assert_awaited_once()


def test_handoff_with_main_task_does_not_duplicate_its_session_suffix(monkeypatch):
    import task_coordination
    channel = AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
    channel._user_id = '@leader:test'
    channel._task_execution = object()
    channel._teamharness_self_trigger = lambda *args: None
    channel._is_dm_room = AsyncMock(side_effect=RuntimeError('verified handoff admitted'))
    event_id = 'a' * 32
    meta = {'sender_id': '@leader:test', 'agent_matrix_user_id': '@leader:test',
            'room_id': '!task:test', 'teamharness_coordination': {
                'id': event_id, 'kind': 'handoff', 'main_task_id': 'main'}}
    control = {'session_id': 'coordination-handoff:' + event_id, 'channel_meta': meta,
               'input': [{'content': [{'text': 'Plan the admitted main Task'}]}]}
    monkeypatch.setattr(task_coordination, 'notification', lambda *args, **kwargs: {
        'session_id': control['session_id'], 'meta': meta, 'text': 'Plan the admitted main Task'})
    event = SimpleNamespace(source={'content': {'org.agentteams.coordination': control}},
        event_id='$handoff', sender='@leader:test', body='Internal planning wakeup')
    room = SimpleNamespace(room_id='!task:test')
    import pytest
    with pytest.raises(RuntimeError, match='verified handoff admitted'):
        asyncio.run(channel._on_room_event(room, event))
    channel._is_dm_room.assert_awaited_once()
    channel._is_dm_room.reset_mock()
    control['session_id'] += ':' + event_id
    asyncio.run(channel._on_room_event(room, event))
    channel._is_dm_room.assert_not_awaited()


def test_internal_coordination_does_not_advertise_private_conversation_controls(monkeypatch):
    channel = AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
    channel._direct_runs = {}
    channel._publish_direct_execution = AsyncMock()
    async def native_stream(self, payload):
        yield 'native tool progress'
    monkeypatch.setattr(AgentTeamsMatrixChannel.__mro__[1], '_stream_with_tracker', native_stream)
    async def run():
        payload = {'meta': {'room_id': '!task:test', 'event_id': '$control', 'is_dm': True,
                            'teamharness_matrix_coordination': True}}
        assert [part async for part in channel._stream_matrix_payload(payload)] == ['native tool progress']
    asyncio.run(run())
    channel._publish_direct_execution.assert_not_awaited()


def test_planning_control_uses_source_identity_without_a_chat_placeholder():
    from unittest.mock import Mock

    channel = AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
    channel._enqueue = Mock()
    channel._room_send_with_retry = AsyncMock()
    channel._client = object()
    meta = {'room_id': '!task:test', 'event_id': '$planning',
            'teamharness_matrix_coordination': True,
            'teamharness_coordination': {'kind': 'handoff'}}
    channel._enqueue_matrix_input({'meta': meta}, None)
    assert meta['org.agentteams.run_id'] == 'qwenpaw:message:!task:test:$planning'
    asyncio.run(channel._ensure_thread_root('!task:test', meta))
    channel._room_send_with_retry.assert_not_awaited()
