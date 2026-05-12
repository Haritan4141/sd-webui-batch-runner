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
・タイトル1：ノーマル
AAAA,
masterpiece, best quality, amazing quality,

・タイトル2：ヌルテカ
BBBB,
masterpiece, best quality, amazing quality,
```

出力サブディレクトリには、`・` を除いたタイトルを使います。

## 実行例

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

サブディレクトリ名はWindowsで使えない文字だけ `_` に置換します。完全にタイトルをそのまま使いたい場合は `--no-sanitize-subdir` を付けてください。

JSONには標準のコメント構文がないため、説明は `_comment_n_iter` のようなキーで書きます。`_comment` で始まるキーはWebUIへ送信する前に削除されます。

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
