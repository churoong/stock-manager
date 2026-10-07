"use client";

import Link from "next/link";
import { useState } from "react";
import { TradeForm } from "@/components/PortfolioView";

/**
 * 그 자리에서 매매 입력 (docs/infra.md 25.894, 2026-10-02 사용자 요청: "종목상세, 추천, 적립에서 매매입력을 바로 할 수 있게해줘").
 *
 * 예전에는 포트폴리오 → 매매 탭 → 종목 검색까지 가야 했다. 지금 보고 있는 종목으로 폼을 펼친다.
 * 폼은 포트폴리오와 **같은 것**(`TradeForm`)이라 저장 규칙이 하나다. 체결가·수량·날짜는 비워 두고 사용자가 넣는다 —
 * 신호의 매수 구간 같은 값을 미리 채우면 "실제로 체결한 값" 이 아닌 숫자가 저장될 수 있다(CLAUDE.md: trades 는 사용자 입력값만).
 */
export interface QuickTradeStock {
  id: number;
  ticker: string;
  name: string;
  country: string;
  currency: string;
  market: string;
  asset_type?: string;
}

export default function QuickTrade({
  stock,
  horizon = "long",
  className = "",
  initialOpen = false,
  initialSide,
}: {
  stock: QuickTradeStock;
  horizon?: "short" | "mid" | "long";
  className?: string;
  /** 처음부터 펼친 채 — 아침 리포트의 "매매 입력" 링크로 왔을 때 (docs/infra.md 25.944) */
  initialOpen?: boolean;
  initialSide?: "buy" | "sell";
}) {
  const [open, setOpen] = useState(initialOpen);
  const [notice, setNotice] = useState<string | null>(null);

  // **단추는 그 줄의 다른 단추(관심 등록·종목 상세) 옆에 붙고, 폼은 줄을 통째로 쓴다** (25.895, 사용자: "매매입력 버튼이 줄바꿈이돼서 보기
  // 안좋더"). 부르는 쪽은 `flex flex-wrap` 줄 안에 둔다 — `basis-full` 인 칸이 다음 줄로 내려간다. 단추 글자는 줄을 바꾸지 않는다
  return (
    <>
      <button
        type="button"
        onClick={() => {
          setOpen((v) => !v);
          setNotice(null);
        }}
        aria-expanded={open}
        className={`inline-flex min-h-10 shrink-0 items-center whitespace-nowrap rounded-lg border px-3 text-sm sm:min-h-0 sm:rounded sm:px-2 sm:py-0.5 sm:text-xs ${
          open
            ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900"
            : "border-slate-300 text-slate-600 dark:border-slate-700 dark:text-slate-300"
        } ${className}`}
      >
        매매 입력
      </button>
      {open ? (
        <div className="basis-full text-left">
          <TradeForm
            fixedStock={stock}
            defaultHorizon={horizon}
            defaultSide={initialSide}
            onSaved={(recalc) => {
              setOpen(false);
              setNotice(
                recalc?.dispatched
                  ? "저장했습니다. 1~2분 뒤 포트폴리오 손익에 반영됩니다"
                  : `저장했습니다. ${recalc?.reason ?? ""}`,
              );
            }}
          />
        </div>
      ) : null}
      {notice ? (
        <span className="basis-full text-left text-xs text-emerald-700 dark:text-emerald-400">
          {notice}{" "}
          <Link href="/portfolio" className="underline">
            포트폴리오
          </Link>
        </span>
      ) : null}
    </>
  );
}
