// US Senior Care Facility Finder. Zero dependencies, no build step.
// data/items.json + site.json -> home / type / state / state x type / city lists (path pagination
// /page/N/ so static assets on Cloudflare serve every page), item detail, about, sitemaps, robots,
// llms.txt, /api/items, /api/collection-status, /healthz.
// Production is Cloudflare Workers Static Assets: scripts/deploy-cf.py runs this server once on the
// ops machine, crawls every page and uploads the bytes. Pagination therefore must be a real path,
// never a query string (assets ignore the query).
const http = require("http");
const fs = require("fs");
const path = require("path");

const ROOT = __dirname;
const PORT = process.env.PORT || 3000;
const site = JSON.parse(fs.readFileSync(path.join(ROOT, "site.json"), "utf8"));
const BASE = `https://${site.host}`;
const PAGE_SIZE = 50;
const SITEMAP_CHUNK = 10000;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const slugify = (s) => String(s).toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/&/g, " and ").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
const plural = (t) => ({
  "Nursing home": "Nursing homes", "Hospice": "Hospices", "Home health agency": "Home health agencies",
  "Community health center": "Community health centers", "Palliative care program": "Palliative care programs",
  "Home care agency": "Home care agencies", "Long-term care facility": "Long-term care facilities",
  "Adult care home": "Adult care homes", "State veterans home": "State veterans homes",
  "Continuing care retirement community": "Continuing care retirement communities",
}[t] || t);

// ---------- data + indexes (rebuilt when items.json changes) ----------
let D = null;
function data() {
  const p = path.join(ROOT, "data", "items.json");
  const mt = fs.statSync(p).mtimeMs;
  if (D && D.mt === mt) return D;
  const rows = JSON.parse(fs.readFileSync(p, "utf8"));
  const bySlug = new Map(rows.map((r) => [r.slug, r]));
  const group = (fn) => {
    const m = new Map();
    for (const r of rows) { const k = fn(r); if (!k) continue; if (!m.has(k)) m.set(k, []); m.get(k).push(r); }
    return m;
  };
  const types = group((r) => r.facets.type);
  const states = group((r) => r.facets.state);
  const cities = group((r) => r.facets.city);
  const stateType = group((r) => r.facets.state && `${r.facets.state}|${r.facets.type}`);
  const typeSlug = new Map([...types.keys()].map((t) => [slugify(plural(t)), t]));
  const stateSlug = new Map([...states.keys()].map((s) => [slugify(s), s]));
  const citySlug = new Map([...cities.keys()].map((c) => [slugify(c), c]));
  const updated = rows.reduce((a, r) => (r.updated_at > a ? r.updated_at : a), "");
  D = { mt, rows, bySlug, types, states, cities, stateType, typeSlug, stateSlug, citySlug, updated };
  return D;
}
const typeUrl = (t) => `/type/${slugify(plural(t))}/`;
const stateUrl = (s) => `/state/${slugify(s)}/`;
const stateTypeUrl = (s, t) => `/state/${slugify(s)}/${slugify(plural(t))}/`;
const cityUrl = (c) => `/city/${slugify(c)}/`;
const itemUrl = (r) => `/items/${r.slug}/`;
const sortedKeys = (m) => [...m.keys()].sort((a, b) => a.localeCompare(b));
const byCount = (m) => [...m.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));

// ---------- HTML ----------
const GA4 = /^G-[A-Z0-9]{6,}$/.test(String(site.ga4_measurement_id || "")) ? `<script async src="https://www.googletagmanager.com/gtag/js?id=${site.ga4_measurement_id}"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','${site.ga4_measurement_id}');
document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a[data-outbound]');if(a&&typeof gtag==='function')gtag('event','click_outbound',{link_url:a.href});});</script>` : "";
const CSS = `body{margin:0;font:16px/1.65 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#1b1f23;background:#fff}
main{max-width:1000px;margin:0 auto;padding:20px 16px}header{border-bottom:1px solid #e3e6ea;background:#f6f8fa}
header .in{max-width:1000px;margin:0 auto;padding:12px 16px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
a{color:#0b57d0}h1{font-size:1.65rem;line-height:1.3;margin:.3em 0 .5em}h2{font-size:1.2rem;margin-top:1.8em}
table{border-collapse:collapse;width:100%;font-size:.94rem}th,td{border-bottom:1px solid #e3e6ea;padding:8px 6px;text-align:left;vertical-align:top}
th{background:#f6f8fa}.wrap{overflow-x:auto}.chips a{display:inline-block;margin:0 6px 8px 0;padding:6px 10px;border:1px solid #cdd3da;border-radius:6px;text-decoration:none;min-height:32px}
.pager a,.pager strong{display:inline-block;margin:0 4px 4px 0;padding:4px 10px;border:1px solid #cdd3da;border-radius:6px;text-decoration:none}
dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px}dt{color:#57606a}.src{font-size:.88rem;color:#57606a}
.crumbs{font-size:.88rem;color:#57606a}footer{max-width:1000px;margin:32px auto;padding:16px;color:#57606a;font-size:.88rem;border-top:1px solid #e3e6ea}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px}.cards a{border:1px solid #cdd3da;border-radius:6px;padding:8px 10px;text-decoration:none;display:block}
.cards a span{display:block;font-size:.85rem;color:#57606a}@media (max-width:560px){dl{grid-template-columns:1fr}}`;

