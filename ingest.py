"""Verify raw pulls against account totals, merge into the rolling 11-day file, build campaign status/budget/goal.

Exits non-zero if any account/date does not reconcile (spend tolerance 0.5, Meta leads exact,
Google conversions tolerance 0.01 because Google reports fractional conversions)."""
import collections
import sys

from common import *

ROLLING = os.path.join(HIST, "rolling_campaign_days.json")
META_FILE = os.path.join(HIST, "campaign_meta.json")
WINDOW = 11


def verify(rows, totals, platform, spend_key, lead_key, lead_tol):
    agg = collections.defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        k = (r["account_name"], r["date"][:10])
        agg[k][0] += num(r.get(spend_key))
        agg[k][1] += num(r.get(lead_key))
    problems, checked = [], 0
    for t in totals:
        k = (t["account_name"], t["date"][:10])
        ts, tl = num(t.get(spend_key)), num(t.get(lead_key))
        cs, cl = agg.get(k, [0.0, 0.0])
        checked += 1
        if abs(ts - cs) > 0.5 or abs(tl - cl) > lead_tol:
            problems.append({"platform": platform, "account": k[0], "date": k[1],
                             "total_spend": round(ts, 2), "campaign_spend": round(cs, 2),
                             "total_leads": tl, "campaign_leads": cl})
    # campaign rows for an account/date with no totals row
    tk = {(t["account_name"], t["date"][:10]) for t in totals}
    for k, (s, l) in agg.items():
        if k not in tk and (s > 0.5 or l > lead_tol):
            problems.append({"platform": platform, "account": k[0], "date": k[1], "total_spend": 0,
                             "campaign_spend": round(s, 2), "total_leads": 0, "campaign_leads": l})
    return checked, problems


