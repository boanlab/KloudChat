import { ApiError } from '@/lib/api'

/** Where in the agent form a refusal points: the form opens that part and focuses it. */
export type AgentErrorPlace = 'name' | 'gap' | 'settings' | 'output' | 'tools' | 'advanced' | 'process'

/** Message and place for every agent-save refusal code; `tests/test_agent_save_errors.py` checks coverage. */
const AGENT_SAVE_ERRORS: Record<string, { message: string; place?: AgentErrorPlace }> = {
  slug_taken: { message: '이미 쓰는 핸들입니다. 다른 핸들을 붙이세요.', place: 'name' },
  not_found: { message: '에이전트를 찾을 수 없습니다. 목록을 새로 고친 뒤 다시 여세요.' },
  agent_retired: { message: '더 이상 제공하지 않는 에이전트라 바꿀 수 없습니다.' },
  duplicate_skill_ids: { message: '스킬과 도구 › 스킬에 같은 스킬이 두 번 들어 있습니다.', place: 'tools' },
  invalid_skill_ids: { message: '스킬과 도구 › 스킬에 쓸 수 없는 스킬이 있습니다. 다시 고르세요.', place: 'tools' },
  duplicate_tool_names: { message: '스킬과 도구 › 도구에 같은 도구가 두 번 들어 있습니다.', place: 'tools' },
  unknown_tools: { message: '스킬과 도구 › 도구에 지금 쓸 수 없는 도구가 있습니다. 빼고 저장하세요.', place: 'tools' },
  check_skill_unknown: { message: '검사 스킬을 찾을 수 없습니다. 추가로 할 일의 검사를 확인하세요.', place: 'process' },
  design_system_not_found: { message: '테마를 찾을 수 없습니다. 템플릿·테마에서 테마를 다시 고르세요.', place: 'output' },
  design_template_not_found: { message: '템플릿을 찾을 수 없습니다. 템플릿·테마에서 템플릿을 다시 고르세요.', place: 'output' },
  design_template_surface_mismatch: {
    message: '템플릿이 사용할 화면과 맞지 않습니다. 템플릿·테마의 템플릿이나 고급 › 사용할 화면을 바꾸세요.',
    place: 'output',
  },
  agent_surface_unknown: { message: '처음 여는 화면을 알 수 없습니다. 고급 › 처음 여는 화면을 다시 고르세요.', place: 'advanced' },
  agent_surface_not_in_kinds: { message: '처음 여는 화면이 사용할 화면에 없습니다. 고급 › 사용할 화면을 확인하세요.', place: 'advanced' },
  doc_format_unknown: { message: '문서 형식을 찾을 수 없습니다. 템플릿·테마에서 다시 고르세요.', place: 'output' },
  doc_format_needs_report: { message: '문서 형식은 보고서 화면에서만 씁니다. 고급 › 사용할 화면에 보고서를 켜세요.', place: 'advanced' },
  gap_detector_unknown: { message: '빠진 정보 질문: 「자료가 답했는지 판단」 방법을 고르세요.', place: 'gap' },
  gap_keywords_empty: { message: '빠진 정보 질문: 「이 낱말이 있으면 답한 것」에 낱말을 하나 이상 적으세요.', place: 'gap' },
  gap_pattern_unsafe: {
    message: '빠진 정보 질문: 패턴은 300자 안에서 반복 횟수를 {0,10}처럼 정해서 쓰고, 반복 안에 반복을 넣지 않습니다.',
    place: 'gap',
  },
  gap_too_many: { message: '빠진 정보 질문은 12개까지입니다.', place: 'gap' },
  gap_id_invalid: { message: '빠진 정보 질문의 번호가 겹칩니다. 그 질문을 지우고 다시 추가하세요.', place: 'gap' },
  gap_question_empty: { message: '빠진 정보 질문: 「물어볼 것」을 적으세요.', place: 'gap' },
  process_sections: { message: '세부 설정: 목차는 12개까지입니다.', place: 'settings' },
  process_table: { message: '세부 설정: 필수 표는 3개까지, 표마다 열 이름을 2~8개 적으세요.', place: 'settings' },
  process_summary: { message: '세부 설정: 핵심 요약 줄 수는 1~4 사이로 적으세요.', place: 'settings' },
  process_style: { message: '세부 설정: 문체는 개조식만 고를 수 있습니다.', place: 'settings' },
  process_pages: { message: '세부 설정: 쪽수 제한은 1~30 사이로 적으세요.', place: 'settings' },
  process_prepass: { message: '세부 설정: 쓰기 전 계산은 다섯 개까지, 목록에 있는 것만 고를 수 있습니다.', place: 'settings' },
  process_date_base: { message: '세부 설정: 날짜 기준은 자료의 날짜나 회의 날짜입니다.', place: 'settings' },
  process_stage: { message: '세부 설정: 값 맞추기에 모르는 항목이 있습니다. 다시 고르세요.', place: 'settings' },
  process_check: { message: '세부 설정: 계산 확인에 모르는 항목이 있습니다. 다시 고르세요.', place: 'settings' },
  process_judged: { message: '세부 설정: 판정 검사에 모르는 항목이 있습니다. 다시 고르세요.', place: 'settings' },
  process_amend: { message: '세부 설정: 빠진 것 채우기에 모르는 항목이 있습니다. 다시 고르세요.', place: 'settings' },
  process_head: { message: '세부 설정: 머리 채우기에 모르는 항목이 있습니다. 다시 고르세요.', place: 'settings' },
  process_rules: { message: '세부 설정: 검토 규칙은 여덟 개까지입니다.', place: 'settings' },
  process_draws: { message: '세부 설정: 그림 설정을 고르세요.', place: 'settings' },
  process_outline: { message: '세부 설정: 고정 목차를 쓰려면 목차를 적으세요.', place: 'settings' },
  process_mode_unknown: { message: '진행 과정을 다시 고르세요.', place: 'process' },
  process_invalid: { message: '진행 과정을 읽지 못했습니다. 진행 과정을 다시 고르세요.', place: 'process' },
  pipeline_not_found: { message: '가져오려던 순서를 찾을 수 없습니다. 진행 과정에서 다시 고르세요.', place: 'process' },
  base_fixed: { message: '기본으로 제공되는 에이전트는 기본 과정을 바꿀 수 없습니다.', place: 'process' },
  base_unknown: { message: '고를 수 없는 기본 과정입니다. 진행 과정에서 다시 고르세요.', place: 'process' },
  base_required: { message: '진행 과정에서 기본 과정을 골라 주세요.', place: 'process' },
  custom_not_offered: { message: '이 기본 과정은 직접 구성할 수 없습니다. 기본 과정 그대로 쓰세요.', place: 'process' },
  bound_surface: {
    message: '마지막 단계가 만드는 것과 이 에이전트가 만드는 것이 다릅니다. 진행 과정의 「이 에이전트가 만드는 것」을 확인하세요.',
    place: 'process',
  },
  invalid_pipeline: { message: '진행 과정의 단계를 확인해 주세요. 문제가 있는 단계에 이유가 적혀 있습니다.', place: 'process' },
}

const CODE = /^([a-z][a-z0-9_]*)(?::.*)?$/
const HANGUL = /[가-힣]/

/** A failed agent save's sentence and place; never a machine code. */
export function agentSaveError(err: unknown, fallback: string): { message: string; place?: AgentErrorPlace } {
  if (!(err instanceof ApiError)) return { message: fallback }
  const code = CODE.exec(err.detail ?? '')?.[1] ?? ''
  const known = code ? AGENT_SAVE_ERRORS[code] : undefined
  if (known) return known
  if (err.status < 500 && HANGUL.test(err.detail ?? '')) return { message: err.detail }
  return { message: fallback }
}
