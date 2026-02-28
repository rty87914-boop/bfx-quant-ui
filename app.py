import streamlit as st
import aiohttp
import asyncio
import pandas as pd
from datetime import timedelta
import logging

# ================= 0. 系統與日誌配置 =================
st.set_page_config(page_title="資金管理終端", layout="wide", initial_sidebar_state="collapsed")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [UI] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= 1. 常數與初始化 =================
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "")

if 'refresh_rate' not in st.session_state: st.session_state.refresh_rate = 300
if 'last_update' not in st.session_state: st.session_state.last_update = "尚未同步"

# ================= 2. 視覺風格定義 (強制消除 iOS 狀態列白邊) =================
_ = st.components.v1.html("""<script>
    try { 
        const doc = window.parent.document;
        doc.body.style.backgroundColor = '#000000';
        doc.documentElement.style.backgroundColor = '#000000';
        
        // 強制清除預設的 theme-color
        const existingMetas = doc.querySelectorAll('meta[name="theme-color"]');
        existingMetas.forEach(m => m.remove());
        
        // 注入純黑 theme-color
        const metaBlack = doc.createElement('meta');
        metaBlack.name = 'theme-color';
        metaBlack.content = '#000000';
        doc.head.appendChild(metaBlack);
        
        const metaApple = doc.createElement('meta');
        metaApple.name = 'apple-mobile-web-app-status-bar-style';
        metaApple.content = 'black-translucent';
        doc.head.appendChild(metaApple);
    } catch(e) {}
</script>""", height=0)

try:
    with open("style.css", "r", encoding="utf-8") as f: st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
except FileNotFoundError: pass

# ================= 3. 資料獲取引擎 =================
async def fetch_cached_data(session) -> dict:
    if not SUPABASE_URL: return {}
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with session.get(f"{SUPABASE_URL}/rest/v1/system_cache?id=eq.1", headers=headers, timeout=5) as res:
            if res.status == 200:
                data = await res.json()
                if data:
                    st.session_state.last_update = data[0].get('updated_at', '尚未同步')
                    return data[0].get('payload', {})
    except Exception: pass
    return {}

async def fetch_bot_decisions(session) -> list:
    if not SUPABASE_URL: return []
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with session.get(f"{SUPABASE_URL}/rest/v1/bot_decisions?select=created_at,bot_rate_yearly,market_frr,market_twap,bot_amount,bot_period&order=created_at.desc&limit=100", headers=headers, timeout=5) as res:
            if res.status == 200: return await res.json()
    except Exception: pass
    return []

async def fetch_all_data():
    async with aiohttp.ClientSession() as session:
        return await asyncio.gather(fetch_cached_data(session), fetch_bot_decisions(session))

# ================= 4. 智能時間與時區轉換 =================
def format_time_smart(seconds):
    if not seconds or seconds >= 9999999: return "--"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h >= 24: return f"{h // 24}天 {h % 24}小時"
    return f"{h}h {m}m"

def parse_wait_time(time_str):
    if "h" in time_str and "m" in time_str:
        parts = time_str.split("h")
        try:
            h = int(parts[0].strip())
            m = parts[1].replace("m","").strip()
            if h >= 24: return f"{h // 24}天 {h % 24}小時"
        except: pass
    return time_str

def get_taiwan_time(utc_iso_str):
    if not utc_iso_str or utc_iso_str == "尚未同步": return "尚未同步"
    try:
        dt = pd.to_datetime(utc_iso_str)
        if dt.tz is None: dt = dt.tz_localize('UTC')
        tw_dt = dt.tz_convert('Asia/Taipei')
        return tw_dt.strftime('%m/%d %H:%M:%S')
    except:
        return str(utc_iso_str).replace("T", " ")[:19]

# ================= 5. UI 渲染邏輯 =================
if not SUPABASE_URL: st.stop()

# 頂部導航列 (無 Emoji，強制水平對齊)
c_title, c_btn = st.columns([1, 1], vertical_alignment="center")
with c_title:
    st.markdown('<h2 style="color:#ffffff; margin:0; font-family:Inter; font-weight:700; font-size:1.4rem; letter-spacing:-0.5px;">資金管理終端</h2>', unsafe_allow_html=True)
