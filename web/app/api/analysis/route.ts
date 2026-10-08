import { NextResponse } from "next/server";
import { execute, ifMissingTable, rowsToObjects } from "@/lib/db";
import { HUB_SQL, RADAR_SQL, groupHub, parseRadars, type HubRow } from "@/lib/analysis";

/**
 * 종목 분석 모아보기 (docs/analysis.md). 일일 배치가 만든 의견 가운데 보유 점검·매수 검토·보유 유지를 읽기만 한다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
export async function GET() {
  try {
    // 표가 없으면(0054 전) 빈 목록 — 고장이 아니라 아직 배치가 안 돈 것이다
    const rs = await execute(HUB_SQL).catch(ifMissingTable({ columns: [], rows: [], affectedRows: 0 }));
    const rows = rowsToObjects<HubRow>(rs);
    // 레이더 (docs/analysis.md 20장) — 설정 두 행. 행이 없으면(배치 전) 레이더만 빠진다
    const radarRows = rowsToObjects<{ key: string; value: string }>(await execute(RADAR_SQL));
    return NextResponse.json({ groups: groupHub(rows), radars: parseRadars(radarRows), computed_at: rows[0]?.computed_at ?? null });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "조회에 실패했습니다"] }, { status: 500 });
  }
}
