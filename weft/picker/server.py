from __future__ import annotations

import base64
import hmac
import json
import secrets
import shutil
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..assets import (
    GEN_IMG_SUBDIR,
    IMG_SUBDIRS,
    append_candidates,
    recompile_exports,
    _atomic_write_json,
    _sync_shot_prompt,
)
from ..exporters.capcut_draft import capcut_running

HTML_PATH = Path(__file__).resolve().parent / "picker.html"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _host_allowed(host: str | None) -> bool:
    """Only accept requests addressed to localhost (DNS-rebinding defense)."""
    host = (host or "").strip().lower()
    if not host:
        return False
    if host.startswith("["):  # [::1] or [::1]:port
        name = host.partition("]")[0].lstrip("[")
    else:
        name = host.rsplit(":", 1)[0] if ":" in host else host
    return name in _LOCAL_HOSTS


# ------------------------------------------------------------------ state ---

def _image_dirs(project_dir: Path, shot_id: str) -> list[Path]:
    """Candidate dirs in read order: provider-neutral images/gen first, then legacy."""
    return [project_dir / "SHOTS" / shot_id / subdir for subdir in IMG_SUBDIRS]


def _candidates(project_dir: Path, shot_id: str) -> list[str]:
    """Merged candidate filenames across all layout dirs (gen shadows legacy on collision)."""
    names: set[str] = set()
    for d in _image_dirs(project_dir, shot_id):
        if not d.is_dir():
            continue
        names.update(p.name for p in d.iterdir()
                     if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith("."))
    return sorted(names)


def _candidate_rel(project_dir: Path, shot_id: str, name: str) -> str:
    """SHOTS/<id>-relative path of a candidate file; new files default to images/gen."""
    for subdir in IMG_SUBDIRS:
        if (project_dir / "SHOTS" / shot_id / subdir / name).is_file():
            return f"{subdir}/{name}"
    return f"{GEN_IMG_SUBDIR}/{name}"


def _narration_context(beats_by_id: dict, cover: dict) -> str:
    if not cover:
        return ""
    ids = list(beats_by_id.keys())
    try:
        i0, i1 = ids.index(cover["from"]), ids.index(cover["to"])
    except (ValueError, KeyError):
        return beats_by_id.get(cover.get("from", ""), {}).get("text", "")
    texts = [beats_by_id[ids[i]].get("text", "") for i in range(i0, i1 + 1)]
    return " ".join(t for t in texts if t).strip()


def build_state(project_dir: Path) -> dict:
    visuals = json.loads((project_dir / "VISUALS.json").read_text(encoding="utf-8"))
    picks = json.loads((project_dir / "PICKS.json").read_text(encoding="utf-8"))
    nar = json.loads((project_dir / "NARRATION.json").read_text(encoding="utf-8"))
    beats_by_id = {b["id"]: b for b in nar["beats"]}
    selections = picks.get("selections", {})
    shots = []
    for s in visuals["shots"]:
        if s.get("source_kind") != "image":
            continue
        sid = s["id"]
        cands = _candidates(project_dir, sid)
        sel = selections.get(sid, "")
        # Only report a pick that is actually saved in PICKS.json (and still
        # exists); never pretend cands[0] is selected when nothing was saved.
        name = sel.split("/")[-1] if any(sel.startswith(f"{sub}/") for sub in IMG_SUBDIRS) else ""
        pick = name if name in cands else ""
        shots.append({
            "shot_id": sid,
            "prompt": s.get("prompt", ""),
            "motion": (s.get("motion") or {}).get("type", "static"),
            "context": _narration_context(beats_by_id, s.get("cover", {})),
            "candidates": cands,
            # SHOTS/<id>-relative path per candidate — clients must use these
            # instead of hardcoding an images/<provider> layout.
            "candidate_paths": {c: _candidate_rel(project_dir, sid, c) for c in cands},
            "pick": pick,
        })
    return {"title": visuals.get("style_bible", "")[:60] or "Weft picker",
            "project_dir": str(project_dir),
            "img_subdir": GEN_IMG_SUBDIR,  # where new generations/uploads land
            "shots": shots}


def _save_pick(project_dir: Path, shot_id: str, candidate: str) -> None:
    picks_path = project_dir / "PICKS.json"
    picks = json.loads(picks_path.read_text(encoding="utf-8"))
    picks.setdefault("selections", {})
    picks.setdefault("auto_picked", [])
    picks.setdefault("overridden", [])
    # Resolve the dir the candidate actually lives in (gen first, legacy fallback)
    # so picks on old images/openai projects keep pointing at existing files.
    picks["selections"][shot_id] = _candidate_rel(project_dir, shot_id, candidate)
    picks["auto_picked"] = [sid for sid in picks["auto_picked"] if sid != shot_id]
    if shot_id not in picks["overridden"]:
        picks["overridden"].append(shot_id)
    _atomic_write_json(picks_path, picks)


def _save_prompt(project_dir: Path, shot_id: str, prompt: str) -> bool:
    """Update the shot's prompt. Returns False when the shot id is unknown."""
    vpath = project_dir / "VISUALS.json"
    visuals = json.loads(vpath.read_text(encoding="utf-8"))
    found = False
    for s in visuals["shots"]:
        if s["id"] == shot_id:
            s["prompt"] = prompt
            found = True
    if not found:
        return False
    _atomic_write_json(vpath, visuals)
    _sync_shot_prompt(project_dir, shot_id, prompt)
    return True


