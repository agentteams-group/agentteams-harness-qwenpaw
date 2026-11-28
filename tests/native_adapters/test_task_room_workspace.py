"""A normal Task-room message uses the same prepared native file directory."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adapter_fixture import HARNESS, core_source
from qwenpaw.config import context
from qwenpaw.agents.tools.file_io import write_file


def test_normal_room_continuation_writes_into_the_prepared_execution(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('room_workspace_plugin', HARNESS / 'adapters/teamharness/plugin.py')
    plugin = importlib.util.module_from_spec(spec); spec.loader.exec_module(plugin)
    plugin.ASSET_DIR = core_source() / 'plugins/teamharness'
    monkeypatch.setenv('AGENTTEAMS_AGENT_ROLE', 'worker')
    monkeypatch.setenv('AGENTTEAMS_CONTROLLER_URL', 'http://fixture.invalid')
    monkeypatch.setenv('AGENTTEAMS_OTEL_ENABLED', 'false')
    root = tmp_path / 'shared/tasks/execution'; output = root / 'workspace'; output.mkdir(parents=True)
    profile = tmp_path / 'member'; profile.mkdir()
    prepared = {'execution': 'execution', 'ack': ('execution', root, 'worker', 'dispatch'),
                'fact': 'Prepared Task continuation.', 'context': {'outputDirectory': str(output),
                    'scratchDirectory': str(output / '.scratch'), 'task': {'id': 'execution', 'mainTaskId': 'main'}}}

    async def run():
        profile_token = context.current_workspace_dir.set(profile)
        ctx = SimpleNamespace(agent=SimpleNamespace(), extras={}, inject_context=Mock(),
            request=SimpleNamespace(channel_meta={'event_id': '$source', 'room_id': '!task:test'}))
        try:
            with patch.object(plugin._native_module(), 'invoke', return_value=prepared) as bridge:
                assert await plugin._manager_report_context(ctx) is False
                bridge.assert_called_once_with({'operation': 'room_context', 'room': '!task:test'})
            assert plugin._execution_task.get() == 'execution'
            assert context.get_tool_base_dir() == output.resolve()
            await write_file('generated.txt', 'Task-room continuation')
            assert (output / 'generated.txt').read_text(encoding='utf-8-sig') == 'Task-room continuation'
            assert not (profile / 'generated.txt').exists()
        finally:
            plugin._reset_public_task_io(ctx)
            for key, variable in [('execution_task_token', plugin._execution_task),
                                  ('ack_scope_token', plugin._ack_scope), ('source_event_token', plugin._source_event)]:
                if key in ctx.extras:
                    variable.reset(ctx.extras[key])
            context.current_workspace_dir.reset(profile_token)
    asyncio.run(run())
