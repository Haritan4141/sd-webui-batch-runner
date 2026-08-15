# SD WebUI Batch Runner

メモ帳に書いた `・タイトル` 区切りのプロンプトを読み取り、AUTOMATIC1111 Stable Diffusion WebUI API の `txt2img` に順番に送信するCLIです。

## 前提

Stable Diffusion WebUIをAPI有効で起動してください。

```powershell
.\webui-user.bat --api
```

## メモ帳形式

プロンプト開始行は固定文字列ではありません。各 `・タイトル` 行の次から、次の `・タイトル` またはファイル末尾までをそのままプロンプトとして扱います。

```text
・タイトル1：A
AAAA,
masterpiece, best quality, amazing quality,

・タイトル2：B
BBBB,
masterpiece, best quality, amazing quality,
```

出力サブディレクトリには、`・` を除いたタイトルを使います。

## 実行例

GUIを起動します。

```powershell
python -m sd_webui_batch.gui
```

Windowsでは `run_gui.bat` をダブルクリックして起動することもできます。

GUIでは、プロンプトファイル、Payload JSON、WebUI URL、生成枚数、Batch Size、基本生成設定、Hires. fix、Checkpoint / VAE / Clip Skip、先頭N件だけ実行、Dry Run、生成開始、WebUI Interrupt / Skipを操作できます。

大量生成は1回のAPIリクエストへ最大100枚ずつ自動分割します。たとえば生成枚数6000、Batch Size 1なら100枚×60回です。GUIにはジョブ番号、送信番号、確定完了枚数、WebUIの現在進捗、現在送信のETAを表示します。

`Dry Run` は画像生成せず、WebUIへ送る予定のpayloadをログに表示します。
`Payload JSON` の `保存` は、GUI上の設定をJSONファイルへ書き戻します。
大量のRunner Dynamic Promptsを使うDry Runは、展開とManifest作成をバックグラウンドで行います。準備中もGUIは応答し、状態欄に進捗を表示します。GUIログのリクエスト一覧は先頭・末尾の要約表示にしますが、展開した全件は従来どおりManifest JSONへ保存されます。

Forge Neo高速版は `run_forge_neo_gui.bat` から起動します。WebUI URLは `http://127.0.0.1:7861`、Payloadは `examples/payload_forge_neo.json`、Runner Dynamic Promptsは有効で起動します。ワイルドカードは `C:\[wildcards]\wildcards` またはメインPCのStabilityMatrix配下を自動選択し、Manifest先もZドライブがないPCではユーザーのDocuments配下へ切り替えます。

## SQLiteプロンプトライブラリ（段階導入版）

GUIの `SQLite管理` から、受け取った依頼、生成済みプロンプト、タイトル別設定を1つのSQLite DBで管理できます。従来のtxt入力とCLIはそのまま利用できます。

おすすめの新しい手順:

1. `run_gui.bat` または `python -m sd_webui_batch.gui` でGUIを起動する。
2. `Payload JSON` に普段使う共通設定を読み込む。
3. `SQLite管理` を開く。
4. 最初に開く `依頼Inbox` タブで、受け取った文章をコピーして `クリップボードから新規` を押す。手入力する場合は `新規` を使う。
5. 自動で入ったキャラクター、絵柄、生成指示を確認する。必要なら `元文から候補抽出` を押し、修正後に `編集内容を保存` を押す。受取原文は候補とは別にそのまま保存される。
6. プロンプト化する依頼を `選択を生成待ち` にし、必要な状態フィルターへ切り替えて `表示中をRequestSet JSONへ書出` を押す。全選択は不要で、現在表示中の依頼がすべてID昇順で出力される。既定の保存先は `Documents/sd-webui-prompt-codex-generate`、出力名は `YYYYMMDD_RequestSet.json`。
7. RequestSet JSONは依頼IDの昇順で書き出される。そのJSONを `sd-webui-prompt-codex-generate` に渡す。生成側は確認用txtと `YYYYMMDD_PromptSet.json` を作成し、各プロンプトへ元の依頼IDを引き継ぐ。
8. `PromptSetを開く` で結果を戻す。初回だけSQLiteへ作成され、同じJSONを開き直した場合は同じ取込単位が更新される。紐づいた依頼は `プロンプト生成済み` になる。
9. プロンプト、絵柄、状態、生成対象、個別Upscalerを確認し、各項目の `編集内容を保存` を押す。編集が終わったら `JSONへ上書き保存` を押し、PromptSet JSON本体へ反映する。
10. `全選択` またはCtrl／Shift選択で生成対象を選び、`選択項目をバッチへ` を押す。確認済みをまとめて管理する場合は `選択を生成準備済み` にしてから `表示中の生成準備済みをバッチへ` を押す。このボタンは非表示の過去セットを対象にしない。
11. 元のバッチGUIで適用Upscalerを確認し、最初は `Dry Run` を実行する。ログの最終payloadを確認してから `生成開始` を押す。

