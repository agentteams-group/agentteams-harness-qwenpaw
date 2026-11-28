"""A native pre-invocation denial is settled; server text is not authority."""
import asyncio
import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from agentscope.tool import ToolResponse
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool, ToolAnnotations
from qwenpaw.drivers.adapters.agentscope_tool import DriverCapabilityTool
from qwenpaw.drivers.capabilities import DriverCapability, DriverInvocationResult
from adapter_fixture import HARNESS
from task_recovery import RecoveryConflict
from task_operation import begin, result_bytes

spec = importlib.util.spec_from_file_location('driver_denial_module', HARNESS / 'adapters/matrix/agentteams_matrix/message_execution.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
driver_registration, guarded_tool = module.driver_registration, module.guarded_tool


@pytest.mark.parametrize('read_only', [True, False, None])
def test_mcp_readonly_failure_uses_the_registered_servers_tool_annotations(read_only):
    from qwenpaw.drivers.manager import DriverManager
    from qwenpaw.drivers.handlers.mcp import MCPDriverHandler

    async def run():
        manager = DriverManager.__new__(DriverManager)
        handler = MCPDriverHandler.__new__(MCPDriverHandler)
        handler._card = SimpleNamespace(enabled=True)
        handler._client = SimpleNamespace(list_tools=AsyncMock(return_value=[Tool(
            name='query', inputSchema={'type':'object'},
            annotations=ToolAnnotations(readOnlyHint=read_only) if read_only is not None else None)]))
        manager._handlers = {'github': handler}
        tool = DriverCapabilityTool(DriverCapability(
            capability_id='github:query', driver_name='github', protocol='mcp', kind='tool',
            action='invoke', name='query'), manager.invoke_capability)
        agent = SimpleNamespace(toolkit=SimpleNamespace(get_tool=AsyncMock(return_value=tool)))
        call = SimpleNamespace(id='call', name='github__query', input={'read_only': True})
        registered = await module.read_only_registration(agent, call)
        assert registered is (read_only is True)
        checkpoint = {}
        def start(identity, calls):
            operation, cp = begin({}, ['team', 'task'], calls)
            checkpoint.update(cp)
            return operation
        def finish(identity, operation, results):
            result_bytes(checkpoint, operation, results)
            return {'execution': {'checkpoint': {'version': 2}}}
        owner = SimpleNamespace(begin_tools=Mock(side_effect=start), finish_tools=Mock(side_effect=finish))
        async def execute(**kwargs):
            yield ToolResponse(state='error', content=[{'type':'text','text':'MCP HTTP 503'}])
        stream = guarded_tool(owner, {'identity': {'task_id':'task'}, 'lock':asyncio.Lock()},
            call, execute, {}, read_only=registered, driver_name='github')
        if read_only is True:
            assert len([item async for item in stream]) == 1
            assert owner.finish_tools.call_args.args[2][0]['effect'] == 'none'
        else:
            with pytest.raises(RecoveryConflict, match='requires verification'):
                [item async for item in stream]
            owner.finish_tools.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['denied', 'server_error', 'transport_error'])
def test_driver_terminal_origin_preserves_denial_and_server_error(outcome):
    async def run():
        metadata = {'ok': False, 'type': 'driver_policy_denied', 'driver_id': 'browser', 'operation': 'invoke'}
        result = (DriverInvocationResult(ok=False, error_type='driver_policy_denied', metadata=metadata)
                  if outcome == 'denied' else DriverInvocationResult(ok=True, value=CallToolResult(
                      isError=True, content=[TextContent(type='text', text=str(metadata))]))
                  if outcome == 'server_error' else DriverInvocationResult(ok=False, error_type='transport_error', message='Connection lost'))
        tool = DriverCapabilityTool(DriverCapability(
            capability_id='browser:snapshot', driver_name='browser', protocol='mcp', kind='tool',
            action='invoke', name='snapshot'), AsyncMock(return_value=result))
        agent = SimpleNamespace(toolkit=SimpleNamespace(get_tool=AsyncMock(return_value=tool)))
        call = SimpleNamespace(id='call', name='snapshot', input={})
        driver = await driver_registration(agent, call)
        checkpoint = {}
        def begin_tools(identity, calls):
            operation, value = begin({}, ['team', 'task'], calls)
            checkpoint.update(value)
            return operation
        def finish_tools(identity, operation, results):
            result_bytes(checkpoint, operation, results)
            return {'execution': {'checkpoint': {'version': 2}}}
        owner = SimpleNamespace(begin_tools=Mock(side_effect=begin_tools), finish_tools=Mock(side_effect=finish_tools))
        async def execute(**kwargs):
            chunk = await tool()
            yield ToolResponse(content=chunk.content, state=chunk.state, metadata=chunk.metadata)
        stream = guarded_tool(owner, {'identity': {'task_id': 'task'}, 'lock': asyncio.Lock()},
            call, execute, {}, driver_name=driver)
        if outcome in {'denied', 'server_error'}:
            assert len([item async for item in stream]) == 1
            receipt = owner.finish_tools.call_args.args[2][0]
            assert receipt['status'] == 'error'
            assert receipt.get('effect') == ('none' if outcome == 'denied' else None)
        else:
            with pytest.raises(RecoveryConflict, match='requires verification'):
                [item async for item in stream]
            owner.finish_tools.assert_not_called()
    asyncio.run(run())
