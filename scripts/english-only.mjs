#!/usr/bin/env node
// US 向けの面に日本語を出さない止め具（配布元 portal-zukan templates/portal-starter/scripts/english-only.mjs）。
// npm run validate から呼ばれ、日本語（かな・漢字・全角カナ）が site.json と data/ に 1 文字でもあれば exit 1 で止める。
// validate は本番デプロイの前段なので、収集・製品・PO のどの担当が日本語を書いても本番に出る前に止まる。
// 日本の面（site.json の lang が en 以外、かつ slug が us- で始まらない）では何もしない。
// 例外は .english-only.json の allow に { "text": "…", "why": "…" } で書く（why の無い行は無効）。
// 2026-10-05 澤居「US向けのサイトで、日本語が混じっている箇所がある。ちゃんと全て英語にする」。実測: US 68 面中 61 面に日本語。
import fs from "node:fs";
import path from "node:path";

const ROOT = process.cwd();
const JA = /[぀-ヿ㐀-鿿ｦ-ﾟ　-〿！-／：-＠]+/g;
const read = (p) => { try { return fs.readFileSync(p, "utf8"); } catch { return null; } };

let site = {};
// site.json が壊れていたら止める。読めないまま {} にすると US 判定が外れて素通りする（2026-10-06 の破る試験で実測）。
try { site = JSON.parse(read(path.join(ROOT, "site.json")) || "{}"); } catch (e) {
  console.error(`english-only: site.json を読めない（${e.message}）。US 向けかどうか判定できないので止める`);
  process.exit(1);
}
const repoName = path.basename(ROOT);
const isUS = site.lang === "en" || String(site.slug || "").startsWith("us-") || repoName.startsWith("us-") || process.env.ENGLISH_ONLY === "1";
if (!isUS) { console.log("english-only: 日本の面なので検査しない"); process.exit(0); }

let conf = {};
try { conf = JSON.parse(read(path.join(ROOT, ".english-only.json")) || "{}"); } catch { conf = {}; }
// かな・漢字を含む語は例外にできない（US 向けの面に日本語の語を残す抜け道になる）。
// 2026-10-06 実測: 製品の担当が日本のサイト名「30代転職ナビ」を例外に入れ、US の面のフッターに出し続けていた。
const KANA_KANJI = /[぀-ヿ㐀-鿿]/;
const rejected = (conf.allow || []).filter((a) => a && a.text && KANA_KANJI.test(a.text));
if (rejected.length) {
  console.error(`english-only: .english-only.json の例外にかな・漢字の語がある（${rejected.map((a) => a.text).join("、")}）。US 向けの面では英語に直す（リンク名なら英語の名前を付ける）`);
  process.exit(1);
}
const allow = new Set((conf.allow || []).filter((a) => a && a.text && a.why).map((a) => a.text));
const roots = ["site.json", "data", "public", ...(conf.paths || [])];
const EXT = /\.(json|jsonl|ndjson|csv|tsv|md|txt|html|xml)$/i;
const SKIP = /(^|\/)(node_modules|\.git|\.next|dist|loop|docs)(\/|$)/;

const hits = [];
const walk = (rel) => {
  const abs = path.join(ROOT, rel);
  let st; try { st = fs.statSync(abs); } catch { return; }
  if (SKIP.test(rel)) return;
  if (st.isDirectory()) { for (const n of fs.readdirSync(abs)) walk(path.join(rel, n)); return; }
  if (!EXT.test(rel) || st.size > 200 * 1024 * 1024) return;
  const text = read(abs) || "";
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    for (const m of line.matchAll(JA)) {
      if (allow.has(m[0])) continue;
      hits.push(`${rel}:${i + 1}: ${m[0].slice(0, 40)}  …${line.slice(Math.max(0, m.index - 30), m.index + 40).replace(/\s+/g, " ")}`);
    }
  });
};
for (const r of roots) walk(r);

