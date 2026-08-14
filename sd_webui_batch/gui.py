from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
from types import SimpleNamespace
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any

from .batching import (
    DEFAULT_MAX_IMAGES_PER_REQUEST,
    BatchChunk,
    split_payload_into_chunks,
)
from .cli import build_payload, strip_comment_fields
from .client import SdWebuiApiError, SdWebuiClient, SdWebuiTransportError
from .dynamic_prompts import (
    DynamicPromptError,
    DynamicPromptExpander,
    plan_dynamic_prompt_chunks,
    write_dynamic_manifest,
)
from .parser import PromptJob, PromptParseError, parse_prompt_note, read_text_file
from .prompt_library import DEFAULT_DATABASE_PATH, PromptLibrary


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROGRESS_POLL_INTERVAL_SECONDS = 1.0
EVENT_DRAIN_BATCH_SIZE = 50
DRY_RUN_REQUEST_DETAIL_LIMIT = 12


def format_dry_run_request_lines(
    chunks: tuple[BatchChunk, ...],
    *,
    detail_limit: int = DRY_RUN_REQUEST_DETAIL_LIMIT,
) -> list[str]:
    """Format a bounded request preview so a large Dry Run stays responsive."""

    def describe(chunk: BatchChunk) -> str:
        return (
            f"request {chunk.ordinal}/{chunk.total_chunks}: images "
            f"{chunk.image_start}-{chunk.image_end}/{chunk.total_images}, "
            f"n_iter={chunk.payload['n_iter']}, "
            f"seed={chunk.payload.get('seed', 'default')}"
        )

    if detail_limit < 2 or len(chunks) <= detail_limit:
        return [describe(chunk) for chunk in chunks]

    first_count = detail_limit // 2
    last_count = detail_limit - first_count
    omitted = len(chunks) - first_count - last_count
    return [
        *(describe(chunk) for chunk in chunks[:first_count]),
        f"... {omitted} request(s) omitted from the GUI log ...",
        *(describe(chunk) for chunk in chunks[-last_count:]),
    ]


class BatchRunnerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("SD WebUI Batch Runner")
        self.root.geometry("1120x820")
        self.root.minsize(980, 700)

        self.base_payload: dict[str, Any] = {}
        self.jobs: list[PromptJob] = []
        self.worker: threading.Thread | None = None
        self.stop_after_current = threading.Event()
        self.interrupt_requested = threading.Event()
        self.skip_requested = threading.Event()
        self.control_in_flight = threading.Event()
        self.control_finished = threading.Event()
        self.control_finished.set()
        self.progress_poll_warning_sent = threading.Event()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.active_run_id = 0
        self.generation_running = False
        self.webui_controls_enabled = False
        self.run_preparing = False
        self.settings_loaded = False
        self.library_selection: tuple[Path, tuple[int, ...]] | None = None

        self.prompt_path_var = tk.StringVar(value=str(PROJECT_ROOT / "examples" / "prompts.txt"))
        self.payload_path_var = tk.StringVar(
            value=os.environ.get(
                "SD_WEBUI_PAYLOAD",
                str(PROJECT_ROOT / "examples" / "payload.json"),
            )
        )
        self.url_var = tk.StringVar(
            value=os.environ.get("SD_WEBUI_URL", "http://127.0.0.1:7860")
        )
        self.timeout_var = tk.StringVar(value="86400")
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.dynamic_prompts_var = tk.BooleanVar(
            value=os.environ.get("SD_WEBUI_DYNAMIC_PROMPTS", "").casefold()
            in {"1", "true", "yes", "on"}
        )
        self.wildcards_dir_var = tk.StringVar(
            value=os.environ.get("SD_WEBUI_WILDCARDS", "")
        )
        self.manifest_dir_var = tk.StringVar(
            value=os.environ.get(
                "SD_WEBUI_MANIFEST_DIR",
                str(PROJECT_ROOT / "manifests"),
            )
        )

        self.n_iter_var = tk.StringVar(value="1")
        self.batch_size_var = tk.StringVar(value="1")
        self.limit_var = tk.StringVar(value="0")
        self.steps_var = tk.StringVar()
        self.cfg_scale_var = tk.StringVar()
        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        self.seed_var = tk.StringVar()
        self.sampler_name_var = tk.StringVar()
        self.scheduler_var = tk.StringVar()

        self.save_images_var = tk.BooleanVar(value=True)
        self.send_images_var = tk.BooleanVar(value=False)
        self.sanitize_subdir_var = tk.BooleanVar(value=True)
        self.stop_on_error_var = tk.BooleanVar(value=False)

        self.enable_hr_var = tk.BooleanVar(value=False)
        self.hr_upscaler_var = tk.StringVar()
        self.hr_scale_var = tk.StringVar()
        self.hr_second_pass_steps_var = tk.StringVar()
        self.denoising_strength_var = tk.StringVar()
        self.hr_cfg_scale_var = tk.StringVar()
        self.hr_rescale_cfg_var = tk.StringVar()
        self.hr_resize_x_var = tk.StringVar()
        self.hr_resize_y_var = tk.StringVar()

        self.checkpoint_var = tk.StringVar()
        self.vae_var = tk.StringVar()
        self.clip_skip_var = tk.StringVar()

        self.status_var = tk.StringVar(value="待機中")
        self.job_count_var = tk.StringVar(value="ジョブ未読み込み")

        self._build_ui()
        self.n_iter_var.trace_add("write", self._on_plan_setting_changed)
        self.batch_size_var.trace_add("write", self._on_plan_setting_changed)
        self.sanitize_subdir_var.trace_add("write", lambda *_: self._update_job_tree(self.jobs))
        self.dynamic_prompts_var.trace_add("write", self._on_plan_setting_changed)
        self._load_payload_if_present()
        self.refresh_jobs(show_errors=False)
        self.settings_loaded = True
        self.root.after(100, self._drain_events)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        outer = ttk.Frame(self.root, padding=10)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)

        self._build_file_section(outer)
        self._build_settings_section(outer)
        self._build_hires_section(outer)
        self._build_action_section(outer)
        self._build_jobs_section(outer)
        self._build_log_section(outer)

    def _build_file_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="ファイル / 接続")
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        frame.columnconfigure(5, weight=1)

        ttk.Label(frame, text="プロンプト").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(frame, textvariable=self.prompt_path_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(frame, text="選択", command=self.browse_prompt).grid(row=0, column=2, padx=4)
        ttk.Button(frame, text="再読込", command=self.refresh_jobs).grid(row=0, column=3, padx=4)

        ttk.Label(frame, text="Payload JSON").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(frame, textvariable=self.payload_path_var).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(frame, text="選択", command=self.browse_payload).grid(row=1, column=2, padx=4)
        ttk.Button(frame, text="保存", command=self.save_payload).grid(row=1, column=3, padx=4)

        ttk.Label(frame, text="WebUI URL").grid(row=0, column=4, sticky="w", padx=(14, 4))
        ttk.Entry(frame, textvariable=self.url_var).grid(row=0, column=5, sticky="ew", padx=4)
        ttk.Label(frame, text="Timeout").grid(row=1, column=4, sticky="w", padx=(14, 4))
        ttk.Entry(frame, width=10, textvariable=self.timeout_var).grid(row=1, column=5, sticky="w", padx=4)

        ttk.Label(frame, text="User").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(frame, textvariable=self.username_var).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Label(frame, text="Password").grid(row=2, column=2, sticky="e", padx=4)
        ttk.Entry(frame, show="*", textvariable=self.password_var).grid(row=2, column=3, sticky="ew", padx=4)
        ttk.Label(frame, textvariable=self.job_count_var).grid(row=2, column=4, sticky="w", padx=(14, 4))
        ttk.Button(frame, text="SQLite管理", command=self.open_prompt_library).grid(
            row=2, column=5, padx=4
        )

        ttk.Checkbutton(
            frame,
            text="Runner Dynamic Prompts (per image / B=1)",
            variable=self.dynamic_prompts_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        ttk.Label(frame, text="Wildcards").grid(row=3, column=2, sticky="e", padx=4)
        ttk.Entry(frame, textvariable=self.wildcards_dir_var).grid(
            row=3, column=3, columnspan=2, sticky="ew", padx=4
        )
        ttk.Button(frame, text="Select", command=self.browse_wildcards).grid(
            row=3, column=5, padx=4
        )

        ttk.Label(frame, text="Manifest directory").grid(
            row=4, column=0, sticky="w", padx=8, pady=6
        )
        ttk.Entry(frame, textvariable=self.manifest_dir_var).grid(
            row=4, column=1, columnspan=4, sticky="ew", padx=4
        )
        ttk.Button(frame, text="Select", command=self.browse_manifest_dir).grid(
            row=4, column=5, padx=4
        )

    def _build_settings_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="生成設定")
        frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for column in range(8):
            frame.columnconfigure(column, weight=1 if column in {1, 3, 5, 7} else 0)

        self._entry_row(frame, 0, 0, "生成枚数", self.n_iter_var, "タイトルごとの枚数。WebUI の Batch Count / API の n_iter。")
        self._entry_row(frame, 0, 2, "Batch Size", self.batch_size_var)
        self._entry_row(frame, 0, 4, "先頭N件", self.limit_var, "0 は全件。")
        self._entry_row(frame, 0, 6, "Seed", self.seed_var)

        self._entry_row(frame, 1, 0, "Steps", self.steps_var)
        self._entry_row(frame, 1, 2, "CFG Scale", self.cfg_scale_var)
        self._entry_row(frame, 1, 4, "Width", self.width_var)
        self._entry_row(frame, 1, 6, "Height", self.height_var)

        self._entry_row(frame, 2, 0, "Sampler", self.sampler_name_var)
        self._entry_row(frame, 2, 2, "Scheduler", self.scheduler_var)
        ttk.Checkbutton(frame, text="画像を保存", variable=self.save_images_var).grid(row=2, column=4, sticky="w", padx=8, pady=5)
        ttk.Checkbutton(frame, text="APIレスポンスに画像を含める", variable=self.send_images_var).grid(row=2, column=5, columnspan=3, sticky="w", padx=8)

        ttk.Checkbutton(frame, text="サブディレクトリ名をWindows向けに整形", variable=self.sanitize_subdir_var).grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=5)
        ttk.Checkbutton(frame, text="エラーで停止", variable=self.stop_on_error_var).grid(row=3, column=3, columnspan=2, sticky="w", padx=8)
        ttk.Label(
            frame,
            text=f"自動分割: 1送信あたり最大{DEFAULT_MAX_IMAGES_PER_REQUEST}枚 / グリッド生成なし",
        ).grid(row=3, column=5, columnspan=3, sticky="w", padx=8)

        ttk.Label(frame, text="Negative Prompt").grid(row=4, column=0, sticky="nw", padx=8, pady=(6, 4))
        self.negative_prompt_text = tk.Text(frame, height=3, wrap="word", undo=True)
        self.negative_prompt_text.grid(row=4, column=1, columnspan=7, sticky="ew", padx=4, pady=(6, 8))

    def _build_hires_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Hires. fix / Override Settings")
        frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for column in range(8):
            frame.columnconfigure(column, weight=1 if column in {1, 3, 5, 7} else 0)

        ttk.Checkbutton(frame, text="Hires. fix", variable=self.enable_hr_var).grid(row=0, column=0, sticky="w", padx=8, pady=5)
        self._entry_row(frame, 0, 2, "Upscaler", self.hr_upscaler_var)
        self._entry_row(frame, 0, 4, "Upscale by", self.hr_scale_var)
        self._entry_row(frame, 0, 6, "Hires steps", self.hr_second_pass_steps_var)

        self._entry_row(frame, 1, 0, "Denoising", self.denoising_strength_var)
        self._entry_row(frame, 1, 2, "Hires CFG", self.hr_cfg_scale_var)
        self._entry_row(frame, 1, 4, "Rescale CFG", self.hr_rescale_cfg_var)
        self._entry_row(frame, 1, 6, "Resize X", self.hr_resize_x_var)

        self._entry_row(frame, 2, 0, "Resize Y", self.hr_resize_y_var)
        self._entry_row(frame, 2, 2, "Checkpoint", self.checkpoint_var)
        self._entry_row(frame, 2, 4, "SD VAE", self.vae_var)
        self._entry_row(frame, 2, 6, "Clip Skip", self.clip_skip_var)

    def _build_action_section(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(6, weight=1)

        self.preview_button = ttk.Button(frame, text="Dry Run", command=self.preview_payloads)
        self.preview_button.grid(row=0, column=0, padx=(0, 6))
        self.start_button = ttk.Button(frame, text="生成開始", command=self.start_generation)
        self.start_button.grid(row=0, column=1, padx=6)
        self.stop_button = ttk.Button(
            frame,
            text=f"現在の{DEFAULT_MAX_IMAGES_PER_REQUEST}枚送信後に停止",
            command=self.request_stop,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=2, padx=6)
        self.interrupt_button = ttk.Button(
            frame,
            text="WebUI Interrupt",
            command=self.interrupt_webui,
            state="disabled",
        )
        self.interrupt_button.grid(row=0, column=3, padx=6)
        self.skip_button = ttk.Button(
            frame,
            text="WebUI Skip",
            command=self.skip_webui,
            state="disabled",
        )
        self.skip_button.grid(row=0, column=4, padx=6)

        ttk.Label(frame, textvariable=self.status_var).grid(row=0, column=6, sticky="e")
        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=1)
        self.progress.grid(row=1, column=0, columnspan=7, sticky="ew", pady=(8, 0))

    def _build_jobs_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="ジョブ")
        frame.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(0, weight=1)

        columns = ("index", "title", "style", "upscaler", "images", "requests", "subdir")
        self.job_tree = ttk.Treeview(frame, columns=columns, show="headings", height=7)
        self.job_tree.heading("index", text="#")
        self.job_tree.heading("title", text="タイトル")
        self.job_tree.heading("style", text="絵柄")
        self.job_tree.heading("upscaler", text="適用Upscaler")
        self.job_tree.heading("images", text="総画像数")
        self.job_tree.heading("requests", text="送信回数")
        self.job_tree.heading("subdir", text="Subdirectory override")
        self.job_tree.column("index", width=50, anchor="center", stretch=False)
        self.job_tree.column("title", width=260)
        self.job_tree.column("style", width=85, anchor="center", stretch=False)
        self.job_tree.column("upscaler", width=150, stretch=False)
        self.job_tree.column("images", width=90, anchor="center", stretch=False)
        self.job_tree.column("requests", width=80, anchor="center", stretch=False)
        self.job_tree.column("subdir", width=340)
        self.job_tree.grid(row=0, column=0, sticky="ew", padx=8, pady=8)

    def _build_log_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="ログ")
        frame.grid(row=5, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(frame, wrap="word", height=12, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

    def _entry_row(
        self,
        parent: ttk.Frame,
        row: int,
        column: int,
        label: str,
        variable: tk.StringVar,
        help_text: str | None = None,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=8, pady=5)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=column + 1, sticky="ew", padx=4, pady=5)
        if help_text:
            entry.configure()
            ToolTip(entry, help_text)

    def browse_prompt(self) -> None:
        path = filedialog.askopenfilename(
            title="プロンプトファイルを選択",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.library_selection = None
            self.prompt_path_var.set(path)
            self.refresh_jobs()

    def open_prompt_library(self) -> None:
        from .library_gui import PromptLibraryWindow

        current_path = (
            self.library_selection[0]
            if self.library_selection is not None
            else DEFAULT_DATABASE_PATH
        )
        PromptLibraryWindow(
            self.root,
            database_path=current_path,
            on_load=self._load_library_selection,
        )

    def _load_library_selection(
        self, database_path: Path, job_ids: tuple[int, ...]
    ) -> None:
        self.library_selection = (database_path, job_ids)
        self.prompt_path_var.set(str(database_path))
        self.refresh_jobs(show_errors=True)
        self.status_var.set("SQLiteライブラリから読込済み：Dry Runで確認してください")

    def browse_payload(self) -> None:
        path = filedialog.askopenfilename(
            title="Payload JSONを選択",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.payload_path_var.set(path)
            self.load_payload()

    def browse_wildcards(self) -> None:
        path = filedialog.askdirectory(title="Select wildcard directory")
        if path:
            self.wildcards_dir_var.set(path)

    def browse_manifest_dir(self) -> None:
        path = filedialog.askdirectory(title="Select manifest directory")
        if path:
            self.manifest_dir_var.set(path)

    def _load_payload_if_present(self) -> None:
        if Path(self.payload_path_var.get()).exists():
            self.load_payload(show_message=False)

    def load_payload(self, show_message: bool = True) -> None:
        path = Path(self.payload_path_var.get())
        try:
            with path.open("r", encoding="utf-8-sig") as file:
                data = json.load(file)
            if not isinstance(data, dict):
                raise ValueError("JSON root must be an object.")
        except Exception as error:
            messagebox.showerror("Payload JSON", f"読み込みに失敗しました。\n{error}")
            return

        self.base_payload = strip_comment_fields(data)
        self._populate_form(self.base_payload)
        self.refresh_jobs(show_errors=False)
        if show_message:
            self._append_log(f"Loaded payload: {path}")

    def save_payload(self) -> None:
        path_text = self.payload_path_var.get().strip()
        if not path_text:
            path = filedialog.asksaveasfilename(
                title="Payload JSONを保存",
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            )
            if not path:
                return
            self.payload_path_var.set(path)

        try:
            payload = self._collect_base_payload()
        except ValueError as error:
            messagebox.showerror("Payload JSON", str(error))
            return

        payload_with_comment = self._with_n_iter_comment(payload)
        path = Path(self.payload_path_var.get())
        path.write_text(json.dumps(payload_with_comment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.base_payload = payload
        self._append_log(f"Saved payload: {path}")

    def refresh_jobs(self, show_errors: bool = True) -> None:
        try:
            if self.library_selection is not None:
                database_path, job_ids = self.library_selection
                jobs = PromptLibrary(database_path).load_generation_jobs(job_ids)
                if not jobs:
                    raise PromptParseError("SQLiteライブラリの選択項目がありません。")
            else:
                path = Path(self.prompt_path_var.get())
                text = read_text_file(path)
                jobs = parse_prompt_note(text)
        except Exception as error:
            self.jobs = []
            self._update_job_tree([])
            self.job_count_var.set("ジョブ未読み込み")
            if show_errors:
                messagebox.showerror("プロンプト", f"読み込みに失敗しました。\n{error}")
            return

        self.jobs = jobs
        self._update_job_tree(jobs)
        source_label = "SQLite" if self.library_selection is not None else "txt"
        self.job_count_var.set(f"{len(jobs)} job(s) loaded / {source_label}")

    def _update_job_tree(self, jobs: list[PromptJob]) -> None:
        for item in self.job_tree.get_children():
            self.job_tree.delete(item)

        n_iter = self._safe_int(self.n_iter_var.get(), default=1)
        batch_size = self._safe_int(self.batch_size_var.get(), default=1)
        try:
            total_images, request_count = calculate_job_plan_counts(
                n_iter,
                batch_size,
                dynamic_prompts=self.dynamic_prompts_var.get(),
            )
        except ValueError:
            total_images: int | str = "-"
            request_count: int | str = "-"
        sanitize = self.sanitize_subdir_var.get()
        for job in jobs:
            subdir = job.subdirectory if not sanitize else self._sanitize_preview(job.subdirectory)
            upscaler = str(job.settings_override.get("hr_upscaler", ""))
            self.job_tree.insert(
                "",
                "end",
                values=(
                    job.index,
                    job.title,
                    job.style_key,
                    upscaler or "（共通）",
                    total_images,
                    request_count,
                    subdir,
                ),
            )

    def _on_plan_setting_changed(self, *_args: Any) -> None:
        self._update_job_tree(self.jobs)
        if self.settings_loaded and not (self.worker and self.worker.is_alive()):
            self.status_var.set("設定変更済み：Dry Runを再実行")

    def _populate_form(self, payload: dict[str, Any]) -> None:
        self.n_iter_var.set(str(payload.get("n_iter", 1)))
        self.batch_size_var.set(str(payload.get("batch_size", 1)))
        self.steps_var.set(self._string_value(payload.get("steps")))
        self.cfg_scale_var.set(self._string_value(payload.get("cfg_scale")))
        self.width_var.set(self._string_value(payload.get("width")))
        self.height_var.set(self._string_value(payload.get("height")))
        self.seed_var.set(self._string_value(payload.get("seed")))
        self.sampler_name_var.set(self._string_value(payload.get("sampler_name")))
        self.scheduler_var.set(self._string_value(payload.get("scheduler")))
        self.save_images_var.set(bool(payload.get("save_images", True)))
        self.send_images_var.set(bool(payload.get("send_images", False)))

        self.negative_prompt_text.delete("1.0", "end")
        self.negative_prompt_text.insert("1.0", str(payload.get("negative_prompt", "")))

        self.enable_hr_var.set(bool(payload.get("enable_hr", False)))
        self.hr_upscaler_var.set(self._string_value(payload.get("hr_upscaler")))
        self.hr_scale_var.set(self._string_value(payload.get("hr_scale")))
        self.hr_second_pass_steps_var.set(self._string_value(payload.get("hr_second_pass_steps")))
        self.denoising_strength_var.set(self._string_value(payload.get("denoising_strength")))
        self.hr_cfg_scale_var.set(self._string_value(payload.get("hr_cfg_scale")))
        self.hr_rescale_cfg_var.set(self._string_value(payload.get("hr_rescale_cfg")))
        self.hr_resize_x_var.set(self._string_value(payload.get("hr_resize_x")))
        self.hr_resize_y_var.set(self._string_value(payload.get("hr_resize_y")))

        override_settings = payload.get("override_settings") or {}
        self.checkpoint_var.set(self._string_value(override_settings.get("sd_model_checkpoint")))
        self.vae_var.set(self._string_value(override_settings.get("sd_vae")))
        self.clip_skip_var.set(self._string_value(override_settings.get("CLIP_stop_at_last_layers")))

    def _collect_base_payload(self) -> dict[str, Any]:
        payload = dict(self.base_payload)

        self._set_int(payload, "n_iter", self.n_iter_var.get(), required=True, default=1)
        self._set_int(payload, "batch_size", self.batch_size_var.get(), required=True, default=1)
        self._set_optional_int(payload, "steps", self.steps_var.get())
        self._set_optional_float(payload, "cfg_scale", self.cfg_scale_var.get())
        self._set_optional_int(payload, "width", self.width_var.get())
        self._set_optional_int(payload, "height", self.height_var.get())
        self._set_optional_int(payload, "seed", self.seed_var.get())
        self._set_optional_string(payload, "sampler_name", self.sampler_name_var.get())
        self._set_optional_string(payload, "scheduler", self.scheduler_var.get())

        payload["save_images"] = self.save_images_var.get()
        payload["send_images"] = self.send_images_var.get()
        payload["negative_prompt"] = self.negative_prompt_text.get("1.0", "end").strip()

        payload["enable_hr"] = self.enable_hr_var.get()
        self._set_optional_string(payload, "hr_upscaler", self.hr_upscaler_var.get())
        self._set_optional_float(payload, "hr_scale", self.hr_scale_var.get())
        self._set_optional_int(payload, "hr_second_pass_steps", self.hr_second_pass_steps_var.get())
        self._set_optional_float(payload, "denoising_strength", self.denoising_strength_var.get())
        self._set_optional_float(payload, "hr_cfg_scale", self.hr_cfg_scale_var.get())
        self._set_optional_float(payload, "hr_rescale_cfg", self.hr_rescale_cfg_var.get())
        self._set_optional_int(payload, "hr_resize_x", self.hr_resize_x_var.get())
        self._set_optional_int(payload, "hr_resize_y", self.hr_resize_y_var.get())

        override_settings = dict(payload.get("override_settings") or {})
        self._set_optional_string(override_settings, "sd_model_checkpoint", self.checkpoint_var.get())
        self._set_optional_string(override_settings, "sd_vae", self.vae_var.get())
        self._set_optional_int(override_settings, "CLIP_stop_at_last_layers", self.clip_skip_var.get())
        if override_settings:
            payload["override_settings"] = override_settings
        else:
            payload.pop("override_settings", None)

        return payload

    def _build_cli_args(self) -> SimpleNamespace:
        return SimpleNamespace(
            batch_count=None,
            batch_size=None,
            no_save_images=not self.save_images_var.get(),
            send_images=self.send_images_var.get(),
            negative_prompt=None,
            sampler_name=None,
            scheduler=None,
            steps=None,
            cfg_scale=None,
            width=None,
            height=None,
            seed=None,
            no_sanitize_subdir=not self.sanitize_subdir_var.get(),
        )

    def _selected_jobs(self) -> list[PromptJob]:
        self.refresh_jobs(show_errors=True)
        jobs = list(self.jobs)
        limit = self._safe_int(self.limit_var.get(), default=0)
        if limit > 0:
            jobs = jobs[:limit]
        if not jobs:
            raise ValueError("実行対象のジョブがありません。")
        return jobs

    def preview_payloads(self) -> None:
        self._start_worker(dry_run=True)

    def start_generation(self) -> None:
        self._start_worker(dry_run=False)

    def _start_worker(self, dry_run: bool) -> None:
        if self.control_in_flight.is_set():
            messagebox.showinfo(
                "WebUI制御中",
                "Interrupt / Skip の送信完了を待ってから再実行してください。",
            )
            return

        if self.worker and self.worker.is_alive():
            messagebox.showinfo("実行中", "すでに実行中です。")
            return

        try:
            jobs = self._selected_jobs()
            base_payload = self._collect_base_payload()
            args = self._build_cli_args()
            dynamic_prompts = self.dynamic_prompts_var.get()
            wildcard_directories: tuple[Path, ...] = ()
            if dynamic_prompts:
                wildcard_directories = tuple(
                    Path(value)
                    for value in self.wildcards_dir_var.get().split(os.pathsep)
                    if value.strip()
                )
            manifest_directory = self.manifest_dir_var.get().strip()
            if dynamic_prompts and not manifest_directory:
                raise DynamicPromptError("Manifest directory is required.")
            timeout = self._parse_timeout()
            client_options = {
                "base_url": self.url_var.get().strip(),
                "timeout": timeout,
                "username": self.username_var.get().strip() or None,
                "password": self.password_var.get() or None,
            }
            stop_on_error = self.stop_on_error_var.get()
            manifest_metadata = {
                "webui_url": self.url_var.get().strip(),
                "prompt_file": str(Path(self.prompt_path_var.get()).resolve()),
                "payload_file": str(Path(self.payload_path_var.get()).resolve()),
                "wildcard_directories": [str(path) for path in wildcard_directories],
                "base_payload": base_payload,
            }
        except (DynamicPromptError, ValueError, PromptParseError) as error:
            messagebox.showerror("設定エラー", str(error))
            return

        self.stop_after_current.clear()
        self.interrupt_requested.clear()
        self.skip_requested.clear()
        self.progress_poll_warning_sent.clear()
        self.active_run_id += 1
        run_id = self.active_run_id
        self.progress.configure(maximum=max(len(jobs), 1), value=0)
        self.status_var.set(f"準備中: 0/{len(jobs)}ジョブ")
        self.webui_controls_enabled = False
        self.run_preparing = True
        self._set_running(True)
        self._clear_log()
        mode = "dry-run" if dry_run else "generation"
        self._append_log(f"Preparing {mode}: {len(jobs)} job(s)")

        self.worker = threading.Thread(
            target=self._prepare_and_run_jobs,
            args=(
                run_id,
                jobs,
                base_payload,
                args,
                dynamic_prompts,
                wildcard_directories,
                manifest_directory,
                manifest_metadata,
                client_options,
                stop_on_error,
                dry_run,
            ),
            daemon=True,
        )
        self.worker.start()

    def _prepare_and_run_jobs(
        self,
        run_id: int,
        jobs: list[PromptJob],
        base_payload: dict[str, Any],
        args: SimpleNamespace,
        dynamic_prompts: bool,
        wildcard_directories: tuple[Path, ...],
        manifest_directory: str,
        manifest_metadata: dict[str, Any],
        client_options: dict[str, Any],
        stop_on_error: bool,
        dry_run: bool,
    ) -> None:
        try:
            expander = (
                DynamicPromptExpander(wildcard_directories)
                if dynamic_prompts
                else None
            )
            dynamic_records: list[dict[str, Any]] = []
            job_plans: list[tuple[PromptJob, tuple[BatchChunk, ...]]] = []
            total_images = 0
            total_requests = 0

            for number, job in enumerate(jobs, start=1):
                if self.stop_after_current.is_set():
                    self.events.put(("prepare_cancelled", {"run_id": run_id}))
                    return

                payload = build_payload(job, args, base_payload)
                if expander is None:
                    chunks = split_payload_into_chunks(
                        payload,
                        max_images_per_request=DEFAULT_MAX_IMAGES_PER_REQUEST,
                    )
                else:
                    chunks, records = plan_dynamic_prompt_chunks(
                        payload,
                        expander,
                        job_index=job.index,
                        job_title=job.title,
                    )
                    dynamic_records.extend(records)
                job_plans.append((job, chunks))
                total_images += sum(chunk.image_count for chunk in chunks)
                total_requests += len(chunks)
                self.events.put(
                    (
                        "planning_progress",
                        {
                            "run_id": run_id,
                            "job_number": number,
                            "job_total": len(jobs),
                            "total_images": total_images,
                            "total_requests": total_requests,
                        },
                    )
                )

            if self.stop_after_current.is_set():
                self.events.put(("prepare_cancelled", {"run_id": run_id}))
                return

            manifest_path = None
            if expander is not None:
                manifest_path = write_dynamic_manifest(
                    manifest_directory,
                    dynamic_records,
                    metadata=manifest_metadata,
                )

            if self.stop_after_current.is_set():
                self.events.put(("prepare_cancelled", {"run_id": run_id}))
                return
        except Exception as error:
            self.events.put(
                (
                    "prepare_failed",
                    {"run_id": run_id, "error": str(error)},
                )
            )
            return

        self.events.put(
            (
                "plan_ready",
                {
                    "run_id": run_id,
                    "dry_run": dry_run,
                    "job_count": len(jobs),
                    "total_images": total_images,
                    "total_requests": total_requests,
                    "request_image_limit": (
                        1 if expander is not None else DEFAULT_MAX_IMAGES_PER_REQUEST
                    ),
                    "manifest_path": str(manifest_path) if manifest_path else "",
                },
            )
        )
        self._run_jobs(
            run_id,
            job_plans,
            client_options,
            stop_on_error,
            dry_run,
        )

    def _run_jobs(
        self,
        run_id: int,
        job_plans: list[tuple[PromptJob, tuple[BatchChunk, ...]]],
        client_options: dict[str, Any],
        stop_on_error: bool,
        dry_run: bool,
    ) -> None:
        client = SdWebuiClient(**client_options)
        progress_client = SdWebuiClient(**{**client_options, "timeout": 5})
        failures = 0
        skipped_requests = 0
        confirmed_images = 0
        total_images = sum(chunk.image_count for _, chunks in job_plans for chunk in chunks)
        outcome = "completed"
        abort_run = False
        partial_images_possible = False

        try:
            for number, (job, chunks) in enumerate(job_plans, start=1):
                subdir = chunks[0].payload["override_settings"]["directories_filename_pattern"]

                if dry_run:
                    self.events.put(
                        (
                            "log",
                            "\n".join(
                                [
                                    f"\nJob {number}/{len(job_plans)}: {job.title}",
                                    f"subdirectory: {subdir}",
                                    (
                                        f"{chunks[0].total_images} image(s) in "
                                        f"{len(chunks)} request(s)"
                                    ),
                                    *format_dry_run_request_lines(chunks),
                                    "first request payload:",
                                    json.dumps(
                                        chunks[0].payload,
                                        ensure_ascii=False,
                                        indent=2,
                                    ),
                                ]
                            ),
                        )
                    )
                    confirmed_images += chunks[0].total_images
                    self._put_run_progress(
                        run_id,
                        number,
                        len(job_plans),
                        chunks[-1],
                        confirmed_images,
                        total_images,
                        phase="dry_run",
                    )
                    continue

                self.events.put(("log", f"\nJob {number}/{len(job_plans)}: {job.title}"))
                self.events.put(("log", f"subdirectory: {subdir}"))
                self.events.put(
                    (
                        "log",
                        f"{chunks[0].total_images} image(s) in {len(chunks)} request(s)",
                    )
                )

                for chunk in chunks:
                    if self.stop_after_current.is_set():
                        outcome = "stopped"
                        abort_run = True
                        break

                    self.events.put(
                        (
                            "log",
                            f"request {chunk.ordinal}/{chunk.total_chunks}: sending images "
                            f"{chunk.image_start}-{chunk.image_end}/{chunk.total_images}",
                        )
                    )
                    self._put_run_progress(
                        run_id,
                        number,
                        len(job_plans),
                        chunk,
                        confirmed_images,
                        total_images,
                        phase="started",
                    )

                    poll_stop = threading.Event()
                    progress_context = {
                        "run_id": run_id,
                        "job_number": number,
                        "job_total": len(job_plans),
                        "chunk_number": chunk.ordinal,
                        "chunk_total": chunk.total_chunks,
                        "chunk_image_count": chunk.image_count,
                        "confirmed_images": confirmed_images,
                        "total_images": total_images,
                    }
                    poller = threading.Thread(
                        target=self._poll_progress,
                        args=(progress_client, poll_stop, progress_context),
                        daemon=True,
                    )
                    poller.start()
                    request_failed = False

                    try:
                        client.txt2img(chunk.payload)
                    except SdWebuiTransportError as error:
                        failures += 1
                        abort_run = True
                        if self.interrupt_requested.is_set():
                            outcome = "stopped"
                            partial_images_possible = True
                            self.events.put(
                                (
                                    "log",
                                    "Interrupted. The current request may contain partially "
                                    "saved images.",
                                )
                            )
                        else:
                            outcome = "failed"
                            self.events.put(("log", f"connection state unknown: {error}"))
                            self.events.put(
                                (
                                    "log",
                                    "Stopping all jobs because WebUI may still be processing "
                                    "this request. It will not be retried automatically.",
                                )
                            )
                    except SdWebuiApiError as error:
                        failures += 1
                        request_failed = True
                        self.events.put(("log", f"request failed: {error}"))
                        self.events.put(
                            (
                                "log",
                                "Skipping the remaining requests for this job; the failed "
                                "request will not be retried automatically.",
                            )
                        )
                        if self.interrupt_requested.is_set():
                            outcome = "stopped"
                            partial_images_possible = True
                            abort_run = True
                        elif stop_on_error:
                            outcome = "failed"
                            abort_run = True
                    except Exception as error:
                        failures += 1
                        outcome = "failed"
                        abort_run = True
                        self.events.put(("log", f"unexpected error: {error}"))
                    else:
                        poll_stop.set()
                        poller.join(timeout=6)
                        if self.interrupt_requested.is_set():
                            outcome = "stopped"
                            partial_images_possible = True
                            abort_run = True
                            self.events.put(
                                (
                                    "log",
                                    "Interrupted. The current request may contain partially "
                                    "saved images and is not counted as confirmed.",
                                )
                            )
                        elif self.skip_requested.is_set():
                            skipped_requests += 1
                            self.events.put(
                                (
                                    "log",
                                    f"request {chunk.ordinal}/{chunk.total_chunks} was "
                                    "skipped; WebUI may have saved some images, but the "
                                    "exact count is unknown. This request is not added to "
                                    "the confirmed total.",
                                )
                            )
                            self._put_run_progress(
                                run_id,
                                number,
                                len(job_plans),
                                chunk,
                                confirmed_images,
                                total_images,
                                phase="skipped",
                            )
                        else:
                            confirmed_images += chunk.image_count
                            percent = chunk.image_end / chunk.total_images * 100
                            self.events.put(
                                (
                                    "log",
                                    f"completed through image {chunk.image_end}/"
                                    f"{chunk.total_images} ({percent:.1f}%)",
                                )
                            )
                            self._put_run_progress(
                                run_id,
                                number,
                                len(job_plans),
                                chunk,
                                confirmed_images,
                                total_images,
                                phase="completed",
                            )
                    finally:
                        poll_stop.set()
                        poller.join(timeout=6)

                    if not self.control_finished.is_set():
                        self.events.put(
                            (
                                "log",
                                "waiting for WebUI Interrupt / Skip control request to finish",
                            )
                        )
                        self.control_finished.wait()
                    self.skip_requested.clear()

                    if abort_run:
                        break

                    if request_failed:
                        # Preserve the old behavior: a failed API request skips the
                        # rest of this logical prompt and proceeds to the next one.
                        break

                    if self.stop_after_current.is_set():
                        outcome = "stopped"
                        abort_run = True
                        self.events.put(("log", "stop requested; stopping after current request"))
                        break

                if abort_run:
                    break
        except Exception as error:
            failures += 1
            outcome = "failed"
            self.events.put(("log", f"worker failed unexpectedly: {error}"))
        finally:
            self.skip_requested.clear()
            if outcome == "completed":
                if failures and skipped_requests:
                    outcome = "completed_with_errors_and_skips"
                elif failures:
                    outcome = "completed_with_errors"
                elif skipped_requests:
                    outcome = "completed_with_skips"
            self.events.put(
                (
                    "done",
                    {
                        "run_id": run_id,
                        "outcome": outcome,
                        "failures": failures,
                        "skipped_requests": skipped_requests,
                        "confirmed_images": confirmed_images,
                        "total_images": total_images,
                        "dry_run": dry_run,
                        "partial_images_possible": partial_images_possible,
                    },
                )
            )

    def _put_run_progress(
        self,
        run_id: int,
        job_number: int,
        job_total: int,
        chunk: BatchChunk,
        confirmed_images: int,
        total_images: int,
        *,
        phase: str,
    ) -> None:
        self.events.put(
            (
                "run_progress",
                {
                    "run_id": run_id,
                    "phase": phase,
                    "job_number": job_number,
                    "job_total": job_total,
                    "chunk_number": chunk.ordinal,
                    "chunk_total": chunk.total_chunks,
                    "chunk_image_count": chunk.image_count,
                    "confirmed_images": confirmed_images,
                    "total_images": total_images,
                    "webui_progress": None,
                    "eta_relative": None,
                },
            )
        )

    def _poll_progress(
        self,
        client: SdWebuiClient,
        stop_event: threading.Event,
        context: dict[str, Any],
    ) -> None:
        maximum_progress = 0.0

        while not stop_event.wait(PROGRESS_POLL_INTERVAL_SECONDS):
            try:
                progress_data = client.get_progress(skip_current_image=True)
            except Exception as error:
                if stop_event.is_set():
                    break
                if not self.progress_poll_warning_sent.is_set():
                    self.progress_poll_warning_sent.set()
                    self.events.put(
                        (
                            "log",
                            f"progress polling unavailable; generation continues: {error}",
                        )
                    )
                continue

            if stop_event.is_set():
                break
            progress = normalize_webui_progress(progress_data.get("progress"))
            if progress is None:
                continue
            maximum_progress = max(maximum_progress, progress)
            value = dict(context)
            value.update(
                {
                    "phase": "polling",
                    "webui_progress": maximum_progress,
                    "eta_relative": normalize_eta(progress_data.get("eta_relative")),
                }
            )
            self.events.put(("run_progress", value))

    def request_stop(self) -> None:
        self.stop_after_current.set()
        if getattr(self, "run_preparing", False):
            self._append_log("準備の停止を要求しました。")
        else:
            self._append_log("Stop requested. 現在の送信完了後に停止します。")

    def interrupt_webui(self) -> None:
        if not self._control_is_available():
            return
        self.interrupt_requested.set()
        self.stop_after_current.set()
        self._append_log("Interrupt requested. 後続の送信は開始しません。")
        self._post_control("interrupt")

    def skip_webui(self) -> None:
        if not self._control_is_available():
            return
        self.skip_requested.set()
        self._append_log("Skip requested. この送信の保存枚数は確定数に含めません。")
        self._post_control("skip")

    def _post_control(self, action: str) -> None:
        if not self._control_is_available():
            return

        try:
            client_options = {
                "base_url": self.url_var.get().strip(),
                "timeout": 10,
                "username": self.username_var.get().strip() or None,
                "password": self.password_var.get() or None,
            }
        except ValueError as error:
            messagebox.showerror("設定エラー", str(error))
            return

        self.control_in_flight.set()
        self.control_finished.clear()
        self._refresh_action_states()

        def worker() -> None:
            try:
                client = SdWebuiClient(**client_options)
                if action == "interrupt":
                    client.interrupt()
                else:
                    client.skip()
            except Exception as error:
                self.events.put(("log", f"{action} failed: {error}"))
            else:
                self.events.put(("log", f"{action} sent"))
            finally:
                self.control_finished.set()
                self.events.put(("control_done", {"action": action}))

        threading.Thread(target=worker, daemon=True).start()

    def _control_is_available(self) -> bool:
        return (
            self.generation_running
            and self.webui_controls_enabled
            and not self.control_in_flight.is_set()
        )

    def _drain_events(self) -> None:
        processed = 0
        try:
            while processed < EVENT_DRAIN_BATCH_SIZE:
                event, value = self.events.get_nowait()
                processed += 1
                if event == "log":
                    self._append_log(str(value))
                elif event == "control_done":
                    self.control_in_flight.clear()
                    self._refresh_action_states()
                elif event == "planning_progress":
                    progress_data = dict(value)
                    if progress_data.get("run_id") != self.active_run_id:
                        continue
                    number = int(progress_data["job_number"])
                    total = int(progress_data["job_total"])
                    self.progress.configure(maximum=max(total, 1), value=number)
                    self.status_var.set(
                        f"準備中: {number}/{total}ジョブ｜"
                        f"{int(progress_data['total_images'])}枚｜"
                        f"{int(progress_data['total_requests'])}送信"
                    )
                elif event == "plan_ready":
                    plan_data = dict(value)
                    if plan_data.get("run_id") != self.active_run_id:
                        continue
                    self.run_preparing = False
                    self.webui_controls_enabled = not bool(plan_data["dry_run"])
                    self._refresh_action_states()
                    total_images = int(plan_data["total_images"])
                    total_requests = int(plan_data["total_requests"])
                    self.progress.configure(maximum=max(total_images, 1), value=0)
                    mode = "dry-run" if plan_data["dry_run"] else "generation"
                    self.status_var.set(
                        f"準備完了: {total_images}枚 / {total_requests}送信"
                    )
                    self._append_log(
                        f"Starting {mode}: {int(plan_data['job_count'])} job(s), "
                        f"{total_images} image(s), {total_requests} request(s)"
                    )
                    self._append_log(
                        f"Each request is limited to "
                        f"{int(plan_data['request_image_limit'])} image(s); "
                        "grid creation is disabled."
                    )
                    if plan_data.get("manifest_path"):
                        self._append_log(
                            f"Dynamic prompt manifest: {plan_data['manifest_path']}"
                        )
                elif event == "prepare_failed":
                    failed_data = dict(value)
                    if failed_data.get("run_id") != self.active_run_id:
                        continue
                    self.run_preparing = False
                    self._set_running(False)
                    self.progress.configure(value=0)
                    self.status_var.set("準備失敗")
                    error = str(
                        failed_data.get("error", "Unknown preparation error")
                    )
                    self._append_log(f"Preparation failed: {error}")
                    messagebox.showerror("設定エラー", error)
                elif event == "prepare_cancelled":
                    cancelled_data = dict(value)
                    if cancelled_data.get("run_id") != self.active_run_id:
                        continue
                    self.run_preparing = False
                    self._set_running(False)
                    self.progress.configure(value=0)
                    self.status_var.set("準備を停止しました")
                    self._append_log(
                        "Preparation stopped before any API request was sent."
                    )
                elif event == "run_progress":
                    progress_data = dict(value)
                    if progress_data.get("run_id") != self.active_run_id:
                        continue
                    confirmed = int(progress_data["confirmed_images"])
                    current = normalize_webui_progress(progress_data.get("webui_progress"))
                    estimated = confirmed
                    if current is not None:
                        estimated += current * int(progress_data["chunk_image_count"])
                    self.progress.configure(value=min(estimated, int(progress_data["total_images"])))
                    self.status_var.set(format_progress_status(progress_data))
                elif event == "done":
                    done_data = dict(value)
                    if done_data.get("run_id") != self.active_run_id:
                        continue
                    failures = int(done_data["failures"])
                    outcome = str(done_data["outcome"])
                    skipped_requests = int(done_data.get("skipped_requests", 0))
                    confirmed = int(done_data["confirmed_images"])
                    total = int(done_data["total_images"])
                    self.run_preparing = False
                    self._set_running(False)
                    if done_data.get("dry_run"):
                        self.progress.configure(value=total)
                        self.status_var.set(f"Dry Run完了: {total}枚 / 送信なし")
                        self._append_log("\nDry Run completed. No API requests were sent.")
                    elif outcome == "completed":
                        self.progress.configure(value=total)
                        self.status_var.set(f"完了: {confirmed}/{total}枚")
                        self._append_log("\nAll jobs completed.")
                    elif outcome == "completed_with_errors":
                        self.progress.configure(value=confirmed)
                        self.status_var.set(f"エラーあり: 確定 {confirmed}/{total}枚")
                        self._append_log(f"\nCompleted with {failures} failure(s).")
                    elif outcome == "completed_with_skips":
                        self.progress.configure(value=confirmed)
                        self.status_var.set(f"スキップあり: 確定 {confirmed}/{total}枚")
                        self._append_log(
                            f"\nCompleted with {skipped_requests} skipped request(s). "
                            "Skipped requests may contain saved images whose exact count "
                            "is unknown."
                        )
                    elif outcome == "completed_with_errors_and_skips":
                        self.progress.configure(value=confirmed)
                        self.status_var.set(f"エラー・スキップあり: 確定 {confirmed}/{total}枚")
                        self._append_log(
                            f"\nCompleted with {failures} failure(s) and "
                            f"{skipped_requests} skipped request(s). Skipped requests may "
                            "contain saved images whose exact count is unknown."
                        )
                    elif outcome == "stopped":
                        self.progress.configure(value=confirmed)
                        self.status_var.set(f"停止: 確定 {confirmed}/{total}枚")
                        if done_data.get("partial_images_possible"):
                            self._append_log(
                                "\nStopped. The interrupted request may contain partially saved images."
                            )
                        else:
                            self._append_log("\nStopped after the current request completed.")
                    else:
                        self.progress.configure(value=confirmed)
                        self.status_var.set(f"異常停止: 確定 {confirmed}/{total}枚")
                        self._append_log(f"\nStopped after {failures} failure(s).")
        except queue.Empty:
            pass

        self.root.after(10 if not self.events.empty() else 100, self._drain_events)

    def _set_running(self, running: bool) -> None:
        self.generation_running = running
        if not running:
            self.webui_controls_enabled = False
            self.run_preparing = False
        self._refresh_action_states()
        if not running:
            self.stop_after_current.clear()
            self.interrupt_requested.clear()
            self.skip_requested.clear()

    def _refresh_action_states(self) -> None:
        control_pending = self.control_in_flight.is_set()
        run_actions_enabled = self.generation_running and not control_pending
        webui_control_enabled = run_actions_enabled and self.webui_controls_enabled
        start_enabled = not self.generation_running and not control_pending

        self.start_button.configure(state="normal" if start_enabled else "disabled")
        self.preview_button.configure(state="normal" if start_enabled else "disabled")
        self.stop_button.configure(state="normal" if run_actions_enabled else "disabled")
        self.interrupt_button.configure(
            state="normal" if webui_control_enabled else "disabled"
        )
        self.skip_button.configure(state="normal" if webui_control_enabled else "disabled")

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _parse_timeout(self) -> float | None:
        value = self.timeout_var.get().strip()
        if not value:
            return 86400
        parsed = float(value)
        if parsed < 0:
            raise ValueError("Timeout must be 0 (no timeout) or a positive number.")
        return None if parsed == 0 else parsed

    def _set_int(self, payload: dict[str, Any], key: str, value: str, *, required: bool, default: int) -> None:
        text = value.strip()
        if not text:
            if required:
                payload[key] = default
            else:
                payload.pop(key, None)
            return
        payload[key] = int(text)

    def _set_optional_int(self, payload: dict[str, Any], key: str, value: str) -> None:
        self._set_int(payload, key, value, required=False, default=0)

    def _set_optional_float(self, payload: dict[str, Any], key: str, value: str) -> None:
        text = value.strip()
        if not text:
            payload.pop(key, None)
            return
        payload[key] = float(text)

    def _set_optional_string(self, payload: dict[str, Any], key: str, value: str) -> None:
        text = value.strip()
        if text:
            payload[key] = text
        else:
            payload.pop(key, None)

    def _safe_int(self, value: str, default: int) -> int:
        try:
            return int(value.strip())
        except ValueError:
            return default

    def _string_value(self, value: Any) -> str:
        return "" if value is None else str(value)

    def _with_n_iter_comment(self, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        inserted = False
        for key, value in payload.items():
            result[key] = value
            if key == "n_iter":
                result["_comment_n_iter"] = "生成枚数（Stable Diffusion WebUIのBatch Countに対応）"
                inserted = True
        if not inserted:
            result["_comment_n_iter"] = "生成枚数（Stable Diffusion WebUIのBatch Countに対応）"
        return result

    def _sanitize_preview(self, value: str) -> str:
        from .cli import sanitize_subdirectory

        return sanitize_subdirectory(value)


def calculate_job_plan_counts(
    n_iter: int,
    batch_size: int,
    *,
    dynamic_prompts: bool,
) -> tuple[int, int]:
    """Return per-job image and API request counts for the GUI preview."""

    if isinstance(n_iter, bool) or not isinstance(n_iter, int) or n_iter <= 0:
        raise ValueError("n_iter must be a positive integer")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    total_images = n_iter * batch_size
    if dynamic_prompts:
        # Runner-side expansion converts every output into its own B=1 request.
        return total_images, total_images

    chunks = split_payload_into_chunks(
        {"n_iter": n_iter, "batch_size": batch_size},
        max_images_per_request=DEFAULT_MAX_IMAGES_PER_REQUEST,
        resolve_random_seeds=False,
    )
    return total_images, len(chunks)


def normalize_webui_progress(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return min(max(parsed, 0.0), 1.0)


def normalize_eta(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def format_eta(seconds: Any) -> str | None:
    parsed = normalize_eta(seconds)
    if parsed is None:
        return None
    total_seconds = int(round(parsed))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"


def format_progress_status(data: dict[str, Any]) -> str:
    base = (
        f"ジョブ {data['job_number']}/{data['job_total']}｜"
        f"送信 {data['chunk_number']}/{data['chunk_total']}｜"
        f"確定 {data['confirmed_images']}/{data['total_images']}枚"
    )
    if data.get("phase") == "dry_run":
        return "Dry Run｜" + base
    if data.get("phase") == "skipped":
        return base + "｜スキップ（保存枚数不明）"

    progress = normalize_webui_progress(data.get("webui_progress"))
    if progress is None:
        return base + ("｜送信完了" if data.get("phase") == "completed" else "｜開始中")

    status = f"{base}｜WebUI {progress * 100:.1f}%"
    eta = format_eta(data.get("eta_relative"))
    if eta is not None:
        status += f"｜現送信ETA {eta}"
    return status


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _event: tk.Event) -> None:
        if self.window is not None:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(self.window, text=self.text, padding=6, relief="solid", borderwidth=1)
        label.pack()

    def hide(self, _event: tk.Event) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    BatchRunnerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
