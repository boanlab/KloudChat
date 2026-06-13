"""Validation harness: scheduler models vs measured ground truth.

No ssh / no live cluster — builds synthetic NodeSpecs and hand-constructed
ModelMetadata (with measured weight bytes + real architecture params) and
exercises memory_model / kv_model / solver. Guards the sizing logic so
regressions surface offline.

Run:  python -m scheduler.tests.test_against_measured   (or pytest)
"""

from __future__ import annotations

from scheduler import kv_model, memory_model
from scheduler.solver import milp
from scheduler.types import (
    Config, Dtype, Feature, ModelMetadata, NodeSpec, Workload, WorkloadKind,
)

GiB = 1024 ** 3
KiB = 1024


# ── measured / spec ground truth (single GPU, kv-cache=fp8) ────────────────
# qwen3.5-122b-a10b: hybrid Mamba MoE deep-research model — 12 full-attention
#   layers (rest linear). NVFP4 weights ~78 GiB.
MD_122B_NVFP4 = ModelMetadata(
    model_id="Qwen/Qwen3.5-122B-A10B-NVFP4",
    n_layers=12, n_kv_heads=4, head_dim=128,           # → 2·12·4·128 = 12288 B/tok (fp8)
    weight_dtype=Dtype.NVFP4, weight_bytes=int(78.0 * GiB), is_hybrid_mamba=True,
)
# gemma-4-26b: dense-ish multimodal MoE (A4B active), NVFP4 weights ~16.4 GiB.
#   All layers KV-bearing (no hybrid-Mamba), so kv/token is larger than the
#   122b's sparse-attention model despite far smaller weights.
MD_GEMMA26_NVFP4 = ModelMetadata(
    model_id="google/gemma-4-26b-it",
    n_layers=48, n_kv_heads=4, head_dim=128,           # dense attention, all layers KV-bearing
    weight_dtype=Dtype.NVFP4, weight_bytes=int(16.4 * GiB),
)


def _node(nid, gpu_class, total_gib, usable_gib=None):
    return NodeSpec(
        node_id=nid, hostname=nid, gpu_class=gpu_class,
        total_vram_bytes=int(total_gib * GiB),
        usable_vram_bytes=int(usable_gib * GiB) if usable_gib else None,
        reserved_bytes=2 * GiB,
    )


def approx(a, b, tol=0.12):
    return abs(a - b) <= tol * max(abs(a), abs(b))


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    return cond


def test_kv_per_token_matches_measured():
    print("kv/token vs measured:")
    ok = True
    bpt122 = kv_model.kv_bytes_per_token(MD_122B_NVFP4, Dtype.FP8)
    # 122b: 2·12·4·128 = 12288 B/tok (fp8 KV)
    ok &= check("122b ~12 KiB/tok", approx(bpt122, 12.0 * KiB), f"{bpt122/KiB:.1f} KiB")
    # gemma (dense, all 48 layers KV-bearing) must be LARGER per token than the
    # 122b's sparse-attention (only 12 full-attention layers) model.
    bptg = kv_model.kv_bytes_per_token(MD_GEMMA26_NVFP4, Dtype.FP8)
    ok &= check("gemma kv/tok > 122b kv/tok (dense vs Mamba effect)", bptg > bpt122,
                f"gemma {bptg/KiB:.1f} vs 122b {bpt122/KiB:.1f}")
    return ok


def test_kv_pool_sizing():
    """GB10@0.70: sanity-check the KV pool for the gemma chat model.

    Model: commitment 0.70·120 = 84 GiB; residual = 84 − W(16.4) − A. NVFP4
    weights leave a large KV pool. With the conservative 2 GiB activation
    default the residual slightly over-estimates the realized pool, so we just
    assert it lands in a sane multi-million-token range.
    """
    print("kv pool sizing (GB10@0.70, gemma NVFP4):")
    node = _node("gb10", "gb10", 120)
    cfg = Config(name="32K@0.70", max_len=32 * KiB * 1, gpu_util=0.70, kv_dtype=Dtype.FP8)
    mb = memory_model.breakdown(workload_kind=WorkloadKind.VLLM, model=MD_GEMMA26_NVFP4,
                                config=cfg, node=node)
    pool = kv_model.kv_capacity_tokens(mb.kv_residual_bytes, MD_GEMMA26_NVFP4, Dtype.FP8)
    return check("gemma GB10@0.70 pool in [1M, 4M] tok", 1_000_000 <= pool <= 4_000_000,
                 f"{pool:,} tok")


