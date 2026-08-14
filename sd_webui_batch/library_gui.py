from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable

from .prompt_library import (
    DEFAULT_DATABASE_PATH,
    LibraryJob,
    PromptLibrary,
    PromptLibraryError,
    RequestRecord,
    extract_request_candidates,
)


LoadCallback = Callable[[Path, tuple[int, ...]], None]
UPSCALER_CHOICES = ("", "Latent (antialiased)", "Lanczos")
PROMPT_STATUS_LABELS = {
    "draft": "下書き",
    "ready": "生成準備済み",
    "generated": "生成済み",
}
PROMPT_STATUS_VALUES = {label: status for status, label in PROMPT_STATUS_LABELS.items()}
REQUEST_STATUS_LABELS = {
    "received": "受付",
    "reviewed": "確認済み",
    "ready_for_prompt": "プロンプト生成待ち",
    "prompt_generated": "プロンプト生成済み",
    "done": "完了",
}
REQUEST_STATUS_VALUES = {
    label: status for status, label in REQUEST_STATUS_LABELS.items()
}
REQUEST_FILTER_CHOICES = (
    "未完了",
    "受付",
    "確認済み",
    "プロンプト生成待ち",
    "プロンプト生成済み",
    "完了",
    "すべて",
)


def request_status_matches_filter(status: str, filter_label: str) -> bool:
    if filter_label == "すべて":
        return True
    if filter_label == "未完了":
        return status != "done"
    return status == REQUEST_STATUS_VALUES.get(filter_label)