依頼の状態は `受付`、`確認済み`、`プロンプト生成待ち`、`プロンプト生成済み`、`完了` です。画面上の状態はすべて日本語で表示されます。依頼Inboxは通常 `未完了` だけを表示し、`状態表示` から状態別または `すべて` へ切り替えられます。画像生成・確認まで終わった依頼は選択して `選択を完了` を押すと通常一覧から隠れ、`完了` フィルターで再表示できます。誤登録や重複以外は削除せず、履歴としてSQLiteへ残す運用を推奨します。

既存プロンプトから始める場合は、`txt取込` または `PromptSetを開く` を使います。依頼Inboxを経由せず、そのまま `プロンプト` タブで確認してバッチへ渡せます。比較用・別バージョンとしてSQLiteだけへ複製したい場合は `PromptSetを追加` を使います。追加したコピーは元JSONを誤って上書きしないため、JSON化するときは `名前を付けて保存` を使用します。

ライブラリDBの既定保存先は `data_local/prompt_library.sqlite3` です。`data_local/` はGit管理対象外です。DBファイルには重要なプロンプトが入るため、必要に応じて別ドライブへバックアップしてください。

初期の絵柄別Upscaler規則:

- `iwn` / `ata`: `Lanczos`
- `ノーマル` / `ヌルテカ` / `bgk` / `mcp` / `qwq` / `lil` / `kak`: `Latent (antialiased)`

`絵柄別Upscaler規則` から値を変更できます。未登録の絵柄は共通Payloadを継承します。1タイトルだけ変えたい場合は `個別Upscaler` を指定します。空欄へ戻すと、絵柄規則または共通Payloadの継承に戻ります。

絵柄プルダウンは、同じDocumentsフォルダにある `sd-webui-prompt-codex-generate/0_SDXL Style Prompt.txt` とGUI再読込時に同期します。`ノーマル`、`ヌルテカ` はそのまま表示し、それ以外は見出し末尾の括弧内コード（例: `bgk`、`mcp`）を表示します。同期で既存のUpscaler規則を上書きすることはありません。新規絵柄は規則を設定するまで共通Payloadを継承します。

設定の優先順位:

```text
GUIの共通Payload → 絵柄別規則 → タイトル個別設定 → runner強制設定
```

runner強制設定は、出力ディレクトリ、グリッド無効化、設定復元など従来の安全設定です。

生成側が `PromptSet JSON` を出力した場合は、SQLite管理画面の `PromptSetを開く` から読み込めます。JSONにある `order`、`source_request_id`、タイトル、絵柄、Prompt、個別設定を維持し、GUIからJSON本体を上書きまたは別名保存できます。上書き前のファイルは同じフォルダの `.promptset_backups` へ自動保存されます。現在のtxtも引き続き取り込めます。

`PromptSetを開く` または `PromptSetを追加` の直後は、`開いているPromptSetのみ表示` が有効になり、そのセットの件数だけを一覧表示します。SQLite内の過去セットも確認したい場合はチェックを外してください。件数表示は `表示件数 / DB全件数` の順です。

### 別PCへPromptSetを渡す

メインPCで編集後、`配布用PromptSetを書き出し` を押すと、絵柄別規則と個別設定を解決した設定が各ジョブの `settings_override` へ埋め込まれます。既定の保存候補はプロジェクト内の `sd-webui-batch-runner/SD-PromptSets` です。このフォルダはGit管理対象外です。メインPCの `Users` 共有を使う場合、9700X／14100Fからは次のUNCフォルダを開けます。

```text
\\DESKTOP-5NPLIIV\Users\Haritan\Documents\sd-webui-batch-runner\SD-PromptSets
```

別PCでは共有上の配布JSONを `PromptSetを開く` で読み込みます。この時点で内容はそのPCのローカルSQLiteへ保存されます。さらに `選択項目をバッチへ` または `表示中の生成準備済みをバッチへ` を押すと、生成ジョブがローカルSQLiteからバッチGUIのメモリへ読み込まれます。その後に共有やメインPCが切断されても生成処理には影響しません。共有JSONへの上書き保存や再読込には接続が必要です。実行状態や履歴がPC間で混ざらないよう、`prompt_library.sqlite3` 自体は共有せず、PromptSet JSONだけを受け渡してください。同じ配布JSONを更新後に開き直すと、そのPCでも重複追加せず同じ取込単位を更新します。

CLIで確認する場合:

まずはAPIに送らず、解析結果とpayloadだけ確認します。

```powershell
python -m sd_webui_batch.cli .\examples\prompts.txt --dry-run
```

