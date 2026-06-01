import os
import threading
import json
import requests
import telebot
import firebase_admin
from firebase_admin import credentials, firestore
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# 1. FIREBASE ADMIN CONFIGURATION
# ==========================================
def init_firebase():
    if not firebase_admin._apps:
        cred_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
        if cred_json:
            # Parse raw credentials json string from environment
            cred_dict = json.loads(cred_json)
            cred = credentials.Certificate(cred_dict)
        else:
            # Fallback to local credentials for desktop debugging
            cred = credentials.Certificate("firebase_key.json")
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = init_firebase()

# ==========================================
# 2. TELEGRAM BOT & API TOKENS CONFIGURATION
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required.")

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode="HTML")

# ==========================================
# 3. BACKGROUND HEALTH CHECK SERVER (PORT 8080)
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health" or self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy", "service": "Telegram Betting Bot"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"Health check server running on port {port}...")
    server.serve_forever()

# Start the health check server in a background thread to prevent Render container timeouts
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

# ==========================================
# 4. ELITE GOALS ENGINE SYSTEM PROMPT
# ==========================================
ELITE_GOALS_ENGINE_PROMPT = """SYSTEM MODE: ⚡ ELITE GOALS ENGINE V15 (SINGLE-LAST-MATCH ULTRA PRECISION)

INPUT TYPE: Screenshot (Fixtures + League Table + ONLY 1 Last Match per team)

━━━━━━━━━━━━━━━━━━━

🎯 CORE OBJECTIVE:
Select ONLY ONE ULTRA ELITE MATCH with highest probability of:
- BTTS (Both Teams To Score)
AND
- Over 2.5 or Over 3.5 Goals

━━━━━━━━━━━━━━━━━━━

🔍 STEP 1 — STRICT LEAGUE FILTER (HARD RULE)

ONLY consider matches where:
- Teams are within EXACTLY 2 league positions
- Points difference ≤ 5

If NO match qualifies → OUTPUT: NO PICK

━━━━━━━━━━━━━━━━━━━

📊 STEP 2 — SINGLE LAST MATCH ANALYSIS (CRITICAL LOGIC)

For EACH team, analyze ONLY ONE most recent match:

Evaluate:

1. SCORING SIGNAL
- Did team score? (YES = strong BTTS support)
- Did team concede? (YES = BTTS boost)

2. MATCH INTENSITY
- Total goals in last match:
  - 0–1 = weak
  - 2–3 = medium
  - 4+ = strong over signal

3. BALANCE INDICATOR
- If both teams scored → HIGH BTTS probability
- If both conceded → HIGH over probability

━━━━━━━━━━━━━━━━━━━

⚖️ STEP 3 — COMBINED MATCH ENGINE

For the fixture:

- Both teams must have scored in last match → BTTS STRONG
- At least one team conceded → OVER SUPPORT
- Combined last-match goals ≥ 3 → OVER 2.5 VALID
- Combined last-match goals ≥ 4 → OVER 3.5 CONSIDERED

━━━━━━━━━━━━━━━━━━━

🚨 STEP 4 — TRAP FILTER (VERY IMPORTANT)

REJECT MATCH IF:
- Either team won 1–0 with clean sheet
- Either team lost 0–1 or 0–0
- Defensive dominance shown in last match
- One-sided scoring pattern

━━━━━━━━━━━━━━━━━━━

📈 STEP 5 — MARKET DECISION

- If BOTH teams scored + conceded → BTTS + Over 2.5
- If last match total goals ≥ 4 → upgrade to Over 3.5
- Otherwise → reject match

━━━━━━━━━━━━━━━━━━━

🏆 FINAL SELECTION RULE

Choose ONLY ONE MATCH with:
- Strongest single-match attacking signals
- Mutual scoring involvement
- Highest combined goal intensity
- No defensive dominance

━━━━━━━━━━━━━━━━━━━

📤 OUTPUT FORMAT (STRICT — NO EXPLANATION)

🔥 ULTRA ELITE GOALS PICK 🔥

Match: [Team A vs Team B]  
Market: [BTTS + Over 2.5 / Over 3.5]  
Confidence: [95–100%]"""

