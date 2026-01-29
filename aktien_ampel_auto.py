import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime

st.set_page_config(page_title="Aktienbewertung (Auto) – Ampelsystem", layout="wide")


# --------------------------
# Ampel-Logik (Fair Value) – nach deinem System
# --------------------------
def ampel_fair_value(current_price: float | None, fair_value: float | None) -> str:
    if current_price is None or fair_value is None or current_price <= 0 or fair_value <= 0:
        return "⚪ Unklar"

    ratio = fair_value / current_price

    # Grün: Fair Value über Kurs
    if ratio > 1.0:
        return "🟢 Grün"

    # Gelb: Fair Value 5–10% unter Kurs => fair_value in [0.90, 0.95] * current_price
    if 0.90 <= ratio <= 0.95:
        return "🟡 Gelb"

    # Rot: Fair Value mehr als 10% unter Kurs
    if ratio < 0.90:
        return "🔴 Rot"

    # 0.95–1.00: leicht unter Kurs -> Gelb
    return "🟡 Gelb"


# --------------------------
# Beispiel-Ampeln für Kennzahlen (kannst du später feinjustieren)
# --------------------------
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
        "Ampel Fair Value",
        "Ampel KGV",
        "Ampel Wachstum",
        "Ampel Marge",
        "Ampel Verschuldung",
        "Ampel FCF",
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


# --------------------------
# Helper: sichere Konvertierungen
# --------------------------
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


# --------------------------
# FX: Umrechnung in EUR
# --------------------------
@st.cache_data(ttl=60 * 30)  # 30 Minuten Cache
def fx_rate_to_eur(from_ccy: str) -> float | None:
    """Gibt den Faktor zurück: amount_in_from_ccy * rate = amount_in_eur"""
    from_ccy = (from_ccy or "").upper()
    if from_ccy in ["EUR", ""]:
        return 1.0

    # Yahoo FX Symbole:
    # EURUSD=X = USD pro 1 EUR -> EUR = USD / (EURUSD=X)
    # EURGBP=X = GBP pro 1 EUR -> EUR = GBP / (EURGBP=X)
    pair = f"EUR{from_ccy}=X"
    try:
        info = yf.Ticker(pair).get_info()
        px = to_float(info.get("regularMarketPrice")) or to_float(info.get("previousClose"))
        if px and px > 0:
            return 1.0 / px
        return None
    except Exception:
        return None


