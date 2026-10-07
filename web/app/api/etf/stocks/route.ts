import { NextResponse } from "next/server";
import {
  buildAccumulationFunnelQuery,
  buildAccumulationGoneQuery,
  buildAccumulationPassedQuery,
  orderFunnel,
  type AccumulationRow,
} from "@/lib/accumulation";
import { execute, rowsToObjects } from "@/lib/db";
import type { Country } from "@/lib/market";
import { fitStockCap, readSetting } from "@/lib/settings";

/**
 * 장기 적립 종목 (docs/accumulation.md). 나라 하나씩 부른다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
/**
 * 비중 상한 두 칸 (docs/infra.md 25.255). 화면의 고정 경고가 설정값을 말하게 한다.
 * 못 읽으면 기본값 — 경고 한 줄의 숫자 때문에 목록을 막지 않는다.
 */
async function 상한읽기(): Promise<{ stock: number; sector: number; warnings: string[] }> {
  const raw: Record<string, unknown> = {};
  try {
    for (const r of rowsToObjects<{ key: string; value: string }>(
      await execute("SELECT key, value FROM settings WHERE key IN ('max_weight_per_stock', 'max_weight_per_sector')"),
    )) {
      try {
        raw[r.key] = JSON.parse(r.value);
      } catch {
        // 깨진 JSON 은 없는 것으로 — 아래 readSetting 이 기본값을 낸다
      }
    }
  } catch {
    // 설정을 못 읽어도 목록은 보인다. 숫자는 기본값으로
  }
  // **경고를 버리지 않고, 배치와 같은 규칙으로 맞춘다** (docs/infra.md 25.357)
  const 종목 = readSetting("max_weight_per_stock", raw.max_weight_per_stock);
  const 섹터 = readSetting("max_weight_per_sector", raw.max_weight_per_sector);
  const 맞춤 = fitStockCap(종목.value, 섹터.value);
  return {
    stock: 맞춤.stock,
    sector: 섹터.value,
    warnings: [종목.warning, 섹터.warning, 맞춤.warning].filter((w): w is string => Boolean(w)),
  };
}

export async function POST(request: Request) {
  let country: Country = "KR";
  try {
    const body = await request.json();
    if (body?.country === "US") country = "US";
  } catch {
    // 본문이 없으면 국내
  }

  try {
    const passedQ = buildAccumulationPassedQuery(country);
    const funnelQ = buildAccumulationFunnelQuery(country);
    const rows = rowsToObjects<AccumulationRow>(await execute(passedQ.sql, passedQ.args));
    const funnelRows = rowsToObjects<{ gate: string | null; n: number; as_of: string | null }>(
      await execute(funnelQ.sql, funnelQ.args),
    );
    const funnel = orderFunnel(funnelRows, country);
    const goneQ = buildAccumulationGoneQuery(country);
    const 빠진통과 = Number((await execute(goneQ.sql, goneQ.args)).rows[0]?.[0] ?? 0);
    // 통과가 0개여도 판정은 돌았다 — 기준일을 탈락 쪽에서 읽는다 (docs/infra.md 25.483)
    const 탈락기준일 = funnelRows.map((r) => r.as_of).filter((d): d is string => !!d).sort().at(-1) ?? null;
    // 판정 뒤 상장폐지·정지된 통과 종목도 판정 수에 센다 — 빼면 "유니버스 N종목" 이 배치 근거 문장("59개 중 k위")과 어긋났다 (25.912)
    const judged = rows.length + 빠진통과 + funnel.reduce((sum, step) => sum + step.failed, 0);
    return NextResponse.json({
      rows,
      funnel,
      judged,
      caps: await 상한읽기(),
      as_of: rows[0]?.as_of_date ?? 탈락기준일,
      notes: judged === 0 ? [`아직 ${country === "US" ? "미국" : "국내"} 판정을 돌린 적이 없습니다. Actions → 장기 적립 종목 판정 을 돌리세요`]
        : 빠진통과 > 0 ? [`판정 뒤 상장폐지·거래 중단된 통과 종목 ${빠진통과}개는 목록에서 뺐습니다 — 순위 번호가 건너뛸 수 있습니다`] : [],
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "조회에 실패했습니다";
    if (/no such (table|column)/i.test(message)) {
      return NextResponse.json({
        rows: [],
        funnel: [],
        judged: 0,
        as_of: null,
        notes: ["장기 적립 종목 표가 아직 없습니다. 판정 배치를 한 번 돌리면 만들어집니다"],
      });
    }
    return NextResponse.json({ errors: [message] }, { status: 500 });
  }
}
