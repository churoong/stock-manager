import { NextResponse } from "next/server";
import { envReport } from "@/lib/envCheck";

/**
 * 환경변수 점검 (docs/infra.md 25.842). **값은 내보내지 않는다** — 들어 있는지·모양이 맞는지만.
 * DB 를 읽지 않아 D1·Turso 가 막힌 날에도 보인다. 미들웨어가 로그인 뒤에 둔다.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({ env_check: envReport() }, { headers: { "Cache-Control": "no-store" } });
}
