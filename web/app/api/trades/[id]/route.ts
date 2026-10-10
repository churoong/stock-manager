import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { TRADES_FOR_HELD, deleteShortfallWarnings, requestRecalc, tradeEditErrors, tradeEditSchema } from "@/lib/portfolio";

/**
 * 매매 기록 한 건: 근거 스냅샷 보기(GET), 지우기(DELETE).
 *
 * 고치기: 메모·수수료·세금은 **제자리에서**(PATCH, 25.1116 — id 가 그대로라 같은 날 매수의 선입선출 순서가 안 바뀐다).
 * 날짜·종목·수량·체결가·투자 기간은 지우고 다시 넣는다. 매수 근거 스냅샷은 넣는 순간의 것이어야 해서, 날짜·수량을 바꾸며
 * 스냅샷을 그대로 두면 근거와 기록이 어긋난다.
 */
type Params = { params: Promise<{ id: string }> };

function parseId(raw: string): number | null {
  const id = Number(raw);
  return Number.isInteger(id) && id > 0 ? id : null;
}

export async function GET(_request: Request, { params }: Params) {
  const id = parseId((await params).id);
  if (!id) return NextResponse.json({ errors: ["잘못된 번호"] }, { status: 400 });
  try {
    const row = rowsToObjects(
      await execute(
        "SELECT t.*, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name FROM trades t JOIN stocks s ON s.id = t.stock_id WHERE t.id = ?",
        [id],
      ),
    )[0];
    if (!row) return NextResponse.json({ errors: ["기록이 없습니다"] }, { status: 404 });
    return NextResponse.json({ trade: row });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "조회 실패"] }, { status: 500 });
  }
}

export async function DELETE(_request: Request, { params }: Params) {
  const id = parseId((await params).id);
  if (!id) return NextResponse.json({ errors: ["잘못된 번호"] }, { status: 400 });
  let 파생지움 = false;
  try {
    // **지우면 그 뒤 매도가 보유를 넘기는지 먼저 본다** (docs/infra.md 25.150).
    // 막지는 않는다 — 매매 기록은 사용자 것이고 시스템이 임의로 손대지 않는다(CLAUDE.md).
    // 다만 그 자리에서 말해 준다. 배치의 같은 경고는 다음 재계산이 끝나야 보인다
    const 대상 = rowsToObjects<{ stock_id: number; side: string }>(
      await execute("SELECT stock_id, side FROM trades WHERE id = ?", [id]),
    )[0];
    if (!대상) return NextResponse.json({ errors: ["기록이 없습니다"] }, { status: 404 });
    // **못 본 것을 "문제 없음" 으로 만들지 않는다** (docs/infra.md 25.163).
    // 전에는 이 조회가 실패하면 빈 목록이 되어, 확인하고 괜찮았던 것과 구별되지 않았다
    const warnings =
      대상.side === "buy"
        ? await execute(TRADES_FOR_HELD, [대상.stock_id])
            // 배치와 같은 규칙으로, 지워서 **새로 늘어나는** 모자람만 (docs/infra.md 25.795)
            .then((r) => deleteShortfallWarnings(rowsToObjects<{ id: number; side: string; trade_date: string; quantity: number }>(r), id))
            .catch(() => [
              "지우기 전에 남은 매도를 확인하지 못했습니다(조회 실패). 지운 뒤 포트폴리오를 확인하세요",
            ])
        : [];

    // 체결 묶음은 파생이라 다음 재계산이 다시 만든다. 원본을 먼저 지우면 외래키가 걸리므로 묶음부터 비운다.
    // **복기도 같다** (docs/infra.md 25.225) — `trade_reviews.buy_trade_id` 도 `trades(id)` 를 가리킨다(0024).
    // 예전에는 묶음만 비워 판 적이 있는 매수를 지우면 외래키에 걸려 500 이었다
    파생지움 = true;
    await execute("DELETE FROM trade_lots WHERE buy_trade_id = ? OR sell_trade_id = ?", [id, id]);
    await execute("DELETE FROM trade_reviews WHERE buy_trade_id = ?", [id]);
    const rs = await execute("DELETE FROM trades WHERE id = ?", [id]);
    if (rs.affectedRows === 0) return NextResponse.json({ errors: ["기록이 없습니다"] }, { status: 404 });
    const recalc = await requestRecalc();
    return NextResponse.json({ ok: true, recalc, warnings });
  } catch (error) {
    // **파생 표를 지우다 멈췄으면 재계산을 부른다** (docs/infra.md 25.591, 감사). 묶음·복기는 지웠는데 원본 삭제가 일시 오류로 실패하면
    // 지문이 그대로라 재계산이 오지 않아, 다음 일일 배치까지 [실현손익]·[복기]에서 그 묶음만 빠진 채 요약과 어긋났다.
    // 원본은 그대로이므로 재계산이 파생 표를 원래대로 되살린다 [확인필요: Turso 파이프라인·D1 batch 의 원자성 — 그래서 한 요청으로 묶지 않았다]
    const recalc = 파생지움 ? await requestRecalc().catch(() => null) : null;
    return NextResponse.json(
      {
        errors: [
          (error instanceof Error ? error.message : "삭제 실패")
            + (파생지움 ? " — 원본은 지우지 못했습니다. 계산 결과를 다시 만들도록 요청했습니다" : ""),
        ],
        recalc,
      },
      { status: 500 },
    );
  }
}

/** 메모·수수료·세금을 제자리에서 고친다 (docs/infra.md 25.1116). 넣을 때와 같은 규칙으로 다시 본다 */
export async function PATCH(request: Request, { params }: Params) {
  const id = parseId((await params).id);
  if (!id) return NextResponse.json({ errors: ["잘못된 번호"] }, { status: 400 });
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ errors: ["요청 본문을 읽지 못했습니다"] }, { status: 400 });
  }
  const parsed = tradeEditSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ errors: parsed.error.issues.map((i) => `${i.path.join(".") || "본문"}: ${i.message}`) }, { status: 400 });
  }
  try {
    const row = rowsToObjects<{ stock_id: number; side: string; trade_date: string; price: number; quantity: number; fee: number | null; tax: number | null; memo: string | null }>(
      await execute("SELECT stock_id, side, trade_date, price, quantity, fee, tax, memo FROM trades WHERE id = ?", [id]),
    )[0];
    if (!row) return NextResponse.json({ errors: ["기록이 없습니다"] }, { status: 404 });
    const errors = tradeEditErrors(row, parsed.data);
    if (errors.length) return NextResponse.json({ errors }, { status: 400 });
    const 새 = { ...row, ...parsed.data };
    await execute("UPDATE trades SET memo = ?, fee = ?, tax = ?, updated_at = ? WHERE id = ?", [
      새.memo ?? null, 새.fee ?? null, 새.tax ?? null, new Date().toISOString(), id,
    ]);
    // 수수료·세금은 원가·실현손익을 바꾼다 — 메모만이면 다시 계산하지 않는다(Actions 분을 아낀다)
    const 계산 = parsed.data.fee !== undefined || parsed.data.tax !== undefined;
    const recalc = 계산 ? await requestRecalc() : null;
    return NextResponse.json({ ok: true, recalc });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "고치기 실패"] }, { status: 500 });
  }
}