with c_btn:
    with st.popover("設定"):
        st.markdown("<div style='font-weight:600; color:#fff; margin-bottom:10px;'>系統設定</div>", unsafe_allow_html=True)
        st.session_state.refresh_rate = st.selectbox("自動刷新頻率", options=[0, 30, 60, 120, 300], format_func=lambda x: {0:"停用", 30:"30秒", 60:"1分鐘", 120:"2分鐘", 300:"5分鐘"}[x], index=[0, 30, 60, 120, 300].index(st.session_state.refresh_rate))
        tw_full_time = get_taiwan_time(st.session_state.last_update)
        st.markdown(f"<div style='color:#7a808a; font-size:0.8rem; margin:10px 0;'>背景同步: {tw_full_time}</div>", unsafe_allow_html=True)
        if st.button("強制刷新", use_container_width=True): st.rerun()

@st.fragment(run_every=timedelta(seconds=st.session_state.refresh_rate) if st.session_state.refresh_rate > 0 else None)
def dashboard_fragment():
    data, decisions = asyncio.run(fetch_all_data())
    if not data: return
        
    tw_full_time = get_taiwan_time(st.session_state.last_update)
    tw_short_time = tw_full_time.split(' ')[1][:5] if ' ' in tw_full_time else ""
    
    # 專業的 Live 狀態燈號 (純 CSS 圓點)
    st.markdown(f"<div style='text-align:right; color:#848e9c; font-size:0.75rem; font-weight:600; margin-top:-22px; margin-bottom:12px;'><span style='display:inline-block; width:6px; height:6px; background-color:#b2ff22; border-radius:50%; margin-right:4px; margin-bottom:1px;'></span>Live {tw_short_time}</div>", unsafe_allow_html=True)

    # 1. 核心資產數據 (移除圖表)
    auto_p_display = f"${data.get('auto_p', 0):,.0f}" if data.get('auto_p', 0) > 0 else "$0 (零成本)"
    st.markdown(f"""<div class="okx-panel"><div class="okx-label" style="margin-bottom:2px;">聯合淨資產 (USD/USDT)</div><div class="okx-value pulse-text" style="font-size:2.8rem; margin-bottom: 24px;">${data.get("total", 0):,.2f} <span style="font-size:0.9rem; color:#7a808a; font-weight:500;">≈ {int(data.get("total", 0)*data.get("fx", 32)):,} TWD</span></div><div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; border-top: 1px solid #1a1d24; padding-top: 20px;"><div><div class="okx-label">投入本金</div><div class="okx-value" style="font-size:1.3rem;">{auto_p_display}</div></div><div><div class="okx-label">今日實現收益</div><div class="okx-value text-green" style="font-size:1.3rem;">+${data.get("today_profit", 0):.2f}</div></div><div><div class="okx-label">累計總收益</div><div class="okx-value text-green" style="font-size:1.3rem;">+${data.get("history", 0):,.2f}</div></div></div></div>""", unsafe_allow_html=True)

    # 2. 策略指標狀態
    next_repay_str = format_time_smart(data.get('next_repayment_time', 9999999))
    st.markdown(f"""<div class="status-grid" style="margin-bottom: 20px;"><div class="status-card"><div class="okx-label">資金使用率</div><div class="okx-value {"text-red" if data.get('idle_pct', 0) > 5 else "text-green"}" style="font-size:1.4rem;">{100 - data.get("idle_pct", 0):.1f}%</div></div><div class="status-card"><div class="okx-label okx-tooltip" data-tip="目前所有借出資金的加權淨年化">當前淨年化 <i>i</i></div><div class="okx-value" style="font-size:1.4rem;">{data.get("active_apr", 0):.2f}%</div></div><div class="status-card"><div class="okx-label">預計利息收入</div><div class="okx-value text-green" style="font-size:1.4rem;">+${data.get("next_payout_total", 0):.2f}</div></div><div class="status-card"><div class="okx-label">最近解鎖時間</div><div class="okx-value" style="font-size:1.4rem;">{next_repay_str}</div></div></div>""", unsafe_allow_html=True)

    # 底部導航列
    tab_main, tab_loans, tab_offers, tab_analytics = st.tabs(["總覽", "借出", "掛單", "分析"])

    with tab_main:
        current_apy = data.get('stats', {}).get('overall', {}).get('true_apy', 0)
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:10px 0 10px 0;'>標竿對比</div>", unsafe_allow_html=True)
        etf_data = [{"name": "本策略 (真實年化)", "rate": current_apy, "is_base": True}, {"name": "0056", "rate": 7.50}, {"name": "00878", "rate": 7.00}, {"name": "00713", "rate": 8.00}]
        max_rate = max([item["rate"] for item in etf_data])

        grid_html = "<div class='etf-grid'>"
        for item in etf_data:
            is_winner = (item["rate"] == max_rate)
            card_class = "etf-card etf-card-active" if is_winner else "etf-card"
            sub_txt = "策略基準" if item.get("is_base") else (f"+{current_apy - item['rate']:.2f}%" if current_apy >= item['rate'] else f"{current_apy - item['rate']:.2f}%")
            sub_style = "color:#7a808a;" if item.get("is_base") else ("color:#b2ff22;" if current_apy >= item['rate'] else "color:#ff4d4f;")
            grid_html += f"<div class='{card_class}'><div class='etf-title'>{item['name']}</div><div class='etf-rate'>{item['rate']:.2f}%</div><div style='font-size:0.8rem; margin-top:8px; font-weight:600; {sub_style}'>{sub_txt}</div></div>"
        grid_html += "</div>"
        st.markdown(grid_html, unsafe_allow_html=True)

        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 10px 0;'>複利推演</div>", unsafe_allow_html=True)
        st.markdown("<div style='color:#7a808a; font-size:0.85rem; margin-bottom:10px;'>基於當前真實等效年化推算預期資產</div>", unsafe_allow_html=True)
        years = st.slider("推演年期 (年)", 1, 5, 2, label_visibility="collapsed")
        
        current_total = data.get("total", 0)
        future_val = current_total * ((1 + current_apy/100) ** years)
        profit_gained = future_val - current_total
        
        st.markdown(f"""<div class="okx-panel-outline" style="display:flex; justify-content:space-between; align-items:center;"><div style="color:#7a808a; font-weight:500;">{years} 年後預估</div><div style="text-align:right;"><div style="color:#b2ff22; font-size:1.6rem; font-weight:700; font-family:'JetBrains Mono', monospace;">${future_val:,.0f}</div><div style="color:#7a808a; font-size:0.85rem;">淨利潤 +${profit_gained:,.0f}</div></div></div>""", unsafe_allow_html=True)

        o_stat = data.get('stats', {}).get('overall', {})
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 10px 0;'>綜合績效指標</div>", unsafe_allow_html=True)
        if o_stat.get("is_empty"): 
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a;'>數據收集載入中...</div>", unsafe_allow_html=True)
        else:
            wait_str = format_time_smart(o_stat.get('wait', 0) * 3600)
            surv_str = format_time_smart(o_stat.get('survive', 0) * 3600)
            st.markdown(f"""<div class='okx-panel'><div class='okx-list-item border-bottom'><div class='okx-list-label okx-tooltip' data-tip="精準扣除所有閒置成本與手續費後的真實獲利能力">真實等效年化 (True APY) <i>i</i></div><div class='okx-list-value text-green' style='font-size:1.3rem;'>{o_stat.get('true_apy', 0):.2f}%</div></div><div class='okx-list-item border-bottom'><div class='okx-list-label'>平均毛年化</div><div class='okx-list-value'>{o_stat.get('gross_rate', 0):.2f}%</div></div><div class='okx-list-item border-bottom'><div class='okx-list-label okx-tooltip' data-tip="資金從回到錢包到下次成功借出的平均等待時間">平均撮合耗時 <i>i</i></div><div class='okx-list-value'>{wait_str}</div></div><div class='okx-list-item'><div class='okx-list-label okx-tooltip' data-tip="合約成功放貸並持續計息的平均壽命">平均存活時間 <i>i</i></div><div class='okx-list-value'>{surv_str}</div></div></div>""", unsafe_allow_html=True)

    with tab_loans:
        loans_data = data.get('loans', [])
        if not loans_data:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>目前無活躍借出合約</div>", unsafe_allow_html=True)
        else:
            total_loan_amt = sum(l.get('金額', l.get('金額 (USD)', 0)) for l in loans_data)
            total_daily_profit = sum(l.get('預估日收', 0) for l in loans_data)
            summary_html = f"""<div style="background: #121418; border-radius: 8px; padding: 16px; margin-top: 10px; margin-bottom: 20px; display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 16px;"><div><div class="okx-label">總借出金額</div><div class="okx-value okx-value-mono" style="font-size:1.2rem;">${total_loan_amt:,.2f}</div></div><div><div class="okx-label">活躍合約數</div><div class="okx-value okx-value-mono" style="font-size:1.2rem;">{len(loans_data)} <span style="font-size:0.8rem; color:#7a808a;">筆</span></div></div><div><div class="okx-label">加權年化</div><div class="okx-value text-green okx-value-mono" style="font-size:1.2rem;">{data.get("active_apr", 0):.2f}%</div></div><div><div class="okx-label">預估總日收</div><div class="okx-value text-green okx-value-mono" style="font-size:1.2rem;">${total_daily_profit:.2f}</div></div></div>"""
            st.markdown(summary_html, unsafe_allow_html=True)

            cards_html = "<div class='okx-card-grid'>"
            for l in loans_data:
                cards_html += f"<div class='okx-item-card'><div class='okx-card-header'><span class='okx-tag tag-gray'>活躍</span><span class='okx-card-amt'>${l.get('金額', l.get('金額 (USD)', 0)):,.2f} <span style='font-size:0.75rem; color:#7a808a;'>{l.get('幣種', 'USD')}</span></span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>淨年化</span><span class='okx-list-value text-green okx-value-mono'>{l.get('年化 (%)', 0):.2f}%</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>預估日收</span><span class='okx-list-value okx-value-mono'>${l.get('預估日收', 0):.2f}</span></div><div class='okx-list-item'><span class='okx-list-label'>到期時間</span><span class='okx-list-value' style='color:#848e9c; font-weight:400;'>{l.get('到期時間', '')}</span></div></div>"
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

    with tab_offers:
        offers_data = data.get('offers', [])
        if not offers_data:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>目前無排隊中掛單</div>", unsafe_allow_html=True)
        else:
            total_offer_amt = sum(o.get('金額', o.get('金額 (USD)', 0)) for o in offers_data)
            stuck_count = data.get('stuck_offers_count', 0)
            summary_html = f"""<div style="background: #121418; border-radius: 8px; padding: 16px; margin-top: 10px; margin-bottom: 20px; display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 16px;"><div><div class="okx-label">總排隊金額</div><div class="okx-value okx-value-mono" style="font-size:1.2rem;">${total_offer_amt:,.2f}</div></div><div><div class="okx-label">排隊掛單數</div><div class="okx-value okx-value-mono" style="font-size:1.2rem;">{len(offers_data)} <span style="font-size:0.8rem; color:#7a808a;">筆</span></div></div><div><div class="okx-label okx-tooltip" data-tip="等待時間超過系統容忍上限，建議手動降價">匹配滯緩 <i>i</i></div><div class="okx-value {'text-red' if stuck_count > 0 else 'text-green'} okx-value-mono" style="font-size:1.2rem;">{stuck_count} <span style="font-size:0.8rem; color:#7a808a;">筆</span></div></div></div>"""
            st.markdown(summary_html, unsafe_allow_html=True)

            cards_html = "<div class='okx-card-grid'>"
            for o in offers_data:
                status_raw = o.get('狀態', '')
                short_status = "滯緩" if "卡單" in status_raw else ("展期" if "換倉" in status_raw else "撮合")
                tag_class = "tag-red" if "卡單" in status_raw else ("tag-gray" if "換倉" in status_raw else "tag-yellow")
                wait_time = parse_wait_time(o.get('排隊時間', ''))
                cards_html += f"<div class='okx-item-card'><div class='okx-card-header'><span class='okx-tag {tag_class}'>{short_status}</span><span class='okx-card-amt'>${o.get('金額', o.get('金額 (USD)', 0)):,.2f} <span style='font-size:0.75rem; color:#7a808a;'>{o.get('幣種', 'USD')}</span></span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>報價 (年化)</span><span class='okx-list-value okx-value-mono'>{o.get('毛年化', '')}</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>合約天期</span><span class='okx-list-value'>{o.get('掛單天期', '')}</span></div><div class='okx-list-item'><span class='okx-list-label'>排隊時長</span><span class='okx-list-value' style='color:#848e9c; font-weight:400;'>{wait_time}</span></div></div>"
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

    with tab_analytics:
        is_spoofed = (data.get('market_frr', 0) - data.get('market_twap', 0)) > 3.0
        spoof_class = "text-red" if is_spoofed else "text-green"
        spoof_text = "溢價過高" if is_spoofed else "結構健康"
        
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:10px 0 12px 0;'>大盤監控</div>", unsafe_allow_html=True)
        market_html = f"""<div style="background: transparent; border-radius: 8px; padding: 16px; margin-bottom: 24px; display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 16px; border: 1px solid #1a1d24;"><div><div class="okx-label">市場結構</div><div class="okx-value {spoof_class}" style="font-size:1.05rem;">{spoof_text}</div></div><div><div class="okx-label okx-tooltip" data-tip="官方顯示的表面基準利率">表面 FRR <i>i</i></div><div class="okx-value okx-value-mono" style="font-size:1.05rem;">{data.get('market_frr', 0):.2f}%</div></div><div><div class="okx-label okx-tooltip" data-tip="過去 3 小時真實成交加權均價">真實 TWAP <i>i</i></div><div class="okx-value okx-value-mono" style="font-size:1.05rem; color:#0ea5e9;">{data.get('market_twap', 0):.2f}%</div></div><div><div class="okx-label okx-tooltip" data-tip="當前訂單簿吃下 50 萬美金的均價">壓力 VWAP <i>i</i></div><div class="okx-value okx-value-mono" style="font-size:1.05rem; color:#fcd535;">{data.get('market_vwap', 0):.2f}%</div></div></div>"""
        st.markdown(market_html, unsafe_allow_html=True)

        st.markdown(f"""<div class="okx-panel" style="padding:16px;"><div style="color: #b2ff22; font-weight: 600; font-size: 0.9rem; margin-bottom: 8px;">系統診斷報告</div><div style="color: #848e9c; font-size: 0.9rem; line-height: 1.6; font-weight:400;">{data.get('ai_insight_stored', '資料解析中...').replace('⚠️', '').replace('✅', '').replace('⚙️', '')}</div></div>""", unsafe_allow_html=True)

        if not decisions:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>資料庫樣本收集載入中...</div>", unsafe_allow_html=True)
        else:
            df = pd.DataFrame(decisions)
            df['時間'] = pd.to_datetime(df.get('created_at', pd.Series(range(len(df))))).dt.tz_convert('Asia/Taipei') if 'created_at' in df.columns else pd.Series(range(len(df)))

            if 'market_frr' in df.columns and 'bot_rate_yearly' in df.columns:
                df['market_twap'] = df.get('market_twap', df['market_frr']).fillna(df['market_frr'])
                
                win_rate_twap = (len(df[df['bot_rate_yearly'] >= df['market_twap']]) / len(df)) * 100 if len(df) > 0 else 0
                avg_spread_twap = (df['bot_rate_yearly'] - df['market_twap']).mean()

                st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 12px 0;'>策略對標分析</div>", unsafe_allow_html=True)
                summary_html = f"""<div style="background: transparent; border: 1px solid #1a1d24; border-radius: 8px; padding: 16px; margin-bottom: 20px; display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 16px;"><div><div class="okx-label okx-tooltip" data-tip="報價成功超越真實成交基準的比例">勝率 (對標 TWAP) <i>i</i></div><div class="okx-value text-green okx-value-mono" style="font-size:1.2rem;">{win_rate_twap:.1f}%</div></div><div><div class="okx-label okx-tooltip" data-tip="機器人比市場平均多賺取的溢價">真 Alpha 報酬 <i>i</i></div><div class="okx-value {'text-green' if avg_spread_twap >=0 else 'text-red'} okx-value-mono" style="font-size:1.2rem;">{avg_spread_twap:+.2f}%</div></div></div>"""
                st.markdown(summary_html, unsafe_allow_html=True)
                
                st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:30px 0 12px 0;'>操作日誌</div>", unsafe_allow_html=True)
                cards_html = "<div class='okx-card-grid'>"
                for _, row in df.head(10).iterrows():
                    spread_twap = row.get('bot_rate_yearly', 0) - row.get('market_twap', 0)
                    tag_class = "tag-green" if spread_twap >= 0 else "tag-gray"
                    cards_html += f"<div class='okx-item-card'><div class='okx-card-header'><span class='okx-tag {tag_class}'>Alpha {spread_twap:+.2f}%</span><span class='okx-card-amt'>${row.get('bot_amount', 0):,.0f}</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>報價</span><span class='okx-list-value okx-value-mono text-green'>{row.get('bot_rate_yearly', 0):.2f}%</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>TWAP</span><span class='okx-list-value okx-value-mono' style='color:#0ea5e9;'>{row.get('market_twap', 0):.2f}%</span></div><div class='okx-list-item'><span class='okx-list-label'>時間</span><span class='okx-list-value' style='color:#848e9c; font-weight:400;'>{row['時間'].strftime('%m/%d %H:%M') if isinstance(row['時間'], pd.Timestamp) else ''}</span></div></div>"
                cards_html += "</div>"
                st.markdown(cards_html, unsafe_allow_html=True)

dashboard_fragment()