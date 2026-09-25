"""Per product x placement (Meta, last 7 days, lead campaigns): spend share, CPM, CTR, form-fill, CPL.

CPL = CPM / (1000 x CTR x FF), so ln(CPL_p / CPL_rest) = ln(CPM ratio) - ln(CTR ratio) - ln(FF ratio).
Each flagged placement's gap vs the rest of the product is split into those three stages."""
import collections
import math

from common import *

FIX = {
    "CPM": "Inventory is expensive here. Exclude or cap this placement, or give it a format made for it "
           "(9:16 video for Stories/Reels) so it wins auctions cheaper.",
    "CTR": "People scroll past the ad here. Add a placement-specific creative (vertical, hook in the first 2s, "
           "text-safe zones) or remove the placement.",
    "Form fill": "Clicks here don't turn into leads (accidental/low-intent taps). Exclude the placement or "
                 "switch to a Higher-intent form (extra qualifying question / review screen).",
    "No leads": "Spent without a single lead. Exclude the placement from this product's ad sets.",
}


def stats(s, i, c, l):
    return {"spend": round(s, 2), "impressions": i, "clicks": c, "leads": l,
            "cpm": round(1000 * s / i, 2) if i else None, "ctr": (c / i) if i else None,
            "ff": (l / c) if c else None, "cpl": round(s / l, 2) if l else None}


def main():
    meta = read_json(os.path.join(HIST, "campaign_meta.json"), {})
    T = threshold("meta")
    rows = raw("placement.json")
    agg = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for r in rows:
        acc, camp = r["account_name"].strip(), r["campaign"]
        m = meta.get(f"meta|{acc}|{camp}", {})
        if m.get("is_lead") is False:
            continue
        prod = m.get("product") or product_of(camp)
        pl = f'{(r.get("publisher_platform") or "?").title()} · {(r.get("platform_position") or "?").replace("_", " ")}'
        a = agg[(prod, pl)]
        a[0] += num(r.get("spend")); a[1] += num(r.get("impressions"))
        a[2] += num(r.get("actions_link_click")); a[3] += num(r.get("actions_lead"))

    by_prod = collections.defaultdict(dict)
    for (prod, pl), v in agg.items():
        by_prod[prod][pl] = v
    products, flags = [], []
    for prod, pls in by_prod.items():
        tot = [sum(v[i] for v in pls.values()) for i in range(4)]
        if tot[0] <= 0:
            continue
        pcpl = cpl(tot[0], tot[3])
        rowsout = []
        for pl, v in sorted(pls.items(), key=lambda x: -x[1][0]):
            st = stats(*v)
            st["placement"] = pl
            st["share"] = v[0] / tot[0]
            rest = [tot[i] - v[i] for i in range(4)]
            rs = stats(*rest)
            flagged = False
            if st["share"] >= 0.10:
                if v[3] == 0 and v[0] >= 1.5 * T:
                    flagged, stage, parts = True, "No leads", {}
                elif pcpl and st["cpl"] and st["cpl"] > 1.3 * pcpl:
                    flagged = True
                    parts = {}
                    if rs["cpm"] and st["cpm"]:
                        parts["CPM"] = math.log(st["cpm"] / rs["cpm"])
                    if rs["ctr"] and st["ctr"]:
                        parts["CTR"] = -math.log(st["ctr"] / rs["ctr"])
                    if rs["ff"] and st["ff"]:
                        parts["Form fill"] = -math.log(st["ff"] / rs["ff"])
                    stage = max(parts, key=parts.get) if parts else "Form fill"
            if flagged:
                excess = v[0] - (v[3] * pcpl if pcpl else 0)
                total_gap = sum(max(x, 0) for x in parts.values()) or 1
                f = {"product": prod, "placement": pl, "share": st["share"], "spend": st["spend"],
                     "leads": v[3], "cpl": st["cpl"], "product_cpl": round(pcpl, 2) if pcpl else None,
                     "stage": stage, "fix": FIX[stage], "excess_spend": round(max(excess, 0), 2),
                     "stage_split": {k: round(max(x, 0) / total_gap, 2) for k, x in parts.items()},
                     "vs_rest": {"cpm": [st["cpm"], rs["cpm"]], "ctr": [st["ctr"], rs["ctr"]],
                                 "ff": [st["ff"], rs["ff"]]}}
                flags.append(f)
                st["flag"] = stage
            rowsout.append(st)
        products.append({"product": prod, "spend": round(tot[0], 2), "leads": tot[3],
                         "cpl": round(pcpl, 2) if pcpl else None, "placements": rowsout})
    products.sort(key=lambda p: -p["spend"])
    flags.sort(key=lambda f: -f["excess_spend"])
    write_json(os.path.join(REPORTS, "placement.json"), {"window": "last 7 days", "products": products,
                                                          "flags": flags})
    print(f"process_placement: {len(products)} products, {len(flags)} flagged placements, "
          f"excess {fmt_money(sum(f['excess_spend'] for f in flags))}")


if __name__ == "__main__":
    main()
