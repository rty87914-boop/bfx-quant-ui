import streamlit as st
import aiohttp
import asyncio
import pandas as pd
from datetime import timedelta
import logging

# ================= 0. 系統與日誌配置 =================
st.set_page_config(
    page_title="Bitfinex 投資監控儀表板", 
    page_icon="📊", 
    layout="wide", 
    initial_sidebar_state="collapsed"
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [UI] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= 1. 常數與初始化 =================
START_DATE_STR = "2026-02-11"
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "")

if 'refresh_rate' not in st.session_state: st.session_state.refresh_rate = 60
if 'last_update' not in st.session_state: st.session_state.last_update = "尚未同步"

# ================= 2. 視覺風格定義 =================
_ = st.components.v1.html("""<script>
    try { const head = window.parent.document.getElementsByTagName('head')[0]; const meta = window.parent.document.createElement('meta'); meta.name = 'apple-mobile-web-app-capable'; meta.content = 'yes'; head.appendChild(meta); } catch(e) {}
</script>""", height=0)

try:
    with open("style.css", "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
except FileNotFoundError:
    logger.warning("找不到 style.css，請建立該檔案以獲得最佳視覺體驗。")

# ================= 3. 資料獲取 (純讀取快取) =================
async def fetch_cached_data() -> dict:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {}
    
    headers = {
        "apikey": SUPABASE_KEY, 
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{SUPABASE_URL}/rest/v1/system_cache?id=eq.1", headers=headers, timeout=5) as res:
                if res.status == 200:
                    data = await res.json()
                    if data and len(data) > 0:
                        # 記錄最後更新時間，方便前台比對
                        st.session_state.last_update = data[0].get('updated_at', '未知時間')
                        return data[0].get('payload', {})
    except Exception as e:
        logger.error(f"UI Fetch Error: {e}")
    return {}

# ================= 4. UI 渲染邏輯 =================
if not SUPABASE_URL: 
    st.error("⚠️ 請在 Secrets 中配置 SUPABASE_URL 與 SUPABASE_KEY")
    st.stop()

with st.sidebar:
    st.markdown("<h3 style='color:#4ade80; font-family:Orbitron; margin-bottom:15px;'>⚙️ 顯示設定</h3>", unsafe_allow_html=True)
    st.session_state.refresh_rate = st.selectbox("刷新頻率", options=[0, 30, 60, 120, 300], format_func=lambda x: {0:"停用", 30:"30秒", 60:"1分", 120:"2分", 300:"5分"}[x], index=[0, 30, 60, 120, 300].index(st.session_state.refresh_rate))
    
    st.markdown("<hr style='border-color: rgba(255,255,255,0.1); margin:15px 0;'>", unsafe_allow_html=True)
    
    # 處理時間格式，將 UTC 轉為視覺上友善的格式
    display_time = st.session_state.last_update
    if "T" in display_time:
        display_time = display_time.replace("T", " ")[:19]
    st.markdown(f"<div style='color:#8899a6; font-size:0.75rem;'>雲端引擎最後同步:<br>{display_time} (UTC)</div>", unsafe_allow_html=True)

c_title, c_btn = st.columns([4, 1])
with c_title:
    st.markdown('<h2 style="color:#4ade80; margin:0; font-family:Orbitron; letter-spacing:1px; line-height:1.2;">BITFINEX 儀表板</h2>', unsafe_allow_html=True)
with c_btn:
    st.markdown('<div class="top-refresh-btn">', unsafe_allow_html=True)
    if st.button("🔄 刷新", use_container_width=True):
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

@st.fragment(run_every=timedelta(seconds=st.session_state.refresh_rate) if st.session_state.refresh_rate > 0 else None)
def dashboard_fragment():
    with st.spinner('⚡ 讀取雲端快取...'):
        data = asyncio.run(fetch_cached_data())
        
    if not data:
        st.warning("⏳ 尚未取得後端引擎的資料，請確認 Render 上的 Worker 是否正常運作中，或等待下一分鐘的同步。")
        st.stop()

    # 渲染 AI 洞察
    st.markdown(f'''
    <div class="metro-box" style="border-left: 4px solid #f97316; padding: 15px; margin-bottom: 15px;">
        <div class="ai-scanner-wrapper"><div class="ai-scanner-line"></div></div>
        <div style="z-index:1; position:relative;">
            <div style="color: #f97316; font-weight: bold; font-size: 0.85rem; margin-bottom: 8px;">🤖 總經與防欺騙教練</div>
            <div style="color: #fff; font-size: 0.85rem; line-height: 1.5;">{data.get('ai_insight_stored', '讀取中...')}</div>
        </div>
    </div>
    ''', unsafe_allow_html=True)

    # 渲染防禦狀態標籤
    c_btn1, c_btn2 = st.columns([3, 1])
    with c_btn1: 
        is_spoofed = (data.get('market_frr', 0) - data.get('market_twap', 0)) > 3.0
        spoof_color = "#ef4444" if is_spoofed else "#4ade80"
        spoof_text = "🚨 FRR 虛標警告" if is_spoofed else "🛡️ 市場利率健康"
        
        st.markdown(f'''
        <div style='display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom: 10px;'>
            <div style='color:#8899a6; font-size:0.75rem;'>🟢 雲端同步中</div>
            <div style='color:{spoof_color}; font-size:0.75rem; border:1px solid rgba({239 if is_spoofed else 74},{68 if is_spoofed else 222},{68 if is_spoofed else 128},0.3); padding:2px 8px; border-radius:12px; background:rgba({239 if is_spoofed else 74},{68 if is_spoofed else 222},{68 if is_spoofed else 128},0.1);'>
                {spoof_text} (FRR: {data.get('market_frr', 0):.1f}% / 真實: {data.get('market_twap', 0):.1f}%)
            </div>
        </div>''', unsafe_allow_html=True)
    with c_btn2: 
        pass # UI 端不再負責喚醒引擎，交由背景 Render 每分鐘自動執行並寫入

    # 渲染頂部總覽
    auto_p_display = f"${data.get('auto_p', 0):,.0f}" if data.get('auto_p', 0) > 0 else "🏆 零成本"
    st.markdown(f'''
    <div class="metro-box" style="border-left: 4px solid #4ade80; padding: 15px;">
        <div class="top-summary-grid">
            <div><div class="label-text">投入本金 <span style='font-weight:normal; font-size:0.7rem;'>({START_DATE_STR[5:]})</span></div><div class="value-text">{auto_p_display}</div></div>
            <div><div class="label-text">浮動配息預估</div><div class="value-text" style="color:#4ade80;">+${data.get("floating_payout", 0):.2f}</div></div>
            <div><div class="label-text">歷史總收益</div><div class="value-text" style="color:#4ade80;">+${data.get("history", 0):,.2f}</div></div>
        </div>
        <div style="border-top: 1px dashed rgba(255,255,255,0.1); margin-top: 5px; padding-top: 10px;">
            <div class="label-text" style="margin-bottom:2px;">USD/USDT 聯合淨資產</div>
            <div class="value-text" style="font-size:1.7rem;">${data.get("total", 0):,.2f} <span style="font-size:0.8rem; color:#aaa; font-weight:normal;">/ 估 {int(data.get("total", 0)*data.get("fx", 32)):,} NTD</span></div>
            <div class="sub-text">資金利用率: <span style="color:#4ade80">{100 - data.get("idle_pct", 0):.1f}%</span> (匯率 {data.get("fx", 32)})</div>
        </div>
    </div>''', unsafe_allow_html=True)

    # 渲染四宮格狀態
    next_repay_str = f"{int(data.get('next_repayment_time', 0)//3600)}h {int((data.get('next_repayment_time', 0)%3600)//60)}m" if data.get('next_repayment_time', 9999999) != 9999999 else "無資金"
    st.markdown(f'''
    <div class="metro-box" style="padding:15px;">
        <div class="status-grid">
            <div class="status-card">
                <div style="color:#0ea5e9; font-size:0.75rem; font-weight:bold; margin-bottom:5px;">當前淨年化</div>
                <div style="color:#fff; font-size:1.2rem; font-family:Orbitron;">{data.get("active_apr", 0):.2f}%</div>
            </div>
            <div class="status-card">
                <div style="color:#facc15; font-size:0.75rem; font-weight:bold; margin-bottom:5px;">下次配息總和</div>
                <div style="color:#fff; font-size:1.2rem; font-family:Orbitron;">+${data.get("next_payout_total", 0):.2f}</div>
                <div style="color:#8899a6; font-size:0.65rem; margin-top:3px;">浮動 ${data.get('floating_payout', 0):.1f}</div>
            </div>
            <div class="status-card {"idle-pulse" if data.get('idle_pct', 0) >= 10.0 else ""}">
                <div style="color:{"#ef4444" if data.get('idle_pct', 0) > 5 else "#4ade80"}; font-size:0.75rem; font-weight:bold; margin-bottom:5px;">閒置流失比</div>
                <div style="color:#fff; font-size:1.2rem; font-family:Orbitron;">{data.get("idle_pct", 0):.1f}%</div>
                <div style="color:#8899a6; font-size:0.65rem; margin-top:3px;">日失 ${data.get('daily_missed', 0):.1f}</div>
            </div>
            <div class="status-card">
                <div style="color:#4ade80; font-size:0.75rem; font-weight:bold; margin-bottom:5px;">最近解鎖</div>
                <div style="color:#fff; font-size:1.1rem; font-family:Orbitron;">{next_repay_str}</div>
            </div>
        </div>
    </div>''', unsafe_allow_html=True)

    tab_main, tab_loans, tab_offers = st.tabs(["📊 表現與對標", "🟢 活躍借出", "⏳ 掛單排隊"])

    with tab_main:
        current_apy = data.get('hist_apy', 0) if data.get('auto_p', 0) > 0 else data.get('stats', {}).get('overall', {}).get('true_apy', 0)
        st.markdown("<h5 style='color:#facc15; font-weight:bold; margin-left:5px; margin-top:5px; font-size:0.85rem;'>🇹🇼 台股 ETF 對標矩陣</h5>", unsafe_allow_html=True)
        etf_data = [{"name": "Bitfinex (目前)", "rate": current_apy, "is_base": True}, {"name": "0056 (元大)", "rate": 7.50}, {"name": "00878 (國泰)", "rate": 7.00}, {"name": "00713 (低波)", "rate": 8.00}]
        max_rate = max([item["rate"] for item in etf_data])

        grid_html = "<div class='etf-grid'>"
        for item in etf_data:
            is_winner = (item["rate"] == max_rate)
            b_color = "rgba(74, 222, 128, 0.6)" if is_winner else "rgba(255,255,255,0.08)"
            bg_color = "rgba(74,222,128,0.08)" if is_winner else "rgba(255,255,255,0.02)"
            
            if item.get("is_base"): sub_txt, sub_col = "策略基準", "#0ea5e9"
            else:
                spread = current_apy - item["rate"]
                sub_col = "#4ade80" if spread >= 0 else "#ef4444"
                sub_txt = f"領先 {spread:+.2f}%" if spread >= 0 else f"落後 {abs(spread):.2f}%"

            grid_html += f'''
            <div class='etf-card' style='background:{bg_color}; border: 1px solid {b_color};'>
                <div class='etf-title'>{"👑 " if is_winner else ""}{item['name']}</div>
                <div class='etf-rate'>{item['rate']:.2f}%</div>
                <div class='etf-spread' style='color:{sub_col};'>{sub_txt}</div>
            </div>'''
        grid_html += "</div>"
        st.markdown(grid_html, unsafe_allow_html=True)

        o_stat = data.get('stats', {}).get('overall', {})
        st.markdown("<h5 style='color:#f97316; font-weight:bold; margin-left:5px; margin-top:15px; font-size:0.85rem;'>📊 機器人綜合策略表現</h5>", unsafe_allow_html=True)
        if o_stat.get("is_empty"): 
            st.markdown("<div class='metro-box' style='padding: 20px; text-align:center;'><div style='color:#8899a6; font-size:0.8rem; font-style:italic;'>📭 需等待首批資金循環</div></div>", unsafe_allow_html=True)
        else:
            st.markdown(f'''
            <div class='metro-box' style='padding: 0;'>
                <div class='perf-container'>
                    <div class='perf-left'>
                        <div style='font-size:0.7rem; color:#8899a6; margin-bottom:5px;'>真實等效年化</div>
                        <div style='color:#f97316; font-size:1.8rem; font-family:Orbitron; font-weight:bold; text-shadow: 0 0 15px rgba(249,115,22,0.4);'>{o_stat.get('true_apy', 0):.2f}%</div>
                    </div>
                    <div class='perf-right'>
                        <span style='color:#4ade80;'>🎯 均毛利率：</span> {o_stat.get('gross_rate', 0):.2f}%<br>
                        <span style='color:#facc15;'>⏳ 平均等待：</span> {o_stat.get('wait', 0):.1f} h<br>
                        <span style='color:#3b82f6;'>🛡️ 平均存活：</span> {o_stat.get('survive', 0):.1f} h
                    </div>
                </div>
            </div>''', unsafe_allow_html=True)

    with tab_loans:
        st.markdown("<h5 style='color:#4ade80; font-size:0.85rem; margin-top:5px; margin-bottom:10px;'>🟢 已成交借出明細 (點擊標題排序)</h5>", unsafe_allow_html=True)
        if data.get('loans'):
            # 確保不會渲染到用來內部排序的隱藏欄位 "_sort_sec"
            df_loans = pd.DataFrame(data['loans']).drop(columns=['_sort_sec'], errors='ignore')
            st.dataframe(
                df_loans,
                column_config={
                    "金額 (USD)": st.column_config.NumberColumn(format="$ %d"),
                    "年化 (%)": st.column_config.NumberColumn(format="%.2f %%"),
                    "預估日收": st.column_config.NumberColumn(format="$ %.2f"),
                },
                hide_index=True, use_container_width=True, height=350
            )
        else:
            st.markdown("<div class='metro-box' style='padding: 20px; text-align:center;'><div style='color:#8899a6; font-size:0.85rem; font-weight:bold;'>💸 目前無活躍借出單</div></div>", unsafe_allow_html=True)

    with tab_offers:
        st.markdown("<h5 style='color:#facc15; font-size:0.85rem; margin-top:5px; margin-bottom:10px;'>⏳ 掛單排隊狀態 (點擊標題排序)</h5>", unsafe_allow_html=True)
        if data.get('offers'):
            st.dataframe(
                data['offers'],
                column_config={
                    "金額 (USD)": st.column_config.NumberColumn(format="$ %d"),
                },
                hide_index=True, use_container_width=True, height=350
            )
        else:
            st.markdown("<div class='metro-box' style='padding: 20px; text-align:center;'><div style='color:#8899a6; font-size:0.85rem; font-weight:bold;'>✨ 目前無排隊中掛單</div></div>", unsafe_allow_html=True)

dashboard_fragment()