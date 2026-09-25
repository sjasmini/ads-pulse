"""Combine reports into reports/report.json: summary, root cause, recommendations, quick answers.
Every recommendation is computed from this run's data files — nothing is hard-coded."""
import collections

from common import *


def load(name, default=None):
    return read_json(os.path.join(REPORTS, name), default)


def pct(x):
    return f"{x:.0%}" if x is not None else "–"


def meta_root_cause(prod_names, ad_days, dates4, snapshot, T):
    """Ad set / ad level breakdown for one Meta product over the 4-day window."""
    adsets = collections.defaultdict(lambda: [0.0, 0.0, "", ""])
    ads = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, "", "", ""])
    for r in ad_days:
        if r["date"][:10] not in dates4 or product_of(r["campaign"]) not in prod_names:
            continue
        a = adsets[str(r.get("adset_id"))]
        a[0] += num(r.get("spend")); a[1] += num(r.get("actions_lead"))
        a[2], a[3] = r.get("adset_name") or "", r["campaign"]
        d = ads[str(r.get("ad_id"))]
        d[0] += num(r.get("spend")); d[1] += num(r.get("actions_lead"))
        d[2] += num(r.get("impressions")); d[3] += num(r.get("actions_link_click"))
        d[4], d[5], d[6] = r.get("ad_name") or "", r.get("adset_name") or "", str(r.get("adset_id"))
    tot_s = sum(v[0] for v in adsets.values())
    tot_l = sum(v[1] for v in adsets.values())
    pcpl = cpl(tot_s, tot_l)
    ad_sets = [{"adset_id": k, "adset": v[2], "campaign": v[3], "spend": round(v[0], 2), "leads": v[1],
                "cpl": round(v[0] / v[1], 2) if v[1] else None, "share": v[0] / tot_s if tot_s else 0,
                "targeting": (snapshot.get(k) or {}).get("targeting")} for k, v in adsets.items() if v[0] > 0]
    ad_sets.sort(key=lambda x: -x["spend"])
    ad_list = [{"ad_id": k, "ad": v[4], "adset": v[5], "adset_id": v[6], "spend": round(v[0], 2), "leads": v[1],
                "cpl": round(v[0] / v[1], 2) if v[1] else None, "ctr": v[3] / v[2] if v[2] else None,
                "ff": v[1] / v[3] if v[3] else None} for k, v in ads.items() if v[0] > 0]
    ad_list.sort(key=lambda x: -x["spend"])

    def bad(x, ref):
        return (x["leads"] == 0 and x["spend"] >= T) or (ref and x["cpl"] and x["cpl"] > 1.3 * ref)
    losers = [a for a in ad_list if bad(a, pcpl) and a["spend"] >= 0.05 * tot_s]
    winners = sorted([a for a in ad_list if a["leads"] >= 5 and a["cpl"] and pcpl and a["cpl"] < pcpl],
                     key=lambda a: a["cpl"])
    worst_sets = [s for s in ad_sets if s["share"] >= 0.15 and bad(s, pcpl)]
    best_sets = sorted([s for s in ad_sets if s["leads"] >= 5 and s["cpl"]], key=lambda s: s["cpl"])
    return {"product_cpl": round(pcpl, 2) if pcpl else None, "ad_sets": ad_sets[:12], "ads": ad_list[:15],
            "losers": losers[:5], "winners": winners[:5], "worst_sets": worst_sets[:3], "best_sets": best_sets[:3]}


