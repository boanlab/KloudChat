# PWRP-HGC — Priority-Weighted Replica Placement under Heterogeneous GPU Constraints

**한눈에 (plain English).** 스케줄러는 *우선순위가 있는 bin-packing* 을 푼다: 여러 AI
워크로드(chat / deep-research / embed / image — 각자 VRAM 요구량과 필요 컨텍스트 길이를
가짐)를 VRAM 이 한정된 GPU 노드들에 배치한다. 목표는 사전식(lexicographic) 우선순위:
**① coverage** — 우선순위 높은 순으로 *서로 다른* 워크로드를 먼저 한 개씩 올린다 →
**② replica** — 남는 VRAM 을 고우선 워크로드 복제본으로 채운다 → **③ KV-QoS** — 남은 슬랙으로
컨텍스트를 최대화한다 → **④ migration / affinity / balance** — 동률일 때만 흔들기 최소화. 이
문제는 NP-hard 라 정확해(MILP)와 근사해(greedy) 두 솔버를 둔다. 아래는 그 형식 정의·난이도
증명·솔버 분석이다. (운영 사용법은 [docs/scheduler.md](../../docs/scheduler.md).)

> **Coverage term shape**: the `coverage` *displayed score*
> ($\sum_w \pi_w \cdot \mathbb{1}[\text{alive}]$, per-workload-active) reads
> per-workload. The LP objective splits this into a *diversity* term over
> distinct active workloads (`Σ π_w · y_w`, phase 1) and a *replica* term over
> placements (`Σ π_w · x[(w,c,n)]`, phase 2), so an additional instance of the
> same workload raises the score only once every distinct workload is up —
> enabling multi-instance plans without ever trading away a distinct capability.
> This keeps the lex priority (diversity ≫ replicas ≫ KV-QoS) and the
> $(1-1/e)$ approximation analysis intact: the per-placement replica form is a
> simple monotone shift (`x ⪰ y`) that preserves the ordering within the primary
> (diversity) domain.

This document specifies the optimization problem the scheduler solves, proves
its hardness, and analyzes the two solvers shipped in `scheduler/solver/`.

## 1. Setting

A cluster has nodes $N$ and serves workloads $W$. Each workload $w$ has a set
of configurations $C_w$ (e.g. context-length × GPU-utilization grid for vLLM
deployments). For every triple $(w, c, n)$ we know:

- $m_{w,c,n}$ — memory the configuration commits on node $n$, including
  weight, activation buffer, KV cache residual, and any co-residency penalty
  (e.g. ComfyUI ↔ vLLM on a unified-memory host).
- $\ell_{\text{eff}}(w, c, n)$ — *effective* maximum context length, the
  minimum of $\ell_{\max}(c)$ and the per-token KV budget
  $T_{KV}/(\alpha \cdot N_{\text{conc}}(w))$. See §3.

Each node $n$ has *effective capacity* $C_n^{\text{eff}} = C_n - \rho_n$
(usable VRAM minus a reserve for OS / page cache).

A user supplies a priority order over *features* (e.g. `[chat, rag, deep-research, image]`). The catalog maps features to workloads
plus optional length requirements: $\text{deep-research}$ implies
$\exists\,(w^*, c, n) \in P :\ \ell_{\text{eff}}(w^*, c, n) \ge 128\,\text{K}$
with $w^* = \text{chat-122b}$ (LDR accumulates search context across iterations, so 128K is required; all chat-122b configs serve 128K).

Workload priority weights are
$\pi_w = 2^{|F|-\text{rank}(f_w)}$ where $f_w$ is the highest-rank feature
that maps to $w$ (with a small baseline for unprioritized workloads). The
doubling makes $\pi_w$ strictly dominate the sum of all lower weights —
this is what gives us lexicographic preferences as a linear objective.

## 2. Decision variables and constraints

We formalize the placement decision as a binary integer program:

$$
\begin{aligned}
x_{w,c,n} &\in \{0,1\} && \text{place } w \text{ in config } c \text{ on } n \\
y_w &\in \{0,1\} && w \text{ has at least } r_w^{\min} \text{ replicas alive}
\end{aligned}
$$

with constraints (see §1 of `solver/milp.py` for the literal LpProblem):

