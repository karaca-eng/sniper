import streamlit as st
import pandas as pd
import asyncio
import json
import websockets
import time
import threading
import requests
from datetime import datetime
from collections import deque

# --- CONFIGURATION ---
FAST_STRIKE_CHG = 0.5  # 1dk içinde minimum % değişim
FAST_STRIKE_VOL = 50000 # 1dk içinde minimum hacim (USDT)
TRI_WINDOW = 180
MAX_DISPLAY_ROWS = 300

class MarketRadar:
    def __init__(self):
        self.history = {}
        self.signals = []
        self.stats_hourly = {}
        self.stats_4h = {}
        self.lock = threading.RLock()
        self.last_heartbeat = 0
        self.total_pairs = 0
        self.last_reset_hour = datetime.now().hour
        self.last_reset_4h_block = datetime.now().hour // 4

    def check_resets(self):
        now = datetime.now()
        if now.hour != self.last_reset_hour:
            self.stats_hourly.clear()
            self.last_reset_hour = now.hour
        if (now.hour // 4) != self.last_reset_4h_block:
            self.stats_4h.clear()
            self.last_reset_4h_block = now.hour // 4

    def process_ticker(self, data):
        now = time.time()
        with self.lock:
            self.check_resets()
            self.last_heartbeat = now
            self.total_pairs = len(data)
            for item in data:
                symbol = item['s']
                if not symbol.endswith('USDT'): continue
                price, quote_vol = float(item['c']), float(item['q'])
                if symbol not in self.history:
                    self.history[symbol] = deque(maxlen=400)
                self.history[symbol].append((now, price, quote_vol))
                self.check_logic(symbol, now)

    def check_logic(self, symbol, now):
        hist = list(self.history[symbol])
        if len(hist) < 10: return

        current = hist[-1]
        # 1 Dakikalık pencereyi bul
        past_1m = next((x for x in hist if now - x[0] <= 60), hist[0])

        c1 = ((current[1] - past_1m[1]) / past_1m[1]) * 100
        vol_1m = current[2] - past_1m[2]

        # SADECE FLASH ATTACK MANTIĞI
        if abs(c1) >= FAST_STRIKE_CHG and vol_1m >= FAST_STRIKE_VOL:
            res_type = "PUMP" if c1 > 0 else "DUMP"
            self.add_signal(symbol, current[1], c1, vol_1m, res_type, "⚡ FLASH")

    def add_signal(self, symbol, price, chg_main, vol, s_type, mode):
        t_str = datetime.now().strftime("%H:%M:%S")
        sym_clean = symbol.replace("USDT", "")
        with self.lock:
            # Aynı saniye içinde aynı sembol için mükerrer sinyali engelle
            for s in self.signals[:5]:
                if s.get('Symbol') == sym_clean and s.get('Time', '') == t_str: return

            if sym_clean not in self.stats_hourly: self.stats_hourly[sym_clean] = {"PUMP": 0, "DUMP": 0}
            self.stats_hourly[sym_clean][s_type] += 1
            if sym_clean not in self.stats_4h: self.stats_4h[sym_clean] = {"PUMP": 0, "DUMP": 0}
            self.stats_4h[sym_clean][s_type] += 1

            self.signals.insert(0, {
                "Time": t_str, "Symbol": sym_clean, "Price": f"{price:.4f}" if price < 1 else f"{price:.2f}",
                "Chg": chg_main, "Vol": vol, "P/D": s_type, "Mode": mode,
                "SnapP": self.stats_4h[sym_clean]["PUMP"], "SnapD": self.stats_4h[sym_clean]["DUMP"]
            })
            if len(self.signals) > MAX_DISPLAY_ROWS: self.signals.pop()

@st.cache_resource
def get_radar_instance(): return MarketRadar()

async def binance_worker(radar_obj):
    uri = "wss://fstream.binance.com/ws/!miniTicker@arr"
    while True:
        try:
            async with websockets.connect(uri) as ws:
                while True:
                    radar_obj.process_ticker(json.loads(await ws.recv()))
        except:
            await asyncio.sleep(5)

# --- UI ---
st.set_page_config(layout="wide", page_title="Flash Speed Radar")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .status-live { color: #00ff88; font-weight: bold; border: 1px solid #00ff88; padding: 2px 10px; border-radius: 15px; font-size: 0.8rem; }
    .pump-label { background-color: #00ff88; color: black; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .dump-label { background-color: #ff4b4b; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
    .stat-card { background-color: #1e2127; padding: 10px; border-radius: 10px; margin-bottom: 10px; border-left: 5px solid #00ff88; }
    table { width: 100%; border-collapse: collapse; }
    th, td { white-space: nowrap; padding: 12px 15px; text-align: left; border-bottom: 1px solid #222; }
    .sym-link { color: #f1c40f; text-decoration: none; font-weight: bold; font-size: 1.1rem; }
    .green-arrow { color: #00ff88; font-weight: bold; }
    .red-arrow { color: #ff4b4b; font-weight: bold; }
    .row-flash-pump { background-color: rgba(0, 255, 136, 0.15) !important; border-left: 5px solid #00ff88 !important; }
    .row-flash-dump { background-color: rgba(255, 75, 75, 0.15) !important; border-left: 5px solid #ff4b4b !important; }
    </style>
""", unsafe_allow_html=True)

radar = get_radar_instance()
if "thread_started" not in st.session_state:
    threading.Thread(target=lambda: asyncio.run(binance_worker(radar)), daemon=True).start()
    st.session_state.thread_started = True

# Header
h1, h2, h3 = st.columns([2, 1, 1])
h1.title("⚡ Flash Speed Radar")
h1.caption("Anlık Momentum Takibi (1 Dakikalık Değişimler)")
status_html = '<span class="status-live">● LIVE STREAMING</span>' if (time.time() - radar.last_heartbeat) < 15 else '<span class="status-offline">● OFFLINE</span>'
h2.markdown(f"<div style='margin-top:10px;'>{status_html}</div>", unsafe_allow_html=True)
h3.metric("Pairs Tracked", radar.total_pairs)

st.divider()

col_side, col_main = st.columns([1, 4])
with col_main:
    header_col, search_col = st.columns([3, 1])
    header_col.subheader("📡 Flash Signals")
    search_query = search_col.text_input("Filter", placeholder="🔍 Sym...", label_visibility="collapsed", key="gs").upper()

placeholder_side = col_side.empty()
placeholder_main = col_main.empty()

while True:
    with placeholder_side.container():
        st.subheader("🔥 Top 5 Activity")
        with radar.lock:
            h_stats = getattr(radar, 'stats_hourly', {})
            sorted_stats = sorted(h_stats.items(), key=lambda x: x[1]['PUMP'] + x[1]['DUMP'], reverse=True)[:5]
            for sym, counts in sorted_stats:
                tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT.P"
                st.markdown(f'''<div class="stat-card"><a href="{tv_url}" target="_blank" class="sym-link">{sym}</a><br>
                <small><span class="green-arrow">↑ {counts["PUMP"]}</span> | <span class="red-arrow">↓ {counts["DUMP"]}</span></small></div>''', unsafe_allow_html=True)

    with placeholder_main.container():
        with radar.lock:
            signals = list(getattr(radar, 'signals', []))
            display_data = [s for s in signals if search_query in s.get('Symbol', '')] if search_query else signals
            if display_data:
                html = "<table><tr><th>Time</th><th>Symbol (4H ↑/↓)</th><th>Price</th><th>1m Momentum</th><th>1m Volume</th><th>Type</th></tr>"
                for row in display_data:
                    sym = row.get('Symbol'); p_count = row.get('SnapP'); d_count = row.get('SnapD')
                    tv_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym}USDT.P"
                    chg = row.get('Chg'); vol = row.get('Vol'); p_type = row.get('P/D')

                    row_class = ' class="row-flash-pump"' if p_type == "PUMP" else ' class="row-flash-dump"'

                    html += f"<tr{row_class}><td>{row.get('Time')}</td>"
                    html += f"<td><a href='{tv_url}' target='_blank' class='sym-link'>{sym}</a> <small class='green-arrow'>↑{p_count}</small> <small class='red-arrow'>↓{d_count}</small></td>"
                    html += f"<td>{row.get('Price')}</td>"
                    html += f"<td style='font-weight:bold;'>{chg:+.2f}%</td>"
                    html += f"<td>{vol / 1000:.1f}k</td>"
                    html += f"<td><span class='{'pump-label' if p_type == 'PUMP' else 'dump-label'}'>{p_type}</span></td></tr>"
                st.markdown(html + "</table>", unsafe_allow_html=True)
            else:
                st.info("Flash radar aktif, sert hareketler bekleniyor... 🔍")
    time.sleep(1)
