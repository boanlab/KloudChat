"""Persisted activation/overhead fit lookup for the memory model.

The analytic memory model in ``memory_model.py`` and ``kv_model.py`` is exact
for weights and KV per-token cost, but vLLM also reserves a non-trivial
*activation/workspace* slice (CUDA scratch, torch.compile graph, sampler
buffers, …). Empirical fits per (model_id, max_len) can refine the planner's
estimate beyond ``DEFAULT_ACTIVATION_BYTES``.

This module currently exposes only the **read path**: ``load()`` parses a
JSON cache at ``~/.cache/kloudchat-scheduler/activation_fit.json`` if it
exists. Population is deferred — to enable empirical fits, an external job
(or a future ``cmd_inventory`` extension) needs to walk live vLLM probes,
solve

        u · C_n  =  W  +  M_A  +  N_blocks · block_size · per_token_KV

for M_A using ``vllm:cache_config_info``'s ``num_gpu_blocks`` /
``block_size``, and write the same JSON shape:

    {"<model_id>|<max_len>": <bytes>, ...}

With an empty / missing file, every caller falls through to
``DEFAULT_ACTIVATION_BYTES`` (the safe, slightly conservative default).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


_CACHE_PATH = Path(os.path.expanduser(
    "~/.cache/kloudchat-scheduler/activation_fit.json"
))


def load() -> dict[tuple[str, int], int]:
    """Re-load the persisted fit table; missing file ⇒ empty dict."""
    if not _CACHE_PATH.is_file():
        return {}
    try:
        raw = json.loads(_CACHE_PATH.read_text())
        # JSON keys can't be tuples — we serialize as "model_id|max_len".
        return {
            (k.split("|", 1)[0], int(k.split("|", 1)[1])): int(v)
            for k, v in raw.items()
        }
    except (OSError, ValueError, KeyError):
        return {}
