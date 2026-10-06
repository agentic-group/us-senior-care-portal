#!/usr/bin/env python3
"""Build data/items.json from the US Portal Atlas care-facility rows.

    python3 scripts/import-atlas.py <us-portal-atlas checkout>

Why: until 2026-10-06 the care-facility rows (CMS nursing homes, home health and hospice
agencies, palliative care programs, state veterans homes, adult care homes, CCRCs, community
health centers) were stored inside the Atlas portal registry. The owner decided each facility
type gets its own portal; this site is the senior-care one. Slugs are kept identical so the
Atlas can 301 /p/<slug> to /items/<slug>/ on this host.

Values are copied verbatim from the Atlas rows (which cite their source page and fetch date) and,
for CMS nursing homes, joined by CCN to the Atlas CMS extract (data/nursing-homes.ts: Provider
Information 4pq5-n9py + Penalties g6vv-u9sr). Nothing is estimated: a value that the source row
does not state is left out. No LLM.
"""
import json
import pathlib
import re
import sys

ATLAS = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "../us-portal-atlas")
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "items.json"
SRC_MAP = pathlib.Path(__file__).resolve().parent.parent / "data" / "atlas-sources.json"

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico", "GU": "Guam",
    "VI": "U.S. Virgin Islands", "AS": "American Samoa", "MP": "Northern Mariana Islands",
}
BY_NAME = {v.lower(): k for k, v in STATES.items()}

# Atlas `src` -> (care type, source label). The Atlas row keeps the source page and fetch date.
SOURCES = {
    "cms-care-compare": ("Nursing home", "CMS Provider Data Catalog: Nursing Home Provider Information"),
    "nchcfa": ("Nursing home", "North Carolina Health Care Facilities Association: Facility Finder"),
    "carealabama": ("Nursing home", "Care of Alabama: Alabama nursing home list"),
    "whca": ("Long-term care facility", "Washington Health Care Association: Facility Finder"),
    "ncdhhs-acls": ("Adult care home", "NC Division of Health Service Regulation: adult care home listings"),
    "nasvh": ("State veterans home", "National Association of State Veterans Homes: directory"),
    "carf-ccrc": ("Continuing care retirement community", "CARF International: America's Best CCRCs 2026"),
    "allianceforcareathome": (None, "National Alliance for Care at Home: Find a Provider"),
    "getpalliativecare": ("Palliative care program", "Center to Advance Palliative Care: provider directory"),
    "hospice101": ("Hospice", "Hospice 101: Florida hospice directory"),
    "health-ny": ("Hospice", "New York State Department of Health: hospice directory"),
    "hrsa-health-centers": ("Community health center", "HRSA: Health Center Program directory"),
}

CITY_ST = re.compile(r"\bin ([A-Za-z][A-Za-z .'\-]+?), ([A-Z]{2})\b")
CITY_STATE_NAME = re.compile(r"\bin ([A-Za-z][A-Za-z .'\-]+?), ([A-Z][a-z]+(?: [A-Z][a-z]+)*)\b")
ZIP = re.compile(r"\(ZIP (\d{5}(?:-\d{4})?)\)")
COUNTY = re.compile(r"\(([A-Za-z .'\-]+) County\)")


def tcase(s):
    """CMS publishes names and cities in upper case. Title-case for reading; keep short all-caps tokens
    that are acronyms (LLC, INC, II) as published."""
    if not s or s != s.upper():
        return s
    keep = {"LLC", "INC", "LP", "LLP", "II", "III", "IV", "PC", "NH", "SNF", "HCC", "VA", "USA", "CCRC", "RN", "NW", "NE", "SW", "SE"}
    out = []
    for w in s.split(" "):
        core = re.sub(r"[^A-Z]", "", w)
        if core in keep:
            out.append(w)
        else:
            # "10TH" -> "10th" (a letter run after a digit is a suffix, not a word)
            out.append(re.sub(r"(?<![0-9])[A-Za-z]+|(?<=[0-9])[A-Za-z]+", lambda m: m.group(0).lower() if m.start() and (w[m.start() - 1].isdigit() or w[m.start() - 1] == "'") else m.group(0)[:1] + m.group(0)[1:].lower(), w))
    return " ".join(out)


