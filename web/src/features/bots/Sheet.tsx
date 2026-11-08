// 시트 껍데기: 데스크톱은 오른쪽에서 슬라이드, 모바일(≤768px)은 바텀시트. Esc·바깥 탭으로 닫는다 (05-ui 7장)
import { useEffect, useRef, type ReactNode } from "react";

interface Props {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** "side": 오른쪽 시트(데스크톱), "bottom": 항상 바텀시트, "drawer": 왼쪽 드로어 */
  variant?: "side" | "bottom" | "drawer";
  testId?: string;
}

export function Sheet({ title, onClose, children, variant = "side", testId }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    // 열리면 시트 안으로 초점을 옮긴다
    ref.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className={`overlay ${variant}`} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className={`sheet ${variant}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={ref}
        data-testid={testId}
      >
        <div className="sheet-head">
          <span className="sheet-title">{title}</span>
          <button className="icon" aria-label="닫기" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  );
}
