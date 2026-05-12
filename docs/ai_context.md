# AI Context

## プロジェクト概要

このプロジェクトは、メモ帳形式で管理している Stable Diffusion WebUI 用プロンプトを読み取り、AUTOMATIC1111 / Forge 系 WebUI の `txt2img` API に順番に送信するローカル自動化ツールである。

プロジェクト名:

- `sd-webui-batch-runner`

GitHub リポジトリ:

- https://github.com/Haritan4141/sd-webui-batch-runner

Git 状態:

- `main` ブランチを `origin/main` に push 済み。
- 初回コミット: `60a7ebe Initial CLI batch runner`

現時点では CLI、内部処理、初期GUIを実装済み。次の作業は GUI の実機運用確認と必要な改善。

主な技術スタック:

- Python 3
- Python 標準ライブラリのみ
  - `argparse`
  - `json`
  - `tkinter`
  - `urllib.request`
  - `unittest`
- Stable Diffusion WebUI API
  - `POST /sdapi/v1/txt2img`
  - `GET /openapi.json`
  - `GET /sdapi/v1/upscalers`

起動・実行例:

```powershell
python -m sd_webui_batch.cli .\examples\prompts.txt --payload-json .\examples\payload.json --dry-run --limit 1
python -m sd_webui_batch.cli .\examples\prompts.txt --payload-json .\examples\payload.json
python -m sd_webui_batch.cli .\examples\prompts.txt --payload-json .\examples\payload_hires.json --limit 1 --batch-count 1
python -m sd_webui_batch.gui
.\run_gui.bat
```

テスト実行:

```powershell
python -m unittest discover -s tests
```

Stable Diffusion WebUI 側は API 有効で起動する必要がある。

```powershell
.\webui-user.bat --api
```

## 現在の作業目的

今回の依頼は GUI 化を進めること。

最終的に達成したい状態:

- CLI と同じ内部処理を使うGUIから、プロンプト解析、payload確認、生成実行を行える。
- GUIで生成枚数 `n_iter` を「生成枚数」として扱い、WebUI Batch Count 相当であることを分かりやすくする。
- 次回以降はGUIの実運用確認と改善を継続できる。

変更対象の範囲:

- `sd_webui_batch/gui.py`
- `sd_webui_batch/client.py`
- `README.md`
- `docs/ai_context.md`

## これまでに実施した作業

追加・変更済みファイル:

- `sd_webui_batch/__init__.py`
  - パッケージ初期化。
- `sd_webui_batch/parser.py`
  - メモ帳形式のパーサ。
  - `・タイトル` 行でジョブを分割。
  - タイトル行の次から、次の `・タイトル` またはファイル末尾までをプロンプト本文として扱う。
  - `prompt,` などの固定開始文字は前提にしない。
  - `~~~~~~` は終端記号ではなく、もし存在すれば通常のプロンプト文字列として扱う。
  - 読み込みエンコーディングは `utf-8-sig`, `utf-16`, `cp932` を試す。
- `sd_webui_batch/client.py`
  - Stable Diffusion WebUI API クライアント。
  - `txt2img` 送信、進捗取得、Basic 認証対応。
  - GUI 用に `/sdapi/v1/interrupt` と `/sdapi/v1/skip` 送信を追加。
- `sd_webui_batch/cli.py`
  - CLI 本体。
  - `--dry-run` で送信予定 payload を表示し、実際には生成しない。
  - `--limit N` で先頭 N 件だけ処理。
  - `payload_json` を共通生成設定として読み込む。
  - `_comment` で始まる JSON キーは WebUI 送信前に削除する。
  - `n_iter` は生成枚数、WebUI の Batch Count に対応。
  - `payload_json` に `n_iter` があれば使用し、なければデフォルト 1。
  - CLI の `--batch-count` が指定された場合だけ `payload_json` の `n_iter` を上書きする。
  - タイトルから `・` を除いた文字列を `override_settings.directories_filename_pattern` に設定する。
  - `save_to_dirs` を有効化する。
  - Hires. fix 有効時、Forge 系 API 互換のため `hr_cfg_scale` / `hr_rescale_cfg` を未指定なら補完する。
