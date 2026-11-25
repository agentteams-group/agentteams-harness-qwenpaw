"""Actual AgentScope turn, tool-result history and no-second-model boundary."""
import asyncio
import importlib.util
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from adapter_fixture import HARNESS

pytest.importorskip('agentscope.agent')


@pytest.mark.parametrize('intake', [False, True])
def test_native_terminal_fact_is_saved_but_only_business_reply_is_visible(intake):
    from agentscope.agent import Agent
    from agentscope.model import ChatModelBase, ChatResponse
    from agentscope.message import Msg, ToolCallBlock, TextBlock
    from agentscope.tool import Toolkit, FunctionTool
    from agentscope.formatter import OpenAIChatFormatter
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from pydantic import BaseModel
    spec = importlib.util.spec_from_file_location('natural_handoff_plugin', HARNESS / 'adapters/teamharness/plugin.py')
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)

    class Parameters(BaseModel):
        pass

    class Model(ChatModelBase):
        def __init__(self):
            super().__init__(None, 'native-boundary-fixture', Parameters(), stream=False)
            self.formatter = OpenAIChatFormatter()
            self.calls = 0

        async def __call__(self, *args, **kwargs):
            self.calls += 1
            assert self.calls == 1, 'Terminal Task feedback must not sample again'
            return ChatResponse(content=[ToolCallBlock(id='actual-tool-call', name='execute_shell_command',
                input=json.dumps({'command': 'agt task create --leader lead --title Own' if intake
                                  else 'agt task submit own --summary Done'}))], is_last=True)

    async def run():
        async def execute(command: str):
            if intake:
                assert 'AGENTTEAMS_ORIGIN_EVENT_ID=$request' in command
                return {'sourceEventId': '$request', 'task': {'id': 'main', 'kind': 'main', 'title': 'Own',
                    'status': 'planned', 'roomId': '!task:test', 'spec': 'Repeated complete snapshot'},
                    'notification': {'status': 'queued', 'eventId': 'notice', 'recipient': 'lead'}}
            return {'task': {'id': 'own', 'status': 'submitted', 'submissionId': 'submission',
                             'spec': 'Repeated complete snapshot'},
                    'notification': {'status': 'queued', 'eventId': 'notice', 'recipient': 'lead'}}

        toolkit = Toolkit()
        await toolkit.add_tool(FunctionTool(execute, name='execute_shell_command',
            permission=PermissionDecision(behavior=PermissionBehavior.ALLOW, message='In-process fixture; no shell runs')))
        model = Model()
        agent = Agent('Worker', 'Own fixture', model, toolkit=toolkit,
                      middlewares=[plugin._sanitizer_factory(None, None)])
        tokens = [(plugin._execution_task, plugin._execution_task.set('own')),
                  (plugin._native_actor, plugin._native_actor.set('@worker:test')),
                  (plugin._creation_sources, plugin._creation_sources.set([
                      {'inputId': 'input', 'eventId': '$request', 'userId': '@human:test', 'roomId': '!source:test'}]))]
        sent = {'event_id': 'notice', 'task_id': 'own', 'kind': 'task_submitted',
                'actor': '@worker:test', 'recipient': '@lead:test', 'room_id': '!task:test',
                'native_id': 'matrix:$actual-send'}
        try:
            with patch.object(plugin, '_controller_json', return_value=sent):
                events = [event async for event in agent.reply_stream(Msg(name='Human', role='user', content=[TextBlock(text='Own requirement')]))]
                from agentscope.event import TextBlockEndEvent, ReplyEndEvent
                final_blocks = [event for event in events if isinstance(event, TextBlockEndEvent)]
                assert len(final_blocks) == 1
                assert next(event for event in events if isinstance(event, ReplyEndEvent)).finished_reason == 'completed'
            visible = final_blocks[0].text
            assert ('已受理' in visible and '查看任务' in visible) if intake else visible == 'NO_REPLY'
            assert final_blocks[0].text == visible
            assert model.calls == 1
            native = json.dumps(await model.formatter.format(agent.state.context), ensure_ascii=False)
            assert 'Task system receipt' in native
            assert 'Repeated complete snapshot' not in native
            if not intake:
                assert 'Task handoff receipt' in native and '$actual-send' in native
        finally:
            for variable, token in reversed(tokens):
                variable.reset(token)

    with patch.dict(os.environ, {'AGENTTEAMS_AGENT_ROLE': 'team_leader' if intake else 'worker'}):
        asyncio.run(run())
