from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .batching import (
    DEFAULT_MAX_IMAGES_PER_REQUEST,
    HARD_MAX_IMAGES_PER_REQUEST,
    BatchChunk,
    split_payload_into_chunks,
)
from .client import SdWebuiApiError, SdWebuiClient, SdWebuiTransportError
from .dynamic_prompts import (
    DynamicPromptError,
    DynamicPromptExpander,
    plan_dynamic_prompt_chunks,
    write_dynamic_manifest,
)
from .parser import PromptJob, PromptParseError, parse_prompt_note, read_text_file
from .prompt_library import merge_payload_overrides


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

    try:
        expander = (
            DynamicPromptExpander(args.wildcards_dir)
            if args.expand_dynamic_prompts
            else None
        )
        dynamic_records: list[dict[str, Any]] = []
        job_chunks: list[tuple[BatchChunk, ...]] = []
        for job in jobs:
            payload = build_payload(job, args, base_payload)
            if expander is None:
                chunks = split_payload_into_chunks(
                    payload,
                    max_images_per_request=args.chunk_size,
                )
            else:
                chunks, records = plan_dynamic_prompt_chunks(
                    payload,
                    expander,
                    job_index=job.index,
                    job_title=job.title,
                )
                dynamic_records.extend(records)
            job_chunks.append(chunks)
    except (DynamicPromptError, ValueError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    if expander is not None:
        manifest_dir = args.manifest_dir or args.prompt_file.parent / "manifests"
        manifest_path = write_dynamic_manifest(
            manifest_dir,
            dynamic_records,
            metadata={
                "webui_url": args.url,
                "prompt_file": str(args.prompt_file.resolve()),
                "payload_file": str(args.payload_json.resolve()) if args.payload_json else None,
                "wildcard_directories": [str(path) for path in expander.directories],
                "base_payload": base_payload,
            },
        )
        print(f"Dynamic prompt manifest: {manifest_path}")

    if args.dry_run:
        print("\nDry run request plan (no API calls will be made):")
        for job, chunks in zip(jobs, job_chunks):
            _print_dry_run_plan(job, chunks)
        return 0

    client = SdWebuiClient(
        base_url=args.url,
        timeout=None if args.timeout == 0 else args.timeout,
        username=args.username,
        password=args.password,
    )

    failures = 0
    abort_run = False
    for number, (job, chunks) in enumerate(zip(jobs, job_chunks), start=1):
        subdir = chunks[0].payload["override_settings"]["directories_filename_pattern"]
        print(f"\nTask {number}/{len(jobs)}: {job.title}")
        print(f"subdirectory: {subdir}")
        print(
            f"{chunks[0].total_images} image(s) in {len(chunks)} request(s); "
            f"up to {args.chunk_size} image(s) per request"
        )

        for chunk in chunks:
            print(
                f"[task {number}/{len(jobs)}][request {chunk.ordinal}/{chunk.total_chunks}] "
                f"sending images {chunk.image_start}-{chunk.image_end}/{chunk.total_images}"
            )
            try:
                response = client.txt2img(chunk.payload)
            except SdWebuiTransportError as error:
                failures += 1
                abort_run = True
                print(f"connection state unknown: {error}", file=sys.stderr)
                print(
                    "Stopping all tasks because WebUI may still be processing this request. "
                    "The request will not be retried automatically.",
                    file=sys.stderr,
                )
                break
            except SdWebuiApiError as error:
                failures += 1
                print(f"request failed: {error}", file=sys.stderr)
                print(
                    "Skipping the remaining requests for this task; this request will not "
                    "be retried automatically.",
                    file=sys.stderr,
                )
                if args.stop_on_error:
                    abort_run = True
                break

            percent = chunk.image_end / chunk.total_images * 100
            print(f"completed through image {chunk.image_end}/{chunk.total_images} ({percent:.1f}%)")
            info = response.get("info")
            if args.print_info and info:
                print(info)

        if abort_run:
            break

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
    dynamic_default = os.environ.get("SD_WEBUI_DYNAMIC_PROMPTS", "").casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    wildcard_default = [
        Path(value)
        for value in os.environ.get("SD_WEBUI_WILDCARDS", "").split(os.pathsep)
        if value.strip()
    ]
    parser.add_argument(
        "--expand-dynamic-prompts",
        action=argparse.BooleanOptionalAction,
        default=dynamic_default,
        help=(
            "Resolve {a|b} and __wildcard__ syntax in the runner. Each output is sent "
            "as an individual B=1 request and recorded in a manifest."
        ),
    )
    parser.add_argument(
        "--wildcards-dir",
        type=Path,
        action="append",
        default=wildcard_default,
        help="Wildcard text directory. May be supplied more than once.",
    )
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=(
            Path(os.environ["SD_WEBUI_MANIFEST_DIR"])
            if os.environ.get("SD_WEBUI_MANIFEST_DIR")
            else None
        ),
        help="Directory for resolved Dynamic Prompts manifests.",
    )
    parser.add_argument(
        "--batch-count",
        type=int,
        help="Batch Count / n_iter. If omitted, uses payload n_iter or 1.",
    )
    parser.add_argument("--batch-size", type=int, help="Batch Size. If omitted, uses payload batch_size or 1.")
    parser.add_argument(
        "--chunk-size",
        type=_parse_chunk_size,
        default=DEFAULT_MAX_IMAGES_PER_REQUEST,
        help=(
            "Maximum generated images per API request (1-"
            f"{HARD_MAX_IMAGES_PER_REQUEST}). Default: %(default)s."
        ),
    )
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
    parser.add_argument("--print-info", action="store_true", help="Print WebUI generation info after each request.")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop at the first failed job.")
    return parser