- `sd_webui_batch/gui.py`
  - `tkinter` ベースのGUI。
  - `python -m sd_webui_batch.gui` で起動。
  - プロンプトファイル選択、Payload JSON選択・保存、WebUI URL、生成枚数、Batch Size、基本生成設定、Hires. fix、Checkpoint / VAE / Clip Skip、先頭N件、Dry Run、生成開始、Interrupt / Skip を操作できる。
  - 長時間生成でUIが固まらないよう、生成処理はバックグラウンドスレッドで実行する。
  - payload合成は `cli.build_payload` を再利用し、CLIとGUIでルールを分岐させない。
- `examples/prompts.txt`
  - メモ帳形式サンプル。
- `examples/payload.json`
  - 通常生成用の共通設定サンプル。
  - `n_iter` と `_comment_n_iter` を含む。
- `examples/payload_hires.json`
  - Hires. fix 用の共通設定サンプル。
  - `enable_hr`, `hr_upscaler`, `hr_scale`, `hr_second_pass_steps`, `denoising_strength`, `hr_cfg_scale`, `hr_rescale_cfg` などを含む。
- `tests/test_parser.py`
  - 可変プロンプト開始行、タイトル分割、`~~~~~~` の通常文字列扱い、サブディレクトリ名サニタイズを検証。
- `tests/test_cli.py`
  - `n_iter` の優先順位、デフォルト、`_comment` キー削除、Hires 互換デフォルトを検証。
- `README.md`
  - CLI の使い方、メモ帳形式、Hires. fix 設定、`n_iter` の意味を記載。

調査して分かったこと:

- AUTOMATIC1111 / Forge 系 WebUI の `txt2img` API では、WebUI の Batch Count は `n_iter` に対応する。
- `batch_size` は WebUI の Batch Size に対応する。
- Hires. fix は API payload に `enable_hr: true` と Hires 系パラメータを入れる。
- ユーザー環境の WebUI API スキーマには `hr_cfg_scale` と `hr_rescale_cfg` が存在する。
- Hires. fix 実行時にこれらが未指定だと `HTTP 500` / `must be real number, not NoneType` が発生した。
- `hr_cfg_scale` と `hr_rescale_cfg` を数値で送ることで最小 Hires API リクエストの HTTP 500 は解消した。

採用した方針:

- ブラウザ自動操作ではなく WebUI API を直接呼ぶ。
- GUI 化前に CLI と内部処理を安定させる。
- 設定は `payload_json` をプリセットとして扱い、GUI からも同じ設定を読み書きできるようにする。
- JSON にはコメントを書けないため、説明は `_comment_*` キーとして持ち、送信前に削除する。
- CLI 引数は明示指定された場合だけ `payload_json` を上書きする。

## 未完了タスク

残っている作業:

- GUI の実機運用確認。
- ユーザーの実際の生成フローで不足しているGUI項目の洗い出し。
- GUI からの長時間・大量ジョブ実行時の操作性確認。
- 実行中の進捗表示を WebUI の `/sdapi/v1/progress` から細かく取るか検討。
- 生成結果フォルダをGUIから開く導線を追加するか検討。

次に確認すべきこと:

- 初期GUIは依存を増やさない `tkinter` を採用済み。
- WebUI API の進捗取得を GUI にどう表示するか。
  - `GET /sdapi/v1/progress?skip_current_image=true` を使う候補。
- `/sdapi/v1/interrupt` と `/sdapi/v1/skip` の送信ボタンは実装済み。実生成中の挙動は追加確認が必要。
- `payload_json` 編集画面で JSON 構文エラーをどう表示するか。
- 生成後の保存先を GUI で表示・開けるようにするか。