function page({ url, title, description, h1, body, jsonld = [], crumbs = [], noindex = false }) {
  const canonical = BASE + url;
  const ld = [...(Array.isArray(jsonld) ? jsonld : [jsonld])];
  if (crumbs.length) ld.push({ "@context": "https://schema.org", "@type": "BreadcrumbList", itemListElement: [["Home", "/"], ...crumbs].map(([n, u], i) => ({ "@type": "ListItem", position: i + 1, name: n, item: BASE + u })) });
  const crumbHtml = crumbs.length ? `<p class="crumbs">${[["Home", "/"], ...crumbs].map(([n, u], i, a) => i === a.length - 1 ? esc(n) : `<a href="${esc(u)}">${esc(n)}</a>`).join(" › ")}</p>` : "";
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
${noindex ? '<meta name="robots" content="noindex">' : ""}<title>${esc(title)}</title><meta name="description" content="${esc(description)}"><link rel="canonical" href="${canonical}">
<meta property="og:title" content="${esc(title)}"><meta property="og:description" content="${esc(description)}"><meta property="og:type" content="website"><meta property="og:url" content="${canonical}"><meta property="og:site_name" content="${esc(site.name)}">
${ld.map((j) => `<script type="application/ld+json">${JSON.stringify(j).replace(/</g, "\\u003c")}</script>`).join("\n")}
${GA4}<style>${CSS}</style></head>
<body><header><div class="in"><a href="/"><strong>${esc(site.name)}</strong></a><nav><a href="/">Home</a> · <a href="/about/">Sources &amp; updates</a></nav></div></header>
<main>${crumbHtml}<h1>${esc(h1)}</h1>${body}</main>
<footer>${esc(site.name)} (${esc(site.host)}) lists ${data().rows.length.toLocaleString("en-US")} US care facilities and providers from public directories. Each entry cites its source page and retrieval date.
Not medical advice; confirm admission, coverage and availability with the facility.<br>Operated by <a href="${esc(site.operator_url)}">${esc(site.operator)}</a>.
${(site.siblings || []).map((s) => ` · <a href="${esc(s.url)}">${esc(s.name)}</a>`).join("")}</footer></body></html>`;
}

const keyCell = (r) => {
  const f = r.fields || {};
  if (f["CMS overall star rating"] && /^\d/.test(f["CMS overall star rating"])) return `CMS ${esc(f["CMS overall star rating"])} stars`;
  if (f["Certified beds"]) return `${esc(f["Certified beds"])} beds`;
  if (f["Licensed beds"]) return `${esc(f["Licensed beds"])} beds`;
  if (f["Services"]) return esc(f["Services"]);
  if (f["Palliative care settings"]) return esc(f["Palliative care settings"]);
  return "";
};
function table(rows) {
  if (!rows.length) return "<p>No entries.</p>";
  return `<div class="wrap"><table><thead><tr><th>Name</th><th>Type</th><th>City</th><th>Phone</th><th>Key facts</th></tr></thead><tbody>${rows.map((r) =>
    `<tr><td><a href="${itemUrl(r)}">${esc(r.name)}</a></td><td>${esc(r.facets.type)}</td><td>${esc(r.facets.city || r.fields.State || "")}</td><td>${esc(r.fields.Phone || "")}</td><td>${keyCell(r)}</td></tr>`).join("")}</tbody></table></div>`;
}
function pager(base, pageNo, max) {
  if (max <= 1) return "";
  const href = (n) => (n === 1 ? base : `${base}page/${n}/`);
  const out = [];
  if (pageNo > 1) out.push(`<a href="${href(1)}" rel="first">First</a><a href="${href(pageNo - 1)}" rel="prev">Prev</a>`);
  for (let n = Math.max(1, pageNo - 4); n <= Math.min(max, pageNo + 4); n++) out.push(n === pageNo ? `<strong>${n}</strong>` : `<a href="${href(n)}">${n}</a>`);
  if (pageNo < max) out.push(`<a href="${href(pageNo + 1)}" rel="next">Next</a><a href="${href(max)}" rel="last">Last</a>`);
  return `<p class="pager">${out.join(" ")}</p>`;
}
function listPage({ base, rows, pageNo, title, h1, intro, description, crumbs, extra = "" }) {
  const max = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  if (pageNo > max) return null;
  const part = rows.slice((pageNo - 1) * PAGE_SIZE, pageNo * PAGE_SIZE);
  const url = pageNo > 1 ? `${base}page/${pageNo}/` : base;
  const suffix = pageNo > 1 ? ` (page ${pageNo} of ${max})` : "";
  const range = rows.length > PAGE_SIZE ? `<p class="src">Showing ${(pageNo - 1) * PAGE_SIZE + 1}–${Math.min(pageNo * PAGE_SIZE, rows.length)} of ${rows.length.toLocaleString("en-US")}.</p>` : "";
  const ld = { "@context": "https://schema.org", "@type": "ItemList", name: h1, numberOfItems: rows.length,
    itemListElement: part.map((r, i) => ({ "@type": "ListItem", position: (pageNo - 1) * PAGE_SIZE + i + 1, name: r.name, url: BASE + itemUrl(r) })) };
  return page({ url, title: title + suffix, description, h1: h1 + suffix, crumbs, jsonld: ld,
    body: `${pageNo === 1 ? intro + extra : ""}${range}${pager(base, pageNo, max)}${table(part)}${pager(base, pageNo, max)}` });
}
const chips = (entries) => `<p class="chips">${entries.map(([label, href, n]) => `<a href="${esc(href)}">${esc(label)} (${n.toLocaleString("en-US")})</a>`).join("")}</p>`;
const sortRows = (rows) => [...rows].sort((a, b) => (a.facets.city || "~").localeCompare(b.facets.city || "~") || a.name.localeCompare(b.name));

function home(pageNo) {
  const d = data();
  const intro = `<p>${esc(site.description)}</p>
<p><strong>${esc(site.answer.question)}</strong> Every entry shows the care type, city and state, and the values its source publishes: address, phone, CMS star ratings, staffing, certified beds and federal penalties for nursing homes; services for home health and hospice agencies; licensed beds for adult care homes.</p>
<h2>By type of care</h2>${chips(byCount(d.types).map(([t, rs]) => [plural(t), typeUrl(t), rs.length]))}
<h2>By state</h2>${chips(sortedKeys(d.states).map((s) => [s, stateUrl(s), d.states.get(s).length]))}
<h2>All facilities</h2>`;
  const ld = { "@context": "https://schema.org", "@type": "Dataset", name: site.name, description: site.description, url: BASE + "/",
    license: site.license, creator: { "@type": "Organization", name: site.operator, url: site.operator_url }, dateModified: d.updated.slice(0, 10),
    distribution: [{ "@type": "DataDownload", encodingFormat: "application/json", contentUrl: BASE + "/api/items?limit=500" }],
    isBasedOn: site.sources.map((s) => s.url) };
  const html = listPage({ base: "/", rows: sortRows(d.rows), pageNo, title: `${site.name}: nursing homes, home health, hospice and senior care by state`, h1: site.name,
    intro, description: site.description, crumbs: [] });
  if (!html) return null;
  return pageNo === 1 ? html.replace("</head>", `<script type="application/ld+json">${JSON.stringify(ld)}</script></head>`) : html;
}

function itemPage(r) {
  const d = data();
  const f = r.fields || {};
  const src = site.sources.find((s) => s.id === r.source_id) || {};
  const dl = Object.entries(f).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${/^https?:\/\//.test(v) ? `<a href="${esc(v)}" rel="nofollow" data-outbound>${esc(v)}</a>` : esc(v)}</dd>`).join("");
  const peers = (d.cities.get(r.facets.city) || []).filter((x) => x.slug !== r.slug).slice(0, 8);
  const sameType = r.facets.state ? (d.stateType.get(`${r.facets.state}|${r.facets.type}`) || []).filter((x) => x.slug !== r.slug && x.facets.city !== r.facets.city).slice(0, 4) : [];
  const card = (x) => `<a href="${itemUrl(x)}"><strong>${esc(x.name)}</strong><span>${esc(x.facets.type)} · ${esc(x.facets.city || "")}</span></a>`;
  const links = [[`All ${plural(r.facets.type).toLowerCase()}`, typeUrl(r.facets.type)]];
  if (r.facets.state) links.push([`${plural(r.facets.type)} in ${r.facets.state}`, stateTypeUrl(r.facets.state, r.facets.type)], [`All care in ${r.facets.state}`, stateUrl(r.facets.state)]);
  if (r.facets.city) links.push([`All care in ${r.facets.city}`, cityUrl(r.facets.city)]);
  const addr = f.Address ? { "@type": "PostalAddress", streetAddress: f.Address.split(",")[0], addressLocality: f.City, addressRegion: f.State, postalCode: f.ZIP, addressCountry: "US" }
    : (f.City ? { "@type": "PostalAddress", addressLocality: f.City, addressRegion: f.State, postalCode: f.ZIP, addressCountry: "US" } : undefined);
  const ld = { "@context": "https://schema.org", "@type": r.facets.type === "Community health center" ? "MedicalClinic" : "MedicalBusiness",
    name: r.name, url: BASE + itemUrl(r), address: addr, telephone: f.Phone, sameAs: f["Official website"] || undefined,
    additionalProperty: Object.entries(f).filter(([k, v]) => !/^https?:/.test(v)).map(([k, v]) => ({ "@type": "PropertyValue", name: k, value: v })),
    subjectOf: { "@type": "WebPage", url: r.source_url, name: src.name } };
  const loc = [r.facets.city, r.facets.state && !r.facets.city ? r.facets.state : null].filter(Boolean).join("");
  const title = `${r.name}${loc ? ` – ${r.facets.type}, ${loc}` : ` – ${r.facets.type}`}`;
  const facts = [f["CMS overall star rating"] && `CMS overall rating ${f["CMS overall star rating"]}`, f["Certified beds"] && `${f["Certified beds"]} certified beds`, f.Phone && `phone ${f.Phone}`].filter(Boolean).join(", ");
  const crumbs = [[plural(r.facets.type), typeUrl(r.facets.type)]];
  if (r.facets.state) crumbs.push([r.facets.state, stateTypeUrl(r.facets.state, r.facets.type)]);
  crumbs.push([r.name, itemUrl(r)]);
  return page({ url: itemUrl(r), title, crumbs, jsonld: ld,
    description: `${r.name} is a ${r.facets.type.toLowerCase()}${loc ? ` in ${loc}` : ""}.${facts ? ` ${facts}.` : ""} Source and retrieval date included.`,
    h1: r.name,
    body: `<p>${esc(r.facets.type)}${loc ? ` in ${esc(loc)}` : ""}.${facts ? ` ${esc(facts[0].toUpperCase() + facts.slice(1))}.` : ""}</p>
<dl>${dl}</dl>
<p class="src">Source: <a href="${esc(r.source_url)}" rel="nofollow" data-outbound>${esc(src.name || r.source_url)}</a> (retrieved ${esc(r.updated_at.slice(0, 10))}). Source note: ${esc(r.summary)}</p>
${r.facets.type === "Nursing home" ? `<p class="src">CMS star ratings run from 1 (much below average) to 5 (much above average). Fines and payment denials cover the trailing three years in the CMS Penalties file.</p>` : ""}
<h2>Related lists</h2><p class="chips">${links.map(([l, u]) => `<a href="${esc(u)}">${esc(l)}</a>`).join("")}</p>
${peers.length ? `<h2>Other care in ${esc(r.facets.city)}</h2><div class="cards">${peers.map(card).join("")}</div>` : ""}
${sameType.length ? `<h2>More ${esc(plural(r.facets.type).toLowerCase())} in ${esc(r.facets.state)}</h2><div class="cards">${sameType.map(card).join("")}</div>` : ""}` });
}

