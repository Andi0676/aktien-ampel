import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
import requests
import xml.etree.ElementTree as ET
import re

st.set_page_config(page_title="Aktienbewertung – Ampelsystem", layout="wide")

# ============================================
# 1) Öffentliche Haupt-Watchlist (Ticker + Name)
#    -> Jeder sieht diese Liste immer.
#    -> Zusätzliche Aktien sind nur pro Sitzung (Tab) und gehen beim Schließen verloren.
# ============================================
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

# ============================================
# 2) Ampel-Logik (Fairer Wert nach deinem System)
#    Fairer Wert = Analysten-Ziel (Ø) (wenn vorhanden)
#    KEIN manueller Fair Value mehr.
# ============================================
def ampel_fair_value(current_price: float | None, fair_value: float | None) -> str:
    if current_price is None or fair_value is None or current_price <= 0 or fair_value <= 0:
        return "⚪ Unklar"
    ratio = fair_value / current_price
    if ratio > 1.0:
        return "🟢 Grün"
    if 0.90 <= ratio <= 0.95:
        return "🟡 Gelb"
    if ratio < 0.90:
        return "🔴 Rot"
    return "🟡 Gelb"


def ampel_kgv(pe: float | None) -> str:
    if pe is None or pe <= 0:
        return "⚪ Unklar"
    if pe <= 20:
        return "🟢 Grün"
    if pe <= 35:
        return "🟡 Gelb"
    return "🔴 Rot"


def ampel_wachstum(rev_growth_pct: float | None) -> str:
    if rev_growth_pct is None:
        return "⚪ Unklar"
    if rev_growth_pct >= 15:
        return "🟢 Grün"
    if rev_growth_pct >= 5:
        return "🟡 Gelb"
    return "🔴 Rot"


def ampel_marge(oper_margin_pct: float | None) -> str:
    if oper_margin_pct is None:
        return "⚪ Unklar"
    if oper_margin_pct >= 15:
        return "🟢 Grün"
    if oper_margin_pct >= 8:
        return "🟡 Gelb"
    return "🔴 Rot"


def ampel_verschuldung(debt_to_equity: float | None) -> str:
    if debt_to_equity is None or debt_to_equity < 0:
        return "⚪ Unklar"
    if debt_to_equity <= 0.6:
        return "🟢 Grün"
    if debt_to_equity <= 1.2:
        return "🟡 Gelb"
    return "🔴 Rot"


def ampel_fcf(fcf_margin_pct: float | None) -> str:
    if fcf_margin_pct is None:
        return "⚪ Unklar"
    if fcf_margin_pct >= 10:
        return "🟢 Grün"
    if fcf_margin_pct >= 3:
        return "🟡 Gelb"
    return "🔴 Rot"


def gesamt_ampel(row: pd.Series) -> str:
    mapping = {"🟢 Grün": 2, "🟡 Gelb": 1, "🔴 Rot": 0, "⚪ Unklar": 1}
    cols = [
        "Ampel Fairer Wert",
        "Ampel KGV",
        "Ampel Wachstum",
        "Ampel Marge",
        "Ampel Verschuldung",
        "Ampel Free Cashflow",
    ]
    score = sum(mapping.get(row.get(c, "⚪ Unklar"), 1) for c in cols)
    max_score = 2 * len(cols)
    ratio = score / max_score
    if ratio >= 0.72:
        return "🟢 Grün"
    if ratio >= 0.50:
        return "🟡 Gelb"
    return "🔴 Rot"


def entscheidung_from_ampel(ampel: str) -> str:
    if ampel == "🟢 Grün":
        return "🟢 KAUFEN"
    if ampel == "🟡 Gelb":
        return "🟡 BEOBACHTEN"
    if ampel == "🔴 Rot":
        return "🔴 NICHT KAUFEN"
    return "⚪ UNKLAR"


# ============================================
# 3) Helper
# ============================================
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


# ============================================
# 4) FX: Umrechnung in EUR
# ============================================
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
    row["Analysten-Ziel (Ø, €)"] = conv(row.get("Analysten-Ziel (Ø)"))
    row["Fairer Wert (€)"] = row.get("Analysten-Ziel (Ø, €)")
    return row


