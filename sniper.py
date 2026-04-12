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

# --- AYARLAR ---
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
        self.btc_status = "Veri Bekleniyor..."
        self.last_heartbeat = 0
        self.add_log("Sistem Cloud Modunda Başlatıldı.")

    def add_log(self, text):
        t = datetime.now().strftime("%H:%M:%S")
        self.logs.appendleft(f"[{t}] {text}")

    def update_btc_guard(self):
        try:
            url = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=2"
            res = requests.get(url, timeout=5).json()
            if res and len(res) >= 2:
                open_p, close_p = float(res[0][1]), float(res[-1][4])
                change = ((close_p - open_p) / open_p) * 100
                if change <= -0.8: self.btc_status = "🔴 TEHLİKE"
                elif change >= 0.5: self.btc_status = "🚀 BOĞA"
                else: self.btc_status = "OK"
        except: self.btc_status = "⚠️ BAĞLANTI HATASI"

    def get_4h_trend(self, symbol):
        try:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit=250"
            data = requests.get(url, timeout=5).json()
            closes = np.array([float(m[4]) for m in data])
            ema_200 = pd.Series(closes).ewm(span=200, adjust=False).mean().iloc[-1]
            return "BULL 🟢" if closes[-1] > ema_200 else "BEAR 🔴"
        except: return "UNKNOWN"

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
        if c_m > c_s and p_m <= p_s: pattern = "🐳 WHALE TRAP"
        elif abs(c_m - c_s) / close.iloc[-1] < 0.00015 and (c_m > p_m): pattern = "📐 DIAGONAL"
        elif c_m > p_m and c_m > macd.iloc[-10:-1].max(): pattern = "🚀 BREAKOUT"
        return status, pattern

    def process_ticker(self, data):
        now = time.time()
        self.last_heartbeat = now
        with self.lock:
            if datetime.now().hour != self.last_reset_hour:
                self.stats_1h.clear(); self.last_reset_hour = datetime.now().hour
            
            # BTC Guard güncellemesini process içine alalım ki Cloud thread'i canlı kalsın
            if int(now) % 300 == 0: self.update_btc_guard()
            
            self.total_pairs = len(data)
            for item in data:
                sym = item['s']
                if not sym.endswith('USDT'): continue
                p, q = float(item['c']), float(item['q'])
                if sym not in self.history: self.history[sym] = deque(maxlen=200)
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
        highs = df['high'].values[:-1]; lows = df['low'].values[:-1]
        res = highs[-LOOKBACK:].max(); sup = lows[-LOOKBACK:].min()
        if p > res:
            self.sent_signals[sym] = time.time()
            macd_stat, pattern = self.analyze_macd_patterns(df)
            trend_4h = self.get_4h_trend(sym)
            vols = df['q_volume'].tail(16).values
            avg_1m = vols.sum() / (16 * 15)
            rv_4h = (df['q_volume'].iloc[-1] / 15) / avg_1m if avg_1m > 0 else 1.0
            
            stats = self.stats_1h.get(sym, {"PUMP": 0, "DUMP": 0})
            range_s = res - sup
            tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}.P"
            
            sig_data = {
                "Zaman": datetime.now().strftime("%H:%M"), "Symbol": tv_url,
                "Tür": "🌟 SÜPER" if (trend_4h.startswith("BULL") and rv_4h > 2.0) else "🛡️ HYBRID",
                "Fiyat": f"{p:.4f}", "Sarı Çizgi": f"{res:.4f}",
                "MACD": f"{macd_stat} | {pattern if pattern else 'NORMAL'}",
                "TP1": f"{res + range_s:.4f}", "TP2": f"{res + (range_s * 1.618):.4f}",
                "STOP": f"{sup:.4f}", "Hacim": f"{rv_4h:.1f}x", "Trend": trend_4h,
                "İştah": f"↑{stats['PUMP']} | ↓{stats['DUMP']}"
            }
            self.display_signals.appendleft(sig_data)
            if pattern: self.pattern_signals.appendleft(sig_data)
            self.retest_watchlist[sym] = {"res": res, "state": "AWAITING"}
            self.add_log(f"Sinyal: {sym}")

    def fetch_klines(self, symbol):
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=50"
        try:
            d = requests.get(url, timeout=5).json()
            df = pd.DataFrame(d, columns=['t','o','high','low','close','v','_','q_volume','_','_','_','_'])
            df[['high','low','close','q_volume']] = df[['high','low','close','q_volume']].astype(float)
            return df
        except: return None

# --- WORKER FUNCTIONS ---
def run_ws_worker(radar_instance):
    async def ws_logic():
        uri = "wss://fstream.binance.com/ws/!miniTicker@arr"
        while True:
            try:
                async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
                    radar_instance.add_log("Binance WebSocket Bağlandı.")
                    while True:
                        msg = await ws.recv()
                        radar_instance.process_ticker(json.loads(msg))
            except Exception as e:
                radar_instance.add_log(f"WS Hatası: {e}. Yeniden deneniyor...")
                await asyncio.sleep(5)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ws_logic())

# --- UI START ---
st.set_page_config(layout="wide", page_title="Sniper Cloud Terminal")

# Global Radar Singleton
@st.cache_resource
def get_radar():
    return EliteSniperTerminalV15()

radar = get_radar()

# Thread Başlatma (Hata Kontrollü)
if "worker_started" not in st.session_state:
    worker_thread = threading.Thread(target=run_ws_worker, args=(radar,), daemon=True)
    add_script_run_ctx(worker_thread)
    worker_thread.start()
    st.session_state.worker_started = True

# UI GÖRÜNÜMÜ
h1, h2, h3 = st.columns([2, 1, 1])
h1.title("🛡️ Sniper Terminal Cloud")
status_color = "green" if (time.time() - radar.last_heartbeat) < 30 else "red"
h2.markdown(f"**Sistem Durumu:** :{status_color}[{'● CANLI' if status_color == 'green' else '● DURDU'}]")
h2.write(f"Takip Edilen: {radar.total_pairs} Çift")
h3.metric("BTC Durumu", radar.btc_status)

col_config = {"Symbol": st.column_config.LinkColumn("Grafik 📈", display_text=r"symbol=BINANCE:(.*?)\.P")}

st.divider()
st.subheader("📡 Canlı Sinyaller")
if radar.display_signals:
    st.dataframe(pd.DataFrame(list(radar.display_signals)), column_config=col_config, use_container_width=True, hide_index=True)
else:
    st.info("WebSocket'ten veri bekleniyor... Eğer bu yazı 1 dakikadan fazla kalırsa sayfayı yenileyin.")

c1, c2 = st.columns(2)
with c1:
    st.subheader("💎 MACD Desenleri")
    if radar.pattern_signals:
        st.dataframe(pd.DataFrame(list(radar.pattern_signals))[["Zaman", "Symbol", "MACD", "Hacim"]], column_config=col_config, use_container_width=True, hide_index=True)
with c2:
    st.subheader("🏗️ Retest")
    if radar.retest_watchlist:
        st.table([{"Symbol": k, "Durum": v['state']} for k, v in radar.retest_watchlist.items()])

# Cloud üzerinde performans için 3 saniye bekleme
time.sleep(3)
st.rerun()