実際に生成します。生成枚数は `payload_json` の `n_iter` を使います。未指定の場合は1です。

```powershell
python -m sd_webui_batch.cli .\examples\prompts.txt
```

WebUIの生成設定を指定する場合は、JSONを渡します。

```powershell
python -m sd_webui_batch.cli .\examples\prompts.txt --payload-json .\examples\payload.json
```

Hires. fixを使う場合:

```powershell
python -m sd_webui_batch.cli .\examples\prompts.txt --payload-json .\examples\payload_hires.json
```

一時的に数件だけ試す場合:

```powershell
python -m sd_webui_batch.cli .\examples\prompts.txt --limit 1 --batch-count 2
```

## 送信内容

各ジョブでは主に以下を送信します。

- `prompt`: タイトル下の本文
- `n_iter`: 生成枚数、WebUIのBatch Countに対応。未指定時は1
- `batch_size`: デフォルト1
- `save_images`: デフォルトtrue
- `send_images`: デフォルトfalse
- `override_settings.save_to_dirs`: true
- `override_settings.directories_filename_pattern`: `・` を除いたタイトル
- `override_settings.return_grid`: 常にfalse
- `override_settings.grid_save`: 常にfalse

サブディレクトリ名はWindowsで使えない文字だけ `_` に置換します。完全にタイトルをそのまま使いたい場合は `--no-sanitize-subdir` を付けてください。

JSONには標準のコメント構文がないため、説明は `_comment_n_iter` のようなキーで書きます。`_comment` で始まるキーはWebUIへ送信する前に削除されます。

## 大量生成・自動分割・グリッド無効化

`n_iter × batch_size` が100枚を超える場合、ランナーが複数の `txt2img` リクエストへ自動分割します。GUIの生成枚数やpayloadの `n_iter` は総Batch Countのままなので、6000から100へ書き換える必要はありません。

- GUI: 1リクエスト最大100枚で固定
- CLI: 既定100枚。`--chunk-size` で1〜100枚の範囲に調整可能（100枚を超える指定は拒否）
- 固定Seed: 分割後も同じ画像を繰り返さないよう、完了画像数に合わせて自動補正
- ランダムSeed (`-1` / 未指定): ジョブ開始時に一度だけ確定し、分割前と同じ連番を維持
- グリッド: ランナーからの全リクエストで `return_grid=false` と `grid_save=false` を強制
- WebUI本体のグローバル設定: 変更しない

グリッド設定はリクエスト中だけ上書きされ、個別画像の保存は従来どおり行われます。payloadにグリッド有効設定が書かれていても、ランナー実行時は無効になります。

```powershell
python -m sd_webui_batch.cli .\examples\prompts.txt --payload-json .\examples\payload.json --chunk-size 100
```

HTTPエラーになった送信は自動再試行せず、そのプロンプトの残り送信を飛ばします。「エラーで停止」が無効なら次のプロンプトへ進みます。タイムアウトや接続切断は、WebUI側が生成を継続中か判断できないため、重複送信を避けて全体を停止します。

GUIの `WebUI Skip` を使った送信は、WebUIが途中まで何枚保存したかAPI応答だけでは確定できません。その送信は確定枚数へ加算せず「保存枚数不明」と表示し、次の最大100枚送信から処理を続けます。`WebUI Interrupt` は後続送信も停止します。

## Hires. fix設定

`payload.json` にはHires. fixの設定も書けます。

```json
{
  "n_iter": 1,
  "_comment_n_iter": "生成枚数（Stable Diffusion WebUIのBatch Countに対応）",
  "batch_size": 1,
  "enable_hr": true,
  "hr_upscaler": "Latent (antialiased)",
  "hr_scale": 1.5,
  "hr_second_pass_steps": 20,
  "denoising_strength": 0.4,
  "hr_cfg_scale": 5.5,
  "hr_rescale_cfg": 0.0,
  "hr_resize_x": 0,
  "hr_resize_y": 0
}
```

画面の主な項目との対応は以下です。

- `Hires. fix`: `enable_hr`
- `Upscaler`: `hr_upscaler`
- `Upscale by`: `hr_scale`
- `Hires steps`: `hr_second_pass_steps`
- `Denoising strength`: `denoising_strength`
- `Hires CFG Scale`: `hr_cfg_scale`
- `Hires Rescale CFG`: `hr_rescale_cfg`
- `Resize width to`: `hr_resize_x`
- `Resize height to`: `hr_resize_y`
- `Stable Diffusion Checkpoint`: `override_settings.sd_model_checkpoint`
- `SD VAE`: `override_settings.sd_vae`
- `Clip Skip`: `override_settings.CLIP_stop_at_last_layers`

`Batch Count` は `n_iter` として指定します。CLIの `--batch-count` を指定した場合だけ、JSONの `n_iter` より優先されます。
