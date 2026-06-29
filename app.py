from datetime import date, datetime, timedelta, timezone
from contextlib import closing
from functools import wraps
import calendar
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
import secrets
import sqlite3
import threading
import traceback
import zipfile
from xml.sax.saxutils import escape

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markupsafe import Markup, escape as html_escape
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps


BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("EVERTIMELINE_DATABASE", BASE_DIR / "evertimeline.sqlite3"))
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
JPEG_STORAGE_MIME = "image/jpeg"
JPEG_STORAGE_QUALITY = 52
JPEG_STORAGE_MAX_EDGE = 1200
COMPACT_JPEG_STORAGE_QUALITY = JPEG_STORAGE_QUALITY
COMPACT_JPEG_STORAGE_MAX_EDGE = JPEG_STORAGE_MAX_EDGE
EXIF_DATE_TAGS = (36867, 36868, 306)
TAG_CHOICES = ("private", "family", "friends", "public")
REACTION_CHOICES = ("like", "love")
DEFAULT_TAG = "private"
PRIVACY_AUDIENCE_LABELS = {
    "private": "Only you",
    "family": "Family",
    "friends": "Friends",
    "public": "All connections",
}
PRIVACY_AUDIENCE_HELP = {
    "private": "Only your account can see this item.",
    "family": "Family connections can see this item.",
    "friends": "Friend and family connections can see this item.",
    "public": "All accepted connections can see this item.",
}
CONNECTION_RELATIONS = ("friend", "family")
CONNECTION_VISIBLE_TAGS = {
    "friend": ("friends", "public"),
    "family": ("family", "friends", "public"),
}
PRIVACY_PREVIEW_OPTIONS = {
    "friend": {
        "mode": "friend",
        "label": "Friend",
        "description": "Friend connections can see friends and public items.",
        "allowed_tags": CONNECTION_VISIBLE_TAGS["friend"],
    },
    "family": {
        "mode": "family",
        "label": "Family",
        "description": "Family connections can see family, friends, and public items.",
        "allowed_tags": CONNECTION_VISIBLE_TAGS["family"],
    },
    "public": {
        "mode": "public",
        "label": "All connections",
        "description": "All accepted connections can see public items.",
        "allowed_tags": ("public",),
    },
}
PASSWORD_RESET_TTL = timedelta(hours=1)
CSRF_SESSION_KEY = "_csrf_token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
LOCAL_ENVIRONMENTS = {"development", "dev", "local", "test"}
DEFAULT_DEV_SECRET_KEY = "dev-change-me"
MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
BACKUP_FORMAT = "evertimeline.account_backup"
BACKUP_FORMAT_VERSION = 1
BACKUP_MANIFEST_NAME = "evertimeline-backup.json"
DEFAULT_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
ADMIN_USERNAME = "Daniel"
SQLITE_BUSY_TIMEOUT_MS = 30000
IMAGE_CONVERSION_COMMIT_INTERVAL = 5
JOB_STATUSES = ("queued", "running", "succeeded", "failed")
JOB_KINDS = ("convert_images", "compact_images", "vacuum_database")


def format_file_size(byte_count):
    value = float(byte_count or 0)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "bytes":
                return f"{int(value)} bytes"
            return f"{value:.1f} {unit}"
        value /= 1024


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def app_environment():
    return (os.environ.get("EVERTIMELINE_ENV") or os.environ.get("FLASK_ENV") or "").strip().lower()


def is_local_development():
    environment = app_environment()
    if environment:
        return environment in LOCAL_ENVIRONMENTS
    return __name__ == "__main__" or env_flag("EVERTIMELINE_SKIP_DB_INIT")


def configured_secret_key():
    secret_key = os.environ.get("SECRET_KEY")
    if secret_key:
        return secret_key
    if is_local_development():
        return DEFAULT_DEV_SECRET_KEY
    raise RuntimeError("SECRET_KEY must be set outside local development.")


def run_debug_enabled():
    return env_flag("EVERTIMELINE_DEBUG") or env_flag("FLASK_DEBUG")


def configured_max_upload_bytes():
    value = os.environ.get("EVERTIMELINE_MAX_UPLOAD_MB")
    if not value:
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        megabytes = int(value)
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES
    if megabytes <= 0:
        return DEFAULT_MAX_UPLOAD_BYTES
    return megabytes * 1024 * 1024


def human_readable_bytes(byte_count):
    if byte_count >= 1024 * 1024 * 1024:
        return f"{byte_count / (1024 * 1024 * 1024):g} GB"
    if byte_count >= 1024 * 1024:
        return f"{byte_count / (1024 * 1024):g} MB"
    if byte_count >= 1024:
        return f"{byte_count / 1024:g} KB"
    return f"{byte_count} bytes"


app = Flask(__name__)
app.config.update(
    SECRET_KEY=configured_secret_key(),
    MAX_CONTENT_LENGTH=configured_max_upload_bytes(),
    CSRF_PROTECT=True,
    LOCAL_PASSWORD_RESET_LINKS=is_local_development(),
    RUN_JOBS_INLINE=False,
)


def csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_field():
    token = html_escape(csrf_token())
    return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(error):
    max_size = human_readable_bytes(app.config["MAX_CONTENT_LENGTH"])
    flash(
        f"That upload is too large. The current limit is {max_size} per request. Try fewer or smaller files.",
        "error",
    )
    target = request.path if request.path.startswith("/") else url_for("timeline")
    return redirect(target), 303


@app.before_request
def validate_csrf_token():
    if request.method in SAFE_METHODS or not app.config.get("CSRF_PROTECT", True):
        return

    expected_token = session.get(CSRF_SESSION_KEY)
    submitted_token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if (
        not expected_token
        or not submitted_token
        or not secrets.compare_digest(expected_token, submitted_token)
    ):
        abort(400)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
        g.db.row_factory = sqlite3.Row
        configure_db_connection(g.db)
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def configure_db_connection(db):
    db.execute("PRAGMA foreign_keys = ON")
    db.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    try:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.OperationalError:
        pass


@app.context_processor
def inject_tag_choices():
    notification_count = 0
    if getattr(g, "user", None) is not None:
        notification_count = get_notification_count(get_db())

    def static_version(filename):
        try:
            return int((BASE_DIR / "static" / filename).stat().st_mtime)
        except OSError:
            return 0

    def privacy_preview():
        return current_privacy_preview()

    def preview_url(endpoint, **values):
        preview = current_privacy_preview()
        if preview is not None and "preview" not in values:
            values["preview"] = preview["mode"]
        return url_for(endpoint, **values)

    def preview_mode_url(mode=None):
        endpoint = request.endpoint or "timeline"
        values = dict(request.view_args or {})
        if mode:
            values["preview"] = mode
        return url_for(endpoint, **values)

    return {
        "tag_choices": TAG_CHOICES,
        "default_tag": DEFAULT_TAG,
        "privacy_labels": PRIVACY_AUDIENCE_LABELS,
        "privacy_help": PRIVACY_AUDIENCE_HELP,
        "privacy_preview": privacy_preview(),
        "privacy_preview_options": PRIVACY_PREVIEW_OPTIONS,
        "preview_url": preview_url,
        "preview_mode_url": preview_mode_url,
        "notification_count": notification_count,
        "static_version": static_version,
        "is_admin_user": current_user_is_admin,
        "csrf_token": csrf_token,
        "csrf_field": csrf_field,
    }


