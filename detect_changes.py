"""Automated change detection + learning (Meta).

1. Normalise raw/config.json -> history/snapshots/<date>.json; diff with the previous snapshot.
2. From raw/ad_days.json detect ads that started / stopped delivering (grouped per ad set per day).
3. First run only: back-fill ad set edits from adset_updated_time.
4. Score every change on its ad set: CPL 3 days before vs 3 days after (change day excluded), net of the
   rest of the account's CPL move over the same days. helped <= -10%, hurt >= +10%, needs 5+ leads per window.
5. Keep history/change_ledger.json, aggregate learnings per change type, list course-corrections."""
import collections
import glob

from common import *

SNAPDIR = os.path.join(HIST, "snapshots")
LEDGER = os.path.join(HIST, "change_ledger.json")
WIN, MIN_LEADS, HELP, HURT = 3, 5, -0.10, 0.10


def _names(lst):
    return sorted({(x.get("name") or str(x.get("id"))) for x in (lst or []) if isinstance(x, dict)})


def norm_targeting(t):
    if isinstance(t, str):
        try:
            t = json.loads(t)
        except ValueError:
            t = {}
    t = t or {}
    geo = t.get("geo_locations") or {}
    xgeo = t.get("excluded_geo_locations") or {}

    def geo_list(g):
        out = list(g.get("countries") or [])
        for k in ("regions", "cities", "zips", "places", "custom_locations", "geo_markets", "subcities"):
            out += [f"{k[:-1] if k.endswith('s') else k}:{n}" for n in _names(g.get(k))]
        return sorted(out)

    interests, jobs, other, excl_flex = set(), set(), set(), set()
    for spec in t.get("flexible_spec") or []:
        for k, v in spec.items():
            names = _names(v)
            if k in ("interests", "behaviors"):
                interests.update(names)
            elif k in ("work_positions", "work_employers"):
                jobs.update(names)
            else:
                other.update(f"{k}:{n}" for n in names)
    for k, v in (t.get("exclusions") or {}).items():
        excl_flex.update(f"{k}:{n}" for n in _names(v))
    placements = sorted(set(t.get("publisher_platforms") or []) |
                        {f"{p.split('_')[0]}:{x}" for p in ("facebook_positions", "instagram_positions",
                                                           "messenger_positions", "audience_network_positions",
                                                           "threads_positions", "whatsapp_positions")
                         for x in (t.get(p) or [])})
    return {
        "age": [t.get("age_min"), t.get("age_max")],
        "gender": sorted(t.get("genders") or []) or ["all"],
        "geo": geo_list(geo),
        "interests": sorted(interests),
        "job_titles": sorted(jobs),
        "other_detailed": sorted(other),
        "custom_audiences": _names(t.get("custom_audiences")),
        "exclusions": sorted(set(_names(t.get("excluded_custom_audiences"))) | set(excl_flex) |
                             {"geo:" + g for g in geo_list(xgeo)}),
        "placements": placements or ["advantage+ placements"],
        "advantage_plus": bool((t.get("targeting_automation") or {}).get("advantage_audience")),
    }


def build_snapshot():
    snap = {}
    for r in raw("config.json"):
        aid = r.get("adset_id")
        if not aid:
            continue
        s = snap.get(aid)
        if s is None:
            s = snap[aid] = {
                "account": r["account_name"].strip(), "campaign": r["campaign"], "adset_name": r.get("adset_name"),
                "status": r.get("adset_effective_status"),
                "budget": num(r.get("adset_daily_budget")) / 100.0 if r.get("adset_daily_budget") else None,
                "bid_strategy": r.get("adset_bid_strategy"),
                "bid_amount": num(r.get("adset_bid_amount")) / 100.0 if r.get("adset_bid_amount") else None,
                "goal": r.get("adsset_optimization_goal"), "destination": r.get("adset_destination_type"),
                "targeting": norm_targeting(r.get("adset_targeting")),
                "updated_time": r.get("adset_updated_time"), "ads": {}}
        s["ads"][str(r.get("ad_id"))] = {"name": r.get("ad_name"), "status": r.get("status"),
                                         "created": r.get("ad_created_time")}
    return snap