// 出力の検査（2026-10-05 追加）。site.json と data/ が英語でも、server.js の固定文（ナビ・見出し・404・llms.txt・
// robots.txt・全角括弧の連結）から日本語が出る（同日の実測: 雛形の T 辞書の漏れ 2 か所・各面の手足し）。
// server.js の応答をそのまま作り、トップ・about・llms.txt・robots.txt・API・404 と sitemap から最大 80 本を調べる。
// server.js が handler を出していれば直接呼び、出していなければ子プロセスで空き port に立てて HTTP で取る
// （IndexNow の初回送信は 90 秒後なので、その前に止める）。ENGLISH_ONLY_RENDER=0 で飛ばせる（理由を残すこと）。
const serverPath = path.join(ROOT, "server.js");
if (process.env.ENGLISH_ONLY_RENDER !== "0" && fs.existsSync(serverPath)) {
  const src = read(serverPath) || "";
  let render, child = null;
  if (/module\.exports\s*=\s*\{[^}]*\bhandler\b|exports\.handler|createApp/.test(src)) {
    const { createRequire } = await import("node:module");
    const server = createRequire(import.meta.url)(serverPath);
    const handler = server.createApp ? server.createApp().handler : server.handler;
    render = async (url) => {
      let body;
      await handler({ url, method: "GET", headers: { host: site.host } }, { writeHead() {}, setHeader() {}, end(b) { body = b; } });
      return Buffer.from(body || "").toString("utf8");
    };
  } else {
    const { spawn } = await import("node:child_process");
    const port = 20000 + Math.floor(Math.random() * 20000);
    child = spawn(process.execPath, [serverPath], { cwd: ROOT, env: { ...process.env, PORT: String(port) }, stdio: "ignore" });
    process.on("exit", () => { try { child.kill(); } catch {} });
    for (let i = 0; i < 100; i++) {
      try { await fetch(`http://127.0.0.1:${port}/robots.txt`); break; } catch { await new Promise((r) => setTimeout(r, 200)); }
    }
    render = async (url) => (await fetch(`http://127.0.0.1:${port}${url}`, { redirect: "manual" })).text();
  }
  const pages = new Set(["/", "/about/", "/llms.txt", "/robots.txt", "/api/items?limit=5", "/does-not-exist/", "/?page=2"]);
  const all = [];
  const walkMap = async (url, depth = 0) => {
    const xml = await render(url);
    for (const m of xml.matchAll(/<loc>(.*?)<\/loc>/g)) {
      const u = new URL(m[1].replaceAll("&amp;", "&"));
      const p = u.pathname + u.search;
      if (p.endsWith(".xml") && depth < 3) await walkMap(p, depth + 1); else all.push(p);
    }
  };
  try { await walkMap("/sitemap.xml"); } catch (e) { hits.push(`(出力) sitemap.xml を作れない: ${e.message}`); }
  const step = Math.max(1, Math.floor(all.length / 80));
  for (let i = 0; i < all.length; i += step) pages.add(all[i]);
  for (const p of pages) {
    let body = "";
    try { body = await render(p); } catch (e) { hits.push(`(出力) ${p}: 応答を作れない ${e.message}`); continue; }
    for (const m of body.matchAll(JA)) {
      if (allow.has(m[0])) continue;
      hits.push(`(出力) ${p}: ${m[0].slice(0, 40)}  …${body.slice(Math.max(0, m.index - 30), m.index + 40).replace(/\s+/g, " ")}`);
      break;
    }
  }
  if (child) child.kill();
}

if (hits.length) {
  console.error(`english-only: 日本語が ${hits.length} 箇所（US 向けの面は英語だけ）。英語に直すか、固有名なら .english-only.json の allow に理由つきで書く:`);
  for (const h of hits.slice(0, 60)) console.error("  " + h);
  if (hits.length > 60) console.error(`  …ほか ${hits.length - 60} 箇所`);
  process.exit(1);
}
console.log("english-only: 日本語 0 箇所");
