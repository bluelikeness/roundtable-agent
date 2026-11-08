// 봇 원문은 마크다운으로 해석하지 않는다. 줄바꿈(white-space: pre-wrap)과 URL 자동 링크만 (docs/05-ui.md 3장)
import { Fragment, type ReactNode } from "react";

const URL_RE = /https?:\/\/[^\s<>"'\])]+/g;

export function linkify(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(URL_RE)) {
    const i = m.index ?? 0;
    // 문장 끝 구두점은 링크에서 뺀다
    const url = m[0].replace(/[.,;:!?。，]+$/, "");
    if (i > last) out.push(<Fragment key={`t${last}`}>{text.slice(last, i)}</Fragment>);
    out.push(
      <a key={`a${i}`} href={url} target="_blank" rel="noopener noreferrer">
        {url}
      </a>,
    );
    last = i + url.length;
  }
  if (last < text.length) out.push(<Fragment key={`t${last}`}>{text.slice(last)}</Fragment>);
  return out;
}
