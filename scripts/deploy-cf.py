#!/usr/bin/env python3
"""ポータル 1 本を新しい Cloudflare の口座の Worker（Workers Static Assets 形）に置く。LLM 無し。

    deploy-cf.py <repo名> <host> [--zone-id <新口座の zone>] [--if-changed] [--dry-run]

2026-10-03 に焼き込み形（全ページを 1 本の Worker に gzip+base64 で埋め込む）から移行した。
なぜ: 焼き込み形は Worker 1 本 10MB の上限があり、収録 5,982 行の面で 214% に達して配信を凍結した（#33）。
assets 形はページを asset として置き、/api/items だけ Worker が行の分割 asset（/__rows/all/<k>.json・/__rows/state/<slug>.json）から組み立てる。
US Senior Care Facility Finder 用（2026-10-06）: 26,658 行は 1 本の __rows.json だと 25MB で asset 1 件の上限（25MiB）と
isolate のメモリ（128MB）に近いので、1,000 行ごとと州ごとに分けた。元は chuukosha-satei-portal の deploy-cf.py。
上限が無く、ページは assets 層が直接返すので冷起動も掛からない。
実証は scripts/cf-migrate/assets-probe.py（14 ページ byte 一致）と assets-deploy.py（6,054 asset）。

手順: GitHub の main を運用機に取る → 手元でサイト（node server.js）を一時的に起動 → サイトマップと決まった口
（robots・llms・/api/collection-status・IndexNow の鍵）を全部取る → /api/items の行全体を __rows.json に →
Workers Scripts Write の一時鍵（expires_on 付き・grantry 経由で発行し台帳に自動記録）を発行 →
assets-upload-session（body は {"manifest": …} で包む。裸だと jwt が短縮系になり upload が常時 401）→
asset 本体を /workers/assets/upload?base64=true に curl 直打ち（grantry は生 body を送れない）→
script PUT は multipart で metadata.assets.jwt に completion token → 住所を Worker の独自ドメインに付ける。
--if-changed は、前回置いたコミットと main が同じなら何もしない（収集の担当の gate.sh の先頭で毎回呼ぶため）。

なぜ（2026-09-25 澤居「今後ポータル、メディア工場は新しいcloudflareのワークスペースで作ってね」「contabo通してない？」）:
Contabo の 1 台に数百サイトを集めると、その 1 台が落ちたら全部止まる。出す所は Cloudflare、作る所は運用機（Hetzner）。
"""
import argparse
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.environ.get("GRANTRY_MCP_DIR", "/workspace/agent-loop"))
from grantry_mcp import Grantry  # noqa: E402



ACCOUNT = "abd7230583dfbed9ea0e9d606cac0db3"   # 新しい Cloudflare の口座（ポータル用）
SCOPE = "portal-cloudflare"
WORK = pathlib.Path(os.environ.get("CF_DEPLOY_WORK", "/workspace/cf-deploy"))
NOT_FOUND = "__404__"          # 手元の 404 の中身の一時的な住所（asset には /404.html で置く）
WORKERS_SCRIPTS_WRITE = "e086da7e2179491d91ee5f35b3ca210a"   # permission group（2026-10-03 実測）

# ページ送りの実パスを HTML から拾う（#81）。先頭の 1 段がない /page/N/（トップ）も拾うこと:
# 2026-10-04 の本番実測で /page/2/ だけ asset が無く 200 の「見つかりません」になっていた（トップの pager が指す）。
PAGER_HREF = re.compile(r'href="((?:/[^"#]*?)?/page/\d+/)"')

# 一致する asset があれば Workers Static Assets は Worker を起動せず assets 層が直接返す。照合にクエリは入らないので
# 旧 ?page=N は 1 枚目の 200 のままになり、Worker の 301 規則が届かない（#81・2026-10-04 本番実測: /zzz-not-real/?page=2
# は 301 なのに /prefecture/東京都/?page=2 だけ 200）。先に Worker を通す口を facet 面へ足せば 301 が緑になる。
# glob（/prefecture/*）は本番で効かなかった（2026-10-05 実測: metadata は PUT されているのに ?page=2 が 200 のまま。
# 2026-10-04 の本番配信でも /prefecture/* を入れて 200 のままだった）ので、一致パスで列挙する。facet 面は 62 面（2026-10-04
# の sitemap・都道府県 48・ブランド 14）で手で数えられる。 facet 面は Worker の ASSETS.fetch（assets.internal）へ落ちて返すので中身は
# 変わらない。速度ゲートが見る / ・ /about/ ・ /hub/ は入れない（Worker を挟むと server 時間が付く）。
def worker_first_routes(paths):
    """先に Worker を通す口。ページ送りは実パス（/page/N/）なので facet 面を Worker に通す必要は無い。
    / と /about/ は Server-Timing を付けるために Worker を通す（速度ゲート）。"""
    return ["/", "/about/", "/api/items"]

