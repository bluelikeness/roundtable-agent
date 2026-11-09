/**
 * 재로그인 시트 (docs/05-ui.md 4장). POST /api/bots/<bot>/login/start 응답의 flow 로 CodeLogin·VncLogin 을 고른다.
 * UI 에 봇별 분기를 두지 않는다. 값(코드·비밀번호)은 허브로 바로 보내고 어디에도 저장하지 않는다.
 */
import { useEffect, useState, type FormEvent } from "react";
import { api, HttpError } from "../../api/rest";
import type { LoginStart } from "../../api/types";
import { useMobile } from "../../lib/media";

type Phase = { kind: "loading" } | { kind: "error"; text: string } | { kind: "ready"; start: LoginStart } | { kind: "done"; ok: boolean };

function errText(e: unknown): string {
  if (e instanceof HttpError) {
    if (e.code === "adapter_unreachable") return "어댑터에 연결할 수 없습니다";
    return `허브 오류 (${e.status} ${e.code})`;
  }
  return "허브에 연결할 수 없습니다";
}

export function LoginSheetBody({ bot, onClose }: { bot: string; onClose: () => void }) {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });

  useEffect(() => {
    let alive = true;
    api
      .loginStart(bot)
      .then((start) => alive && setPhase({ kind: "ready", start }))
      .catch((e) => alive && setPhase({ kind: "error", text: errText(e) }));
    return () => {
      alive = false;
    };
  }, [bot]);

  if (phase.kind === "loading") return <p className="muted">로그인 흐름을 시작하는 중…</p>;
  if (phase.kind === "error") return <p className="error">{phase.text}</p>;
  if (phase.kind === "done") return <Done ok={phase.ok} onClose={onClose} />;
  const finish = (ok: boolean) => setPhase({ kind: "done", ok });
  return phase.start.flow === "code" ? (
    <CodeLogin bot={bot} hint={phase.start.hint} onDone={finish} />
  ) : (
    <VncLogin bot={bot} hint={phase.start.hint} vncUrl={phase.start.vnc_url} onDone={finish} />
  );
}

function Done({ ok, onClose }: { ok: boolean; onClose: () => void }) {
  return (
    <div className="login-done" data-testid="login-done">
      <p className={ok ? "" : "error"}>{ok ? "로그인되었습니다." : "로그인을 확인하지 못했습니다. 다시 시도해 주세요."}</p>
      <button className="btn" onClick={onClose}>
        닫기
      </button>
    </div>
  );
}

/** 인증코드형: 1단계 코드 → 필요하면 2단계 비밀번호 → 완료 */
export function CodeLogin({ bot, hint, onDone }: { bot: string; hint: string | null; onDone: (ok: boolean) => void }) {
  const [step, setStep] = useState<"code" | "password">("code");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!value.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.loginSubmit(bot, step === "code" ? { code: value.trim() } : { password: value });
      setValue("");
      if (r.ok) onDone(true);
      else if (r.next === "password") setStep("password");
      else setError("확인되지 않았습니다. 다시 입력해 주세요.");
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="login-form" onSubmit={submit} data-testid="code-login">
      <p>{step === "code" ? "인증코드를 보냈습니다." : "비밀번호가 필요합니다."}</p>
      {hint && step === "code" && <p className="muted">{hint}</p>}
      <label>
        {step === "code" ? "인증코드" : "비밀번호"}
        <input
          autoFocus
          type={step === "code" ? "text" : "password"}
          inputMode={step === "code" ? "numeric" : undefined}
          autoComplete={step === "code" ? "one-time-code" : "current-password"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button className="btn primary" disabled={busy || !value.trim()}>
        {busy ? "확인 중…" : "확인"}
      </button>
    </form>
  );
}

/** noVNC 수동 로그인: 화면에서 로그인을 마치고 완료를 누른다. 실제 noVNC 연결은 P2 */
export function VncLogin({
  bot,
  hint,
  vncUrl,
  onDone,
}: {
  bot: string;
  hint: string | null;
  vncUrl: string | null;
  onDone: (ok: boolean) => void;
}) {
  const mobile = useMobile();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const complete = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.loginComplete(bot);
      onDone(r.ok);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="vnc-login" data-testid="vnc-login">
      {mobile && <p className="hint">가로 모드로 돌리면 화면이 더 잘 보입니다.</p>}
      {vncUrl ? (
        <iframe className="vnc-frame" src={vncUrl} title={`${bot} 로그인 화면`} />
      ) : (
        <div className="vnc-frame placeholder">noVNC 화면이 여기에 열립니다 (어댑터 연결 후)</div>
      )}
      {hint && <p className="muted">{hint}</p>}
      <p>로그인을 마치면 완료를 누르세요.</p>
      {error && <p className="error">{error}</p>}
      <button className="btn primary" onClick={() => void complete()} disabled={busy}>
        {busy ? "확인 중…" : "완료"}
      </button>
    </div>
  );
}