# --------------------------
# Abruf Aktie (yfinance)
# --------------------------
def fetch_yfinance_raw(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.get_info()

    price = to_float(safe_get(info, "currentPrice")) or to_float(safe_get(info, "regularMarketPrice"))
    pe = to_float(safe_get(info, "trailingPE")) or to_float(safe_get(info, "forwardPE"))

    rev_growth = to_pct(safe_get(info, "revenueGrowth"))       # 0.12 -> 12%
    oper_margin = to_pct(safe_get(info, "operatingMargins"))   # 0.18 -> 18%

    # Debt/Equity: Yahoo oft als Prozent (z.B. 150 = 1.5)
    d2e_raw = to_float(safe_get(info, "debtToEquity"))
    debt_to_equity = None
    if d2e_raw is not None:
        debt_to_equity = d2e_raw / 100.0 if d2e_raw > 10 else d2e_raw

    # FCF-Marge = (freeCashflow / totalRevenue) * 100
    fcf = to_float(safe_get(info, "freeCashflow"))
    revenue = to_float(safe_get(info, "totalRevenue"))
    fcf_margin = None
    if fcf is not None and revenue is not None and revenue != 0:
        fcf_margin = (fcf / revenue) * 100.0

    target_mean = to_float(safe_get(info, "targetMeanPrice"))
    name = safe_get(info, "shortName") or ticker.upper()
    ccy = (safe_get(info, "currency") or "").upper()

    return {
        "Ticker": ticker.upper(),
        "Name": name,
        "Währung": ccy if ccy else "Unbekannt",
        "Kurs": price,
        "KGV": pe,
        "Umsatzwachstum YoY (%)": rev_growth,
        "Operative Marge (%)": oper_margin,
        "Debt/Equity": debt_to_equity,
        "FCF-Marge (%)": fcf_margin,
        "Analysten-Ziel (Ø)": target_mean,
    }


def convert_money_to_eur(row: dict) -> dict:
    ccy = row.get("Währung", "Unbekannt")
    rate = fx_rate_to_eur(ccy)
    row["FX → EUR"] = rate

    def conv(v):
        v = to_float(v)
        if v is None:
            return None
        if rate is None:
            return v  # keine Umrechnung möglich -> Rohwert
        return v * rate

    # Nur Geldwerte umrechnen:
    row["Kurs (€)"] = conv(row.get("Kurs"))
    row["Fair Value (€)"] = conv(row.get("Fair Value"))
    row["Analysten-Ziel (Ø, €)"] = conv(row.get("Analysten-Ziel (Ø)"))

    return row


# --------------------------
# UI
# --------------------------
st.title("📈 Aktienbewertung (Auto-Abruf) + Ampelsystem (Deutsch + €)")

with st.sidebar:
    st.header("Eingabe")
    tickers_raw = st.text_area("Ticker (eine pro Zeile)", value="AAPL\nMSFT\nNVDA")

    st.divider()
    fair_mode = st.radio(
        "Fair Value Quelle",
        options=["Analysten-Ziel (Ø) nutzen (wenn vorhanden)", "Manuell (Fallback)"],
        index=0,
    )
    manual_fair_value = st.number_input("Manueller Fair Value (in €)", min_value=0.0, value=0.0, step=1.0)

    st.divider()
    auto_fetch = st.checkbox("Beim Laden automatisch abrufen", value=True)
    st.caption("Hinweis: yfinance/Yahoo ist gratis, aber bei manchen Tickers fehlen Kennzahlen.")

colA, colB = st.columns([1, 2])
with colA:
    fetch_now = st.button("🔄 Kurse aktualisieren", type="primary")
with colB:
    st.write("")

# Trigger-Logik: entweder Button oder automatischer Abruf beim Laden
if "has_run" not in st.session_state:
    st.session_state.has_run = False

should_run = fetch_now or (auto_fetch and not st.session_state.has_run)

if should_run:
    st.session_state.has_run = True

    tickers = [t.strip() for t in tickers_raw.splitlines() if t.strip()]
    rows = []
    errors = []

    for tk in tickers:
        try:
            data = fetch_yfinance_raw(tk)

            # Fair Value festlegen
            fair_value_raw = None
            if fair_mode.startswith("Analysten") and data.get("Analysten-Ziel (Ø)") not in [None, 0]:
                fair_value_raw = data["Analysten-Ziel (Ø)"]
            elif manual_fair_value and manual_fair_value > 0:
                # manuell ist in EUR gedacht -> wir speichern als EUR direkt
                # und lassen "Kurs/Fair Value" Rohwerte in Originalwährung; EUR-Spalte bekommt es direkt
                fair_value_raw = None  # Rohwert bleibt leer
                data["Fair Value"] = None
                data["Fair Value (€)"] = float(manual_fair_value)
            else:
                data["Fair Value"] = None

            if "Fair Value (€)" not in data:
                data["Fair Value"] = fair_value_raw

            # In EUR umrechnen (Kurs & Analysten-Ziel)
            data = convert_money_to_eur(data)

            # Wenn Fair Value aus Analysten-Ziel kommt -> EUR Fair Value setzen
            if fair_mode.startswith("Analysten") and data.get("Analysten-Ziel (Ø, €)") is not None:
                data["Fair Value (€)"] = data["Analysten-Ziel (Ø, €)"]

            rows.append(data)

        except Exception as e:
            errors.append((tk.upper(), str(e)))

    df = pd.DataFrame(rows)

    # Ampeln berechnen auf EUR-Werten (wenn vorhanden)
    df["Ampel Fair Value"] = df.apply(lambda r: ampel_fair_value(r.get("Kurs (€)"), r.get("Fair Value (€)")), axis=1)
    df["Ampel KGV"] = df["KGV"].apply(lambda x: ampel_kgv(to_float(x)))
    df["Ampel Wachstum"] = df["Umsatzwachstum YoY (%)"].apply(lambda x: ampel_wachstum(to_float(x)))
    df["Ampel Marge"] = df["Operative Marge (%)"].apply(lambda x: ampel_marge(to_float(x)))
    df["Ampel Verschuldung"] = df["Debt/Equity"].apply(lambda x: ampel_verschuldung(to_float(x)))
    df["Ampel FCF"] = df["FCF-Marge (%)"].apply(lambda x: ampel_fcf(to_float(x)))

    df["Gesamt-Ampel"] = df.apply(gesamt_ampel, axis=1)
    df["Entscheidung"] = df["Gesamt-Ampel"].apply(entscheidung_from_ampel)

    # Spalten auf Deutsch + Euro sichtbar
    # (Rohwerte behalten wir optional, aber Euro ist vorne)
    cols = [
        "Entscheidung",
        "Gesamt-Ampel",
        "Ticker",
        "Name",
        "Währung",
        "Kurs (€)",
        "Fair Value (€)",
        "Analysten-Ziel (Ø, €)",
        "KGV",
        "Umsatzwachstum YoY (%)",
        "Operative Marge (%)",
        "Debt/Equity",
        "FCF-Marge (%)",
        "Ampel Fair Value",
        "Ampel KGV",
        "Ampel Wachstum",
        "Ampel Marge",
        "Ampel Verschuldung",
        "Ampel FCF",
    ]
    cols = [c for c in cols if c in df.columns]

    st.subheader("Ergebnis")
    st.caption(f"Zuletzt aktualisiert: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    st.dataframe(df[cols], width="stretch")

    st.download_button(
        "⬇️ Ergebnis als CSV herunterladen",
        data=df[cols].to_csv(index=False).encode("utf-8"),
        file_name="aktien_ampel_auto_eur_de.csv",
        mime="text/csv",
    )

    st.caption("Fair-Value Ampel: 🟢 Fair Value > Kurs | 🟡 Fair Value 5–10% unter Kurs | 🔴 >10% unter Kurs.")

    if errors:
        st.warning("Einige Ticker konnten nicht geladen werden:")
        for tk, msg in errors:
            st.write(f"- {tk}: {msg}")
else:
    st.info("Klicke auf **„Kurse aktualisieren“** oder aktiviere **„Beim Laden automatisch abrufen“**.")
