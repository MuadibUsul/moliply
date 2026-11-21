import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  const base = (process.env.NEXT_PUBLIC_STORE_URL || "https://moliply.shop").replace(/\/$/, "");
  const now = new Date();
  return [
    { url: `${base}/`, lastModified: now, changeFrequency: "daily", priority: 1 },
    { url: `${base}/products`, lastModified: now, changeFrequency: "daily", priority: 0.9 },
    { url: `${base}/redeem`, lastModified: now, changeFrequency: "weekly", priority: 0.8 },
    { url: `${base}/help`, lastModified: now, changeFrequency: "weekly", priority: 0.6 },
  ];
}
