import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
import requests
import xml.etree.ElementTree as ET
import re

st.set_page_config(page_title="Aktienbewertung – Profi-Score (0–100)", layout="wide")

# =========================================================
# 1) Öffentliche Haupt-Watchlist (Ticker + Name) – immer sichtbar
# =========================================================
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

    # DACH/EU – Yahoo-Suffixe:
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
# 2) Profi-Logik: Branchen-Profile + Gewichtung + Schwellen
#    Score 0–100 + Red-Flags + Entscheidung
# =========================================================

# Ampel -> Punkte in Prozent: Grün=100%, Gelb=50%, Rot=0%, Unklar=50%
AMP_TO_PCT = {"🟢": 1.0, "🟡": 0.5, "🔴": 0.0, "⚪": 0.5}

# Basisschwellen (branchenneutral) – “Profi entschärft”
THRESHOLDS_DEFAULT = {
    "pe": (30, 60),           # Grün <= 30, Gelb <= 60, sonst Rot
    "de": (1.5, 3.0),         # Debt/Equity: Grün <= 1.5, Gelb <= 3.0, sonst Rot
    "growth": (15, 5),        # Wachstum: Grün >= 15, Gelb >= 5, sonst Rot
    "margin": (15, 8),        # Operative Marge: Grün >= 15, Gelb >= 8, sonst Rot
    "fcf": (10, 3),           # FCF-Marge: Grün >= 10, Gelb >= 3, sonst Rot
}

# Branchen-Profile (Gewichte müssen 100 ergeben)
PROFILE_RULES = {
    "Tech/Software": {
        "weights": {"growth": 30, "margin": 25, "fcf": 25, "de": 10, "pe": 10},
        "thresholds": {"pe": (40, 80), "de": (2.0, 4.0)}  # Tech darf teurer sein, Schulden etwas toleranter
    },
    "Industrie/Zyklisch": {
        "weights": {"growth": 25, "margin": 20, "fcf": 20, "de": 20, "pe": 15},
        "thresholds": {"pe": (35, 70), "de": (2.0, 4.0)}
    },
    "Konsum/Marke": {
        "weights": {"growth": 15, "margin": 30, "fcf": 30, "de": 15, "pe": 10},
        "thresholds": {"pe": (35, 70), "de": (2.0, 4.0)}
    },
    "Finanzen/Banken": {
        # Debt/Equity ist bei Banken nicht sinnvoll -> Gewicht 0 (neutral)
        "weights": {"growth": 25, "margin": 35, "fcf": 20, "de": 0, "pe": 20},
        "thresholds": {"pe": (25, 45)}  # konservativer bei KGV
    },
    "Default": {
        "weights": {"growth": 25, "margin": 20, "fcf": 20, "de": 20, "pe": 15},
        "thresholds": {}
    }
}

FINANCE_SECTORS = {"Financial Services", "Financial", "Banks", "Insurance"}
TECH_SECTORS = {"Technology", "Information Technology", "Communication Services"}
INDUSTRIAL_SECTORS = {"Industrials", "Basic Materials", "Energy", "Utilities"}
CONSUMER_SECTORS = {"Consumer Defensive", "Consumer Cyclical"}

# Red Flags:
# 1) FCF-Marge < 0  -> max Entscheidung = Beobachten (Score gedeckelt < 70)
# 2) Operative Marge < 5 -> max Beobachten
# 3) Debt/Equity > 3.0 UND FCF-Marge < 5 -> Score halbieren
def apply_red_flags(score_0_100: float, oper_margin_pct, fcf_margin_pct, debt_to_equity, profile_name: str) -> tuple[float, list]:
    flags = []
    score = score_0_100

    # Banken: Debt/Equity ignorieren wir bei Flags (weil interpretativ schwierig)
    ignore_de = (profile_name == "Finanzen/Banken")

    if fcf_margin_pct is not None and fcf_margin_pct < 0:
        flags.append("Red Flag: Free Cashflow negativ (Geldverbrennung) → max. BEOBACHTEN")
        score = min(score, 69.9)

    if oper_margin_pct is not None and oper_margin_pct < 5:
        flags.append("Red Flag: Operative Marge < 5% → max. BEOBACHTEN")
        score = min(score, 69.9)

    if (not ignore_de) and debt_to_equity is not None and fcf_margin_pct is not None:
        if debt_to_equity > 3.0 and fcf_margin_pct < 5:
            flags.append("Red Flag: Debt/Equity > 3 UND FCF < 5% → Score halbiert")
            score = score * 0.5

    return score, flags

