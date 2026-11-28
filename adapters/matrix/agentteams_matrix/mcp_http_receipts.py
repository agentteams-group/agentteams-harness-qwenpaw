"""Preserve an HTTP tool response before the native SDK tears down its session."""
import httpx
from mcp.client import streamable_http as transport
from mcp.shared.exceptions import McpError
from qwenpaw.drivers.handlers.mcp import MCPDriverHandler


def install(observer):
    if getattr(transport.StreamableHTTPTransport, '_agentteams_http_receipts', False):
        return
    post = transport.StreamableHTTPTransport._handle_post_request
    execute = MCPDriverHandler._execute

    async def observed_post(self, ctx):
        try:
            return await post(self, ctx)
        except httpx.HTTPStatusError as error:
            request = ctx.session_message.message.root
            if not isinstance(request, transport.JSONRPCRequest) or request.method != 'tools/call':
                raise
            # A returned HTTP failure is terminal, but does not prove that the
            # remote operation had no partial effects. Never replay this call.
            response = transport.JSONRPCError(jsonrpc='2.0', id=request.id,
                error=transport.ErrorData(code=-32000,
                    message=f'MCP HTTP {error.response.status_code}: {error.response.reason_phrase}',
                    data={'agentteams_http_status': error.response.status_code}))
            await ctx.read_stream_writer.send(transport.SessionMessage(transport.JSONRPCMessage(response)))

    async def observed_execute(self, *args, **kwargs):
        try:
            return await execute(self, *args, **kwargs)
        except McpError as error:
            data = error.error.data
            current = observer.get()
            if current is not None and isinstance(data, dict) and isinstance(data.get('agentteams_http_status'), int):
                current['received'] = True
            raise

    transport.StreamableHTTPTransport._handle_post_request = observed_post
    MCPDriverHandler._execute = observed_execute
    transport.StreamableHTTPTransport._agentteams_http_receipts = True
