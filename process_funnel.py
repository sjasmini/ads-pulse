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
    """'Level 3 FP (Future Prospect)' -> 3, 'Level 2a' -> 2; Closed / Hold / Created / DNR / Level R -> 0."""
    m = re.search(r"(?i)level\s*(\d)", s or "")
    return int(m.group(1)) if m else 0


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def toks(s):
    return tuple(sorted(t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t))


class NameMatcher:
    """Matches a UTM campaign / ad-group value from the CRM to the ad platform's name.

    Order: exact (ignoring case, spaces and punctuation) -> same words in any order
    (SC_FRM_CertPrep_PanIndia = SC_Certprep_FRM_PanIndia) -> unique prefix (UTM values cut at 50 characters,
    or PWC_FAP_Perf -> PWC_FAP_Perf_AdGroup)."""

    def __init__(self, names):
        self.exact, self.words = {}, collections.defaultdict(set)
        for n in names:
            self.exact.setdefault(norm(n), n)
            self.words[toks(n)].add(n)
        self.keys = sorted(self.exact)

    def get(self, value):
        k = norm(value)
        if not k:
            return None
        if k in self.exact:
            return self.exact[k]
        w = self.words.get(toks(value))
        if w and len(w) == 1:
            return next(iter(w))
        if len(k) >= 10:
            hits = [x for x in self.keys if x.startswith(k)]
            if len(hits) == 1:
                return self.exact[hits[0]]
        return None


