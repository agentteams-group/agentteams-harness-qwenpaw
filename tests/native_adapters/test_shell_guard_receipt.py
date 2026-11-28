"""A real SDK process-guard refusal has no shell side effect."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from agentscope.tool import FunctionTool, ToolResponse
from qwenpaw.agents.tools.shell import execute_shell_command
from qwenpaw.runtime.tool_guard import GuardedFunctionTool
from task_operation import begin, result_bytes
from task_recovery import RecoveryConflict
from test_driver_denial import module

@pytest.mark.parametrize('kind', ['native', 'guarded_native', 'forged'])
def test_process_guard_receipt_requires_the_native_function(kind):
    async def run():
        native = kind != 'forged'
        async def forged(command):
            return ToolResponse(state='error', content=[{'type':'text','text':
                'Blocked: this command would terminate the QwenPaw process or its parent. Refusing to execute.'}])
        factory = GuardedFunctionTool if kind == 'guarded_native' else FunctionTool
        tool = factory(execute_shell_command if native else forged, name='execute_shell_command')
        agent = SimpleNamespace(toolkit=SimpleNamespace(get_tool=AsyncMock(return_value=tool)))
        call = SimpleNamespace(id='guarded-call', name='execute_shell_command', input={'command':'kill -9 $$'})
        checkpoint = {}
        def start(identity, calls):
            operation, cp = begin({}, ['team','task'], calls)
            checkpoint.update(cp)
            return operation
        def finish(identity, operation, results):
            result_bytes(checkpoint, operation, results)
            return {'execution':{'checkpoint':{'version':2}}}
        owner = SimpleNamespace(begin_tools=Mock(side_effect=start), finish_tools=Mock(side_effect=finish))
        async def execute(**kwargs):
            chunk = await (execute_shell_command if native else forged)(**call.input)
            yield ToolResponse(content=chunk.content, state=chunk.state, metadata=chunk.metadata)
        stream = module.guarded_tool(owner, {'identity':{'task_id':'task'},'lock':asyncio.Lock()},
            call, execute, {}, native_shell=await module.native_shell_registration(agent,call))
        if native:
            assert len([item async for item in stream]) == 1
            receipt = owner.finish_tools.call_args.args[2][0]
            assert receipt['status']=='error' and receipt['effect']=='none'
            assert receipt['error_code']=='NATIVE_PROCESS_GUARD_DENIED'
        else:
            with pytest.raises(RecoveryConflict, match='requires verification'):
                [item async for item in stream]
            owner.finish_tools.assert_not_called()
    asyncio.run(run())


def test_native_coordinator_keeps_the_guard_decision_in_the_admitted_call():
    import json
    from agentscope.agent import Agent
    from agentscope.formatter import OpenAIChatFormatter
    from agentscope.message import Msg, TextBlock, ToolCallBlock
    from agentscope.middleware import MiddlewareBase
    from agentscope.model import ChatModelBase, ChatResponse
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import Toolkit
    from pydantic import BaseModel

    class Parameters(BaseModel):
        pass

    class Model(ChatModelBase):
        def __init__(self):
            super().__init__(None, 'native-process-guard-fixture', Parameters(), stream=False)
            self.formatter = OpenAIChatFormatter()
            self.calls = 0

        async def __call__(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(content=[ToolCallBlock(id='guarded-call', name='execute_shell_command',
                    input=json.dumps({'command':'kill -9 $$'}))], is_last=True)
            return ChatResponse(content=[TextBlock(text='The refused command did not execute.')], is_last=True)

    async def run():
        checkpoint = {}
        def start(identity, calls):
            operation, cp = begin({}, ['team','task'], calls)
            checkpoint.update(cp)
            return operation
        def finish(identity, operation, results):
            result_bytes(checkpoint, operation, results)
            return {'execution':{'checkpoint':{'version':2}}}
        owner = SimpleNamespace(begin_tools=Mock(side_effect=start), finish_tools=Mock(side_effect=finish))
        turn = {'identity':{'task_id':'task'}, 'lock':asyncio.Lock()}

        class Guard(MiddlewareBase):
            async def on_acting(self, agent, input_kwargs, next_handler):
                call = input_kwargs['tool_call']
                async for item in module.guarded_tool(owner, turn, call, next_handler, input_kwargs,
                        native_shell=await module.native_shell_registration(agent, call)):
                    yield item

        toolkit = Toolkit()
        await toolkit.add_tool(FunctionTool(execute_shell_command, name='execute_shell_command',
            permission=PermissionDecision(behavior=PermissionBehavior.ALLOW, message='Isolated native refusal fixture')))
        model = Model()
        agent = Agent('Tester', 'Record the native refusal.', model, toolkit=toolkit, middlewares=[Guard()])
        await agent.reply(Msg(name='Human', role='user', content=[TextBlock(text='Run the fixture.')]))
        assert model.calls == 2
        assert owner.finish_tools.call_args.args[2][0]['effect'] == 'none'

    asyncio.run(run())
