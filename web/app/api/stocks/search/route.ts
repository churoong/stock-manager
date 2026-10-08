import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { SEARCH_LIST_TTL_MS, STOCK_SEARCH_LIST, filterSearch, type SearchRow } from "@/lib/portfolio";

/**
 * 종목 찾기(매매·배당 입력 폼·메뉴 검색). 티커·야후 심볼·한글·영문 이름으로 찾는다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 *
 * 검색 대상 목록을 서버 메모리에 잠시 둔다 (docs/infra.md 25.1032) — 검색마다 종목 표 전체를 훑지 않는다.
 * 서버(함수 인스턴스)가 새로 뜨면 다시 읽는다.
 */
let 목록: { rows: SearchRow[]; at: number } | null = null;

export async function GET(request: Request) {
  const q = new URL(request.url).searchParams.get("q")?.trim() ?? "";
  if (q.length < 1) return NextResponse.json({ stocks: [] });
  try {
    if (!목록 || Date.now() - 목록.at > SEARCH_LIST_TTL_MS) {
      목록 = { rows: rowsToObjects<SearchRow>(await execute(STOCK_SEARCH_LIST)), at: Date.now() };
    }
    return NextResponse.json({ stocks: filterSearch(목록.rows, q.slice(0, 40)) });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "검색 실패"] }, { status: 500 });
  }
}