def test_admission_triples():
    """Admission/sizing logic via _enumerate_triples (no CBC needed).

    Checks the byte-capacity admission and effective-len sizing, including
    hybrid-Mamba sequence accounting and tensor-parallel placement.
    """
    print("admission (_enumerate_triples):")
    w122 = Workload(id="chat-122b", kind=WorkloadKind.VLLM, model=MD_122B_NVFP4,
                    min_replicas=1, max_replicas=2, expected_concurrent_sessions=2,
                    configs=(
                        Config("64K@0.85", 64 * KiB, 0.85, Dtype.FP8),
                        Config("128K@0.90", 128 * KiB, 0.90, Dtype.FP8),
                    ))
    pro5000 = _node("p5", "pro5000", 48)   # single PRO5000 (48GB)
    pro6000 = _node("p6", "pro6000", 96)   # single PRO6000 (96GB)
    triples = milp._enumerate_triples(
        [w122], [pro5000, pro6000], activation_fit=None, node_reserve={})
    by_node = {}
    for t in triples:
        by_node.setdefault(t.node_id, []).append(t.config.name)
    ok = True
    # 122b weights 78 GiB: 0.85·48=40.8 GiB commitment < weights → NOT admissible
    # on a single 48G PRO5000 (residual negative). PRO6000 (96G) fits.
    ok &= check("122b NOT on single 48G PRO5000 (commit<weights)",
                not by_node.get("p5", []))
    ok &= check("122b configs admissible on PRO6000", len(by_node.get("p6", [])) >= 1,
                f"{by_node.get('p6')}")
    return ok


MD_CODER_FP8 = ModelMetadata(
    model_id="Qwen/Qwen3-Coder-Next-FP8",
    n_layers=24, n_kv_heads=2, head_dim=128,            # → ~12 KiB/tok
    weight_dtype=Dtype.FP8, weight_bytes=int(75.0 * GiB),
)


def test_gemma_fits_long_ctx_on_48g():
    """On a 48G card the gemma NVFP4 weight (16.4G) leaves enough KV for long
    context — a 128K config gets a large effective length even on a tight card."""
    print("gemma long-context on 48G:")
    node = _node("p5", "pro5000", 48)
    cfg = Config("128K@0.85", 128 * KiB, 0.85, Dtype.FP8)
    mb = memory_model.breakdown(workload_kind=WorkloadKind.VLLM, model=MD_GEMMA26_NVFP4,
                                config=cfg, node=node)
    kvb = kv_model.breakdown(config=cfg, model=MD_GEMMA26_NVFP4,
                             kv_memory_bytes=mb.kv_residual_bytes,
                             expected_concurrent_sessions=1)
    ok = check("gemma residual > 0 on 48G", mb.kv_residual_bytes > 0,
               f"{mb.kv_residual_bytes/GiB:.1f} GiB KV")
    ok &= check("gemma eff_len reaches ≥128K on 48G", kvb.effective_max_len >= 128 * KiB,
                f"{kvb.effective_max_len/KiB:.0f}K")
    return ok


