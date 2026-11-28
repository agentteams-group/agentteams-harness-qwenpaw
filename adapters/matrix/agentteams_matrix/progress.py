"""Display native envelope calls and results without changing model history."""
import json
import time


from run_progress import progress_value as bounded


class MatrixProgress:
    def __init__(self, send, run_id):
        self.send, self.run_id, self.calls = send, run_id, {}
        self.closed = False

    async def publish(self, block, previous=None):
        content = {'msgtype': 'm.notice', 'body': block.get('tool_name') or '执行过程',
                   'org.agentteams.progress_only': True, 'm.mentions': {'user_ids': []},
                   'org.agentteams.run': {'version': '1', 'run_id': self.run_id, 'blocks': [block]}}
        if previous:
            content = {**content, 'm.new_content': content,
                       'm.relates_to': {'rel_type': 'm.replace', 'event_id': previous}}
        response = await self.send(content)
        return getattr(response, 'event_id', None)

    async def tool(self, event, *, finished):
        for part in event.content:
            data = getattr(part, 'data', None)
            if not isinstance(data, dict) or not data.get('call_id') or not data.get('name'):
                continue
            identity = data['call_id']
            previous = self.calls.get(identity)
            if finished:
                if not previous:
                    continue
                block = {**previous['block'], 'result': bounded(data.get('output')),
                         'status': 'succeeded' if data.get('state') == 'success' else 'failed',
                         'finished_at': int(time.time() * 1000)}
                await self.publish(block, previous['event_id'])
            elif not previous:
                arguments = data.get('arguments', {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {'raw': arguments}
                block = {'type': 'tool_call', 'tool_call_id': identity, 'tool_name': data['name'],
                         'arguments': bounded(arguments), 'status': 'running',
                         'started_at': int(time.time() * 1000)}
                self.calls[identity] = {'block': block, 'event_id': await self.publish(block)}

    async def finish(self, kind='quiet'):
        if self.closed:
            return
        await self.publish({'type': 'error', 'kind': kind,
                            'title': {'quiet': '已处理', 'failed': '执行失败', 'cancelled': '已停止'}[kind]})
        self.closed = True
