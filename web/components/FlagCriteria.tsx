"use client";

import CriteriaTable from "@/components/CriteriaTable";
import { STALE_AFTER_DAYS, parseCriteria } from "@/lib/recommend";

/**
 * 매도 플래그의 근거표 (docs/sell_flags.md). **플래그가 보이는 모든 화면이 이것을 쓴다** (docs/infra.md 25.262).
 *
 * 종목 상세에만 있었고, 보유가 가장 많이 보이는 포트폴리오 화면은 문장(`rationale_text`)만 그렸다 — 질의가
 * `rationale_data` 를 읽어 오면서도 펼칠 곳이 없었다. CLAUDE.md 절대 규칙: 근거에 쓴 수치는 어느 행·언제·출처인지 펼쳐 볼 수 있어야 한다.
 */
export default function FlagCriteria({ raw, asOf }: { raw: unknown; asOf: unknown }) {
  const rows = parseCriteria(typeof raw === "string" ? raw : null);
  // **0줄이어도 접어서 보여 준다** (2026-09-23, docs/infra.md 25.174). 예전에는 여기서
  // null 을 돌려줘 근거가 없는 플래그가 근거가 있는 플래그와 똑같이 보였다.
  // 0줄일 때 `CriteriaTable` 이 빨간 경고를 그린다(25.71) — 그 경고를 부를 수 있게 둔다
  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-[11px] underline opacity-80">근거 {rows.length}개</summary>
      <CriteriaTable rows={rows} caption={`판정일 ${String(asOf ?? "-")}`} docPath="docs/sell_flags.md" staleAfterDays={STALE_AFTER_DAYS} />
    </details>
  );
}
