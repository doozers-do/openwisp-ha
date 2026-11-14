"""Minimal authenticated Supervisor self-info server for disposable HTTPS checks."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class SelfInfo(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/addons/self/info':
            self.send_error(404)
            return
        if self.headers.get('Authorization') != 'Bearer disposable-supervisor-token':
            self.send_error(401)
            return
        body = Path('/fixture/supervisor-info.json').read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


HTTPServer(('0.0.0.0', 80), SelfInfo).serve_forever()
