"""Render out/dashboard.html (artifact page) and out/email_<mode>.html + out/email.json from reports/report.json.

Email rules: HTML tables with inline styles only (no <style>, no images, no attachments),
red cells use both bgcolor and background-color, table first, keep it short."""
import html
import sys

from common import *

RED_BG, RED_FG = "#FCE4E1", "#B42318"
TD = "padding:5px 7px;border:1px solid #D9DFE7;font:13px Arial,sans-serif;"
TH = TD + "background-color:#EEF2F6;font-weight:bold;text-align:left;"
E = html.escape


def cell(text, red=False, right=True, bold=False):
    style = TD + ("text-align:right;" if right else "") + ("font-weight:bold;" if bold else "")
    if red:
        return f'<td bgcolor="{RED_BG}" style="{style}background-color:{RED_BG};color:{RED_FG};font-weight:bold;">{text}</td>'
    return f'<td style="{style}">{text}</td>'


def above(spend, leads, T):
    return spend > 0 and ((leads > 0 and spend / leads > T) or (leads == 0 and spend >= T))


def short_date(d):
    x = ddate(d)
    return f"{x.day} {x.strftime('%b')}"


def ask_context(r):
    """Compact text context for the dashboard's Ask box (sample input is capped at 64 KiB)."""
    L = [f"Data through {r['data_date']}. 4-day window {r['dates4'][0]}..{r['dates4'][-1]}. "
         f"Targets: Meta CPL {r['thresholds']['meta']}, Google CPL {r['thresholds']['google']}."]
    for a in r["accounts"]:
        L.append(f"\n[{a['platform']}] {a['account']}: 4d spend {a['spend4']:.0f}, leads {a['leads4']:.0f}, CPL {a['cpl4']}")
        for p in a["products"]:
            L.append(f"  product {p['product']}: CPL4 {p['cpl4']} spend4 {p['spend4']:.0f} leads4 {p['leads4']:.0f} "
                     f"{p['severity']} trend {p['trend']}; daily CPL " + ",".join(str(d['cpl']) for d in p['days']))
        for c in a["campaigns"][:12]:
            L.append(f"    campaign {c['campaign']}: CPL4 {c['cpl4']} CPL11 {c['cpl11']} spend11 {c['spend11']:.0f} "
                     f"{c['status']} budget {c['daily_budget']} trend {c['trend']}")
    L.append("\nNON-LEAD CAMPAIGNS: " + "; ".join(f"{n['campaign']} ({n['goal']}) cost per {n['metric_label']} "
                                                  f"{n['cost_per4']}" for n in r["non_lead"]))
    L.append("\nACTIONS: " + " | ".join(f"{u['product']} {u['platform']} {u['account']}: " + " ".join(u["actions"])
                                         for u in r["urgent"] + r["watch"]))
    L.append("\nPLACEMENT FLAGS: " + " | ".join(
        f"{f['product']} {f['placement']} share {f['share']:.0%} CPL {f['cpl']} vs {f['product_cpl']} stage {f['stage']} "
        f"excess {f['excess_spend']:.0f}" for f in r["placement"]["flags"]))
    ch = r["changes"]
    L.append(f"\nCHANGES last 7 days: {ch['recent_count']}. Learnings: " + " | ".join(
        f"{l['type']}: {l['helped']} helped/{l['hurt']} hurt, median {l['median_net']}" for l in ch["learnings"]))
    L.append("Course-corrections: " + " | ".join(f"{c['adset_name']} {c['type']} {c['detail'][:60]} -> {c['problem']}"
                                                 for c in ch["course_corrections"][:8]))
    if r["funnel"]["configured"]:
        L.append("\nFUNNEL: " + " | ".join(f"{p['product']} leads {p['leads']} SQL {p['sql']} sales {p['sales']} "
                                           f"revenue {p['revenue']} ROI {p['roi']} {p['verdict']}" for p in r["funnel"]["products"]))
    else:
        L.append("\nFUNNEL: not connected (no revenue/ROI data).")
    L.append("\nCOPY: " + " ".join(r["creative"].get("callouts", [])))
    txt = "\n".join(L)
    return txt[:52000]


