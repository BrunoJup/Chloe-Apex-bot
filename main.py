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

# Album / Media Group Global Buffers
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
ELITE_GOALS_ENGINE_PROMPT = """SYSTEM MODE: ⚡ ELITE GOALS ENGINE V15 (SINGLE-LAST-MATCH ULTRA PRECISION)
INPUT TYPE: Screenshots (Fixtures + League Table + ONLY 1 Last Match per team)

🎯 CORE OBJECTIVE:
Select ONLY ONE ULTRA ELITE MATCH with highest probability of:
- BTTS (Both Teams To Score) AND Over 2.5 or Over 3.5 Goals

🔍 STEP 1 — STRICT LEAGUE FILTER (HARD RULE)
ONLY consider matches where:
- Teams are within EXACTLY 2 league positions
- Points difference ≤ 5
If NO match qualifies → OUTPUT: NO PICK

📊 STEP 2 — SINGLE LAST MATCH ANALYSIS (CRITICAL LOGIC)
For EACH team, analyze ONLY ONE most recent match:
1. SCORING SIGNAL: Did team score? (YES = strong BTTS support), Did team concede? (YES = BTTS boost)
2. MATCH INTENSITY: Total goals in last match (0–1 = weak, 2–3 = medium, 4+ = strong over signal)
3. BALANCE INDICATOR: If both teams scored → HIGH BTTS probability, If both conceded → HIGH over probability

⚖️ STEP 3 — COMBINED MATCH ENGINE
- Both teams must have scored in last match → BTTS STRONG
- At least one team conceded → OVER SUPPORT
- Combined last-match goals ≥ 3 → OVER 2.5 VALID
- Combined last-match goals ≥ 4 → OVER 3.5 CONSIDERED

🚨 STEP 4 — TRAP FILTER (VERY IMPORTANT)
REJECT MATCH IF: Either team won 1–0 with clean sheet, Either team lost 0–1 or 0–0, Defensive dominance shown in last match, One-sided scoring pattern

📈 STEP 5 — MARKET DECISION
- If BOTH teams scored + conceded → BTTS + Over 2.5
- If last match total goals ≥ 4 → upgrade to Over 3.5
- Otherwise → reject match

🏆 FINAL SELECTION RULE
Choose ONLY ONE MATCH with strongest single-match attacking signals, mutual scoring involvement, highest combined goal intensity, no defensive dominance.

📤 OUTPUT FORMAT (STRICT — NO EXPLANATION)
🔥 ULTRA ELITE GOALS PICK 🔥
Match: [Team A vs Team B]  
Market: [BTTS + Over 2.5 / Over 3.5]  
Confidence: [95–100%]"""

RESULT_PROMPT = """SYSTEM MODE: ⚡ RESULT EXTRACTION ENGINE V1.0
Analyze the provided screenshots showing completed football match results. Extract match names and final scores.
OUTPUT FORMAT (STRICT): Return ONLY plain text list of matches and scores, one per line. No introduction.
Example:
Team A 2-1 Team B
Team C 0-0 Team D"""

# ==========================================
# 5. CORE UTILITY ENGINE FUNCTIONS
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
            max_tokens=1000  # Strict allocation caps request token ceiling for free tier accounts
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
                "team_b": match_line.group(2).strip(),
                "btts": "YES",
                "over25": True if "Over 2.5" in raw_text else False,
                "over35": True if "Over 3.5" in raw_text else False
            }
    except Exception:
        return None

def evaluate_bet(pred_data, scores_text):
    team_a, team_b = pred_data["team_a"], pred_data["team_b"]
    pattern = rf"({re.escape(team_a)}|{re.escape(team_b)})\s*(\d+)\s*-\s*(\d+)\s*({re.escape(team_a)}|{re.escape(team_b)})"
    match = re.search(pattern, scores_text, re.IGNORECASE)
    
    if not match:
        return "NOT_FOUND", None

    first_team, s1, s2 = match.group(1), int(match.group(2)), int(match.group(3))
    score_a, score_b = (s1, s2) if first_team.lower() == team_a.lower() else (s2, s1)
    
    total = score_a + score_b
    actual_btts = "YES" if (score_a > 0 and score_b > 0) else "NO"
    
    btts_win = (pred_data["btts"] == actual_btts)
    o25_win = (total > 2) if pred_data["over25"] else True
    o35_win = (total > 3) if pred_data["over35"] else True
    
    score_str = f"{team_a} {score_a}-{score_b} {team_b}"
    return ("WON", score_str) if (btts_win and o25_win and o35_win) else ("LOST", score_str)