$$
\begin{aligned}
&(\text{C1: single-config-per-node}) &&\sum_c x_{w,c,n} \le 1 && \forall w, n \\
&(\text{C2: replica bounds}) && r_w^{\min} y_w \le \sum_{c,n} x_{w,c,n} \le r_w^{\max} y_w && \forall w \\
&(\text{C3: node capacity}) && \sum_{w,c} m_{w,c,n}\, x_{w,c,n} \le C_n^{\text{eff}} && \forall n \\
&(\text{C4: feature admission}) && \sum_{(w^*,c,n) \in A(F)} x_{w^*,c,n} \ge 1 && \forall F \text{ requested}
\end{aligned}
$$

where $A(F) = \{ (w^*, c, n) : \ell_{\text{eff}}(w^*, c, n) \ge \hat\ell_F \}$
is the set of triples that *admit* feature $F$.

## 3. KV cache as a first-class memory consumer

The analytic per-token KV cost for a model with $L$ KV-bearing layers,
$H$ KV heads, head dim $d$, and KV dtype size $\beta_{kv}$ is

$$
M_{\text{KV}}^{\text{tok}}(w, c) = 2 \cdot L \cdot H \cdot d \cdot \beta_{kv}
$$

The residual KV budget on a node (after weights and activation) is

$$
M_{\text{KV}}(w, c, n) = u(c) \cdot C_n - W_w - M_A(w, c, n)
$$

and the *token capacity* is $T_{KV} = M_{\text{KV}} / M_{\text{KV}}^{\text{tok}}$.
The *effective* max-len with admission margin $\alpha$ and expected
concurrent sessions $N_{\text{conc}}$ is

$$
\ell_{\text{eff}}(w, c, n) = \min\!\left( \ell_{\max}(c),\, \frac{T_{KV}}{\alpha \cdot N_{\text{conc}}(w)} \right)
$$

Layered architectures with mixed full/linear attention (e.g. the
qwen3.5-122b-a10b MoE) count only `full_attention` layers in $L$ —
linear-attention layers contribute
a fixed state that does not scale with sequence length and is folded into $M_A$.

When $\ell_{\max}$ and $\alpha \cdot N_{\text{conc}}$ are both fixed at
problem-build time, $\ell_{\text{eff}}(w, c, n)$ is a precomputable constant
and $A(F)$ is a fixed index set — constraint (C4) stays linear. If a future
extension promotes $N_{\text{conc}}(w)$ to a decision variable (auto-scaling
QoS), then $\ell_{\text{eff}}$ becomes piecewise linear and big-M
linearization applies; see §6.4 for the construction.

## 4. Objective

Placement runs in two phases, encoded in a single linear objective by weight
separation. Phase 1 (*diversity*) brings up one replica of each prioritized
workload; phase 2 (*replica fill*) spends leftover capacity on extra replicas
of the highest-priority workloads. Below these sit KV-QoS, migration,
affinity, and imbalance, optimized as a lexicographic vector ordered as

$$
\max \big(\,\text{diversity},\; \text{replicas},\; \text{KV-QoS},\; -\text{migration},\; \text{affinity},\; -\text{imbalance}\,\big)
$$

implemented with hierarchical weights:

$$
\max\; W_{\text{div}}\!\sum_w \pi_w y_w + W_{\text{rep}}\!\sum_{w,c,n} \pi_w x_{w,c,n} + W_{kv}\!\sum_{w,c,n} \pi_w \rho_{w,c,n} x_{w,c,n} + W_{\text{aff}}\!\sum_{w,c,n} \mu_{w,c,n} a_n x_{w,c,n} - W_{\text{mig}}\, d(P, P_{\text{cur}}) - W_{\text{bal}}\, B(\mathbf{x})
$$

