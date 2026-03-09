import sqlite3
import random
import requests
import re
from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
DB = "practice.db"

GUARDIAN_URL = (
    "https://content.guardianapis.com/search"
    "?show-fields=body,headline"
    "&api-key=test&page-size=20"
    "&section=lifeandstyle|food|travel|culture|science|environment"
    "&order-by=newest"
)

def init_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_title TEXT,
                article_url TEXT,
                article_body TEXT,
                chinese_translation TEXT,
                english_back_translation TEXT,
                reference_translation TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # migrate: add column if it didn't exist yet
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        if "reference_translation" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN reference_translation TEXT")

def get_stats():
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            "SELECT date(created_at) as d FROM sessions ORDER BY d DESC"
        ).fetchall()
    import datetime as dt
    dates = [r[0] for r in rows]
    total = len(dates)
    unique_days = sorted(set(dates), reverse=True)
    streak = 0
    today = dt.date.today()
    for i, d in enumerate(unique_days):
        expected = str(today - dt.timedelta(days=i))
        if d == expected:
            streak += 1
        else:
            break
    weekday = today.weekday()
    week_start = str(today - dt.timedelta(days=weekday))
    this_week = sum(1 for d in unique_days if d >= week_start)
    return {"total": total, "streak": streak, "this_week": this_week}

def extract_passage(text, target_words=80):
    """Pick a random window of substantive paragraphs totalling ~target_words."""
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if len(p.split()) >= 20]
    if not paragraphs:
        return text[:1000]
    # start from a random paragraph (bias toward early-to-mid)
    start = random.randint(0, max(0, len(paragraphs) - 1))
    kept, count = [], 0
    for p in paragraphs[start:]:
        kept.append(p)
        count += len(p.split())
        if count >= target_words:
            break
    # if we didn't hit target, prepend earlier paragraphs
    if count < target_words and start > 0:
        for p in reversed(paragraphs[:start]):
            kept.insert(0, p)
            count += len(p.split())
            if count >= target_words:
                break
    return "\n\n".join(kept)

def fetch_article():
    try:
        resp = requests.get(GUARDIAN_URL, timeout=10)
        data = resp.json()
        results = [
            r for r in data["response"]["results"]
            if r.get("fields", {}).get("body")
        ]
        if not results:
            return None
        article = random.choice(results)
        raw = article["fields"]["body"]
        import html as html_module
        body = re.sub(r"</p>", "\n\n", raw, flags=re.IGNORECASE)
        body = re.sub(r"<[^>]+>", "", body)
        body = html_module.unescape(body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        body = extract_passage(body)
        return {
            "title": article["fields"].get("headline", article.get("webTitle", "")),
            "url": article.get("webUrl", ""),
            "body": body,
        }
    except Exception as e:
        return {"title": "Failed to load article", "url": "", "body": str(e)}

def translate_to_chinese(text):
    try:
        from deep_translator import GoogleTranslator
        # GoogleTranslator has a 5000 char limit per call
        return GoogleTranslator(source="en", target="zh-CN").translate(text[:4500])
    except Exception:
        return ""

init_db()

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    article = fetch_article()
    return templates.TemplateResponse("index.html", {"request": request, "article": article})

@app.post("/submit")
async def submit(
    article_title: str = Form(""),
    article_url: str = Form(""),
    article_body: str = Form(""),
    chinese_translation: str = Form(""),
    english_back_translation: str = Form(""),
):
    reference = translate_to_chinese(article_body)
    with sqlite3.connect(DB) as conn:
        cur = conn.execute(
            "INSERT INTO sessions (article_title, article_url, article_body, chinese_translation, english_back_translation, reference_translation) VALUES (?,?,?,?,?,?)",
            (article_title, article_url, article_body, chinese_translation, english_back_translation, reference),
        )
        session_id = cur.lastrowid
    return RedirectResponse(f"/review/{session_id}", status_code=303)

STOP_WORDS = {
    'the','a','an','is','are','was','were','be','been','being','have','has','had',
    'do','does','did','will','would','could','should','may','might','to','of','in',
    'for','on','with','at','by','from','as','into','and','but','or','not','so',
    'if','that','this','it','its','he','she','they','i','we','you','my','his',
    'her','our','their','what','who','which','just','also','very','more','than',
    'then','now','about','out','up','when','there','all','no','one','can','said',
}

def highlight_missed(original, back):
    """Highlight any word in the original that is absent from the back-translation."""
    import html
    back_words = {w.lower() for w in re.findall(r'[a-zA-Z]+', back)}

    def replace(m):
        word = m.group(0)
        if word.lower() not in back_words:
            return f'<mark class="miss">{word}</mark>'
        return word

    safe = html.escape(original)
    highlighted = re.sub(r'[a-zA-Z]+', replace, safe)
    return highlighted.replace('\n\n', '<br><br>').replace('\n', '<br>')

def text_to_html(text):
    import html
    return html.escape(text).replace('\n\n', '<br><br>').replace('\n', '<br>')

@app.get("/review/{session_id}", response_class=HTMLResponse)
async def review(request: Request, session_id: int):
    with sqlite3.connect(DB) as conn:
        row = conn.execute(
            "SELECT article_title, article_url, article_body, chinese_translation, english_back_translation, reference_translation FROM sessions WHERE id=?",
            (session_id,)
        ).fetchone()
    if not row:
        return RedirectResponse("/history")
    session = {
        "title": row[0], "url": row[1],
        "body_html": highlight_missed(row[2], row[4] or ""),
        "cn": row[3] or "—",
        "back_html": text_to_html(row[4] or ""),
        "ref": row[5],
    }
    return templates.TemplateResponse("review.html", {"request": request, "session": session})

@app.get("/practice-again/{session_id}", response_class=HTMLResponse)
async def practice_again(request: Request, session_id: int):
    with sqlite3.connect(DB) as conn:
        row = conn.execute(
            "SELECT article_title, article_url, article_body FROM sessions WHERE id=?",
            (session_id,)
        ).fetchone()
    if not row:
        return RedirectResponse("/")
    article = {"title": row[0], "url": row[1], "body": row[2]}
    return templates.TemplateResponse("index.html", {"request": request, "article": article})

@app.get("/history", response_class=HTMLResponse)
async def history(request: Request):
    with sqlite3.connect(DB) as conn:
        rows = conn.execute("""
            SELECT MAX(id) as latest_id, article_title, article_url, article_body,
                   chinese_translation, english_back_translation,
                   COUNT(*) as times, MAX(created_at) as last_date
            FROM sessions
            GROUP BY article_url
            ORDER BY last_date DESC
        """).fetchall()
    sessions = [
        {"id": r[0], "title": r[1], "url": r[2], "body": r[3],
         "cn": r[4], "en": r[5], "times": r[6], "date": r[7]}
        for r in rows
    ]
    stats = get_stats()
    return templates.TemplateResponse("history.html", {"request": request, "sessions": sessions, "stats": stats})