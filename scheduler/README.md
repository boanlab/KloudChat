# scheduler

KV-aware placement planner for KloudChat's heterogeneous GPU cluster.

Given a list of compute nodes (probed live over ssh) and a user-supplied
feature priority list (`chat,rag,deep-research,image`), the scheduler
decides which workloads (`chat-gemma4-26b`, `chat-122b`, `coder-next`,
`embed-bge-m3`, `image-flux`) to deploy on which node, in which configuration
(max-context-length × GPU-utilization), so that the maximum number of
high-priority features survives within the cluster's memory budget.

The problem is formalized in [`docs/ALGORITHM.md`](docs/ALGORITHM.md)
(Priority-Weighted Replica Placement under Heterogeneous GPU Constraints —
NP-hard, MILP exact for our scale) and evaluated on the live cluster in
[`docs/EVALUATION.md`](docs/EVALUATION.md).

## Quick start

```bash
# 1. probe the cluster
python -m scheduler inventory

# 2. plan with the MILP solver
python -m scheduler plan --priorities chat,rag,deep-research,image

# 3. show the diff without touching anything
python -m scheduler apply --dry-run

# 4. apply (shows the diff, then prompts for confirmation)
python -m scheduler apply

# 5. apply without the prompt
python -m scheduler apply -y
```

## Deploying to a new cluster

All cluster-specific bindings (node hosts, container names, ports, env-var
prefixes, model checkpoint paths, compose working dir, litellm route mapping)
live in a **site config** — a single YAML file. The operator file
`scheduler/sites/kloudchat.yaml` is gitignored; copy it from the tracked
template `scheduler/sites/kloudchat.yaml.example` (for KloudChat workloads) or
`scheduler/sites/example.yaml` (for a different model family).

```bash
# 1. Copy the template to a per-user override path (optional but recommended).
cp scheduler/sites/kloudchat.yaml.example ~/.config/kloudchat-scheduler/site.yaml

# 2. Edit it for your cluster — node ssh targets, container names, etc.
# 3. Tell the scheduler where to find it (either):
export KLOUDCHAT_SCHEDULER_SITE=~/.config/kloudchat-scheduler/site.yaml
# or pass --site on every invocation:
python -m scheduler --site ~/.config/kloudchat-scheduler/site.yaml inventory
```

The site config doesn't touch the catalog (workload archetypes, KV/memory
models, solver algorithms) — those are cluster-agnostic. You only need to
edit `catalog.py` if your cluster runs a **different model family** (Llama
instead of Qwen, etc.); even then it's just adding a `WorkloadTemplate(...)`
with the appropriate `model_id` and config grid.

The site resolution order is:
1. `--site /path` CLI flag
2. `KLOUDCHAT_SCHEDULER_SITE` env var
3. `~/.config/kloudchat-scheduler/site.yaml`
4. `scheduler/sites/kloudchat.yaml` (gitignored — copy from `kloudchat.yaml.example` for KloudChat workloads, or `example.yaml` for a new model family)

If none of these exists, the loader raises `FileNotFoundError` with a hint to copy one of the templates.

## What makes this not a 50-line greedy script

1. **KV cache is first-class.** The planner sizes
   `chat-gemma4-26b @ 32K/0.60`'s *effective* context as
   `min(32K, T_KV/(α·N_conc))` where `T_KV` is fit empirically from
   each live vLLM's reported `num_gpu_blocks`. Without that, you'd
   plan placements that pass admission checks and then have vLLM
   reject long prompts at runtime.

2. **Two solvers, side by side.** A priority-ordered greedy
   `(1-1/e)`-approximation as baseline, and an exact MILP via PuLP +
   CBC. `python -m scheduler eval` prints both scores so you can see
   where they disagree — the gap is a useful sanity signal.

3. **Sensitivity analysis.** "What if I add another node?" / "what
   if 254 goes down?" run a perturbed solve and diff the placements.
   "What's the most constrained node?" reads the LP relaxation's dual
   values on capacity rows — the shadow price per GiB.

