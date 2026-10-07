import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { STOCK_SEARCH, stockSearchArgs } from "@/lib/portfolio";

/**
 * 종목 찾기(매매·배당 입력 폼). 티커·야후 심볼·한글·영문 이름으로 찾는다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
export async function GET(request: Request) {
  const q = new URL(request.url).searchParams.get("q")?.trim() ?? "";
  if (q.length < 1) return NextResponse.json({ stocks: [] });
  try {
    const rs = await execute(STOCK_SEARCH, stockSearchArgs(q.slice(0, 40)));
    return NextResponse.json({ stocks: rowsToObjects(rs) });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "검색 실패"] }, { status: 500 });
  }
}