WORKER = """const HOST=__HOST__;
const OLD=__OLD__;
const STATUS=__STATUS__;
const REL=__REL__;
const TOTAL=__TOTAL__;
const CHUNK=__CHUNK__;
const NCHUNK=__NCHUNK__;
function relKey(req){const k=new URL(req.url);k.searchParams.set('__rel',REL);return new Request(k.toString(),{method:'GET'});}
const slug=(s)=>String(s).toLowerCase().normalize('NFKD').replace(/[\\u0300-\\u036f]/g,'').replace(/&/g,' and ').replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'');
const SHARD=new Map();
async function shard(env,p){
 if(SHARD.has(p))return SHARD.get(p);
 const r=await env.ASSETS.fetch(new Request('https://assets.internal'+p));
 const v=r.status===200?JSON.parse(await r.text()):null;
 if(p==='/__rows/cityidx.json'||p.startsWith('/__rows/state/')){SHARD.set(p,v);if(SHARD.size>12)SHARD.delete(SHARD.keys().next().value);}
 return v;
}
const AMEMO=new Map();
async function apiItems(env,req,u,ctx){
 const rk=relKey(req).url;
 const hdr={'cache-control':'public, max-age=0, must-revalidate','content-type':'application/json; charset=utf-8'};
 const memo=AMEMO.get(rk);
 if(memo!==undefined)return new Response(req.method==='HEAD'?null:memo,{status:200,headers:hdr});
 const c=(typeof caches!=='undefined'&&caches.default)?caches.default:null;
 if(c){const hit=await c.match(relKey(req));if(hit){const t=await hit.text();AMEMO.set(rk,t);if(AMEMO.size>100)AMEMO.delete(AMEMO.keys().next().value);return new Response(req.method==='HEAD'?null:t,{status:200,headers:hdr});}}
 const q=u.searchParams;
 const limit=Math.min(parseInt(q.get('limit')||'100',10)||100,500);
 const offset=Math.max(0,parseInt(q.get('offset')||'0',10)||0);
 const fType=q.get('type'),fState=q.get('state'),fCity=q.get('city'),fQ=q.get('q');
 let total=0,items=[];
 if(!fType&&!fState&&!fCity&&!fQ){
  total=TOTAL;
  for(let k=Math.floor(offset/CHUNK);k<NCHUNK&&k*CHUNK<offset+limit;k++){
   const rows=await shard(env,'/__rows/all/'+k+'.json')||[];
   for(let i=0;i<rows.length;i++){const n=k*CHUNK+i;if(n>=offset&&n<offset+limit)items.push(rows[i]);}
  }
 }else{
  const ok=(it)=>{const f=it.facets||{};return (!fType||f.type===fType)&&(!fState||f.state===fState)&&(!fCity||f.city===fCity)&&(!fQ||JSON.stringify(it).includes(fQ));};
  let st=fState;
  if(!st&&fCity){const idx=await shard(env,'/__rows/cityidx.json')||{};st=idx[fCity];if(!st)st='__none__';}
  const take=(rows)=>{for(const it of rows){if(ok(it)){if(total>=offset&&items.length<limit)items.push(it);total++;}}};
  if(st){take(await shard(env,'/__rows/state/'+slug(st)+'.json')||[]);}
  else{for(let k=0;k<NCHUNK;k++)take(await shard(env,'/__rows/all/'+k+'.json')||[]);}
 }
 const body=JSON.stringify({total,items});
 AMEMO.set(rk,body);if(AMEMO.size>100)AMEMO.delete(AMEMO.keys().next().value);
 const res=new Response(req.method==='HEAD'?null:body,{status:200,headers:{'cache-control':'public, max-age=300','content-type':'application/json; charset=utf-8'}});
 if(c&&req.method==='GET'&&ctx&&ctx.waitUntil)ctx.waitUntil(c.put(relKey(req),res.clone()));
 res.headers.set('cache-control','public, max-age=0, must-revalidate');
 return res;
}
export default{async fetch(req,env,ctx){
 const t0=Date.now();
 const st=(r)=>{try{r.headers.set('server-timing','server;dur='+Math.max(0,Date.now()-t0))}catch(e){}return r;};
 const u=new URL(req.url);
 if(OLD.includes(u.hostname))return Response.redirect('https://'+HOST+u.pathname+u.search,301);
 if(u.pathname.startsWith('/__rows/'))return st(new Response('Not found',{status:404}));
 const pg=u.searchParams.get('page');
 if(pg&&/^[0-9]+$/.test(pg)&&pg!=='1'&&!u.pathname.startsWith('/api/')){const b=u.pathname.endsWith('/')?u.pathname:u.pathname+'/';return Response.redirect('https://'+HOST+b+'page/'+pg+'/',301);}
 if(u.pathname==='/api/items')return st(await apiItems(env,req,u,ctx));
 if(u.pathname==='/api/collection-status')return st(new Response(req.method==='HEAD'?null:STATUS,{status:200,headers:{'cache-control':'public, max-age=300','content-type':'application/json; charset=utf-8'}}));
 const a=await env.ASSETS.fetch(new Request('https://assets.internal'+u.pathname+u.search));
 if(a.status===404){const nf=await env.ASSETS.fetch(new Request('https://assets.internal/404.html'));return st(new Response(nf.body,{status:404,headers:nf.headers}));}
 return st(new Response(a.body,{status:a.status,headers:a.headers}));
}};
"""


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def fetch(base, path):
    req = urllib.request.Request(base + path, headers={"User-Agent": "cf-deploy", "Accept-Encoding": "identity"})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, r.headers.get("content-type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type"), e.read()


