import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone, timedelta
import requests
import xml.etree.ElementTree as ET
import re
from email.utils import parsedate_to_datetime

st.set_page_config(page_title="Aktienbewertung – Profi-Score (0–100)", layout="wide")

# =========================================================
# 1) Öffentliche Haupt-Watchlist (Ticker + Name) – immer sichtbar
# =========================================================
DEFAULT_WATCHLIST = [
    "IWDA.L",      # iShares Core MSCI World (LSE)
    "EIMI.L",      # iShares MSCI Emerging Markets (LSE)
    "SMT.L",       # Scottish Mortgage Trust
    "ABBN.SW",     # ABB (Schweiz)
    "AAPL",        # Apple
    "KO",          # Coca-Cola
    "META",        # Meta Platforms
    "NFLX",        # Netflix
    "NVDA",        # NVIDIA
    "CRWD",        # CrowdStrike
    "NTLA",        # Intellia Therapeutics
    "NOVO-B.CO"    # Novo Nordisk B (Kopenhagen)
]
MASTER_WATCHLIST = [
    {"ticker": "AAPL", "name": "Apple Inc."},
    {"ticker": "MSFT", "name": "Microsoft Corp."},
    {"ticker": "NVDA", "name": "NVIDIA Corp."},
    {"ticker": "GOOGL", "name": "Alphabet Inc. (Class A)"},
    {"ticker": "AMZN", "name": "Amazon.com Inc."},
    {"ticker": "TSLA", "name": "Tesla Inc."},
    {"ticker": "TSM", "name": "Taiwan Semiconductor (ADR)"},
    {"ticker": "V", "name": "Visa Inc."},
    {"ticker": "JNJ", "name": "Johnson & Johnson"},
    {"ticker": "MCD", "name": "McDonald's Corp."},
    {"ticker": "WMT", "name": "Walmart Inc."},
    {"ticker": "WM", "name": "Waste Management Inc."},
    {"ticker": "ASML", "name": "ASML Holding N.V."},
    {"ticker": "NET", "name": "Cloudflare Inc."},
    {"ticker": "XOM", "name": "Exxon Mobil Corp."},
    {"ticker": "NTLA", "name": "Intellia Therapeutics Inc."},
    {"ticker": "ALV.DE", "name": "Allianz SE"},
    {"ticker": "MUV2.DE", "name": "Münchener Rückversicherung AG"},
    {"ticker": "RHM.DE", "name": "Rheinmetall AG"},
    {"ticker": "SAP.DE", "name": "SAP SE"},
    {"ticker": "SIE.DE", "name": "Siemens AG"},
    {"ticker": "ENR.DE", "name": "Siemens Energy AG"},
    {"ticker": "NESN.SW", "name": "Nestlé S.A."},
    {"ticker": "NOVN.SW", "name": "Novartis AG"},
    {"ticker": "ROG.SW", "name": "Roche Holding AG"},
    {"ticker": "RBI.VI", "name": "Raiffeisen Bank International AG"},
    {"ticker": "SU.PA", "name": "Schneider Electric SE"},
    {"ticker": "DIS", "name": "The Walt Disney Company"},
    {"ticker": "SHOP", "name": "Shopify Inc."},
]

# =========================================================
# 2) Scoring – verschärft + Branchenprofile + Kategorien
#    - Score 0–100 (gewichtete Ampeln)
#    - Gelb/Unklar zählen nur 30%
#    - Schwellen für Entscheidung: Kaufen ab 75, Beobachten ab 62
# =========================================================
AMP_TO_PCT = {"🟢": 1.00, "🟡": 0.30, "🔴": 0.00, "⚪": 0.30}

THRESHOLDS_DEFAULT = {
    "pe": (30, 60),
    "de": (1.5, 3.0),
    "growth": (15, 5),
    "margin": (15, 8),
    "fcf": (10, 3),
}

PROFILE_RULES = {
    "Tech/Software": {
        "weights": {"growth": 30, "margin": 25, "fcf": 25, "de": 10, "pe": 10},
        "thresholds": {"pe": (40, 80), "de": (2.0, 4.0)},
    },
    "Industrie/Zyklisch": {
        "weights": {"growth": 25, "margin": 20, "fcf": 20, "de": 20, "pe": 15},
        "thresholds": {"pe": (35, 70), "de": (2.0, 4.0)},
    },
    "Konsum/Marke": {
        "weights": {"growth": 15, "margin": 30, "fcf": 30, "de": 15, "pe": 10},
        "thresholds": {"pe": (35, 70), "de": (2.0, 4.0)},
    },
    "Finanzen/Banken": {
        "weights": {"growth": 25, "margin": 35, "fcf": 20, "de": 0, "pe": 20},
        "thresholds": {"pe": (25, 45)},
    },
    "Default": {
        "weights": {"growth": 25, "margin": 20, "fcf": 20, "de": 20, "pe": 15},
        "thresholds": {},
    },
}

