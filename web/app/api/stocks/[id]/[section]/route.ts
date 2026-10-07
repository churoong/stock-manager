import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { type Exec, SECTIONS, type Section, loadSection, parseStockId, rangeSchema } from "@/lib/stockDetail";

/**
 * 종목 상세 섹션 하나 (docs/design.md 4장 종목 API, docs/stock_detail.md).
 *   prices?range=3M|1Y|3Y|5Y  financials  valuation  metrics  news  events  signals
 *
 * 섹션마다 따로 부르는 이유: 하나가 실패해도 화면의 나머지는 보여야 한다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
type Params = { params: Promise<{ id: string; section: string }> };

const exec: Exec = async (sql, args = []) => rowsToObjects(await execute(sql, args));

export async function GET(request: Request, { params }: Params) {
  const { id: rawId, section } = await params;
  const id = parseStockId(rawId);
  if (!id) return NextResponse.json({ errors: ["잘못된 종목 번호"] }, { status: 400 });
  if (!(SECTIONS as readonly string[]).includes(section)) {
    return NextResponse.json({ errors: ["없는 섹션"] }, { status: 404 });
  }
  const range = rangeSchema.safeParse(new URL(request.url).searchParams.get("range") ?? undefined);
  if (!range.success) return NextResponse.json({ errors: ["range 는 3M·1Y·3Y·5Y"] }, { status: 400 });
  try {
    const stock = (await exec("SELECT country FROM stocks WHERE id = ?", [id]))[0];
    if (!stock) return NextResponse.json({ errors: ["종목이 없습니다"] }, { status: 404 });
    const result = await loadSection(exec, section as Section, id, {
      range: range.data,
      country: String(stock.country),
    });
    return NextResponse.json(result);
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "조회 실패"] }, { status: 500 });
  }
}
