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
MIN_VOL_3M = 40000

class VisualSniperRadarV13:
    def __init__(self):
        self.history = {}
        self.stats_1h = {}
        self.last_activity_time = {}
        self.radar_candidates = {}   
        self.display_signals = deque(maxlen=100)
        self.lock = threading.RLock()
        self.total_pairs = 0
        self.last_reset_hour = datetime.now().hour
        self.btc_status = "Bekleniyor..."

    def analyze_macd_window(self, df):
        # Sağlıklı MACD Hesaplama
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        macd_line = exp1 - exp2
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        
        count = 0
        for i in range(1, 16):
            if len(macd_line) < i+1: break
            m_curr, m_prev = macd_line.iloc[-i], macd_line.iloc[-(i+1)]
            s_curr, s_prev = signal_line.iloc[-i], signal_line.iloc[-(i+1)]
            
            # Paralel Çıkış ve Mesafe Disiplini
            is_healthy = (m_curr > 0 and s_curr > 0) and \
                         (m_curr > s_curr) and \
                         (m_curr > m_prev and s_curr > s_prev) and \
                         ((m_curr - s_curr) >= (m_prev - s_prev) * 0.97)
            
            if is_healthy: count += 1
            else: break
        return count

    def update_btc_status(self):
        try:
            url = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=2"
            res = requests.get(url, timeout=2).json()
            change = ((float(res[-1][4]) - float(res[0][1])) / float(res[0][1])) * 100
            self.btc_status = f"{'🟢' if change > -0.4 else '🔴'} BTC: %{change:.2f}"
        except: pass

    def process_ticker(self, data):
        now = time.time()
        with self.lock:
            if datetime.now().hour != self.last_reset_hour:
                self.stats_1h.clear(); self.last_reset_hour = datetime.now().hour
            if int(now) % 300 == 0: self.update_btc_status()
            
            for item in data:
                symbol = item['s']
                if not symbol.endswith('USDT'): continue
                price = float(item['c'])
                if symbol not in self.history: self.history[symbol] = deque(maxlen=200)
                self.history[symbol].append((now, price))
                self.detect_logic(symbol, price, now)

    def detect_logic(self, symbol, price, now):
        hist = list(self.history[symbol])
        if len(hist) < 20: return
        past_1m = next((x for x in hist if now - x[0] <= 60), hist[0])
        p_chg = ((price - past_1m[1]) / past_1m[1]) * 100
        
        # Aktivite Takibi
        if abs(p_chg) >= 0.5 and now - self.last_activity_time.get(symbol, 0) > 15:
            if symbol not in self.stats_1h: self.stats_1h[symbol] = {"PUMP": 0, "DUMP": 0}
            self.stats_1h[symbol]["PUMP" if p_chg > 0 else "DUMP"] += 1
            self.last_activity_time[symbol] = now

        # Derin Analiz Tetikleyici
        if p_chg >= 0.2:
            self.run_visual_analysis(symbol, price)

    def run_visual_analysis(self, symbol, price):
        df = self.fetch_klines(symbol)
        if df is None: return
        
        highs = df['high'].values[:-1]
        res_line = highs[-LOOKBACK:].max()
        macd_count = self.analyze_macd_window(df)
        dist = ((price / res_line) - 1) * 100
        
        clean_sym = symbol.replace("USDT", "")
        tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}.P"

        with self.lock:
            # RADAR GÜNCELLEME (En az 2 mum olanlar)
            if macd_count >= 2:
                self.radar_candidates[symbol] = {
                    "Symbol": clean_sym,
                    "MACD": macd_count,
                    "Price": f"{price:.4f}",
                    "To_Res": f"{dist:+.2f}%",
                    "Chart": tv_link
                }
            elif symbol in self.radar_candidates: del self.radar_candidates[symbol]

            # SİNYAL TETİKLEYİCİ (3-10 Mum Arası)
            if 3 <= macd_count <= 10:
                # Aynı mum sayısı için mükerrer kaydı engelle
                if not any(s['Symbol'] == clean_sym and s['MACD'] == f"{macd_count}.M" for s in self.display_signals):
                    stats = self.stats_1h.get(symbol, {"PUMP": 0, "DUMP": 0})
                    self.display_signals.appendleft({
                        "Time": datetime.now().strftime("%H:%M:%S"),
                        "Symbol": clean_sym,
                        "Price": f"{price:.4f}",
                        "MACD": f"{macd_count}.M",
                        "1H Aktivite": f"↑{stats['PUMP']} | ↓{stats['DUMP']}",
                        "Chart": tv_link
                    })

    def fetch_klines(self, symbol):
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=60"
        try:
            d = requests.get(url, timeout=2).json()
            df = pd.DataFrame(d, columns=['t','o','high','low','close','v','_','q','_','_','_','_'])
            df[['high','low','close']] = df[['high','low','close']].astype(float)
            return df
        except: return None

# --- UI SETTINGS ---
st.set_page_config(layout="wide", page_title="Visual Sniper Terminal")

radar = st.cache_resource(lambda: VisualSniperRadarV13())()

def run_ws():
    async def logic():
        uri = "wss://fstream.binance.com/ws/!miniTicker@arr"
        async with websockets.connect(uri, ping_interval=15, ping_timeout=15) as ws:
            while True: radar.process_ticker(json.loads(await ws.recv()))
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop); loop.run_until_complete(logic())

if "init" not in st.session_state:
    t = threading.Thread(target=run_ws, daemon=True)
    add_script_run_ctx(t); t.start()
    st.session_state.init = True

# --- RENDER ---
st.title("🎯 Visual Sniper Terminal V13")
h1, h2, h3 = st.columns(3)
h1.metric("Market", radar.btc_status)
h2.metric("Takipte", f"{len(radar.radar_candidates)} Aday")
h3.write(f"**Son Güncelleme:** {datetime.now().strftime('%H:%M:%S')}")

st.divider()

# Tablo link ayarları
link_config = {"Chart": st.column_config.LinkColumn("Grafik", display_text="Aç 📈")}

col_main, col_side = st.columns([2, 1])

with col_main:
    st.subheader("📡 Canlı Trend Sinyalleri (3-10. Mum)")
    if radar.display_signals:
        st.dataframe(pd.DataFrame(list(radar.display_signals)), width="stretch", hide_index=True, column_config=link_config)
    else:
        st.info("Trend başlangıcı (3. mum ve üzeri) bekleniyor...")

with col_side:
    st.subheader("🔍 Radar: Isınan Coinler")
    if radar.radar_candidates:
        cdf = pd.DataFrame.from_dict(radar.radar_candidates, orient='index')
        st.dataframe(cdf.sort_values('MACD', ascending=False), hide_index=True, column_config=link_config)
    else:
        st.write("Disiplini koruyan aday yok.")

time.sleep(1.5); st.rerun()