FINANCE_HINTS = ("Bank", "Insurance", "Financial", "Credit", "Capital Markets")

BUY_SCORE = 75.0
WATCH_SCORE = 62.0


def apply_red_flags(score_0_100: float, oper_margin_pct, fcf_margin_pct, debt_to_equity, profile_name: str):
    """Red Flags bleiben intern + in Details sichtbar, aber nicht mehr in der Haupttabelle."""
    flags = []
    score = score_0_100
    ignore_de = (profile_name == "Finanzen/Banken")

    if fcf_margin_pct is not None and fcf_margin_pct < 0:
        flags.append("Free Cashflow negativ → max. BEOBACHTEN")
        score = min(score, BUY_SCORE - 0.1)

    if oper_margin_pct is not None and oper_margin_pct < 5:
        flags.append("Operative Marge < 5% → max. BEOBACHTEN")
        score = min(score, BUY_SCORE - 0.1)

    if (not ignore_de) and debt_to_equity is not None and fcf_margin_pct is not None:
        if debt_to_equity > 3.0 and fcf_margin_pct < 5:
            flags.append("Debt/Equity > 3 UND FCF < 5% → Score halbiert")
            score = score * 0.5

    return score, flags


def base_decision(score_0_100: float) -> str:
    if score_0_100 >= BUY_SCORE:
        return "🟢 KAUFEN"
    if score_0_100 >= WATCH_SCORE:
        return "🟡 BEOBACHTEN"
    return "🔴 NICHT KAUFEN"


def analyst_signal(rec_key: str | None, rec_mean: float | None):
    key = (rec_key or "").lower().strip()
    if key in ("strong_buy", "buy"):
        return "🟢 Analysten: Kauf"
    if key in ("hold", "neutral"):
        return "🟡 Analysten: Neutral"
    if key in ("sell", "strong_sell", "underperform", "underweight"):
        return "🔴 Analysten: Verkauf"

    if rec_mean is not None:
        if rec_mean <= 2.0:
            return "🟢 Analysten: Kauf"
        if rec_mean <= 3.0:
            return "🟡 Analysten: Neutral"
        return "🔴 Analysten: Verkauf"

    return "⚪ Analysten: Unklar"


def final_bucket(decision: str, style: str, analyst_sig: str):
    if decision.startswith("🟢"):
        if analyst_sig.startswith("🟢"):
            return "🟢 Kauf (Analysten)"
        if style == "Megatrend":
            return "🚀 Kauf (Megatrend)"
        if style == "Defensiv":
            return "🛡️ Defensiv kaufen"
        return "🟢 Kaufen (Core)"

    if decision.startswith("🟡"):
        if style == "Megatrend":
            return "🚀 Beobachten (Megatrend)"
        if style == "Defensiv":
            return "🛡️ Beobachten (Defensiv)"
        return "🟡 Beobachten"

    return "🔴 Nicht kaufen"


# =========================================================
# 3) Helper
# =========================================================
def safe_get(d: dict, key: str):
    val = d.get(key)
    return None if val in [None, "None", "nan"] else val


def to_float(x):
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def to_pct(x):
    if x is None:
        return None
    try:
        return float(x) * 100.0
    except Exception:
        return None


def norm_ticker(t: str) -> str:
    return (t or "").upper().strip()


def clamp(v, lo, hi):
    if v is None:
        return None
    return max(lo, min(hi, v))


def as_score_0_10(score_0_100: float) -> float:
    return round(score_0_100 / 10.0, 1)


# =========================================================
# 4) FX: Umrechnung in EUR
# =========================================================
@st.cache_data(ttl=60 * 30)
def fx_rate_to_eur(from_ccy: str) -> float | None:
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


def convert_money_to_eur(row: dict) -> dict:
    ccy = row.get("Währung", "Unbekannt")
    rate = fx_rate_to_eur(ccy)
    row["FX → EUR"] = rate

    def conv(v):
        v = to_float(v)
        if v is None:
            return None
        if rate is None:
            return v
        return v * rate

    row["Kurs (€)"] = conv(row.get("Kurs"))
    row["Erwartung (€)"] = conv(row.get("Erwartung (raw)"))
    return row


