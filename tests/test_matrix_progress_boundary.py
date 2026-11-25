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
