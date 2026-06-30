# EverTimeline

A small Flask app for building a personal timeline from a birthday, calendar years, monthly photo uploads, and picture messages.

## Run

```powershell
python -m pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000.

Debug mode is off by default. Set `EVERTIMELINE_DEBUG=1` for local debugging.

## Configuration

Local `python app.py` runs with a development secret key. For any non-local environment, set `SECRET_KEY` to a strong random value and set `EVERTIMELINE_ENV=production`.

Password reset links are shown in the browser only for local development. To send reset links by email, configure Resend with:

```powershell
$env:RESEND_API_KEY = "re_..."
$env:RESEND_FROM_EMAIL = "EverTimeline <reset@your-verified-domain.com>"
```

Resend requires a valid API key and a verified sending domain. In production, leave local reset links disabled and use the email delivery path.

## Storage

The app creates `evertimeline.sqlite3` automatically. User accounts, birthday values, uploaded image blobs, optional photo dates, and picture messages are stored in that local SQLite database.