def test_tp2_coder_on_2gpu_node():
    """H4: coder-next (75G) fits tp=2 on a 2×48G node, not tp=1."""
    print("coder-next TP=2 on 2-GPU node (H4):")
    one = _node("p5x1", "pro5000", 48)              # gpu_count default 1
    two = NodeSpec(node_id="p5x2", hostname="p5x2",
                   gpu_class="pro5000",
                   total_vram_bytes=48 * GiB, reserved_bytes=2 * GiB, gpu_count=2)
    w = Workload(id="coder-next", kind=WorkloadKind.VLLM, model=MD_CODER_FP8,
                 min_replicas=1, max_replicas=1, expected_concurrent_sessions=4,
                 configs=(
                     Config("32K@0.90-tp1", 32 * KiB, 0.90, Dtype.FP8),
                     Config("32K@0.90-tp2", 32 * KiB, 0.90, Dtype.FP8, tp_size=2),
                 ))
    triples = milp._enumerate_triples([w], [one, two], activation_fit=None, node_reserve={})
    cfgs = {(t.node_id, t.config.name) for t in triples}
    ok = check("coder-next tp=1 NOT on single 48G (weights>commit)",
               ("p5x1", "32K@0.90-tp1") not in cfgs)
    ok &= check("coder-next tp=2 NOT on single-GPU node (tp>gpu_count)",
                ("p5x1", "32K@0.90-tp2") not in cfgs)
    ok &= check("coder-next tp=2 admissible on 2-GPU node",
                ("p5x2", "32K@0.90-tp2") in cfgs, f"{sorted(c for n,c in cfgs if n=='p5x2')}")
    return ok


def test_catalog_builds_with_measured_weights():
    """H2: build_catalog (no site → cache-only) still wires weight overrides.
    We can't probe here, so just assert the template weights are set."""
    print("catalog measured-weight plumbing (H2):")
    from scheduler import catalog
    by_id = {t.id: t for t in catalog.WORKLOAD_TEMPLATES}
    ok = check("chat-gemma4-26b weight override set",
               by_id["chat-gemma4-26b"].weight_bytes == int(16.4 * GiB))
    # The catalog ships exactly the current model set (no stale workloads).
    ok &= check("catalog ships only the current workload set",
                set(by_id) == {"chat-gemma4-26b", "embed-bge-m3",
                               "chat-122b", "coder-next", "image-flux"},
                f"{sorted(by_id)}")
    ok &= check("122b carries tp2 + max_num_seqs=128 configs",
                any(c.tp_size == 2 for c in by_id["chat-122b"].configs)
                and all(c.max_num_seqs == 128 for c in by_id["chat-122b"].configs))
    return ok


def test_two_phase_diversity_before_replicas():
    """Two-phase placement: every distinct workload is brought up (phase 1)
    before any throughput replica (phase 2). Two GB10 nodes fit EITHER two
    replicas of the high-priority chat model OR (one chat + one big 122b). The
    solver must choose diversity — place the lower-priority 122b rather than a
    second gemma replica, because W_div dominates W_rep in the objective."""
    print("two-phase diversity-first:")
    nodes = [_node("n1", "gb10", 128, 112), _node("n2", "gb10", 128, 112)]
    chat = Workload(
        id="chat", kind=WorkloadKind.VLLM, model=MD_GEMMA26_NVFP4,
        min_replicas=1, max_replicas=4, expected_concurrent_sessions=4,
        configs=(Config(name="16K@0.45", max_len=16 * 1024, gpu_util=0.45, kv_dtype=Dtype.FP8),),
    )
    deep = Workload(
        id="deep", kind=WorkloadKind.VLLM, model=MD_122B_NVFP4,
        min_replicas=1, max_replicas=1, expected_concurrent_sessions=2,
        configs=(Config(name="64K@0.85", max_len=64 * 1024, gpu_util=0.85, kv_dtype=Dtype.FP8),),
    )
    feats = [Feature("chat", requires_workload="chat"),
             Feature("deep-research", requires_workload="deep")]
    plan = milp.solve(workloads=[chat, deep], nodes=nodes,
                      priority_order=["chat", "deep-research"], features=feats)
    placed = sorted({p.workload_id for p in plan.placements})
    return check("low-priority distinct workload placed over a 2nd high-priority replica",
                 "chat" in placed and "deep" in placed, f"placed={placed}")


def main():
    results = []
    for fn in (test_kv_per_token_matches_measured,
               test_kv_pool_sizing,
               test_admission_triples,
               test_gemma_fits_long_ctx_on_48g,
               test_tp2_coder_on_2gpu_node,
               test_catalog_builds_with_measured_weights,
               test_two_phase_diversity_before_replicas):
        results.append(fn())
    passed = sum(bool(r) for r in results)
    print(f"\n{passed}/{len(results)} test groups passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
