import os
import json
import time
import hmac
import hashlib
import asyncio
import aiohttp
from aiohttp import web
import logging
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Tuple, Optional

# ================= 0. 系統與日誌配置 =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [WORKER] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= 1. 常數與環境變數配置 =================
try:
    TW_TZ = ZoneInfo("Asia/Taipei")
except Exception:
    TW_TZ = timezone(timedelta(hours=8))

LENDING_START_STR = "2026-02-11"

FEE_MULTIPLIER = 0.85
MS_PER_DAY = 86400000.0
MS_PER_HOUR = 3600000.0
DAYS_PER_YEAR = 365.0
MIN_BILLED_MS = 3600000.0
FRESH_OFFER_LIMIT_MS = 1800000.0
API_PAGE_LIMIT = 500  
MIN_LEND_AMOUNT = 150.0
DEFAULT_FX_RATE = 32.0

# 策略常數
SPIKE_THRESHOLD_RATE = 10.0      
SPIKE_PROB_THRESHOLD = 0.55      
VERIFICATION_WINDOW_SEC = 300    

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
BFX_KEY_1 = os.environ.get("BFX_KEY", "")
BFX_SECRET_1 = os.environ.get("BFX_SECRET", "")

_LAST_NONCE = int(time.time() * 1000000)
_BFX_SEMAPHORE = asyncio.Semaphore(5)

seen_trade_ids = {}
seen_spike_ids = set()
is_first_load = True

# 記憶體內預測驗證佇列
pending_spike_verifications = []

# 機器學習初始預設參數
DEFAULT_ML_WEIGHTS = {
    "base_rate": {
        "w_twap": 0.4,
        "w_vwap": 0.4,
        "w_frr": 0.2,
        "bias": 0.0,
        "learning_rate": 0.005,
        "error_threshold": 0.5
    },
    "spike_prob": {
        "w_obi": 2.0,            
        "w_momentum": 1.5,        
        "bias": -3.0,            
        "learning_rate": 0.01
    },
    "target_regression": {
        "w_vwap": 1.2,           
        "w_obi": 5.0,            # 權重下調，平滑預估極值，交由線上梯度下降動態校正
        "learning_rate": 0.05
    },
    "metrics": {
        "total_alerts": 0,
        "hits": 0,
        "misses": 0,
        "target_error_sum": 0.0,
        "last_alert_ts": 0
    }
}

def get_nonce() -> str:
    global _LAST_NONCE
    current = int(time.time() * 1000000)
    if current <= _LAST_NONCE: current = _LAST_NONCE + 1
    _LAST_NONCE = current
    return str(current)

def safe_list(data: Any) -> List[Any]:
    if isinstance(data, list) and (len(data) == 0 or isinstance(data[0], list)): return data
    return []

def calculate_obi(bid_vol: float, ask_vol: float) -> float:
    total_vol = bid_vol + ask_vol
    if total_vol == 0: return 0.0
    return (bid_vol - ask_vol) / total_vol

# ================= 2. API 與資料庫呼叫 =================
async def fetch_bfx_async(session: aiohttp.ClientSession, path: str, api_key: str, api_secret: str, params: Optional[Dict] = None) -> List[Any]:
    if not api_key or not api_secret: return []
    url = f"https://api.bitfinex.com/v2/{path}"
    body = json.dumps(params) if params else "{}"
    
    async with _BFX_SEMAPHORE:
        for attempt in range(3): 
            nonce = get_nonce()
            sig_payload = f"/api/v2/{path}{nonce}{body}".encode('utf8')
            sig = hmac.new(api_secret.encode('utf8'), sig_payload, hashlib.sha384).hexdigest()
            headers = {"bfx-nonce": nonce, "bfx-apikey": api_key, "bfx-signature": sig, "content-type": "application/json"}
            
            try:
                if attempt == 0: await asyncio.sleep(0.05) 
                async with session.post(url, headers=headers, data=body, timeout=10) as res:
                    if res.status == 200: return await res.json()
                    elif res.status == 429: await asyncio.sleep(2.0)
                    else: await asyncio.sleep(1.0)
            except Exception as e:
                logger.error(f"BFX API Error on {path}: {str(e)}")
    return []

async def fetch_public_api_async(session: aiohttp.ClientSession, url: str) -> Any:
    try:
        async with session.get(url, timeout=5) as res:
            if res.status == 200: return await res.json()
    except Exception as e: logger.error(f"Public API error {url}: {str(e)}")
    return []

