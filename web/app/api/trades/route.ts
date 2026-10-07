import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { issueTexts } from "@/lib/validationErrors";
import { userTimeOf } from "@/lib/market";
import {
  FX_ON_OR_BEFORE,
  DUPLICATE_WINDOW_MS,
  TRADES_FOR_HELD,
  RECENT_SAME_TRADE,
  SNAPSHOT_AT,
  TRADE_INSERT,
  TRADE_PRICE_BASIS,
  TRADES_LIST,
  capList,
  decideFx,
  fxDateNote,
  fxRangeWarning,
  isFutureDate,
  heldOn,
  laterShortfall,
  qtyText,
  requestRecalc,
  tradeInputSchema,
  tradePriceCheck,
} from "@/lib/portfolio";

/**
 * 매매 기록 (docs/portfolio.md). 사용자 입력 원본만 저장한다. 손익은 배치가 계산한다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
export async function GET() {
  try {
    const { rows, truncated } = capList(rowsToObjects(await execute(TRADES_LIST)));
    return NextResponse.json({ trades: rows, truncated });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "조회 실패"] }, { status: 500 });
  }
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ errors: ["요청 본문을 읽지 못했습니다"] }, { status: 400 });
  }
  const parsed = tradeInputSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  }
  const input = parsed.data;
  if (isFutureDate(input.trade_date)) {
    return NextResponse.json({ errors: ["미래 날짜의 매매는 기록할 수 없습니다"] }, { status: 400 });
  }

  try {
    const stock = rowsToObjects<{ currency: string }>(
      await execute("SELECT currency FROM stocks WHERE id = ?", [input.stock_id]),
    )[0];
    if (!stock) return NextResponse.json({ errors: ["종목을 찾지 못했습니다"] }, { status: 400 });

    // 환율: 원화는 1. 미국은 입력값(증권사 체결 환율)이 우선, 없으면 그날 USDKRW 종가 (사용자 결정 2026-09-17)
    // 환율 규칙은 `decideFx` 한 곳에 있다 (docs/infra.md 25.72). 여기서 다시 쓰지 않는다
    // 원화 종목은 환율을 찾지 않는다 — 읽기 예산도 아낀다 (docs/infra.md 24절)
    const fx =
      stock.currency === "KRW"
        ? undefined
        : rowsToObjects<{ date: string; rate: number }>(await execute(FX_ON_OR_BEFORE, [input.trade_date]))[0];
    const 환율 = decideFx(stock.currency, input.fx_rate, fx, input.trade_date);
    if ("error" in 환율) {
      return NextResponse.json(
        { errors: ["그날 환율이 저장돼 있지 않습니다. 증권사 체결 환율을 직접 입력해 주세요"] },
        { status: 400 },
      );
    }
    const fxRate = 환율.rate;
    const fxSource = 환율.source;
    const fxWarning = fxRangeWarning(stock.currency, fxSource, fxRate);
    // 자동 환율이 그날 것이 아니면 어느 날 것인지 (25.591)
    const fxDateWarning = fxDateNote(fxSource, fx, input.trade_date);

    // 방금 같은 매매가 저장됐으면 묻는다 (docs/infra.md 25.785) — 응답이 늦어 다시 누른 것일 수 있다.
    // **매도 보유 검사보다 먼저** (25.789, 교차검증) — 매도를 다시 누르면 "보유 0주보다 많이 팔 수 없습니다" 가 먼저 떴다
    if (!input.confirm_duplicate) {
      const 이후 = new Date(Date.now() - DUPLICATE_WINDOW_MS).toISOString();
      const 같은것 = rowsToObjects<{ created_at: string }>(
        await execute(RECENT_SAME_TRADE, [input.stock_id, input.side, input.trade_date, input.price, input.quantity, 이후]),
      )[0];
      if (같은것) {
        return NextResponse.json(
          {
            errors: [`${userTimeOf(같은것.created_at)}(한국 시각)에 같은 매매(${input.trade_date} ${input.side === "buy" ? "매수" : "매도"} ${qtyText(input.quantity)}주 × ${input.price.toLocaleString()})가 이미 저장됐습니다 — 응답이 늦어 다시 누른 것이면 목록을 확인해 주세요`],
            duplicate: true,
            confirm_needed: "duplicate", // 화면의 확인 루프 하나가 읽는 이름 (25.945). 옛 깃발도 남긴다
          },
          { status: 409 },
        );
      }
    }

    if (input.side === "sell") {
      // 보유는 **배치와 같은 규칙**으로 센다 — 날짜순·같은 날 매수 먼저, 보유를 넘는 매도는 보유분까지만 (docs/infra.md 25.793)
      const 기록 = rowsToObjects<{ id: number; side: string; trade_date: string; quantity: number }>(
        await execute(TRADES_FOR_HELD, [input.stock_id]),
      );
      // 같은 날의 기존 매도 뒤에 넣는다 — 새 기록은 id 가 가장 크다
      const held = heldOn(기록, input.trade_date);
      if (input.quantity > held + 1e-9) {
        return NextResponse.json(
          { errors: [`그날까지 보유 ${qtyText(held)}주보다 많이 팔 수 없습니다. 매수 기록을 먼저 넣어 주세요`] },
          { status: 400 },
        );
      }
      // 그 뒤에 이미 있는 매도까지 본다 (docs/infra.md 25.201). 과거 날짜로 끼워 넣는 길이다
      const 뒤 = laterShortfall(기록, { trade_date: input.trade_date, quantity: input.quantity });
      if (뒤) {
        return NextResponse.json(
          { errors: [`이 매도를 넣으면 ${뒤.d} 매도가 보유보다 ${qtyText(뒤.short)}주 많아집니다. 날짜와 수량을 확인해 주세요`] },
          { status: 400 },
        );
      }
    }

    // 체결가 크기 검사 (docs/infra.md 25.785). **중복 확인 뒤에** 둔다 (25.790, 교차검증) — 확인하고 저장한 매매를 다시 누르면
    // "100배 넘게 다릅니다" 가 먼저 떠 앞 저장이 실패한 것처럼 보였다 — 통화 실수는 막고, 그 안쪽의 이상한 값은 저장하되 알린다
    const 시세 = rowsToObjects<{ date: string; close: number | null; high: number | null; low: number | null }>(
      await execute(TRADE_PRICE_BASIS, [input.stock_id, input.trade_date, input.trade_date]),
    )[0];
    const 가격검사 = tradePriceCheck(input.price, 시세, input.trade_date, stock.currency);
    // 역분할이 100:1 을 넘는 종목의 옛 매매는 정당하게 벌어진다 — 막지 않고 묻는다 (25.789, 교차검증)
    if (가격검사.error && !input.confirm_price) {
      return NextResponse.json({ errors: [가격검사.error], price_unit: true, confirm_needed: "price_unit" }, { status: 409 });
    }
    // 매수 당시 근거를 얼린다(복기의 기준). 매도는 비운다
    let snapshot: { as_of_date: string | null; total_score: number | null; factor_scores: string | null; sentiment_score: number | null; signal_type: string | null } | undefined;
    if (input.side === "buy") {
      snapshot = rowsToObjects<NonNullable<typeof snapshot>>(
        await execute(SNAPSHOT_AT, [input.horizon ?? "long", input.stock_id, input.trade_date]),
      )[0];
    }

    const now = new Date().toISOString();
    await execute(TRADE_INSERT, [
      input.stock_id, input.side, input.trade_date, input.price, input.quantity, stock.currency, fxRate, fxSource,
      input.fee ?? null, input.tax ?? null, input.horizon ?? null, input.memo ?? null,
      snapshot?.as_of_date ?? null, snapshot?.total_score ?? null, snapshot?.signal_type ?? null,
      snapshot?.sentiment_score ?? null, snapshot?.factor_scores ?? null, now, now,
    ]);
    const recalc = await requestRecalc();
    return NextResponse.json({ ok: true, fx_rate: fxRate, fx_rate_source: fxSource, recalc, warnings: [가격검사.warning, fxWarning, fxDateWarning].filter((w): w is string => Boolean(w)) });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "저장 실패"] }, { status: 500 });
  }
}
