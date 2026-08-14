"""Runner-side Dynamic Prompts and wildcard expansion.

The implementation intentionally covers the stable syntax used by the local
workflow: ``{a|b}`` alternatives and ``__name__`` text wildcards. Expansion
happens before API submission, so Forge Neo does not need the legacy WebUI
extension and every resolved prompt can be recorded in a manifest.
"""

from __future__ import annotations

import json
import random
import re
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .batching import BatchChunk, split_payload_into_chunks


INLINE_PATTERN = re.compile(r"\{([^{}]*)\}")
WILDCARD_PATTERN = re.compile(r"__([^\r\n]+?)__")
MAX_EXPANSION_PASSES = 100


class DynamicPromptError(ValueError):
    """Raised when dynamic prompt input cannot be resolved safely."""


@dataclass(frozen=True)
class Expansion:
    text: str
    choices: tuple[dict[str, Any], ...]


class DynamicPromptExpander:
    def __init__(self, wildcard_directories: Iterable[str | Path]):
        directories = tuple(Path(path).expanduser().resolve() for path in wildcard_directories)
        if not directories:
            raise DynamicPromptError("At least one wildcard directory is required.")
        for directory in directories:
            if not directory.is_dir():
                raise DynamicPromptError(f"Wildcard directory does not exist: {directory}")
        self.directories = directories
        self._wildcard_files = self._index_files()
        self._line_cache: dict[Path, tuple[str, ...]] = {}

    def _index_files(self) -> dict[str, Path]:
        indexed: dict[str, Path] = {}
        stem_aliases: dict[str, Path | None] = {}
        for directory in self.directories:
            for path in sorted(directory.rglob("*.txt")):
                relative_key = path.relative_to(directory).with_suffix("").as_posix().casefold()
                indexed.setdefault(relative_key, path)
                stem_key = path.stem.casefold()
                if stem_key not in stem_aliases:
                    stem_aliases[stem_key] = path
                elif stem_aliases[stem_key] != path:
                    stem_aliases[stem_key] = None
        for key, path in stem_aliases.items():
            if path is not None:
                indexed.setdefault(key, path)
        return indexed

    def _read_lines(self, path: Path) -> tuple[str, ...]:
        if path in self._line_cache:
            return self._line_cache[path]
        data = path.read_bytes()
        text = None
        for encoding in ("utf-8-sig", "utf-16", "cp932"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise DynamicPromptError(f"Could not decode wildcard file: {path}")
        lines = tuple(
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        if not lines:
            raise DynamicPromptError(f"Wildcard file has no usable entries: {path}")
        self._line_cache[path] = lines
        return lines

    def expand(self, text: str, rng: random.Random) -> Expansion:
        value = text
        trace: list[dict[str, Any]] = []
        for _ in range(MAX_EXPANSION_PASSES):
            inline = INLINE_PATTERN.search(value)
            wildcard = WILDCARD_PATTERN.search(value)
            if inline is None and wildcard is None:
                return Expansion(value, tuple(trace))

            # Expand whichever token begins first. Nested braces are naturally
            # handled because INLINE_PATTERN only matches innermost braces.
            if inline is not None and (wildcard is None or inline.start() < wildcard.start()):
                options = _split_options(inline.group(1))
                if not options:
                    raise DynamicPromptError(f"Empty inline choice in prompt: {inline.group(0)}")
                selected = rng.choice(options)
                trace.append(
                    {
                        "kind": "inline",
                        "token": inline.group(0),
                        "selected": selected,
                    }
                )
                value = value[: inline.start()] + selected + value[inline.end() :]
                continue

            assert wildcard is not None
            name = wildcard.group(1).strip().replace("\\", "/")
            key = name.casefold()
            path = self._wildcard_files.get(key)
            if path is None:
                raise DynamicPromptError(
                    f"Wildcard '__{name}__' was not found in: "
                    + ", ".join(str(directory) for directory in self.directories)
                )
            selected = rng.choice(self._read_lines(path))
            trace.append(
                {
                    "kind": "wildcard",
                    "token": wildcard.group(0),
                    "name": name,
                    "file": str(path),
                    "selected": selected,
                }
            )
            value = value[: wildcard.start()] + selected + value[wildcard.end() :]

        raise DynamicPromptError(
            f"Prompt expansion exceeded {MAX_EXPANSION_PASSES} passes; "
            "check for recursive wildcards."
        )


def plan_dynamic_prompt_chunks(
    payload: Mapping[str, Any],
    expander: DynamicPromptExpander,
    *,
    job_index: int,
    job_title: str,
) -> tuple[tuple[BatchChunk, ...], tuple[dict[str, Any], ...]]:
    """Resolve one prompt per output image and return B=1 API requests."""

    batch_size = payload.get("batch_size", 1)
    n_iter = payload.get("n_iter", 1)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise DynamicPromptError(f"batch_size must be a positive integer, got {batch_size!r}")
    if isinstance(n_iter, bool) or not isinstance(n_iter, int) or n_iter <= 0:
        raise DynamicPromptError(f"n_iter must be a positive integer, got {n_iter!r}")

    total_images = batch_size * n_iter
    per_image = deepcopy(dict(payload))
    per_image["batch_size"] = 1
    per_image["n_iter"] = total_images
    base_chunks = split_payload_into_chunks(per_image, max_images_per_request=1)
    source_prompt = str(payload.get("prompt", ""))
    source_negative = str(payload.get("negative_prompt", ""))
    chunks: list[BatchChunk] = []
    records: list[dict[str, Any]] = []

    for image_index, chunk in enumerate(base_chunks, start=1):
        resolved = deepcopy(chunk.payload)
        generation_seed = resolved.get("seed", -1)
        rng = random.Random(f"{generation_seed}:{job_index}:{image_index}")
        positive = expander.expand(source_prompt, rng)
        negative = expander.expand(source_negative, rng)
        resolved["prompt"] = positive.text
        resolved["negative_prompt"] = negative.text
        chunks.append(replace(chunk, payload=resolved))
        records.append(
            {
                "job_index": job_index,
                "job_title": job_title,
                "image_index": image_index,
                "total_images": total_images,
                "seed": generation_seed,
                "subseed": resolved.get("subseed"),
                "source_prompt": source_prompt,
                "resolved_prompt": positive.text,
                "source_negative_prompt": source_negative,
                "resolved_negative_prompt": negative.text,
                "choices": [*positive.choices, *negative.choices],
                "resolved_payload": deepcopy(resolved),
            }
        )

    return tuple(chunks), tuple(records)


def write_dynamic_manifest(
    directory: str | Path,
    records: Iterable[Mapping[str, Any]],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    destination = Path(directory).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = destination / f"dynamic-prompts-{stamp}.json"
    document = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": dict(metadata or {}),
        "records": [dict(record) for record in records],
    }
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _split_options(value: str) -> list[str]:
    options: list[str] = []
    current: list[str] = []
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            options.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    options.append("".join(current))
    return options
