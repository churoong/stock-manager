import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { TAX_SIM_SQL, type TaxSim } from "@/lib/taxSim";

/**
 * 계좌별 세후 적립 시뮬레이션 (docs/etf.md 11.7, 25.1003). 포트폴리오 재계산이 저장한 값을 읽기만 한다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
export async function GET() {
  try {
    const row = rowsToObjects<{ tax_sim_json: string | null }>(await execute(TAX_SIM_SQL))[0];
    const sim = row?.tax_sim_json ? (JSON.parse(row.tax_sim_json) as TaxSim) : null;
    return NextResponse.json({ sim, note: sim ? null : "아직 계산되지 않았습니다 — 설정에서 세율을 저장하면 다시 계산합니다" });
  } catch (error) {
    const message = error instanceof Error ? error.message : "조회에 실패했습니다";
    // 칸이 없으면(0052 전) 아직 배치가 안 돈 것이다 — 고장이 아니라 "아직" 이다
    if (/no such (column|table)/i.test(message)) {
      return NextResponse.json({ sim: null, note: "아직 계산되지 않았습니다 — 다음 포트폴리오 재계산 뒤에 보입니다" });
    }
    return NextResponse.json({ errors: [message] }, { status: 500 });
  }
}
