"""A room follow-up must reuse the admitted native Task session, not a fresh chat."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import yaml

from agentteams_matrix.message_execution import MessageExecution
from task_recovery import RecoveryConflict
import pytest


def test_room_followup_reuses_task_session_and_prepares_its_identity(monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime.yaml'
    runtime.write_text(yaml.safe_dump({'member': {'runtimeName': 'dev'}, 'team': {'name': 'alpha'}}))
    monkeypatch.setenv('TEAMHARNESS_RUNTIME_CONFIG', str(runtime))
    identity = {'event_key': 'input', 'generation': 1}
    task = {'task_id': 'child', 'room_id': '!task:test', 'assigned_to': 'dev',
            'task_kind': 'execution', 'status': 'in_progress',
            'execution': {'generation': 2, 'instance_id': 'instance', 'process_id': 'process'}}
    matrix = Mock()
    matrix.message.return_value = {'recovery': 'resume'}
    matrix.message_claim.return_value = {'recovery': 'resume', 'identity': identity,
        'input': {'room_id': '!task:test'}, 'checkpoint_digest': 'message-cp', 'execution': {'checkpoint': {}}}
    sessions = Mock()
    sessions.load.return_value = {'generation': 1, 'checkpoint_digest': 'message-cp'}
    execution = SimpleNamespace(matrix=matrix, sessions=sessions, recovery=Mock(), native_active=set(),
        verify=Mock(), select_session=AsyncMock(return_value='matrix:task:original-session'))
    execution.recovery.discover.return_value = {'tasks': [{'team_id': 'alpha', 'task': task}]}
    channel = SimpleNamespace(_workspace=SimpleNamespace(task_tracker=SimpleNamespace()))
    messages = MessageExecution(channel, execution)
    turn = asyncio.run(messages.admit('input'))
    assert turn['session_id'] == 'matrix:task:original-session'
    assert turn['task_context']['task_id'] == 'child'
    assert not turn['rebuild']
    execution.native_active.add(('alpha', 'child', 2))
    execution.select_session.reset_mock()
    assert asyncio.run(messages.admit('input')) is None
    execution.select_session.assert_not_awaited()


def test_unknown_message_operation_is_not_restarted_in_a_task_session():
    matrix = Mock()
    matrix.message.return_value = {'recovery': 'verify'}
    execution = SimpleNamespace(matrix=matrix, sessions=Mock())
    channel = SimpleNamespace(_workspace=SimpleNamespace(task_tracker=SimpleNamespace()))
    messages = MessageExecution(channel, execution)
    assert asyncio.run(messages.admit('input')) is None
    matrix.message_claim.assert_not_called()


@pytest.mark.parametrize("text", ["Model summary", ""])
def test_submitted_followup_publishes_task_result_once_before_retiring_message(text):
    identity = {"event_key": "source", "generation": 1}
    context = {"team_id": "alpha", "task_id": "child", "generation": 2}
    order = []
    async def finish(*args):
        order.append("task-delivered")
    matrix = Mock()
    matrix.message_cancel.side_effect = lambda actual: order.append("message-retired")
    execution = SimpleNamespace(matrix=matrix, sessions=Mock(), verify=Mock(return_value={"status": "completed"}),
        finish=AsyncMock(side_effect=finish))
    channel = SimpleNamespace(_workspace=SimpleNamespace(task_tracker=SimpleNamespace()))
    messages = MessageExecution(channel, execution)
    messages.turns["source"] = {"identity": identity, "task_context": context, "session_id": "native-task"}
    asyncio.run(messages.finish(identity, text))
    execution.finish.assert_awaited_once_with(context, "native-task", text)
    matrix.message_save_reply.assert_not_called()
    assert order == ["task-delivered", "message-retired"]


def test_task_publication_failure_keeps_followup_pending_without_a_second_chat_reply():
    identity = {"event_key": "source", "generation": 1}
    matrix = Mock()
    execution = SimpleNamespace(matrix=matrix, sessions=Mock(), verify=Mock(return_value={"status": "submitted"}),
        finish=AsyncMock(side_effect=RecoveryConflict("delivery changed")))
    messages = MessageExecution(SimpleNamespace(_workspace=SimpleNamespace(task_tracker=SimpleNamespace())), execution)
    messages.turns["source"] = {"identity": identity, "task_context": {"task_id": "child"}, "session_id": "native"}
    with pytest.raises(RecoveryConflict):
        asyncio.run(messages.finish(identity, "summary"))
    matrix.message_cancel.assert_not_called()
    matrix.message_save_reply.assert_not_called()


@pytest.mark.parametrize("delivered", [True, False])
def test_restart_after_task_submission_does_not_reexecute_the_supplement(delivered):
    identity = {"event_key": "input", "generation": 1}
    prefix = "matrix:task:" + "task-key" + ":"
    matrix = Mock()
    matrix.message.return_value = {"recovery": "resume"}
    matrix.message_claim.return_value = {"recovery": "resume", "identity": identity,
        "input": {"room_id": "!task:test", "native_commit": {"native_ref": prefix + "original"}}}
    task = {"task_id": "child", "room_id": "!task:test", "status": "completed",
        "execution": {"checkpoint": {"delivery": "delivered" if delivered else "pending"}}}
    recovery = Mock()
    recovery.discover.return_value = {"tasks": [{"team_id": "alpha", "task": task}]}
    recovery.current.return_value = task
    execution = SimpleNamespace(matrix=matrix, sessions=Mock(), recovery=recovery,
        key=Mock(return_value="task-key"), recover=AsyncMock())
    messages = MessageExecution(SimpleNamespace(_workspace=SimpleNamespace(task_tracker=SimpleNamespace())), execution)
    assert asyncio.run(messages.admit("input")) is None
    execution.recover.assert_awaited_once_with(only_task="child")
    if delivered:
        matrix.message_cancel.assert_called_once_with(identity)
    else:
        matrix.message_cancel.assert_not_called()
    execution.sessions.load.assert_not_called()


def test_active_task_supplement_claims_before_native_write_and_retirement():
    from agentteams_matrix.task_inputs import TaskAssignments
    from task_recovery import Recovery
    async def run():
        order, native = [], {}
        identity = {'team_id':'alpha','task_id':'child','generation':1,'instance_id':'instance','process_id':'process'}
        checkpoint = {}
        view = {'generation':1,'instance_id':'instance','checkpoint_digest':Recovery.digest(checkpoint),'cache':{}}
        agent = SimpleNamespace(state=SimpleNamespace(context=[]))
        async def observe(message):
            order.append('observe')
            agent.state.context.append(message)
        agent.observe = observe
        agent.state_dict = lambda: {'state':{'context':[m.model_dump(mode='json') for m in agent.state.context]}}
        async def save(**kwargs):
            order.append('persist')
            native['saved'] = {'agent':kwargs['agent'].state_dict()}
        store = SimpleNamespace(save_session_state=AsyncMock(side_effect=save),get_session_state_dict=AsyncMock(side_effect=lambda **kw:native['saved']))
        matrix = Mock()
        def claim(key):
            order.append('claim')
            if native.get('committed') and not native.get('claimed'):
                return {'recovery':'verify'}
            native['claimed'] = True
            return {'recovery':'resume','identity':{'event_key':key}}
        matrix.message_claim.side_effect = claim
        matrix.native_input.side_effect = lambda key: {'input':{'native_commit':native.get('receipt',{})}}
        matrix.native_current.side_effect = lambda *args: bool(native.get('committed'))
        def commit(key, evidence, ref, digest):
            order.append('commit')
            native.update(committed=True,receipt={'native_ref':ref,'native_digest':digest})
        matrix.native_committed.side_effect = commit
        matrix.message_cancel.side_effect = lambda identity: order.append('retire')
        channel = SimpleNamespace(channel='agentteams_matrix',_workspace=SimpleNamespace(session=store,task_tracker=SimpleNamespace()),_session_receipts=None)
        execution = SimpleNamespace(channel=channel,matrix=matrix,key=lambda *args:'key',
            verify=Mock(return_value={'room_id':'!room:test','execution':{'checkpoint':checkpoint}}),sessions=SimpleNamespace(load=Mock(return_value=view)))
        record = {'room_id':'!room:test','event_id':'$supplement','intent':'message','input':{'sender':'@lead:test','content':{'body':'new requirement'}}}
        await TaskAssignments(execution).attach(identity,record,'new requirement',ctx=SimpleNamespace(session_id='matrix:task:key:session',mode_state={}),agent=agent)
        assert order == ['claim','observe','persist','commit','claim','retire']
        assert len(agent.state.context) == 1
    asyncio.run(run())
