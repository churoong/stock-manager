import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";
import PaperView from "@/components/PaperView";

export const dynamic = "force-dynamic";

/**
 * 모의 투자 계좌 (docs/paper.md, 2026-10-15 사용자 지시). 입금만 사용자가 하고 매매는 배치의 규칙서가 한다.
 */
export default function PaperPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/paper" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="전략 — 가상모의투자" doc="docs/paper.md">
          넣은 예수금 안에서 계좌마다 제 규칙대로 사고팝니다. 실제 돈·실제 주문은 없습니다. B 는 아침 리포트 2부 그대로, A·C·D·E 는
          투자위원회를 여는 회사, G 는 B 에 추세 익절, H 는 회사들의 추천을 성적으로 합친 지주사(KIS모의투자가 베낌), R 은 무작위 대조입니다.
          복기용이며 규칙을 켜고 끄는 근거로는 쓰지 않습니다.
        </PageHeader>
        <PaperView />
      </main>
      <Footer />
    </div>
  );
}
