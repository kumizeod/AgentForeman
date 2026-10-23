"""Worker-only HTTP surface. Administration stays local through the CLI."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from .pool import Pool


class PoolServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, project, host='127.0.0.1', port=8770):
        self.pool = Pool(project)
        super().__init__((host,port), Handler)
        self.pump_stop = threading.Event()
        self.pump = threading.Thread(target=self.advance_councils,daemon=True)
        self.pump.start()

    def advance_councils(self):
        from .council import Council
        council = Council(self.pool.root.parent)
        while not self.pump_stop.wait(1):
            try:
                council.advance_all()
            except Exception:
                pass  # SQLite/immutable receipts allow safe resumption on next tick.

    def server_close(self):
        self.pump_stop.set()
        self.pump.join(timeout=16)
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.connection.settimeout(15)
        try:
            token = self.headers.get('Authorization','')
            member = self.server.pool.authenticate(token[7:] if token.startswith('Bearer ') else None)
            if not member:
                return self.reply(401, {'error':'Unauthorized'})
            if self.headers.get('Origin') or self.headers.get('Transfer-Encoding'):
                return self.reply(403, {'error':'Worker requests only'})
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 1000000:
                return self.reply(413, {'error':'Invalid request length'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data,dict):
                raise ValueError('Object required')
            pool = self.server.pool
            if self.path == '/claim' and set(data)=={'session'}:
                result = {'task':pool.claim(member,data['session'])}
            elif self.path == '/heartbeat' and set(data)=={'task','lease'}:
                result = {'accepted':pool.heartbeat(member,data['task'],data['lease'])}
            elif self.path == '/complete' and set(data)=={'task','lease','outcome','result','retry_at'}:
                result = {'accepted':pool.complete(member,**data)}
            else:
                return self.reply(400, {'error':'Unknown worker request'})
            self.reply(200,result)
        except (ValueError,TypeError,KeyError):
            self.reply(400,{'error':'Invalid worker request or lease'})
        except Exception:
            self.reply(500,{'error':'Coordinator request failed'})

    def reply(self, code, value):
        body = json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        self.wfile.write(body)
