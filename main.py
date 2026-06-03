import os
import threading
import json
import base64
import re
import time
import requests
import telebot
import firebase_admin
from firebase_admin import credentials, firestore, storage
from openai import OpenAI
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# 1. FIREBASE ADMIN CONFIGURATION
# ==========================================
def init_firebase():
    if not firebase_admin._apps:
        cred_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
        bucket_url = os.environ.get("FIREBASE_BUCKET_URL")
        
        if cred_json:
            cred_dict = json.loads(cred_json)
            cred = credentials.Certificate(cred_dict)
        elif os.path.exists("serviceAccountKey.json"):
            cred = credentials.Certificate("serviceAccountKey.json")
        elif os.path.exists("firebase_key.json"):
            cred = credentials.Certificate("firebase_key.json")
        else:
            raise FileNotFoundError("❌ CRITICAL: No Firebase credentials file found! Check Render Secret Files.")
            
        firebase_admin.initialize_app(cred, {
            'storageBucket': bucket_url
        })
    return firestore.client()

db = init_firebase()
bucket = storage.bucket()

# ==========================================
# 2. TELEGRAM BOT & API TOKENS CONFIGURATION
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required.")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY environment variable is required.")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode="HTML")
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)

media_groups = {}
media_locks = {}

# ==========================================
# 3. BACKGROUND HEALTH CHECK SERVER (PORT 8080)
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ["/health", "/"]:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy", "service": "Telegram Betting Bot"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_HEAD(self):
        if self.path in ["/health", "/"]:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"Health check server running on port {port}...")
    server.serve_forever()

health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

# ==========================================
# 4. HYPER-TARGETED V15 PROMPT WITH WEIGHTED MEMORY
# ==========================================
ELITE_GOALS_ENGINE_PROMPT = """SYSTEM MODE: ⚡ ELITE GOALS ENGINE V15 (HYPER-TARGETED MEMORY CALIBRATION)
INPUT TYPE: Screenshots (Completed Results + New Fixtures + New League Table)

🎯 CORE OBJECTIVE:
Select ONLY ONE ULTRA ELITE MATCH from the new fixtures with the absolute highest probability of:
- BTTS (Both Teams To Score) AND Over 2.5 or Over 3.5 Goals

🧠 HYPER-TARGETED MEMORY DECAY & ALGORITHMIC LOOP LOGIC:
Virtual football engines run on repetitive cyclic matchup profiles across quick seasons. Below your input parameters, you are supplied with real-time extracted data from your database historical memory ledger. 
Execute this strict weighting system:

🔥 CRITICAL OVERRIDE RULE (THE HYPER-TARGETED EDGE):
- Look closely at the "INJECTED SYSTEM MEMORY LAYER".
- If any fixture in the upcoming schedule shows a historical matchup average >= 3.0 goals and a BTTS Ratio >= 75%, AUTOMATICALLY BYPASS standard conservative league filtering.
- Elevate this match to your absolute priority #1 pick. Lock in the market as BTTS + Over 2.5 (or Over 3.5 if average goals >= 3.8) and flag confidence as 98-100%.

📋 STANDARD PIPELINE FILTERING (IF NO HISTORY OVERRIDE APPLIES):
1. LEAGUE FILTER: Teams must be within EXACTLY 2 league standings positions, and points gap <= 5.
2. LAST MATCH METRICS: Both teams must have scored and conceded in their immediate previous round match.
3. TRAP FILTER: Instantly reject any fixture if either team came off a 0-0, 1-0, or 0-1 low-intensity gridlock.

📤 OUTPUT FORMAT (STRICT — NO EXTRA PROSE):
🔥 ULTRA ELITE GOALS PICK 🔥
Match: [Team A vs Team B]  
Market: [BTTS + Over 2.5 / Over 3.5]  
Confidence: [95–100%]"""

RESULT_PROMPT = """SYSTEM MODE: ⚡ RESULT EXTRACTION ENGINE V2.0 (HIGH-RESILIENCE PARSING)
Examine the provided screenshots of completed football scores. Extract every match name and final score.
You must return them cleanly formatted, even if text spacing on the screen is tight or compressed.
OUTPUT FORMAT (STRICT): Return ONLY plain text list of matches and scores, one per line. No introduction.
Example:
WOL 2-2 LEE
ARS 1-0 CHE
MUN 3-1 LIV"""

