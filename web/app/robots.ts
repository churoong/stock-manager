import type { MetadataRoute } from "next";

// 개인용이다. 어떤 검색엔진도 들이지 않는다.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", disallow: "/" },
  };
}