def _generate_and_pick(project_dir: Path, shot_id: str, n: int, prompt: str | None) -> dict:
    result = append_candidates(project_dir, shot_id, n=n, prompt=prompt)
    if result["new"]:
        _save_pick(project_dir, shot_id, result["new"][0])
        result["pick"] = result["new"][0]
    return result


def _add_external(project_dir: Path, shot_id: str, data: bytes) -> str:
    from PIL import Image
    import io
    d = project_dir / "SHOTS" / shot_id / GEN_IMG_SUBDIR  # uploads land in the neutral dir
    d.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(p.stem.split("_")[-1])
                 for legacy in _image_dirs(project_dir, shot_id)
                 for p in legacy.glob("external_*.png")
                 if p.stem.split("_")[-1].isdigit()], default=0)
    name = f"external_{n:03d}.png"
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.save(d / name)
    return name


# ---------------------------------------------------------------- handler ---

def _make_handler(project_dir: Path, token: str):
    write_lock = threading.Lock()  # serialize all mutating handlers (ThreadingHTTPServer)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, body):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self):
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

        def _host_ok(self) -> bool:
            if _host_allowed(self.headers.get("Host")):
                return True
            self.send_error(403, "forbidden host")
            return False

        def _token_ok(self) -> bool:
            sent = self.headers.get("X-Weft-Token", "")
            if sent and hmac.compare_digest(sent, token):
                return True
            self._json(403, {"ok": False, "error": "토큰 불일치 — picker가 연 URL로 접속하세요"})
            return False

        def do_GET(self):
            if not self._host_ok():
                return
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                html = HTML_PATH.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            if path == "/api/state":
                self._json(200, build_state(project_dir))
                return
            if path.startswith("/img/"):
                parts = path.split("/", 3)
                if len(parts) != 4:
                    self.send_error(400); return
                sid = urllib.parse.unquote(parts[2]); fn = urllib.parse.unquote(parts[3])
                if any(x in sid or x in fn for x in ("/", "..")):
                    self.send_error(403); return
                p = None
                for d in _image_dirs(project_dir, sid):  # gen first, then legacy
                    candidate = (d / fn).resolve()
                    try:
                        candidate.relative_to((project_dir / "SHOTS").resolve())
                    except ValueError:
                        self.send_error(403); return
                    if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTS:
                        p = candidate; break
                if p is None:
                    self.send_error(404); return
                data = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type",
                                 CONTENT_TYPES.get(p.suffix.lower(), "application/octet-stream"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_error(404)

        def do_POST(self):
            if not self._host_ok():
                return
            if not self._token_ok():  # CSRF: mutating requests need the session token
                return
            path = urllib.parse.urlparse(self.path).path
            try:
                with write_lock:
                    self._dispatch_post(path)
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})

        def _dispatch_post(self, path):
            if path == "/api/pick":
                b = self._read_json()
                _save_pick(project_dir, b["shot_id"], b["candidate"])
                self._json(200, {"ok": True}); return
            if path == "/api/prompt":
                b = self._read_json()
                if not _save_prompt(project_dir, b["shot_id"], b["prompt"].strip()):
                    self._json(404, {"ok": False, "error": f"shot 없음: {b['shot_id']}"}); return
                self._json(200, {"ok": True}); return
            if path == "/api/generate":
                b = self._read_json()
                n = max(1, min(4, int(b.get("n", 1))))
                r = _generate_and_pick(project_dir, b["shot_id"], n=n,
                                       prompt=(b.get("prompt") or None))
                self._json(200, {"ok": True, "new": r["new"],
                                 "pick": r.get("pick"),
                                 "candidates": _candidates(project_dir, b["shot_id"])}); return
            if path == "/api/external":
                b = self._read_json()
                if b.get("data"):
                    raw = base64.b64decode(b["data"].split(",")[-1])
                elif b.get("path"):
                    raw = Path(b["path"]).expanduser().read_bytes()
                else:
                    raise ValueError("data 또는 path 필요")
                name = _add_external(project_dir, b["shot_id"], raw)
                self._json(200, {"ok": True, "name": name,
                                 "candidates": _candidates(project_dir, b["shot_id"])}); return
            if path == "/api/build":
                from ..exporters.capcut_draft import build_capcut_draft
                from ..cli import _default_capcut_folder
                recompile_exports(project_dir)
                running = capcut_running()
                folder = _default_capcut_folder(project_dir)
                res = build_capcut_draft(project_dir, folder_name=folder,
                                         register=not running)
                res["capcut_running"] = running
                res["folder_name"] = folder
                self._json(200, {"ok": True, **res}); return
            self.send_error(404)

    return H


def serve(project_dir: str | Path, port: int = 8770, open_browser: bool = True) -> None:
    project_dir = Path(project_dir).resolve()
    if not (project_dir / "VISUALS.json").is_file():
        raise SystemExit(f"[picker] VISUALS.json 없음: {project_dir}")
    token = secrets.token_urlsafe(32)  # per-session CSRF token
    handler = _make_handler(project_dir, token)
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), handler); port = p; break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("[picker] 포트 점유")
    url = f"http://127.0.0.1:{port}/?token={token}"
    n = len(build_state(project_dir)["shots"])
    print(f"[picker] {n} image shots · {project_dir}")
    print(f"[picker] 열기: {url}  (Ctrl+C 로 종료)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[picker] 종료")
    finally:
        httpd.server_close()
