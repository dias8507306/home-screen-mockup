"""Randomizes every business number in a rendered dashboard snapshot (home_mockup.html).

The mockup is a static HTML snapshot of a server-rendered dashboard page. This script
rewrites the numbers so that no real metric survives, while keeping the page coherent:

* money values  -> scaled by a hidden random factor (+ per-value jitter for daily values)
* counts        -> scaled by a hidden random factor with per-value jitter
* the same original value always maps to the same new value, so repeated figures
  (e.g. a total shown in a KPI card, a chart and a text report) stay in sync
* days, hours, dates, small counters (< 32) and percentages are left as they are
* chart series inside <script type="application/json"> and the inline JS data objects
  are rewritten key-by-key with the same mapping

Usage:  python scripts/randomize_snapshot.py home_mockup.html [--seed N]
"""
import json, random, re, sys

path = sys.argv[1]
# No fixed seed by default: the scale factors must stay secret, otherwise the original
# numbers could be recovered by dividing them back out.
seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else None
rnd = random.Random(seed) if seed is not None else random.SystemRandom()
K_MONEY = rnd.uniform(0.62, 0.9)
K_COUNT = rnd.uniform(0.7, 0.92)
memo_m, memo_c, memo_p = {}, {}, {}
SP = "   "


def money(v):
    if v not in memo_m:
        j = 1 if v >= 1_000_000 else rnd.uniform(0.92, 1.08)
        memo_m[v] = int(round(v * K_MONEY * j, -2)) if v >= 1000 else int(round(v * K_MONEY * j))
    return memo_m[v]


def count(v):
    if v < 32:
        return v
    if v not in memo_c:
        memo_c[v] = max(32, int(round(v * K_COUNT * rnd.uniform(0.93, 1.07))))
    return memo_c[v]


def fmt_int(n, sep):
    s = f"{n:,}".replace(",", sep)
    return s


NUM = re.compile(r"(?<![\w#.:/\-])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:([.,])(\d+))?")


def transform_text(t):
    def rep(m):
        start, end = m.span()
        whole, dec_sep, dec = m.group(1), m.group(2), m.group(3)
        after = t[end:end + 8]
        before = t[max(0, start - 3):start]
        if re.match(r"[:.\-/]\d", after):  # times, dates
            return m.group(0)
        if re.match(r"\s*%", after):  # percentages: jittered, 0 and 100 kept
            key = m.group(0)
            if key not in memo_p:
                x = float(key.replace(",", ".").replace(" ", ""))
                if x in (0, 100):
                    memo_p[key] = key
                else:
                    y = x * rnd.uniform(0.82, 1.18)
                    memo_p[key] = (f"{y:.{len(dec)}f}".replace(".", dec_sep)) if dec else str(min(99, max(1, round(y))) if x < 100 else round(y))
            return memo_p[key]
        sep = next((c for c in whole if c in SP), " ")
        v = int(re.sub(r"\D", "", whole))
        if 2000 <= v <= 2099 and len(whole) == 4:  # years
            return m.group(0)
        is_money = bool(re.match(r"\s*(₸|млн)", after))
        if dec is not None:
            x = float(f"{v}.{dec}")
            if re.match(r"\s*млн", after):
                y = round(x * K_MONEY, len(dec))
            elif x < 32 and not is_money:
                y = round(x * K_COUNT * rnd.uniform(0.95, 1.05), len(dec))
            else:
                return m.group(0)
            return f"{y:.{len(dec)}f}".replace(".", dec_sep)
        if v < 32 and not is_money:  # days, hours, small counters ("09", "27")
            return m.group(0)
        n = money(v) if is_money else count(v)
        return fmt_int(n, sep) if any(c in SP for c in whole) or n >= 1000 and is_money else str(n)

    return NUM.sub(rep, t)


TAG = re.compile(r"<(?:[^>\"']|\"[^\"]*\"|'[^']*')*>")
ATTR = re.compile(r'\b(data-tip|title|aria-label)="([^"]*)"')
RAW = re.compile(r"(<script\b.*?</script>|<style\b.*?</style>)", re.S | re.I)  # left untouched here


def transform_html(chunk):
    out, pos = [], 0
    for m in TAG.finditer(chunk):
        out.append(transform_text(chunk[pos:m.start()]))
        tag = ATTR.sub(lambda a: f'{a.group(1)}="{transform_text(a.group(2))}"', m.group(0))
        tag = re.sub(r'data-to="(\d+)"', lambda a: f'data-to="{money(int(a.group(1))) if int(a.group(1)) >= 1000 else count(int(a.group(1)))}"', tag)
        out.append(tag)
        pos = m.end()
    out.append(transform_text(chunk[pos:]))
    return "".join(out)


src = open(path, encoding="utf-8").read()
html = "".join(part if i % 2 else transform_html(part) for i, part in enumerate(RAW.split(src)))

# chart series (JSON) — rewrite key by key with the same mapping
def fix_series(obj):
    money_tab = obj.get("unit", "").strip() == "₸"
    f = money if money_tab else count
    obj["cur"] = [f(x) for x in obj["cur"]]
    obj["prev"] = [f(x) for x in obj["prev"]]
    obj["total"] = f(obj["total"])
    if isinstance(obj.get("delta"), (int, float)) and obj["delta"]:
        obj["delta"] = max(1, round(obj["delta"] * rnd.uniform(0.7, 1.3)))
    return obj

def fix_json_block(m):
    d = json.loads(m.group(1))
    d["s"] = [fix_series(x) for x in d["s"]]
    return '<script type="application/json">' + json.dumps(d, ensure_ascii=False) + "</script>"

html = re.sub(r'<script type="application/json">(.*?)</script>', fix_json_block, html, flags=re.S)

def fix_S(m):
    rows = json.loads(m.group(1))
    for r in rows:
        r["leads"], r["amount"] = count(r["leads"]), money(r["amount"]) if r["amount"] else 0
    return "const S=" + json.dumps(rows, ensure_ascii=False) + ";"

html = re.sub(r"const S=(\[.*?\]);", fix_S, html, flags=re.S)

def fix_C(m):
    c = json.loads(m.group(1))
    for k in ("today_leads", "lead_day"):
        if isinstance(c.get(k), int):
            c[k] = count(c[k])
    if isinstance(c.get("bonus"), int) and c["bonus"]:
        c["bonus"] = money(c["bonus"])
    return "const C=" + json.dumps(c, ensure_ascii=False) + ";"

html = re.sub(r"const C=(\{.*?\});(?=const q=)", fix_C, html, flags=re.S)
open(path, "w", encoding="utf-8", newline="").write(html)
print(f"randomized {len(memo_m)} money values and {len(memo_c)} counts")
