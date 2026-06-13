# 코딩 에이전트 연동 (Claude Code / Codex)

> Anthropic **Claude Code** · OpenAI **Codex CLI** 를 KloudChat 에 연결하는 가이드.
>
> - 발급받은 **본인 API 키**만으로 두 도구의 UX 그대로 사용 — 코드는 외부로 미유출
> - 요청은 KloudChat 의 **LiteLLM 게이트웨이** 경유 → 코딩 특화 모델 `local/qwen3-coder-next` 로 라우팅

## 왜?

- Claude Code / Codex 의 에이전트 · 멀티턴 · 파일 편집 UX 그대로 + 코드는 내부에만
- frontier 모델 개별 청구 없이 사내 게이트웨이로 사용
- LibreChat 과 같은 LiteLLM 게이트웨이 공유 — 키 · 예산 · 로깅이 통합 집계

## 필요한 것

| 항목 | 어디서 |
|---|---|
| **LiteLLM 엔드포인트** (`http://<host>:8000`) | KloudChat 관리자에게 확인 |
| **본인 API 키** (`sk-...`) | 관리자가 `manage.sh` 로 발급 (사용자별 키 · 예산) |

> LibreChat 로그인 때와 동일한 게이트웨이/계정을 가리킨다 — 사용량·예산이 한곳에 집계된다.
> 아래 `<host>:8000` 과 `sk-...` 는 위 두 값으로 바꿔 쓴다.

## Claude Code

환경변수 몇 개로 Claude Code 가 LiteLLM 의 **Anthropic-호환 endpoint** 경유 라우팅:

```bash
export ANTHROPIC_BASE_URL="http://<host>:8000"        # KloudChat LiteLLM 엔드포인트
export ANTHROPIC_AUTH_TOKEN="sk-..."                  # 발급받은 본인 키
export ANTHROPIC_MODEL="local/qwen3-coder-next"
export ANTHROPIC_DEFAULT_OPUS_MODEL="local/qwen3-coder-next"
export ANTHROPIC_DEFAULT_SONNET_MODEL="local/qwen3-coder-next"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="local/gemma-4-26b"

claude
```

- **영구 적용**: 위 줄들을 `~/.bashrc` / `~/.zshrc` 에 등록
- **프로젝트별 관리**: `direnv` (`.envrc`) 권장

**라우팅 흐름**:

- Claude Code 가 `POST /v1/messages` 를 게이트웨이로 전송
- LiteLLM 의 `anthropic_proxy` 가 **Anthropic Messages API → OpenAI chat API** 변환
- `local/qwen3-coder-next` 로 라우팅

### 모델 슬롯 매핑

Claude Code 는 작업 난이도에 따라 4가지 슬롯 사용. 모델↔슬롯 매핑 조정 가능:

| 슬롯 | 매핑 | 용도 |
|---|---|---|
| `ANTHROPIC_MODEL` · OPUS · SONNET | `local/qwen3-coder-next` | 코딩 (어려운 ~ 균형 작업) |
| HAIKU | `local/gemma-4-26b` | 가벼운 작업 |

- **긴 context 작업** (대용량 codebase 분석 · deep search 결과 활용) 잦으면 → SONNET 슬롯에 `local/qwen3.5:122b` 매핑 가능
- 사용 가능 `model_name` 은 LibreChat dropdown · 관리자 안내 참고

## OpenAI Codex CLI

`OPENAI_BASE_URL` 로 endpoint 변경 가능 → LiteLLM 의 **OpenAI 호환 `/v1`** 직접 지정:

```bash
export OPENAI_BASE_URL="http://<host>:8000/v1"
export OPENAI_API_KEY="sk-..."                        # 발급받은 본인 키
codex --model "local/qwen3-coder-next"
```

`~/.codex/config.toml` 에 프로파일 등록 시 매번 flag 불필요:

```toml
[model_providers.kloudchat]
name = "KloudChat"
base_url = "http://<host>:8000/v1"
env_key = "KLOUDCHAT_API_KEY"

[profiles.qwen-coder]
model = "local/qwen3-coder-next"
model_provider = "kloudchat"
```

```bash
KLOUDCHAT_API_KEY="sk-..." codex --profile qwen-coder
```

## 같이 보면 좋은 문서

- [모델 설정](../operator/models.md) — 사용 가능한 `model_name` 카탈로그
