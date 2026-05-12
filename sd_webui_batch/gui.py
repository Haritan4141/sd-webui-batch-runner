from __future__ import annotations

import json
from pathlib import Path
import queue
from types import SimpleNamespace
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any

from .cli import build_payload, strip_comment_fields
from .client import SdWebuiApiError, SdWebuiClient
from .parser import PromptJob, PromptParseError, parse_prompt_note, read_text_file


PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()

        self.prompt_path_var = tk.StringVar(value=str(PROJECT_ROOT / "examples" / "prompts.txt"))
        self.payload_path_var = tk.StringVar(value=str(PROJECT_ROOT / "examples" / "payload.json"))
        self.url_var = tk.StringVar(value="http://127.0.0.1:7860")
        self.timeout_var = tk.StringVar(value="86400")
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()

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
        self.n_iter_var.trace_add("write", lambda *_: self._update_job_tree(self.jobs))
        self.sanitize_subdir_var.trace_add("write", lambda *_: self._update_job_tree(self.jobs))
        self._load_payload_if_present()
        self.refresh_jobs(show_errors=False)
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
        ttk.Label(frame, textvariable=self.job_count_var).grid(row=2, column=4, columnspan=2, sticky="w", padx=(14, 4))

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
        self.stop_button = ttk.Button(frame, text="現在のジョブ後に停止", command=self.request_stop, state="disabled")
        self.stop_button.grid(row=0, column=2, padx=6)
        self.interrupt_button = ttk.Button(frame, text="WebUI Interrupt", command=self.interrupt_webui)
        self.interrupt_button.grid(row=0, column=3, padx=6)
        self.skip_button = ttk.Button(frame, text="WebUI Skip", command=self.skip_webui)
        self.skip_button.grid(row=0, column=4, padx=6)

        ttk.Label(frame, textvariable=self.status_var).grid(row=0, column=6, sticky="e")
        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=1)
        self.progress.grid(row=1, column=0, columnspan=7, sticky="ew", pady=(8, 0))

    def _build_jobs_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="ジョブ")
        frame.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(0, weight=1)

        columns = ("index", "title", "images", "subdir")
        self.job_tree = ttk.Treeview(frame, columns=columns, show="headings", height=7)
        self.job_tree.heading("index", text="#")
        self.job_tree.heading("title", text="タイトル")
        self.job_tree.heading("images", text="生成枚数")
        self.job_tree.heading("subdir", text="Subdirectory override")
        self.job_tree.column("index", width=50, anchor="center", stretch=False)
        self.job_tree.column("title", width=320)
        self.job_tree.column("images", width=90, anchor="center", stretch=False)
        self.job_tree.column("subdir", width=420)
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
            self.prompt_path_var.set(path)
            self.refresh_jobs()

    def browse_payload(self) -> None:
        path = filedialog.askopenfilename(
            title="Payload JSONを選択",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.payload_path_var.set(path)
            self.load_payload()

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
        path = Path(self.prompt_path_var.get())
        try:
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
        self.job_count_var.set(f"{len(jobs)} job(s) loaded")

    def _update_job_tree(self, jobs: list[PromptJob]) -> None:
        for item in self.job_tree.get_children():
            self.job_tree.delete(item)

        n_iter = self._safe_int(self.n_iter_var.get(), default=1)
        sanitize = self.sanitize_subdir_var.get()
        for job in jobs:
            subdir = job.subdirectory if not sanitize else self._sanitize_preview(job.subdirectory)
            self.job_tree.insert("", "end", values=(job.index, job.title, n_iter, subdir))

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
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("実行中", "すでに実行中です。")
            return

        try:
            jobs = self._selected_jobs()
            base_payload = self._collect_base_payload()
            args = self._build_cli_args()
            timeout = self._parse_timeout()
            client_options = {
                "base_url": self.url_var.get().strip(),
                "timeout": timeout,
                "username": self.username_var.get().strip() or None,
                "password": self.password_var.get() or None,
            }
            stop_on_error = self.stop_on_error_var.get()
        except (ValueError, PromptParseError) as error:
            messagebox.showerror("設定エラー", str(error))
            return

        self.stop_after_current.clear()
        self.progress.configure(maximum=max(len(jobs), 1), value=0)
        self._set_running(True)
        self._clear_log()
        mode = "dry-run" if dry_run else "generation"
        self._append_log(f"Starting {mode}: {len(jobs)} job(s)")

        self.worker = threading.Thread(
            target=self._run_jobs,
            args=(jobs, base_payload, args, client_options, stop_on_error, dry_run),
            daemon=True,
        )
        self.worker.start()

    def _run_jobs(
        self,
        jobs: list[PromptJob],
        base_payload: dict[str, Any],
        args: SimpleNamespace,
        client_options: dict[str, Any],
        stop_on_error: bool,
        dry_run: bool,
    ) -> None:
        client = SdWebuiClient(**client_options)
        failures = 0

        try:
            for number, job in enumerate(jobs, start=1):
                payload = build_payload(job, args, base_payload)
                subdir = payload["override_settings"]["directories_filename_pattern"]
                self.events.put(("log", f"\n{number}/{len(jobs)}: {job.title}"))
                self.events.put(("log", f"subdirectory: {subdir}"))

                if dry_run:
                    self.events.put(("log", json.dumps(payload, ensure_ascii=False, indent=2)))
                else:
                    try:
                        client.txt2img(payload)
                    except SdWebuiApiError as error:
                        failures += 1
                        self.events.put(("log", f"failed: {error}"))
                        if stop_on_error:
                            break
                    else:
                        self.events.put(("log", "completed"))

                self.events.put(("progress", number))
                if self.stop_after_current.is_set():
                    self.events.put(("log", "stop requested; stopping after current job"))
                    break
        finally:
            self.events.put(("done", failures))

    def request_stop(self) -> None:
        self.stop_after_current.set()
        self._append_log("Stop requested. 現在のジョブ完了後に停止します。")

    def interrupt_webui(self) -> None:
        self._post_control("interrupt")

    def skip_webui(self) -> None:
        self._post_control("skip")

    def _post_control(self, action: str) -> None:
        try:
            client_options = {
                "base_url": self.url_var.get().strip(),
                "timeout": self._parse_timeout(),
                "username": self.username_var.get().strip() or None,
                "password": self.password_var.get() or None,
            }
        except ValueError as error:
            messagebox.showerror("設定エラー", str(error))
            return

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

        threading.Thread(target=worker, daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "log":
                    self._append_log(str(value))
                elif event == "progress":
                    self.progress.configure(value=value)
                    self.status_var.set(f"進行中: {value}/{int(self.progress['maximum'])}")
                elif event == "done":
                    failures = int(value)
                    self._set_running(False)
                    self.status_var.set("完了" if failures == 0 else f"完了: {failures} failure(s)")
                    self._append_log("\nAll jobs completed." if failures == 0 else f"\nCompleted with {failures} failure(s).")
        except queue.Empty:
            pass

        self.root.after(100, self._drain_events)

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.preview_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        if not running:
            self.stop_after_current.clear()

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
