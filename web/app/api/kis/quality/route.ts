import { NextResponse } from "next/server";
import { execute, explain, ifMissingTable, rowsToObjects } from "@/lib/db";
import {
  QUALITY_DAYS,
  QUALITY_SQL,
  currentAccountRows,
  type QualityRow,
} from "@/lib/kisQuality";
import { CHALLENGER_KEYS } from "@/lib/kisChallenger";
import { BROKER_KEYS } from "@/lib/paper";

/**
 * KIS모의투자 실행 품질 추이 — 배치가 날마다 적은 비교 요약을 시장마다 읽기만 한다 (docs/paper.md 15.8, docs/infra.md 25.1504).
 * 둘째 모의 계좌(도전자, 15.10·25.1515)도 같은 꼴로 `KR2`·`US2`.
 * 미들웨어가 인증 뒤에 둔다. 0078 전 DB 면 표가 없어 빈 계열(0078~0080 사이는 `summary_json` 칸이 없어 오류 — 운영 DB 는 이미 그 뒤).
 */
export async function GET() {
  const 부터 = new Date(Date.now() - QUALITY_DAYS * 86_400_000)
    .toISOString()
    .slice(0, 10);
  try {
    // 첫째(챔피언) 국내·미국, 둘째(도전자, 25.1515) 국내·미국 — 둘째 키가 없으면 줄이 없어 빈 계열
    const 칸 = [
      BROKER_KEYS.KR,
      BROKER_KEYS.US,
      CHALLENGER_KEYS.KR,
      CHALLENGER_KEYS.US,
    ];
    const [KR, US, KR2, US2] = await Promise.all(
      칸.map(async (broker) => {
        const r = await execute(QUALITY_SQL, [broker, 부터]).catch(
          ifMissingTable(null),
        );
        // 도전자를 바꾸면 앞 계좌 줄이 같은 키에 남는다 — 지금 계좌 줄만(25.1642)
        return r ? currentAccountRows(rowsToObjects<QualityRow>(r)) : [];
      }),
    );
    return NextResponse.json({ KR, US, KR2, US2 });
  } catch (e) {
    return NextResponse.json(
      { errors: [explain(e instanceof Error ? e.message : String(e))] },
      { status: 500 },
    );
  }
}
