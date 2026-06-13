# Evaluation

Empirical comparison of greedy vs MILP on the KloudChat live cluster, with
KV-cache calibration data and sensitivity scenarios.

The numbers in this document are *snapshotted* from a single planning run
against two GB10 nodes (122 GiB usable unified memory each) with
`expected_concurrent_sessions = 4` for chat-gemma4-26b. The exact node identities
are deployment-specific; reproduce against your own cluster with

```bash
python -m scheduler eval
```

## 1. Calibration findings

`calibration.py` solves the budget identity

$$
u \cdot C_n \;-\; W \;-\; (N_{\text{blocks}} \cdot \text{block\_size}) \cdot M_{KV}^{\text{tok}} \;=\; M_A
$$

against three live deployments on the cluster:

| Model | $\ell_{\max}$ | $u$ | $W$ (analytic) | reported $N_{\text{blocks}}$ | implied $M_A$ |
|---|---|---|---|---|---|
| qwen3.5-122b-a10b-NVFP4 | 32K | 0.60 | 61.4 GiB | 1628 | **41.2 GiB** |
| gemma-4-26b-NVFP4       | 16K | 0.45 | 13.8 GiB | 30,611 | **26.3 GiB** |
| BGE-M3 (embedding)      |  8K | 0.30 |  1.7 GiB | 1 | 34.8 GiB (outlier) |

The qwen3.5-122b row is the headline data point: the *implied* activation buffer
is 41 GiB — about 67% of the weight size. Possible explanations include
(a) MoE expert routing overhead, (b) conservative torch.compile graph
reservation, (c) vLLM's internal slack reserved for sampler/scheduler
bookkeeping. The current model attributes all three to a single $M_A$
constant; ALGORITHM.md §7.2 lists the decomposition as future work.

The BGE-M3 row is an outlier in the opposite direction: at $u = 0.30$ vLLM
claims 36.5 GiB but with only 1.7 GiB of weight and effectively no KV cache,
nearly the entire budget is unused — the implied $M_A$ is meaningless. The
calibration step keeps it in the table but the planner downweights it (we
never solve a placement where BGE-M3 sets the activation floor for another
workload).

## 2. Solver comparison on the live cluster

> Note: the worked example below uses illustrative configs (gemma@16K, 122b@64K) to show
> solver mechanics. In the live catalog chat-122b is floored at 128K (deep-research needs
> ≥128K) and the planner maximizes chat-gemma context from slack VRAM, so real placements differ.

Cluster: 2 × GB10, 122 GiB each. Priorities:
`[chat, rag, deep-research, image]`. Initial placement:

```
253: chat-gemma4-26b@16K/0.45, image-flux, embed-bge-m3
254: chat-122b@64K/0.60, coder-next@32K/0.88, embed-bge-m3
```

| Metric | Greedy | MILP |
|---|---|---|
| `coverage` | 26.12 | 26.00 |
| `kv_qos`   | 34.12 | 28.29 |
| `migration` | 6 | 9 |
| `imbalance` | 0.033 | 0.004 |

**Greedy** keeps `coder-next` alive (priority weight 0.125 baseline) by
choosing a smaller `chat-122b` config; its score is lexicographically
better on coverage by 0.12. **MILP** drops `coder-next` (no admissible triple
in the residual capacity once chat-122b takes a full 0.70 share) but
better-balances the cluster (imbalance 0.004 vs 0.033). The gap is the
joint-objective phenomenon called out in ALGORITHM.md §7.4: MILP
optimizes coverage exactly, but the resulting coverage tie lets the
lower-rank metrics (`kv_qos`, `imbalance`) push the choice to a different
placement set.

## 3. Sensitivity

### 3.1 Adding a third GB10

```
+ chat-122b on 255 cfg=64K@0.60
+ coder-next on 255 cfg=32K@0.88
Δcoverage = +0.12   Δkv_qos = +0.12
```

The single biggest dividend of a third node is bringing `coder-next` back —
exactly the workload MILP had to drop on the 2-node cluster. The new node
absorbs both a second chat-122b replica and the coder-next that the 2-node
MILP has to evict.

### 3.2 Losing node 254

```
- chat-122b on 254 cfg=64K@0.70
- embed-bge-m3 on 254 cfg=embed@0.15
- image-flux on 253 cfg=flux-q8
+ embed-bge-m3 on 253 cfg=embed@0.15
Δcoverage = −2.00   Δkv_qos = −18.00
```

A single-node outage costs us `image-flux` entirely (ComfyUI's 30 GiB FLUX
peak can't fit alongside chat-122b on the surviving 253), and the surviving
chat-122b loses its long-context QoS. The cluster is *not* fault-tolerant
under current placement; running with `r_w^{\min} = 2` for chat-122b would
guarantee a long-context survivor at the cost of dropping `coder-next`.

### 3.3 Shadow prices

LP-relaxed dual values on the per-node capacity rows:

```
[253] +1.795e-01 obj/GiB  binding=False
[254] +1.879e-01 obj/GiB  binding=False
```

Both nodes' capacity constraints are slack at the LP optimum, meaning a
small capacity bump won't change coverage. The positive (but tiny)
shadow prices indicate marginal `kv_qos` gains from extra memory. Read
together with §3.1: the planner doesn't *need* more memory on the
existing nodes — it needs another node to break the partition deadlock.

## 4. KV-aware admission in practice

For the MILP placement above, the effective max-len computed for each
chat-122b deployment (concurrency = 4, admission margin = 1.1) is:

| Node | $\ell_{\max}$ | $u$ | $M_{KV}$ residual | $T_{KV}$ tokens | $\ell_{\text{eff}}$ |
|---|---|---|---|---|---|
| 253 | 64K | 0.60 | 0.20 GiB | 17,691 | 4,020 |
| 254 | 64K | 0.70 | 12.30 GiB | ≈ 1.2M | 65,536 (capped) |

The 253 deployment, with chat-122b co-resident under ComfyUI's pressure,
admits only ~4K tokens under concurrent load. The deep-research feature is
admitted only by a chat-122b deployment whose effective context clears the
catalog's `min_effective_len` (see `scheduler/catalog.py`), so the MILP
lands it on 254 automatically — this is the constraint that drives the
asymmetric `max_input_tokens` in the generated litellm-config (the KV-tight
253 gets a small ceiling, the roomy 254 the full served context).

This is the central empirical claim of the algorithm: planning without
the KV model would have generated identical 64K `max_input_tokens` for
both deployments, sending long prompts to the KV-tight node where vLLM
rejects them at runtime.

## 5. Reproduction

```bash
# 1. one-off cluster probe + calibration cache populate
python -m scheduler inventory --hosts gpu-1=ubuntu@gpu-node-1,gpu-2=ubuntu@gpu-node-2

# 2. plan with greedy and MILP, write reports
python -m scheduler plan --solver=greedy --priorities chat,rag,deep-research,image
python -m scheduler plan --solver=milp   --priorities chat,rag,deep-research,image

# 3. sensitivity scenarios
python -m scheduler sensitivity --remove-node 254
python -m scheduler sensitivity --add-node 255:gb10:122

# 4. dry-run apply
python -m scheduler apply --plan plan.json --dry-run
```

The cache at `~/.cache/kloudchat-scheduler/` persists model metadata and
activation fits across runs.
