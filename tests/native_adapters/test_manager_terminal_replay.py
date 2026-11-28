"""Native admission honors completed inputs from a different Manager runtime."""
import asyncio
from types import SimpleNamespace

from agentteams_receipts import Receipts
from agentteams_matrix.channel import AgentTeamsMatrixChannel
from agentteams_matrix.session_receipts import SessionReceipts


def test_completed_manager_source_does_not_reenter_native_dispatch(tmp_path):
    outbox = Receipts(tmp_path)
    outbox.enqueue('!private:test', '$done', 'completed')
    receipts = SessionReceipts.__new__(SessionReceipts)
    receipts._manager_outbox = outbox
    channel = AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
    channel._session_receipts = receipts
    asyncio.run(channel._on_room_event(SimpleNamespace(room_id='!private:test'),
                                      SimpleNamespace(event_id='$done')))
