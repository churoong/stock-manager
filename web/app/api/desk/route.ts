import { NextResponse } from "next/server";
import { execute, explain, ifMissingTable, rowsToObjects } from "@/lib/db";
import { DESK_ACCOUNTS, DESK_LATEST, EMPTY_DESK, pickDesk } from "@/lib/desk";

/**
 * 운용 상황실 — 배치가 적은 그날 요약을 읽기만 한다 (docs/desk.md 2장, docs/infra.md 25.1501). 미들웨어가 인증 뒤에 둔다.
 * 0084 전 DB 면 표가 없다 — 빈 보기와 `ready: false`("아직 운용 요약이 없습니다"). 다른 실패(한도·인증)는 없는 것으로 말하지 않고
 * 까닭을 돌려준다(25.163).
 */
export async function GET() {
  try {
    const result = await execute(DESK_LATEST).catch(ifMissingTable(null));
    // 순위표 밖 계좌(개설 전·첫 평가 전)를 보이려고 계좌 표도(25.2175). 표가 없을 때만 빈 목록 — 다른 실패(한도·인증)는
    // 삼키지 않는다(25.163, 운용 요약과 같은 DB 라 그 실패는 위 읽기도 같이 겪는다)
    const 계좌표 = await execute(DESK_ACCOUNTS).catch(ifMissingTable(null));
    const accounts = 계좌표 ? rowsToObjects<{ id: string; created_at: string | null }>(계좌표) : [];
    if (!result) return NextResponse.json({ ready: false, desk: EMPTY_DESK, accounts });
    const rows = rowsToObjects<{ as_of: string; summary_json: string | null; updated_at: string | null }>(result);
    return NextResponse.json({ ready: true, desk: pickDesk(rows), accounts });
  } catch (e) {
    // 다른 경로와 같은 꼴(`errors` 배열) — 화면이 까닭을 그대로 보인다(25.1503, 검토 2)
    return NextResponse.json({ errors: [explain(e instanceof Error ? e.message : String(e))] }, { status: 500 });
  }
}
