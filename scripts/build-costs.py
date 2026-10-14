#!/usr/bin/env python3
"""Build data/costs.json from CMS cost reports (public domain, 17 U.S.C. 105).

Nursing homes: CMS Skilled Nursing Facility Cost Report (FY 2024 PUF). Per facility:
  cost per resident day   = Less Total Operating Expense / Total Days Total (latest FY 2023 or FY 2024 report per CCN)
  revenue per resident day = Net Patient Revenue / Total Days Total (what the facility actually collected, all payers)
  charge per resident day  = Gross Revenue / Total Days Total (list charges before payer discounts)
Home health: CMS Home Health Agency Cost Report (2024 PUF): average cost per visit (all disciplines, RN, aide).
State and metro (CBSA) values are medians of facility values. Medicaid base rate per state comes from
data/medicaid-nf-rates.json (MACPAC Table A-1).

  python3 scripts/build-costs.py            # downloads both CSVs (~15 MB) to a temp dir
"""
import csv, json, os, statistics, sys, tempfile, urllib.request, collections

SNF_FILES = ["https://data.cms.gov/sites/default/files/2026-10/a3055a9a-a1db-4b01-956f-2492479d157f/CostReportsnf_Final_23.csv",
             "https://data.cms.gov/sites/default/files/2026-10/0f2b9acd-7ed2-4984-b0d7-6ec003493c1c/CostReportsnf_Final_24.csv"]
SNF = (SNF_FILES[-1], "https://data.cms.gov/provider-compliance/cost-report/skilled-nursing-facility-cost-report")
HHA = ("https://data.cms.gov/sites/default/files/2026-09/8ee21209-1a1f-4564-abfb-b83d2fef37a8/CostReport_HHA_2024.csv",
       "https://data.cms.gov/provider-compliance/cost-report/home-health-agency-cost-report")
RETRIEVED = os.environ.get("RETRIEVED", "2026-10-14")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ST = {"AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California","CO":"Colorado","CT":"Connecticut","DE":"Delaware","DC":"District of Columbia","FL":"Florida","GA":"Georgia","HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa","KS":"Kansas","KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland","MA":"Massachusetts","MI":"Michigan","MN":"Minnesota","MS":"Mississippi","MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada","NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico","NY":"New York","NC":"North Carolina","ND":"North Dakota","OH":"Ohio","OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania","PR":"Puerto Rico","RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah","VT":"Vermont","VA":"Virginia","WA":"Washington","WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming","GU":"Guam","VI":"Virgin Islands"}

def rows(url, tmp):
    p = os.path.join(tmp, os.path.basename(url))
    if not os.path.exists(p):
        urllib.request.urlretrieve(url, p)
    with open(p, encoding="latin-1", newline="") as f:
        r = csv.reader(f); h = [" ".join(c.split()) for c in next(r)]
        for x in r: yield dict(zip(h, x))

def num(v):
    try: return float(str(v).replace(",", "").replace("$", ""))
    except ValueError: return None

def med(xs): return round(statistics.median(xs), 2) if xs else None