def dashboard(r):
    slim = json.loads(json.dumps(r))
    for a in slim["accounts"]:
        for p in a["products"]:
            p.pop("root_cause", None)
    for x in slim["root_cause"]:
        rc = x.get("root_cause", {})
        for s in rc.get("ad_sets", []):
            t = s.get("targeting") or {}
            s["targeting"] = {k: t.get(k) for k in ("advantage_plus", "interests", "job_titles", "custom_audiences", "age")} if t else None
    data = json.dumps(slim, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    ctx = ask_context(r).replace("</", "<\\/")
    tpl = open(os.path.join(ROOT, "templates", "dashboard.html"), encoding="utf-8").read()
    page = tpl.replace("__REPORT_JSON__", data).replace("__ASK_CONTEXT__", E(ctx, quote=False))
    with open(os.path.join(OUT, "dashboard.html"), "w", encoding="utf-8") as f:
        f.write(page)
    return len(page)


def product_table(r, min_spend):
    d4 = r["dates4"]
    rows, hidden = [], 0
    head = "".join(f'<th style="{TH}text-align:right;">{short_date(d)}</th>' for d in d4)
    out = [f'<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">'
           f'<tr><th style="{TH}">Account</th><th style="{TH}">Product</th>{head}'
           f'<th style="{TH}text-align:right;">4-day CPL</th><th style="{TH}text-align:right;">Spend</th>'
           f'<th style="{TH}text-align:right;">Leads</th></tr>']
    for a in r["accounts"]:
        T = a["threshold"]
        prods = [p for p in a["products"] if p["spend4"] >= min_spend]
        hidden += len(a["products"]) - len(prods)
        for i, p in enumerate(prods):
            acc = f"{'Meta' if a['platform'] == 'meta' else 'Google'} · {E(a['account'])}" if i == 0 else ""
            days = "".join(cell(fmt_money(d["cpl"]) if d["leads"] and d["spend"] else ("0 leads" if d["spend"] else "–"),
                                above(d["spend"], d["leads"], T)) for d in p["days"])
            out.append(f"<tr>{cell(acc, right=False)}{cell(E(p['product']), right=False)}{days}"
                       f"{cell(fmt_money(p['cpl4']), above(p['spend4'], p['leads4'], T), bold=True)}"
                       f"{cell(fmt_money(p['spend4']))}{cell(format(p['leads4'], ',.0f'))}</tr>")
    out.append("</table>")
    return "".join(out), hidden


def campaign_table(r, min_spend):
    d11 = r["dates11"]
    head = "".join(f'<th style="{TH}text-align:right;">{ddate(d).day}</th>' for d in d11)
    out = [f'<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">'
           f'<tr><th style="{TH}">Campaign</th>{head}<th style="{TH}text-align:right;">11-day CPL</th>'
           f'<th style="{TH}">Status</th></tr>']
    hidden = 0
    for a in r["accounts"]:
        T = a["threshold"]
        camps = [c for c in a["campaigns"] if c["spend11"] >= min_spend][:SETTINGS.get("weekly_max_per_account", 8)]
        hidden += len(a["campaigns"]) - len(camps)
        if not camps:
            continue
        out.append(f'<tr><td colspan="{len(d11) + 3}" style="{TD}background-color:#F6F8FA;font-weight:bold;">'
                   f"{'Meta' if a['platform'] == 'meta' else 'Google'} · {E(a['account'])} (target {fmt_money(T)})</td></tr>")
        for c in camps:
            days = "".join(cell(f"{d['cpl']:,.0f}" if d["leads"] and d["spend"] else ("0" if d["spend"] else "·"),
                                above(d["spend"], d["leads"], T)) for d in c["days"])
            st = (c["status"] or "").replace("_", " ").lower()
            if not c["delivering"] and "deliver" not in st:
                st += " (not delivering)"
            out.append(f"<tr>{cell(E(c['campaign']), right=False)}{days}"
                       f"{cell(fmt_money(c['cpl11']), above(c['spend11'], c['leads11'], T), bold=True)}"
                       f"{cell(E(st), right=False)}</tr>")
    out.append("</table>")
    return "".join(out), hidden


def para(t):
    return f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;margin:14px 0 6px;">{t}</p>'


def h(t):
    return f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:bold;margin:20px 0 6px;">{t}</p>'


def ul(items):
    if not items:
        return ""
    li = "".join(f'<li style="margin-bottom:5px;">{x}</li>' for x in items)
    return f'<ul style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.45;margin:4px 0 8px;padding-left:20px;">{li}</ul>'


def daily_email(r):
    d4 = r["dates4"]
    pt = r["platform_totals"]
    dates = f"{short_date(d4[0])} – {short_date(d4[-1])}"
    table, hidden = product_table(r, SETTINGS.get("email_min_spend4", 5000))
    intro = para(f"Hi, here is the 4-day CPL view for {dates}. Red cells are above target "
                 f"(Meta {fmt_money(THRESHOLDS['meta'])}, Google {fmt_money(THRESHOLDS['google'])}).")
    summ = []
    for p, name in (("meta", "Meta"), ("google", "Google")):
        if pt.get(p) and pt[p]["spend4"]:
            summ.append(f"<b>{name}</b>: {fmt_money(pt[p]['spend4'])} spend, {pt[p]['leads4']:,.0f} leads, CPL "
                        f"{fmt_money(pt[p]['cpl4'])} — {pt[p]['critical']} critical, {pt[p]['watch']} on watch.")
    if r["urgent"]:
        u = r["urgent"][0]
        summ.append(f"Biggest gap: {E(u['product'])} ({u['platform'].title()}, {E(u['account'])}) at "
                    f"{fmt_money(u['cpl4'])} CPL, {fmt_money(max(u['over_target'], 0))} above what target CPL would cost.")
    nl = [n for n in r["non_lead"] if n["delivering"]]
    if nl:
        summ.append("Non-lead campaigns judged on their own metric: " + "; ".join(
            f"{E(n['campaign'])} {fmt_money(n['cost_per4']) if n['cost_per4'] else 'no results yet'} per {E(n['metric_label'])}"
            for n in nl[:3]) + ".")
    urgent = []
    for u in r["urgent"][:SETTINGS.get("email_max_urgent", 6)]:
        acts = "".join(f"<br>• {E(a)}" for a in u["actions"][:2]) or "<br>• Check the lead form and landing experience."
        urgent.append(f"<b>{E(u['product'])}</b> ({u['platform'].title()} · {E(u['account'])}, CPL {fmt_money(u['cpl4'])}){acts}")
    upd = []
    if r["watch"]:
        upd.append("Watch: " + ", ".join(f"{E(w['product'])} ({w['platform'].title()} {fmt_money(w['cpl4'])})"
                                          for w in r["watch"][:6]) + ".")
    for s in r["scale"][:2]:
        upd.append("Scale-up: " + E(s["text"]))
    for c in r["changes"]["course_corrections"][:2]:
        upd.append(f"Course-correction: {E(c['adset_name'])} ({E(c['account'])}) — {E(c['type'])} on {c['date']}: "
                   f"{E(c['problem'])}. {E(c['action'])}.")
    upd.append(f"Changes detected in the last 7 days: {r['changes']['recent_count']}.")
    link = r.get("dashboard_url") or SETTINGS.get("dashboard_url")
    foot = (para(f'Full detail (campaign trends, placements, changes, root cause, Ask box): '
                 f'<a href="{E(link)}">{E(link)}</a>') if link else "")
    note = para(f'<span style="color:#5B6878;font-size:12px;">{hidden} smaller product rows (under '
                f'{fmt_money(SETTINGS.get("email_min_spend4", 5000))} in 4 days) are on the dashboard.</span>') if hidden else ""
    body = intro + table + note + h("Summary") + ul(summ) + h("Urgent action") + ul(urgent or ["Nothing critical today."]) \
        + h("Update / change") + ul(upd) + foot
    subject = f"Meta Ads Daily CPL - 4-day view + actions ({dates})"
    return subject, f'<div style="max-width:980px;">{body}</div>'


def weekly_email(r):
    d11 = r["dates11"]
    dates = f"{short_date(d11[0])} – {short_date(d11[-1])}"
    table, hidden = campaign_table(r, SETTINGS.get("weekly_min_spend11", 20000))
    intro = para(f"Hi, here is the 11-day campaign view for {dates}. Columns are day of month; red cells are above "
                 f"target (Meta {fmt_money(THRESHOLDS['meta'])}, Google {fmt_money(THRESHOLDS['google'])}).")
    wk = r["weekly"]
    summ = []
    for k in ("consistently above", "getting worse", "improving", "paused / not delivering"):
        v = wk.get(k, [])
        if v:
            summ.append(f"<b>{k.capitalize()}</b> ({len(v)}): " + ", ".join(E(x) for x in v[:6])
                        + (f" and {len(v) - 6} more" if len(v) > 6 else "") + ".")
    fun = r["funnel"]
    fb = ([f"{E(p['product'])}: {p['sql']:,} SQL, {p['sales']:,} enrolled, cost per SQL {fmt_money(p.get('cost_per_sql'))}"
           + (f", ROI {p['roi']}×" if p.get('roi') is not None else "") + f" — {p['verdict']}"
           for p in fun["products"][:6]] + [E(c) for c in fun.get("callouts", [])[:2]]
          if fun["configured"] else ["Funnel sheet not connected yet — no SQL / revenue view."])
    pb = [f"{E(f['product'])}: {E(f['placement'])} is {f['share']:.0%} of spend at {fmt_money(f['cpl'])} vs "
          f"{fmt_money(f['product_cpl'])} ({f['stage']} stage), {fmt_money(f['excess_spend'])} excess."
          for f in r["placement"]["flags"][:4]]
    lb = [E(l["advice"]) for l in r["changes"]["learnings"] if l.get("advice")] or \
         ["No change type has a consistent enough effect to advise on yet."]
    link = r.get("dashboard_url") or SETTINGS.get("dashboard_url")
    note = para(f'<span style="color:#5B6878;font-size:12px;">{hidden} campaigns under '
                f'{fmt_money(SETTINGS.get("weekly_min_spend11", 20000))} in 11 days are on the dashboard.</span>') if hidden else ""
    body = intro + table + note + h("Weekly summary") + ul(summ) + h("Funnel") + ul(fb) + h("Placements") + ul(pb) \
        + h("What we learned from changes") + ul(lb) + (para(f'Dashboard: <a href="{E(link)}">{E(link)}</a>') if link else "")
    subject = f"Meta Ads Weekly CPL - 11-day campaign view ({dates})"
    return subject, f'<div style="max-width:1100px;">{body}</div>'


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    r = read_json(os.path.join(REPORTS, "report.json"))
    size = dashboard(r)
    subject, body = (weekly_email if mode == "weekly" else daily_email)(r)
    assert "<img" not in body and "<style" not in body
    path = os.path.join(OUT, f"email_{mode}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    rc = SETTINGS["recipients"]
    team = bool(rc.get("team_mode"))
    write_json(os.path.join(OUT, "email.json"), {
        "mode": mode, "subject": subject, "html_file": f"out/email_{mode}.html",
        "to": rc["to"] if team else [rc["me"]], "cc": rc["cc"] if team else [],
        "team_mode": team, "data_date": r["data_date"]})
    print(f"render: dashboard {size / 1024:.0f} KB, {mode} email {len(body) / 1024:.0f} KB -> "
          f"{'TEAM' if team else 'ME ONLY'}; subject: {subject}")


if __name__ == "__main__":
    main()