PAGE_QUEUE_MAX = 60000  # v4  焼き込む口の上限（ページ送りの暴走止め。Worker 10MB の上限は大きさの検査が別に見る）


def sitemap_paths(base, host, path="/sitemap.xml", seen=None):
    seen = seen if seen is not None else set()
    if path in seen:
        return []
    seen.add(path)
    s, _ct, b = fetch(base, path)
    if s != 200:
        return []
    import re
    out = []
    for loc in re.findall(r"<loc>([^<]+)</loc>", b.decode("utf-8", "replace")):
        p = urllib.parse.urlparse(loc.strip()).path or "/"
        if p.endswith(".xml"):
            # 子の sitemap 自体も asset に置く（置かないと本番の /sitemaps/*.xml が 404 になる・2026-10-06 実測）
            out.append(p)
            out += sitemap_paths(base, host, p, seen)
        else:
            out.append(p)
    return out


def call(g, tool, args):
    r = g.call(tool, args)
    if isinstance(r, dict) and "content" in r:
        t = r["content"][0]["text"]
        try:
            return json.loads(t)
        except Exception:
            return {"text": t}
    return r


def curl(args, timeout=300):
    return subprocess.run(["curl", "-sS", "--max-time", str(timeout), *args],
                          capture_output=True, text=True)


def issue_deploy_token():
    """Workers Scripts Write の一時鍵（expires_on 付き）を Grantry 経由で発行する。
    発行は grantry_mcp.record_cf_token_created が state/cf-tokens-created.jsonl に自動記録する。
    裸の policy は「effect must be present」「permissions or permission_groups が必要」で 400 になる（2026-10-03 実測）。"""
    g = Grantry()
    exp = time.strftime("%Y-%m-%dT%H:00:00Z", time.gmtime(time.time() + 8 * 3600))
    res = call(g, "cloudflare_request", {
        "scope": SCOPE, "method": "POST", "path": f"/accounts/{ACCOUNT}/tokens",
        "body": {"name": "portal-assets-deploy", "expires_on": exp,
                 "policies": [{"effect": "allow",
                               "permission_groups": [{"id": WORKERS_SCRIPTS_WRITE}],
                               "resources": {f"com.cloudflare.api.account.{ACCOUNT}": "*"}}]}})
    val = (res.get("body") or {}).get("result", {}).get("value") if isinstance(res, dict) else None
    if not val:
        sys.exit(f"一時鍵を発行できない: {json.dumps(res, ensure_ascii=False)[:400]}")
    return val


def size_report(size, pages, head, assets_bytes=0):
    """大きさを残す（焼き込み形の 10MB 上限の点検 cf-deploy:size が読む形を保つ）。
    assets 形は script を 10MB の上限に載せない（ページは asset・API は __rows.json から組み立て）。"""
    return {"size": size, "max": 9_500_000, "ratio": round(size / 9_500_000, 6), "pages": pages,
            "assets": assets_bytes, "form": "assets", "commit": head[:12],
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "warn": None}


ROW_CHUNK = 1000


def slugify(v):
    import unicodedata
    v = unicodedata.normalize("NFKD", str(v).lower())
    v = "".join(ch for ch in v if not unicodedata.combining(ch)).replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", v).strip("-")


