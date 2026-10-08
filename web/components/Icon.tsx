import type { IconName } from "@/lib/menu";

/**
 * 메뉴·단추 아이콘. 선 두께만 쓰는 단순한 그림이라 따로 라이브러리를 들이지 않았다.
 *
 * 왜 아이콘을 붙였나: 폰 아래 막대가 글자만 있어 "어디를 눌러야 하는지" 한눈에 안 들어왔다
 * (2026-09-18 사용자 "메뉴 위치나 버튼을 찾기 어렵다"). 글자는 그대로 두고 아이콘을 더한다.
 */
const PATHS: Record<IconName, React.ReactNode> = {
  recommend: <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4.5 4.5" />
    </>
  ),
  wallet: (
    <>
      <rect x="3" y="6" width="18" height="13" rx="2.5" />
      <path d="M3 10h18" />
      <path d="M16 14.5h2" />
    </>
  ),
  bell: (
    <>
      <path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z" />
      <path d="M10 20.5a2 2 0 0 0 4 0" />
    </>
  ),
  // 종목 분석 — 문서 위 돋보기 (25.1016)
  analysis: (
    <>
      <path d="M6 3.5h8l4 4V20.5H6z" />
      <path d="M9 10h6M9 13.5h3" />
      <circle cx="14.5" cy="16" r="2.2" />
      <path d="M16.2 17.7l1.8 1.8" />
    </>
  ),
  more: (
    <>
      <circle cx="6" cy="12" r="1.3" />
      <circle cx="12" cy="12" r="1.3" />
      <circle cx="18" cy="12" r="1.3" />
    </>
  ),
  etf: (
    <>
      <path d="M4 17l5-5 4 4 7-7" />
      <path d="M15 9h5v5" />
    </>
  ),
  report: (
    <>
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <path d="M8.5 8h7M8.5 12h7M8.5 16h4" />
    </>
  ),
  backtest: <path d="M4 20h16M7 17v-5M11 17V7M15 17v-8M19 17v-3" />,
  status: <path d="M3 12h4l2.5-6 4 12 2.5-6H21" />,
  settings: (
    <>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
    </>
  ),
  logout: (
    <>
      <path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4" />
      <path d="M10 16l-4-4 4-4M6 12h10" />
    </>
  ),
  back: <path d="M15 5l-7 7 7 7" />,
  chevron: <path d="M9 5l7 7-7 7" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  info: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5M12 8h.01" />
    </>
  ),
};

export default function Icon({ name, className = "h-6 w-6" }: { name: IconName; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {PATHS[name]}
    </svg>
  );
}
