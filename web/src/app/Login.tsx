import { useState, type FormEvent } from "react";
import { HttpError } from "../api/rest";
import { login } from "../store";

const ERRORS: Record<string, string> = {
  invalid_credentials: "아이디·비밀번호·인증 코드가 맞지 않습니다",
  locked: "로그인 시도가 많아 잠시 잠겼습니다. 나중에 다시 시도하세요",
};

export function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password, totp);
    } catch (err) {
      setError(err instanceof HttpError ? (ERRORS[err.code] ?? `로그인 실패 (${err.code})`) : "허브에 연결할 수 없습니다");
      setPassword("");
      setTotp("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <form onSubmit={onSubmit}>
        <h1>Roundtable-Agent</h1>
        <label>
          아이디
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label>
          비밀번호
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        <label>
          인증 코드(TOTP)
          <input
            value={totp}
            onChange={(e) => setTotp(e.target.value.replace(/\D/g, ""))}
            inputMode="numeric"
            autoComplete="one-time-code"
            minLength={6}
            maxLength={8}
            required
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="btn primary block" disabled={busy}>
          로그인
        </button>
      </form>
    </main>
  );
}
