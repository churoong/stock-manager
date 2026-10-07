import { NextResponse } from "next/server";
import { execute } from "@/lib/db";

/**
 * 매도 플래그 확인 (docs/sell_flags.md). 확인한 플래그는 조건이 이어지는 동안 리포트에서 빠진다.
 * 매매를 만들지 않는다. 표시만 바꾼다(자동 매도 없음).
 */
type Params = { params: Promise<{ id: string }> };

export async function POST(_request: Request, { params }: Params) {
  const id = Number((await params).id);
  if (!Number.isInteger(id) || id <= 0) return NextResponse.json({ errors: ["잘못된 번호"] }, { status: 400 });
  try {
    // **살아 있는 행만** 확인한다 (docs/infra.md 25.926, 감사). 지난날 행은 지워지지 않고 비활성으로 남는데(sell_flags.py),
    // 다음 실행은 **활성 행의 확인만** 이어받는다. 밤새 열어 둔 화면의 어제 번호로 누르면 비활성 행에 찍혀 `{ok:true}` 가
    // 왔고, 다시 불러온 화면에는 오늘 행이 확인 안 됨으로 남았다 — 눌렀는데 그대로인 버튼이었다
    const rs = await execute(
      "UPDATE sell_flags SET dismissed_at = ? WHERE id = ? AND dismissed_at IS NULL AND is_active = 1",
      [new Date().toISOString(), id],
    );
    if (rs.affectedRows > 0) return NextResponse.json({ ok: true });
    // **바뀐 행이 없으면 까닭을 말한다** (docs/infra.md 25.554, 감사 재현). 저녁(미국) 매도 플래그 실행은 그날 행을 지우고
    // 새 번호로 다시 넣는다 — 아침에 연 화면의 옛 번호로 누르면 200 `{ok:false}` 가 와 화면은 성공처럼 다시 불러왔고,
    // 플래그는 확인 안 됨으로 남아 왜 안 되는지 알 수 없었다
    // 이미 확인된 **살아 있는** 행만 성공이다. 비활성 행(지난날 번호)은 지워진 번호와 같이 409 (25.926)
    const 행 = await execute(
      "SELECT dismissed_at FROM sell_flags WHERE id = ? AND is_active = 1 AND dismissed_at IS NOT NULL",
      [id],
    );
    if (행.rows.length > 0) return NextResponse.json({ ok: true, already: true }); // 이미 확인됨
    return NextResponse.json(
      { errors: ["그사이 매도 플래그가 다시 계산됐습니다 — 화면을 새로 불러온 뒤 다시 눌러 주세요"] },
      { status: 409 },
    );
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "저장 실패"] }, { status: 500 });
  }
}