def _parse_chunk_size(value: str) -> int:
    try:
        chunk_size = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("chunk size must be an integer") from error

    if not 1 <= chunk_size <= HARD_MAX_IMAGES_PER_REQUEST:
        raise argparse.ArgumentTypeError(
            f"chunk size must be between 1 and {HARD_MAX_IMAGES_PER_REQUEST}"
        )
    return chunk_size


def _print_dry_run_plan(job: PromptJob, chunks: tuple[BatchChunk, ...]) -> None:
    first = chunks[0]
    print(
        f"\n[{job.index}] {job.title}: {first.total_images} image(s), "
        f"{first.total_chunks} request(s)"
    )
    for chunk in chunks:
        print(
            f"  request {chunk.ordinal}/{chunk.total_chunks}: "
            f"images {chunk.image_start}-{chunk.image_end}, "
            f"n_iter={chunk.payload['n_iter']}, seed={chunk.payload.get('seed', 'default')}"
        )
    print("  first request payload:")
    print(json.dumps(first.payload, ensure_ascii=False, indent=2))


def load_payload_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}

    with path.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise SystemExit("--payload-json must contain a JSON object.")

    return strip_comment_fields(data)


def build_payload(job: PromptJob, args: argparse.Namespace, base_payload: dict[str, Any]) -> dict[str, Any]:
    payload = merge_payload_overrides(base_payload, job.settings_override)
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
    # The runner consumes individually saved images and never uses a grid.
    # Both settings must be false because Forge creates a grid when either
    # return_grid or grid_save is enabled.
    override_settings["return_grid"] = False
    override_settings["grid_save"] = False
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

    # Classic/reForge use hr_cfg_scale while Forge Neo uses hr_cfg. Keep both
    # so the same payload profile remains valid against either API.
    hires_cfg = payload.get(
        "hr_cfg",
        payload.get("hr_cfg_scale", payload.get("cfg_scale", 7.0)),
    )
    payload.setdefault("hr_cfg_scale", hires_cfg)
    payload.setdefault("hr_cfg", hires_cfg)
    payload.setdefault("hr_rescale_cfg", 0.0)

    # Forge Neo 2.28 iterates this value during Hires processing. Its API
    # schema currently supplies None when the field is omitted.
    payload.setdefault("hr_additional_modules", [])


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
