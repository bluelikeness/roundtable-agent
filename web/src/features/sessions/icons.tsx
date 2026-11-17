// 세션 목록용 선 아이콘(16px, currentColor). 이모지 대신 쓴다
import type { ReactNode } from "react";

function Svg({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <svg
      className={`ico${className ? ` ${className}` : ""}`}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

/** 접기 화살표. 펼친 상태는 CSS 로 90도 돌린다 */
export const Chevron = ({ open }: { open: boolean }) => (
  <Svg className={`chevron${open ? " open" : ""}`}>
    <path d="M9 6l6 6-6 6" />
  </Svg>
);

export const FolderPlus = () => (
  <Svg>
    <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6H9l2 2h8.5A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z" />
    <path d="M12 11v5M9.5 13.5h5" />
  </Svg>
);

export const Trash = () => (
  <Svg>
    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />
  </Svg>
);

export const More = () => (
  <Svg>
    <circle cx="5" cy="12" r="1" fill="currentColor" />
    <circle cx="12" cy="12" r="1" fill="currentColor" />
    <circle cx="19" cy="12" r="1" fill="currentColor" />
  </Svg>
);
