"""Split large Stable Diffusion WebUI payloads into bounded requests."""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


HARD_MAX_IMAGES_PER_REQUEST = 100
DEFAULT_MAX_IMAGES_PER_REQUEST = HARD_MAX_IMAGES_PER_REQUEST
WEBUI_RANDOM_SEED_UPPER_BOUND = 4294967294


@dataclass(frozen=True)
class BatchChunk:
    """One API request and the progress metadata that surrounds it.

    ``ordinal``, ``image_start``, and ``image_end`` are one-based so they can
    be displayed directly by a GUI or CLI. ``image_end`` is inclusive.
    """

    ordinal: int
    total_chunks: int
    image_start: int
    image_end: int
    image_count: int
    total_images: int
    completed_images_before: int
    payload: dict[str, Any]


def split_payload_into_chunks(
    payload: Mapping[str, Any],
    max_images_per_request: int = DEFAULT_MAX_IMAGES_PER_REQUEST,
    *,
    resolve_random_seeds: bool = True,
) -> tuple[BatchChunk, ...]:
    """Return independent payloads with a bounded number of output images.

    Stable Diffusion WebUI produces ``n_iter * batch_size`` images. This
    function treats the input ``n_iter`` as the requested total batch count
    and changes only ``n_iter`` in each chunk. Fixed seed values are advanced
    the same way WebUI advances them: the main ``seed`` advances outside
    variation mode, while ``subseed`` advances whenever it is fixed. Random
    seed values are resolved once per call, matching Forge's
    ``processing.get_fixed_seed`` behavior, so all chunks retain the same
    sequence a single request would have produced.

    Set ``resolve_random_seeds`` to false only for non-executing plan/count
    displays that must not consume randomness. Such payloads must be split
    again with the default behavior before they are sent to WebUI.

    The input mapping and all of its nested values are left untouched.
    """

    _require_positive_int("max_images_per_request", max_images_per_request)
    if max_images_per_request > HARD_MAX_IMAGES_PER_REQUEST:
        raise ValueError(
            "max_images_per_request cannot exceed the hard limit of "
            f"{HARD_MAX_IMAGES_PER_REQUEST} images"
        )

    n_iter = payload.get("n_iter", 1)
    batch_size = payload.get("batch_size", 1)
    _require_positive_int("n_iter", n_iter)
    _require_positive_int("batch_size", batch_size)

    if batch_size > max_images_per_request:
        raise ValueError(
            "batch_size cannot exceed max_images_per_request because a single "
            "iteration cannot be split without changing batch_size"
        )

    iterations_per_chunk = max_images_per_request // batch_size
    total_chunks = (n_iter + iterations_per_chunk - 1) // iterations_per_chunk
    total_images = n_iter * batch_size
    chunks: list[BatchChunk] = []
    completed_iterations = 0
    resolved_payload = deepcopy(dict(payload))
    if resolve_random_seeds:
        resolved_payload["seed"] = _get_fixed_seed(resolved_payload.get("seed"))
        resolved_payload["subseed"] = _get_fixed_seed(resolved_payload.get("subseed"))

    for ordinal in range(1, total_chunks + 1):
        chunk_n_iter = min(iterations_per_chunk, n_iter - completed_iterations)
        completed_images = completed_iterations * batch_size
        image_count = chunk_n_iter * batch_size
        chunk_payload = deepcopy(resolved_payload)
        chunk_payload["n_iter"] = chunk_n_iter

        # WebUI increments the main seed only when variation/subseed mode is
        # disabled. In variation mode the main seed stays fixed while the
        # subseed advances for each image.
        if chunk_payload.get("subseed_strength", 0) == 0:
            _offset_fixed_seed(chunk_payload, "seed", completed_images)
        _offset_fixed_seed(chunk_payload, "subseed", completed_images)

        chunks.append(
            BatchChunk(
                ordinal=ordinal,
                total_chunks=total_chunks,
                image_start=completed_images + 1,
                image_end=completed_images + image_count,
                image_count=image_count,
                total_images=total_images,
                completed_images_before=completed_images,
                payload=chunk_payload,
            )
        )
        completed_iterations += chunk_n_iter

    return tuple(chunks)


def _require_positive_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")


def _offset_fixed_seed(payload: dict[str, Any], key: str, offset: int) -> None:
    value = payload.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        payload[key] = value + offset


def _get_fixed_seed(seed: Any) -> Any:
    """Mirror Forge ``processing.get_fixed_seed`` for one seed value."""

    if seed == "" or seed is None:
        seed = -1
    elif isinstance(seed, str):
        try:
            seed = int(seed)
        except Exception:
            seed = -1

    if seed == -1:
        return int(random.randrange(WEBUI_RANDOM_SEED_UPPER_BOUND))

    return seed
