"""Optional real OpenCode 2.2.1 integration; only the local model API is scripted."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from agentforeman.files import atomic_json, read_json
from agentforeman.pool_worker import Worker


class LocalModel(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append(body)
        chunks=[{'id':'test','object':'chat.completion.chunk','model':'test','choices':[{'index':0,'delta':{'role':'assistant','content':'{"answer":"42"}'},'finish_reason':None}]},
                {'id':'test','object':'chat.completion.chunk','model':'test','choices':[{'index':0,'delta':{},'finish_reason':'stop'}], 'usage':{'prompt_tokens':100,'completion_tokens':5,'total_tokens':105}}]
        raw=(''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode()
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)


@unittest.skipUnless(os.environ.get('FOREMAN_OPENCODE_BINARY'),'Set FOREMAN_OPENCODE_BINARY to test the real installed OpenCode 2 CLI')
class NativeOpenCodeTests(unittest.TestCase):
    def test_v2_native_supervisor_uses_own_context_no_title_call_and_validates_result(self):
        with tempfile.TemporaryDirectory(prefix='foreman-native-opencode-') as temp:
            root=Path(temp);api=ThreadingHTTPServer(('127.0.0.1',0),LocalModel);api.daemon_threads=True;api.calls=[]
            thread=threading.Thread(target=api.serve_forever,daemon=True);thread.start()
            try:
                # Supplying only a named local test model prevents accidental use
                # of a paid provider; the command pins this exact model.
                context=root/'worker/tasks/test_native/1/context'
                atomic_json(context/'opencode.json',{'providers':{'foreman-test':{
                    'package':'@opencode/ai/providers/openai-compatible',
                    'settings':{'baseURL':f'http://127.0.0.1:{api.server_port}/v1','apiKey':'TEST ONLY'},
                    'models':{'test':{'name':'TEST ONLY'}}}}})
                with patch.dict(os.environ,{'FOREMAN_NATIVE_TOKEN':'t'*40,'OPENCODE_DB':str(root/'private.sqlite3'),'PWD':str(root/'wrong-parent-directory')}):
                    worker=Worker({'state_dir':str(root/'worker'),'token_env':'FOREMAN_NATIVE_TOKEN','provider':'opencode',
                                   'model':'foreman-test/test','timeout_seconds':30,'command':[os.environ['FOREMAN_OPENCODE_BINARY']]})
                    result=worker.execute({'id':'test_native','attempts':1,'deadline':time.time()+45,
                        'request':{'prompt':'Return answer 42. Do not call tools.','schema':{'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}}})
                self.assertEqual(result['outcome'],'success',result)
                self.assertEqual(result['result'],{'answer':'42'})
                self.assertEqual(len(api.calls),1,'Explicit title must avoid an extra title-generation model call')
                config=read_json(context/'.opencode/opencode.json')
                self.assertEqual(config['permissions'],[{'action':'*','resource':'*','effect':'deny'}])
                job=root/'worker/tasks/test_native/1/job'
                self.assertEqual(read_json(job/'result.json')['exit_code'],0)
                spec=read_json(job/'spec.json')
                self.assertEqual(spec['env']['PWD'],str(context))
                self.assertIn('--standalone',spec['argv'])
            finally:
                api.shutdown();api.server_close();thread.join(timeout=5)
