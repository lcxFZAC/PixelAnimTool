"""Personal local/online workshop. No keys on disk; single-user cloud deployment."""
from __future__ import annotations

import argparse
import base64
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
import engine

ROOT = Path(__file__).resolve().parent
MAX_BODY = 48 * 1024 * 1024


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def folder(self, pid):
        if not isinstance(pid, str) or not re.fullmatch(r"[a-f0-9]{32}", pid):
            raise ValueError("项目 ID 无效")
        return self.root / pid

    def read(self, pid, include_deleted=False):
        with self.lock:
            value = json.loads((self.folder(pid) / "project.json").read_text(encoding="utf-8"))
            if value.get("deletedAt") and not include_deleted:
                raise FileNotFoundError("项目已在回收站")
            return value

    def save(self, project, pid=None, expected=None):
        clean = engine.validate_project(project)
        with self.lock:
            if pid is not None and not (self.folder(pid) / "project.json").exists():
                raise FileNotFoundError("项目不存在")
            pid = pid or uuid.uuid4().hex
            folder = self.folder(pid)
            version = 0
            if (folder / "project.json").exists():
                version = self.read(pid)["version"]
                if expected != version:
                    raise Conflict("项目已被其他页面或生成任务更新，请重新打开后再编辑")
            clean.update(id=pid, version=version + 1, updatedAt=time.strftime("%Y-%m-%d %H:%M:%S"))
            folder.mkdir(exist_ok=True)
            self.atomic(folder / "project.json", clean)
            return clean

    @staticmethod
    def atomic(path, value):
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)

    def set_deleted(self, pid, expected, deleted):
        with self.lock:
            p = self.read(pid, include_deleted=True)
            if p["version"] != expected:
                raise Conflict("项目已更新，请刷新项目列表后重试")
            if bool(p.get("deletedAt")) == deleted:
                raise Conflict("项目状态已改变，请刷新列表")
            p["version"] += 1
            if deleted:
                p["deletedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            else:
                p.pop("deletedAt", None)
            self.atomic(self.folder(pid) / "project.json", p)
            return {"id": pid, "version": p["version"], "deleted": deleted}

    def listing(self, deleted=False):
        with self.lock:
            items = []
            for path in self.root.glob("*/project.json"):
                try:
                    p = json.loads(path.read_text(encoding="utf-8"))
                    if bool(p.get("deletedAt")) != deleted:
                        continue
                    thumbnail = engine.encode_image(engine.flatten(p["clips"][0]["frames"][0], (p["settings"]["width"], p["settings"]["height"]))) if p["clips"] else None
                    items.append({k: p[k] for k in ("id", "name", "settings", "updatedAt", "version")} | {"clips": len(p["clips"]), "thumbnail": thumbnail, "deletedAt": p.get("deletedAt")})
                except (OSError, ValueError, KeyError):
                    continue
            return sorted(items, key=lambda p: p["updatedAt"], reverse=True)


def call_gemini(key, model, prompt, references):
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model):
        raise ValueError("模型 ID 无效")
    parts = [{"text": prompt}]
    for ref in references:
        if ref:
            im = engine.decode_image(ref)
            # Bound request size; pixel masters below this threshold are never resampled.
            if max(im.size) > 2048:
                im.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            parts.append({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(engine.png_bytes(im)).decode()}})
    body = dict(contents=[dict(role="user", parts=parts)], generationConfig=dict(responseModalities=["TEXT", "IMAGE"]))
    request = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise ValueError("模型响应过大，请减少单次帧数")
        result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        # Do not reflect upstream body: it may contain request data or credentials.
        labels = {400: "请求参数或模型不支持生图", 401: "API Key 无效", 403: "Key 权限或地区不可用", 404: "模型不存在或已下线", 429: "额度不足或请求过多", 500: "模型服务异常", 503: "模型服务繁忙"}
        raise ValueError(f"Gemini HTTP {exc.code}：{labels.get(exc.code, '请求失败')}。本次未自动重试；检查后可续做。") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("Gemini 连接失败或超时；请求可能已经计费，请检查网络与服务记录后再续做") from None
    candidates = result.get("candidates", [])
    if not candidates:
        raise ValueError("Gemini 未返回候选图，可能被内容策略拦截；请调整输入")
    images, notes = [], []
    for part in candidates[0].get("content", {}).get("parts", []):
        if part.get("thought"):
            continue
        if "text" in part:
            notes.append(str(part["text"])[:2000])
        blob = part.get("inlineData", part.get("inline_data"))
        if blob and blob.get("mimeType", blob.get("mime_type", "")).startswith("image/"):
            mime = blob.get("mimeType", blob.get("mime_type"))
            images.append(engine.decode_image(f"data:{mime};base64,{blob['data']}"))
    if len(images) != 1:
        raise ValueError(f"模型返回 {len(images)} 张图，要求恰好一张动作图集；请缩小任务后续做")
    return images[0], "\n".join(notes)