function about() {
  const d = data();
  const counts = {};
  for (const r of d.rows) counts[r.source_id] = (counts[r.source_id] || 0) + 1;
  return page({ url: "/about/", title: `Sources & updates | ${site.name}`, description: `Sources, licenses and update dates for ${site.name}.`, h1: "Sources & updates", crumbs: [["Sources & updates", "/about/"]],
    body: `<p>${esc(site.description)}</p><h2>Sources</h2><ul>${site.sources.map((s) => `<li><a href="${esc(s.url)}" rel="nofollow">${esc(s.name)}</a>: ${(counts[s.id] || 0).toLocaleString("en-US")} entries. ${esc(s.note || "")}</li>`).join("")}</ul>
<h2>Updates</h2><p>${d.rows.length.toLocaleString("en-US")} entries. Latest source retrieval ${esc(d.updated.slice(0, 10))}. Ingestion status is machine-readable at <a href="/api/collection-status">/api/collection-status</a>; all rows at <a href="/api/items?limit=500">/api/items</a>.</p>
<h2>Corrections & removal requests</h2><p>For errors or removal requests, use the <a href="${esc(site.operator_url)}#contact">operator contact form</a> and include the page URL.</p>` });
}

function notFound() {
  return page({ url: "/404/", title: `Not found | ${site.name}`, description: "Page not found.", h1: "Page not found",
    noindex: true, body: `<p>The page may have moved. Browse by <a href="/">type of care or state</a>.</p>` });
}