async def async_supabase_post(session: aiohttp.ClientSession, endpoint: str, payload: Any):
    if not SUPABASE_URL or not SUPABASE_KEY: return
    headers = {
        "apikey": SUPABASE_KEY, 
        "Authorization": f"Bearer {SUPABASE_KEY}", 
        "Content-Type": "application/json", 
        "Prefer": "resolution=merge-duplicates"
    }
    try:
        async with session.post(f"{SUPABASE_URL}/rest/v1/{endpoint}", headers=headers, json=payload, timeout=10) as res:
            if res.status not in (200, 201, 204): 
                error_detail = await res.text()
                logger.error(f"Supabase POST Error [{endpoint}] Status {res.status}: {error_detail}")
    except Exception as e:
        logger.error(f"Supabase Connection Error [{endpoint}]: {str(e)}")

async def async_supabase_get(session: aiohttp.ClientSession, endpoint: str, query: str = "") -> List[Dict]:
    if not SUPABASE_URL or not SUPABASE_KEY: return []
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        async with session.get(f"{SUPABASE_URL}/rest/v1/{endpoint}?{query}", headers=headers, timeout=10) as res:
            if res.status == 200: return await res.json()
            else:
                error_detail = await res.text()
                logger.error(f"Supabase GET Error [{endpoint}] Status {res.status}: {error_detail}")
    except Exception as e:
        logger.error(f"Supabase GET Connection Error [{endpoint}]: {str(e)}")
    return []

async def async_supabase_count(session: aiohttp.ClientSession, endpoint: str) -> int:
    if not SUPABASE_URL or not SUPABASE_KEY: return 0
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Prefer": "count=exact"}
    try:
        async with session.get(f"{SUPABASE_URL}/rest/v1/{endpoint}?select=id&limit=1", headers=headers, timeout=5) as res:
            if res.status in [200, 206]:
                cr = res.headers.get("Content-Range", "")
                if "/" in cr:
                    return int(cr.split("/")[-1])
    except Exception: pass
    return 0

# ================= 3. 動態演算法校正與雙軌寫入 =================
def calculate_base_rate_prediction(twap: float, vwap: float, frr: float, ml_weights: Dict) -> float:
    w_base = ml_weights.get('base_rate', DEFAULT_ML_WEIGHTS['base_rate'])
    return (w_base['w_twap'] * twap) + (w_base['w_vwap'] * vwap) + (w_base['w_frr'] * frr) + w_base['bias']

def calculate_spike_probability(obi: float, twap: float, vwap: float, ml_weights: Dict) -> float:
    w_spike = ml_weights.get('spike_prob', DEFAULT_ML_WEIGHTS['spike_prob'])
    momentum = (vwap / twap) if twap > 0 else 1.0
    z = (w_spike['w_obi'] * obi) + (w_spike['w_momentum'] * momentum) + w_spike['bias']
    z = max(-20.0, min(20.0, z))
    probability = 1.0 / (1.0 + math.exp(-z))
    return probability

def calculate_spike_target(vwap: float, obi: float, ml_weights: Dict) -> float:
    w_reg = ml_weights.get('target_regression', DEFAULT_ML_WEIGHTS['target_regression'])
    base_target = vwap * w_reg['w_vwap']
    pressure_premium = max(0, obi) * w_reg['w_obi']
    return max(SPIKE_THRESHOLD_RATE, base_target + pressure_premium)

def online_base_rate_calibration(actual: float, predicted: float, features: Dict, ml_weights: Dict) -> Dict:
    updated_weights = ml_weights.copy()
    w_base = updated_weights.get('base_rate', DEFAULT_ML_WEIGHTS['base_rate']).copy()
    
    lr = w_base.get('learning_rate', 0.005)
    error = predicted - actual
    
    if abs(error) < w_base.get('error_threshold', 0.5):
        return updated_weights

    w_base['w_twap'] -= lr * error * features['twap']
    w_base['w_vwap'] -= lr * error * features['vwap']
    w_base['w_frr'] -= lr * error * features['frr']
    w_base['bias'] -= lr * error
    
    updated_weights['base_rate'] = w_base
    return updated_weights

def penalize_spike_false_positive(features: Dict, predicted_prob: float, ml_weights: Dict) -> Dict:
    updated_weights = ml_weights.copy()
    w_spike = updated_weights.get('spike_prob', DEFAULT_ML_WEIGHTS['spike_prob']).copy()
    
    lr = w_spike.get('learning_rate', 0.01)
    error = predicted_prob
    momentum = (features['vwap'] / features['twap']) if features['twap'] > 0 else 1.0
    
    w_spike['w_obi'] -= lr * error * features['obi']
    w_spike['w_momentum'] -= lr * error * momentum
    w_spike['bias'] -= lr * error
    
    updated_weights['spike_prob'] = w_spike
    logger.info(f"[ML Calibration] False Positive Penalty Applied. New Spike Bias: {w_spike['bias']:.4f}")
    return updated_weights

