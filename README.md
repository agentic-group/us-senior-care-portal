# US Senior Care Facility Finder (senior-care.navi-index.com)

Nursing homes, home health agencies, hospices, palliative care programs, adult care homes, state veterans homes,
continuing care retirement communities and community health centers in every US state, with the values each
public source publishes (CMS star ratings, staffing, certified beds and 3-year penalties for nursing homes; services
for home health and hospice agencies; licensed beds for adult care homes), plus address and phone.

- Created 2026-10-06 by moving the care-facility rows out of the US Portal Atlas (agentic-group/us-portal-atlas,
  `data/portals-rows.json`, sources listed in `site.json`). The owner decided each facility type gets its own portal.
  Atlas `/p/<slug>` and `/nursing-homes/*` 301 here (Atlas `data/facility-redirects.json`).
- Slug rule: Atlas slug lower-cased, non `[a-z0-9-]` runs to one hyphen, hyphen runs collapsed, cut at 121 characters.
  The Atlas redirect uses the same rule. Do not change slugs without adding `past_slugs` handling.
- Operator: Agentic, Inc. English only (`scripts/english-only.mjs`).

## Run

```sh
npm start              # node server.js (PORT)
npm run validate       # data/items.json shape, answer columns, English only
python3 scripts/import-atlas.py <us-portal-atlas checkout>   # rebuild data/items.json from the Atlas rows
```

## Pages

`/` (types, states, all rows), `/type/<type>/`, `/state/<state>/`, `/state/<state>/<type>/`, `/city/<city>-<st>/`,
`/items/<slug>/`, `/about/`, `/sitemap.xml` (index), `/robots.txt` (training disallowed, search allowed), `/llms.txt`,
`/api/items` (type, state, city, q, limit, offset), `/api/collection-status`. Pagination is a real path (`/page/N/`).

## Deploy (Cloudflare, not Contabo)

Production is Workers Static Assets on the portal Cloudflare account (Grantry scope `portal-cloudflare`), Worker
`portal-us-senior-care`, custom domain `senior-care.navi-index.com` (zone navi-index.com `5377dda84a3d3f53ad8eb2b4b992bc26`).
On the ops machine:

```sh
python3 scripts/deploy-cf.py us-senior-care-portal senior-care.navi-index.com --zone-id 5377dda84a3d3f53ad8eb2b4b992bc26 [--if-changed]
```

It runs `server.js` once, crawls every page (~37k), uploads them as assets, and serves `/api/items` from row shards
(`/__rows/all/<k>.json` per 1,000 rows, `/__rows/state/<state>.json`). A single rows file would be ~25 MB, at the
per-asset limit; do not go back to it. The script self-tests the generated Worker against `server.js` before upload.
Merging to main does not deploy by itself.

## Where it goes wrong

- Assets ignore the query string. Never paginate with `?page=`; the Worker 301s old `?page=N` to `/page/N/`.
- `data/items.json` is generated. Fix the importer, not the JSON.
