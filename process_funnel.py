"""Funnel & ROI per product (funnel sheet) + copy / positioning / audience analysis (creative sheet or ad names).

Both sheets are optional. Without the funnel sheet the ROI section says "not configured".
Without the creative sheet, the copy analysis runs on ad names from raw/ad_days.json (no SQL columns)."""
import collections
import csv
import re

from common import *

SHEETS = os.path.join(RAW, "sheets")
# personal-data columns: a person's name, phone or email (Campaign Name / Ad Name are fine)
PII = re.compile(r"(?i)^\s*((first|last|full|lead|contact|student|owner)?[ _]?name|.*e-?mail.*|.*phone.*|.*mobile.*)\s*$")

ANGLES = [
    ("AI/tool", r"\bai\b|gen ?ai|chatgpt|tool|copilot|automation"),
    ("pain", r"no[ _]?ca|no[ _]?mba|stuck|tired|fail|struggl|without|rejected|low salary"),
    ("speed", r"\d+ ?(month|week|day)s?|fast|quick|in \d"),
    ("career", r"job|career|placement|salary|switch|analyst|hiring|role|promotion|jobtitle|itswitch"),
    ("credential", r"iim|isb|kpmg|pwc|ibm|certif|institute|logo|building|accredit|campus"),
    ("outcome", r"outcome|result|success|alumni|leader|cfo|story|testimonial"),
    ("product", r"product|course|program|curriculum|syllabus|module|neobank|fintech|financial"),
    ("ease", r"easy|simple|anyone|beginner|no experience|from scratch"),
    ("curiosity", r"secret|why|how|what|golmine|goldmine|hidden|truth|myth"),
]
FORMATS = [("video", r"video|reel|vid\b|\[video\]"), ("carousel", r"carousel"),
           ("human/UGC", r"human|ugc|karan|boy|girl|face|founder|talking"),
           ("static", r"static|image|img|banner|poster")]
AUDIENCES = [("lookalike", r"lookalike|look a like|\blal|\bll_|\d%"), ("job titles", r"job ?title|jt\b|work"),
             ("retargeting", r"remarket|retarget|abandon|rmkt|website visitor"),
             ("interest", r"interest|fos|field ?of ?study"), ("open/broad", r"\bopen\b|broad|advantage")]


def tag(text, table, default):
    t = (text or "").lower().replace("_", " ")
    for name, rx in table:
        if re.search(rx, t):
            return name
    return default