def online_target_regression_calibration(actual_max_rate: float, predicted_target: float, features: Dict, ml_weights: Dict) -> Dict:
    updated_weights = ml_weights.copy()
    w_reg = updated_weights.get('target_regression', DEFAULT_ML_WEIGHTS['target_regression']).copy()
    
    lr = w_reg.get('learning_rate', 0.05)
    error = predicted_target - actual_max_rate
    
    w_reg['w_vwap'] -= lr * error * (features['vwap'] / 10.0) 
    w_reg['w_obi'] -= lr * error * max(0, features['obi'])
    
    w_reg['w_vwap'] = max(0.8, min(2.5, w_reg['w_vwap']))
    w_reg['w_obi'] = max(1.0, min(30.0, w_reg['w_obi']))
    
    updated_weights['target_regression'] = w_reg
    logger.info(f"[ML Calibration] Target Regression tuned. New w_vwap: {w_reg['w_vwap']:.3f}, w_obi: {w_reg['w_obi']:.3f}")
    return updated_weights

async def log_and_predict_decision(
    session: aiohttp.ClientSession, offer_id: str, symbol: str, actual_rate: float, 
    market_twap: float, market_vwap: float, market_frr: float, amount: float, period: int,
    ml_weights: Dict
) -> Dict:
    predicted_rate = calculate_base_rate_prediction(market_twap, market_vwap, market_frr, ml_weights)
    
    decision_payload = {
        "offer_id": offer_id,
        "symbol": symbol,
        "bot_rate_yearly": actual_rate,
        "market_twap": market_twap,
        "market_vwap": market_vwap,
        "market_frr": market_frr,
        "bot_amount": amount,
        "bot_period": period
    }
    
    prediction_payload = {
        "offer_id": offer_id,
        "predicted_rate": predicted_rate,
        "actual_rate": actual_rate,
        "error_rate": predicted_rate - actual_rate,
        "features_snapshot": {
            "twap": market_twap,
            "vwap": market_vwap,
            "frr": market_frr
        }
    }
    
    write_tasks = [
        async_supabase_post(session, "bot_decisions?on_conflict=offer_id", [decision_payload]),
        async_supabase_post(session, "bot_prediction_logs?on_conflict=offer_id", [prediction_payload])
    ]
    await asyncio.gather(*write_tasks)
    
    new_weights = online_base_rate_calibration(
        actual=actual_rate, 
        predicted=predicted_rate, 
        features={"twap": market_twap, "vwap": market_vwap, "frr": market_frr}, 
        ml_weights=ml_weights
    )
    return new_weights

