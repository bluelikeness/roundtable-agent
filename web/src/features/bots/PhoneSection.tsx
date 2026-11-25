/**
 * 봇 상태 패널의 "사용할 폰". 여기서 고른 폰이 기본 휴대폰이 된다: 폰을 정하지 않은 봇(PHONE_DEVICE 없음)이
 * 그 폰을 쓴다. 휴대폰은 여러 대가 함께 붙고, 고른다고 다른 폰을 끊지 않는다(폰 게이트웨이 PUT /prefer).
 * 바꾸는 동안에만 몇 초마다 다시 읽는다(평소에는 폴링하지 않는다).
 */
import { useCallback, useEffect, useState } from "react";
import { api, HttpError } from "../../api/rest";
import type { PhoneOut, PhoneRow } from "../../api/types";
import { errorText, notify } from "../../store";

/** 모델 번호 → 알아보기 쉬운 이름(모르는 기기는 받은 이름 그대로) */
const MODEL_NAMES: [RegExp, string][] = [
  [/SM-S938/i, "갤럭시 S25 울트라"],
  [/SM-S928/i, "갤럭시 S24 울트라"],
  [/SM-N960/i, "갤럭시 노트9"],
  [/SM-N975|SM-N976/i, "갤럭시 노트10+"],
  [/SM-X91[06]/i, "갤럭시 탭 S9 울트라"],
  // 그 밖의 갤럭시 탭은 모델이 많아 계열로 묶는다(SM-X…: 탭 S8 이후·A9, SM-T…/SM-P…: 이전 탭)
  [/SM-[XTP]\d{3}/i, "갤럭시 탭"],
];

export function phoneLabel(device: string): string {
  const hit = MODEL_NAMES.find(([re]) => re.test(device));
  return hit ? `${hit[1]} (${device.replace(/^samsung\s+/i, "")})` : device;
}

/** 우선 폰을 골랐는데 아직 그 폰이 붙지 않았다 */
export function switching(p: PhoneOut): boolean {
  return !!p.prefer && !(p.current ?? "").includes(p.prefer);
}

const POLL_MS = 3000;
const POLL_FOR_MS = 120_000;

export function PhoneSection() {
  const [phone, setPhone] = useState<PhoneOut | null>(null);
  const [off, setOff] = useState(false);
  const [pollUntil, setPollUntil] = useState(0);

  const load = useCallback(async () => {
    try {
      setPhone(await api.phone());
    } catch (e) {
      if (e instanceof HttpError && e.status === 404) setOff(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!phone || !switching(phone) || Date.now() > pollUntil) return;
    const t = setTimeout(() => void load(), POLL_MS);
    return () => clearTimeout(t);
  }, [phone, pollUntil, load]);

  if (off) return null;

  const choose = async (device: string) => {
    try {
      setPhone(await api.setPhone(device));
      setPollUntil(Date.now() + POLL_FOR_MS);
      notify(device ? `${phoneLabel(device)}로 바꿉니다. 붙는 데 30초쯤 걸릴 수 있어요` : "먼저 붙은 폰을 씁니다");
    } catch (e) {
      notify(errorText(e), "warn");
    }
  };

  const rows: PhoneRow[] = phone?.devices ?? [];
  const known = rows.some((r) => r.device === phone?.prefer);
  return (
    <section className="phone-section" data-testid="phone-section">
      <label className="phone-title" htmlFor="phone-select">
        사용할 폰
      </label>
      {!phone ? (
        <div className="muted">확인 중</div>
      ) : (
        <>
          <select
            id="phone-select"
            className="model-select phone-select"
            value={phone.prefer}
            onChange={(e) => void choose(e.target.value)}
          >
            <option value="">먼저 붙은 폰</option>
            {rows.map((r) => (
              <option key={r.device} value={r.device}>
                {phoneLabel(r.device)}
                {r.android ? ` · 안드로이드 ${r.android}` : ""}
              </option>
            ))}
            {phone.prefer && !known && <option value={phone.prefer}>{phone.prefer}</option>}
          </select>
          <div className={`phone-state${phone.current ? "" : " down"}`}>
            {phone.current ? `연결: ${phoneLabel(phone.current)}` : "연결된 폰 없음"}
            {switching(phone) && <span className="muted"> · 바꾸는 중</span>}
          </div>
          {switching(phone) && (
            <div className="hint">고른 폰에서 워커 앱이 켜져 있어야 합니다. 붙으면 봇 상태를 다시 확인합니다.</div>
          )}
        </>
      )}
    </section>
  );
}