既知の問題・注意:

- GitHub リポジトリは `https://github.com/Haritan4141/sd-webui-batch-runner`。
- `main` ブランチは `origin/main` を追跡している。
- `__pycache__` がテスト実行で生成されることがある。必要がなければ成果物として扱わない。
- `examples/prompts.txt` はユーザーが動作確認用に書き換えている可能性がある。勝手に上書きしない。
- `payload.json` / `payload_hires.json` の `n_iter` はユーザーが 1 または 2 などに調整する可能性がある。不要に戻さない。

## 動作確認・検証状況

実行済みコマンド:

```powershell
python -m unittest discover -s tests
python -m sd_webui_batch.cli .\examples\prompts.txt --dry-run --limit 1
python -m sd_webui_batch.cli .\examples\prompts.txt --payload-json .\examples\payload.json --dry-run --limit 1
python -m sd_webui_batch.cli .\examples\prompts.txt --payload-json .\examples\payload.json --dry-run --limit 1 --batch-count 3
python -m sd_webui_batch.cli .\examples\prompts.txt --payload-json .\examples\payload_hires.json --dry-run --limit 1
python -m py_compile sd_webui_batch\gui.py sd_webui_batch\client.py
python -c "import sd_webui_batch.gui; print('gui import ok')"
python -c "import tkinter as tk; from sd_webui_batch.gui import BatchRunnerApp; root=tk.Tk(); root.withdraw(); BatchRunnerApp(root); root.destroy(); print('gui instantiate ok')"
```

確認できたこと:

- メモ帳形式から複数ジョブを解析できる。
- プロンプト開始文字は固定されていない。
- `AAAA,` / `BBBB,` / 任意の先頭行がプロンプト本文として残る。
- `~~~~~~` は終端扱いされない。
- dry-run は生成せず、送信予定 payload のみ表示する。
- `--limit 1` は先頭 1 件のみ対象にする。
- `payload_json` の `n_iter` が使われる。
- `--batch-count` を指定した場合だけ `payload_json` の `n_iter` を上書きする。
- `_comment` で始まるキーは送信 payload から削除される。
- Hires. fix 用 payload に `hr_cfg_scale` / `hr_rescale_cfg` を含めることで、ユーザー環境の Hires HTTP 500 は解消した。
- ユーザーが `payload_hires.json` での動作確認に成功した。
- GUIモジュールのimportと非表示ウィンドウでの初期化は確認済み。

まだ確認できていないこと:

- GUIからの実際の画像生成。
- 長時間・大量ジョブ実行時の中断、スキップ、再実行。
- API 認証あり環境での動作。
- WebUI 起動前や接続失敗時の GUI 表示。
- 生成結果フォルダを GUI から開く導線。

過去に出たエラー:

```text
HTTP 500 from http://127.0.0.1:7860/sdapi/v1/txt2img:
{"error":"TypeError","detail":"","body":"","errors":"must be real number, not NoneType"}
```

原因:

- Hires. fix 有効時、ユーザー環境の Forge 系 API で `hr_cfg_scale` / `hr_rescale_cfg` が未指定のため `None` が内部の数値処理に渡った。

対応:

- `examples/payload_hires.json` に以下を追加。

```json
"hr_cfg_scale": 5.5,
"hr_rescale_cfg": 0.0
```

- `cli.py` でも Hires. fix 有効時に未指定なら補完する。

## 重要なファイル・ディレクトリ

- `README.md`
  - ユーザー向け使い方。
- `run_gui.bat`
  - Windows向けGUI起動バッチ。プロジェクトルートへ移動して `python -m sd_webui_batch.gui` を実行する。
- `.gitignore`
  - Python キャッシュ、仮想環境、ローカル一時ファイルを Git 管理から除外。
- `docs/ai_context.md`
  - AI エージェント向け引き継ぎ資料。このファイル。
