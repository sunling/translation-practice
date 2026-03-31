import os
import random
import requests
import re
from pathlib import Path
from contextlib import contextmanager
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Load .env file manually (no python-dotenv dependency)
import os as _os
_env_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".env")
try:
    with open(_env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                _os.environ[key] = value
except FileNotFoundError:
    pass  # No .env file present

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")
GUARDIAN_API_KEY = os.environ.get("GUARDIAN_API_KEY", "test")

if not DATABASE_URL:
    print("WARNING: DATABASE_URL not set. Running without database.")

def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.set_client_encoding('UTF8')
    return conn

def release_conn(conn):
    conn.close()

@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_conn(conn)

GUARDIAN_URL = (
    "https://content.guardianapis.com/search"
    "?show-fields=body,headline"
    f"&api-key={GUARDIAN_API_KEY}&page-size=20"
    "&section=lifeandstyle|food|travel|culture|science|environment"
    "&order-by=newest"
)

# Initialize FastAPI with lifespan
def init_db():
    """Initialize database tables."""
    if not DATABASE_URL:
        print("WARNING: DATABASE_URL not set. Database features will not work.")
        return
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id SERIAL PRIMARY KEY,
                        article_title TEXT,
                        article_url TEXT,
                        article_body TEXT,
                        chinese_translation TEXT,
                        english_back_translation TEXT,
                        reference_translation TEXT,
                        source_type VARCHAR(20) DEFAULT 'guardian',
                        source_lang VARCHAR(10) DEFAULT 'en',
                        target_lang VARCHAR(10) DEFAULT 'en',
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                # Migrate existing rows to have source_type
                cur.execute("""
                    ALTER TABLE sessions 
                    ADD COLUMN IF NOT EXISTS source_type VARCHAR(20) DEFAULT 'guardian',
                    ADD COLUMN IF NOT EXISTS source_lang VARCHAR(10) DEFAULT 'en',
                    ADD COLUMN IF NOT EXISTS target_lang VARCHAR(10) DEFAULT 'en'
                """)
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database initialization error: {e}")

app = FastAPI()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "Invalid input. Please check your submission."}
    )

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "database": "connected" if DATABASE_URL else "not configured"
    }

def get_stats():
    if not DATABASE_URL:
        return {"total": 0, "streak": 0, "this_week": 0}
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DATE(created_at) as d FROM sessions ORDER BY d DESC")
                rows = cur.fetchall()
        import datetime as dt
        dates = [str(r[0]) for r in rows]
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
    except Exception:
        return {"total": 0, "streak": 0, "this_week": 0}

def extract_passage(text, target_words=80):
    """Pick a random window of substantive paragraphs totalling ~target_words."""
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if len(p.split()) >= 20]
    if not paragraphs:
        return text[:1000]
    start = random.randint(0, max(0, len(paragraphs) - 1))
    kept, count = [], 0
    for p in paragraphs[start:]:
        kept.append(p)
        count += len(p.split())
        if count >= target_words:
            break
    if count < target_words and start > 0:
        for p in reversed(paragraphs[:start]):
            kept.insert(0, p)
            count += len(p.split())
            if count >= target_words:
                break
    return "\n\n".join(kept)

FALLBACK_ARTICLES = [
    {
        "title": "The Art of Translation",
        "url": "https://example.com/translation",
        "body": "Translation is not merely about converting words from one language to another. It is about capturing the essence, the nuance, and the cultural context that gives meaning to the original text. A good translator must understand both the source and target cultures deeply."
    },
    {
        "title": "Learning Through Practice",
        "url": "https://example.com/practice",
        "body": "Language learning requires consistent practice. The more you engage with the language, the more natural it becomes. Reading aloud, translating, and back-translating are excellent methods for improving fluency and understanding."
    }
]