def diff(prev, cur, change_date_for):
    out = []
    for aid, c in cur.items():
        p = prev.get(aid)
        base = {"adset_id": aid, "account": c["account"], "campaign": c["campaign"],
                "adset_name": c["adset_name"], "source": "snapshot"}
        d = change_date_for(c)
        if p is None:
            continue   # new ad sets show up via ad_days "started delivering"
        if (p.get("budget") or 0) != (c.get("budget") or 0) and p.get("budget") and c.get("budget"):
            t = "budget up" if c["budget"] > p["budget"] else "budget down"
            out.append({**base, "date": d, "type": t, "detail": f"{fmt_money(p['budget'])} → {fmt_money(c['budget'])}/day",
                        "pct": round(c["budget"] / p["budget"] - 1, 3)})
        if p.get("bid_strategy") != c.get("bid_strategy") or p.get("bid_amount") != c.get("bid_amount"):
            out.append({**base, "date": d, "type": "bid",
                        "detail": f"{p.get('bid_strategy')} {p.get('bid_amount') or ''} → {c.get('bid_strategy')} {c.get('bid_amount') or ''}"})
        if p.get("goal") != c.get("goal"):
            out.append({**base, "date": d, "type": "optimisation goal", "detail": f"{p.get('goal')} → {c.get('goal')}"})
        pt, ct = p.get("targeting", {}), c.get("targeting", {})
        for k, label in (("age", "age"), ("gender", "gender"), ("geo", "geo"), ("interests", "interests"),
                         ("job_titles", "job titles"), ("custom_audiences", "custom audiences"),
                         ("exclusions", "exclusions"), ("placements", "placements"),
                         ("advantage_plus", "Advantage+ audience"), ("other_detailed", "detailed targeting")):
            if pt.get(k) != ct.get(k):
                if isinstance(ct.get(k), list) and k != "age":
                    added = sorted(set(ct.get(k) or []) - set(pt.get(k) or []))
                    removed = sorted(set(pt.get(k) or []) - set(ct.get(k) or []))
                    det = "; ".join(x for x in (("+ " + ", ".join(added[:6])) if added else "",
                                                ("− " + ", ".join(removed[:6])) if removed else "") if x)
                else:
                    det = f"{pt.get(k)} → {ct.get(k)}"
                out.append({**base, "date": d, "type": label, "detail": det})
    return out


