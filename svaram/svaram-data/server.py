"""Offline survey app. Run with Python 3.10+; no third-party packages needed."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sqlite3
import sys
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "svaram-h10"))
from h10rig.registry import Registry, Conflict, encode

BASELINE = HERE.parent / "participant-baseline.html"
QUESTIONNAIRES = {"pre": ("participant_baseline", "1.1.0"), "post": ("participant_post", "1.0.0")}


def definitions():
    source = BASELINE.read_text()
    states = json.loads(re.search(r"STATES=(\[.*?\]);", source).group(1))
    post = {
        "recall": "right_now_after_session", "states": states,
        "scale": {"min": 0, "max": 10, "anchors": ["Not at all", "Extremely"]},
        "experience": [
            ["physical_change", "What changes did you notice in your body?"],
            ["emotional_change", "What changes did you notice in your feelings or thoughts?"],
            ["breath_body", "What did you notice about your breathing or your body?"],
            ["significant_moments", "Were there any important moments or unusual sensations?"],
            ["experience", "How would you describe the experience?"],
        ],
        "missing_values": "null means unanswered; zero is a valid rating",
        "protocol_basis": "Study proposal: repeat comparable state measures and add qualitative experience questions.",
    }
    return source, post


class SurveyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, root):
        self.registry = Registry(root, create=True)
        self.source, self.post = definitions()
        self.registry.register_questionnaire(*QUESTIONNAIRES["pre"], {"source_html": self.source, "missing_values": "null means unanswered"})
        self.registry.register_questionnaire(*QUESTIONNAIRES["post"], self.post)
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Do not put participant IDs or survey responses in console logs.

    def reply(self, value, code=200, content_type="application/json; charset=utf-8"):
        body = (encode(value) if content_type.startswith("application/json") else value).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def validate_host(self):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            raise ValueError("Use the local address printed by the survey app")

    def do_GET(self):
        try:
            self.validate_host()
            request = urlsplit(self.path)
            query = parse_qs(request.query)
            if request.path == "/api/registry":
                self.reply({"participants": self.server.registry.participants(), "sessions": self.server.registry.sessions(), "root": str(self.server.registry.root)})
            elif request.path == "/api/assessment":
                sid, kind = query["session"][0], query["kind"][0]
                self.reply({"session": self.server.registry.session(sid), **self.server.registry.assessment(sid, kind)})
            elif request.path == "/":
                self.page((HERE / "web" / "index.html").read_text())
            elif request.path == "/baseline":
                # Reuse the existing questionnaire. Disable its browser-draft restore.
                source = self.server.source.replace("restore();conditional();progress();", "conditional();progress();")
                self.page(source.replace("</body>", '<script src="/survey.js"></script></body>'))
            elif request.path == "/post":
                source = (HERE / "web" / "post.html").read_text().replace("__POST_DEFINITION__", encode(self.server.post))
                self.page(source)
            elif request.path in ("/survey.js", "/app.js", "/style.css"):
                kind = "text/css" if request.path.endswith("css") else "text/javascript"
                self.reply((HERE / "web" / request.path[1:]).read_text(), content_type=kind + "; charset=utf-8")
            else:
                self.reply({"error": "Not found"}, 404)
        except (ValueError, KeyError, sqlite3.Error, OSError) as exc:
            self.reply({"error": str(exc)}, 400)

    def page(self, html):
        html = html.replace("</head>", f'<meta name="api-token" content="{self.server.token}"></head>')
        self.reply(html, content_type="text/html; charset=utf-8")

    def do_POST(self):
        try:
            self.validate_host()
            if not secrets.compare_digest(self.headers.get("X-Study-Token", ""), self.server.token):
                self.reply({"error": "Reload the page to reconnect to the survey app"}, 403)
                return
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                raise ValueError("Requests must come from the local survey app")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 250_000:
                raise ValueError("Request exceeds the size limit")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Request must be an object")
            registry = self.server.registry
            if self.path == "/api/participants":
                self.reply({"id": registry.add_participant(data["code"])})
            elif self.path == "/api/sessions":
                self.reply({"id": registry.create_session(data["participant_id"], data.get("protocol", "sonorium-2026-v1"))})
            elif self.path == "/api/assessment":
                kind = data["kind"]
                if kind not in QUESTIONNAIRES:
                    raise ValueError("Unknown questionnaire")
                answers = data["data"]["answers"]
                for key, value in answers.items():
                    if key.startswith("state_") and value is not None:
                        if type(value) is not int or not 0 <= value <= 10:
                            raise ValueError("State ratings must be whole numbers from 0 to 10")
                    if isinstance(value, str) and len(value) > 2000:
                        raise ValueError("An answer exceeds 2000 characters")
                self.reply(registry.save_assessment(data["session_id"], kind, *QUESTIONNAIRES[kind], data["data"], data["version"], data.get("submit") is True, data.get("request_id")))
            elif self.path == "/api/reconcile":
                self.reply({"errors": registry.reconcile()})
            elif self.path == "/api/backup":
                from datetime import datetime
                name = "registry-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".sqlite"
                registry.backup(registry.root / "backups" / name)
                self.reply({"name": name})
            else:
                self.reply({"error": "Not found"}, 404)
        except Conflict as exc:
            self.reply({"error": str(exc)}, 409)
        except sqlite3.OperationalError as exc:
            self.reply({"error": f"Database unavailable. No save confirmed. Retry: {exc}"}, 503)
        except (ValueError, KeyError, TypeError, AttributeError, sqlite3.IntegrityError, OSError) as exc:
            self.reply({"error": str(exc)}, 400)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=HERE / "data")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with SurveyServer(("127.0.0.1", args.port), args.data_root) as server:
        print(f"Survey app: http://127.0.0.1:{server.server_port}", flush=True)
        print(f"Shared data folder: {server.registry.root}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
