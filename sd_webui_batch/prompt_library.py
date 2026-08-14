from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .parser import PromptJob, extract_style_key, parse_prompt_note, read_text_file


SCHEMA_VERSION = 2
PROMPT_SET_SCHEMA_VERSION = 1
REQUEST_SET_SCHEMA_VERSION = 1
REQUEST_STATUSES = (
    "received",
    "reviewed",
    "ready_for_prompt",
    "prompt_generated",
    "done",
)
DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parent.parent / "data_local" / "prompt_library.sqlite3"
)
DEFAULT_STYLE_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "sd-webui-prompt-codex-generate"
    / "0_SDXL Style Prompt.txt"
)
STYLE_RULE_DEFAULTS_VERSION = "1"


class PromptLibraryError(ValueError):
    """Raised when prompt-library data is invalid or cannot be imported."""


@dataclass(frozen=True)
class LibraryJob:
    id: int
    collection_id: int
    collection_name: str
    sort_order: int
    title: str
    prompt: str
    style_key: str
    status: str
    enabled: bool
    settings_override: dict[str, Any]
    effective_settings: dict[str, Any]
    notes: str
    request_id: int | None = None

    @property
    def effective_upscaler(self) -> str:
        return str(self.effective_settings.get("hr_upscaler", ""))


@dataclass(frozen=True)
class StyleRule:
    style_key: str
    settings_override: dict[str, Any]

    @property
    def hr_upscaler(self) -> str:
        return str(self.settings_override.get("hr_upscaler", ""))


@dataclass(frozen=True)
class RequestRecord:
    id: int
    source: str
    source_reference: str
    received_at: str
    raw_text: str
    characters_text: str
    style_key: str
    instructions_text: str
    status: str
    notes: str
    created_at: str
    updated_at: str

    @property
    def preview(self) -> str:
        return " ".join(self.raw_text.split())[:120]


LATENT_UPSCALER = "Latent (antialiased)"
DEFAULT_STYLE_RULES = (
    StyleRule("ノーマル", {"hr_upscaler": LATENT_UPSCALER}),
    StyleRule("ヌルテカ", {"hr_upscaler": LATENT_UPSCALER}),
    StyleRule("bgk", {"hr_upscaler": LATENT_UPSCALER}),
    StyleRule("mcp", {"hr_upscaler": LATENT_UPSCALER}),
    StyleRule("qwq", {"hr_upscaler": LATENT_UPSCALER}),
    StyleRule("lil", {"hr_upscaler": LATENT_UPSCALER}),
    StyleRule("kak", {"hr_upscaler": LATENT_UPSCALER}),
    StyleRule("iwn", {"hr_upscaler": "Lanczos"}),
    StyleRule("ata", {"hr_upscaler": "Lanczos"}),
)
LEGACY_STYLE_RULE_DEFAULTS = {
    "ノーマル": {"hr_upscaler": LATENT_UPSCALER},
    "ヌルテカ": {"hr_upscaler": "Lanczos"},
    "mcp": {"hr_upscaler": LATENT_UPSCALER},
}


def parse_style_prompt_catalog(text: str) -> tuple[str, ...]:
    """Return GUI style keys from the generator's ●-headed style prompt file."""

    style_keys: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("●"):
            continue
        heading = line[1:].strip()
        if heading in {"ノーマル", "ヌルテカ"}:
            style_key = heading
        elif heading.endswith(")") and "(" in heading:
            style_key = heading[heading.rfind("(") + 1 : -1].strip()
        else:
            continue
        folded = style_key.casefold()
        if not style_key or folded in seen:
            continue
        seen.add(folded)
        style_keys.append(style_key)
    return tuple(style_keys)


