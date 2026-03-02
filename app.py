import streamlit as st
import aiohttp
import asyncio
import pandas as pd
from datetime import timedelta, datetime
import logging
import json

# ================= 0. 系統與日誌配置 =================
st.set_page_config(page_title="資金管理終端", layout="wide", initial_sidebar_state="collapsed")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [UI] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= 1. 常數與初始化 =================
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "")
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", "")

if 'refresh_rate' not in st.session_state: st.session_state.refresh_rate = 300
if 'last_update' not in st.session_state: st.session_state.last_update = "尚未同步"
if 'ai_insight_result' not in st.session_state: st.session_state.ai_insight_result = None
if 'logged_in_user' not in st.session_state: st.session_state.logged_in_user = None

# ================= 2. 視覺風格定義 =================
_ = st.components.v1.html("""<script>
    function forceBlackAndPWA(doc) {
        if (!doc) return;
        doc.documentElement.style.background = '#000000';
        doc.body.style.background = '#000000';
        const oldMetas = doc.querySelectorAll('meta[name="theme-color"]');
        oldMetas.forEach(m => m.remove());
        const metaBlack = doc.createElement('meta');
        metaBlack.name = 'theme-color';
        metaBlack.content = '#000000';
        doc.head.appendChild(metaBlack);
    }
    try { forceBlackAndPWA(document); } catch(e) {}
    try { forceBlackAndPWA(window.parent.document); } catch(e) {}
</script>""", height=0, width=0)

# ================= 3. 資料獲取與設定引擎 =================
async def fetch_cached_data(session, db_id) -> dict:
    if not SUPABASE_URL: return {}
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with session.get(f"{SUPABASE_URL}/rest/v1/system_cache?id=eq.{db_id}", headers=headers, timeout=5) as res:
            if res.status == 200:
                data = await res.json()
                if data:
                    if db_id == 1: st.session_state.last_update = data[0].get('updated_at', '尚未同步')
                    return data[0].get('payload', {})
    except Exception: pass
    return {}

async def fetch_all_auth_data() -> dict:
    default_users = {
        "mingyu": {"pin": "1234", "name": "量化主理人", "role": "lending", "db_id": 1},
        "friend": {"pin": "5678", "name": "DOT 投資人", "role": "staking", "db_id": 2}
    }
    if not SUPABASE_URL: return default_users
    
    async with aiohttp.ClientSession() as session:
        r1, r2 = await asyncio.gather(fetch_cached_data(session, 1), fetch_cached_data(session, 2))
        if r1.get('settings', {}).get('pin'): default_users["mingyu"]["pin"] = str(r1['settings']['pin'])
        if r2.get('settings', {}).get('pin'): default_users["friend"]["pin"] = str(r2['settings']['pin'])
        return default_users

async def update_user_settings(db_id: int, new_settings: dict):
    if not SUPABASE_URL: return False
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    async with aiohttp.ClientSession() as session:
        current_payload = await fetch_cached_data(session, db_id)
        current_settings = current_payload.get('settings', {})
        if not isinstance(current_settings, dict): current_settings = {}
        
        current_settings.update(new_settings)
        current_payload['settings'] = current_settings
        
        update_body = {"id": db_id, "payload": current_payload, "updated_at": datetime.utcnow().isoformat()}
        try:
            async with session.post(f"{SUPABASE_URL}/rest/v1/system_cache?on_conflict=id", headers=headers, json=update_body) as res:
                if res.status in (200, 201, 204): return True
        except Exception: pass
    return False

async def fetch_bot_decisions(session) -> list:
    if not SUPABASE_URL: return []
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with session.get(f"{SUPABASE_URL}/rest/v1/bot_decisions?select=created_at,bot_rate_yearly,market_frr,market_twap,bot_amount,bot_period&order=created_at.desc&limit=100", headers=headers, timeout=5) as res:
            if res.status == 200: return await res.json()
    except Exception: pass
    return []

async def fetch_equity_history(session) -> list:
    if not SUPABASE_URL: return []
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with session.get(f"{SUPABASE_URL}/rest/v1/bfx_nav?select=record_date,auto_p,hist_p&order=record_date.asc", headers=headers, timeout=5) as res:
            if res.status == 200: return await res.json()
    except Exception: pass
    return []

