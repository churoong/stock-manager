export const meta = {
  name: 'defect-round',
  description: 'stock-manager 결함 루프 한 회차 — 영역별 감사 → 찾은 결함마다 다른 에이전트가 반박 → 진짜만 (docs/loops.md L2)',
  whenToUse: '평일 결함 회차. 고치기·시험·기록·교차검증·푸시는 이 결과를 받은 주 세션이 한다',
  phases: [
    { title: 'Audit', detail: '영역마다 감사 에이전트 하나 — 우선순위 ① 모의투자 정확성·무사고 먼저' },
    { title: 'Refute', detail: '찾은 결함마다 반박 에이전트 — 찾은 쪽과 확인하는 쪽이 다르다' },
  ],
}

// 역할 에이전트 부르기 — 등록된 이름(`.claude/agents/<이름>.md`)으로 먼저, 세션이 그 정의를 아직 읽지 않았으면(세션
// 시작 뒤에 더한 역할은 목록에 없다 — 25.1751 첫 실행) 범용 에이전트가 그 정의 파일을 읽고 따르게 한다. 정의는 한 곳
const 저장소 = (args && args.repo) || '/home/user/stock-manager'
async function 역할(이름, 글, opts) {
  try {
    const r = await agent(글, { ...opts, agentType: 이름 })
    if (r !== null && r !== undefined) return r
  } catch (e) {
    log(`${이름} 이 등록돼 있지 않아 정의 파일로 부른다`)
  }
  return agent(`역할 정의 ${저장소}/.claude/agents/${이름}.md 를 먼저 읽고 그 지시(읽기 전용·출력 꼴)를 그대로 따르세요.\n\n${글}`,
    { ...opts, agentType: 'general-purpose', label: `${opts.label || 이름}*` })
}

// args: { areas?: string[], maxRefute?: 5 }. 영역을 안 주면 아래 넷 — 주 세션이 docs/loops.md 회전표로 골라 준다
const 기본영역 = [
  '가상모의투자 장부·체결 판정 (batch/services/paper.py, batch/jobs/paper.py, docs/paper.md 2~3장)',
  'KIS모의투자 주문 경로·맞추기 (batch/jobs/broker_mirror.py, batch/services/broker_mirror.py, batch/sources/kis_vts.py, docs/paper.md 15장)',
  '매도 플래그·위험 점검·장중 감시 목록 (batch/services/sell_flags.py, batch/services/risk_gate.py, batch/jobs/monitor_targets.py)',
  '일일 배치 흐름·텔레그램 리포트·발송 (batch/jobs/daily.py, batch/notify/*, docs/reports.md)',
]
const 영역 = ((args && args.areas) || 기본영역).slice(0, 4)
const 반박상한 = Math.min((args && args.maxRefute) || 5, 5) // 4 + 5 = 9 에이전트

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' }, severity: { type: 'string', enum: ['must', 'should', 'note'] },
          where: { type: 'string', description: 'file:line' }, repro: { type: 'string' }, fix: { type: 'string' },
        },
        required: ['title', 'severity', 'where', 'repro'],
      },
    },
    scope: { type: 'string', description: '본 범위' },
  },
  required: ['findings'],
}
const REFUTE = {
  type: 'object',
  properties: {
    real: { type: 'string', enum: ['real', 'false', 'uncertain'] },
    severity: { type: 'string', enum: ['must', 'should', 'note'] },
    evidence: { type: 'string' }, fix: { type: 'string' },
  },
  required: ['real', 'evidence'],
}

phase('Audit')
const 감사 = await parallel(영역.map((a, i) => () =>
  역할('stock-defect-auditor', `맡은 영역: ${a}\n돈·장부·주문·알림을 틀리게 하는 실제 결함을 재현과 함께 찾으세요. docs/infra.md 25.x 에 이미 있는 것은 빼세요.`,
    { schema: FINDINGS, label: `audit:${i + 1}`, phase: 'Audit' })))
const 무게 = { must: 0, should: 1, note: 2 }
// 영역을 넘어 같은 자리를 둘이 낸 것은 하나로 — 반박 전에 전체가 필요해 여기는 장벽이 맞다
const 본 = new Set()
const 모두 = 감사.filter(Boolean).flatMap((r) => r.findings || [])
  .filter((f) => { const k = `${f.where}|${f.title}`; if (본.has(k)) return false; 본.add(k); return true })
  .sort((x, y) => 무게[x.severity] - 무게[y.severity])
const 고른 = 모두.filter((f) => f.severity !== 'note').slice(0, 반박상한)
if (모두.length > 고른.length) log(`결함 ${모두.length}건 중 ${고른.length}건만 반박 — 참고·넘친 것은 다음 회차(결과의 skipped)`)

const 판정 = await parallel(고른.map((f, i) => () =>
  역할('stock-refuter', `다음 결함 주장을 반박하세요.\n제목: ${f.title}\n자리: ${f.where}\n주장한 재현: ${f.repro}\n주장한 고칠 방향: ${f.fix || ''}`,
    { schema: REFUTE, label: `refute:${i + 1}`, phase: 'Refute' })
    .then((v) => ({ ...f, refute: v }))))
const 진짜 = 판정.filter(Boolean).filter((f) => f.refute && f.refute.real === 'real')
return {
  confirmed: 진짜,
  uncertain: 판정.filter(Boolean).filter((f) => f.refute && f.refute.real === 'uncertain'),
  refuted: 판정.filter(Boolean).filter((f) => f.refute && f.refute.real === 'false').map((f) => f.title),
  skipped: 모두.filter((f) => !고른.includes(f)),
  scopes: 감사.filter(Boolean).map((r) => r.scope || ''),
}
