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
