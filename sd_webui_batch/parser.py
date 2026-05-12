from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path


TITLE_PATTERN = re.compile(r"^\s*・(?P<title>.+?)\s*$")


class PromptParseError(ValueError):
    """Raised when a prompt note cannot be parsed into jobs."""


@dataclass(frozen=True)
class PromptJob:
    index: int
    title: str
    prompt: str
    line_number: int

    @property
    def subdirectory(self) -> str:
        return self.title


def read_text_file(path: str | Path) -> str:
    """Read a Notepad-exported prompt file with common Windows encodings."""

    file_path = Path(path)
    data = file_path.read_bytes()

    for encoding in ("utf-8-sig", "utf-16", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise PromptParseError(f"Could not decode file: {file_path}")


def parse_prompt_note(text: str) -> list[PromptJob]:
    """Parse bullet-title prompt notes into generation jobs.

    A job starts at a line beginning with `・`. The prompt is every line after
    that title until the next title or the end of the file. The first prompt
    line is not treated specially, so labels like `AAAA,` and `BBBB,` are kept
    as part of the prompt.
    """

    jobs: list[PromptJob] = []
    current_title: str | None = None
    current_line_number = 0
    current_prompt_lines: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_line_number, current_prompt_lines

        if current_title is None:
            return

        prompt = _normalize_prompt_lines(current_prompt_lines)
        if not prompt:
            raise PromptParseError(
                f"Prompt is empty for title '{current_title}' at line {current_line_number}."
            )

        jobs.append(
            PromptJob(
                index=len(jobs) + 1,
                title=current_title,
                prompt=prompt,
                line_number=current_line_number,
            )
        )
        current_title = None
        current_line_number = 0
        current_prompt_lines = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        title_match = TITLE_PATTERN.match(raw_line)
        if title_match:
            flush()
            title = title_match.group("title").strip()
            if not title:
                raise PromptParseError(f"Title is empty at line {line_number}.")
            current_title = title
            current_line_number = line_number
            current_prompt_lines = []
            continue

        if current_title is not None:
            current_prompt_lines.append(raw_line.rstrip())

    flush()

    if not jobs:
        raise PromptParseError("No prompt jobs were found. Add lines beginning with '・'.")

    return jobs


def _normalize_prompt_lines(lines: list[str]) -> str:
    cleaned = list(lines)

    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    return "\n".join(cleaned).strip()
