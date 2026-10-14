#!/usr/bin/env python3
"""Mock du Graph API Meta pour le stress-test fb-message-guardian v0.2.0.
usage: fbmock.py PORT [ok|error]"""
import json, os, sys
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

PORT = int(sys.argv[1])
MODE = sys.argv[2] if len(sys.argv) > 2 else "ok"
PAGE = "999999999999999"
LOG = "/tmp/opencode/fbmock_posts.log"

def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")

def fixture():
    now = datetime.now(timezone.utc)
    return {"data": [
        {"id": "t_A", "updated_time": ts(now - timedelta(minutes=2)),
         "participants": {"data": [{"id": PAGE, "name": "M2Tech"},
                                   {"id": "10001", "name": "Marc Tremblay"}]},
         "messages": {"data": [
             {"id": "m_in_2", "created_time": ts(now - timedelta(minutes=2)),
              "from": {"id": "10001", "name": "Marc Tremblay"},
              "to": {"data": [{"id": PAGE}]},
              "message": "Bonjour, un devis pour un site web svp ?"},
             {"id": "m_out_1", "created_time": ts(now - timedelta(hours=3)),
              "from": {"id": PAGE, "name": "M2Tech"},
              "to": {"data": [{"id": "10001"}]}, "message": "Merci !"}]}},
        {"id": "t_B", "updated_time": ts(now - timedelta(hours=1)),
         "participants": {"data": [{"id": PAGE, "name": "M2Tech"},
                                   {"id": "10002", "name": "Solo Page"}]},
         "messages": {"data": [
             {"id": "m_out_2", "created_time": ts(now - timedelta(hours=1)),
              "from": {"id": PAGE, "name": "M2Tech"},
              "to": {"data": [{"id": "10002"}]}, "message": "Bonjour !"}]}},
        {"id": "t_C", "updated_time": ts(now - timedelta(hours=30)),
         "participants": {"data": [{"id": PAGE, "name": "M2Tech"},
                                   {"id": "10003", "name": "Sophie 👩"}]},
         "messages": {"data": [
             {"id": "m_in_3", "created_time": ts(now - timedelta(hours=30)),
              "from": {"id": "10003", "name": "Sophie"},
              "to": {"data": [{"id": PAGE}]}, "message": "salut"}]}},
        {"id": "t_D", "updated_time": ts(now - timedelta(minutes=5)),
         "participants": {"data": [{"id": PAGE, "name": "M2Tech"},
                                   {"id": "10004", "name": "Clément Æ—very long"}]},
         "messages": {"data": [
             {"id": "m_in_4", "created_time": ts(now - timedelta(minutes=5)),
              "from": {"id": "10004", "name": "Clément Æ"},
              "to": {"data": [{"id": PAGE}]},
              "message": "Le prix 🏷️ pour un site " + "x" * 600}]}},
        {"id": "t_E", "updated_time": ts(now - timedelta(minutes=7))},
        {"id": "t_F", "updated_time": ts(now - timedelta(minutes=6)),
         "participants": {"data": [{"id": PAGE, "name": "M2Tech"},
                                   {"id": "10005", "name": "Anonyme Cinq"}]},
         "messages": {"data": [
             {"id": "m_in_5", "created_time": ts(now - timedelta(minutes=6)),
              "from": {"id": "10005"},
              "to": {"data": [{"id": PAGE}]}, "message": "un renseignement joli 🎨"}]}},
    ]}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        if MODE == "error":
            body = json.dumps({"error": {"code": 190, "message": "Error validating access token"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == "/v21.0/%s/conversations" % PAGE:
            body = json.dumps(fixture()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(n)
        with open(LOG, "ab") as f:
            f.write(self.path.encode() + b" || " + payload + b"\n")
        if MODE == "error":
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"code": 10, "message": "(#10) Not authorized"}}).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"recipient_id": "10001", "message_id": "mid_mock_out_1"}).encode())

HTTPServer(("127.0.0.1", PORT), H).serve_forever()
