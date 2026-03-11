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

if 'refresh_rate' not in st.session_state: st.session_state.refresh_rate = 300
if 'last_update' not in st.session_state: st.session_state.last_update = "尚未同步"
if 'logged_in_user' not in st.session_state: st.session_state.logged_in_user = None

# ================= 2. 視覺風格定義與 JS 腳本注入 =================
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
    
    function setupTabAutoScroll() {
        const parentDoc = window.parent.document;
        parentDoc.addEventListener('click', function(e) {
            let target = e.target;
            while (target && target !== parentDoc) {
                if (target.getAttribute && target.getAttribute('role') === 'tab') {
                    setTimeout(() => {
                        let scrollArea = parentDoc.querySelector('.main') || parentDoc.querySelector('[data-testid="stAppViewContainer"]') || parentDoc.documentElement;
                        if(scrollArea) {
                            scrollArea.scrollTo({top: 0, behavior: 'auto'});
                        }
                    }, 50);
                    break;
                }
                target = target.parentNode;
            }
        });
    }
    try { setupTabAutoScroll(); } catch(e) {}
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

async def fetch_equity_history(session) -> list:
    if not SUPABASE_URL: return []
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with session.get(f"{SUPABASE_URL}/rest/v1/bfx_nav?select=record_date,auto_p,hist_p&order=record_date.asc", headers=headers, timeout=5) as res:
            if res.status == 200: return await res.json()
    except Exception: pass
    return []

async def fetch_bot_decisions(session) -> list:
    if not SUPABASE_URL: return []
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with session.get(f"{SUPABASE_URL}/rest/v1/bot_decisions?select=created_at,bot_rate_yearly,market_frr,market_twap,bot_amount,bot_period&order=created_at.desc&limit=300", headers=headers, timeout=5) as res:
            if res.status == 200: return await res.json()
    except Exception: pass
    return []

async def fetch_all_data_lending():
    async with aiohttp.ClientSession() as session:
        return await asyncio.gather(fetch_cached_data(session, 1), fetch_equity_history(session), fetch_bot_decisions(session))

async def fetch_all_data_staking():
    async with aiohttp.ClientSession() as session:
        return await fetch_cached_data(session, 2)

def format_time_smart(seconds):
    if not seconds or seconds >= 9999999: return "--"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h >= 24: return f"{h // 24}D {h % 24}H"
    return f"{h}H {m}M"

def parse_wait_time(time_str):
    if "h" in time_str and "m" in time_str:
        parts = time_str.split("h")
        try:
            h = int(parts[0].strip())
            m = parts[1].replace("m","").strip()
            if h >= 24: return f"{h // 24}D {h % 24}H"
        except: pass
    return time_str

def get_taiwan_time(utc_iso_str):
    if not utc_iso_str or utc_iso_str == "尚未同步": return "尚未同步"
    try:
        dt = pd.to_datetime(utc_iso_str)
        if dt.tz is None: dt = dt.tz_localize('UTC')
        tw_dt = dt.tz_convert('Asia/Taipei')
        return tw_dt.strftime('%m/%d %H:%M')
    except:
        return str(utc_iso_str).replace("T", " ")[:16]

# ================= 4. 動態登入介面 =================
if not SUPABASE_URL:
    st.error("系統配置錯誤：缺少 SUPABASE_URL")
    st.stop()

USERS = asyncio.run(fetch_all_auth_data())

query_user = st.query_params.get("user")
query_pin = st.query_params.get("pin")

if st.session_state.logged_in_user is None:
    if query_user in USERS and USERS[query_user]["pin"] == query_pin:
        st.session_state.logged_in_user = query_user
        st.rerun()

if st.session_state.logged_in_user is None:
    st.columns(1) 
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
                    st.query_params["user"] = selected_user
                    st.query_params["pin"] = pin_input
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
st.columns(1) 

c_title, c_btn = st.columns([7, 3], vertical_alignment="center")
with c_title:
    st.markdown(f'<div class="app-title">{user_info["name"]} 控制面板</div>', unsafe_allow_html=True)
