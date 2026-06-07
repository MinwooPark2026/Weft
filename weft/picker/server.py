from __future__ import annotations

import base64
import json
import shutil
import subprocess
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..assets import append_candidates, recompile_exports, _sync_shot_prompt

HTML_PATH = Path(__file__).resolve().parent / "picker.html"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
IMG_SUBDIR = "images/openai"


# ------------------------------------------------------------------ state ---

def _candidates(project_dir: Path, shot_id: str) -> list[str]:
    d = project_dir / "SHOTS" / shot_id / IMG_SUBDIR
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith("."))


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
        pick = sel.split("/")[-1] if sel.startswith(IMG_SUBDIR) else (cands[0] if cands else "")
        shots.append({
            "shot_id": sid,
            "prompt": s.get("prompt", ""),
            "motion": (s.get("motion") or {}).get("type", "static"),
            "context": _narration_context(beats_by_id, s.get("cover", {})),
            "candidates": cands,
            "pick": pick,
        })
    return {"title": visuals.get("style_bible", "")[:60] or "Weft picker",
            "project_dir": str(project_dir), "shots": shots}


def _save_pick(project_dir: Path, shot_id: str, candidate: str) -> None:
    picks_path = project_dir / "PICKS.json"
    picks = json.loads(picks_path.read_text(encoding="utf-8"))
    picks.setdefault("selections", {})
    picks.setdefault("auto_picked", [])
    picks.setdefault("overridden", [])
    picks["selections"][shot_id] = f"{IMG_SUBDIR}/{candidate}"
    picks["auto_picked"] = [sid for sid in picks["auto_picked"] if sid != shot_id]
    if shot_id not in picks["overridden"]:
        picks["overridden"].append(shot_id)
    picks_path.write_text(json.dumps(picks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _save_prompt(project_dir: Path, shot_id: str, prompt: str) -> None:
    vpath = project_dir / "VISUALS.json"
    visuals = json.loads(vpath.read_text(encoding="utf-8"))
    for s in visuals["shots"]:
        if s["id"] == shot_id:
            s["prompt"] = prompt
    vpath.write_text(json.dumps(visuals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _sync_shot_prompt(project_dir, shot_id, prompt)


def _add_external(project_dir: Path, shot_id: str, data: bytes) -> str:
    from PIL import Image
    import io
    d = project_dir / "SHOTS" / shot_id / IMG_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(p.stem.split("_")[-1]) for p in d.glob("external_*.png")
                 if p.stem.split("_")[-1].isdigit()], default=0)
    name = f"external_{n:03d}.png"
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.save(d / name)
    return name


def _capcut_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", "CapCut"], capture_output=True).returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------- handler ---

def _make_handler(project_dir: Path):
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

        def do_GET(self):
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
                p = (project_dir / "SHOTS" / sid / IMG_SUBDIR / fn).resolve()
                try:
                    p.relative_to((project_dir / "SHOTS").resolve())
                except ValueError:
                    self.send_error(403); return
                if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
                    self.send_error(404); return
                data = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_error(404)

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            try:
                if path == "/api/pick":
                    b = self._read_json()
                    _save_pick(project_dir, b["shot_id"], b["candidate"])
                    self._json(200, {"ok": True}); return
                if path == "/api/prompt":
                    b = self._read_json()
                    _save_prompt(project_dir, b["shot_id"], b["prompt"].strip())
                    self._json(200, {"ok": True}); return
                if path == "/api/generate":
                    b = self._read_json()
                    r = append_candidates(project_dir, b["shot_id"], n=int(b.get("n", 1)),
                                          prompt=(b.get("prompt") or None))
                    self._json(200, {"ok": True, "new": r["new"],
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
                    recompile_exports(project_dir)
                    running = _capcut_running()
                    res = build_capcut_draft(project_dir, folder_name="weft_ep2",
                                             register=not running)
                    res["capcut_running"] = running
                    self._json(200, {"ok": True, **res}); return
                self.send_error(404)
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)})

    return H


def serve(project_dir: str | Path, port: int = 8770, open_browser: bool = True) -> None:
    project_dir = Path(project_dir).resolve()
    if not (project_dir / "VISUALS.json").is_file():
        raise SystemExit(f"[picker] VISUALS.json 없음: {project_dir}")
    handler = _make_handler(project_dir)
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), handler); port = p; break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("[picker] 포트 점유")
    url = f"http://127.0.0.1:{port}/"
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
