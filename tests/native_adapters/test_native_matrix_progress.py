"""Actual SDK envelope IDs/results survive the Matrix display boundary."""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from adapter_fixture import core_source
sys.path.insert(0, str(core_source() / 'shared/lib'))


def test_native_envelope_parallel_tool_results_are_id_correlated_and_display_only():
    from agentscope.event import (ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
        ToolResultStartEvent, ToolResultTextDeltaEvent, ToolResultEndEvent)
    from agentscope.message import ToolResultState
    from qwenpaw.runtime.envelope import Envelope
    from qwenpaw.schemas import Message, RunStatus
    from agentteams_matrix.channel import AgentTeamsMatrixChannel

    async def check():
        sent = []
        async def send(room, event_type, content):
            assert (room, event_type) == ('!task:test', 'm.room.message')
            sent.append(content)
            return SimpleNamespace(event_id='$' + str(len(sent)))
        channel = AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
        channel._client = object()
        channel._active_thread_roots = {}
        channel._write_attachment_context = lambda *args: None
        channel._room_send_with_retry = send
        channel._tool_output_media_parts = lambda event: []
        channel.show_tool_calls = channel.show_tool_results = channel.show_thinking = True
        channel.send_content_parts = AsyncMock()
        envelope, meta = Envelope('native-fixture'), {}
        async def translate(event):
            async for part in envelope.translate_event(event):
                if isinstance(part, Message) and part.status == RunStatus.Completed:
                    await channel.on_event_message_completed(None, '!task:test', part, meta)
        for identity, file in [('one', 'index.html'), ('two', 'missing.html')]:
            await translate(ToolCallStartEvent(reply_id='native', tool_call_id=identity, tool_call_name='read_file'))
            await translate(ToolCallDeltaEvent(reply_id='native', tool_call_id=identity,
                delta=json.dumps({'path':file, 'api_key':'private'})))
            await translate(ToolCallEndEvent(reply_id='native', tool_call_id=identity))
        for identity, output, state in [('two','not found',ToolResultState.ERROR), ('one','<html>ok</html>',ToolResultState.SUCCESS)]:
            await translate(ToolResultStartEvent(reply_id='native', tool_call_id=identity, tool_call_name='read_file'))
            await translate(ToolResultTextDeltaEvent(reply_id='native', tool_call_id=identity, delta=output))
            await translate(ToolResultEndEvent(reply_id='native', tool_call_id=identity, state=state))
        await meta['_matrix_native_progress'].finish()
        blocks = [(row.get('m.new_content') or row)['org.agentteams.run']['blocks'][0] for row in sent[1:]]
        assert [block['tool_call_id'] for block in blocks[:4]] == ['one','two','two','one']
        assert blocks[2]['status'] == 'failed' and blocks[3]['status'] == 'succeeded'
        assert blocks[3]['arguments'] == {'path':'index.html', 'api_key':'[REDACTED]'}
        assert blocks[3]['result'] == '<html>ok</html>'
        assert sent[3]['m.relates_to']['event_id'] == '$3'
        assert sent[4]['m.relates_to']['event_id'] == '$2'
        assert blocks[-1]['kind'] == 'quiet'
        assert all((row.get('m.new_content') or row)['org.agentteams.progress_only'] for row in sent)
        assert all((row.get('m.new_content') or row).get('m.mentions',{'user_ids':[]}) == {'user_ids':[]} for row in sent)
        final = channel._matrix_text_content('!task:test', '页面已完成', meta, 'm.text')
        assert final['org.agentteams.run_id'] == meta['org.agentteams.run_id']
        assert 'org.agentteams.progress_only' not in final
    asyncio.run(check())


def test_managed_turns_link_saved_results_without_chat_receipts():
    from agentteams_matrix.channel import AgentTeamsMatrixChannel
    from agentteams_matrix.progress import MatrixProgress

    async def check():
        channel = AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
        channel._client = object()
        channel._active_thread_roots = {}
        channel._write_attachment_context = lambda *args: None
        channel._send_typing = AsyncMock()
        channel._room_send_with_retry = AsyncMock(return_value=SimpleNamespace(event_id='$answer'))
        channel._task_execution = SimpleNamespace(finish=AsyncMock(), messages=SimpleNamespace(finish=AsyncMock()))
        source = {'event_id': '$input', 'room_id': '!room:test', 'input': {'content': {}}}
        task = {'team_id': 'squad', 'task_id': 'execution', 'generation': 2}
        for meta, expected in [
            ({'task_execution': task}, 'qwenpaw:task:squad:execution:2'),
            ({'message_execution': {'event_key': 'accepted'}, 'event_id': '$input'}, 'qwenpaw:message:!room:test:$input'),
        ]:
            channel._room_send_with_retry.reset_mock()
            await channel._ensure_thread_root('!room:test', meta)
            channel._room_send_with_retry.assert_not_awaited()
            assert meta['org.agentteams.run_id'] == expected
            send = AsyncMock()
            meta['_matrix_native_progress'] = MatrixProgress(send, expected)
            await channel._on_process_completed(SimpleNamespace(session_id='native'), '!room:test', meta)
            send.assert_not_awaited()
            if 'task_execution' in meta:
                await channel.send_task_reply('!room:test', '开发完成', 'saved', task)
            else:
                await channel.send_message_reply(source, '验收通过', 'saved')
            content = channel._room_send_with_retry.call_args.args[2]
            assert content['org.agentteams.run_id'] == expected
            assert content['body'] in ('开发完成', '验收通过')
            if 'task_execution' in meta:
                assert content['org.agentteams.run']['blocks'] == [{'type': 'text', 'text': '开发完成'}]
                assert content['org.agentteams.run']['run_id'] == expected
    asyncio.run(check())


def test_typed_planning_event_uses_verified_ingress_with_its_original_type():
    from nio import Event
    from qwenpaw_matrix_sync import ManagedNioSync
    from agentteams_matrix.channel import AgentTeamsMatrixChannel

    async def check():
        base = {'type': 'org.agentteams.coordination', 'sender': '@lead:test',
                'origin_server_ts': 1, 'event_id': '$handoff'}
        packet = {'channel_meta': {'teamharness_coordination': {'kind': 'handoff'}}}
        event = Event.parse_event({**base, 'content': {'body': '内部受理',
                                  'org.agentteams.coordination': packet}})
        channel = AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
        channel._on_room_event = AsyncMock()
        sync = ManagedNioSync.__new__(ManagedNioSync)
        sync.channel = channel
        sync.matrix = SimpleNamespace(prepare_input=lambda room, event_id, source, intent, task_id=None: source)
        assert sync.handler(event) == channel._on_room_event
        source = await sync.prepare('!task:test', event, 'message')
        assert source['type'] == base['type'] and source['content'] == event.source['content']
        await channel._on_coordination_notice('!task:test', event)
        channel._on_room_event.assert_awaited_once_with('!task:test', event)
        unrelated = Event.parse_event({**base, 'type': 'example.unrelated', 'content': event.source['content']})
        assert sync.handler(unrelated) is None
        await channel._on_coordination_notice('!task:test', unrelated)
        channel._on_room_event.assert_awaited_once()
    asyncio.run(check())
