"""Shared settings, product mapping, lead/non-lead classification and row loading."""
import datetime as dt
import json
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "raw")
HIST = os.path.join(ROOT, "history")
REPORTS = os.path.join(ROOT, "reports")
OUT = os.path.join(ROOT, "out")
for _d in (HIST, REPORTS, OUT, os.path.join(HIST, "snapshots")):
    os.makedirs(_d, exist_ok=True)

SETTINGS = json.load(open(os.path.join(ROOT, "settings.json"), encoding="utf-8"))
THRESHOLDS = SETTINGS["thresholds"]          # {"meta": 500, "google": 1200}
CUR = SETTINGS.get("currency", "₹")
LEAD_GOALS = set(SETTINGS.get("lead_goals", ["LEAD_GENERATION", "QUALITY_LEAD"]))
_RULES = [(re.compile(p), name) for p, name in SETTINGS["product_rules"]]


def threshold(platform):
    return THRESHOLDS[platform]


def product_of(campaign):
    """Product by campaign-name prefix (text before first '|'); explicit prefix map wins, then keyword rules."""
    name = campaign or ""
    prefix = name.split("|", 1)[0].strip()
    pm = SETTINGS.get("product_map_prefix", {})
    if prefix in pm:
        return pm[prefix]
    for rx, prod in _RULES:
        if rx.search(name):
            return prod
    return SETTINGS.get("product_rule_default", "Other")


def load_rows(path):
    """Accepts {"result":[...]} or a bare list. Missing file -> []."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("result", data.get("data", []))
    return data or []


def raw(name):
    return load_rows(os.path.join(RAW, name))


def num(x):
    try:
        return float(x) if x is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def cpl(spend, leads):
    return spend / leads if leads else None


def ist_today():
    off = dt.timedelta(minutes=SETTINGS.get("timezone_offset_minutes", 330))
    return (dt.datetime.now(dt.timezone.utc) + off).date()


def dstr(d):
    return d.isoformat()


def ddate(s):
    return dt.date.fromisoformat(s[:10])


def median(xs):
    return statistics.median(xs) if xs else None


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fail(msg, code=2):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(code)


def fmt_money(x):
    if x is None:
        return "–"
    return f"{CUR}{x:,.0f}"


def classify_goal(goal, destination, pixel_leads, form_leads, campaign):
    """Return (is_lead, metric_key, metric_label).

    Lead-optimised = LEAD_GENERATION / QUALITY_LEAD, or OFFSITE_CONVERSIONS that is actually producing leads
    (website+lead-form destination, or pixel/form leads recorded). Anything else is judged on its own metric."""
    ov = SETTINGS.get("non_lead_overrides", {})
    if campaign in ov:
        o = ov[campaign]
        return False, o["metric"], o.get("label", o["metric"])
    if campaign in SETTINGS.get("lead_overrides", []):
        return True, "leads", "lead"
    g = (goal or "").upper()
    if g in LEAD_GOALS:
        return True, "leads", "lead"
    if g == "OFFSITE_CONVERSIONS":
        if "LEAD" in (destination or "").upper() or pixel_leads > 0 or form_leads > 0:
            return True, "leads", "lead"
        return False, "registrations", "registration"
    if g in ("CONVERSATIONS", "MESSAGING_PURCHASE_CONVERSION", "MESSAGING_APPOINTMENT_CONVERSION") or \
            "WHATSAPP" in (destination or "").upper() or "MESSENGER" in (destination or "").upper():
        return False, "conversations", "WhatsApp/Messenger conversation"
    if g in ("LANDING_PAGE_VIEWS", "LINK_CLICKS"):
        return False, "lpv", "landing-page view"
    if g in ("REACH", "IMPRESSIONS", "AD_RECALL_LIFT", "THRUPLAY", "VIDEO_VIEWS", "POST_ENGAGEMENT"):
        return False, "impressions_k", "1,000 impressions"
    if not g:
        return None, "leads", "lead"   # unknown (not delivering, never seen)
    return False, "lpv", "landing-page view"
