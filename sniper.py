import streamlit as st
import pandas as pd
import numpy as np
import asyncio
import json
import websockets
import time
import threading
import requests
from datetime import datetime
from collections import deque
from streamlit.runtime.scriptrunner import add_script_run_ctx

# --- CONFIGURATION ---
LOOKBACK = 20
FAST_STRIKE_CHG = 0.8
VOL_AVG_PERIOD = 20


class EliteSniperTerminalV15:
    def __init__(self):
        self.history = {}
        self.stats_1h = {}
        self.last_activity_time = {}
        self.sent_signals = {}
        self.retest_watchlist = {}
        self.display_signals = deque(maxlen=100)
        self.pattern_signals = deque(maxlen=50)
        self.logs = deque(maxlen=10)
        self.lock = threading.RLock()
        self.total_pairs = 0
        self.last_reset_hour = datetime.now().hour
        self.btc_status = "Bilinmiyor"
        self.add_log("Sistem V15 Başlatıldı. Dashboard Modu Aktif.")

    def add_log(self, text):
        t = datetime.now().strftime("%H:%M:%S")
        self.logs.appendleft(f"[{t}] {text}")

    # --- 🛡️ MARKET & TREND ANALİZİ ---
    def update_btc_guard(self):
        try:
            url = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=2"
            res = requests.get(url, timeout=2).json()
            open_p, close_p = float(res[0][1]), float(res[-1][4])
            change = ((close_p - open_p) / open_p) * 100
            if change <= -0.8:
                self.btc_status = "🔴 TEHLİKE"
            elif change >= 0.5:
                self.btc_status = "🚀 BOĞA"
            else:
                self.btc_status = "OK"
        except:
            self.btc_status = "⚠️ HATA"

    def get_4h_trend(self, symbol):
        try:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit=250"
            data = requests.get(url, timeout=3).json()
            closes = np.array([float(m[4]) for m in data])
            ema_200 = pd.Series(closes).ewm(span=200, adjust=False).mean().iloc[-1]
            return "BULL 🟢" if closes[-1] > ema_200 else "BEAR 🔴"
        except:
            return "UNKNOWN"

    def get_rv_4h(self, df):
        try:
            vols = df['q_volume'].tail(16).values
            avg_1m = vols.sum() / (16 * 15)
            curr_1m = df['q_volume'].iloc[-1] / 15
            return curr_1m / avg_1m if avg_1m > 0 else 1.0
        except:
            return 1.0

    def analyze_macd_patterns(self, df):
        close = df['close']
        fast = close.ewm(span=12, adjust=False).mean()
        slow = close.ewm(span=26, adjust=False).mean()
        macd = fast - slow
        signal = macd.ewm(span=9, adjust=False).mean()
        c_m, p_m = macd.iloc[-1], macd.iloc[-2]
        c_s, p_s = signal.iloc[-1], signal.iloc[-2]
        if c_m < 0: return "🔴 0 Altı", None
        status = "🟢 0 Üstü"
        pattern = None
        if c_m > c_s and p_m <= p_s:
            pattern = "🐳 WHALE TRAP"
        elif abs(c_m - c_s) / close.iloc[-1] < 0.00015 and (c_m > p_m):
            pattern = "📐 DIAGONAL"
        elif c_m > p_m and c_m > macd.iloc[-10:-1].max():
            pattern = "🚀 BREAKOUT"
        return status, pattern

    def process_ticker(self, data):
        now = time.time()
        with self.lock:
            if datetime.now().hour != self.last_reset_hour:
                self.stats_1h.clear();
                self.last_reset_hour = datetime.now().hour
            if int(now) % 300 == 0: self.update_btc_guard()
            for item in data:
                sym = item['s']
                if not sym.endswith('USDT'): continue
                p, q = float(item['c']), float(item['q'])
                if sym not in self.history: self.history[sym] = deque(maxlen=300)
                self.history[sym].append((now, p, q))
                self.update_activity(sym, now)
                self.detect_logic(sym, p, now)

    def update_activity(self, sym, now):
        hist = list(self.history[sym])
        if len(hist) < 20 or now - self.last_activity_time.get(sym, 0) < 15: return
        p_chg = ((hist[-1][1] - hist[0][1]) / hist[0][1]) * 100
        if abs(p_chg) >= 0.5:
            if sym not in self.stats_1h: self.stats_1h[sym] = {"PUMP": 0, "DUMP": 0}
            self.stats_1h[sym]["PUMP" if p_chg > 0 else "DUMP"] += 1
            self.last_activity_time[sym] = now

    def detect_logic(self, sym, p, now):
        hist = list(self.history[sym])
        if len(hist) < 20: return
        p_1m = next((x for x in hist if now - x[0] <= 60), hist[0])
        if ((p - p_1m[1]) / p_1m[1]) * 100 >= FAST_STRIKE_CHG:
            self.process_breakout(sym, p)

    def process_breakout(self, sym, p):
        if time.time() - self.sent_signals.get(sym, 0) < 300: return
        df = self.fetch_klines(sym)
        if df is None: return
        highs = df['high'].values[:-1];
        lows = df['low'].values[:-1]
        res = highs[-LOOKBACK:].max();
        sup = lows[-LOOKBACK:].min()
        if p > res:
            self.sent_signals[sym] = time.time()
            macd_stat, pattern = self.analyze_macd_patterns(df)
            trend_4h = self.get_4h_trend(sym)
            rv_4h = self.get_rv_4h(df)
            stats = self.stats_1h.get(sym, {"PUMP": 0, "DUMP": 0})
            range_s = res - sup
            tp1, tp2, sl = res + range_s, res + (range_s * 1.618), sup
            tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}.P"

            is_super = (trend_4h.startswith("BULL") and rv_4h > 2.0 and self.btc_status == "OK")

            sig_data = {
                "Zaman": datetime.now().strftime("%H:%M"),
                "Symbol": tv_url,
                "Tür": "🌟 SÜPER" if is_super else "🛡️ HYBRID",
                "Fiyat": f"{p:.4f}",
                "Sarı Çizgi": f"{res:.4f}",
                "MACD": f"{macd_stat} | {pattern if pattern else 'NORMAL'}",
                "TP1": f"{tp1:.4f}",
                "TP2": f"{tp2:.4f}",
                "STOP": f"{sl:.4f}",
                "Hacim": f"{rv_4h:.1f}x",
                "Trend": trend_4h,
                "İştah": f"↑{stats['PUMP']} | ↓{stats['DUMP']}",
                "BTC": self.btc_status
            }

            self.display_signals.appendleft(sig_data)
            if pattern:
                self.pattern_signals.appendleft(sig_data)
            self.retest_watchlist[sym] = {"res": res, "state": "AWAITING"}
            self.add_log(f"Yeni Sinyal: {sym} ({sig_data['Tür']})")

    def fetch_klines(self, symbol):
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=100"
        try:
            d = requests.get(url, timeout=3).json()
            df = pd.DataFrame(d, columns=['t', 'o', 'high', 'low', 'close', 'v', '_', 'q_volume', '_', '_', '_', '_'])
            df[['high', 'low', 'close', 'q_volume']] = df[['high', 'low', 'close', 'q_volume']].astype(float)
            return df
        except:
            return None


