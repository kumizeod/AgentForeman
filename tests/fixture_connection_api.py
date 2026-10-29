"""TEST ONLY local API responses. Real HTTP, processes and checks; no live LLM."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

from tests.fixture_modes import answer_for
from tests.test_briefing import reply


class FixtureAPI(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self):
        super().__init__(('127.0.0.1',0),Handler)
        self.requests=[];self.correct=True;self.error_code=None
    @property
    def url(self):return f'http://127.0.0.1:{self.server_port}/v1'


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(body)
        if self.server.error_code:
            self.send_response(self.server.error_code);self.send_header('Content-Length','0');self.send_header('Retry-After','60');self.end_headers();return
        message=body['messages'][0]['content']
        if 'TEST ONLY browser cancel live' in message:
            print('TEST_SLOW_REQUEST_STARTED',flush=True)
            self.connection.settimeout(30)
            try:self.connection.recv(1)
            except OSError:pass
            return
        prompt,raw_schema=message.rsplit('\nReturn only valid JSON conforming to this schema:\n',1)
        schema=json.loads(raw_schema)
        if set(schema['properties'])=={'answer'}:
            answer={'answer':'42' if self.server.correct else '43'}
        else:
            context=json.loads(prompt.splitlines()[-1])
            if 'contract' in schema['properties']:
                answer=reply(manual=not context['checks'])
                if 'role_plan' in schema['properties']:
                    ids=[c['member'] for c in context['available_connections'] if c['member']!=context['execution']['chair']]
                    answer['role_plan']={'mode':context['execution']['mode'],'members':list(reversed(ids[:2])),
                       'reviewers':ids[2:3] if context['execution']['mode']=='cooperative' else [],
                       'rationale':'TEST ONLY chairman chose roles from the advertised connections'}
            else:answer=answer_for(schema,context)
        if self.path.endswith('/messages'):
            result={'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(answer)}]}
        else:result={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]}
        result['usage'] = {'input_tokens': 100, 'output_tokens': 50} if self.path.endswith('/messages') else {'prompt_tokens': 100, 'completion_tokens': 50}
        raw=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


if __name__=='__main__':
    server=FixtureAPI();print(server.url,flush=True)
    try:server.serve_forever()
    finally:server.server_close()
