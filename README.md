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
