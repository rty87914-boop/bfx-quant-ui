import streamlit as st
import aiohttp
import asyncio
import pandas as pd
from datetime import timedelta
import logging

# ================= 0. 系統與日誌配置 =================
st.set_page_config(
    page_title="Bitfinex 量化終端", 
    page_icon="⚡", 
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

# ================= 3. 資料獲取 =================
async def fetch_cached_data() -> dict:
    if not SUPABASE_URL or not SUPABASE_KEY: return {}
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with aiohttp.ClientSession() as session:
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
    st.markdown("<h3 style='color:#ffffff; font-family:Inter; font-weight:700; margin-bottom:15px;'>系統設定</h3>", unsafe_allow_html=True)
    st.session_state.refresh_rate = st.selectbox("前端刷新頻率", options=[0, 30, 60, 120, 300], format_func=lambda x: {0:"停用", 30:"30秒", 60:"1分", 120:"2分", 300:"5分"}[x], index=[0, 30, 60, 120, 300].index(st.session_state.refresh_rate))
    st.markdown("<hr style='border-color: #1a1d24; margin:15px 0;'>", unsafe_allow_html=True)
    display_time = st.session_state.last_update.replace("T", " ")[:19] if "T" in st.session_state.last_update else st.session_state.last_update
    st.markdown(f"<div style='color:#7a808a; font-size:0.85rem;'>資料庫最後同步:<br><span style='color:#ffffff;'>{display_time}</span></div>", unsafe_allow_html=True)

# 頂部導航列 
c_title, c_btn = st.columns([10, 2], vertical_alignment="bottom")
with c_title:
    st.markdown('<h2 style="color:#ffffff; margin:0; font-family:Inter; font-weight:800; font-size:2rem; letter-spacing:-0.5px;">資金管理終端</h2>', unsafe_allow_html=True)
with c_btn:
    if st.button("↻ 刷新資料"): st.rerun()

@st.fragment(run_every=timedelta(seconds=st.session_state.refresh_rate) if st.session_state.refresh_rate > 0 else None)
def dashboard_fragment():
    data = asyncio.run(fetch_cached_data())
    
    if not data:
        st.warning("尚未取得後端引擎的資料。")
        st.stop()
        
    time_str = st.session_state.last_update.split('T')[1][:5] if 'T' in st.session_state.last_update else ""
    st.toast(f"資料已同步 ({time_str})", icon="🟢")

    # 1. 核心資產數據
    auto_p_display = f"${data.get('auto_p', 0):,.0f}" if data.get('auto_p', 0) > 0 else "$0 (零成本)"
    st.markdown(f"""<div class="okx-panel" style="margin-top: 20px;"><div class="okx-label" style="margin-bottom:2px;">聯合淨資產 (USD/USDT)</div><div class="okx-value" style="font-size:2.5rem; margin-bottom: 24px;">${data.get("total", 0):,.2f} <span style="font-size:0.9rem; color:#7a808a; font-weight:500;">≈ {int(data.get("total", 0)*data.get("fx", 32)):,} TWD</span></div><div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; border-top: 1px solid #1a1d24; padding-top: 20px;"><div><div class="okx-label">合約投入本金</div><div class="okx-value" style="font-size:1.3rem;">{auto_p_display}</div></div><div><div class="okx-label">今日已實現收益</div><div class="okx-value text-green" style="font-size:1.3rem;">+${data.get("today_profit", 0):.2f}</div></div><div><div class="okx-label">累計總收益</div><div class="okx-value text-green" style="font-size:1.3rem;">+${data.get("history", 0):,.2f}</div></div></div></div>""", unsafe_allow_html=True)

    # 2. 策略指標狀態 
    next_repay_str = f"{int(data.get('next_repayment_time', 0)//3600)}h {int((data.get('next_repayment_time', 0)%3600)//60)}m" if data.get('next_repayment_time', 9999999) != 9999999 else "--"
    st.markdown(f"""<div class="status-grid" style="margin-bottom: 24px;"><div class="status-card"><div class="okx-label">資金使用率</div><div class="okx-value {"text-red" if data.get('idle_pct', 0) > 5 else "text-green"}" style="font-size:1.4rem;">{100 - data.get("idle_pct", 0):.1f}%</div></div><div class="status-card"><div class="okx-label">當前淨年化</div><div class="okx-value" style="font-size:1.4rem;">{data.get("active_apr", 0):.2f}%</div></div><div class="status-card"><div class="okx-label">預計利息收入</div><div class="okx-value text-green" style="font-size:1.4rem;">+${data.get("next_payout_total", 0):.2f}</div></div><div class="status-card"><div class="okx-label">最近解鎖時間</div><div class="okx-value" style="font-size:1.4rem;">{next_repay_str}</div></div></div>""", unsafe_allow_html=True)

    tab_main, tab_loans, tab_offers = st.tabs(["策略表現", "活躍借出", "排隊掛單"])

    with tab_main:
        current_apy = data.get('hist_apy', 0) if data.get('auto_p', 0) > 0 else data.get('stats', {}).get('overall', {}).get('true_apy', 0)
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1rem; margin:20px 0 12px 0;'>標竿對比 (Benchmark)</div>", unsafe_allow_html=True)
        etf_data = [{"name": "本策略", "rate": current_apy, "is_base": True}, {"name": "0056", "rate": 7.50}, {"name": "00878", "rate": 7.00}, {"name": "00713", "rate": 8.00}]
        max_rate = max([item["rate"] for item in etf_data])

        grid_html = "<div class='etf-grid'>"
        for item in etf_data:
            is_winner = (item["rate"] == max_rate)
            card_class = "etf-card etf-card-active" if is_winner else "etf-card"
            
            if item.get("is_base"): 
                sub_txt = "基準"
                sub_style = "color:#7a808a;"
            else:
                spread = current_apy - item["rate"]
                sub_txt = f"+{spread:.2f}%" if spread >= 0 else f"{spread:.2f}%"
                sub_style = "color:#b2ff22;" if spread >= 0 else "color:#ff4d4f;"

            grid_html += f"<div class='{card_class}'><div class='etf-title'>{item['name']}</div><div class='etf-rate'>{item['rate']:.2f}%</div><div style='font-size:0.8rem; margin-top:8px; font-weight:600; {sub_style}'>{sub_txt}</div></div>"
        grid_html += "</div>"
        st.markdown(grid_html, unsafe_allow_html=True)

        o_stat = data.get('stats', {}).get('overall', {})
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1rem; margin:24px 0 12px 0;'>綜合績效指標</div>", unsafe_allow_html=True)
        if o_stat.get("is_empty"): 
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a;'>等待數據收集</div>", unsafe_allow_html=True)
        else:
            st.markdown(f"""<div class='okx-panel'><div class='okx-list-item border-bottom'><div class='okx-list-label'>真實等效年化 (True APY)</div><div class='okx-list-value text-green' style='font-size:1.4rem;'>{o_stat.get('true_apy', 0):.2f}%</div></div><div class='okx-list-item border-bottom'><div class='okx-list-label'>平均毛年化</div><div class='okx-list-value'>{o_stat.get('gross_rate', 0):.2f}%</div></div><div class='okx-list-item border-bottom'><div class='okx-list-label'>平均撮合耗時</div><div class='okx-list-value'>{o_stat.get('wait', 0):.1f} h</div></div><div class='okx-list-item'><div class='okx-list-label'>平均存活時間</div><div class='okx-list-value'>{o_stat.get('survive', 0):.1f} h</div></div></div>""", unsafe_allow_html=True)

        # 【搬移完成】AI 策略分析引擎改至此處
        st.markdown(f"""<div class="okx-panel" style="margin-top: 20px;"><div style="color: #b2ff22; font-weight: 700; font-size: 0.9rem; margin-bottom: 8px; display:flex; align-items:center; gap:6px;"><span style="width:6px; height:6px; border-radius:50%; background:#b2ff22;"></span>策略分析引擎</div><div style="color: #ffffff; font-size: 0.95rem; line-height: 1.6; font-weight:400;">{data.get('ai_insight_stored', '資料解析中...')}</div></div>""", unsafe_allow_html=True)

    with tab_loans:
        loans_data = data.get('loans', [])
        if not loans_data:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>目前無活躍借出合約</div>", unsafe_allow_html=True)
        else:
            # 活躍單專屬統整儀表板 (安全降級防護)
            total_loan_amt = sum(l.get('金額', l.get('金額 (USD)', 0)) for l in loans_data)
            total_daily_profit = sum(l.get('預估日收', 0) for l in loans_data)
            loan_count = len(loans_data)
            avg_apr = data.get("active_apr", 0)

            summary_html = f"""<div style="background: #121418; border-radius: 12px; padding: 16px; margin-top: 10px; margin-bottom: 20px; display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 16px;"><div><div class="okx-label">總借出金額</div><div class="okx-value okx-value-mono" style="font-size:1.3rem;">${total_loan_amt:,.2f}</div></div><div><div class="okx-label">活躍合約數</div><div class="okx-value okx-value-mono" style="font-size:1.3rem;">{loan_count} <span style="font-size:0.9rem; color:#7a808a;">筆</span></div></div><div><div class="okx-label">加權年化</div><div class="okx-value text-green okx-value-mono" style="font-size:1.3rem;">{avg_apr:.2f}%</div></div><div><div class="okx-label">預估總日收</div><div class="okx-value text-green okx-value-mono" style="font-size:1.3rem;">${total_daily_profit:.2f}</div></div></div>"""
            st.markdown(summary_html, unsafe_allow_html=True)

            # 卡片牆 (加入 .get 安全讀取)
            cards_html = "<div class='okx-card-grid'>"
            for l in loans_data:
                amt = l.get('金額', l.get('金額 (USD)', 0))
                coin = l.get('幣種', 'USD')
                apr = l.get('年化 (%)', 0)
                profit = l.get('預估日收', 0)
                end_time = l.get('到期時間', '')
                
                cards_html += f"<div class='okx-item-card'><div class='okx-card-header'><span class='okx-tag tag-green'>活躍中</span><span class='okx-card-amt'>${amt:,.2f} <span style='font-size:0.8rem; color:#7a808a;'>{coin}</span></span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>淨年化</span><span class='okx-list-value text-green okx-value-mono'>{apr:.2f}%</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>預估日收</span><span class='okx-list-value okx-value-mono'>${profit:.2f}</span></div><div class='okx-list-item'><span class='okx-list-label'>到期時間</span><span class='okx-list-value' style='color:#7a808a; font-weight:500;'>{end_time}</span></div></div>"
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

    with tab_offers:
        offers_data = data.get('offers', [])
        if not offers_data:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>目前無排隊中掛單</div>", unsafe_allow_html=True)
        else:
            # 掛單專屬統整儀表板 (安全降級防護)
            total_offer_amt = sum(o.get('金額', o.get('金額 (USD)', 0)) for o in offers_data)
            offer_count = len(offers_data)
            stuck_count = data.get('stuck_offers_count', 0)

            summary_html = f"""<div style="background: #121418; border-radius: 12px; padding: 16px; margin-top: 10px; margin-bottom: 20px; display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 16px;"><div><div class="okx-label">總排隊金額</div><div class="okx-value okx-value-mono" style="font-size:1.3rem;">${total_offer_amt:,.2f}</div></div><div><div class="okx-label">排隊掛單數</div><div class="okx-value okx-value-mono" style="font-size:1.3rem;">{offer_count} <span style="font-size:0.9rem; color:#7a808a;">筆</span></div></div><div><div class="okx-label">匹配滯緩</div><div class="okx-value {'text-red' if stuck_count > 0 else 'text-green'} okx-value-mono" style="font-size:1.3rem;">{stuck_count} <span style="font-size:0.9rem; color:#7a808a;">筆</span></div></div></div>"""
            st.markdown(summary_html, unsafe_allow_html=True)

            # 卡片牆 (加入 .get 安全讀取)
            cards_html = "<div class='okx-card-grid'>"
            for o in offers_data:
                status_raw = o.get('狀態', '')
                short_status = "匹配滯緩" if "卡單" in status_raw else ("合約展期" if "換倉" in status_raw else "撮合中")
                tag_class = "tag-red" if "卡單" in status_raw else ("tag-green" if "換倉" in status_raw else "tag-yellow")
                
                amt = o.get('金額', o.get('金額 (USD)', 0))
                coin = o.get('幣種', 'USD')
                apr = o.get('毛年化', '')
                period = o.get('掛單天期', '')
                wait_time = o.get('排隊時間', '')
                
                cards_html += f"<div class='okx-item-card'><div class='okx-card-header'><span class='okx-tag {tag_class}'>{short_status}</span><span class='okx-card-amt'>${amt:,.2f} <span style='font-size:0.8rem; color:#7a808a;'>{coin}</span></span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>報價 (年化)</span><span class='okx-list-value okx-value-mono'>{apr}</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>合約天期</span><span class='okx-list-value'>{period}</span></div><div class='okx-list-item'><span class='okx-list-label'>已排隊時長</span><span class='okx-list-value' style='color:#7a808a; font-weight:500;'>{wait_time}</span></div></div>"
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

    # 4. 底部系統監控列
    st.markdown("<hr style='border-color: #1a1d24; margin:40px 0 20px 0;'>", unsafe_allow_html=True)
    
    is_spoofed = (data.get('market_frr', 0) - data.get('market_twap', 0)) > 3.0
    spoof_class = "alert" if is_spoofed else ""
    spoof_text = "FRR 溢價警告" if is_spoofed else "正常"

    st.markdown(f"""<div style="margin-bottom: 20px;"><div class="footer-tag {spoof_class}">市場結構: <span>{spoof_text}</span></div><div class="footer-tag">綜合 FRR: <span class="okx-value-mono">{data.get('market_frr', 0):.1f}%</span></div><div class="footer-tag">綜合 TWAP: <span class="okx-value-mono">{data.get('market_twap', 0):.1f}%</span></div><div class="footer-tag">策略側錄: <span class="okx-value-mono">{data.get('logged_decisions_count', 0)}</span> 筆</div></div>""", unsafe_allow_html=True)

dashboard_fragment()