class Jobs:
    def __init__(self, store, provider=call_gemini):
        self.store, self.provider = store, provider
        self.items = {}
        self.lock = threading.RLock()

    def busy(self, pid=None):
        with self.lock:
            return any(j["state"] in ("queued", "running", "cancelling") and (pid is None or j["projectId"] == pid) for j in self.items.values())

    def start(self, payload):
        if payload.get("enabled") is not True:
            raise ValueError("Gemini 默认关闭；请先主动开启")
        key = str(payload.get("key", "")).strip()
        if not key or len(key) > 256 or any(c.isspace() for c in key):
            raise ValueError("请输入有效的 API Key（仅用于本次会话，不写入文件）")
        model = str(payload.get("model", "gemini-nano-banana-2.1"))
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model):
            raise ValueError("模型 ID 无效")
        pid = payload["id"]
        with self.lock:
            if self.busy():
                raise Conflict("已有生成任务，请等待结束或取消后再开始")
            project = self.store.read(pid)
            if not project.get("reference"):
                raise ValueError("请先上传并保存正面立绘")
            todo = engine.plan(payload.get("pack", "single"), payload.get("action", "walk"), payload.get("direction", "e"))
            replace = payload.get("replace") is True
            existing = {c["id"] for c in project["clips"]}
            if not replace:
                todo = [(a, d) for a, d in todo if f"{a}_{d}" not in existing]
            if not todo:
                raise ValueError("动作均已存在。若要返修，请选择单个动作并勾选替换；续做只处理缺失项")
            if replace and len(todo) != 1:
                raise ValueError("替换只支持单个动作，避免覆盖整包已验收素材")
            if len(existing | {f"{a}_{d}" for a,d in todo}) > 32:
                raise ValueError("项目动作数量超限，请新建项目")
            jid = uuid.uuid4().hex
            job = dict(id=jid, projectId=pid, state="queued", total=len(todo), done=0, current="准备中", message="", cancel=False)
            self.items[jid] = job
            self.persist(job)
            # Credentials live only in the worker call, not in job state or disk.
            threading.Thread(target=self.run, args=(jid, todo, key, model, str(payload.get("keyword", ""))[:1000], str(payload.get("repair", ""))[:1000]), daemon=True).start()
            return dict(job)

    def persist(self, job):
        folder = self.store.folder(job["projectId"])
        self.store.atomic(folder / "last-job.json", {k:v for k,v in job.items() if k != "cancel"})

    def snapshot(self, jid):
        with self.lock:
            if jid not in self.items:
                raise ValueError("任务已不在当前会话中；重新打开项目后可按缺失动作续做")
            return dict(self.items[jid])

    def cancel(self, jid):
        with self.lock:
            job = self.items[jid]
            if job["state"] in ("queued", "running"):
                job.update(cancel=True, state="cancelling", message="停止后续请求；正在发送的请求无法撤销计费，返回结果会保留")
                self.persist(job)
            return dict(job)

    def run(self, jid, todo, key, model, keyword, repair):
        job = self.items[jid]
        try:
            for action, direction in todo:
                with self.lock:
                    if job["cancel"]:
                        break
                    job.update(state="running", current=f"{action}_{direction}", message="生成中；返回后执行确定性处理与检查")
                    self.persist(job)
                project = self.store.read(job["projectId"])
                cfg = project["settings"]
                prompt, columns, rows, count = engine.generation_prompt(cfg, action, direction, keyword, repair)
                raw, note = self.provider(key, model, prompt, [project["reference"], project.get("masters", {}).get(direction)])
                cid = f"{action}_{direction}"
                sid = uuid.uuid4().hex
                source_dir = self.store.folder(project["id"]) / "sources"
                source_dir.mkdir(exist_ok=True)
                (source_dir / f"{sid}.png").write_bytes(engine.png_bytes(raw))
                (source_dir / f"{sid}.txt").write_text(prompt, encoding="utf-8")
                try:
                    frames, provenance = engine.pixel_process(raw, cfg, columns, rows, count, "magenta", True, True, True)
                except ValueError as exc:
                    raise ValueError(f"源图已保留为 {sid}.png，自动切片失败：{exc}。在项目的源稿列表下载后手动导入。") from exc
                provenance.update(model=model, prompt=prompt, sourceId=sid)
                if note:
                    (source_dir / f"{sid}-response.txt").write_text(note, encoding="utf-8")
                clip = dict(id=cid, action=action, direction=direction, fps=engine.PRESETS["actions"][action]["fps"], loop=action in ("idle", "walk", "battle_idle"), frames=frames, provenance=provenance, review={"state":"candidate"})
                project["clips"] = [c for c in project["clips"] if c["id"] != cid] + [clip]
                saved = self.store.save(project, project["id"], project["version"])
                report = next(c for c in saved["clips"] if c["id"] == cid)["report"]
                with self.lock:
                    job["done"] += 1
                    job["message"] = f"已保存 {cid} 候选；{len(report['blockers'])} 项阻断，{len(report['warnings'])} 项提醒，需视觉验收"
                    self.persist(job)
            with self.lock:
                job["state"] = "cancelled" if job["cancel"] else "completed"
                self.persist(job)
        except Exception as exc:
            with self.lock:
                message = str(exc) if isinstance(exc, (ValueError, Conflict)) else "处理失败，已保留先前完成的动作；请检查服务终端或源稿后续做"
                job.update(state="failed", message=message.replace(key, "[已隐藏]")[:2000])
                self.persist(job)
        finally:
            key = None