// ---------- machine-readable ----------
function sitemapIndex() {
  const d = data();
  const n = Math.ceil(d.rows.length / SITEMAP_CHUNK);
  const maps = ["/sitemaps/lists.xml", ...Array.from({ length: n }, (_, i) => `/sitemaps/items-${i + 1}.xml`)];
  return `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${maps.map((m) => `<sitemap><loc>${BASE}${m}</loc></sitemap>`).join("")}</sitemapindex>\n`;
}
const urlset = (urls, lastmod) => `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls.map((u) => `<url><loc>${BASE}${u}</loc>${lastmod ? `<lastmod>${lastmod}</lastmod>` : ""}</url>`).join("")}</urlset>\n`;
function listUrls() {
  const d = data();
  return ["/", "/about/", ...sortedKeys(d.types).map(typeUrl), ...sortedKeys(d.states).map(stateUrl),
    ...[...d.stateType.keys()].sort().map((k) => stateTypeUrl(...k.split("|"))), ...sortedKeys(d.cities).map(cityUrl)];
}
function llms() {
  const d = data();
  return `# ${site.name}\n\n> ${site.description}\n\n${d.rows.length} entries across ${d.states.size} states and territories. Sources: ${site.sources.map((s) => s.name).join("; ")}. Operated by ${site.operator}.\n\n## Pages\n- [All facilities](${BASE}/)\n${byCount(d.types).map(([t, rs]) => `- [${plural(t)}](${BASE}${typeUrl(t)}): ${rs.length}`).join("\n")}\n- By state: ${BASE}/state/<state>/ and ${BASE}/state/<state>/<type>/ (for example ${BASE}/state/ohio/nursing-homes/)\n- By city: ${BASE}/city/<city>-<st>/ (for example ${BASE}/city/columbus-oh/)\n- Facility detail: ${BASE}/items/<slug>/\n- [Sources & updates](${BASE}/about/)\n\n## API\n- ${BASE}/api/items?limit=100&offset=0 (filters: type, state, city, q)\n- ${BASE}/api/collection-status\n- Sitemap: ${BASE}/sitemap.xml\n`;
}
function robots() {
  return `# Training crawls are disallowed; search (including AI search) is allowed.\nUser-agent: GPTBot\nDisallow: /\n\nUser-agent: CCBot\nDisallow: /\n\nUser-agent: ClaudeBot\nDisallow: /\n\nUser-agent: Google-Extended\nDisallow: /\n\nUser-agent: *\nAllow: /\n\nSitemap: ${BASE}/sitemap.xml\n`;
}
function apiItems(qs) {
  const d = data();
  let sel = d.rows;
  for (const k of ["type", "state", "city"]) { const v = qs.get(k); if (v) sel = sel.filter((r) => r.facets[k] === v); }
  const q = qs.get("q"); if (q) sel = sel.filter((r) => JSON.stringify(r).includes(q));
  const limit = Math.min(parseInt(qs.get("limit") || "100", 10) || 100, 500);
  const offset = parseInt(qs.get("offset") || "0", 10) || 0;
  return { total: sel.length, items: sel.slice(offset, offset + limit) };
}
function collectionStatus() {
  const d = data();
  const per = {};
  for (const r of d.rows) { const p = (per[r.source_id] ||= { id: r.source_id, count: 0, latest: "" }); p.count++; if (r.updated_at > p.latest) p.latest = r.updated_at; }
  return { site: site.host, total: d.rows.length, updated_at: d.updated, sources: site.sources.map((s) => ({ id: s.id, name: s.name, url: s.url, count: (per[s.id] || {}).count || 0, latest: (per[s.id] || {}).latest || null })) };
}

