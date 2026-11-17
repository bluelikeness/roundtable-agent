/**
 * 봇 상태 패널 (docs/05-ui.md 4장). 데스크톱은 오른쪽 260px 기둥, 모바일은 바텀시트 안에 같은 내용.
 * 값은 bot.status 이벤트로만 갱신한다(폴링 없음). 문제가 있는 봇만 경고색.
 */
import { useEffect, useRef, useState } from "react";
import type { BotInfo } from "../../api/types";
import { botColor } from "../../lib/colors";
import {
  openSheet,
  probeBot,
  resetBot,
  setBotEnabled,
  setUi,
  useStore,
} from "../../store";
import { PhoneSection } from "./PhoneSection";
import { formatTokens, rowView } from "./status";

function useMinuteTick(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function BotRow({ bot, color, now }: { bot: BotInfo; color: string; now: number }) {
  const st = useStore((s) => s.botStatus[bot.name]);
  const hasSession = useStore((s) => s.activeId !== null);
  const [menu, setMenu] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const v = rowView(st, now);

  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menu]);

  const act = (f: () => void) => () => {
    setMenu(false);
    f();
  };

  return (
    <li className={`bot-row tone-${v.tone}`} data-testid={`bot-row-${bot.name}`}>
      <span className={`dot ${v.tone}`} aria-hidden />
      <div className="bot-text">
        <div className="bot-line">
          <span className="bot-label" style={{ color }}>
            {bot.name}
          </span>
          <span className="bot-state">{v.label}</span>
        </div>
        {v.sub && <div className="bot-sub muted">{v.sub}</div>}
      </div>
      <div className="bot-actions" ref={ref}>
        {v.action === "relogin" && (
          <button className="btn small" onClick={() => openSheet({ kind: "login", bot: bot.name })}>
            재로그인
          </button>
        )}
        {v.action === "enable" && (
          <button className="btn small" onClick={() => void setBotEnabled(bot.name, true)}>
            활성화
          </button>
        )}
        <button className="icon" aria-label={`${bot.name} 메뉴`} aria-expanded={menu} onClick={() => setMenu(!menu)}>
          ⋯
        </button>
        {menu && (
          <div className="menu" role="menu">
            <button role="menuitem" onClick={act(() => void probeBot(bot.name))}>
              상태 새로고침
            </button>
            <button role="menuitem" onClick={act(() => openSheet({ kind: "login", bot: bot.name }))}>
              재로그인
            </button>
            {hasSession && (
              <button role="menuitem" onClick={act(() => void resetBot(bot.name))}>
                대화 초기화
              </button>
            )}
            <button role="menuitem" onClick={act(() => openSheet({ kind: "logs", bot: bot.name }))}>
              로그 보기
            </button>
            {v.action === "enable" ? (
              <button role="menuitem" onClick={act(() => void setBotEnabled(bot.name, true))}>
                활성화
              </button>
            ) : (
              <button role="menuitem" className="danger" onClick={act(() => void setBotEnabled(bot.name, false))}>
                비활성화
              </button>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

function ClaudeSection() {
  const c = useStore((s) => s.claude);
  return (
    <section className={`claude-section${c && !c.runner_ok ? " down" : ""}`} data-testid="claude-section">
      <div className="claude-title">Claude Code</div>
      {!c ? (
        <div className="muted">확인 중</div>
      ) : (
        <>
          <div>
            {c.runner_ok ? "러너 연결" : "러너 응답 없음"}
            <span className="muted"> · 오늘 {c.runs_today}회 · {formatTokens(c.tokens_today)} 토큰</span>
          </div>
          {!c.runner_ok && (
            <div className="hint">
              러너 컨테이너 상태를 확인하세요. 로그인이 풀렸다면 러너 컨테이너에서 <code>claude login</code> 필요
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function BotPanelBody() {
  const bots = useStore((s) => s.bots);
  const now = useMinuteTick();
  return (
    <>
      <ul className="bot-list">
        {bots.map((b) => (
          <BotRow key={b.name} bot={b} color={botColor(b.name, bots)} now={now} />
        ))}
        {bots.length === 0 && <li className="muted">등록된 봇이 없습니다</li>}
      </ul>
      <PhoneSection />
      <ClaudeSection />
    </>
  );
}

/** 데스크톱 오른쪽 기둥 */
export function BotPanel({ onCollapse }: { onCollapse: () => void }) {
  return (
    <aside className="bot-panel" aria-label="봇 상태">
      <div className="panel-head">
        <span>봇 상태</span>
        <button className="icon" aria-label="봇 상태 접기" onClick={onCollapse}>
          »
        </button>
      </div>
      <BotPanelBody />
    </aside>
  );
}

/** 모바일 헤더: 봇별 색 점 + 문제 배지. 탭하면 바텀시트 */
export function StatusDots() {
  const bots = useStore((s) => s.bots);
  const status = useStore((s) => s.botStatus);
  const problems = bots.filter((b) => rowView(status[b.name]).problem).length;
  return (
    <button
      className="status-dots"
      aria-label={problems ? `봇 상태 (문제 ${problems}개)` : "봇 상태"}
      onClick={() => setUi({ botSheet: true })}
      data-testid="status-dots"
    >
      {bots.map((b) => {
        const v = rowView(status[b.name]);
        return (
          <span
            key={b.name}
            className={`hdot ${v.tone}`}
            style={{ background: v.tone === "ok" ? botColor(b.name, bots) : undefined }}
          />
        );
      })}
      {problems > 0 && <span className="bang">!</span>}
    </button>
  );
}