with $W_{\text{div}} \gg W_{\text{rep}} \gg W_{kv} \gg W_{\text{mig}} \gg W_{\text{aff}} \approx W_{\text{bal}}$
(the solver uses $10^9, 10^3, 10, 10^{-2}, 10^{-3}, 10^{-3}$ for div, rep, kv,
mig, aff, bal — migration outranks the affinity/balance nudges, so the planner
won't reshuffle a running workload for a marginal GPU-tier gain). The diversity
term scores each *distinct* active workload ($y_w$) priority-weighted; the
replica term scores every *placement* ($x_{w,c,n}$), so it raises the score
for additional instances of the same workload. Because $W_{\text{div}} \gg W_{\text{rep}}$,
the first instance of even the lowest-priority workload outweighs any number
of extra replicas of higher-priority ones — every distinct capability comes
up before any throughput replica. The QoS ratio
$\rho_{w,c,n} = \min(1, \ell_{\text{eff}}/\ell_{\text{target}}(w))$ is a
constant per triple. $\ell_{\text{target}}(w)$ is the workload's **largest
configured context** (the ceiling of its config grid), not the feature floor
$\hat\ell_F$ — so $\rho$ rewards *upgrading* a placement toward its longest
context and the planner spends genuine slack VRAM on a bigger KV window instead
of leaving it idle. The hard floor $\hat\ell_F$ still gates admission $A(F)$
above; KV-QoS sitting below coverage in the lex order means context is only ever
grown from slack, never by under-serving another workload. The affinity per-triple weight $\mu_{w,c,n} = m_{w,c,n}/\text{GiB}$
is the config's commitment in gibibytes; the per-node score
$a_n = \tau(n) + 0.01 \cdot C_n^{\text{eff}}/\text{GiB}$ has integer tier
$\tau$ (PRO6000 = 5, PRO5000 = 4, RTX5090 = 3, RTX4090 = 2, GB10 = 1, unknown = 0)
plus a small VRAM tiebreaker so two same-tier nodes with different headroom
remain distinguishable. The product $\mu \cdot a$ is thus largest for the
heaviest config landed on the highest-tier node — pulling heavy workloads
toward better GPUs whenever the higher-rank terms are tied. Migration $d$ and
imbalance $B$ are both expressible as linear combinations of $\{x_{w,c,n}\}$.

The doubling $\pi_w = 2^{|F|-\text{rank}}$ combined with $W_{\text{div}} \gg W_{\text{rep}}$
guarantees that no KV-QoS / affinity improvement, nor any number of replicas,
can offset losing a higher-priority distinct workload — i.e. diversity is the
true lexicographic primary. $W_{\text{aff}} < W_{kv}$ ensures KV-QoS still
wins ties; affinity only discriminates among placements that already match on
coverage and effective context.

## 5. NP-hardness

**Proposition.** PWRP-HGC is NP-hard, even when $|F|=0$ (no feature
constraints), $|N| = 2$, all configurations have $\ell_{\max}=0$, and
priorities are uniform.

*Proof sketch.* Restrict to the instance class
- $|N| = 2$ nodes with $C_1^{\text{eff}} = C_2^{\text{eff}} = B$,
- $|W|$ workloads each with a single config of memory $m_w \in \mathbb{Z}_+$,
  $r_w^{\min} = r_w^{\max} = 1$, $\pi_w = m_w$.

Maximizing $\sum_w \pi_w y_w$ is equivalent to selecting a subset
$S \subseteq W$ with $\sum_{w \in S} m_w$ maximum that can be partitioned
into two bins of capacity $B$ each. This is *2-Partition into Bounded Bins*,
which generalizes the classical PARTITION problem and is NP-hard
(Garey & Johnson 1979, SP12). $\square$

When $|N|$ is bounded by a constant the problem is *weakly* NP-hard via the
same reduction; for our deployments ($|N| \le 10$), MILP runtimes stay
well under one second.

## 6. Algorithms

### 6.1 Greedy (baseline)

Order workloads by descending $\pi_w$. For each workload in turn, attempt
to place $r_w^{\min}$ replicas, then optionally a bonus replica up to
$r_w^{\max}$ if residual capacity allows. For each replica, pick the
$(c, n)$ that maximizes $\ell_{\text{eff}}$ with smallest charge as
tiebreaker.

**Approximation analysis.** The coverage function
$f(S) = \sum_{w \in S} \pi_w$ is monotone non-decreasing in the survival
set $S \subseteq W$ and *submodular* — every workload $w$ contributes
$\pi_w$ once or not at all, so $f(S \cup \{w\}) - f(S) = \pi_w \cdot \mathbf{1}[w \notin S]$
is non-increasing in $S$. The feasible region is the intersection of a
per-node knapsack and a per-workload uniform matroid (replica count cap).
The classical Nemhauser-Wolsey-Fisher result (1978) gives a $(1-1/e) \approx 0.632$
worst-case approximation for greedy maximization of a monotone
submodular function over a matroid. A weighted knapsack relaxation
preserves the bound up to constants — see Calinescu et al. 2007 for the
modern continuous-greedy treatment which retains $(1-1/e)$.

In practice — on our concrete cluster — greedy reaches the same coverage
as MILP because the priority weights make the problem strongly monotone
(top-rank workload dominates the budget). Greedy is therefore a useful
sanity-check baseline and a safe fallback when CBC is unavailable.

### 6.2 MILP (exact)

`solver/milp.py` builds the LpProblem above and dispatches CBC via PuLP.
For the cluster sizes we ship ($|N| \le 8$, $|W| \le 20$, $|C_w| \le 8$),
CBC finishes well under a second.

### 6.3 LP relaxation and shadow prices

Relaxing $x_{w,c,n}, y_w \in [0, 1]$ produces a fractional placement and a
set of dual values $\pi_n^{\text{cap}}$ on the per-node capacity rows. By
LP duality, $\pi_n^{\text{cap}}$ is the marginal increase in the objective
per unit of capacity on node $n$ — the *shadow price* of memory. The
sensitivity report exposes these so the operator can answer "which node
should I expand first?" without re-running the planner.

### 6.4 Big-M linearization (extension)

The shipped solver treats $N_{\text{conc}}(w)$ as a catalog constant, which
keeps $\ell_{\text{eff}}$ a precomputed constant and the whole program linear
without big-M variables. If $N_{\text{conc}}(w)$ ever becomes a decision
(auto-scaling QoS), $\ell_{\text{eff}}$ turns into a piecewise-linear function
of integer breakpoints, handled as follows. For each breakpoint $b$, introduce
$z_{w,c,n,b} \in \{0,1\}$ indicating that the chosen $N_{\text{conc}}$ falls
in interval $b$, then
$\ell_{\text{eff}} = \sum_b \ell_b z_{w,c,n,b}$ and feasibility uses
$\sum_b z_{w,c,n,b} = x_{w,c,n}$. The big-M is bounded by
$\max_b \ell_b - \min_b \ell_b$.

## 7. Known limitations and open problems

1. **Co-residency penalty is static input, not decision.** The current
   model treats ComfyUI co-residency as an input (`comfy_co_resident_nodes`),
   which forces the caller to commit to ComfyUI placement before solving.
   A correct treatment introduces $z_n^{\text{comfy}}$ tied to the
   `image-flux` placement and multiplies the per-node memory term by
   $(1 + \delta z_n^{\text{comfy}})$; the product is nonlinear but
   linearizable by McCormick envelopes when $x$ is binary.

2. **Activation model is empirical.** $M_A$ is fit from one observation per
   $(\text{model}, \ell_{\max})$ point. Sparse expert routing (the
   qwen3.5-122b-a10b MoE) inflates $M_A$ in ways not captured by the analytic formula —
   `calibration.py` records the residual but does not yet attribute it to
   architectural causes. A more refined model would split $M_A$ into
   workspace, KV-paging-table, and MoE-routing components.

3. **Concurrency is per-workload static.** $N_{\text{conc}}(w)$ is a catalog
   constant. Real-world load varies (chat is bursty, embed is steady),
   suggesting a stochastic chance-constrained variant where
   $\Pr(\text{admission failure}) \le \epsilon$ replaces (C4).

4. **Greedy's approximation guarantee does not transfer cleanly to lex
   objectives.** $(1 - 1/e)$ holds for the coverage component but not for
   the joint lexicographic objective — a small coverage gain can be
   sacrificed by greedy for a much larger KV-QoS improvement that MILP
   would refuse. EVALUATION.md quantifies this gap.

## References

- Nemhauser, Wolsey, Fisher 1978 — "An analysis of approximations for
  maximizing submodular set functions."
- Calinescu, Chekuri, Pál, Vondrák 2007 — "Maximizing a submodular set
  function subject to a matroid constraint."
- Garey & Johnson 1979 — *Computers and Intractability* (SP12, partition).
- Kwon et al. 2023 — "Efficient Memory Management for Large Language
  Model Serving with PagedAttention" (SOSP).
- Wolsey 1998 — *Integer Programming* (Wiley), §4.2 on LP duality.
