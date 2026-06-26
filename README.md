# EverTimeline

A small Flask app for building a personal timeline from a birthday, calendar years, monthly photo uploads, and picture messages.

## Run

```powershell
python -m pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000.

## Storage

The app creates `evertimeline.sqlite3` automatically. User accounts, birthday values, uploaded image blobs, optional photo dates, and picture messages are stored in that local SQLite database.