def decision_from_score(score_0_100: float) -> str:
    # Entschärft wie du wolltest: 70/100 (=7/10) ist KAUFEN
    if score_0_100 >= 70:
        return "🟢 KAUFEN"
    if score_0_100 >= 55:
        return "🟡 BEOBACHTEN"
    return "🔴 NICHT KAUFEN"

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

# =========================================================
# 4) FX: Umrechnung in EUR
# =========================================================
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
    return row

# =========================================================
# 5) Branchenprofil ermitteln (best effort)
# =========================================================
def detect_profile(sector: str | None, industry: str | None) -> str:
    s = (sector or "").strip()
    i = (industry or "").strip()

    # Finanzwerte
    if s in FINANCE_SECTORS or "Bank" in i or "Insurance" in i or "Financial" in i:
        return "Finanzen/Banken"

    # Tech/Software
    if s in TECH_SECTORS or "Software" in i or "Semiconductor" in i or "Internet" in i:
        return "Tech/Software"

    # Konsum/Marke
    if s in CONSUMER_SECTORS or "Retail" in i or "Beverage" in i or "Food" in i:
        return "Konsum/Marke"

    # Industrie/Zyklisch
    if s in INDUSTRIAL_SECTORS or "Industrial" in i or "Aerospace" in i or "Defense" in i:
        return "Industrie/Zyklisch"

    return "Default"

def get_thresholds_for_profile(profile_name: str) -> dict:
    base = dict(THRESHOLDS_DEFAULT)
    override = PROFILE_RULES.get(profile_name, PROFILE_RULES["Default"]).get("thresholds", {})
    # override merges only provided keys
    for k, v in override.items():
        base[k] = v
    return base

def get_weights_for_profile(profile_name: str) -> dict:
    return PROFILE_RULES.get(profile_name, PROFILE_RULES["Default"])["weights"]

# =========================================================
# 6) Ampeln pro Kriterium (ohne Fair Value)
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
    # Banken: Debt/Equity ist nicht sinnvoll -> neutral
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
    # Sum(weight * pct) with weights summing to 100 (or close)
    total_weight = sum(weights.values()) if weights else 100
    if total_weight <= 0:
        return 0.0
    s = 0.0
    for key, w in weights.items():
        pct = amp_to_pct(ampels.get(key, "⚪ Unklar"))
        s += w * pct
    # Normalize to 0..100 even if weights don't sum exactly 100
    return (s / total_weight) * 100.0

# =========================================================
# 7) Datenabruf (yfinance)
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
    }

# =========================================================
# 8) WKN/ISIN -> Ticker (OpenFIGI, best effort)
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
def resolve_via_openfigi(input_code: str) -> dict | None:
    code = norm_ticker(input_code)
    if not code:
        return None

    id_type = None
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
# 9) News (Google News RSS + Welt-Schlagzeilen)
# =========================================================
@st.cache_data(ttl=10 * 60)
def google_news_rss(query: str, max_items: int = 6):
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

# =========================================================
# 10) UI State
# =========================================================
if "extra_watchlist" not in st.session_state:
    st.session_state["extra_watchlist"] = []  # nur Sitzung

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
# 11) Layout
# =========================================================
st.title("📈 Aktienbewertung – Profi-Score (0–100)")
st.caption("Profisystem: gewichtete Kennzahlen + Red Flags + Branchenprofile. Kein Fair Value / keine Kursziele.")