4. **Two-phase placement under one lexicographic objective.** Phase 1
   (diversity) brings up one replica of each prioritized workload; phase 2
   fills spare capacity with throughput replicas. Below those sit KV-QoS,
   affinity, migration cost, and load balance. Implemented as additive
   weights `(10⁹, 10³, 10, 10⁻³, 10⁻², 10⁻³)` (div, rep, kv, aff, mig, bal),
   with `W_div ≫ W_rep` so the first instance of even the lowest-priority
   workload outweighs any number of extra replicas of higher-priority ones —
   and high-rank gains can never be sacrificed for low-rank ones.

## Files

| File | What it does |
|---|---|
| `types.py` | Dataclasses for nodes, workloads, configs, plans, scores |
| `site.py` + `sites/*.yaml` | Cluster-specific bindings (hosts, containers, ports, paths, litellm routes) |
| `inventory.py` | ssh + comfyui/vllm probe → NodeSpec list with running workloads |
| `model_metadata.py` | Reads `config.json` from a node; handles GQA, hybrid attention, MoE |
| `kv_model.py` | Per-token KV cost, effective max-len, concurrency factor |
| `memory_model.py` | Weight + activation + KV residual + co-residency penalty |
| `calibration.py` | Fits `M_A` from live `num_gpu_blocks`; persists across runs |
| `catalog.py` | Workload archetypes + their config grids + features (cluster-agnostic) |
| `solver/greedy.py` | Priority-ordered first-fit; doubles as `(1-1/e)` baseline |
| `solver/milp.py` | PuLP + CBC; replicates the formulation in ALGORITHM.md |
| `evaluator.py` | Coverage / KV-QoS / migration / imbalance scoring |
| `sensitivity.py` | What-if (add/remove node, re-rank priorities) + LP shadow prices |
| `applier.py` | Plan diff → .env updates, container actions, systemd, litellm-config |
| `__main__.py` | CLI: `inventory`/`plan`/`apply`/`sensitivity`/`eval` |

## Dependencies

```bash
sudo apt install python3-pulp coinor-cbc python3-yaml
```

That's it — no pip, no venv, no transformers. The model metadata loader
parses `config.json` directly, the MILP solver is open-source CBC, the
site config is plain YAML, and everything else is in the Python standard
library.

## Adding a workload

1. Drop a `WorkloadTemplate(...)` into `catalog.WORKLOAD_TEMPLATES`. Set
   `model_id` to the HF checkpoint identity (the metadata cache key). In
   your site YAML, bind the new workload id to a `model_path` containing
   `config.json` — `catalog._fetch_with_site` will ssh into each node in
   `nodes:` and cat that file.
2. Optionally add a `Feature(...)` to `catalog.FEATURES` if the workload
   should be controllable via the user-facing priority list.
3. Re-run `python -m scheduler plan`. The new workload's `(max_len, util)`
   grid will be enumerated alongside the existing ones.

## Adding a node

Just add it to `--hosts`. The probe figures out GPU class, total VRAM, and
which containers (if any) are already running. No config changes needed
unless the new node uses a different ssh user.

## Known limits

Most are tracked in [`docs/ALGORITHM.md` §7](docs/ALGORITHM.md#7-known-limitations-and-open-problems).
The two most user-visible:

- **ComfyUI co-residency is an input, not a decision.** The planner asks
  you to commit to which nodes host ComfyUI before solving. Lifting that
  constraint would require a McCormick-envelope linearization
  (see [`docs/ALGORITHM.md` §7](docs/ALGORITHM.md#7-known-limitations-and-open-problems)).
- **Activation buffer (`M_A`) is empirical.** We solve for it from
  observed `num_gpu_blocks` but the fitted value (~41 GiB for the
  qwen3.5-122b-a10b MoE) is large enough that it dominates the budget.
  Splitting it into workspace / MoE-routing / KV-paging components is open work.