// ---------- routing ----------
function route(pathname, qs) {
  const d = data();
  const html = (b) => b && ["text/html; charset=utf-8", b];
  const json = (o) => ["application/json; charset=utf-8", JSON.stringify(o)];
  const xml = (s) => ["application/xml; charset=utf-8", s];
  const txt = (s) => ["text/plain; charset=utf-8", s];
  if (pathname === "/robots.txt") return txt(robots());
  if (pathname === "/llms.txt") return txt(llms());
  if (pathname === "/sitemap.xml") return xml(sitemapIndex());
  if (pathname === "/sitemaps/lists.xml") return xml(urlset(listUrls(), d.updated.slice(0, 10)));
  let m = pathname.match(/^\/sitemaps\/items-(\d+)\.xml$/);
  if (m) { const i = +m[1] - 1; const part = d.rows.slice(i * SITEMAP_CHUNK, (i + 1) * SITEMAP_CHUNK); return part.length ? xml(urlset(part.map(itemUrl))) : null; }
  if (pathname === "/api/items") return json(apiItems(qs));
  if (pathname === "/api/collection-status") return json(collectionStatus());
  if (pathname === "/healthz") return json({ ok: true, total: d.rows.length });
  if (site.indexnow_key && pathname === `/${site.indexnow_key}.txt`) return txt(site.indexnow_key);
  if (pathname === "/about/") return html(about());
  m = pathname.match(/^\/items\/([a-z0-9-]+)\/$/);
  if (m) { const r = d.bySlug.get(m[1]); return r ? html(itemPage(r)) : null; }
  // list pages with optional /page/N/
  m = pathname.match(/^(.*\/)page\/(\d+)\/$/);
  const base = m ? m[1] : pathname;
  const pageNo = m ? parseInt(m[2], 10) : 1;
  if (m && pageNo < 2) return null;
  if (base === "/") return html(home(pageNo));
  m = base.match(/^\/type\/([a-z0-9-]+)\/$/);
  if (m && d.typeSlug.has(m[1])) {
    const t = d.typeSlug.get(m[1]); const rows = d.types.get(t);
    const intro = `<p>${rows.length.toLocaleString("en-US")} ${esc(plural(t).toLowerCase())} in the US, from public directories. Pick a state:</p>${chips(sortedKeys(d.states).filter((s) => d.stateType.has(`${s}|${t}`)).map((s) => [s, stateTypeUrl(s, t), d.stateType.get(`${s}|${t}`).length]))}<h2>All ${esc(plural(t).toLowerCase())}</h2>`;
    return html(listPage({ base, rows: sortRows([...rows].sort((a, b) => (a.facets.state || "~").localeCompare(b.facets.state || "~"))), pageNo, title: `${plural(t)} in the US by state | ${site.name}`, h1: `${plural(t)} in the US`, intro,
      description: `${rows.length} ${plural(t).toLowerCase()} across the US with address, phone and source${t === "Nursing home" ? ", CMS star ratings, staffing, beds and penalties" : ""}.`, crumbs: [[plural(t), base]] }));
  }
  m = base.match(/^\/state\/([a-z0-9-]+)\/(?:([a-z0-9-]+)\/)?$/);
  if (m && d.stateSlug.has(m[1])) {
    const s = d.stateSlug.get(m[1]);
    if (m[2]) {
      const t = d.typeSlug.get(m[2]); const rows = t && d.stateType.get(`${s}|${t}`);
      if (!rows) return null;
      const cities = byCount(new Map([...d.cities].filter(([c, rs]) => rs.some((r) => r.facets.state === s && r.facets.type === t)).map(([c, rs]) => [c, rs.filter((r) => r.facets.type === t)])));
      const rated = rows.filter((r) => /^\d/.test(r.fields["CMS overall star rating"] || ""));
      const avg = rated.length ? (rated.reduce((a, r) => a + parseInt(r.fields["CMS overall star rating"], 10), 0) / rated.length).toFixed(1) : null;
      const intro = `<p>${rows.length} ${esc(plural(t).toLowerCase())} in ${esc(s)}${avg ? `; ${rated.length} carry CMS star ratings, averaging ${avg} of 5` : ""}.</p><h2>By city</h2>${chips(cities.slice(0, 60).map(([c, rs]) => [c, cityUrl(c), rs.length]))}<h2>All ${esc(plural(t).toLowerCase())} in ${esc(s)}</h2>`;
      return html(listPage({ base, rows: sortRows(rows), pageNo, title: `${plural(t)} in ${s} (${rows.length}) | ${site.name}`, h1: `${plural(t)} in ${s}`, intro,
        description: `${rows.length} ${plural(t).toLowerCase()} in ${s}${avg ? ` with CMS star ratings (average ${avg}), staffing, beds and penalties` : ""}, plus address and phone.`, crumbs: [[s, stateUrl(s)], [plural(t), base]] }));
    }
    const rows = d.states.get(s);
    const types = byCount(new Map([...d.types.keys()].filter((t) => d.stateType.has(`${s}|${t}`)).map((t) => [t, d.stateType.get(`${s}|${t}`)])));
    const cities = byCount(new Map([...d.cities].filter(([c, rs]) => rs[0].facets.state === s)));
    const intro = `<p>${rows.length.toLocaleString("en-US")} care facilities and providers in ${esc(s)}.</p><h2>By type of care</h2>${chips(types.map(([t, rs]) => [plural(t), stateTypeUrl(s, t), rs.length]))}<h2>By city</h2>${chips(cities.slice(0, 80).map(([c, rs]) => [c, cityUrl(c), rs.length]))}<h2>All in ${esc(s)}</h2>`;
    return html(listPage({ base, rows: sortRows(rows), pageNo, title: `Nursing homes, home health and hospice in ${s} | ${site.name}`, h1: `Senior care in ${s}`, intro,
      description: `${rows.length} nursing homes, home health agencies, hospices and other care providers in ${s}, with ratings, beds, address and phone where published.`, crumbs: [[s, base]] }));
  }
  m = base.match(/^\/city\/([a-z0-9-]+)\/$/);
  if (m && d.citySlug.has(m[1])) {
    const c = d.citySlug.get(m[1]); const rows = d.cities.get(c); const s = rows[0].facets.state;
    const types = byCount(new Map([...new Set(rows.map((r) => r.facets.type))].map((t) => [t, rows.filter((r) => r.facets.type === t)])));
    const intro = `<p>${rows.length} care ${rows.length === 1 ? "provider" : "providers"} in ${esc(c)}: ${types.map(([t, rs]) => `${rs.length} ${esc((rs.length === 1 ? t : plural(t)).toLowerCase())}`).join(", ")}.</p><p class="chips"><a href="${stateUrl(s)}">All care in ${esc(s)}</a>${types.map(([t]) => `<a href="${stateTypeUrl(s, t)}">${esc(plural(t))} in ${esc(s)}</a>`).join("")}</p>`;
    return html(listPage({ base, rows: sortRows(rows), pageNo, title: `Nursing homes and senior care in ${c} | ${site.name}`, h1: `Senior care in ${c}`, intro,
      description: `${rows.length} care providers in ${c}: ${types.map(([t, rs]) => `${rs.length} ${(rs.length === 1 ? t : plural(t)).toLowerCase()}`).join(", ")}. Address, phone and ratings where published.`, crumbs: [[s, stateUrl(s)], [c, base]] }));
  }
  return null;
}

if (require.main === module) {
  http.createServer((req, res) => {
    const u = new URL(req.url, "http://x");
    let out = null;
    try { out = route(u.pathname, u.searchParams); } catch (e) { res.writeHead(500); res.end(String(e && e.stack)); return; }
    if (!out) { res.writeHead(404, { "content-type": "text/html; charset=utf-8" }); res.end(notFound()); return; }
    res.writeHead(200, { "content-type": out[0], "cache-control": "public, max-age=300" });
    res.end(req.method === "HEAD" ? undefined : out[1]);
  }).listen(PORT, () => console.log(`listening on ${PORT}`));
}
module.exports = { route, data, slugify };