with st.sidebar:
    st.header("📌 Watchlist")
    st.caption("Hauptliste ist fix sichtbar. Eigene Adds sind nur für diese Sitzung (Tab) und gehen beim Schließen verloren.")
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
                    st.session_state["extra_watchlist"].append({
                        "raw": raw,
                        "type": code_type,
                        "ticker": resolved["ticker"],
                        "name": resolved["name"],
                        "isin": resolved.get("isin") or (raw if code_type == "ISIN" else ""),
                    })
                    st.success(f"Hinzugefügt: {resolved['ticker']} – {resolved['name']}")
            else:
                tkr = norm_ticker(raw)
                existing = {r["Ticker"] for r in combined_watchlist_rows()}
                if tkr in existing:
                    st.info("Dieser Ticker ist schon in der Liste.")
                else:
                    # Name/ISIN best effort via yfinance
                    try:
                        info = yf.Ticker(tkr).get_info() or {}
                        nm = info.get("shortName") or info.get("longName") or tkr
                        isin = info.get("isin") or ""
                    except Exception:
                        nm = tkr
                        isin = ""
                    st.session_state["extra_watchlist"].append({
                        "raw": raw,
                        "type": "TICKER",
                        "ticker": tkr,
                        "name": nm,
                        "isin": isin
                    })
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
    st.caption("Kurse/Kennzahlen: yfinance. News: Google News RSS. (Für ISIN/WKN braucht's requests.)")

colA, colB, colC = st.columns([1, 1, 2])
with colA:
    fetch_now = st.button("🔄 Kurse & Bewertung aktualisieren", type="primary")
with colB:
    news_now = st.button("📰 News aktualisieren")
with colC:
    st.write("")

if "has_run" not in st.session_state:
    st.session_state.has_run = False

tickers_rows = combined_watchlist_rows()
tickers = [r["Ticker"] for r in tickers_rows]

should_run = fetch_now or (auto_fetch and not st.session_state.has_run)

