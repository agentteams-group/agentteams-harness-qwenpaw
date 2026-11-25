"""Recovery and terminal native closure preserve the same owner without resampling."""
import asyncio
import copy
import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from adapter_fixture import HARNESS
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
    run.matrix = SimpleNamespace(start=lambda: None)
    run.select_session = AsyncMock(return_value='actual-native-session')
    run.deliver = AsyncMock()
    identity = {'task_id': 'T', 'team_id': 'team', 'generation': 1,
                'instance_id': 'container/start', 'process_id': 'own-process'}
    return run, task, identity, authority, channel


def test_recovered_native_metadata_binds_the_actual_sending_worker(execution):
    run, task, identity, authority, channel = execution
    asyncio.run(run.recover())
    payload = channel.enqueue_recovered_task.call_args.args[0]
    assert payload['meta']['agent_matrix_user_id'] == '@worker:test'
    assert payload['meta']['task_execution'] == identity


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