def make_handler(store, jobs, cloud=False, password=""):
    csrf = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = "PixelWorkshop/1.0"

        def log_message(self, fmt, *args):
            # URLs, bodies and credentials are never logged.
            pass

        def output(self, value, status=200, mime="application/json; charset=utf-8", filename=None):
            data = json.dumps(value, ensure_ascii=False).encode() if not isinstance(value, bytes) else value
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def guard(self, mutation=False, public=False):
            host = self.headers.get("Host", "")
            if not cloud and host not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
                raise PermissionError("拒绝非本机 Host")
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != host:
                raise PermissionError("拒绝跨站请求")
            if cloud and not public:
                supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
                if not hmac.compare_digest(supplied.encode(), password.encode()):
                    raise PermissionError("请输入在线工坊访问口令")
            if mutation and not hmac.compare_digest(self.headers.get("X-Workshop", ""), csrf):
                raise PermissionError("会话已过期，请刷新页面")

        def do_GET(self):
            try:
                path = urlsplit(self.path).path
                public = path in ("/", "/api/info", "/health") or path.startswith("/static/")
                self.guard(public=public)
                if path == "/health":
                    return self.output({"ok": True, "app": "pixel-workshop"})
                if path == "/api/info":
                    return self.output(dict(csrf=csrf, cloud=cloud, presets=engine.PRESETS, version="1.0.0"))
                if path == "/api/projects":
                    return self.output(store.listing())
                if path == "/api/trash":
                    return self.output(store.listing(deleted=True))
                if path.startswith("/api/projects/"):
                    return self.output(store.read(path.rsplit("/", 1)[-1]))
                if path.startswith("/api/jobs/"):
                    return self.output(jobs.snapshot(path.rsplit("/", 1)[-1]))
                match = re.fullmatch(r"/api/sources/([a-f0-9]{32})(?:/([a-f0-9]{32}))?", path)
                if match:
                    store.read(match[1])
                    folder = store.folder(match[1]) / "sources"
                    if match[2]:
                        return self.output((folder / f"{match[2]}.png").read_bytes(), mime="image/png", filename=f"{match[2]}.png")
                    return self.output([p.stem for p in folder.glob("*.png")])
                relative = "index.html" if path == "/" else path.removeprefix("/static/")
                if path != "/" and not path.startswith("/static/"):
                    raise FileNotFoundError()
                target = (ROOT / "static" / relative).resolve()
                if not target.is_relative_to((ROOT / "static").resolve()) or not target.is_file():
                    raise FileNotFoundError()
                return self.output(target.read_bytes(), mime=(mimetypes.guess_type(target.name)[0] or "application/octet-stream"))
            except Exception as exc:
                self.fail(exc)

        def do_POST(self):
            try:
                self.guard(mutation=True)
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise ValueError("只接受 JSON 请求")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError("请求大小超出限制（48 MB）")
                p = json.loads(self.rfile.read(length))
                path = urlsplit(self.path).path
                if path in ("/api/delete-project", "/api/restore-project"):
                    with jobs.lock:
                        if jobs.busy(p["id"]):
                            raise Conflict("此项目正在生成，请等待结束后再操作")
                        return self.output(store.set_deleted(p["id"], p["version"], path == "/api/delete-project"))
                if path == "/api/save":
                    with jobs.lock:
                        if p.get("id") and jobs.busy(p["id"]):
                            raise Conflict("生成期间暂不能编辑项目；取消或等待完成后再保存")
                        return self.output(store.save(p["project"], p.get("id"), p.get("version")))
                if path == "/api/process":
                    source = engine.decode_image(p["image"])
                    frames, provenance = engine.pixel_process(source, p["settings"], p.get("columns", 1), p.get("rows", 1), p.get("count", 1), p.get("background", "keep"), p.get("quantize", False), p.get("outline", False), p.get("fit", True))
                    return self.output(dict(frames=frames, provenance=provenance))
                if path == "/api/review":
                    with jobs.lock:
                        if jobs.busy(p["id"]):
                            raise Conflict("请在生成结束后验收")
                        project = store.read(p["id"])
                        if project["version"] != p["version"]:
                            raise Conflict("项目已更新，请刷新后重新验收")
                        clip = next((c for c in project["clips"] if c["id"] == p["clipId"]), None)
                        if not clip:
                            raise ValueError("动作不存在")
                        report = engine.audit(clip, project["settings"])
                        state = p["state"]
                        if state not in ("candidate", "accepted", "revision"):
                            raise ValueError("验收状态无效")
                        if state == "accepted" and not report["technicalPass"]:
                            raise ValueError("请先修复检查中的阻断问题")
                        clip["review"] = dict(state=state, hash=engine.appearance_hash(clip, (project["settings"]["width"], project["settings"]["height"]), project["settings"]))
                        return self.output(store.save(project, project["id"], project["version"]))
                if path == "/api/export":
                    project = store.read(p["id"])
                    return self.output(engine.export_project(project, p.get("acceptedOnly", False)), mime="application/zip", filename="pixel-workshop.zip")
                if path == "/api/generate":
                    return self.output(jobs.start(p), status=202)
                if path == "/api/cancel":
                    return self.output(jobs.cancel(p["id"]))
                if path == "/api/shutdown" and not cloud:
                    if jobs.busy():
                        raise Conflict("生成任务仍在运行，请先取消并等待返回")
                    self.output({"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                raise FileNotFoundError()
            except Exception as exc:
                self.fail(exc)

        def fail(self, exc):
            if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                return
            status = 403 if isinstance(exc, PermissionError) else 409 if isinstance(exc, Conflict) else 404 if isinstance(exc, FileNotFoundError) else 400 if isinstance(exc, (ValueError, KeyError, TypeError)) else 500
            message = str(exc) if isinstance(exc, (ValueError, PermissionError, Conflict)) else "找不到资源" if status == 404 else "请求格式错误" if status == 400 else "服务处理失败，请检查数据文件或重启"
            self.output({"error": message[:2000]}, status=status)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--data", default=str(ROOT / "data"))
    parser.add_argument("--cloud", action="store_true")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    password = os.environ.get("WORKSHOP_ACCESS_TOKEN", "")
    if args.cloud and len(password) < 16:
        parser.error("在线模式必须设置至少 16 位的 WORKSHOP_ACCESS_TOKEN")
    store = Store(args.data)
    jobs = Jobs(store)
    server = ThreadingHTTPServer(("0.0.0.0" if args.cloud else "127.0.0.1", args.port), make_handler(store, jobs, args.cloud, password))
    server.daemon_threads = True
    server.timeout = 1
    print(f"Pixel Workshop: http://127.0.0.1:{server.server_port}", flush=True)
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