def read_csv(name):
    p = os.path.join(SHEETS, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if rows and any(PII.match(k or "") for k in rows[0].keys()):
        fail(f"{name} has personal-data columns; export a tab without names/phones/emails", 4)
    return rows


def col(row, *cands):
    keys = {k.lower().strip(): k for k in row}
    for c in cands:
        for k in keys:
            if re.fullmatch(c, k):
                return num(str(row[keys[k]]).replace(",", "").replace("₹", "").replace("%", "") or 0)
    return None


def verdict_for(spend, leads, roi):
    if spend and not leads:
        return "check tagging"
    if roi is None:
        return "fee not set" if spend else "no spend"
    if roi >= 3:
        return "scale"
    if roi < 1:
        return "fix or cut"
    if roi < 1.8:
        return "watch"
    return "hold"


def stage_level(s):
    m = re.search(r"(?i)level\s*(\d)", s or "")
    return int(m.group(1)) if m else 0


def crm_funnel(rows):
    """Lead-level LeadSquared dump -> funnel per product / campaign / source over the last N days of leads.

    SQL = reached Level 3+, MQL = Level 2+, Sale = Level 5 (current or old stage, so Closed/Lost leads keep the
    highest level they reached). Leads are matched to ad campaigns by exact campaign name; spend is the same
    window's spend for those campaigns (raw/spend_60d.json, raw/google_spend_60d.json)."""
    cfg = SETTINGS.get("crm", {})
    days = cfg.get("window_days", 60)
    fees = cfg.get("program_fees", {})
    pmap = cfg.get("program_map", {})
    hdr = {k.lower().strip(): k for k in rows[0]}
    g = lambda r, *names: next((r.get(hdr[n], "") for n in names if n in hdr), "")
    cut = dstr(ist_today() - dt.timedelta(days=days))

    spend = {}
    for r in raw("spend_60d.json"):
        spend[("meta", r["campaign"].strip().lower())] = (r["campaign"], num(r.get("spend")), num(r.get("actions_lead")))
    for r in raw("google_spend_60d.json"):
        spend[("google", r["campaign"].strip().lower())] = (r["campaign"], num(r.get("cost")), num(r.get("conversions")))
    by_name = {}
    for (plat, low), v in spend.items():
        by_name.setdefault(low, (plat,) + v)

    def parse_date(x):
        x = (x or "").strip()
        for cand in (x, x.split(" ")[0], x[:19], x[:16]):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                        "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y", "%Y/%m/%d", "%d-%b-%y"):
                try:
                    return dt.datetime.strptime(cand, fmt).date()
                except ValueError:
                    pass
        return None

    leads, bad_dates = [], 0
    for r in rows:
        d = parse_date(g(r, "created on", "created date", "createdon"))
        if d is None:
            bad_dates += 1
            continue
        if dstr(d) < cut:
            continue
        lvl = max(stage_level(g(r, "lead stage")), stage_level(g(r, "old lead stage")))
        # Meta leads carry the campaign in "Campaign Name"; Google leads carry it in "Campaign Program"
        camp, hit = "", None
        for cand in (g(r, "campaign name"), g(r, "campaign program")):
            cand = (cand or "").strip()
            if cand and not camp:
                camp = cand
            if cand and by_name.get(cand.lower()):
                camp, hit = cand, by_name[cand.lower()]
                break
        program = (g(r, "pr", "pr2", "program") or "").strip()
        product = pmap.get(program) or (product_of(hit[1]) if hit else None) or program or "Unknown"
        leads.append({"date": dstr(d), "source": (g(r, "pls", "lead source") or "Unknown").strip() or "Unknown",
                      "campaign": camp, "matched": bool(hit), "platform": hit[0] if hit else None,
                      "adgroup": (g(r, "ad_group") or "").strip(), "ad": (g(r, "keyword utm term") or "").strip(),
                      "program": program, "product": product,
                      "location": (g(r, "gr", "city") or "").strip(), "level": lvl,
                      "mql": lvl >= 2, "sql": lvl >= 3, "sale": lvl >= 5})

    def roll(items, spend_total=None):
        n = len(items)
        mql = sum(x["mql"] for x in items); sql = sum(x["sql"] for x in items); sale = sum(x["sale"] for x in items)
        fee_of = lambda x: fees.get(x["program"], fees.get(x["product"]))
        rev = sum(fee_of(x) or 0 for x in items if x["sale"])
        missing_fee = any(fee_of(x) is None for x in items if x["sale"])
        sp = spend_total or 0
        # ROI only when every enrolment in the group has a known fee; otherwise it would look falsely low
        roi = (rev / sp) if (sp and fees and not missing_fee and (sale or items)) else None
        return {"leads": n, "mql": mql, "sql": sql, "sales": sale, "sql_pct": sql / n if n else None,
                "sale_pct": sale / n if n else None, "spend": round(sp, 2),
                "cost_per_lead": round(sp / n, 2) if sp and n else None,
                "cost_per_sql": round(sp / sql, 2) if sp and sql else None,
                "cost_per_sale": round(sp / sale, 2) if sp and sale else None,
                "revenue": rev if fees else None, "roi": round(roi, 2) if roi is not None else None}

    # campaign level (paid, matched)
    camp_rows = collections.defaultdict(list)
    for x in leads:
        if x["matched"]:
            camp_rows[x["campaign"].lower()].append(x)
    campaigns = []
    for low, items in camp_rows.items():
        plat, name, sp, pl = by_name[low]
        c = {"campaign": name, "platform": plat, "product": product_of(name), "platform_leads": pl, **roll(items, sp)}
        c["verdict"] = verdict_for(sp, c["leads"], c["roi"])
        campaigns.append(c)
    # campaigns with spend but no CRM leads -> check tagging
    for low, (plat, name, sp, pl) in by_name.items():
        if low not in camp_rows and sp >= 5 * threshold(plat):
            campaigns.append({"campaign": name, "platform": plat, "product": product_of(name), "platform_leads": pl,
                              **roll([], sp), "verdict": "check tagging"})
    campaigns.sort(key=lambda c: -c["spend"])

    # product level: paid spend of matched campaigns + all CRM leads of that product that came from those campaigns
    prod = collections.defaultdict(lambda: {"items": [], "spend": 0.0, "untracked": 0.0})
    for c in campaigns:   # spend counts only where the campaign's leads reach the CRM; the rest is "untracked"
        prod[c["product"]]["spend" if c["leads"] else "untracked"] += c["spend"]
    for x in leads:
        if x["matched"]:
            prod[product_of(x["campaign"])]["items"].append(x)
    products = []
    for p, v in prod.items():
        row = {"product": p, **roll(v["items"], v["spend"]), "untracked_spend": round(v["untracked"], 2)}
        row["verdict"] = verdict_for(row["spend"] or row["untracked_spend"], row["leads"], row["roi"])
        products.append(row)
    products.sort(key=lambda x: -x["spend"])

    by_source = []
    for src in sorted({x["source"] for x in leads}):
        items = [x for x in leads if x["source"] == src]
        by_source.append({"source": src, "matched": sum(x["matched"] for x in items), **roll(items)})
    by_source.sort(key=lambda s: -s["leads"])

    # ad set / ad group level (audience SQL rate), paid only, 10+ leads
    ag = collections.defaultdict(list)
    for x in leads:
        if x["matched"] and x["adgroup"]:
            ag[(x["campaign"], x["adgroup"])].append(x)
    ad = collections.defaultdict(list)   # Meta: utm term carries the ad name
    for x in leads:
        if x["matched"] and x["platform"] == "meta" and x["ad"]:
            ad[(x["campaign"], x["ad"])].append(x)
    ads = sorted(({"campaign": k[0], "ad": k[1], **roll(v)} for k, v in ad.items() if len(v) >= 10),
                 key=lambda a: -a["leads"])
    adgroups = sorted(({"campaign": k[0], "adgroup": k[1], **roll(v)} for k, v in ag.items() if len(v) >= 10),
                      key=lambda a: -a["leads"])

    callouts = []
    good = [c for c in campaigns if c["leads"] >= 20 and c["cost_per_lead"]]
    if good:
        cl = min(good, key=lambda c: c["cost_per_lead"])
        withsql = [c for c in good if c["cost_per_sql"]]
        if withsql:
            cs = min(withsql, key=lambda c: c["cost_per_sql"])
            if cl["campaign"] != cs["campaign"]:
                callouts.append(f"Cheapest lead is not the cheapest SQL: '{cl['campaign']}' has the lowest cost per CRM "
                                f"lead ({fmt_money(cl['cost_per_lead'])}) but '{cs['campaign']}' has the lowest cost per "
                                f"SQL ({fmt_money(cs['cost_per_sql'])}).")
    ags = [a for a in adgroups if a["leads"] >= 20]
    if len(ags) >= 6:
        rates = sorted(a["sql_pct"] for a in ags)
        lo, hi = rates[len(rates) // 4], rates[(3 * len(rates)) // 4]
        for a in ags[:40]:
            if a["sql_pct"] <= lo and a["leads"] >= 40:
                callouts.append(f"Low-quality volume: '{a['adgroup']}' ({a['campaign']}) — {a['leads']} leads, "
                                f"SQL rate {a['sql_pct']:.0%}.")
            elif a["sql_pct"] >= hi and a["sql_pct"] > 0:
                callouts.append(f"Hidden gem: '{a['adgroup']}' ({a['campaign']}) — SQL rate {a['sql_pct']:.0%} "
                                f"on {a['leads']} leads.")
    n = len(leads)
    matched = sum(x["matched"] for x in leads)
    return {"configured": True, "mode": "crm", "window_days": days, "leads_in_window": n,
            "matched_leads": matched, "match_rate": matched / n if n else None, "bad_dates": bad_dates,
            "revenue_available": bool(fees), "products": products, "campaigns": campaigns[:80],
            "by_source": by_source, "adgroups": adgroups[:60], "ads": ads[:60], "callouts": callouts[:8],
            "unmatched_campaign_names": [k for k, _ in collections.Counter(
                x["campaign"] for x in leads if not x["matched"] and x["campaign"]).most_common(15)]}


def funnel(spend_by_product):
    rows = read_csv("funnel.csv")
    if rows is None:
        return {"configured": False, "products": []}
    keys = {k.lower().strip() for k in rows[0]}
    if "prospect id" in keys and "lead stage" in keys:
        return crm_funnel(rows)
    agg = collections.defaultdict(collections.Counter)
    pkey = next((k for k in rows[0] if re.search(r"(?i)product|program|course", k)), list(rows[0])[0])
    for r in rows:
        p = product_of(r.get(pkey, ""))
        for field, pats in (("leads", [r"(crm )?leads?"]), ("sql", [r"sqls?", r"qualified.*"]),
                            ("sales", [r"sales", r"enrol+ments?", r"admissions?", r"registrations?"]),
                            ("revenue", [r"revenue.*", r"collections?"]), ("spend", [r"spend", r"cost"])):
            v = col(r, *pats)
            if v:
                agg[p][field] += v
    out = []
    for p in sorted(set(agg) | set(spend_by_product)):
        a = agg.get(p, collections.Counter())
        spend = a.get("spend") or spend_by_product.get(p, 0)
        leads, sql, sales, rev = a.get("leads", 0), a.get("sql", 0), a.get("sales", 0), a.get("revenue", 0)
        roi = rev / spend if spend else None
        if spend and not leads:
            verdict = "check tagging"
        elif roi is None:
            verdict = "no spend"
        elif roi >= 3:
            verdict = "scale"
        elif roi < 1:
            verdict = "fix or cut"
        elif roi < 1.8:
            verdict = "watch"
        else:
            verdict = "hold"
        out.append({"product": p, "spend": round(spend, 2), "leads": leads, "sql": sql,
                    "sql_pct": sql / leads if leads else None, "sales": sales,
                    "cost_per_sql": round(spend / sql, 2) if sql else None,
                    "cost_per_sale": round(spend / sales, 2) if sales else None,
                    "revenue": rev, "roi": round(roi, 2) if roi is not None else None, "verdict": verdict})
    out.sort(key=lambda x: -(x["spend"] or 0))
    return {"configured": True, "products": out}


def creative():
    sheet = read_csv("creative.csv")
    rows = []
    if sheet:
        for r in sheet:
            name = next((r[k] for k in r if re.search(r"(?i)ad ?name|creative", k)), "")
            rows.append({"ad_name": name, "text": " ".join(str(v) for k, v in r.items() if re.search(r"(?i)hook|copy|headline", k)),
                         "spend": col(r, r"spend|cost") or 0, "impressions": col(r, r"impressions?") or 0,
                         "clicks": col(r, r"(link )?clicks?") or 0, "leads": col(r, r"leads?") or 0,
                         "sql": col(r, r"sqls?", r"qualified.*")})
        source = "creative sheet"
    else:
        per = collections.defaultdict(lambda: collections.Counter())
        meta_prod = {}
        dates = sorted({r["date"][:10] for r in raw("ad_days.json")})
        last7 = set(dates[-7:])
        for r in raw("ad_days.json"):
            if r["date"][:10] not in last7:
                continue
            k = r.get("ad_name") or ""
            per[k].update({"spend": num(r.get("spend")), "impressions": num(r.get("impressions")),
                           "clicks": num(r.get("actions_link_click")), "leads": num(r.get("actions_lead"))})
            meta_prod[k] = (product_of(r["campaign"]), r.get("adset_name") or "")
        for k, v in per.items():
            rows.append({"ad_name": k, "text": "", "product": meta_prod[k][0], "adset": meta_prod[k][1], **v, "sql": None})
        source = "ad names (last 7 days) — add the creative sheet for SQL columns"
    for r in rows:
        blob = f"{r['ad_name']} {r.get('text', '')}"
        r["angle"] = tag(blob, ANGLES, "untagged")
        r["format"] = tag(blob, FORMATS, "unknown")
        r["audience"] = tag(f"{r.get('adset', '')} {r['ad_name']}", AUDIENCES, "unknown")
        hook = re.sub(r"(?i)\b(lg|online|video|static|image|img|set|copy|new|v\d+|\d+[a-z]{3}\d+|\d+)\b", " ",
                      r["ad_name"].replace("_", " ").replace("[", " ").replace("]", " "))
        r["hook"] = re.sub(r"\s+", " ", hook).strip()[:60]

    def group(key):
        g = collections.defaultdict(lambda: collections.Counter())
        for r in rows:
            g[r[key]].update({k: (r[k] or 0) for k in ("spend", "impressions", "clicks", "leads")})
            if r.get("sql") is not None:
                g[r[key]]["sql"] += r["sql"]; g[r[key]]["has_sql"] = 1
        out = []
        for name, v in g.items():
            if v["spend"] <= 0:
                continue
            out.append({"name": name, "spend": round(v["spend"], 2), "leads": v["leads"],
                        "ctr": v["clicks"] / v["impressions"] if v["impressions"] else None,
                        "ff": v["leads"] / v["clicks"] if v["clicks"] else None,
                        "cpl": round(v["spend"] / v["leads"], 2) if v["leads"] else None,
                        "sql_rate": v["sql"] / v["leads"] if v.get("has_sql") and v["leads"] else None,
                        "cost_per_sql": round(v["spend"] / v["sql"], 2) if v.get("has_sql") and v["sql"] else None})
        return sorted(out, key=lambda x: -x["spend"])

    angles, audiences, formats = group("angle"), group("audience"), group("format")
    callouts = []
    with_sql = [a for a in angles if a["cost_per_sql"]]
    if with_sql:
        cheap_lead = min((a for a in angles if a["cpl"]), key=lambda a: a["cpl"])
        cheap_sql = min(with_sql, key=lambda a: a["cost_per_sql"])
        if cheap_lead["name"] != cheap_sql["name"]:
            callouts.append(f"Cheapest lead is not the cheapest SQL: '{cheap_lead['name']}' has the lowest CPL "
                            f"({fmt_money(cheap_lead['cpl'])}) but '{cheap_sql['name']}' has the lowest cost per SQL "
                            f"({fmt_money(cheap_sql['cost_per_sql'])}).")
        ads = [r for r in rows if r["impressions"] and r["leads"] >= 5 and r.get("sql") is not None]
        if len(ads) >= 8:
            ctrs = sorted(r["clicks"] / r["impressions"] for r in ads)
            srs = sorted(r["sql"] / r["leads"] for r in ads)
            q = lambda xs, p: xs[int(p * (len(xs) - 1))]
            for r in ads:
                c, s = r["clicks"] / r["impressions"], r["sql"] / r["leads"]
                if c >= q(ctrs, .75) and s <= q(srs, .25):
                    callouts.append(f"Click-bait: '{r['ad_name']}' — CTR {c:.2%} but SQL rate {s:.0%}.")
                if c <= q(ctrs, .25) and s >= q(srs, .75):
                    callouts.append(f"Hidden gem: '{r['ad_name']}' — low CTR {c:.2%} but SQL rate {s:.0%}.")
    else:
        good = [a for a in angles if a["cpl"] and a["leads"] >= 20 and a["name"] != "untagged"]
        if len(good) >= 2:
            b, w = min(good, key=lambda a: a["cpl"]), max(good, key=lambda a: a["cpl"])
            callouts.append(f"On CPL alone, '{b['name']}' angles ({fmt_money(b['cpl'])}) beat '{w['name']}' "
                            f"({fmt_money(w['cpl'])}). Without SQL data this can't say which brings better leads.")
        hi = [r for r in rows if r["impressions"] > 20000 and r["clicks"]]
        if hi:
            top = max(hi, key=lambda r: r["clicks"] / r["impressions"])
            ff = top["leads"] / top["clicks"]
            callouts.append(f"Highest-CTR ad: '{top['ad_name']}' ({top['clicks'] / top['impressions']:.2%} CTR, "
                            f"{ff:.1%} form fill).")
    return {"source": source, "angles": angles, "audiences": audiences, "formats": formats,
            "callouts": callouts, "ads_tagged": len(rows)}


def main():
    rows = read_json(os.path.join(HIST, "rolling_campaign_days.json"), [])
    spend = collections.Counter()
    for r in rows:
        spend[product_of(r["campaign"])] += r["spend"]
    f = funnel(spend)
    c = creative()
    write_json(os.path.join(REPORTS, "funnel.json"), {"funnel": f, "creative": c})
    print(f"process_funnel: funnel {'configured' if f['configured'] else 'not configured'}, "
          f"creative from {c['source']}: {len(c['angles'])} angles, {len(c['callouts'])} callouts")


if __name__ == "__main__":
    main()