# ==========================================
# 6. BATCH PROCESSING LOGIC FOR ALBUMS
# ==========================================
def process_delayed_group(chat_id, media_group_id, caption):
    time.sleep(2.0)  # Safe collection buffering delay
    
    with media_locks[media_group_id]:
        paths = media_groups.get(media_group_id, [])
        if not paths:
            return
        del media_groups[media_group_id]
        
    try:
        is_result_workflow = caption and caption.strip().lower() in ['/result', '/update']
        
        if is_result_workflow:
            bot.send_message(chat_id, f"🔢 Processing ({len(paths)}) screenshots inside album... Updating system history.")
            scores_text = call_vision_ai_multi(paths, RESULT_PROMPT)
            
            if not scores_text or "ERROR" in scores_text:
                bot.send_message(chat_id, f"❌ Vision extraction failure:\n<code>{scores_text}</code>")
                return

            pending_docs = db.collection("predictions").where("status", "==", "PENDING").stream()
            updated_count = 0
            
            for doc in pending_docs:
                pred_data = parse_prediction(doc.to_dict().get("raw_prediction", ""))
                if pred_data:
                    status, score_str = evaluate_bet(pred_data, scores_text)
                    if status != "NOT_FOUND":
                        db.collection("predictions").document(doc.id).update({
                            "status": status, "actual_outcome": score_str
                        })
                        updated_count += 1
            
            bot.send_message(chat_id, f"🏁 Processing complete! Verified and updated ({updated_count}) matches inside Firestore.")
            
        else:
            bot.send_message(chat_id, f"🧠 Image grouping detected ({len(paths)} files). Launching V15 Multi-Image Evaluation...")
            prediction_result = call_vision_ai_multi(paths, ELITE_GOALS_ENGINE_PROMPT)
            bot.send_message(chat_id, prediction_result)

            if "NO PICK" not in prediction_result and "ERROR" not in prediction_result:
                unique_id = f"{chat_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
                
                blob = bucket.blob(f"screenshots/{unique_id}_main.jpg")
                blob.upload_from_filename(paths[0])
                blob.make_public()
                
                db.collection("predictions").document(unique_id).set({
                    "chat_id": chat_id,
                    "timestamp": datetime.utcnow(),
                    "raw_prediction": prediction_result,
                    "image_url": blob.public_url,
                    "status": "PENDING",
                    "actual_outcome": None
                })
                
    except Exception as e:
        bot.send_message(chat_id, f"❌ Engine failure during batch runtime processing:\n<code>{str(e)}</code>")
    finally:
        for p in paths:
            if os.path.exists(p):
                os.remove(p)

# ==========================================
# 7. TELEGRAM BOT EVENT HANDLERS
# ==========================================
@bot.message_handler(commands=['start'])
def start(message):
    welcome_text = (
        "📸 <b>PRO Goals Detector Active!</b>\n\n"
        "• Send individual or grouped fixture photos (Albums) for <b>V15 Predictions</b>.\n"
        "• Send results photos with the caption <code>/result</code> to auto-update statistics."
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

        # Handle Album Grouping
        if message.media_group_id:
            mg_id = message.media_group_id
            if mg_id not in media_locks:
                media_locks[mg_id] = threading.Lock()
                media_groups[mg_id] = []
                
                t = threading.Thread(target=process_delayed_group, args=(message.chat.id, mg_id, message.caption))
                t.start()
                
            with media_locks[mg_id]:
                media_groups[mg_id].append(local_path)
                
        # Handle Standard Single Image
        else:
            is_result = message.caption and message.caption.strip().lower() in ['/result', '/update']
            paths = [local_path]
            
            if is_result:
                bot.reply_to(message, "🔢 Processing single result image...")
                scores_text = call_vision_ai_multi(paths, RESULT_PROMPT)
                
                if not scores_text or "ERROR" in scores_text:
                    bot.reply_to(message, f"❌ Vision extraction failure:\n<code>{scores_text}</code>")
                    return

                pending_docs = db.collection("predictions").where("status", "==", "PENDING").stream()
                updated_count = 0
                for doc in pending_docs:
                    pred_data = parse_prediction(doc.to_dict().get("raw_prediction", ""))
                    if pred_data:
                        status, score_str = evaluate_bet(pred_data, scores_text)
                        if status != "NOT_FOUND":
                            db.collection("predictions").document(doc.id).update({
                                "status": status, "actual_outcome": score_str
                            })
                            updated_count += 1
                bot.reply_to(message, f"🏁 Done! Verified and updated ({updated_count}) matches inside Firestore.")
            else:
                bot.reply_to(message, "🧠 Running V15 Elite Goals Engine...")
                prediction_result = call_vision_ai_multi(paths, ELITE_GOALS_ENGINE_PROMPT)
                bot.reply_to(message, prediction_result)

                if "NO PICK" not in prediction_result and "ERROR" not in prediction_result:
                    unique_id = f"{message.chat.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
                    blob = bucket.blob(f"screenshots/{unique_id}.jpg")
                    blob.upload_from_filename(local_path)
                    blob.make_public()
                    
                    db.collection("predictions").document(unique_id).set({
                        "chat_id": message.chat.id,
                        "timestamp": datetime.utcnow(),
                        "raw_prediction": prediction_result,
                        "image_url": blob.public_url,
                        "status": "PENDING",
                        "actual_outcome": None
                    })
            
            if os.path.exists(local_path):
                os.remove(local_path)

    except Exception as e:
        print(f"System incoming image exception: {e}")
        bot.reply_to(message, f"❌ Pipeline structural fault: <code>{str(e)}</code>")

if __name__ == "__main__":
    print("🚀 Bot process listening to Telegram Polling infrastructure...")
    bot.infinity_polling()
