from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from sd_webui_batch.cli import build_payload
from sd_webui_batch.prompt_library import (
    DEFAULT_STYLE_RULES,
    PromptLibrary,
    extract_request_candidates,
    parse_style_prompt_catalog,
)


class PromptLibraryTests(unittest.TestCase):
    def test_default_rules_include_catalog_styles_for_pcs_without_catalog_file(self):
        rules = {rule.style_key: rule.hr_upscaler for rule in DEFAULT_STYLE_RULES}

        for style_key in {"jir", "iwn", "ata", "foo", "sym", "ter", "rub", "moo"}:
            self.assertEqual(rules[style_key], "Lanczos")
        for style_key in {"ノーマル", "ヌルテカ", "bgk", "mcp", "qwq", "lil", "kak"}:
            self.assertEqual(rules[style_key], "Latent (antialiased)")

    def test_parses_style_catalog_display_names(self):
        style_keys = parse_style_prompt_catalog(
            "●ノーマル\n,\n\n"
            "●ヌルテカ\nstyle prompt,\n\n"
            "●Blue_GK (bgk)\nstyle prompt,\n\n"
            "●むちぱん (mcp)\nstyle prompt,\n\n"
            "●Artist style (qwq)\nstyle prompt,\n\n"
            "●Lile リール Style - IL (lil)\nstyle prompt,\n\n"
            "●かけうどん Style - IL (kak)\nstyle prompt,\n\n"
            "●超ジロー Hires. fix Lanczos (jir)\nstyle prompt,\n\n"
            "●岩野健太 自作 Hires. fix Lanczos (iwn)\nstyle prompt,\n\n"
            "●atahuta 自作 Hires. fix Lanczos (ata)\nstyle prompt,\n\n"
            "●ふおおおお 自作 Hires. fix Lanczos (foo)\nstyle prompt,\n\n"
            "●シャーやま 自作 Hires. fix Lanczos (sym)\nstyle prompt,\n\n"
            "●てるびぅむ 自作 Hires. fix Lanczos (ter)\nstyle prompt,\n\n"
            "●るべゑ 自作 Hires. fix Lanczos (rub)\nstyle prompt,\n\n"
            "●Moo 自作 Hires. fix Lanczos (moo)\nstyle prompt,\n\n"
            "●括弧なしの追加絵柄\nstyle prompt,\n"
        )

        self.assertEqual(
            style_keys,
            (
                "ノーマル",
                "ヌルテカ",
                "bgk",
                "mcp",
                "qwq",
                "lil",
                "kak",
                "jir",
                "iwn",
                "ata",
                "foo",
                "sym",
                "ter",
                "rub",
                "moo",
            ),
        )

    def test_syncs_style_catalog_without_overwriting_upscaler_rules(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "0_SDXL Style Prompt.txt"
            catalog.write_text(
                "●ノーマル\n,\n\n"
                "●ヌルテカ\nstyle prompt,\n\n"
                "●Blue_GK (bgk)\nstyle prompt,\n\n"
                "●むちぱん (mcp)\nstyle prompt,\n\n"
                "●Artist style (qwq)\nstyle prompt,\n\n"
                "●超ジロー Hires. fix Lanczos (jir)\nstyle prompt,\n\n"
                "●岩野健太 自作 Hires. fix Lanczos (iwn)\nstyle prompt,\n",
                encoding="utf-8",
            )
            library = PromptLibrary(root / "library.sqlite3")
            library.initialize()
            library.set_style_rule("mcp", {"hr_upscaler": "R-ESRGAN 4x+"})

            style_keys = library.sync_style_prompt_catalog(catalog)
            rules = {rule.style_key: rule for rule in library.list_style_rules()}
            request_id = library.create_request("test character bgk composition")

            self.assertEqual(
                style_keys,
                ("ノーマル", "ヌルテカ", "bgk", "mcp", "qwq", "jir", "iwn"),
            )
            self.assertEqual(rules["mcp"].hr_upscaler, "R-ESRGAN 4x+")
            self.assertEqual(rules["bgk"].hr_upscaler, "Latent (antialiased)")
            self.assertEqual(rules["qwq"].hr_upscaler, "Latent (antialiased)")
            self.assertEqual(rules["jir"].hr_upscaler, "Lanczos")
            self.assertEqual(rules["iwn"].hr_upscaler, "Lanczos")
            self.assertEqual(library.get_request(request_id).style_key, "bgk")

    def test_catalog_sync_fills_empty_rule_without_overwriting_custom_rule(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "0_SDXL Style Prompt.txt"
            catalog.write_text(
                "●超ジロー Hires. fix Lanczos (jir)\nstyle prompt,\n\n"
                "●Artist style (qwq)\nstyle prompt,\n",
                encoding="utf-8",
            )
            library = PromptLibrary(root / "library.sqlite3")
            library.initialize()
            library.set_style_rule("jir", {})
            library.set_style_rule("qwq", {"hr_upscaler": "R-ESRGAN 4x+"})

            library.sync_style_prompt_catalog(catalog)
            rules = {rule.style_key: rule for rule in library.list_style_rules()}

            self.assertEqual(rules["jir"].hr_upscaler, "Lanczos")
            self.assertEqual(rules["qwq"].hr_upscaler, "R-ESRGAN 4x+")

    def test_imports_legacy_text_and_resolves_style_upscalers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prompt_path = root / "prompts.txt"
            prompt_path.write_text(
                "・aaa：ノーマル\nprompt a\n\n"
                "・bbb：ヌルテカ\nprompt b\n\n"
                "・ccc：mcp\nprompt c\n\n"
                "・ddd：iwn\nprompt d\n\n"
                "・eee：ata\nprompt e\n",
                encoding="utf-8",
            )
            library = PromptLibrary(root / "library.sqlite3")

            _collection_id, count = library.import_text_file(prompt_path)
            jobs = library.list_jobs()

            self.assertEqual(count, 5)
            self.assertEqual(
                [job.style_key for job in jobs],
                ["ノーマル", "ヌルテカ", "mcp", "iwn", "ata"],
            )
            self.assertEqual(
                [job.effective_upscaler for job in jobs],
                [
                    "Latent (antialiased)",
                    "Latent (antialiased)",
                    "Latent (antialiased)",
                    "Lanczos",
                    "Lanczos",
                ],
            )

    def test_migrates_only_empty_or_legacy_default_style_rules(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "library.sqlite3"
            library = PromptLibrary(path)
            library.initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "DELETE FROM metadata WHERE key = 'style_rule_defaults_version'"
                )
                connection.execute(
                    """
                    UPDATE style_rules SET settings_override_json = ?
                    WHERE style_key = 'ヌルテカ'
                    """,
                    (json.dumps({"hr_upscaler": "Lanczos"}),),
                )
                connection.execute(
                    """
                    UPDATE style_rules SET settings_override_json = '{}'
                    WHERE style_key = 'iwn'
                    """
                )
                connection.execute(
                    """
                    UPDATE style_rules SET settings_override_json = ?
                    WHERE style_key = 'mcp'
                    """,
                    (json.dumps({"hr_upscaler": "R-ESRGAN 4x+"}),),
                )
                connection.commit()

            rules = {rule.style_key: rule for rule in library.list_style_rules()}

            self.assertEqual(rules["ヌルテカ"].hr_upscaler, "Latent (antialiased)")
            self.assertEqual(rules["iwn"].hr_upscaler, "Lanczos")
            self.assertEqual(rules["mcp"].hr_upscaler, "R-ESRGAN 4x+")

    def test_job_override_wins_over_style_rule_and_base_payload(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prompt_set = root / "set.json"
            prompt_set.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "collection": "test",
                        "jobs": [
                            {
                                "title": "bbb",
                                "style": "ヌルテカ",
                                "prompt": "prompt b",
                                "settings_override": {
                                    "hr_upscaler": "R-ESRGAN 4x+",
                                    "override_settings": {"sd_vae": "Custom VAE"},
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            library = PromptLibrary(root / "library.sqlite3")
            library.import_prompt_set(prompt_set)
            job = library.load_generation_jobs([library.list_jobs()[0].id])[0]
            args = _build_args()

            payload = build_payload(
                job,
                args,
                {
                    "hr_upscaler": "Latent (antialiased)",
                    "override_settings": {
                        "sd_model_checkpoint": "model.safetensors",
                        "sd_vae": "Automatic",
                    },
                },
            )

            self.assertEqual(payload["hr_upscaler"], "R-ESRGAN 4x+")
            self.assertEqual(
                payload["override_settings"]["sd_model_checkpoint"],
                "model.safetensors",
            )
            self.assertEqual(payload["override_settings"]["sd_vae"], "Custom VAE")

    def test_updates_job_and_keeps_unknown_styles_inheriting_common_payload(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prompt_path = root / "prompts.txt"
            prompt_path.write_text("・aaa：未登録\nprompt a\n", encoding="utf-8")
            library = PromptLibrary(root / "library.sqlite3")
            library.import_text_file(prompt_path)
            record = library.list_jobs()[0]

            self.assertEqual(record.effective_settings, {})
            library.update_job(
                record.id,
                title=record.title,
                prompt=record.prompt,
                style_key=record.style_key,
                status="ready",
                enabled=True,
                settings_override={"hr_upscaler": "Lanczos"},
                notes="checked",
            )
            updated = library.get_job(record.id)

            self.assertEqual(updated.status, "ready")
            self.assertEqual(updated.effective_upscaler, "Lanczos")
            self.assertEqual(updated.notes, "checked")

    def test_extracts_editable_request_candidates_without_dropping_raw_content(self):
        candidates = extract_request_candidates(
            "神成きゅぴ　ノーマル　騎乗位　釘打ちピストン\n補足：アヘ顔中出し",
            ["ノーマル", "ヌルテカ", "mcp"],
        )

        self.assertEqual(candidates["characters_text"], "神成きゅぴ")
        self.assertEqual(candidates["style_key"], "ノーマル")
        self.assertIn("騎乗位", candidates["instructions_text"])
        self.assertEqual(candidates["notes"], "アヘ顔中出し")

    def test_character_candidate_does_not_absorb_instructions_before_trailing_style(self):
        raw_text = "ぶいすぽ　盛り体型　対面座位　乳吸い　中出し　bgk"

        candidates = extract_request_candidates(
            raw_text,
            ["ノーマル", "ヌルテカ", "bgk", "mcp", "qwq"],
        )

        self.assertEqual(candidates["characters_text"], "ぶいすぽ")
        self.assertEqual(candidates["style_key"], "bgk")
        self.assertEqual(candidates["instructions_text"], raw_text)

    def test_short_style_code_must_be_a_separate_token(self):
        candidates = extract_request_candidates(
            "test character data composition",
            ["ata"],
        )

        self.assertEqual(candidates["style_key"], "")
        self.assertEqual(candidates["characters_text"], "")

    def test_request_inbox_round_trip_and_request_set_export(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            library = PromptLibrary(root / "library.sqlite3")
            request_id = library.create_request(
                "キャラ：テストキャラ\n絵柄：ヌルテカ\n構図：テスト構図",
                source="manual-copy",
                source_reference="request-001",
                received_at="2026-08-14T21:05:51+09:00",
            )
            created = library.get_request(request_id)

            self.assertEqual(created.characters_text, "テストキャラ")
            self.assertEqual(created.style_key, "ヌルテカ")
            self.assertEqual(created.instructions_text, "テスト構図")

            library.update_request(
                request_id,
                source=created.source,
                source_reference=created.source_reference,
                received_at=created.received_at,
                raw_text=created.raw_text,
                characters_text=created.characters_text,
                style_key=created.style_key,
                instructions_text=created.instructions_text,
                status="ready_for_prompt",
                notes="checked",
            )
            destination = root / "20260814_RequestSet.json"
            count = library.export_request_set(destination, [request_id])
            payload = json.loads(destination.read_text(encoding="utf-8"))

            self.assertEqual(count, 1)
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["requests"][0]["request_id"], request_id)
            self.assertEqual(payload["requests"][0]["style"], "ヌルテカ")
            self.assertEqual(library.get_request(request_id).status, "ready_for_prompt")

    def test_request_set_export_sorts_requests_by_id_ascending(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            library = PromptLibrary(root / "library.sqlite3")
            request_ids = [
                library.create_request(f"依頼 {number}")
                for number in range(1, 4)
            ]
            destination = root / "20260815_RequestSet.json"

            library.export_request_set(
                destination,
                [request_ids[2], request_ids[0], request_ids[1]],
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))

            self.assertEqual(
                [item["request_id"] for item in payload["requests"]],
                request_ids,
            )

    def test_prompt_set_links_back_to_request_and_updates_status(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            library = PromptLibrary(root / "library.sqlite3")
            request_id = library.create_request("キャラ：test\n絵柄：ノーマル")
            request_set = root / "PromptSet.json"
            request_set.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "collection": "linked",
                        "jobs": [
                            {
                                "title": "test：ノーマル",
                                "style": "ノーマル",
                                "prompt": "prompt,",
                                "source_request_id": request_id,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            library.import_prompt_set(request_set)
            job = library.list_jobs()[0]

            self.assertEqual(job.request_id, request_id)
            self.assertEqual(job.source_request_id, request_id)
            self.assertEqual(library.get_request(request_id).status, "prompt_generated")

    def test_open_prompt_set_updates_in_place_and_preserves_local_fields(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prompt_set = root / "PromptSet.json"
            prompt_set.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "collection": "linked-document",
                        "jobs": [
                            {
                                "order": 2,
                                "source_request_id": 202,
                                "title": "second",
                                "style": "bgk",
                                "prompt": "prompt second",
                            },
                            {
                                "order": 1,
                                "source_request_id": 201,
                                "title": "first",
                                "style": "ata",
                                "prompt": "prompt first",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            library = PromptLibrary(root / "library.sqlite3")

            first_result = library.open_prompt_set(prompt_set)
            first_jobs = library.list_jobs()
            self.assertTrue(first_result.created)
            self.assertEqual([job.title for job in first_jobs], ["first", "second"])
            self.assertEqual(first_jobs[0].source_request_id, 201)
            self.assertIsNone(first_jobs[0].request_id)
            library.update_job(
                first_jobs[0].id,
                title=first_jobs[0].title,
                prompt=first_jobs[0].prompt,
                style_key=first_jobs[0].style_key,
                status="ready",
                enabled=False,
                settings_override=first_jobs[0].settings_override,
                notes="local review",
            )

            prompt_set.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "collection": "linked-document",
                        "jobs": [
                            {
                                "order": 1,
                                "source_request_id": 201,
                                "title": "first updated",
                                "style": "ata",
                                "prompt": "prompt first updated",
                            },
                            {
                                "order": 3,
                                "source_request_id": 203,
                                "title": "third",
                                "style": "mcp",
                                "prompt": "prompt third",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            second_result = library.open_prompt_set(prompt_set)
            updated_jobs = library.list_jobs()

            self.assertFalse(second_result.created)
            self.assertEqual(second_result.collection_id, first_result.collection_id)
            self.assertEqual(second_result.added, 1)
            self.assertEqual(second_result.updated, 1)
            self.assertEqual(second_result.removed, 1)
            self.assertEqual(len(library.list_collections()), 1)
            self.assertEqual([job.title for job in updated_jobs], ["first updated", "third"])
            self.assertEqual(updated_jobs[0].status, "ready")
            self.assertFalse(updated_jobs[0].enabled)
            self.assertEqual(updated_jobs[0].notes, "local review")

    def test_add_prompt_set_creates_an_independent_copy(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prompt_set = root / "PromptSet.json"
            prompt_set.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "collection": "copy-test",
                        "jobs": [{"title": "one", "prompt": "prompt one"}],
                    }
                ),
                encoding="utf-8",
            )
            library = PromptLibrary(root / "library.sqlite3")

            library.open_prompt_set(prompt_set)
            copied_id, copied_count = library.import_prompt_set(
                prompt_set, as_copy=True
            )
            collections = library.list_collections()

            self.assertEqual(copied_count, 1)
            self.assertEqual(len(collections), 2)
            copied = library.get_collection(copied_id)
            self.assertEqual(copied.source_kind, "promptset-copy")
            self.assertEqual(len(library.list_jobs()), 2)

    def test_prompt_set_save_preserves_unknown_fields_and_portable_settings(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            prompt_set = root / "PromptSet.json"
            prompt_set.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "collection": "save-test",
                        "producer": {"name": "codex"},
                        "jobs": [
                            {
                                "order": 1,
                                "source_request_id": 501,
                                "title": "editable",
                                "style": "ata",
                                "prompt": "before",
                                "custom_field": "keep-me",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            library = PromptLibrary(root / "library.sqlite3")
            result = library.open_prompt_set(prompt_set)
            job = library.list_jobs()[0]
            library.update_job(
                job.id,
                title="edited",
                prompt="after",
                style_key="ata",
                status="ready",
                enabled=False,
                settings_override={},
                notes="reviewed",
            )
            self.assertTrue(library.get_collection(result.collection_id).json_dirty)

            library.export_prompt_set(result.collection_id, prompt_set)
            self.assertFalse(library.get_collection(result.collection_id).json_dirty)
            saved = json.loads(prompt_set.read_text(encoding="utf-8"))
            portable_path = root / "Ready" / "PromptSet.json"
            library.export_prompt_set(
                result.collection_id,
                portable_path,
                portable=True,
                create_backup=False,
            )
            portable = json.loads(portable_path.read_text(encoding="utf-8"))

            self.assertEqual(saved["producer"], {"name": "codex"})
            self.assertEqual(saved["jobs"][0]["custom_field"], "keep-me")
            self.assertEqual(saved["jobs"][0]["title"], "edited")
            self.assertEqual(saved["jobs"][0]["prompt"], "after")
            self.assertEqual(saved["jobs"][0]["source_request_id"], 501)
            self.assertEqual(saved["jobs"][0]["status"], "ready")
            self.assertFalse(saved["jobs"][0]["enabled"])
            self.assertEqual(saved["jobs"][0]["notes"], "reviewed")
            self.assertNotIn("settings_override", saved["jobs"][0])
            self.assertEqual(
                portable["jobs"][0]["settings_override"]["hr_upscaler"],
                "Lanczos",
            )
            backups = list((root / ".promptset_backups").glob("PromptSet.*.json"))
            self.assertEqual(len(backups), 1)

    def test_initialization_migrates_version_one_database(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "library.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.executescript(
                    """
                    CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE collections(
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        source_path TEXT NOT NULL DEFAULT '',
                        source_kind TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE style_rules(
                        style_key TEXT PRIMARY KEY COLLATE NOCASE,
                        settings_override_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE prompt_jobs(
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
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO metadata(key, value) VALUES('schema_version', '1');
                    """
                )

            library = PromptLibrary(database)
            library.initialize()
            with closing(sqlite3.connect(database)) as connection:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(prompt_jobs)")
                }
                collection_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(collections)")
                }
                request_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='requests'"
                ).fetchone()

            self.assertIn("request_id", columns)
            self.assertIn("source_request_id", columns)
            self.assertIn("json_dirty", collection_columns)
            self.assertIsNotNone(request_table)


def _build_args():
    class Args:
        batch_count = None
        batch_size = None
        no_save_images = False
        send_images = False
        negative_prompt = None
        sampler_name = None
        scheduler = None
        steps = None
        cfg_scale = None
        width = None
        height = None
        seed = None
        no_sanitize_subdir = False

    return Args()


if __name__ == "__main__":
    unittest.main()