# ==========================================
# 5. BANKROLL & STAKING ENGINE CORE LOGIC
# ==========================================
INITIAL_BANKROLL = 100000.0  
ESTIMATED_MARKET_ODDS = 2.40  

def get_or_init_bankroll():
    ref = db.collection("investment").document("bankroll_ledger")
    doc = ref.get()
    if doc.exists:
        return doc.to_dict()
    else:
        initial_data = {
            "current_balance": INITIAL_BANKROLL,
            "total_profit": 0.0,
            "total_invested": 0.0,
            "total_bets": 0,
            "won_bets": 0,
            "lost_bets": 0,
            "updated_at": datetime.utcnow()
        }
        ref.set(initial_data)
        return initial_data

def calculate_kelly_stake(confidence_pct, bankroll_bal):
    p = confidence_pct / 100.0
    q = 1.0 - p
    b = ESTIMATED_MARKET_ODDS - 1.0
    
    if b <= 0:
        return 0.0, 0.0
        
    img_kelly = (b * p - q) / b
    safe_f = img_kelly * 0.25  # Defensive Quarter-Kelly setting
    
    if safe_f < 0.02:
        safe_f = 0.02
    if safe_f > 0.15:
        safe_f = 0.15
        
    suggested_units = round(bankroll_bal * safe_f, 0)
    return safe_f * 100.0, suggested_units

def update_bankroll_on_settlement(status, absolute_stake_units):
    ref = db.collection("investment").document("bankroll_ledger")
    ledger = get_or_init_bankroll()
    
    current_bal = ledger["current_balance"]
    total_profit = ledger["total_profit"]
    won_count = ledger["won_bets"]
    lost_count = ledger["lost_bets"]
    total_invested = ledger["total_invested"] + absolute_stake_units
    
    if status == "WON":
        gross_return = absolute_stake_units * ESTIMATED_MARKET_ODDS
        net_gain = gross_return - absolute_stake_units
        current_bal += net_gain
        total_profit += net_gain
        won_count += 1
    else:
        current_bal -= absolute_stake_units
        total_profit -= absolute_stake_units
        lost_count += 1
        
    updated_data = {
        "current_balance": round(current_bal, 2),
        "total_profit": round(total_profit, 2),
        "total_invested": round(total_invested, 2),
        "total_bets": won_count + lost_count,
        "won_bets": won_count,
        "lost_bets": lost_count,
        "updated_at": datetime.utcnow()
    }
    ref.update(updated_data)
    return updated_data