def build_shards(rows):
    """/api/items の行を分割 asset にする。1,000 行ごと（素の取り出し・type や q の絞り込み）と州ごと（state・city の絞り込み）。"""
    enc = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":")).encode()
    out = {}
    for k in range(0, len(rows), ROW_CHUNK):
        out[f"/__rows/all/{k // ROW_CHUNK}.json"] = enc(rows[k:k + ROW_CHUNK])
    by_state, cityidx = {}, {}
    for r in rows:
        f = r.get("facets") or {}
        if f.get("state"):
            by_state.setdefault(f["state"], []).append(r)
            if f.get("city"):
                cityidx[f["city"]] = f["state"]
    for st, rs in by_state.items():
        out[f"/__rows/state/{slugify(st)}.json"] = enc(rs)
    out["/__rows/cityidx.json"] = enc(cityidx)
    big = max(len(b) for b in out.values())
    if big > 20_000_000:
        sys.exit(f"分割 asset が大きすぎる（{big:,} バイト）")
    return out


def worker_selftest(js, shards, pages, rows):
    harness = r"""
const fs = await import("node:fs/promises");
const dir = process.env.SHARD_DIR;
const mod = await import(process.env.WORKER_PATH);
const env = { ASSETS: { fetch: async (r) => {
  const u = new URL(r.url);
  try { const b = await fs.readFile(dir + "/" + encodeURIComponent(u.pathname)); return new Response(b, { status: 200, headers: { "content-type": "application/json" } }); }
  catch { return new Response("nf", { status: 404 }); }
} } };
globalThis.caches = { default: { match: async () => null, put: async () => {} } };
const ctx = { waitUntil: (p) => p };
const out = {};
for (const p of JSON.parse(process.env.PROBES)) {
  const res = await mod.default.fetch(new Request("https://example.test" + p), env, ctx);
  const t = await res.text();
  let j = null; try { j = JSON.parse(t); } catch {}
  out[p] = { status: res.status, total: j && j.total, first: j && j.items && j.items[0] && j.items[0].slug, n: j && j.items && j.items.length, st: res.headers.get("server-timing") };
}
process.stdout.write(JSON.stringify(out));
"""
    f0 = rows[0]["facets"]
    big_state = max({r["facets"].get("state") for r in rows if r["facets"].get("state")}, key=lambda s: sum(1 for r in rows if r["facets"].get("state") == s))
    city = next(r["facets"]["city"] for r in rows if r["facets"].get("city"))
    q = urllib.parse.quote
    probes = ["/api/items", "/api/items?limit=500&offset=1500", f"/api/items?limit=500&offset={len(rows) - 10}",
              f"/api/items?state={q(big_state)}&limit=500&offset=500", f"/api/items?city={q(city)}",
              f"/api/items?type={q(f0['type'])}&limit=50&offset=100", "/api/items?q=Hospice&limit=5", "/api/collection-status"]
    with tempfile.TemporaryDirectory() as td:
        for sp, sb in shards.items():
            (pathlib.Path(td) / urllib.parse.quote(sp, safe="")).write_bytes(sb)
        wp = pathlib.Path(td) / "worker.mjs"
        wp.write_text(js)
        r = subprocess.run(["node", "--input-type=module", "-e", harness], capture_output=True, text=True, timeout=180,
                           env={**os.environ, "WORKER_PATH": str(wp), "SHARD_DIR": td, "PROBES": json.dumps(probes)})
    if r.returncode != 0:
        print(r.stderr[-1500:])
        return False
    got = json.loads(r.stdout)
    ok = True
    for p in probes:
        g = got[p]
        if p == "/api/collection-status":
            good = g["status"] == 200 and g["total"] == len(rows)
        else:
            exp = json.loads(pages_api(p, rows))
            good = g["status"] == 200 and g["total"] == exp["total"] and g["first"] == (exp["items"][0]["slug"] if exp["items"] else None) and g["n"] == len(exp["items"])
        good = good and "server;dur=" in (g["st"] or "")
        print(("ok   " if good else "FAIL ") + p + ("" if good else f" got={g}"))
        ok = ok and good
    return ok


