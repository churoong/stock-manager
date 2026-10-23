"use client";

import { pageRangeText, type Page } from "@/lib/paging";

/** 쪽 넘기기 — 한 쪽뿐이면 그리지 않는다 (docs/infra.md 25.1767) */
export default function Pager({ p, onPage }: { p: Page<unknown>; onPage: (page: number) => void }) {
  if (p.pages <= 1) return null;
  const 단추 = "rounded border border-slate-300 px-2 py-0.5 disabled:opacity-40 dark:border-slate-700";
  return (
    <nav className="mt-2 flex flex-wrap items-center gap-2 text-xs" aria-label="쪽 넘기기">
      <button type="button" className={단추} disabled={p.page === 0} onClick={() => onPage(0)}>
        처음
      </button>
      <button type="button" className={단추} disabled={p.page === 0} onClick={() => onPage(p.page - 1)}>
        이전
      </button>
      <span className="text-slate-500">
        {p.page + 1} / {p.pages}쪽 · {pageRangeText(p)}
      </span>
      <button type="button" className={단추} disabled={p.page >= p.pages - 1} onClick={() => onPage(p.page + 1)}>
        다음
      </button>
    </nav>
  );
}