def init_db():
    with closing(sqlite3.connect(DATABASE, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)) as db:
        db.row_factory = sqlite3.Row
        configure_db_connection(db)
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                first_name TEXT NOT NULL DEFAULT '',
                last_name TEXT NOT NULL DEFAULT '',
                email TEXT,
                password_hash TEXT NOT NULL,
                birthday TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                original_filename TEXT,
                title TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                image_hash TEXT,
                mime_type TEXT NOT NULL,
                image_data BLOB NOT NULL,
                photo_date TEXT,
                location_name TEXT,
                latitude REAL,
                longitude REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                photo_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (photo_id) REFERENCES photos (id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS text_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                body TEXT NOT NULL,
                entry_date TEXT,
                location_name TEXT,
                latitude REAL,
                longitude REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS text_entry_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (entry_id) REFERENCES text_entries (id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS item_reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item_kind TEXT NOT NULL CHECK (item_kind IN ('photo', 'text')),
                item_id INTEGER NOT NULL,
                reaction TEXT NOT NULL CHECK (reaction IN ('like', 'love')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (user_id, item_kind, item_id),
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS message_notification_reads (
                user_id INTEGER NOT NULL,
                message_kind TEXT NOT NULL CHECK (message_kind IN ('photo', 'text')),
                message_id INTEGER NOT NULL,
                read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, message_kind, message_id),
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reaction_notification_reads (
                user_id INTEGER NOT NULL,
                reaction_id INTEGER NOT NULL,
                read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, reaction_id),
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (reaction_id) REFERENCES item_reactions (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (user_id, name),
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS photo_tags (
                photo_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (photo_id, tag_id),
                FOREIGN KEY (photo_id) REFERENCES photos (id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS text_entry_tags (
                entry_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (entry_id, tag_id),
                FOREIGN KEY (entry_id) REFERENCES text_entries (id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (user_id, name),
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS photo_people (
                photo_id INTEGER NOT NULL,
                person_id INTEGER NOT NULL,
                PRIMARY KEY (photo_id, person_id),
                FOREIGN KEY (photo_id) REFERENCES photos (id) ON DELETE CASCADE,
                FOREIGN KEY (person_id) REFERENCES people (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS text_entry_people (
                entry_id INTEGER NOT NULL,
                person_id INTEGER NOT NULL,
                PRIMARY KEY (entry_id, person_id),
                FOREIGN KEY (entry_id) REFERENCES text_entries (id) ON DELETE CASCADE,
                FOREIGN KEY (person_id) REFERENCES people (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS saved_timeline_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                query_text TEXT NOT NULL DEFAULT '',
                item_kind TEXT NOT NULL DEFAULT '' CHECK (item_kind IN ('', 'photo', 'text')),
                people_text TEXT NOT NULL DEFAULT '',
                location_text TEXT NOT NULL DEFAULT '',
                privacy_tag TEXT NOT NULL DEFAULT '',
                date_start TEXT NOT NULL DEFAULT '',
                date_end TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS timeline_stories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                subtitle TEXT NOT NULL DEFAULT '',
                source_mode TEXT NOT NULL DEFAULT 'search' CHECK (source_mode IN ('search', 'collections')),
                filter_payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS photo_import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS photo_import_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                original_filename TEXT,
                mime_type TEXT NOT NULL,
                image_data BLOB NOT NULL,
                image_hash TEXT NOT NULL,
                detected_date TEXT,
                detected_source TEXT NOT NULL DEFAULT '',
                duplicate_photo_id INTEGER,
                duplicate_import_item_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (batch_id) REFERENCES photo_import_batches (id) ON DELETE CASCADE,
                FOREIGN KEY (duplicate_photo_id) REFERENCES photos (id) ON DELETE SET NULL,
                FOREIGN KEY (duplicate_import_item_id) REFERENCES photo_import_items (id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS connection_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                relation TEXT NOT NULL CHECK (relation IN ('friend', 'family')),
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'declined')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                responded_at TEXT,
                CHECK (requester_id <> recipient_id),
                FOREIGN KEY (requester_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (recipient_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'family', 'friends', 'public')),
                cover_item_kind TEXT CHECK (cover_item_kind IN ('photo', 'text')),
                cover_item_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chapter_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id INTEGER NOT NULL,
                item_kind TEXT NOT NULL CHECK (item_kind IN ('photo', 'text')),
                item_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (chapter_id, item_kind, item_id),
                FOREIGN KEY (chapter_id) REFERENCES chapters (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chapter_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id INTEGER NOT NULL,
                inviter_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'declined')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                responded_at TEXT,
                UNIQUE (chapter_id, recipient_id),
                CHECK (inviter_id <> recipient_id),
                FOREIGN KEY (chapter_id) REFERENCES chapters (id) ON DELETE CASCADE,
                FOREIGN KEY (inviter_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (recipient_id) REFERENCES users (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('convert_images', 'compact_images', 'vacuum_database')),
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
                progress_current INTEGER NOT NULL DEFAULT 0,
                progress_total INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );
            """
        )
        ensure_user_profile_columns(db)
        ensure_photo_columns(db)
        ensure_chapter_columns(db)
        ensure_timeline_location_columns(db)
        ensure_photo_import_columns(db)
        db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique
            ON users (email)
            WHERE email IS NOT NULL
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS connection_requests_requester_status
            ON connection_requests (requester_id, status)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS connection_requests_recipient_status
            ON connection_requests (recipient_id, status)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS password_reset_tokens_user_used
            ON password_reset_tokens (user_id, used_at)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS text_entry_messages_entry_created
            ON text_entry_messages (entry_id, created_at)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS item_reactions_item
            ON item_reactions (item_kind, item_id, reaction)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS message_notification_reads_user_kind
            ON message_notification_reads (user_id, message_kind)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS reaction_notification_reads_user
            ON reaction_notification_reads (user_id, reaction_id)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS chapters_user_created
            ON chapters (user_id, created_at)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS chapter_items_chapter_position
            ON chapter_items (chapter_id, position, id)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS people_user_name
            ON people (user_id, name)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS photo_people_person
            ON photo_people (person_id, photo_id)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS text_entry_people_person
            ON text_entry_people (person_id, entry_id)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS saved_timeline_views_user_updated
            ON saved_timeline_views (user_id, updated_at, created_at)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS timeline_stories_user_updated
            ON timeline_stories (user_id, updated_at, created_at)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS chapter_invites_recipient_status
            ON chapter_invites (recipient_id, status, created_at)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS chapter_invites_chapter_status
            ON chapter_invites (chapter_id, status, recipient_id)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS photos_user_image_hash
            ON photos (user_id, image_hash)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS photo_import_batches_user_created
            ON photo_import_batches (user_id, created_at)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS photo_import_items_batch
            ON photo_import_items (batch_id, id)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS jobs_user_created
            ON jobs (user_id, created_at, id)
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS jobs_status_created
            ON jobs (status, created_at, id)
            """
        )
        db.commit()


def ensure_user_profile_columns(db):
    columns = {
        row[1]
        for row in db.execute("PRAGMA table_info(users)").fetchall()
    }
    migrations = {
        "first_name": "ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT ''",
        "last_name": "ALTER TABLE users ADD COLUMN last_name TEXT NOT NULL DEFAULT ''",
        "email": "ALTER TABLE users ADD COLUMN email TEXT",
    }
    for column_name, statement in migrations.items():
        if column_name not in columns:
            db.execute(statement)


def ensure_photo_columns(db):
    columns = {
        row[1]
        for row in db.execute("PRAGMA table_info(photos)").fetchall()
    }
    migrations = {
        "title": "ALTER TABLE photos ADD COLUMN title TEXT NOT NULL DEFAULT ''",
        "caption": "ALTER TABLE photos ADD COLUMN caption TEXT NOT NULL DEFAULT ''",
        "image_hash": "ALTER TABLE photos ADD COLUMN image_hash TEXT",
    }
    for column_name, statement in migrations.items():
        if column_name not in columns:
            db.execute(statement)
    backfill_photo_hashes(db)


def ensure_chapter_columns(db):
    columns = {
        row[1]
        for row in db.execute("PRAGMA table_info(chapters)").fetchall()
    }
    migrations = {
        "visibility": "ALTER TABLE chapters ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'",
        "cover_item_kind": "ALTER TABLE chapters ADD COLUMN cover_item_kind TEXT",
        "cover_item_id": "ALTER TABLE chapters ADD COLUMN cover_item_id INTEGER",
    }
    for column_name, statement in migrations.items():
        if column_name not in columns:
            db.execute(statement)


def ensure_table_columns(db, table_name, migrations):
    columns = {
        row[1]
        for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, statement in migrations.items():
        if column_name not in columns:
            db.execute(statement)


def ensure_timeline_location_columns(db):
    location_migrations = {
        "location_name": "ADD COLUMN location_name TEXT",
        "latitude": "ADD COLUMN latitude REAL",
        "longitude": "ADD COLUMN longitude REAL",
    }
    for table_name in ("photos", "text_entries"):
        ensure_table_columns(
            db,
            table_name,
            {
                column_name: f"ALTER TABLE {table_name} {statement}"
                for column_name, statement in location_migrations.items()
            },
        )


def ensure_photo_import_columns(db):
    ensure_table_columns(
        db,
        "photo_import_items",
        {
            "duplicate_photo_id": "ALTER TABLE photo_import_items ADD COLUMN duplicate_photo_id INTEGER",
            "duplicate_import_item_id": "ALTER TABLE photo_import_items ADD COLUMN duplicate_import_item_id INTEGER",
        },
    )


@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        g.user = get_db().execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


def birthday_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(**kwargs):
        if not g.user["birthday"]:
            return redirect(url_for("birthday"))
        return view(**kwargs)

    return wrapped_view


def parse_iso_date(value, field_name):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a valid date.")


def timeline_years_for_user(user):
    birthday = parse_iso_date(user["birthday"], "Birthday")
    return range(birthday.year, date.today().year + 1)


def user_years():
    return timeline_years_for_user(g.user)


def validate_year_month_for_user(user, year, month):
    years = timeline_years_for_user(user)
    if year not in years:
        abort(404)
    if month < 1 or month > 12:
        abort(404)


def validate_year_month(year, month):
    validate_year_month_for_user(g.user, year, month)


def items_before_birthday_query(date_column):
    return f"""
        user_id = ?
        AND (
            year < ?
            OR (year = ? AND month < ?)
            OR ({date_column} IS NOT NULL AND {date_column} < ?)
        )
    """


def load_items_before_birthday(db, user_id, birthday_date):
    params = (
        user_id,
        birthday_date.year,
        birthday_date.year,
        birthday_date.month,
        birthday_date.isoformat(),
    )
    photo_rows = db.execute(
        f"""
        SELECT id, year, month, photo_date AS item_date, COALESCE(NULLIF(title, ''), original_filename, 'Photo') AS title
        FROM photos
        WHERE {items_before_birthday_query("photo_date")}
        ORDER BY year ASC, month ASC, id ASC
        """,
        params,
    ).fetchall()
    text_rows = db.execute(
        f"""
        SELECT id, year, month, entry_date AS item_date, 'Text entry' AS title
        FROM text_entries
        WHERE {items_before_birthday_query("entry_date")}
        ORDER BY year ASC, month ASC, id ASC
        """,
        params,
    ).fetchall()
    return photo_rows, text_rows


def birthday_deletion_summary(db, user_id, birthday_date):
    photo_rows, text_rows = load_items_before_birthday(db, user_id, birthday_date)
    periods = {}
    for row in [*photo_rows, *text_rows]:
        key = (row["year"], row["month"])
        periods[key] = periods.get(key, 0) + 1
    affected_periods = [
        {
            "label": f"{MONTH_NAMES[month - 1]} {year}",
            "count": count,
        }
        for (year, month), count in sorted(periods.items())
    ]
    return {
        "photo_count": len(photo_rows),
        "text_count": len(text_rows),
        "total_count": len(photo_rows) + len(text_rows),
        "affected_periods": affected_periods,
    }


def delete_rows_by_ids(db, statement, ids, prefix_params=(), suffix_params=()):
    if not ids:
        return
    placeholders = ",".join(["?"] * len(ids))
    db.execute(
        statement.format(placeholders=placeholders),
        tuple(prefix_params) + tuple(ids) + tuple(suffix_params),
    )


def delete_items_before_birthday(db, user_id, birthday_date):
    photo_rows, text_rows = load_items_before_birthday(db, user_id, birthday_date)
    photo_ids = [row["id"] for row in photo_rows]
    text_ids = [row["id"] for row in text_rows]

    delete_rows_by_ids(
        db,
        """
        DELETE FROM message_notification_reads
        WHERE message_kind = 'photo'
          AND message_id IN (
            SELECT id FROM messages WHERE photo_id IN ({placeholders})
          )
        """,
        photo_ids,
    )
    delete_rows_by_ids(
        db,
        """
        DELETE FROM message_notification_reads
        WHERE message_kind = 'text'
          AND message_id IN (
            SELECT id FROM text_entry_messages WHERE entry_id IN ({placeholders})
          )
        """,
        text_ids,
    )
    delete_rows_by_ids(
        db,
        """
        DELETE FROM reaction_notification_reads
        WHERE reaction_id IN (
            SELECT id FROM item_reactions
            WHERE item_kind = 'photo' AND item_id IN ({placeholders})
        )
        """,
        photo_ids,
    )
    delete_rows_by_ids(
        db,
        """
        DELETE FROM reaction_notification_reads
        WHERE reaction_id IN (
            SELECT id FROM item_reactions
            WHERE item_kind = 'text' AND item_id IN ({placeholders})
        )
        """,
        text_ids,
    )
    delete_rows_by_ids(
        db,
        "DELETE FROM item_reactions WHERE item_kind = 'photo' AND item_id IN ({placeholders})",
        photo_ids,
    )
    delete_rows_by_ids(
        db,
        "DELETE FROM item_reactions WHERE item_kind = 'text' AND item_id IN ({placeholders})",
        text_ids,
    )
    delete_rows_by_ids(
        db,
        """
        DELETE FROM chapter_items
        WHERE item_kind = 'photo'
          AND item_id IN ({placeholders})
          AND chapter_id IN (SELECT id FROM chapters WHERE user_id = ?)
        """,
        photo_ids,
        suffix_params=(user_id,),
    )
    delete_rows_by_ids(
        db,
        """
        DELETE FROM chapter_items
        WHERE item_kind = 'text'
          AND item_id IN ({placeholders})
          AND chapter_id IN (SELECT id FROM chapters WHERE user_id = ?)
        """,
        text_ids,
        suffix_params=(user_id,),
    )
    delete_rows_by_ids(
        db,
        "DELETE FROM photos WHERE user_id = ? AND id IN ({placeholders})",
        photo_ids,
        prefix_params=(user_id,),
    )
    delete_rows_by_ids(
        db,
        "DELETE FROM text_entries WHERE user_id = ? AND id IN ({placeholders})",
        text_ids,
        prefix_params=(user_id,),
    )
    return {
        "photo_count": len(photo_ids),
        "text_count": len(text_ids),
        "total_count": len(photo_ids) + len(text_ids),
    }


def normalize_email(value):
    return (value or "").strip().lower()


def normalize_photo_title(value):
    return " ".join((value or "").strip().split())[:120]


def normalize_photo_caption(value):
    return (value or "").strip()[:2000]


def photo_display_title(photo):
    return (photo["title"] or "").strip() or photo["original_filename"] or "Photo"


def item_has_location(item):
    location_name = item["location_name"] if "location_name" in item.keys() else ""
    latitude = item["latitude"] if "latitude" in item.keys() else None
    longitude = item["longitude"] if "longitude" in item.keys() else None
    return bool(
        (location_name or "").strip()
        or latitude is not None
        or longitude is not None
    )


def guided_prompt(label, text, target):
    return {
        "label": label,
        "text": text,
        "target": target,
    }


def guided_prompts_for_item(kind, item, people=None):
    people = people or []
    prompts = []
    if kind == "photo":
        if not (item["caption"] or "").strip():
            prompts.append(
                guided_prompt(
                    "What do you remember most?",
                    "What I remember most about this moment is ",
                    "caption",
                )
            )
        if not people:
            prompts.append(
                guided_prompt(
                    "Who was there?",
                    "Add the people who were part of this memory.",
                    "people",
                )
            )
        if not item_has_location(item):
            prompts.append(
                guided_prompt(
                    "Where was this?",
                    "Add the city, venue, or place tied to this memory.",
                    "location",
                )
            )
        prompts.append(
            guided_prompt(
                "What happened before or after?",
                "Before or after this photo, ",
                "caption",
            )
        )
    else:
        body = (item["body"] or "").strip()
        if len(body) < 140:
            prompts.append(
                guided_prompt(
                    "Add why it mattered",
                    "\n\nWhy this memory matters: ",
                    "body",
                )
            )
        if not people:
            prompts.append(
                guided_prompt(
                    "Who was part of it?",
                    "Add the people who were part of this memory.",
                    "people",
                )
            )
        if not item_has_location(item):
            prompts.append(
                guided_prompt(
                    "Where did it happen?",
                    "Add the city, venue, or place tied to this memory.",
                    "location",
                )
            )
        prompts.append(
            guided_prompt(
                "What happened next?",
                "\n\nWhat happened next: ",
                "body",
            )
        )
    return prompts[:4]


def photo_image_hash(image_data):
    return hashlib.sha256(image_data).hexdigest()


def storage_jpeg_from_image(
    image_data,
    quality=JPEG_STORAGE_QUALITY,
    max_edge=JPEG_STORAGE_MAX_EDGE,
):
    try:
        with Image.open(io.BytesIO(image_data)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                rgba_image = image.convert("RGBA")
                background = Image.new("RGB", rgba_image.size, (255, 255, 255))
                background.paste(rgba_image, mask=rgba_image.getchannel("A"))
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")

            if max(image.size) > max_edge:
                image.thumbnail(
                    (max_edge, max_edge),
                    Image.Resampling.LANCZOS,
                )

            buffer = io.BytesIO()
            image.save(
                buffer,
                format="JPEG",
                quality=quality,
                subsampling="4:2:0",
                optimize=True,
                progressive=True,
            )
            return buffer.getvalue()
    except Exception as exc:
        raise ValueError("Image could not be converted to JPEG.") from exc


def current_user_is_admin():
    return getattr(g, "user", None) is not None and g.user["username"] == ADMIN_USERNAME


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(**kwargs):
        if not current_user_is_admin():
            abort(404)
        return view(**kwargs)

    return wrapped_view


def admin_image_storage_summary(db):
    photos = db.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN mime_type = ? THEN 1 ELSE 0 END) AS jpeg_count
        FROM photos
        """,
        (JPEG_STORAGE_MIME,),
    ).fetchone()
    imports = db.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN mime_type = ? THEN 1 ELSE 0 END) AS jpeg_count
        FROM photo_import_items
        """,
        (JPEG_STORAGE_MIME,),
    ).fetchone()
    photo_total = photos["total_count"] or 0
    import_total = imports["total_count"] or 0
    photo_jpeg = photos["jpeg_count"] or 0
    import_jpeg = imports["jpeg_count"] or 0
    image_bytes = db.execute(
        """
        SELECT
            COALESCE((SELECT SUM(LENGTH(image_data)) FROM photos), 0) +
            COALESCE((SELECT SUM(LENGTH(image_data)) FROM photo_import_items), 0) AS total_bytes
        """
    ).fetchone()["total_bytes"]
    page_size = db.execute("PRAGMA page_size").fetchone()[0]
    page_count = db.execute("PRAGMA page_count").fetchone()[0]
    freelist_count = db.execute("PRAGMA freelist_count").fetchone()[0]
    database_size = page_size * page_count
    reclaimable_size = page_size * freelist_count
    return {
        "photo_total": photo_total,
        "photo_jpeg": photo_jpeg,
        "photo_non_jpeg": photo_total - photo_jpeg,
        "import_total": import_total,
        "import_jpeg": import_jpeg,
        "import_non_jpeg": import_total - import_jpeg,
        "image_size": image_bytes,
        "image_size_label": format_file_size(image_bytes),
        "database_size": database_size,
        "database_size_label": format_file_size(database_size),
        "reclaimable_size": reclaimable_size,
        "reclaimable_size_label": format_file_size(reclaimable_size),
    }


def convert_image_rows_to_jpeg(
    db,
    table_name,
    id_column,
    hash_column=True,
    include_jpeg=False,
    quality=JPEG_STORAGE_QUALITY,
    max_edge=JPEG_STORAGE_MAX_EDGE,
    progress_callback=None,
):
    where_clause = "" if include_jpeg else "WHERE mime_type IS NULL OR mime_type != ?"
    params = () if include_jpeg else (JPEG_STORAGE_MIME,)
    rows = db.execute(
        f"""
        SELECT {id_column} AS row_id
        FROM {table_name}
        {where_clause}
        ORDER BY {id_column} ASC
        """,
        params,
    ).fetchall()
    converted_count = 0
    skipped_count = 0
    saved_bytes = 0
    processed_count = 0
    for row in rows:
        image_row = db.execute(
            f"""
            SELECT image_data
            FROM {table_name}
            WHERE {id_column} = ?
            """,
            (row["row_id"],),
        ).fetchone()
        if image_row is None:
            skipped_count += 1
            processed_count += 1
            if progress_callback:
                progress_callback(processed_count)
            continue

        original_size = len(image_row["image_data"])
        try:
            image_data = storage_jpeg_from_image(
                image_row["image_data"],
                quality=quality,
                max_edge=max_edge,
            )
        except ValueError:
            skipped_count += 1
            processed_count += 1
            if progress_callback:
                progress_callback(processed_count)
            continue
        if len(image_data) >= original_size and include_jpeg:
            skipped_count += 1
            processed_count += 1
            if progress_callback:
                progress_callback(processed_count)
            continue

        if hash_column:
            db.execute(
                f"""
                UPDATE {table_name}
                SET image_data = ?, mime_type = ?, image_hash = ?
                WHERE {id_column} = ?
                """,
                (image_data, JPEG_STORAGE_MIME, photo_image_hash(image_data), row["row_id"]),
            )
        else:
            db.execute(
                f"""
                UPDATE {table_name}
                SET image_data = ?, mime_type = ?
                WHERE {id_column} = ?
                """,
                (image_data, JPEG_STORAGE_MIME, row["row_id"]),
            )
        converted_count += 1
        saved_bytes += max(original_size - len(image_data), 0)
        processed_count += 1
        if progress_callback:
            progress_callback(processed_count)
        if converted_count % IMAGE_CONVERSION_COMMIT_INTERVAL == 0:
            db.commit()
    return {"converted": converted_count, "skipped": skipped_count, "saved_bytes": saved_bytes}


def count_image_rows_to_jpeg(db, table_name, id_column, include_jpeg=False):
    where_clause = "" if include_jpeg else "WHERE mime_type IS NULL OR mime_type != ?"
    params = () if include_jpeg else (JPEG_STORAGE_MIME,)
    return db.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM {table_name}
        {where_clause}
        """,
        params,
    ).fetchone()["count"]


def count_all_database_images_to_jpeg(db, include_jpeg=False):
    return (
        count_image_rows_to_jpeg(db, "photos", "id", include_jpeg)
        + count_image_rows_to_jpeg(db, "photo_import_items", "id", include_jpeg)
    )


def convert_all_database_images_to_jpeg(
    db,
    include_jpeg=False,
    quality=JPEG_STORAGE_QUALITY,
    max_edge=JPEG_STORAGE_MAX_EDGE,
    progress_callback=None,
):
    processed_total = 0

    def table_progress(processed_count):
        if progress_callback:
            progress_callback(processed_total + processed_count)

    photo_total = count_image_rows_to_jpeg(db, "photos", "id", include_jpeg)
    photo_result = convert_image_rows_to_jpeg(
        db,
        "photos",
        "id",
        True,
        include_jpeg=include_jpeg,
        quality=quality,
        max_edge=max_edge,
        progress_callback=table_progress,
    )
    processed_total += photo_total
    import_result = convert_image_rows_to_jpeg(
        db,
        "photo_import_items",
        "id",
        True,
        include_jpeg=include_jpeg,
        quality=quality,
        max_edge=max_edge,
        progress_callback=table_progress,
    )
    return {
        "converted": photo_result["converted"] + import_result["converted"],
        "skipped": photo_result["skipped"] + import_result["skipped"],
        "photos_converted": photo_result["converted"],
        "import_items_converted": import_result["converted"],
        "saved_bytes": photo_result["saved_bytes"] + import_result["saved_bytes"],
    }


def vacuum_database(db):
    before_size = DATABASE.stat().st_size if DATABASE.exists() else 0
    db.commit()
    try:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        pass
    db.execute("VACUUM")
    try:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        pass
    after_size = DATABASE.stat().st_size if DATABASE.exists() else 0
    return {
        "before_size": before_size,
        "after_size": after_size,
        "saved_bytes": max(before_size - after_size, 0),
    }


def json_dumps(data):
    return json.dumps(data or {}, sort_keys=True)


def json_loads_object(value):
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def utc_timestamp():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat(sep=" ")


def job_row_to_dict(job):
    result = json_loads_object(job["result_json"])
    return {
        "id": job["id"],
        "user_id": job["user_id"],
        "kind": job["kind"],
        "title": job["title"],
        "status": job["status"],
        "progress_current": job["progress_current"],
        "progress_total": job["progress_total"],
        "progress_percent": job_progress_percent(job),
        "message": job["message"],
        "result": result,
        "error": job["error"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "updated_at": job["updated_at"],
        "result_summary": job_result_summary(job["kind"], result),
    }


def job_progress_percent(job):
    total = job["progress_total"] or 0
    if total <= 0:
        return 100 if job["status"] == "succeeded" else 0
    return min(100, round((job["progress_current"] or 0) * 100 / total))


def job_result_summary(kind, result):
    if not result:
        return ""
    if kind == "convert_images":
        return (
            f"Converted {result.get('converted', 0)} image rows "
            f"({result.get('photos_converted', 0)} photos, "
            f"{result.get('import_items_converted', 0)} import items)."
        )
    if kind == "compact_images":
        return (
            f"Compacted {result.get('converted', 0)} image rows and saved "
            f"{format_file_size(result.get('saved_bytes', 0))} in image data. "
            f"Database is now {format_file_size(result.get('database_size', 0))}."
        )
    if kind == "vacuum_database":
        return (
            f"Reclaimed {format_file_size(result.get('saved_bytes', 0))}. "
            f"Database is now {format_file_size(result.get('after_size', 0))}."
        )
    return ""


def update_job(db, job_id, **fields):
    allowed_fields = {
        "status",
        "progress_current",
        "progress_total",
        "message",
        "result_json",
        "error",
        "started_at",
        "finished_at",
        "updated_at",
    }
    updates = {
        key: value
        for key, value in fields.items()
        if key in allowed_fields
    }
    if "updated_at" not in updates:
        updates["updated_at"] = utc_timestamp()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    db.execute(
        f"UPDATE jobs SET {assignments} WHERE id = ?",
        (*updates.values(), job_id),
    )
    db.commit()


def get_job(db, job_id):
    return db.execute(
        "SELECT * FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()


def recent_admin_jobs(db, limit=8):
    return [
        job_row_to_dict(row)
        for row in db.execute(
            """
            SELECT *
            FROM jobs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    ]


def enqueue_job(user_id, kind, title, payload=None):
    if kind not in JOB_KINDS:
        raise ValueError("Unknown job kind.")
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO jobs (user_id, kind, title, payload_json)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, kind, title, json_dumps(payload)),
    )
    db.commit()
    job_id = cursor.lastrowid
    if app.config.get("RUN_JOBS_INLINE"):
        run_job(job_id)
    else:
        thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
        thread.start()
    return job_id


def run_job(job_id):
    with closing(sqlite3.connect(DATABASE, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)) as db:
        db.row_factory = sqlite3.Row
        configure_db_connection(db)
        job = get_job(db, job_id)
        if job is None or job["status"] != "queued":
            return
        now = utc_timestamp()
        update_job(
            db,
            job_id,
            status="running",
            started_at=now,
            progress_current=0,
            progress_total=0,
            message="Starting...",
            error="",
        )
        try:
            result = run_job_payload(db, job_id, job["kind"], json_loads_object(job["payload_json"]))
        except Exception as exc:
            update_job(
                db,
                job_id,
                status="failed",
                finished_at=utc_timestamp(),
                message="Job failed.",
                error=f"{exc}\n{traceback.format_exc(limit=8)}",
            )
            return
        update_job(
            db,
            job_id,
            status="succeeded",
            finished_at=utc_timestamp(),
            progress_current=result.get("progress_current", result.get("progress_total", 1)),
            progress_total=result.get("progress_total", result.get("progress_current", 1)),
            message=result.get("message", "Done."),
            result_json=json_dumps(result.get("result", {})),
            error="",
        )


def run_job_payload(db, job_id, kind, payload):
    if kind == "convert_images":
        return run_convert_images_job(db, job_id, include_jpeg=False)
    if kind == "compact_images":
        return run_convert_images_job(
            db,
            job_id,
            include_jpeg=True,
            quality=COMPACT_JPEG_STORAGE_QUALITY,
            max_edge=COMPACT_JPEG_STORAGE_MAX_EDGE,
            vacuum_after=True,
        )
    if kind == "vacuum_database":
        return run_vacuum_job(db, job_id)
    raise ValueError("Unknown job kind.")


def run_convert_images_job(
    db,
    job_id,
    include_jpeg=False,
    quality=JPEG_STORAGE_QUALITY,
    max_edge=JPEG_STORAGE_MAX_EDGE,
    vacuum_after=False,
):
    total = count_all_database_images_to_jpeg(db, include_jpeg=include_jpeg)
    message = "Compacting image storage..." if include_jpeg else "Converting images to JPEG..."
    update_job(db, job_id, progress_total=total, message=message)

    def progress_callback(processed_count):
        update_job(
            db,
            job_id,
            progress_current=processed_count,
            progress_total=total,
            message=message,
        )

    result = convert_all_database_images_to_jpeg(
        db,
        include_jpeg=include_jpeg,
        quality=quality,
        max_edge=max_edge,
        progress_callback=progress_callback,
    )
    db.commit()
    if vacuum_after:
        update_job(
            db,
            job_id,
            progress_current=total,
            progress_total=total + 1,
            message="Reclaiming database space...",
        )
        vacuum_result = vacuum_database(db)
        result["database_size"] = vacuum_result["after_size"]
        result["vacuum_saved_bytes"] = vacuum_result["saved_bytes"]
        total += 1
    final_message = job_result_summary("compact_images" if include_jpeg else "convert_images", result)
    return {
        "progress_current": total,
        "progress_total": total,
        "message": final_message or "No image rows were changed.",
        "result": result,
    }


def run_vacuum_job(db, job_id):
    update_job(
        db,
        job_id,
        progress_current=0,
        progress_total=1,
        message="Reclaiming database space...",
    )
    result = vacuum_database(db)
    update_job(
        db,
        job_id,
        progress_current=1,
        progress_total=1,
        message="Database cleanup finished.",
    )
    return {
        "progress_current": 1,
        "progress_total": 1,
        "message": job_result_summary("vacuum_database", result),
        "result": result,
    }


def filename_date_candidate(filename):
    normalized = Path(filename or "").stem
    normalized = re.sub(r"[_+.]+", " ", normalized)
    month_names = {
        name.lower(): index
        for index, name in enumerate(calendar.month_name)
        if name
    }
    month_names.update(
        {
            name.lower(): index
            for index, name in enumerate(calendar.month_abbr)
            if name
        }
    )

    numeric_patterns = (
        r"(?<!\d)((?:19|20)\d{2})[-/ .]?([01]\d)[-/ .]?([0-3]\d)(?!\d)",
        r"(?<!\d)([0-3]?\d)[-/ .]([01]?\d)[-/ .]((?:19|20)\d{2})(?!\d)",
        r"(?<!\d)([01]?\d)[-/ .]([0-3]?\d)[-/ .]((?:19|20)\d{2})(?!\d)",
    )
    for pattern in numeric_patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        if len(match.group(1)) == 4:
            year, month, day = match.groups()
        elif int(match.group(1)) > 12:
            day, month, year = match.groups()
        else:
            month, day, year = match.groups()
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            continue

    month_first_pattern = (
        r"\b("
        + "|".join(re.escape(name) for name in sorted(month_names, key=len, reverse=True))
        + r")\s+([0-3]?\d)(?:st|nd|rd|th)?(?:,)?\s+((?:19|20)\d{2})\b"
    )
    day_first_pattern = (
        r"\b([0-3]?\d)(?:st|nd|rd|th)?\s+("
        + "|".join(re.escape(name) for name in sorted(month_names, key=len, reverse=True))
        + r")(?:,)?\s+((?:19|20)\d{2})\b"
    )
    for pattern in (month_first_pattern, day_first_pattern):
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        if match.group(1).lower() in month_names:
            month_name, day, year = match.groups()
        else:
            day, month_name, year = match.groups()
        try:
            return date(int(year), month_names[month_name.lower()], int(day))
        except ValueError:
            continue

    return None


def detect_import_photo_date(image_data, filename):
    detected_date, detected_source = best_photo_date_candidate(image_data, filename)
    if detected_date:
        return detected_date.isoformat(), detected_source

    return None, ""


def backfill_photo_hashes(db):
    rows = db.execute(
        """
        SELECT id, image_data
        FROM photos
        WHERE image_hash IS NULL OR image_hash = ''
        """
    ).fetchall()
    for row in rows:
        photo_id = row["id"] if hasattr(row, "keys") else row[0]
        image_data = row["image_data"] if hasattr(row, "keys") else row[1]
        db.execute(
            "UPDATE photos SET image_hash = ? WHERE id = ?",
            (photo_image_hash(image_data), photo_id),
        )


def find_duplicate_photo(db, user_id, image_hash):
    if not image_hash:
        return None
    return db.execute(
        """
        SELECT id, year, month, original_filename, title, caption, photo_date, created_at
        FROM photos
        WHERE user_id = ? AND image_hash = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (user_id, image_hash),
    ).fetchone()


def is_valid_email(value):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value or ""))


def utc_now():
    return datetime.now(timezone.utc)


def hash_reset_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def find_user_for_password_reset(db, identifier):
    normalized_identifier = (identifier or "").strip()
    if not normalized_identifier:
        return None

    lookup_value = normalized_identifier.lower()
    return db.execute(
        """
        SELECT *
        FROM users
        WHERE lower(username) = ? OR lower(COALESCE(email, '')) = ?
        """,
        (lookup_value, lookup_value),
    ).fetchone()


def create_password_reset_token(db, user_id):
    db.execute(
        """
        UPDATE password_reset_tokens
        SET used_at = CURRENT_TIMESTAMP
        WHERE user_id = ? AND used_at IS NULL
        """,
        (user_id,),
    )
    token = secrets.token_urlsafe(32)
    expires_at = (utc_now() + PASSWORD_RESET_TTL).isoformat()
    db.execute(
        """
        INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
        VALUES (?, ?, ?)
        """,
        (user_id, hash_reset_token(token), expires_at),
    )
    return token


def get_valid_password_reset(db, token):
    token_hash = hash_reset_token(token or "")
    reset_row = db.execute(
        """
        SELECT prt.*, u.username
        FROM password_reset_tokens prt
        JOIN users u ON u.id = prt.user_id
        WHERE prt.token_hash = ? AND prt.used_at IS NULL
        """,
        (token_hash,),
    ).fetchone()
    if reset_row is None:
        return None

    try:
        expires_at = datetime.fromisoformat(reset_row["expires_at"])
    except ValueError:
        return None

    if expires_at <= utc_now():
        return None
    return reset_row


def normalize_month_date(value, field_name, year, month):
    normalized_value = (value or "").strip()
    if not normalized_value:
        return None

    parsed_date = parse_iso_date(normalized_value, field_name)
    if parsed_date.year != year or parsed_date.month != month:
        raise ValueError(f"{field_name} must belong to the selected month.")
    return parsed_date.isoformat()


def parse_exif_date_value(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    normalized_value = str(value or "").strip()
    match = re.match(r"^(\d{4})[:/-](\d{2})[:/-](\d{2})", normalized_value)
    if not match:
        return None

    try:
        return date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
    except ValueError:
        return None


def detect_photo_taken_date(image_data):
    try:
        with Image.open(io.BytesIO(image_data)) as image:
            exif = image.getexif()
    except Exception:
        return None

    if not exif:
        return None

    for tag in EXIF_DATE_TAGS:
        parsed_date = parse_exif_date_value(exif.get(tag))
        if parsed_date:
            return parsed_date
    return None


def best_photo_date_candidate(image_data, filename=None):
    detected_date = detect_photo_taken_date(image_data)
    if detected_date:
        return detected_date, "metadata"

    detected_date = filename_date_candidate(filename)
    if detected_date:
        return detected_date, "filename"

    return None, ""


def photo_date_from_upload(image_data, year, month, manual_date=None, filename=None):
    if manual_date:
        return manual_date, False, False

    detected_date, detected_source = best_photo_date_candidate(image_data, filename)
    if detected_date is None:
        return None, False, False
    if detected_date.year == year and detected_date.month == month:
        return detected_date.isoformat(), True, False
    return None, False, bool(detected_source)


def uploaded_photo_files():
    return [
        image
        for image in request.files.getlist("photo")
        if image is not None and image.filename
    ]


def insert_photo_record(db, filename, mime_type, image_data, image_hash, year, month, photo_date, title, caption, tags, location, people=None):
    cursor = db.execute(
        """
        INSERT INTO photos (
            user_id, year, month, original_filename, title, caption, image_hash, mime_type, image_data, photo_date,
            location_name, latitude, longitude
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["id"],
            year,
            month,
            secure_filename(filename),
            title,
            caption,
            image_hash,
            mime_type,
            image_data,
            photo_date,
            location["location_name"],
            location["latitude"],
            location["longitude"],
        ),
    )
    set_tags_for_item(db, "photo", cursor.lastrowid, tags)
    set_people_for_item(db, "photo", cursor.lastrowid, people or [])
    return cursor.lastrowid


def insert_uploaded_photo(db, image, image_data, image_hash, year, month, photo_date, title, caption, tags, location, people=None):
    return insert_photo_record(
        db,
        image.filename,
        JPEG_STORAGE_MIME,
        image_data,
        image_hash,
        year,
        month,
        photo_date,
        title,
        caption,
        tags,
        location,
        people,
    )


def photo_upload_summary(uploaded_count, auto_dated_count, manual_date_used, skipped_count, ignored_exif_count, duplicate_count=0):
    noun = "photo" if uploaded_count == 1 else "photos"
    parts = [f"Uploaded {uploaded_count} {noun}."]
    if auto_dated_count:
        auto_noun = "photo" if auto_dated_count == 1 else "photos"
        parts.append(f"Auto-dated {auto_dated_count} {auto_noun} from image metadata.")
    elif manual_date_used:
        parts.append("Applied the selected date to every uploaded photo.")
    if ignored_exif_count:
        ignored_noun = "date" if ignored_exif_count == 1 else "dates"
        parts.append(f"Ignored {ignored_exif_count} detected {ignored_noun} outside this month.")
    if skipped_count:
        skipped_noun = "file" if skipped_count == 1 else "files"
        parts.append(f"Skipped {skipped_count} {skipped_noun}.")
    if duplicate_count:
        duplicate_noun = "duplicate photo" if duplicate_count == 1 else "duplicate photos"
        parts.append(f"Skipped {duplicate_count} {duplicate_noun} already in your timeline.")
    return " ".join(parts)


def import_detection_label(source):
    labels = {
        "metadata": "Detected from metadata",
        "filename": "Detected from filename",
    }
    return labels.get(source or "", "No date detected")


def empty_location_payload():
    return {
        "location_name": "",
        "latitude": None,
        "longitude": None,
    }


def get_import_batch(db, token):
    batch = db.execute(
        """
        SELECT *
        FROM photo_import_batches
        WHERE token = ? AND user_id = ?
        """,
        (token, g.user["id"]),
    ).fetchone()
    if batch is None:
        abort(404)
    return batch


def load_import_items(db, batch_id):
    return db.execute(
        """
        SELECT id, original_filename, mime_type, image_hash, detected_date,
               detected_source, duplicate_photo_id, duplicate_import_item_id,
               created_at
        FROM photo_import_items
        WHERE batch_id = ?
        ORDER BY id ASC
        """,
        (batch_id,),
    ).fetchall()


def get_import_item(db, batch_id, item_id):
    item = db.execute(
        """
        SELECT *
        FROM photo_import_items
        WHERE batch_id = ? AND id = ?
        """,
        (batch_id, item_id),
    ).fetchone()
    if item is None:
        abort(404)
    return item


def import_duplicate_payload(db, token, batch_id, item):
    if item["duplicate_photo_id"]:
        photo = db.execute(
            """
            SELECT id, year, month, original_filename, title, caption, photo_date, created_at
            FROM photos
            WHERE id = ? AND user_id = ?
            """,
            (item["duplicate_photo_id"], g.user["id"]),
        ).fetchone()
        if photo is None:
            return None
        return {
            "source": "timeline",
            "label": "Already in timeline",
            "title": photo_display_title(photo),
            "date_label": format_timeline_date_label(photo["year"], photo["month"], photo["photo_date"]),
            "image_url": url_for("photo_image", photo_id=photo["id"]),
            "open_url": url_for(
                "month_view",
                year=photo["year"],
                month=photo["month"],
                focus=timeline_item_focus("photo", photo["id"]),
            ),
        }

    if item["duplicate_import_item_id"]:
        import_item = db.execute(
            """
            SELECT id, original_filename, detected_date
            FROM photo_import_items
            WHERE id = ? AND batch_id = ?
            """,
            (item["duplicate_import_item_id"], batch_id),
        ).fetchone()
        if import_item is None:
            return None
        return {
            "source": "batch",
            "label": "Already in this batch",
            "title": import_item["original_filename"] or "Photo",
            "date_label": import_item["detected_date"] or "No date",
            "image_url": url_for("timeline_import_item_image", token=token, item_id=import_item["id"]),
            "open_url": "",
        }

    return None


def import_review_items(db, token, batch_id, item_rows, form=None):
    review_items = []
    for item in item_rows:
        item_id = str(item["id"])
        default_tag = DEFAULT_TAG
        duplicate = import_duplicate_payload(db, token, batch_id, item)
        review_items.append(
            {
                "id": item["id"],
                "original_filename": item["original_filename"],
                "detected_date": item["detected_date"] or "",
                "detected_source": item["detected_source"] or "",
                "detected_label": import_detection_label(item["detected_source"]),
                "duplicate": duplicate,
                "review_date": (
                    form.get(f"photo_date_{item_id}", item["detected_date"] or "")
                    if form is not None
                    else item["detected_date"] or ""
                ),
                "review_tag": (
                    form.get(f"tags_{item_id}", default_tag)
                    if form is not None
                    else default_tag
                ),
                "skip_checked": (
                    f"skip_{item_id}" in form
                    if form is not None
                    else duplicate is not None
                ),
            }
        )
    return review_items


def normalize_import_photo_date(value):
    parsed_date = parse_iso_date(value, "Photo date")
    birthday_date = parse_iso_date(g.user["birthday"], "Birthday")
    if parsed_date < birthday_date:
        raise ValueError("Photo date cannot be before your birthday.")
    if parsed_date.year not in timeline_years_for_user(g.user):
        raise ValueError("Photo date must belong to your timeline years.")
    return parsed_date


def get_owned_photo(photo_id):
    photo = get_db().execute(
        """
        SELECT * FROM photos
        WHERE id = ? AND user_id = ?
        """,
        (photo_id, g.user["id"]),
    ).fetchone()
    if photo is None:
        abort(404)
    return photo


def get_owned_text_entry(entry_id):
    entry = get_db().execute(
        """
        SELECT * FROM text_entries
        WHERE id = ? AND user_id = ?
        """,
        (entry_id, g.user["id"]),
    ).fetchone()
    if entry is None:
        abort(404)
    return entry


def get_owned_timeline_item(item_kind, item_id):
    if item_kind == "photo":
        return get_owned_photo(item_id)
    if item_kind == "text":
        return get_owned_text_entry(item_id)
    abort(404)


def message_author_name(row):
    return user_full_name(row) or row["username"]


def message_payload(row):
    return {
        "id": row["id"],
        "body": row["body"],
        "created_at": row["created_at"],
        "author_name": message_author_name(row),
    }


def load_messages_for_timeline_item(db, item_kind, item_id):
    if item_kind == "photo":
        rows = db.execute(
            """
            SELECT m.id, m.body, m.created_at, u.username, u.first_name, u.last_name
            FROM messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.photo_id = ?
            ORDER BY m.created_at ASC, m.id ASC
            """,
            (item_id,),
        ).fetchall()
    elif item_kind == "text":
        rows = db.execute(
            """
            SELECT tem.id, tem.body, tem.created_at, u.username, u.first_name, u.last_name
            FROM text_entry_messages tem
            JOIN users u ON u.id = tem.user_id
            WHERE tem.entry_id = ?
            ORDER BY tem.created_at ASC, tem.id ASC
            """,
            (item_id,),
        ).fetchall()
    else:
        abort(404)
    return [message_payload(row) for row in rows]


def create_timeline_item_message(db, item_kind, item_id, body):
    if item_kind == "photo":
        cursor = db.execute(
            """
            INSERT INTO messages (photo_id, user_id, body)
            VALUES (?, ?, ?)
            """,
            (item_id, g.user["id"], body),
        )
        select_message = """
            SELECT m.id, m.body, m.created_at, u.username, u.first_name, u.last_name
            FROM messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.id = ?
        """
    elif item_kind == "text":
        cursor = db.execute(
            """
            INSERT INTO text_entry_messages (entry_id, user_id, body)
            VALUES (?, ?, ?)
            """,
            (item_id, g.user["id"], body),
        )
        select_message = """
            SELECT tem.id, tem.body, tem.created_at, u.username, u.first_name, u.last_name
            FROM text_entry_messages tem
            JOIN users u ON u.id = tem.user_id
            WHERE tem.id = ?
        """
    else:
        abort(404)

    db.commit()
    row = db.execute(select_message, (cursor.lastrowid,)).fetchone()
    return message_payload(row)


def timeline_item_sort_key(item):
    return (
        item["year"],
        item["month"],
        1 if item["display_date"] else 0,
        item["display_date"] or f"{item['year']:04d}-{item['month']:02d}-00",
        item["kind"],
        item["id"],
    )


def format_timeline_date_label(year, month, display_date):
    if display_date:
        return display_date
    return f"{year:04d}-{month:02d}-??"


def normalize_coordinate(value, label, minimum, maximum):
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    try:
        coordinate = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if coordinate < minimum or coordinate > maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}.")
    return coordinate


def normalize_location_payload(payload):
    location_name = " ".join((payload.get("location_name") or "").strip().split())
    latitude = normalize_coordinate(payload.get("latitude"), "Latitude", -90, 90)
    longitude = normalize_coordinate(payload.get("longitude"), "Longitude", -180, 180)
    if (latitude is None) != (longitude is None):
        raise ValueError("Latitude and longitude must be provided together.")
    return {
        "location_name": location_name,
        "latitude": latitude,
        "longitude": longitude,
    }


def timeline_location_payload(row):
    return {
        "location_name": row["location_name"] if "location_name" in row.keys() else "",
        "latitude": row["latitude"] if "latitude" in row.keys() else None,
        "longitude": row["longitude"] if "longitude" in row.keys() else None,
    }


def map_position(latitude, longitude):
    return {
        "x": ((longitude + 180) / 360) * 100,
        "y": ((90 - latitude) / 180) * 100,
    }


def normalize_place_name(value):
    return " ".join((value or "").strip().split())


def place_group_key(value):
    return normalize_place_name(value).casefold()


def normalize_tag_name(value):
    return " ".join((value or "").strip().lower().split())


def normalize_tag_choice(value):
    tag = normalize_tag_name(value)
    if tag in TAG_CHOICES:
        return tag
    return ""


def parse_tags(value):
    if isinstance(value, (list, tuple, set)):
        chunks = value
    else:
        chunks = (value or "").replace(";", ",").split(",")

    for chunk in chunks:
        tag = normalize_tag_choice(chunk)
        if tag:
            return [tag]
    return [DEFAULT_TAG]


def tags_to_text(tags):
    return parse_tags(tags)[0]


def privacy_label_for_tags(tags):
    return PRIVACY_AUDIENCE_LABELS.get(tags_to_text(tags), PRIVACY_AUDIENCE_LABELS[DEFAULT_TAG])


def privacy_help_for_tags(tags):
    return PRIVACY_AUDIENCE_HELP.get(tags_to_text(tags), PRIVACY_AUDIENCE_HELP[DEFAULT_TAG])


def privacy_payload_for_tags(tags):
    return {
        "privacy_label": privacy_label_for_tags(tags),
        "privacy_help": privacy_help_for_tags(tags),
    }


def get_or_create_tag(db, name):
    db.execute(
        "INSERT OR IGNORE INTO tags (user_id, name) VALUES (?, ?)",
        (g.user["id"], name),
    )
    return db.execute(
        "SELECT id FROM tags WHERE user_id = ? AND name = ?",
        (g.user["id"], name),
    ).fetchone()["id"]


def tag_join_for_kind(kind):
    if kind == "photo":
        return "photo_tags", "photo_id"
    if kind == "text":
        return "text_entry_tags", "entry_id"
    raise ValueError("Unknown tag kind.")


def set_tags_for_item(db, kind, item_id, tags):
    join_table, id_column = tag_join_for_kind(kind)
    db.execute(f"DELETE FROM {join_table} WHERE {id_column} = ?", (item_id,))
    for tag in parse_tags(tags):
        tag_id = get_or_create_tag(db, tag)
        db.execute(
            f"INSERT OR IGNORE INTO {join_table} ({id_column}, tag_id) VALUES (?, ?)",
            (item_id, tag_id),
        )


def bulk_set_privacy(db, year, tag, month=None):
    normalized_tag = normalize_tag_choice(tag)
    if not normalized_tag:
        raise ValueError("Choose a valid visibility option.")

    total = 0
    for table, kind in (("photos", "photo"), ("text_entries", "text")):
        query = f"SELECT id FROM {table} WHERE user_id = ? AND year = ?"
        params = [g.user["id"], year]
        if month is not None:
            query += " AND month = ?"
            params.append(month)
        rows = db.execute(query, tuple(params)).fetchall()
        for row in rows:
            set_tags_for_item(db, kind, row["id"], [normalized_tag])
            total += 1
    return total


def load_tags_for_items(db, kind, item_ids, owner_id=None):
    if not item_ids:
        return {}

    tag_owner_id = owner_id if owner_id is not None else g.user["id"]
    join_table, id_column = tag_join_for_kind(kind)
    placeholders = ",".join(["?"] * len(item_ids))
    rows = db.execute(
        f"""
        SELECT jt.{id_column} AS item_id, t.name
        FROM {join_table} jt
        JOIN tags t ON t.id = jt.tag_id
        WHERE t.user_id = ? AND jt.{id_column} IN ({placeholders})
        ORDER BY t.name ASC
        """,
        (tag_owner_id, *item_ids),
    ).fetchall()
    tags_by_item = {item_id: [] for item_id in item_ids}
    for row in rows:
        tag = normalize_tag_choice(row["name"])
        if tag and tag not in tags_by_item[row["item_id"]]:
            tags_by_item.setdefault(row["item_id"], []).append(tag)
    return {item_id: parse_tags(tags) for item_id, tags in tags_by_item.items()}


def get_tags_for_item(db, kind, item_id, owner_id=None):
    return load_tags_for_items(db, kind, [item_id], owner_id).get(item_id, [])


def get_all_tags(db):
    return list(TAG_CHOICES)


def normalize_person_name(value):
    return " ".join((value or "").strip().split())[:80]


def parse_people(value):
    if isinstance(value, (list, tuple, set)):
        chunks = value
    else:
        chunks = (value or "").replace(";", ",").split(",")

    people = []
    seen = set()
    for chunk in chunks:
        name = normalize_person_name(chunk)
        key = name.casefold()
        if name and key not in seen:
            people.append(name)
            seen.add(key)
    return people


def people_to_text(people):
    return ", ".join(parse_people(people))


def get_or_create_person(db, name):
    normalized_name = normalize_person_name(name)
    existing = db.execute(
        """
        SELECT id
        FROM people
        WHERE user_id = ? AND lower(name) = lower(?)
        """,
        (g.user["id"], normalized_name),
    ).fetchone()
    if existing is not None:
        return existing["id"]

    db.execute(
        "INSERT OR IGNORE INTO people (user_id, name) VALUES (?, ?)",
        (g.user["id"], normalized_name),
    )
    return db.execute(
        """
        SELECT id
        FROM people
        WHERE user_id = ? AND lower(name) = lower(?)
        """,
        (g.user["id"], normalized_name),
    ).fetchone()["id"]


def person_join_for_kind(kind):
    if kind == "photo":
        return "photo_people", "photo_id"
    if kind == "text":
        return "text_entry_people", "entry_id"
    raise ValueError("Unknown person-tag kind.")


def set_people_for_item(db, kind, item_id, people):
    join_table, id_column = person_join_for_kind(kind)
    db.execute(f"DELETE FROM {join_table} WHERE {id_column} = ?", (item_id,))
    for name in parse_people(people):
        person_id = get_or_create_person(db, name)
        db.execute(
            f"INSERT OR IGNORE INTO {join_table} ({id_column}, person_id) VALUES (?, ?)",
            (item_id, person_id),
        )


def load_people_for_items(db, kind, item_ids, owner_id=None):
    if not item_ids:
        return {}

    person_owner_id = owner_id if owner_id is not None else g.user["id"]
    join_table, id_column = person_join_for_kind(kind)
    placeholders = ",".join(["?"] * len(item_ids))
    rows = db.execute(
        f"""
        SELECT jp.{id_column} AS item_id, p.name
        FROM {join_table} jp
        JOIN people p ON p.id = jp.person_id
        WHERE p.user_id = ? AND jp.{id_column} IN ({placeholders})
        ORDER BY lower(p.name) ASC, p.name ASC
        """,
        (person_owner_id, *item_ids),
    ).fetchall()
    people_by_item = {item_id: [] for item_id in item_ids}
    for row in rows:
        name = normalize_person_name(row["name"])
        if name and name not in people_by_item[row["item_id"]]:
            people_by_item.setdefault(row["item_id"], []).append(name)
    return people_by_item


def get_people_for_item(db, kind, item_id, owner_id=None):
    return load_people_for_items(db, kind, [item_id], owner_id).get(item_id, [])


def people_payload(people):
    parsed_people = parse_people(people)
    return {
        "people": parsed_people,
        "people_text": people_to_text(parsed_people),
    }


def people_year_label(years):
    years = sorted(years)
    if not years:
        return ""
    if len(years) == 1:
        return str(years[0])
    return f"{years[0]}-{years[-1]}"


def memory_word(count):
    return "memory" if count == 1 else "memories"


def chapter_word(count):
    return "chapter" if count == 1 else "chapters"


def get_timeline_person(db, person_id):
    person = db.execute(
        """
        SELECT id, name
        FROM people
        WHERE id = ? AND user_id = ?
        """,
        (person_id, g.user["id"]),
    ).fetchone()
    if person is None:
        abort(404)
    return person


def build_people_summaries(db):
    photo_rows = db.execute(
        """
        SELECT p.id AS person_id, p.name, ph.id AS item_id, ph.year, ph.month,
               ph.photo_date AS display_date
        FROM people p
        JOIN photo_people pp ON pp.person_id = p.id
        JOIN photos ph ON ph.id = pp.photo_id AND ph.user_id = p.user_id
        WHERE p.user_id = ?
        """,
        (g.user["id"],),
    ).fetchall()
    text_rows = db.execute(
        """
        SELECT p.id AS person_id, p.name, te.id AS item_id, te.year, te.month,
               te.entry_date AS display_date
        FROM people p
        JOIN text_entry_people tep ON tep.person_id = p.id
        JOIN text_entries te ON te.id = tep.entry_id AND te.user_id = p.user_id
        WHERE p.user_id = ?
        """,
        (g.user["id"],),
    ).fetchall()

    summaries = {}

    def add_row(row, kind):
        person_id = row["person_id"]
        summary = summaries.setdefault(
            person_id,
            {
                "id": person_id,
                "name": normalize_person_name(row["name"]),
                "photo_count": 0,
                "text_count": 0,
                "years": set(),
                "latest_sort_key": None,
                "latest_label": "",
                "url": url_for("timeline_person", person_id=person_id),
                "draft_url": "",
            },
        )
        if kind == "photo":
            summary["photo_count"] += 1
        else:
            summary["text_count"] += 1
        summary["years"].add(row["year"])
        sort_key = timeline_item_sort_key(
            {
                "year": row["year"],
                "month": row["month"],
                "display_date": row["display_date"],
                "kind": kind,
                "id": row["item_id"],
            }
        )
        if summary["latest_sort_key"] is None or sort_key > summary["latest_sort_key"]:
            summary["latest_sort_key"] = sort_key
            summary["latest_label"] = format_timeline_date_label(
                row["year"],
                row["month"],
                row["display_date"],
            )

    for row in photo_rows:
        add_row(row, "photo")
    for row in text_rows:
        add_row(row, "text")

    people = []
    for summary in summaries.values():
        summary["item_count"] = summary["photo_count"] + summary["text_count"]
        summary["year_label"] = people_year_label(summary["years"])
        summary["draft_url"] = url_for("chapter_draft", person=summary["name"])
        summary.pop("years", None)
        summary.pop("latest_sort_key", None)
        people.append(summary)

    people.sort(key=lambda item: (-item["item_count"], item["name"].casefold()))
    return people


def item_refs_for_items(items):
    return [(item["kind"], item["id"]) for item in items if item["kind"] in ("photo", "text")]


def load_message_count_for_refs(db, refs):
    photo_ids = [item_id for kind, item_id in refs if kind == "photo"]
    text_ids = [item_id for kind, item_id in refs if kind == "text"]
    return sum(load_timeline_search_message_counts(db, "photo", photo_ids).values()) + sum(
        load_timeline_search_message_counts(db, "text", text_ids).values()
    )


def load_chapters_for_refs(db, refs):
    chapters = {}
    for kind in ("photo", "text"):
        item_ids = [item_id for ref_kind, item_id in refs if ref_kind == kind]
        if not item_ids:
            continue
        placeholders = ",".join(["?"] * len(item_ids))
        rows = db.execute(
            f"""
            SELECT c.id, c.title, c.description, c.visibility, COUNT(ci.id) AS matched_count
            FROM chapter_items ci
            JOIN chapters c ON c.id = ci.chapter_id
            WHERE c.user_id = ? AND ci.item_kind = ? AND ci.item_id IN ({placeholders})
            GROUP BY c.id
            """,
            (g.user["id"], kind, *item_ids),
        ).fetchall()
        for row in rows:
            chapter = chapters.setdefault(
                row["id"],
                {
                    "id": row["id"],
                    "title": row["title"],
                    "description": row["description"] or "",
                    "visibility": chapter_visibility(row["visibility"]),
                    "matched_count": 0,
                    "url": url_for("chapter_detail", chapter_id=row["id"]),
                },
            )
            chapter["matched_count"] += row["matched_count"]
    results = list(chapters.values())
    for chapter in results:
        chapter.update(privacy_payload_for_tags([chapter["visibility"]]))
    results.sort(key=lambda chapter: (-chapter["matched_count"], chapter["title"].casefold()))
    return results


def hub_stat(label, value):
    return {"label": label, "value": value}


def build_people_hub_overview(people):
    total_memories = sum(person["item_count"] for person in people)
    total_photos = sum(person["photo_count"] for person in people)
    total_text = sum(person["text_count"] for person in people)
    return [
        hub_stat("Tagged people", len(people)),
        hub_stat("Tagged memories", total_memories),
        hub_stat("Photos", total_photos),
        hub_stat("Text entries", total_text),
    ]


def build_person_hub(db, person, items):
    refs = item_refs_for_items(items)
    places = sorted({normalize_place_name(item.get("location_name", "")) for item in items if normalize_place_name(item.get("location_name", ""))})
    chapters = load_chapters_for_refs(db, refs)
    missing_place_count = sum(1 for item in items if not normalize_place_name(item.get("location_name", "")))
    missing_caption_count = sum(1 for item in items if item["kind"] == "photo" and not item.get("has_caption"))
    photo_count = sum(1 for item in items if item["kind"] == "photo")
    text_count = sum(1 for item in items if item["kind"] == "text")
    years = sorted({item["year"] for item in items})
    return {
        "stats": [
            hub_stat("Memories", len(items)),
            hub_stat("Photos", photo_count),
            hub_stat("Text entries", text_count),
            hub_stat("Places", len(places)),
            hub_stat("Messages", load_message_count_for_refs(db, refs)),
            hub_stat("Chapters", len(chapters)),
        ],
        "chapters": chapters[:6],
        "places": places[:8],
        "year_label": people_year_label(years),
        "gap_notes": [
            note
            for note in (
                f"{missing_place_count} {memory_word(missing_place_count)} without a place" if missing_place_count else "",
                f"{missing_caption_count} {'photo needs' if missing_caption_count == 1 else 'photos need'} captions" if missing_caption_count else "",
            )
            if note
        ],
        "draft_url": url_for("chapter_draft", person=person["name"]),
        "search_url": url_for("timeline_search", q=person["name"]),
    }


def build_person_timeline_items(db, person_id):
    get_timeline_person(db, person_id)
    photo_rows = db.execute(
        """
        SELECT ph.id, ph.year, ph.month, ph.original_filename, ph.title, ph.caption,
               ph.photo_date, ph.location_name, ph.latitude, ph.longitude, ph.created_at
        FROM photos ph
        JOIN photo_people pp ON pp.photo_id = ph.id
        WHERE ph.user_id = ? AND pp.person_id = ?
        """,
        (g.user["id"], person_id),
    ).fetchall()
    text_rows = db.execute(
        """
        SELECT te.id, te.year, te.month, te.body, te.entry_date, te.location_name,
               te.latitude, te.longitude, te.created_at, te.updated_at
        FROM text_entries te
        JOIN text_entry_people tep ON tep.entry_id = te.id
        WHERE te.user_id = ? AND tep.person_id = ?
        """,
        (g.user["id"], person_id),
    ).fetchall()

    photo_ids = [row["id"] for row in photo_rows]
    text_ids = [row["id"] for row in text_rows]
    photo_tags = load_tags_for_items(db, "photo", photo_ids)
    text_tags = load_tags_for_items(db, "text", text_ids)
    photo_people = load_people_for_items(db, "photo", photo_ids)
    text_people = load_people_for_items(db, "text", text_ids)

    items = []
    for row in photo_rows:
        tags = photo_tags.get(row["id"], [])
        people = photo_people.get(row["id"], [])
        sort_key = timeline_item_sort_key(
            {
                "year": row["year"],
                "month": row["month"],
                "display_date": row["photo_date"],
                "kind": "photo",
                "id": row["id"],
            }
        )
        items.append(
            {
                "kind": "photo",
                "kind_label": "Photo",
                "id": row["id"],
                "title": photo_display_title(row),
                "preview": short_preview(row["caption"] or "Photo"),
                "has_caption": bool((row["caption"] or "").strip()),
                "date_label": format_timeline_date_label(row["year"], row["month"], row["photo_date"]),
                "source_label": item_source_label(row["year"], row["month"]),
                "year": row["year"],
                "month": row["month"],
                "location_name": row["location_name"] or "",
                "image_url": url_for("photo_image", photo_id=row["id"]),
                "url": timeline_item_link(g.user["id"], row["year"], row["month"], "photo", row["id"]),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "sort_key": sort_key,
            }
        )

    for row in text_rows:
        tags = text_tags.get(row["id"], [])
        people = text_people.get(row["id"], [])
        sort_key = timeline_item_sort_key(
            {
                "year": row["year"],
                "month": row["month"],
                "display_date": row["entry_date"],
                "kind": "text",
                "id": row["id"],
            }
        )
        items.append(
            {
                "kind": "text",
                "kind_label": "Text",
                "id": row["id"],
                "title": "Text entry",
                "preview": short_preview(row["body"], 180),
                "date_label": format_timeline_date_label(row["year"], row["month"], row["entry_date"]),
                "source_label": item_source_label(row["year"], row["month"]),
                "year": row["year"],
                "month": row["month"],
                "location_name": row["location_name"] or "",
                "url": timeline_item_link(g.user["id"], row["year"], row["month"], "text", row["id"]),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "sort_key": sort_key,
            }
        )

    items.sort(key=lambda item: item["sort_key"], reverse=True)
    for item in items:
        item.pop("sort_key", None)
    return items


def tags_visible_to_connection(tags, allowed_tags):
    if allowed_tags is None:
        return True
    return bool(set(parse_tags(tags)) & set(allowed_tags))


def current_privacy_preview():
    mode = (request.args.get("preview") or "").strip().lower()
    option = PRIVACY_PREVIEW_OPTIONS.get(mode)
    if option is None:
        return None
    return dict(option)


def privacy_preview_allowed_tags(preview=None):
    preview = preview if preview is not None else current_privacy_preview()
    if preview is None:
        return None
    return preview["allowed_tags"]


def reaction_payload(kind, item_id, counts=None, user_reaction=None):
    counts = counts or {}
    return {
        "kind": kind,
        "item_id": item_id,
        "like_count": counts.get("like", 0),
        "love_count": counts.get("love", 0),
        "user_reaction": user_reaction or "",
        "reaction_url": url_for("timeline_item_reaction", item_kind=kind, item_id=item_id),
    }


def load_reaction_payloads(db, item_refs):
    refs = sorted({(kind, int(item_id)) for kind, item_id in item_refs if kind in ("photo", "text")})
    payloads = {
        (kind, item_id): reaction_payload(kind, item_id)
        for kind, item_id in refs
    }
    if not refs:
        return payloads

    for kind in ("photo", "text"):
        item_ids = [item_id for ref_kind, item_id in refs if ref_kind == kind]
        if not item_ids:
            continue
        placeholders = ",".join(["?"] * len(item_ids))
        count_rows = db.execute(
            f"""
            SELECT item_id, reaction, COUNT(*) AS reaction_count
            FROM item_reactions
            WHERE item_kind = ? AND item_id IN ({placeholders})
            GROUP BY item_id, reaction
            """,
            (kind, *item_ids),
        ).fetchall()
        for row in count_rows:
            key = (kind, row["item_id"])
            payloads[key][f"{row['reaction']}_count"] = row["reaction_count"]

        user_rows = db.execute(
            f"""
            SELECT item_id, reaction
            FROM item_reactions
            WHERE user_id = ? AND item_kind = ? AND item_id IN ({placeholders})
            """,
            (g.user["id"], kind, *item_ids),
        ).fetchall()
        for row in user_rows:
            payloads[(kind, row["item_id"])]["user_reaction"] = row["reaction"]

    return payloads


def attach_reactions(db, items):
    payloads = load_reaction_payloads(db, [(item["kind"], item["id"]) for item in items])
    for item in items:
        item["reactions"] = payloads.get(
            (item["kind"], item["id"]),
            reaction_payload(item["kind"], item["id"]),
        )
    return items


def get_timeline_item_for_reaction(item_kind, item_id):
    if item_kind == "photo":
        table = "photos"
    elif item_kind == "text":
        table = "text_entries"
    else:
        abort(404)

    db = get_db()
    item = db.execute(
        f"""
        SELECT *
        FROM {table}
        WHERE id = ?
        """,
        (item_id,),
    ).fetchone()
    if item is None:
        abort(404)

    owner_id = item["user_id"]
    if owner_id == g.user["id"]:
        return item

    tags = get_tags_for_item(db, item_kind, item_id, owner_id)
    if tags_visible_to_connection(tags, ("public",)):
        return item

    connection = db.execute(
        """
        SELECT relation
        FROM connection_requests
        WHERE status = 'accepted'
          AND (
            (requester_id = ? AND recipient_id = ?)
            OR (recipient_id = ? AND requester_id = ?)
          )
        ORDER BY responded_at DESC, created_at DESC, id DESC
        LIMIT 1
        """,
        (g.user["id"], owner_id, g.user["id"], owner_id),
    ).fetchone()
    if connection and tags_visible_to_connection(
        tags,
        CONNECTION_VISIBLE_TAGS.get(connection["relation"], ("public",)),
    ):
        return item

    shared_chapter = db.execute(
        """
        SELECT ci.id
        FROM chapter_invites ci
        JOIN chapter_items ch_item ON ch_item.chapter_id = ci.chapter_id
        WHERE ci.recipient_id = ?
          AND ci.status = 'accepted'
          AND ch_item.item_kind = ?
          AND ch_item.item_id = ?
        LIMIT 1
        """,
        (g.user["id"], item_kind, item_id),
    ).fetchone()
    if shared_chapter is not None:
        return item

    abort(404)


def visible_tags_for_connection(connected_user):
    return CONNECTION_VISIBLE_TAGS.get(connected_user["connection_relation"], ("public",))


def get_year_counts(db, user_id=None, allowed_tags=None):
    owner_id = user_id if user_id is not None else g.user["id"]
    counts = {}
    if allowed_tags is not None:
        photo_rows = db.execute(
            """
            SELECT id, year
            FROM photos
            WHERE user_id = ?
            """,
            (owner_id,),
        ).fetchall()
        photo_tags = load_tags_for_items(db, "photo", [row["id"] for row in photo_rows], owner_id)
        for row in photo_rows:
            if tags_visible_to_connection(photo_tags.get(row["id"], []), allowed_tags):
                counts[row["year"]] = counts.get(row["year"], 0) + 1

        text_rows = db.execute(
            """
            SELECT id, year
            FROM text_entries
            WHERE user_id = ?
            """,
            (owner_id,),
        ).fetchall()
        text_tags = load_tags_for_items(db, "text", [row["id"] for row in text_rows], owner_id)
        for row in text_rows:
            if tags_visible_to_connection(text_tags.get(row["id"], []), allowed_tags):
                counts[row["year"]] = counts.get(row["year"], 0) + 1
        return counts

    for table in ("photos", "text_entries"):
        rows = db.execute(
            f"""
            SELECT year, COUNT(*) AS item_count
            FROM {table}
            WHERE user_id = ?
            GROUP BY year
            """,
            (owner_id,),
        ).fetchall()
        for row in rows:
            counts[row["year"]] = counts.get(row["year"], 0) + row["item_count"]
    return counts


def get_month_counts(db, year, user_id=None, allowed_tags=None):
    owner_id = user_id if user_id is not None else g.user["id"]
    counts = {}
    if allowed_tags is not None:
        photo_rows = db.execute(
            """
            SELECT id, month
            FROM photos
            WHERE user_id = ? AND year = ?
            """,
            (owner_id, year),
        ).fetchall()
        photo_tags = load_tags_for_items(db, "photo", [row["id"] for row in photo_rows], owner_id)
        for row in photo_rows:
            if tags_visible_to_connection(photo_tags.get(row["id"], []), allowed_tags):
                counts[row["month"]] = counts.get(row["month"], 0) + 1

        text_rows = db.execute(
            """
            SELECT id, month
            FROM text_entries
            WHERE user_id = ? AND year = ?
            """,
            (owner_id, year),
        ).fetchall()
        text_tags = load_tags_for_items(db, "text", [row["id"] for row in text_rows], owner_id)
        for row in text_rows:
            if tags_visible_to_connection(text_tags.get(row["id"], []), allowed_tags):
                counts[row["month"]] = counts.get(row["month"], 0) + 1
        return counts

    for table in ("photos", "text_entries"):
        rows = db.execute(
            f"""
            SELECT month, COUNT(*) AS item_count
            FROM {table}
            WHERE user_id = ? AND year = ?
            GROUP BY month
            """,
            (owner_id, year),
        ).fetchall()
        for row in rows:
            counts[row["month"]] = counts.get(row["month"], 0) + row["item_count"]
    return counts


def user_full_name(user):
    return " ".join(
        part
        for part in (user["first_name"], user["last_name"])
        if part
    ).strip()


def public_user_payload(user):
    return {
        "id": user["id"],
        "username": user["username"],
        "full_name": user_full_name(user),
        "email": user["email"] or "",
    }


def short_preview(value, length=110):
    text = " ".join((value or "").split())
    if len(text) <= length:
        return text
    return f"{text[: length - 1].rstrip()}..."


def get_public_photo(photo_id):
    photo = get_db().execute(
        """
        SELECT p.*
        FROM photos p
        JOIN photo_tags pt ON pt.photo_id = p.id
        JOIN tags t ON t.id = pt.tag_id
                   AND t.user_id = p.user_id
                   AND t.name = 'public'
        WHERE p.id = ?
        """,
        (photo_id,),
    ).fetchone()
    if photo is None:
        abort(404)
    return photo


def random_public_photos(db, limit=48):
    rows = db.execute(
        """
        SELECT
            p.id,
            p.user_id,
            p.original_filename,
            p.title,
            p.caption,
            p.photo_date,
            p.created_at,
            u.username,
            u.first_name,
            u.last_name,
            COALESCE(message_counts.message_count, 0) AS message_count
        FROM photos p
        JOIN users u ON u.id = p.user_id
        JOIN photo_tags pt ON pt.photo_id = p.id
        JOIN tags t ON t.id = pt.tag_id
                   AND t.user_id = p.user_id
                   AND t.name = 'public'
        LEFT JOIN (
            SELECT photo_id, COUNT(*) AS message_count
            FROM messages
            GROUP BY photo_id
        ) message_counts ON message_counts.photo_id = p.id
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    photos = []
    for row in rows:
        owner_name = user_full_name(row) or row["username"]
        connection_state = (
            {"status": "self", "label": "Your photo", "can_request": False}
            if row["user_id"] == g.user["id"]
            else connection_state_for_user(db, row["user_id"])
        )
        photos.append(
            {
                "kind": "photo",
                "id": row["id"],
                "owner_id": row["user_id"],
                "owner_username": row["username"],
                "image_url": url_for("public_photo_image", photo_id=row["id"]),
                "messages_url": url_for("public_photo_messages", photo_id=row["id"]),
                "title": photo_display_title(row),
                "caption": row["caption"] or "",
                "owner_name": owner_name,
                "display_date": row["photo_date"],
                "message_count": row["message_count"],
                "connection_state": connection_state,
            }
        )
    return attach_reactions(db, photos)


def build_splash_photo_page(db):
    page_size = request.args.get("page_size", default=80, type=int)
    if page_size is None:
        page_size = 80
    page_size = max(1, min(page_size, 240))

    page = request.args.get("page", default=0, type=int)
    if page is None:
        page = 0

    seed = (request.args.get("seed") or "").strip()[:80] or secrets.token_hex(8)
    rows = db.execute(
        """
        SELECT id, original_filename, title, caption, photo_date, created_at
        FROM photos
        WHERE user_id = ?
        ORDER BY id ASC
        """,
        (g.user["id"],),
    ).fetchall()
    photos = [dict(row) for row in rows]
    random.Random(seed).shuffle(photos)

    total = len(photos)
    total_pages = (total + page_size - 1) // page_size if total else 0
    if total_pages:
        page = page % total_pages
    else:
        page = 0

    start = page * page_size
    page_photos = photos[start:start + page_size]
    return {
        "seed": seed,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "photos": [
            {
                "id": photo["id"],
                "title": photo_display_title(photo),
                "caption": photo["caption"] or "",
                "display_date": photo["photo_date"] or "",
                "thumbnail_url": url_for("splash_photo_thumbnail", photo_id=photo["id"]),
                "full_url": url_for("photo_image", photo_id=photo["id"]),
            }
            for photo in page_photos
        ],
    }


def build_on_this_day_items(db, owner_id, month, day, image_url_builder):
    date_key = f"{month:02d}-{day:02d}"
    photo_rows = db.execute(
        """
        SELECT id, year, month, original_filename, title, caption, photo_date,
               location_name, latitude, longitude, created_at
        FROM photos
        WHERE user_id = ?
          AND photo_date IS NOT NULL
          AND substr(photo_date, 6, 5) = ?
        ORDER BY photo_date ASC, id ASC
        """,
        (owner_id, date_key),
    ).fetchall()
    text_rows = db.execute(
        """
        SELECT id, year, month, body, entry_date, location_name, latitude, longitude,
               created_at, updated_at
        FROM text_entries
        WHERE user_id = ?
          AND entry_date IS NOT NULL
          AND substr(entry_date, 6, 5) = ?
        ORDER BY entry_date ASC, id ASC
        """,
        (owner_id, date_key),
    ).fetchall()
    photo_tags = load_tags_for_items(db, "photo", [photo["id"] for photo in photo_rows], owner_id)
    text_tags = load_tags_for_items(db, "text", [entry["id"] for entry in text_rows], owner_id)
    photo_people = load_people_for_items(db, "photo", [photo["id"] for photo in photo_rows], owner_id)
    text_people = load_people_for_items(db, "text", [entry["id"] for entry in text_rows], owner_id)
    items = []
    for photo in photo_rows:
        tags = photo_tags.get(photo["id"], [])
        people = photo_people.get(photo["id"], [])
        items.append(
            {
                "kind": "photo",
                "id": photo["id"],
                "year": photo["year"],
                "month": photo["month"],
                "original_filename": photo["original_filename"],
                "title": photo["title"] or "",
                "display_title": photo_display_title(photo),
                "caption": photo["caption"] or "",
                "display_date": photo["photo_date"],
                "date_label": format_timeline_date_label(photo["year"], photo["month"], photo["photo_date"]),
                **timeline_location_payload(photo),
                "created_at": photo["created_at"],
                "image_url": image_url_builder(photo["id"]),
                "messages_url": url_for("timeline_item_messages", item_kind="photo", item_id=photo["id"]),
                "entry_ref": timeline_item_focus("photo", photo["id"]),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "guided_prompts": guided_prompts_for_item("photo", photo, people),
            }
        )
    for entry in text_rows:
        tags = text_tags.get(entry["id"], [])
        people = text_people.get(entry["id"], [])
        items.append(
            {
                "kind": "text",
                "id": entry["id"],
                "year": entry["year"],
                "month": entry["month"],
                "body": entry["body"],
                "display_date": entry["entry_date"],
                "date_label": format_timeline_date_label(entry["year"], entry["month"], entry["entry_date"]),
                **timeline_location_payload(entry),
                "created_at": entry["created_at"],
                "updated_at": entry["updated_at"],
                "entry_ref": timeline_item_focus("text", entry["id"]),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "guided_prompts": guided_prompts_for_item("text", entry, people),
            }
        )
    items.sort(key=timeline_item_sort_key)
    return attach_reactions(db, items)


def timeline_item_focus(kind, item_id):
    return f"{kind}-{item_id}"


def timeline_item_link(owner_id, year, month, kind, item_id):
    focus = timeline_item_focus(kind, item_id)
    if owner_id == g.user["id"]:
        return url_for("month_view", year=year, month=month, focus=focus)
    return url_for(
        "connection_month_view",
        connection_id=owner_id,
        year=year,
        month=month,
        focus=focus,
    )


def redirect_back(default_endpoint="timeline", **kwargs):
    target = request.form.get("next") or request.args.get("next")
    if target and target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(url_for(default_endpoint, **kwargs))


def request_includes_messages():
    return request.args.get("include_messages", "1") != "0"


def get_chapter_options(db):
    return db.execute(
        """
        SELECT id, title
        FROM chapters
        WHERE user_id = ?
        ORDER BY lower(title) ASC, id ASC
        """,
        (g.user["id"],),
    ).fetchall()


def chapter_visibility(value):
    return normalize_tag_choice(value) or DEFAULT_TAG


def parse_chapter_cover_ref(value):
    if not value:
        return None, None
    try:
        item_kind, item_id = value.split(":", 1)
        item_id = int(item_id)
    except (TypeError, ValueError):
        return None, None
    if item_kind not in ("photo", "text") or item_id <= 0:
        return None, None
    return item_kind, item_id


def chapter_cover_ref(item):
    return f"{item['kind']}:{item['id']}"


def chapter_cover_exists(db, chapter_id, item_kind, item_id):
    if not item_kind or not item_id:
        return True
    row = db.execute(
        """
        SELECT id
        FROM chapter_items
        WHERE chapter_id = ? AND item_kind = ? AND item_id = ?
        """,
        (chapter_id, item_kind, item_id),
    ).fetchone()
    return row is not None


def get_chapter_cover(db, chapter, image_url_builder, owner_id=None):
    owner_id = owner_id if owner_id is not None else g.user["id"]
    selected_kind = chapter["cover_item_kind"] if "cover_item_kind" in chapter.keys() else None
    selected_id = chapter["cover_item_id"] if "cover_item_id" in chapter.keys() else None
    row = None
    if selected_kind and selected_id:
        row = db.execute(
            """
            SELECT item_kind, item_id
            FROM chapter_items
            WHERE chapter_id = ? AND item_kind = ? AND item_id = ?
            """,
            (chapter["id"], selected_kind, selected_id),
        ).fetchone()
    if row is None:
        row = db.execute(
            """
            SELECT item_kind, item_id
            FROM chapter_items
            WHERE chapter_id = ?
            ORDER BY position ASC, id ASC
            LIMIT 1
            """,
            (chapter["id"],),
        ).fetchone()
    if row is None:
        return None

    if row["item_kind"] == "photo":
        photo = db.execute(
            """
            SELECT id, original_filename, title, caption
            FROM photos
            WHERE id = ? AND user_id = ?
            """,
            (row["item_id"], owner_id),
        ).fetchone()
        if photo is None:
            return None
        return {
            "kind": "photo",
            "image_url": image_url_builder(photo["id"]),
            "label": photo_display_title(photo),
        }

    entry = db.execute(
        """
        SELECT id, body
        FROM text_entries
        WHERE id = ? AND user_id = ?
        """,
        (row["item_id"], owner_id),
    ).fetchone()
    if entry is None:
        return None
    return {
        "kind": "text",
        "body": entry["body"],
        "label": "Text entry",
    }


def get_chapters_with_counts(db):
    rows = db.execute(
        """
        SELECT
            c.*,
            COUNT(ci.id) AS item_count
        FROM chapters c
        LEFT JOIN chapter_items ci ON ci.chapter_id = c.id
        WHERE c.user_id = ?
        GROUP BY c.id
        ORDER BY c.created_at DESC, c.id DESC
        """,
        (g.user["id"],),
    ).fetchall()
    chapters = []
    for row in rows:
        chapter = dict(row)
        chapter["visibility"] = chapter_visibility(chapter.get("visibility"))
        chapter.update(privacy_payload_for_tags([chapter["visibility"]]))
        chapter["cover"] = get_chapter_cover(
            db,
            row,
            lambda photo_id: url_for("photo_image", photo_id=photo_id),
        )
        chapters.append(chapter)
    return chapters


def parse_optional_year(value):
    try:
        year = int((value or "").strip())
    except (TypeError, ValueError):
        return None
    if year in user_years():
        return year
    return None


def chapter_draft_filters(args=None):
    args = args or request.args
    year_from = parse_optional_year(args.get("year_from"))
    year_to = parse_optional_year(args.get("year_to"))
    if year_from is not None and year_to is not None and year_from > year_to:
        year_from, year_to = year_to, year_from
    visibility = normalized_search_filter(args.get("visibility"), ("all", *TAG_CHOICES))
    return {
        "q": (args.get("q") or "").strip(),
        "person": (args.get("person") or "").strip(),
        "place": (args.get("place") or "").strip(),
        "year_from": year_from,
        "year_to": year_to,
        "visibility": visibility,
    }


def chapter_draft_has_filters(filters):
    return any(
        (
            filters["q"],
            filters["person"],
            filters["place"],
            filters["year_from"] is not None,
            filters["year_to"] is not None,
            filters["visibility"] != "all",
        )
    )


def text_matches_query(fields, query):
    if not query:
        return True
    needle = query.lower()
    return any(needle in str(field or "").lower() for field in fields)


def chapter_draft_item_matches(filters, item, tags, people):
    if filters["visibility"] != "all" and tags_to_text(tags) != filters["visibility"]:
        return False
    if filters["year_from"] is not None and item["year"] < filters["year_from"]:
        return False
    if filters["year_to"] is not None and item["year"] > filters["year_to"]:
        return False
    if filters["person"] and not text_matches_query(people, filters["person"]):
        return False
    if filters["place"] and not text_matches_query((item["location_name"],), filters["place"]):
        return False
    return True


def chapter_draft_item_payload(item, tags, people):
    date_label = format_timeline_date_label(item["year"], item["month"], item["display_date"])
    preview = item["caption"] if item["kind"] == "photo" else item["body"]
    return {
        "kind": item["kind"],
        "id": item["id"],
        "ref": f"{item['kind']}:{item['id']}",
        "year": item["year"],
        "month": item["month"],
        "date_label": date_label,
        "source_label": item_source_label(item["year"], item["month"]),
        "title": item["title"],
        "preview": short_preview(preview or "", 150),
        "location_name": item["location_name"] or "",
        "people_text": people_to_text(people),
        "privacy_label": privacy_label_for_tags(tags),
        "sort_key": timeline_item_sort_key(item),
    }


def chapter_draft_title(filters, items):
    if filters["person"]:
        return f"{filters['person']} Memories"
    if filters["place"]:
        return f"{filters['place']} Memories"
    if filters["q"]:
        return f"{filters['q']} Story"
    years = sorted({item["year"] for item in items})
    if len(years) == 1:
        return f"{years[0]} Memories"
    if years:
        return f"{years[0]}-{years[-1]} Memories"
    return "New Chapter Draft"


def chapter_draft_description(filters, items):
    labels = []
    if filters["person"]:
        labels.append(f"featuring {filters['person']}")
    if filters["place"]:
        labels.append(f"around {filters['place']}")
    if filters["q"]:
        labels.append(f"matching '{filters['q']}'")
    years = sorted({item["year"] for item in items})
    if years:
        year_label = str(years[0]) if len(years) == 1 else f"{years[0]}-{years[-1]}"
        labels.append(f"from {year_label}")
    detail = ", ".join(labels)
    if detail:
        return f"Suggested from {len(items)} timeline memories {detail}."
    return f"Suggested from {len(items)} recent timeline memories."


def build_chapter_draft(db, filters, limit=24):
    photo_rows = db.execute(
        """
        SELECT id, year, month, original_filename, title, caption, photo_date AS display_date,
               location_name, latitude, longitude, created_at
        FROM photos
        WHERE user_id = ?
        """,
        (g.user["id"],),
    ).fetchall()
    text_rows = db.execute(
        """
        SELECT id, year, month, body, entry_date AS display_date,
               location_name, latitude, longitude, created_at
        FROM text_entries
        WHERE user_id = ?
        """,
        (g.user["id"],),
    ).fetchall()
    photo_tags = load_tags_for_items(db, "photo", [row["id"] for row in photo_rows])
    text_tags = load_tags_for_items(db, "text", [row["id"] for row in text_rows])
    photo_people = load_people_for_items(db, "photo", [row["id"] for row in photo_rows])
    text_people = load_people_for_items(db, "text", [row["id"] for row in text_rows])
    items = []

    for row in photo_rows:
        tags = photo_tags.get(row["id"], [DEFAULT_TAG])
        people = photo_people.get(row["id"], [])
        item = {
            "kind": "photo",
            "id": row["id"],
            "year": row["year"],
            "month": row["month"],
            "display_date": row["display_date"],
            "title": photo_display_title(row),
            "caption": row["caption"] or "",
            "location_name": row["location_name"] or "",
        }
        if not chapter_draft_item_matches(filters, item, tags, people):
            continue
        if not text_matches_query(
            (row["original_filename"], row["title"], row["caption"], row["display_date"], item["location_name"], *people),
            filters["q"],
        ):
            continue
        items.append(chapter_draft_item_payload(item, tags, people))

    for row in text_rows:
        tags = text_tags.get(row["id"], [DEFAULT_TAG])
        people = text_people.get(row["id"], [])
        item = {
            "kind": "text",
            "id": row["id"],
            "year": row["year"],
            "month": row["month"],
            "display_date": row["display_date"],
            "title": "Text entry",
            "body": row["body"],
            "location_name": row["location_name"] or "",
        }
        if not chapter_draft_item_matches(filters, item, tags, people):
            continue
        if not text_matches_query(
            (row["body"], row["display_date"], item["location_name"], *people),
            filters["q"],
        ):
            continue
        items.append(chapter_draft_item_payload(item, tags, people))

    items.sort(key=lambda item: item["sort_key"])
    if not chapter_draft_has_filters(filters):
        items = list(reversed(items))[:limit]
        items.reverse()
    else:
        items = items[:limit]
    for item in items:
        item.pop("sort_key", None)
    return {
        "title": chapter_draft_title(filters, items),
        "description": chapter_draft_description(filters, items),
        "items": items,
    }


def parse_chapter_draft_refs(values):
    refs = []
    seen = set()
    for value in values:
        try:
            kind, raw_id = value.split(":", 1)
            item_id = int(raw_id)
        except (AttributeError, ValueError):
            continue
        if kind not in ("photo", "text") or item_id <= 0:
            continue
        key = (kind, item_id)
        if key in seen:
            continue
        seen.add(key)
        refs.append(key)
    return refs


def parse_chapter_bulk_photo_ids(value, limit=1000):
    try:
        raw_values = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        raw_values = str(value or "").split(",")
    if not isinstance(raw_values, list):
        return []

    photo_ids = []
    seen = set()
    for raw_value in raw_values:
        try:
            photo_id = int(raw_value)
        except (TypeError, ValueError):
            continue
        if photo_id <= 0 or photo_id in seen:
            continue
        seen.add(photo_id)
        photo_ids.append(photo_id)
        if len(photo_ids) >= limit:
            break
    return photo_ids


def selected_chapter_bulk_photos(db, photo_ids):
    if not photo_ids:
        return []

    placeholders = ",".join(["?"] * len(photo_ids))
    rows = db.execute(
        f"""
        SELECT id, original_filename, title, caption, photo_date, year, month
        FROM photos
        WHERE user_id = ?
          AND id IN ({placeholders})
        """,
        (g.user["id"], *photo_ids),
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    photos = []
    for photo_id in photo_ids:
        row = by_id.get(photo_id)
        if row is None:
            continue
        photos.append(
            {
                "id": row["id"],
                "title": photo_display_title(row),
                "caption": row["caption"] or "",
                "display_date": row["photo_date"] or format_timeline_date_label(row["year"], row["month"], None),
                "thumbnail_url": url_for("splash_photo_thumbnail", photo_id=row["id"]),
            }
        )
    return photos


def accepted_connection_between(db, user_id, other_user_id):
    return db.execute(
        """
        SELECT id, relation
        FROM connection_requests
        WHERE status = 'accepted'
          AND (
            (requester_id = ? AND recipient_id = ?)
            OR (requester_id = ? AND recipient_id = ?)
          )
        ORDER BY responded_at DESC, created_at DESC, id DESC
        LIMIT 1
        """,
        (user_id, other_user_id, other_user_id, user_id),
    ).fetchone()


def chapter_invites_for_chapter(db, chapter_id):
    return db.execute(
        """
        SELECT
            ci.*,
            u.username,
            u.first_name,
            u.last_name,
            u.email
        FROM chapter_invites ci
        JOIN users u ON u.id = ci.recipient_id
        WHERE ci.chapter_id = ?
        ORDER BY
            CASE ci.status WHEN 'pending' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END,
            lower(u.username) ASC
        """,
        (chapter_id,),
    ).fetchall()


def invitable_chapter_connections(db, chapter_id):
    invited_ids = {
        row["recipient_id"]
        for row in db.execute(
            "SELECT recipient_id FROM chapter_invites WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchall()
    }
    return [
        connection
        for connection in get_accepted_connections(db)
        if connection["id"] not in invited_ids
    ]


def get_incoming_chapter_invites(db):
    rows = db.execute(
        """
        SELECT
            ci.*,
            c.title,
            c.description,
            u.username,
            u.first_name,
            u.last_name
        FROM chapter_invites ci
        JOIN chapters c ON c.id = ci.chapter_id
        JOIN users u ON u.id = ci.inviter_id
        WHERE ci.recipient_id = ? AND ci.status = 'pending'
        ORDER BY ci.created_at ASC, ci.id ASC
        """,
        (g.user["id"],),
    ).fetchall()
    return [
        {
            **dict(row),
            "inviter_name": user_full_name(row) or row["username"],
        }
        for row in rows
    ]


def get_shared_chapters(db):
    rows = db.execute(
        """
        SELECT
            ci.id AS invite_id,
            ci.created_at AS invited_at,
            c.*,
            u.username,
            u.first_name,
            u.last_name
        FROM chapter_invites ci
        JOIN chapters c ON c.id = ci.chapter_id
        JOIN users u ON u.id = c.user_id
        WHERE ci.recipient_id = ? AND ci.status = 'accepted'
        ORDER BY COALESCE(c.updated_at, ci.responded_at, ci.created_at) DESC, ci.id DESC
        """,
        (g.user["id"],),
    ).fetchall()
    chapters = []
    for row in rows:
        chapter = dict(row)
        chapter["owner_name"] = user_full_name(row) or row["username"]
        chapter["cover"] = get_chapter_cover(
            db,
            row,
            lambda photo_id, chapter_id=row["id"]: url_for(
                "shared_chapter_photo_image",
                chapter_id=chapter_id,
                photo_id=photo_id,
            ),
            owner_id=row["user_id"],
        )
        chapters.append(chapter)
    return chapters


def get_shared_chapter(chapter_id):
    row = get_db().execute(
        """
        SELECT
            ci.id AS invite_id,
            c.*,
            u.username,
            u.first_name,
            u.last_name
        FROM chapter_invites ci
        JOIN chapters c ON c.id = ci.chapter_id
        JOIN users u ON u.id = c.user_id
        WHERE ci.chapter_id = ?
          AND ci.recipient_id = ?
          AND ci.status = 'accepted'
        """,
        (chapter_id, g.user["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    chapter = dict(row)
    chapter["owner_name"] = user_full_name(row) or row["username"]
    return chapter


def shared_chapter_contains_item(db, chapter_id, item_kind, item_id, recipient_id=None):
    recipient_id = recipient_id if recipient_id is not None else g.user["id"]
    row = db.execute(
        """
        SELECT c.*
        FROM chapter_invites ci
        JOIN chapters c ON c.id = ci.chapter_id
        JOIN chapter_items ch_item ON ch_item.chapter_id = c.id
        WHERE ci.chapter_id = ?
          AND ci.recipient_id = ?
          AND ci.status = 'accepted'
          AND ch_item.item_kind = ?
          AND ch_item.item_id = ?
        """,
        (chapter_id, recipient_id, item_kind, item_id),
    ).fetchone()
    return row


def get_shared_chapter_item_access(chapter_id, item_kind, item_id):
    if item_kind not in ("photo", "text"):
        abort(404)
    db = get_db()
    chapter = shared_chapter_contains_item(db, chapter_id, item_kind, item_id)
    if chapter is None:
        abort(404)
    return chapter


def get_owned_chapter(chapter_id):
    chapter = get_db().execute(
        """
        SELECT *
        FROM chapters
        WHERE id = ? AND user_id = ?
        """,
        (chapter_id, g.user["id"]),
    ).fetchone()
    if chapter is None:
        abort(404)
    return chapter


def get_owned_chapter_item(db, chapter_id, chapter_item_id):
    item = db.execute(
        """
        SELECT ci.*
        FROM chapter_items ci
        JOIN chapters c ON c.id = ci.chapter_id
        WHERE ci.id = ? AND ci.chapter_id = ? AND c.user_id = ?
        """,
        (chapter_item_id, chapter_id, g.user["id"]),
    ).fetchone()
    if item is None:
        abort(404)
    return item


def next_chapter_position(db, chapter_id):
    row = db.execute(
        """
        SELECT COALESCE(MAX(position), 0) + 1 AS next_position
        FROM chapter_items
        WHERE chapter_id = ?
        """,
        (chapter_id,),
    ).fetchone()
    return row["next_position"]


def compact_chapter_positions(db, chapter_id):
    rows = db.execute(
        """
        SELECT id
        FROM chapter_items
        WHERE chapter_id = ?
        ORDER BY position ASC, id ASC
        """,
        (chapter_id,),
    ).fetchall()
    for index, row in enumerate(rows, start=1):
        db.execute(
            "UPDATE chapter_items SET position = ? WHERE id = ?",
            (index, row["id"]),
        )


def move_chapter_item(db, chapter_id, chapter_item_id, direction):
    rows = db.execute(
        """
        SELECT id, position
        FROM chapter_items
        WHERE chapter_id = ?
        ORDER BY position ASC, id ASC
        """,
        (chapter_id,),
    ).fetchall()
    item_ids = [row["id"] for row in rows]
    if chapter_item_id not in item_ids:
        abort(404)

    current_index = item_ids.index(chapter_item_id)
    if direction == "up":
        target_index = current_index - 1
    elif direction == "down":
        target_index = current_index + 1
    else:
        abort(400)

    if target_index < 0 or target_index >= len(rows):
        return False

    current = rows[current_index]
    target = rows[target_index]
    db.execute(
        "UPDATE chapter_items SET position = ? WHERE id = ?",
        (target["position"], current["id"]),
    )
    db.execute(
        "UPDATE chapter_items SET position = ? WHERE id = ?",
        (current["position"], target["id"]),
    )
    return True


def item_source_label(year, month):
    return f"{MONTH_NAMES[month - 1]} {year}"


def build_chapter_items(
    db,
    chapter_id,
    image_url_builder,
    message_url_builder=None,
    can_message=False,
    owner_id=None,
    item_url_builder=None,
    reaction_url_builder=None,
    include_messages=True,
):
    owner_id = owner_id if owner_id is not None else g.user["id"]
    refs = db.execute(
        """
        SELECT id, item_kind, item_id, position
        FROM chapter_items
        WHERE chapter_id = ?
        ORDER BY position ASC, id ASC
        """,
        (chapter_id,),
    ).fetchall()
    if not refs:
        return []

    photo_ids = [row["item_id"] for row in refs if row["item_kind"] == "photo"]
    text_ids = [row["item_id"] for row in refs if row["item_kind"] == "text"]

    photo_map = {}
    if photo_ids:
        placeholders = ",".join(["?"] * len(photo_ids))
        rows = db.execute(
            f"""
            SELECT id, year, month, original_filename, title, caption, photo_date, created_at
            FROM photos
            WHERE user_id = ? AND id IN ({placeholders})
            """,
            (owner_id, *photo_ids),
        ).fetchall()
        photo_map = {row["id"]: row for row in rows}

    text_map = {}
    if text_ids:
        placeholders = ",".join(["?"] * len(text_ids))
        rows = db.execute(
            f"""
            SELECT id, year, month, body, entry_date, created_at, updated_at
            FROM text_entries
            WHERE user_id = ? AND id IN ({placeholders})
            """,
            (owner_id, *text_ids),
        ).fetchall()
        text_map = {row["id"]: row for row in rows}

    photo_tags = load_tags_for_items(db, "photo", photo_ids, owner_id)
    text_tags = load_tags_for_items(db, "text", text_ids, owner_id)
    photo_people = load_people_for_items(db, "photo", photo_ids, owner_id)
    text_people = load_people_for_items(db, "text", text_ids, owner_id)
    items = []
    for ref in refs:
        if ref["item_kind"] == "photo":
            photo = photo_map.get(ref["item_id"])
            if photo is None:
                continue
            tags = photo_tags.get(photo["id"], [])
            people = photo_people.get(photo["id"], [])
            messages_url = message_url_builder("photo", photo["id"]) if message_url_builder else ""
            item_url = (
                item_url_builder("photo", photo["id"])
                if item_url_builder
                else timeline_item_link(owner_id, photo["year"], photo["month"], "photo", photo["id"])
            )
            item = {
                "kind": "photo",
                "id": photo["id"],
                "chapter_item_id": ref["id"],
                "position": ref["position"],
                "year": photo["year"],
                "month": photo["month"],
                "display_date": photo["photo_date"],
                "date_label": format_timeline_date_label(photo["year"], photo["month"], photo["photo_date"]),
                "created_at": photo["created_at"],
                "source_label": item_source_label(photo["year"], photo["month"]),
                "url": item_url,
                "image_url": image_url_builder(photo["id"]),
                "caption": photo["caption"] or "",
                "messages_url": messages_url,
                "can_message": can_message and bool(messages_url),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "title": photo_display_title(photo),
            }
            if include_messages:
                item["messages"] = load_messages_for_timeline_item(db, "photo", photo["id"]) if message_url_builder else []
            items.append(item)
        else:
            entry = text_map.get(ref["item_id"])
            if entry is None:
                continue
            tags = text_tags.get(entry["id"], [])
            people = text_people.get(entry["id"], [])
            messages_url = message_url_builder("text", entry["id"]) if message_url_builder else ""
            item_url = (
                item_url_builder("text", entry["id"])
                if item_url_builder
                else timeline_item_link(owner_id, entry["year"], entry["month"], "text", entry["id"])
            )
            item = {
                "kind": "text",
                "id": entry["id"],
                "chapter_item_id": ref["id"],
                "position": ref["position"],
                "year": entry["year"],
                "month": entry["month"],
                "display_date": entry["entry_date"],
                "date_label": format_timeline_date_label(entry["year"], entry["month"], entry["entry_date"]),
                "created_at": entry["created_at"],
                "source_label": item_source_label(entry["year"], entry["month"]),
                "url": item_url,
                "body": entry["body"],
                "messages_url": messages_url,
                "can_message": can_message and bool(messages_url),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "title": "Text entry",
            }
            if include_messages:
                item["messages"] = load_messages_for_timeline_item(db, "text", entry["id"]) if message_url_builder else []
            items.append(item)
    attach_reactions(db, items)
    if reaction_url_builder:
        for item in items:
            item["reactions"]["reaction_url"] = reaction_url_builder(item["kind"], item["id"])
    return items


def get_unread_message_notifications(db):
    photo_rows = db.execute(
        """
        SELECT
            'photo' AS item_kind,
            m.id AS message_id,
            m.body,
            m.created_at,
            p.id AS item_id,
            p.year,
            p.month,
            p.photo_date AS item_date,
            COALESCE(NULLIF(p.title, ''), p.original_filename, 'Photo') AS item_title,
            p.user_id AS owner_id,
            u.username,
            u.first_name,
            u.last_name
        FROM messages m
        JOIN photos p ON p.id = m.photo_id
        JOIN users u ON u.id = m.user_id
        LEFT JOIN message_notification_reads mnr
          ON mnr.user_id = ?
         AND mnr.message_kind = 'photo'
         AND mnr.message_id = m.id
        WHERE p.user_id = ?
          AND m.user_id <> ?
          AND mnr.message_id IS NULL
        """,
        (g.user["id"], g.user["id"], g.user["id"]),
    ).fetchall()
    text_rows = db.execute(
        """
        SELECT
            'text' AS item_kind,
            tem.id AS message_id,
            tem.body,
            tem.created_at,
            te.id AS item_id,
            te.year,
            te.month,
            te.entry_date AS item_date,
            'Text entry' AS item_title,
            te.user_id AS owner_id,
            u.username,
            u.first_name,
            u.last_name
        FROM text_entry_messages tem
        JOIN text_entries te ON te.id = tem.entry_id
        JOIN users u ON u.id = tem.user_id
        LEFT JOIN message_notification_reads mnr
          ON mnr.user_id = ?
         AND mnr.message_kind = 'text'
         AND mnr.message_id = tem.id
        WHERE te.user_id = ?
          AND tem.user_id <> ?
          AND mnr.message_id IS NULL
        """,
        (g.user["id"], g.user["id"], g.user["id"]),
    ).fetchall()

    notifications = []
    for row in [*photo_rows, *text_rows]:
        actor_name = message_author_name(row)
        item_label = row["item_title"] or "Photo"
        notifications.append(
            {
                "kind": row["item_kind"],
                "message_id": row["message_id"],
                "actor_name": actor_name,
                "body": row["body"],
                "preview": short_preview(row["body"]),
                "created_at": row["created_at"],
                "item_label": item_label,
                "item_date": row["item_date"] or format_timeline_date_label(row["year"], row["month"], None),
                "url": timeline_item_link(
                    row["owner_id"],
                    row["year"],
                    row["month"],
                    row["item_kind"],
                    row["item_id"],
                ),
            }
        )
    notifications.sort(key=lambda item: item["created_at"], reverse=True)
    return notifications


def get_unread_message_notification_count(db):
    photo_row = db.execute(
        """
        SELECT COUNT(*) AS unread_count
        FROM messages m
        JOIN photos p ON p.id = m.photo_id
        LEFT JOIN message_notification_reads mnr
          ON mnr.user_id = ?
         AND mnr.message_kind = 'photo'
         AND mnr.message_id = m.id
        WHERE p.user_id = ?
          AND m.user_id <> ?
          AND mnr.message_id IS NULL
        """,
        (g.user["id"], g.user["id"], g.user["id"]),
    ).fetchone()
    text_row = db.execute(
        """
        SELECT COUNT(*) AS unread_count
        FROM text_entry_messages tem
        JOIN text_entries te ON te.id = tem.entry_id
        LEFT JOIN message_notification_reads mnr
          ON mnr.user_id = ?
         AND mnr.message_kind = 'text'
         AND mnr.message_id = tem.id
        WHERE te.user_id = ?
          AND tem.user_id <> ?
          AND mnr.message_id IS NULL
        """,
        (g.user["id"], g.user["id"], g.user["id"]),
    ).fetchone()
    return (photo_row["unread_count"] if photo_row else 0) + (text_row["unread_count"] if text_row else 0)


def mark_message_notifications_read(db, notifications):
    for notification in notifications:
        db.execute(
            """
            INSERT OR IGNORE INTO message_notification_reads (user_id, message_kind, message_id)
            VALUES (?, ?, ?)
            """,
            (g.user["id"], notification["kind"], notification["message_id"]),
        )
    if notifications:
        db.commit()


def get_unread_reaction_notifications(db):
    photo_rows = db.execute(
        """
        SELECT
            'photo' AS item_kind,
            ir.id AS reaction_id,
            ir.reaction,
            ir.created_at,
            p.id AS item_id,
            p.year,
            p.month,
            p.photo_date AS item_date,
            COALESCE(NULLIF(p.title, ''), p.original_filename, 'Photo') AS item_title,
            p.user_id AS owner_id,
            u.username,
            u.first_name,
            u.last_name
        FROM item_reactions ir
        JOIN photos p ON ir.item_kind = 'photo' AND p.id = ir.item_id
        JOIN users u ON u.id = ir.user_id
        LEFT JOIN reaction_notification_reads rnr
          ON rnr.user_id = ?
         AND rnr.reaction_id = ir.id
        WHERE p.user_id = ?
          AND ir.user_id <> ?
          AND (rnr.reaction_id IS NULL OR rnr.read_at < ir.created_at)
        """,
        (g.user["id"], g.user["id"], g.user["id"]),
    ).fetchall()
    text_rows = db.execute(
        """
        SELECT
            'text' AS item_kind,
            ir.id AS reaction_id,
            ir.reaction,
            ir.created_at,
            te.id AS item_id,
            te.year,
            te.month,
            te.entry_date AS item_date,
            'Text entry' AS item_title,
            te.body AS body,
            te.user_id AS owner_id,
            u.username,
            u.first_name,
            u.last_name
        FROM item_reactions ir
        JOIN text_entries te ON ir.item_kind = 'text' AND te.id = ir.item_id
        JOIN users u ON u.id = ir.user_id
        LEFT JOIN reaction_notification_reads rnr
          ON rnr.user_id = ?
         AND rnr.reaction_id = ir.id
        WHERE te.user_id = ?
          AND ir.user_id <> ?
          AND (rnr.reaction_id IS NULL OR rnr.read_at < ir.created_at)
        """,
        (g.user["id"], g.user["id"], g.user["id"]),
    ).fetchall()

    notifications = []
    for row in [*photo_rows, *text_rows]:
        item_label = row["item_title"] or "Photo"
        reaction_label = "liked" if row["reaction"] == "like" else "loved"
        notifications.append(
            {
                "kind": row["item_kind"],
                "reaction_id": row["reaction_id"],
                "reaction": row["reaction"],
                "reaction_label": reaction_label,
                "actor_name": message_author_name(row),
                "created_at": row["created_at"],
                "item_label": item_label,
                "item_date": row["item_date"] or format_timeline_date_label(row["year"], row["month"], None),
                "preview": short_preview(row["body"] if row["item_kind"] == "text" else item_label),
                "url": timeline_item_link(
                    row["owner_id"],
                    row["year"],
                    row["month"],
                    row["item_kind"],
                    row["item_id"],
                ),
            }
        )
    notifications.sort(key=lambda item: item["created_at"], reverse=True)
    return notifications


def get_unread_reaction_notification_count(db):
    photo_row = db.execute(
        """
        SELECT COUNT(*) AS unread_count
        FROM item_reactions ir
        JOIN photos p ON ir.item_kind = 'photo' AND p.id = ir.item_id
        LEFT JOIN reaction_notification_reads rnr
          ON rnr.user_id = ?
         AND rnr.reaction_id = ir.id
        WHERE p.user_id = ?
          AND ir.user_id <> ?
          AND (rnr.reaction_id IS NULL OR rnr.read_at < ir.created_at)
        """,
        (g.user["id"], g.user["id"], g.user["id"]),
    ).fetchone()
    text_row = db.execute(
        """
        SELECT COUNT(*) AS unread_count
        FROM item_reactions ir
        JOIN text_entries te ON ir.item_kind = 'text' AND te.id = ir.item_id
        LEFT JOIN reaction_notification_reads rnr
          ON rnr.user_id = ?
         AND rnr.reaction_id = ir.id
        WHERE te.user_id = ?
          AND ir.user_id <> ?
          AND (rnr.reaction_id IS NULL OR rnr.read_at < ir.created_at)
        """,
        (g.user["id"], g.user["id"], g.user["id"]),
    ).fetchone()
    return (photo_row["unread_count"] if photo_row else 0) + (text_row["unread_count"] if text_row else 0)


def mark_reaction_notifications_read(db, notifications):
    for notification in notifications:
        db.execute(
            """
            INSERT INTO reaction_notification_reads (user_id, reaction_id, read_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, reaction_id)
            DO UPDATE SET read_at = CURRENT_TIMESTAMP
            """,
            (g.user["id"], notification["reaction_id"]),
        )
    if notifications:
        db.commit()


def get_accepted_connections(db):
    rows = db.execute(
        """
        SELECT
            cr.id AS request_id,
            u.id AS user_id,
            cr.relation,
            u.username,
            u.first_name,
            u.last_name,
            u.email
        FROM connection_requests cr
        JOIN users u ON u.id = cr.recipient_id
        WHERE cr.requester_id = ? AND cr.status = 'accepted'
        UNION ALL
        SELECT
            cr.id AS request_id,
            u.id AS user_id,
            cr.relation,
            u.username,
            u.first_name,
            u.last_name,
            u.email
        FROM connection_requests cr
        JOIN users u ON u.id = cr.requester_id
        WHERE cr.recipient_id = ? AND cr.status = 'accepted'
        ORDER BY username ASC
        """,
        (g.user["id"], g.user["id"]),
    ).fetchall()
    return [
        {
            "request_id": row["request_id"],
            "id": row["user_id"],
            "username": row["username"],
            "full_name": user_full_name(row),
            "email": row["email"] or "",
            "relation": row["relation"],
            "allowed_tags": CONNECTION_VISIBLE_TAGS.get(row["relation"], ("public",)),
        }
        for row in rows
    ]


def actor_label(user_id, name):
    return "You" if user_id == g.user["id"] else name


def owner_label(owner_id, name):
    return "your" if owner_id == g.user["id"] else f"{name}'s"


def add_activity_event(feed_items, *, created_at, type_label, summary, meta="", preview="", url=""):
    if not created_at:
        return
    feed_items.append(
        {
            "created_at": created_at,
            "type_label": type_label,
            "summary": summary,
            "item_date": meta,
            "preview": short_preview(preview or ""),
            "url": url,
        }
    )


def add_activity_item(feed_items, *, created_at, actor_id, actor_name, owner_id, owner_name, action, item_kind, item_id, year, month, display_date=None, body=None, type_label="Timeline"):
    if item_kind == "photo":
        target_label = "photo"
    else:
        target_label = "text entry"
    add_activity_event(
        feed_items,
        created_at=created_at,
        type_label=type_label,
        summary=action.format(
            actor=actor_label(actor_id, actor_name),
            owner=owner_label(owner_id, owner_name),
            item=target_label,
        ),
        meta=display_date or format_timeline_date_label(year, month, None),
        preview=body,
        url=timeline_item_link(owner_id, year, month, item_kind, item_id),
    )


def get_activity_feed(db, limit=60):
    connections = get_accepted_connections(db)
    connection_by_id = {connection["id"]: connection for connection in connections}
    visible_owner_ids = [g.user["id"], *connection_by_id.keys()]
    if not visible_owner_ids:
        return []

    placeholders = ",".join(["?"] * len(visible_owner_ids))
    user_rows = db.execute(
        f"""
        SELECT id, username, first_name, last_name
        FROM users
        WHERE id IN ({placeholders})
        """,
        tuple(visible_owner_ids),
    ).fetchall()
    user_names = {row["id"]: (user_full_name(row) or row["username"]) for row in user_rows}
    user_names[g.user["id"]] = user_full_name(g.user) or g.user["username"]

    feed_items = []

    for owner_id in visible_owner_ids:
        allowed_tags = None if owner_id == g.user["id"] else connection_by_id[owner_id]["allowed_tags"]
        photo_rows = db.execute(
            """
            SELECT id, year, month, original_filename, title, caption, photo_date, created_at
            FROM photos
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 40
            """,
            (owner_id,),
        ).fetchall()
        photo_tags = load_tags_for_items(db, "photo", [row["id"] for row in photo_rows], owner_id)
        for row in photo_rows:
            if not tags_visible_to_connection(photo_tags.get(row["id"], []), allowed_tags):
                continue
            add_activity_item(
                feed_items,
                created_at=row["created_at"],
                actor_id=owner_id,
                actor_name=user_names[owner_id],
                owner_id=owner_id,
                owner_name=user_names[owner_id],
                action="{actor} added a {item}",
                item_kind="photo",
                item_id=row["id"],
                year=row["year"],
                month=row["month"],
                display_date=row["photo_date"],
                body=row["caption"] or photo_display_title(row),
                type_label="Upload",
            )

        text_rows = db.execute(
            """
            SELECT id, year, month, body, entry_date, created_at
            FROM text_entries
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 40
            """,
            (owner_id,),
        ).fetchall()
        text_tags = load_tags_for_items(db, "text", [row["id"] for row in text_rows], owner_id)
        for row in text_rows:
            if not tags_visible_to_connection(text_tags.get(row["id"], []), allowed_tags):
                continue
            add_activity_item(
                feed_items,
                created_at=row["created_at"],
                actor_id=owner_id,
                actor_name=user_names[owner_id],
                owner_id=owner_id,
                owner_name=user_names[owner_id],
                action="{actor} added a {item}",
                item_kind="text",
                item_id=row["id"],
                year=row["year"],
                month=row["month"],
                display_date=row["entry_date"],
                body=row["body"],
                type_label="Text",
            )

    message_photo_rows = db.execute(
        f"""
        SELECT
            m.id AS message_id,
            m.body,
            m.created_at,
            m.user_id AS actor_id,
            p.id AS item_id,
            p.user_id AS owner_id,
            p.year,
            p.month,
            p.photo_date AS item_date,
            u.username,
            u.first_name,
            u.last_name
        FROM messages m
        JOIN photos p ON p.id = m.photo_id
        JOIN users u ON u.id = m.user_id
        WHERE p.user_id IN ({placeholders})
        ORDER BY m.created_at DESC
        LIMIT 80
        """,
        tuple(visible_owner_ids),
    ).fetchall()
    for row in message_photo_rows:
        owner_id = row["owner_id"]
        allowed_tags = None if owner_id == g.user["id"] else connection_by_id[owner_id]["allowed_tags"]
        tags = get_tags_for_item(db, "photo", row["item_id"], owner_id)
        if not tags_visible_to_connection(tags, allowed_tags):
            continue
        actor_name = user_full_name(row) or row["username"]
        add_activity_item(
            feed_items,
            created_at=row["created_at"],
            actor_id=row["actor_id"],
            actor_name=actor_name,
            owner_id=owner_id,
            owner_name=user_names.get(owner_id, "Someone"),
            action="{actor} commented on {owner} {item}",
            item_kind="photo",
            item_id=row["item_id"],
            year=row["year"],
            month=row["month"],
            display_date=row["item_date"],
            body=row["body"],
            type_label="Comment",
        )

    message_text_rows = db.execute(
        f"""
        SELECT
            tem.id AS message_id,
            tem.body,
            tem.created_at,
            tem.user_id AS actor_id,
            te.id AS item_id,
            te.user_id AS owner_id,
            te.year,
            te.month,
            te.entry_date AS item_date,
            u.username,
            u.first_name,
            u.last_name
        FROM text_entry_messages tem
        JOIN text_entries te ON te.id = tem.entry_id
        JOIN users u ON u.id = tem.user_id
        WHERE te.user_id IN ({placeholders})
        ORDER BY tem.created_at DESC
        LIMIT 80
        """,
        tuple(visible_owner_ids),
    ).fetchall()
    for row in message_text_rows:
        owner_id = row["owner_id"]
        allowed_tags = None if owner_id == g.user["id"] else connection_by_id[owner_id]["allowed_tags"]
        tags = get_tags_for_item(db, "text", row["item_id"], owner_id)
        if not tags_visible_to_connection(tags, allowed_tags):
            continue
        actor_name = user_full_name(row) or row["username"]
        add_activity_item(
            feed_items,
            created_at=row["created_at"],
            actor_id=row["actor_id"],
            actor_name=actor_name,
            owner_id=owner_id,
            owner_name=user_names.get(owner_id, "Someone"),
            action="{actor} commented on {owner} {item}",
            item_kind="text",
            item_id=row["item_id"],
            year=row["year"],
            month=row["month"],
            display_date=row["item_date"],
            body=row["body"],
            type_label="Comment",
        )

    reaction_rows = db.execute(
        f"""
        SELECT
            ir.reaction,
            ir.created_at,
            ir.user_id AS actor_id,
            ir.item_kind,
            ir.item_id,
            COALESCE(p.user_id, te.user_id) AS owner_id,
            COALESCE(p.year, te.year) AS year,
            COALESCE(p.month, te.month) AS month,
            COALESCE(p.photo_date, te.entry_date) AS item_date,
            COALESCE(NULLIF(p.title, ''), p.original_filename, te.body, 'Text entry') AS preview,
            u.username,
            u.first_name,
            u.last_name
        FROM item_reactions ir
        LEFT JOIN photos p ON ir.item_kind = 'photo' AND p.id = ir.item_id
        LEFT JOIN text_entries te ON ir.item_kind = 'text' AND te.id = ir.item_id
        JOIN users u ON u.id = ir.user_id
        WHERE COALESCE(p.user_id, te.user_id) IN ({placeholders})
        ORDER BY ir.created_at DESC
        LIMIT 80
        """,
        tuple(visible_owner_ids),
    ).fetchall()
    for row in reaction_rows:
        owner_id = row["owner_id"]
        allowed_tags = None if owner_id == g.user["id"] else connection_by_id[owner_id]["allowed_tags"]
        tags = get_tags_for_item(db, row["item_kind"], row["item_id"], owner_id)
        if not tags_visible_to_connection(tags, allowed_tags):
            continue
        actor_name = user_full_name(row) or row["username"]
        reaction_word = "liked" if row["reaction"] == "like" else "loved"
        add_activity_item(
            feed_items,
            created_at=row["created_at"],
            actor_id=row["actor_id"],
            actor_name=actor_name,
            owner_id=owner_id,
            owner_name=user_names.get(owner_id, "Someone"),
            action=f"{{actor}} {reaction_word} {{owner}} {{item}}",
            item_kind=row["item_kind"],
            item_id=row["item_id"],
            year=row["year"],
            month=row["month"],
            display_date=row["item_date"],
            body=row["preview"],
            type_label="Reaction",
        )

    chapter_rows = db.execute(
        """
        SELECT id, title, description, created_at, updated_at
        FROM chapters
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 60
        """,
        (g.user["id"],),
    ).fetchall()
    for row in chapter_rows:
        add_activity_event(
            feed_items,
            created_at=row["created_at"],
            type_label="Chapter",
            summary=f"You created chapter \"{row['title']}\"",
            meta=row["created_at"],
            preview=row["description"] or "",
            url=url_for("chapter_detail", chapter_id=row["id"]),
        )
        if row["updated_at"]:
            add_activity_event(
                feed_items,
                created_at=row["updated_at"],
                type_label="Chapter",
                summary=f"You updated chapter \"{row['title']}\"",
                meta=row["updated_at"],
                preview=row["description"] or "",
                url=url_for("chapter_detail", chapter_id=row["id"]),
            )

    chapter_item_rows = db.execute(
        """
        SELECT
            ci.item_kind,
            ci.item_id,
            ci.created_at,
            c.id AS chapter_id,
            c.title AS chapter_title,
            COALESCE(p.year, te.year) AS year,
            COALESCE(p.month, te.month) AS month,
            COALESCE(p.photo_date, te.entry_date) AS item_date,
            COALESCE(NULLIF(p.title, ''), p.original_filename, te.body, 'Text entry') AS preview
        FROM chapter_items ci
        JOIN chapters c ON c.id = ci.chapter_id
        LEFT JOIN photos p ON ci.item_kind = 'photo' AND p.id = ci.item_id
        LEFT JOIN text_entries te ON ci.item_kind = 'text' AND te.id = ci.item_id
        WHERE c.user_id = ?
        ORDER BY ci.created_at DESC
        LIMIT 60
        """,
        (g.user["id"],),
    ).fetchall()
    for row in chapter_item_rows:
        target_label = "photo" if row["item_kind"] == "photo" else "text entry"
        add_activity_event(
            feed_items,
            created_at=row["created_at"],
            type_label="Chapter",
            summary=f"You added a {target_label} to \"{row['chapter_title']}\"",
            meta=row["item_date"] or format_timeline_date_label(row["year"], row["month"], None),
            preview=row["preview"],
            url=url_for("chapter_detail", chapter_id=row["chapter_id"]),
        )

    chapter_invite_rows = db.execute(
        """
        SELECT
            ci.*,
            c.title AS chapter_title,
            inviter.username AS inviter_username,
            inviter.first_name AS inviter_first_name,
            inviter.last_name AS inviter_last_name,
            recipient.username AS recipient_username,
            recipient.first_name AS recipient_first_name,
            recipient.last_name AS recipient_last_name
        FROM chapter_invites ci
        JOIN chapters c ON c.id = ci.chapter_id
        JOIN users inviter ON inviter.id = ci.inviter_id
        JOIN users recipient ON recipient.id = ci.recipient_id
        WHERE ci.inviter_id = ? OR ci.recipient_id = ?
        ORDER BY ci.created_at DESC
        LIMIT 80
        """,
        (g.user["id"], g.user["id"]),
    ).fetchall()
    for row in chapter_invite_rows:
        inviter_name = user_full_name(
            {
                "first_name": row["inviter_first_name"],
                "last_name": row["inviter_last_name"],
            }
        ) or row["inviter_username"]
        recipient_name = user_full_name(
            {
                "first_name": row["recipient_first_name"],
                "last_name": row["recipient_last_name"],
            }
        ) or row["recipient_username"]
        if row["inviter_id"] == g.user["id"]:
            created_summary = f"You invited {recipient_name} to \"{row['chapter_title']}\""
            url = url_for("chapter_detail", chapter_id=row["chapter_id"])
        else:
            created_summary = f"{inviter_name} invited you to \"{row['chapter_title']}\""
            url = (
                url_for("shared_chapter_detail", chapter_id=row["chapter_id"])
                if row["status"] == "accepted"
                else url_for("notifications")
            )
        add_activity_event(
            feed_items,
            created_at=row["created_at"],
            type_label="Share",
            summary=created_summary,
            meta=row["status"],
            url=url,
        )
        if row["responded_at"]:
            if row["recipient_id"] == g.user["id"]:
                response_summary = f"You {row['status']} the invite to \"{row['chapter_title']}\""
            else:
                response_summary = f"{recipient_name} {row['status']} your invite to \"{row['chapter_title']}\""
            add_activity_event(
                feed_items,
                created_at=row["responded_at"],
                type_label="Share",
                summary=response_summary,
                meta=row["status"],
                url=url,
            )

    connection_rows = db.execute(
        """
        SELECT
            cr.*,
            requester.username AS requester_username,
            requester.first_name AS requester_first_name,
            requester.last_name AS requester_last_name,
            recipient.username AS recipient_username,
            recipient.first_name AS recipient_first_name,
            recipient.last_name AS recipient_last_name
        FROM connection_requests cr
        JOIN users requester ON requester.id = cr.requester_id
        JOIN users recipient ON recipient.id = cr.recipient_id
        WHERE cr.requester_id = ? OR cr.recipient_id = ?
        ORDER BY cr.created_at DESC
        LIMIT 80
        """,
        (g.user["id"], g.user["id"]),
    ).fetchall()
    for row in connection_rows:
        requester_name = user_full_name(
            {
                "first_name": row["requester_first_name"],
                "last_name": row["requester_last_name"],
            }
        ) or row["requester_username"]
        recipient_name = user_full_name(
            {
                "first_name": row["recipient_first_name"],
                "last_name": row["recipient_last_name"],
            }
        ) or row["recipient_username"]
        if row["requester_id"] == g.user["id"]:
            created_summary = f"You sent {recipient_name} a connection request"
        else:
            created_summary = f"{requester_name} sent you a connection request"
        add_activity_event(
            feed_items,
            created_at=row["created_at"],
            type_label="Connection",
            summary=created_summary,
            meta=row["relation"],
            url=url_for("connections"),
        )
        if row["responded_at"]:
            if row["recipient_id"] == g.user["id"]:
                response_summary = f"You {row['status']} {requester_name}'s connection request"
            else:
                response_summary = f"{recipient_name} {row['status']} your connection request"
            add_activity_event(
                feed_items,
                created_at=row["responded_at"],
                type_label="Connection",
                summary=response_summary,
                meta=row["relation"],
                url=url_for("connections"),
            )

    feed_items.sort(key=lambda item: item["created_at"], reverse=True)
    return feed_items[:limit]


def get_notification_count(db):
    row = db.execute(
        """
        SELECT COUNT(*) AS notification_count
        FROM connection_requests
        WHERE recipient_id = ? AND status = 'pending'
        """,
        (g.user["id"],),
    ).fetchone()
    connection_count = row["notification_count"] if row is not None else 0
    row = db.execute(
        """
        SELECT COUNT(*) AS invite_count
        FROM chapter_invites
        WHERE recipient_id = ? AND status = 'pending'
        """,
        (g.user["id"],),
    ).fetchone()
    chapter_invite_count = row["invite_count"] if row is not None else 0
    return (
        connection_count
        + chapter_invite_count
        + get_unread_message_notification_count(db)
        + get_unread_reaction_notification_count(db)
    )


def get_incoming_connection_requests(db):
    rows = db.execute(
        """
        SELECT
            cr.id,
            cr.relation,
            cr.created_at,
            u.username,
            u.first_name,
            u.last_name,
            u.email
        FROM connection_requests cr
        JOIN users u ON u.id = cr.requester_id
        WHERE cr.recipient_id = ? AND cr.status = 'pending'
        ORDER BY cr.created_at ASC, cr.id ASC
        """,
        (g.user["id"],),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "relation": row["relation"],
            "created_at": row["created_at"],
            "username": row["username"],
            "full_name": user_full_name(row),
            "email": row["email"] or "",
        }
        for row in rows
    ]


def active_connection_request_between(db, other_user_id):
    return db.execute(
        """
        SELECT *
        FROM connection_requests
        WHERE status IN ('pending', 'accepted')
          AND (
            (requester_id = ? AND recipient_id = ?)
            OR (requester_id = ? AND recipient_id = ?)
          )
        ORDER BY
            CASE status WHEN 'accepted' THEN 0 ELSE 1 END,
            created_at DESC,
            id DESC
        LIMIT 1
        """,
        (g.user["id"], other_user_id, other_user_id, g.user["id"]),
    ).fetchone()


def connection_state_for_user(db, other_user_id):
    request_row = active_connection_request_between(db, other_user_id)
    if request_row is None:
        return {"status": "none", "label": "", "can_request": True}
    if request_row["status"] == "accepted":
        return {"status": "connected", "label": "Connected", "can_request": False}
    if request_row["requester_id"] == g.user["id"]:
        return {"status": "pending_sent", "label": "Request sent", "can_request": False}
    return {"status": "pending_received", "label": "Incoming request", "can_request": False}


def search_people(db, query):
    normalized_query = (query or "").strip().lower()
    if not normalized_query:
        return []

    pattern = f"%{normalized_query}%"
    rows = db.execute(
        """
        SELECT id, username, first_name, last_name, email
        FROM users
        WHERE id <> ?
          AND (
            lower(username) LIKE ?
            OR lower(COALESCE(email, '')) LIKE ?
            OR lower(trim(first_name || ' ' || last_name)) LIKE ?
          )
        ORDER BY username ASC
        LIMIT 50
        """,
        (g.user["id"], pattern, pattern, pattern),
    ).fetchall()

    results = []
    for row in rows:
        person = public_user_payload(row)
        person["connection_state"] = connection_state_for_user(db, row["id"])
        results.append(person)
    return results


TIMELINE_SEARCH_KIND_CHOICES = ("all", "photo", "text", "message", "chapter")
TIMELINE_SEARCH_TRI_CHOICES = ("all", "with", "without")
TIMELINE_SEARCH_CHAPTER_CHOICES = ("all", "in", "out")


def normalized_search_filter(value, choices, default="all"):
    value = (value or default).strip().lower()
    return value if value in choices else default


def normalized_search_year(value):
    try:
        year = int((value or "").strip())
    except (TypeError, ValueError):
        return None
    if 1 <= year <= 9999:
        return year
    return None


def timeline_search_filters(args=None):
    if args is None:
        args = request.args
    year_from = normalized_search_year(args.get("year_from"))
    year_to = normalized_search_year(args.get("year_to"))
    if year_from is not None and year_to is not None and year_from > year_to:
        year_from, year_to = year_to, year_from
    return {
        "query": (args.get("q") or args.get("query") or "").strip(),
        "kind": normalized_search_filter(args.get("kind"), TIMELINE_SEARCH_KIND_CHOICES),
        "visibility": normalized_search_filter(args.get("visibility"), ("all", *TAG_CHOICES)),
        "year_from": year_from,
        "year_to": year_to,
        "location": normalized_search_filter(args.get("location"), TIMELINE_SEARCH_TRI_CHOICES),
        "caption": normalized_search_filter(args.get("caption"), TIMELINE_SEARCH_TRI_CHOICES),
        "messages": normalized_search_filter(args.get("messages"), TIMELINE_SEARCH_TRI_CHOICES),
        "chapter": normalized_search_filter(args.get("chapter"), TIMELINE_SEARCH_CHAPTER_CHOICES),
    }


def timeline_search_has_active_filters(filters):
    return any(
        (
            filters["query"],
            filters["kind"] != "all",
            filters["visibility"] != "all",
            filters["year_from"] is not None,
            filters["year_to"] is not None,
            filters["location"] != "all",
            filters["caption"] != "all",
            filters["messages"] != "all",
            filters["chapter"] != "all",
        )
    )


def timeline_search_query_matches(fields, query):
    if not query:
        return True
    needle = query.lower()
    return any(needle in str(field or "").lower() for field in fields)


def timeline_search_has_location(row):
    return bool((row["location_name"] or "").strip() or row["latitude"] is not None or row["longitude"] is not None)


def load_timeline_search_message_counts(db, kind, item_ids):
    if not item_ids:
        return {}
    placeholders = ",".join(["?"] * len(item_ids))
    if kind == "photo":
        rows = db.execute(
            f"""
            SELECT photo_id AS item_id, COUNT(*) AS count
            FROM messages
            WHERE photo_id IN ({placeholders})
            GROUP BY photo_id
            """,
            tuple(item_ids),
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT entry_id AS item_id, COUNT(*) AS count
            FROM text_entry_messages
            WHERE entry_id IN ({placeholders})
            GROUP BY entry_id
            """,
            tuple(item_ids),
        ).fetchall()
    return {row["item_id"]: row["count"] for row in rows}


def load_timeline_search_chapter_memberships(db, kind, item_ids):
    if not item_ids:
        return {}
    placeholders = ",".join(["?"] * len(item_ids))
    rows = db.execute(
        f"""
        SELECT ci.item_id, COUNT(*) AS count, GROUP_CONCAT(c.title, ', ') AS titles
        FROM chapter_items ci
        JOIN chapters c ON c.id = ci.chapter_id
        WHERE c.user_id = ? AND ci.item_kind = ? AND ci.item_id IN ({placeholders})
        GROUP BY ci.item_id
        """,
        (g.user["id"], kind, *item_ids),
    ).fetchall()
    return {
        row["item_id"]: {
            "count": row["count"],
            "titles": row["titles"] or "",
        }
        for row in rows
    }


def timeline_search_item_matches(filters, kind, row, tags, message_count=0, chapter_count=0):
    if filters["kind"] not in ("all", kind):
        return False
    if filters["visibility"] != "all" and tags_to_text(tags) != filters["visibility"]:
        return False
    if filters["year_from"] is not None and row["year"] < filters["year_from"]:
        return False
    if filters["year_to"] is not None and row["year"] > filters["year_to"]:
        return False
    has_location = timeline_search_has_location(row)
    if filters["location"] == "with" and not has_location:
        return False
    if filters["location"] == "without" and has_location:
        return False
    if filters["caption"] != "all":
        if kind != "photo":
            return False
        has_caption = bool((row["caption"] or "").strip())
        if filters["caption"] == "with" and not has_caption:
            return False
        if filters["caption"] == "without" and has_caption:
            return False
    if filters["messages"] == "with" and message_count <= 0:
        return False
    if filters["messages"] == "without" and message_count > 0:
        return False
    if filters["chapter"] == "in" and chapter_count <= 0:
        return False
    if filters["chapter"] == "out" and chapter_count > 0:
        return False
    return True


def timeline_search_meta(row, tags, message_count=0, chapter_membership=None, people=None):
    meta = [f"Visible: {privacy_label_for_tags(tags)}"]
    if timeline_search_has_location(row):
        meta.append(row["location_name"] or "Mapped")
    if people:
        meta.append(f"People: {people_to_text(people)}")
    if message_count:
        message_word = "message" if message_count == 1 else "messages"
        meta.append(f"{message_count} {message_word}")
    if chapter_membership and chapter_membership.get("count"):
        chapter_word = "chapter" if chapter_membership["count"] == 1 else "chapters"
        meta.append(f"{chapter_membership['count']} {chapter_word}")
    return meta


def timeline_search_context(row, display_date):
    return f"{MONTH_NAMES[row['month'] - 1]} {row['year']} - {display_date}"


def timeline_search_chapter_filters_apply(filters):
    return any(
        (
            filters["year_from"] is not None,
            filters["year_to"] is not None,
            filters["location"] != "all",
            filters["caption"] != "all",
            filters["messages"] != "all",
            filters["chapter"] != "all",
        )
    )


def timeline_search_parent_filters(filters):
    parent_filters = dict(filters)
    parent_filters["kind"] = "all"
    return parent_filters


def search_timeline_content(
    db,
    filters,
    *,
    include_messages=True,
    include_chapters=True,
    item_limit=80,
):
    if isinstance(filters, str):
        filters = timeline_search_filters({"q": filters})
    normalized_query = filters["query"]
    if not timeline_search_has_active_filters(filters):
        return []

    results = []
    seen = set()

    def add_result(key, result):
        if key in seen:
            return
        seen.add(key)
        results.append(result)

    photo_rows = db.execute(
        """
        SELECT id, year, month, original_filename, title, caption, photo_date,
               location_name, latitude, longitude, created_at
        FROM photos
        WHERE user_id = ?
        ORDER BY COALESCE(photo_date, created_at) DESC, id DESC
        LIMIT ?
        """,
        (g.user["id"], item_limit),
    ).fetchall()
    photo_ids = [row["id"] for row in photo_rows]
    photo_tags = load_tags_for_items(db, "photo", photo_ids)
    photo_people = load_people_for_items(db, "photo", photo_ids)
    photo_message_counts = load_timeline_search_message_counts(db, "photo", photo_ids)
    photo_chapters = load_timeline_search_chapter_memberships(db, "photo", photo_ids)
    for row in photo_rows:
        people = photo_people.get(row["id"], [])
        if not timeline_search_query_matches(
            (
                row["original_filename"],
                row["title"],
                row["caption"],
                row["photo_date"],
                row["year"],
                f"{row['year']:04d}-{row['month']:02d}",
                row["location_name"],
                *people,
            ),
            normalized_query,
        ):
            continue
        tags = photo_tags.get(row["id"], [DEFAULT_TAG])
        message_count = photo_message_counts.get(row["id"], 0)
        chapter_membership = photo_chapters.get(row["id"], {})
        if not timeline_search_item_matches(
            filters,
            "photo",
            row,
            tags,
            message_count,
            chapter_membership.get("count", 0),
        ):
            continue
        date_label = format_timeline_date_label(row["year"], row["month"], row["photo_date"])
        add_result(
            ("photo", row["id"]),
            {
                "kind": "Photo",
                "kind_key": "photo",
                "title": photo_display_title(row),
                "context": timeline_search_context(row, date_label),
                "preview": short_preview(row["caption"] or "Matched photo filename, date, or person.", 180),
                "meta": timeline_search_meta(row, tags, message_count, chapter_membership, people),
                "image_url": url_for("photo_image", photo_id=row["id"]),
                "url": timeline_item_link(g.user["id"], row["year"], row["month"], "photo", row["id"]),
            },
        )

    text_rows = db.execute(
        """
        SELECT id, year, month, body, entry_date, location_name, latitude, longitude, created_at
        FROM text_entries
        WHERE user_id = ?
        ORDER BY COALESCE(entry_date, created_at) DESC, id DESC
        LIMIT ?
        """,
        (g.user["id"], item_limit),
    ).fetchall()
    text_ids = [row["id"] for row in text_rows]
    text_tags = load_tags_for_items(db, "text", text_ids)
    text_people = load_people_for_items(db, "text", text_ids)
    text_message_counts = load_timeline_search_message_counts(db, "text", text_ids)
    text_chapters = load_timeline_search_chapter_memberships(db, "text", text_ids)
    for row in text_rows:
        people = text_people.get(row["id"], [])
        if not timeline_search_query_matches(
            (
                row["body"],
                row["entry_date"],
                row["year"],
                f"{row['year']:04d}-{row['month']:02d}",
                row["location_name"],
                *people,
            ),
            normalized_query,
        ):
            continue
        tags = text_tags.get(row["id"], [DEFAULT_TAG])
        message_count = text_message_counts.get(row["id"], 0)
        chapter_membership = text_chapters.get(row["id"], {})
        if not timeline_search_item_matches(
            filters,
            "text",
            row,
            tags,
            message_count,
            chapter_membership.get("count", 0),
        ):
            continue
        date_label = format_timeline_date_label(row["year"], row["month"], row["entry_date"])
        add_result(
            ("text", row["id"]),
            {
                "kind": "Text entry",
                "kind_key": "text",
                "title": "Text entry",
                "context": timeline_search_context(row, date_label),
                "preview": short_preview(row["body"], 180),
                "meta": timeline_search_meta(row, tags, message_count, chapter_membership, people),
                "url": timeline_item_link(g.user["id"], row["year"], row["month"], "text", row["id"]),
            },
        )

    include_message_results = include_messages and (
        filters["kind"] == "message" or (filters["kind"] == "all" and normalized_query)
    )
    if include_message_results and filters["messages"] != "without":
        photo_message_rows = db.execute(
            """
            SELECT
                m.id AS message_id,
                m.body,
                m.created_at,
                p.id AS item_id,
                p.year,
                p.month,
                p.original_filename,
                p.title,
                p.caption,
                p.photo_date AS item_date,
                p.location_name,
                p.latitude,
                p.longitude,
                COALESCE(NULLIF(p.title, ''), p.original_filename, 'Photo') AS item_title,
                u.username,
                u.first_name,
                u.last_name
            FROM messages m
            JOIN photos p ON p.id = m.photo_id
            JOIN users u ON u.id = m.user_id
            WHERE p.user_id = ?
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT 120
            """,
            (g.user["id"],),
        ).fetchall()
        for row in photo_message_rows:
            people = photo_people.get(row["item_id"], [])
            if not timeline_search_query_matches((row["body"], row["item_title"], *people), normalized_query):
                continue
            tags = photo_tags.get(row["item_id"], get_tags_for_item(db, "photo", row["item_id"]))
            message_count = photo_message_counts.get(row["item_id"], 1)
            chapter_membership = photo_chapters.get(row["item_id"], {})
            if not timeline_search_item_matches(
                timeline_search_parent_filters(filters),
                "photo",
                row,
                tags,
                message_count,
                chapter_membership.get("count", 0),
            ):
                continue
            add_result(
                ("photo-message", row["message_id"]),
                {
                    "kind": "Message",
                    "kind_key": "message",
                    "title": row["item_title"] or "Photo message",
                    "context": f"Photo message by {message_author_name(row)}",
                    "preview": short_preview(row["body"], 180),
                    "meta": timeline_search_meta(row, tags, message_count, chapter_membership, people),
                    "image_url": url_for("photo_image", photo_id=row["item_id"]),
                    "url": timeline_item_link(g.user["id"], row["year"], row["month"], "photo", row["item_id"]),
                },
            )

        text_message_rows = db.execute(
            """
            SELECT
                tem.id AS message_id,
                tem.body,
                tem.created_at,
                te.id AS item_id,
                te.year,
                te.month,
                te.body AS item_body,
                te.entry_date AS item_date,
                te.location_name,
                te.latitude,
                te.longitude,
                u.username,
                u.first_name,
                u.last_name
            FROM text_entry_messages tem
            JOIN text_entries te ON te.id = tem.entry_id
            JOIN users u ON u.id = tem.user_id
            WHERE te.user_id = ?
            ORDER BY tem.created_at DESC, tem.id DESC
            LIMIT 120
            """,
            (g.user["id"],),
        ).fetchall()
        for row in text_message_rows:
            people = text_people.get(row["item_id"], [])
            if not timeline_search_query_matches((row["body"], row["item_body"], *people), normalized_query):
                continue
            tags = text_tags.get(row["item_id"], get_tags_for_item(db, "text", row["item_id"]))
            message_count = text_message_counts.get(row["item_id"], 1)
            chapter_membership = text_chapters.get(row["item_id"], {})
            if not timeline_search_item_matches(
                timeline_search_parent_filters(filters),
                "text",
                row,
                tags,
                message_count,
                chapter_membership.get("count", 0),
            ):
                continue
            add_result(
                ("text-message", row["message_id"]),
                {
                    "kind": "Message",
                    "kind_key": "message",
                    "title": "Text entry message",
                    "context": f"Text entry message by {message_author_name(row)}",
                    "preview": short_preview(row["body"], 180),
                    "meta": timeline_search_meta(row, tags, message_count, chapter_membership, people),
                    "url": timeline_item_link(g.user["id"], row["year"], row["month"], "text", row["item_id"]),
                },
            )

    if include_chapters:
        chapter_rows = db.execute(
            """
            SELECT
                c.id,
                c.title,
                c.description,
                c.visibility,
                c.created_at,
                COUNT(ci.id) AS item_count
            FROM chapters c
            LEFT JOIN chapter_items ci ON ci.chapter_id = c.id
            WHERE c.user_id = ?
            GROUP BY c.id
            ORDER BY c.created_at DESC, c.id DESC
            LIMIT 40
            """,
            (g.user["id"],),
        ).fetchall()
        for row in chapter_rows:
            if filters["kind"] not in ("all", "chapter"):
                continue
            if timeline_search_chapter_filters_apply(filters):
                continue
            if filters["visibility"] != "all" and row["visibility"] != filters["visibility"]:
                continue
            if not timeline_search_query_matches((row["title"], row["description"]), normalized_query):
                continue
            item_word = "item" if row["item_count"] == 1 else "items"
            add_result(
                ("chapter", row["id"]),
                {
                    "kind": "Chapter",
                    "kind_key": "chapter",
                    "title": row["title"],
                    "context": f"{row['item_count']} {item_word}",
                    "preview": short_preview(row["description"] or "Chapter title matched.", 180),
                    "meta": [f"Visible: {PRIVACY_AUDIENCE_LABELS[row['visibility']]}"],
                    "url": url_for("chapter_detail", chapter_id=row["id"]),
                },
            )

    return results[:item_limit]


COLLECTION_KIND_CHOICES = ("", "photo", "text")
TIMELINE_STORY_SOURCE_CHOICES = ("search", "collections")


def timeline_story_source_mode(source):
    mode = (source.get("source_mode") or source.get("mode") or "search").strip().lower()
    if mode not in TIMELINE_STORY_SOURCE_CHOICES:
        return "search"
    return mode


def normalize_story_filter_payload(source):
    if source is None:
        return {}
    if isinstance(source, str):
        try:
            return json.loads(source)
        except (TypeError, json.JSONDecodeError):
            return {}
    if isinstance(source, dict):
        return source
    if hasattr(source, "to_dict"):
        return source.to_dict(flat=True)
    try:
        return dict(source)
    except (TypeError, ValueError):
        return {}


def timeline_story_filters(source):
    source = normalize_story_filter_payload(source)
    mode = timeline_story_source_mode(source)
    return mode, parse_story_filters(source, mode)


def parse_story_filters(payload, mode):
    payload = normalize_story_filter_payload(payload)
    if mode == "collections":
        return collection_filters_from_source(payload)
    filters = timeline_search_filters(payload)
    if filters["kind"] == "message":
        filters["kind"] = "all"
    return filters


def timeline_story_has_filters(mode, filters):
    if mode == "collections":
        return collection_filters_have_values(filters)
    return timeline_search_has_active_filters(filters)


def timeline_story_filter_values(mode, filters):
    if mode == "collections":
        return collection_query_values(filters)
    values = {}
    for key, value in filters.items():
        if value not in (None, "", "all"):
            values[key] = value
    return values


def normalize_collection_date(value):
    text = (value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError("Choose valid collection dates.")


def collection_filters_from_source(source):
    item_kind = (source.get("item_kind") or "").strip().lower()
    if item_kind not in COLLECTION_KIND_CHOICES:
        item_kind = ""
    privacy_tag = normalize_tag_choice(source.get("privacy_tag", ""))

    date_start = normalize_collection_date(source.get("date_start", ""))
    date_end = normalize_collection_date(source.get("date_end", ""))
    if date_start and date_end and date_start > date_end:
        raise ValueError("Start date must be before end date.")

    return {
        "q": " ".join((source.get("q") or source.get("query_text") or "").strip().split())[:200],
        "item_kind": item_kind,
        "people": people_to_text(source.get("people") or source.get("people_text") or ""),
        "location": " ".join((source.get("location") or source.get("location_text") or "").strip().split())[:160],
        "privacy_tag": privacy_tag,
        "date_start": date_start,
        "date_end": date_end,
    }


def collection_filters_have_values(filters):
    return any(filters.get(key) for key in ("q", "item_kind", "people", "location", "privacy_tag", "date_start", "date_end"))


def collection_query_values(filters):
    values = {}
    for key, value in filters.items():
        if value:
            values[key] = value
    return values


def saved_view_filters(row):
    return {
        "q": row["query_text"],
        "item_kind": row["item_kind"],
        "people": row["people_text"],
        "location": row["location_text"],
        "privacy_tag": row["privacy_tag"],
        "date_start": row["date_start"],
        "date_end": row["date_end"],
    }


def collection_view_payload(row):
    filters = saved_view_filters(row)
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "filters": filters,
        "url": url_for("timeline_collections", **collection_query_values(filters)),
    }


def get_saved_collection_view(db, view_id):
    row = db.execute(
        """
        SELECT *
        FROM saved_timeline_views
        WHERE id = ? AND user_id = ?
        """,
        (view_id, g.user["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def load_saved_collection_views(db):
    rows = db.execute(
        """
        SELECT *
        FROM saved_timeline_views
        WHERE user_id = ?
        ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
        """,
        (g.user["id"],),
    ).fetchall()
    return [collection_view_payload(row) for row in rows]


def build_timeline_stories_query(db, mode, filters):
    if mode == "collections":
        results = search_collection_items(db, filters)
        return [dict(item, kind_key=item["kind"]) for item in results]
    normalized_filters = dict(filters)
    items = search_timeline_content(
        db,
        normalized_filters,
        include_messages=False,
        include_chapters=False,
        item_limit=240,
    )
    return [item for item in items if item.get("kind_key") in ("photo", "text")]


def load_timeline_stories(db):
    rows = db.execute(
        """
        SELECT *
        FROM timeline_stories
        WHERE user_id = ?
        ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
        """,
        (g.user["id"],),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "subtitle": row["subtitle"],
            "source_mode": row["source_mode"],
            "filter_payload": normalize_story_filter_payload(row["filter_payload"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "url": url_for("timeline_story", story_id=row["id"]),
        }
        for row in rows
    ]


def get_timeline_story(db, story_id):
    row = db.execute(
        """
        SELECT *
        FROM timeline_stories
        WHERE id = ? AND user_id = ?
        """,
        (story_id, g.user["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def collection_item_date_expr(table_alias, date_column):
    return f"COALESCE({table_alias}.{date_column}, printf('%04d-%02d-01', {table_alias}.year, {table_alias}.month))"


def add_people_collection_conditions(conditions, params, kind, table_alias, people):
    join_table, id_column = person_join_for_kind(kind)
    item_id_column = f"{table_alias}.id"
    user_id_column = f"{table_alias}.user_id"
    for person in parse_people(people):
        conditions.append(
            f"""
            EXISTS (
                SELECT 1
                FROM {join_table} person_join
                JOIN people pe ON pe.id = person_join.person_id
                WHERE person_join.{id_column} = {item_id_column}
                  AND pe.user_id = {user_id_column}
                  AND lower(pe.name) LIKE ?
            )
            """
        )
        params.append(f"%{person.lower()}%")


def add_privacy_collection_condition(conditions, params, kind, table_alias, privacy_tag):
    if not privacy_tag:
        return
    join_table, id_column = tag_join_for_kind(kind)
    conditions.append(
        f"""
        EXISTS (
            SELECT 1
            FROM {join_table} tag_join
            JOIN tags t ON t.id = tag_join.tag_id
            WHERE tag_join.{id_column} = {table_alias}.id
              AND t.user_id = {table_alias}.user_id
              AND t.name = ?
        )
        """
    )
    params.append(privacy_tag)


def collection_item_matches_query_clause(kind, table_alias):
    if kind == "photo":
        return f"""
        (
            lower(COALESCE({table_alias}.original_filename, '')) LIKE ?
            OR lower(COALESCE({table_alias}.title, '')) LIKE ?
            OR lower(COALESCE({table_alias}.caption, '')) LIKE ?
            OR lower(COALESCE({table_alias}.photo_date, '')) LIKE ?
            OR lower(COALESCE({table_alias}.location_name, '')) LIKE ?
            OR CAST({table_alias}.year AS TEXT) LIKE ?
            OR printf('%04d-%02d', {table_alias}.year, {table_alias}.month) LIKE ?
            OR EXISTS (
                SELECT 1
                FROM photo_people pp
                JOIN people pe ON pe.id = pp.person_id
                WHERE pp.photo_id = {table_alias}.id
                  AND pe.user_id = {table_alias}.user_id
                  AND lower(pe.name) LIKE ?
            )
            OR EXISTS (
                SELECT 1
                FROM messages m
                WHERE m.photo_id = {table_alias}.id
                  AND lower(m.body) LIKE ?
            )
        )
        """
    return f"""
    (
        lower({table_alias}.body) LIKE ?
        OR lower(COALESCE({table_alias}.entry_date, '')) LIKE ?
        OR lower(COALESCE({table_alias}.location_name, '')) LIKE ?
        OR CAST({table_alias}.year AS TEXT) LIKE ?
        OR printf('%04d-%02d', {table_alias}.year, {table_alias}.month) LIKE ?
        OR EXISTS (
            SELECT 1
            FROM text_entry_people tep
            JOIN people pe ON pe.id = tep.person_id
            WHERE tep.entry_id = {table_alias}.id
              AND pe.user_id = {table_alias}.user_id
              AND lower(pe.name) LIKE ?
        )
        OR EXISTS (
            SELECT 1
            FROM text_entry_messages tem
            WHERE tem.entry_id = {table_alias}.id
              AND lower(tem.body) LIKE ?
        )
    )
    """


def search_collection_items(db, filters):
    results = []

    if filters["item_kind"] in ("", "photo"):
        conditions = ["p.user_id = ?"]
        params = [g.user["id"]]
        if filters["q"]:
            pattern = f"%{filters['q'].lower()}%"
            conditions.append(collection_item_matches_query_clause("photo", "p"))
            params.extend([pattern] * 9)
        if filters["location"]:
            conditions.append("lower(COALESCE(p.location_name, '')) LIKE ?")
            params.append(f"%{filters['location'].lower()}%")
        if filters["date_start"]:
            conditions.append(f"{collection_item_date_expr('p', 'photo_date')} >= ?")
            params.append(filters["date_start"])
        if filters["date_end"]:
            conditions.append(f"{collection_item_date_expr('p', 'photo_date')} <= ?")
            params.append(filters["date_end"])
        add_people_collection_conditions(conditions, params, "photo", "p", filters["people"])
        add_privacy_collection_condition(conditions, params, "photo", "p", filters["privacy_tag"])
        photo_rows = db.execute(
            f"""
            SELECT p.id, p.year, p.month, p.original_filename, p.title, p.caption, p.photo_date,
                   p.location_name, p.latitude, p.longitude, p.created_at
            FROM photos p
            WHERE {" AND ".join(conditions)}
            ORDER BY {collection_item_date_expr('p', 'photo_date')} DESC, p.id DESC
            LIMIT 80
            """,
            tuple(params),
        ).fetchall()
        photo_tags = load_tags_for_items(db, "photo", [row["id"] for row in photo_rows])
        photo_people = load_people_for_items(db, "photo", [row["id"] for row in photo_rows])
        for row in photo_rows:
            tags = photo_tags.get(row["id"], [])
            people = photo_people.get(row["id"], [])
            results.append(
                {
                    "kind": "photo",
                    "kind_key": "photo",
                    "id": row["id"],
                    "year": row["year"],
                    "month": row["month"],
                    "title": photo_display_title(row),
                    "preview": row["caption"] or row["original_filename"] or "Photo",
                    "date_label": format_timeline_date_label(row["year"], row["month"], row["photo_date"]),
                    "display_date": row["photo_date"],
                    "location_name": row["location_name"] or "",
                    "tags": tags,
                    "tags_text": tags_to_text(tags),
                    **people_payload(people),
                    **privacy_payload_for_tags(tags),
                    "url": timeline_item_link(g.user["id"], row["year"], row["month"], "photo", row["id"]),
                }
            )

    if filters["item_kind"] in ("", "text"):
        conditions = ["te.user_id = ?"]
        params = [g.user["id"]]
        if filters["q"]:
            pattern = f"%{filters['q'].lower()}%"
            conditions.append(collection_item_matches_query_clause("text", "te"))
            params.extend([pattern] * 7)
        if filters["location"]:
            conditions.append("lower(COALESCE(te.location_name, '')) LIKE ?")
            params.append(f"%{filters['location'].lower()}%")
        if filters["date_start"]:
            conditions.append(f"{collection_item_date_expr('te', 'entry_date')} >= ?")
            params.append(filters["date_start"])
        if filters["date_end"]:
            conditions.append(f"{collection_item_date_expr('te', 'entry_date')} <= ?")
            params.append(filters["date_end"])
        add_people_collection_conditions(conditions, params, "text", "te", filters["people"])
        add_privacy_collection_condition(conditions, params, "text", "te", filters["privacy_tag"])
        text_rows = db.execute(
            f"""
            SELECT te.id, te.year, te.month, te.body, te.entry_date,
                   te.location_name, te.latitude, te.longitude, te.created_at, te.updated_at
            FROM text_entries te
            WHERE {" AND ".join(conditions)}
            ORDER BY {collection_item_date_expr('te', 'entry_date')} DESC, te.id DESC
            LIMIT 80
            """,
            tuple(params),
        ).fetchall()
        text_tags = load_tags_for_items(db, "text", [row["id"] for row in text_rows])
        text_people = load_people_for_items(db, "text", [row["id"] for row in text_rows])
        for row in text_rows:
            tags = text_tags.get(row["id"], [])
            people = text_people.get(row["id"], [])
            results.append(
                {
                    "kind": "text",
                    "kind_key": "text",
                    "id": row["id"],
                    "year": row["year"],
                    "month": row["month"],
                    "title": "Text entry",
                    "preview": row["body"],
                    "date_label": format_timeline_date_label(row["year"], row["month"], row["entry_date"]),
                    "display_date": row["entry_date"],
                    "location_name": row["location_name"] or "",
                    "tags": tags,
                    "tags_text": tags_to_text(tags),
                    **people_payload(people),
                    **privacy_payload_for_tags(tags),
                    "url": timeline_item_link(g.user["id"], row["year"], row["month"], "text", row["id"]),
                }
            )

    results.sort(key=timeline_item_sort_key)
    return results[:120]


def build_timeline_map_items(db):
    photo_rows = db.execute(
        """
        SELECT id, year, month, original_filename, title, caption, photo_date,
               location_name, latitude, longitude, created_at
        FROM photos
        WHERE user_id = ?
          AND (
            COALESCE(location_name, '') <> ''
            OR (latitude IS NOT NULL AND longitude IS NOT NULL)
          )
        """,
        (g.user["id"],),
    ).fetchall()
    text_rows = db.execute(
        """
        SELECT id, year, month, body, entry_date, location_name, latitude, longitude, created_at
        FROM text_entries
        WHERE user_id = ?
          AND (
            COALESCE(location_name, '') <> ''
            OR (latitude IS NOT NULL AND longitude IS NOT NULL)
          )
        """,
        (g.user["id"],),
    ).fetchall()

    items = []
    for row in photo_rows:
        has_coordinates = row["latitude"] is not None and row["longitude"] is not None
        item = {
            "kind": "photo",
            "id": row["id"],
            "year": row["year"],
            "month": row["month"],
            "title": photo_display_title(row),
            "preview": short_preview(row["caption"] or "Photo"),
            "has_caption": bool((row["caption"] or "").strip()),
            "date_label": format_timeline_date_label(row["year"], row["month"], row["photo_date"]),
            "location_name": row["location_name"] or "",
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "has_coordinates": has_coordinates,
            "url": timeline_item_link(g.user["id"], row["year"], row["month"], "photo", row["id"]),
            "sort_key": timeline_item_sort_key(
                {
                    "year": row["year"],
                    "month": row["month"],
                    "display_date": row["photo_date"],
                    "kind": "photo",
                    "id": row["id"],
                }
            ),
        }
        if has_coordinates:
            item.update(map_position(row["latitude"], row["longitude"]))
        items.append(item)

    for row in text_rows:
        has_coordinates = row["latitude"] is not None and row["longitude"] is not None
        item = {
            "kind": "text",
            "id": row["id"],
            "year": row["year"],
            "month": row["month"],
            "title": "Text entry",
            "preview": short_preview(row["body"]),
            "date_label": format_timeline_date_label(row["year"], row["month"], row["entry_date"]),
            "location_name": row["location_name"] or "",
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "has_coordinates": has_coordinates,
            "url": timeline_item_link(g.user["id"], row["year"], row["month"], "text", row["id"]),
            "sort_key": timeline_item_sort_key(
                {
                    "year": row["year"],
                    "month": row["month"],
                    "display_date": row["entry_date"],
                    "kind": "text",
                    "id": row["id"],
                }
            ),
        }
        if has_coordinates:
            item.update(map_position(row["latitude"], row["longitude"]))
        items.append(item)

    items.sort(key=lambda item: item["sort_key"])
    for item in items:
        item.pop("sort_key", None)
    return items


def filter_timeline_map_items(items, item_type="all", year=None):
    filtered = items
    if item_type in ("photo", "text"):
        filtered = [item for item in filtered if item["kind"] == item_type]
    if year is not None:
        filtered = [item for item in filtered if item["year"] == year]
    return filtered


def build_place_summaries(items):
    groups = {}
    for item in items:
        location_name = normalize_place_name(item["location_name"])
        if not location_name:
            continue
        key = place_group_key(location_name)
        group = groups.setdefault(
            key,
            {
                "location_name": location_name,
                "item_count": 0,
                "photo_count": 0,
                "text_count": 0,
                "mapped_count": 0,
                "years": set(),
                "coordinates": [],
                "latest_label": "",
                "url": url_for("timeline_place", name=location_name),
            },
        )
        group["item_count"] += 1
        group["photo_count"] += 1 if item["kind"] == "photo" else 0
        group["text_count"] += 1 if item["kind"] == "text" else 0
        group["years"].add(item["year"])
        group["latest_label"] = item["date_label"]
        if item["has_coordinates"]:
            group["mapped_count"] += 1
            group["coordinates"].append((item["latitude"], item["longitude"]))

    summaries = []
    for group in groups.values():
        years = sorted(group.pop("years"))
        coordinates = group.pop("coordinates")
        group["year_label"] = (
            str(years[0])
            if len(years) == 1
            else f"{years[0]}-{years[-1]}"
        )
        group["has_coordinates"] = bool(coordinates)
        if coordinates:
            latitude = sum(point[0] for point in coordinates) / len(coordinates)
            longitude = sum(point[1] for point in coordinates) / len(coordinates)
            group["latitude"] = latitude
            group["longitude"] = longitude
            group.update(map_position(latitude, longitude))
        summaries.append(group)

    summaries.sort(key=lambda group: (-group["item_count"], group["location_name"].casefold()))
    return summaries


def build_place_hub(db, place_name, items):
    refs = item_refs_for_items(items)
    chapters = load_chapters_for_refs(db, refs)
    missing_coordinates = sum(1 for item in items if not item["has_coordinates"])
    missing_captions = sum(1 for item in items if item["kind"] == "photo" and not item.get("has_caption"))
    photo_count = sum(1 for item in items if item["kind"] == "photo")
    text_count = sum(1 for item in items if item["kind"] == "text")
    years = sorted({item["year"] for item in items})
    return {
        "stats": [
            hub_stat("Memories", len(items)),
            hub_stat("Photos", photo_count),
            hub_stat("Text entries", text_count),
            hub_stat("Messages", load_message_count_for_refs(db, refs)),
            hub_stat("Chapters", len(chapters)),
            hub_stat("Years", len(years)),
        ],
        "chapters": chapters[:6],
        "year_label": people_year_label(years),
        "gap_notes": [
            note
            for note in (
                f"{missing_coordinates} {memory_word(missing_coordinates)} need coordinates" if missing_coordinates else "",
                f"{missing_captions} {'photo needs' if missing_captions == 1 else 'photos need'} captions" if missing_captions else "",
            )
            if note
        ],
        "draft_url": url_for("chapter_draft", place=place_name),
        "search_url": url_for("timeline_collections", location=place_name),
    }


def current_profile_form_values():
    return {
        "first_name": g.user["first_name"] or "",
        "last_name": g.user["last_name"] or "",
        "email": g.user["email"] or "",
    }


def get_connected_user(connection_id):
    connected_user = get_db().execute(
        """
        SELECT u.*, cr.relation AS connection_relation
        FROM users u
        JOIN connection_requests cr ON (
            (cr.requester_id = ? AND cr.recipient_id = u.id)
            OR (cr.recipient_id = ? AND cr.requester_id = u.id)
        )
        WHERE u.id = ?
          AND cr.status = 'accepted'
        ORDER BY cr.responded_at DESC, cr.created_at DESC, cr.id DESC
        LIMIT 1
        """,
        (g.user["id"], g.user["id"], connection_id),
    ).fetchone()
    if connected_user is None:
        abort(404)
    return connected_user


def get_connection_photo(connection_id, photo_id):
    return get_connection_timeline_item(connection_id, "photo", photo_id)


def get_connection_timeline_item(connection_id, item_kind, item_id):
    db = get_db()
    connected_user = get_connected_user(connection_id)
    if item_kind == "photo":
        table = "photos"
        tag_kind = "photo"
    elif item_kind == "text":
        table = "text_entries"
        tag_kind = "text"
    else:
        abort(404)

    item = db.execute(
        f"""
        SELECT *
        FROM {table}
        WHERE id = ? AND user_id = ?
        """,
        (item_id, connected_user["id"]),
    ).fetchone()
    if item is None:
        abort(404)

    tags = get_tags_for_item(db, tag_kind, item_id, connected_user["id"])
    if not tags_visible_to_connection(tags, visible_tags_for_connection(connected_user)):
        abort(404)
    return item


def build_month_items(db, owner_id, year, month, image_url_builder, allowed_tags=None):
    photo_rows = db.execute(
        """
        SELECT id, original_filename, title, caption, photo_date,
               location_name, latitude, longitude, created_at
        FROM photos
        WHERE user_id = ? AND year = ? AND month = ?
        ORDER BY COALESCE(photo_date, created_at) DESC, id DESC
        """,
        (owner_id, year, month),
    ).fetchall()
    text_rows = db.execute(
        """
        SELECT id, body, entry_date, location_name, latitude, longitude, created_at, updated_at
        FROM text_entries
        WHERE user_id = ? AND year = ? AND month = ?
        ORDER BY COALESCE(entry_date, created_at) DESC, id DESC
        """,
        (owner_id, year, month),
    ).fetchall()
    photo_tags = load_tags_for_items(db, "photo", [photo["id"] for photo in photo_rows], owner_id)
    text_tags = load_tags_for_items(db, "text", [entry["id"] for entry in text_rows], owner_id)
    photo_people = load_people_for_items(db, "photo", [photo["id"] for photo in photo_rows], owner_id)
    text_people = load_people_for_items(db, "text", [entry["id"] for entry in text_rows], owner_id)
    items = []
    for photo in photo_rows:
        tags = photo_tags.get(photo["id"], [])
        people = photo_people.get(photo["id"], [])
        if not tags_visible_to_connection(tags, allowed_tags):
            continue
        items.append(
            {
                "kind": "photo",
                "id": photo["id"],
                "year": year,
                "month": month,
                "original_filename": photo["original_filename"],
                "title": photo["title"] or "",
                "display_title": photo_display_title(photo),
                "caption": photo["caption"] or "",
                "display_date": photo["photo_date"],
                **timeline_location_payload(photo),
                "created_at": photo["created_at"],
                "image_url": image_url_builder(photo["id"]),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "guided_prompts": guided_prompts_for_item("photo", photo, people),
            }
        )
    for entry in text_rows:
        tags = text_tags.get(entry["id"], [])
        people = text_people.get(entry["id"], [])
        if not tags_visible_to_connection(tags, allowed_tags):
            continue
        items.append(
            {
                "kind": "text",
                "id": entry["id"],
                "year": year,
                "month": month,
                "body": entry["body"],
                "display_date": entry["entry_date"],
                **timeline_location_payload(entry),
                "created_at": entry["created_at"],
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "guided_prompts": guided_prompts_for_item("text", entry, people),
            }
        )
    items.sort(key=timeline_item_sort_key)
    return attach_reactions(db, items)


def build_timeline_api_items(
    db,
    owner_id,
    image_url_builder,
    selected_year=None,
    allowed_tags=None,
    message_url_builder=None,
    can_message=False,
    include_messages=True,
):
    query_suffix = ""
    query_params = [owner_id]
    if selected_year is not None:
        query_suffix = " AND year = ?"
        query_params.append(selected_year)

    photo_rows = db.execute(
        f"""
        SELECT id, year, month, original_filename, title, caption, photo_date,
               location_name, latitude, longitude, created_at
        FROM photos
        WHERE user_id = ?{query_suffix}
        """,
        query_params,
    ).fetchall()
    text_rows = db.execute(
        f"""
        SELECT id, year, month, body, entry_date, location_name, latitude, longitude, created_at, updated_at
        FROM text_entries
        WHERE user_id = ?{query_suffix}
        """,
        query_params,
    ).fetchall()
    photo_tags = load_tags_for_items(db, "photo", [photo["id"] for photo in photo_rows], owner_id)
    text_tags = load_tags_for_items(db, "text", [entry["id"] for entry in text_rows], owner_id)
    photo_people = load_people_for_items(db, "photo", [photo["id"] for photo in photo_rows], owner_id)
    text_people = load_people_for_items(db, "text", [entry["id"] for entry in text_rows], owner_id)
    visible_photo_rows = [
        photo
        for photo in photo_rows
        if tags_visible_to_connection(photo_tags.get(photo["id"], []), allowed_tags)
    ]
    visible_text_rows = [
        entry
        for entry in text_rows
        if tags_visible_to_connection(text_tags.get(entry["id"], []), allowed_tags)
    ]
    messages_by_photo = {}
    photo_ids = [photo["id"] for photo in visible_photo_rows]
    if include_messages and photo_ids:
        placeholders = ",".join(["?"] * len(photo_ids))
        message_rows = db.execute(
            f"""
            SELECT m.photo_id, m.id, m.body, m.created_at, u.username, u.first_name, u.last_name
            FROM messages m
            JOIN users u ON u.id = m.user_id
            WHERE m.photo_id IN ({placeholders})
            ORDER BY m.created_at ASC, m.id ASC
            """,
            tuple(photo_ids),
        ).fetchall()
        for message in message_rows:
            messages_by_photo.setdefault(message["photo_id"], []).append(message_payload(message))

    messages_by_text = {}
    text_ids = [entry["id"] for entry in visible_text_rows]
    if include_messages and text_ids:
        placeholders = ",".join(["?"] * len(text_ids))
        message_rows = db.execute(
            f"""
            SELECT tem.entry_id, tem.id, tem.body, tem.created_at, u.username, u.first_name, u.last_name
            FROM text_entry_messages tem
            JOIN users u ON u.id = tem.user_id
            WHERE tem.entry_id IN ({placeholders})
            ORDER BY tem.created_at ASC, tem.id ASC
            """,
            tuple(text_ids),
        ).fetchall()
        for message in message_rows:
            messages_by_text.setdefault(message["entry_id"], []).append(message_payload(message))

    items = []
    for photo in visible_photo_rows:
        display_date = photo["photo_date"]
        tags = photo_tags.get(photo["id"], [])
        people = photo_people.get(photo["id"], [])
        messages_url = message_url_builder("photo", photo["id"]) if message_url_builder else ""
        item = {
            "kind": "photo",
            "id": photo["id"],
            "year": photo["year"],
            "month": photo["month"],
            "display_date": display_date,
            "date_label": format_timeline_date_label(photo["year"], photo["month"], display_date),
            **timeline_location_payload(photo),
            "created_at": photo["created_at"],
            "image_url": image_url_builder(photo["id"]),
            "caption": photo["caption"] or "",
            "messages_url": messages_url,
            "can_message": can_message and bool(messages_url),
            "tags": tags,
            "tags_text": tags_to_text(tags),
            **people_payload(people),
            **privacy_payload_for_tags(tags),
            "title": photo_display_title(photo),
            "guided_prompts": guided_prompts_for_item("photo", photo, people),
        }
        if include_messages:
            item["messages"] = messages_by_photo.get(photo["id"], [])
        items.append(item)

    for entry in visible_text_rows:
        display_date = entry["entry_date"]
        tags = text_tags.get(entry["id"], [])
        people = text_people.get(entry["id"], [])
        messages_url = message_url_builder("text", entry["id"]) if message_url_builder else ""
        item = {
            "kind": "text",
            "id": entry["id"],
            "year": entry["year"],
            "month": entry["month"],
            "display_date": display_date,
            "date_label": format_timeline_date_label(entry["year"], entry["month"], display_date),
            **timeline_location_payload(entry),
            "created_at": entry["created_at"],
            "body": entry["body"],
            "messages_url": messages_url,
            "can_message": can_message and bool(messages_url),
            "tags": tags,
            "tags_text": tags_to_text(tags),
            **people_payload(people),
            **privacy_payload_for_tags(tags),
            "title": "Text entry",
            "guided_prompts": guided_prompts_for_item("text", entry, people),
        }
        if include_messages:
            item["messages"] = messages_by_text.get(entry["id"], [])
        items.append(item)

    items.sort(key=timeline_item_sort_key)
    return items


def build_pdf_export_items(db, owner_id, year, month=None):
    photo_query = """
        SELECT id, year, month, original_filename, title, caption, mime_type, image_data, photo_date, created_at
        FROM photos
        WHERE user_id = ? AND year = ?
    """
    text_query = """
        SELECT id, year, month, body, entry_date, created_at, updated_at
        FROM text_entries
        WHERE user_id = ? AND year = ?
    """
    params = [owner_id, year]
    if month is not None:
        photo_query += " AND month = ?"
        text_query += " AND month = ?"
        params.append(month)

    photo_rows = db.execute(photo_query, tuple(params)).fetchall()
    text_rows = db.execute(text_query, tuple(params)).fetchall()
    photo_tags = load_tags_for_items(db, "photo", [photo["id"] for photo in photo_rows], owner_id)
    text_tags = load_tags_for_items(db, "text", [entry["id"] for entry in text_rows], owner_id)
    photo_people = load_people_for_items(db, "photo", [photo["id"] for photo in photo_rows], owner_id)
    text_people = load_people_for_items(db, "text", [entry["id"] for entry in text_rows], owner_id)

    items = []
    for photo in photo_rows:
        tags = photo_tags.get(photo["id"], [])
        people = photo_people.get(photo["id"], [])
        items.append(
            {
                "kind": "photo",
                "id": photo["id"],
                "year": photo["year"],
                "month": photo["month"],
                "title": photo_display_title(photo),
                "caption": photo["caption"] or "",
                "display_date": photo["photo_date"],
                "date_label": format_timeline_date_label(photo["year"], photo["month"], photo["photo_date"]),
                "created_at": photo["created_at"],
                "mime_type": photo["mime_type"],
                "image_data": photo["image_data"],
                "messages": load_messages_for_timeline_item(db, "photo", photo["id"]),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "guided_prompts": guided_prompts_for_item("photo", photo, people),
            }
        )

    for entry in text_rows:
        tags = text_tags.get(entry["id"], [])
        people = text_people.get(entry["id"], [])
        items.append(
            {
                "kind": "text",
                "id": entry["id"],
                "year": entry["year"],
                "month": entry["month"],
                "title": "Text entry",
                "display_date": entry["entry_date"],
                "date_label": format_timeline_date_label(entry["year"], entry["month"], entry["entry_date"]),
                "created_at": entry["created_at"],
                "body": entry["body"],
                "messages": load_messages_for_timeline_item(db, "text", entry["id"]),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "guided_prompts": guided_prompts_for_item("text", entry, people),
            }
        )

    items.sort(key=timeline_item_sort_key)
    return items


def pdf_paragraph(text):
    return escape(str(text or "")).replace("\n", "<br/>")


def pdf_image_flowable(image_data, max_width, max_height):
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image

    image_buffer = io.BytesIO(image_data)
    reader = ImageReader(image_buffer)
    width, height = reader.getSize()
    if width <= 0 or height <= 0:
        raise ValueError("Invalid image dimensions.")

    scale = min(max_width / width, max_height / height)
    image_buffer.seek(0)
    image = Image(image_buffer)
    image.drawWidth = width * scale
    image.drawHeight = height * scale
    return image


def render_timeline_pdf(title, subtitle, items):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.62 * inch,
        leftMargin=0.62 * inch,
        topMargin=0.72 * inch,
        bottomMargin=0.68 * inch,
        title=title,
        author="EverTimeline",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "EverTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=29,
        textColor=colors.HexColor("#202522"),
        spaceAfter=8,
    )
    subtitle_style = ParagraphStyle(
        "EverSubtitle",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#68706a"),
        spaceAfter=16,
    )
    month_style = ParagraphStyle(
        "EverMonth",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        textColor=colors.HexColor("#166d67"),
        spaceBefore=12,
        spaceAfter=8,
    )
    item_title_style = ParagraphStyle(
        "EverItemTitle",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#202522"),
        spaceBefore=8,
        spaceAfter=3,
    )
    meta_style = ParagraphStyle(
        "EverMeta",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#68706a"),
        spaceAfter=7,
    )
    body_style = ParagraphStyle(
        "EverBody",
        parent=styles["BodyText"],
        fontSize=10.2,
        leading=14.5,
        textColor=colors.HexColor("#202522"),
        spaceAfter=8,
    )
    message_style = ParagraphStyle(
        "EverMessage",
        parent=styles["BodyText"],
        leftIndent=12,
        fontSize=8.8,
        leading=12,
        textColor=colors.HexColor("#202522"),
        spaceAfter=4,
    )
    empty_style = ParagraphStyle(
        "EverEmpty",
        parent=styles["BodyText"],
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#68706a"),
    )

    story = [
        Paragraph(pdf_paragraph(title), title_style),
        Paragraph(pdf_paragraph(subtitle), subtitle_style),
    ]

    if not items:
        story.append(Paragraph("No timeline items found for this export.", empty_style))

    current_month = None
    max_image_width = 5.5 * inch
    max_image_height = 3.45 * inch
    for item in items:
        month_key = (item["year"], item["month"])
        if month_key != current_month:
            if current_month is not None:
                story.append(Spacer(1, 6))
            current_month = month_key
            story.append(Paragraph(f"{MONTH_NAMES[item['month'] - 1]} {item['year']}", month_style))

        item_title = item["title"] if item["kind"] == "photo" else "Text entry"
        meta_parts = [item["date_label"], f"Visible: {item['privacy_label']}"]
        if item.get("people_text"):
            meta_parts.append(f"People: {item['people_text']}")
        meta = " | ".join(meta_parts)
        story.append(Paragraph(pdf_paragraph(item_title), item_title_style))
        story.append(Paragraph(pdf_paragraph(meta), meta_style))

        if item["kind"] == "photo":
            try:
                story.append(pdf_image_flowable(item["image_data"], max_image_width, max_image_height))
                story.append(Spacer(1, 8))
            except Exception:
                story.append(Paragraph("Photo could not be rendered in this PDF.", body_style))
            if item.get("caption"):
                story.append(Paragraph(pdf_paragraph(item["caption"]), body_style))
        else:
            story.append(Paragraph(pdf_paragraph(item["body"]), body_style))

        if item["messages"]:
            story.append(Paragraph("Messages", meta_style))
            for message in item["messages"]:
                message_text = f"{message['author_name']} ({message['created_at']}): {message['body']}"
                story.append(Paragraph(pdf_paragraph(message_text), message_style))

        story.append(HRFlowable(width="100%", thickness=0.45, color=colors.HexColor("#d9ded8")))
        story.append(Spacer(1, 8))

    def draw_footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#68706a"))
        canvas.drawString(document.leftMargin, 0.38 * inch, "EverTimeline")
        canvas.drawRightString(
            letter[0] - document.rightMargin,
            0.38 * inch,
            f"Page {document.page}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    buffer.seek(0)
    return buffer.getvalue()


def pdf_export_response(title, subtitle, filename, items):
    pdf_bytes = render_timeline_pdf(title, subtitle, items)
    response = Response(pdf_bytes, mimetype="application/pdf")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Content-Length"] = str(len(pdf_bytes))
    return response


def dict_from_row(row, fields):
    return {field: row[field] for field in fields}


def author_backup_payload(row):
    return {
        "id": row["user_id"],
        "username": row["username"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "email": row["email"] or "",
        "display_name": user_full_name(row) or row["username"],
        "is_owner": row["user_id"] == g.user["id"],
    }


def backup_photo_path(photo):
    filename = secure_filename(photo["original_filename"] or "") or f"photo-{photo['id']}"
    if "." not in filename:
        extension = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }.get(photo["mime_type"], "")
        filename = f"{filename}{extension}"
    return f"photos/{photo['id']}-{filename}"


def load_backup_message_map(db, item_kind):
    if item_kind == "photo":
        rows = db.execute(
            """
            SELECT
                m.id,
                m.photo_id AS item_id,
                m.user_id,
                m.body,
                m.created_at,
                u.username,
                u.first_name,
                u.last_name,
                u.email
            FROM messages m
            JOIN photos p ON p.id = m.photo_id
            JOIN users u ON u.id = m.user_id
            WHERE p.user_id = ?
            ORDER BY m.created_at ASC, m.id ASC
            """,
            (g.user["id"],),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT
                tem.id,
                tem.entry_id AS item_id,
                tem.user_id,
                tem.body,
                tem.created_at,
                u.username,
                u.first_name,
                u.last_name,
                u.email
            FROM text_entry_messages tem
            JOIN text_entries te ON te.id = tem.entry_id
            JOIN users u ON u.id = tem.user_id
            WHERE te.user_id = ?
            ORDER BY tem.created_at ASC, tem.id ASC
            """,
            (g.user["id"],),
        ).fetchall()

    messages_by_item = {}
    for row in rows:
        messages_by_item.setdefault(row["item_id"], []).append(
            {
                "id": row["id"],
                "body": row["body"],
                "created_at": row["created_at"],
                "author": author_backup_payload(row),
            }
        )
    return messages_by_item


def load_backup_reaction_map(db, item_kind):
    owner_table = "photos" if item_kind == "photo" else "text_entries"
    rows = db.execute(
        f"""
        SELECT
            ir.id,
            ir.item_id,
            ir.user_id,
            ir.reaction,
            ir.created_at,
            u.username,
            u.first_name,
            u.last_name,
            u.email
        FROM item_reactions ir
        JOIN {owner_table} item ON item.id = ir.item_id
        JOIN users u ON u.id = ir.user_id
        WHERE ir.item_kind = ?
          AND item.user_id = ?
        ORDER BY ir.created_at ASC, ir.id ASC
        """,
        (item_kind, g.user["id"]),
    ).fetchall()

    reactions_by_item = {}
    for row in rows:
        reactions_by_item.setdefault(row["item_id"], []).append(
            {
                "id": row["id"],
                "reaction": row["reaction"],
                "created_at": row["created_at"],
                "author": author_backup_payload(row),
            }
        )
    return reactions_by_item


def build_account_backup_manifest(db):
    photo_rows = db.execute(
        """
        SELECT id, year, month, original_filename, title, caption, image_hash, mime_type, photo_date, created_at
        FROM photos
        WHERE user_id = ?
        ORDER BY year ASC, month ASC, COALESCE(photo_date, ''), id ASC
        """,
        (g.user["id"],),
    ).fetchall()
    text_rows = db.execute(
        """
        SELECT id, year, month, body, entry_date, created_at, updated_at
        FROM text_entries
        WHERE user_id = ?
        ORDER BY year ASC, month ASC, COALESCE(entry_date, ''), id ASC
        """,
        (g.user["id"],),
    ).fetchall()
    chapter_rows = db.execute(
        """
        SELECT id, title, description, created_at, updated_at
        FROM chapters
        WHERE user_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (g.user["id"],),
    ).fetchall()
    chapter_item_rows = db.execute(
        """
        SELECT ci.id, ci.chapter_id, ci.item_kind, ci.item_id, ci.position, ci.created_at
        FROM chapter_items ci
        JOIN chapters c ON c.id = ci.chapter_id
        WHERE c.user_id = ?
        ORDER BY ci.chapter_id ASC, ci.position ASC, ci.id ASC
        """,
        (g.user["id"],),
    ).fetchall()

    photo_tags = load_tags_for_items(db, "photo", [row["id"] for row in photo_rows])
    text_tags = load_tags_for_items(db, "text", [row["id"] for row in text_rows])
    photo_people = load_people_for_items(db, "photo", [row["id"] for row in photo_rows])
    text_people = load_people_for_items(db, "text", [row["id"] for row in text_rows])
    photo_messages = load_backup_message_map(db, "photo")
    text_messages = load_backup_message_map(db, "text")
    photo_reactions = load_backup_reaction_map(db, "photo")
    text_reactions = load_backup_reaction_map(db, "text")

    chapter_items_by_chapter = {}
    for row in chapter_item_rows:
        chapter_items_by_chapter.setdefault(row["chapter_id"], []).append(
            dict_from_row(
                row,
                ("id", "item_kind", "item_id", "position", "created_at"),
            )
        )

    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "exported_at": utc_now().isoformat(),
        "user": {
            "username": g.user["username"],
            "first_name": g.user["first_name"],
            "last_name": g.user["last_name"],
            "email": g.user["email"] or "",
            "birthday": g.user["birthday"],
            "created_at": g.user["created_at"],
        },
        "photos": [
            {
                **dict_from_row(
                    row,
                    ("id", "year", "month", "original_filename", "title", "caption", "image_hash", "mime_type", "photo_date", "created_at"),
                ),
                "tags": photo_tags.get(row["id"], [DEFAULT_TAG]),
                "people": photo_people.get(row["id"], []),
                "image_path": backup_photo_path(row),
                "messages": photo_messages.get(row["id"], []),
                "reactions": photo_reactions.get(row["id"], []),
            }
            for row in photo_rows
        ],
        "text_entries": [
            {
                **dict_from_row(
                    row,
                    ("id", "year", "month", "body", "entry_date", "created_at", "updated_at"),
                ),
                "tags": text_tags.get(row["id"], [DEFAULT_TAG]),
                "people": text_people.get(row["id"], []),
                "messages": text_messages.get(row["id"], []),
                "reactions": text_reactions.get(row["id"], []),
            }
            for row in text_rows
        ],
        "chapters": [
            {
                **dict_from_row(
                    row,
                    ("id", "title", "description", "created_at", "updated_at"),
                ),
                "items": chapter_items_by_chapter.get(row["id"], []),
            }
            for row in chapter_rows
        ],
    }


def account_backup_response(db):
    manifest = build_account_backup_manifest(db)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            BACKUP_MANIFEST_NAME,
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        )
        for photo in manifest["photos"]:
            row = db.execute(
                "SELECT image_data FROM photos WHERE id = ? AND user_id = ?",
                (photo["id"], g.user["id"]),
            ).fetchone()
            if row is not None:
                archive.writestr(photo["image_path"], row["image_data"])

    backup_bytes = buffer.getvalue()
    username = secure_filename(g.user["username"]) or "account"
    exported_on = datetime.now().strftime("%Y%m%d")
    filename = f"evertimeline-backup-{username}-{exported_on}.zip"
    response = Response(backup_bytes, mimetype="application/zip")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Content-Length"] = str(len(backup_bytes))
    return response


def backup_list(manifest, key):
    value = manifest.get(key, [])
    if not isinstance(value, list):
        raise ValueError("Backup manifest is not valid.")
    return value


def validate_backup_item_date(year, month, day_value, field_name):
    try:
        year = int(year)
        month = int(month)
    except (TypeError, ValueError) as exc:
        raise ValueError("Backup item has an invalid year or month.") from exc
    if year < 1 or year > date.today().year or month < 1 or month > 12:
        raise ValueError("Backup item has an invalid year or month.")
    if day_value:
        parsed_day = parse_iso_date(str(day_value), field_name)
        if parsed_day.year != year or parsed_day.month != month:
            raise ValueError(f"{field_name} must belong to the imported item month.")
    return year, month


def validate_backup_image_path(path):
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Backup photo entry is missing an image path.")
    normalized = PurePosixPath(path)
    if normalized.is_absolute() or ".." in normalized.parts or normalized.parts[0] != "photos":
        raise ValueError("Backup photo entry has an unsafe image path.")
    return str(normalized)


def backup_text(value, default="", limit=None):
    if value is None:
        return default
    text = str(value)
    if limit is not None:
        return text[:limit]
    return text


def backup_created_at(value):
    text = backup_text(value, "", 64).strip()
    return text or utc_now().isoformat()


def read_backup_manifest(archive):
    try:
        manifest_data = archive.read(BACKUP_MANIFEST_NAME)
    except KeyError as exc:
        raise ValueError("Choose an EverTimeline backup zip.") from exc

    try:
        manifest = json.loads(manifest_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Backup manifest could not be read.") from exc

    if not isinstance(manifest, dict):
        raise ValueError("Backup manifest is not valid.")
    if manifest.get("format") != BACKUP_FORMAT:
        raise ValueError("This zip is not an EverTimeline account backup.")
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise ValueError("This backup format is not supported by this version of EverTimeline.")
    return manifest


def restore_backup_birthday(db, manifest):
    user = manifest.get("user") or {}
    if not isinstance(user, dict):
        raise ValueError("Backup user metadata is not valid.")
    birthday = (user.get("birthday") or "").strip()
    if not birthday:
        return

    birthday_date = parse_iso_date(birthday, "Backup birthday")
    if birthday_date > date.today():
        raise ValueError("Backup birthday cannot be in the future.")
    db.execute(
        "UPDATE users SET birthday = ? WHERE id = ?",
        (birthday_date.isoformat(), g.user["id"]),
    )


def import_photo_from_backup(db, archive, photo):
    if not isinstance(photo, dict):
        raise ValueError("Backup photo entry is not valid.")

    year, month = validate_backup_item_date(
        photo.get("year"),
        photo.get("month"),
        photo.get("photo_date"),
        "Photo date",
    )
    image_path = validate_backup_image_path(photo.get("image_path"))
    try:
        info = archive.getinfo(image_path)
    except KeyError as exc:
        raise ValueError("Backup photo image is missing from the zip.") from exc

    if info.file_size > app.config["MAX_CONTENT_LENGTH"]:
        raise ValueError("Backup contains a photo that is too large.")

    original_image_data = archive.read(info)
    mime_type = backup_text(photo.get("mime_type"), "image/png", 64)
    if mime_type not in ALLOWED_IMAGE_MIMES:
        raise ValueError("Backup contains an unsupported photo type.")

    try:
        storage_image_data = storage_jpeg_from_image(original_image_data)
    except ValueError as exc:
        raise ValueError("Backup contains a photo that could not be converted.") from exc

    image_hash = photo_image_hash(storage_image_data)
    duplicate = find_duplicate_photo(db, g.user["id"], image_hash)
    if duplicate is not None:
        return {
            "id": duplicate["id"],
            "created": False,
            "duplicate": True,
        }

    cursor = db.execute(
        """
        INSERT INTO photos (
            user_id, year, month, original_filename, title, caption, image_hash, mime_type, image_data, photo_date, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["id"],
            year,
            month,
            secure_filename(backup_text(photo.get("original_filename"), "", 255)) or None,
            normalize_photo_title(photo.get("title", "")),
            normalize_photo_caption(photo.get("caption", "")),
            image_hash,
            JPEG_STORAGE_MIME,
            storage_image_data,
            photo.get("photo_date") or None,
            backup_created_at(photo.get("created_at")),
        ),
    )
    set_tags_for_item(db, "photo", cursor.lastrowid, photo.get("tags", [DEFAULT_TAG]))
    set_people_for_item(db, "photo", cursor.lastrowid, photo.get("people", []))
    return {
        "id": cursor.lastrowid,
        "created": True,
        "duplicate": False,
    }


def import_text_entry_from_backup(db, entry):
    if not isinstance(entry, dict):
        raise ValueError("Backup text entry is not valid.")

    body = backup_text(entry.get("body"))
    if not body.strip():
        raise ValueError("Backup contains an empty text entry.")
    year, month = validate_backup_item_date(
        entry.get("year"),
        entry.get("month"),
        entry.get("entry_date"),
        "Text entry date",
    )

    cursor = db.execute(
        """
        INSERT INTO text_entries (
            user_id, year, month, body, entry_date, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["id"],
            year,
            month,
            body,
            entry.get("entry_date") or None,
            backup_created_at(entry.get("created_at")),
            entry.get("updated_at") or None,
        ),
    )
    set_tags_for_item(db, "text", cursor.lastrowid, entry.get("tags", [DEFAULT_TAG]))
    set_people_for_item(db, "text", cursor.lastrowid, entry.get("people", []))
    return cursor.lastrowid


def import_messages_from_backup(db, item_kind, item_id, messages):
    if not isinstance(messages, list):
        raise ValueError("Backup messages are not valid.")

    imported_count = 0
    table = "messages" if item_kind == "photo" else "text_entry_messages"
    id_column = "photo_id" if item_kind == "photo" else "entry_id"
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Backup message is not valid.")
        body = backup_text(message.get("body")).strip()
        if not body:
            continue
        db.execute(
            f"""
            INSERT INTO {table} ({id_column}, user_id, body, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (item_id, g.user["id"], body, backup_created_at(message.get("created_at"))),
        )
        imported_count += 1
    return imported_count


def import_owner_reactions_from_backup(db, item_kind, item_id, reactions):
    if not isinstance(reactions, list):
        raise ValueError("Backup reactions are not valid.")

    imported_count = 0
    for reaction_row in reactions:
        if not isinstance(reaction_row, dict):
            raise ValueError("Backup reaction is not valid.")
        author = reaction_row.get("author") or {}
        if not author.get("is_owner"):
            continue
        reaction = backup_text(reaction_row.get("reaction")).strip().lower()
        if reaction not in REACTION_CHOICES:
            continue
        db.execute(
            """
            INSERT INTO item_reactions (user_id, item_kind, item_id, reaction, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, item_kind, item_id)
            DO UPDATE SET reaction = excluded.reaction, created_at = excluded.created_at
            """,
            (
                g.user["id"],
                item_kind,
                item_id,
                reaction,
                backup_created_at(reaction_row.get("created_at")),
            ),
        )
        imported_count += 1
    return imported_count


def import_chapters_from_backup(db, chapters, photo_id_map, text_id_map):
    if not isinstance(chapters, list):
        raise ValueError("Backup chapters are not valid.")

    imported_chapters = 0
    imported_chapter_items = 0
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ValueError("Backup chapter is not valid.")
        title = backup_text(chapter.get("title")).strip()
        if not title:
            continue

        cursor = db.execute(
            """
            INSERT INTO chapters (user_id, title, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                g.user["id"],
                title,
                backup_text(chapter.get("description"), None),
                backup_created_at(chapter.get("created_at")),
                chapter.get("updated_at") or None,
            ),
        )
        new_chapter_id = cursor.lastrowid
        imported_chapters += 1

        items = chapter.get("items", [])
        if not isinstance(items, list):
            raise ValueError("Backup chapter items are not valid.")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Backup chapter item is not valid.")
            item_kind = item.get("item_kind")
            if item_kind == "photo":
                new_item_id = photo_id_map.get(item.get("item_id"))
            elif item_kind == "text":
                new_item_id = text_id_map.get(item.get("item_id"))
            else:
                new_item_id = None
            if not new_item_id:
                continue

            db.execute(
                """
                INSERT OR IGNORE INTO chapter_items (
                    chapter_id, item_kind, item_id, position, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    new_chapter_id,
                    item_kind,
                    new_item_id,
                    int(item.get("position") or next_chapter_position(db, new_chapter_id)),
                    backup_created_at(item.get("created_at")),
                ),
            )
            imported_chapter_items += 1
        compact_chapter_positions(db, new_chapter_id)

    return imported_chapters, imported_chapter_items


def import_account_backup(backup_file):
    data = backup_file.read()
    if not data:
        raise ValueError("Choose a backup zip to import.")

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = read_backup_manifest(archive)
            db = get_db()
            restore_backup_birthday(db, manifest)

            photo_id_map = {}
            text_id_map = {}
            imported_photos = 0
            duplicate_photos = 0
            imported_messages = 0
            imported_reactions = 0

            for photo in backup_list(manifest, "photos"):
                photo_import = import_photo_from_backup(db, archive, photo)
                new_id = photo_import["id"]
                photo_id_map[photo.get("id")] = new_id
                if photo_import["created"]:
                    imported_photos += 1
                    imported_messages += import_messages_from_backup(
                        db,
                        "photo",
                        new_id,
                        photo.get("messages", []),
                    )
                    imported_reactions += import_owner_reactions_from_backup(
                        db,
                        "photo",
                        new_id,
                        photo.get("reactions", []),
                    )
                else:
                    duplicate_photos += 1

            for entry in backup_list(manifest, "text_entries"):
                new_id = import_text_entry_from_backup(db, entry)
                text_id_map[entry.get("id")] = new_id
                imported_messages += import_messages_from_backup(
                    db,
                    "text",
                    new_id,
                    entry.get("messages", []),
                )
                imported_reactions += import_owner_reactions_from_backup(
                    db,
                    "text",
                    new_id,
                    entry.get("reactions", []),
                )

            imported_chapters, imported_chapter_items = import_chapters_from_backup(
                db,
                backup_list(manifest, "chapters"),
                photo_id_map,
                text_id_map,
            )
    except zipfile.BadZipFile as exc:
        raise ValueError("Choose a valid zip file.") from exc

    return {
        "photos": imported_photos,
        "duplicate_photos": duplicate_photos,
        "text_entries": len(text_id_map),
        "messages": imported_messages,
        "reactions": imported_reactions,
        "chapters": imported_chapters,
        "chapter_items": imported_chapter_items,
    }


@app.route("/")
def index():
    if g.user is None:
        return redirect(url_for("login"))
    if not g.user["birthday"]:
        return redirect(url_for("birthday"))
    return redirect(url_for("timeline"))


@app.route("/home")
@birthday_required
def home():
    return render_template("home.html", photos=random_public_photos(get_db()))


@app.route("/splash")
@birthday_required
def splash():
    return render_template("splash.html", seed=secrets.token_hex(8))


@app.route("/api/splash-photos")
@birthday_required
def splash_photos():
    return jsonify(build_splash_photo_page(get_db()))


@app.route("/photo/<int:photo_id>/thumbnail")
@birthday_required
def splash_photo_thumbnail(photo_id):
    photo = get_owned_photo(photo_id)
    try:
        thumbnail_data = storage_jpeg_from_image(
            photo["image_data"],
            quality=46,
            max_edge=260,
        )
    except ValueError:
        abort(404)
    response = Response(thumbnail_data, mimetype=JPEG_STORAGE_MIME)
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


@app.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        form_values = {
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
        }

        if not username or not first_name or not last_name or not email or not password:
            flash("All fields are required.", "error")
            return render_template("register.html", form=form_values)

        if not is_valid_email(email):
            flash("Enter a valid email address.", "error")
            return render_template("register.html", form=form_values)

        try:
            db = get_db()
            cursor = db.execute(
                """
                INSERT INTO users (
                    username, first_name, last_name, email, password_hash
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    username,
                    first_name,
                    last_name,
                    email,
                    generate_password_hash(password),
                ),
            )
            db.commit()
        except sqlite3.IntegrityError as exc:
            error_text = str(exc).lower()
            if "email" in error_text:
                flash("That email address is already registered.", "error")
            else:
                flash("That username is already taken.", "error")
            return render_template("register.html", form=form_values)

        session.clear()
        session["user_id"] = cursor.lastrowid
        return redirect(url_for("birthday"))

    return render_template("register.html", form={})


@app.route("/profile", methods=("GET", "POST"))
@login_required
def profile():
    form_values = current_profile_form_values()

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = normalize_email(request.form.get("email", ""))
        form_values = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
        }

        if not first_name or not last_name or not email:
            flash("First name, last name, and email address are required.", "error")
            return render_template("profile.html", form=form_values)

        if not is_valid_email(email):
            flash("Enter a valid email address.", "error")
            return render_template("profile.html", form=form_values)

        try:
            db = get_db()
            db.execute(
                """
                UPDATE users
                SET first_name = ?, last_name = ?, email = ?
                WHERE id = ?
                """,
                (first_name, last_name, email, g.user["id"]),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("That email address is already registered.", "error")
            return render_template("profile.html", form=form_values)

        flash("Profile updated.", "success")
        return redirect(url_for("profile"))

    return render_template("profile.html", form=form_values)


@app.route("/account/export.zip")
@login_required
def export_account_backup():
    return account_backup_response(get_db())


@app.route("/account/import", methods=("POST",))
@login_required
def import_account_backup_route():
    backup_file = request.files.get("backup")
    if backup_file is None or not backup_file.filename:
        flash("Choose an EverTimeline backup zip to import.", "error")
        return redirect(url_for("profile"))

    db = get_db()
    try:
        counts = import_account_backup(backup_file)
    except ValueError as exc:
        db.rollback()
        flash(str(exc), "error")
        return redirect(url_for("profile"))

    db.commit()
    duplicate_summary = ""
    if counts["duplicate_photos"]:
        duplicate_noun = "duplicate photo" if counts["duplicate_photos"] == 1 else "duplicate photos"
        duplicate_summary = f" Skipped {counts['duplicate_photos']} {duplicate_noun} already in your timeline."
    flash(
        (
            "Imported backup: "
            f"{counts['photos']} photos, "
            f"{counts['text_entries']} text entries, "
            f"{counts['messages']} messages, "
            f"{counts['chapters']} chapters."
            f"{duplicate_summary}"
        ),
        "success",
    )
    return redirect(url_for("profile"))


@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    return render_template(
        "admin.html",
        image_summary=admin_image_storage_summary(db),
        admin_jobs=recent_admin_jobs(db),
    )


@app.route("/admin/images/convert-jpeg", methods=("POST",))
@admin_required
def admin_convert_images_to_jpeg():
    job_id = enqueue_job(
        g.user["id"],
        "convert_images",
        "Convert images to JPEG",
    )
    flash(f"Image conversion job #{job_id} started.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/images/compact", methods=("POST",))
@admin_required
def admin_compact_images():
    job_id = enqueue_job(
        g.user["id"],
        "compact_images",
        "Compact image storage",
    )
    flash(f"Image compaction job #{job_id} started.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/database/vacuum", methods=("POST",))
@admin_required
def admin_vacuum_database():
    job_id = enqueue_job(
        g.user["id"],
        "vacuum_database",
        "Reclaim database space",
    )
    flash(f"Database cleanup job #{job_id} started.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/jobs/<int:job_id>")
@admin_required
def admin_job_status(job_id):
    job = get_job(get_db(), job_id)
    if job is None:
        abort(404)
    return jsonify(job_row_to_dict(job))


@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?",
            (username,),
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "error")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user["id"]
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/forgot-password", methods=("GET", "POST"))
def forgot_password():
    reset_url = None
    identifier = ""

    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        if not identifier:
            flash("Enter your username or email address.", "error")
            return render_template(
                "forgot_password.html",
                identifier=identifier,
                reset_url=reset_url,
            )

        db = get_db()
        user = find_user_for_password_reset(db, identifier)
        if user is not None and app.config.get("LOCAL_PASSWORD_RESET_LINKS", False):
            token = create_password_reset_token(db, user["id"])
            db.commit()
            reset_url = url_for("reset_password", token=token, _external=True)

        flash(
            "If an account matches that information and reset delivery is configured, reset instructions will be available.",
            "success",
        )

    return render_template(
        "forgot_password.html",
        identifier=identifier,
        reset_url=reset_url,
    )


@app.route("/reset-password/<token>", methods=("GET", "POST"))
def reset_password(token):
    db = get_db()
    reset_row = get_valid_password_reset(db, token)
    if reset_row is None:
        flash("That password reset link is invalid or expired.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not password:
            flash("Enter a new password.", "error")
            return render_template("reset_password.html", token=token)

        if password != confirm_password:
            flash("The passwords do not match.", "error")
            return render_template("reset_password.html", token=token)

        db.execute(
            """
            UPDATE users
            SET password_hash = ?
            WHERE id = ?
            """,
            (generate_password_hash(password), reset_row["user_id"]),
        )
        db.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (reset_row["id"],),
        )
        db.commit()
        session.clear()
        flash("Password updated. You can log in with your new password.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)


@app.route("/logout", methods=("POST",))
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/birthday", methods=("GET", "POST"))
@login_required
def birthday():
    if request.method == "POST":
        birthday_value = request.form.get("birthday", "")
        try:
            birthday_date = parse_iso_date(birthday_value, "Birthday")
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("birthday.html")

        if birthday_date > date.today():
            flash("Birthday cannot be in the future.", "error")
            return render_template("birthday.html")

        db = get_db()
        current_birthday = g.user["birthday"]
        birthday_changed = current_birthday != birthday_date.isoformat()
        is_confirming = request.form.get("action") == "confirm"

        if current_birthday and birthday_changed and not is_confirming:
            return render_template(
                "birthday.html",
                confirmation_required=True,
                pending_birthday=birthday_date.isoformat(),
                deletion_summary=birthday_deletion_summary(db, g.user["id"], birthday_date),
            )

        if current_birthday and birthday_changed and is_confirming:
            confirmation_text = request.form.get("confirmation_text", "").strip().lower()
            deletion_summary = birthday_deletion_summary(db, g.user["id"], birthday_date)
            if confirmation_text != "proceed":
                flash("Type proceed to confirm the birthday change.", "error")
                return render_template(
                    "birthday.html",
                    confirmation_required=True,
                    pending_birthday=birthday_date.isoformat(),
                    deletion_summary=deletion_summary,
                )

            deleted_counts = delete_items_before_birthday(db, g.user["id"], birthday_date)
        else:
            deleted_counts = {"total_count": 0, "photo_count": 0, "text_count": 0}

        db.execute(
            "UPDATE users SET birthday = ? WHERE id = ?",
            (birthday_date.isoformat(), g.user["id"]),
        )
        db.commit()
        if deleted_counts["total_count"]:
            photo_word = "photo" if deleted_counts["photo_count"] == 1 else "photos"
            text_word = "text entry" if deleted_counts["text_count"] == 1 else "text entries"
            flash(
                (
                    "Birthday updated. Deleted "
                    f"{deleted_counts['photo_count']} {photo_word} and "
                    f"{deleted_counts['text_count']} {text_word} before {birthday_date.isoformat()}."
                ),
                "success",
            )
        elif birthday_changed:
            flash("Birthday updated.", "success")
        return redirect(url_for("timeline"))

    return render_template("birthday.html")


@app.route("/timeline")
@birthday_required
def timeline():
    db = get_db()
    preview = current_privacy_preview()
    years = list(user_years())
    year_counts = get_year_counts(db, allowed_tags=privacy_preview_allowed_tags(preview))
    return render_template("timeline.html", years=years, year_counts=year_counts)


@app.route("/timeline/import", methods=("GET", "POST"))
@birthday_required
def timeline_import():
    if request.method == "POST":
        images = uploaded_photo_files()
        if not images:
            flash("Choose at least one image to import.", "error")
            return redirect(url_for("timeline_import"))

        db = get_db()
        token = secrets.token_urlsafe(24)
        batch = db.execute(
            """
            INSERT INTO photo_import_batches (user_id, token)
            VALUES (?, ?)
            """,
            (g.user["id"], token),
        )
        staged_count = 0
        skipped_count = 0
        duplicate_count = 0
        seen_hashes = {}

        for image in images:
            if image.mimetype not in ALLOWED_IMAGE_MIMES:
                skipped_count += 1
                continue

            original_image_data = image.read()
            if not original_image_data:
                skipped_count += 1
                continue

            try:
                storage_image_data = storage_jpeg_from_image(original_image_data)
            except ValueError:
                skipped_count += 1
                continue

            image_hash = photo_image_hash(storage_image_data)
            duplicate = find_duplicate_photo(db, g.user["id"], image_hash)
            duplicate_import_item_id = seen_hashes.get(image_hash)
            duplicate_photo_id = duplicate["id"] if duplicate is not None else None
            if duplicate_photo_id or duplicate_import_item_id:
                duplicate_count += 1

            detected_date, detected_source = detect_import_photo_date(original_image_data, image.filename)
            cursor = db.execute(
                """
                INSERT INTO photo_import_items (
                    batch_id, original_filename, mime_type, image_data,
                    image_hash, detected_date, detected_source,
                    duplicate_photo_id, duplicate_import_item_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.lastrowid,
                    secure_filename(image.filename) or "photo",
                    JPEG_STORAGE_MIME,
                    storage_image_data,
                    image_hash,
                    detected_date,
                    detected_source,
                    duplicate_photo_id,
                    duplicate_import_item_id,
                ),
            )
            if image_hash not in seen_hashes:
                seen_hashes[image_hash] = cursor.lastrowid
            staged_count += 1

        if staged_count == 0:
            db.rollback()
            flash("No photos prepared. Choose JPG, PNG, GIF, or WebP images.", "error")
            return redirect(url_for("timeline_import"))

        db.commit()
        parts = [f"Prepared {staged_count} {'photo' if staged_count == 1 else 'photos'} for review."]
        if skipped_count:
            parts.append(f"Skipped {skipped_count} unsupported or empty {'file' if skipped_count == 1 else 'files'}.")
        if duplicate_count:
            parts.append(f"Flagged {duplicate_count} possible duplicate {'photo' if duplicate_count == 1 else 'photos'}.")
        flash(" ".join(parts), "success")
        return redirect(url_for("timeline_import_review", token=token))

    return render_template("timeline_import.html")


@app.route("/timeline/import/<token>", methods=("GET", "POST"))
@birthday_required
def timeline_import_review(token):
    db = get_db()
    batch = get_import_batch(db, token)
    item_rows = db.execute(
        """
        SELECT *
        FROM photo_import_items
        WHERE batch_id = ?
        ORDER BY id ASC
        """,
        (batch["id"],),
    ).fetchall()

    if request.method == "POST" and request.form.get("action") == "discard":
        db.execute("DELETE FROM photo_import_batches WHERE id = ?", (batch["id"],))
        db.commit()
        flash("Import batch discarded.", "success")
        return redirect(url_for("timeline_import"))

    if request.method == "POST":
        errors = []
        prepared = []
        skipped_count = 0
        for item in item_rows:
            item_id = str(item["id"])
            if f"skip_{item_id}" in request.form:
                skipped_count += 1
                continue

            raw_date = (request.form.get(f"photo_date_{item_id}") or "").strip()
            if not raw_date:
                errors.append(f"{item['original_filename']}: choose a date or skip this photo.")
                continue

            try:
                parsed_date = normalize_import_photo_date(raw_date)
            except ValueError as exc:
                errors.append(f"{item['original_filename']}: {exc}")
                continue

            tags = parse_tags(request.form.get(f"tags_{item_id}", DEFAULT_TAG))
            prepared.append(
                {
                    "item": item,
                    "date": parsed_date,
                    "tags": tags,
                }
            )

        if errors:
            for error in errors[:4]:
                flash(error, "error")
            if len(errors) > 4:
                flash(f"Fix {len(errors) - 4} more import issue(s).", "error")
            return render_template(
                "timeline_import_review.html",
                token=token,
                items=import_review_items(db, token, batch["id"], item_rows, request.form),
            )

        imported_count = 0
        duplicate_count = 0
        imported_months = set()
        for prepared_item in prepared:
            item = prepared_item["item"]
            existing_duplicate = find_duplicate_photo(db, g.user["id"], item["image_hash"])
            duplicate_was_reviewed = (
                (item["duplicate_photo_id"] and existing_duplicate and existing_duplicate["id"] == item["duplicate_photo_id"])
                or item["duplicate_import_item_id"]
            )
            if existing_duplicate and not duplicate_was_reviewed:
                duplicate_count += 1
                continue

            parsed_date = prepared_item["date"]
            insert_photo_record(
                db,
                item["original_filename"],
                item["mime_type"],
                item["image_data"],
                item["image_hash"],
                parsed_date.year,
                parsed_date.month,
                parsed_date.isoformat(),
                "",
                "",
                prepared_item["tags"],
                empty_location_payload(),
            )
            imported_count += 1
            imported_months.add((parsed_date.year, parsed_date.month))

        db.execute("DELETE FROM photo_import_batches WHERE id = ?", (batch["id"],))
        db.commit()

        parts = []
        if imported_count:
            parts.append(f"Imported {imported_count} {'photo' if imported_count == 1 else 'photos'}.")
        else:
            parts.append("No photos imported.")
        if skipped_count:
            parts.append(f"Skipped {skipped_count} during review.")
        if duplicate_count:
            parts.append(f"Skipped {duplicate_count} duplicate {'photo' if duplicate_count == 1 else 'photos'}.")
        flash(" ".join(parts), "success" if imported_count else "error")

        if len(imported_months) == 1:
            year, month = next(iter(imported_months))
            return redirect(url_for("month_view", year=year, month=month))
        return redirect(url_for("timeline"))

    return render_template(
        "timeline_import_review.html",
        token=token,
        items=import_review_items(db, token, batch["id"], item_rows),
    )


@app.route("/timeline/import/<token>/items/<int:item_id>/image")
@birthday_required
def timeline_import_item_image(token, item_id):
    db = get_db()
    batch = get_import_batch(db, token)
    item = get_import_item(db, batch["id"], item_id)
    return Response(item["image_data"], mimetype=item["mime_type"])


@app.route("/on-this-day")
@birthday_required
def on_this_day():
    raw_date = request.args.get("date", "").strip()
    selected_date = date.today()
    if raw_date:
        try:
            selected_date = date.fromisoformat(raw_date)
        except ValueError:
            flash("Choose a valid date.", "error")
            return redirect(url_for("on_this_day"))

    items = build_on_this_day_items(
        get_db(),
        g.user["id"],
        selected_date.month,
        selected_date.day,
        lambda photo_id: url_for("photo_image", photo_id=photo_id),
    )
    years = sorted({item["year"] for item in items})
    return render_template(
        "on_this_day.html",
        selected_date=selected_date,
        selected_date_value=selected_date.isoformat(),
        month_day_label=f"{MONTH_NAMES[selected_date.month - 1]} {selected_date.day}",
        items=items,
        years=years,
    )


@app.route("/timeline/search")
@birthday_required
def timeline_search():
    db = get_db()
    filters = timeline_search_filters()
    quick_searches = [
        {
            "label": "Public photos without captions",
            "url": url_for("timeline_search", kind="photo", visibility="public", caption="without"),
        },
        {
            "label": "Items with places",
            "url": url_for("timeline_search", location="with"),
        },
        {
            "label": "Items with messages",
            "url": url_for("timeline_search", messages="with"),
        },
        {
            "label": "Memories not in chapters",
            "url": url_for("timeline_search", chapter="out"),
        },
        {
            "label": "Public view audit",
            "url": url_for("timeline_search", visibility="public"),
        },
    ]
    return render_template(
        "timeline_search.html",
        query=filters["query"],
        filters=filters,
        quick_searches=quick_searches,
        kind_options=[
            ("all", "Everything"),
            ("photo", "Photos"),
            ("text", "Text entries"),
            ("message", "Messages"),
            ("chapter", "Chapters"),
        ],
        tri_options=[
            ("all", "Any"),
            ("with", "With"),
            ("without", "Without"),
        ],
        chapter_options=[
            ("all", "Any"),
            ("in", "In a chapter"),
            ("out", "Not in a chapter"),
        ],
        results=search_timeline_content(db, filters),
        has_query=timeline_search_has_active_filters(filters),
    )


@app.route("/timeline/collections", methods=("GET", "POST"))
@birthday_required
def timeline_collections():
    db = get_db()
    try:
        filters = collection_filters_from_source(request.form if request.method == "POST" else request.args)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("timeline_collections"))

    if request.method == "POST":
        title = " ".join((request.form.get("title") or "").strip().split())[:120]
        if not title:
            flash("Name the saved view.", "error")
            return redirect(url_for("timeline_collections", **collection_query_values(filters)))
        if not collection_filters_have_values(filters):
            flash("Choose at least one filter before saving a view.", "error")
            return redirect(url_for("timeline_collections"))

        db.execute(
            """
            INSERT INTO saved_timeline_views (
                user_id, title, query_text, item_kind, people_text,
                location_text, privacy_tag, date_start, date_end
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                g.user["id"],
                title,
                filters["q"],
                filters["item_kind"],
                filters["people"],
                filters["location"],
                filters["privacy_tag"],
                filters["date_start"],
                filters["date_end"],
            ),
        )
        db.commit()
        flash("Saved timeline view.", "success")
        return redirect(url_for("timeline_collections", **collection_query_values(filters)))

    results = search_collection_items(db, filters) if collection_filters_have_values(filters) else []
    return render_template(
        "timeline_collections.html",
        filters=filters,
        results=results,
        has_filters=collection_filters_have_values(filters),
        saved_views=load_saved_collection_views(db),
        collection_query_values=collection_query_values(filters),
    )


@app.route("/timeline/collections/<int:view_id>/delete", methods=("POST",))
@birthday_required
def delete_timeline_collection(view_id):
    get_saved_collection_view(get_db(), view_id)
    db = get_db()
    db.execute(
        """
        DELETE FROM saved_timeline_views
        WHERE id = ? AND user_id = ?
        """,
        (view_id, g.user["id"]),
    )
    db.commit()
    flash("Deleted saved view.", "success")
    return redirect(url_for("timeline_collections"))


@app.route("/timeline/stories", methods=("GET", "POST"))
@birthday_required
def timeline_stories():
    db = get_db()

    if request.method == "POST":
        title = " ".join((request.form.get("title") or "").strip().split())[:120]
        if not title:
            flash("Give your story a title.", "error")
            return redirect(request.url)

        try:
            source_mode, story_filters = timeline_story_filters(request.form)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(request.url)
        if not timeline_story_has_filters(source_mode, story_filters):
            flash("Pick at least one filter before saving a story.", "error")
            return redirect(
                url_for(
                    "timeline_stories",
                    source_mode=source_mode,
                    **timeline_story_filter_values(source_mode, story_filters),
                )
            )

        subtitle = (request.form.get("subtitle") or "").strip()[:240]
        db.execute(
            """
            INSERT INTO timeline_stories (
                user_id, title, subtitle, source_mode, filter_payload
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                g.user["id"],
                title,
                subtitle,
                source_mode,
                json.dumps(story_filters),
            ),
        )
        story_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        db.commit()
        flash("Saved memory story.", "success")
        return redirect(url_for("timeline_story", story_id=story_id))

    try:
        source_mode, filters = timeline_story_filters(request.args)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("timeline_stories"))
    has_filters = timeline_story_has_filters(source_mode, filters)
    items = build_timeline_stories_query(db, source_mode, filters) if has_filters else []
    return render_template(
        "timeline_stories.html",
        stories=load_timeline_stories(db),
        source_mode=source_mode,
        filters=filters,
        filter_values=timeline_story_filter_values(source_mode, filters),
        has_filters=has_filters,
        items=items,
    )


@app.route("/timeline/stories/<int:story_id>")
@birthday_required
def timeline_story(story_id):
    db = get_db()
    story = get_timeline_story(db, story_id)
    source_mode = story["source_mode"]
    story_filters = parse_story_filters(story["filter_payload"], source_mode)
    has_filters = timeline_story_has_filters(source_mode, story_filters)
    items = build_timeline_stories_query(db, source_mode, story_filters) if has_filters else []
    source_label = "Timeline search" if source_mode == "search" else "Saved collection filters"
    return render_template(
        "timeline_story.html",
        story=story,
        source_label=source_label,
        filters=story_filters,
        items=items,
        has_filters=has_filters,
    )


@app.route("/timeline/stories/<int:story_id>/delete", methods=("POST",))
@birthday_required
def delete_timeline_story(story_id):
    db = get_db()
    get_timeline_story(db, story_id)
    db.execute(
        """
        DELETE FROM timeline_stories
        WHERE id = ? AND user_id = ?
        """,
        (story_id, g.user["id"]),
    )
    db.commit()
    flash("Deleted memory story.", "success")
    return redirect(url_for("timeline_stories"))


@app.route("/timeline/people")
@birthday_required
def timeline_people():
    people = build_people_summaries(get_db())
    return render_template(
        "timeline_people.html",
        people=people,
        overview=build_people_hub_overview(people),
    )


@app.route("/timeline/people/<int:person_id>")
@birthday_required
def timeline_person(person_id):
    db = get_db()
    person = get_timeline_person(db, person_id)
    items = build_person_timeline_items(db, person_id)
    return render_template(
        "timeline_person.html",
        person=person,
        items=items,
        hub=build_person_hub(db, person, items),
    )

@app.route("/timeline/map")
@birthday_required
def timeline_map():
    db = get_db()
    all_items = build_timeline_map_items(db)
    item_type = request.args.get("type", "all")
    if item_type not in ("all", "photo", "text"):
        item_type = "all"
    selected_year = request.args.get("year", type=int)
    available_years = sorted({item["year"] for item in all_items})
    if selected_year is not None and selected_year not in user_years():
        abort(404)

    items = filter_timeline_map_items(all_items, item_type, selected_year)
    place_summaries = build_place_summaries(items)
    return render_template(
        "timeline_map.html",
        items=items,
        mapped_items=[item for item in items if item["has_coordinates"]],
        named_items=[item for item in items if not item["has_coordinates"]],
        place_summaries=place_summaries,
        mapped_places=[place for place in place_summaries if place["has_coordinates"]],
        coordinate_items=[
            item
            for item in items
            if item["has_coordinates"] and not normalize_place_name(item["location_name"])
        ],
        available_years=available_years,
        selected_year=selected_year,
        selected_type=item_type,
    )


@app.route("/timeline/map/place")
@birthday_required
def timeline_place():
    place_name = normalize_place_name(request.args.get("name", ""))
    if not place_name:
        abort(404)

    db = get_db()
    all_items = build_timeline_map_items(db)
    target_key = place_group_key(place_name)
    items = [
        item
        for item in all_items
        if place_group_key(item["location_name"]) == target_key
    ]
    if not items:
        abort(404)

    return render_template(
        "timeline_place.html",
        place_name=normalize_place_name(items[0]["location_name"]),
        items=items,
        mapped_items=[item for item in items if item["has_coordinates"]],
        year_label=build_place_summaries(items)[0]["year_label"],
        hub=build_place_hub(db, normalize_place_name(items[0]["location_name"]), items),
    )


@app.route("/year/<int:year>")
@birthday_required
def year_view(year):
    validate_year_month(year, 1)
    db = get_db()
    preview = current_privacy_preview()
    month_counts = get_month_counts(db, year, allowed_tags=privacy_preview_allowed_tags(preview))
    months = [(index + 1, month) for index, month in enumerate(MONTH_NAMES)]
    return render_template(
        "months.html",
        year=year,
        months=months,
        month_counts=month_counts,
    )


@app.route("/year/<int:year>/export.pdf")
@birthday_required
def export_year_pdf(year):
    validate_year_month(year, 1)
    db = get_db()
    title = f"EverTimeline {year}"
    owner = user_full_name(g.user) or g.user["username"]
    subtitle = f"Owner: {owner} | Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    items = build_pdf_export_items(db, g.user["id"], year)
    return pdf_export_response(
        title,
        subtitle,
        f"evertimeline-{year}.pdf",
        items,
    )


@app.route("/year/<int:year>/privacy", methods=("POST",))
@birthday_required
def bulk_year_privacy(year):
    validate_year_month(year, 1)
    tag = request.form.get("tags", "")
    db = get_db()
    try:
        total = bulk_set_privacy(db, year, tag)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("year_view", year=year))

    db.commit()
    if total:
        flash(f"Updated visibility for {total} items in {year}.", "success")
    else:
        flash(f"No items found in {year}.", "error")
    return redirect(url_for("year_view", year=year))


@app.route("/year/<int:year>/<int:month>", methods=("GET", "POST"))
@birthday_required
def month_view(year, month):
    validate_year_month(year, month)
    db = get_db()
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])

    if request.method == "POST":
        images = uploaded_photo_files()
        photo_date = request.form.get("photo_date", "")
        photo_title = normalize_photo_title(request.form.get("title", ""))
        photo_caption = normalize_photo_caption(request.form.get("caption", ""))
        tags = parse_tags(request.form.get("tags", ""))
        people = parse_people(request.form.get("people", ""))

        if not images:
            flash("Choose at least one image to upload.", "error")
            return redirect(url_for("month_view", year=year, month=month))

        try:
            manual_photo_date = normalize_month_date(
                photo_date,
                "Photo date",
                year,
                month,
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("month_view", year=year, month=month))

        try:
            location = normalize_location_payload(request.form)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("month_view", year=year, month=month))

        uploaded_count = 0
        auto_dated_count = 0
        ignored_exif_count = 0
        skipped_count = 0
        duplicate_count = 0

        for image in images:
            if image.mimetype not in ALLOWED_IMAGE_MIMES:
                skipped_count += 1
                continue

            original_image_data = image.read()
            if not original_image_data:
                skipped_count += 1
                continue

            try:
                storage_image_data = storage_jpeg_from_image(original_image_data)
            except ValueError:
                skipped_count += 1
                continue

            image_hash = photo_image_hash(storage_image_data)
            if find_duplicate_photo(db, g.user["id"], image_hash):
                duplicate_count += 1
                continue

            normalized_photo_date, used_auto_date, ignored_exif_date = photo_date_from_upload(
                original_image_data,
                year,
                month,
                manual_photo_date,
                image.filename,
            )
            insert_uploaded_photo(
                db,
                image,
                storage_image_data,
                image_hash,
                year,
                month,
                normalized_photo_date,
                photo_title,
                photo_caption,
                tags,
                location,
                people,
            )
            uploaded_count += 1
            if used_auto_date:
                auto_dated_count += 1
            if ignored_exif_date:
                ignored_exif_count += 1

        if uploaded_count == 0:
            db.rollback()
            if duplicate_count:
                duplicate_noun = "duplicate photo" if duplicate_count == 1 else "duplicate photos"
                flash(f"No new photos uploaded. Skipped {duplicate_count} {duplicate_noun} already in your timeline.", "error")
            else:
                flash("No photos uploaded. Choose JPG, PNG, GIF, or WebP images.", "error")
            return redirect(url_for("month_view", year=year, month=month))

        db.commit()
        flash(
            photo_upload_summary(
                uploaded_count,
                auto_dated_count,
                bool(manual_photo_date),
                skipped_count,
                ignored_exif_count,
                duplicate_count,
            ),
            "success",
        )
        return redirect(url_for("month_view", year=year, month=month))

    preview = current_privacy_preview()
    items = build_month_items(
        db,
        g.user["id"],
        year,
        month,
        lambda photo_id: url_for("photo_image", photo_id=photo_id),
        privacy_preview_allowed_tags(preview),
    )
    return render_template(
        "month.html",
        year=year,
        month=month,
        month_name=MONTH_NAMES[month - 1],
        month_start=month_start.isoformat(),
        month_end=month_end.isoformat(),
        items=items,
        chapters=get_chapter_options(db),
    )


@app.route("/year/<int:year>/<int:month>/export.pdf")
@birthday_required
def export_month_pdf(year, month):
    validate_year_month(year, month)
    db = get_db()
    month_name = MONTH_NAMES[month - 1]
    title = f"EverTimeline {month_name} {year}"
    owner = user_full_name(g.user) or g.user["username"]
    subtitle = f"Owner: {owner} | Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    items = build_pdf_export_items(db, g.user["id"], year, month)
    return pdf_export_response(
        title,
        subtitle,
        f"evertimeline-{year}-{month:02d}.pdf",
        items,
    )


@app.route("/year/<int:year>/<int:month>/privacy", methods=("POST",))
@birthday_required
def bulk_month_privacy(year, month):
    validate_year_month(year, month)
    tag = request.form.get("tags", "")
    db = get_db()
    try:
        total = bulk_set_privacy(db, year, tag, month)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("month_view", year=year, month=month))

    db.commit()
    month_name = MONTH_NAMES[month - 1]
    if total:
        flash(f"Updated visibility for {total} items in {month_name} {year}.", "success")
    else:
        flash(f"No items found in {month_name} {year}.", "error")
    return redirect(url_for("month_view", year=year, month=month))


@app.route("/year/<int:year>/<int:month>/text", methods=("POST",))
@birthday_required
def create_text_entry(year, month):
    validate_year_month(year, month)
    body = request.form.get("body", "")
    entry_date = request.form.get("entry_date", "")
    tags = parse_tags(request.form.get("tags", ""))
    people = parse_people(request.form.get("people", ""))

    if not body.strip():
        flash("Text entry cannot be empty.", "error")
        return redirect(url_for("month_view", year=year, month=month))

    try:
        normalized_entry_date = normalize_month_date(
            entry_date,
            "Text entry date",
            year,
            month,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("month_view", year=year, month=month))

    try:
        location = normalize_location_payload(request.form)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("month_view", year=year, month=month))

    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO text_entries (
            user_id, year, month, body, entry_date, location_name, latitude, longitude
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["id"],
            year,
            month,
            body,
            normalized_entry_date,
            location["location_name"],
            location["latitude"],
            location["longitude"],
        ),
    )
    set_tags_for_item(db, "text", cursor.lastrowid, tags)
    set_people_for_item(db, "text", cursor.lastrowid, people)
    db.commit()
    flash("Text entry saved.", "success")
    return redirect(url_for("month_view", year=year, month=month))


@app.route("/api/timeline-items")
@birthday_required
def timeline_items():
    db = get_db()
    preview = current_privacy_preview()
    selected_year = request.args.get("year", type=int)
    if selected_year is not None:
        if selected_year not in user_years():
            abort(404)
    return jsonify(
        build_timeline_api_items(
            db,
            g.user["id"],
            lambda photo_id: url_for("photo_image", photo_id=photo_id),
            selected_year,
            privacy_preview_allowed_tags(preview),
            message_url_builder=lambda item_kind, item_id: url_for(
                "timeline_item_messages",
                item_kind=item_kind,
                item_id=item_id,
            ),
            can_message=True,
            include_messages=request_includes_messages(),
        )
    )


@app.route("/chapters", methods=("GET", "POST"))
@birthday_required
def chapters():
    db = get_db()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        visibility = chapter_visibility(request.form.get("visibility"))
        if not title:
            flash("Chapter title is required.", "error")
            return redirect(url_for("chapters"))

        cursor = db.execute(
            """
            INSERT INTO chapters (user_id, title, description, visibility)
            VALUES (?, ?, ?, ?)
            """,
            (g.user["id"], title, description or None, visibility),
        )
        db.commit()
        flash("Chapter created.", "success")
        return redirect(url_for("chapter_detail", chapter_id=cursor.lastrowid))

    return render_template(
        "chapters.html",
        chapters=get_chapters_with_counts(db),
        shared_chapters=get_shared_chapters(db),
    )


@app.route("/chapters/draft", methods=("GET", "POST"))
@birthday_required
def chapter_draft():
    db = get_db()
    filters = chapter_draft_filters()
    draft = build_chapter_draft(db, filters)
    form_title = request.form.get("title", draft["title"]).strip()
    form_description = request.form.get("description", draft["description"]).strip()
    form_visibility = chapter_visibility(request.form.get("visibility", DEFAULT_TAG))
    selected_refs = request.form.getlist("item_refs") if request.method == "POST" else [item["ref"] for item in draft["items"]]

    if request.method == "POST":
        refs = parse_chapter_draft_refs(selected_refs)
        if not form_title:
            flash("Chapter title is required.", "error")
        elif not refs:
            flash("Choose at least one memory for the chapter.", "error")
        else:
            owned_refs = [
                (item_kind, get_owned_timeline_item(item_kind, item_id)["id"])
                for item_kind, item_id in refs
            ]
            cursor = db.execute(
                """
                INSERT INTO chapters (
                    user_id, title, description, visibility, cover_item_kind, cover_item_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    g.user["id"],
                    form_title,
                    form_description or None,
                    form_visibility,
                    owned_refs[0][0],
                    owned_refs[0][1],
                ),
            )
            chapter_id = cursor.lastrowid
            for position, (item_kind, item_id) in enumerate(owned_refs, start=1):
                db.execute(
                    """
                    INSERT INTO chapter_items (chapter_id, item_kind, item_id, position)
                    VALUES (?, ?, ?, ?)
                    """,
                    (chapter_id, item_kind, item_id, position),
                )
            db.commit()
            flash("Chapter draft created.", "success")
            return redirect(url_for("chapter_detail", chapter_id=chapter_id))

    return render_template(
        "chapter_draft.html",
        filters=filters,
        draft=draft,
        title=form_title,
        description=form_description,
        visibility=form_visibility,
        selected_refs=set(selected_refs),
        years=list(user_years()),
    )


@app.route("/chapters/bulk-select")
@birthday_required
def chapter_bulk_select():
    return render_template("chapter_bulk_select.html", seed=secrets.token_hex(8))


@app.route("/chapters/bulk-review", methods=("GET", "POST"))
@birthday_required
def chapter_bulk_review():
    if request.method == "GET":
        return redirect(url_for("chapter_bulk_select"))

    db = get_db()
    photo_ids = parse_chapter_bulk_photo_ids(request.form.get("selected_photo_ids"))
    photos = selected_chapter_bulk_photos(db, photo_ids)
    if not photos:
        flash("Choose at least one photo for the chapter.", "error")
        return redirect(url_for("chapter_bulk_select"))
    if len(photos) != len(photo_ids):
        flash("Some selected photos could not be found.", "error")
        return redirect(url_for("chapter_bulk_select"))

    return render_template(
        "chapter_bulk_review.html",
        photos=photos,
        selected_photo_ids=photo_ids,
        form={
            "title": "",
            "description": "",
            "visibility": DEFAULT_TAG,
        },
    )


@app.route("/chapters/bulk-create", methods=("POST",))
@birthday_required
def chapter_bulk_create():
    db = get_db()
    photo_ids = parse_chapter_bulk_photo_ids(request.form.get("selected_photo_ids"))
    photos = selected_chapter_bulk_photos(db, photo_ids)
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    visibility = chapter_visibility(request.form.get("visibility"))

    if not photos:
        flash("Choose at least one photo for the chapter.", "error")
        return redirect(url_for("chapter_bulk_select"))
    if len(photos) != len(photo_ids):
        flash("Some selected photos could not be found.", "error")
        return redirect(url_for("chapter_bulk_select"))
    if not title:
        flash("Chapter title is required.", "error")
        return render_template(
            "chapter_bulk_review.html",
            photos=photos,
            selected_photo_ids=photo_ids,
            form={
                "title": title,
                "description": description,
                "visibility": visibility,
            },
        )

    cursor = db.execute(
        """
        INSERT INTO chapters (user_id, title, description, visibility)
        VALUES (?, ?, ?, ?)
        """,
        (g.user["id"], title, description or None, visibility),
    )
    chapter_id = cursor.lastrowid
    for position, photo_id in enumerate(photo_ids, start=1):
        db.execute(
            """
            INSERT INTO chapter_items (chapter_id, item_kind, item_id, position)
            VALUES (?, 'photo', ?, ?)
            """,
            (chapter_id, photo_id, position),
        )
    db.commit()
    flash("Chapter created from selected photos.", "success")
    return redirect(url_for("chapter_detail", chapter_id=chapter_id))

@app.route("/chapters/<int:chapter_id>")
@birthday_required
def chapter_detail(chapter_id):
    db = get_db()
    chapter = get_owned_chapter(chapter_id)
    items = build_chapter_items(
        db,
        chapter_id,
        lambda photo_id: url_for("photo_image", photo_id=photo_id),
    )
    chapter = dict(chapter)
    chapter["visibility"] = chapter_visibility(chapter.get("visibility"))
    chapter.update(privacy_payload_for_tags([chapter["visibility"]]))
    chapter["cover"] = get_chapter_cover(
        db,
        chapter,
        lambda photo_id: url_for("photo_image", photo_id=photo_id),
    )
    return render_template(
        "chapter.html",
        chapter=chapter,
        items=items,
        cover_options=items,
        invite_connections=invitable_chapter_connections(db, chapter_id),
        chapter_invites=chapter_invites_for_chapter(db, chapter_id),
        selected_cover_ref=(
            f"{chapter['cover_item_kind']}:{chapter['cover_item_id']}"
            if chapter.get("cover_item_kind") and chapter.get("cover_item_id")
            else ""
        ),
    )


@app.route("/chapters/<int:chapter_id>/settings", methods=("POST",))
@birthday_required
def update_chapter_settings(chapter_id):
    db = get_db()
    get_owned_chapter(chapter_id)
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    visibility = chapter_visibility(request.form.get("visibility"))
    cover_kind, cover_id = parse_chapter_cover_ref(request.form.get("cover_ref", ""))

    if not title:
        flash("Chapter title is required.", "error")
        return redirect(url_for("chapter_detail", chapter_id=chapter_id))

    if cover_kind and not chapter_cover_exists(db, chapter_id, cover_kind, cover_id):
        flash("Choose a cover from this chapter.", "error")
        return redirect(url_for("chapter_detail", chapter_id=chapter_id))

    db.execute(
        """
        UPDATE chapters
        SET title = ?,
            description = ?,
            visibility = ?,
            cover_item_kind = ?,
            cover_item_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
        """,
        (
            title,
            description or None,
            visibility,
            cover_kind,
            cover_id,
            chapter_id,
            g.user["id"],
        ),
    )
    db.commit()
    flash("Chapter settings saved.", "success")
    return redirect(url_for("chapter_detail", chapter_id=chapter_id))


@app.route("/chapters/<int:chapter_id>/invites", methods=("POST",))
@birthday_required
def create_chapter_invite(chapter_id):
    db = get_db()
    get_owned_chapter(chapter_id)
    recipient_id = request.form.get("recipient_id", type=int)
    if recipient_id is None or recipient_id == g.user["id"]:
        flash("Choose a connection to invite.", "error")
        return redirect(url_for("chapter_detail", chapter_id=chapter_id))

    recipient = db.execute(
        """
        SELECT id
        FROM users
        WHERE id = ?
        """,
        (recipient_id,),
    ).fetchone()
    if recipient is None or accepted_connection_between(db, g.user["id"], recipient_id) is None:
        flash("Invite an accepted connection to share this chapter.", "error")
        return redirect(url_for("chapter_detail", chapter_id=chapter_id))

    existing = db.execute(
        """
        SELECT id, status
        FROM chapter_invites
        WHERE chapter_id = ? AND recipient_id = ?
        """,
        (chapter_id, recipient_id),
    ).fetchone()
    if existing is not None:
        flash("That connection already has an invite for this chapter.", "error")
        return redirect(url_for("chapter_detail", chapter_id=chapter_id))

    db.execute(
        """
        INSERT INTO chapter_invites (chapter_id, inviter_id, recipient_id)
        VALUES (?, ?, ?)
        """,
        (chapter_id, g.user["id"], recipient_id),
    )
    db.commit()
    flash("Chapter invite sent.", "success")
    return redirect(url_for("chapter_detail", chapter_id=chapter_id))


@app.route("/chapters/<int:chapter_id>/invites/<int:invite_id>/revoke", methods=("POST",))
@birthday_required
def revoke_chapter_invite(chapter_id, invite_id):
    db = get_db()
    get_owned_chapter(chapter_id)
    invite = db.execute(
        """
        SELECT id
        FROM chapter_invites
        WHERE id = ? AND chapter_id = ?
        """,
        (invite_id, chapter_id),
    ).fetchone()
    if invite is None:
        abort(404)

    db.execute(
        "DELETE FROM chapter_invites WHERE id = ? AND chapter_id = ?",
        (invite_id, chapter_id),
    )
    db.commit()
    flash("Chapter invite removed.", "success")
    return redirect(url_for("chapter_detail", chapter_id=chapter_id))


@app.route("/chapter-invites/<int:invite_id>/accept", methods=("POST",))
@birthday_required
def accept_chapter_invite(invite_id):
    db = get_db()
    invite = db.execute(
        """
        SELECT *
        FROM chapter_invites
        WHERE id = ? AND recipient_id = ? AND status = 'pending'
        """,
        (invite_id, g.user["id"]),
    ).fetchone()
    if invite is None:
        abort(404)

    db.execute(
        """
        UPDATE chapter_invites
        SET status = 'accepted', responded_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (invite_id,),
    )
    db.commit()
    flash("Chapter invite accepted.", "success")
    return redirect(url_for("shared_chapter_detail", chapter_id=invite["chapter_id"]))


@app.route("/chapter-invites/<int:invite_id>/decline", methods=("POST",))
@birthday_required
def decline_chapter_invite(invite_id):
    db = get_db()
    invite = db.execute(
        """
        SELECT id
        FROM chapter_invites
        WHERE id = ? AND recipient_id = ? AND status = 'pending'
        """,
        (invite_id, g.user["id"]),
    ).fetchone()
    if invite is None:
        abort(404)

    db.execute(
        """
        UPDATE chapter_invites
        SET status = 'declined', responded_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (invite_id,),
    )
    db.commit()
    flash("Chapter invite declined.", "success")
    return redirect(url_for("notifications"))


@app.route("/chapters/<int:chapter_id>/delete", methods=("POST",))
@birthday_required
def delete_chapter(chapter_id):
    get_owned_chapter(chapter_id)
    db = get_db()
    db.execute(
        "DELETE FROM chapters WHERE id = ? AND user_id = ?",
        (chapter_id, g.user["id"]),
    )
    db.commit()
    flash("Chapter deleted.", "success")
    return redirect(url_for("chapters"))


@app.route("/chapters/items", methods=("POST",))
@birthday_required
def add_chapter_item():
    db = get_db()
    chapter_id = request.form.get("chapter_id", type=int)
    item_kind = request.form.get("item_kind", "")
    item_id = request.form.get("item_id", type=int)
    if chapter_id is None or item_id is None or item_kind not in ("photo", "text"):
        flash("Choose a chapter and item.", "error")
        return redirect_back()

    get_owned_chapter(chapter_id)
    item = get_owned_timeline_item(item_kind, item_id)
    existing = db.execute(
        """
        SELECT id
        FROM chapter_items
        WHERE chapter_id = ? AND item_kind = ? AND item_id = ?
        """,
        (chapter_id, item_kind, item_id),
    ).fetchone()
    if existing is not None:
        flash("That item is already in this chapter.", "error")
        return redirect_back("chapter_detail", chapter_id=chapter_id)

    db.execute(
        """
        INSERT INTO chapter_items (chapter_id, item_kind, item_id, position)
        VALUES (?, ?, ?, ?)
        """,
        (chapter_id, item_kind, item["id"], next_chapter_position(db, chapter_id)),
    )
    db.execute(
        "UPDATE chapters SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (chapter_id,),
    )
    db.commit()
    flash("Added item to chapter.", "success")
    return redirect_back("chapter_detail", chapter_id=chapter_id)


@app.route("/chapters/<int:chapter_id>/items/<int:chapter_item_id>/remove", methods=("POST",))
@birthday_required
def remove_chapter_item(chapter_id, chapter_item_id):
    db = get_db()
    get_owned_chapter(chapter_id)
    get_owned_chapter_item(db, chapter_id, chapter_item_id)
    db.execute(
        "DELETE FROM chapter_items WHERE id = ? AND chapter_id = ?",
        (chapter_item_id, chapter_id),
    )
    compact_chapter_positions(db, chapter_id)
    db.execute(
        "UPDATE chapters SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (chapter_id,),
    )
    db.commit()
    flash("Removed item from chapter.", "success")
    return redirect(url_for("chapter_detail", chapter_id=chapter_id))


@app.route("/chapters/<int:chapter_id>/items/<int:chapter_item_id>/move", methods=("POST",))
@birthday_required
def reorder_chapter_item(chapter_id, chapter_item_id):
    db = get_db()
    get_owned_chapter(chapter_id)
    get_owned_chapter_item(db, chapter_id, chapter_item_id)
    direction = request.form.get("direction", "")
    if move_chapter_item(db, chapter_id, chapter_item_id, direction):
        db.execute(
            "UPDATE chapters SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (chapter_id,),
        )
        db.commit()
    return redirect(url_for("chapter_detail", chapter_id=chapter_id))


@app.route("/api/chapters/<int:chapter_id>/items/reorder", methods=("POST",))
@birthday_required
def reorder_chapter_items(chapter_id):
    db = get_db()
    get_owned_chapter(chapter_id)
    payload = request.get_json(silent=True) or {}
    item_ids = payload.get("item_ids") or []
    if not isinstance(item_ids, list):
        return jsonify({"error": "Invalid item order."}), 400

    try:
        normalized_ids = [int(item_id) for item_id in item_ids]
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid item order."}), 400

    rows = db.execute(
        """
        SELECT id
        FROM chapter_items
        WHERE chapter_id = ?
        ORDER BY position ASC, id ASC
        """,
        (chapter_id,),
    ).fetchall()
    existing_ids = [row["id"] for row in rows]
    if sorted(normalized_ids) != sorted(existing_ids):
        return jsonify({"error": "Item order does not match this chapter."}), 400

    for position, item_id in enumerate(normalized_ids, start=1):
        db.execute(
            """
            UPDATE chapter_items
            SET position = ?
            WHERE id = ? AND chapter_id = ?
            """,
            (position, item_id, chapter_id),
        )
    db.execute(
        "UPDATE chapters SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (chapter_id,),
    )
    db.commit()
    return jsonify({"status": "ok"})


@app.route("/api/chapters/<int:chapter_id>/items")
@birthday_required
def chapter_items(chapter_id):
    db = get_db()
    get_owned_chapter(chapter_id)
    return jsonify(
        build_chapter_items(
            db,
            chapter_id,
            lambda photo_id: url_for("photo_image", photo_id=photo_id),
            message_url_builder=lambda item_kind, item_id: url_for(
                "timeline_item_messages",
                item_kind=item_kind,
                item_id=item_id,
            ),
            can_message=True,
            include_messages=request_includes_messages(),
        )
    )


@app.route("/shared/chapters/<int:chapter_id>")
@birthday_required
def shared_chapter_detail(chapter_id):
    db = get_db()
    chapter = get_shared_chapter(chapter_id)
    items = build_chapter_items(
        db,
        chapter_id,
        lambda photo_id: url_for(
            "shared_chapter_photo_image",
            chapter_id=chapter_id,
            photo_id=photo_id,
        ),
        owner_id=chapter["user_id"],
        item_url_builder=lambda item_kind, item_id: url_for(
            "shared_chapter_detail",
            chapter_id=chapter_id,
            _anchor=f"{item_kind}-{item_id}",
        ),
        reaction_url_builder=lambda item_kind, item_id: url_for(
            "shared_chapter_item_reaction",
            chapter_id=chapter_id,
            item_kind=item_kind,
            item_id=item_id,
        ),
    )
    chapter["cover"] = get_chapter_cover(
        db,
        chapter,
        lambda photo_id: url_for(
            "shared_chapter_photo_image",
            chapter_id=chapter_id,
            photo_id=photo_id,
        ),
        owner_id=chapter["user_id"],
    )
    return render_template(
        "shared_chapter.html",
        chapter=chapter,
        items=items,
    )


@app.route("/shared/chapters/<int:chapter_id>/photo/<int:photo_id>/image")
@birthday_required
def shared_chapter_photo_image(chapter_id, photo_id):
    chapter = get_shared_chapter_item_access(chapter_id, "photo", photo_id)
    photo = get_db().execute(
        """
        SELECT image_data, mime_type
        FROM photos
        WHERE id = ? AND user_id = ?
        """,
        (photo_id, chapter["user_id"]),
    ).fetchone()
    if photo is None:
        abort(404)
    return Response(photo["image_data"], mimetype=photo["mime_type"])


@app.route("/shared/chapters/<int:chapter_id>/api/items")
@birthday_required
def shared_chapter_items(chapter_id):
    db = get_db()
    chapter = get_shared_chapter(chapter_id)
    return jsonify(
        build_chapter_items(
            db,
            chapter_id,
            lambda photo_id: url_for(
                "shared_chapter_photo_image",
                chapter_id=chapter_id,
                photo_id=photo_id,
            ),
            message_url_builder=lambda item_kind, item_id: url_for(
                "shared_chapter_item_messages",
                chapter_id=chapter_id,
                item_kind=item_kind,
                item_id=item_id,
            ),
            can_message=True,
            owner_id=chapter["user_id"],
            item_url_builder=lambda item_kind, item_id: url_for(
                "shared_chapter_detail",
                chapter_id=chapter_id,
                _anchor=f"{item_kind}-{item_id}",
            ),
            reaction_url_builder=lambda item_kind, item_id: url_for(
                "shared_chapter_item_reaction",
                chapter_id=chapter_id,
                item_kind=item_kind,
                item_id=item_id,
            ),
            include_messages=request_includes_messages(),
        )
    )


@app.route("/shared/chapters/<int:chapter_id>/api/timeline-item/<item_kind>/<int:item_id>/messages", methods=("GET", "POST"))
@birthday_required
def shared_chapter_item_messages(chapter_id, item_kind, item_id):
    get_shared_chapter_item_access(chapter_id, item_kind, item_id)
    db = get_db()

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        body = (payload.get("body") or "").strip()
        if not body:
            return jsonify({"error": "Message cannot be empty."}), 400

        message = create_timeline_item_message(db, item_kind, item_id, body)
        return jsonify(message), 201

    return jsonify(load_messages_for_timeline_item(db, item_kind, item_id))


@app.route("/shared/chapters/<int:chapter_id>/api/timeline-item/<item_kind>/<int:item_id>/reaction", methods=("GET", "PUT", "DELETE"))
@birthday_required
def shared_chapter_item_reaction(chapter_id, item_kind, item_id):
    get_shared_chapter_item_access(chapter_id, item_kind, item_id)
    return timeline_item_reaction_response(get_db(), item_kind, item_id)


@app.route("/connections/<int:connection_id>/timeline")
@login_required
def connection_timeline(connection_id):
    db = get_db()
    connected_user = get_connected_user(connection_id)
    allowed_tags = visible_tags_for_connection(connected_user)
    years = list(timeline_years_for_user(connected_user))
    year_counts = get_year_counts(db, connected_user["id"], allowed_tags)
    return render_template(
        "connection_timeline.html",
        connection=public_user_payload(connected_user),
        years=years,
        year_counts=year_counts,
    )


@app.route("/connections/<int:connection_id>/year/<int:year>")
@login_required
def connection_year_view(connection_id, year):
    db = get_db()
    connected_user = get_connected_user(connection_id)
    allowed_tags = visible_tags_for_connection(connected_user)
    validate_year_month_for_user(connected_user, year, 1)
    month_counts = get_month_counts(db, year, connected_user["id"], allowed_tags)
    months = [(index + 1, month) for index, month in enumerate(MONTH_NAMES)]
    return render_template(
        "connection_months.html",
        connection=public_user_payload(connected_user),
        year=year,
        months=months,
        month_counts=month_counts,
    )


@app.route("/connections/<int:connection_id>/year/<int:year>/<int:month>")
@login_required
def connection_month_view(connection_id, year, month):
    db = get_db()
    connected_user = get_connected_user(connection_id)
    allowed_tags = visible_tags_for_connection(connected_user)
    validate_year_month_for_user(connected_user, year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    items = build_month_items(
        db,
        connected_user["id"],
        year,
        month,
        lambda photo_id: url_for(
            "connection_photo_image",
            connection_id=connected_user["id"],
            photo_id=photo_id,
        ),
        allowed_tags,
    )
    return render_template(
        "connection_month.html",
        connection=public_user_payload(connected_user),
        year=year,
        month=month,
        month_name=MONTH_NAMES[month - 1],
        month_start=month_start.isoformat(),
        month_end=month_end.isoformat(),
        items=items,
    )


@app.route("/connections/<int:connection_id>/api/timeline-items")
@login_required
def connection_timeline_items(connection_id):
    db = get_db()
    connected_user = get_connected_user(connection_id)
    allowed_tags = visible_tags_for_connection(connected_user)
    selected_year = request.args.get("year", type=int)
    if selected_year is not None:
        if selected_year not in timeline_years_for_user(connected_user):
            abort(404)
    return jsonify(
        build_timeline_api_items(
            db,
            connected_user["id"],
            lambda photo_id: url_for(
                "connection_photo_image",
                connection_id=connected_user["id"],
                photo_id=photo_id,
            ),
            selected_year,
            allowed_tags,
            message_url_builder=lambda item_kind, item_id: url_for(
                "connection_timeline_item_messages",
                connection_id=connected_user["id"],
                item_kind=item_kind,
                item_id=item_id,
            ),
            can_message=True,
            include_messages=request_includes_messages(),
        )
    )


@app.route("/connections/<int:connection_id>/photo/<int:photo_id>/image")
@login_required
def connection_photo_image(connection_id, photo_id):
    photo = get_connection_photo(connection_id, photo_id)
    return Response(photo["image_data"], mimetype=photo["mime_type"])


@app.route("/connections/<int:connection_id>/api/photo/<int:photo_id>/messages")
@login_required
def connection_photo_messages(connection_id, photo_id):
    get_connection_photo(connection_id, photo_id)
    return jsonify(load_messages_for_timeline_item(get_db(), "photo", photo_id))


@app.route("/connections/<int:connection_id>/api/timeline-item/<item_kind>/<int:item_id>/messages", methods=("GET", "POST"))
@login_required
def connection_timeline_item_messages(connection_id, item_kind, item_id):
    get_connection_timeline_item(connection_id, item_kind, item_id)
    db = get_db()

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        body = (payload.get("body") or "").strip()
        if not body:
            return jsonify({"error": "Message cannot be empty."}), 400

        message = create_timeline_item_message(db, item_kind, item_id, body)
        return jsonify(message), 201

    return jsonify(load_messages_for_timeline_item(db, item_kind, item_id))


@app.route("/search")
@login_required
def search():
    db = get_db()
    query = request.args.get("q", "").strip()
    return render_template(
        "search.html",
        query=query,
        results=search_people(db, query),
        has_query=bool(query),
    )


@app.route("/connections")
@login_required
def connections():
    db = get_db()
    return render_template(
        "connections.html",
        incoming_requests=get_incoming_connection_requests(db),
        connections=get_accepted_connections(db),
    )


@app.route("/notifications")
@login_required
def notifications():
    db = get_db()
    message_notifications = get_unread_message_notifications(db)
    reaction_notifications = get_unread_reaction_notifications(db)
    mark_message_notifications_read(db, message_notifications)
    mark_reaction_notifications_read(db, reaction_notifications)
    return render_template(
        "notifications.html",
        incoming_requests=get_incoming_connection_requests(db),
        chapter_invites=get_incoming_chapter_invites(db),
        message_notifications=message_notifications,
        reaction_notifications=reaction_notifications,
        feed_items=get_activity_feed(db),
    )


@app.route("/api/notifications/count")
@login_required
def notification_count():
    return jsonify({"count": get_notification_count(get_db())})


@app.route("/activity")
@login_required
def activity():
    return render_template("activity.html", feed_items=get_activity_feed(get_db(), limit=120))


@app.route("/connections/request", methods=("POST",))
@login_required
def create_connection_request():
    db = get_db()
    query = request.form.get("q", "").strip()
    recipient_id = request.form.get("recipient_id", type=int)
    relation = request.form.get("relation", "")

    def connection_redirect():
        target = request.form.get("next", "")
        if target and target.startswith("/") and not target.startswith("//"):
            return redirect(target)
        return redirect(url_for("search", q=query))

    recipient = None
    if recipient_id is not None:
        recipient = db.execute(
            """
            SELECT id, username, first_name, last_name, email
            FROM users
            WHERE id = ?
            """,
            (recipient_id,),
        ).fetchone()

    if recipient is None or recipient["id"] == g.user["id"]:
        flash("Choose a valid user to connect with.", "error")
        return connection_redirect()

    if relation not in CONNECTION_RELATIONS:
        flash("Choose whether this connection is a friend or family.", "error")
        return connection_redirect()

    state = connection_state_for_user(db, recipient["id"])
    if state["status"] == "connected":
        flash("You are already connected.", "error")
        return connection_redirect()
    if state["status"] == "pending_sent":
        flash("A connection request has already been sent.", "error")
        return connection_redirect()
    if state["status"] == "pending_received":
        flash("This user already sent you a request. Accept it on the Connections page.", "error")
        return connection_redirect()

    db.execute(
        """
        INSERT INTO connection_requests (requester_id, recipient_id, relation)
        VALUES (?, ?, ?)
        """,
        (g.user["id"], recipient["id"], relation),
    )
    db.commit()
    flash("Connection request sent.", "success")
    return connection_redirect()


@app.route("/connections/<int:request_id>/accept", methods=("POST",))
@login_required
def accept_connection_request(request_id):
    db = get_db()
    redirect_target = "notifications" if request.form.get("next") == "notifications" else "connections"
    request_row = db.execute(
        """
        SELECT *
        FROM connection_requests
        WHERE id = ? AND recipient_id = ? AND status = 'pending'
        """,
        (request_id, g.user["id"]),
    ).fetchone()
    if request_row is None:
        abort(404)

    db.execute(
        """
        UPDATE connection_requests
        SET status = 'accepted', responded_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (request_id,),
    )
    db.commit()
    flash("Connection accepted.", "success")
    return redirect(url_for(redirect_target))


@app.route("/connections/<int:request_id>/decline", methods=("POST",))
@login_required
def decline_connection_request(request_id):
    db = get_db()
    redirect_target = "notifications" if request.form.get("next") == "notifications" else "connections"
    request_row = db.execute(
        """
        SELECT *
        FROM connection_requests
        WHERE id = ? AND recipient_id = ? AND status = 'pending'
        """,
        (request_id, g.user["id"]),
    ).fetchone()
    if request_row is None:
        abort(404)

    db.execute(
        """
        UPDATE connection_requests
        SET status = 'declined', responded_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (request_id,),
    )
    db.commit()
    flash("Connection request declined.", "success")
    return redirect(url_for(redirect_target))


@app.route("/connections/<int:request_id>/relation", methods=("POST",))
@login_required
def update_connection_relation(request_id):
    relation = request.form.get("relation", "")
    if relation not in CONNECTION_RELATIONS:
        flash("Choose friend or family.", "error")
        return redirect(url_for("connections"))

    db = get_db()
    request_row = db.execute(
        """
        SELECT id
        FROM connection_requests
        WHERE id = ?
            AND status = 'accepted'
            AND (requester_id = ? OR recipient_id = ?)
        """,
        (request_id, g.user["id"], g.user["id"]),
    ).fetchone()
    if request_row is None:
        abort(404)

    db.execute(
        """
        UPDATE connection_requests
        SET relation = ?
        WHERE id = ?
        """,
        (relation, request_id),
    )
    db.commit()
    flash("Connection type updated.", "success")
    return redirect(url_for("connections"))


@app.route("/connections/<int:request_id>/remove", methods=("POST",))
@login_required
def remove_connection(request_id):
    db = get_db()
    request_row = db.execute(
        """
        SELECT id
        FROM connection_requests
        WHERE id = ?
            AND status = 'accepted'
            AND (requester_id = ? OR recipient_id = ?)
        """,
        (request_id, g.user["id"], g.user["id"]),
    ).fetchone()
    if request_row is None:
        abort(404)

    db.execute("DELETE FROM connection_requests WHERE id = ?", (request_id,))
    db.commit()
    flash("Connection removed.", "success")
    return redirect(url_for("connections"))


@app.route("/photo/<int:photo_id>/image")
@birthday_required
def photo_image(photo_id):
    photo = get_owned_photo(photo_id)
    return Response(photo["image_data"], mimetype=photo["mime_type"])


@app.route("/public/photo/<int:photo_id>/image")
@birthday_required
def public_photo_image(photo_id):
    photo = get_public_photo(photo_id)
    return Response(photo["image_data"], mimetype=photo["mime_type"])


@app.route("/public/photo/<int:photo_id>/messages", methods=("GET", "POST"))
@birthday_required
def public_photo_messages(photo_id):
    get_public_photo(photo_id)
    db = get_db()

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        body = (payload.get("body") or "").strip()
        if not body:
            return jsonify({"error": "Message cannot be empty."}), 400

        message = create_timeline_item_message(db, "photo", photo_id, body)
        return jsonify(message), 201

    return jsonify(load_messages_for_timeline_item(db, "photo", photo_id))


@app.route("/api/photo/<int:photo_id>", methods=("GET", "PATCH", "DELETE"))
@birthday_required
def delete_photo(photo_id):
    photo = get_owned_photo(photo_id)
    db = get_db()
    if request.method == "GET":
        tags = get_tags_for_item(db, "photo", photo_id)
        people = get_people_for_item(db, "photo", photo_id)
        prompts = guided_prompts_for_item("photo", photo, people)
        return jsonify(
            {
                "id": photo["id"],
                "title": photo["title"] or "",
                "display_title": photo_display_title(photo),
                "caption": photo["caption"] or "",
                "photo_date": photo["photo_date"],
                "original_filename": photo["original_filename"],
                "created_at": photo["created_at"],
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "guided_prompts": prompts,
            }
        )

    if request.method == "PATCH":
        payload = request.get_json(silent=True) or request.form
        title = normalize_photo_title(payload.get("title", ""))
        caption = normalize_photo_caption(payload.get("caption", ""))
        db.execute(
            """
            UPDATE photos
            SET title = ?, caption = ?
            WHERE id = ? AND user_id = ?
            """,
            (title, caption, photo_id, g.user["id"]),
        )
        db.commit()
        photo = get_owned_photo(photo_id)
        tags = get_tags_for_item(db, "photo", photo_id)
        people = get_people_for_item(db, "photo", photo_id)
        prompts = guided_prompts_for_item("photo", photo, people)
        return jsonify(
            {
                "id": photo["id"],
                "title": photo["title"] or "",
                "display_title": photo_display_title(photo),
                "caption": photo["caption"] or "",
                "photo_date": photo["photo_date"],
                "original_filename": photo["original_filename"],
                "created_at": photo["created_at"],
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **people_payload(people),
                **privacy_payload_for_tags(tags),
                "guided_prompts": prompts,
            }
        )

    db.execute(
        """
        DELETE FROM chapter_items
        WHERE item_kind = 'photo'
          AND item_id = ?
          AND chapter_id IN (SELECT id FROM chapters WHERE user_id = ?)
        """,
        (photo_id, g.user["id"]),
    )
    db.execute(
        "DELETE FROM item_reactions WHERE item_kind = 'photo' AND item_id = ?",
        (photo_id,),
    )
    db.execute(
        "DELETE FROM photos WHERE id = ? AND user_id = ?",
        (photo_id, g.user["id"]),
    )
    db.commit()
    return ("", 204)


@app.route("/api/photo/<int:photo_id>/tags", methods=("GET", "PATCH"))
@birthday_required
def photo_tags(photo_id):
    get_owned_photo(photo_id)
    db = get_db()

    if request.method == "PATCH":
        payload = request.get_json(silent=True) or request.form
        tags = parse_tags(payload.get("tags", ""))
        set_tags_for_item(db, "photo", photo_id, tags)
        db.commit()

    tags = get_tags_for_item(db, "photo", photo_id)
    return jsonify({"tags": tags, "tags_text": tags_to_text(tags), **privacy_payload_for_tags(tags)})


@app.route("/api/photo/<int:photo_id>/people", methods=("GET", "PATCH"))
@birthday_required
def photo_people(photo_id):
    get_owned_photo(photo_id)
    db = get_db()

    if request.method == "PATCH":
        payload = request.get_json(silent=True) or request.form
        people = parse_people(payload.get("people", ""))
        set_people_for_item(db, "photo", photo_id, people)
        db.commit()

    people = get_people_for_item(db, "photo", photo_id)
    photo = get_owned_photo(photo_id)
    return jsonify(
        {
            **people_payload(people),
            "guided_prompts": guided_prompts_for_item("photo", photo, people),
        }
    )


@app.route("/api/photo/<int:photo_id>/location", methods=("GET", "PATCH"))
@birthday_required
def photo_location(photo_id):
    photo = get_owned_photo(photo_id)
    db = get_db()

    if request.method == "PATCH":
        payload = request.get_json(silent=True) or request.form
        try:
            location = normalize_location_payload(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        db.execute(
            """
            UPDATE photos
            SET location_name = ?, latitude = ?, longitude = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                location["location_name"],
                location["latitude"],
                location["longitude"],
                photo_id,
                g.user["id"],
            ),
        )
        db.commit()
        photo = get_owned_photo(photo_id)

    people = get_people_for_item(db, "photo", photo_id)
    return jsonify(
        {
            **timeline_location_payload(photo),
            "guided_prompts": guided_prompts_for_item("photo", photo, people),
        }
    )


@app.route("/api/text-entry/<int:entry_id>", methods=("GET", "PATCH", "DELETE"))
@birthday_required
def text_entry(entry_id):
    entry = get_owned_text_entry(entry_id)
    db = get_db()

    if request.method == "DELETE":
        db.execute(
            """
            DELETE FROM chapter_items
            WHERE item_kind = 'text'
              AND item_id = ?
              AND chapter_id IN (SELECT id FROM chapters WHERE user_id = ?)
            """,
            (entry_id, g.user["id"]),
        )
        db.execute(
            "DELETE FROM item_reactions WHERE item_kind = 'text' AND item_id = ?",
            (entry_id,),
        )
        db.execute(
            "DELETE FROM text_entries WHERE id = ? AND user_id = ?",
            (entry_id, g.user["id"]),
        )
        db.commit()
        return ("", 204)

    if request.method == "PATCH":
        payload = request.get_json(silent=True) or request.form
        body = payload.get("body") or ""
        entry_date = payload.get("entry_date", "")
        tags = parse_tags(payload.get("tags", ""))
        people = parse_people(payload.get("people", ""))

        if not body.strip():
            return jsonify({"error": "Text entry cannot be empty."}), 400

        try:
            normalized_entry_date = normalize_month_date(
                entry_date,
                "Text entry date",
                entry["year"],
                entry["month"],
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            location = normalize_location_payload(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        db.execute(
            """
            UPDATE text_entries
            SET body = ?, entry_date = ?, location_name = ?, latitude = ?, longitude = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
            """,
            (
                body,
                normalized_entry_date,
                location["location_name"],
                location["latitude"],
                location["longitude"],
                entry_id,
                g.user["id"],
            ),
        )
        set_tags_for_item(db, "text", entry_id, tags)
        set_people_for_item(db, "text", entry_id, people)
        db.commit()
        entry = get_owned_text_entry(entry_id)

    tags = get_tags_for_item(db, "text", entry_id)
    people = get_people_for_item(db, "text", entry_id)
    return jsonify(
        {
            "id": entry["id"],
            "body": entry["body"],
            "entry_date": entry["entry_date"],
            **timeline_location_payload(entry),
            "created_at": entry["created_at"],
            "updated_at": entry["updated_at"],
            "tags": tags,
            "tags_text": tags_to_text(tags),
            **people_payload(people),
            **privacy_payload_for_tags(tags),
            "guided_prompts": guided_prompts_for_item("text", entry, people),
        }
    )


@app.route("/api/timeline-item/<item_kind>/<int:item_id>/messages", methods=("GET", "POST"))
@birthday_required
def timeline_item_messages(item_kind, item_id):
    get_owned_timeline_item(item_kind, item_id)
    db = get_db()

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        body = (payload.get("body") or "").strip()
        if not body:
            return jsonify({"error": "Message cannot be empty."}), 400

        message = create_timeline_item_message(db, item_kind, item_id, body)
        return jsonify(message), 201

    return jsonify(load_messages_for_timeline_item(db, item_kind, item_id))


@app.route("/api/timeline-item/<item_kind>/<int:item_id>/reaction", methods=("GET", "PUT", "DELETE"))
@login_required
def timeline_item_reaction(item_kind, item_id):
    get_timeline_item_for_reaction(item_kind, item_id)
    db = get_db()
    return timeline_item_reaction_response(db, item_kind, item_id)


def timeline_item_reaction_response(db, item_kind, item_id):
    if item_kind not in ("photo", "text"):
        abort(404)

    if request.method == "PUT":
        payload = request.get_json(silent=True) or request.form
        reaction = (payload.get("reaction") or "").strip().lower()
        if reaction not in REACTION_CHOICES:
            return jsonify({"error": "Choose like or love."}), 400

        db.execute(
            """
            INSERT INTO item_reactions (user_id, item_kind, item_id, reaction)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, item_kind, item_id)
            DO UPDATE SET reaction = excluded.reaction, created_at = CURRENT_TIMESTAMP
            """,
            (g.user["id"], item_kind, item_id, reaction),
        )
        db.commit()
    elif request.method == "DELETE":
        db.execute(
            """
            DELETE FROM item_reactions
            WHERE user_id = ? AND item_kind = ? AND item_id = ?
            """,
            (g.user["id"], item_kind, item_id),
        )
        db.commit()

    payloads = load_reaction_payloads(db, [(item_kind, item_id)])
    return jsonify(payloads[(item_kind, item_id)])


@app.route("/api/photo/<int:photo_id>/messages", methods=("GET", "POST"))
@birthday_required
def photo_messages(photo_id):
    get_owned_photo(photo_id)
    db = get_db()

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        body = (payload.get("body") or "").strip()
        if not body:
            return jsonify({"error": "Message cannot be empty."}), 400

        message = create_timeline_item_message(db, "photo", photo_id, body)
        return jsonify(message), 201

    return jsonify(load_messages_for_timeline_item(db, "photo", photo_id))


if __name__ == "__main__":
    init_db()
    app.run(debug=run_debug_enabled())
elif os.environ.get("EVERTIMELINE_SKIP_DB_INIT") != "1":
    init_db()
