import { NextResponse } from "next/server";
import { execute, explain, ifMissingTable, rowsToObjects } from "@/lib/db";
import { QUALITY_DAYS, QUALITY_SQL, type QualityRow } from "@/lib/kisQuality";
import { BROKER_KEYS } from "@/lib/paper";

/**
 * KIS모의투자 실행 품질 추이 — 배치가 날마다 적은 비교 요약을 시장마다 읽기만 한다 (docs/paper.md 15.8, docs/infra.md 25.1504).
 * 미들웨어가 인증 뒤에 둔다. 0078 전 DB 면 표가 없어 빈 계열(0078~0080 사이는 `summary_json` 칸이 없어 오류 — 운영 DB 는 이미 그 뒤).
 */
export async function GET() {
  const 부터 = new Date(Date.now() - QUALITY_DAYS * 86_400_000).toISOString().slice(0, 10);
  try {
    const [KR, US] = await Promise.all(
      (["KR", "US"] as const).map(async (m) => {
        const r = await execute(QUALITY_SQL, [BROKER_KEYS[m], 부터]).catch(ifMissingTable(null));
        return r ? rowsToObjects<QualityRow>(r) : [];
      }),
    );
    return NextResponse.json({ KR, US });
  } catch (e) {
    return NextResponse.json({ errors: [explain(e instanceof Error ? e.message : String(e))] }, { status: 500 });
  }
}