- `sd_webui_batch/`
  - Python パッケージ本体。
- `sd_webui_batch/parser.py`
  - メモ帳形式の解析ロジック。
- `sd_webui_batch/client.py`
  - WebUI API 通信。
- `sd_webui_batch/cli.py`
  - CLI、payload 合成、Hires 互換補完。
- `sd_webui_batch/gui.py`
  - GUI本体。
- `examples/prompts.txt`
  - 動作確認用プロンプトファイル。ユーザーが変更している可能性があるため注意。
- `examples/payload.json`
  - 通常生成設定。
- `examples/payload_hires.json`
  - Hires. fix 生成設定。
- `tests/test_parser.py`
  - パーサのテスト。
- `tests/test_cli.py`
  - CLI payload 合成のテスト。

## 注意事項・制約

- 既存仕様を壊さないこと。
- `・タイトル` の分割仕様を維持すること。
- プロンプト開始文字を固定しないこと。
- `~~~~~~` を終端扱いしないこと。
- `n_iter` は内部名として扱い、ユーザー向けには「生成枚数」「Batch Count 相当」と説明すること。
- `payload_json` の値を尊重すること。
- CLI 引数で明示された値だけ `payload_json` を上書きすること。
- `_comment` で始まるキーは送信前に削除すること。
- Hires. fix では Forge 系互換の `hr_cfg_scale` / `hr_rescale_cfg` を考慮すること。
- 影響範囲が大きい変更は、理由と影響範囲を明確にすること。
- ユーザーが作成・変更したファイルを勝手に上書きしないこと。
- 自分が変更していない差分を勝手に修正・削除しないこと。
- 不明点がある場合は推測で大きく進めず、必要に応じて確認すること。
- `examples/prompts.txt` などユーザーがテスト用に変更しやすいファイルは特に慎重に扱うこと。

## Git 操作に関する厳守事項

危険な Git 操作は絶対に行わないこと。

特に以下は禁止:

- `git reset`
- `git reset --hard`
- `git clean`
- `git checkout -- .`
- `git restore .`
- `git push --force`
- `git push -f`
- `git rebase`
- 履歴を書き換える操作
- ユーザーの許可なくファイルを削除する操作

コミット、ブランチ作成、push、pull、merge、rebase などが必要そうな場合は、実行前に必ずユーザーに確認すること。

既存の変更を勝手に破棄しないこと。

Git 操作を提案する場合は、実行内容とリスクを説明すること。

GitHub リポジトリは `https://github.com/Haritan4141/sd-webui-batch-runner`。ローカル Git 操作を行う場合も、危険操作や履歴を書き換える操作は禁止。

## 運用ルール

- 次回以降の AI エージェントは、作業開始時にまず `/docs/ai_context.md` を読むこと。
- 重要な進捗があったら `/docs/ai_context.md` を随時更新すること。
- 方針変更、重要な実装完了、問題の発見、未完了タスクの追加・解決があった場合は必ず追記すること。
- 作業を中断する前、または一段落したタイミングで、最新状況を反映すること。
- 更新時は既存内容を尊重し、必要な情報を追記・整理すること。
- 既存内容を大きく削除・置換する場合は、事前に理由を説明すること。
- 次回セッションの AI エージェントがこのファイルを最初に読む前提で、簡潔かつ具体的に書くこと。

## 更新履歴

- 2026-05-12
  - 初版作成。
  - CLI 実装済み内容、Hires. fix の Forge 系互換対応、検証状況、GUI 化前の未完了タスク、Git 操作禁止事項を整理。
  - GitHub リポジトリ URL とプロジェクト名 `sd-webui-batch-runner` を追記。
  - ローカル Git 初期化、初回コミット、`origin` 設定、`main` ブランチの GitHub push を完了。
  - `tkinter` ベースの初期GUIを追加。`python -m sd_webui_batch.gui` で起動可能。
  - GUI起動用 `run_gui.bat` を追加。