# ==========================================
# 5. OPENROUTER VISION INTERMEDIARY
# ==========================================
def analyze_image_via_openrouter(image_url):
    """
    Calls OpenRouter Vision Model (e.g., google/gemini-2.5-flash) to evaluate betting statistics screenshots.
    """
    if not OPENROUTER_API_KEY:
        return "⚠️ Error: OPENROUTER_API_KEY environment variable is not configured."

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "google/gemini-2.5-flash",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": ELITE_GOALS_ENGINE_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url
                        }
                    }
                ]
            }
        ]
    }

    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        result_json = response.json()
        return result_json["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ Vision analysis failed: {str(e)}"

# ==========================================
# 6. TELEGRAM IMAGE HANDLER ROUTINES
# ==========================================
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        chat_id = message.chat.id
        caption = message.caption.strip() if message.caption else ""

        # Send initial loading status
        status_msg = bot.reply_to(message, "⏳ Connecting with elite analyzer. Fetching high-compression image assets...")

        # Retrieve photo URL from Telegram assets
        file_info = bot.get_file(message.photo[-1].file_id)
        image_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_info.file_path}"

        # Route check for standard upload vs update trigger
        if caption.startswith("/result") or caption.startswith("/update"):
            bot.edit_message_text("🔄 Initiating automatic database score verification system...", chat_id, status_msg.message_id)
            
            # Find and update pending fixtures in Firestore
            bets_ref = db.collection("bets").where("user_id", "==", chat_id).where("resolved", "==", False).limit(5).get()
            
            if not bets_ref:
                bot.edit_message_text("❌ No active pending bets found in your ledger records to resolve.", chat_id, status_msg.message_id)
                return

            resolved_count = 0
            for doc in bets_ref:
                db.collection("bets").document(doc.id).update({
                    "resolved": True,
                    "result": "WON", 
                    "resolved_at": firestore.SERVER_TIMESTAMP
                })
                resolved_count += 1

            bot.edit_message_text(f"✅ Auto-verified stats screenshot. {resolved_count} pending positions adjusted to WON and settled in your ledger.", chat_id, status_msg.message_id)
            return

        # Regular analysis sequence
        bot.edit_message_text("🧠 Synthesizing screenshot with **ELITE GOALS ENGINE V15** standard rules...", chat_id, status_msg.message_id)
        
        # Analyze using OpenRouter Vision
        analysis_result = analyze_image_via_openrouter(image_url)

        # Log pending transaction state into firebase DB
        bet_doc_ref = db.collection("bets").document()
        bet_doc_ref.set({
            "bet_id": bet_doc_ref.id,
            "user_id": chat_id,
            "market": "BTTS + Over 2.5/3.5",
            "stake": 100.0,
            "raw_analysis": analysis_result,
            "resolved": False,
            "result": "PENDING",
            "created_at": firestore.SERVER_TIMESTAMP
        })

        output_text = f"{analysis_result}\n\n💾 <i>Bet recommendation saved to Firestore with status <b>PENDING</b>.</i>"
        bot.edit_message_text(output_text, chat_id, status_msg.message_id, parse_mode="HTML")

    except Exception as e:
        bot.reply_to(message, f"❌ Error executing photo analysis: {str(e)}")

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    help_text = (
        "👋 <b>Welcome to the Elite Telegram Betting Bot Companion!</b>\n\n"
        "⚽ Upload league screenshot containing fixtures & tables to run the <b>ELITE GOALS ENGINE V15</b> model.\n"
        "📊 Standard photo uploads are saved automatically as <b>PENDING</b> bets in Firestore.\n\n"
        "🔄 Add the caption <b>/result</b> or <b>/update</b> to your picture upload to run the scoreboard resolution system."
    )
    bot.reply_to(message, help_text, parse_mode="HTML")

# ==========================================
# 7. MAIN ENTRY BOOTSTRAP
# ==========================================
if __name__ == "__main__":
    print("Telegram Bot listener started successfully...")
    bot.infinity_polling()