def main():
    ing = read_json(os.path.join(REPORTS, "ingest.json"), {})
    D = ddate(ing["data_date"])
    today = D + dt.timedelta(days=1)
    T = threshold("meta")

    cur = build_snapshot()
    if not cur:
        fail("raw/config.json empty — cannot snapshot")
    snaps = sorted(glob.glob(os.path.join(SNAPDIR, "*.json")))
    snaps = [s for s in snaps if os.path.basename(s)[:10] != dstr(today)]
    prev = read_json(snaps[-1], {}) if snaps else None
    prev_date = ddate(os.path.basename(snaps[-1])[:10]) if snaps else None
    write_json(os.path.join(SNAPDIR, f"{dstr(today)}.json"), cur)

    def change_date_for(c):
        u = c.get("updated_time")
        if u and prev_date:
            ud = ddate(u)
            if prev_date <= ud <= today:
                return dstr(ud)
        return dstr(today)

    ledger = read_json(LEDGER, [])
    known = {e["id"] for e in ledger}
    found = []
    if prev is not None:
        found += diff(prev, cur, change_date_for)

    # ---- ad-level: started / stopped delivering, from ad_days
    ad_days = raw("ad_days.json")
    all_dates = sorted({r["date"][:10] for r in ad_days})
    w_start = all_dates[0] if all_dates else dstr(D)
    per_ad = collections.defaultdict(dict)
    info = {}
    adset_day = collections.defaultdict(lambda: [0.0, 0.0])
    acct_day = collections.defaultdict(lambda: [0.0, 0.0])
    adset_acct = {}
    for r in ad_days:
        acc, aid, adid, d = r["account_name"].strip(), str(r.get("adset_id")), str(r.get("ad_id")), r["date"][:10]
        s, l = num(r.get("spend")), num(r.get("actions_lead"))
        per_ad[(aid, adid)][d] = (s, l)
        info[(aid, adid)] = (acc, r["campaign"], r.get("ad_name"))
        adset_day[(aid, d)][0] += s; adset_day[(aid, d)][1] += l
        acct_day[(acc, d)][0] += s; acct_day[(acc, d)][1] += l
        adset_acct[aid] = (acc, r["campaign"])
    # A start/stop only counts when it is material, so normal day-to-day delivery gaps are not "changes":
    #  start = no spend on the first 3 days of the window, then >= 5% of the ad set's spend since it started
    #  stop  = >= 10% of the ad set's spend in the 3 days before, then zero spend every day after
    starts, stops = collections.defaultdict(list), collections.defaultdict(list)
    first_ok = dstr(ddate(w_start) + dt.timedelta(days=3))
    for (aid, adid), days in per_ad.items():
        ds = sorted(days)
        if ds[0] >= first_ok:
            own = sum(days[d][0] for d in ds)
            tot = sum(v[0] for (a, d), v in adset_day.items() if a == aid and d >= ds[0])
            if tot and own / tot >= 0.05:
                starts[(aid, ds[0])].append(adid)
        if ds[-1] < dstr(D):
            prior = [dstr(ddate(ds[-1]) - dt.timedelta(days=i)) for i in range(3)]
            own = sum(days.get(d, (0, 0))[0] for d in prior)
            tot = sum(adset_day.get((aid, d), (0, 0))[0] for d in prior)
            if tot and own / tot >= 0.10:
                stops[(aid, dstr(ddate(ds[-1]) + dt.timedelta(days=1)))].append(adid)
    names = {aid: s["adset_name"] for aid, s in cur.items()}
    for r in ad_days:
        names.setdefault(str(r.get("adset_id")), r.get("adset_name") or str(r.get("adset_id")))
    for (aid, d), ads in starts.items():
        acc, camp, _ = info[(aid, ads[0])]
        found.append({"adset_id": aid, "account": acc, "campaign": camp, "adset_name": names.get(aid, aid),
                      "date": d, "type": "new ad", "source": "ad_days", "ads": ads,
                      "detail": f"{len(ads)} ad(s) started: " + ", ".join(str(info[(aid, a)][2]) for a in ads[:4])})
    for (aid, d), ads in stops.items():
        acc, camp, _ = info[(aid, ads[0])]
        found.append({"adset_id": aid, "account": acc, "campaign": camp, "adset_name": names.get(aid, aid),
                      "date": d, "type": "ad stopped", "source": "ad_days", "ads": ads,
                      "detail": f"{len(ads)} ad(s) stopped: " + ", ".join(str(info[(aid, a)][2]) for a in ads[:4])})

    # ---- first run: back-fill ad set edits from adset_updated_time
    if prev is None:
        for aid, c in cur.items():
            u = c.get("updated_time")
            if u and w_start <= u[:10] <= dstr(today):
                found.append({"adset_id": aid, "account": c["account"], "campaign": c["campaign"],
                              "adset_name": c["adset_name"], "date": u[:10], "type": "ad set edited",
                              "source": "backfill", "detail": "edited (details not captured before first snapshot)"})

    for f in found:
        f["id"] = f"{f['adset_id']}|{f['type']}|{f['date']}|{f.get('detail', '')[:60]}"
        if f["id"] not in known:
            f["status"] = "pending"
            ledger.append(f)
            known.add(f["id"])

    # ---- score (re-score anything not final while its windows are inside ad_days)
    def window(key_fn, days):
        s = l = 0.0
        for d in days:
            v = key_fn(d)
            s += v[0]; l += v[1]
        return s, l

    for e in ledger:
        if e.get("status") in ("scored", "not enough data") and e.get("final"):
            continue
        c = ddate(e["date"])
        before = [dstr(c - dt.timedelta(days=i)) for i in range(WIN, 0, -1)]
        after = [dstr(c + dt.timedelta(days=i)) for i in range(1, WIN + 1)]
        if after[-1] > dstr(D):
            e["status"] = "pending"
            continue
        if before[0] < w_start:
            e.update(status="not enough data", reason="before-window older than available data", final=True)
            continue
        aid = e["adset_id"]
        acc = e["account"]
        sb, lb = window(lambda d: adset_day.get((aid, d), (0, 0)), before)
        sa, la = window(lambda d: adset_day.get((aid, d), (0, 0)), after)
        ab, alb = window(lambda d: acct_day.get((acc, d), (0, 0)), before)
        aa, ala = window(lambda d: acct_day.get((acc, d), (0, 0)), after)
        rb, rlb, ra, rla = ab - sb, alb - lb, aa - sa, ala - la
        e.update(spend_before=round(sb, 2), leads_before=lb, spend_after=round(sa, 2), leads_after=la,
                 cpl_before=round(sb / lb, 2) if lb else None, cpl_after=round(sa / la, 2) if la else None)
        if e["type"] == "new ad":
            ns = nl = 0.0
            for a in e.get("ads", []):
                for d, (s, l) in per_ad.get((aid, a), {}).items():
                    if d >= e["date"]:
                        ns += s; nl += l
            e["new_ad_spend"], e["new_ad_leads"] = round(ns, 2), nl
            e["new_ad_cpl"] = round(ns / nl, 2) if nl else None
        if lb < MIN_LEADS or la < MIN_LEADS or rlb <= 0 or rla <= 0:
            e.update(status="not enough data", reason=f"needs {MIN_LEADS}+ leads per window", final=True)
            continue
        move = (sa / la) / (sb / lb) - 1
        rest = (ra / rla) / (rb / rlb) - 1
        net = move - rest
        e.update(status="scored", final=True, move=round(move, 3), account_move=round(rest, 3), net=round(net, 3),
                 verdict="helped" if net <= HELP else "hurt" if net >= HURT else "neutral")

    ledger.sort(key=lambda e: (e["date"], e["account"], e["adset_name"] or ""))
    write_json(LEDGER, ledger)

    # ---- learnings per change type
    by_type = collections.defaultdict(list)
    for e in ledger:
        if e.get("status") == "scored":
            by_type[e["type"]].append(e)
    learnings = []
    for t, es in sorted(by_type.items()):
        cnt = collections.Counter(e["verdict"] for e in es)
        med = median([e["net"] for e in es])
        n = len(es)
        advice = None
        if n >= 3:
            if cnt["helped"] > n / 2 and med <= HELP:
                advice = f"{t} has tended to help (net CPL {med:+.0%} median over {n} changes)."
            elif cnt["hurt"] > n / 2 and med >= HURT:
                advice = f"{t} has tended to hurt (net CPL {med:+.0%} median over {n} changes) — be cautious."
        learnings.append({"type": t, "n": n, "helped": cnt["helped"], "hurt": cnt["hurt"],
                          "neutral": cnt["neutral"], "median_net": med, "advice": advice})

    # ---- course-corrections (hurt AND actually a problem)
    corrections = []
    for e in ledger:
        if e.get("status") != "scored":
            continue
        prob = None
        if e["verdict"] == "hurt" and (e.get("cpl_after") or 0) > T:
            prob = f"CPL after {fmt_money(e['cpl_after'])} is above the {fmt_money(T)} threshold"
        if e["type"] == "new ad" and e.get("new_ad_cpl") and e.get("cpl_before") and \
                e["new_ad_cpl"] > 1.3 * e["cpl_before"]:
            prob = f"new ad CPL {fmt_money(e['new_ad_cpl'])} vs ad set's prior {fmt_money(e['cpl_before'])}"
        if e["verdict"] == "hurt" and prob:
            action = {"budget up": "Step the budget back down to the previous level",
                      "budget down": "Restore the previous budget",
                      "new ad": "Pause the new ad(s)",
                      "ad stopped": "Re-activate the stopped ad(s) that were carrying the ad set"}.get(
                e["type"], f"Revert the {e['type']} change")
            corrections.append({**{k: e.get(k) for k in ("date", "account", "campaign", "adset_name", "type",
                                                           "detail", "cpl_before", "cpl_after", "net")},
                                "problem": prob, "action": action})
    corrections.sort(key=lambda c: -(c.get("net") or 0))

    recent_cut = dstr(D - dt.timedelta(days=6))
    recent = [e for e in ledger if e["date"] >= recent_cut]
    write_json(os.path.join(REPORTS, "changes.json"), {
        "snapshot": dstr(today), "previous_snapshot": dstr(prev_date) if prev_date else None,
        "first_run": prev is None, "recent": recent[-300:], "recent_count": len(recent),
        "new_today": len([f for f in found if f["id"] in {x['id'] for x in recent}]),
        "status_counts": dict(collections.Counter(e["status"] for e in ledger)),
        "learnings": learnings, "course_corrections": corrections})
    print(f"detect_changes: {len(ledger)} ledger entries ({len(recent)} in last 7 days), "
          f"{len(learnings)} change types scored, {len(corrections)} course-corrections"
          + (" [first run: back-filled from adset_updated_time]" if prev is None else ""))


if __name__ == "__main__":
    main()
