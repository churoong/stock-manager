import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";
import RecommendList from "@/components/RecommendList";

export const dynamic = "force-dynamic";

/**
 * 추천 종목 화면.
 *
 * 종목 찾기(스크리너)와 짝을 이룬다. **그쪽은 내가 조건을 정하고, 여기는
 * 규칙이 고른 결과를 본다.**
 *
 * docs/design.md 3.9 절의 **1부 개별 종목**이다. 포트폴리오 제약을 보지 않고
 * 종목 자체를 본다. 1부만 보고 판단해도 되게 만든다.
 */
export default function RecommendPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/recommend" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="오늘의 추천" doc="docs/signals.md">
          조건을 정하지 않아도 규칙이 고른 종목을 보여 줍니다. 근거 문장에 나오는 수치는 전부 저장된 값입니다.
          단기·중기·장기는 보는 것이 달라 섞지 않고 나눠 보여 줍니다.
        </PageHeader>
        <RecommendList />
      </main>
      <Footer />
    </div>
  );
}
