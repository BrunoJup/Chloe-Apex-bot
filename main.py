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
# 4. ELITE GOALS ENGINE SYSTEM PROMPTS
# ==========================================
ELITE_GOALS_ENGINE_PROMPT = """SYSTEM MODE: ⚡ ELITE GOALS ENGINE V15 (SEASONAL SELF-LEARNING UPGRADE)
INPUT TYPE: Screenshots (Completed Results + New Fixtures + New League Table)

🎯 CORE OBJECTIVE:
Select ONLY ONE ULTRA ELITE MATCH from the new fixtures with the absolute highest probability of:
- BTTS (Both Teams To Score) AND Over 2.5 or Over 3.5 Goals

🧠 VIRTUAL FOOTBALL SEASONAL LOOP SELF-LEARNING LOGIC:
Virtual leagues run in cyclical loops where historical matchup profiles repeat across different seasons. 
You are supplied with historic trend summaries for matchups. Use them to heavily bias selection:
- If a matchup historically results in high scoring BTTS patterns across multiple seasons, elevate its priority.
- If a matchup traditionally shows defensive gridlocks or highly asymmetric clean sheets, filter it out as a TRAP.

🔍 STEP 1 — STRICT LEAGUE FILTER
ONLY consider new fixtures where:
- Teams are within EXACTLY 2 league positions
- Points difference ≤ 5

📊 STEP 2 — HISTORIC PROFILE & LAST MATCH CORRELATION
For qualifying pairs, check their singular last match data and historical scoreline loops:
1. SCORING TRENDS: Did both teams display mutual scoring and conceding trends?
2. INTENSITY CLUSTERS: Total combined goals in historical loops ≥ 3 for Over 2.5, and ≥ 4 for Over 3.5 signals.

📤 OUTPUT FORMAT (STRICT — NO EXTRA PROSE):
🔥 ULTRA ELITE GOALS PICK 🔥
Match: [Team A vs Team B]  
Market: [BTTS + Over 2.5 / Over 3.5]  
Confidence: [95–100%]"""

RESULT_PROMPT = """SYSTEM MODE: ⚡ RESULT EXTRACTION ENGINE V1.0
Analyze the images showing completed football match results. Extract match names and final scores.
OUTPUT FORMAT (STRICT): Return ONLY plain text list of matches and scores, one per line. No introduction.
Example:
Team A 2-1 Team B
Team C 0-0 Team D"""

# ==========================================
# 5. UTILITY & INTELLIGENT MATCHUP LEARNING FUNCTIONS
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
        match_line = re.search(r"Match:\s*(.+)\s*vs\s*(.+)", raw_text)
        if match_line:
            return {
                "team_a": match_line.group(1).strip(),
                "team_b": message_line.group(2).strip() if (match_line and len(match_line.groups()) > 1) else "",
                "raw_match": match_line.group(0).replace("Match:", "").strip(),
                "btts": "YES",
                "over25": True if "Over 2.5" in raw_text else False,
                "over35": True if "Over 3.5" in raw_text else False
            }
    except Exception:
        return None

def extract_teams_from_raw_match(raw_match_str):
    parts = re.split(r'\bvs\b', raw_match_str, flags=re.IGNORECASE)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, None

def save_matchup_history_to_learning_layer(scores_text):
    """Parses and updates structural virtual matchup loop historical behaviors."""
    lines = scores_text.split('\n')
    for line in lines:
        match = re.search(r"(.+?)\s*(\d+)\s*-\s*(\d+)\s*(.+)", line)
        if match:
            t1, s1, s2, t2 = match.group(1).strip(), int(match.group(2)), int(match.group(3)), match.group(4).strip()
            # Alpha-sorted pair path to create permanent cross-season historical reference IDs
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
                
                # Recalculate average profiles
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
    """Compiles the database self-learning history into structural text for the context injector."""
    try:
        history_ref = db.collection("matchup_history").where("avg_goals", ">=", 2.5).limit(20).stream()
        context_lines = []
        for doc in history_ref:
            d = doc.to_dict()
            context_lines.append(f"Matchup Loop Profile [{d['team_x']} vs {d['team_y']}] -> Avg Goals: {d['avg_goals']:.1f}, BTTS Ratio: {d['btts_probability']*100:%}")
        return "\n".join(context_lines) if context_lines else "No historical high-scoring trends indexed yet."
    except Exception:
        return "History read timeout."

def evaluate_and_format_settlement(pred_data, scores_text):
    raw_match_str = pred_data.get("raw_match", "")
    team_a, team_b = extract_teams_from_raw_match(raw_match_str)
    
    if not team_a or not team_b:
        return None, None

    pattern = rf"({re.escape(team_a)}|{re.escape(team_b)})\s*(\d+)\s*-\s*(\d+)\s*({re.escape(team_a)}|{re.escape(team_b)})"
    match = re.search(pattern, scores_text, re.IGNORECASE)
    
    if not match:
        return "NOT_FOUND", None

    first_team, s1, s2 = match.group(1), int(match.group(2)), int(match.group(3))
    score_a, score_b = (s1, s2) if first_team.lower() == team_a.lower() else (s2, s1)
    
    total = score_a + score_b
    actual_btts = "YES" if (score_a > 0 and score_b > 0) else "NO"
    
    btts_win = ("YES" == actual_btts)
    o25_win = (total > 2) if pred_data.get("over25", True) else True
    o35_win = (total > 3) if pred_data.get("over35", False) else True
    
    is_overall_win = btts_win and o25_win and o35_win
    status_str = "WON" if is_overall_win else "LOST"
    
    # Premium Results Card Visual Generator
    card_title = "🏆 <b>MATCH RESOLUTION CARD</b>" if is_overall_win else "📉 <b>MATCH RESOLUTION CARD</b>"
    status_badge = "🟩 <b>[PROFIT — WIN]</b>" if is_overall_win else "🟥 <b>[LOSS — MISSED]</b>"
    target_market = f"BTTS + Over 2.5" if not pred_data.get("over35") else "BTTS + Over 3.5"
    
    beautiful_card = (
        f"{card_title}\n"
        f"-----------------------------------------\n"
        f"⚽ <b>Match:</b> {team_a} vs {team_b}\n"
        f"🏁 <b>Final Score:</b> <code>{score_a} - {score_b}</code> (Total: {total} Goals)\n"
        f"🎯 <b>Target Market:</b> {target_market}\n"
        f"📊 <b>BTTS Landed:</b> {'✅ Yes' if actual_btts == 'YES' else '❌ No'}\n"
        f"-----------------------------------------\n"
        f"✨ <b>Outcome:</b> {status_badge}\n"
    )
    
    return status_str, beautiful_card