def crm_funnel(rows):
    """Lead-level LeadSquared extract -> Lead / MQL / SQL / enrolled with cost per stage at product, campaign,
    ad set (Meta) / ad group (Google) and ad (Meta) level.

    MQL = reached Level 2+, SQL = Level 3+ (3, 3a, 3 FP, 4), enrolled = Level 5, using the higher of Lead Stage and
    Old Lead Stage so Closed/Lost leads keep the level they reached. Spend comes from Windsor pulls for EXACTLY the
    dates the lead sheet covers (raw/meta_ads_window.json, raw/google_adgroups_window.json) — never a longer window,
    which would inflate every cost per lead / SQL."""
    cfg = SETTINGS.get("crm", {})
    fees = cfg.get("program_fees", {})
    pmap = cfg.get("program_map", {})
    excl = set(cfg.get("exclude_accounts", ["MyCaptain"]))
    hdr = {k.lower().strip(): k for k in rows[0]}
    g = lambda r, *names: next((r.get(hdr[n], "") for n in names if n in hdr), "")

    def parse_date(x):
        x = (x or "").strip()
        for cand in (x, x.split(" ")[0], x[:19], x[:16]):
            for fmt in ("%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y",
                        "%Y/%m/%d", "%d-%b-%y"):
                try:
                    return dt.datetime.strptime(cand, fmt).date()
                except ValueError:
                    pass
        return None

    # ---- ad platform spend for the lead window (ad level for Meta, ad group level for Google)
    meta = [r for r in raw("meta_ads_window.json") if r.get("account_name") not in excl]
    goog = [r for r in raw("google_adgroups_window.json") if r.get("account_name") not in excl]
    if not meta and not goog:
        fail("no window spend files (raw/meta_ads_window.json / raw/google_adgroups_window.json)", 6)
    sp = collections.defaultdict(lambda: collections.Counter())      # key -> spend / platform_leads / clicks / impr
    camp_plat = {}
    for r in meta:
        c, s, a = r["campaign"], r.get("adset_name") or "", r.get("ad_name") or ""
        v = {"spend": num(r.get("spend")), "platform_leads": num(r.get("actions_lead")),
             "clicks": num(r.get("actions_link_click")), "impressions": num(r.get("impressions"))}
        for key in (("c", c), ("s", c, s), ("a", c, s, a)):
            sp[key].update(v)
        camp_plat[c] = "meta"
    for r in goog:
        c, s = r["campaign"], r.get("ad_group_name") or ""
        v = {"spend": num(r.get("cost")), "platform_leads": num(r.get("conversions")),
             "clicks": num(r.get("clicks")), "impressions": num(r.get("impressions"))}
        for key in (("c", c), ("s", c, s)):
            sp[key].update(v)
        camp_plat.setdefault(c, "google")
    meta_c = NameMatcher([c for c, p in camp_plat.items() if p == "meta"])
    goog_c = NameMatcher([c for c, p in camp_plat.items() if p == "google"])
    sets = collections.defaultdict(list); ads_of = collections.defaultdict(list)
    for k in sp:
        if k[0] == "s":
            sets[k[1]].append(k[2])
        elif k[0] == "a":
            ads_of[(k[1], k[2])].append(k[3])
    set_m = {c: NameMatcher(v) for c, v in sets.items()}
    ad_m = {cs: NameMatcher(v) for cs, v in ads_of.items()}

    leads, bad_dates = [], 0
    for r in rows:
        d = parse_date(g(r, "created on", "created date", "createdon"))
        if d is None:
            bad_dates += 1
            continue
        lvl = max(stage_level(g(r, "lead stage")), stage_level(g(r, "old lead stage")))
        cn, cprog = (g(r, "campaign name") or "").strip(), (g(r, "campaign program") or "").strip()
        camp = plat = None
        for cand in (cn, cprog):          # Meta leads carry the campaign in Campaign Name, Google in Campaign Program
            for m, p in ((meta_c, "meta"), (goog_c, "google")):
                hit = m.get(cand)
                if hit:
                    camp, plat = hit, p
                    break
            if camp:
                break
        agv, adv = (g(r, "ad_group") or "").strip(), (g(r, "keyword utm term") or "").strip()
        adset = set_m[camp].get(agv) if camp in set_m else None
        ad = ad_m[(camp, adset)].get(adv) if plat == "meta" and adset and (camp, adset) in ad_m else None
        program = (g(r, "pr", "pr2", "program") or "").strip()
        product = pmap.get(program) or (product_of(camp) if camp else None) or program or "Unknown"
        leads.append({"date": dstr(d), "source": (g(r, "pls", "lead source") or "Unknown").strip() or "Unknown",
                      "campaign": camp or cn or cprog, "matched": bool(camp), "platform": plat,
                      "adset": adset, "adset_raw": agv, "ad": ad, "keyword": adv if plat == "google" else "",
                      "program": program, "product": product, "level": lvl,
                      "mql": lvl >= 2, "sql": lvl >= 3, "sale": lvl >= 5})
    dates = sorted(x["date"] for x in leads)
    first, last = (dates[0], dates[-1]) if dates else (None, None)

    def roll(items, s=None):
        s = s or {}
        spend = s.get("spend", 0.0)
        n = len(items)
        mql = sum(x["mql"] for x in items); sql = sum(x["sql"] for x in items); sale = sum(x["sale"] for x in items)
        fee_of = lambda x: fees.get(x["program"], fees.get(x["product"]))
        rev = sum(fee_of(x) or 0 for x in items if x["sale"])
        missing_fee = any(fee_of(x) is None for x in items if x["sale"])
        roi = (rev / spend) if (spend and fees and n and not missing_fee) else None
        c = lambda k: round(spend / k, 2) if spend and k else None
        pl = s.get("platform_leads", 0.0)
        return {"spend": round(spend, 2), "platform_leads": round(pl, 1), "platform_cpl": c(pl),
                "leads": n, "mql": mql, "sql": sql, "sales": sale,
                "mql_pct": mql / n if n else None, "sql_pct": sql / n if n else None,
                "sale_pct": sale / n if n else None,
                "cost_per_lead": c(n), "cost_per_mql": c(mql), "cost_per_sql": c(sql), "cost_per_sale": c(sale),
                "revenue": rev if fees else None, "roi": round(roi, 2) if roi is not None else None,
                "fee_missing": missing_fee}

    paid = [x for x in leads if x["matched"]]
    # ---- campaign level: every campaign with spend in the window, with or without CRM leads
    by_c = collections.defaultdict(list)
    for x in paid:
        by_c[x["campaign"]].append(x)
    campaigns = []
    for c, p in camp_plat.items():
        row = {"campaign": c, "platform": p, "product": product_of(c), **roll(by_c.get(c, []), sp[("c", c)])}
        if row["spend"] <= 0 and not row["leads"]:
            continue
        row["verdict"] = verdict_for(row["spend"], row["leads"], row["roi"])
        campaigns.append(row)
    campaigns.sort(key=lambda c: -c["spend"])

    # ---- ad set (Meta) / ad group (Google) level
    by_s = collections.defaultdict(list)
    for x in paid:
        by_s[(x["campaign"], x["adset"])].append(x)
    adsets = []
    for k, s in sp.items():
        if k[0] != "s":
            continue
        row = {"campaign": k[1], "adset": k[2], "platform": camp_plat[k[1]], "product": product_of(k[1]),
               **roll(by_s.get((k[1], k[2]), []), s)}
        if row["spend"] > 0 or row["leads"]:
            adsets.append(row)
    for (c, s), items in by_s.items():          # CRM leads whose ad-group value matches no ad group
        if s is None:
            adsets.append({"campaign": c, "adset": "(ad group not identified)", "platform": camp_plat[c],
                           "product": product_of(c), **roll(items)})
    adsets.sort(key=lambda a: -a["spend"])

    # ---- ad level (Meta: utm term = ad name)
    by_a = collections.defaultdict(list)
    for x in paid:
        if x["ad"]:
            by_a[(x["campaign"], x["adset"], x["ad"])].append(x)
    ads = []
    for k, s in sp.items():
        if k[0] != "a":
            continue
        row = {"campaign": k[1], "adset": k[2], "ad": k[3], "platform": "meta", "product": product_of(k[1]),
               **roll(by_a.get(k[1:], []), s)}
        if row["spend"] > 0 or row["leads"]:
            ads.append(row)
    ads.sort(key=lambda a: -a["spend"])

    # ---- Google keywords (no keyword spend pulled; lead quality only)
    by_k = collections.defaultdict(list)
    for x in paid:
        if x["platform"] == "google" and x["keyword"]:
            by_k[(x["campaign"], x["keyword"].lower())].append(x)
    keywords = sorted(({"campaign": k[0], "keyword": k[1], "platform": "google", "product": product_of(k[0]),
                        **roll(v)} for k, v in by_k.items() if len(v) >= 10), key=lambda a: -a["leads"])

    # ---- product level
    prod_sp = collections.Counter(); prod_items = collections.defaultdict(list)
    for c in campaigns:
        prod_sp[c["product"]] += c["spend"]
    for x in paid:
        prod_items[product_of(x["campaign"])].append(x)
    products = []
    for p in set(prod_sp) | set(prod_items):
        row = {"product": p, **roll(prod_items.get(p, []), {"spend": prod_sp[p], "platform_leads": sum(
            c["platform_leads"] for c in campaigns if c["product"] == p)})}
        row["verdict"] = verdict_for(row["spend"], row["leads"], row["roi"])
        products.append(row)
    products.sort(key=lambda x: -x["spend"])

    platforms = []
    for p in ("meta", "google"):
        platforms.append({"platform": p, **roll([x for x in paid if x["platform"] == p], {
            "spend": sum(c["spend"] for c in campaigns if c["platform"] == p),
            "platform_leads": sum(c["platform_leads"] for c in campaigns if c["platform"] == p)})})

    by_source = []
    for src in sorted({x["source"] for x in leads}):
        items = [x for x in leads if x["source"] == src]
        by_source.append({"source": src, "matched": sum(x["matched"] for x in items), **roll(items)})
    by_source.sort(key=lambda s: -s["leads"])

    callouts = []
    good = [c for c in campaigns if c["leads"] >= 20 and c["cost_per_sql"]]
    if good:
        cl = min(good, key=lambda c: c["cost_per_lead"]); cs = min(good, key=lambda c: c["cost_per_sql"])
        if cl["campaign"] != cs["campaign"]:
            callouts.append(f"Cheapest lead is not the cheapest SQL: '{cl['campaign']}' has the lowest cost per CRM "
                            f"lead ({fmt_money(cl['cost_per_lead'])}, cost per SQL {fmt_money(cl['cost_per_sql'])}) "
                            f"but '{cs['campaign']}' has the lowest cost per SQL ({fmt_money(cs['cost_per_sql'])}).")
    zero = [a for a in ads if a["leads"] >= 25 and a["sql"] == 0]
    for a in sorted(zero, key=lambda a: -a["spend"])[:3]:
        callouts.append(f"No SQLs: ad '{a['ad']}' ({a['campaign']}) — {a['leads']} CRM leads, "
                        f"{fmt_money(a['spend'])} spent, 0 SQL.")
    gems = [a for a in ads if a["leads"] >= 20 and a["cost_per_sql"]]
    for a in sorted(gems, key=lambda a: a["cost_per_sql"])[:3]:
        callouts.append(f"Best cost per SQL: ad '{a['ad']}' ({a['campaign']}) — {fmt_money(a['cost_per_sql'])} "
                        f"per SQL, SQL rate {a['sql_pct']:.0%} on {a['leads']} leads.")
    wasted = [c for c in campaigns if c["spend"] >= 25000 and not c["leads"]]
    if wasted:
        callouts.append(f"{len(wasted)} campaigns spent {fmt_money(sum(c['spend'] for c in wasted))} with no lead "
                        f"reaching the CRM under their name — check UTM tagging: "
                        + ", ".join(c["campaign"] for c in wasted[:4]) + ".")
    n = len(leads)
    matched = len(paid)
    meta_leads = [x for x in leads if x["source"] == "Facebook"]
    return {"configured": True, "mode": "crm", "window_start": first, "window_end": last,
            "window_days": (ddate(last) - ddate(first)).days + 1 if first else 0,
            "leads_in_window": n, "matched_leads": matched, "match_rate": matched / n if n else None,
            "adset_match_rate": sum(1 for x in paid if x["adset"]) / matched if matched else None,
            "ad_match_rate": (sum(1 for x in paid if x["ad"]) / sum(1 for x in paid if x["platform"] == "meta")
                              if any(x["platform"] == "meta" for x in paid) else None),
            "facebook_untagged": sum(1 for x in meta_leads if not x["matched"]),
            "bad_dates": bad_dates, "revenue_available": bool(fees),
            "fees_known": sorted(fees), "platforms": platforms, "products": products,
            "campaigns": campaigns[:150], "adsets": adsets[:250], "ads": ads[:300], "keywords": keywords[:60],
            "by_source": by_source, "callouts": callouts[:8],
            "unmatched_campaign_names": [k for k, _ in collections.Counter(
                x["campaign"] for x in leads if not x["matched"] and x["campaign"]).most_common(15)]}


def funnel(spend_by_product):
    rows = read_csv("funnel.csv")
    if rows is None:
        prev = read_json(os.path.join(REPORTS, "funnel.json"), {}).get("funnel", {})
        if prev.get("configured"):   # keep the last good funnel instead of blanking it (pipeline keeps the old report)
            fail("funnel sheet not downloaded today; keeping the previous funnel report", 7)
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
