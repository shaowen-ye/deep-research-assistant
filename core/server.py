import json
import mimetypes
import re
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler
from urllib import parse

from . import editor as editor_mod
from .config import STATIC_DIR, public_settings, update_settings
from .exporters import make_artifact_zip
from .state import (
    RUNNING,
    RUNNING_LOCK,
    job_dir,
    load_state,
    normalize_state,
    save_state,
)
from .worker import (
    create_job,
    health,
    list_jobs,
    normalize_job_citations,
    plan_action,
    start_job_thread,
)


class Handler(BaseHTTPRequestHandler):
    server_version = "DeepResearchApp/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, content_type="text/plain; charset=utf-8", status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body_json(self):
        length = int(self.headers.get("Content-Length") or "0")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            return self.serve_file(STATIC_DIR / "index.html")
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path.startswith("/static/"):
            return self.serve_file(STATIC_DIR / path.removeprefix("/static/"))

        if path == "/api/health":
            return self.send_json(health())
        if path == "/api/settings":
            return self.send_json(public_settings())
        if path == "/api/jobs":
            return self.send_json({"jobs": list_jobs()})

        match = re.fullmatch(r"/api/jobs/([^/]+)", path)
        if match:
            job_id = parse.unquote(match.group(1))
            state = load_state(job_id)
            if not state:
                return self.send_json({"error": "job not found"}, 404)
            state = normalize_state(state)
            with RUNNING_LOCK:
                state["thread_running"] = job_id in RUNNING and RUNNING[job_id].is_alive()
            return self.send_json(state)

        match = re.fullmatch(r"/api/jobs/([^/]+)/(progress|plan|report)", path)
        if match:
            job_id, name = parse.unquote(match.group(1)), match.group(2)
            if name == "progress":
                file_name = "research_progress.md"
            elif name == "plan":
                file_name = "research_plan.md"
            else:
                file_name = "research_report.md"
            target = job_dir(job_id) / file_name
            if not target.exists():
                return self.send_text("")
            return self.send_text(target.read_text(encoding="utf-8"))

        match = re.fullmatch(r"/api/jobs/([^/]+)/edit", path)
        if match:
            job_id = parse.unquote(match.group(1))
            if not load_state(job_id):
                return self.send_json({"error": "job not found"}, 404)
            try:
                return self.send_json(editor_mod.get_state(job_id))
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 400)

        match = re.fullmatch(r"/api/jobs/([^/]+)/edit/version/([A-Za-z0-9_-]+)", path)
        if match:
            job_id = parse.unquote(match.group(1))
            version = match.group(2)
            try:
                return self.send_text(editor_mod.read_version(job_id, version))
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 404)

        match = re.fullmatch(r"/files/([^/]+)/(.*)", path)
        if match:
            job_id = parse.unquote(match.group(1))
            rel = parse.unquote(match.group(2))
            if rel == "research_artifacts.zip":
                make_artifact_zip(job_id)
            return self.serve_job_file(job_id, rel)

        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/jobs":
            try:
                state = create_job(self.read_body_json())
                return self.send_json(state, 201)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 400)

        if path == "/api/settings":
            try:
                return self.send_json(update_settings(self.read_body_json()))
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 400)

        match = re.fullmatch(r"/api/jobs/([^/]+)/edit/(message|apply|reject|rollback|reset)", path)
        if match:
            job_id, action = parse.unquote(match.group(1)), match.group(2)
            if not load_state(job_id):
                return self.send_json({"error": "job not found"}, 404)
            body = self.read_body_json()
            try:
                if action == "message":
                    state = editor_mod.send_message(
                        job_id,
                        body.get("message") or "",
                        body.get("provider") or "",
                        body.get("model") or None,
                    )
                elif action == "apply":
                    state = editor_mod.apply_patches(job_id, body.get("patch_ids") or [])
                elif action == "reject":
                    state = editor_mod.reject_patches(job_id, body.get("patch_ids") or [])
                elif action == "rollback":
                    state = editor_mod.rollback(job_id, body.get("version") or "")
                else:
                    state = editor_mod.reset_session(job_id)
                return self.send_json(state)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 400)

        match = re.fullmatch(r"/api/jobs/([^/]+)/(resume|stop|approve|refine|reveal|normalize)", path)
        if match:
            job_id, action = parse.unquote(match.group(1)), match.group(2)
            state = load_state(job_id)
            if not state:
                return self.send_json({"error": "job not found"}, 404)
            if action == "normalize":
                try:
                    return self.send_json(normalize_job_citations(job_id))
                except Exception as exc:
                    return self.send_json({"error": str(exc)}, 500)
            if action == "reveal":
                target = job_dir(job_id)
                try:
                    subprocess.run(["open", str(target)], check=True)
                    return self.send_json({"revealed": True, "path": str(target)})
                except Exception as exc:
                    return self.send_json({"error": str(exc), "path": str(target)}, 500)
            if action in ("approve", "refine"):
                try:
                    return self.send_json(plan_action(job_id, action, self.read_body_json()))
                except Exception as exc:
                    return self.send_json({"error": str(exc)}, 400)
            if action == "resume":
                if state.get("local_status") == "awaiting_approval":
                    return self.send_json({"error": "plan is waiting for approval or refinement"}, 400)
                state["stop_requested"] = False
                state["local_status"] = "queued"
                save_state(job_id, state)
                started = start_job_thread(job_id)
                return self.send_json({"started": started, "state": load_state(job_id)})
            state["stop_requested"] = True
            save_state(job_id, state)
            return self.send_json({"stopping": True, "state": state})

        return self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        parsed = parse.urlparse(self.path)
        match = re.fullmatch(r"/api/jobs/([^/]+)", parsed.path)
        if not match:
            return self.send_json({"error": "not found"}, 404)

        job_id = parse.unquote(match.group(1))
        with RUNNING_LOCK:
            running = job_id in RUNNING and RUNNING[job_id].is_alive()
        if running:
            state = load_state(job_id)
            state["stop_requested"] = True
            save_state(job_id, state)
            return self.send_json({"error": "job is running; stop it first"}, 409)

        path = job_dir(job_id)
        if path.exists():
            shutil.rmtree(path)
        return self.send_json({"deleted": True})

    def serve_file(self, path):
        path = path.resolve()
        if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.exists() or not path.is_file():
            return self.send_json({"error": "file not found"}, 404)
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_job_file(self, job_id, rel):
        base = job_dir(job_id).resolve()
        target = (base / rel).resolve()
        if not str(target).startswith(str(base)) or not target.exists() or not target.is_file():
            return self.send_json({"error": "file not found"}, 404)
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if target.suffix in (".zip", ".pdf", ".md"):
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
