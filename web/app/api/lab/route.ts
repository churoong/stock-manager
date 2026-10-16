import { NextResponse } from "next/server";
import { execute, explain, ifMissingTable, rowsToObjects } from "@/lib/db";
import { LAB_ATTRIBUTION_SQL, LAB_HOLDING_SQL, holdingPoints, type LabAttribution } from "@/lib/lab";
import type { Attribution } from "@/lib/paper";

/**
 * 연구실 — 성과 분해 비교·지주사 가중치 추이 (docs/desk.md 5장, docs/infra.md 25.1506). 미들웨어가 인증 뒤에 둔다.
 * 배치가 적은 것을 읽기만 한다. 표가 없으면(마이그레이션 전) 빈 것.
 */
export async function GET() {
  try {
    const [attr, hold] = await Promise.all([
      execute(LAB_ATTRIBUTION_SQL).catch(ifMissingTable(null)),
      execute(LAB_HOLDING_SQL).catch(ifMissingTable(null)),
    ]);
    const attribution: LabAttribution[] = (attr ? rowsToObjects<{ account_id: string; as_of: string; attribution_json: string | null; name: string | null }>(attr) : []).map(
      (r) => {
        let a: Attribution | null = null;
        try {
          a = JSON.parse(r.attribution_json || "null");
        } catch {
          a = null;
        }
        return { id: r.account_id, name: r.name ?? r.account_id, as_of: r.as_of, attribution: a };
      },
    );
    const holding = holdingPoints(hold ? rowsToObjects<{ kr: string | null; kr_run: string | null; us: string | null; us_run: string | null }>(hold) : []);
    return NextResponse.json({ attribution, holding });
  } catch (e) {
    return NextResponse.json({ errors: [explain(e instanceof Error ? e.message : String(e))] }, { status: 500 });
  }
}
