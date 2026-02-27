import streamlit as st
import aiohttp
import asyncio
import pandas as pd
from datetime import timedelta, datetime
import logging

# ================= 0. 系統與日誌配置 =================
st.set_page_config(
    page_title="Bitfinex 量化終端", 
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
    logger.warning("找不到 style.css，請確認檔案位置。")

# ================= 3. 資料獲取 (純讀取快取) =================
async def fetch_cached_data() -> dict:
    if not SUPABASE_URL or not SUPABASE_KEY: return {}
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with aiohttp.ClientSession() as session:
            # 只讀取我們後端引擎準備好在 id=1 的那包整理好的 payload
            async with session.get(f"{SUPABASE_URL}/rest/v1/system_cache?id=eq.1", headers=headers, timeout=5) as res:
                if res.status == 200:
                    data = await res.json()
                    if data and len(data) > 0:
                        st.session_state.last_update = data[0].get('updated_at', '未知時間')
                        return data[0].get('payload', {})
    except Exception as e: logger.error(f"UI Fetch Error: {e}")
    return {}

# ================= 4. UI 渲染邏輯 =================
if not SUPABASE_URL: 
    st.error("⚠️ 請在 Secrets 中配置 SUPABASE_URL 與 SUPABASE_KEY")
    st.stop()

with st.sidebar:
    st.markdown("<h3 style='color:#eaecef; font-family:Inter; font-weight:600; margin-bottom:15px;'>⚙️ 系統設定</h3>", unsafe_allow_html=True)
    st.session_state.refresh_rate = st.selectbox("刷新頻率", options=[0, 30, 60, 120, 300], format_func=lambda x: {0:"停用", 30:"30秒", 60:"1分", 120:"2分", 300:"5分"}[x], index=[0, 30, 60, 120, 300].index(st.session_state.refresh_rate))
    
    st.markdown("<hr style='border-color: #2b3139; margin:15px 0;'>", unsafe_allow_html=True)
    
    display_time = st.session_state.last_update.replace("T", " ")[:19] if "T" in st.session_state.last_update else st.session_state.last_update
    st.markdown(f"<div style='color:#848e9c; font-size:0.8rem;'>引擎最後同步時間:<br><span style='color:#eaecef;'>{display_time}</span></div>", unsafe_allow_html=True)

c_title, c_btn = st.columns([5, 1])
with c_title:
    st.markdown('<h2 style="color:#eaecef; margin:0; font-family:Inter; font-weight:700; letter-spacing:0.5px;">Bitfinex 量化終端</h2>', unsafe_allow_html=True)
with c_btn:
    st.markdown('<div class="top-refresh-btn">', unsafe_allow_html=True)
    if st.button("🔄 手動刷新", use_container_width=True): st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

@st.fragment(run_every=timedelta(seconds=st.session_state.refresh_rate) if st.session_state.refresh_rate > 0 else None)
def dashboard_fragment():
    data = asyncio.run(fetch_cached_data())
    
    if not data:
        st.warning("⏳ 尚未取得後端引擎的資料，請確認 Render 上的 Worker 是否正常運作中。")
        st.stop()
        
    # 微交互：Toast 輕量通知 (取代擋畫面的 Spinner)
    time_str = st.session_state.last_update.split('T')[1][:5] if 'T' in st.session_state.last_update else ""
    st.toast(f"⚡ 數據同步成功 ({time_str})", icon="✅")

    # 1. 頂部狀態與 AI 診斷 (將標題改為正式金融術語「AI 診斷與策略分析」)
    is_spoofed = (data.get('market_frr', 0) - data.get('market_twap', 0)) > 3.0
    spoof_color = "#f6465d" if is_spoofed else "#0ecb81"
    spoof_bg = "rgba(246, 70, 93, 0.1)" if is_spoofed else "rgba(14, 203, 129, 0.1)"
    spoof_text = "🚨 市場 FRR 虛標警告" if is_spoofed else "🛡️ 市場利率結構健康"

    st.markdown(f'''
    <div style='display:flex; justify-content:space-between; align-items:center; margin-top:15px; margin-bottom: 20px;'>
        <div style='color:{spoof_color}; font-size:0.85rem; font-weight:600; border:1px solid {spoof_color}; padding:6px 12px; border-radius:6px; background:{spoof_bg};'>
            {spoof_text} (FRR: {data.get('market_frr', 0):.1f}% / 真實成交基準: {data.get('market_twap', 0):.1f}%)
        </div>
        <div style='color:#848e9c; font-size:0.85rem;'>
            🕵️ 策略側錄捕獲：<span style='color:#eaecef; font-weight:600;'>{data.get('logged_decisions_count', 0)} 筆決策</span>
        </div>
    </div>
    
    <div class="okx-panel" style="border-left: 4px solid #fcd535;">
        <div style="color: #fcd535; font-weight: 600; font-size: 0.9rem; margin-bottom: 10px;">🤖 AI 診斷與策略分析</div>
        <div style="color: #eaecef; font-size: 0.85rem; line-height: 1.6;">{data.get('ai_insight_stored', '讀取中...')}</div>
    </div>
    ''', unsafe_allow_html=True)

    # 2. 核心資產數據
    auto_p_display = f"${data.get('auto_p', 0):,.0f}" if data.get('auto_p', 0) > 0 else "🏆 零成本"
    st.markdown(f'''
    <div class="okx-panel">
        <div class="top-summary-grid">
            <div><div class="okx-label">合約投入本金 ({START_DATE_STR[5:]})</div><div class="okx-value">{auto_p_display}</div></div>
            <div><div class="okx-label">今日已實現收益</div><div class="okx-value text-green">+${data.get("today_profit", 0):.2f}</div></div>
            <div><div class="okx-label">累計總收益</div><div class="okx-value text-green">+${data.get("history", 0):,.2f}</div></div>
        </div>
        <div style="border-top: 1px solid #2b3139; margin-top: 5px; padding-top: 20px;">
            <div class="okx-label" style="margin-bottom:2px;">聯合淨資產 (USD/USDT)</div>
            <div class="okx-value" style="font-size:2rem;">${data.get("total", 0):,.2f} <span style="font-size:0.9rem; color:#5e6673; font-weight:normal;">≈ {int(data.get("total", 0)*data.get("fx", 32)):,} TWD</span></div>
            <div class="okx-subtext">資金利用率: <span class="text-green">{100 - data.get("idle_pct", 0):.1f}%</span> (參考匯率 {data.get("fx", 32)})</div>
        </div>
    </div>''', unsafe_allow_html=True)

    # 3. 策略指標狀態
    next_repay_str = f"{int(data.get('next_repayment_time', 0)//3600)}h {int((data.get('next_repayment_time', 0)%3600)//60)}m" if data.get('next_repayment_time', 9999999) != 9999999 else "無解鎖資金"
    st.markdown(f'''
    <div class="okx-panel" style="padding:16px;">
        <div class="status-grid">
            <div class="status-card">
                <div class="okx-label">當前淨年化 (APR)</div>
                <div class="okx-value" style="font-size:1.4rem;">{data.get("active_apr", 0):.2f}%</div>
            </div>
            <div class="status-card">
                <div class="okx-label">預計配息總和</div>
                <div class="okx-value text-green" style="font-size:1.4rem;">+${data.get("next_payout_total", 0):.2f}</div>
                <div class="okx-subtext">未結浮動 ${data.get('floating_payout', 0):.1f}</div>
            </div>
            <div class="status-card">
                <div class="okx-label">資金閒置率</div>
                <div class="okx-value {"text-red" if data.get('idle_pct', 0) > 5 else "text-green"}" style="font-size:1.4rem;">{data.get("idle_pct", 0):.1f}%</div>
                <div class="okx-subtext">日折損預估 ${data.get('daily_missed', 0):.1f}</div>
            </div>
            <div class="status-card">
                <div class="okx-label">最近合約到期</div>
                <div class="okx-value" style="font-size:1.4rem;">{next_repay_str}</div>
            </div>
        </div>
    </div>''', unsafe_allow_html=True)

    tab_main, tab_loans, tab_offers = st.tabs(["📊 策略表現對標", "🟢 活躍借出卡片", "⏳ 排隊掛單卡片"])

    with tab_main:
        current_apy = data.get('hist_apy', 0) if data.get('auto_p', 0) > 0 else data.get('stats', {}).get('overall', {}).get('true_apy', 0)
        st.markdown("<h5 style='color:#eaecef; font-weight:600; font-size:0.95rem; margin:15px 0;'>🇹🇼 台股 ETF 對標矩陣</h5>", unsafe_allow_html=True)
        etf_data = [{"name": "Bitfinex 量化策略", "rate": current_apy, "is_base": True}, {"name": "0056 元大高股息", "rate": 7.50}, {"name": "00878 國泰高股息", "rate": 7.00}, {"name": "00713 元大低波", "rate": 8.00}]
        max_rate = max([item["rate"] for item in etf_data])

        grid_html = "<div class='etf-grid'>"
        for item in etf_data:
            is_winner = (item["rate"] == max_rate)
            b_color = "#0ecb81" if is_winner else "#2b3139"
            bg_color = "rgba(14, 203, 129, 0.05)" if is_winner else "#0b0e11"
            
            if item.get("is_base"): 
                sub_txt, sub_bg, sub_col = "策略基準", "rgba(234, 236, 239, 0.1)", "#eaecef"
            else:
                spread = current_apy - item["rate"]
                sub_col = "#0ecb81" if spread >= 0 else "#f6465d"
                sub_bg = "rgba(14, 203, 129, 0.1)" if spread >= 0 else "rgba(246, 70, 93, 0.1)"
                sub_txt = f"領先 {spread:+.2f}%" if spread >= 0 else f"落後 {abs(spread):.2f}%"

            grid_html += f'''
            <div class='etf-card' style='background:{bg_color}; border: 1px solid {b_color};'>
                <div class='etf-title'>{"👑 " if is_winner else ""}{item['name']}</div>
                <div class='etf-rate'>{item['rate']:.2f}%</div>
                <div class='etf-spread' style='color:{sub_col}; background:{sub_bg};'>{sub_txt}</div>
            </div>'''
        grid_html += "</div>"
        st.markdown(grid_html, unsafe_allow_html=True)

        o_stat = data.get('stats', {}).get('overall', {})
        st.markdown("<h5 style='color:#eaecef; font-weight:600; font-size:0.95rem; margin:25px 0 15px 0;'>✅ 策略綜合績效數據 (基於真實回測)</h5>", unsafe_allow_html=True)
        if o_stat.get("is_empty"): 
            st.markdown("<div class='okx-panel' style='text-align:center; color:#848e9c;'>⏳ 等待首個合約循環完成數據收集</div>", unsafe_allow_html=True)
        else:
            st.markdown(f'''
            <div class='okx-panel' style='padding: 18px;'>
                <div class='perf-container'>
                    <div class='perf-left'>
                        <div class='okx-label'>真實等效年化 (True APY)</div>
                        <div class='okx-value text-green' style='font-size:2.2rem;'>{o_stat.get('true_apy', 0):.2f}%</div>
                    </div>
                    <div class='perf-right' style='color:#eaecef;'>
                        <div style='margin-bottom:8px;'><span style='color:#848e9c; display:inline-block; width:100px;'>🎯 平均毛年化：</span> {o_stat.get('gross_rate', 0):.2f}%</div>
                        <div style='margin-bottom:8px;'><span style='color:#848e9c; display:inline-block; width:100px;'>⏳ 平均撮合：</span> {o_stat.get('wait', 0):.1f} 小時</div>
                        <div><span style='color:#848e9c; display:inline-block; width:100px;'>🛡️ 平均展期：</span> {o_stat.get('survive', 0):.1f} 小時</div>
                    </div>
                </div>
            </div>''', unsafe_allow_html=True)

    with tab_loans:
        loans_data = data.get('loans', [])
        if not loans_data:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#848e9c; padding: 40px;'>💸 目前無活躍狀態借出合約</div>", unsafe_allow_html=True)
        else:
            # 修正文字跑版與佈局跑版
            cards_html = "<div class='okx-card-grid'><div style='display:none;'></div>"
            for l in loans_data:
                cards_html += f"""
                <div class='okx-item-card'>
                    <div class='okx-card-header'>
                        <span class='okx-tag tag-green'>活躍借出</span>
                        <span class='okx-card-amt'>${l['金額 (USD)']:,.2f}</span>
                    </div>
                    <div class='okx-card-body'>
                        <div class='okx-card-col'>
                            <span class='okx-label'>淨年化 (%)</span>
                            <span class='okx-value text-green'>{l['年化 (%)']:.2f}%</span>
                        </div>
                        <div class='okx-card-col text-right'>
                            <span class='okx-label'>預估日收</span>
                            <span class='okx-value'>${l['預估日收']:.2f}</span>
                        </div>
                        <div class='okx-card-col'>
                            <span class='okx-label'>出借時間</span>
                            <span class='okx-value' style='font-size:0.95rem; color:#eaecef;'>{l['出借時間']}</span>
                        </div>
                        <div class='okx-card-col text-right'>
                            <span class='okx-label'>到期時間</span>
                            <span class='okx-value text-red' style='font-size:0.95rem;'>{l['到期時間']}</span>
                        </div>
                    </div>
                </div>
                """
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

    with tab_offers:
        offers_data = data.get('offers', [])
        if not offers_data:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#848e9c; padding: 40px;'>✨ 目前無排隊中掛單</div>", unsafe_allow_html=True)
        else:
            # 優化掛單語意用語
            cards_html = "<div class='okx-card-grid'><div style='display:none;'></div>"
            for o in offers_data:
                # 將「卡單滯銷」改為專業用語「匹配滯緩」
                status_raw = o['狀態']
                short_status = "匹配滯緩" if "卡單" in status_raw else ("合約展期" if "換倉" in status_raw else "訂單撮合中")
                tag_class = "tag-red" if "卡單" in status_raw else ("tag-green" if "換倉" in status_raw else "tag-yellow")
                
                cards_html += f"""
                <div class='okx-item-card'>
                    <div class='okx-card-header'>
                        <span class='okx-tag {tag_class}'>{short_status}</span>
                        <span class='okx-card-amt'>${o['金額 (USD)']:,.2f}</span>
                    </div>
                    <div class='okx-card-body'>
                        <div class='okx-card-col'>
                            <span class='okx-label'>報價 (年化)</span>
                            <span class='okx-value' style='font-size:1.1rem;'>{o['毛年化']}</span>
                        </div>
                        <div class='okx-card-col text-right'>
                            <span class='okx-label'>合約天期</span>
                            <span class='okx-value'>{o['掛單天期']}</span>
                        </div>
                        <div class='okx-card-col'>
                            <span class='okx-label'>已排隊時長</span>
                            <span class='okx-value' style='color:#848e9c;'>{o['排隊時間']}</span>
                        </div>
                        <div class='okx-card-col text-right'>
                            <span class='okx-label'>排隊狀態</span>
                            <span class='okx-value text-yellow' style='font-size:0.95rem; line-height:1.2; font-weight:500;'>{status_raw}</span>
                        </div>
                    </div>
                </div>
                """
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

dashboard_fragment()