def fetch_article():
    """Fetch a random article from The Guardian API with fallback."""
    try:
        resp = requests.get(GUARDIAN_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("response", {}).get("status") != "ok":
            raise ValueError(f"API error: {data.get('response', {}).get('message', 'Unknown error')}")
        
        results = [
            r for r in data["response"]["results"]
            if r.get("fields", {}).get("body")
        ]
        if not results:
            return random.choice(FALLBACK_ARTICLES)
        
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
    except requests.RequestException:
        return random.choice(FALLBACK_ARTICLES)
    except Exception as e:
        return random.choice(FALLBACK_ARTICLES)

def translate_to_chinese(text):
    """Translate text to Chinese with error handling."""
    try:
        from deep_translator import GoogleTranslator
        result = GoogleTranslator(source="en", target="zh-CN").translate(text[:4500])
        return result if result else "(Translation service unavailable)"
    except Exception as e:
        return f"(Translation failed: {str(e)[:50]})"

# Initialize database on startup
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
    source_type: str = Form("guardian"),
    source_lang: str = Form("en"),
    target_lang: str = Form("en"),
):
    # Validation
    errors = []
    if not article_body or len(article_body.strip()) < 10:
        errors.append("Article body is required (min 10 characters)")
    if not chinese_translation or len(chinese_translation.strip()) < 5:
        errors.append("Chinese translation is required (min 5 characters)")
    if not english_back_translation or len(english_back_translation.strip()) < 10:
        errors.append("Back-translation is required (min 10 characters)")
    
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    
    # Only generate reference translation for English source
    reference = ""
    if source_lang == "en":
        reference = translate_to_chinese(article_body)
    
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sessions 
                    (article_title, article_url, article_body, chinese_translation, 
                     english_back_translation, reference_translation, source_type, source_lang, target_lang) 
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (article_title, article_url, article_body, chinese_translation, 
                 english_back_translation, reference, source_type, source_lang, target_lang),
            )
            session_id = cur.fetchone()[0]
    
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
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT article_title, article_url, article_body, chinese_translation, 
                    english_back_translation, reference_translation, source_type, source_lang, target_lang 
                    FROM sessions WHERE id=%s""",
                (session_id,)
            )
            row = cur.fetchone()
    
    if not row:
        return RedirectResponse("/history")
    session = {
        "title": row[0], "url": row[1],
        "body_html": highlight_missed(row[2], row[4] or ""),
        "cn": row[3] or "—",
        "back_html": text_to_html(row[4] or ""),
        "ref": row[5],
        "source_type": row[6],
        "source_lang": row[7] or "en",
        "target_lang": row[8] or "en",
    }
    return templates.TemplateResponse("review.html", {"request": request, "session": session})

@app.get("/practice-again/{session_id}", response_class=HTMLResponse)
async def practice_again(request: Request, session_id: int):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT article_title, article_url, article_body, reference_translation FROM sessions WHERE id=%s",
                (session_id,)
            )
            row = cur.fetchone()
    
    if not row:
        return RedirectResponse("/")
    article = {"title": row[0], "url": row[1], "body": row[2]}
    return templates.TemplateResponse("index.html", {"request": request, "article": article, "prefill_cn": row[3] or ""})

@app.get("/history", response_class=HTMLResponse)
async def history(request: Request):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH counts AS (
                    SELECT article_url, COUNT(*) as times, MAX(created_at) as last_date
                    FROM sessions GROUP BY article_url
                ),
                latest AS (
                    SELECT DISTINCT ON (article_url)
                        id, article_title, article_url, article_body,
                        chinese_translation, english_back_translation,
                        source_lang, target_lang
                    FROM sessions ORDER BY article_url, created_at DESC
                )
                SELECT l.id, l.article_title, l.article_url, l.article_body,
                       l.chinese_translation, l.english_back_translation,
                       l.source_lang, l.target_lang,
                       c.times, c.last_date
                FROM latest l JOIN counts c ON l.article_url = c.article_url
                ORDER BY c.last_date DESC
            """)
            rows = cur.fetchall()
    sessions = [
        {"id": r[0], "title": r[1], "url": r[2], "body": r[3],
         "cn": r[4], "en": r[5], "source_lang": r[6] or "en", "target_lang": r[7] or "en",
         "times": r[8], "date": r[9]}
        for r in rows
    ]
    stats = get_stats()
    return templates.TemplateResponse("history.html", {"request": request, "sessions": sessions, "stats": stats})
