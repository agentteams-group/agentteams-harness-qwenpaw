import asyncio
import json
from unittest.mock import AsyncMock
from types import SimpleNamespace

from agentscope.state import AgentState
from agentteams_matrix.session_receipts import SessionReceipts


def test_delayed_handoff_survives_restart_and_enters_just_the_sender_session(tmp_path, monkeypatch):
    async def check():
        session_id, room = 'matrix:task:test:1', '!task:test'
        actor = '@dev:test'
        state = AgentState()
        outgoing = {'task': {'id': 'child', 'mainTaskId': 'main', 'submissionId': 's1'},
                    'assignments': [{'status': 'queued', 'eventId': 'dispatch1', 'taskId': 'test'}]}
        state.middle_context['teamharness.outgoing'] = {'session': session_id, 'room': room, 'result': outgoing}
        doc = {'agent': {'state': state.model_dump(mode='json')}}
        path = tmp_path / 'state.json'
        path.write_text(json.dumps(doc))

        class Session:
            async def get_session_state_dict(self, session_id, *, user_id, channel, allow_not_exist):
                assert (session_id, user_id, channel, allow_not_exist) == ('matrix:task:test:1', room, 'agentteams_matrix', False)
                return json.loads(path.read_text())

            async def update_session_state(self, session_id, field, state, *, user_id, channel):
                assert (session_id, field, user_id, channel) == ('matrix:task:test:1', 'agent.state', room, 'agentteams_matrix')
                path.write_text(json.dumps({'agent': {'state': state}}))

        channel = SimpleNamespace(_workspace=SimpleNamespace(session=Session()), channel='agentteams_matrix',
                                  matrix_user_id=actor, _client=None)
        receipt_id = json.dumps(['dispatch', 'dispatch1', 'matrix:$published'])
        fact = {'receiptId': receipt_id, 'fact': '已向测试节点派单。'}
        confirm = AsyncMock(side_effect=[[], [fact]])
        for _ in range(2):
            receipts = SessionReceipts(channel, tmp_path)
            monkeypatch.setattr(receipts, 'confirmed_handoffs', confirm)
            await receipts.start()
            await receipts.stop()
            await receipts.flush()
        saved = AgentState.model_validate(json.loads(path.read_text())['agent']['state'])
        assert 'teamharness.outgoing' not in saved.middle_context
        assert sum('已向测试节点派单' in str(msg.content) for msg in saved.context) == 1
        again = SessionReceipts(channel, tmp_path)
        await again.start()
        await again.stop()
        await again.flush()
        assert confirm.await_count == 2

    asyncio.run(check())