def main():
    meta_rows, meta_tot = raw("campaign_days.json"), raw("totals.json")
    g_rows, g_tot = raw("google_campaign_days.json"), raw("google_totals.json")
    if not meta_rows or not meta_tot:
        fail("raw/campaign_days.json or raw/totals.json missing/empty")

    c1, p1 = verify(meta_rows, meta_tot, "meta", "spend", "actions_lead", 0)
    c2, p2 = (0, [])
    if g_rows or g_tot:
        c2, p2 = verify(g_rows, g_tot, "google", "cost", "conversions", 0.01)
    problems = p1 + p2
    for p in problems:
        print("MISMATCH", p, file=sys.stderr)

    dates = sorted({r["date"][:10] for r in meta_tot})
    data_date = dates[-1]
    yesterday = dstr(ist_today() - dt.timedelta(days=1))
    g_dates = sorted({r["date"][:10] for r in g_tot})

    # ---- messaging / alternative metrics per Meta campaign-day
    alt = {}
    for r in raw("messaging.json"):
        k = ("meta", r["account_name"], r["campaign"], r["date"][:10])
        alt[k] = {"lpv": num(r.get("actions_landing_page_view")),
                  "pixel_leads": num(r.get("actions_offsite_conversion_fb_pixel_lead")),
                  "registrations": num(r.get("actions_offsite_conversion_fb_pixel_complete_registration")),
                  "conversations": num(r.get("actions_onsite_conversion_messaging_conversation_started_7d"))}

    new = []
    for r in meta_rows:
        k = ("meta", r["account_name"], r["campaign"], r["date"][:10])
        row = {"platform": "meta", "account": r["account_name"].strip(), "campaign": r["campaign"],
               "date": k[3], "spend": num(r.get("spend")), "leads": num(r.get("actions_lead")),
               "ctr": num(r.get("ctr")), "frequency": num(r.get("frequency"))}
        row.update(alt.get(k, {}))
        new.append(row)
    for r in g_rows:
        clicks, imps = num(r.get("clicks")), num(r.get("impressions"))
        new.append({"platform": "google", "account": r["account_name"].strip(), "campaign": r["campaign"],
                    "date": r["date"][:10], "spend": num(r.get("cost")), "leads": num(r.get("conversions")),
                    "clicks": clicks, "impressions": imps, "ctr": (100 * clicks / imps) if imps else 0.0,
                    "channel": r.get("advertising_channel_type"), "bidding": r.get("bidding_strategy_type"),
                    "status": r.get("campaign_status"), "budget": num(r.get("budget_amount"))})

    # ---- merge into rolling file (new pull replaces the dates it covers, per platform)
    old = read_json(ROLLING, [])
    covered = collections.defaultdict(set)
    for r in new:
        covered[r["platform"]].add(r["date"])
    merged = [r for r in old if r["date"] not in covered[r["platform"]]] + new
    cutoff = dstr(ddate(data_date) - dt.timedelta(days=WINDOW - 1))
    merged = [r for r in merged if r["date"] >= cutoff and r["date"] <= data_date]
    merged.sort(key=lambda r: (r["platform"], r["account"], r["campaign"], r["date"]))

    # ---- campaign meta: goal, lead/non-lead, status, budget, delivering
    meta = read_json(META_FILE, {})
    for v in meta.values():
        v["delivering"] = False
    sums = collections.defaultdict(lambda: collections.Counter())
    for r in merged:
        sums[(r["platform"], r["account"], r["campaign"])].update(
            {"pixel_leads": r.get("pixel_leads", 0), "leads": r["leads"]})

    goals = collections.defaultdict(collections.Counter)
    # 11-day goal pull (every ad set that spent) is the primary source; yesterday's config overlays it
    for r in raw("goals.json") + raw("config.json"):
        g = (r.get("adsset_optimization_goal") or "", r.get("adset_destination_type") or "")
        goals[(r["account_name"].strip(), r["campaign"])][g] += num(r.get("spend")) + 0.001
    for (acc, camp), cnt in goals.items():
        m = meta.setdefault(f"meta|{acc}|{camp}", {})
        (goal, dest), _ = cnt.most_common(1)[0]
        m.update({"goal": goal, "destination": dest, "goals_seen": sorted({g for g, _ in cnt}),
                  "goal_verified": dstr(ist_today())})

    budgets = collections.defaultdict(lambda: {"camp_budget": 0.0, "adset_budget": 0.0, "status": None,
                                               "active_adsets": 0, "adsets": 0})
    for r in raw("status.json"):
        b = budgets[(r["account_name"].strip(), r["campaign"])]
        b["status"] = r.get("campaign_status")
        b["adsets"] += 1
        if r.get("campaign_daily_budget"):
            b["camp_budget"] = num(r["campaign_daily_budget"]) / 100.0
        if (r.get("adset_effective_status") or "") == "ACTIVE":
            b["active_adsets"] += 1
            b["adset_budget"] += num(r.get("adset_daily_budget")) / 100.0
    for (acc, camp), b in budgets.items():
        m = meta.setdefault(f"meta|{acc}|{camp}", {})
        m.update({"status": b["status"], "daily_budget": b["camp_budget"] or b["adset_budget"] or None,
                  "active_adsets": b["active_adsets"], "delivering": True})

    last_google = {}
    for r in merged:
        if r["platform"] == "google":
            last_google[(r["account"], r["campaign"])] = r
    for (acc, camp), r in last_google.items():
        m = meta.setdefault(f"google|{acc}|{camp}", {})
        m.update({"status": r.get("status"), "daily_budget": r.get("budget") or None, "channel": r.get("channel"),
                  "goal": "CONVERSIONS", "delivering": r["date"] == (g_dates[-1] if g_dates else data_date)
                  and r["spend"] > 0})

    for r in merged:
        meta.setdefault(f"{r['platform']}|{r['account']}|{r['campaign']}", {})
    for key, m in meta.items():
        plat, acc, camp = key.split("|", 2)
        m["platform"], m["account"], m["campaign"], m["product"] = plat, acc, camp, product_of(camp)
        s = sums.get((plat, acc, camp), {})
        if plat == "google":
            m["is_lead"], m["metric"], m["metric_label"] = True, "leads", "conversion"
        else:
            m["is_lead"], m["metric"], m["metric_label"] = classify_goal(
                m.get("goal"), m.get("destination"), s.get("pixel_leads", 0), s.get("leads", 0), camp)
            if not m.get("delivering"):
                m["status"] = m.get("status") if m.get("status") in ("PAUSED",) else "NOT DELIVERING"
    # drop meta for campaigns that left the window
    live = {(r["platform"], r["account"], r["campaign"]) for r in merged}
    meta = {k: v for k, v in meta.items() if tuple(k.split("|", 2)) in live}

    write_json(ROLLING, merged)
    write_json(META_FILE, meta)
    report = {"data_date": data_date, "expected_date": yesterday, "fresh": data_date == yesterday,
              "google_data_date": g_dates[-1] if g_dates else None,
              "dates": sorted({r["date"] for r in merged}),
              "checked_account_days": {"meta": c1, "google": c2}, "problems": problems,
              "rows": len(merged), "campaigns": len(meta),
              "unknown_goal": [k for k, v in meta.items() if v.get("is_lead") is None]}
    write_json(os.path.join(REPORTS, "ingest.json"), report)
    print(f"ingest: meta {c1} account-days, google {c2} checked; {len(problems)} mismatches; "
          f"data date {data_date} (expected {yesterday}); {len(meta)} campaigns")
    if problems:
        fail("totals do not reconcile — not trusting any table", 3)


if __name__ == "__main__":
    main()
