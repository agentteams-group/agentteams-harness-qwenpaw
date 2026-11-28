"""Recovery and terminal native closure preserve the same owner without resampling."""
import asyncio
import copy
import importlib.util
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from adapter_fixture import HARNESS, core_source
sys.path.insert(0, str(core_source() / 'shared/lib'))
from task_recovery import Recovery, RecoveryConflict


@pytest.fixture
def execution():
    spec = importlib.util.spec_from_file_location('natural_task_execution',
        HARNESS / 'adapters/matrix/agentteams_matrix/task_execution.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    task = {'task_id': 'T', 'room_id': '!task:test', 'status': 'in_progress',
            'execution': {'runtime': 'qwenpaw', 'process_id': 'own-process',
                'instance_id': 'container/start', 'generation': 1, 'state': 'active',
                'checkpoint': {'version': 1}}}
    authority = SimpleNamespace(process='own-process', current=lambda *args: copy.deepcopy(task),
        discover=lambda: {'instance_id': 'container/start', 'tasks': [{'team_id': 'team', 'task': copy.deepcopy(task)}]},
        claim=lambda *args: {'task': copy.deepcopy(task), 'spec': 'Own requirement'})
    channel = SimpleNamespace(matrix_user_id='@worker:test', enqueue_recovered_task=AsyncMock())
    run = module.TaskExecution.__new__(module.TaskExecution)
    run.channel, run.recovery = channel, authority
    run.lock, run.admitted, run.native_active = asyncio.Lock(), set(), set()
    run.failed_retries = set()
    run.matrix = SimpleNamespace(start=lambda: None)
    run.select_session = AsyncMock(return_value='actual-native-session')
    run.deliver = AsyncMock()
    identity = {'task_id': 'T', 'team_id': 'team', 'generation': 1,
                'instance_id': 'container/start', 'process_id': 'own-process'}
    return run, task, identity, authority, channel


def test_recovered_native_metadata_binds_the_actual_sending_worker(execution):
    run, task, identity, authority, channel = execution
    task['main_task_id'] = 'main-one'
    asyncio.run(run.recover())
    payload = channel.enqueue_recovered_task.call_args.args[0]
    assert payload['meta']['agent_matrix_user_id'] == '@worker:test'
    assert payload['meta']['task_execution'] == identity
    assert payload['meta']['main_task_id'] == 'main-one'


def test_completed_handoff_only_settles_the_same_native_owner(execution):
    run, task, identity, authority, channel = execution
    task.update(status='completed', result_path='shared/tasks/T/result.md')
    task['execution']['state'] = 'released'
    assert run.verify(identity, finished=True) == task
    assert run.verify(identity, monitoring=True) == task
    with pytest.raises(RecoveryConflict):
        run.verify(identity)
    for key in ('process_id', 'instance_id', 'generation'):
        replaced = {**identity, key: 'successor'}
        with pytest.raises(RecoveryConflict):
            run.verify(replaced, finished=True)
    asyncio.run(run.recover())
    channel.enqueue_recovered_task.assert_not_awaited()


def test_silent_terminal_callback_requires_a_durable_result(execution):
    run, task, identity, authority, channel = execution
    task['status'] = 'completed'
    task['execution']['state'] = 'released'
    with pytest.raises(RuntimeError, match='durable result'):
        asyncio.run(run.finish(identity, 'actual-native-session', ''))
    task['result_path'] = 'shared/tasks/T/result.md'
    task['execution']['checkpoint'].update(delivery='delivered', result={'key': 'saved'})
    prepared = []
    authority.prepare_reply = lambda *args: (prepared.append(args), (task, 'stored report'))[1]
    asyncio.run(run.finish(identity, 'actual-native-session', ''))
    assert prepared == [('T', 'team', '')]
    run.deliver.assert_not_awaited()
    assert identity['checkpoint_digest'] == Recovery.digest(task['execution']['checkpoint'])


def test_same_process_recovers_submitted_report_without_another_native_turn(execution):
    run, task, identity, authority, channel = execution
    task.update(status="completed", result_path="shared/tasks/T/result.md")
    task["execution"]["state"] = "released"
    run.admitted.add(("team", "T", 1))
    def claim(*args):
        task["execution"]["checkpoint"].update(delivery="pending", result={"key": "saved"})
        return {"task": copy.deepcopy(task), "saved_result": "Authoritative report"}
    authority.claim = claim
    asyncio.run(run.recover())
    run.deliver.assert_awaited_once()
    assert run.deliver.call_args.args[-1] == "Authoritative report"
    channel.enqueue_recovered_task.assert_not_awaited()


def test_failed_transport_retries_once_per_verified_progress_checkpoint(execution):
    from unittest.mock import Mock
    run, task, identity, authority, channel = execution
    authority.release_failed_turn = Mock(return_value=True)
    asyncio.run(run.retry_failed(identity))
    asyncio.run(run.retry_failed(identity))
    authority.release_failed_turn.assert_called_once_with(identity)
    task['execution']['checkpoint']['completed_steps'] = ['verified-tool']
    asyncio.run(run.retry_failed(identity))
    assert authority.release_failed_turn.call_count == 2


def test_same_process_reclaims_released_failed_turn_without_an_at_message(execution):
    run, task, identity, authority, channel = execution
    task['execution']['state'] = 'released'
    run.admitted.add(('team', 'T', 1))
    def claim(*args):
        task['execution'].update(state='active', generation=2)
        return {'task': copy.deepcopy(task), 'spec': 'Continue verified progress'}
    authority.claim = claim
    asyncio.run(run.recover())
    assert channel.enqueue_recovered_task.call_args.args[0]['meta']['task_execution']['generation'] == 2


@pytest.mark.parametrize('failure,retry', [('transport', True), ('iterations', True), ('cancelled', False), ('business', False)])
def test_only_transport_failure_requests_native_task_retry(failure, retry):
    import httpx
    from unittest.mock import Mock, patch
    from agentteams_matrix.channel import AgentTeamsMatrixChannel, NativeIterationLimit
    from qwenpaw.app.channels.base import BaseChannel
    error = {'iterations': NativeIterationLimit('native stop'), 'transport': httpx.RemoteProtocolError('incomplete chunked read'),
             'cancelled': asyncio.CancelledError(), 'business': RuntimeError('task failed')}[failure]
    async def stream(self, payload):
        yield None
        raise error
    run = SimpleNamespace(verify=Mock(), retry_failed=AsyncMock(), native_closed=Mock(),
                          recovery=SimpleNamespace(interrupted=Mock()))
    channel = AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
    channel._task_execution, channel._session_receipts = run, None
    async def consume():
        async for _ in channel._stream_recovered_task({'meta': {'task_execution': {'task_id': 'T', 'team_id': 'team'}}}):
            pass
    with patch.object(BaseChannel, '_stream_with_tracker', stream), pytest.raises(type(error)):
        asyncio.run(consume())
    assert run.retry_failed.await_count == int(retry)
    run.native_closed.assert_called_once()


def test_iteration_stop_retry_is_bounded_even_when_read_receipts_grow(execution):
    from unittest.mock import Mock
    run, task, identity, authority, channel = execution
    authority.release_failed_turn = Mock(return_value=True)
    asyncio.run(run.retry_failed(identity, iteration_limit=True))
    task['execution']['checkpoint']['completed_steps'] = ['another-read']
    asyncio.run(run.retry_failed(identity, iteration_limit=True))
    authority.release_failed_turn.assert_called_once_with(identity)
