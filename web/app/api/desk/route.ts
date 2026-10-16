import { NextResponse } from "next/server";
import { execute, explain, ifMissingTable, rowsToObjects } from "@/lib/db";
import { DESK_LATEST, EMPTY_DESK, pickDesk } from "@/lib/desk";

/**
 * 운용 상황실 — 배치가 적은 그날 요약을 읽기만 한다 (docs/desk.md 2장, docs/infra.md 25.1501). 미들웨어가 인증 뒤에 둔다.
 * 0084 전 DB 면 표가 없다 — 빈 보기와 `ready: false`("아직 운용 요약이 없습니다"). 다른 실패(한도·인증)는 없는 것으로 말하지 않고
 * 까닭을 돌려준다(25.163).
 */
export async function GET() {
  try {
    const result = await execute(DESK_LATEST).catch(ifMissingTable(null));
    if (!result) return NextResponse.json({ ready: false, desk: EMPTY_DESK });
    const rows = rowsToObjects<{ as_of: string; summary_json: string | null; updated_at: string | null }>(result);
    return NextResponse.json({ ready: true, desk: pickDesk(rows) });
  } catch (e) {
    return NextResponse.json({ error: explain(e instanceof Error ? e.message : String(e)) }, { status: 500 });
  }
}
