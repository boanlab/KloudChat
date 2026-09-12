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

## Conditional uncertainty

The later user feedback on 2026-09-12 supersedes the blanket notice policy in
`31b68a5`. The server no longer appends a warning to every successful response.
Greetings, straightforward arithmetic, translations and established explanations
should be direct. An unknown training cutoff alone is not evidence of uncertainty.

The model is instructed to identify only materially unresolved claims and explain
why: missing evidence, conflicting sources, estimates or possibly changed facts.
A training-cutoff notice is requested only for material freshness uncertainty,
not for every missing input or estimate. No question-keyword confidence classifier,
additional judge call or verification retry is introduced. The model's judgment
can still be wrong; this is not a calibrated factual-confidence detector.

The server only normalizes an already-generated, recognized trailing notice:
duplicates (including the Korean variant without the initial `다만`) become one
notice with the actual model's catalogue metadata. Full-answer retract/delta
events keep the stream and stored content aligned without removing an earlier
quoted occurrence. Plain answers and claim-specific uncertainty are unchanged.
Usage remains the provider-reported usage; normalization invents no model tokens.
Empty, failed or stopped chat turns do not become artificial notice-only answers.

Recognized old boilerplate is removed from assistant-history copies sent to the
model, reducing repetitive imitation. Stored history, user/tool text and quoted
or fenced examples are not rewritten. Policy metadata remains compatible with
earlier no-search follow-up consent handling.

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
writer instructions ask for supported prose and a claim-specific uncertainty note
only when needed. Prose is never appended outside structured JSON. Successful
artifact creation acknowledgements no longer get a training-cutoff warning.
Without a complete executed-writer trace its date remains unknown.

The artifact's sections, slides, HTML and source arrays are not rewritten by
the deterministic notice code. A caveat inside an exported document is therefore
prompt-driven, not guaranteed by a postprocessor. The page/report-template path
now persists its previously missing completion message; its pre-existing
accounting asymmetry is unchanged and is not a billing fix in this PR.

## Verification

Source: conditional revision of PR #184, parent `31b68a5`, upstream/main `f537972`.
Earlier full-suite and live-model figures describe earlier revisions, not this one.

| Check | Result | Scope |
| --- | --- | --- |
| Full offline API | 3,075 passed, 1 skipped, 11 existing warnings | Actual application code with synthetic dependencies; 82 blocked socket attempts, zero real HTTP requests |
| New cutoff metadata tests | 43 passed; all 43 failed before implementation | Validation, duplicate aliases and no inferred dates |
| New agent policy tests | 34 passed; staged regressions reproduced before fixes | Search continuation, permissions, source claims and masking |
| Pure accuracy notice tests | 48 passed | Conditional instructions, exact normalization, dates, history and language |
| Chat/comparison + document runtime tests | 78 passed; before this fix 50 failed and 28 passed | No blanket footer, duplicate normalization, stream/store, fallback, errors and usage |
| Production Playwright | 64 passed, 0 skipped/flaky/unexpected | Built React UI, controlled API/SSE fixtures |
| API Ruff, Web build/Oxlint, diff checks | Passed | Existing phone CSS and large-chunk build warnings retained |

The 64 browser cases consist of 54 streamed-and-reloaded answers across nine
domains, three cutoff states and two viewports; six initially saved uncertain
answers; and four legacy saved policy responses. Ten `conditional-*` screenshots
show synthetic arithmetic/greetings without notices and uncertain answers with
one notice; both model label and conversation title identify synthetic data.
These mocks validate rendering, not actual Qwen uncertainty judgment. No model
completion is required by these code/browser regression tests.

## Limits and merge guidance

This is hallucination mitigation and transparent uncertainty, not a universal
truth detector. A disclaimer cannot make an unsupported answer correct. No
real-Qwen hallucination reduction percentage, provider-locality proof or general
factual-correctness result is claimed. Earlier political-abstention trials are
historical and cannot be reused as evidence for this new behavior.

PR #183 is still separate. When resolving shared session/agent code, preserve
its required calculation gate, read prerequisites and tool-origin answers.
Only model-generated answers with material freshness uncertainty should receive
a model-training notice; tool results and completion receipts should not. Do not
restore the removed freshness hard abort from the old combined QA reference;
rerun cross-policy tests against the actual merged main.
