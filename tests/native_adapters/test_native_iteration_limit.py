"""A real SDK budget stop must survive the native envelope as typed metadata."""
import asyncio
import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from adapter_fixture import HARNESS


def test_native_budget_stop_preserves_its_reason_until_task_recovery():
    from agentscope.agent import Agent
    from agentscope.formatter import OpenAIChatFormatter
    from agentscope.message import Msg, TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse
    from agentscope.tool import Toolkit, FunctionTool, ToolResponse
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from pydantic import BaseModel
    from qwenpaw.runtime.executor import AgentExecutor
    from qwenpaw.runtime.envelope import Envelope
    from agentteams_matrix.channel import AgentTeamsMatrixChannel, NativeIterationLimit
    spec = importlib.util.spec_from_file_location('iteration_native_plugin', HARNESS/'adapters/teamharness/plugin.py')
    plugin = importlib.util.module_from_spec(spec); spec.loader.exec_module(plugin)

    class Parameters(BaseModel):
        pass
    class Model(ChatModelBase):
        def __init__(self):
            super().__init__(None, 'iteration-fixture', Parameters(), stream=False)
            self.formatter = OpenAIChatFormatter(); self.calls=0
        async def __call__(self, *args, **kwargs):
            self.calls+=1
            return ChatResponse(content=[ToolCallBlock(id='read', name='fixture_read', input='{}')]
                if self.calls==1 else [TextBlock(text='Stopped at the native budget.')], is_last=True)
    async def read():
        return ToolResponse(content=[{'type':'text','text':'verified'}])
    async def run():
        ctx=SimpleNamespace(request=SimpleNamespace(channel_meta={'task_execution':{'task_id':'T'}}),extras={})
        toolkit=Toolkit()
        await toolkit.add_tool(FunctionTool(read,name='fixture_read',permission=PermissionDecision(behavior=PermissionBehavior.ALLOW,message="Isolated fixture")))
        agent=Agent('Worker','Use the fixture tool.',Model(),toolkit=toolkit,
                    middlewares=[plugin._sanitizer_factory(ctx,None)])
        agent.react_config.max_iters=1
        messages=[e async for e in AgentExecutor(agent,Envelope('session')).run(
            [Msg(name='Human',role='user',content=[TextBlock(text='Run the fixture.')])])]
        stops=[e for e in messages if (getattr(e,'metadata',None) or {}).get('org.agentteams.native_stop')=='exceed_max_iters'
               and getattr(e,'object',None)=='message' and str(getattr(e,'status',None)).lower().endswith('completed')]
        assert stops, [(getattr(e,'object',None),getattr(e,'metadata',None)) for e in messages]
        channel=AgentTeamsMatrixChannel.__new__(AgentTeamsMatrixChannel)
        with pytest.raises(NativeIterationLimit):
            await channel.on_event_message_completed(ctx.request,'!task',stops[-1],ctx.request.channel_meta)
    asyncio.run(run())
