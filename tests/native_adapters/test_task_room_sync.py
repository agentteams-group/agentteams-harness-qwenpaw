from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentteams_matrix.managed_sync import ManagedNioSync


@pytest.mark.asyncio
@pytest.mark.parametrize("binding, delivered", [({}, False), ({"taskId": "main"}, True)])
async def test_task_room_supplement_does_not_wait_for_chat_history(binding, delivered):
    sync = ManagedNioSync.__new__(ManagedNioSync)
    sync.waiting, sync.assignments, sync.messages = {}, None, True
    sync.client = SimpleNamespace(rooms={"!task:test": object()}, user_id="@dev:test")
    sync.matrix = SimpleNamespace(input=Mock(return_value={
        "intent": "message", "room_id": "!task:test", "event_id": "$supplement",
        "input": {"sender": "@leader:test", "content": {"body": "updated requirement"}},
    }))
    sync.dispatch_message = AsyncMock()
    with patch("task_coordination.controller_json", return_value=binding) as authority:
        await sync.dispatch({"state": {"pending": ["input"], "rooms": {"!task:test": {"gaps": ["older"]}}}})
    authority.assert_called_once_with("tasks/room-context?roomId=%21task%3Atest")
    assert sync.dispatch_message.await_count == int(delivered)


@pytest.mark.asyncio
async def test_registration_and_poll_failures_leave_receiver_available_for_later_work():
    import asyncio
    sync = ManagedNioSync.__new__(ManagedNioSync)
    sync.lock = asyncio.Lock()
    sync.matrix = SimpleNamespace(start=Mock(side_effect=[OSError('Controller restarting'), None]))
    sync.poll = AsyncMock(side_effect=[OSError('temporary sync failure')] * 4 + [False])
    with patch('agentteams_matrix.managed_sync.asyncio.sleep', new_callable=AsyncMock) as sleep:
        await sync.run()
    assert sync.matrix.start.call_count == 2
    assert sync.poll.await_count == 5
    assert sleep.await_count == 5
