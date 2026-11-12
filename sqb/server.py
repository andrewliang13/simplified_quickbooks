"""HTTP server (standard library only) and request dispatcher."""
import json
import mimetypes
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db
from .router import ROUTES, Ctx
from .util import ApiError
# Importing the modules registers their routes.
from . import accounts, names, journals, banking, invoices, reconcile, reports  # noqa: F401,E402

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')


class App:
    def __init__(self, db_path):
        self.db_path = db_path
        db.init(db_path)

    def handle(self, method, path, query=None, body=None):
        """Dispatch one API call. Returns (http_status, json_payload)."""
        matched_path = False
        for m, regex, fn in ROUTES:
            mo = regex.match(path)
            if not mo:
                continue
            matched_path = True
            if m != method:
                continue
            if any(len(v) > 15 for v in mo.groupdict().values()):
                return 404, {'error': 'Not found'}
            conn = db.connect(self.db_path)
            try:
                ctx = Ctx(conn, {k: int(v) for k, v in mo.groupdict().items()}, query or {}, body or {})
                result = fn(ctx)
                conn.commit()
                return 200, result
            except ApiError as e:
                conn.rollback()
                return e.status, {'error': e.message}
            except Exception as e:  # pragma: no cover - unexpected
                conn.rollback()
                traceback.print_exc()
                return 500, {'error': 'Server error: %s' % e}
            finally:
                conn.close()
        return (405, {'error': 'Method not allowed'}) if matched_path else (404, {'error': 'Not found'})


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, data, ctype='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def _static(self, path):
            rel = 'index.html' if path in ('/', '') else path[len('/static/'):] if path.startswith('/static/') else None
            if rel is None:
                return self._send(404, b'Not found', 'text/plain')
            full = os.path.normpath(os.path.join(STATIC, rel))
            if not full.startswith(STATIC) or not os.path.isfile(full):
                return self._send(404, b'Not found', 'text/plain')
            with open(full, 'rb') as f:
                data = f.read()
            ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
            if ctype.startswith('text/') or ctype.endswith('javascript'):
                ctype += '; charset=utf-8'
            self._send(200, data, ctype)

        def _api(self, method):
            u = urlparse(self.path)
            if not u.path.startswith('/api/'):
                if method == 'GET':
                    return self._static(u.path)
                return self._send(404, b'Not found', 'text/plain')
            query = {k: v[-1] for k, v in parse_qs(u.query).items()}
            body = {}
            n = int(self.headers.get('Content-Length') or 0)
            if n:
                try:
                    body = json.loads(self.rfile.read(n).decode('utf-8'))
                    if not isinstance(body, dict):
                        raise ValueError
                except ValueError:
                    return self._send(400, json.dumps({'error': 'Invalid JSON body'}).encode())
            status, payload = app.handle(method, u.path, query, body)
            self._send(status, json.dumps(payload).encode('utf-8'))

        def do_GET(self):
            self._api('GET')

        def do_POST(self):
            self._api('POST')

        def do_PUT(self):
            self._api('PUT')

        def do_DELETE(self):
            self._api('DELETE')

    return Handler


def serve(db_path, host='127.0.0.1', port=8000):
    app = App(db_path)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    print('Simplified QuickBooks running at http://%s:%d  (data file: %s)' % (host, port, db_path))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
