import { NextResponse } from "next/server";
import { execute, explain, ifMissingTable, rowsToObjects } from "@/lib/db";
import {
  ACTIVE_FLAGS, LAST_EDIT, LAST_RECALC_RUN, LOTS, POSITIONS, SUMMARY, TRADES_VERSION, parseJson,
  capList, recalcStuckNote,
} from "@/lib/portfolio";

/**
 * 포트폴리오 조회 (docs/portfolio.md). 배치가 계산해 둔 파생 표를 읽기만 한다.
 *
 * summary.trades_version 이 지금 원본의 지문과 다르면 "계산 중" 이다. 저장 뒤 1~2분이 걸린다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
export async function GET() {
  let 플래그_오류: string | null = null;
  try {
    // 날짜별 평가 계열(portfolio_values)은 **읽지 않는다** (docs/infra.md 25.793, 매매 입력 감사 #5). 화면이 쓰지 않는데 첫 매매일부터 하루 한 행을
    // 상한 없이 읽었고, 재계산 중에는 30초마다 다시 읽어 DB 읽기 한도를 썼다. 차트를 붙일 때 상한과 함께 되살린다
    const [summaryRs, positionsRs, lotsRs, versionRs, flagsRs] = await Promise.all([
      execute(SUMMARY),
      execute(POSITIONS),
      execute(LOTS),
      execute(TRADES_VERSION),
      // 표가 없으면(0023 전) 빈 목록. **그 밖의 실패는 화면에 말한다** (docs/infra.md 25.223) — 조용히 비면
      // 보유 종목의 손절 플래그가 "오늘 플래그 없음" 과 똑같이 보인다. 경로 전체를 실패시키면 보유 화면이 통째로 안 보이므로
      // 빈 목록 + 경고로 간다
      execute(ACTIVE_FLAGS).catch((error: unknown) => {
        const 빈것 = { columns: [], rows: [], affectedRows: 0 };
        try {
          return ifMissingTable(빈것)(error);
        } catch {
          플래그_오류 = error instanceof Error ? error.message : String(error);
          return 빈것;
        }
      }),
    ]);
    const summary = rowsToObjects<Record<string, unknown>>(summaryRs)[0];
    const currentVersion = String(rowsToObjects<{ version: string }>(versionRs)[0]?.version ?? "");
    const stale = !summary || summary.trades_version !== currentVersion;
    // 오래 "계산 중" 이면 멈춘 것인지 본다 (25.554). 계산 중일 때만 두 번 더 읽는다. 못 읽으면 예전처럼 "계산 중"
    let stuck: string | null = null;
    if (stale) {
      try {
        const [editRs, runRs] = await Promise.all([execute(LAST_EDIT), execute(LAST_RECALC_RUN)]);
        const lastEdit = rowsToObjects<{ u: string | null }>(editRs)[0]?.u ?? null;
        type Run = { status: string; started_at: string | null; error_text: string | null };
        const summaryAt = summary?.created_at ? String(summary.created_at) : null;
        stuck = recalcStuckNote(stale, lastEdit, rowsToObjects<Run>(runRs)[0] ?? null, new Date(), summaryAt);
      } catch {
        // 못 읽으면 **예전처럼 "계산 중"** 이다 — 한 번의 순간 실패로 빨간 띠를 띄우고 다시 부르기를 멈췄다 (25.555, 교차검증)
        stuck = null;
      }
    }
    return NextResponse.json({
      as_of: summary?.as_of_date ?? null,
      stale,
      recalc_stuck: stuck,
      has_trades: !currentVersion.startsWith("t0:"),
      totals: parseJson(summary?.totals_json, null),
      allocation: parseJson(summary?.allocation_json, null),
      metrics: parseJson(summary?.metrics_json, null),
      upcoming: parseJson(summary?.upcoming_json, []),
      warnings: [
        ...parseJson<string[]>(summary?.warnings_json, []),
        ...(플래그_오류 ? [`매도 플래그를 읽지 못했습니다 — 손절·목표 경고가 빠져 있을 수 있습니다 (${explain(플래그_오류)})`] : []),
      ],
      positions: rowsToObjects(positionsRs),
      ...(() => {
        const { rows, truncated } = capList(rowsToObjects(lotsRs));
        return { lots: rows, lots_truncated: truncated };
      })(),
      flags: rowsToObjects(flagsRs),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "조회에 실패했습니다";
    if (/no such table/i.test(message)) {
      return NextResponse.json({
        as_of: null, stale: false, has_trades: false, totals: null, allocation: null, metrics: null,
        upcoming: [], warnings: ["포트폴리오 표가 아직 없습니다. 배치를 한 번 돌리면 만들어집니다"],
        positions: [], lots: [], flags: [],
      });
    }
    return NextResponse.json({ errors: [message] }, { status: 500 });
  }
}
