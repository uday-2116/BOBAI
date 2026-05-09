import sys
import os
import re
import json
import threading
import time
import random
import logging
from datetime import datetime

import pygame
import pytz
import pyttsx3
import requests
import speech_recognition as sr
import google.generativeai as genai
from dotenv import load_dotenv
import pygame
import threading

# ───────── MUSIC SYSTEM ─────────
SONGS_FOLDER = "your songs folder location"

pygame.mixer.init()

AUDIO_PLAYING = False
AUDIO_STOP = False

def get_random_song():
    files = [f for f in os.listdir(SONGS_FOLDER)
             if f.lower().endswith((".mp3", ".wav", ".ogg"))]
    if not files:
        return None, None
    song = random.choice(files)
    return os.path.join(SONGS_FOLDER, song), song

def play_song_blocking(path):
    global AUDIO_PLAYING, AUDIO_STOP
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        AUDIO_PLAYING = True
        AUDIO_STOP = False

        while pygame.mixer.music.get_busy():
            if AUDIO_STOP:
                pygame.mixer.music.stop()
                break
            time.sleep(0.1)

        AUDIO_PLAYING = False
    except Exception as e:
        print("Audio error:", e)

def play_music():
    file_path, name = get_random_song()
    if not file_path:
        speak("Oh no! I could not find any songs!")
        return

    speak(f"Playing {name.replace('_',' ')}")

    threading.Thread(
        target=play_song_blocking,
        args=(file_path,),
        daemon=True
    ).start()

def stop_music():
    global AUDIO_STOP
    AUDIO_STOP = True

# ─────────────────────────────────────────────────────────────
# LOAD + VALIDATE KEYS
# ─────────────────────────────────────────────────────────────

load_dotenv()
SEARCHAPI_KEY = os.getenv("SEARCHAPI_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

missing = []
if not SEARCHAPI_KEY:  missing.append("SEARCHAPI_KEY")
if not GEMINI_API_KEY: missing.append("GEMINI_API_KEY")

if missing:
    print("\n" + "=" * 60)
    print("  ERROR: Missing API keys in .env file:")
    for k in missing:
        print(f"    - {k}")
    print()
    print("  Create a .env file in this folder with:")
    print("    SEARCHAPI_KEY=your-searchapi-key")
    print("    GEMINI_API_KEY=your-gemini-key")
    print()
    print("  SearchAPI key → https://www.searchapi.io/dashboard")
    print("  Gemini key    → https://aistudio.google.com/app/apikey")
    print("=" * 60 + "\n")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
)
log = logging.getLogger("BOB")

# ─────────────────────────────────────────────────────────────
# GEMINI SETUP
# ─────────────────────────────────────────────────────────────

genai.configure(api_key=GEMINI_API_KEY)

BOB_SYSTEM_INSTRUCTION = """
You are BOB, a magical and friendly voice assistant for children under 10 years old.

STRICT RULES — always follow these:
- Use simple words a 5 to 7 year old understands easily
- Be warm, enthusiastic, encouraging like a fun older sibling
- Use words like: superstar, explorer, magical, wow, brilliant, amazing
- Keep responses SHORT: 3 to 5 sentences maximum
- NEVER use markdown: no asterisks, no hashes, no bullet points, no numbered lists
- Write in plain natural spoken sentences only
- NEVER say you are an AI, Gemini, or Claude. Your name is only BOB.
- NEVER say anything scary, violent in detail, or inappropriate for children
"""

# Single model: gemini-2.5-flash-lite (free tier)
GEMINI_MODEL_NAME = "gemini-2.5-flash-lite"
GEMINI_MODEL = genai.GenerativeModel(
    model_name=GEMINI_MODEL_NAME,
    system_instruction=BOB_SYSTEM_INSTRUCTION
)

# Minimum gap (seconds) between consecutive Gemini calls to avoid RPM limits
GEMINI_CALL_INTERVAL = 5
_last_gemini_call: float = 0.0