async def fetch_all_data_lending():
    async with aiohttp.ClientSession() as session:
        return await asyncio.gather(fetch_cached_data(session, 1), fetch_bot_decisions(session), fetch_equity_history(session))

async def fetch_all_data_staking():
    async with aiohttp.ClientSession() as session:
        return await fetch_cached_data(session, 2)

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

# ================= 4. 動態登入介面 (絕對置中) =================
if not SUPABASE_URL:
    st.error("系統配置錯誤：缺少 SUPABASE_URL")
    st.stop()

USERS = asyncio.run(fetch_all_auth_data())

if st.session_state.logged_in_user is None:
    st.columns(1) # 隱形盾牌
    st.markdown("<div style='text-align:center; margin-top:8vh; margin-bottom: 24px;'><h1 style='color:#ffffff; font-weight:700;'>資金管理終端登入</h1></div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1.5, 1])
    with c2:
        with st.container(border=True):
            selected_user = st.selectbox("選擇帳號", options=list(USERS.keys()), format_func=lambda x: USERS[x]["name"])
            pin_input = st.text_input("輸入密碼 (PIN)", type="password")
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("登入系統", use_container_width=True, type="primary"):
                if USERS[selected_user]["pin"] == pin_input:
                    st.session_state.logged_in_user = selected_user
                    st.rerun()
                else:
                    st.error("密碼錯誤，請重試。")
    st.stop()

# ================= 5. 載入面板專屬 CSS =================
try:
    with open("style.css", "r", encoding="utf-8") as f: 
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
except FileNotFoundError: pass

user_info = USERS[st.session_state.logged_in_user]

if user_info["role"] == "staking":
    user_data = asyncio.run(fetch_all_data_staking())
else:
    user_data = {}

# ================= 6. UI 渲染邏輯 =================
st.columns(1) # 🛡️ 隱形盾牌保護設定按鈕

c_title, c_btn = st.columns([8, 2], vertical_alignment="center")
with c_title:
    st.markdown(f'<div class="app-title">{user_info["name"]} 面板</div>', unsafe_allow_html=True)
with c_btn:
    with st.popover("⚙️ 設定"):
        st.markdown("<div style='font-weight:600; color:#fff; margin-bottom:10px;'>介面設定</div>", unsafe_allow_html=True)
        st.session_state.refresh_rate = st.selectbox("自動刷新頻率", options=[0, 30, 60, 120, 300], format_func=lambda x: {0:"停用", 30:"30秒", 60:"1分鐘", 120:"2分鐘", 300:"5分鐘"}[x], index=[0, 30, 60, 120, 300].index(st.session_state.refresh_rate))
        
        if user_info["role"] == "staking":
            st.markdown("<hr style='margin: 10px 0; border-color: #2b3139;'>", unsafe_allow_html=True)
            st.markdown("<div style='font-weight:600; color:#fff; margin-bottom:10px;'>API 與策略設定</div>", unsafe_allow_html=True)
            
            saved_settings = user_data.get('settings', {})
            new_apy = st.number_input("預期 APY (%)", value=float(saved_settings.get('apy', 15.0)), step=0.5, format="%.2f")
            new_key = st.text_input("Bitfinex API Key", value=saved_settings.get('api_key', ''), type="password")
            new_secret = st.text_input("Bitfinex API Secret", value=saved_settings.get('api_secret', ''), type="password")
            
            if st.button("更新策略與金鑰", use_container_width=True, type="primary"):
                with st.spinner("正在安全寫入..."):
                    asyncio.run(update_user_settings(user_info["db_id"], {
                        "apy": new_apy,
                        "api_key": new_key.strip(),
                        "api_secret": new_secret.strip()
                    }))
                st.success("儲存成功！背景 Worker 將自動套用。")
                st.rerun()

        st.markdown("<hr style='margin: 10px 0; border-color: #2b3139;'>", unsafe_allow_html=True)
        st.markdown("<div style='font-weight:600; color:#fff; margin-bottom:10px;'>安全設定</div>", unsafe_allow_html=True)
        new_pin = st.text_input("設定新密碼 (PIN)", type="password")
        if st.button("修改登入密碼", use_container_width=True):
            if new_pin and len(new_pin) >= 4:
                with st.spinner("更新密碼中..."):
                    asyncio.run(update_user_settings(user_info["db_id"], {"pin": new_pin.strip()}))
                st.success("密碼修改成功！下次登入請使用新密碼。")
            else:
                st.warning("密碼至少需要 4 個字元")

        st.markdown("<hr style='margin: 10px 0; border-color: #2b3139;'>", unsafe_allow_html=True)
        tw_full_time = get_taiwan_time(st.session_state.last_update)
        st.markdown(f"<div style='color:#7a808a; font-size:0.8rem; margin:10px 0;'>背景同步: {tw_full_time}</div>", unsafe_allow_html=True)
        if st.button("強制刷新畫面", use_container_width=True): st.rerun()
        if st.button("登出系統", use_container_width=True): 
            st.session_state.logged_in_user = None
            st.rerun()

# ----------------- 模組 A：量解放貸面板 (Ming Yu) -----------------
@st.fragment(run_every=timedelta(seconds=st.session_state.refresh_rate) if st.session_state.refresh_rate > 0 else None)
def lending_dashboard_fragment():
    data, decisions, equity_history = asyncio.run(fetch_all_data_lending())
    if not data: return
        
    tw_full_time = get_taiwan_time(st.session_state.last_update)
    tw_short_time = tw_full_time.split(' ')[1][:5] if ' ' in tw_full_time else ""
    
    auto_p_display = f"${data.get('auto_p', 0):,.0f}" if data.get('auto_p', 0) > 0 else "$0"
    
    st.markdown(f"""
    <div class="okx-panel">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <div class="okx-label" style="margin: 0;">聯合淨資產 (USD/USDT)</div>
            <div style="color:#b2ff22; font-size:0.75rem; font-weight:600; display:flex; align-items:center;">
                <span style="display:inline-block; width:6px; height:6px; background-color:#b2ff22; border-radius:50%; margin-right:4px;"></span>Live {tw_short_time}
            </div>
        </div>
        <div style="display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; margin-bottom: 16px;">
            <div class="pulse-text okx-value-mono" style="font-size:2.4rem; font-weight:700; color:#ffffff; line-height:1;">${data.get("total", 0):,.2f}</div>
            <div style="font-size:0.9rem; color:#7a808a; font-weight:500; font-family:'Inter'; white-space:nowrap;">≈ {int(data.get("total", 0)*data.get("fx", 32)):,} TWD</div>
        </div>
        <div class="stats-3-col">
            <div><div class="okx-label" style="white-space:nowrap;">投入本金</div><div class="okx-value-mono" style="font-size:1.05rem; color:#fff;">{auto_p_display}</div></div>
            <div><div class="okx-label" style="white-space:nowrap;">今日收益</div><div class="text-green okx-value-mono" style="font-size:1.05rem;">+${data.get("today_profit", 0):.2f}</div></div>
            <div><div class="okx-label" style="white-space:nowrap;">累計收益</div><div class="text-green okx-value-mono" style="font-size:1.05rem;">+${data.get("history", 0):,.2f}</div></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    next_repay_str = format_time_smart(data.get('next_repayment_time', 9999999))
    st.markdown(f"""
    <div class="stats-2-col">
        <div class="status-card"><div class="okx-label">資金使用率</div><div class="okx-value-mono {"text-red" if data.get('idle_pct', 0) > 5 else "text-green"}" style="font-size:1.3rem;">{100 - data.get("idle_pct", 0):.1f}%</div></div>
        <div class="status-card"><div class="okx-label okx-tooltip" data-tip="目前所有借出資金的加權淨年化">當前淨年化 <i>i</i></div><div class="okx-value-mono" style="font-size:1.3rem; color:#fff;">{data.get("active_apr", 0):.2f}%</div></div>
        <div class="status-card"><div class="okx-label">預計利息收入</div><div class="text-green okx-value-mono" style="font-size:1.3rem;">+${data.get("next_payout_total", 0):.2f}</div></div>
        <div class="status-card"><div class="okx-label" style="white-space:nowrap;">最近解鎖時間</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">{next_repay_str}</div></div>
    </div>
    """, unsafe_allow_html=True)

    tab_main, tab_loans, tab_offers, tab_analytics = st.tabs(["總覽", "借出", "掛單", "分析"])

    with tab_main:
        if equity_history:
            df_eq = pd.DataFrame(equity_history)
            df_eq['日期'] = pd.to_datetime(df_eq['record_date'])
            df_eq = df_eq.sort_values('日期')
            df_eq['Month'] = df_eq['日期'].dt.strftime('%Y-%m')
            
            monthly_cum = df_eq.groupby('Month')['hist_p'].last()
            monthly_profit = monthly_cum.diff().fillna(monthly_cum)
            available_months = list(monthly_profit.index)[::-1] 
            
            st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:10px 0 10px 0;'>月度收益報告</div>", unsafe_allow_html=True)
            selected_month = st.selectbox("切換月份", available_months, label_visibility="collapsed")
            
            if selected_month:
                sel_profit = monthly_profit[selected_month]
                p_color = "#b2ff22" if sel_profit >= 0 else "#ff4d4f"
                p_sign = "+" if sel_profit >= 0 else ""
                st.markdown(f"""<div style='background: #0c0e12; border: 1px solid #1a1d24; border-radius: 12px; padding: 24px 20px; text-align: center; margin-bottom: 24px;'><div style='color: #7a808a; font-size: 0.9rem; margin-bottom: 8px; font-weight: 500;'>結算月份：{selected_month}</div><div style='color: {p_color}; font-size: 2.5rem; font-weight: 700; font-family: "JetBrains Mono", monospace; letter-spacing: -1px;'>{p_sign}${sel_profit:.2f}</div></div>""", unsafe_allow_html=True)
        else:
            st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:10px 0 10px 0;'>月度收益報告</div>", unsafe_allow_html=True)
            st.markdown("<div class='okx-panel-outline' style='text-align:center; color:#7a808a;'>累積數據中...</div>", unsafe_allow_html=True)

        current_apy = data.get('stats', {}).get('overall', {}).get('true_apy', 0)
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 10px 0;'>標竿對比</div>", unsafe_allow_html=True)
        etf_data = [{"name": "本策略 (真實)", "rate": current_apy, "is_base": True}, {"name": "0056", "rate": 7.50}, {"name": "00878", "rate": 7.00}, {"name": "00713", "rate": 8.00}]
        max_rate = max([item["rate"] for item in etf_data])

        grid_html = "<div class='etf-grid'>"
        for item in etf_data:
            is_winner = (item["rate"] == max_rate)
            card_class = "etf-card etf-card-active" if is_winner else "etf-card"
            sub_txt = "策略基準" if item.get("is_base") else (f"+{current_apy - item['rate']:.2f}%" if current_apy >= item['rate'] else f"{current_apy - item['rate']:.2f}%")
            sub_style = "color:#7a808a;" if item.get("is_base") else ("color:#b2ff22;" if current_apy >= item['rate'] else "color:#ff4d4f;")
            grid_html += f"<div class='{card_class}'><div class='etf-title'>{item['name']}</div><div class='etf-rate okx-value-mono'>{item['rate']:.2f}%</div><div style='font-size:0.75rem; margin-top:6px; font-weight:600; font-family: \"JetBrains Mono\"; {sub_style}'>{sub_txt}</div></div>"
        grid_html += "</div>"
        st.markdown(grid_html, unsafe_allow_html=True)

        # === ✅ 主理人專屬：綜合績效指標 ===
        o_stat = data.get('stats', {}).get('overall', {})
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 10px 0;'>綜合績效指標</div>", unsafe_allow_html=True)
        if o_stat.get("is_empty"): 
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a;'>數據收集載入中...</div>", unsafe_allow_html=True)
        else:
            wait_str = format_time_smart(o_stat.get('wait', 0) * 3600)
            surv_str = format_time_smart(o_stat.get('survive', 0) * 3600)
            st.markdown(f"""<div class='okx-panel' style='padding: 16px;'><div class='okx-list-item border-bottom'><div class='okx-list-label okx-tooltip' data-tip="精準扣除所有閒置成本與手續費後的真實獲利能力">真實等效年化 (True APY) <i>i</i></div><div class='okx-list-value text-green okx-value-mono' style='font-size:1.2rem;'>{o_stat.get('true_apy', 0):.2f}%</div></div><div class='okx-list-item border-bottom'><div class='okx-list-label'>平均毛年化</div><div class='okx-list-value okx-value-mono'>{o_stat.get('gross_rate', 0):.2f}%</div></div><div class='okx-list-item border-bottom'><div class='okx-list-label okx-tooltip' data-tip="資金從回到錢包到下次成功借出的平均等待時間">平均撮合耗時 <i>i</i></div><div class='okx-list-value'>{wait_str}</div></div><div class='okx-list-item'><div class='okx-list-label okx-tooltip' data-tip="合約成功放貸並持續計息的平均壽命">平均存活時間 <i>i</i></div><div class='okx-list-value'>{surv_str}</div></div></div>""", unsafe_allow_html=True)

    with tab_loans:
        loans_data = data.get('loans', [])
        if not loans_data:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>目前無活躍借出合約</div>", unsafe_allow_html=True)
        else:
            total_loan_amt = sum(l.get('金額', l.get('金額 (USD)', 0)) for l in loans_data)
            total_daily_profit = sum(l.get('預估日收', 0) for l in loans_data)
            st.markdown(f"""<div class="stats-2-col" style="margin-top:10px;"><div class="status-card"><div class="okx-label">總借出金額</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">${total_loan_amt:,.0f}</div></div><div class="status-card"><div class="okx-label">活躍合約數</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">{len(loans_data)} <span style="font-size:0.8rem; color:#7a808a; font-family:'Inter';">筆</span></div></div><div class="status-card"><div class="okx-label">加權年化</div><div class="text-green okx-value-mono" style="font-size:1.2rem;">{data.get("active_apr", 0):.2f}%</div></div><div class="status-card"><div class="okx-label">預估總日收</div><div class="text-green okx-value-mono" style="font-size:1.2rem;">${total_daily_profit:.2f}</div></div></div>""", unsafe_allow_html=True)
            
            cards_html = "<div class='okx-card-grid'>"
            for l in loans_data:
                cards_html += f"<div class='okx-item-card'><div class='okx-card-header'><span class='okx-tag tag-gray'>活躍</span><span class='okx-card-amt'>${l.get('金額', l.get('金額 (USD)', 0)):,.2f}</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>淨年化</span><span class='okx-list-value text-green okx-value-mono'>{l.get('年化 (%)', 0):.2f}%</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>預估日收</span><span class='okx-list-value okx-value-mono'>${l.get('預估日收', 0):.2f}</span></div><div class='okx-list-item'><span class='okx-list-label'>到期時間</span><span class='okx-list-value' style='color:#848e9c; font-weight:400;'>{l.get('到期時間', '')}</span></div></div>"
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

    with tab_offers:
        offers_data = data.get('offers', [])
        if not offers_data:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>目前無排隊中掛單</div>", unsafe_allow_html=True)
        else:
            total_offer_amt = sum(o.get('金額', o.get('金額 (USD)', 0)) for o in offers_data)
            stuck_count = data.get('stuck_offers_count', 0)
            st.markdown(f"""<div class="stats-3-col" style="margin-top:10px;"><div class="status-card"><div class="okx-label" style="white-space:nowrap;">總排隊金額</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">${total_offer_amt:,.0f}</div></div><div class="status-card"><div class="okx-label" style="white-space:nowrap;">排隊掛單數</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">{len(offers_data)} <span style="font-size:0.8rem; color:#7a808a; font-family:'Inter';">筆</span></div></div><div class="status-card"><div class="okx-label okx-tooltip" data-tip="等待時間超過系統容忍上限，建議手動降價">匹配滯緩 <i>i</i></div><div class="{'text-red' if stuck_count > 0 else 'text-green'} okx-value-mono" style="font-size:1.2rem;">{stuck_count} <span style="font-size:0.8rem; color:#7a808a; font-family:'Inter';">筆</span></div></div></div>""", unsafe_allow_html=True)

            cards_html = "<div class='okx-card-grid'>"
            for o in offers_data:
                status_raw = o.get('狀態', '')
                short_status = "滯緩" if "卡單" in status_raw else ("展期" if "換倉" in status_raw else "撮合")
                tag_class = "tag-red" if "卡單" in status_raw else ("tag-gray" if "換倉" in status_raw else "tag-yellow")
                wait_time = parse_wait_time(o.get('排隊時間', ''))
                cards_html += f"<div class='okx-item-card'><div class='okx-card-header'><span class='okx-tag {tag_class}'>{short_status}</span><span class='okx-card-amt'>${o.get('金額', o.get('金額 (USD)', 0)):,.2f}</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>報價 (年化)</span><span class='okx-list-value okx-value-mono'>{o.get('毛年化', '')}</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>合約天期</span><span class='okx-list-value'>{o.get('掛單天期', '')}</span></div><div class='okx-list-item'><span class='okx-list-label'>排隊時長</span><span class='okx-list-value' style='color:#848e9c; font-weight:400;'>{wait_time}</span></div></div>"
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

    with tab_analytics:
        # === ✅ 主理人專屬：市場結構與壓力 VWAP ===
        is_spoofed = (data.get('market_frr', 0) - data.get('market_twap', 0)) > 3.0
        spoof_class = "text-red" if is_spoofed else "text-green"
        spoof_text = "溢價過高" if is_spoofed else "結構健康"
        
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:10px 0 12px 0;'>大盤監控</div>", unsafe_allow_html=True)
        st.markdown(f"""<div class="stats-2-col" style="margin-bottom: 24px;"><div class="status-card"><div class="okx-label">市場結構</div><div class="okx-value {spoof_class}" style="font-size:1.1rem;">{spoof_text}</div></div><div class="status-card"><div class="okx-label okx-tooltip" data-tip="官方顯示的表面基準利率">表面 FRR <i>i</i></div><div class="okx-value okx-value-mono" style="font-size:1.1rem; color:#fff;">{data.get('market_frr', 0):.2f}%</div></div><div class="status-card"><div class="okx-label okx-tooltip" data-tip="過去 3 小時真實成交加權均價">真實 TWAP <i>i</i></div><div class="okx-value okx-value-mono" style="font-size:1.1rem; color:#0ea5e9;">{data.get('market_twap', 0):.2f}%</div></div><div class="status-card"><div class="okx-label okx-tooltip" data-tip="當前訂單簿吃下 50 萬美金的均價">壓力 VWAP <i>i</i></div><div class="okx-value okx-value-mono" style="font-size:1.1rem; color:#fcd535;">{data.get('market_vwap', 0):.2f}%</div></div></div>""", unsafe_allow_html=True)
        
        # === ✅ 主理人專屬：AI 大腦診斷 ===
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:10px 0 12px 0;'>系統大腦診斷</div>", unsafe_allow_html=True)
        if st.button("執行最新 AI 診斷 (Groq)", use_container_width=True):
            with st.spinner("Groq 正在極速解析大盤數據..."):
                if not GROQ_API_KEY:
                    st.session_state.ai_insight_result = "⚠️ 尚未設定 GROQ_API_KEY。請至 Streamlit Cloud > Settings > Secrets 貼上金鑰。"
                else:
                    try:
                        import groq
                        client = groq.Groq(api_key=GROQ_API_KEY)
                        prompt = f"""
                        你是一位專業的量化放貸分析師。請根據以下最新市場數據給出 50 字以內的精簡策略建議：
                        - 當前表面 FRR: {data.get('market_frr', 0)}%
                        - 真實成交均價 TWAP: {data.get('market_twap', 0)}%
                        - 資金閒置率: {data.get('idle_pct', 0)}%
                        - 目前我的加權淨年化: {data.get('active_apr', 0)}%
                        """
                        response = client.chat.completions.create(
                            model="llama-3.3-70b-versatile",
                            messages=[
                                {"role": "system", "content": "你是一個冷靜、專業的交易員大腦，請勿使用 Emoji。"},
                                {"role": "user", "content": prompt}
                            ],
                            max_tokens=150
                        )
                        st.session_state.ai_insight_result = response.choices[0].message.content.strip()
                    except Exception as e:
                        st.session_state.ai_insight_result = f"Groq API 呼叫失敗: {str(e)}"

        if st.session_state.ai_insight_result:
            st.markdown(f"""<div class="okx-panel" style="padding:16px; margin-bottom:24px; border-color: #3b4048;"><div style="color: #ffffff; font-weight: 600; font-size: 0.9rem; margin-bottom: 8px;">即時診斷報告 (Powered by Groq)</div><div style="color: #848e9c; font-size: 0.9rem; line-height: 1.6; font-weight:400;">{st.session_state.ai_insight_result}</div></div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""<div class="okx-panel-outline" style="padding:16px; margin-bottom:24px; text-align:center;"><div style="color: #50555e; font-size: 0.85rem; font-weight:500;">點擊上方按鈕，呼叫 Groq 執行深度分析</div></div>""", unsafe_allow_html=True)

        if not decisions:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>資料庫樣本收集載入中...</div>", unsafe_allow_html=True)
        else:
            df = pd.DataFrame(decisions)
            df['時間'] = pd.to_datetime(df.get('created_at', pd.Series(range(len(df))))).dt.tz_convert('Asia/Taipei') if 'created_at' in df.columns else pd.Series(range(len(df)))

            if 'market_frr' in df.columns and 'bot_rate_yearly' in df.columns:
                df['market_twap'] = df.get('market_twap', df['market_frr']).fillna(df['market_frr'])
                win_rate_twap = (len(df[df['bot_rate_yearly'] >= df['market_twap']]) / len(df)) * 100 if len(df) > 0 else 0
                avg_spread_twap = (df['bot_rate_yearly'] - df['market_twap']).mean()
                
                # === ✅ 主理人專屬：策略對標與操作日誌 ===
                st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 12px 0;'>策略對標分析</div>", unsafe_allow_html=True)
                st.markdown(f"""<div class="stats-2-col" style="margin-bottom: 20px;"><div class="status-card"><div class="okx-label okx-tooltip" data-tip="報價成功超越真實成交基準的比例">勝率 (對標 TWAP) <i>i</i></div><div class="text-green okx-value-mono" style="font-size:1.2rem;">{win_rate_twap:.1f}%</div></div><div class="status-card"><div class="okx-label okx-tooltip" data-tip="機器人比市場平均多賺取的溢價">真 Alpha 報酬 <i>i</i></div><div class="{'text-green' if avg_spread_twap >=0 else 'text-red'} okx-value-mono" style="font-size:1.2rem;">{avg_spread_twap:+.2f}%</div></div></div>""", unsafe_allow_html=True)
                
                st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:30px 0 12px 0;'>側錄操作日誌</div>", unsafe_allow_html=True)
                cards_html = "<div class='okx-card-grid'>"
                for _, row in df.head(10).iterrows():
                    spread_twap = row.get('bot_rate_yearly', 0) - row.get('market_twap', 0)
                    tag_class = "tag-green" if spread_twap >= 0 else "tag-gray"
                    cards_html += f"<div class='okx-item-card'><div class='okx-card-header'><span class='okx-tag {tag_class}'>Alpha {spread_twap:+.2f}%</span><span class='okx-card-amt'>${row.get('bot_amount', 0):,.0f}</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>報價</span><span class='okx-list-value okx-value-mono text-green'>{row.get('bot_rate_yearly', 0):.2f}%</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>TWAP</span><span class='okx-list-value okx-value-mono' style='color:#0ea5e9;'>{row.get('market_twap', 0):.2f}%</span></div><div class='okx-list-item'><span class='okx-list-label'>時間</span><span class='okx-list-value' style='color:#848e9c; font-weight:400;'>{row['時間'].strftime('%m/%d %H:%M') if isinstance(row['時間'], pd.Timestamp) else ''}</span></div></div>"
                cards_html += "</div>"
                st.markdown(cards_html, unsafe_allow_html=True)

# ----------------- 模組 B：DOT 淨本金面板 (Friend) -----------------
@st.fragment(run_every=timedelta(seconds=st.session_state.refresh_rate) if st.session_state.refresh_rate > 0 else None)
def staking_dashboard_fragment():
    global user_data
    if not user_data: 
        st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>請先至上方「⚙️ 設定」輸入您的 API 金鑰，等待 Worker 同步資料...</div>", unsafe_allow_html=True)
        return
        
    tw_full_time = get_taiwan_time(st.session_state.last_update)
    tw_short_time = tw_full_time.split(' ')[1][:5] if ' ' in tw_full_time else ""
    
    settings = user_data.get("settings", {})
    user_apy = float(settings.get("apy", 15.0))

    dot_balance = user_data.get("dot_balance", 0.0)
    dot_price = user_data.get("dot_price_usd", 0.0)
    usd_twd_fx = user_data.get("fx", 32.0)
    
    total_rewards_dot = user_data.get("total_rewards_dot", 0.0)
    principal_dot = user_data.get("principal_dot", 0.0)
    
    total_usd = dot_balance * dot_price
    total_twd = total_usd * usd_twd_fx
    
    rewards_usd = total_rewards_dot * dot_price
    rewards_twd = rewards_usd * usd_twd_fx

    daily_reward_dot = dot_balance * (user_apy / 100) / 365
    daily_reward_usd = daily_reward_dot * dot_price
    daily_reward_twd = daily_reward_usd * usd_twd_fx

    if dot_balance == 0 and not settings.get('api_key'):
        st.info("💡 提示：點擊右上方「⚙️ 設定」，輸入您的 Bitfinex API 金鑰即可自動讀取 DOT 放貸與質押庫存。")
        return

    st.markdown(f"""
    <div class="okx-panel">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <div class="okx-label" style="margin: 0;">目前帳戶總資產 (DOT)</div>
            <div style="color:#e6007a; font-size:0.75rem; font-weight:600; display:flex; align-items:center;">
                <span style="display:inline-block; width:6px; height:6px; background-color:#e6007a; border-radius:50%; margin-right:4px;"></span>即時匯率 {tw_short_time}
            </div>
        </div>
        <div style="display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; margin-bottom: 16px;">
            <div class="pulse-text okx-value-mono" style="font-size:2.4rem; font-weight:700; color:#ffffff; line-height:1;">{dot_balance:,.2f}</div>
            <div style="font-size:0.9rem; color:#7a808a; font-weight:500; font-family:'Inter'; white-space:nowrap;">≈ ${total_usd:,.2f} USD</div>
        </div>
        <div class="stats-3-col" style="margin-top:0;">
            <div><div class="okx-label" style="white-space:nowrap;">淨投入本金</div><div class="okx-value-mono" style="font-size:1.05rem; color:#fff;">{principal_dot:,.2f} <span style="font-size:0.75rem; color:#7a808a;">DOT</span></div></div>
            <div><div class="okx-label" style="white-space:nowrap;">累計利息</div><div class="text-green okx-value-mono" style="font-size:1.05rem;">+{total_rewards_dot:,.4f} <span style="font-size:0.75rem; color:#7a808a;">DOT</span></div></div>
            <div><div class="okx-label" style="white-space:nowrap;">總等值台幣</div><div class="okx-value-mono" style="font-size:1.05rem; color:#fff;">≈ {int(total_twd):,}</div></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 10px 0;'>質押績效與匯率</div>", unsafe_allow_html=True)
    st.markdown(f"""
    <div class="stats-2-col">
        <div class="status-card"><div class="okx-label">設定預期 APY</div><div class="okx-value-mono text-green" style="font-size:1.4rem;">{user_apy:.1f}%</div></div>
        <div class="status-card"><div class="okx-label okx-tooltip" data-tip="基於設定 APY 計算">預估日收 (DOT) <i>i</i></div><div class="okx-value-mono text-green" style="font-size:1.4rem;">+{daily_reward_dot:.4f}</div></div>
        <div class="status-card"><div class="okx-label">DOT / USD</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">${dot_price:.4f}</div></div>
        <div class="status-card"><div class="okx-label">USD / TWD</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">{usd_twd_fx:.2f}</div></div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 10px 0;'>累積質押獎勵結算</div>", unsafe_allow_html=True)
    st.markdown(f"""
    <div class='okx-panel' style='padding: 16px;'>
        <div class='okx-list-item border-bottom'>
            <div class='okx-list-label'>純利息獲得 (DOT)</div>
            <div class='okx-list-value text-green okx-value-mono' style='font-size:1.3rem;'>+{total_rewards_dot:,.4f}</div>
        </div>
        <div class='okx-list-item border-bottom'>
            <div class='okx-list-label'>換算美金 (USD)</div>
            <div class='okx-list-value okx-value-mono'>+${rewards_usd:,.2f}</div>
        </div>
        <div class='okx-list-item'>
            <div class='okx-list-label'>換算台幣 (TWD)</div>
            <div class='okx-list-value okx-value-mono'>+ NT$ {int(rewards_twd):,}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# 路由判斷
if user_info["role"] == "lending":
    lending_dashboard_fragment()
elif user_info["role"] == "staking":
    staking_dashboard_fragment()