// 모바일 판정 (docs/05-ui.md 7장: ≤ 768px). CSS 미디어 쿼리와 같은 기준
import { useEffect, useState } from "react";

export const MOBILE_QUERY = "(max-width: 768px)";

function matches(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(MOBILE_QUERY).matches
    : false;
}

export function useMobile(): boolean {
  const [m, setM] = useState(matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(MOBILE_QUERY);
    const on = () => setM(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return m;
}
