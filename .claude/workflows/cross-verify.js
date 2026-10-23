export const meta = {
  name: 'cross-verify',
  description: 'stock-manager 푸시 전 교차검증 — 커밋 범위를 렌즈 셋(돈·장부·주문 / 리포트·텔레그램·웹 / 기록·시험)으로 반박, [필수] 합계 (docs/loops.md L3)',
  whenToUse: '묶음 푸시 전. 위험 커밋(KIS·일일 흐름·워크플로·마이그레이션·가상모의투자 주문 경로)은 [필수] 0 이어야 푸시',
  phases: [{ title: 'Verify', detail: '렌즈마다 교차검증 에이전트 하나' }],
}

// args: { range?: 'origin/main..HEAD', lenses?: string[] }
const 범위 = (args && args.range) || 'origin/main..HEAD'
const 렌즈 = (args && args.lenses) || [
  '돈·장부·주문 — 가상모의투자 장부·체결 판정·위험 점검·KIS모의투자 주문 경로·맞추기에 닿는 바뀐 줄',
  '리포트·텔레그램·웹 — 일일 리포트 조립·조각 나누기·저장 본문·웹 화면에 닿는 바뀐 줄',
  '기록·시험 — docs/infra.md 25.x 가 코드와 맞는가, 되돌리기 확인에 적은 시험이 실제로 그 규칙을 잡는가, 빠진 시험',
]
const RESULT = {
  type: 'object',
  properties: {
    must: { type: 'integer', minimum: 0 },
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          level: { type: 'string', enum: ['must', 'should', 'note'] },
          where: { type: 'string' }, what: { type: 'string' }, fix: { type: 'string' },
        },
        required: ['level', 'where', 'what'],
      },
    },
    ok: { type: 'string', description: '확인하고 문제없던 것' },
  },
  required: ['must', 'items'],
}

phase('Verify')
const 결과 = await parallel(렌즈.map((l, i) => () =>
  agent(`커밋 범위 ${범위} 를 교차검증하세요. 렌즈: ${l}. 첫 판단을 must(=[필수] 수)에 넣으세요.`,
    { agentType: 'stock-cross-verifier', schema: RESULT, label: `xv:${i + 1}`, phase: 'Verify' })))
const 살아 = 결과.filter(Boolean)
// 렌즈 하나라도 결과가 없으면 통과로 치지 않는다 — 위험 커밋 푸시 문
const 필수 = 살아.reduce((s, r) => s + (r.must || 0), 0)
return {
  range: 범위,
  must: 살아.length < 렌즈.length ? null : 필수,
  missingLenses: 렌즈.length - 살아.length,
  items: 살아.flatMap((r) => r.items || []),
  ok: 살아.map((r) => r.ok || ''),
}