# ==========================================
# 6. HIGH-RESILIENCE UTILITY FUNCTIONS
# ==========================================
def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def call_vision_ai_multi(image_paths, prompt_text):
    user_content = [{"type": "text", "text": "Analyze these collected screenshots together structurally based on your engine instructions."}]
    for path in image_paths:
        base64_image = encode_image(path)
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
        })

    try:
        response = client.chat.completions.create(
            model="google/gemini-2.5-flash",  
            messages=[
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1,
            max_tokens=1000
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"❌ Vision API Error: {e}")
        return f"ERROR: {str(e)}"

def parse_prediction(raw_text):
    try:
        match_line = re.search(r"Match:\s*(.+)\s*vs\s*(.+)", raw_text, re.IGNORECASE)
        conf_line = re.search(r"Confidence:\s*(\d+)", raw_text, re.IGNORECASE)
        
        if match_line:
            team_a = match_line.group(1).strip()
            team_b = match_line.group(2).strip()
            confidence = int(conf_line.group(1)) if conf_line else 95
            return {
                "team_a": team_a,
                "team_b": team_b,
                "raw_match": f"{team_a} vs {team_b}",
                "confidence": confidence,
                "btts": "YES",
                "over25": True if "Over 2.5" in raw_text else False,
                "over35": True if "Over 3.5" in raw_text else False
            }
    except Exception as e:
        print(f"Parsing exception: {e}")
        return None

def save_matchup_history_to_learning_layer(scores_text):
    """Parses and updates structural virtual matchup loops with high space resilience."""
    lines = scores_text.split('\n')
    for line in lines:
        # 🔥 TUNED REGEX: Catches normal separation 'WOL 2 - 2 LEE' AND compressed clumps 'WOL2-2LEE' seamlessly
        match = re.search(r"([A-Za-z]{2,4})\s*(\d+)\s*[-–:]\s*(\d+)\s*([A-Za-z]{2,4})", line)
        if match:
            t1, s1, s2, t2 = match.group(1).strip(), int(match.group(2)), int(match.group(3)), match.group(4).strip()
            sorted_teams = sorted([t1.lower(), t2.lower()])
            matchup_id = f"loop_{sorted_teams[0]}_{sorted_teams[1]}"
            
            total_goals = s1 + s2
            is_btts = "YES" if (s1 > 0 and s2 > 0) else "NO"
            
            doc_ref = db.collection("matchup_history").document(matchup_id)
            doc = doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                historical_scores = data.get("historical_scores", [])
                historical_scores.append(f"{s1}-{s2}")
                
                total_entries = len(historical_scores)
                btts_count = sum(1 for s in historical_scores if int(s.split('-')[0]) > 0 and int(s.split('-')[1]) > 0)
                
                doc_ref.update({
                    "historical_scores": historical_scores,
                    "avg_goals": ((data.get("avg_goals", total_goals) * (total_entries - 1)) + total_goals) / total_entries,
                    "btts_probability": btts_count / total_entries
                })
            else:
                doc_ref.set({
                    "team_x": sorted_teams[0],
                    "team_y": sorted_teams[1],
                    "historical_scores": [f"{s1}-{s2}"],
                    "avg_goals": float(total_goals),
                    "btts_probability": 1.0 if is_btts == "YES" else 0.0
                })

def get_historical_context_string_for_engine():
    try:
        history_ref = db.collection("matchup_history").where("avg_goals", ">=", 2.5).limit(25).stream()
        context_lines = []
        for doc in history_ref:
            d = doc.to_dict()
            context_lines.append(f"Matchup Loop Profile [{d['team_x'].upper()} vs {d['team_y'].upper()}] -> Avg Goals: {d['avg_goals']:.1f}, BTTS Ratio: {d['btts_probability']*100:.1f}%")
        return "\n".join(context_lines) if context_lines else "No historical high-scoring trends indexed yet."
    except Exception:
        return "History read timeout."

def evaluate_and_format_settlement(pred_data, doc_id, scores_text):
    team_a = pred_data.get("team_a")
    team_b = pred_data.get("team_b")
    allocated_stake = pred_data.get("allocated_stake_units", 2000.0)
    
    if not team_a or not team_b:
        return None, None

    # 🔥 TUNED REGEX: Resilient parsing for compressed strings on settlement lookups
    pattern = rf"({re.escape(team_a)}|{re.escape(team_b)})\s*(\d+)\s*[-–:]\s*(\d+)\s*({re.escape(team_a)}|{re.escape(team_b)})"
    match = re.search(pattern, scores_text, re.IGNORECASE)
    
    if not match:
        return "NOT_FOUND", None

    first_team, s1, s2 = match.group(1), int(match.group(2)), int(match.group(3))
    score_a, score_b = (s1, s2) if first_team.lower() == team_a.lower() else (s2, s1)
    
    total = score_a + score_b
    actual_btts = "YES" if (score_a > 0 and score_b > 0) else "NO"
    
    btts_win = (actual_btts == "YES")
    o25_win = (total > 2) if pred_data.get("over25", True) else True
    o35_win = (total > 3) if pred_data.get("over35", False) else True
    
    is_overall_win = btts_win and o25_win and o35_win
    status_str = "WON" if is_overall_win else "LOST"
    
    updated_ledger = update_bankroll_on_settlement(status_str, allocated_stake)
    roi = (updated_ledger["total_profit"] / updated_ledger["total_invested"]) * 100 if updated_ledger["total_invested"] > 0 else 0.0
    
    card_header = "🏆 <b>PREDICTION WINNER CARD</b>" if status_str == "WON" else "📉 <b>PREDICTION LOSS CARD</b>"
    status_badge = f"🟩 <b>[WON (+{round(allocated_stake * (ESTIMATED_MARKET_ODDS - 1), 0)} Units)]</b>" if status_str == "WON" else f"🟥 <b>[LOST (-{allocated_stake} Units)]</b>"
    target_market = "BTTS + Over 3.5" if pred_data.get("over35") else "BTTS + Over 2.5"
    
    beautiful_card = (
        f"{card_header}\n"
        f"<code>-------------------------------------</code>\n"
        f"⚽ <b>Match:</b> {team_a.upper()} vs {team_b.upper()}\n"
        f"🏁 <b>Result:</b> <code>{score_a} - {score_b}</code> ({total} goals)\n"
        f"🎯 <b>Target Market:</b> {target_market}\n"
        f"📊 <b>BTTS Landed:</b> {'✅ YES' if actual_btts == 'YES' else '❌ NO'}\n"
        f"💰 <b>Risk Allocated:</b> {allocated_stake} Units\n"
        f"<code>-------------------------------------</code>\n"
        f"✨ <b>Outcome:</b> {status_badge}\n"
        f"誠 <b>Wallet Balance:</b> {updated_ledger['current_balance']} Units\n"
        f"📈 <b>All-Time ROI:</b> {roi:.2f}%\n"
        f"📊 <b>Record (W/L):</b> {updated_ledger['won_bets']}W - {updated_ledger['lost_bets']}L"
    )
    
    return status_str, beautiful_card

# ==========================================
# 7. PIPELINE PROCESSING ENGINE
# ==========================================
def process_unified_pipeline_album(chat_id, media_group_id):
    time.sleep(2.5)  
    
    with media_locks[media_group_id]:
        paths = media_groups.get(media_group_id, [])
        if not paths:
            return
        del media_groups[media_group_id]
        
    try:
        bot.send_message(chat_id, f"⚡ <b>Unified Investment Pipeline Active ({len(paths)} files).</b>\n\nStep 1: Auditing past results & balancing ledger entries...")
        
        scores_text = call_vision_ai_multi(paths, RESULT_PROMPT)
        
        if scores_text and "ERROR" not in scores_text:
            save_matchup_history_to_learning_layer(scores_text)
            
            pending_docs = db.collection("predictions").where("status", "==", "PENDING").stream()
            settled_count = 0
            
            for doc in pending_docs:
                doc_data = doc.to_dict()
                pred_data = parse_prediction(doc_data.get("raw_prediction", ""))
                
                if pred_data:
                    pred_data["allocated_stake_units"] = doc_data.get("allocated_stake_units", 2000.0)
                    status, beautiful_card = evaluate_and_format_settlement(pred_data, doc.id, scores_text)
                    if status and status != "NOT_FOUND":
                        db.collection("predictions").document(doc.id).update({
                            "status": status, 
                            "actual_outcome": status,
                            "resolved_at": datetime.utcnow()
                        })
                        bot.send_message(chat_id, beautiful_card, parse_mode="HTML")
                        settled_count += 1
                        
            if settled_count == 0:
                bot.send_message(chat_id, "ℹ️ No pending database matches found matching these completed scores.")
        else:
            bot.send_message(chat_id, "⚠️ Notice: Could not extract clear score lines from this album step.")

        # Step 2: Run predictions for upcoming fixtures
        bot.send_message(chat_id, "Step 2: Injecting memory weights. Evaluating new patterns for next season picks...")
        historical_context = get_historical_context_string_for_engine()
        
        customized_prediction_prompt = f"{ELITE_GOALS_ENGINE_PROMPT}\n\n[INJECTED SYSTEM MEMORY LAYER - HISTORICAL SEASONS HIGHLIGHTS]:\n{historical_context}"
        prediction_result = call_vision_ai_multi(paths, customized_prediction_prompt)
        
        # Step 3: Run Kelly Investment risk pricing modeling if a valid pick is found
        if "NO PICK" not in prediction_result and "ERROR" not in prediction_result:
            parsed_data = parse_prediction(prediction_result)
            if parsed_data:
                ledger = get_or_init_bankroll()
                current_bal = ledger["current_balance"]
                
                stake_pct, absolute_units = calculate_kelly_stake(parsed_data["confidence"], current_bal)
                
                actionable_card = (
                    f"{prediction_result}\n\n"
                    f"⚖️ <b>KELLY INVESTMENT SYSTEM DESIGN:</b>\n"
                    f"🐳 <b>Suggested Risk Weight:</b> {stake_pct:.2f}% of capital\n"
                    f"💵 <b>Calculated Investment:</b> <code>{absolute_units} Units</code>"
                )
                
                bot.send_message(chat_id, actionable_card)
                
                unique_id = f"{chat_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
                db.collection("predictions").document(unique_id).set({
                    "chat_id": chat_id,
                    "timestamp": datetime.utcnow(),
                    "raw_prediction": prediction_result,
                    "team_a": parsed_data.get("team_a"),
                    "team_b": parsed_data.get("team_b"),
                    "allocated_stake_units": absolute_units,
                    "status": "PENDING",
                    "actual_outcome": None
                })
        else:
            bot.send_message(chat_id, prediction_result)
                
    except Exception as e:
        bot.send_message(chat_id, f"❌ Pipeline structural runtime exception:\n<code>{str(e)}</code>")
    finally:
        for p in paths:
            if os.path.exists(p):
                os.remove(p)

# ==========================================
# 8. TELEGRAM EVENT SUB-CHANNELS
# ==========================================
@bot.message_handler(commands=['start'])
def start(message):
    welcome_text = (
        "🚀 <b>Hyper-Targeted V15 Self-Learning System Active!</b>\n\n"
        "Send your match package album components together in a single burst. The tuning tweaks handle tight mobile spacing and enforce historical matchup override rules seamlessly!"
    )
    bot.reply_to(message, welcome_text, parse_mode="HTML")

@bot.message_handler(commands=['bankroll'])
def show_bankroll(message):
    ledger = get_or_init_bankroll()
    roi = (ledger["total_profit"] / ledger["total_invested"]) * 100 if ledger["total_invested"] > 0 else 0.0
    report = (
        f"📊 <b>LIVE SYSTEM INVESTMENT REPORT</b>\n"
        f"<code>-------------------------------------</code>\n"
        f"💳 <b>Current Wallet:</b> {ledger['current_balance']} Units\n"
        f"💰 <b>Net System Profit:</b> {ledger['total_profit']} Units\n"
        f"🛡️ <b>Total Volume Traded:</b> {ledger['total_invested']} Units\n"
        f"📈 <b>Live Yield Performance:</b> {roi:.2f}% ROI\n"
        f"🎯 <b>Total Slips Evaluated:</b> {ledger['total_bets']} matches\n"
        f"🟩 <b>Successful Runs:</b> {ledger['won_bets']}\n"
        f"🟥 <b>Deficit Runs:</b> {ledger['lost_bets']}"
    )
    bot.reply_to(message, report, parse_mode="HTML")

@bot.message_handler(content_types=['photo'])
def handle_incoming_photo(message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        img_data = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_info.file_path}").content
        
        local_path = f"img_{message.message_id}_{message.chat.id}.jpg"
        with open(local_path, "wb") as f:
            f.write(img_data)

        if message.media_group_id:
            mg_id = message.media_group_id
            if mg_id not in media_locks:
                media_locks[mg_id] = threading.Lock()
                media_groups[mg_id] = []
                
                t = threading.Thread(target=process_unified_pipeline_album, args=(message.chat.id, mg_id))
                t.start()
                
            with media_locks[mg_id]:
                media_groups[mg_id].append(local_path)
        else:
            mg_id = f"single_run_{message.message_id}"
            media_locks[mg_id] = threading.Lock()
            media_groups[mg_id] = [local_path]
            process_unified_pipeline_album(message.chat.id, mg_id)

    except Exception as e:
        bot.reply_to(message, f"❌ Ingestion Error: <code>{str(e)}</code>")

if __name__ == "__main__":
    print("🚀 Hyper-Targeted Processing Engine Running...")
    bot.infinity_polling()