def phone_fmt(digits):
    d = re.sub(r"\D", "", digits or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else None


def new_slug(atlas_slug):
    s = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", atlas_slug.lower())).strip("-")
    return s[:121].rstrip("-")


def load_cms():
    txt = (ATLAS / "data" / "nursing-homes.ts").read_text()
    out = {}
    for line in txt.splitlines():
        line = line.strip().rstrip(",")
        if line.startswith('{"ccn"'):
            r = json.loads(line)
            out[r["ccn"]] = r
    meta = {k: re.search(rf'{k} = "([^"]+)"', txt).group(1) for k in ("NURSING_HOMES_FETCHED_AT", "NURSING_HOMES_PROVIDER_SOURCE_URL")}
    return out, meta


def city_state(desc):
    m = CITY_ST.search(desc)
    if m and m.group(2) in STATES:
        return m.group(1).strip(), m.group(2)
    m = CITY_STATE_NAME.search(desc)
    if m and m.group(2).lower() in BY_NAME:
        return m.group(1).strip(), BY_NAME[m.group(2).lower()]
    return None, None


def main():
    rows = json.loads((ATLAS / "data" / "portals-rows.json").read_text())
    cms, cms_meta = load_cms()
    items, unparsed, per_src = [], [], {}
    for r in rows:
        src = r.get("src")
        if src not in SOURCES:
            continue
        ctype, src_label = SOURCES[src]
        d = r.get("description") or ""
        f = {}
        city, st = city_state(d)
        name = r["name"]
        official = None
        if src == "cms-care-compare":
            ccn = r["slug"].removeprefix("cms-nh-").upper()
            c = cms.get(ccn)
            f["CMS Certification Number (CCN)"] = ccn
            if c:
                name = tcase(c["name"])
                city, st = tcase(c["city"]), c["state"]
                f["Address"] = f'{tcase(c["address"])}, {tcase(c["city"])}, {c["state"]} {c["zip"]}'
                f["ZIP"] = c["zip"]
                if c.get("phone"):
                    f["Phone"] = c["phone"]
                f["CMS overall star rating"] = f'{c["overall"]} of 5'
                f["Health inspection rating"] = f'{c["health"]} of 5'
                f["Staffing rating"] = f'{c["staffing"]} of 5'
                f["RN staffing hours per resident per day"] = f'{c["rnHours"]:.2f}'
                f["Certified beds"] = str(c["beds"])
                f["Ownership"] = c["ownership"]
                f["Federal fines (last 3 years)"] = str(c["fines"])
                f["Total fine amount (last 3 years)"] = f'${c["fineAmount"]:,}'
                f["Medicare payment denials (last 3 years)"] = str(c["denials"])
                f["Ratings source"] = f'CMS Provider Data Catalog datasets 4pq5-n9py and g6vv-u9sr (retrieved {cms_meta["NURSING_HOMES_FETCHED_AT"]})'
            else:
                name = tcase(name)
                city = tcase(city)
                z = ZIP.search(d)
                if z:
                    f["ZIP"] = z.group(1)
                f["CMS overall star rating"] = "Not rated by CMS in the source file"
            f["Medicare Care Compare page"] = r["url"]
        elif src == "allianceforcareathome":
            m = re.match(r"^(?P<svc>.+?) provider at (?P<addr>.+), (?P<city>[^,]+), (?P<st>[A-Z]{2}), listed", d)
            svc = d.split(" provider at ")[0]
            if m:
                city, st = m.group("city").strip(), m.group("st")
                f["Address"] = f'{m.group("addr")}, {city}, {st}'
                svc = m.group("svc")
            f["Services"] = svc
            ctype = "Home health agency" if "Home Health" in svc else ("Hospice" if "Hospice" in svc else "Home care agency")
        elif src == "getpalliativecare":
            m = re.search(r"offers (?P<set>.+?) palliative care; phone \+?(?P<ph>\d{10,11})", d)
            if m:
                f["Palliative care settings"] = m.group("set")
                p = phone_fmt(m.group("ph"))
                if p:
                    f["Phone"] = p
            m = re.search(r"program is listed as (.+?)\. Listed website: (\S+?)\.?$", d)
            if m:
                f["Palliative care program"] = m.group(1)
                official = "https://" + m.group(2).removeprefix("https://").removeprefix("http://")
            elif r["url"] and "capc.org" not in r["url"]:
                official = r["url"]
        elif src == "nasvh":
            m = re.search(r"Phone: ([^.]+?)\. Address: (.+?)\. Beds: (.+?)\. Site: (\S+?)\.?$", d)
            if m:
                f["Phone"] = m.group(1).strip()
                f["Address"] = m.group(2).strip()
                f["Beds by level of care"] = m.group(3).strip()
                official = m.group(4)
                a = re.search(r", ([A-Za-z .'\-]+), ([A-Z]{2}) \d{5}", f["Address"])
                if a:
                    city, st = a.group(1).strip(), a.group(2)
            else:
                m = re.search(r"Phone: ([^.]+?)\.", d)
                if m:
                    f["Phone"] = m.group(1).strip()
        elif src == "ncdhhs-acls":
            m = re.search(r"(\d+) beds", d)
            if m:
                f["Licensed beds"] = m.group(1)
        elif src == "carf-ccrc":
            f["Accreditation"] = "CARF-accredited; listed among America's Best Continuing Care Retirement Communities 2026"
            official = r["url"]
        elif src == "hrsa-health-centers":
            f["Program"] = "Federally Qualified Health Center (Health Center Program grantee)"
        z = ZIP.search(d)
        if z and "ZIP" not in f:
            f["ZIP"] = z.group(1)
        cty = COUNTY.search(d)
        if cty:
            f["County"] = cty.group(1).strip()
        if not city or not st:
            unparsed.append(r["slug"])
        f = {"Care type": ctype, **({"City": city} if city else {}), **({"State": STATES[st]} if st else {}), **f}
        if official:
            f["Official website"] = official
        facets = {"type": ctype}
        if st:
            facets["state"] = STATES[st]
        if city and st:
            facets["city"] = f"{city}, {st}"
        per_src[src] = per_src.get(src, 0) + 1
        items.append({
            # Atlas slugs may carry upper case (CMS CCNs like 01A193) or exceed 121 characters. The new slug is
            # the Atlas slug lower-cased, non [a-z0-9-] runs turned into one hyphen, hyphen runs collapsed, cut at 121 characters; the Atlas 301 applies the same rule.
            "slug": new_slug(r["slug"]),
            "name": name,
            "summary": d,
            "updated_at": (r.get("operatorFetchedAt") or r.get("addedAt") or "2026-09-23") + "T00:00:00Z",
            "source_id": src,
            "source_url": r["url"],
            "facets": facets,
            "fields": f,
        })
    seen = {}
    for it in items:
        if it["slug"] in seen:
            raise SystemExit(f"slug collision after lower-casing/cutting: {it['slug']}")
        seen[it["slug"]] = 1
    items.sort(key=lambda x: (x["facets"].get("state", "~"), x["facets"].get("city", "~"), x["name"]))
    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=0) + "\n")
    SRC_MAP.write_text(json.dumps({"per_source": per_src, "unparsed_city_state": unparsed}, indent=1) + "\n")
    print(f"{len(items)} rows; per source {per_src}; without city/state {len(unparsed)}")


if __name__ == "__main__":
    main()