def actions_for(platform, prod, rc, placement_flags, T):
    acts = []
    if platform == "meta":
        for lo in rc.get("losers", []):
            same = [w for w in rc["winners"] if w["adset_id"] == lo["adset_id"]] or rc["winners"]
            if same:
                w = same[0]
                excess = lo["spend"] - (lo["leads"] * (rc["product_cpl"] or 0))
                acts.append({"kind": "pause_ad", "excess": excess,
                             "text": f"Pause ad '{lo['ad']}' ({fmt_money(lo['spend'])}, {int(lo['leads'])} leads, "
                                     f"CPL {fmt_money(lo['cpl'])}) in {lo['adset']} and move its budget to "
                                     f"'{w['ad']}' (CPL {fmt_money(w['cpl'])})."})
                break
        for f in placement_flags:
            if f["product"] == prod:
                acts.append({"kind": "placement", "excess": f["excess_spend"],
                             "text": f"Shift platform: {f['placement']} takes {pct(f['share'])} of spend at CPL "
                                     f"{fmt_money(f['cpl'])} vs {fmt_money(f['product_cpl'])} (gap is at the "
                                     f"{f['stage']} stage). {f['fix']}"})
                break
        for s in rc.get("worst_sets", []):
            t = s.get("targeting") or {}
            if t and not t.get("interests") and not t.get("job_titles") and not t.get("custom_audiences"):
                acts.append({"kind": "targeting", "excess": s["spend"] - s["leads"] * (rc["product_cpl"] or 0),
                             "text": f"Ad set '{s['adset']}' runs broad (no interests, job titles or custom "
                                     f"audiences) at CPL {fmt_money(s['cpl'])}. Add interest / job-title targeting."})
            else:
                best = rc["best_sets"][0] if rc.get("best_sets") else None
                acts.append({"kind": "segment", "excess": s["spend"] - s["leads"] * (rc["product_cpl"] or 0),
                             "text": f"Exclude the worst segment: cut ad set '{s['adset']}' ({pct(s['share'])} of "
                                     f"spend, CPL {fmt_money(s['cpl']) if s['cpl'] else 'no leads'})"
                                     + (f" and move budget to '{best['adset']}' (CPL {fmt_money(best['cpl'])})."
                                        if best and best["adset_id"] != s["adset_id"] else ".")})
            break
    else:
        camps = rc.get("campaigns", [])
        pc = rc.get("product_cpl")
        bad = [c for c in camps if c["share"] >= 0.05 and ((c["leads"] == 0 and c["spend"] >= T) or
                                                          (c["cpl"] and c["cpl"] > max(1.3 * (pc or 0), T)))]
        bad.sort(key=lambda c: -(c["spend"] - c["leads"] * T))
        good_all = sorted([c for c in camps if c["leads"] >= 5 and c["cpl"] and c["cpl"] <= T and c["share"] >= 0.03
                           and "traffic" not in c["campaign"].lower()], key=lambda c: c["cpl"])
        for b in bad[:2]:
            good = [g for g in good_all if g.get("channel") == b.get("channel")] or good_all
            acts.append({"kind": "google_shift", "excess": b["spend"] - b["leads"] * (pc or 0),
                         "text": f"Cut budget on '{b['campaign']}' (CPL {fmt_money(b['cpl']) if b['cpl'] else 'no conversions'}"
                                 f", {pct(b['share'])} of spend)"
                                 + (f" and move it to '{good[0]['campaign']}' (CPL {fmt_money(good[0]['cpl'])})."
                                    if good else "; no campaign in this product is under target yet — review search terms.")})
    if platform == "meta" and not acts and rc.get("ad_sets"):
        sets = [x for x in rc["ad_sets"] if x["spend"] > 0]
        worst = max(sets, key=lambda x: x["spend"] - x["leads"] * T)
        best = rc["best_sets"][0] if rc.get("best_sets") else None
        if worst["spend"] - worst["leads"] * T > 0:
            acts.append({"kind": "segment", "excess": worst["spend"] - worst["leads"] * T,
                         "text": f"Cut ad set '{worst['adset']}' (CPL {fmt_money(worst['cpl']) if worst['cpl'] else 'no leads'}, "
                                 f"{pct(worst['share'])} of spend)"
                                 + (f" and move budget to '{best['adset']}' (CPL {fmt_money(best['cpl'])})."
                                    if best and best["adset_id"] != worst["adset_id"] else
                                    "; no ad set here is converting well yet — refresh the creative and form.")})
    acts.sort(key=lambda a: -(a["excess"] or 0))
    return acts[:2]


