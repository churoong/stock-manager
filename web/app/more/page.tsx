import Link from "next/link";
import Footer from "@/components/Footer";
import Icon from "@/components/Icon";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";
import { MORE_ITEMS } from "@/lib/menu";

export const dynamic = "force-dynamic";

/**
 * 더보기 (docs/pwa.md 3.2). 폰 아래 막대의 다섯째 칸.
 *
 * 아래 막대에 없는 화면(장기 적립·일일 리포트·백테스트·시스템 상태·설정)과 로그아웃을 한 곳에 모은다.
 * 전에는 이것들이 긴 화면 맨 아래 고지 옆 작은 링크였다(2026-09-18 사용자 "메뉴 위치나 버튼을 찾기 어렵다").
 */
export default function MorePage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/more" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="더보기" />
        <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white dark:divide-slate-800 dark:border-slate-800 dark:bg-slate-900">
          {MORE_ITEMS.map((item) => (
            <li key={item.href}>
              <Link
                href={item.href}
                className="flex min-h-14 items-center gap-3 px-4 py-3 active:bg-slate-100 dark:active:bg-slate-800"
              >
                <Icon name={item.icon} className="h-6 w-6 shrink-0 text-slate-500 dark:text-slate-400" />
                <span className="min-w-0 flex-1">
                  <span className="block text-[15px] font-medium">{item.label}</span>
                  {item.note ? (
                    <span className="block truncate text-xs text-slate-500 dark:text-slate-400">{item.note}</span>
                  ) : null}
                </span>
                <Icon name="chevron" className="h-5 w-5 shrink-0 text-slate-300 dark:text-slate-600" />
              </Link>
            </li>
          ))}
        </ul>

        <form action="/api/auth/logout" method="post" className="mt-4">
          <button
            type="submit"
            className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white text-[15px] text-rose-600 active:bg-slate-100 dark:border-slate-800 dark:bg-slate-900 dark:active:bg-slate-800"
          >
            <Icon name="logout" className="h-5 w-5" />
            로그아웃
          </button>
        </form>
      </main>
      <Footer />
    </div>
  );
}