def calc_potential_pct(price_eur: float | None, exp_eur: float | None) -> float | None:
    if price_eur is None or exp_eur is None or price_eur <= 0:
        return None
    return (exp_eur / price_eur - 1.0) * 100.0


# =========================================================
# 5) Branchenprofil ermitteln (best effort)
# =========================================================
def detect_profile(sector: str | None, industry: str | None) -> str:
    s = (sector or "").strip()
    i = (industry or "").strip()

    if any(h.lower() in i.lower() for h in FINANCE_HINTS) or any(h.lower() in s.lower() for h in ("financial", "banks", "insurance")):
        return "Finanzen/Banken"
    if any(h.lower() in i.lower() for h in ("software", "semiconductor", "internet", "data", "cyber")) or "technology" in s.lower():
        return "Tech/Software"
    if any(h.lower() in i.lower() for h in ("retail", "food", "beverage", "household", "consumer")) or "consumer" in s.lower():
        return "Konsum/Marke"
    if any(h.lower() in i.lower() for h in ("industrial", "aerospace", "defense", "machinery", "automation")) or any(
        x in s.lower() for x in ("industrials", "energy", "utilities", "basic materials")
    ):
        return "Industrie/Zyklisch"
    return "Default"


def get_thresholds_for_profile(profile_name: str) -> dict:
    base = dict(THRESHOLDS_DEFAULT)
    override = PROFILE_RULES.get(profile_name, PROFILE_RULES["Default"]).get("thresholds", {})
    for k, v in override.items():
        base[k] = v
    return base


def get_weights_for_profile(profile_name: str) -> dict:
    return PROFILE_RULES.get(profile_name, PROFILE_RULES["Default"])["weights"]


# =========================================================
# 6) Ampeln pro Kriterium
# =========================================================
def ampel_pe(pe: float | None, thresholds: dict) -> str:
    if pe is None or pe <= 0:
        return "⚪ Unklar"
    g, y = thresholds["pe"]
    if pe <= g:
        return "🟢 Grün"
    if pe <= y:
        return "🟡 Gelb"
    return "🔴 Rot"


def ampel_growth(rev_growth_pct: float | None, thresholds: dict) -> str:
    if rev_growth_pct is None:
        return "⚪ Unklar"
    g, y = thresholds["growth"]
    if rev_growth_pct >= g:
        return "🟢 Grün"
    if rev_growth_pct >= y:
        return "🟡 Gelb"
    return "🔴 Rot"


def ampel_margin(oper_margin_pct: float | None, thresholds: dict) -> str:
    if oper_margin_pct is None:
        return "⚪ Unklar"
    g, y = thresholds["margin"]
    if oper_margin_pct >= g:
        return "🟢 Grün"
    if oper_margin_pct >= y:
        return "🟡 Gelb"
    return "🔴 Rot"


def ampel_de(debt_to_equity: float | None, thresholds: dict, profile_name: str) -> str:
    if profile_name == "Finanzen/Banken":
        return "⚪ Unklar"
    if debt_to_equity is None or debt_to_equity < 0:
        return "⚪ Unklar"
    g, y = thresholds["de"]
    if debt_to_equity <= g:
        return "🟢 Grün"
    if debt_to_equity <= y:
        return "🟡 Gelb"
    return "🔴 Rot"


def ampel_fcf(fcf_margin_pct: float | None, thresholds: dict) -> str:
    if fcf_margin_pct is None:
        return "⚪ Unklar"
    g, y = thresholds["fcf"]
    if fcf_margin_pct >= g:
        return "🟢 Grün"
    if fcf_margin_pct >= y:
        return "🟡 Gelb"
    return "🔴 Rot"


def amp_to_pct(ampel_text: str) -> float:
    if "🟢" in ampel_text:
        return AMP_TO_PCT["🟢"]
    if "🟡" in ampel_text:
        return AMP_TO_PCT["🟡"]
    if "🔴" in ampel_text:
        return AMP_TO_PCT["🔴"]
    return AMP_TO_PCT["⚪"]


def weighted_score_0_100(ampels: dict, weights: dict) -> float:
    total_weight = sum(weights.values()) if weights else 100
    if total_weight <= 0:
        return 0.0
    s = 0.0
    for key, w in weights.items():
        pct = amp_to_pct(ampels.get(key, "⚪ Unklar"))
        s += w * pct
    return (s / total_weight) * 100.0