def main():
    ing, full = load("ingest.json"), load("full.json")
    plc = load("placement.json", {"products": [], "flags": []})
    chg = load("changes.json", {"recent": [], "learnings": [], "course_corrections": [], "recent_count": 0})
    fun = load("funnel.json", {"funnel": {"configured": False, "products": []}, "creative": {}})
    ad_days = raw("ad_days.json")
    snaps = sorted(os.listdir(os.path.join(HIST, "snapshots")))
    snapshot = read_json(os.path.join(HIST, "snapshots", snaps[-1]), {}) if snaps else {}
    dates4 = set(full["dates4"])

    urgent, watch, scale, root = [], [], [], []
    for a in full["accounts"]:
        T = a["threshold"]
        for p in a["products"]:
            if a["platform"] == "meta":
                rc = meta_root_cause({p["product"]}, [r for r in ad_days if r["account_name"].strip() == a["account"]],
                                     dates4, snapshot, T)
            else:
                camps = [c for c in a["campaigns"] if c["product"] == p["product"]]
                tot = sum(c["spend4"] for c in camps) or 1
                rc = {"product_cpl": p["cpl4"], "campaigns": [
                    {"campaign": c["campaign"], "spend": c["spend4"], "leads": c["leads4"], "cpl": c["cpl4"],
                     "channel": c.get("channel"),
                     "share": c["spend4"] / tot} for c in sorted(camps, key=lambda c: -c["spend4"]) if c["spend4"] > 0]}
            p["root_cause"] = rc
            if p["severity"] in ("critical", "watch"):
                acts = actions_for(a["platform"], p["product"], rc, plc["flags"] if a["platform"] == "meta" else [], T)
                p["actions"] = acts
                item = {"platform": a["platform"], "account": a["account"], "product": p["product"],
                        "severity": p["severity"], "cpl4": p["cpl4"], "spend4": p["spend4"], "leads4": p["leads4"],
                        "threshold": T, "actions": [x["text"] for x in acts],
                        "excess": sum(max(x["excess"] or 0, 0) for x in acts)}
                (urgent if p["severity"] == "critical" else watch).append(item)
                root.append({**item, "root_cause": rc})
            elif p["cpl4"] and p["cpl4"] < 0.6 * T and p["leads4"] >= 20:
                scale.append({"platform": a["platform"], "account": a["account"], "product": p["product"],
                              "cpl4": p["cpl4"], "leads4": p["leads4"], "threshold": T,
                              "text": f"{p['product']} ({a['account']}) is at {fmt_money(p['cpl4'])} CPL on "
                                      f"{int(p['leads4'])} leads — room to scale budget ~20%."})
    for x in urgent + watch:
        x["over_target"] = round(x["spend4"] - x["leads4"] * x["threshold"], 2)
    urgent.sort(key=lambda x: -x["over_target"])
    watch.sort(key=lambda x: -x["spend4"])
    scale.sort(key=lambda x: x["cpl4"] / x["threshold"])

    plat_tot = {}
    for plat in ("meta", "google"):
        acc = [a for a in full["accounts"] if a["platform"] == plat]
        s, l = sum(a["spend4"] for a in acc), sum(a["leads4"] for a in acc)
        plat_tot[plat] = {"spend4": round(s, 2), "leads4": l, "cpl4": round(s / l, 2) if l else None,
                          "threshold": threshold(plat),
                          "critical": sum(1 for a in acc for p in a["products"] if p["severity"] == "critical"),
                          "watch": sum(1 for a in acc for p in a["products"] if p["severity"] == "watch")}

    # weekly buckets (campaign level)
    weekly = collections.defaultdict(list)
    for a in full["accounts"]:
        for c in a["campaigns"]:
            label = f"{c['campaign']} ({a['account']})"
            if not c["delivering"] and c["spend11"] > 0:
                weekly["paused / not delivering"].append(label)
            elif c["trend"] in ("consistently above", "getting worse", "improving"):
                weekly[c["trend"]].append(label)

    # ---- quick answers for the Ask box (used when Claude is not available to the viewer)
    qa = []
    fp = {p["product"]: p for p in fun["funnel"].get("products", [])}
    for a in full["accounts"]:
        for p in a["products"]:
            prod = p["product"]
            pf = [f for f in plc["flags"] if f["product"] == prod]
            ch = [e for e in chg["recent"] if product_of(e["campaign"]) == prod and e["account"] == a["account"]]
            lines = [f"{prod} on {a['platform'].title()} ({a['account']}): 4-day CPL {fmt_money(p['cpl4'])} "
                     f"({fmt_money(p['spend4'])} spend, {int(p['leads4'])} leads) — {p['severity']}, "
                     f"threshold {fmt_money(a['threshold'])}."]
            if p.get("actions"):
                lines.append("Fix: " + " ".join(x["text"] for x in p["actions"]))
            if pf:
                lines.append(f"Placement issue: {pf[0]['placement']} CPL {fmt_money(pf[0]['cpl'])} vs "
                             f"{fmt_money(pf[0]['product_cpl'])}, {pf[0]['stage']} stage.")
            if prod in fp:
                f = fp[prod]
                lines.append(f"Funnel ({fun['funnel'].get('window_days', '')} days): {int(f['sql'])} SQLs, {int(f['sales'])} enrolled, "
                             f"cost per SQL {fmt_money(f.get('cost_per_sql'))}"
                             + (f", ROI {f['roi']}x" if f.get('roi') is not None else "") + f" — {f['verdict']}.")
            if ch:
                lines.append(f"Recent changes: {len(ch)} (latest {ch[-1]['date']}: {ch[-1]['type']} — {ch[-1]['detail'][:80]}).")
            qa.append({"keys": [prod.lower(), a["account"].lower()] + prod.lower().replace("(", " ").replace(")", " ").split(),
                       "q": f"How is {prod} doing?", "a": " ".join(lines)})
    fix_first = urgent[:3]
    qa += [
        {"keys": ["fix first", "priority", "urgent", "what should", "first"], "q": "What should we fix first?",
         "a": " ".join(f"{i + 1}. {u['product']} ({u['platform'].title()}, {u['account']}): CPL {fmt_money(u['cpl4'])}. "
                       + (u["actions"][0] if u["actions"] else "") for i, u in enumerate(fix_first)) or "Nothing critical today."},
        {"keys": ["wast", "burn", "excess", "budget"], "q": "Where are we wasting budget?",
         "a": " ".join(f"{f['product']} on {f['placement']}: {fmt_money(f['excess_spend'])} above what the product's "
                       f"CPL would have bought." for f in plc["flags"][:4]) or "No placement is wasting budget this week."},
        {"keys": ["placement", "instagram", "facebook", "reels", "stories", "feed"], "q": "Which placements are a problem?",
         "a": " ".join(f"{f['product']}: {f['placement']} ({pct(f['share'])} of spend) CPL {fmt_money(f['cpl'])} vs "
                       f"{fmt_money(f['product_cpl'])} — {f['stage']} stage." for f in plc["flags"][:5]) or "No flagged placements."},
        {"keys": ["chang", "edit", "what changed", "new ad"], "q": "What changed?",
         "a": f"{chg['recent_count']} changes in the last 7 days. "
              + " ".join(f"{c['date']} {c['adset_name']}: {c['type']} ({c['detail'][:60]}) — {c['problem']}."
                         for c in chg["course_corrections"][:3])},
        {"keys": ["money", "roi", "revenue", "profit", "make money"], "q": "Which products make money?",
         "a": (" ".join(f"{p['product']}: {p['sales']} enrolled, cost per enrolment {fmt_money(p.get('cost_per_sale'))}"
                        + (f", ROI {p['roi']}x" if p.get('roi') is not None else "") + f" ({p['verdict']})."
                        for p in fun["funnel"]["products"][:6])
               if fun["funnel"]["configured"] else "The funnel sheet isn't connected yet, so revenue/ROI isn't available.")},
        {"keys": ["copy", "creative", "angle", "hook", "works"], "q": "Which copy works?",
         "a": " ".join(fun["creative"].get("callouts", [])) or "Not enough data."},
    ]

    report = {"data_date": full["data_date"], "dates4": full["dates4"], "dates11": full["dates11"],
              "generated_ist": str(ist_today()), "thresholds": THRESHOLDS, "currency": CUR,
              "platform_totals": plat_tot, "accounts": full["accounts"], "non_lead": full["non_lead"],
              "urgent": urgent, "watch": watch, "scale": scale[:6], "root_cause": root,
              "weekly": {k: v for k, v in weekly.items()},
              "placement": plc, "changes": chg, "funnel": fun["funnel"], "creative": fun["creative"],
              "quick_answers": qa, "verification": {"problems": ing["problems"], "checked": ing["checked_account_days"],
                                                    "fresh": ing["fresh"]},
              "dashboard_url": SETTINGS.get("dashboard_url", "")}
    write_json(os.path.join(REPORTS, "report.json"), report)
    print(f"build_report: {len(urgent)} urgent, {len(watch)} watch, {len(scale)} scale-ups, {len(qa)} quick answers")


if __name__ == "__main__":
    main()
