/**
 * The names the interface gives the detector's categories.
 *
 * Shared by the composer's decision dialog and the transcript: a category
 * somebody accepted under one name must not come back under another one turn
 * later.
 */
export const FINDING_LABEL: Record<string, string> = {
  email: '이메일',
  phone: '전화번호',
  government_id: '주민 식별번호',
  payment_card: '결제카드',
  ip_address: 'IP 주소',
  api_key: 'API 키',
  jwt: 'JWT',
  private_key: '개인키',
}
