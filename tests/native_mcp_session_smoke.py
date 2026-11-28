"""Exercise the pinned QwenPaw HTTP MCP client against a real expired session."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

from qwenpaw.drivers.handlers.mcp_stateful_client import HttpStatefulClient


async def main():
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
        assert not (await client.call_tool('record', {})).isError
        old = client.session
        sessions.clear()
        try:
            await client.call_tool('record', {})
        except Exception as error:
            print('expired call:', type(error).__name__, flush=True)
        else:
            raise AssertionError('Expired call unexpectedly succeeded')
        assert len(effects) == 1, 'expired call was replayed'
        async with asyncio.timeout(8):
            while client.session is old or not client.is_connected:
                await asyncio.sleep(0.05)
        assert not (await client.call_tool('record', {})).isError
        assert len(effects) == 2
        assert len(initializations) == 2
        print('PASS: native client reconnects; expired call is not replayed; next call succeeds', flush=True)
    finally:
        await client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == '__main__':
    asyncio.run(main())
