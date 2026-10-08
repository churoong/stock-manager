/**
 * 화면 이동 메뉴의 구성 (docs/pwa.md 3.2). 메뉴(Nav)와 더보기 화면이 이것을 읽는다.
 *
 * **폰 아래 막대는 여섯 칸이다: 추천·적립·찾기·내 계좌·알림·더보기** (2026-09-18 사용자 결정).
 * 전에는 글자만 있는 여섯 칸이었고, 리포트·백테스트·시스템 상태는 긴 화면 맨 아래 고지 옆에
 * 숨어 있어 "메뉴 위치나 버튼을 찾기 어렵다" 는 말을 들었다. 그래서 아이콘을 붙이고, 아래 막대에
 * 없는 화면은 전부 더보기 한 곳에서 가게 했다. 처음에는 적립도 더보기로 보냈다가(다섯 칸) 같은 날
 * 사용자가 "장기 적립은 메뉴에 노출" 을 원해 다시 올렸다. 한 칸이 약 62px(375px 폭)이라 아이콘·글자는 들어간다.
 */

export type IconName =
  | "recommend" | "search" | "wallet" | "bell" | "more"
  | "etf" | "report" | "backtest" | "status" | "settings"
  | "logout" | "back" | "chevron" | "close" | "info";

export interface MenuItem {
  href: string;
  label: string;
  icon: IconName;
  /** 더보기 화면에서 이름 아래 적는 한 줄 */
  note?: string;
}

/** 넓은 화면의 위쪽 가로 메뉴. 자리가 넉넉해 거의 다 보인다 */
export const DESKTOP_TABS: Array<{ href: string; label: string }> = [
  { href: "/recommend", label: "오늘의 추천" },
  // 장기 적립은 타이밍을 보지 않아 오늘의 추천과 나눴다 (docs/etf.md 4장)
  { href: "/etf", label: "장기 적립" },
  { href: "/screener", label: "종목 찾기" },
  // 한 종목의 결론·근거·반대 목소리 (docs/analysis.md, 25.1016)
  { href: "/analysis", label: "종목 분석" },
  { href: "/portfolio", label: "내 포트폴리오" },
  { href: "/alerts", label: "알림" },
  // 규칙을 고칠 때만 보는 화면이라 뒤쪽에 둔다 (docs/backtest.md)
  { href: "/reports", label: "일일 리포트" },
  { href: "/backtest", label: "백테스트" },
  { href: "/settings", label: "설정" },
];

/** 폰 아래 막대. 엄지가 닿는 자리에 자주 쓰는 다섯과 더보기 */
export const PHONE_TABS: MenuItem[] = [
  { href: "/recommend", label: "추천", icon: "recommend" },
  { href: "/etf", label: "적립", icon: "etf" },
  { href: "/screener", label: "찾기", icon: "search" },
  { href: "/portfolio", label: "내 계좌", icon: "wallet" },
  { href: "/alerts", label: "알림", icon: "bell" },
  { href: "/more", label: "더보기", icon: "more" },
];

/** 더보기 화면. 아래 막대에 없는 화면은 여기서 간다 (로그아웃도 여기 있다) */
export const MORE_ITEMS: MenuItem[] = [
  { href: "/analysis", label: "종목 분석", icon: "search", note: "한 종목의 결론·근거·반대 목소리" },
  { href: "/reports", label: "일일 리포트", icon: "report", note: "텔레그램으로 보낸 아침 리포트 보관함" },
  { href: "/backtest", label: "백테스트", icon: "backtest", note: "규칙이 과거에 통했는지" },
  { href: "/status", label: "시스템 상태", icon: "status", note: "배치·알림이 도는지 보고, 수동으로 돌린다" },
  { href: "/settings", label: "설정", icon: "settings", note: "가중치·목표가·손절선·세율" },
];

/** 폰 위쪽 제목줄에 쓰는 화면 이름. 본문 제목은 폰에서 숨긴다(두 번 나오면 화면만 밀린다) */
export const TITLES: Record<string, string> = {
  "/recommend": "오늘의 추천",
  "/etf": "장기 적립",
  "/screener": "종목 찾기",
  "/analysis": "종목 분석",
  "/portfolio": "내 포트폴리오",
  "/alerts": "알림",
  "/reports": "일일 리포트",
  "/backtest": "백테스트",
  "/settings": "설정",
  "/status": "시스템 상태",
  "/more": "더보기",
  "/stocks": "종목",
};

/**
 * 뒤로 단추가 갈 곳. **아래 막대에 없는 화면에만** 둔다.
 * 홈 화면에 설치한 앱에는 브라우저의 뒤로 단추가 없다 — 종목 상세에서 돌아갈 길이 아래 막대뿐이었다.
 * 들어온 길이 있으면 그리로(history.back), 없으면(알림에서 바로 열었을 때) 여기로 간다.
 */
export const BACK_TO: Record<string, string> = {
  "/stocks": "/recommend",
  "/reports": "/more",
  "/backtest": "/more",
  "/settings": "/more",
  "/status": "/more",
  "/analysis": "/more",
};

/** 지금 화면에서 아래 막대의 어느 칸을 켤지. 더보기 안의 화면이면 더보기를 켠다 */
export function activePhoneTab(current: string): string | null {
  if (PHONE_TABS.some((tab) => tab.href === current)) return current;
  if (MORE_ITEMS.some((item) => item.href === current)) return "/more";
  return null;
}