def main():
    tmp = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
    fac = {}
    for x in (r for u in SNF_FILES for r in rows(u, tmp)):
        days, cost, net, gross = num(x["Total Days Total"]), num(x["Less Total Operating Expense"]), num(x["Net Patient Revenue"]), num(x["Gross Revenue"])
        ccn = x["Provider CCN"].strip().zfill(6)
        st = ST.get(x["State Code"].strip())
        if not (days and days >= 1000 and cost and st): continue
        cpd = cost / days
        if not 50 <= cpd <= 3000: continue
        rpd = net / days if net and 50 <= net / days <= 3000 else None
        gpd = gross / days if gross and 50 <= gross / days <= 5000 else None
        prev = fac.get(ccn)
        if prev and prev["fy_end"] >= x["Fiscal Year End Date"]: continue
        fac[ccn] = {"ccn": ccn, "state": st, "city": x["City"].strip().title(), "cbsa": x["Medicare CBSA Number"].strip(),
                    "cost_per_day": round(cpd, 2), "revenue_per_day": rpd and round(rpd, 2), "charge_per_day": gpd and round(gpd, 2),
                    "beds": num(x["Number of Beds"]), "fy_end": x["Fiscal Year End Date"]}
    hh = collections.defaultdict(lambda: {"all": [], "rn": [], "aide": []})
    for x in rows(HHA[0], tmp):
        st = ST.get(x["State Code"].strip()); tc, tv = num(x["Total Cost"]), num(x["Total, Total Visits"])
        if not (st and tc and tv and tv >= 500): continue
        v = tc / tv
        if 20 <= v <= 1000: hh[st]["all"].append(v)
        for k, col in (("rn", "Skilled Nursing Care-RN, Avg Cost Per Visit"), ("aide", "Home Health Aide, Avg Cost Per Visit")):
            a = num(x.get(col))
            if a and 20 <= a <= 1000: hh[st][k].append(a)
    mcd = json.load(open(os.path.join(ROOT, "data", "medicaid-nf-rates.json")))
    by_state = collections.defaultdict(list); by_cbsa = collections.defaultdict(list)
    for f in fac.values():
        by_state[f["state"]].append(f)
        if f["cbsa"] and f["cbsa"] not in ("0", "00000") and len(f["cbsa"]) == 5: by_cbsa[(f["state"], f["cbsa"])].append(f)
    def summary(fs):
        return {"facilities": len(fs), "median_cost_per_day": med([f["cost_per_day"] for f in fs]),
                "median_revenue_per_day": med([f["revenue_per_day"] for f in fs if f["revenue_per_day"]]),
                "median_charge_per_day": med([f["charge_per_day"] for f in fs if f["charge_per_day"]])}
    states = {}
    for st in sorted(set(by_state) | set(hh)):
        s = summary(by_state.get(st, []))
        s["medicaid"] = mcd["states"].get(st)
        h = hh.get(st)
        s["home_health"] = h and {"agencies": len(h["all"]), "median_cost_per_visit": med(h["all"]), "median_rn_visit_cost": med(h["rn"]), "median_aide_visit_cost": med(h["aide"])}
        metros = []
        for (s2, cb), fs in by_cbsa.items():
            if s2 != st or len(fs) < 3: continue
            city = collections.Counter(f["city"] for f in fs).most_common(1)[0][0]
            metros.append({"cbsa": cb, "label": f"Rural {st}" if cb.startswith("999") else f"{city} area", **summary(fs)})
        s["metros"] = sorted(metros, key=lambda m: -m["facilities"])
        states[st] = s
    allh = [v for h in hh.values() for v in h["all"]]
    out = {"retrieved": RETRIEVED,
           "sources": {"snf": {"name": "CMS Skilled Nursing Facility Cost Report, FY 2023 and FY 2024 public use files", "url": SNF[1], "file": SNF[0]},
                       "hha": {"name": "CMS Home Health Agency Cost Report, 2024 public use file", "url": HHA[1], "file": HHA[0]},
                       "medicaid": {"name": mcd["source"], "url": mcd["source_url"], "file": mcd["pdf_url"], "note": mcd["note"]}},
           "national": {**summary(list(fac.values())), "medicaid": mcd["states"].get("National"),
                        "home_health": {"agencies": len(allh), "median_cost_per_visit": med(allh)}},
           "states": states,
           "facilities": {k: {kk: v[kk] for kk in ("cost_per_day", "revenue_per_day", "charge_per_day", "fy_end")} for k, v in sorted(fac.items())}}
    json.dump(out, open(os.path.join(ROOT, "data", "costs.json"), "w"), separators=(",", ":"))
    print(f"facilities={len(fac)} states={len(states)} metros={sum(len(s['metros']) for s in states.values())} hha={len(allh)}")

if __name__ == "__main__":
    main()
