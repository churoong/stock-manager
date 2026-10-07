import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { issueTexts } from "@/lib/validationErrors";
import {
  BUY_COST_UNTIL,
  DIVIDENDS_LIST,
  DUPLICATE_WINDOW_MS,
  RECENT_SAME_DIVIDEND,
  capList,
  FX_ON_OR_BEFORE,
  dividendInputSchema,
  dividendSizeCheck,
  decideFx,
  fxDateNote,
  fxRangeWarning,
  isFutureDate,
  requestRecalc,
} from "@/lib/portfolio";

/**
 * 배당 수령 기록 (docs/portfolio.md 4장). 사용자 입력 원본. 세후 실수령 = 세전 − 원천징수.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
export async function GET() {
  try {
    const { rows, truncated } = capList(rowsToObjects(await execute(DIVIDENDS_LIST)));
    return NextResponse.json({ dividends: rows, truncated });
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
  const parsed = dividendInputSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  }
  const input = parsed.data;
  if (isFutureDate(input.pay_date)) {
    return NextResponse.json({ errors: ["미래 날짜의 배당은 기록할 수 없습니다"] }, { status: 400 });
  }
  if (input.tax > input.gross_amount) {
    return NextResponse.json({ errors: ["세금이 세전 금액보다 큽니다"] }, { status: 400 });
  }

  try {
    const stock = rowsToObjects<{ currency: string }>(
      await execute("SELECT currency FROM stocks WHERE id = ?", [input.stock_id]),
    )[0];
    if (!stock) return NextResponse.json({ errors: ["종목을 찾지 못했습니다"] }, { status: 400 });

    // 방금 같은 배당이 있으면 한 번 묻는다 — 저장 응답이 끊긴 뒤 다시 누른 것일 수 있다 (25.829)
    if (!input.confirm_duplicate) {
      const 이후 = new Date(Date.now() - DUPLICATE_WINDOW_MS).toISOString();
      const 같은 = (await execute(RECENT_SAME_DIVIDEND, [input.stock_id, input.pay_date, input.gross_amount, input.tax, 이후])).rows[0];
      if (같은) {
        return NextResponse.json(
          { errors: ["방금 같은 배당(종목·지급일·세전·세금)을 저장했습니다 — 한 번 더 저장할까요?"], duplicate: true, confirm_needed: "duplicate" },
          { status: 409 },
        );
      }
    }

    // 넣은 원금에 비해 말이 되는 크기인가 — 통화를 잘못 적은 배당을 잡는다 (docs/infra.md 25.404)
    const 원금 = Number(
      rowsToObjects<{ cost: number }>(await execute(BUY_COST_UNTIL, [input.stock_id, input.pay_date]))[0]?.cost ?? 0,
    );
    const 크기 = dividendSizeCheck(input.gross_amount, 원금, stock.currency);
    // **묻고, 확인하면 저장한다** (docs/infra.md 25.934, 감사). 예전에는 400 으로 끝이라, 기록 전부터 500주를 들고 있다가 1주만 새로 적은
    // 사용자의 진짜 배당은 **어떻게 해도 저장할 수 없었다**(원금 = 기록된 1주뿐). 통화 오타를 잡는 문(25.404)은 그대로 — 한 번 묻는다
    if (크기.error && !input.confirm_size) {
      return NextResponse.json(
        { errors: [크기.error], size_unit: true, confirm_needed: "size" }, // 둘째 문장은 화면(`lib/confirm.CONFIRM_HINT`)이 붙인다 (25.945)
        { status: 409 },
      );
    }
    if (크기.error) 크기.warning = 크기.error;

    // 환율 규칙은 `decideFx` 한 곳에 있다 (docs/infra.md 25.72). 여기서 다시 쓰지 않는다
    // 원화 종목은 환율을 찾지 않는다 — 읽기 예산도 아낀다 (docs/infra.md 24절)
    const fx =
      stock.currency === "KRW"
        ? undefined
        : rowsToObjects<{ date: string; rate: number }>(await execute(FX_ON_OR_BEFORE, [input.pay_date]))[0];
    const 환율 = decideFx(stock.currency, input.fx_rate, fx, input.pay_date);
    if ("error" in 환율) {
      return NextResponse.json(
        { errors: ["그날 환율이 저장돼 있지 않습니다. 환율을 직접 입력해 주세요"] },
        { status: 400 },
      );
    }
    const fxRate = 환율.rate;
    const fxSource = 환율.source;
    const fxWarning = fxRangeWarning(stock.currency, fxSource, fxRate);
    // 자동 환율이 그날 것이 아니면 어느 날 것인지 (25.591)
    const fxDateWarning = fxDateNote(fxSource, fx, input.pay_date);

    const now = new Date().toISOString();
    await execute(
      `INSERT INTO dividend_receipts (stock_id, pay_date, amount_per_share, quantity, gross_amount, tax, net_amount,
         currency, fx_rate, fx_rate_source, memo, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.stock_id, input.pay_date, input.amount_per_share ?? null, input.quantity ?? null, input.gross_amount,
        input.tax, input.gross_amount - input.tax, stock.currency, fxRate, fxSource, input.memo ?? null, now, now,
      ],
    );
    const recalc = await requestRecalc();
    return NextResponse.json({ ok: true, fx_rate: fxRate, fx_rate_source: fxSource, recalc, warnings: [fxWarning, fxDateWarning, 크기.warning].filter((w): w is string => Boolean(w)) });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "저장 실패"] }, { status: 500 });
  }
}