def pages_api(p, rows):
    """server.js の apiItems と同じ絞り込み（自検の正解）。"""
    u = urllib.parse.urlparse(p)
    qs = dict(urllib.parse.parse_qsl(u.query))
    sel = rows
    for k in ("type", "state", "city"):
        if qs.get(k):
            sel = [r for r in sel if r["facets"].get(k) == qs[k]]
    if qs.get("q"):
        sel = [r for r in sel if qs["q"] in json.dumps(r, ensure_ascii=False, separators=(",", ":"))]
    limit = min(int(qs.get("limit") or 100) or 100, 500)
    off = int(qs.get("offset") or 0)
    return json.dumps({"total": len(sel), "items": sel[off:off + limit]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("host")
    ap.add_argument("--zone-id", required=True)
    ap.add_argument("--old-host", action="append", default=[])
    ap.add_argument("--if-changed", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    d = WORK / a.repo
    WORK.mkdir(parents=True, exist_ok=True)
    tok_file = pathlib.Path(os.environ.get("GH_APP_TOKENS", "/workspace/gh-app-tokens")) / "product-agentic-group.token"
    tok = tok_file.read_text().strip() if tok_file.exists() else os.environ.get("GH_TOKEN", "")
    url = f"https://x-access-token:{tok}@github.com/agentic-group/{a.repo}.git"
    if (d / ".git").exists():
        r = sh(["git", "-C", str(d), "fetch", "-q", url, "main"])
        if r.returncode:
            sys.exit(f"fetch に失敗: {r.stderr[-300:].replace(tok, '***')}")
        sh(["git", "-C", str(d), "reset", "-q", "--hard", "FETCH_HEAD"])
    else:
        r = sh(["git", "clone", "-q", "--depth", "1", url, str(d)])
        if r.returncode:
            sys.exit(f"clone に失敗: {r.stderr[-300:].replace(tok, '***')}")
        sh(["git", "-C", str(d), "remote", "set-url", "origin", f"https://github.com/agentic-group/{a.repo}.git"])
    # 走らせた写しが古いと、焼き込む口の追加などの直しが乗らない（2026-09-25 実測: 古い写しで走って
    # /api/collection-status 無しの Worker を本番に置いた）。取ってきた main 側の自分で走り直す
    mine = pathlib.Path(__file__).resolve()
    fetched = (d / "scripts" / "deploy-cf.py").resolve()
    if os.environ.get("CF_DEPLOY_REEXEC") != "1" and mine != fetched and fetched.exists():
        r = subprocess.run([sys.executable, str(fetched), *sys.argv[1:]], env={**os.environ, "CF_DEPLOY_REEXEC": "1"})
        raise SystemExit(r.returncode)
    head = sh(["git", "-C", str(d), "rev-parse", "HEAD"]).stdout.strip()
    stamp = d.parent / f"{a.repo}.deployed"
    if a.if_changed and stamp.exists() and stamp.read_text().strip() == head:
        print(f"変わっていない（{head[:8]}）。置き直さない")
        return 0

    port = free_port()
    proc = subprocess.Popen(["node", "server.js"], cwd=d, env={**os.environ, "PORT": str(port)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                if fetch(base, "/robots.txt")[0] == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            sys.exit("手元でサイトが立ち上がらない（120 秒）")
        site = json.loads((d / "site.json").read_text())
        paths = sitemap_paths(base, a.host)
        # 必須の口（1 つでも取れなければ置かない。2026-09-25 実測: /api/collection-status をこの一覧に入れ忘れ、
        # 手元の server.js は通るのに本番だけ 404 が続いた。雛形の llms.txt はこの口を案内している）
        # /api/items・/api/collection-status はポータルの口。メディア（法人破産メディアなど）は持たないので server.js にある時だけ
        srv = (d / "server.js").read_text(errors="replace") if (d / "server.js").exists() else ""
        required = ["/sitemap.xml", "/robots.txt", "/llms.txt"] + [x for x in ("/api/items", "/api/collection-status") if x in srv]
        paths += required + ["/news-sitemap.xml", "/feed.xml", "/rss.xml", "/favicon.ico", "/favicon.svg",
                             "/BingSiteAuth.xml", "/news/", "/tags/"]
        if site.get("indexnow_key"):
            paths.append(f"/{site['indexnow_key']}.txt")
        pages, ng = {}, []
        import re
        queue = list(dict.fromkeys(paths))
        for p in queue:
            s, ct, b = fetch(base, p)
            if s != 200:
                ng.append((s, p))
                continue
            if p == "/healthz" and "json" in (ct or ""):
                # 配信元を本番で確かめられるように、Worker が返す healthz に runtime を足す（Contabo の healthz には無い）
                try:
                    hz = json.loads(b)
                    hz.update({"runtime": "cloudflare", "built_from": head[:12]})
                    b = json.dumps(hz, ensure_ascii=False).encode()
                except Exception:
                    pass
            if p == "/api/items" or p.startswith("/api/items?"):
                continue  # 動的な口。asset に置かない（Worker が rows から組み立てる）
            pages[urllib.parse.unquote(p)] = (ct, b)
            # ページの中から参照している自分の部品（画像・CSS・JS）も拾う。1 回だけ・同じ住所は取り直さない
            # ページ送りの 2 枚目以降（/page/N/）も拾う（#81・2026-10-04）。sitemap には 1 枚目しか無い
            if "html" in (ct or ""):
                # ページ送り（?page=N）も焼き込む。sitemap に載らないので、拾わないと 2 ページ目以降が
                # pathname だけの既定応答（1 ページ目の複製）になる（2026-10-06 shodankai 実測: /?page=2 がトップと同一）
                for ref in re.findall(r'href="(/[^"#]*\?page=\d+)"', b.decode("utf-8", "replace")):
                    # 一覧の住所は日本語のまま出ることがある（/event_kind/異業種交流会/?page=2）。取る前に符号化する
                    ref = urllib.parse.quote(ref.replace("&amp;", "&"), safe="/?=&%")
                    if ref not in queue and len(queue) < PAGE_QUEUE_MAX:
                        queue.append(ref)
                for ref in re.findall(r'(?:src|href)="(/[^"#?]+\.(?:png|jpe?g|webp|gif|svg|ico|css|js|woff2?))"', b.decode("utf-8", "replace")):
                    if ref not in queue:
                        queue.append(ref)
                for ref in PAGER_HREF.findall(b.decode("utf-8", "replace")):
                    q = urllib.parse.quote(ref, safe="/%")
                    if q not in queue:
                        queue.append(q)
        # /api/items の行全体を取り、__rows.json asset に置く（Worker が offset・facet・q を通して組み立てる）。
        # 取り切れないときは置かない（/api/items は total 0 の API になるので置き直さない）
        rows_json, facet_keys = None, []
        try:
            facet_keys = [f.get("key") for f in (site.get("facets") or []) if f.get("key")]
            s1, _c, b1 = fetch(base, "/api/items?limit=1")
            total = int((json.loads(b1) or {}).get("total") or 0) if s1 == 200 else 0
            rows, off = [], 0
            while off < total:
                s2, _c2, b2 = fetch(base, f"/api/items?limit=500&offset={off}")
                if s2 != 200:
                    break
                part = json.loads(b2).get("items") or []
                if not part:
                    break
                rows += part
                off += len(part)
            if not (total > 0 and len(rows) >= total):
                sys.exit(f"/api/items の行を取り切れず（total {total}・取得 {len(rows)}）。置かない")
            rows_json = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        except SystemExit:
            raise
        except Exception as e:
            sys.exit(f"/api/items の行を取れず置かない: {e}")
        s, ct, b = fetch(base, "/__cf_deploy_404__/")
        pages["/404.html"] = (ct, b)
    finally:
        proc.terminate()
    if ng:
        print("取れなかった口:", ng[:10])
    missing = [p for p in required if any(x[1] == p for x in ng) and p != "/api/items"]
    if missing:
        sys.exit(f"必須の口が取れない（これを置くと本番で 404 になる）: {missing}")

    import blake3
    def _entry(b, ct):
        e = {"hash": blake3.blake3(b).hexdigest()[:32], "size": len(b)}
        # cache-control を明示する。指定が無いと Cloudflare の既定（public, max-age=0, must-revalidate）で、
        # 要求ごとに再検証が走ってページが毎回 11〜18ms になる（2026-10-03 23:57 実測: / の server 10/13/11ms・/about/ 18/17/17ms）。
        if ct and any(k in ct for k in ("html", "json", "xml", "text")):
            e["httpMetadata"] = {"cacheControl": "public, max-age=300"}
        return e
    manifest = {p: _entry(b, ct) for p, (ct, b) in pages.items()}
    shards = build_shards(rows)
    for sp, sb in shards.items():
        manifest[sp] = _entry(sb, "application/json; charset=utf-8")
        pages[sp] = ("application/json; charset=utf-8", sb)
    # manifest の hash が全部既に上げ済みだと session が完了系 jwt（325 字・aud "ewc"）を返し、
    # その jwt では upload も script PUT も弾かれる（Unauthorized・10305）。必ず 1 件新規の sentinel を
    # 混ぜて 550 字の正系 jwt を引く（2026-10-03 実測・00:14 の全成功は全件新規だったから）
    sn = f"/__deploy-stamp-{time.strftime('%Y%m%dT%H%M%S')}.txt"
    sb = time.strftime("%Y-%m-%dT%H:%M:%S%z").encode()
    pages[sn] = ("text/plain; charset=utf-8", sb)
    manifest[sn] = {"hash": blake3.blake3(sb).hexdigest()[:32], "size": len(sb)}
    # run_worker_first の配列形は本番で効かない（2026-10-06 実測: metadata PUT は成功・glob は 400・zone の
    # Workers Route も custom domain+assets では効かず、/ も /api/collection-status も assets 直返し＝
    # Server-Timing 無しで speed:api が毎周誤赤）。asset を置かず asset-miss で Worker を通す
    # （STATUS を script に埋め込んで st 付き・isolate 内 CS キャッシュで返す）。
    status_json = ""
    if "/api/collection-status" in pages:
        status_json = pages["/api/collection-status"][1].decode("utf-8", "replace")
        del manifest["/api/collection-status"]
        del pages["/api/collection-status"]
    if "/api/collection-status" in manifest or "/api/collection-status" in pages:
        sys.exit("/api/collection-status が asset に残っている。run_worker_first は本番で効かないため asset-miss で Worker を通す")
    assets_bytes = sum(m["size"] for m in manifest.values())
    js = (WORKER.replace("__HOST__", json.dumps(a.host)).replace("__OLD__", json.dumps(a.old_host))
          .replace("__STATUS__", json.dumps(status_json)).replace("__REL__", json.dumps(head[:16]))
          .replace("__TOTAL__", str(len(rows))).replace("__CHUNK__", str(ROW_CHUNK))
          .replace("__NCHUNK__", str((len(rows) + ROW_CHUNK - 1) // ROW_CHUNK)))
    if not status_json or "__STATUS__" in js:
        sys.exit("/api/collection-status の列を script に埋め込めない")
    # 上げる前に生成した Worker を手元の node で動かし、/api/items の全形（素・offset・state・city・type・q）が
    # 手元の server.js と同じ件数・同じ先頭行を返すか確かめる。違えば配らない。
    if not worker_selftest(js, shards, pages, rows):
        sys.exit("生成した Worker が自検を通らない（配らない）")
    print("worker selftest ok")
    print(f"asset {len(manifest)} 件・合計 {assets_bytes:,} バイト・script {len(js):,} バイト・コミット {head[:8]}")
    rep = size_report(len(js.encode()), len(pages) - 1, head, assets_bytes)
    try:
        (d.parent / f"{a.repo}.size.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"大きさの記録を書けない: {e}")
    if a.dry_run:
        return 0

    out = d.parent / f"{a.repo}.build"
    out.mkdir(exist_ok=True)
    for p, (_ct, b) in pages.items():
        (out / manifest[p]["hash"]).write_text(__import__("base64").b64encode(b).decode())
    # session への body は {"manifest": …} で包む。裸の mapping を送ると jwt が短縮系（aud "ewc"・325 字）
    # になり upload が常時 401 になる（2026-10-02 実測・包むと 550 字の正系 jwt で 202）
    (out / "manifest-session.json").write_text(json.dumps({"manifest": manifest}))
    cf_tok = issue_deploy_token()
    t0 = time.time()
    shost = f"portal-{a.repo.removesuffix('-portal')}"  # 本番 script（probe 名は cleanup 後に 10000 認証誤・2026-10-03 実測）
    r = curl(["-X", "POST", f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/workers/scripts/{shost}/assets-upload-session",
              "-H", f"Authorization: Bearer {cf_tok}", "-H", "Content-Type: application/json",
              "--data-binary", f"@{out / 'manifest-session.json'}"], timeout=120)
    jwt = None
    try:
        js2 = json.loads(r.stdout)
        jwt = (js2.get("result") or {}).get("jwt")
        need = set((js2.get("result") or {}).get("hashes") or [])
    except Exception:
        js2 = {}
        need = set()
    if not js2.get("success") or not jwt:
        sys.exit(f"assets-upload-session に入らない: {r.stdout[:300]}")
    print(f"session jwt ok（upload {len(need) if need else len(manifest)} 件）({time.time() - t0:.1f}s)", flush=True)

    upurl = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/workers/assets/upload?base64=true"
    # 1 POST の合計 base64 を 3MB 未満に抑える（大きい塊で 401 が出た実測あり・待ち再試行で回復も確認）
    items = [(p, manifest[p]["hash"], len((out / manifest[p]["hash"]).read_text())) for p in manifest]
    if need:
        items = [it for it in items if it[1] in need]
    upn, nreq, i = 0, 0, 0
    while i < len(items):
        chunk, size = [], 0
        # 1 件目は上限超えでも必ず採る（rows.json asset の base64 4.1MB が単体で 3MB 上限を超え、空チャンクの 0 件 POST が再試行ループに落ちた・2026-10-03 実測）
        while i < len(items) and (not chunk or (len(chunk) < 80 and size + items[i][2] < 3_000_000)):
            chunk.append(items[i])
            size += items[i][2]
            i += 1
        fargs = []
        for _p, h, _n in chunk:
            fargs += ["-F", f"{h}=@{out / h};type=application/null"]
        ok = False
        for wait in (0, 10, 30, 60, 120, 240, 480):
            if wait:
                print(f"  upload {len(chunk)} 件が弾かれた。{wait}s 待って送り直す", flush=True)
                time.sleep(wait)
            elif nreq:
                time.sleep(2)  # 連打の直後は 401 で弾かれる実測（2026-10-01）。1 POST ごとに間隔を置く
            r = curl(["-X", "POST", upurl, "-H", f"Authorization: Bearer {jwt}", *fargs], timeout=600)
            nreq += 1
            try:
                j = json.loads(r.stdout)
                ok = j.get("success")
                jwt = (j.get("result") or {}).get("jwt") or jwt
            except Exception:
                ok = False
            if ok:
                break
        if not ok:
            sys.exit(f"assets/upload に入らない（{chunk[0][0]} ほか {len(chunk)} 件）: {r.stdout[:300]}")
        upn += len(chunk)
        if upn % 400 < 25 or upn == len(items):
            print(f"upload {upn}/{len(items)}・{time.time() - t0:.0f}s・POST {nreq}", flush=True)
    print(f"upload 完了 {upn} 件・{time.time() - t0:.1f}s・POST {nreq} 本", flush=True)

    (out / "worker.mjs").write_text(js)
    (out / "metadata.json").write_text(json.dumps({
        "main_module": "worker.mjs", "compatibility_date": "2026-09-01",
        "bindings": [{"type": "assets", "name": "ASSETS"}],
        # 先に Worker を通す口は worker_first_routes()（このファイルの上・sitemap の facet 面から導出）。
        # True（全部）は実 host での ASSETS.fetch が 1042 になる（2026-10-03 staging 実測）。
        "assets": {"jwt": jwt, "run_worker_first": worker_first_routes(paths)}}))
    g = Grantry()
    up = call(g, "grantry_create_upload_url", {"max_uses": 2, "ttl_seconds": 900})
    ids = {}
    for name, ctype in (("metadata.json", "application/json"), ("worker.mjs", "application/javascript+module")):
        r = sh(["curl", "-s", "-F", f"file=@{out / name};type={ctype}", up["upload_url"]])
        ids[name] = json.loads(r.stdout)["file_id"]
    script = f"portal-{a.repo.removesuffix('-portal')}"
    res = call(g, "cloudflare_request", {
        "scope": SCOPE, "method": "PUT", "path": f"/accounts/{ACCOUNT}/workers/scripts/{script}", "timeout_ms": 120000,
        "files": [{"field": "metadata", "file_id": ids["metadata.json"], "content_type": "application/json", "filename": "metadata.json"},
                  {"field": "worker.mjs", "file_id": ids["worker.mjs"], "content_type": "application/javascript+module", "filename": "worker.mjs"}]})
    if res.get("status") != 200:
        sys.exit(f"Worker を置けない: {json.dumps(res, ensure_ascii=False)[:400]}")
    # 住所を Worker に付ける。同じ名前の A/CNAME（Contabo 向け）が残っていると付かないので先に外す
    recs = call(g, "cloudflare_list_dns_records", {"scope": SCOPE, "zone_id": a.zone_id, "name": a.host})
    for r in recs.get("results", []):
        if r["type"] in ("A", "AAAA", "CNAME") and not (r.get("meta") or {}).get("origin_worker_id"):
            call(g, "cloudflare_request", {"scope": SCOPE, "method": "DELETE", "path": f"/zones/{a.zone_id}/dns_records/{r['id']}"})
            print(f"Contabo 向けの記録を外した: {r['type']} {r['name']} → {r['content']}")
    dom = call(g, "cloudflare_request", {"scope": SCOPE, "method": "PUT", "path": f"/accounts/{ACCOUNT}/workers/domains",
                                         "body": {"hostname": a.host, "service": script, "zone_id": a.zone_id, "environment": "production"}})
    if dom.get("status") != 200:
        sys.exit(f"住所を付けられない: {json.dumps(dom, ensure_ascii=False)[:400]}")
    # 置いた後に本番を読み直す（件数が手元と同じか）
    # 住所を Worker に付けた直後は、新しい名前がまだ引けないことがある（2026-09-25 実測: 同時に 6 本出したら
    # 5 本が「Name or service not known」で落ち、数分後の再実行では全部通った）。引けるまで最大 5 分待つ。
    s, b = None, b""
    for _i in range(60):
        time.sleep(5)
        try:
            s, _ct, b = fetch(f"https://{a.host}", "/api/items")
            # 置き換えの切り替え中は 500 を返すことがある（2026-09-25 zeirishi・gyoumu-itaku 実測: 直後の 1 回が 500、
            # 数秒後から 200）。5xx は切り替え待ちとして取り直す。
            if s < 500:
                break
            print(f"本番 {s}。切り替え中とみて待って取り直す")
        except urllib.error.URLError as e:
            print(f"まだ引けない（{e.reason}）。待って取り直す")
    if s is None:
        sys.exit(f"{a.host} が 5 分たっても引けない")
    live = json.loads(b).get("total") if s == 200 else None
    local = len(rows)
    print(f"本番 {s}・件数 本番 {live} / 手元 {local}")
    if live != local:
        sys.exit("本番の件数が手元と合わない（読み直しで失敗）")
    stamp.write_text(head)
    return 0


if __name__ == "__main__":
    sys.exit(main())