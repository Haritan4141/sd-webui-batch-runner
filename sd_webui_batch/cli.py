from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .client import SdWebuiApiError, SdWebuiClient
from .parser import PromptJob, PromptParseError, parse_prompt_note, read_text_file


INVALID_WINDOWS_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        text = read_text_file(args.prompt_file)
        jobs = parse_prompt_note(text)
    except PromptParseError as error:
        print(f"Parse error: {error}", file=sys.stderr)
        return 2

    if args.limit is not None:
        jobs = jobs[: args.limit]

    base_payload = load_payload_json(args.payload_json)

    print(f"Loaded {len(jobs)} job(s) from {args.prompt_file}")
    for job in jobs:
        subdir = get_subdirectory(job, sanitize=not args.no_sanitize_subdir)
        print(f"[{job.index}] {job.title} -> {subdir}")

    if args.dry_run:
        print("\nDry run payload preview:")
        for job in jobs:
            payload = build_payload(job, args, base_payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    client = SdWebuiClient(
        base_url=args.url,
        timeout=None if args.timeout == 0 else args.timeout,
        username=args.username,
        password=args.password,
    )

    failures = 0
    for number, job in enumerate(jobs, start=1):
        payload = build_payload(job, args, base_payload)
        subdir = payload["override_settings"]["directories_filename_pattern"]
        print(f"\n{number}/{len(jobs)} generating: {job.title}")
        print(f"subdirectory: {subdir}")

        try:
            response = client.txt2img(payload)
        except SdWebuiApiError as error:
            failures += 1
            print(f"failed: {error}", file=sys.stderr)
            if args.stop_on_error:
                break
            continue

        info = response.get("info")
        print("completed")
        if args.print_info and info:
            print(info)

    if failures:
        print(f"\nCompleted with {failures} failure(s).", file=sys.stderr)
        return 1

    print("\nAll jobs completed.")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Stable Diffusion WebUI txt2img jobs from a Japanese bullet prompt note."
    )
    parser.add_argument("prompt_file", type=Path, help="Path to the Notepad prompt text file.")
    parser.add_argument(
        "--url",
        default=os.environ.get("SD_WEBUI_URL", "http://127.0.0.1:7860"),
        help="Stable Diffusion WebUI URL. Default: %(default)s",
    )
    parser.add_argument(
        "--payload-json",
        type=Path,
        help="Optional JSON file with base txt2img settings. CLI/job values override it.",
    )
    parser.add_argument(
        "--batch-count",
        type=int,
        help="Batch Count / n_iter. If omitted, uses payload n_iter or 1.",
    )
    parser.add_argument("--batch-size", type=int, help="Batch Size. If omitted, uses payload batch_size or 1.")
    parser.add_argument("--negative-prompt", help="Negative prompt applied to all jobs.")
    parser.add_argument("--sampler-name", help="Sampler name applied to all jobs.")
    parser.add_argument("--scheduler", help="Scheduler name applied to all jobs.")
    parser.add_argument("--steps", type=int, help="Sampling steps applied to all jobs.")
    parser.add_argument("--cfg-scale", type=float, help="CFG scale applied to all jobs.")
    parser.add_argument("--width", type=int, help="Image width applied to all jobs.")
    parser.add_argument("--height", type=int, help="Image height applied to all jobs.")
    parser.add_argument("--seed", type=int, help="Seed applied to all jobs. Omit for WebUI default.")
    parser.add_argument(
        "--send-images",
        action="store_true",
        help="Return generated images in the API response. Disabled by default to reduce memory traffic.",
    )
    parser.add_argument(
        "--no-save-images",
        action="store_true",
        help="Do not save images through WebUI. Default is to save.",
    )
    parser.add_argument(
        "--no-sanitize-subdir",
        action="store_true",
        help="Use title text as-is for the subdirectory pattern.",
    )
    parser.add_argument("--timeout", type=float, default=86400, help="API timeout seconds. Use 0 for no timeout.")
    parser.add_argument("--username", help="API basic auth username, if WebUI uses --api-auth.")
    parser.add_argument("--password", help="API basic auth password, if WebUI uses --api-auth.")
    parser.add_argument("--limit", type=int, help="Run only the first N jobs.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print payloads without calling WebUI.")
    parser.add_argument("--print-info", action="store_true", help="Print WebUI generation info after each job.")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop at the first failed job.")
    return parser


def load_payload_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}

    with path.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise SystemExit("--payload-json must contain a JSON object.")

    return strip_comment_fields(data)


def build_payload(job: PromptJob, args: argparse.Namespace, base_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(base_payload)
    payload["prompt"] = job.prompt

    if args.batch_count is not None:
        payload["n_iter"] = args.batch_count
    else:
        payload.setdefault("n_iter", 1)

    if args.batch_size is not None:
        payload["batch_size"] = args.batch_size
    else:
        payload.setdefault("batch_size", 1)

    if args.no_save_images:
        payload["save_images"] = False
    else:
        payload.setdefault("save_images", True)

    if args.send_images:
        payload["send_images"] = True
    else:
        payload.setdefault("send_images", False)

    optional_fields = {
        "negative_prompt": args.negative_prompt,
        "sampler_name": args.sampler_name,
        "scheduler": args.scheduler,
        "steps": args.steps,
        "cfg_scale": args.cfg_scale,
        "width": args.width,
        "height": args.height,
        "seed": args.seed,
    }
    for key, value in optional_fields.items():
        if value is not None:
            payload[key] = value

    apply_hires_compatibility_defaults(payload)

    override_settings = dict(payload.get("override_settings") or {})
    override_settings["save_to_dirs"] = True
    override_settings["directories_filename_pattern"] = get_subdirectory(
        job,
        sanitize=not args.no_sanitize_subdir,
    )
    payload["override_settings"] = override_settings
    payload["override_settings_restore_afterwards"] = True

    return payload


def apply_hires_compatibility_defaults(payload: dict[str, Any]) -> None:
    if not payload.get("enable_hr"):
        return

    # Forge/reForge variants expose these Hires fields through the API schema
    # without usable defaults. Supplying numbers avoids None reaching math code.
    payload.setdefault("hr_cfg_scale", payload.get("cfg_scale", 7.0))
    payload.setdefault("hr_rescale_cfg", 0.0)


def strip_comment_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_comment_fields(item)
            for key, item in value.items()
            if not key.startswith("_comment")
        }

    if isinstance(value, list):
        return [strip_comment_fields(item) for item in value]

    return value


def get_subdirectory(job: PromptJob, sanitize: bool) -> str:
    if not sanitize:
        return job.subdirectory
    return sanitize_subdirectory(job.subdirectory)


def sanitize_subdirectory(value: str) -> str:
    sanitized = INVALID_WINDOWS_NAME_CHARS.sub("_", value)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" .")
    return sanitized or "untitled"


if __name__ == "__main__":
    raise SystemExit(main())
