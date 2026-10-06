// data/items.json の形の検査（CI と収集の締めで使う。LLM 無し）。
// 行: {slug, name, summary?, updated_at (ISO), source_id (site.json の sources[].id), source_url, facets: {key: value}, fields?: {}}
import { readFileSync } from "node:fs";

const site = JSON.parse(readFileSync(new URL("../site.json", import.meta.url), "utf8"));
const rows = JSON.parse(readFileSync(new URL("../data/items.json", import.meta.url), "utf8"));
const srcIds = new Set(site.sources.map((s) => s.id));
const facetKeys = site.facets.map((f) => f.key);
const problems = [];
const slugs = new Set();
// 旧 slug の転送表（server.js の 301 解決の入力）。{ "旧slug": "現行slug か 元データID" }
if (site.slug_redirects !== undefined && (typeof site.slug_redirects !== "object" || site.slug_redirects === null || Array.isArray(site.slug_redirects) || Object.entries(site.slug_redirects).some(([k, v]) => !/^[a-z0-9][a-z0-9-]{0,120}$/.test(k) || typeof v !== "string" || !v))) problems.push("site.json の slug_redirects は {旧slug: 現行slug か 元データID} のオブジェクト");
if (site.slug_redirects && typeof site.slug_redirects === "object" && !Array.isArray(site.slug_redirects)) {
  const cur = new Set((Array.isArray(rows) ? rows : []).map((it) => it?.slug));
  for (const k of Object.keys(site.slug_redirects)) if (cur.has(k)) problems.push(`slug_redirects の「${k}」は現行の slug と重複（転送表に要らない）`);
}
if (!Array.isArray(rows)) problems.push("items.json は配列");
for (const [i, it] of (Array.isArray(rows) ? rows : []).entries()) {
  const at = `#${i} ${it?.slug ?? ""}`;
  if (!it?.slug || !/^[a-z0-9][a-z0-9-]{0,120}$/.test(it.slug)) problems.push(`${at}: slug が不正`);
  if (slugs.has(it?.slug)) problems.push(`${at}: slug 重複`);
  slugs.add(it?.slug);
  if (!it?.name) problems.push(`${at}: name が無い`);
  if (!it?.updated_at || Number.isNaN(Date.parse(it.updated_at))) problems.push(`${at}: updated_at が ISO でない`);
  if (!srcIds.has(it?.source_id)) problems.push(`${at}: source_id が site.json に無い（出典の無い行は載せない）`);
  if (!it?.source_url || !/^https?:\/\//.test(it.source_url)) problems.push(`${at}: source_url が無い（原典へ戻れない行は載せない）`);
  for (const k of Object.keys(it?.facets || {})) if (!facetKeys.includes(k)) problems.push(`${at}: facets.${k} は site.json の facets に無い`);
  if (it?.past_slugs !== undefined && (!Array.isArray(it.past_slugs) || it.past_slugs.some((p) => typeof p !== "string" || !/^[a-z0-9][a-z0-9-]{0,120}$/.test(p)))) problems.push(`${at}: past_slugs は旧 slug の文字列配列`);
  if (Array.isArray(it?.past_slugs) && it.past_slugs.includes(it?.slug)) problems.push(`${at}: past_slugs に現行 slug が入っている`);
}
// 中身の無いリンク集は載せない（2026-09-17 澤居）。site.json の answer.fields（問いの答えになる列）が行の fields に入っている割合
const answerFields = (site.answer && Array.isArray(site.answer.fields)) ? site.answer.fields : [];
if (!answerFields.length) problems.push("site.json の answer.fields が無い（利用者の問いの答えになる列を宣言する。出典リンクだけのサイトは作らない）");
else if (Array.isArray(rows) && rows.length) {
  const withAnswer = rows.filter((it) => answerFields.some((f) => { const v = (it?.fields || {})[f]; return v !== undefined && v !== null && v !== "" && !/^https?:\/\//.test(String(v)); })).length;
  const min = Number(site.answer.min_rate ?? 0.8);
  if (withAnswer / rows.length < min) problems.push(`答えの列が入っている行 ${withAnswer}/${rows.length}（下限 ${Math.round(min * 100)}%）。原典から値を取り込む`);
}
for (const p of problems.slice(0, 50)) console.error("NG", p);
console.log(`${Array.isArray(rows) ? rows.length : 0} 行、問題 ${problems.length}`);
process.exit(problems.length ? 1 : 0);
