import streamlit as st
import pandas as pd
import numpy as np
import ccxt
import time
import threading
from datetime import datetime

# --- KESİN ÇÖZÜM: PAYLAŞILAN BELLEK KANCASI ---
@st.cache_resource
def get_global_state():
    # Bu liste tüm sekmeler ve threadler arasında ortaktır.
    return {"history": [], "sent": set()}

state = get_global_state()

# --- YAPILANDIRMA ---
TIMEFRAME = "15m"
VOL_THRESHOLD = 20000 
VOL_SMA_LB = 10
K2_LOOKBACK = 5

# Binance bağlantısı (Streamlit Cloud için enableRateLimit true olmalı)
exchange = ccxt.binance({"enableRateLimit": True})

def get_tv_link(symbol: str):
    clean = symbol.replace("/", "").upper()
    return f"https://www.tradingview.com/chart/?symbol=BINANCE:{clean}"

# --- ANALİZ FONKSİYONU ---
def analyze_symbol(symbol: str):
    try:
        # Veri çekme
        raw = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=60)
        df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
        df["usdt_vol"] = df["close"] * df["volume"]
        
        last_vol = df["usdt_vol"].iloc[-1]
        avg_vol = df["usdt_vol"].rolling(window=VOL_SMA_LB).mean().iloc[-1]
        
        closes, highs, lows = df["close"].values, df["high"].values, df["low"].values
        r1, r2 = np.nan, np.nan
        for i in range(K2_LOOKBACK, len(df) - 1):
            prev_max = highs[i-K2_LOOKBACK:i].max()
            if closes[i] > prev_max:
                r2, r1 = r1, lows[i]
        
        last_candle = df.iloc[-1]
        
        # Filtre: Hacim Şartı (20k USDT ve Ortalama Üstü)
        if last_vol >= VOL_THRESHOLD and last_vol > avg_vol:
            for level, label, l_type in [(r1, "🟠 RESET 1", "R1"), (r2, "🟣 RESET 2", "R2")]:
                if not np.isnan(level) and last_candle["low"] <= level and last_candle["close"] >= (level * 0.99):
                    sig_id = f"{symbol}_{l_type}_{last_candle['ts']}"
                    
                    if sig_id not in state["sent"]:
                        state["sent"].add(sig_id)
                        
                        sig_info = {
                            "symbol": symbol, 
                            "label": label, 
                            "price": last_candle["close"],
                            "time": datetime.now().strftime('%H:%M:%S'), 
                            "url": get_tv_link(symbol),
                            "vol": f"{last_vol/1000:.1f}k"
                        }
                        
                        # Dashboard listesine ekle
                        state["history"].insert(0, sig_info)
    except:
        pass

# --- TARAYICI DÖNGÜSÜ ---
def full_market_scanner():
    try:
        exchange.load_markets()
        # Sadece aktif USDT pariteleri
        symbols = [s for s in exchange.symbols if "/USDT" in s and exchange.markets[s]['active'] 
                   and not any(x in s for x in ["UP/", "DOWN/", "BUSD/", "USDC/"])]
        while True:
            for sym in symbols:
                analyze_symbol(sym)
                time.sleep(0.12) # Rate limit koruması
    except:
        time.sleep(10)
        full_market_scanner()

# Thread'i bir kez başlat
if "scanner_active" not in st.session_state:
    t = threading.Thread(target=full_market_scanner, daemon=True)
    t.start()
    st.session_state.scanner_active = True

# --- UI TASARIMI ---
st.set_page_config(page_title="ALGO TERMINAL", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #000000; color: white; }
    .signal-card { 
        background: #0a0a0a; border: 1px solid #1a1a1a; 
        padding: 25px; border-radius: 15px; margin-bottom: 20px;
    }
    .sym-link { font-size: 36px; font-weight: 900; color: #ffffff !important; text-decoration: none !important; }
    .sym-link:hover { color: #ffaa00 !important; }
    .price-text { font-size: 28px; color: #00ff00; font-family: monospace; font-weight: bold; }
    .label-text { font-size: 20px; color: #ffaa00; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

st.title("🚨 CANLI SİNYAL TERMİNALİ")
st.write(f"Filtre: >{VOL_THRESHOLD} USDT Hacim | Periyot: {TIMEFRAME}")

# Sinyalleri Listele
current_history = list(state["history"])

if not current_history:
    st.info("Market taranıyor, ilk sinyal bekleniyor... (Otomatik yenilenir)")
else:
    for sig in current_history[:50]:
        st.markdown(f"""
        <div class="signal-card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <a href="{sig['url']}" target="_blank" class="sym-link">{sig['symbol']}</a> <br>
                    <span class="label-text">{sig['label']}</span>
                </div>
                <div style="text-align: right;">
                    <span class="price-text">{sig['price']}</span> <span style="color:#444">USDT</span> <br>
                    <div style="color:#666; font-size:14px; margin-top:10px;">
                        🕒 {sig['time']} &nbsp; | &nbsp; 📊 Hacim: {sig['vol']} USDT
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

# Sayfayı 10 saniyede bir yenile
time.sleep(10)
st.rerun()