# ================= 4. 完整帳本與歷史回測 =================
async def sync_ledger_history_async(session: aiohttp.ClientSession, now_ts: float) -> Tuple[float, float, float, float, float, int]:
    db_auto_p, db_hist_p, db_sum_v_t, db_last_sync_ts = 0.0, 0.0, 0.0, 0
    db_res = await async_supabase_get(session, "bfx_nav", "order=record_date.desc&limit=1")
    if db_res:
        db_auto_p = float(db_res[0].get('auto_p', 0))
        db_hist_p = float(db_res[0].get('hist_p', 0))
        db_sum_v_t = float(db_res[0].get('sum_v_t', 0))
        db_last_sync_ts = int(db_res[0].get('last_sync_ts', 0))

    global_start_ts = int(datetime.strptime(LENDING_START_STR, "%Y-%m-%d").replace(tzinfo=TW_TZ).timestamp() * 1000)
    fetch_start_ts = db_last_sync_ts + 1 if db_last_sync_ts > 0 else global_start_ts
    today_start_ts = int(datetime.now(TW_TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)

    all_ledgers = []
    for currency in ['USD', 'UST']:
        curr_end = int(now_ts)
        while True:
            chunk = await fetch_bfx_async(session, f'auth/r/ledgers/{currency}/hist', BFX_KEY_1, BFX_SECRET_1, {"start": fetch_start_ts, "end": curr_end, "limit": 2500})
            safe_chunk = safe_list(chunk)
            if not safe_chunk: break
            all_ledgers.extend(safe_chunk)
            if len(safe_chunk) < 2500: break
            curr_end = safe_chunk[-1][3] - 1  
            await asyncio.sleep(0.5)

    all_ledgers.sort(key=lambda x: x[3])
    
    daily_snapshots = {}
    curr_auto_p, curr_hist_p, curr_sum_v_t = db_auto_p, db_hist_p, db_sum_v_t
    max_ts_seen = db_last_sync_ts
    seen_ledger_ids = set()

    for i in all_ledgers:
        if len(i) >= 9:
            l_id, mts, val, desc_lower = i[0], i[3], i[5], i[8].lower()
            if l_id not in seen_ledger_ids:
                seen_ledger_ids.add(l_id)
                if mts > max_ts_seen: max_ts_seen = mts
                
                if val > 0 and ("payment" in desc_lower or "interest" in desc_lower):
                    curr_hist_p += val
                elif ("deposit" in desc_lower or "withdrawal" in desc_lower) and "transfer" not in desc_lower: 
                    curr_auto_p += val; curr_sum_v_t += val * mts 
                
                date_str = datetime.fromtimestamp(mts / 1000, tz=TW_TZ).strftime("%Y-%m-%d")
                daily_snapshots[date_str] = {
                    "record_date": date_str,
                    "auto_p": curr_auto_p,
                    "hist_p": curr_hist_p,
                    "sum_v_t": curr_sum_v_t,
                    "last_sync_ts": max_ts_seen
                }

    if daily_snapshots:
        payload_list = list(daily_snapshots.values())
        await async_supabase_post(session, "bfx_nav?on_conflict=record_date", payload_list)

    days_since = max(1.0, (now_ts - global_start_ts) / MS_PER_DAY)
    avg_capital = ((now_ts * curr_auto_p - curr_sum_v_t) / MS_PER_DAY) / days_since if days_since > 0 else curr_auto_p
    hist_apy = ((1 + curr_hist_p / avg_capital) ** (DAYS_PER_YEAR / days_since) - 1.0) * 100.0 if avg_capital > 0 else 0.0

    today_usd = await fetch_bfx_async(session, 'auth/r/ledgers/USD/hist', BFX_KEY_1, BFX_SECRET_1, {"start": today_start_ts, "end": int(now_ts), "limit": 1000})
    today_ust = await fetch_bfx_async(session, 'auth/r/ledgers/UST/hist', BFX_KEY_1, BFX_SECRET_1, {"start": today_start_ts, "end": int(now_ts), "limit": 1000})
    today_profit = 0.0
    seen_today = set()
    for i in safe_list(today_usd) + safe_list(today_ust):
        if len(i) >= 9:
            l_id, val, desc_lower = i[0], i[5], i[8].lower()
            if l_id not in seen_today and val > 0 and ("payment" in desc_lower or "interest" in desc_lower):
                seen_today.add(l_id)
                today_profit += val

    return curr_auto_p, curr_hist_p, hist_apy, today_profit, curr_sum_v_t, max_ts_seen

# ================= 5. 巨觀資料萃取 =================
async def get_macro_data_async(session: aiohttp.ClientSession, now_ts: float, last_payout_ts: float, next_payout_ts: float, frr_dict: Dict[str, float]) -> float:
    global_start_ts = int(datetime.strptime(LENDING_START_STR, "%Y-%m-%d").replace(tzinfo=TW_TZ).timestamp() * 1000)
    recent_history_start_ts = max(global_start_ts, now_ts - (7 * MS_PER_DAY))
    
    db_latest_update = 0
    res = await async_supabase_get(session, "bfx_macro_history", "select=mts_update&order=mts_update.desc&limit=1")
    if res: db_latest_update = int(res[0]['mts_update'])
        
    fetch_start_ts = max(recent_history_start_ts, db_latest_update) if db_latest_update > 0 else global_start_ts
    
    all_macro_hist = []
    for endpoint in ['credits/fUSD/hist', 'loans/fUSD/hist']:
        curr_end = int(now_ts)
        while True:
            chunk = await fetch_bfx_async(session, f'auth/r/funding/{endpoint}', BFX_KEY_1, BFX_SECRET_1, {"limit": API_PAGE_LIMIT, "start": fetch_start_ts, "end": curr_end})
            safe_chunk = safe_list(chunk)
            if not safe_chunk: break
            all_macro_hist.extend(safe_chunk)
            if len(safe_chunk) < API_PAGE_LIMIT: break
            curr_end = safe_chunk[-1][4] - 1  
            await asyncio.sleep(0.5)

    new_closed_records = []
    for hc in all_macro_hist:
        if len(hc) > 13:
            status_str = str(hc[7]).upper()
            if "CLOSED" in status_str:
                h_mts_update, symbol = hc[4], hc[1]
                h_mts_create = hc[13] if (len(hc) > 13 and isinstance(hc[13], (int, float)) and hc[13] > 0) else hc[3]
                h_amount = abs(float(hc[5])) if hc[5] is not None else 0.0
                
                try:
                    raw_rate = float(hc[11]) if (len(hc) > 11 and hc[11] is not None) else 0.0
                except (ValueError, TypeError):
                    raw_rate = 0.0
                    
                h_rate_yearly = raw_rate * DAYS_PER_YEAR * 100
                if h_rate_yearly <= 0.0001: h_rate_yearly = frr_dict.get(symbol, frr_dict.get('fUSD', 0.0))
                
                new_closed_records.append({"id": hc[0], "symbol": symbol, "amount": h_amount, "rate_yearly": h_rate_yearly, "mts_create": h_mts_create, "mts_update": h_mts_update, "status": status_str, "survive_h": (h_mts_update - h_mts_create) / MS_PER_HOUR})

    if new_closed_records: 
        await async_supabase_post(session, "bfx_macro_history?on_conflict=id", new_closed_records)
        
    db_records = await async_supabase_get(session, "bfx_macro_history", f"mts_update.gte.{int(last_payout_ts)}&order=mts_update.desc&limit=5000")

    merged_dict = {rec['id']: rec for rec in db_records}
    for rec in new_closed_records: merged_dict[rec['id']] = rec
        
    all_records = list(merged_dict.values())
    realized_payout = 0.0

    for rec in all_records:
        h_mts_create, h_mts_update, h_amount, h_rate_yearly = rec['mts_create'], rec['mts_update'], float(rec['amount']), float(rec['rate_yearly'])
        h_rate_daily = h_rate_yearly / (DAYS_PER_YEAR * 100)
        
        if rec['status'] == 'CLOSED' and h_mts_update > last_payout_ts:
            overlap_ms = MIN_BILLED_MS if (h_mts_update - h_mts_create) < MIN_BILLED_MS else max(0, min(h_mts_update, now_ts) - max(h_mts_create, last_payout_ts))
            realized_payout += h_amount * h_rate_daily * (overlap_ms / MS_PER_DAY) * FEE_MULTIPLIER

    return realized_payout

# ================= 6. 核心管線：量解放貸 =================
async def run_lending_pipeline():
    if not BFX_KEY_1: return
    global is_first_load, seen_trade_ids, seen_spike_ids, pending_spike_verifications
    now = datetime.now(TW_TZ)
    now_ts = now.timestamp() * 1000
    now_sec = now.timestamp()

    if len(seen_spike_ids) > 5000: seen_spike_ids.clear()

    if now.hour < 9 or (now.hour == 9 and now.minute < 30): 
        next_payout_dt = now.replace(hour=9, minute=30, second=0, microsecond=0); last_payout_dt = next_payout_dt - timedelta(days=1)
    else: 
        last_payout_dt = now.replace(hour=9, minute=30, second=0, microsecond=0); next_payout_dt = last_payout_dt + timedelta(days=1)
    last_payout_ts, next_payout_ts = last_payout_dt.timestamp() * 1000, next_payout_dt.timestamp() * 1000

    async with aiohttp.ClientSession() as session:
        db_res_cache = await async_supabase_get(session, "system_cache", "id=eq.1")
        existing_payload = db_res_cache[0].get('payload', {}) if db_res_cache else {}
        existing_settings = existing_payload.get('settings', {})
        ml_weights = existing_payload.get('ml_weights', DEFAULT_ML_WEIGHTS)

        if 'metrics' not in ml_weights:
            ml_weights['metrics'] = {"total_alerts": 0, "hits": 0, "misses": 0, "target_error_sum": 0.0, "last_alert_ts": 0}
        if 'target_regression' not in ml_weights:
            ml_weights['target_regression'] = DEFAULT_ML_WEIGHTS['target_regression']

        # 非同步撈取側錄樣本數
        count_decisions_task = async_supabase_count(session, "bot_decisions")
        count_spikes_task = async_supabase_count(session, "market_spike_logs")

        tasks = [
            fetch_public_api_async(session, "https://api.bitfinex.com/v2/tickers?symbols=fUSD"), 
            fetch_public_api_async(session, "https://api.bitfinex.com/v2/book/fUSD/P0?len=100"),      
            fetch_public_api_async(session, "https://api.bitfinex.com/v2/trades/fUSD/hist?limit=500"),
            fetch_bfx_async(session, 'auth/r/wallets', BFX_KEY_1, BFX_SECRET_1),                                              
            fetch_bfx_async(session, 'auth/r/funding/credits/fUSD', BFX_KEY_1, BFX_SECRET_1), fetch_bfx_async(session, 'auth/r/funding/loans/fUSD', BFX_KEY_1, BFX_SECRET_1), 
            fetch_bfx_async(session, 'auth/r/funding/offers/fUSD', BFX_KEY_1, BFX_SECRET_1), 
            fetch_public_api_async(session, "https://max-api.maicoin.com/api/v2/tickers/usdttwd"),
            fetch_bfx_async(session, 'auth/r/funding/trades/fUSD/hist', BFX_KEY_1, BFX_SECRET_1, {"limit": 30}),
            count_decisions_task,
            count_spikes_task
        ]
        results = await asyncio.gather(*tasks)
        
        frr_dict = {"fUSD": 0.0}
        for t in safe_list(results[0]):
            if t[0] in frr_dict: frr_dict[t[0]] = float(t[1]) * 365 * 100
        avg_frr = frr_dict.get("fUSD", 0.0)
        
        raw_book_usd = safe_list(results[1])
        asks_list, bids_list = [], []
        total_ask_vol, total_bid_vol = 0.0, 0.0
        
        for b in raw_book_usd:
            if len(b) >= 4:
                raw_rate, period, vol = b[0], b[1], b[3]    
                eff_rate = avg_frr if raw_rate <= 0.00000001 else (raw_rate * 365 * 100)
                if vol > 0:
                    asks_list.append({"rate": eff_rate, "period": period, "vol": abs(vol)})
                    total_ask_vol += abs(vol)
                elif vol < 0:
                    bids_list.append({"rate": eff_rate, "period": period, "vol": abs(vol)})
                    total_bid_vol += abs(vol)

        obi_current = calculate_obi(total_bid_vol, total_ask_vol)
        asks = sorted(asks_list, key=lambda x: x["rate"])
        bids = sorted(bids_list, key=lambda x: x["rate"], reverse=True)
        top_bids = bids[:15]

        cum_vol, vwap_sum, vwap_rate_macro = 0, 0, avg_frr
        for a in asks:
            cum_vol += a["vol"]
            vwap_sum += a["vol"] * a["rate"]
            if cum_vol >= 2000000: break
        if cum_vol > 0: vwap_rate_macro = vwap_sum / cum_vol

        raw_trades_usd = safe_list(results[2])
        valid_trades = [t for t in raw_trades_usd if len(t) >= 5 and (now_ts - t[1]) < 3 * MS_PER_HOUR]
        twap_vol, twap_sum, twap_rate_3h = 0, 0, avg_frr
        for t in valid_trades:
            vol, rate = abs(t[2]), t[3] * 365 * 100
            twap_vol += vol; twap_sum += vol * rate
        if twap_vol > 0: twap_rate_3h = twap_sum / twap_vol

        # ================= [全市場高利雷達與機率預測模組] =================
        spike_payloads = []
        actual_spike_occurred = False
        
        for t in raw_trades_usd:
            if len(t) >= 5:
                trade_id = str(t[0])
                if trade_id in seen_spike_ids: continue
                seen_spike_ids.add(trade_id)

                rate_y = t[3] * 365 * 100
                if rate_y >= SPIKE_THRESHOLD_RATE:
                    actual_spike_occurred = True
                    spike_payloads.append({
                        "trade_id": trade_id, "symbol": "fUSD", "spike_rate_yearly": rate_y,
                        "amount": abs(t[2]), "market_twap": twap_rate_3h, "market_vwap": vwap_rate_macro,
                        "market_frr": avg_frr, "book_ask_vol": total_ask_vol, "book_bid_vol": total_bid_vol,
                        "obi": obi_current
                    })

        if spike_payloads:
            await async_supabase_post(session, "market_spike_logs?on_conflict=trade_id", spike_payloads)
            
        current_spike_prob = calculate_spike_probability(obi_current, twap_rate_3h, vwap_rate_macro, ml_weights)
        current_spike_target = calculate_spike_target(vwap_rate_macro, obi_current, ml_weights)
        
        active_verifications = []
        updated_ml_weights = ml_weights.copy()
        
        max_actual_rate = max([p['spike_rate_yearly'] for p in spike_payloads]) if spike_payloads else 0.0

        for v in pending_spike_verifications:
            if actual_spike_occurred:
                updated_ml_weights['metrics']['hits'] += 1
                predicted_tgt = v.get('predicted_target', SPIKE_THRESHOLD_RATE)
                target_error = abs(max_actual_rate - predicted_tgt)
                updated_ml_weights['metrics']['target_error_sum'] = updated_ml_weights['metrics'].get('target_error_sum', 0.0) + target_error
                
                updated_ml_weights = online_target_regression_calibration(
                    actual_max_rate=max_actual_rate,
                    predicted_target=predicted_tgt,
                    features=v['features'],
                    ml_weights=updated_ml_weights
                )
                logger.info(f"[Sniper Tracker] True Positive! Target Error: {target_error:.2f}%. Regression updated.")
            elif now_sec - v['ts'] > VERIFICATION_WINDOW_SEC:
                updated_ml_weights['metrics']['misses'] += 1
                updated_ml_weights = penalize_spike_false_positive(v['features'], v['prob'], updated_ml_weights)
                logger.info("[Sniper Tracker] False Positive! Prob Model penalized.")
            else:
                active_verifications.append(v)
        
        pending_spike_verifications = active_verifications
        
        if current_spike_prob > SPIKE_PROB_THRESHOLD:
            if now_sec - updated_ml_weights['metrics'].get('last_alert_ts', 0) > VERIFICATION_WINDOW_SEC:
                pending_spike_verifications.append({
                    "ts": now_sec, "prob": current_spike_prob,
                    "predicted_target": current_spike_target,
                    "features": {"obi": obi_current, "twap": twap_rate_3h, "vwap": vwap_rate_macro, "frr": avg_frr}
                })
                updated_ml_weights['metrics']['total_alerts'] += 1
                updated_ml_weights['metrics']['last_alert_ts'] = now_sec
                logger.info("[Sniper Tracker] New sniper alert registered.")
        # ============================================================

        wallets = safe_list(results[3])
        total_assets = sum([w[2] for w in wallets if len(w) > 2 and w[0] == 'funding' and w[1] in ['USD']])
        
        raw_credits_loans = safe_list(results[4]) + safe_list(results[5])
        raw_all_offers = safe_list(results[6])

        loans, pending_offers = [], []
        seen_loan_ids_local = set()
        
        for c in raw_credits_loans:
            if len(c) > 12 and c[0] not in seen_loan_ids_local:
                seen_loan_ids_local.add(c[0])
                symbol, amt = c[1], abs(float(c[5]))
                try:
                    dr = float(c[11]) if (len(c) > 11 and c[11] is not None) else 0.0
                except (ValueError, TypeError): dr = 0.0
                r_y = dr * DAYS_PER_YEAR * 100
                if r_y <= 0.0001: 
                    r_y = frr_dict.get(symbol, avg_frr)
                    dr = r_y / (DAYS_PER_YEAR * 100)
                try:
                    period_val = int(c[12]) if (len(c) > 12 and c[12] is not None) else 0
                except (ValueError, TypeError): period_val = 0
                loans.append({"id": c[0], "symbol": symbol, "mts_create": c[3], "amount": amt, "rate_daily": dr, "rate_yearly": r_y, "period_days": period_val, "daily_profit": amt * dr * FEE_MULTIPLIER})

        async_db_tasks = []
        for o in raw_all_offers:
            if len(o) > 15:
                offer_id = str(o[0])
                symbol = o[1]
                try:
                    rate_daily = float(o[14]) if o[14] is not None else None
                except (ValueError, TypeError): rate_daily = None
                rate_y = (rate_daily * DAYS_PER_YEAR * 100) if rate_daily else frr_dict.get(symbol, avg_frr)
                try:
                    period_val = int(o[15]) if o[15] is not None else 0
                except (ValueError, TypeError): period_val = 0
                
                spread_twap = rate_y - twap_rate_3h
                spread_vwap = rate_y - vwap_rate_macro

                pending_offers.append({
                    "id": offer_id, "symbol": symbol, "amount": o[4], 
                    "rate_yearly": rate_y, "period_days": period_val, 
                    "wait_ms": now_ts - (o[2] if o[2] else 0), "is_frr": rate_daily is None,
                    "spread_twap": spread_twap, "spread_vwap": spread_vwap 
                })
                
                task = log_and_predict_decision(
                    session=session, offer_id=offer_id, symbol=symbol, actual_rate=rate_y,
                    market_twap=twap_rate_3h, market_vwap=vwap_rate_macro, market_frr=avg_frr,
                    amount=float(o[4]), period=period_val, ml_weights=updated_ml_weights
                )
                async_db_tasks.append(task)

        if async_db_tasks:
            calibration_results = await asyncio.gather(*async_db_tasks)
            if calibration_results:
                updated_ml_weights = calibration_results[-1]

        raw_matched_trades = safe_list(results[8])
        
        matched_trades_list = []
        for t in raw_matched_trades:
            if len(t) >= 7:
                trade_id = str(t[0])
                symbol = t[1]
                mts_create = t[2]
                amount = abs(float(t[4]))
                yearly_rate = float(t[5])
                period = int(t[6])
                time_str = datetime.fromtimestamp(mts_create / 1000, tz=TW_TZ).strftime('%H:%M:%S')
                yearly_rate_pct = yearly_rate * 100
                matched_trades_list.append({
                    "時間": time_str, "利率": f"{yearly_rate_pct:.4f}", "期間": period, "數量": amount, "_mts": mts_create
                })
        
        matched_trades_list.sort(key=lambda x: x['_mts'], reverse=True)
        for m in matched_trades_list:
            m.pop('_mts', None)
            
        if is_first_load: is_first_load = False

        final_auto_p, final_hist_p, hist_apy, today_profit, final_sum_v_t, final_sync_ts = await sync_ledger_history_async(session, now_ts)
        realized_payout = await get_macro_data_async(session, now_ts, last_payout_ts, next_payout_ts, frr_dict)

        floating_payout, ui_loans, active_amt, weighted_rate_sum, min_rem_sec = 0.0, [], 0.0, 0.0, 9999999
        for loan in loans:
            amt, dr, r_y, period, mts_create = loan['amount'], loan['rate_daily'], loan['rate_yearly'], loan['period_days'], loan['mts_create']
            loan_end_ts = mts_create + (period * MS_PER_DAY)
            rem_sec = max(0, (datetime.fromtimestamp(loan_end_ts/1000, tz=TW_TZ) - datetime.fromtimestamp(now_ts/1000, tz=TW_TZ)).total_seconds())
            if rem_sec > 0: min_rem_sec = min(min_rem_sec, rem_sec)
            active_amt += amt
            weighted_rate_sum += (amt * r_y)
            
            display_symbol = loan['symbol'].replace('f', '').replace('UST', 'USDT')
            ui_loans.append({
                "幣種": display_symbol, "金額": amt, "年化 (%)": r_y * FEE_MULTIPLIER, 
                "預估日收": loan['daily_profit'], "出借時間": datetime.fromtimestamp(mts_create/1000, tz=TW_TZ).strftime('%m/%d %H:%M'), 
                "到期時間": datetime.fromtimestamp(loan_end_ts/1000, tz=TW_TZ).strftime('%m/%d %H:%M'), "_sort_sec": rem_sec
            })
            
            # 精準實時結算邏輯 (重構錨點與1小時強制約束)
            overlap_ms = max(now_ts - mts_create, MIN_BILLED_MS) if mts_create >= last_payout_ts else (now_ts - last_payout_ts)
            floating_payout += amt * dr * (overlap_ms / MS_PER_DAY) * FEE_MULTIPLIER

        active_apr = ((weighted_rate_sum / active_amt) * FEE_MULTIPLIER) if active_amt > 0 else 0.0
        ui_loans_sorted = sorted(ui_loans, key=lambda x: x['_sort_sec'])
        overall_true_apy = active_apr * (active_amt / total_assets) if total_assets > 0 else 0.0

        fx_data = results[7]
        final_fx = float(fx_data.get('last', DEFAULT_FX_RATE)) if isinstance(fx_data, dict) else DEFAULT_FX_RATE
        raw_idle_amt = max(0, total_assets - active_amt)
        effective_idle_amt = raw_idle_amt if raw_idle_amt >= MIN_LEND_AMOUNT else 0.0
        idle_pct = (effective_idle_amt / total_assets * 100) if total_assets > 0 else 0.0
        
        ui_offers = []
        for o in pending_offers:
            wait_ms, wait_min = o['wait_ms'], int(o['wait_ms'] / 60000)
            wait_str = f"{int(wait_min//60)}h {wait_min%60}m" if wait_min > 0 else "剛掛出"
            status_text = "🟢 換倉中" if wait_ms < FRESH_OFFER_LIMIT_MS else "🟡 排隊中"
            rate_val = f"{o['rate_yearly']:.2f}% (FRR)" if o.get('is_frr', False) else f"{o['rate_yearly']:.2f}%"
            display_symbol = o['symbol'].replace('f', '').replace('UST', 'USDT')
            
            ui_offers.append({ 
                "幣種": display_symbol, "金額": o['amount'], "毛年化": rate_val, 
                "掛單天期": f"{o['period_days']}天", "排隊時間": wait_str, "狀態": status_text,
                "raw_rate": o['rate_yearly'], "spread_twap": o['spread_twap'], "spread_vwap": o['spread_vwap'] 
            })

        prediction_metrics = {
            "spike_probability_pct": round(current_spike_prob * 100, 2),
            "suggested_spike_target": current_spike_target, 
            "is_sniper_mode_active": current_spike_prob > SPIKE_PROB_THRESHOLD,
            "current_obi": round(obi_current, 4),
            "metrics": updated_ml_weights.get("metrics", {})
        }

        sample_counts = {
            "decisions": results[9],
            "spikes": results[10]
        }

        final_payload = { 
            "settings": existing_settings,
            "ml_weights": updated_ml_weights,
            "prediction_metrics": prediction_metrics,
            "sample_counts": sample_counts,
            "total": total_assets, "active_apr": active_apr, "active": active_amt, "loans": ui_loans_sorted, "offers": ui_offers, 
            "history": final_hist_p, "auto_p": final_auto_p, "today_profit": today_profit, "realized_payout": realized_payout, "floating_payout": floating_payout, 
            "next_payout_total": realized_payout + floating_payout, "fx": final_fx, "next_repayment_time": min_rem_sec, "idle_pct": idle_pct, 
            "idle_amt": effective_idle_amt, 
            "hist_apy": hist_apy, "true_apy": overall_true_apy,
            "market_frr": avg_frr, "market_vwap": vwap_rate_macro, "market_twap": twap_rate_3h,
            "matched_trades": matched_trades_list,
            "top_bids": top_bids
        }

        try:
            update_body = {"id": 1, "payload": final_payload, "updated_at": datetime.utcnow().isoformat()}
            await async_supabase_post(session, "system_cache?on_conflict=id", update_body)
            logger.info(f"[Lending Analytics] Engine synchronized. Precision data updated.")
        except Exception as e:
            logger.error(f"Cache Write Error: {str(e)}")

# ================= 7. 伺服器啟動與主線程入口 =================
async def health_check(request):
    return web.Response(text="Quantitative Engine is operational.")

async def start_dummy_server():
    app = web.Application()
    app.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"[Server] Service binding on port: {port}")

async def main_loop():
    await start_dummy_server()
    while True:
        try:
            await asyncio.gather(
                run_lending_pipeline()
            )
        except Exception as e:
            logger.error(f"Execution loop exception: {str(e)}")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main_loop())