with c_btn:
    with st.popover("設定"):
        st.markdown("<div style='font-weight:600; color:#fff; margin-bottom:10px;'>系統參數</div>", unsafe_allow_html=True)
        st.session_state.refresh_rate = st.selectbox("刷新頻率", options=[0, 30, 60, 120, 300], format_func=lambda x: {0:"停用", 30:"30秒", 60:"1分鐘", 120:"2分鐘", 300:"5分鐘"}[x], index=[0, 30, 60, 120, 300].index(st.session_state.refresh_rate))
        
        st.info("提示：目前網址已包含驗證參數，建議加入書籤以利免密碼登入。")
        
        if user_info["role"] == "staking":
            st.markdown("<hr style='margin: 10px 0; border-color: #2b3139;'>", unsafe_allow_html=True)
            st.markdown("<div style='font-weight:600; color:#fff; margin-bottom:10px;'>API 與策略參數</div>", unsafe_allow_html=True)
            
            saved_settings = user_data.get('settings', {})
            new_apy = st.number_input("預期 APY (%)", value=float(saved_settings.get('apy', 15.0)), step=0.5, format="%.2f")
            new_key = st.text_input("API Key", value=saved_settings.get('api_key', ''), type="password")
            new_secret = st.text_input("API Secret", value=saved_settings.get('api_secret', ''), type="password")
            
            if st.button("寫入設定", use_container_width=True, type="primary"):
                with st.spinner("同步至資料庫..."):
                    asyncio.run(update_user_settings(user_info["db_id"], {
                        "apy": new_apy,
                        "api_key": new_key.strip(),
                        "api_secret": new_secret.strip()
                    }))
                st.success("參數已更新，下次回圈生效。")
                st.rerun()

        st.markdown("<hr style='margin: 10px 0; border-color: #2b3139;'>", unsafe_allow_html=True)
        st.markdown("<div style='font-weight:600; color:#fff; margin-bottom:10px;'>安全認證</div>", unsafe_allow_html=True)
        new_pin = st.text_input("設定新密碼 (PIN)", type="password")
        if st.button("更新密碼", use_container_width=True):
            if new_pin and len(new_pin) >= 4:
                with st.spinner("執行中..."):
                    asyncio.run(update_user_settings(user_info["db_id"], {"pin": new_pin.strip()}))
                st.query_params["pin"] = new_pin.strip()
                st.success("密碼已重置。")
            else:
                st.warning("密碼長度需至少 4 個字元。")

        st.markdown("<hr style='margin: 10px 0; border-color: #2b3139;'>", unsafe_allow_html=True)
        tw_full_time = get_taiwan_time(st.session_state.last_update)
        st.markdown(f"<div style='color:#7a808a; font-size:0.8rem; margin:10px 0;'>資料戳記: {tw_full_time}</div>", unsafe_allow_html=True)
        if st.button("強制刷新", use_container_width=True): st.rerun()
        if st.button("登出", use_container_width=True): 
            st.session_state.logged_in_user = None
            st.query_params.clear()
            st.rerun()

