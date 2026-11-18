"""Minimal stdlib web UI for resolving merge conflicts item by item."""

from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .normalize import canonical_json
from .session import Session, SessionError, UnresolvedConflictsError


_UI_PATH = Path(__file__).with_name("ui.html")


def serve(base: dict, left: dict, right: dict,
          host: str = "127.0.0.1", port: int = 8765,
          output_dir: Path | str = "artifacts") -> None:
    session = Session(base, left, right)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    page = _UI_PATH.read_text(encoding="utf-8")

    handler = build_handler(base, left, right, output_dir)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Serving merge UI at http://{host}:{port}  (Ctrl+C to stop)")
    print(f"Exports will be written under {output_dir}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


def build_handler(base: dict, left: dict, right: dict,
                  output_dir: Path | str):
    """Return a request handler class bound to a fresh merge session."""
    session = Session(base, left, right)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    page = _UI_PATH.read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "AudioMerge/1.0"

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            if route in ("/", "/index.html"):
                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif route == "/api/state":
                self._send_json(session.snapshot())
            else:
                self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            try:
                payload = self._read_json()
                if route == "/api/resolve":
                    session.resolve(payload["conflictId"], payload["choice"])
                    self._send_json(session.snapshot())
                elif route == "/api/reset":
                    session.reset(payload.get("conflictId"))
                    self._send_json(session.snapshot())
                elif route == "/api/resolve-all":
                    session.resolve_all(payload["choice"])
                    self._send_json(session.snapshot())
                elif route == "/api/export":
                    project = session.export()
                    project_path = output_dir / "merged_project.json"
                    log_path = output_dir / "merge_log.json"
                    project_path.write_text(canonical_json(project),
                                            encoding="utf-8")
                    log_path.write_text(
                        canonical_json(session.result.log), encoding="utf-8"
                    )
                    self._send_json({
                        "ok": True,
                        "projectPath": str(project_path),
                        "logPath": str(log_path),
                        "project": project,
                    })
                else:
                    self.send_error(404)
            except (UnresolvedConflictsError, SessionError,
                    KeyError, json.JSONDecodeError) as exc:
                self._send_json({"error": str(exc)}, status=400)

        def log_message(self, fmt: str, *args) -> None:
            print(f"[web] {fmt % args}")

    return Handler
