// 로그 보기 (docs/05-ui.md 4장): 어댑터 GET /logs 를 허브가 프록시. 디버깅용이라 꾸미지 않는다.
import { useEffect, useState } from "react";
import { api } from "../../api/rest";
import type { BotLogEntry } from "../../api/types";
import { absolute } from "../../lib/time";

const LIMIT = 20;

function cell(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
}

export function BotLogsBody({ bot }: { bot: string }) {
  const [rows, setRows] = useState<BotLogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .botLogs(bot)
      .then((r) => alive && setRows(Array.isArray(r) ? r.slice(-LIMIT).reverse() : []))
      .catch(() => alive && setError("로그를 불러오지 못했습니다"));
    return () => {
      alive = false;
    };
  }, [bot]);

  if (error) return <p className="error">{error}</p>;
  if (!rows) return <p className="muted">불러오는 중…</p>;
  if (!rows.length) return <p className="muted">기록이 없습니다</p>;
  return (
    <table className="logs" data-testid="bot-logs">
      <thead>
        <tr>
          <th>시각</th>
          <th>입력</th>
          <th>ok</th>
          <th>ms</th>
          <th>error</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td>{typeof r.at === "string" ? absolute(r.at) : cell(r.at ?? r.ts)}</td>
            <td>{cell(r.text ?? r.input).slice(0, 40)}</td>
            <td>{cell(r.ok)}</td>
            <td>{cell(r.latency_ms)}</td>
            <td>{cell(r.error)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