# ==========================================
# 6. PIPELINE PROCESSING ENGINE
# ==========================================
def process_unified_pipeline_album(chat_id, media_group_id):
    time.sleep(2.5)  # Safe batch capture sync buffer
    
    with media_locks[media_group_id]:
        paths = media_groups.get(media_group_id, [])
        if not paths:
            return
        del media_groups[media_group_id]
        
    try:
        bot.send_message(chat_id, f"⚡ <b>Unified Multi-Image Pipeline Activated ({len(paths)} files).</b>\n\nStep 1: Parsing past match outcomes to update self-learning histories...")
        
        # 1. First Pass: Read Results and update Firestore Statistics
        scores_text = call_vision_ai_multi(paths, RESULT_PROMPT)
        
        if scores_text and "ERROR" not in scores_text:
            # Train the self-learning virtual historical loop engine
            save_matchup_history_to_learning_layer(scores_text)
            
            # Settle any active pending historical predictions
            pending_docs = db.collection("predictions").where("status", "==", "PENDING").stream()
            settled_count = 0
            
            for doc in pending_docs:
                pred_data = parse_prediction(doc.to_dict().get("raw_prediction", ""))
                if pred_data:
                    status, beautiful_card = evaluate_and_format_settlement(pred_data, scores_text)
                    if status and status != "NOT_FOUND":
                        db.collection("predictions").document(doc.id).update({
                            "status": status, "actual_outcome": status
                        })
                        bot.send_message(chat_id, beautiful_card, parse_mode="HTML")
                        settled_count += 1
                        
            bot.send_message(chat_id, f"✅ Statistics Synchronized. Loop learning entries updated. ({settled_count}) old tickets settled.")
        else:
            bot.send_message(chat_id, "⚠️ Notice: No readable score text matching patterns identified in this set.")

        # 2. Second Pass: Extract historical data and feed into V15 prediction engine
        bot.send_message(chat_id, "Step 2: Injecting memory weights. Evaluating new patterns for next season picks...")
        historical_context = get_historical_context_string_for_engine()
        
        customized_prediction_prompt = f"{ELITE_GOALS_ENGINE_PROMPT}\n\n[INJECTED SYSTEM MEMORY LAYER - HISTORICAL SEASONS HIGHLIGHTS]:\n{historical_context}"
        prediction_result = call_vision_ai_multi(paths, customized_prediction_prompt)
        
        bot.send_message(chat_id, prediction_result)

        # Log new prediction entry if valid pick was established
        if "NO PICK" not in prediction_result and "ERROR" not in prediction_result:
            parsed_data = parse_prediction(prediction_result)
            unique_id = f"{chat_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            
            blob = bucket.blob(f"screenshots/{unique_id}_bundle.jpg")
            blob.upload_from_filename(paths[0])
            blob.make_public()
            
            db.collection("predictions").document(unique_id).set({
                "chat_id": chat_id,
                "timestamp": datetime.utcnow(),
                "raw_prediction": prediction_result,
                "raw_match": parsed_data.get("raw_match") if parsed_data else "Unknown Match",
                "image_url": blob.public_url,
                "status": "PENDING",
                "actual_outcome": None
            })
                
    except Exception as e:
        bot.send_message(chat_id, f"❌ Pipeline structural runtime exception:\n<code>{str(e)}</code>")
    finally:
        for p in paths:
            if os.path.exists(p):
                os.remove(p)

# ==========================================
# 7. TELEGRAM EVENT SUB-CHANNELS
# ==========================================
@bot.message_handler(commands=['start'])
def start(message):
    welcome_text = (
        "🚀 <b>All-In-One V15 Self-Learning Engine Ready!</b>\n\n"
        "Simply upload your entire match package screenshots as a single group album:\n"
        "• <b>Past Results</b> (to close old bets and update loop learning patterns)\n"
        "• <b>New Fixtures + Current Table</b> (for generation of next ultra precision picks)\n\n"
        "<i>Everything settles and generates automatically in a single unified message burst!</i>"
    )
    bot.reply_to(message, welcome_text, parse_mode="HTML")

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
            # Handle lone single images seamlessly by casting into unified single image pipelines
            bot.reply_to(message, "💡 <i>Tip: Send images together as a grouped batch album for best analysis performance. Running isolated scan...</i>")
            mg_id = f"single_run_{message.message_id}"
            media_locks[mg_id] = threading.Lock()
            media_groups[mg_id] = [local_path]
            process_unified_pipeline_album(message.chat.id, mg_id)

    except Exception as e:
        bot.reply_to(message, f"❌ Ingestion Error: <code>{str(e)}</code>")

if __name__ == "__main__":
    print("🚀 Unified Infinite Polling Layer Active...")
    bot.infinity_polling()
