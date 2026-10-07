import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { type Exec, loadOverview, parseStockId } from "@/lib/stockDetail";

/**
 * 종목 상세 머리: 마스터 + 최신 점수·팩터 + 보유·매도 플래그·관심 (docs/stock_detail.md).
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
type Params = { params: Promise<{ id: string }> };

// 라우트 파일은 HTTP 메서드 외의 이름을 내보내면 빌드가 막힌다
const tursoExec: Exec = async (sql, args = []) => rowsToObjects(await execute(sql, args));

export async function GET(_request: Request, { params }: Params) {
  const id = parseStockId((await params).id);
  if (!id) return NextResponse.json({ errors: ["잘못된 종목 번호"] }, { status: 400 });
  try {
    const overview = await loadOverview(tursoExec, id);
    if (!overview) return NextResponse.json({ errors: ["종목이 없습니다"] }, { status: 404 });
    return NextResponse.json(overview);
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "조회 실패"] }, { status: 500 });
  }
}