def merge_payload_overrides(
    base: dict[str, Any], override: dict[str, Any] | None
) -> dict[str, Any]:
    """Deep-copy and recursively merge a per-style or per-job payload override."""

    merged = deepcopy(base)
    if not override:
        return merged

    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_payload_overrides(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def extract_request_candidates(
    raw_text: str, style_keys: Iterable[str] = ()
) -> dict[str, str]:
    """Extract conservative editable candidates while preserving the raw request."""

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    characters = ""
    style = ""
    instruction_lines: list[str] = []
    note_lines: list[str] = []

    character_labels = ("キャラクター", "キャラ")
    style_labels = ("絵柄", "スタイル")
    instruction_labels = ("構図", "内容", "要望", "リクエスト")
    note_labels = ("補足", "備考", "できれば")

    for line in lines:
        label, value = _split_labeled_line(line)
        if label in character_labels and value:
            characters = value
        elif label in style_labels and value:
            style = value
        elif label in instruction_labels and value:
            instruction_lines.append(value)
        elif label in note_labels and value:
            note_lines.append(value)
        else:
            instruction_lines.append(line)

    known_styles = sorted(
        {value.strip() for value in style_keys if value.strip()},
        key=len,
        reverse=True,
    )
    if not style:
        request_tokens = {
            token.casefold() for token in _request_candidate_tokens(raw_text)
        }
        for candidate in known_styles:
            if candidate.casefold() in request_tokens:
                style = candidate
                break

    if not characters and style and lines:
        first_line = lines[0]
        position = first_line.casefold().find(style.casefold())
        if position > 0:
            prefix = first_line[:position].strip(" 　:：,、")
            prefix_tokens = _request_candidate_tokens(prefix)
            if prefix_tokens and prefix_tokens[0] in character_labels:
                prefix_tokens = prefix_tokens[1:]
            if prefix_tokens:
                candidate = prefix_tokens[0]
                if candidate not in style_labels:
                    characters = candidate

    return {
        "characters_text": characters,
        "style_key": style,
        "instructions_text": "\n".join(instruction_lines).strip(),
        "notes": "\n".join(note_lines).strip(),
    }


class PromptLibrary:
    def __init__(self, path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS collections (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_path TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS style_rules (
                    style_key TEXT PRIMARY KEY COLLATE NOCASE,
                    settings_override_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL DEFAULT '',
                    source_reference TEXT NOT NULL DEFAULT '',
                    received_at TEXT NOT NULL DEFAULT '',
                    raw_text TEXT NOT NULL DEFAULT '',
                    characters_text TEXT NOT NULL DEFAULT '',
                    style_key TEXT NOT NULL DEFAULT '',
                    instructions_text TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'received',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS prompt_jobs (
                    id INTEGER PRIMARY KEY,
                    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    sort_order INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    style_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    settings_override_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT NOT NULL DEFAULT '',
                    source_line INTEGER NOT NULL DEFAULT 0,
                    request_id INTEGER REFERENCES requests(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_prompt_jobs_collection_order
                    ON prompt_jobs(collection_id, sort_order, id);
                CREATE INDEX IF NOT EXISTS idx_prompt_jobs_style
                    ON prompt_jobs(style_key);
                CREATE INDEX IF NOT EXISTS idx_prompt_jobs_status
                    ON prompt_jobs(status, enabled);
                CREATE INDEX IF NOT EXISTS idx_requests_status
                    ON requests(status, received_at, id);
                """
            )
            prompt_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(prompt_jobs)").fetchall()
            }
            if "request_id" not in prompt_columns:
                connection.execute(
                    """
                    ALTER TABLE prompt_jobs
                    ADD COLUMN request_id INTEGER REFERENCES requests(id) ON DELETE SET NULL
                    """
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_prompt_jobs_request
                ON prompt_jobs(request_id)
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            now = _utc_now()
            for rule in DEFAULT_STYLE_RULES:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO style_rules(
                        style_key, settings_override_json, created_at, updated_at
                    ) VALUES(?, ?, ?, ?)
                    """,
                    (
                        rule.style_key,
                        _dump_object(rule.settings_override),
                        now,
                        now,
                    ),
                )
            defaults_version = connection.execute(
                "SELECT value FROM metadata WHERE key = 'style_rule_defaults_version'"
            ).fetchone()
            if defaults_version is None:
                for rule in DEFAULT_STYLE_RULES:
                    row = connection.execute(
                        "SELECT settings_override_json FROM style_rules WHERE style_key = ?",
                        (rule.style_key,),
                    ).fetchone()
                    current = _load_object(row["settings_override_json"])
                    legacy_default = LEGACY_STYLE_RULE_DEFAULTS.get(rule.style_key)
                    if current == {} or current == legacy_default:
                        connection.execute(
                            """
                            UPDATE style_rules
                            SET settings_override_json = ?, updated_at = ?
                            WHERE style_key = ?
                            """,
                            (
                                _dump_object(rule.settings_override),
                                now,
                                rule.style_key,
                            ),
                        )
                connection.execute(
                    """
                    INSERT INTO metadata(key, value)
                    VALUES('style_rule_defaults_version', ?)
                    """,
                    (STYLE_RULE_DEFAULTS_VERSION,),
                )

    def import_text_file(self, path: str | Path) -> tuple[int, int]:
        source = Path(path)
        jobs = parse_prompt_note(read_text_file(source))
        return self._insert_collection(
            source.stem,
            source_path=str(source.resolve()),
            source_kind="text",
            jobs=[
                {
                    "title": job.title,
                    "prompt": job.prompt,
                    "style_key": job.style_key,
                    "settings_override": {},
                    "source_line": job.line_number,
                }
                for job in jobs
            ],
        )

    def import_prompt_set(self, path: str | Path) -> tuple[int, int]:
        source = Path(path)
        try:
            data = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise PromptLibraryError(f"PromptSet JSONを読み込めません: {error}") from error

        if not isinstance(data, dict):
            raise PromptLibraryError("PromptSet JSONのルートはオブジェクトにしてください。")
        if data.get("schema_version") != PROMPT_SET_SCHEMA_VERSION:
            raise PromptLibraryError(
                f"未対応のschema_versionです: {data.get('schema_version')!r}"
            )
        raw_jobs = data.get("jobs")
        if not isinstance(raw_jobs, list) or not raw_jobs:
            raise PromptLibraryError("PromptSet JSONのjobsには1件以上必要です。")

        jobs: list[dict[str, Any]] = []
        for index, item in enumerate(raw_jobs, start=1):
            if not isinstance(item, dict):
                raise PromptLibraryError(f"jobs[{index}]はオブジェクトにしてください。")
            title = str(item.get("title", "")).strip()
            prompt = str(item.get("prompt", "")).strip()
            if not title or not prompt:
                raise PromptLibraryError(
                    f"jobs[{index}]のtitleとpromptは空にできません。"
                )
            style_key = str(item.get("style", "")).strip() or extract_style_key(title)
            settings_override = item.get("settings_override", {})
            if not isinstance(settings_override, dict):
                raise PromptLibraryError(
                    f"jobs[{index}].settings_overrideはオブジェクトにしてください。"
                )
            jobs.append(
                {
                    "title": title,
                    "prompt": prompt,
                    "style_key": style_key,
                    "settings_override": settings_override,
                    "source_line": 0,
                    "request_id": _optional_positive_int(
                        item.get("source_request_id"),
                        f"jobs[{index}].source_request_id",
                    ),
                }
            )

        collection_name = str(data.get("collection", "")).strip() or source.stem
        return self._insert_collection(
            collection_name,
            source_path=str(source.resolve()),
            source_kind="promptset-json",
            jobs=jobs,
        )

    def import_request_set(self, path: str | Path) -> int:
        source_path = Path(path)
        try:
            data = json.loads(source_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise PromptLibraryError(f"RequestSet JSONを読み込めません: {error}") from error
        if not isinstance(data, dict):
            raise PromptLibraryError("RequestSet JSONのルートはオブジェクトにしてください。")
        if data.get("schema_version") != REQUEST_SET_SCHEMA_VERSION:
            raise PromptLibraryError(
                f"未対応のRequestSet schema_versionです: {data.get('schema_version')!r}"
            )
        raw_requests = data.get("requests")
        if not isinstance(raw_requests, list) or not raw_requests:
            raise PromptLibraryError("RequestSet JSONのrequestsには1件以上必要です。")

        self.initialize()
        now = _utc_now()
        with self._connection() as connection:
            for index, item in enumerate(raw_requests, start=1):
                if not isinstance(item, dict):
                    raise PromptLibraryError(
                        f"requests[{index}]はオブジェクトにしてください。"
                    )
                raw_text = str(item.get("raw_text", "")).strip()
                if not raw_text:
                    raise PromptLibraryError(
                        f"requests[{index}].raw_textは空にできません。"
                    )
                characters = item.get("characters", "")
                if isinstance(characters, list):
                    characters = ", ".join(str(value).strip() for value in characters)
                self._insert_request_row(
                    connection,
                    source=str(item.get("source", "")).strip(),
                    source_reference=str(item.get("source_reference", "")).strip(),
                    received_at=str(item.get("received_at", "")).strip(),
                    raw_text=raw_text,
                    characters_text=str(characters).strip(),
                    style_key=str(item.get("style", "")).strip(),
                    instructions_text=str(item.get("instructions", "")).strip(),
                    status="received",
                    notes=str(item.get("notes", "")).strip(),
                    now=now,
                )
        return len(raw_requests)

    def create_request(
        self,
        raw_text: str = "",
        *,
        source: str = "manual",
        source_reference: str = "",
        received_at: str | None = None,
    ) -> int:
        self.initialize()
        candidates = extract_request_candidates(
            raw_text, (rule.style_key for rule in self.list_style_rules())
        )
        now = _utc_now()
        with self._connection() as connection:
            cursor = self._insert_request_row(
                connection,
                source=source.strip(),
                source_reference=source_reference.strip(),
                received_at=(received_at or _local_now()).strip(),
                raw_text=raw_text.strip(),
                characters_text=candidates["characters_text"],
                style_key=candidates["style_key"],
                instructions_text=candidates["instructions_text"],
                status="received",
                notes=candidates["notes"],
                now=now,
            )
            return int(cursor.lastrowid)

    def list_requests(self) -> list[RequestRecord]:
        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM requests
                ORDER BY id DESC
                """
            ).fetchall()
        return [self._row_to_request(row) for row in rows]

    def get_request(self, request_id: int) -> RequestRecord:
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM requests WHERE id = ?", (request_id,)
            ).fetchone()
        if row is None:
            raise PromptLibraryError(f"依頼ID {request_id} が見つかりません。")
        return self._row_to_request(row)

    def update_request(
        self,
        request_id: int,
        *,
        source: str,
        source_reference: str,
        received_at: str,
        raw_text: str,
        characters_text: str,
        style_key: str,
        instructions_text: str,
        status: str,
        notes: str,
    ) -> None:
        raw_text = raw_text.strip()
        if not raw_text:
            raise PromptLibraryError("元の依頼文は空にできません。")
        if status not in REQUEST_STATUSES:
            raise PromptLibraryError(f"未対応の依頼状態です: {status}")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE requests
                SET source = ?, source_reference = ?, received_at = ?, raw_text = ?,
                    characters_text = ?, style_key = ?, instructions_text = ?,
                    status = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    source.strip(),
                    source_reference.strip(),
                    received_at.strip(),
                    raw_text,
                    characters_text.strip(),
                    style_key.strip(),
                    instructions_text.strip(),
                    status,
                    notes.strip(),
                    _utc_now(),
                    request_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PromptLibraryError(f"依頼ID {request_id} が見つかりません。")

    def delete_requests(self, request_ids: Iterable[int]) -> int:
        ids = tuple(dict.fromkeys(int(value) for value in request_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM requests WHERE id IN ({placeholders})", ids
            )
            return int(cursor.rowcount)

    def export_request_set(
        self,
        path: str | Path,
        request_ids: Iterable[int],
        *,
        collection: str = "",
    ) -> int:
        ids = tuple(sorted({int(value) for value in request_ids}))
        if not ids:
            raise PromptLibraryError("書き出す依頼を選択してください。")
        records_by_id = {record.id: record for record in self.list_requests()}
        records = [records_by_id[value] for value in ids if value in records_by_id]
        if not records:
            raise PromptLibraryError("書き出し対象の依頼が見つかりません。")

        destination = Path(path)
        payload = {
            "schema_version": REQUEST_SET_SCHEMA_VERSION,
            "collection": collection.strip() or destination.stem.replace("_RequestSet", ""),
            "requests": [
                {
                    "request_id": record.id,
                    "source": record.source,
                    "source_reference": record.source_reference,
                    "received_at": record.received_at,
                    "raw_text": record.raw_text,
                    "characters": record.characters_text,
                    "style": record.style_key,
                    "instructions": record.instructions_text,
                    "notes": record.notes,
                }
                for record in records
            ],
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
        return len(records)

    def create_job(self, collection_id: int | None = None) -> int:
        self.initialize()
        now = _utc_now()
        with self._connection() as connection:
            if collection_id is None:
                row = connection.execute(
                    "SELECT id FROM collections ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO collections(name, source_kind, created_at, updated_at)
                        VALUES(?, 'manual', ?, ?)
                        """,
                        ("手動作成", now, now),
                    )
                    collection_id = int(cursor.lastrowid)
                else:
                    collection_id = int(row["id"])
            order_row = connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM prompt_jobs WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
            cursor = connection.execute(
                """
                INSERT INTO prompt_jobs(
                    collection_id, sort_order, title, prompt, created_at, updated_at
                ) VALUES(?, ?, '新規タイトル', 'prompt,', ?, ?)
                """,
                (collection_id, int(order_row["next_order"]), now, now),
            )
            return int(cursor.lastrowid)

    def list_jobs(self) -> list[LibraryJob]:
        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT j.*, c.name AS collection_name
                FROM prompt_jobs AS j
                JOIN collections AS c ON c.id = j.collection_id
                ORDER BY c.id DESC, j.sort_order, j.id
                """
            ).fetchall()
            rules = self._style_rule_map(connection)
        return [self._row_to_job(row, rules) for row in rows]

    def get_job(self, job_id: int) -> LibraryJob:
        self.initialize()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT j.*, c.name AS collection_name
                FROM prompt_jobs AS j
                JOIN collections AS c ON c.id = j.collection_id
                WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise PromptLibraryError(f"プロンプトID {job_id} が見つかりません。")
            rules = self._style_rule_map(connection)
        return self._row_to_job(row, rules)

    def update_job(
        self,
        job_id: int,
        *,
        title: str,
        prompt: str,
        style_key: str,
        status: str,
        enabled: bool,
        settings_override: dict[str, Any],
        notes: str,
    ) -> None:
        title = title.strip()
        prompt = prompt.strip()
        if not title or not prompt:
            raise PromptLibraryError("タイトルとプロンプトは空にできません。")
        if status not in {"draft", "ready", "generated"}:
            raise PromptLibraryError(f"未対応の状態です: {status}")
        now = _utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE prompt_jobs
                SET title = ?, prompt = ?, style_key = ?, status = ?, enabled = ?,
                    settings_override_json = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    prompt,
                    style_key.strip(),
                    status,
                    int(enabled),
                    _dump_object(settings_override),
                    notes.strip(),
                    now,
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PromptLibraryError(f"プロンプトID {job_id} が見つかりません。")

    def delete_jobs(self, job_ids: Iterable[int]) -> int:
        ids = tuple(dict.fromkeys(int(value) for value in job_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM prompt_jobs WHERE id IN ({placeholders})", ids
            )
            return int(cursor.rowcount)

    def list_style_rules(self) -> list[StyleRule]:
        self.initialize()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT style_key, settings_override_json FROM style_rules ORDER BY style_key COLLATE NOCASE"
            ).fetchall()
        return [
            StyleRule(row["style_key"], _load_object(row["settings_override_json"]))
            for row in rows
        ]

    def sync_style_prompt_catalog(
        self, path: str | Path = DEFAULT_STYLE_PROMPT_PATH
    ) -> tuple[str, ...]:
        """Add style names from the generator catalog without overwriting rules."""

        source = Path(path)
        if not source.is_file():
            return ()
        try:
            style_keys = parse_style_prompt_catalog(
                source.read_text(encoding="utf-8-sig")
            )
        except OSError as error:
            raise PromptLibraryError(f"絵柄一覧を読み込めません: {error}") from error
        if not style_keys:
            return ()

        self.initialize()
        now = _utc_now()
        with self._connection() as connection:
            for style_key in style_keys:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO style_rules(
                        style_key, settings_override_json, created_at, updated_at
                    ) VALUES(?, '{}', ?, ?)
                    """,
                    (style_key, now, now),
                )
        return style_keys

    def set_style_rule(self, style_key: str, settings_override: dict[str, Any]) -> None:
        style_key = style_key.strip()
        if not style_key:
            raise PromptLibraryError("絵柄名は空にできません。")
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO style_rules(
                    style_key, settings_override_json, created_at, updated_at
                ) VALUES(?, ?, ?, ?)
                ON CONFLICT(style_key) DO UPDATE SET
                    settings_override_json = excluded.settings_override_json,
                    updated_at = excluded.updated_at
                """,
                (style_key, _dump_object(settings_override), now, now),
            )

    def load_generation_jobs(self, job_ids: Iterable[int]) -> list[PromptJob]:
        ids = tuple(dict.fromkeys(int(value) for value in job_ids))
        if not ids:
            return []
        jobs_by_id = {job.id: job for job in self.list_jobs()}
        result: list[PromptJob] = []
        for job_id in ids:
            record = jobs_by_id.get(job_id)
            if record is None or not record.enabled:
                continue
            result.append(
                PromptJob(
                    index=len(result) + 1,
                    title=record.title,
                    prompt=record.prompt,
                    line_number=0,
                    style_key=record.style_key,
                    settings_override=record.effective_settings,
                    source_id=record.id,
                )
            )
        return result

    def _insert_collection(
        self,
        name: str,
        *,
        source_path: str,
        source_kind: str,
        jobs: list[dict[str, Any]],
    ) -> tuple[int, int]:
        self.initialize()
        now = _utc_now()
        with self._connection() as connection:
            unique_name = self._unique_collection_name(connection, name.strip() or "プロンプト")
            cursor = connection.execute(
                """
                INSERT INTO collections(name, source_path, source_kind, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (unique_name, source_path, source_kind, now, now),
            )
            collection_id = int(cursor.lastrowid)
            linked_request_ids: set[int] = set()
            for order, job in enumerate(jobs, start=1):
                request_id = job.get("request_id")
                if request_id is not None:
                    exists = connection.execute(
                        "SELECT 1 FROM requests WHERE id = ?", (request_id,)
                    ).fetchone()
                    if exists is None:
                        request_id = None
                    else:
                        linked_request_ids.add(int(request_id))
                connection.execute(
                    """
                    INSERT INTO prompt_jobs(
                        collection_id, sort_order, title, prompt, style_key,
                        settings_override_json, source_line, request_id, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        collection_id,
                        order,
                        job["title"],
                        job["prompt"],
                        job.get("style_key", ""),
                        _dump_object(job.get("settings_override", {})),
                        int(job.get("source_line", 0)),
                        request_id,
                        now,
                        now,
                    ),
                )
            if linked_request_ids:
                placeholders = ",".join("?" for _ in linked_request_ids)
                connection.execute(
                    f"""
                    UPDATE requests
                    SET status = 'prompt_generated', updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (now, *sorted(linked_request_ids)),
                )
        return collection_id, len(jobs)

    @staticmethod
    def _insert_request_row(
        connection: sqlite3.Connection,
        *,
        source: str,
        source_reference: str,
        received_at: str,
        raw_text: str,
        characters_text: str,
        style_key: str,
        instructions_text: str,
        status: str,
        notes: str,
        now: str,
    ) -> sqlite3.Cursor:
        return connection.execute(
            """
            INSERT INTO requests(
                source, source_reference, received_at, raw_text, characters_text,
                style_key, instructions_text, status, notes, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                source_reference,
                received_at,
                raw_text,
                characters_text,
                style_key,
                instructions_text,
                status,
                notes,
                now,
                now,
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _style_rule_map(self, connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        rows = connection.execute(
            "SELECT style_key, settings_override_json FROM style_rules"
        ).fetchall()
        return {
            str(row["style_key"]).strip().casefold(): _load_object(
                row["settings_override_json"]
            )
            for row in rows
        }

    def _row_to_job(
        self, row: sqlite3.Row, rules: dict[str, dict[str, Any]]
    ) -> LibraryJob:
        settings_override = _load_object(row["settings_override_json"])
        style_settings = rules.get(str(row["style_key"]).strip().casefold(), {})
        effective_settings = merge_payload_overrides(style_settings, settings_override)
        return LibraryJob(
            id=int(row["id"]),
            collection_id=int(row["collection_id"]),
            collection_name=str(row["collection_name"]),
            sort_order=int(row["sort_order"]),
            title=str(row["title"]),
            prompt=str(row["prompt"]),
            style_key=str(row["style_key"]),
            status=str(row["status"]),
            enabled=bool(row["enabled"]),
            settings_override=settings_override,
            effective_settings=effective_settings,
            notes=str(row["notes"]),
            request_id=(int(row["request_id"]) if row["request_id"] is not None else None),
        )

    @staticmethod
    def _row_to_request(row: sqlite3.Row) -> RequestRecord:
        return RequestRecord(
            id=int(row["id"]),
            source=str(row["source"]),
            source_reference=str(row["source_reference"]),
            received_at=str(row["received_at"]),
            raw_text=str(row["raw_text"]),
            characters_text=str(row["characters_text"]),
            style_key=str(row["style_key"]),
            instructions_text=str(row["instructions_text"]),
            status=str(row["status"]),
            notes=str(row["notes"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _unique_collection_name(connection: sqlite3.Connection, base: str) -> str:
        existing = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM collections").fetchall()
        }
        if base not in existing:
            return base
        suffix = 2
        while f"{base} ({suffix})" in existing:
            suffix += 1
        return f"{base} ({suffix})"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _local_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _request_candidate_tokens(text: str) -> tuple[str, ...]:
    normalized = text
    for separator in (
        ",",
        "、",
        ":",
        "：",
        ";",
        "；",
        "/",
        "|",
        "(",
        ")",
        "（",
        "）",
        "[",
        "]",
        "［",
        "］",
        "【",
        "】",
        "。",
    ):
        normalized = normalized.replace(separator, " ")
    return tuple(token for token in normalized.split() if token)


def _split_labeled_line(line: str) -> tuple[str, str]:
    for separator in ("：", ":"):
        label, found, value = line.partition(separator)
        if found:
            return label.strip(), value.strip()
    parts = line.split(None, 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", line.strip()


def _optional_positive_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise PromptLibraryError(f"{field_name}は正の整数にしてください。")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise PromptLibraryError(f"{field_name}は正の整数にしてください。") from error
    if parsed <= 0:
        raise PromptLibraryError(f"{field_name}は正の整数にしてください。")
    return parsed


def _dump_object(value: dict[str, Any]) -> str:
    if not isinstance(value, dict):
        raise PromptLibraryError("設定上書きはJSONオブジェクトにしてください。")
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_object(value: str) -> dict[str, Any]:
    try:
        loaded = json.loads(value or "{}")
    except json.JSONDecodeError as error:
        raise PromptLibraryError(f"保存済み設定JSONが壊れています: {error}") from error
    if not isinstance(loaded, dict):
        raise PromptLibraryError("保存済み設定はJSONオブジェクトではありません。")
    return loaded
