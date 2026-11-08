/**
 * structured 요소 6종 (docs/05-ui.md 3장). 봇 원문 말풍선 안 raw 아래에 순서대로 그린다.
 * raw 에 이미 [버튼: X] 표기가 있으므로 보조 표시이고, 못 그려도 정보는 남는다.
 *
 * structured 는 봇이 만든 값이라 믿지 않는다: 모양이 틀린 항목은 버리고, 링크는 http(s)만 연다.
 */
import { useState } from "react";
import { imageSrc } from "../../api/rest";
import type {
  Structured,
  StructuredButton,
  StructuredCard,
  StructuredImage,
  StructuredLink,
} from "../../api/types";

const str = (v: unknown): v is string => typeof v === "string" && v.length > 0;
const optStr = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

export function safeUrl(url: unknown): string | null {
  if (!str(url)) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
  } catch {
    return null;
  }
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function obj(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

/** 서버의 structured(dict)를 화면용으로 거른다 */
export function parseStructured(raw: Record<string, unknown> | null | undefined): Structured {
  if (!raw) return {};
  const out: Structured = {};
  const buttons = arr(raw.buttons)
    .map(obj)
    .filter((b): b is Record<string, unknown> => !!b && str(b.label) && str(b.action_id))
    .map((b) => ({ label: b.label as string, action_id: b.action_id as string }));
  if (buttons.length) out.buttons = buttons;
  const choices = arr(raw.choices).filter(str);
  if (choices.length) out.choices = choices;
  const links = arr(raw.links)
    .map(obj)
    .filter((l): l is Record<string, unknown> => !!l && safeUrl(l.url) !== null)
    .map((l) => ({ title: optStr(l.title) ?? String(l.url), url: safeUrl(l.url)! }));
  if (links.length) out.links = links;
  const cards = arr(raw.cards)
    .map(obj)
    .filter((c): c is Record<string, unknown> => !!c && str(c.title))
    .map((c) => ({
      title: c.title as string,
      subtitle: optStr(c.subtitle),
      body: optStr(c.body),
      url: safeUrl(c.url),
      image_url: optStr(c.image_url),
    }));
  if (cards.length) out.cards = cards;
  const images = arr(raw.images)
    .map(obj)
    .filter((i): i is Record<string, unknown> => !!i && str(i.url))
    .map((i) => ({ url: i.url as string, alt: optStr(i.alt) }));
  if (images.length) out.images = images;
  const conf = obj(raw.confirmation_required);
  if (conf && str(conf.summary)) out.confirmation_required = { summary: conf.summary, action_id: optStr(conf.action_id) };
  return out;
}

export function hasInside(s: Structured): boolean {
  return !!(s.buttons || s.choices || s.links || s.cards || s.images);
}

interface ChipProps {
  bot: string;
  color: string;
  /** 이 봇의 가장 최근 응답인지. 아니면 action_id 가 만료됐다 */
  active: boolean;
  onSend: (text: string) => void;
}

export function Buttons({ items, bot, color, active, onSend }: ChipProps & { items: StructuredButton[] }) {
  return (
    <div className="chips" data-testid="buttons">
      {items.map((b) => (
        <button
          key={b.action_id}
          className="chip"
          style={{ borderColor: color, color }}
          disabled={!active}
          title={active ? `@${bot} [tap:${b.action_id}]` : "만료됨"}
          onClick={() => onSend(`@${bot} [tap:${b.action_id}]`)}
        >
          {b.label}
        </button>
      ))}
    </div>
  );
}

export function Choices({ items, bot, active, onSend }: Omit<ChipProps, "color"> & { items: string[] }) {
  return (
    <div className="chips" data-testid="choices">
      {items.map((c) => (
        <button
          key={c}
          className="chip gray"
          disabled={!active}
          title={active ? undefined : "만료됨"}
          onClick={() => onSend(`@${bot} ${c}`)}
        >
          {c}
        </button>
      ))}
    </div>
  );
}

export function Links({ items }: { items: StructuredLink[] }) {
  return (
    <ul className="links" data-testid="links">
      {items.map((l, i) => (
        <li key={`${l.url}-${i}`}>
          <a href={l.url} target="_blank" rel="noopener noreferrer">
            {l.title}
          </a>
        </li>
      ))}
    </ul>
  );
}

function Thumb({ bot, url, alt }: { bot: string; url: string | null | undefined; alt: string }) {
  const src = url ? imageSrc(bot, url) : null;
  if (!src) return null;
  return <img src={src} alt={alt} loading="lazy" referrerPolicy="no-referrer" />;
}

export function Cards({ items, bot }: { items: StructuredCard[]; bot: string }) {
  return (
    <div className="cards" data-testid="cards">
      {items.map((c, i) => {
        const inner = (
          <>
            <Thumb bot={bot} url={c.image_url} alt="" />
            <div className="card-text">
              <div className="card-title">{c.title}</div>
              {c.subtitle && <div className="card-sub muted">{c.subtitle}</div>}
              {c.body && <div className="card-body">{c.body}</div>}
            </div>
          </>
        );
        return c.url ? (
          <a key={i} className="card" href={c.url} target="_blank" rel="noopener noreferrer">
            {inner}
          </a>
        ) : (
          <div key={i} className="card">
            {inner}
          </div>
        );
      })}
    </div>
  );
}

export function Images({ items, bot }: { items: StructuredImage[]; bot: string }) {
  const [zoom, setZoom] = useState<string | null>(null);
  const shown = items.map((im) => ({ src: imageSrc(bot, im.url), alt: im.alt ?? "" })).filter((x) => x.src);
  if (!shown.length) return null;
  return (
    <>
      <div className="images" data-testid="images">
        {shown.map((im, i) => (
          <button key={i} className="thumb" onClick={() => setZoom(im.src)} aria-label={`이미지 확대${im.alt ? `: ${im.alt}` : ""}`}>
            <img src={im.src!} alt={im.alt} loading="lazy" referrerPolicy="no-referrer" />
          </button>
        ))}
      </div>
      {zoom && (
        <div className="lightbox" role="dialog" aria-label="이미지 확대" onClick={() => setZoom(null)}>
          <img src={zoom} alt="" />
        </div>
      )}
    </>
  );
}

/** 말풍선 밖 아래 강조 박스. 버튼은 없다: 확정은 Claude 정리의 [진행]으로만 */
export function ConfirmBox({ summary }: { summary: string }) {
  return (
    <div className="confirm-box" role="note" data-testid="confirm-box">
      <div className="confirm-summary">{summary}</div>
      <div className="confirm-note">이 작업은 확인이 필요합니다</div>
    </div>
  );
}

/** 말풍선 안쪽 요소(확정 박스 제외) */
export function StructuredInside({ s, ...chip }: ChipProps & { s: Structured }) {
  return (
    <>
      {s.buttons && <Buttons items={s.buttons} {...chip} />}
      {s.choices && <Choices items={s.choices} bot={chip.bot} active={chip.active} onSend={chip.onSend} />}
      {s.links && <Links items={s.links} />}
      {s.cards && <Cards items={s.cards} bot={chip.bot} />}
      {s.images && <Images items={s.images} bot={chip.bot} />}
    </>
  );
}