def run_ws_worker(radar_instance):
    async def ws_logic():
        uri = "wss://fstream.binance.com/ws/!miniTicker@arr"
        while True:
            try:
                async with websockets.connect(uri, ping_interval=15, ping_timeout=15) as ws:
                    radar_instance.add_log("Binance Bağlantısı Tamam.")
                    while True:
                        data = await ws.recv()
                        radar_instance.process_ticker(json.loads(data))
            except:
                await asyncio.sleep(5)

    loop = asyncio.new_event_loop();
    asyncio.set_event_loop(loop);
    loop.run_until_complete(ws_logic())


# --- UI (STREAMLIT V15) ---
st.set_page_config(layout="wide", page_title="Sniper V15 Dashboard")
radar = st.cache_resource(lambda: EliteSniperTerminalV15())()

if "init" not in st.session_state:
    t = threading.Thread(target=run_ws_worker, args=(radar,), daemon=True)
    add_script_run_ctx(t);
    t.start()
    st.session_state.init = True

# HEADER
h1, h2, h3 = st.columns([2, 1, 1])
h1.title("🛡️ Sniper Terminal V15 ELITE")
h2.metric("BTC Durumu", radar.btc_status)
h3.write(f"**Son İşlem:** {list(radar.logs)[0] if radar.logs else 'Bekleniyor...'}")

# Link Config
column_config = {
    "Symbol": st.column_config.LinkColumn("Coin (Grafik 📈)", display_text=r"symbol=BINANCE:(.*?)\.P")
}

st.divider()

# ANA TABLO (Telegram Mesaj İçeriği Tabloya Dönüştü)
st.subheader("📡 Canlı Sinyal Raporları (Detaylı)")
if radar.display_signals:
    df_main = pd.DataFrame(list(radar.display_signals))
    st.dataframe(df_main, column_config=column_config, use_container_width=True, hide_index=True)
else:
    st.info("Piyasa taranıyor, ilk sinyal raporu bekleniyor...")

st.divider()

# ALT BÖLÜM: MACD DESENLERİ VE RETEST
col_a, col_b = st.columns(2)

with col_a:
    st.subheader("💎 Elite MACD Desenleri")
    if radar.pattern_signals:
        df_pat = pd.DataFrame(list(radar.pattern_signals))
        st.dataframe(df_pat[["Zaman", "Symbol", "Tür", "MACD", "Trend", "Hacim"]], column_config=column_config,
                     use_container_width=True, hide_index=True)
    else:
        st.write("Özel MACD deseni henüz yok.")

with col_b:
    st.subheader("🏗️ Retest & Geri Çekilme Takibi")
    if radar.retest_watchlist:
        retest_list = []
        for sym, data in radar.retest_watchlist.items():
            retest_list.append({"Symbol": sym, "Direnç": f"{data['res']:.4f}", "Durum": data['state']})
        st.table(retest_list)
    else:
        st.write("Şu an pusu bekleyen retest yok.")

time.sleep(1.5);
st.rerun()