# =========================================================
# 7) Megatrend / Defensiv (DEINE 2 REGELN)
# =========================================================
def classify_style(
    sector: str | None,
    beta: float | None,
    dividend_yield_pct: float | None,
    a_growth: str,
    a_margin: str,
    a_fcf: str,
):
    s = (sector or "").lower()

    defensive_sector = any(x in s for x in ("consumer defensive", "utilities", "healthcare"))

    # Regel 1: Megatrend = Wachstum 🟢 UND (Marge 🟢 ODER FCF 🟢)
    is_megatrend = ("🟢" in a_growth) and (("🟢" in a_margin) or ("🟢" in a_fcf))
    if is_megatrend:
        return "Megatrend"

    # Regel 2: Defensiv = Beta ≤ 1.0 UND (Dividende ≥ 2.5% ODER defensiver Sektor)
    low_beta = (beta is not None and beta <= 1.0)
    decent_div = (dividend_yield_pct is not None and dividend_yield_pct >= 2.5)
    is_defensive = low_beta and (decent_div or defensive_sector)

    if is_defensive:
        return "Defensiv"

    return "Core"


# =========================================================
# 8) Datenabruf (yfinance)
# =========================================================
def fetch_yfinance_raw(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.get_info() or {}

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
    if fcf is not None and revenue is not None and revenue != 0:
        fcf_margin = (fcf / revenue) * 100.0

    name = safe_get(info, "shortName") or safe_get(info, "longName") or ticker.upper()
    ccy = (safe_get(info, "currency") or "").upper()
    isin = safe_get(info, "isin")
    sector = safe_get(info, "sector")
    industry = safe_get(info, "industry")

    beta = to_float(safe_get(info, "beta"))
    div_yield = safe_get(info, "dividendYield")
    dividend_yield_pct = to_pct(div_yield)  # 0.02 -> 2%

    rec_key = safe_get(info, "recommendationKey")
    rec_mean = to_float(safe_get(info, "recommendationMean"))

    # Erwartung: Analysten-Konsens (Ø)
    expectation_raw = to_float(safe_get(info, "targetMeanPrice"))

    return {
        "Ticker": ticker.upper(),
        "Name": name,
        "ISIN": isin,
        "Sektor": sector,
        "Industrie": industry,
        "Währung": ccy if ccy else "Unbekannt",
        "Kurs": price,
        "KGV": pe,
        "Umsatzwachstum YoY (%)": rev_growth,
        "Operative Marge (%)": oper_margin,
        "Debt/Equity": debt_to_equity,
        "FCF-Marge (%)": fcf_margin,
        "Beta": beta,
        "Dividendenrendite (%)": dividend_yield_pct,
        "Analysten Key": rec_key,
        "Analysten Mean": rec_mean,
        "Erwartung (raw)": expectation_raw,  # Originalwährung
    }


# =========================================================
# 9) WKN/ISIN -> Ticker (OpenFIGI)
# =========================================================
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")
WKN_RE = re.compile(r"^[A-Z0-9]{6}$")

EXCH_TO_SUFFIX = {
    "GY": ".DE",
    "GR": ".DE",
    "SW": ".SW",
    "VX": ".VI",
    "FP": ".PA",
    "NA": ".AS",
    "IM": ".MI",
    "LN": ".L",
}

@st.cache_data(ttl=60 * 60)
def resolve_via_openfigi(input_code: str):
    code = norm_ticker(input_code)
    if not code:
        return None

    if ISIN_RE.match(code):
        id_type = "ID_ISIN"
    elif WKN_RE.match(code):
        id_type = "ID_WERTPAPIER"
    else:
        return None

    payload = [{"idType": id_type, "idValue": code}]
    try:
        r = requests.post("https://api.openfigi.com/v3/mapping", json=payload, timeout=12)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data or "data" not in data[0] or not data[0]["data"]:
            return None

        hit = data[0]["data"][0]
        ticker = (hit.get("ticker") or "").upper().strip()
        name = (hit.get("name") or "").strip()
        exch = (hit.get("exchCode") or "").upper().strip()
        if not ticker:
            return None

        suffix = EXCH_TO_SUFFIX.get(exch, "")
        yahoo_ticker = ticker + suffix if suffix and "." not in ticker else ticker
        isin = hit.get("securityIdentifier")

        return {"ticker": yahoo_ticker, "name": name or yahoo_ticker, "isin": isin, "exch": exch}
    except Exception:
        return None


def classify_input(user_input: str) -> str:
    code = norm_ticker(user_input)
    if ISIN_RE.match(code):
        return "ISIN"
    if WKN_RE.match(code):
        return "WKN"
    return "TICKER"


# =========================================================
# 10) News (Google News RSS) – “nicht zu viel”, “nicht zu alt”
#    - max pro Aktie: 5
#    - max Welt: 10
#    - bis 2 Monate, aber nur wenn “wichtig”
#    - wenn nichts passt: einfach nichts anzeigen
# =========================================================
IMPORTANT_KEYWORDS = [
    # DE
    "quartal", "quartalszahlen", "jahreszahlen", "ausblick", "prognose", "gewinnwarnung", "gewinn", "umsatz",
    "übernahme", "fusion", "deal", "partnerschaft", "auftrag", "großauftrag",
    "ceo", "cfo", "vorstand", "managementwechsel",
    "klage", "strafe", "ermittlung", "regulierung",
    "dividende", "aktienrückkauf", "buyback",
    "hochgestuft", "herabgestuft", "rating",
    # EN
    "earnings", "guidance", "forecast", "outlook", "revenue", "profit", "warning",
    "acquisition", "merger", "deal", "partnership", "contract", "order",
    "ceo", "cfo", "resigns", "steps down",
    "lawsuit", "fine", "investigation", "regulation",
    "dividend", "buyback", "share repurchase",
    "upgrade", "downgrade", "rating",
    # Macro / Markets
    "fed", "ecb", "interest rate", "zinsen", "inflation", "rezession", "geopolit", "krieg"
]

@st.cache_data(ttl=10 * 60)
def google_news_rss_raw(query: str, max_items: int = 25):
    url = "https://news.google.com/rss/search"
    params = {"q": query, "hl": "de", "gl": "AT", "ceid": "AT:de"}
    r = requests.get(url, params=params, timeout=12)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    items = []
    for item in root.findall(".//item")[:max_items]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub = item.findtext("pubDate") or ""
        items.append({"title": title, "link": link, "pub": pub})
    return items


def parse_pubdate(pub: str):
    if not pub:
        return None
    try:
        dt = parsedate_to_datetime(pub)
        # normalize
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def is_important_title(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in IMPORTANT_KEYWORDS)


def filter_news(items, now_utc: datetime, max_age_days: int = 60, recent_days: int = 7, max_keep: int = 5):
    """
    Regel:
    - Zeige nur Items <= 60 Tage
    - Behalte Items, wenn:
        (a) sehr aktuell (<= recent_days) ODER
        (b) wichtig (keyword)
    - Rückgabe max_keep
    """
    if not items:
        return []

    out = []
    for it in items:
        dt = parse_pubdate(it.get("pub", ""))
        if dt is None:
            # ohne Datum: nur, wenn "wichtig"
            if is_important_title(it.get("title", "")):
                out.append({**it, "dt": None})
            continue

        age = now_utc - dt
        if age > timedelta(days=max_age_days):
            continue

        if age <= timedelta(days=recent_days) or is_important_title(it.get("title", "")):
            out.append({**it, "dt": dt})

    # Sort: zuerst Datum (neu -> alt), dann ohne Datum zuletzt
    out.sort(key=lambda x: (x["dt"] is None, x["dt"] or now_utc), reverse=False)
    # obige Sortierung ist “None last” aber älteste zuerst; wir drehen jetzt um:
    out = list(reversed(out))

    return out[:max_keep]


# =========================================================
# 11) UI State
# =========================================================
if "extra_watchlist" not in st.session_state:
    st.session_state["extra_watchlist"] = []

if "last_df" not in st.session_state:
    st.session_state["last_df"] = None


def combined_watchlist_rows():
    combined = {norm_ticker(x["ticker"]): {"Ticker": norm_ticker(x["ticker"]), "Name": x["name"], "ISIN": ""} for x in MASTER_WATCHLIST}
    for x in st.session_state["extra_watchlist"]:
        t = norm_ticker(x.get("ticker"))
        if t and t not in combined:
            combined[t] = {"Ticker": t, "Name": x.get("name", ""), "ISIN": x.get("isin") or ""}
    rows = list(combined.values())
    rows.sort(key=lambda r: r["Ticker"])
    return rows


# =========================================================
# 12) Sidebar
# =========================================================
st.title("📈 Aktienbewertung – Profi-Score (0–100)")
st.caption(
    "Handy-clean: Top 10 bleiben sichtbar, Gesamtliste ohne Dopplung. "
    "KGV immer sichtbar. News: max. wenige, bis 2 Monate wenn wichtig – sonst nichts."
)

with st.sidebar:
    st.header("📌 Watchlist")
    st.caption("Hauptliste fix. Eigene Adds nur für diese Sitzung (Tab).")
    st.dataframe(pd.DataFrame(combined_watchlist_rows()), use_container_width=True, height=260)

    st.subheader("➕ Hinzufügen (Ticker / WKN / ISIN)")
    inp = st.text_input("Eingabe", placeholder="z.B. SAP.DE oder 716460 oder US0378331005")
    add_btn = st.button("➕ Zur Sitzung hinzufügen")

    if add_btn:
        raw = (inp or "").strip()
        if not raw:
            st.warning("Bitte etwas eingeben.")
        else:
            code_type = classify_input(raw)
            if code_type in ("ISIN", "WKN"):
                resolved = resolve_via_openfigi(raw)
                if resolved is None:
                    st.error("ISIN/WKN konnte nicht eindeutig aufgelöst werden. Bitte Ticker direkt eingeben (z.B. SAP.DE).")
                else:
                    st.session_state["extra_watchlist"].append(
                        {
                            "raw": raw,
                            "type": code_type,
                            "ticker": resolved["ticker"],
                            "name": resolved["name"],
                            "isin": resolved.get("isin") or (raw if code_type == "ISIN" else ""),
                        }
                    )
                    st.success(f"Hinzugefügt: {resolved['ticker']} – {resolved['name']}")
            else:
                tkr = norm_ticker(raw)
                existing = {r["Ticker"] for r in combined_watchlist_rows()}
                if tkr in existing:
                    st.info("Dieser Ticker ist schon in der Liste.")
                else:
                    try:
                        info = yf.Ticker(tkr).get_info() or {}
                        nm = info.get("shortName") or info.get("longName") or tkr
                        isin = info.get("isin") or ""
                    except Exception:
                        nm = tkr
                        isin = ""
                    st.session_state["extra_watchlist"].append({"raw": raw, "type": "TICKER", "ticker": tkr, "name": nm, "isin": isin})
                    st.success(f"Hinzugefügt: {tkr} – {nm}")

    st.subheader("🗑️ Meine hinzugefügten (Sitzung)")
    if st.session_state["extra_watchlist"]:
        for i, x in enumerate(list(st.session_state["extra_watchlist"])):
            col1, col2 = st.columns([4, 1])
            col1.write(f"{x.get('ticker','')} – {x.get('name','')}")
            if col2.button("🗑️", key=f"del_{i}_{x.get('ticker','')}"):
                st.session_state["extra_watchlist"].pop(i)
                st.rerun()
        if st.button("🧹 Alle eigenen entfernen"):
            st.session_state["extra_watchlist"] = []
            st.rerun()
    else:
        st.caption("Noch nichts hinzugefügt.")

    st.divider()
    auto_fetch = st.checkbox("Beim Laden automatisch abrufen", value=True)
    show_news = st.checkbox("News anzeigen (Welt + ausgewählte Aktien)", value=False)


# =========================================================
# 13) Controls
# =========================================================
colA, colB, colC = st.columns([1, 2, 2])
with colA:
    fetch_now = st.button("🔄 Aktualisieren", type="primary")
with colB:
    search = st.text_input("🔎 Suche (Ticker / Name)", placeholder="z.B. NVDA oder Siemens")
with colC:
    st.write("")

if "has_run" not in st.session_state:
    st.session_state.has_run = False

tickers_rows = combined_watchlist_rows()
tickers = [r["Ticker"] for r in tickers_rows]
should_run = fetch_now or (auto_fetch and not st.session_state.has_run)


# =========================================================
# 14) Build DataFrame
# =========================================================
def build_dataframe():
    rows = []
    errors = []

    for tk in tickers:
        try:
            data = fetch_yfinance_raw(tk)
            data = convert_money_to_eur(data)

            profile = detect_profile(data.get("Sektor"), data.get("Industrie"))
            thresholds = get_thresholds_for_profile(profile)
            weights = get_weights_for_profile(profile)

            # Ampeln
            a_pe = ampel_pe(data.get("KGV"), thresholds)
            a_growth = ampel_growth(data.get("Umsatzwachstum YoY (%)"), thresholds)
            a_margin = ampel_margin(data.get("Operative Marge (%)"), thresholds)
            a_de = ampel_de(data.get("Debt/Equity"), thresholds, profile)
            a_fcf = ampel_fcf(data.get("FCF-Marge (%)"), thresholds)

            ampels = {"pe": a_pe, "growth": a_growth, "margin": a_margin, "de": a_de, "fcf": a_fcf}
            base_score = weighted_score_0_100(ampels, weights)

            final_score, red_flags = apply_red_flags(
                base_score,
                data.get("Operative Marge (%)"),
                data.get("FCF-Marge (%)"),
                data.get("Debt/Equity"),
                profile,
            )

            decision = base_decision(final_score)
            analyst_sig = analyst_signal(data.get("Analysten Key"), data.get("Analysten Mean"))

            style = classify_style(
                data.get("Sektor"),
                data.get("Beta"),
                data.get("Dividendenrendite (%)"),
                a_growth,
                a_margin,
                a_fcf,
            )

            bucket = final_bucket(decision, style, analyst_sig)

            # Erwartung & Potenzial
            pot = calc_potential_pct(data.get("Kurs (€)"), data.get("Erwartung (€)"))

            # Output
            data["Profil"] = profile
            data["Stil"] = style
            data["Analysten-Signal"] = analyst_sig
            data["Kategorie"] = bucket

            data["Score (0–100)"] = round(final_score, 1)
            data["Score (0–10)"] = as_score_0_10(final_score)
            data["Entscheidung"] = decision

            data["Potenzial (%)"] = None if pot is None else round(pot, 1)

            # Details (nicht in Haupttabelle zwingend)
            data["Ampel KGV"] = a_pe
            data["Ampel Wachstum"] = a_growth
            data["Ampel Marge"] = a_margin
            data["Ampel Verschuldung"] = a_de
            data["Ampel Free Cashflow"] = a_fcf
            data["_RedFlags"] = red_flags

            rows.append(data)

        except Exception as e:
            errors.append((tk.upper(), str(e)))

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(by="Score (0–100)", ascending=False).reset_index(drop=True)

        # Suche (Ticker/Name)
        q = (search or "").strip().lower()
        if q:
            df = df[
                df["Ticker"].astype(str).str.lower().str.contains(q)
                | df["Name"].astype(str).str.lower().str.contains(q)
            ].reset_index(drop=True)

    return df, errors


# =========================================================
# 15) Render
# =========================================================
if should_run:
    st.session_state.has_run = True
    df, errors = build_dataframe()
    st.session_state["last_df"] = df
else:
    df = st.session_state.get("last_df")
    errors = []

if df is not None and not df.empty:
    st.caption(f"Zuletzt aktualisiert: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")

    # ---- TOP 10 (immer sichtbar)
    st.markdown("## 🏆 Top 10 (nach Score)")
    top_cols = [
        "Entscheidung",
        "Score (0–10)",
        "Kategorie",
        "Ticker",
        "Name",
        "Kurs (€)",
        "Erwartung (€)",
        "Potenzial (%)",
        "KGV",
    ]
    top_cols = [c for c in top_cols if c in df.columns]
    st.dataframe(df[top_cols].head(10), use_container_width=True)

    # ---- Gesamte Watchlist OHNE Dopplung der Top 10
    st.markdown("## 📋 Gesamte Watchlist")
    rest = df.iloc[10:].copy()

    if rest.empty:
        st.caption("Keine weiteren Aktien (nur Top 10 vorhanden oder Suche filtert stark).")
    else:
        main_cols = [
            "Entscheidung",
            "Score (0–10)",
            "Kategorie",
            "Ticker",
            "Name",
            "Kurs (€)",
            "Erwartung (€)",
            "Potenzial (%)",
            "KGV",
        ]
        main_cols = [c for c in main_cols if c in rest.columns]
        st.dataframe(rest[main_cols], use_container_width=True, height=520)

    # ---- Details / Ampeln / Red Flags (nur wenn du willst)
    st.markdown("## 🔎 Details (optional)")
    labels = [f"{r['Ticker']} — {r.get('Name','')}" for _, r in df.iterrows()]
    selected = st.multiselect("Aktien auswählen (mehrfach möglich):", options=labels, default=[])

    if selected:
        label_to_ticker = {f"{r['Ticker']} — {r.get('Name','')}": r["Ticker"] for _, r in df.iterrows()}
        st.markdown("### Ausgewählte Details")
        for lab in selected:
            tkr = label_to_ticker.get(lab)
            if not tkr:
                continue
            row = df[df["Ticker"] == tkr].iloc[0].to_dict()

            st.markdown(
                f"**{row.get('Ticker','')} – {row.get('Name','')}**  \n"
                f"**{row.get('Entscheidung','')}** | Score: **{row.get('Score (0–10)','')}** | "
                f"{row.get('Kategorie','')}"
            )

            with st.expander("Details öffnen"):
                # Kennzahlen
                left, right = st.columns(2)

                with left:
                    st.write("**Preis & Erwartung**")
                    st.write(f"- Kurs (€): {row.get('Kurs (€)')}")
                    st.write(f"- Erwartung (€): {row.get('Erwartung (€)')}")
                    st.write(f"- Potenzial (%): {row.get('Potenzial (%)')}")
                    st.write(f"- KGV: {row.get('KGV')}")

                with right:
                    st.write("**Fundamentaldaten**")
                    st.write(f"- Umsatzwachstum YoY (%): {row.get('Umsatzwachstum YoY (%)')}")
                    st.write(f"- Operative Marge (%): {row.get('Operative Marge (%)')}")
                    st.write(f"- FCF-Marge (%): {row.get('FCF-Marge (%)')}")
                    st.write(f"- Debt/Equity: {row.get('Debt/Equity')}")
                    st.write(f"- Dividendenrendite (%): {row.get('Dividendenrendite (%)')}")
                    st.write(f"- Beta: {row.get('Beta')}")

                st.write("**Profil & Analysten**")
                st.write(f"- Profil: {row.get('Profil')}")
                st.write(f"- Stil: {row.get('Stil')}")
                st.write(f"- Analysten-Signal: {row.get('Analysten-Signal')}")
                st.write(f"- ISIN: {row.get('ISIN')}")
                st.write(f"- Sektor: {row.get('Sektor')}")
                st.write(f"- Industrie: {row.get('Industrie')}")

                st.write("**Ampeln**")
                st.write(
                    f"- KGV: {row.get('Ampel KGV')} | Wachstum: {row.get('Ampel Wachstum')} | "
                    f"Marge: {row.get('Ampel Marge')} | FCF: {row.get('Ampel Free Cashflow')} | "
                    f"Verschuldung: {row.get('Ampel Verschuldung')}"
                )

                flags = row.get("_RedFlags") or []
                if flags:
                    st.warning("Red Flags: " + " | ".join(flags))

                # News direkt bei der Aktie (nur wenn aktiviert)
                if show_news:
                    now_utc = datetime.now(timezone.utc)
                    q = f"{row.get('Ticker','')} {row.get('Name','')} Aktie OR stock"
                    try:
                        raw_items = google_news_rss_raw(q, max_items=25)
                        items = filter_news(raw_items, now_utc, max_age_days=60, recent_days=7, max_keep=5)
                    except Exception:
                        items = []

                    if items:
                        st.write("**News (aktuell oder wichtig)**")
                        for it in items:
                            st.markdown(f"- [{it['title']}]({it['link']})")
                    # Wenn keine: nichts anzeigen (wie gewünscht)

    # Download
    st.download_button(
        "⬇️ CSV herunterladen",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="aktien_profi_score.csv",
        mime="text/csv",
    )

    st.caption(
        f"Scoring: Gelb/Unklar zählen nur 30%. Kaufen ab {BUY_SCORE}/100, Beobachten ab {WATCH_SCORE}/100. "
        "Erwartung (€) = Analysten-Konsens (Ø), falls verfügbar."
    )

    if errors:
        st.warning("Einige Ticker konnten nicht geladen werden:")
        for tk, msg in errors:
            st.write(f"- {tk}: {msg}")

    # ---- Welt-News (kurz, nur wenn was passt)
    if show_news:
        st.markdown("## 🌍 Welt-News (kurz, nur relevant)")
        now_utc = datetime.now(timezone.utc)
        world_q = "stock market OR Börse OR inflation OR Zentralbank OR Fed OR EZB OR earnings OR geopolitics"
        try:
            raw_world = google_news_rss_raw(world_q, max_items=30)
            world_items = filter_news(raw_world, now_utc, max_age_days=60, recent_days=7, max_keep=10)
        except Exception:
            world_items = []

        if world_items:
            for it in world_items:
                st.markdown(f"- [{it['title']}]({it['link']})")
        # Wenn keine: nichts anzeigen (wie gewünscht)

else:
    st.info("Klicke auf **„Aktualisieren“** (oder aktiviere Auto-Abruf), um Daten zu sehen.")

# =========================================================
# Hinweis: requirements.txt
# streamlit
# pandas
# yfinance
# requests
# =========================================================