# ============================================
# 5) Datenabruf (yfinance)
# ============================================
def fetch_yfinance_raw(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.get_info()

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

    target_mean = to_float(safe_get(info, "targetMeanPrice"))
    name = safe_get(info, "shortName") or safe_get(info, "longName") or ticker.upper()
    ccy = (safe_get(info, "currency") or "").upper()

    # ISIN ist bei manchen Unternehmen vorhanden
    isin = safe_get(info, "isin")

    return {
        "Ticker": ticker.upper(),
        "Name": name,
        "ISIN": isin,
        "Währung": ccy if ccy else "Unbekannt",
        "Kurs": price,
        "KGV": pe,
        "Umsatzwachstum YoY (%)": rev_growth,
        "Operative Marge (%)": oper_margin,
        "Debt/Equity": debt_to_equity,
        "FCF-Marge (%)": fcf_margin,
        "Analysten-Ziel (Ø)": target_mean,
    }


# ============================================
# 6) WKN/ISIN -> Ticker (best-effort, ohne Datenbank)
#    Wir versuchen OpenFIGI (gratis, aber manchmal limitiert).
#    Wenn nicht eindeutig gefunden: Nutzer soll Ticker direkt eingeben.
# ============================================
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")
WKN_RE = re.compile(r"^[A-Z0-9]{6}$")

# grobes Mapping (OpenFIGI/BBG Exchange Codes -> Yahoo-Suffix)
EXCH_TO_SUFFIX = {
    "GY": ".DE",  # Germany (oft Xetra)
    "GR": ".DE",
    "SW": ".SW",  # Switzerland
    "VX": ".VI",  # Vienna (kann abweichen; best effort)
    "FP": ".PA",  # Paris
    "NA": ".AS",  # Amsterdam
    "IM": ".MI",  # Milan
    "LN": ".L",   # London
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
        id_type = "ID_WERTPAPIER"  # WKN best-effort
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

        # nimm den 1. Treffer (best-effort)
        hit = data[0]["data"][0]
        ticker = (hit.get("ticker") or "").upper().strip()
        name = (hit.get("name") or "").strip()
        exch = (hit.get("exchCode") or "").upper().strip()

        if not ticker:
            return None

        suffix = EXCH_TO_SUFFIX.get(exch, "")
        yahoo_ticker = ticker + suffix if suffix and "." not in ticker else ticker

        # OpenFIGI liefert manchmal ISIN mit
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


# ============================================
# 7) News (Google News RSS + Welt-Schlagzeilen)
# ============================================
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


# ============================================
# 8) UI State
# ============================================
if "extra_watchlist" not in st.session_state:
    # nur pro Sitzung – weg nach Tab schließen/Reload je nach Browser
    st.session_state["extra_watchlist"] = []  # list of dicts: {"raw":..., "ticker":..., "name":..., "isin":..., "type":...}

def combined_watchlist_rows():
    combined = {norm_ticker(x["ticker"]): {"Ticker": norm_ticker(x["ticker"]), "Name": x["name"], "ISIN": ""} for x in MASTER_WATCHLIST}
    for x in st.session_state["extra_watchlist"]:
        t = norm_ticker(x.get("ticker"))
        if t and t not in combined:
            combined[t] = {"Ticker": t, "Name": x.get("name",""), "ISIN": x.get("isin") or ""}
    rows = list(combined.values())
    rows.sort(key=lambda r: r["Ticker"])
    return rows


# ============================================
# 9) UI Layout
# ============================================
st.title("📈 Aktienbewertung + Ampelsystem (Deutsch + €)")
st.caption("Fairer Wert = Analysten-Ziel (Ø), wenn verfügbar. Kein manueller Fair Value.")

with st.sidebar:
    st.header("📌 Haupt-Watchlist (öffentlich)")
    st.caption("Diese Liste ist fix sichtbar. Zusätzlich kannst du für diese Sitzung Aktien hinzufügen.")

    st.dataframe(pd.DataFrame(combined_watchlist_rows()), use_container_width=True, height=260)

    st.subheader("➕ Hinzufügen (Ticker / WKN / ISIN)")
    inp = st.text_input("Eingabe", placeholder="z.B. SAP.DE oder 716460 (WKN) oder US0378331005 (ISIN)")
    add_btn = st.button("➕ Zur Sitzung hinzufügen")

    if add_btn:
        code_type = classify_input(inp)
        raw = inp.strip()

        if not raw:
            st.warning("Bitte etwas eingeben.")
        else:
            resolved = None
            if code_type in ("ISIN", "WKN"):
                resolved = resolve_via_openfigi(raw)

                if resolved is None:
                    st.error("Konnte ISIN/WKN nicht eindeutig auflösen. Bitte Ticker direkt eingeben (z.B. SAP.DE).")
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
                # Ticker: Name holen via yfinance (best effort)
                tkr = norm_ticker(raw)
                try:
                    info = yf.Ticker(tkr).get_info()
                    nm = (info.get("shortName") or info.get("longName") or tkr)
                    isin = info.get("isin") or ""
                except Exception:
                    nm = tkr
                    isin = ""

                # Duplikat check
                existing = {r["Ticker"] for r in combined_watchlist_rows()}
                if tkr in existing:
                    st.info("Dieser Ticker ist schon in der Liste.")
                else:
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
    st.caption("Hinweis: Kurse/Kennzahlen via yfinance (gratis). News via Google News RSS.")


colA, colB, colC = st.columns([1, 1, 2])
with colA:
    fetch_now = st.button("🔄 Kurse & Kennzahlen aktualisieren", type="primary")
with colB:
    news_now = st.button("📰 News aktualisieren")
with colC:
    st.write("")

# Trigger-Logik
if "has_run" not in st.session_state:
    st.session_state.has_run = False

tickers_rows = combined_watchlist_rows()
tickers = [r["Ticker"] for r in tickers_rows]

should_run = fetch_now or (auto_fetch and not st.session_state.has_run)

# ============================================
# 10) Daten laden + bewerten
# ============================================
if should_run:
    st.session_state.has_run = True

    rows = []
    errors = []

    for tk in tickers:
        try:
            data = fetch_yfinance_raw(tk)

            # Fairer Wert = Analysten-Ziel (Ø)
            data["Fair Value"] = data.get("Analysten-Ziel (Ø)")

            data = convert_money_to_eur(data)

            rows.append(data)
        except Exception as e:
            errors.append((tk.upper(), str(e)))

    df = pd.DataFrame(rows)

    # Ampeln auf EUR-Werten (wo möglich)
    df["Ampel Fairer Wert"] = df.apply(lambda r: ampel_fair_value(r.get("Kurs (€)"), r.get("Fairer Wert (€)")), axis=1)
    df["Ampel KGV"] = df["KGV"].apply(lambda x: ampel_kgv(to_float(x)))
    df["Ampel Wachstum"] = df["Umsatzwachstum YoY (%)"].apply(lambda x: ampel_wachstum(to_float(x)))
    df["Ampel Marge"] = df["Operative Marge (%)"].apply(lambda x: ampel_marge(to_float(x)))
    df["Ampel Verschuldung"] = df["Debt/Equity"].apply(lambda x: ampel_verschuldung(to_float(x)))
    df["Ampel Free Cashflow"] = df["FCF-Marge (%)"].apply(lambda x: ampel_fcf(to_float(x)))

    df["Gesamt-Ampel"] = df.apply(gesamt_ampel, axis=1)
    df["Entscheidung"] = df["Gesamt-Ampel"].apply(entscheidung_from_ampel)

    # Potenzial (falls fairer Wert vorhanden)
    def potenzial_pct(row):
        k = row.get("Kurs (€)")
        fv = row.get("Fairer Wert (€)")
        if k and fv and k > 0:
            return (fv / k - 1.0) * 100.0
        return None

    df["Potenzial (%)"] = df.apply(potenzial_pct, axis=1)

    # Hauptansicht (übersichtlich)
    cols_main = [
        "Entscheidung",
        "Gesamt-Ampel",
        "Ticker",
        "Name",
        "ISIN",
        "Währung",
        "Kurs (€)",
        "Fairer Wert (€)",
        "Potenzial (%)",
        "KGV",
        "Umsatzwachstum YoY (%)",
    ]
    cols_main = [c for c in cols_main if c in df.columns]

    # Details
    cols_details = [
        "Operative Marge (%)",
        "Debt/Equity",
        "FCF-Marge (%)",
        "Ampel Fairer Wert",
        "Ampel KGV",
        "Ampel Wachstum",
        "Ampel Marge",
        "Ampel Verschuldung",
        "Ampel Free Cashflow",
        "Analysten-Ziel (Ø, €)",
    ]
    cols_details = [c for c in cols_details if c in df.columns]

    st.subheader("Ergebnis")
    st.caption(f"Zuletzt aktualisiert: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")

    st.dataframe(df[cols_main], width="stretch")

    with st.expander("Details (Kennzahlen & Einzelampeln) anzeigen"):
        st.dataframe(df[cols_details], width="stretch")

    st.download_button(
        "⬇️ Ergebnis als CSV herunterladen",
        data=df[cols_main + cols_details].to_csv(index=False).encode("utf-8"),
        file_name="aktien_bewertung.csv",
        mime="text/csv",
    )

    st.caption("Fairer Wert Ampel: 🟢 fairer Wert > Kurs | 🟡 fairer Wert 5–10% unter Kurs | 🔴 >10% unter Kurs.")

    if errors:
        st.warning("Einige Ticker konnten nicht geladen werden:")
        for tk, msg in errors:
            st.write(f"- {tk}: {msg}")

else:
    st.info("Klicke auf **„Kurse & Kennzahlen aktualisieren“** oder aktiviere **„Beim Laden automatisch abrufen“**.")


# ============================================
# 11) News (Button)
# ============================================
if news_now:
    st.subheader("📰 News zu deinen Aktien")
    st.caption("Headlines via Google News RSS (de/AT).")

    # pro Aktie: lieber nicht zu viele auf einmal
    for row in tickers_rows[:25]:
        t = row["Ticker"]
        n = row.get("Name") or t
        q = f"{t} {n} Aktie OR stock"
        items = []
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

# ============================================
# Hinweis:
# Für WKN/ISIN-Auflösung wird "requests" benötigt.
# In requirements.txt musst du zusätzlich eintragen:
# requests
# ============================================
