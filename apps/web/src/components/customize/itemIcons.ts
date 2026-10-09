import {
  Bot,
  BookOpen,
  Calculator,
  CalendarDays,
  ChartColumn,
  ClipboardCheck,
  Code2,
  FileText,
  GraduationCap,
  Image,
  Languages,
  ListChecks,
  Mail,
  Megaphone,
  Palette,
  PenLine,
  Presentation,
  Scale,
  SearchCheck,
  ShieldCheck,
  Sparkles,
  Table2,
  Users,
  Workflow,
  type LucideIcon,
} from 'lucide-react'
import { kindMeta } from '@/lib/kinds'
import type { SessionKind } from '@/types'

/** What a hub item is; decides the last-resort icon. */
export type ItemType = 'agent' | 'skill' | 'workflow' | 'design'

/** First matching keyword wins; Korean and English both, lower-cased before the test. */
const KEYWORDS: [RegExp, LucideIcon][] = [
  [/슬라이드|발표|덱|deck|slide|presentation/, Presentation],
  [/논문|연구|학회|강의|수업|교육|paper|research|lecture/, GraduationCap],
  [/데이터|분석|통계|지표|차트|data|analy|chart|metric/, ChartColumn],
  [/표|엑셀|스프레드|csv|excel|sheet|table/, Table2],
  [/번역|translat/, Languages],
  [/코드|개발|프로그래|code|develop|program/, Code2],
  [/메일|이메일|편지|mail|letter/, Mail],
  [/회의|인터뷰|면담|meeting|interview/, Users],
  [/검토|리뷰|검수|교정|review|proofread/, SearchCheck],
  [/점검|체크|확인|check|audit/, ClipboardCheck],
  [/일정|계획|로드맵|schedule|plan|roadmap/, CalendarDays],
  [/법|계약|규정|정책|legal|contract|policy/, Scale],
  [/예산|견적|비용|가격|계산|budget|quote|cost|price|calc/, Calculator],
  [/보안|개인정보|security|privacy/, ShieldCheck],
  [/홍보|보도|마케팅|press|marketing/, Megaphone],
  [/디자인|서식|양식|브랜드|design|brand|template/, Palette],
  [/이미지|그림|사진|image|picture|photo/, Image],
  [/요약|정리|목록|summar|list/, ListChecks],
  [/글|작성|초안|자기소개|경력|write|draft|resume/, PenLine],
  [/문서|보고|공문|기안|report|document|memo/, FileText],
  [/읽기|독서|자료|read|knowledge/, BookOpen],
]

const BY_TYPE: Record<ItemType, LucideIcon> = {
  agent: Bot,
  skill: Sparkles,
  workflow: Workflow,
  design: Palette,
}

/** An icon for an item from its name and summary, else its first surface, else its type. */
export function itemIcon(type: ItemType, text: string, surfaces: SessionKind[] = []): LucideIcon {
  const needle = text.toLowerCase()
  for (const [pattern, icon] of KEYWORDS) if (pattern.test(needle)) return icon
  const surface = surfaces.find((k) => k !== 'chat') ?? (type === 'agent' ? undefined : surfaces[0])
  if (surface) return kindMeta[surface].icon
  return BY_TYPE[type]
}
