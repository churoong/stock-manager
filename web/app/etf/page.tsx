import EtfList from "@/components/EtfList";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";

export const dynamic = "force-dynamic";

/**
 * 장기 적립 ETF 화면.
 *
 * 오늘의 추천과 질문이 다르다. 그쪽은 "지금 좋은가", 여기는 "20년 동안 매달
 * 사도 되는가" 다. 그래서 탭을 나눴다(docs/etf.md 4장).
 */
export default function EtfPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/etf" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="장기 적립" doc="docs/etf.md">
          10~20년 동안 매달 정해진 금액을 사도 되는 ETF 입니다. 타이밍은 보지 않습니다.
          보수·규모·운용 이력·지수의 넓이로 고르고, <b>수익률로 줄 세우지 않습니다</b>.
          &quot;참고&quot; 로 적힌 추천 종목 겹침은 고르는 기준이 아닙니다.
        </PageHeader>
        <EtfList />
      </main>
      <Footer />
    </div>
  );
}
