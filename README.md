# Translation Practice

A FastAPI web application for practicing English-Chinese-English back-translation with articles from The Guardian.

## Features

- **Fetch Articles**: Randomly selects articles from The Guardian API
- **Translation Workflow**:
  1. Read English article aloud
  2. Translate to Chinese
  3. Translate back to English without looking at original
- **Review & Compare**: Side-by-side comparison with highlighting of missed words
- **Progress Tracking**: Day streak, weekly count, and total sessions
- **History**: View all past practice sessions

## Setup

### Requirements

- Python 3.9+
- PostgreSQL database (uses NeonDB in production)

### Installation

```bash
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql://user:password@host/database?sslmode=require
GUARDIAN_API_KEY=your_guardian_api_key  # optional, defaults to "test"
```

### Database

The app automatically creates the required `sessions` table on startup.

## Running Locally

```bash
conda activate ling
uvicorn main:app --reload
```

Then open http://localhost:8000 in your browser.

## Deployment

The app is configured for Vercel deployment via `vercel.json`.

## Project Structure

```
.
├── main.py              # FastAPI application
├── requirements.txt     # Python dependencies
├── vercel.json         # Vercel deployment config
├── .env                # Environment variables (not in git)
└── templates/
    ├── index.html      # Practice page
    ├── review.html     # Review comparison page
    └── history.html    # History and stats page
```
