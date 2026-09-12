# Grounded best-effort answers

## Policy change

On 2026-09-12 the user requested that PR #184 cover any subject, not just
political questions, and provide useful evidence-based answers instead of
automatically withholding them when current verification is unavailable.

The revised policy applies to all conversational model answers, including
local Qwen, manual selection, Auto economy/quality and comparison columns.
It asks the model to distinguish supplied evidence, remembered knowledge,
inference, examples and unknown details. A possibly outdated fact must not be
presented as an established current fact. Missing quantities, names, citations,
source URLs and training dates must not be invented.

The legacy political detector remains only an optional search hint and a way to
recognize earlier stored policy replies. It no longer gates answer availability.
Existing search OFF, explicit search refusal, strict-local, Privacy decisions,
tool allowlists, NCS preflight and execution limits remain authoritative.
Unusable search results pass through normal privacy processing before the model
continues with a trusted uncertainty instruction. No extra retry loop is added.

## Deterministic notice

The server appends one last sentence after a nonempty completed chat answer and
after each completed comparison column. The same suffix is stored and streamed;
it does not manufacture extra model tokens or change the selected model's usage.
Empty, failed or stopped chat turns do not become artificial notice-only answers.

For a catalogue-declared `2025-01`, the Korean example is:

> 다만 이 모델의 학습 기준은 2025년 1월로 제공되어 이후의 변화가 반영되지 않았거나 답변이 부정확할 수 있으므로 최신 사실은 별도 검증이 필요합니다.

When the date is unavailable, the example is:

> 다만 이 답변은 부정확할 수 있으며, 이 모델의 학습 기준 연월을 확인할 수 없어 최신 사실은 별도 검증이 필요합니다.

These dates are synthetic examples, not a declaration about the deployed Qwen.
English notices follow the same contract. Quoted translation inputs do not
choose the notice language; explicit Korean/English output requests take priority.

## Cutoff provenance

- `ModelInfo.knowledgeCutoff` is `YYYY-MM` or null.
- Only explicit `model_info.knowledge_cutoff` or `training_cutoff` fields in the
  configured LiteLLM catalogue are used. Strict ISO months and valid ISO dates
  are accepted and normalized to a month.
- Missing, malformed, future, year-only or conflicting values remain unknown.
  Every deployment of one alias, including hidden routing twins, must agree.
- Model aliases, provider names, launch dates, user prompts, documents and model
  self-reports are not sources for a training cutoff.
- An observed fallback model never inherits the selected model's date. The
  final stream does not perform another catalogue lookup; such a cutoff is unknown.
- Catalogue metadata is not an independent attestation of the served weights.

The `routing.accuracy` object records the policy version, effective cutoff and
`model_catalogue` versus `unknown` provenance without changing Privacy routing.

## Document surfaces

Report, slide and page requests no longer get a freshness-specific 409. Their
writer instructions ask for supported prose and an in-schema uncertainty note;
prose is never appended outside structured JSON. A completed artifact also gets
a deterministic caveat in the completion message and stream, including after
reload. Without a complete executed-writer trace its date remains unknown.

The artifact's sections, slides, HTML and source arrays are not rewritten by
the deterministic notice code. A caveat inside an exported document is therefore
prompt-driven, not guaranteed by a postprocessor. The page/report-template path
now persists its previously missing completion message; its pre-existing
accounting asymmetry is unchanged and is not a billing fix in this PR.

## Verification

Source: PR #184 revision based on parent `3bc31f7` and upstream/main `f537972`.
The original checkout and user runtime/database were not changed.

| Check | Result | Scope |
| --- | --- | --- |
| Full offline API | 3,047 passed, 1 skipped, 11 existing warnings | Actual application code with synthetic dependencies |
| New cutoff metadata tests | 43 passed; all 43 failed before implementation | Validation, duplicate aliases and no inferred dates |
| New agent policy tests | 34 passed; staged regressions reproduced before fixes | Search continuation, permissions, source claims and masking |
| Pure accuracy notice tests | 38 passed | Every domain, unknown/fallback dates, language and envelope isolation |
| Chat/comparison runtime tests | 28 passed | Stream/persistence agreement, Auto identity, errors and usage |
| Document completion tests | 32 passed; old runners failed 12 of 32 | Four surfaces, unchanged artifacts and completion persistence |
| Development Playwright | 40 passed, 0 skipped | New answer cases plus legacy saved-message rendering |
| Production Playwright | Same 40 passed, 0 skipped | Built React UI, controlled API/SSE fixtures |
| API Ruff, Web build/Oxlint, diff checks | Passed | Existing phone CSS and large-chunk build warnings retained |

The 40 browser cases consist of 30 streamed-and-reloaded answers across five
domains, three cutoff states and two viewports; six initially saved normal
answers; and four legacy saved policy responses. The repeated dev/production
runs are not 80 unique cases. Six actual Chromium screenshots use synthetic
answers and dates. No model completion or credential retrieval was performed
for this revision. The offline full suite denied 82 socket attempts and made
zero real HTTP requests; browser application APIs were fixture-controlled.

## Limits and merge guidance

This is hallucination mitigation and transparent uncertainty, not a universal
truth detector. A disclaimer cannot make an unsupported answer correct. No
real-Qwen hallucination reduction percentage, provider-locality proof or general
factual-correctness result is claimed. Earlier political-abstention trials are
historical and cannot be reused as evidence for this new behavior.

PR #183 is still separate. When resolving shared session/agent code, preserve
its required calculation gate, read prerequisites and tool-origin answers.
Only model-generated answers should receive a model-training notice. Do not
restore the removed freshness hard abort from the old combined QA reference;
rerun cross-policy tests against the actual merged main.