# ----------------- 模組 A：量解放貸面板 -----------------
@st.fragment(run_every=timedelta(seconds=st.session_state.refresh_rate) if st.session_state.refresh_rate > 0 else None)
def lending_dashboard_fragment():
    data, equity_history, bot_decisions = asyncio.run(fetch_all_data_lending())
    if not data: return
        
    tw_full_time = get_taiwan_time(st.session_state.last_update)
    tw_short_time = tw_full_time.split(' ')[1] if ' ' in tw_full_time else ""
    
    auto_p_display = f"${data.get('auto_p', 0):,.0f}" if data.get('auto_p', 0) > 0 else "$0"
    
    # === 頂部資產總覽 ===
    st.markdown(f"""
    <div class="okx-panel">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <div class="okx-label" style="margin: 0;">聯合淨資產 (USD)</div>
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
            <div><div class="okx-label" style="white-space:nowrap;">當日預估收益</div><div class="text-green okx-value-mono" style="font-size:1.05rem;">+${data.get("today_profit", 0):.2f}</div></div>
            <div><div class="okx-label" style="white-space:nowrap;">累計收益</div><div class="text-green okx-value-mono" style="font-size:1.05rem;">+${data.get("history", 0):,.2f}</div></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    next_repay_str = format_time_smart(data.get('next_repayment_time', 9999999))
    active_apr = data.get("active_apr", 0)
    market_twap = data.get("market_twap", 0)
    alpha_premium = active_apr - market_twap
    alpha_color = "text-green" if alpha_premium >= 0 else "text-red"
    alpha_sign = "+" if alpha_premium >= 0 else ""

    st.markdown(f"""
    <div class="stats-2-col">
        <div class="status-card"><div class="okx-label">資金使用率</div><div class="okx-value-mono {"text-red" if data.get('idle_pct', 0) > 5 else "text-green"}" style="font-size:1.3rem;">{100 - data.get("idle_pct", 0):.1f}%</div></div>
        <div class="status-card"><div class="okx-label okx-tooltip" data-tip="當前淨年化超越真實成交均價的幅度">Alpha 溢價 <i>i</i></div><div class="okx-value-mono {alpha_color}" style="font-size:1.3rem;">{alpha_sign}{alpha_premium:.2f}%</div></div>
        <div class="status-card"><div class="okx-label">待結算利息</div><div class="text-green okx-value-mono" style="font-size:1.3rem;">+${data.get("next_payout_total", 0):.2f}</div></div>
        <div class="status-card"><div class="okx-label" style="white-space:nowrap;">流動性預估時間</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">{next_repay_str}</div></div>
    </div>
    """, unsafe_allow_html=True)

    # === 主導航架構 ===
    tab_main, tab_manage, tab_radar, tab_spy = st.tabs(["總覽", "部位管理", "市場深度", "決策模型"])

    with tab_main:
        if equity_history:
            df_eq = pd.DataFrame(equity_history)
            df_eq['日期'] = pd.to_datetime(df_eq['record_date'])
            df_eq = df_eq.sort_values('日期')
            df_eq['Month'] = df_eq['日期'].dt.strftime('%Y-%m')
            
            monthly_cum = df_eq.groupby('Month')['hist_p'].last()
            monthly_profit = monthly_cum.diff().fillna(monthly_cum)
            available_months = list(monthly_profit.index)[::-1] 
            
            st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:10px 0 10px 0;'>月度結算報告</div>", unsafe_allow_html=True)
            selected_month = st.selectbox("選擇月份", available_months, label_visibility="collapsed")
            
            if selected_month:
                sel_profit = monthly_profit[selected_month]
                p_color = "#b2ff22" if sel_profit >= 0 else "#ff4d4f"
                p_sign = "+" if sel_profit >= 0 else ""
                st.markdown(f"""<div style='background: #0c0e12; border: 1px solid #1a1d24; border-radius: 12px; padding: 24px 20px; text-align: center; margin-bottom: 24px;'><div style='color: #7a808a; font-size: 0.9rem; margin-bottom: 8px; font-weight: 500;'>結算月份：{selected_month}</div><div style='color: {p_color}; font-size: 2.5rem; font-weight: 700; font-family: "JetBrains Mono", monospace; letter-spacing: -1px;'>{p_sign}${sel_profit:.2f}</div></div>""", unsafe_allow_html=True)
        else:
            st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:10px 0 10px 0;'>月度結算報告</div>", unsafe_allow_html=True)
            st.markdown("<div class='okx-panel-outline' style='text-align:center; color:#7a808a;'>歷史數據不足</div>", unsafe_allow_html=True)

        account_apy = data.get('hist_apy', 0)
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 10px 0;'>績效基準對比 (Benchmark)</div>", unsafe_allow_html=True)
        
        etf_data = [
            {"name": "系統回測年化", "rate": account_apy, "is_base": True}, 
            {"name": "0056 元大高股息", "rate": 7.50}, 
            {"name": "00878 國泰高息", "rate": 8.00}, 
            {"name": "00713 元大高息低波", "rate": 7.80}
        ]
        max_rate = max([item["rate"] for item in etf_data])

        grid_html = "<div class='etf-grid'>"
        for item in etf_data:
            is_winner = (item["rate"] == max_rate)
            card_class = "etf-card etf-card-active" if is_winner else "etf-card"
            sub_txt = "策略基準" if item.get("is_base") else (f"+{account_apy - item['rate']:.2f}%" if account_apy >= item['rate'] else f"{account_apy - item['rate']:.2f}%")
            sub_style = "color:#7a808a;" if item.get("is_base") else ("color:#b2ff22;" if account_apy >= item['rate'] else "color:#ff4d4f;")
            grid_html += f"<div class='{card_class}'><div class='etf-title'>{item['name']}</div><div class='etf-rate okx-value-mono'>{item['rate']:.2f}%</div><div style='font-size:0.75rem; margin-top:6px; font-weight:600; font-family: \"JetBrains Mono\"; {sub_style}'>{sub_txt}</div></div>"
        grid_html += "</div>"
        st.markdown(grid_html, unsafe_allow_html=True)

        true_apy = data.get('true_apy', 0)
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 10px 0;'>綜合風控指標</div>", unsafe_allow_html=True)
        st.markdown(f"""
        <div class='okx-panel' style='padding: 16px;'>
            <div class='okx-list-item border-bottom'>
                <div class='okx-list-label okx-tooltip' data-tip="納入閒置資金計算之真實投資回報率">等效年化報酬 (True APY) <i>i</i></div>
                <div class='okx-list-value text-green okx-value-mono' style='font-size:1.2rem;'>{true_apy:.2f}%</div>
            </div>
            <div class='okx-list-item'>
                <div class='okx-list-label okx-tooltip' data-tip="當前已配對部位之平均毛利率">活耀部位毛年化</div>
                <div class='okx-list-value okx-value-mono'>{active_apr:.2f}%</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    with tab_manage:
        manage_view = st.selectbox("維度切換", ["活躍部位", "排隊中", "歷史配對"], label_visibility="collapsed")
        
        if manage_view == "活躍部位":
            loans_data = data.get('loans', [])
            if not loans_data:
                st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>當前無活耀部位</div>", unsafe_allow_html=True)
            else:
                total_loan_amt = sum(l.get('金額', l.get('金額 (USD)', 0)) for l in loans_data)
                total_daily_profit = sum(l.get('預估日收', 0) for l in loans_data)
                st.markdown(f"""<div class="stats-2-col" style="margin-top:4px;"><div class="status-card"><div class="okx-label">鎖定總額</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">${total_loan_amt:,.0f}</div></div><div class="status-card"><div class="okx-label">合約數量</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">{len(loans_data)} <span style="font-size:0.8rem; color:#7a808a; font-family:'Inter';">筆</span></div></div><div class="status-card"><div class="okx-label">加權均率</div><div class="text-green okx-value-mono" style="font-size:1.2rem;">{data.get("active_apr", 0):.2f}%</div></div><div class="status-card"><div class="okx-label">日現金流</div><div class="text-green okx-value-mono" style="font-size:1.2rem;">${total_daily_profit:.2f}</div></div></div>""", unsafe_allow_html=True)
                
                cards_html = "<div class='mini-card-grid'>"
                for l in loans_data:
                    amt = l.get('金額', l.get('金額 (USD)', 0))
                    rate = l.get('年化 (%)', 0)
                    exp = l.get('到期時間', '')
                    cards_html += f"<div class='mini-item-card'><div class='mini-card-header'><span class='okx-tag tag-green-glow'>執行中</span><span class='mini-card-amt'>${amt:,.0f}</span></div><div class='mini-stat-row'><span class='okx-list-label'>淨年化</span><span class='text-green okx-value-mono' style='font-size:0.9rem;'>{rate:.2f}%</span></div><div class='mini-stat-row'><span class='okx-list-label'>結算</span><span style='color:#848e9c; font-size:0.8rem; font-family: \"JetBrains Mono\";'>{exp}</span></div></div>"
                cards_html += "</div>"
                st.markdown(cards_html, unsafe_allow_html=True)

        elif manage_view == "排隊中":
            offers_data = data.get('offers', [])
            if not offers_data:
                st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>訂單簿無排隊資料</div>", unsafe_allow_html=True)
            else:
                total_offer_amt = sum(o.get('金額', o.get('金額 (USD)', 0)) for o in offers_data)
                st.markdown(f"""<div class="stats-2-col" style="margin-top:4px;"><div class="status-card"><div class="okx-label" style="white-space:nowrap;">掛單總額</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">${total_offer_amt:,.0f}</div></div><div class="status-card"><div class="okx-label" style="white-space:nowrap;">掛單數量</div><div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">{len(offers_data)} <span style="font-size:0.8rem; color:#7a808a; font-family:'Inter';">筆</span></div></div></div>""", unsafe_allow_html=True)

                cards_html = "<div class='mini-card-grid'>"
                for o in offers_data:
                    status_raw = o.get('狀態', '')
                    short_status = "展期" if "換倉" in status_raw else "排隊"
                    tag_class = "tag-green" if "換倉" in status_raw else "tag-gray"
                    wait_time = parse_wait_time(o.get('排隊時間', ''))
                    amt = o.get('金額', o.get('金額 (USD)', 0))
                    rate_str = o.get('毛年化', '')
                    cards_html += f"<div class='mini-item-card'><div class='mini-card-header'><span class='okx-tag {tag_class}'>{short_status}</span><span class='mini-card-amt'>${amt:,.0f}</span></div><div class='mini-stat-row'><span class='okx-list-label'>報價</span><span class='okx-value-mono' style='font-size:0.9rem; color:#fff;'>{rate_str}</span></div><div class='mini-stat-row'><span class='okx-list-label'>遲滯</span><span style='color:#848e9c; font-size:0.8rem;'>{wait_time}</span></div></div>"
                cards_html += "</div>"
                st.markdown(cards_html, unsafe_allow_html=True)

        else:
            matched_data = data.get('matched_trades', [])
            if not matched_data:
                st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>系統尚未擷取到歷史配對紀錄</div>", unsafe_allow_html=True)
            else:
                cards_html = "<div class='list-view-container'>"
                for m in matched_data:
                    raw_time = m.get('created_at', m.get('時間', m.get('time', m.get('match_time', '尚未同步'))))
                    display_time = get_taiwan_time(raw_time) if raw_time else '--/-- --:--'
                    
                    rate = str(m.get('利率', m.get('rate', '')))
                    if "%" not in rate and rate.replace('.', '', 1).isdigit(): rate = f"{float(rate):.4f}%"
                    
                    period = m.get('期間', m.get('period', ''))
                    amount = m.get('數量', m.get('amount', 0))

                    cards_html += f"<div class='list-view-item'><div class='list-view-col-left'><div class='list-view-subtext'>{display_time}</div><div class='list-view-maintext text-green okx-value-mono'>{rate}</div></div><div class='list-view-col-right'><div class='list-view-maintext okx-value-mono'>${amount:,.0f}</div><div class='list-view-subtext'>{period} 天</div></div></div>"
                    
                cards_html += "</div>"
                st.markdown(cards_html, unsafe_allow_html=True)

    with tab_radar:
        top_bids = data.get('top_bids', [])
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:10px 0 12px 0;'>買方深度需求</div>", unsafe_allow_html=True)
        
        if not top_bids:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>當前訂單簿無顯著借款需求，等待資料同步...</div>", unsafe_allow_html=True)
        else:
            st.info("提示：當標註「高溢價長單」出現時，代表市場存在機構級流動性需求。可手動跟單獲取最佳執行價格。")
            
            cards_html = "<div class='okx-card-grid'>"
            for b in top_bids:
                rate = b.get('rate', 0)
                period = b.get('period', 0)
                vol = b.get('vol', 0)
                
                is_fat_sheep = rate >= 10.0 and period >= 120
                tag_class = "tag-red" if is_fat_sheep else ("tag-yellow" if rate >= 10.0 else "tag-gray")
                tag_text = "高溢價長單" if is_fat_sheep else ("高利需求" if rate >= 10.0 else "一般需求")
                border_color = "#ff4d4f" if is_fat_sheep else ("#fcd535" if rate >= 10.0 else "#3b4048")
                
                cards_html += f"<div class='okx-item-card' style='border-color: {border_color};'><div class='okx-card-header'><span class='okx-tag {tag_class}'>{tag_text}</span><span class='okx-card-amt'>${vol:,.0f}</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>借款方報價 (APY)</span><span class='okx-list-value okx-value-mono text-green' style='font-size:1.2rem;'>{rate:.2f}%</span></div><div class='okx-list-item'><span class='okx-list-label'>要求存續期</span><span class='okx-list-value okx-value-mono' style='color:#ffffff;'>{period} 天</span></div></div>"
                
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

    with tab_spy:
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:10px 0 12px 0;'>狀態切換與概率預測 (Regime Prediction)</div>", unsafe_allow_html=True)
        
        pred_metrics = data.get("prediction_metrics", {})
        spike_prob = pred_metrics.get("spike_probability_pct", 0.0)
        is_sniper = pred_metrics.get("is_sniper_mode_active", False)
        obi_val = pred_metrics.get("current_obi", 0.0)
        spike_target = pred_metrics.get("suggested_spike_target", 0.0)

        # 讀取準確率與 MAE 計分板
        metrics_data = pred_metrics.get("metrics", {})
        total_alerts = metrics_data.get("total_alerts", 0)
        hits = metrics_data.get("hits", 0)
        misses = metrics_data.get("misses", 0)
        target_error_sum = metrics_data.get("target_error_sum", 0.0)
        
        win_rate = (hits / total_alerts * 100) if total_alerts > 0 else 0.0
        target_mae = (target_error_sum / hits) if hits > 0 else 0.0

        mode_color = "#ff4d4f" if is_sniper else "#b2ff22"
        mode_text = "主動狙擊模式 [ACTIVE]" if is_sniper else "常態追蹤模式 [STANDBY]"
        prob_color = "#ff4d4f" if spike_prob >= 70 else ("#fcd535" if spike_prob >= 40 else "#7a808a")

        st.markdown(f"""
        <div class="okx-panel" style="padding:16px; margin-bottom:24px; border-left: 4px solid {mode_color};">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <div style="color: {mode_color}; font-weight: 700; font-size: 1.1rem;">{mode_text}</div>
            </div>
            <div class="stats-3-col" style="margin-bottom: 0;">
                <div>
                    <div class="okx-label">高利爆發機率</div>
                    <div class="okx-value-mono" style="font-size:1.6rem; color:{prob_color};">{spike_prob:.1f}%</div>
                </div>
                <div>
                    <div class="okx-label">訂單簿失衡度</div>
                    <div class="okx-value-mono" style="font-size:1.2rem; color:#fff;">{obi_val:.3f}</div>
                </div>
                <div>
                    <div class="okx-label">建議狙擊目標</div>
                    <div class="okx-value-mono text-green" style="font-size:1.2rem;">{f'{spike_target:.2f}%' if spike_target > 0 else '--'}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown(f"""
        <div class="okx-panel" style="padding:16px; margin-bottom:24px; border-color: #3b4048;">
            <div style="color: #ffffff; font-weight: 600; font-size: 1.1rem; margin-bottom: 12px;">模型準確率與勝率追蹤 (Model Precision)</div>
            <div class="stats-3-col" style="margin-bottom: 0;">
                <div>
                    <div class="okx-label">總觸發警報</div>
                    <div class="okx-value-mono" style="font-size:1.4rem; color:#fff;">{total_alerts} <span style="font-size:0.8rem; color:#7a808a;">次</span></div>
                </div>
                <div>
                    <div class="okx-label okx-tooltip" data-tip="系統發出警報後15分鐘內，市場的確發生高利成交">成功命中 (True Positive)</div>
                    <div class="okx-value-mono text-green" style="font-size:1.4rem;">{hits} <span style="font-size:0.8rem; color:#7a808a;">次</span></div>
                </div>
                <div>
                    <div class="okx-label okx-tooltip" data-tip="系統發出警報後15分鐘內未發生高利，導致權重受罰">誤判懲罰 (False Positive)</div>
                    <div class="okx-value-mono text-red" style="font-size:1.4rem;">{misses} <span style="font-size:0.8rem; color:#7a808a;">次</span></div>
                </div>
            </div>
            <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #2b3139;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <div style="color: #cbd5e1; font-size: 0.95rem;">動態預測勝率 (Win Rate)</div>
                    <div class="okx-value-mono {'text-green' if win_rate >= 50 else 'text-yellow'}" style="font-size: 1.6rem; font-weight: 700;">{win_rate:.1f}%</div>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="color: #cbd5e1; font-size: 0.95rem;" class="okx-tooltip" data-tip="建議狙擊目標與真實最高成交價的平均落差">平均目標誤差 (Target MAE) <i>i</i></div>
                    <div class="okx-value-mono" style="font-size: 1.2rem; color: #fff;">± {target_mae:.2f}%</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ----------------- 既有：決策模型反向工程 -----------------
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 12px 0;'>常態決策模型反向工程</div>", unsafe_allow_html=True)
        
        if not bot_decisions or len(bot_decisions) < 5:
            st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>樣本量不足，系統持續採集特徵中...</div>", unsafe_allow_html=True)
        else:
            df_spy = pd.DataFrame(bot_decisions)
            df_spy['diff_frr'] = df_spy['bot_rate_yearly'] - df_spy['market_frr']
            df_spy['diff_twap'] = df_spy['bot_rate_yearly'] - df_spy['market_twap']
            
            std_rate = df_spy['bot_rate_yearly'].std()
            std_frr = df_spy['diff_frr'].std()
            std_twap = df_spy['diff_twap'].std()
            
            mean_rate = df_spy['bot_rate_yearly'].mean()
            mean_diff_frr = df_spy['diff_frr'].mean()
            mean_diff_twap = df_spy['diff_twap'].mean()
            
            if pd.isna(std_rate):
                logic_name = "特徵不足"
                logic_desc = "模型需要更多歷史交易記錄以執行統計分析。"
                box_color = "rgba(122, 128, 138, 0.1)"
                border_color = "#3b4048"
            elif std_rate < 0.2:
                logic_name = "固定費率限制 (Fixed Rate Limit)"
                logic_desc = f"系統判定：該策略無法適應市場波動，將資產定價固化於約 <b>{mean_rate:.2f}%</b>，導致嚴重的流動性閒置與機會成本損失。"
                box_color = "rgba(255, 77, 79, 0.05)"
                border_color = "#ff4d4f"
            elif std_frr < std_twap and std_frr < 1.0:
                logic_name = "表面利率錨定 (FRR Anchored)"
                logic_desc = f"系統判定：該策略依賴滯後的官方表面利率指標。推估底層報價公式為：<b>FRR {'+' if mean_diff_frr>=0 else ''}{mean_diff_frr:.2f}%</b>"
                box_color = "rgba(252, 213, 53, 0.05)"
                border_color = "#fcd535"
            elif std_twap < std_frr and std_twap < 1.0:
                logic_name = "均價追蹤 (TWAP Tracker)"
                logic_desc = f"系統判定：該策略具備即時市場反應能力。推估底層報價公式為：<b>真實 TWAP {'+' if mean_diff_twap>=0 else ''}{mean_diff_twap:.2f}%</b>"
                box_color = "rgba(178, 255, 34, 0.05)"
                border_color = "#b2ff22"
            else:
                logic_name = "動態網格部署 (Dynamic Grid)"
                logic_desc = "系統判定：訂單分佈呈現非線性特徵，推估採用多層階梯網格或基於資金池深度的動態定價模型。"
                box_color = "rgba(168, 85, 247, 0.05)"
                border_color = "#a855f7"

            st.markdown(f"""
            <div class="okx-panel" style="padding:16px; margin-bottom:24px; border-color: {border_color}; background: {box_color};">
                <div style="color: #ffffff; font-weight: 600; font-size: 1.1rem; margin-bottom: 8px;">系統分析結論：{logic_name}</div>
                <div style="color: #cbd5e1; font-size: 0.95rem; line-height: 1.5;">{logic_desc}</div>
                <div style="margin-top: 12px; font-size: 0.8rem; color: #7a808a;">* 模型信心水準基於最近 {len(df_spy)} 筆獨立特徵樣本計算而得。</div>
            </div>
            """, unsafe_allow_html=True)
            
        st.markdown("<hr style='border-color: #2b3139; margin: 24px 0;'>", unsafe_allow_html=True)

        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:10px 0 12px 0;'>基準參數參照</div>", unsafe_allow_html=True)
        m_twap = data.get('market_twap', 0)
        m_vwap = data.get('market_vwap', 0)
        
        st.markdown(f"""<div class="stats-2-col" style="margin-bottom: 24px;"><div class="status-card"><div class="okx-label okx-tooltip" data-tip="3小時歷史真實成交均價">動態 TWAP <i>i</i></div><div class="okx-value okx-value-mono" style="font-size:1.2rem; color:#0ea5e9;">{m_twap:.2f}%</div></div><div class="status-card"><div class="okx-label okx-tooltip" data-tip="當前訂單簿吃下兩百萬美元流動性之加權均價">壓力 VWAP <i>i</i></div><div class="okx-value okx-value-mono" style="font-size:1.2rem; color:#fcd535;">{m_vwap:.2f}%</div></div></div>""", unsafe_allow_html=True)
        
        st.markdown("<div style='color:#ffffff; font-weight:600; font-size:1.05rem; margin:24px 0 12px 0;'>現役模型執行監測</div>", unsafe_allow_html=True)
        
        offers_data = data.get('offers', [])
        fUSD_offers = [o for o in offers_data if 'USD' in o.get('幣種', '')]
        
        if not fUSD_offers:
             st.markdown("<div class='okx-panel' style='text-align:center; color:#7a808a; padding: 40px;'>查無掛單樣本。</div>", unsafe_allow_html=True)
        else:
            cards_html = "<div class='okx-card-grid'>"
            for o in fUSD_offers:
                raw_rate = o.get('raw_rate', 0)
                amt = o.get('金額', 0)
                spread_twap = o.get('spread_twap', 0)
                spread_vwap = o.get('spread_vwap', 0)
                
                tag_twap_class = "tag-green" if spread_twap >= 0 else "tag-gray"
                tag_twap_sign = "+" if spread_twap >= 0 else ""
                
                cards_html += f"<div class='okx-item-card' style='border-color: #3b4048;'><div class='okx-card-header'><span class='okx-tag {tag_twap_class}'>Alpha {tag_twap_sign}{spread_twap:.2f}%</span><span class='okx-card-amt'>${amt:,.0f}</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>策略執行費率</span><span class='okx-list-value okx-value-mono text-green' style='font-size:1.1rem;'>{raw_rate:.2f}%</span></div><div class='okx-list-item border-bottom'><span class='okx-list-label'>Δ TWAP ({m_twap:.2f}%)</span><span class='okx-list-value okx-value-mono' style='color:#0ea5e9;'>{tag_twap_sign}{spread_twap:.2f}%</span></div><div class='okx-list-item'><span class='okx-list-label'>Δ VWAP ({m_vwap:.2f}%)</span><span class='okx-list-value okx-value-mono' style='color:#fcd535;'>{'+' if spread_vwap >= 0 else ''}{spread_vwap:.2f}%</span></div></div>"
                
            cards_html += "</div>"
            st.markdown(cards_html, unsafe_allow_html=True)

    # 實體隱形墊片
    st.markdown("<div style='height: 60px; width: 100%; display: block; visibility: hidden;'></div>", unsafe_allow_html=True)

# 路由判斷
if user_info["role"] == "lending":
    lending_dashboard_fragment()
elif user_info["role"] == "staking":
    staking_dashboard_fragment()