class PromptLibraryWindow:
    """Small SQLite prompt editor that feeds selected records to the runner GUI."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        database_path: str | Path = DEFAULT_DATABASE_PATH,
        on_load: LoadCallback,
    ) -> None:
        self.on_load = on_load
        self.window = tk.Toplevel(parent)
        self.window.title("SQLiteプロンプトライブラリ")
        self.window.geometry("1280x780")
        self.window.minsize(1050, 680)
        self.window.transient(parent)

        self.database_path_var = tk.StringVar(value=str(database_path))
        self.title_var = tk.StringVar()
        self.style_var = tk.StringVar()
        self.status_var = tk.StringVar(value=PROMPT_STATUS_LABELS["draft"])
        self.enabled_var = tk.BooleanVar(value=True)
        self.upscaler_override_var = tk.StringVar()
        self.rule_style_var = tk.StringVar()
        self.rule_upscaler_var = tk.StringVar()
        self.summary_var = tk.StringVar(value="未読み込み")
        self.request_source_var = tk.StringVar(value="manual")
        self.request_source_reference_var = tk.StringVar()
        self.request_received_at_var = tk.StringVar()
        self.request_characters_var = tk.StringVar()
        self.request_style_var = tk.StringVar()
        self.request_status_var = tk.StringVar(
            value=REQUEST_STATUS_LABELS["received"]
        )
        self.request_filter_var = tk.StringVar(value="未完了")
        self.request_summary_var = tk.StringVar(value="未読み込み")

        self.library = PromptLibrary(database_path)
        self.jobs: dict[int, LibraryJob] = {}
        self.current_job_id: int | None = None
        self.requests: dict[int, RequestRecord] = {}
        self.current_request_id: int | None = None

        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)

        source = ttk.LabelFrame(self.window, text="ライブラリ", padding=8)
        source.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text="SQLite DB").grid(row=0, column=0, sticky="w")
        ttk.Entry(source, textvariable=self.database_path_var).grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ttk.Button(source, text="DBを開く", command=self.choose_database).grid(
            row=0, column=2, padx=3
        )
        ttk.Button(source, text="新規DB", command=self.choose_new_database).grid(
            row=0, column=3, padx=3
        )
        ttk.Button(source, text="再読込", command=self.reload).grid(
            row=0, column=4, padx=3
        )
        ttk.Button(source, text="txt取込", command=self.import_text).grid(
            row=0, column=5, padx=3
        )
        ttk.Button(source, text="PromptSet JSON取込", command=self.import_prompt_set).grid(
            row=0, column=6, padx=3
        )
        ttk.Button(source, text="RequestSet JSON取込", command=self.import_request_set).grid(
            row=0, column=7, padx=3
        )

        self.notebook = ttk.Notebook(self.window)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=6)
        prompt_page = ttk.Frame(self.notebook)
        request_page = ttk.Frame(self.notebook)
        prompt_page.columnconfigure(0, weight=1)
        prompt_page.rowconfigure(0, weight=1)
        self.notebook.add(request_page, text="依頼Inbox")
        self.notebook.add(prompt_page, text="プロンプト")

        body = ttk.Panedwindow(prompt_page, orient="horizontal")
        body.grid(row=0, column=0, sticky="nsew")

        list_frame = ttk.LabelFrame(body, text="プロンプト一覧", padding=6)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        body.add(list_frame, weight=3)

        columns = (
            "id",
            "collection",
            "status",
            "enabled",
            "title",
            "style",
            "upscaler",
        )
        self.tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        headings = {
            "id": "ID",
            "collection": "取込単位",
            "status": "状態",
            "enabled": "有効",
            "title": "タイトル",
            "style": "絵柄",
            "upscaler": "適用Upscaler",
        }
        widths = {
            "id": 55,
            "collection": 130,
            "status": 100,
            "enabled": 50,
            "title": 310,
            "style": 90,
            "upscaler": 165,
        }
        for key in columns:
            self.tree.heading(key, text=headings[key])
            self.tree.column(
                key,
                width=widths[key],
                anchor="center" if key in {"id", "status", "enabled", "style"} else "w",
                stretch=key in {"title", "collection"},
            )
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        list_actions = ttk.Frame(list_frame)
        list_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(list_actions, text="新規", command=self.create_job).pack(side="left")
        ttk.Button(list_actions, text="全選択", command=self.select_all).pack(
            side="left", padx=(5, 0)
        )
        ttk.Button(
            list_actions,
            text="選択を生成準備済み",
            command=self.mark_selected_ready,
        ).pack(side="left", padx=5)
        ttk.Button(list_actions, text="選択削除", command=self.delete_selected).pack(
            side="left"
        )
        ttk.Label(list_actions, textvariable=self.summary_var).pack(side="right")

        editor = ttk.LabelFrame(body, text="選択項目を編集", padding=8)
        editor.columnconfigure(1, weight=1)
        editor.rowconfigure(4, weight=1)
        body.add(editor, weight=2)

        ttk.Label(editor, text="タイトル").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(editor, textvariable=self.title_var).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=3
        )

        ttk.Label(editor, text="絵柄").grid(row=1, column=0, sticky="w", pady=3)
        self.style_combo = ttk.Combobox(editor, textvariable=self.style_var)
        self.style_combo.grid(row=1, column=1, sticky="ew", padx=(6, 10), pady=3)
        ttk.Label(editor, text="状態").grid(row=1, column=2, sticky="w", pady=3)
        ttk.Combobox(
            editor,
            textvariable=self.status_var,
            values=tuple(PROMPT_STATUS_LABELS.values()),
            state="readonly",
            width=13,
        ).grid(row=1, column=3, sticky="ew", padx=(6, 0), pady=3)

        ttk.Label(editor, text="個別Upscaler").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(
            editor,
            textvariable=self.upscaler_override_var,
            values=UPSCALER_CHOICES,
        ).grid(row=2, column=1, sticky="ew", padx=(6, 10), pady=3)
        ttk.Checkbutton(editor, text="生成対象", variable=self.enabled_var).grid(
            row=2, column=2, columnspan=2, sticky="w", pady=3
        )
        ttk.Label(
            editor,
            text="空欄なら絵柄規則、それもなければ共通Payloadを継承します。",
            foreground="#555555",
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(0, 5))

        ttk.Label(editor, text="Prompt").grid(row=4, column=0, sticky="nw", pady=3)
        self.prompt_text = scrolledtext.ScrolledText(editor, wrap="word", height=18)
        self.prompt_text.grid(
            row=4, column=1, columnspan=3, sticky="nsew", padx=(6, 0), pady=3
        )

        ttk.Label(editor, text="メモ").grid(row=5, column=0, sticky="nw", pady=3)
        self.notes_text = tk.Text(editor, wrap="word", height=3)
        self.notes_text.grid(
            row=5, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=3
        )
        ttk.Button(editor, text="編集内容を保存", command=self.save_current).grid(
            row=6, column=1, columnspan=3, sticky="e", pady=(7, 0)
        )

        rule_frame = ttk.LabelFrame(prompt_page, text="絵柄別Upscaler規則", padding=8)
        rule_frame.grid(row=1, column=0, sticky="ew", pady=6)
        rule_frame.columnconfigure(1, weight=1)
        rule_frame.columnconfigure(3, weight=1)
        ttk.Label(rule_frame, text="絵柄").grid(row=0, column=0, sticky="w")
        self.rule_style_combo = ttk.Combobox(
            rule_frame, textvariable=self.rule_style_var
        )
        self.rule_style_combo.grid(row=0, column=1, sticky="ew", padx=6)
        self.rule_style_combo.bind("<<ComboboxSelected>>", self._on_rule_selected)
        ttk.Label(rule_frame, text="Upscaler").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            rule_frame,
            textvariable=self.rule_upscaler_var,
            values=UPSCALER_CHOICES[1:],
        ).grid(row=0, column=3, sticky="ew", padx=6)
        ttk.Button(rule_frame, text="規則を保存", command=self.save_style_rule).grid(
            row=0, column=4, padx=3
        )

        footer = ttk.Frame(prompt_page, padding=(0, 4, 0, 4))
        footer.grid(row=2, column=0, sticky="ew")
        ttk.Button(
            footer,
            text="生成準備済み全件をバッチへ",
            command=self.load_all_ready,
        ).pack(side="right")
        ttk.Button(
            footer,
            text="選択項目をバッチへ",
            command=self.load_selected,
        ).pack(side="right", padx=6)

        self._build_request_page(request_page)

    def _build_request_page(self, page: ttk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        body = ttk.Panedwindow(page, orient="horizontal")
        body.grid(row=0, column=0, sticky="nsew")

        list_frame = ttk.LabelFrame(body, text="受け取った依頼", padding=6)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        body.add(list_frame, weight=3)

        columns = ("id", "status", "received_at", "characters", "style", "preview")
        self.request_tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        headings = {
            "id": "ID",
            "status": "状態",
            "received_at": "受取日時",
            "characters": "キャラクター",
            "style": "絵柄",
            "preview": "依頼内容",
        }
        widths = {
            "id": 50,
            "status": 140,
            "received_at": 145,
            "characters": 170,
            "style": 90,
            "preview": 320,
        }
        for key in columns:
            self.request_tree.heading(key, text=headings[key])
            self.request_tree.column(
                key,
                width=widths[key],
                anchor="center" if key in {"id", "status", "style"} else "w",
                stretch=key in {"characters", "preview"},
            )
        scrollbar = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.request_tree.yview
        )
        self.request_tree.configure(yscrollcommand=scrollbar.set)
        self.request_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.request_tree.bind("<<TreeviewSelect>>", self._on_request_tree_select)

        actions = ttk.Frame(list_frame)
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(actions, text="新規", command=self.create_request).pack(side="left")
        ttk.Button(
            actions,
            text="クリップボードから新規",
            command=self.create_request_from_clipboard,
        ).pack(side="left", padx=5)
        ttk.Button(actions, text="全選択", command=self.select_all_requests).pack(
            side="left"
        )
        ttk.Button(actions, text="選択削除", command=self.delete_selected_requests).pack(
            side="left", padx=(5, 0)
        )
        ttk.Label(actions, textvariable=self.request_summary_var).pack(side="right")

        workflow_actions = ttk.Frame(list_frame)
        workflow_actions.grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0)
        )
        ttk.Label(workflow_actions, text="状態表示").pack(side="left")
        self.request_filter_combo = ttk.Combobox(
            workflow_actions,
            textvariable=self.request_filter_var,
            values=REQUEST_FILTER_CHOICES,
            state="readonly",
            width=18,
        )
        self.request_filter_combo.pack(side="left", padx=(5, 10))
        self.request_filter_combo.bind(
            "<<ComboboxSelected>>", self._on_request_filter_changed
        )
        ttk.Button(
            workflow_actions,
            text="選択をプロンプト生成待ち",
            command=self.mark_requests_ready,
        ).pack(side="left")
        ttk.Button(
            workflow_actions,
            text="選択を完了",
            command=self.mark_requests_done,
        ).pack(side="left", padx=5)

        export_actions = ttk.Frame(list_frame)
        export_actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Button(
            export_actions,
            text="選択をRequestSet JSONへ書出",
            command=self.export_selected_request_set,
        ).pack(side="left")
        ttk.Label(
            export_actions,
            text="生成ツールに渡す依頼ファイルを作ります",
            foreground="#555555",
        ).pack(side="left", padx=8)

        editor = ttk.LabelFrame(body, text="依頼を確認・整形", padding=8)
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)
        editor.rowconfigure(6, weight=3)
        editor.rowconfigure(8, weight=2)
        body.add(editor, weight=2)

        ttk.Label(
            editor,
            text="受け取った元文はそのまま保存されます。候補抽出後に自由に修正できます。",
            foreground="#555555",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 5))

        ttk.Label(editor, text="取得元").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(editor, textvariable=self.request_source_var).grid(
            row=1, column=1, sticky="ew", padx=(6, 10), pady=3
        )
        ttk.Label(editor, text="参照").grid(row=1, column=2, sticky="w", pady=3)
        ttk.Entry(editor, textvariable=self.request_source_reference_var).grid(
            row=1, column=3, sticky="ew", padx=(6, 0), pady=3
        )

        ttk.Label(editor, text="受取日時").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(editor, textvariable=self.request_received_at_var).grid(
            row=2, column=1, sticky="ew", padx=(6, 10), pady=3
        )
        ttk.Label(editor, text="状態").grid(row=2, column=2, sticky="w", pady=3)
        ttk.Combobox(
            editor,
            textvariable=self.request_status_var,
            values=tuple(REQUEST_STATUS_LABELS.values()),
            state="readonly",
        ).grid(row=2, column=3, sticky="ew", padx=(6, 0), pady=3)

        ttk.Label(editor, text="キャラクター").grid(
            row=3, column=0, sticky="w", pady=3
        )
        ttk.Entry(editor, textvariable=self.request_characters_var).grid(
            row=3, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=3
        )
        ttk.Label(editor, text="絵柄").grid(row=4, column=0, sticky="w", pady=3)
        self.request_style_combo = ttk.Combobox(
            editor, textvariable=self.request_style_var
        )
        self.request_style_combo.grid(
            row=4, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=3
        )

        ttk.Label(editor, text="受取原文").grid(row=6, column=0, sticky="nw", pady=3)
        self.request_raw_text = scrolledtext.ScrolledText(
            editor, wrap="word", height=10
        )
        self.request_raw_text.grid(
            row=6, column=1, columnspan=3, sticky="nsew", padx=(6, 0), pady=3
        )
        ttk.Button(
            editor,
            text="元文から候補抽出",
            command=self.extract_request_fields,
        ).grid(row=7, column=1, sticky="w", padx=(6, 0), pady=(2, 5))

        ttk.Label(editor, text="生成指示").grid(row=8, column=0, sticky="nw", pady=3)
        self.request_instructions_text = scrolledtext.ScrolledText(
            editor, wrap="word", height=6
        )
        self.request_instructions_text.grid(
            row=8, column=1, columnspan=3, sticky="nsew", padx=(6, 0), pady=3
        )
        ttk.Label(editor, text="管理メモ").grid(row=9, column=0, sticky="nw", pady=3)
        self.request_notes_text = tk.Text(editor, wrap="word", height=3)
        self.request_notes_text.grid(
            row=9, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=3
        )
        ttk.Button(
            editor, text="編集内容を保存", command=self.save_current_request
        ).grid(row=10, column=1, columnspan=3, sticky="e", pady=(7, 0))

    def choose_database(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.window,
            title="SQLiteプロンプトライブラリを開く",
            filetypes=[("SQLite database", "*.sqlite3"), ("All files", "*.*")],
        )
        if path:
            self.database_path_var.set(path)
            self.reload()

    def choose_new_database(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.window,
            title="新しいSQLiteプロンプトライブラリを作成",
            defaultextension=".sqlite3",
            filetypes=[("SQLite database", "*.sqlite3"), ("All files", "*.*")],
            initialfile="prompt_library.sqlite3",
        )
        if not path:
            return
        if Path(path).exists():
            messagebox.showerror(
                "新規DB",
                "選択したファイルは既に存在します。既存DBは「DBを開く」から選択してください。",
                parent=self.window,
            )
            return
        self.database_path_var.set(path)
        self.reload()

    def reload(
        self,
        *,
        select_job_id: int | None = None,
        select_request_id: int | None = None,
    ) -> None:
        try:
            self.library = PromptLibrary(Path(self.database_path_var.get()))
            catalog_style_names = self.library.sync_style_prompt_catalog()
            records = self.library.list_jobs()
            rules = self.library.list_style_rules()
            request_records = self.library.list_requests()
        except Exception as error:
            messagebox.showerror("SQLiteライブラリ", str(error), parent=self.window)
            return

        self.jobs = {record.id: record for record in records}
        for item in self.tree.get_children():
            self.tree.delete(item)
        for record in records:
            self.tree.insert(
                "",
                "end",
                iid=str(record.id),
                values=(
                    record.id,
                    record.collection_name,
                    PROMPT_STATUS_LABELS.get(record.status, record.status),
                    "✓" if record.enabled else "",
                    record.title,
                    record.style_key,
                    record.effective_upscaler or "（共通）",
                ),
            )

        discovered_style_names = (
            {record.style_key for record in records if record.style_key}
            | {rule.style_key for rule in rules}
            | {record.style_key for record in request_records if record.style_key}
        )
        style_names = list(catalog_style_names)
        known_style_names = {value.casefold() for value in style_names}
        for style_name in sorted(discovered_style_names, key=str.casefold):
            if style_name.casefold() not in known_style_names:
                style_names.append(style_name)
                known_style_names.add(style_name.casefold())
        self.style_combo.configure(values=style_names)
        self.rule_style_combo.configure(values=style_names)
        self.request_style_combo.configure(values=style_names)
        self.summary_var.set(f"{len(records)}件 / DB: {self.library.path.name}")
        if rules and not self.rule_style_var.get():
            self.rule_style_var.set(rules[0].style_key)
            self.rule_upscaler_var.set(rules[0].hr_upscaler)
        if select_job_id is not None and str(select_job_id) in self.tree.get_children():
            self.tree.selection_set(str(select_job_id))
            self.tree.see(str(select_job_id))
        elif records:
            self.tree.selection_set(str(records[0].id))

        self.requests = {record.id: record for record in request_records}
        self._refresh_request_tree(select_request_id=select_request_id)

    def _refresh_request_tree(
        self, *, select_request_id: int | None = None
    ) -> None:
        visible_records = [
            record
            for record in self.requests.values()
            if request_status_matches_filter(
                record.status, self.request_filter_var.get()
            )
        ]
        for item in self.request_tree.get_children():
            self.request_tree.delete(item)
        for record in visible_records:
            self.request_tree.insert(
                "",
                "end",
                iid=str(record.id),
                values=(
                    record.id,
                    REQUEST_STATUS_LABELS.get(record.status, record.status),
                    record.received_at.replace("T", " ")[:19],
                    record.characters_text,
                    record.style_key,
                    record.preview,
                ),
            )
        self.request_summary_var.set(
            f"表示 {len(visible_records)} / 全{len(self.requests)}件"
        )

        visible_ids = {record.id for record in visible_records}
        target_id = select_request_id
        if target_id not in visible_ids:
            target_id = self.current_request_id
        if target_id not in visible_ids:
            target_id = visible_records[0].id if visible_records else None
        if target_id is None:
            self.current_request_id = None
            self._clear_request_editor()
            return
        self.request_tree.selection_set(str(target_id))
        self.request_tree.see(str(target_id))
        self._on_request_tree_select()

    def _on_request_filter_changed(self, _event=None) -> None:
        self._refresh_request_tree()

    def import_text(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.window,
            title="既存プロンプトtxtを取り込む",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            _collection_id, count = self.library.import_text_file(path)
        except Exception as error:
            messagebox.showerror("txt取込", str(error), parent=self.window)
            return
        self.reload()
        messagebox.showinfo("txt取込", f"{count}件を取り込みました。", parent=self.window)

    def import_prompt_set(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.window,
            title="PromptSet JSONを取り込む",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            _collection_id, count = self.library.import_prompt_set(path)
        except Exception as error:
            messagebox.showerror("PromptSet取込", str(error), parent=self.window)
            return
        self.reload()
        messagebox.showinfo(
            "PromptSet取込", f"{count}件を取り込みました。", parent=self.window
        )

    def import_request_set(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.window,
            title="RequestSet JSONを取り込む",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            count = self.library.import_request_set(path)
        except Exception as error:
            messagebox.showerror("RequestSet取込", str(error), parent=self.window)
            return
        self.reload()
        self.notebook.select(0)
        messagebox.showinfo(
            "RequestSet取込", f"{count}件を取り込みました。", parent=self.window
        )

    def create_request(self) -> None:
        try:
            request_id = self.library.create_request(
                source="manual",
                received_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
        except Exception as error:
            messagebox.showerror("依頼の新規作成", str(error), parent=self.window)
            return
        self.reload(select_request_id=request_id)
        self.notebook.select(0)
        self.request_raw_text.focus_set()

    def create_request_from_clipboard(self) -> None:
        try:
            raw_text = self.window.clipboard_get().strip()
        except tk.TclError:
            raw_text = ""
        if not raw_text:
            messagebox.showwarning(
                "クリップボードから新規",
                "クリップボードにテキストがありません。",
                parent=self.window,
            )
            return
        try:
            request_id = self.library.create_request(
                raw_text,
                source="clipboard",
                received_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
        except Exception as error:
            messagebox.showerror(
                "クリップボードから新規", str(error), parent=self.window
            )
            return
        self.reload(select_request_id=request_id)
        self.notebook.select(0)

    def select_all_requests(self) -> None:
        self.request_tree.selection_set(self.request_tree.get_children())

    def mark_requests_ready(self) -> None:
        self._set_selected_request_status(
            "ready_for_prompt",
            action_title="プロンプト生成待ちへ変更",
        )

    def mark_requests_done(self) -> None:
        self._set_selected_request_status("done", action_title="完了へ変更")

    def _set_selected_request_status(
        self, status: str, *, action_title: str
    ) -> None:
        ids = self._selected_request_ids()
        if not ids:
            messagebox.showwarning(
                action_title,
                "変更する依頼を選択してください。",
                parent=self.window,
            )
            return
        try:
            for request_id in ids:
                record = self.requests[request_id]
                self.library.update_request(
                    record.id,
                    source=record.source,
                    source_reference=record.source_reference,
                    received_at=record.received_at,
                    raw_text=record.raw_text,
                    characters_text=record.characters_text,
                    style_key=record.style_key,
                    instructions_text=record.instructions_text,
                    status=status,
                    notes=record.notes,
                )
        except PromptLibraryError as error:
            messagebox.showerror(action_title, str(error), parent=self.window)
            return
        self.reload(select_request_id=ids[0] if len(ids) == 1 else None)

    def delete_selected_requests(self) -> None:
        ids = self._selected_request_ids()
        if not ids:
            messagebox.showwarning(
                "依頼を削除", "削除する依頼を選択してください。", parent=self.window
            )
            return
        if not messagebox.askyesno(
            "依頼を削除",
            f"選択した{len(ids)}件を依頼Inboxから削除しますか？",
            parent=self.window,
        ):
            return
        try:
            deleted = self.library.delete_requests(ids)
        except Exception as error:
            messagebox.showerror("依頼を削除", str(error), parent=self.window)
            return
        self.current_request_id = None
        self.reload()
        messagebox.showinfo(
            "依頼を削除", f"{deleted}件を削除しました。", parent=self.window
        )

    def _on_request_tree_select(self, _event=None) -> None:
        ids = self._selected_request_ids()
        if len(ids) != 1:
            self.current_request_id = None
            return
        record = self.requests.get(ids[0])
        if record is None:
            return
        self.current_request_id = record.id
        self.request_source_var.set(record.source)
        self.request_source_reference_var.set(record.source_reference)
        self.request_received_at_var.set(record.received_at)
        self.request_characters_var.set(record.characters_text)
        self.request_style_var.set(record.style_key)
        self.request_status_var.set(
            REQUEST_STATUS_LABELS.get(record.status, record.status)
        )
        self._replace_text(self.request_raw_text, record.raw_text)
        self._replace_text(self.request_instructions_text, record.instructions_text)
        self._replace_text(self.request_notes_text, record.notes)

    def _clear_request_editor(self) -> None:
        self.request_source_var.set("")
        self.request_source_reference_var.set("")
        self.request_received_at_var.set("")
        self.request_characters_var.set("")
        self.request_style_var.set("")
        self.request_status_var.set(REQUEST_STATUS_LABELS["received"])
        self._replace_text(self.request_raw_text, "")
        self._replace_text(self.request_instructions_text, "")
        self._replace_text(self.request_notes_text, "")

    def extract_request_fields(self) -> None:
        raw_text = self.request_raw_text.get("1.0", "end").strip()
        if not raw_text:
            messagebox.showwarning(
                "候補抽出", "受取原文を入力してください。", parent=self.window
            )
            return
        style_keys = self.window.tk.splitlist(
            self.request_style_combo.cget("values")
        )
        candidates = extract_request_candidates(raw_text, style_keys)
        if candidates["characters_text"]:
            self.request_characters_var.set(candidates["characters_text"])
        if candidates["style_key"]:
            self.request_style_var.set(candidates["style_key"])
        self._replace_text(
            self.request_instructions_text, candidates["instructions_text"]
        )
        if candidates["notes"]:
            self._replace_text(self.request_notes_text, candidates["notes"])

    def save_current_request(self) -> None:
        if self._persist_current_request():
            self.reload(select_request_id=self.current_request_id)

    def _persist_current_request(self) -> bool:
        if self.current_request_id is None:
            messagebox.showwarning(
                "依頼を保存",
                "編集する依頼を1件選択してください。",
                parent=self.window,
            )
            return False
        try:
            self.library.update_request(
                self.current_request_id,
                source=self.request_source_var.get(),
                source_reference=self.request_source_reference_var.get(),
                received_at=self.request_received_at_var.get(),
                raw_text=self.request_raw_text.get("1.0", "end").strip(),
                characters_text=self.request_characters_var.get(),
                style_key=self.request_style_var.get(),
                instructions_text=self.request_instructions_text.get(
                    "1.0", "end"
                ).strip(),
                status=REQUEST_STATUS_VALUES.get(
                    self.request_status_var.get(), self.request_status_var.get()
                ),
                notes=self.request_notes_text.get("1.0", "end").strip(),
            )
        except PromptLibraryError as error:
            messagebox.showerror("依頼を保存", str(error), parent=self.window)
            return False
        return True

    def export_selected_request_set(self) -> None:
        ids = self._selected_request_ids()
        if not ids:
            messagebox.showwarning(
                "RequestSet書出",
                "書き出す依頼を選択してください。",
                parent=self.window,
            )
            return
        if self.current_request_id in ids and not self._persist_current_request():
            return
        path = filedialog.asksaveasfilename(
            parent=self.window,
            title="生成ツール用RequestSet JSONを書き出す",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=str(self.library.path.parent),
            initialfile=f"{datetime.now():%Y%m%d}_RequestSet.json",
        )
        if not path:
            return
        try:
            count = self.library.export_request_set(path, ids)
        except PromptLibraryError as error:
            messagebox.showerror("RequestSet書出", str(error), parent=self.window)
            return
        self.reload(select_request_id=ids[0] if len(ids) == 1 else None)
        messagebox.showinfo(
            "RequestSet書出",
            f"{count}件を書き出しました。\n{path}",
            parent=self.window,
        )

    def create_job(self) -> None:
        try:
            job_id = self.library.create_job()
        except Exception as error:
            messagebox.showerror("新規作成", str(error), parent=self.window)
            return
        self.reload(select_job_id=job_id)

    def delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showwarning("選択削除", "削除する項目を選択してください。", parent=self.window)
            return
        if not messagebox.askyesno(
            "選択削除",
            f"選択した{len(ids)}件をライブラリから削除しますか？",
            parent=self.window,
        ):
            return
        try:
            deleted = self.library.delete_jobs(ids)
        except Exception as error:
            messagebox.showerror("選択削除", str(error), parent=self.window)
            return
        self.current_job_id = None
        self.reload()
        messagebox.showinfo(
            "選択削除", f"{deleted}件を削除しました。", parent=self.window
        )

    def select_all(self) -> None:
        self.tree.selection_set(self.tree.get_children())

    def mark_selected_ready(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showwarning(
                "生成準備済みへ変更",
                "変更する項目を選択してください。",
                parent=self.window,
            )
            return
        try:
            for job_id in ids:
                record = self.jobs[job_id]
                self.library.update_job(
                    record.id,
                    title=record.title,
                    prompt=record.prompt,
                    style_key=record.style_key,
                    status="ready",
                    enabled=record.enabled,
                    settings_override=record.settings_override,
                    notes=record.notes,
                )
        except PromptLibraryError as error:
            messagebox.showerror(
                "生成準備済みへ変更", str(error), parent=self.window
            )
            return
        self.reload()

    def _on_tree_select(self, _event=None) -> None:
        ids = self._selected_ids()
        if len(ids) != 1:
            return
        record = self.jobs.get(ids[0])
        if record is None:
            return
        self.current_job_id = record.id
        self.title_var.set(record.title)
        self.style_var.set(record.style_key)
        self.status_var.set(PROMPT_STATUS_LABELS.get(record.status, record.status))
        self.enabled_var.set(record.enabled)
        self.upscaler_override_var.set(
            str(record.settings_override.get("hr_upscaler", ""))
        )
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", record.prompt)
        self.notes_text.delete("1.0", "end")
        self.notes_text.insert("1.0", record.notes)

    def save_current(self) -> None:
        if self.current_job_id is None:
            messagebox.showwarning("保存", "編集する項目を1件選択してください。", parent=self.window)
            return
        record = self.jobs.get(self.current_job_id)
        if record is None:
            return
        override = dict(record.settings_override)
        upscaler = self.upscaler_override_var.get().strip()
        if upscaler:
            override["hr_upscaler"] = upscaler
        else:
            override.pop("hr_upscaler", None)
        try:
            self.library.update_job(
                self.current_job_id,
                title=self.title_var.get(),
                prompt=self.prompt_text.get("1.0", "end").strip(),
                style_key=self.style_var.get(),
                status=PROMPT_STATUS_VALUES.get(
                    self.status_var.get(), self.status_var.get()
                ),
                enabled=self.enabled_var.get(),
                settings_override=override,
                notes=self.notes_text.get("1.0", "end").strip(),
            )
        except PromptLibraryError as error:
            messagebox.showerror("保存", str(error), parent=self.window)
            return
        self.reload(select_job_id=self.current_job_id)

    def _on_rule_selected(self, _event=None) -> None:
        selected = self.rule_style_var.get().strip().casefold()
        for rule in self.library.list_style_rules():
            if rule.style_key.casefold() == selected:
                self.rule_upscaler_var.set(rule.hr_upscaler)
                return
        self.rule_upscaler_var.set("")

    def save_style_rule(self) -> None:
        style = self.rule_style_var.get().strip()
        upscaler = self.rule_upscaler_var.get().strip()
        if not upscaler:
            messagebox.showwarning(
                "絵柄規則", "Upscalerを入力してください。", parent=self.window
            )
            return
        try:
            self.library.set_style_rule(style, {"hr_upscaler": upscaler})
        except PromptLibraryError as error:
            messagebox.showerror("絵柄規則", str(error), parent=self.window)
            return
        self.reload(select_job_id=self.current_job_id)

    def load_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showwarning(
                "バッチへ読込", "生成する項目を選択してください。", parent=self.window
            )
            return
        self._load_into_runner(ids)

    def load_all_ready(self) -> None:
        ids = tuple(
            record.id
            for record in self.jobs.values()
            if record.enabled and record.status == "ready"
        )
        if not ids:
            messagebox.showwarning(
                "バッチへ読込",
                "有効な生成準備済み項目がありません。",
                parent=self.window,
            )
            return
        self._load_into_runner(ids)

    def _load_into_runner(self, ids: tuple[int, ...]) -> None:
        enabled_ids = tuple(
            job_id for job_id in ids if self.jobs.get(job_id) and self.jobs[job_id].enabled
        )
        if not enabled_ids:
            messagebox.showwarning(
                "バッチへ読込", "選択項目はすべて生成対象外です。", parent=self.window
            )
            return
        self.on_load(self.library.path, enabled_ids)
        self.window.destroy()

    def _selected_ids(self) -> tuple[int, ...]:
        return tuple(int(item) for item in self.tree.selection())

    def _selected_request_ids(self) -> tuple[int, ...]:
        return tuple(int(item) for item in self.request_tree.selection())

    @staticmethod
    def _replace_text(widget: tk.Text, value: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
