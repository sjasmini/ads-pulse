"""4-day product table + 11-day campaign trend with status/budget, severity, non-lead card -> reports/full.json"""
import collections

from common import *


def day_above(spend, leads, T):
    return spend > 0 and ((leads > 0 and spend / leads > T) or (leads == 0 and spend >= T))


def severity(days, T):
    """days: list of (spend, leads) for the window. critical if window CPL > 1.3x threshold or above threshold
    on at least (window days - 1) days; watch if at least 1 day above; else healthy."""
    spend = sum(s for s, _ in days)
    leads = sum(l for _, l in days)
    active = [d for d in days if d[0] > 0]
    above = sum(1 for s, l in active if day_above(s, l, T))
    c = cpl(spend, leads)
    if spend <= 0:
        return "inactive", above
    if (c is not None and c > 1.3 * T) or (leads == 0 and spend >= 1.3 * T) or \
            (above >= len(days) - 1):
        return "critical", above
    if above >= 1:
        return "watch", above
    return "healthy", above


def trend(days11, T):
    act = [(s, l) for s, l in days11 if s > 0]
    first, last = days11[:4], days11[-4:]
    c1 = cpl(sum(s for s, _ in first), sum(l for _, l in first))
    c2 = cpl(sum(s for s, _ in last), sum(l for _, l in last))
    above = sum(1 for s, l in act if day_above(s, l, T))
    if len(act) >= 6 and above >= len(act) - 2:
        return "consistently above"
    if c1 and c2 and c2 > c1 * 1.2 and c2 > T:
        return "getting worse"
    if c1 and c2 and c2 < c1 * 0.8:
        return "improving"
    if c1 and not c2 and sum(s for s, _ in last) >= T:
        return "getting worse"
    return "stable"


def main():
    rows = read_json(os.path.join(HIST, "rolling_campaign_days.json"), [])
    meta = read_json(os.path.join(HIST, "campaign_meta.json"), {})
    ing = read_json(os.path.join(REPORTS, "ingest.json"), {})
    data_date = ing["data_date"]
    D = ddate(data_date)
    d11 = [dstr(D - dt.timedelta(days=i)) for i in range(10, -1, -1)]
    d4 = d11[-4:]

    cd = collections.defaultdict(dict)   # (plat, acc, camp) -> date -> row
    for r in rows:
        cd[(r["platform"], r["account"], r["campaign"])][r["date"]] = r

    accounts = collections.defaultdict(lambda: {"products": collections.defaultdict(list), "campaigns": []})
    non_lead = []
    for key, days in cd.items():
        plat, acc, camp = key
        m = meta.get("|".join(key), {})
        T = threshold(plat)
        seq11 = [(num(days.get(d, {}).get("spend")), num(days.get(d, {}).get("leads"))) for d in d11]
        spend11 = sum(s for s, _ in seq11)
        if spend11 <= 0:
            continue
        is_lead = m.get("is_lead")
        if is_lead is None:
            is_lead = sum(l for _, l in seq11) > 0
        status = m.get("status") or "?"
        delivering = bool(m.get("delivering"))
        if not is_lead:
            mk = m.get("metric", "lpv")
            def metric_of(r):
                if mk == "impressions_k":
                    return num(r.get("impressions")) / 1000.0
                return num(r.get(mk))
            m11 = sum(metric_of(days.get(d, {})) for d in d11)
            m4 = sum(metric_of(days.get(d, {})) for d in d4)
            s4 = sum(s for s, _ in seq11[-4:])
            non_lead.append({"platform": plat, "account": acc, "campaign": camp, "product": product_of(camp),
                             "goal": m.get("goal"), "destination": m.get("destination"),
                             "metric_label": m.get("metric_label"), "metric_key": mk,
                             "spend11": round(spend11, 2), "metric11": m11,
                             "cost_per11": round(spend11 / m11, 2) if m11 else None,
                             "spend4": round(s4, 2), "metric4": m4, "cost_per4": round(s4 / m4, 2) if m4 else None,
                             "form_leads11": sum(l for _, l in seq11),
                             "status": status, "delivering": delivering,
                             "daily_budget": m.get("daily_budget")})
            continue
        sev, above4 = severity(seq11[-4:], T)
        c = {"campaign": camp, "product": m.get("product") or product_of(camp), "status": status,
             "delivering": delivering, "daily_budget": m.get("daily_budget"), "goal": m.get("goal"),
             "channel": m.get("channel"),
             "days": [{"date": d, "spend": round(s, 2), "leads": l, "cpl": round(s / l, 2) if l else None}
                      for d, (s, l) in zip(d11, seq11)],
             "spend11": round(spend11, 2), "leads11": sum(l for _, l in seq11),
             "cpl11": round(cpl(spend11, sum(l for _, l in seq11)) or 0, 2) or None,
             "spend4": round(sum(s for s, _ in seq11[-4:]), 2), "leads4": sum(l for _, l in seq11[-4:]),
             "severity": sev, "days_above4": above4, "trend": trend(seq11, T),
             "days_above11": sum(1 for s, l in seq11 if day_above(s, l, T))}
        c["cpl4"] = round(c["spend4"] / c["leads4"], 2) if c["leads4"] else None
        A = accounts[(plat, acc)]
        A["campaigns"].append(c)
        A["products"][c["product"]].append((c, seq11))

    out_accounts = []
    for (plat, acc), A in accounts.items():
        T = threshold(plat)
        prods = []
        for prod, items in A["products"].items():
            seq = [(sum(s[i][0] for _, s in items), sum(s[i][1] for _, s in items)) for i in range(11)]
            s4 = sum(s for s, _ in seq[-4:])
            l4 = sum(l for _, l in seq[-4:])
            if s4 <= 0 and all(not c["delivering"] for c, _ in items):
                continue
            sev, above = severity(seq[-4:], T)
            prods.append({"product": prod, "spend4": round(s4, 2), "leads4": l4,
                          "cpl4": round(s4 / l4, 2) if l4 else None, "severity": sev, "days_above": above,
                          "days": [{"date": d, "spend": round(s, 2), "leads": l, "cpl": round(s / l, 2) if l else None}
                                   for d, (s, l) in zip(d4, seq[-4:])],
                          "campaigns": sorted(c["campaign"] for c, _ in items),
                          "trend": trend(seq, T)})
        prods.sort(key=lambda p: -p["spend4"])
        camps = sorted(A["campaigns"], key=lambda c: -c["spend11"])
        s4 = sum(p["spend4"] for p in prods)
        l4 = sum(p["leads4"] for p in prods)
        out_accounts.append({"platform": plat, "account": acc, "threshold": T, "spend4": round(s4, 2),
                             "leads4": l4, "cpl4": round(s4 / l4, 2) if l4 else None,
                             "products": prods, "campaigns": camps})
    out_accounts.sort(key=lambda a: (a["platform"] != "meta", -a["spend4"]))
    non_lead.sort(key=lambda x: -x["spend11"])

    sev_count = collections.Counter(p["severity"] for a in out_accounts for p in a["products"])
    write_json(os.path.join(REPORTS, "full.json"), {
        "data_date": data_date, "dates4": d4, "dates11": d11, "thresholds": THRESHOLDS,
        "accounts": out_accounts, "non_lead": non_lead, "severity_counts": dict(sev_count)})
    print(f"process_full: {len(out_accounts)} accounts, "
          f"{sum(len(a['products']) for a in out_accounts)} product rows {dict(sev_count)}, {len(non_lead)} non-lead")


if __name__ == "__main__":
    main()
