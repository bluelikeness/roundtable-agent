// Claude 정리의 확정 질문 감지 (docs/05-ui.md 2장 "Claude 정리 안의 버튼", 9장 미결).
// 1차 신호는 봇 응답의 structured.confirmation_required 다(MessageList latestKeys → Final confirmPending).
// 이 정규식은 보조 신호: 마커가 없는 봇(원문이 확정을 묻지만 어댑터가 못 뽑은 경우)에서 마지막 줄이
// "진행할까요?"류면 UI 가 [진행] [취소] 를 붙인다. 버튼은 입력을 대신 쓸 뿐 별도 API 가 아니다.

const CONFIRM_RE = /(진행|확정|결제|예약|구매|주문|신청|전송|보낼|실행)[^\n]*(할까요|하시겠어요|하시겠습니까|해도 될까요|할지 알려)/;

// 프롬프트(orchestrator.md)는 "진행할까요?"로 끝내라고 하지만, '"진행해"라고 말씀해 주세요'로 끝내는 경우도 받는다
const SAY_CONFIRM_RE = /["“'‘]진행해["”'’]\s*라고/;

export const CONFIRM_TEXT = "진행해";
export const CANCEL_TEXT = "취소";

export function isConfirmQuestion(text: string): boolean {
  const lines = text
    .split("\n")
    .map((l) => l.replace(/[*_`>#]/g, "").trim())
    .filter(Boolean);
  const last = lines[lines.length - 1] ?? "";
  return CONFIRM_RE.test(last) || SAY_CONFIRM_RE.test(last);
}