# =========================================================
# 12) Daten laden + Profi-Score berechnen
# =========================================================
if should_run:
    st.session_state.has_run = True

    rows = []
    errors = []

    for tk in tickers:
        try:
            data = fetch_yfinance_raw(tk)
            data = convert_money_to_eur(data)

            profile = detect_profile(data.get("Sektor"), data.get("Industrie"))
            thresholds = get_thresholds_for_profile(profile)
            weights = get_weights_for_profile(profile)

            # Ampeln (ohne Fair Value)
            a_pe = ampel_pe(data.get("KGV"), thresholds)
            a_growth = ampel_growth(data.get("Umsatzwachstum YoY (%)"), thresholds)
            a_margin = ampel_margin(data.get("Operative Marge (%)"), thresholds)
            a_de = ampel_de(data.get("Debt/Equity"), thresholds, profile)
            a_fcf = ampel_fcf(data.get("FCF-Marge (%)"), thresholds)

            # Für Score nutzen wir Keys: growth, margin, fcf, de, pe
            ampels = {
                "pe": a_pe,
                "growth": a_growth,
                "margin": a_margin,
                "de": a_de,
                "fcf": a_fcf,
            }

            base_score = weighted_score_0_100(ampels, weights)
            final_score, red_flags = apply_red_flags(
                base_score,
                data.get("Operative Marge (%)"),
                data.get("FCF-Marge (%)"),
                data.get("Debt/Equity"),
                profile
            )

            data["Profil"] = profile
            data["Score (0–100)"] = round(final_score, 1)
            data["Entscheidung"] = decision_from_score(final_score)

            # Ampeln als eigene Spalten
            data["Ampel KGV"] = a_pe
            data["Ampel Wachstum"] = a_growth
            data["Ampel Marge"] = a_margin
            data["Ampel Verschuldung"] = a_de
            data["Ampel Free Cashflow"] = a_fcf
            data["Red Flags"] = " | ".join(red_flags) if red_flags else ""

            rows.append(data)

        except Exception as e:
            errors.append((tk.upper(), str(e)))

    df = pd.DataFrame(rows)

    if not df.empty:
        # Sortierung nach Score
        df = df.sort_values(by="Score (0–100)", ascending=False).reset_index(drop=True)

        st.subheader("Ergebnis")
        st.caption(f"Zuletzt aktualisiert: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")

        # Top 10 Ranking
        st.markdown("### 🏆 Top 10 (Ranking nach Score)")
        top10_cols = ["Score (0–100)", "Entscheidung", "Ticker", "Name", "Profil", "Kurs (€)", "KGV"]
        top10_cols = [c for c in top10_cols if c in df.columns]
        st.dataframe(df[top10_cols].head(10), use_container_width=True)

        # Hauptansicht (übersichtlich)
        st.markdown("### 📋 Gesamtliste (Kurzansicht)")
        cols_main = [
            "Score (0–100)",
            "Entscheidung",
            "Ticker",
            "Name",
            "Profil",
            "Währung",
            "Kurs (€)",
            "KGV",
            "Umsatzwachstum YoY (%)",
        ]
        cols_main = [c for c in cols_main if c in df.columns]
        st.dataframe(df[cols_main], use_container_width=True)

        # Details + Einzelampeln + Red Flags
        st.markdown("### 🔎 Details (Kennzahlen, Ampeln, Red Flags)")
        cols_details = [
            "Ticker",
            "Sektor",
            "Industrie",
            "Operative Marge (%)",
            "FCF-Marge (%)",
            "Debt/Equity",
            "Ampel KGV",
            "Ampel Wachstum",
            "Ampel Marge",
            "Ampel Verschuldung",
            "Ampel Free Cashflow",
            "Red Flags",
        ]
        cols_details = [c for c in cols_details if c in df.columns]
        with st.expander("Details anzeigen"):
            st.dataframe(df[cols_details], use_container_width=True)

        st.download_button(
            "⬇️ Ergebnis als CSV herunterladen",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="aktien_profi_score.csv",
            mime="text/csv",
        )

        st.caption(
            "Entscheidung: 🟢 Kaufen ab 70/100 | 🟡 Beobachten 55–69 | 🔴 Nicht kaufen < 55. "
            "Red Flags können Kaufen verhindern oder Score reduzieren."
        )

    if errors:
        st.warning("Einige Ticker konnten nicht geladen werden:")
        for tk, msg in errors:
            st.write(f"- {tk}: {msg}")

else:
    st.info("Klicke auf **„Kurse & Bewertung aktualisieren“** oder aktiviere **„Beim Laden automatisch abrufen“**.")

# =========================================================
# 13) News (Button)
# =========================================================
if news_now:
    st.subheader("📰 News zu deinen Aktien")
    st.caption("Headlines via Google News RSS (de/AT).")

    for row in tickers_rows[:25]:
        t = row["Ticker"]
        n = row.get("Name") or t
        q = f"{t} {n} Aktie OR stock"
        try:
            items = google_news_rss(q, max_items=5)
        except Exception:
            items = []

        with st.expander(f"{t} – {n}", expanded=False):
            if not items:
                st.write("Keine Headlines gefunden oder Feed gerade nicht erreichbar.")
            else:
                for it in items:
                    st.markdown(f"- [{it['title']}]({it['link']})  \n  _{it['pub']}_")

    st.divider()
    st.subheader("🌍 Welt-Schlagzeilen (Finanzen)")
    world_q = "stock market OR Börse OR inflation OR earnings OR Zentralbank"
    world_items = google_news_rss(world_q, max_items=10)
    for it in world_items:
        st.markdown(f"- [{it['title']}]({it['link']})  \n  _{it['pub']}_")
        try:
            st.toast(it["title"])
        except Exception:
            pass

# =========================================================
# Hinweis:
# In requirements.txt muss mindestens stehen:
# streamlit
# pandas
# yfinance
# requests
# =========================================================
