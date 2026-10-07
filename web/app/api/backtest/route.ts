import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import {
  LATEST_GROUP, LATEST_STRESS, LATEST_STRESS_RUN, RECENT_BATCH_RUNS, RECENT_GROUPS, RUNS_IN_GROUP,
  curvesSql, groupNotice, pickCurveRuns, stocksSql, stressNoSignalNote,
  type BacktestRun, type BatchRunRow, type CurvePoint, type StressRun,
} from "@/lib/backtest";

/**
 * 백테스트·스트레스 결과 조회 (docs/backtest.md, docs/stress.md).
 *
 * 배치가 저장해 둔 것을 읽기만 한다. 여기서 지표를 다시 계산하지 않는다.
 * 자본곡선은 실행 하나당 1,200행쯤이라 **화면이 고른 전략만** 가져온다(curves=...).
 *
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */

/** 한 번에 가져올 자본곡선 수. 3개면 종합·벤치마크·비교 하나다. 더 늘리면 응답이 무거워진다 */
const MAX_CURVES = 3;

export async function GET(request: Request) {
  const url = new URL(request.url);
  const market = url.searchParams.get("market") === "US" ? "US" : "KR";
  const wanted = (url.searchParams.get("curves") ?? "composite,benchmark")
    .split(",").map((s) => s.trim()).filter(Boolean).slice(0, MAX_CURVES);

  try {
    const [groupRs, recentRs, stressRs, statusRs, stressRunRs] = await Promise.all([
      execute(LATEST_GROUP, [market]),
      execute(RECENT_GROUPS, [market]),
      execute(LATEST_STRESS, [market]),
      execute(RECENT_BATCH_RUNS),
      execute(LATEST_STRESS_RUN, [market]),
    ]);
    const asked = url.searchParams.get("group");
    const latest = rowsToObjects<{ group_id: string }>(groupRs)[0]?.group_id ?? null;
    // **묶음은 그 시장 것만** (docs/infra.md 25.313). 예전에는 `?market=KR&group=<미국 묶음>` 이면 응답의 market 은 KR 인데
    // 실행·곡선은 미국 것이고 스트레스만 KR 이라 나라가 섞인 화면이 나왔다. 다른 시장 묶음이면 그 시장의 최신으로 물러난다
    let groupId = asked || latest;
    let runs = groupId ? rowsToObjects<BacktestRun>(await execute(RUNS_IN_GROUP, [groupId, market])) : [];
    if (asked && runs.length === 0 && latest && latest !== asked) {
      groupId = latest;
      runs = rowsToObjects<BacktestRun>(await execute(RUNS_IN_GROUP, [latest, market]));
    }
    const chosen = pickCurveRuns(runs, wanted, MAX_CURVES);
    const curves = chosen.length
      ? rowsToObjects<CurvePoint>(await execute(curvesSql(chosen.length), chosen.map((r) => r.id)))
      : [];

    const stress = rowsToObjects<StressRun>(stressRs)[0] ?? null;
    let basketStocks: Array<{ id: number; ticker: string; name: string }> = [];
    if (stress) {
      const ids = Object.keys(safeObject(stress.basket_json)).map(Number).filter((n) => Number.isFinite(n));
      // D1 은 질의당 파라미터 100개라 나눠 읽는다 (docs/infra.md 25.5)
      for (let i = 0; i < ids.length; i += 90) {
        const part = ids.slice(i, i + 90);
        basketStocks.push(
          ...rowsToObjects<{ id: number; ticker: string; name: string }>(await execute(stocksSql(part.length), part)),
        );
      }
    }

    return NextResponse.json({
      market,
      group_id: groupId,
      runs,
      curves,
      curve_runs: chosen.map((r) => ({ run_id: r.id, strategy: r.strategy })),
      groups: rowsToObjects(recentRs),
      stress,
      // 신호 0건이라 돌지 않은 날은 지난 바스켓이라고 말한다 (25.825)
      stress_note: stressNoSignalNote(
        rowsToObjects<{ status: string; step_log: string | null }>(stressRunRs)[0], stress?.as_of_date ?? null,
      ),
      basket_stocks: basketStocks,
      status: rowsToObjects<BatchRunRow>(statusRs),
      notice: groupNotice(asked, groupId, latest, stress !== null, rowsToObjects<{ group_id: string }>(recentRs)[0]?.group_id ?? latest),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "조회에 실패했습니다";
    if (/no such table/i.test(message)) {
      return NextResponse.json({
        market, group_id: null, runs: [], curves: [], curve_runs: [], groups: [], stress: null,
        basket_stocks: [], status: [],
        notice: "백테스트 표가 아직 없습니다. 마이그레이션(migrate.yml) 을 한 번 돌리면 만들어집니다",
      });
    }
    return NextResponse.json({ errors: [message] }, { status: 500 });
  }
}

function safeObject(raw: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}
