from __future__ import annotations

import queue
import subprocess
import tempfile
import threading
from pathlib import Path
from tkinter import (
    BooleanVar,
    Button,
    Canvas,
    END,
    Entry,
    Frame,
    Label,
    LabelFrame,
    OptionMenu,
    Scrollbar,
    StringVar,
    Tk,
    Text,
    colorchooser,
    filedialog,
    messagebox,
)
from tkinter.scrolledtext import ScrolledText

from lyricalign.gui.command import (
    DEFAULT_PIPELINE_PYTHON_PATH,
    DEFAULT_QWEN_MODEL_PATH,
    DEFAULT_R2_CHECKPOINT_PATH,
    GuiRunConfig,
    build_batch_command,
    build_child_environment,
)


LANGUAGES = (
    "Chinese",
    "English",
    "Cantonese",
    "Japanese",
    "French",
    "German",
    "Italian",
    "Portuguese",
    "Russian",
    "Spanish",
)
STAGES = ("all", "prepare", "align", "render")
PRESETS = ("default", "compare-models", "compare-inputs", "all-individual", "full-demo")
SEPARATORS = ("demucs", "spleeter")


class KaraokeGui:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("LyricAlignment 字幕烧录工具")
        self.root.geometry("1060x820")
        self.process: subprocess.Popen[str] | None = None
        self.messages: queue.Queue[str] = queue.Queue()

        self.python_executable = StringVar(value=str(DEFAULT_PIPELINE_PYTHON_PATH))
        self.media = StringVar()
        self.lyrics_file = StringVar()
        self.output_dir = StringVar()
        self.language = StringVar(value="Chinese")
        self.stage = StringVar(value="all")
        self.preset = StringVar(value="default")
        self.separator = StringVar(value="demucs")
        self.render_audio = StringVar(value="source")
        self.font = StringVar()
        self.subtitle_band_height = StringVar()
        self.karaoke_primary_color = StringVar(value="#FFE800")
        self.karaoke_secondary_color = StringVar(value="#909090")
        self.subtitle_preview_color = StringVar(value="#A0A0A0")
        self.subtitle_outline_color = StringVar(value="#101010")
        self.audio_width = StringVar(value="1280")
        self.audio_height = StringVar(value="720")
        self.background_image = StringVar()
        self.device = StringVar(value="cpu")
        self.demucs_device = StringVar(value="cpu")
        self.demucs_torch_home = StringVar()
        self.demucs_command = StringVar()
        self.model = StringVar(value=str(DEFAULT_QWEN_MODEL_PATH))
        self.r1_checkpoint = StringVar()
        self.r2_checkpoint = StringVar(value=str(DEFAULT_R2_CHECKPOINT_PATH))
        self.cache_dir = StringVar()
        self.local_files_only = BooleanVar(value=False)
        self.force_prepare = BooleanVar(value=False)
        self.force_separation = BooleanVar(value=False)
        self.force_align = BooleanVar(value=False)
        self.force_render = BooleanVar(value=False)

        self._build()
        self._poll_messages()

    def _build(self) -> None:
        canvas = Canvas(self.root, highlightthickness=0)
        scrollbar = Scrollbar(self.root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        main = Frame(canvas, padx=12, pady=12)
        window_id = canvas.create_window((0, 0), window=main, anchor="nw")

        def configure_scroll_region(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def configure_window_width(event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        def on_mousewheel(event) -> None:
            if event.num == 4:
                canvas.yview_scroll(-3, "units")
            elif event.num == 5:
                canvas.yview_scroll(3, "units")
            else:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        main.bind("<Configure>", configure_scroll_region)
        canvas.bind("<Configure>", configure_window_width)
        canvas.bind_all("<MouseWheel>", on_mousewheel)
        canvas.bind_all("<Button-4>", on_mousewheel)
        canvas.bind_all("<Button-5>", on_mousewheel)

        files = LabelFrame(main, text="1. 输入文件", padx=10, pady=10)
        files.pack(fill="x")
        self._path_row(files, "音频 / MV", self.media, self._choose_media, row=0)
        self._path_row(files, "歌词 TXT", self.lyrics_file, self._choose_lyrics, row=1)
        Label(files, text="也可以在下面直接粘贴歌词；选择 TXT 时优先使用 TXT。").grid(row=2, column=1, sticky="w")

        Label(files, text="粘贴歌词").grid(row=3, column=0, sticky="nw", pady=(8, 0))
        self.lyrics_text = Text(files, height=6, wrap="word")
        self.lyrics_text.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        files.columnconfigure(1, weight=1)

        output = LabelFrame(main, text="2. 输出与任务", padx=10, pady=10)
        output.pack(fill="x", pady=(10, 0))
        self._path_row(output, "输出目录", self.output_dir, self._choose_output_dir, row=0, directory=True)
        self._option_row(output, "语言", self.language, LANGUAGES, row=1, column=0)
        self._option_row(output, "阶段", self.stage, STAGES, row=1, column=2)
        self._option_row(output, "预设", self.preset, PRESETS, row=2, column=0)
        self._option_row(output, "成品音轨", self.render_audio, ("source", "aligned"), row=2, column=2)
        self._entry_row(output, "字幕字体", self.font, row=3)
        Label(output, text="为空时按语种自动选择；Windows 中文可填 Microsoft YaHei。").grid(row=4, column=1, sticky="w")
        self._entry_row(output, "字幕带高度", self.subtitle_band_height, row=5)
        self._palette_row(output, row=6)
        self._path_row(output, "音频背景图", self.background_image, self._choose_background, row=7)
        self._entry_pair(output, "纯音频画布", self.audio_width, "x", self.audio_height, row=8)
        output.columnconfigure(1, weight=1)
        output.columnconfigure(3, weight=1)

        models = LabelFrame(main, text="3. 模型与分离器", padx=10, pady=10)
        models.pack(fill="x", pady=(10, 0))
        self._path_row(models, "Python 解释器", self.python_executable, self._choose_python, row=0)
        self._option_row(models, "分离器", self.separator, SEPARATORS, row=1, column=0)
        self._option_row(models, "对齐设备", self.device, ("cpu", "cuda"), row=1, column=2)
        self._option_row(models, "Demucs 设备", self.demucs_device, ("cpu", "cuda"), row=2, column=0)
        self._path_row(models, "Demucs 缓存", self.demucs_torch_home, self._choose_demucs_cache, row=3, directory=True)
        self._entry_row(models, "Demucs 命令", self.demucs_command, row=4)
        self._entry_row(models, "Qwen 模型路径", self.model, row=5)
        self._path_row(models, "R1 checkpoint", self.r1_checkpoint, self._choose_r1, row=6, directory=True)
        self._path_row(models, "R2 checkpoint", self.r2_checkpoint, self._choose_r2, row=7, directory=True)
        self._path_row(models, "HF 缓存目录", self.cache_dir, self._choose_cache, row=8, directory=True)
        models.columnconfigure(1, weight=1)

        flags = LabelFrame(main, text="4. 续跑 / 强制刷新", padx=10, pady=10)
        flags.pack(fill="x", pady=(10, 0))
        self._check(flags, "只用本地模型", self.local_files_only, 0)
        self._check(flags, "重做准备", self.force_prepare, 1)
        self._check(flags, "重做人声分离", self.force_separation, 2)
        self._check(flags, "重做对齐", self.force_align, 3)
        self._check(flags, "重做渲染", self.force_render, 4)

        actions = Frame(main)
        actions.pack(fill="x", pady=(10, 0))
        Button(actions, text="预检 dry-run", command=lambda: self._start(dry_run=True)).pack(side="left")
        Button(actions, text="开始生成成品", command=lambda: self._start(dry_run=False)).pack(side="left", padx=(8, 0))
        Button(actions, text="停止当前任务", command=self._stop).pack(side="left", padx=(8, 0))
        Button(actions, text="清空日志", command=lambda: self.log.delete("1.0", END)).pack(side="left", padx=(8, 0))

        Label(main, text="运行日志").pack(anchor="w", pady=(10, 0))
        self.log = ScrolledText(main, height=18, wrap="word")
        self.log.pack(fill="both", expand=True)

    def _path_row(self, parent: Frame, label: str, variable: StringVar, command, *, row: int, directory: bool = False) -> None:
        Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=3)
        text = "选择目录" if directory else "选择文件"
        Button(parent, text=text, command=command).grid(row=row, column=2, sticky="e", padx=(6, 0), pady=3)

    def _entry_row(self, parent: Frame, label: str, variable: StringVar, *, row: int) -> None:
        Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        Entry(parent, textvariable=variable).grid(row=row, column=1, columnspan=3, sticky="ew", pady=3)

    def _entry_pair(
        self,
        parent: Frame,
        label: str,
        left: StringVar,
        separator: str,
        right: StringVar,
        *,
        row: int,
    ) -> None:
        Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        Entry(parent, textvariable=left, width=8).grid(row=row, column=1, sticky="w", pady=3)
        Label(parent, text=separator).grid(row=row, column=1, sticky="w", padx=(76, 0), pady=3)
        Entry(parent, textvariable=right, width=8).grid(row=row, column=1, sticky="w", padx=(96, 0), pady=3)

    def _palette_row(self, parent: Frame, *, row: int) -> None:
        Label(parent, text="字幕颜色").grid(row=row, column=0, sticky="w", pady=3)
        holder = Frame(parent)
        holder.grid(row=row, column=1, columnspan=3, sticky="w", pady=3)
        self._color_button(holder, "高亮", self.karaoke_primary_color).pack(side="left", padx=(0, 8))
        self._color_button(holder, "未唱", self.karaoke_secondary_color).pack(side="left", padx=(0, 8))
        self._color_button(holder, "预览", self.subtitle_preview_color).pack(side="left", padx=(0, 8))
        self._color_button(holder, "描边", self.subtitle_outline_color).pack(side="left")

    def _color_button(self, parent: Frame, label: str, variable: StringVar) -> Button:
        button = Button(parent, text=label, width=8)

        def refresh() -> None:
            button.configure(background=variable.get(), activebackground=variable.get())

        def choose() -> None:
            _rgb, color = colorchooser.askcolor(color=variable.get(), title=f"选择{label}颜色")
            if color:
                variable.set(color.upper())
                refresh()

        button.configure(command=choose)
        refresh()
        return button

    def _option_row(self, parent: Frame, label: str, variable: StringVar, options: tuple[str, ...], *, row: int, column: int) -> None:
        Label(parent, text=label).grid(row=row, column=column, sticky="w", pady=3)
        OptionMenu(parent, variable, *options).grid(row=row, column=column + 1, sticky="ew", pady=3)

    def _check(self, parent: Frame, label: str, variable: BooleanVar, column: int) -> None:
        from tkinter import Checkbutton

        Checkbutton(parent, text=label, variable=variable).grid(row=0, column=column, sticky="w")

    def _choose_media(self) -> None:
        path = filedialog.askopenfilename(
            title="选择音频或 MV",
            filetypes=[
                ("media", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.wav *.flac *.mp3 *.m4a *.aac *.ogg *.opus *.wma"),
                ("all", "*.*"),
            ],
        )
        if path:
            self.media.set(path)

    def _choose_python(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 Python 解释器",
            filetypes=[("python", "python.exe python"), ("all", "*.*")],
        )
        if path:
            self.python_executable.set(path)

    def _choose_lyrics(self) -> None:
        path = filedialog.askopenfilename(title="选择歌词 TXT", filetypes=[("text", "*.txt"), ("all", "*.*")])
        if path:
            self.lyrics_file.set(path)

    def _choose_background(self) -> None:
        path = filedialog.askopenfilename(
            title="选择音频背景图片",
            filetypes=[
                ("image", "*.png *.jpg *.jpeg *.webp *.bmp"),
                ("all", "*.*"),
            ],
        )
        if path:
            self.background_image.set(path)

    def _choose_output_dir(self) -> None:
        self._set_directory(self.output_dir, "选择输出目录")

    def _choose_demucs_cache(self) -> None:
        self._set_directory(self.demucs_torch_home, "选择 Demucs/Torch 权重缓存目录")

    def _choose_r1(self) -> None:
        self._set_directory(self.r1_checkpoint, "选择 R1 checkpoint 目录")

    def _choose_r2(self) -> None:
        self._set_directory(self.r2_checkpoint, "选择 R2 checkpoint 目录")

    def _choose_cache(self) -> None:
        self._set_directory(self.cache_dir, "选择 Hugging Face 缓存目录")

    def _set_directory(self, variable: StringVar, title: str) -> None:
        path = filedialog.askdirectory(title=title)
        if path:
            variable.set(path)

    def _config(self, *, dry_run: bool) -> GuiRunConfig:
        media = Path(self.media.get().strip())
        if not media.is_file():
            raise ValueError("请选择有效的音频或 MV 文件。")
        lyrics = self._lyrics_path(media)
        return GuiRunConfig(
            media=media,
            lyrics=lyrics,
            python_executable=self._required_file(self.python_executable, "Python 解释器"),
            output_dir=self._optional_path(self.output_dir),
            language=self.language.get(),
            stage=self.stage.get(),
            preset=self.preset.get(),
            separator=self.separator.get(),
            render_audio=self.render_audio.get(),
            font=self.font.get(),
            subtitle_band_height=self._optional_int(self.subtitle_band_height, "字幕带高度"),
            karaoke_primary_color=self.karaoke_primary_color.get(),
            karaoke_secondary_color=self.karaoke_secondary_color.get(),
            subtitle_preview_color=self.subtitle_preview_color.get(),
            subtitle_outline_color=self.subtitle_outline_color.get(),
            audio_width=self._required_int(self.audio_width, "纯音频画布宽度"),
            audio_height=self._required_int(self.audio_height, "纯音频画布高度"),
            background_image=self._optional_file(self.background_image, "音频背景图"),
            device=self.device.get(),
            demucs_device=self.demucs_device.get(),
            demucs_torch_home=self._optional_path(self.demucs_torch_home),
            demucs_command=self.demucs_command.get(),
            model=self.model.get(),
            r1_checkpoint=self._optional_path(self.r1_checkpoint),
            r2_checkpoint=self._optional_path(self.r2_checkpoint),
            cache_dir=self._optional_path(self.cache_dir),
            local_files_only=self.local_files_only.get(),
            force_prepare=self.force_prepare.get(),
            force_separation=self.force_separation.get(),
            force_align=self.force_align.get(),
            force_render=self.force_render.get(),
            dry_run=dry_run,
        )

    def _lyrics_path(self, media: Path) -> Path:
        selected = self.lyrics_file.get().strip()
        if selected:
            path = Path(selected)
            if not path.is_file():
                raise ValueError("歌词 TXT 路径无效。")
            return path
        text = self.lyrics_text.get("1.0", END).strip()
        if not text:
            raise ValueError("请选择歌词 TXT，或在文本框粘贴歌词。")
        temp_dir = Path(tempfile.gettempdir()) / "lyricalign_gui_lyrics"
        temp_dir.mkdir(parents=True, exist_ok=True)
        path = temp_dir / f"{media.stem}.txt"
        path.write_text(text + "\n", encoding="utf-8")
        return path

    def _optional_path(self, variable: StringVar) -> Path | None:
        value = variable.get().strip()
        return Path(value) if value else None

    def _optional_file(self, variable: StringVar, label: str) -> Path | None:
        value = variable.get().strip()
        if not value:
            return None
        path = Path(value)
        if not path.is_file():
            raise ValueError(f"{label}路径无效。")
        return path

    def _required_file(self, variable: StringVar, label: str) -> Path:
        value = variable.get().strip()
        path = Path(value) if value else None
        if path is None or not path.is_file():
            raise ValueError(f"{label}路径无效。")
        return path

    def _optional_int(self, variable: StringVar, label: str) -> int | None:
        value = variable.get().strip()
        if not value:
            return None
        return self._parse_positive_int(value, label)

    def _required_int(self, variable: StringVar, label: str) -> int:
        return self._parse_positive_int(variable.get().strip(), label)

    def _parse_positive_int(self, value: str, label: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{label}必须是整数。") from exc
        if parsed <= 0:
            raise ValueError(f"{label}必须大于 0。")
        return parsed

    def _start(self, *, dry_run: bool) -> None:
        if self.process is not None and self.process.poll() is None:
            messagebox.showwarning("任务正在运行", "请先停止或等待当前任务结束。")
            return
        try:
            config = self._config(dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("配置错误", str(exc))
            return
        command = build_batch_command(config)
        self._append_log("\n$ " + subprocess.list2cmdline(command) + "\n")

        def worker() -> None:
            try:
                self.process = subprocess.Popen(
                    command,
                    cwd=str(Path(__file__).resolve().parents[3]),
                    env=build_child_environment(config),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                )
                assert self.process.stdout is not None
                for line in self.process.stdout:
                    self.messages.put(line)
                code = self.process.wait()
                self.messages.put(f"\n[exit code] {code}\n")
            except Exception as exc:  # noqa: BLE001
                self.messages.put(f"\n[gui error] {type(exc).__name__}: {exc}\n")

        threading.Thread(target=worker, daemon=True).start()

    def _stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self._append_log("\n[gui] 已请求停止当前任务。\n")

    def _poll_messages(self) -> None:
        while True:
            try:
                self._append_log(self.messages.get_nowait())
            except queue.Empty:
                break
        self.root.after(100, self._poll_messages)

    def _append_log(self, text: str) -> None:
        self.log.insert(END, text)
        self.log.see(END)


def main() -> int:
    root = Tk()
    KaraokeGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
