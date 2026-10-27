import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import Settings
from .. import APP_NAME, __version__
from ..media import AUDIO_SUFFIXES, IMAGE_SUFFIXES, VIDEO_SUFFIXES
from ..timeline import Timeline, Style, quality
from .service import Projects
from ..text_normalization import clean_lyric_text

MAX_AUDIO_MB = 200
MAX_IMAGE_MB = 30


class Create(BaseModel):
    video: str
    song: str = ""
    artist: str = ""
    language: str = "auto"


def requested_canvas(value):
    """Parse an optional ``宽x高`` canvas request; empty means follow the image."""
    value = (value or "").strip().lower()
    if not value:
        return None
    parts = value.split("x")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("画布尺寸格式应为 宽x高，例如 1920x1080")
    width, height = (int(part) for part in parts)
    if not (320 <= width <= 3840 and 240 <= height <= 3840) or width % 2 or height % 2:
        raise ValueError("画布宽高必须是 320–3840 之间的偶数")
    return (width, height)


async def save_upload(file, folder, allowed, label, limit_mb):
    """Stream one uploaded file to disk and reject unsupported or oversized input."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed:
        raise ValueError(f"不支持的{label}格式")
    path = folder / (os.urandom(16).hex() + suffix)
    limit = limit_mb * 1024 * 1024
    written = 0
    try:
        with path.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise ValueError(f"{label}文件不能超过 {limit_mb} MB")
                target.write(chunk)
        if not written:
            raise ValueError(f"{label}文件为空")
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


class Lyrics(BaseModel):
    text: str | None = None
    candidate: str | None = None


class Information(BaseModel):
    song: str = ""
    artist: str = ""
    language: str = Field(default="auto", pattern="^(auto|zh|ja|en)$")


class Edit(BaseModel):
    revision: int
    timeline: Timeline


class Task(BaseModel):
    action: str
    options: dict = Field(default_factory=dict)


def create_app(settings=None):
    settings = settings or Settings()
    store = Projects(settings)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            store.pool.shutdown(wait=False)

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    app.state.projects = store

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        # Prevent external web pages from reading local files or invoking local actions.
        try:
            host = urlparse("http://" + request.headers.get("host", "")).hostname
        except ValueError:
            host = None
        if host not in ("127.0.0.1", "localhost", "::1", "testserver"):
            return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "不允许跨站访问本地项目"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return JSONResponse(
            {"detail": str(error)},
            status_code=409
            if "正在处理" in str(error) or "已更新" in str(error)
            else 400,
        )

    @app.exception_handler(FileNotFoundError)
    async def not_found(request, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.get("/api/health")
    def health():
        checks = {}
        for name in ("ffmpeg", "ffprobe"):
            try:
                checks[name] = {"ok": True, "path": settings.tool(name)}
            except Exception as e:
                checks[name] = {"ok": False, "error": str(e)}
        for name, path in (
            ("tifa_model", settings.tifa_model),
            ("tifa_source", settings.tifa_source / "infer.py"),
            ("tifa_python", settings.tifa_python),
        ):
            checks[name] = {"ok": path.is_file(), "path": str(path)}
        try:
            from ..media import run

            filters = run(
                [settings.tool("ffmpeg"), "-hide_banner", "-filters"], timeout=15
            )
            checks["libass"] = {
                "ok": " subtitles " in filters,
                "error": "FFmpeg 需要包含 libass subtitles 滤镜",
            }
        except Exception as e:
            checks["libass"] = {"ok": False, "error": str(e)}
        locator_ready = all(
            p.is_file()
            for p in (
                settings.locator_python,
                settings.asr_model / "config.json",
            )
        )
        for name, path in (
            ("qwen_python", settings.locator_python),
            ("asr_model", settings.asr_model / "config.json"),
            ("kim_python", settings.separation_python),
            ("kim_model", settings.kim_model),
            (
                "kim_config",
                settings.kim_source / "configs/config_vocals_mel_band_roformer.yaml",
            ),
            ("kim_source", settings.kim_source / "utils.py"),
        ):
            checks[name] = {"ok": path.is_file(), "path": str(path)}
        return {
            "ok": all(c["ok"] for c in checks.values()),
            "checks": checks,
            "default_device": settings.device,
            "devices": ["cuda"],
            "projects": str(settings.projects),
            "locator_ready": locator_ready,
            "kim_ready": all(
                path.is_file()
                for path in (
                    settings.separation_python,
                    settings.kim_model,
                    settings.kim_source
                    / "configs/config_vocals_mel_band_roformer.yaml",
                    settings.kim_source / "utils.py",
                )
            ),
        }

    @app.get("/api/projects")
    def list_projects():
        return store.list()

    @app.post("/api/projects")
    def create(body: Create):
        return store.create(body.video, body.song, body.artist, body.language)

    @app.post("/api/upload")
    async def upload(
        video: UploadFile | None = File(None),
        audio: UploadFile | None = File(None),
        background: UploadFile | None = File(None),
        canvas: str = Form(""),
        song: str = Form(""),
        artist: str = Form(""),
        language: str = Form("auto"),
    ):
        uploads = settings.projects / "_uploads"
        uploads.mkdir(exist_ok=True)
        if audio or background:
            if not (audio and background):
                raise ValueError("音频和背景图需要一起提供")
            size = requested_canvas(canvas)
            audio_path = await save_upload(
                audio, uploads, AUDIO_SUFFIXES, "音频", MAX_AUDIO_MB
            )
            try:
                background_path = await save_upload(
                    background, uploads, IMAGE_SUFFIXES, "背景图", MAX_IMAGE_MB
                )
            except Exception:
                audio_path.unlink(missing_ok=True)
                raise
            try:
                project = store.create(
                    None,
                    song,
                    artist,
                    language,
                    source={
                        "audio": str(audio_path),
                        "background": str(background_path),
                        "canvas": size,
                        "name": audio.filename,
                    },
                )
            except Exception:
                audio_path.unlink(missing_ok=True)
                background_path.unlink(missing_ok=True)
                raise
            return store.get(project["id"])
        if not video:
            raise ValueError("请选择视频文件，或同时提供音频与背景图")
        suffix = Path(video.filename or "").suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            raise ValueError("不支持的视频格式")
        path = uploads / (os.urandom(16).hex() + suffix)
        try:
            with path.open("wb") as f:
                while chunk := await video.read(1024 * 1024):
                    f.write(chunk)
            project = store.create(str(path), song, artist, language)
            store.update(
                project["id"],
                video_name=(video.filename or "视频" + suffix)
                .replace("\\", "/")
                .rsplit("/", 1)[-1],
            )
            return store.get(project["id"])
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await video.close()

    @app.get("/api/projects/{pid}")
    def get(pid: str):
        return store.get(pid)

    @app.put("/api/workflows/{pid}/information")
    def information(pid: str, body: Information):
        with store.lock:
            p = store.idle(pid)
            changed = any(p[k] != v for k, v in body.model_dump().items())
            store.update(pid, **body.model_dump())
            if changed:
                (store.folder(pid) / "candidates.json").unlink(missing_ok=True)
        return store.get(pid)

    @app.post("/api/projects/{pid}/lyrics")
    def lyrics(pid: str, body: Lyrics):
        return store.set_lyrics(pid, body.text, body.candidate)

    @app.post("/api/lyrics/import")
    async def import_lyrics(file: UploadFile = File(...)):
        """Decode a user-selected lyric file for review, without saving it as confirmed text."""
        try:
            if Path(file.filename or "").suffix.lower() not in (".txt", ".lrc"):
                raise ValueError("歌词文件必须是 TXT 或 LRC")
            data = await file.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError("歌词文件不能超过 2 MB")
            encodings = (
                ("utf-16",)
                if data.startswith((b"\xff\xfe", b"\xfe\xff"))
                else ("utf-8-sig", "gb18030")
            )
            for encoding in encodings:
                try:
                    text = data.decode(encoding)
                    if "\x00" in text:
                        raise ValueError("歌词文件含有无效字符，请另存为 UTF-8 TXT")
                    return {
                        "text": clean_lyric_text(text),
                        "encoding": encoding,
                        "saved": False,
                    }
                except UnicodeDecodeError:
                    continue
            raise ValueError("无法读取歌词文件，请另存为 UTF-8 TXT")
        finally:
            await file.close()

    @app.put("/api/projects/{pid}/timeline")
    def edit(pid: str, body: Edit):
        return store.save_timeline(pid, body.timeline, body.revision)

    @app.post("/api/projects/{pid}/undo")
    def undo(pid: str):
        return store.undo(pid)

    @app.post("/api/projects/{pid}/shift")
    def shift(pid: str, body: dict):
        p = store.idle(pid)
        t = Timeline.model_validate(p["timeline"])
        delta = float(body["seconds"])
        for line in t.lines:
            for unit in line.units:
                if unit.start is not None:
                    unit.start += delta
                    unit.end += delta
                    for part in (*unit.phones, *unit.readings):
                        part.start += delta
                        part.end += delta
        quality(t)
        return store.save_timeline(pid, t, int(body["revision"]))

    @app.put("/api/projects/{pid}/style")
    def style(pid: str, body: Style):
        with store.lock:
            store.idle(pid)
            store.update(
                pid,
                style=body.model_copy(
                    update={"mode": "double", "lead": 0, "auto_complete": True}
                ).model_dump(),
            )
        return store.get(pid)

    @app.post("/api/projects/{pid}/tasks")
    def task(pid: str, body: Task):
        return store.submit(pid, body.action, body.options)

    @app.post("/api/workflows/{pid}/steps/{step}")
    def step_task(pid: str, step: int, body: Task):
        allowed = {
            1: ("media",),
            2: ("search",),
            3: ("align", "segment", "complete"),
            4: ("export",),
        }
        if body.action not in allowed.get(step, ()):
            raise ValueError("操作不属于当前步骤")
        p = store.get(pid)
        if not p["workflow"]["steps"][step - 1]["available"]:
            raise ValueError("请先完成前一步；没有完全匹配也可以完成时间戳步骤")
        return store.submit(pid, body.action, body.options)

    @app.get("/api/projects/{pid}/video")
    def original(pid: str):
        return FileResponse(store.get(pid)["video"])

    @app.get("/api/projects/{pid}/files/{name:path}")
    def artifact(pid: str, name: str):
        folder = store.folder(pid).resolve()
        path = (folder / name).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            raise FileNotFoundError("文件不存在")
        return FileResponse(path)

    app.mount(
        "/",
        StaticFiles(
            directory=Path(__file__).resolve().parents[1] / "frontend", html=True
        ),
        name="frontend",
    )
    return app