def _clean(text: str) -> str:
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"_+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def gemini(prompt: str, max_tokens: int = 400) -> str:
    """
    Call Gemini with pacing and retry on rate-limit.
    Enforces a minimum gap between calls to stay within free-tier RPM.
    """
    global _last_gemini_call

    # Pace calls to avoid hitting free-tier RPM
    elapsed = time.time() - _last_gemini_call
    if elapsed < GEMINI_CALL_INTERVAL:
        wait = GEMINI_CALL_INTERVAL - elapsed
        log.info(f"Pacing Gemini call — waiting {wait:.1f}s")
        time.sleep(wait)

    for attempt in range(3):
        try:
            _last_gemini_call = time.time()
            resp = GEMINI_MODEL.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(max_output_tokens=max_tokens)
            )
            return _clean(resp.text)
        except Exception as e:
            err = str(e).lower()
            log.error(f"Gemini error attempt {attempt+1}: {e}")
            if "quota" in err or "limit" in err or "429" in err or "resource_exhausted" in err:
                wait = 30 * (attempt + 1)
                log.info(f"Rate limited — waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                break

    log.error("Gemini failed after retries.")
    return "Hmm, I need a little break! Let us try again in a moment!"
def gemini_json(prompt: str) -> dict:
    """Call Gemini expecting a JSON response. Returns dict or {}."""
    raw = gemini(
        prompt + "\n\nRespond ONLY with valid JSON. No markdown, no backticks, no explanation.",
        max_tokens=300
    )
    try:
        raw = re.sub(r"```json|```", "", raw).strip()
        return json.loads(raw)
    except Exception as e:
        log.error(f"Gemini JSON parse error: {e}")
        return {}

# ─────────────────────────────────────────────────────────────
# TEXT-TO-SPEECH  (fresh engine per call = Windows loop fix)
# ─────────────────────────────────────────────────────────────

def speak(text: str):
    """Speak text aloud. Fresh pyttsx3 engine each call fixes Windows silence bug."""
    clean = (
        text.replace("**", "").replace("*", "").replace("#", "")
            .replace("_", " ").replace("  ", " ").strip()
    )
    print(f"\nBOB: {clean}\n")
    try:
        engine = pyttsx3.init()
        voices = engine.getProperty("voices")
        for v in voices:
            if any(kw in v.name.lower() for kw in
                   ["zira", "hazel", "female", "fiona", "moira", "samantha"]):
                engine.setProperty("voice", v.id)
                break
        engine.setProperty("rate", 150)
        engine.setProperty("volume", 1.0)
        engine.say(clean)
        engine.runAndWait()
        engine.stop()
    except Exception as e:
        log.error(f"TTS error: {e}")

# ─────────────────────────────────────────────────────────────
# SPEECH RECOGNITION
# ─────────────────────────────────────────────────────────────

_REC = sr.Recognizer()
_REC.energy_threshold         = 300
_REC.dynamic_energy_threshold = True
_REC.pause_threshold          = 1.2


def listen(timeout: int = 12, phrase_limit: int = 20) -> str:
    """Listen from mic, return recognised text or empty string."""
    with sr.Microphone() as src:
        _REC.adjust_for_ambient_noise(src, duration=0.4)
        print("Listening...")
        try:
            audio = _REC.listen(src, timeout=timeout, phrase_time_limit=phrase_limit)
        except sr.WaitTimeoutError:
            return ""
    try:
        text = _REC.recognize_google(audio)
        print(f"You said: {text}")
        return text.strip()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        log.warning(f"STT error: {e}")
        return ""


def listen_for_answer(wait_seconds: int = 10) -> str:
    """
    Listen specifically for the child's answer to a question.
    Gives wait_seconds of silence tolerance before timing out.
    Returns spoken text or "" if no answer.
    """
    with sr.Microphone() as src:
        _REC.adjust_for_ambient_noise(src, duration=0.3)
        print(f"Waiting {wait_seconds}s for child's answer...")
        try:
            audio = _REC.listen(src, timeout=wait_seconds, phrase_time_limit=8)
        except sr.WaitTimeoutError:
            print("No answer — timed out.")
            return ""
    try:
        text = _REC.recognize_google(audio)
        print(f"Child answered: {text}")
        return text.strip()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError:
        return ""

# ─────────────────────────────────────────────────────────────
# SEARCHAPI
# ─────────────────────────────────────────────────────────────

SEARCHAPI_URL = "https://www.searchapi.io/api/v1/search"


def search(query: str, num: int = 5) -> str:
    """
    Search Google via SearchAPI and return combined snippet text.
    Returns raw text for Gemini to process — not pre-summarised.
    """
    params = {
        "engine":  "google",
        "q":       query,
        "api_key": SEARCHAPI_KEY,
        "num":     num,
        "safe":    "active",
    }
    try:
        resp = requests.get(SEARCHAPI_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"SearchAPI error: {e}")
        return ""

    parts = []

    # Answer box (best for direct facts)
    ab = data.get("answer_box", {})
    for field in ["answer", "snippet", "result", "description"]:
        val = ab.get(field)
        if val:
            if isinstance(val, list):
                val = " ".join(str(v) for v in val)
            parts.append(str(val).strip())
            break

    # Knowledge graph
    kg = data.get("knowledge_graph", {})
    if kg.get("description"):
        parts.append(kg["description"])

    # Organic snippets
    for item in data.get("organic_results", [])[:4]:
        s = item.get("snippet", "").strip()
        if s:
            parts.append(s)

    combined = " ".join(parts)
    combined = re.sub(r"\s+", " ", combined).strip()
    return combined[:2000]  # Cap at 2000 chars to avoid huge prompts

# ─────────────────────────────────────────────────────────────
# STORY CHAPTERS  (just search queries — NO hardcoded questions)
# ─────────────────────────────────────────────────────────────

# 7 Kandas of Ramayana
RAMAYANA_SEGMENTS = [
    # Kanda 1: Bala Kanda
    "Ramayana Bala Kanda King Dasharatha Ayodhya four sons born Rama Lakshmana Bharata Shatrughna putrakameshti yagna",
    # Kanda 2: Ayodhya Kanda
    "Ramayana Ayodhya Kanda Kaikeyi boon Manthara Rama exile 14 years Bharata refuses throne keeps sandals",
    # Kanda 3: Aranya Kanda
    "Ramayana Aranya Kanda forest exile Surpanakha nose cut Ravana golden deer Maricha kidnaps Sita Jatayu fight",
    # Kanda 4: Kishkindha Kanda
    "Ramayana Kishkindha Kanda Rama meets Hanuman Sugriva friendship kills Vali monkey army search Sita",
    # Kanda 5: Sundara Kanda
    "Ramayana Sundara Kanda Hanuman leaps ocean Lanka finds Sita Ashoka grove gives ring burns Lanka returns",
    # Kanda 6: Yuddha Kanda
    "Ramayana Yuddha Kanda Rama army builds Ram Setu bridge crosses ocean Lanka war Ravana killed Sita rescued Pushpaka vimana returns Ayodhya",
    # Kanda 7: Uttara Kanda
    "Ramayana Uttara Kanda Rama coronation king Ayodhya Sita exile Luv Kush born Valmiki ashram reunited",
]

RAMAYANA_KANDA_NAMES = [
    "Bala Kanda — The Story of Rama's Birth",
    "Ayodhya Kanda — The Exile from Ayodhya",
    "Aranya Kanda — Adventures in the Forest",
    "Kishkindha Kanda — The Kingdom of Monkeys",
    "Sundara Kanda — Hanuman's Great Journey",
    "Yuddha Kanda — The Great Battle of Lanka",
    "Uttara Kanda — Rama's Reign and Legacy",
]

MAHABHARATA_SEGMENTS = [
    "Mahabharata Pandavas five brothers Kauravas hundred cousins Hastinapur rivalry childhood",
    "Mahabharata Dronacharya trains Pandavas Kauravas archery Arjuna best student bird eye",
    "Mahabharata Kauravas build lac wax house trap Pandavas escape secret tunnel Vidura",
    "Mahabharata Yudhishthira dice game Shakuni cheating Pandavas lose kingdom Draupadi",
    "Mahabharata Pandavas 13 years exile 12 years forest 1 year incognito Virata kingdom",
    "Mahabharata Krishna Arjuna Bhagavad Gita chariot Kurukshetra dharma advice before war",
    "Mahabharata Kurukshetra 18 day war Pandavas victory Kauravas defeated Duryodhana falls",
]

STORY_PROGRESS: dict = {
    "ramayana":    0,
    "mahabharata": 0,
}

# Stores last told story segment text for question generation
LAST_SEGMENT: dict = {
    "ramayana":    "",
    "mahabharata": "",
}

ANSWER_WAIT = 10   # seconds to wait for child's answer

# Flag to signal mid-story stop
STORY_STOP_FLAG: dict = {"active": False}

# ─────────────────────────────────────────────────────────────
# DYNAMIC STORY ENGINE
# ─────────────────────────────────────────────────────────────

def check_stop_during_story() -> bool:
    """
    Listen briefly (3s) for a stop command during story auto-continue.
    Returns True if kid said stop, False otherwise.
    """
    with sr.Microphone() as src:
        _REC.adjust_for_ambient_noise(src, duration=0.2)
        try:
            audio = _REC.listen(src, timeout=3, phrase_time_limit=5)
        except sr.WaitTimeoutError:
            return False
    try:
        text = _REC.recognize_google(audio).lower().strip()
        print(f"[Story check] Heard: {text}")
        if any(k in text for k in STOP_KW):
            return True
    except Exception:
        pass
    return False


def run_story_chapter(story: str, auto_continue: bool = False):
    """
    Full chapter flow for one Kanda:
      1. Check stop flag before starting
      2. Announce Kanda name and fetch content via SearchAPI
      3. Gemini narrates as a child-friendly 4-sentence segment
      4. Gemini generates a question from the segment
      5. BOB asks — waits 10s for answer — Gemini evaluates
      6. Listen 3s for stop command, then auto-continue to next Kanda
    """
    # Check stop flag before starting this chapter
    if STORY_STOP_FLAG["active"]:
        STORY_STOP_FLAG["active"] = False
        return

    story       = story.lower()
    segments    = RAMAYANA_SEGMENTS if story == "ramayana" else MAHABHARATA_SEGMENTS
    story_title = "Ramayana" if story == "ramayana" else "Mahabharata"
    kanda_names = RAMAYANA_KANDA_NAMES if story == "ramayana" else None

    idx = STORY_PROGRESS.get(story, 0)

    if idx >= len(segments):
        STORY_PROGRESS[story] = 0
        speak(
            f"Wow! We finished all 7 Kandas of the {story_title}! "
            "You are an amazing superstar listener! "
            "Say Ramayana again to start the whole adventure from the beginning!"
        )
        return

    segment_query = segments[idx]
    STORY_PROGRESS[story] = idx + 1
    chapter_num = idx + 1

    # ── Step 1: Announce Kanda ────────────────────────────────
    if kanda_names:
        kanda_label = kanda_names[idx]
        speak(f"Kanda {chapter_num} of 7 — {kanda_label}! Let me get the story ready!")
    else:
        speak(f"Chapter {chapter_num} of the {story_title}! Let me get the story ready!")

    raw_content = search(segment_query)
    if not raw_content or len(raw_content) < 50:
        raw_content = f"This is an important and exciting part of the {story_title} story."
    log.info(f"Fetched {len(raw_content)} chars for segment {idx + 1}")

    # ── Step 2: Gemini narrates ───────────────────────────────
    narration_prompt = f"""
You are BOB, a friendly storyteller for children under 8.
Here is factual content about part of the {story_title}:

{raw_content}

Tell this as an exciting, warm, child-friendly story segment.
Rules:
- Exactly 4 sentences
- Simple words a 6 year old understands
- Make it exciting and fun
- No markdown, no bullet points, plain sentences only
- Do NOT ask a question yet — just tell the story
"""
    story_segment = gemini(narration_prompt, max_tokens=250)
    speak(story_segment)
    LAST_SEGMENT[story] = story_segment

    # ── Step 3: Gemini generates question ─────────────────────
    question_prompt = f"""
You just told this story to a young child:

"{story_segment}"

Now create ONE simple question about this story to ask the child.
Rules:
- The answer must be clearly found in the story above
- Keep the question very short and simple — one sentence
- Make it fun and exciting
- No markdown, plain text only
- Return ONLY the question, nothing else
"""
    question = gemini(question_prompt, max_tokens=80)
    question = question.strip().strip('"').strip()
    if not question.endswith("?"):
        question += "?"

    # ── Step 4: Ask question ──────────────────────────────────
    speak(f"Now here is my question for you! {question}")

    # ── Step 5: Wait for answer ───────────────────────────────
    child_answer = listen_for_answer(wait_seconds=ANSWER_WAIT)

    # Check if child said stop during answer window
    if child_answer and any(k in child_answer.lower() for k in STOP_KW):
        speak("Okay superstar! We will pause the story here. Come back whenever you want to continue!")
        STORY_STOP_FLAG["active"] = False
        return

    # ── Step 6: Evaluate answer ───────────────────────────────
    if not child_answer:
        reveal_prompt = f"""
You are BOB, a friendly kids assistant.
You told this story: "{story_segment}"
You asked: "{question}"
The child did not answer within 10 seconds.

Tell the child what the answer is in 2 sentences.
Then encourage them warmly to keep listening.
Plain sentences only, no markdown.
"""
        response = gemini(reveal_prompt, max_tokens=120)
        speak(response)
    else:
        evaluate_prompt = f"""
You are BOB, a friendly kids assistant.
You told this story: "{story_segment}"
You asked: "{question}"
The child answered: "{child_answer}"

Decide if the child's answer is correct, partially correct, or wrong based on the story.
Then respond warmly in 2 to 3 sentences:
- If correct: praise enthusiastically, confirm the answer
- If partially correct: praise the effort, gently complete the answer
- If wrong: say "good try!", kindly give the correct answer from the story
Plain sentences only. No markdown. Be very warm and encouraging.
"""
        response = gemini(evaluate_prompt, max_tokens=150)
        speak(response)

    # ── Step 7: Brief pause — listen for stop — auto-continue ─
    remaining = len(segments) - STORY_PROGRESS[story]
    if remaining > 0:
        speak("Great job! Coming up next — get ready!")
        # Listen 3 seconds — if kid says stop, pause the story
        if check_stop_during_story():
            speak("Okay superstar! We will pause the story here. Come back whenever you want to continue!")
            return
        run_story_chapter(story, auto_continue=True)
    else:
        STORY_PROGRESS[story] = 0
        speak(
            f"And that was the very last Kanda of the {story_title}! "
            "All 7 Kandas done — you are an incredible superstar! "
            "Say Ramayana to hear it all over again from the beginning!"
        )

# ─────────────────────────────────────────────────────────────
# OTHER TOOLS
# ─────────────────────────────────────────────────────────────

JOKES = [
    "Why did the teddy bear say no to dessert? Because she was already stuffed!",
    "What do you call a fake noodle? An impasta!",
    "Why did the banana go to the doctor? Because it wasn't peeling well!",
    "What do you call a sleepy bull? A bulldozer!",
    "Why do fish swim in salt water? Because pepper makes them sneeze!",
    "What do you call a bear with no teeth? A gummy bear!",
    "Why did the math book look so sad? Because it had too many problems!",
    "What do elephants use to talk to each other? Elephones!",
    "What do you call a dinosaur that crashes their car? Tyrannosaurus wrecks!",
    "Why can't Elsa have a balloon? Because she will let it go!",
]


def tool_joke():
    speak(f"Hehe, get ready to giggle!  {random.choice(JOKES)}  Did that make you laugh?")


def tool_time():
    tz  = pytz.timezone("Asia/Kolkata")
    now = datetime.now(tz)
    speak(
        f"Let me check the magic clock!  "
        f"It is {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d')}.  "
        "What kind of fun should we have right now?"
    )


def tool_general_question(user_question: str):
    """Answer any question using SearchAPI content + Gemini narration."""
    speak("Great question! Let me find out for you!")

    raw = search(f"simple explanation {user_question}", num=3)

    if not raw:
        speak(
            f"Hmm, I searched everywhere but could not find a great answer right now. "
            "Let us try asking something else!"
        )
        return

    answer_prompt = f"""
You are BOB, a friendly voice assistant for children under 8.
A child asked: "{user_question}"

Here is information I found:
{raw[:1000]}

Answer the child's question in 3 simple, fun, exciting sentences.
Use words a 6 year old understands easily.
No markdown, plain sentences only. Be enthusiastic!
"""
    answer = gemini(answer_prompt, max_tokens=200)
    speak(answer)

# ─────────────────────────────────────────────────────────────
# INTENT DETECTION
# ─────────────────────────────────────────────────────────────

STOP_KW        = ["stop", "bye buddy", "goodbye", "that's all", "i want to stop",
                  "go away", "bye bob", "shut up", "be quiet", "see you later"]
JOKE_KW        = ["joke", "funny", "laugh", "giggle", "silly", "make me laugh",
                  "tell me something funny"]
TIME_KW        = ["what time", "time is it", "what is the time", "clock", "tell me the time"]
NEXT_KW        = ["next", "continue", "more", "keep going", "what happens next",
                  "go on", "next chapter", "then what", "and then", "ready", "yes go"]
RAMAYANA_KW    = ["ramayana", "hanuman", "ravana", "ayodhya", "sita rama",
                  "ram sita", "lakshmana", "lord ram", "rama story"]
MAHABHARATA_KW = ["mahabharata", "mahabharat", "pandava", "kaurava",
                  "kurukshetra", "arjuna", "drona", "bhagavad", "gita",
                  "yudhishthira", "duryodhana", "mahabharata story"]
SEARCH_KW      = ["what is", "who is", "why is", "how does", "tell me about",
                  "explain", "what are", "how do", "where is", "when did",
                  "how many", "which is", "search", "find out", "what was",
                  "who was", "why do", "how is", "what does"]
MUSIC_KW = [
    "music", "song", "play music", "play song",
    "play me a song", "play me music",
    "rhyme", "nursery rhyme"
]


def detect_intent(text: str) -> tuple:
    lower = text.lower().strip()
    if any(k in lower for k in STOP_KW):         return "stop", ""
    if any(k in lower for k in MUSIC_KW):        return "music", ""
    if any(k in lower for k in JOKE_KW):          return "joke", ""
    if any(k in lower for k in TIME_KW):          return "time", ""
    if any(k in lower for k in RAMAYANA_KW):
        if any(k in lower for k in NEXT_KW):      return "next", "ramayana"
        return "ramayana", ""
    if any(k in lower for k in MAHABHARATA_KW):
        if any(k in lower for k in NEXT_KW):      return "next", "mahabharata"
        return "mahabharata", ""
    if any(k in lower for k in NEXT_KW):          return "next", ""
    for kw in SEARCH_KW:
        if lower.startswith(kw) or f" {kw} " in f" {lower} ":
            query = lower
            for sw in SEARCH_KW:
                query = query.replace(sw, "")
            return "search", query.strip(" ?.")
    if "?" in text or len(lower.split()) >= 4:
        return "search", lower.strip("?. ")
    return "chat", lower

# ─────────────────────────────────────────────────────────────
# CONVERSATION STATE
# ─────────────────────────────────────────────────────────────

class State:
    active_story: str = ""

STATE = State()

CHAT_REPLIES = [
    "That is so cool! Tell me more, little explorer!",
    "Wow, you are so smart! What else would you like to know?",
    "I love chatting with you! Shall we hear a story or a joke?",
    "Ha, that is amazing! You always have the best ideas!",
    "You are my favourite adventure buddy! What shall we do next?",
]


def handle(user_text: str) -> str:
    """Route user speech to the right handler. Returns 'stop' or 'ok'."""
    intent, payload = detect_intent(user_text)

    if intent == "stop":
        STATE.active_story = ""
        stop_music()
        speak("Okay superstar! I will be quiet now. Come back whenever you want to play. Bye for now!")
        return "stop"

    if intent == "joke":
        tool_joke()

    elif intent == "music":
        play_music()

    elif intent == "time":
        tool_time()

    elif intent == "search":
        tool_general_question(payload or user_text)

    elif intent == "ramayana":
        STATE.active_story = "ramayana"
        run_story_chapter("ramayana")

    elif intent == "mahabharata":
        STATE.active_story = "mahabharata"
        run_story_chapter("mahabharata")

    elif intent == "next":
        story = payload or STATE.active_story
        if not story:
            speak("Which story shall we continue? Say Ramayana or Mahabharata and I will keep going!")
        else:
            STATE.active_story = story
            run_story_chapter(story)

    else:
        speak(random.choice(CHAT_REPLIES))

    return "ok"

# ─────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────

STOP_KW_SET   = set(STOP_KW)
STOP_TIME     = 0.0
STOP_DURATION = 2 * 3600
SILENCE_LIMIT = 3600

GREETING = (
    "Hello little explorer! I am BOB, your magical friend! "
    "What would you like to do today?"
)

RE_GREET = [
    "Hello again friend! I am still here. What shall we do today?",
    "I missed you! Want to continue our story or ask me something new?",
    "Oh wonderful, you are back! Shall we continue the adventure?",
    "Hey superstar! Ready for more fun? Just say the word!",
]


def main():
    global STOP_TIME

    log.info("BOB starting up...")
    print("\n" + "=" * 60)
    print("  BOB  -  Kids Voice Assistant")
    print("  SearchAPI + Gemini AI Powered")
    print()
    print("  Say 'Tell me the Ramayana story'")
    print("  Say 'Tell me the Mahabharata story'")
    print("  Say 'next' for the next chapter")
    print("  Say 'tell me a joke'")
    print("  Ask ANY question")
    print("  Say 'stop' to pause BOB for 2 hours")
    print()
    print("  STORY FLOW:")
    print("    SearchAPI fetches content")
    print("    Gemini narrates 4-sentence story segment")
    print("    Gemini creates a NEW question from the segment")
    print("    BOB asks → waits 10s → Gemini evaluates answer")
    print("    Correct → praise   Wrong → correct   Silent → reveal")
    print("    Story AUTO-CONTINUES to next chapter automatically!")
    print()
    print("  Press Ctrl+C to quit.")
    print("=" * 60 + "\n")

    speak(GREETING)
    last_activity = time.time()

    while True:
        user_text = listen(timeout=12, phrase_limit=20)
        now = time.time()

        if not user_text:
            if STOP_TIME == 0 and (now - last_activity) >= SILENCE_LIMIT:
                speak(random.choice(RE_GREET))
                last_activity = now
            continue

        last_activity = now

        if any(k in user_text.lower() for k in STOP_KW):
            STOP_TIME = now
            speak("Okay superstar! I will be quiet now. Come back whenever you want. Bye for now!")
            continue

        if STOP_TIME != 0:
            elapsed = now - STOP_TIME
            if elapsed < STOP_DURATION:
                log.info(f"Stop window — {int(STOP_DURATION - elapsed)}s remaining.")
                continue
            else:
                STOP_TIME = 0
                speak(random.choice(RE_GREET))
                continue

        handle(user_text)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        speak("Goodbye superstar! See you next time!")
        log.info("BOB shut down.")
        sys.exit(0)