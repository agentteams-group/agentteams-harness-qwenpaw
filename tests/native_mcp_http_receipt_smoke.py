"""Keep real HTTP failures terminal while unreturned MCP calls stay fenced."""
import asyncio
import json
import threading
import importlib.util
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from agentscope.tool import ToolResponse
from qwenpaw.drivers.handlers.mcp import MCPDriverHandler
from qwenpaw.drivers.capabilities import DriverCapability, format_capability_id
from qwenpaw.drivers.adapters.agentscope_tool import DriverCapabilityTool
from task_recovery import RecoveryConflict
from task_operation import begin, result_bytes

spec = importlib.util.spec_from_file_location("http_receipt", Path(os.environ["HARNESS_SOURCE"]) / "adapters/matrix/agentteams_matrix/message_execution.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

from qwenpaw.drivers.handlers.mcp_stateful_client import HttpStatefulClient


async def run_case(outcome):
    sessions, effects = set(), []
    initializations = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, payload=None, session=None):
            data = json.dumps(payload).encode() if payload is not None else b''
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            if session:
                self.send_header('Mcp-Session-Id', session)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply(405)

        def do_DELETE(self):
            self.reply(200)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            session = self.headers.get('Mcp-Session-Id')
            if session and session not in sessions:
                self.reply(404, {'jsonrpc': '2.0', 'id': body.get('id'), 'error': {'code': -32001, 'message': 'Session not found'}})
                return
            method = body['method']
            if method == 'initialize':
                session = uuid4().hex
                sessions.add(session)
                initializations.append(session)
                result = {'protocolVersion': body['params']['protocolVersion'], 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fixture', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': [{'name': 'record', 'description': 'Local test effect', 'inputSchema': {'type': 'object'}}]}
            elif method == 'tools/call':
                effects.append(body['id'])
                if len(effects) == 1 and outcome == 'http':
                    self.reply(503, {'message': 'fixture unavailable'})
                    return
                if len(effects) == 1 and outcome == 'disconnect':
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if len(effects) == 1 and outcome == 'server_error':
                    self.reply(200, {'jsonrpc':'2.0', 'id':body['id'], 'result': {'isError':True, 'content':[{'type':'text','text':'partial change failed'}]}})
                    return
                result = {'content': [{'type': 'text', 'text': 'recorded'}]}
            else:
                self.reply(202)
                return
            self.reply(200, {'jsonrpc': '2.0', 'id': body['id'], 'result': result}, session)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = HttpStatefulClient('fixture', 'streamable_http', f'http://127.0.0.1:{server.server_port}/mcp', timeout=3)
    try:
        await client.connect()
        await client.list_tools()
        handler = MCPDriverHandler.__new__(MCPDriverHandler)
        handler._card = SimpleNamespace(name='fixture', enabled=True, config={})
        handler._client = client
        handler._authorize_invocation = AsyncMock(return_value=None)
        handler._credential_provider = SimpleNamespace(resolve=AsyncMock(return_value=None))
        capability_id = format_capability_id('mcp', 'fixture', 'tool', 'invoke', 'record')
        tool = DriverCapabilityTool(DriverCapability(capability_id=capability_id,
            driver_name='fixture', protocol='mcp', kind='tool', action='invoke', name='record'), handler.invoke_capability)
        checkpoint = {}
        def start(identity, calls):
            operation, cp = begin({}, ['team', 'task'], calls)
            checkpoint.update(cp)
            return operation
        def finish(identity, operation, results):
            result_bytes(checkpoint, operation, results)
            return {'execution': {'checkpoint': {'version':2}}}
        owner = SimpleNamespace(begin_tools=Mock(side_effect=start), finish_tools=Mock(side_effect=finish))
        async def execute(**kwargs):
            chunk = await tool()
            yield ToolResponse(content=chunk.content, state=chunk.state, metadata=chunk.metadata)
        async def invoke(call_id):
            stream = module.guarded_tool(owner, {'identity':{'task_id':'task'}, 'lock':asyncio.Lock()},
                SimpleNamespace(id=call_id, name='record', input={}), execute, {}, driver_name='fixture')
            return [item async for item in stream]
        if outcome == 'disconnect':
            try:
                await asyncio.wait_for(invoke('first'), timeout=8)
            except RecoveryConflict:
                pass
            else:
                raise AssertionError('A call without a returned result was not fenced')
            owner.finish_tools.assert_not_called()
        else:
            result = await asyncio.wait_for(invoke('first'), timeout=8)
            assert result[0].state == 'error'
            receipt = owner.finish_tools.call_args.args[2][0]
            assert receipt['status'] == 'error'
            assert 'effect' not in receipt, 'A failed response does not prove no partial effects'
            result = await asyncio.wait_for(invoke('second'), timeout=8)
            assert result[0].state == 'success'
            assert len(effects) == 2, 'The failed call was replayed'
        print('PASS:', outcome, flush=True)
    finally:
        await client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == '__main__':
    for outcome in ['http', 'server_error', 'disconnect']:
        asyncio.run(run_case(outcome))
