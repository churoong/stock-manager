"use client";

import { MARKET_TABS, type Country } from "@/lib/market";

/**
 * 국내 / 미국 탭. 화면 맨 위에 둔다.
 *
 * counts 를 주면 탭에 건수를 붙인다. 다른 시장에 무엇이 있는지 누르지 않고도 안다.
 * 건수가 0 이어도 누를 수 있게 둔다 — "왜 비었는지" 안내가 그 탭 안에 있다.
 */
export default function MarketTabs({
  active,
  onChange,
  counts,
}: {
  active: Country;
  onChange: (next: Country) => void;
  counts?: Partial<Record<Country, number>>;
}) {
  return (
    <div role="tablist" aria-label="시장" className="mb-3 flex gap-1 border-b border-slate-200 dark:border-slate-800">
      {MARKET_TABS.map((tab) => {
        const selected = tab.country === active;
        const count = counts?.[tab.country];
        return (
          <button
            key={tab.country}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(tab.country)}
            // 폰에서는 두 탭이 폭을 나눠 갖고 손가락 높이(44px)를 채운다 (docs/pwa.md 3.2)
            className={`-mb-px min-h-11 flex-1 border-b-2 px-4 py-2 text-sm transition sm:flex-none ${
              selected
                ? "border-slate-900 font-semibold text-slate-900 dark:border-slate-100 dark:text-slate-100"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
            }`}
          >
            {tab.label}
            {count !== undefined ? <span className="ml-1 text-xs text-slate-400">{count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
