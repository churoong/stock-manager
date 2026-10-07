import Link from "next/link";
import BackButton from "@/components/BackButton";
import Icon from "@/components/Icon";
import StockSearch, { PhoneSearch } from "@/components/StockSearch";
import { BACK_TO, DESKTOP_TABS, PHONE_TABS, TITLES, activePhoneTab } from "@/lib/menu";

/**
 * 화면 이동 메뉴. 구성은 lib/menu.ts, 까닭은 docs/pwa.md 3.1·3.2.
 *
 * **폰에서는 아래, 넓은 화면에서는 위에 둔다** (2026-09-17 사용자 요청 "메뉴가 너무 위에있어").
 * 홈 화면에 설치하면 주소창이 사라져 메뉴가 화면 맨 위 끝에 붙는다. 한 손으로 쥔
 * 폰에서 엄지가 닿지 않는 자리다. 폰에서 자주 누르는 것은 아래가 맞다.
 *
 * 폰 아래 막대는 **아이콘 + 글자 다섯 칸**(추천·찾기·내 계좌·알림·더보기)이다 (2026-09-18 사용자 결정).
 * 위쪽 제목줄에는 화면 이름, 아래 막대에 없는 화면이면 **뒤로 단추**, 오른쪽에 검색이 있다.
 * 로그아웃은 더보기로 옮겼다 — 거의 안 쓰는 것이 매 화면 제목줄 자리를 차지했다.
 *
 * 노치·홈 인디케이터를 피하려고 안전 영역(env(safe-area-inset-*))만큼 띄운다.
 * 이것을 빼면 설치형 앱에서 메뉴가 시계 밑으로 들어가거나 인디케이터에 가린다.
 *
 * 배경은 반투명이 아니라 **불투명**이다. 뒤 글자가 비치면 메뉴가 없는 것처럼 보인다.
 */
export default function Nav({ current }: { current: string }) {
  const active = activePhoneTab(current);
  const back = BACK_TO[current];
  return (
    <>
      {/* 넓은 화면: 위쪽 가로 메뉴 */}
      <header className="hidden border-b border-slate-200 bg-white sm:block dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-2">
          <nav className="-mx-1 flex gap-1 overflow-x-auto px-1">
            {DESKTOP_TABS.map((tab) => (
              <Link
                key={tab.href}
                href={tab.href}
                className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-sm ${
                  current === tab.href
                    ? "bg-slate-900 font-medium text-white dark:bg-slate-100 dark:text-slate-900"
                    : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
                }`}
              >
                {tab.label}
              </Link>
            ))}
          </nav>
          <div className="ml-3 shrink-0">
            <StockSearch />
          </div>
          <form action="/api/auth/logout" method="post">
            <button type="submit" className="ml-2 whitespace-nowrap text-xs text-slate-500 underline dark:text-slate-400">
              로그아웃
            </button>
          </form>
        </div>
      </header>

      {/* 폰: 위에는 뒤로 · 화면 이름 · 검색. 설치형 앱에서 상태표시줄에 가리지 않게 띄운다 */}
      <header
        className="sticky top-0 z-20 border-b border-slate-200 bg-white sm:hidden dark:border-slate-800 dark:bg-slate-900"
        style={{ paddingTop: "env(safe-area-inset-top)" }}
      >
        <div className="relative flex h-12 items-center gap-1 px-2">
          {back ? <BackButton fallback={back} /> : <span className="w-2" />}
          <span className="min-w-0 flex-1 truncate text-base font-semibold">
            {TITLES[current] ?? "주식 분석·매매관리"}
          </span>
          <PhoneSearch />
        </div>
      </header>

      {/* 폰: 아래 막대. 엄지가 닿는 자리다. 높이(h-14)는 globals.css 의 --phone-nav 와 같아야 한다 */}
      <nav
        className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white shadow-[0_-1px_6px_rgba(0,0,0,0.06)] sm:hidden dark:border-slate-800 dark:bg-slate-900"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
        aria-label="주요 화면"
      >
        <div className="flex items-stretch">
          {PHONE_TABS.map((tab) => {
            const on = active === tab.href;
            return (
              <Link
                key={tab.href}
                href={tab.href}
                aria-current={on ? "page" : undefined}
                className={`flex h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] ${
                  on ? "font-semibold text-slate-900 dark:text-white" : "text-slate-500 dark:text-slate-400"
                }`}
              >
                <Icon name={tab.icon} className={`h-6 w-6 ${on ? "stroke-[2.3]" : ""}`} />
                {tab.label}
              </Link>
            );
          })}
        </div>
      </nav>
    </>
  );
}
