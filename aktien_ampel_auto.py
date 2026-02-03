# aktien_ampel_auto.py
# WICHTIG (requirements.txt):
# streamlit
# pandas
# yfinance
# feedparser
# requests

import re
import uuid
import time
from datetime import datetime, timedelta, timezone

import streamlit as st
import pandas as pd
import yfinance as yf
import feedparser
import requests


# ============================================================
# Page
# ============================================================
st.set_page_config(
    page_title="Aktienbewertung – Watchlist + Score",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Handy-clean: etwas weniger “Luft”, aber nicht zu eng
st.markdown(
    """
<style>
    .block-container { padding-top: 1.0rem; padding-bottom: 1.0rem; }
    h1,h2,h3 { margin-bottom: .4rem; }
    .stDataFrame { border-radius: 12px; }
    .stExpander { border-radius: 12px; }
    .stButton > button { border-radius: 12px; }
    .small-muted { opacity: .75; font-size: .9rem; }
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# Helpers
# ============================================================
def to_float(x):
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def to_pct(x):
    v = to_float(x)
    if v is None:
        return None
    return v * 100.0


def safe_get(d: dict, key: str):
    val = d.get(key)
    if val in [None, "None", "nan"]:
        return None
    return val


def fmt_eur(x):
    v = to_float(x)
    if v is None:
        return "—"
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_num(x, d=2):
    v = to_float(x)
    if v is None:
        return "—"
    return f"{v:.{d}f}"


def fmt_pct(x, d=1):
    v = to_float(x)
    if v is None:
        return "—"
    return f"{v:.{d}f}%"


@st.cache_data(ttl=60 * 30)
def fx_rate_to_eur(from_ccy: str) -> float | None:
    """Faktor: amount_in_from_ccy * rate = amount_in_eur"""
    from_ccy = (from_ccy or "").upper()
    if from_ccy in ["EUR", ""]:
        return 1.0
    pair = f"EUR{from_ccy}=X"
    try:
        info = yf.Ticker(pair).get_info()
        px = to_float(info.get("regularMarketPrice")) or to_float(info.get("previousClose"))
        if px and px > 0:
            return 1.0 / px
        return None
    except Exception:
        return None


def money_to_eur(amount: float | None, ccy: str) -> float | None:
    if amount is None:
        return None
    rate = fx_rate_to_eur(ccy)
    if rate is None:
        return None
    return amount * rate


def is_isin(s: str) -> bool:
    s = (s or "").strip().upper()
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", s))


def is_wkn(s: str) -> bool:
    s = (s or "").strip().upper()
    return bool(re.fullmatch(r"[A-Z0-9]{6}", s))


def normalize_token(s: str) -> str:
    return (s or "").strip().upper()


# ============================================================
# Deine feste Watchlist (ohne ETFs) + Mapping für WKN/ISIN
# ============================================================
DEFAULT_WATCHLIST = [
    # Big Tech / Megatrend
    {"Ticker": "GOOGL", "Name": "Alphabet Inc.", "WKN": "", "ISIN": ""},
    {"Ticker": "MSFT",  "Name": "Microsoft Corporation", "WKN": "", "ISIN": ""},
    {"Ticker": "TSM",   "Name": "Taiwan Semiconductor Manufacturing", "WKN": "", "ISIN": ""},
    {"Ticker": "NVDA",  "Name": "NVIDIA Corporation", "WKN": "", "ISIN": ""},
    {"Ticker": "META",  "Name": "Meta Platforms", "WKN": "", "ISIN": ""},
    {"Ticker": "NFLX",  "Name": "Netflix", "WKN": "", "ISIN": ""},
    {"Ticker": "SHOP",  "Name": "Shopify", "WKN": "", "ISIN": ""},

    # DACH/CH
    {"Ticker": "SAP.DE",  "Name": "SAP SE", "WKN": "", "ISIN": ""},
    {"Ticker": "ROG.SW",  "Name": "Roche Holding", "WKN": "", "ISIN": ""},
    {"Ticker": "NOVN.SW", "Name": "Novartis", "WKN": "", "ISIN": ""},
    {"Ticker": "MUV2.DE", "Name": "Münchener Rück", "WKN": "", "ISIN": ""},
    {"Ticker": "ALV.DE",  "Name": "Allianz", "WKN": "", "ISIN": ""},
    {"Ticker": "RHM.DE",  "Name": "Rheinmetall", "WKN": "", "ISIN": ""},
    {"Ticker": "R3NK.DE", "Name": "Renk Group", "WKN": "", "ISIN": ""},
    {"Ticker": "SIE.DE",  "Name": "Siemens", "WKN": "", "ISIN": ""},
    {"Ticker": "ENR.DE",  "Name": "Siemens Energy", "WKN": "", "ISIN": ""},

    # US Defensiv / Quality
    {"Ticker": "KO",    "Name": "Coca-Cola", "WKN": "", "ISIN": ""},
    {"Ticker": "MCD",   "Name": "McDonald's", "WKN": "", "ISIN": ""},
    {"Ticker": "JNJ",   "Name": "Johnson & Johnson", "WKN": "", "ISIN": ""},
    {"Ticker": "WMT",   "Name": "Walmart", "WKN": "", "ISIN": ""},
    {"Ticker": "V",     "Name": "Visa", "WKN": "", "ISIN": ""},
    {"Ticker": "DIS",   "Name": "Walt Disney", "WKN": "", "ISIN": ""},
    {"Ticker": "XOM",   "Name": "Exxon Mobil", "WKN": "", "ISIN": ""},
    {"Ticker": "WM",    "Name": "Waste Management", "WKN": "", "ISIN": ""},

    # Cyber / Biotech
    {"Ticker": "CRWD",  "Name": "CrowdStrike", "WKN": "", "ISIN": ""},
    {"Ticker": "NTLA",  "Name": "Intellia Therapeutics", "WKN": "", "ISIN": ""},

    # EU/CH/NO
    {"Ticker": "NESN.SW", "Name": "Nestlé", "WKN": "", "ISIN": ""},
    {"Ticker": "NVO",     "Name": "Novo Nordisk ADR", "WKN": "", "ISIN": ""},

    # ABB (je nach Börse: ABB oder ABBN.SW)
    {"Ticker": "ABB",   "Name": "ABB", "WKN": "", "ISIN": ""},

    # Trust (optional)
    {"Ticker": "SMT.L", "Name": "Scottish Mortgage Investment Trust", "WKN": "", "ISIN": ""},
]


def build_lookup_index(rows: list[dict]) -> tuple[dict, dict, dict]:
    by_ticker = {}
    by_wkn = {}
    by_isin = {}
    for r in rows:
        t = normalize_token(r.get("Ticker"))
        if t:
            by_ticker[t] = r
        w = normalize_token(r.get("WKN"))
        if w:
            by_wkn[w] = r
        i = normalize_token(r.get("ISIN"))
        if i:
            by_isin[i] = r
    return by_ticker, by_wkn, by_isin


# ============================================================
# Shared “global” additions (ohne DB)
# ============================================================
@st.cache_resource
def global_store():
    return {"added": []}  # list of dicts


def get_session_id() -> str:
    if "sid" not in st.session_state:
        st.session_state.sid = str(uuid.uuid4())
    return st.session_state.sid


def current_watchlist() -> list[dict]:
    base = DEFAULT_WATCHLIST.copy()
    base_tickers = {normalize_token(x["Ticker"]) for x in base}
    added = global_store()["added"]
    for a in added:
        if normalize_token(a.get("Ticker")) not in base_tickers:
            base.append({k: a.get(k, "") for k in ["Ticker", "Name", "WKN", "ISIN"]})
    base = sorted(base, key=lambda r: (normalize_token(r.get("Ticker")), normalize_token(r.get("Name"))))
    return base


# ============================================================
# Scoring (ohne Fair Value) – 7/10 = Kaufen
# ============================================================
def score_kgv(pe: float | None) -> tuple[int, str]:
    if pe is None or pe <= 0:
        return 5, "KGV unbekannt → neutral"
    if pe <= 25:
        return 10, "KGV günstig (≤25)"
    if pe <= 35:
        return 8, "KGV ok (≤35)"
    if pe <= 50:
        return 6, "KGV hoch (≤50)"
    return 3, "KGV sehr hoch (>50)"


def score_wachstum(rev_growth_pct: float | None) -> tuple[int, str]:
    if rev_growth_pct is None:
        return 5, "Wachstum unbekannt → neutral"
    if rev_growth_pct >= 25:
        return 10, "Wachstum sehr stark (≥25%)"
    if rev_growth_pct >= 15:
        return 9, "Wachstum stark (≥15%)"
    if rev_growth_pct >= 8:
        return 7, "Wachstum solide (≥8%)"
    if rev_growth_pct >= 3:
        return 6, "Wachstum niedrig (≥3%)"
    return 4, "Wachstum schwach (<3%)"


def score_marge(oper_margin_pct: float | None) -> tuple[int, str]:
    if oper_margin_pct is None:
        return 5, "Marge unbekannt → neutral"
    if oper_margin_pct >= 25:
        return 10, "Operative Marge top (≥25%)"
    if oper_margin_pct >= 15:
        return 8, "Operative Marge gut (≥15%)"
    if oper_margin_pct >= 10:
        return 7, "Operative Marge ok (≥10%)"
    if oper_margin_pct >= 5:
        return 5, "Operative Marge dünn (≥5%)"
    return 3, "Operative Marge schwach (<5%)"


def score_verschuldung(debt_to_equity: float | None) -> tuple[int, str]:
    if debt_to_equity is None or debt_to_equity < 0:
        return 5, "Verschuldung unbekannt → neutral"
    if debt_to_equity <= 0.7:
        return 9, "Verschuldung niedrig (D/E ≤0.7)"
    if debt_to_equity <= 1.5:
        return 7, "Verschuldung ok (D/E ≤1.5)"
    if debt_to_equity <= 2.5:
        return 5, "Verschuldung erhöht (D/E ≤2.5)"
    return 3, "Verschuldung hoch (D/E >2.5)"


def score_fcf(fcf_margin_pct: float | None) -> tuple[int, str]:
    if fcf_margin_pct is None:
        return 5, "FCF-Marge unbekannt → neutral"
    if fcf_margin_pct >= 15:
        return 10, "FCF-Marge sehr stark (≥15%)"
    if fcf_margin_pct >= 8:
        return 8, "FCF-Marge gut (≥8%)"
    if fcf_margin_pct >= 3:
        return 6, "FCF-Marge ok (≥3%)"
    return 4, "FCF-Marge schwach (<3%)"


def score_analyst(upside_pct: float | None, target_eur: float | None) -> tuple[int, str]:
    if target_eur is None:
        return 5, "Erwartung (Analysten-Ziel) fehlt → neutral"
    if upside_pct is None:
        return 5, "Upside unbekannt → neutral"
    if upside_pct >= 30:
        return 10, "Analysten-Upside hoch (≥30%)"
    if upside_pct >= 15:
        return 8, "Analysten-Upside gut (≥15%)"
    if upside_pct >= 5:
        return 6, "Analysten-Upside gering (≥5%)"
    if upside_pct >= -10:
        return 4, "Analysten sehen wenig Luft"
    return 2, "Analysten eher negativ (<-10%)"


def points_to_ampel(score_0_10: float) -> str:
    if score_0_10 >= 7.0:
        return "🟢 Grün"
    if score_0_10 >= 5.5:
        return "🟡 Gelb"
    return "🔴 Rot"


def decision_from_score(score_0_10: float) -> str:
    if score_0_10 >= 7.0:
        return "🟢 KAUFEN"
    if score_0_10 >= 5.5:
        return "🟡 BEOBACHTEN"
    return "🔴 NICHT KAUFEN"


def trend_profil(sector: str | None, rev_growth_pct: float | None, beta: float | None, dividend_yield_pct: float | None) -> str:
    s = (sector or "").lower()

    megatrend_sectors = [
        "technology", "semiconductors", "communication services",
        "software", "internet", "healthcare", "biotechnology",
        "renewable", "industrial", "aerospace", "defense",
    ]
    defensiv_sectors = [
        "consumer defensive", "consumer staples", "utilities",
        "healthcare", "telecom", "insurance",
    ]

    is_mega = any(k in s for k in megatrend_sectors) and (rev_growth_pct is not None and rev_growth_pct >= 10)
    is_def = any(k in s for k in defensiv_sectors) and ((beta is None) or (beta <= 1.15))

    if is_def and dividend_yield_pct and dividend_yield_pct >= 1.5:
        return "🛡️ Defensiv"
    if is_mega:
        return "🚀 Megatrend"
    if is_def:
        return "🛡️ Defensiv"
    return "⚖️ Neutral"


# ============================================================
# Rate-limit Fix: Cooldown + sanfter Retry
# ============================================================
COOLDOWN_SECONDS = 45

def can_run_now() -> bool:
    last = st.session_state.get("last_run_epoch")
    if last is None:
        return True
    return (time.time() - last) >= COOLDOWN_SECONDS


def cooldown_left() -> int:
    last = st.session_state.get("last_run_epoch")
    if last is None:
        return 0
    left = int(COOLDOWN_SECONDS - (time.time() - last))
    return max(0, left)


def get_info_with_retry(ticker: str, retries: int = 2):
    for i in range(retries + 1):
        try:
            return yf.Ticker(ticker).get_info() or {}
        except Exception:
            if i == retries:
                return {}
            time.sleep(1.0 + i * 2.0)


@st.cache_data(ttl=60 * 30)  # länger cache => weniger Requests
def fetch_stock_info_cached(ticker: str) -> dict:
    info = get_info_with_retry(ticker, retries=2)

    price = to_float(safe_get(info, "currentPrice")) or to_float(safe_get(info, "regularMarketPrice"))
    pe = to_float(safe_get(info, "trailingPE")) or to_float(safe_get(info, "forwardPE"))

    rev_growth = to_pct(safe_get(info, "revenueGrowth"))
    oper_margin = to_pct(safe_get(info, "operatingMargins"))

    d2e_raw = to_float(safe_get(info, "debtToEquity"))
    debt_to_equity = None
    if d2e_raw is not None:
        debt_to_equity = d2e_raw / 100.0 if d2e_raw > 10 else d2e_raw

    fcf = to_float(safe_get(info, "freeCashflow"))
    revenue = to_float(safe_get(info, "totalRevenue"))
    fcf_margin = None
    if fcf is not None and revenue:
        try:
            if revenue != 0:
                fcf_margin = (fcf / revenue) * 100.0
        except Exception:
            pass

    target_mean = to_float(safe_get(info, "targetMeanPrice"))

    name = safe_get(info, "shortName") or safe_get(info, "longName") or ticker.upper()
    ccy = (safe_get(info, "currency") or "").upper() or "Unbekannt"
    sector = safe_get(info, "sector")
    industry = safe_get(info, "industry")
    beta = to_float(safe_get(info, "beta"))
    div_yield = safe_get(info, "dividendYield")
    div_yield_pct = to_pct(div_yield) if div_yield is not None else None

    price_eur = money_to_eur(price, ccy) if price is not None else None
    target_eur = money_to_eur(target_mean, ccy) if target_mean is not None else None
    upside_pct = None
    if price_eur is not None and target_eur is not None and price_eur != 0:
        upside_pct = (target_eur / price_eur - 1.0) * 100.0

    return {
        "Ticker": ticker.upper(),
        "Name": name,
        "Währung": ccy,
        "Kurs (€)": price_eur,
        "KGV": pe,
        "Umsatzwachstum YoY (%)": rev_growth,
        "Operative Marge (%)": oper_margin,
        "Debt/Equity": debt_to_equity,
        "FCF-Marge (%)": fcf_margin,
        "Erwartung (Analysten-Ziel, €)": target_eur,
        "Upside zum Ziel (%)": upside_pct,
        "Sektor": sector,
        "Industrie": industry,
        "Beta": beta,
        "Dividendenrendite (%)": div_yield_pct,
    }


def compute_score(row: dict) -> dict:
    pe = row.get("KGV")
    rev = row.get("Umsatzwachstum YoY (%)")
    mar = row.get("Operative Marge (%)")
    de = row.get("Debt/Equity")
    fcfm = row.get("FCF-Marge (%)")
    upside = row.get("Upside zum Ziel (%)")
    target_eur = row.get("Erwartung (Analysten-Ziel, €)")

    s_pe, r_pe = score_kgv(to_float(pe))
    s_rev, r_rev = score_wachstum(to_float(rev))
    s_mar, r_mar = score_marge(to_float(mar))
    s_de, r_de = score_verschuldung(to_float(de))
    s_fcf, r_fcf = score_fcf(to_float(fcfm))
    s_an, r_an = score_analyst(to_float(upside), to_float(target_eur))

    weights = {
        "KGV": 0.12,
        "Wachstum": 0.22,
        "Marge": 0.20,
        "Verschuldung": 0.14,
        "FCF": 0.17,
        "Erwartung": 0.15,
    }

    score = (
        s_pe * weights["KGV"] +
        s_rev * weights["Wachstum"] +
        s_mar * weights["Marge"] +
        s_de * weights["Verschuldung"] +
        s_fcf * weights["FCF"] +
        s_an * weights["Erwartung"]
    )

    profil = trend_profil(
        row.get("Sektor"),
        to_float(rev),
        to_float(row.get("Beta")),
        to_float(row.get("Dividendenrendite (%)")),
    )

    return {
        "Score (0–10)": round(score, 2),
        "Ampel": points_to_ampel(score),
        "Entscheidung": decision_from_score(score),
        "Profil": profil,
        "Begründung (kurz)": " | ".join([r_pe, r_rev, r_mar, r_de, r_fcf, r_an]),
    }


# ============================================================
# Positionsänderung (Δ Rang) + “Warum?”
# ============================================================
def delta_arrow(delta_rank: int | None) -> str:
    if delta_rank is None:
        return "⏺"
    if delta_rank > 0:
        return f"▲{int(delta_rank)}"
    if delta_rank < 0:
        return f"▼{abs(int(delta_rank))}"
    return "—"


def build_snapshot(df_sorted: pd.DataFrame) -> dict:
    snap = {}
    for i, r in df_sorted.reset_index(drop=True).iterrows():
        tk = str(r.get("Ticker", "")).upper()
        if not tk:
            continue
        snap[tk] = {
            "Rang": int(i + 1),
            "Score": to_float(r.get("Score (0–10)")),
            "KGV": to_float(r.get("KGV")),
            "Wachstum": to_float(r.get("Umsatzwachstum YoY (%)")),
            "Marge": to_float(r.get("Operative Marge (%)")),
            "FCF": to_float(r.get("FCF-Marge (%)")),
            "DE": to_float(r.get("Debt/Equity")),
            "Upside": to_float(r.get("Upside zum Ziel (%)")),
        }
    return snap


def why_from_prev(curr: dict, prev: dict | None) -> list[str]:
    if not prev:
        return ["Neu oder zuvor nicht vorhanden → kein Vergleich möglich."]

    def ch(label, c, p, unit=""):
        c = to_float(c)
        p = to_float(p)
        if c is None or p is None:
            return None
        d = c - p
        if abs(d) < 1e-9:
            return None
        sign = "↑" if d > 0 else "↓"
        return (abs(d), f"{label}: {p:.2f}{unit} → {c:.2f}{unit} ({sign} {abs(d):.2f}{unit})")

    changes = []
    for item in [
        ch("Score", curr.get("Score (0–10)"), prev.get("Score")),
        ch("KGV", curr.get("KGV"), prev.get("KGV")),
        ch("Wachstum", curr.get("Umsatzwachstum YoY (%)"), prev.get("Wachstum"), "%"),
        ch("Marge", curr.get("Operative Marge (%)"), prev.get("Marge"), "%"),
        ch("FCF-Marge", curr.get("FCF-Marge (%)"), prev.get("FCF"), "%"),
        ch("Debt/Equity", curr.get("Debt/Equity"), prev.get("DE")),
        ch("Upside", curr.get("Upside zum Ziel (%)"), prev.get("Upside"), "%"),
    ]:
        if item:
            changes.append(item)

    changes.sort(key=lambda x: x[0], reverse=True)
    if not changes:
        return ["Keine relevanten Änderungen in den Daten gefunden (oder Daten fehlen)."]
    return [t for _, t in changes[:4]]


# ============================================================
# NEWS
# ============================================================
GLOBAL_RSS_FEEDS = [
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
    ("Reuters Markets", "https://feeds.reuters.com/news/markets"),
    ("MarketWatch Top", "https://feeds.marketwatch.com/marketwatch/topstories/"),
]


@st.cache_data(ttl=60 * 20)
def fetch_global_news(days_back: int = 60, max_items: int = 12) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    items = []
    for source_name, url in GLOBAL_RSS_FEEDS:
        try:
            d = feedparser.parse(url)
            for e in d.entries[: max_items * 2]:
                published = None
                if getattr(e, "published_parsed", None):
                    published = datetime.fromtimestamp(time.mktime(e.published_parsed), tz=timezone.utc)
                elif getattr(e, "updated_parsed", None):
                    published = datetime.fromtimestamp(time.mktime(e.updated_parsed), tz=timezone.utc)

                if published and published < cutoff:
                    continue

                title = getattr(e, "title", None)
                link = getattr(e, "link", None)
                if not title or not link:
                    continue
                items.append({"Quelle": source_name, "Titel": title, "Link": link, "Zeit": published})
        except Exception:
            continue

    seen = set()
    out = []
    for it in sorted(items, key=lambda x: x["Zeit"] or datetime.now(timezone.utc), reverse=True):
        if it["Link"] in seen:
            continue
        seen.add(it["Link"])
        out.append(it)
        if len(out) >= max_items:
            break
    return out


@st.cache_data(ttl=60 * 20)
def fetch_stock_news_yf(ticker: str, days_back: int = 60, max_items: int = 6) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    try:
        t = yf.Ticker(ticker)
        news = getattr(t, "news", None) or []
        items = []
        for n in news:
            title = n.get("title")
            link = n.get("link") or n.get("url")
            pub = n.get("providerPublishTime")
            if not title or not link:
                continue
            dt = None
            if pub:
                try:
                    dt = datetime.fromtimestamp(int(pub), tz=timezone.utc)
                except Exception:
                    dt = None
            if dt and dt < cutoff:
                continue
            items.append({"Titel": title, "Link": link, "Zeit": dt, "Quelle": n.get("publisher") or "Yahoo/Partner"})
        items = sorted(items, key=lambda x: x["Zeit"] or datetime.now(timezone.utc), reverse=True)[:max_items]
        return items
    except Exception:
        return []


# ============================================================
# UI – Header
# ============================================================
st.title("📌 Watchlist + Bewertung (Score) – clean für Handy")
st.caption("Optik wie davor – plus **Pfeile für Positionsänderung**. Klick auf den Pfeil/Zeile → Details → „Warum?“.")


# ============================================================
# Session State
# ============================================================
if "last_df" not in st.session_state:
    st.session_state.last_df = None
if "last_run_at" not in st.session_state:
    st.session_state.last_run_at = None
if "last_errors" not in st.session_state:
    st.session_state.last_errors = []
if "prev_snapshot" not in st.session_state:
    st.session_state.prev_snapshot = None  # ticker -> snapshot
if "why_ticker" not in st.session_state:
    st.session_state.why_ticker = None
if "last_run_epoch" not in st.session_state:
    st.session_state.last_run_epoch = None


# ============================================================
# Controls (oben kompakt)
# ============================================================
sid = get_session_id()
wl = current_watchlist()
by_ticker, by_wkn, by_isin = build_lookup_index(wl)

with st.container():
    c1, c2 = st.columns([1.2, 1])
    with c1:
        query = st.text_input("Suche (Ticker / Name / WKN / ISIN)", value="", placeholder="z.B. NVDA, SAP, DE000..., WKN ...")
    with c2:
        colA, colB = st.columns(2)
        with colA:
            disabled = not can_run_now()
            label = "🔄 Aktualisieren" if not disabled else f"⏳ Bitte warten ({cooldown_left()}s)"
            run_now = st.button(label, type="primary", use_container_width=True, disabled=disabled)
        with colB:
            show_global_news = st.toggle("🌍 Welt-News", value=True)

st.divider()


# ============================================================
# Add / Remove – ohne DB
# ============================================================
with st.expander("➕ Aktie hinzufügen (alle sehen sie, solange die App läuft)", expanded=False):
    add_in = st.text_input("Ticker oder WKN oder ISIN", value="", placeholder="z.B. KO oder DE000... oder WKN")
    add_name = st.text_input("Optional: Name (wenn leer, wird automatisch geholt)", value="")
    add_btn = st.button("Hinzufügen", use_container_width=True)

    if add_btn:
        token = normalize_token(add_in)
        resolved_ticker = None

        if token in by_ticker:
            resolved_ticker = token
        elif is_isin(token) and token in by_isin:
            resolved_ticker = normalize_token(by_isin[token]["Ticker"])
        elif is_wkn(token) and token in by_wkn:
            resolved_ticker = normalize_token(by_wkn[token]["Ticker"])
        else:
            if re.fullmatch(r"[A-Z0-9\.\-]{1,15}", token):
                resolved_ticker = token

        if not resolved_ticker:
            st.error("Konnte das nicht auflösen. WKN/ISIN klappt nur, wenn in deiner Mapping-Liste hinterlegt – sonst Ticker eingeben.")
        else:
            name_final = add_name.strip()
            if not name_final:
                try:
                    info = fetch_stock_info_cached(resolved_ticker)
                    name_final = info.get("Name") or resolved_ticker
                except Exception:
                    name_final = resolved_ticker

            store = global_store()
            already = {normalize_token(x.get("Ticker")) for x in store["added"]}
            base = {normalize_token(x.get("Ticker")) for x in DEFAULT_WATCHLIST}

            if normalize_token(resolved_ticker) in base:
                st.info("Dieser Wert ist schon in der festen Watchlist enthalten.")
            elif normalize_token(resolved_ticker) in already:
                st.info("Dieser Wert wurde bereits hinzugefügt.")
            else:
                store["added"].append(
                    {
                        "Ticker": resolved_ticker,
                        "Name": name_final,
                        "WKN": "",
                        "ISIN": "",
                        "added_by": sid,
                        "added_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                st.success(f"Hinzugefügt: {resolved_ticker} – {name_final}")

    store = global_store()
    mine = [x for x in store["added"] if x.get("added_by") == sid]
    if mine:
        st.markdown("**Deine hinzugefügten Werte (nur du kannst diese löschen):**")
        opts = [f'{x["Ticker"]} – {x.get("Name","")}' for x in mine]
        sel = st.selectbox("Auswahl", opts, index=0)
        if st.button("🗑️ Ausgewählten löschen", use_container_width=True):
            idx = opts.index(sel)
            to_del = mine[idx]
            store["added"] = [x for x in store["added"] if x is not to_del]
            st.success("Gelöscht. (Hinweis: Ohne Datenbank kann nach Neustart alles weg sein.)")
    else:
        st.caption("Du hast in dieser Sitzung noch nichts hinzugefügt.")


# ============================================================
# Filter Watchlist
# ============================================================
wl = current_watchlist()

def filter_watchlist(rows: list[dict], q: str) -> list[dict]:
    q = (q or "").strip().lower()
    if not q:
        return rows
    out = []
    for r in rows:
        t = (r.get("Ticker") or "").lower()
        n = (r.get("Name") or "").lower()
        w = (r.get("WKN") or "").lower()
        i = (r.get("ISIN") or "").lower()
        if q in t or q in n or q in w or q in i:
            out.append(r)
    return out

wl_filtered = filter_watchlist(wl, query)


# ============================================================
# Fetch & compute (nur bei Klick oder erstem Start)
# ============================================================
if run_now or st.session_state.last_df is None:
    st.session_state.last_run_epoch = time.time()

    rows = []
    errors = []

    for r in wl_filtered:
        tk = normalize_token(r.get("Ticker"))
        if not tk:
            continue
        try:
            info = fetch_stock_info_cached(tk)

            # WKN/ISIN aus watchlist “drüberkopieren”
            info["WKN"] = r.get("WKN", "")
            info["ISIN"] = r.get("ISIN", "")

            # Score
            s = compute_score(info)
            info.update(s)

            rows.append(info)
        except Exception as e:
            errors.append((tk, str(e)))

    df = pd.DataFrame(rows)
    st.session_state.last_df = df
    st.session_state.last_run_at = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    st.session_state.last_errors = errors

df = st.session_state.last_df if st.session_state.last_df is not None else pd.DataFrame()


# ============================================================
# Display – Top 10 + Watchlist (Optik wie davor) + Pfeile
# ============================================================
st.subheader("🏆 Top 10 (nach Score)")
st.caption(f"Zuletzt aktualisiert: {st.session_state.last_run_at or '—'}")

if df.empty:
    st.info("Noch keine Daten. Klick auf **Aktualisieren**.")
else:
    df_sorted = df.sort_values(by=["Score (0–10)"], ascending=False).reset_index(drop=True)
    df_sorted["Rang"] = range(1, len(df_sorted) + 1)

    prev = st.session_state.prev_snapshot or {}

    def calc_delta_rank(tk: str, curr_rank: int) -> int | None:
        p = prev.get(tk)
        if not p:
            return None
        return p.get("Rang") - curr_rank  # + = besser

    def calc_delta_score(tk: str, curr_score: float | None) -> float | None:
        p = prev.get(tk)
        if not p:
            return None
        ps = p.get("Score")
        if curr_score is None or ps is None:
            return None
        return round(curr_score - ps, 2)

    df_sorted["Δ Rang"] = df_sorted.apply(lambda x: calc_delta_rank(str(x["Ticker"]).upper(), int(x["Rang"])), axis=1)
    df_sorted["Δ Score"] = df_sorted.apply(lambda x: calc_delta_score(str(x["Ticker"]).upper(), to_float(x["Score (0–10)"])), axis=1)
    df_sorted["Pfeil"] = df_sorted["Δ Rang"].apply(lambda dr: delta_arrow(dr))

    top10 = df_sorted.head(10).copy()
    top10_tickers = set(top10["Ticker"].astype(str).str.upper().tolist())
    rest = df_sorted[~df_sorted["Ticker"].astype(str).str.upper().isin(top10_tickers)].copy()

    cols_top = [
        "Pfeil",
        "Rang",
        "Ampel",
        "Entscheidung",
        "Score (0–10)",
        "Profil",
        "Ticker",
        "Name",
        "Kurs (€)",
        "KGV",
        "Erwartung (Analysten-Ziel, €)",
        "Upside zum Ziel (%)",
        "Δ Score",
    ]
    cols_top = [c for c in cols_top if c in top10.columns]

    # ✅ Tabellen-Optik wie davor, aber klickbar via Selection
    sel_top = st.dataframe(
        top10[cols_top],
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
    )

    # Wenn Zeile gewählt: merken wir den Ticker als "why"
    try:
        if sel_top and sel_top.get("selection") and sel_top["selection"].get("rows"):
            ridx = sel_top["selection"]["rows"][0]
            if 0 <= ridx < len(top10):
                st.session_state.why_ticker = str(top10.iloc[ridx]["Ticker"]).upper()
    except Exception:
        pass

    st.markdown("")
    st.subheader("📋 Gesamte Watchlist")
    cols_all = [
        "Pfeil",
        "Rang",
        "Ampel",
        "Entscheidung",
        "Score (0–10)",
        "Profil",
        "Ticker",
        "Name",
        "Kurs (€)",
        "KGV",
        "Erwartung (Analysten-Ziel, €)",
        "Upside zum Ziel (%)",
        "Δ Score",
        "Sektor",
    ]
    cols_all = [c for c in cols_all if c in rest.columns]

    sel_rest = st.dataframe(
        rest[cols_all],
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
    )

    try:
        if sel_rest and sel_rest.get("selection") and sel_rest["selection"].get("rows"):
            ridx = sel_rest["selection"]["rows"][0]
            if 0 <= ridx < len(rest):
                st.session_state.why_ticker = str(rest.iloc[ridx]["Ticker"]).upper()
    except Exception:
        pass

    # “Warum?” (Option 2): Erklärung im Details-Aufklapper (oder direkt unten als Hilfe)
    if st.session_state.why_ticker:
        tk = st.session_state.why_ticker
        crow = df_sorted[df_sorted["Ticker"].astype(str).str.upper() == tk]
        if not crow.empty:
            curr = crow.iloc[0].to_dict()
            prev_row = prev.get(tk)
            with st.expander(f"Warum? – {tk} (Positions-/Datenänderung)", expanded=False):
                for line in why_from_prev(curr, prev_row):
                    st.write(f"- {line}")
                st.caption("Hinweis: Vergleich seit dem letzten Aktualisieren. (Beim ersten Lauf gibt’s noch keinen Vergleich.)")

    # ============================================================
    # Details & News je Aktie (aufklappbar) – Option 2 „Warum?“ hier
    # ============================================================
    st.markdown("")
    st.subheader("🔎 Details & News je Aktie (aufklappen)")

    display_order = pd.concat([top10, rest], ignore_index=True)
    options = display_order["Ticker"].astype(str).tolist()
    selected = st.multiselect(
        "Wähle eine oder mehrere Aktien aus",
        options=options,
        default=options[:3] if len(options) >= 3 else options,
    )

    for tk in selected:
        row = display_order[display_order["Ticker"] == tk].head(1)
        if row.empty:
            continue
        r = row.iloc[0].to_dict()
        tk_up = str(tk).upper()

        header = f'{r.get("Ampel","")}  {r.get("Pfeil","")}  {tk_up} – {r.get("Name","")}'
        with st.expander(header, expanded=False):
            cA, cB, cC = st.columns(3)
            with cA:
                st.metric("Kurs (€)", fmt_eur(r.get("Kurs (€)")))
                st.metric("KGV", fmt_num(r.get("KGV"), 1))
            with cB:
                st.metric("Erwartung (€)", fmt_eur(r.get("Erwartung (Analysten-Ziel, €)")))
                st.metric("Upside (%)", fmt_pct(r.get("Upside zum Ziel (%)"), 1))
            with cC:
                st.metric("Score (0–10)", fmt_num(r.get("Score (0–10)"), 2))
                st.write(f'**Profil:** {r.get("Profil","—")}')

            st.markdown("**Kurzbegründung:**")
            st.write(r.get("Begründung (kurz)", "—"))

            # ✅ Option 2: Warum nur hier
            if st.button("Warum hat sich der Rang geändert?", key=f"why_btn_{tk_up}"):
                prev_row = prev.get(tk_up)
                st.markdown("**Warum? (seit letztem Update)**")
                for line in why_from_prev(r, prev_row):
                    st.write(f"- {line}")

            mini = {
                "Umsatzwachstum YoY (%)": r.get("Umsatzwachstum YoY (%)"),
                "Operative Marge (%)": r.get("Operative Marge (%)"),
                "FCF-Marge (%)": r.get("FCF-Marge (%)"),
                "Debt/Equity": r.get("Debt/Equity"),
                "Beta": r.get("Beta"),
                "Dividendenrendite (%)": r.get("Dividendenrendite (%)"),
                "Industrie": r.get("Industrie"),
                "WKN": r.get("WKN"),
                "ISIN": r.get("ISIN"),
            }
            st.dataframe(pd.DataFrame([mini]), use_container_width=True, hide_index=True)

            st.markdown("**Aktien-News (aktuell & relevant):**")
            news = fetch_stock_news_yf(tk_up, days_back=60, max_items=6)
            if not news:
                st.caption("Keine passenden aktuellen News gefunden.")
            else:
                first = news[:2]
                restn = news[2:]
                for it in first:
                    ts = it["Zeit"].strftime("%d.%m.%Y") if it.get("Zeit") else ""
                    st.markdown(
                        f'- [{it["Titel"]}]({it["Link"]})  \n'
                        f'<span class="small-muted">{it.get("Quelle","")} • {ts}</span>',
                        unsafe_allow_html=True
                    )
                if restn:
                    with st.expander("Mehr Aktien-News anzeigen", expanded=False):
                        for it in restn:
                            ts = it["Zeit"].strftime("%d.%m.%Y") if it.get("Zeit") else ""
                            st.markdown(
                                f'- [{it["Titel"]}]({it["Link"]})  \n'
                                f'<span class="small-muted">{it.get("Quelle","")} • {ts}</span>',
                                unsafe_allow_html=True
                            )

    # Snapshot nach Rendern speichern (für nächstes Update)
    st.session_state.prev_snapshot = build_snapshot(df_sorted)


# ============================================================
# Welt-News
# ============================================================
if show_global_news:
    st.markdown("")
    st.subheader("🌍 Weltweite Schlagzeilen (seriöse RSS-Feeds)")
    global_news = fetch_global_news(days_back=60, max_items=10)

    if not global_news:
        st.caption("Keine neuen/geeigneten Welt-Schlagzeilen in den letzten Wochen gefunden.")
    else:
        head = global_news[:4]
        tail = global_news[4:]

        for it in head:
            ts = it["Zeit"].astimezone().strftime("%d.%m.%Y") if it.get("Zeit") else ""
            st.markdown(
                f'- [{it["Titel"]}]({it["Link"]})  \n'
                f'<span class="small-muted">{it["Quelle"]} • {ts}</span>',
                unsafe_allow_html=True
            )

        if tail:
            with st.expander("Mehr Welt-News anzeigen", expanded=False):
                for it in tail:
                    ts = it["Zeit"].astimezone().strftime("%d.%m.%Y") if it.get("Zeit") else ""
                    st.markdown(
                        f'- [{it["Titel"]}]({it["Link"]})  \n'
                        f'<span class="small-muted">{it["Quelle"]} • {ts}</span>',
                        unsafe_allow_html=True
                    )


# ============================================================
# Errors
# ============================================================
errs = st.session_state.get("last_errors", [])
if errs:
    st.markdown("")
    with st.expander("⚠️ Hinweise / Fehler (einige Ticker konnten nicht geladen werden)", expanded=False):
        for tk, msg in errs:
            st.write(f"- **{tk}**: {msg}")


# ============================================================
# Footer / Kurz-Erklärung
# ============================================================
st.markdown("---")
with st.expander("ℹ️ So funktioniert das Bewertungssystem (kurz)", expanded=False):
    st.write(
        """
**Score (0–10)** = gewichtete Punkte aus:
- **Wachstum**, **Profitabilität**, **Cashflow**, **Verschuldung**, **KGV**, **Erwartung (Analysten-Ziel)**  

Ampel:
- 🟢 **≥ 7.0** = Kaufen
- 🟡 **≥ 5.5** = Beobachten
- 🔴 **< 5.5** = Nicht kaufen

Pfeile (Positionsänderung):
- **▲** = im Ranking gestiegen, **▼** = gefallen, **⏺/—** = keine/unklar (z.B. beim ersten Lauf)
- Vergleich funktioniert, sobald du mindestens **2× aktualisiert** hast.
- „Warum?“ siehst du im **Details-Aufklapper** per Button.
"""
    )
