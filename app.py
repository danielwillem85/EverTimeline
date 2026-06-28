from datetime import date, datetime, timedelta, timezone
from contextlib import closing
from functools import wraps
import calendar
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sqlite3
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
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from PIL import Image


BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("EVERTIMELINE_DATABASE", BASE_DIR / "evertimeline.sqlite3"))
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
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


app = Flask(__name__)
app.config.update(
    SECRET_KEY=configured_secret_key(),
    MAX_CONTENT_LENGTH=128 * 1024 * 1024,
    CSRF_PROTECT=True,
    LOCAL_PASSWORD_RESET_LINKS=is_local_development(),
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
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


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

    return {
        "tag_choices": TAG_CHOICES,
        "default_tag": DEFAULT_TAG,
        "privacy_labels": PRIVACY_AUDIENCE_LABELS,
        "privacy_help": PRIVACY_AUDIENCE_HELP,
        "notification_count": notification_count,
        "static_version": static_version,
        "csrf_token": csrf_token,
        "csrf_field": csrf_field,
    }


def init_db():
    with closing(sqlite3.connect(DATABASE)) as db:
        db.execute("PRAGMA foreign_keys = ON")
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
            """
        )
        ensure_user_profile_columns(db)
        ensure_chapter_columns(db)
        ensure_timeline_location_columns(db)
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
        SELECT id, year, month, photo_date AS item_date, original_filename AS title
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
    match = re.match(r"^(\d{4}):(\d{2}):(\d{2})", normalized_value)
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


def photo_date_from_upload(image_data, year, month, manual_date=None):
    if manual_date:
        return manual_date, False, False

    detected_date = detect_photo_taken_date(image_data)
    if detected_date is None:
        return None, False, False
    if detected_date.year == year and detected_date.month == month:
        return detected_date.isoformat(), True, False
    return None, False, True


def uploaded_photo_files():
    return [
        image
        for image in request.files.getlist("photo")
        if image is not None and image.filename
    ]


def insert_uploaded_photo(db, image, image_data, year, month, photo_date, tags, location):
    cursor = db.execute(
        """
        INSERT INTO photos (
            user_id, year, month, original_filename, mime_type, image_data, photo_date,
            location_name, latitude, longitude
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["id"],
            year,
            month,
            secure_filename(image.filename),
            image.mimetype,
            image_data,
            photo_date,
            location["location_name"],
            location["latitude"],
            location["longitude"],
        ),
    )
    set_tags_for_item(db, "photo", cursor.lastrowid, tags)
    return cursor.lastrowid


def photo_upload_summary(uploaded_count, auto_dated_count, manual_date_used, skipped_count, ignored_exif_count):
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
    return " ".join(parts)


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


def tags_visible_to_connection(tags, allowed_tags):
    if allowed_tags is None:
        return True
    return bool(set(parse_tags(tags)) & set(allowed_tags))


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
                "title": row["original_filename"] or "Public photo",
                "owner_name": owner_name,
                "display_date": row["photo_date"],
                "message_count": row["message_count"],
                "connection_state": connection_state,
            }
        )
    return attach_reactions(db, photos)


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


def get_chapter_cover(db, chapter, image_url_builder):
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
            SELECT id, original_filename
            FROM photos
            WHERE id = ? AND user_id = ?
            """,
            (row["item_id"], g.user["id"]),
        ).fetchone()
        if photo is None:
            return None
        return {
            "kind": "photo",
            "image_url": image_url_builder(photo["id"]),
            "label": photo["original_filename"] or "Photo",
        }

    entry = db.execute(
        """
        SELECT id, body
        FROM text_entries
        WHERE id = ? AND user_id = ?
        """,
        (row["item_id"], g.user["id"]),
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


def build_chapter_items(db, chapter_id, image_url_builder, message_url_builder=None, can_message=False):
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
            SELECT id, year, month, original_filename, photo_date, created_at
            FROM photos
            WHERE user_id = ? AND id IN ({placeholders})
            """,
            (g.user["id"], *photo_ids),
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
            (g.user["id"], *text_ids),
        ).fetchall()
        text_map = {row["id"]: row for row in rows}

    photo_tags = load_tags_for_items(db, "photo", photo_ids, g.user["id"])
    text_tags = load_tags_for_items(db, "text", text_ids, g.user["id"])
    items = []
    for ref in refs:
        if ref["item_kind"] == "photo":
            photo = photo_map.get(ref["item_id"])
            if photo is None:
                continue
            tags = photo_tags.get(photo["id"], [])
            messages_url = message_url_builder("photo", photo["id"]) if message_url_builder else ""
            items.append(
                {
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
                    "url": timeline_item_link(g.user["id"], photo["year"], photo["month"], "photo", photo["id"]),
                    "image_url": image_url_builder(photo["id"]),
                    "messages": load_messages_for_timeline_item(db, "photo", photo["id"]) if message_url_builder else [],
                    "messages_url": messages_url,
                    "can_message": can_message and bool(messages_url),
                    "tags": tags,
                    "tags_text": tags_to_text(tags),
                    **privacy_payload_for_tags(tags),
                    "title": photo["original_filename"] or "Photo",
                }
            )
        else:
            entry = text_map.get(ref["item_id"])
            if entry is None:
                continue
            tags = text_tags.get(entry["id"], [])
            messages_url = message_url_builder("text", entry["id"]) if message_url_builder else ""
            items.append(
                {
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
                    "url": timeline_item_link(g.user["id"], entry["year"], entry["month"], "text", entry["id"]),
                    "body": entry["body"],
                    "messages": load_messages_for_timeline_item(db, "text", entry["id"]) if message_url_builder else [],
                    "messages_url": messages_url,
                    "can_message": can_message and bool(messages_url),
                    "tags": tags,
                    "tags_text": tags_to_text(tags),
                    **privacy_payload_for_tags(tags),
                    "title": "Text entry",
                }
            )
    return attach_reactions(db, items)


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
            p.original_filename AS item_title,
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
            p.original_filename AS item_title,
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


def add_activity_item(feed_items, *, created_at, actor_id, actor_name, owner_id, owner_name, action, item_kind, item_id, year, month, display_date=None, body=None):
    if item_kind == "photo":
        target_label = "photo"
    else:
        target_label = "text entry"
    feed_items.append(
        {
            "created_at": created_at,
            "actor_name": actor_label(actor_id, actor_name),
            "owner_name": owner_name,
            "summary": action.format(
                actor=actor_label(actor_id, actor_name),
                owner=owner_label(owner_id, owner_name),
                item=target_label,
            ),
            "item_kind": item_kind,
            "item_date": display_date or format_timeline_date_label(year, month, None),
            "preview": short_preview(body or ""),
            "url": timeline_item_link(owner_id, year, month, item_kind, item_id),
        }
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
            SELECT id, year, month, original_filename, photo_date, created_at
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
                body=row["original_filename"] or "Photo",
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
    return (
        connection_count
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


def search_timeline_content(db, query):
    normalized_query = (query or "").strip()
    if not normalized_query:
        return []

    pattern = f"%{normalized_query.lower()}%"
    results = []
    seen = set()

    def add_result(key, result):
        if key in seen:
            return
        seen.add(key)
        results.append(result)

    photo_rows = db.execute(
        """
        SELECT id, year, month, original_filename, photo_date, location_name, latitude, longitude, created_at
        FROM photos
        WHERE user_id = ?
          AND (
            lower(COALESCE(original_filename, '')) LIKE ?
            OR lower(COALESCE(photo_date, '')) LIKE ?
            OR CAST(year AS TEXT) LIKE ?
            OR printf('%04d-%02d', year, month) LIKE ?
          )
        ORDER BY COALESCE(photo_date, created_at) DESC, id DESC
        LIMIT 40
        """,
        (g.user["id"], pattern, pattern, pattern, pattern),
    ).fetchall()
    for row in photo_rows:
        date_label = format_timeline_date_label(row["year"], row["month"], row["photo_date"])
        add_result(
            ("photo", row["id"]),
            {
                "kind": "Photo",
                "title": row["original_filename"] or "Photo",
                "context": f"{MONTH_NAMES[row['month'] - 1]} {row['year']} - {date_label}",
                "preview": "Matched photo filename or date.",
                "url": timeline_item_link(g.user["id"], row["year"], row["month"], "photo", row["id"]),
            },
        )

    text_rows = db.execute(
        """
        SELECT id, year, month, body, entry_date, created_at
        FROM text_entries
        WHERE user_id = ?
          AND (
            lower(body) LIKE ?
            OR lower(COALESCE(entry_date, '')) LIKE ?
            OR CAST(year AS TEXT) LIKE ?
            OR printf('%04d-%02d', year, month) LIKE ?
          )
        ORDER BY COALESCE(entry_date, created_at) DESC, id DESC
        LIMIT 40
        """,
        (g.user["id"], pattern, pattern, pattern, pattern),
    ).fetchall()
    for row in text_rows:
        date_label = format_timeline_date_label(row["year"], row["month"], row["entry_date"])
        add_result(
            ("text", row["id"]),
            {
                "kind": "Text entry",
                "title": "Text entry",
                "context": f"{MONTH_NAMES[row['month'] - 1]} {row['year']} - {date_label}",
                "preview": short_preview(row["body"], 180),
                "url": timeline_item_link(g.user["id"], row["year"], row["month"], "text", row["id"]),
            },
        )

    photo_message_rows = db.execute(
        """
        SELECT
            m.id AS message_id,
            m.body,
            m.created_at,
            p.id AS item_id,
            p.year,
            p.month,
            p.photo_date AS item_date,
            p.original_filename AS item_title,
            u.username,
            u.first_name,
            u.last_name
        FROM messages m
        JOIN photos p ON p.id = m.photo_id
        JOIN users u ON u.id = m.user_id
        WHERE p.user_id = ? AND lower(m.body) LIKE ?
        ORDER BY m.created_at DESC, m.id DESC
        LIMIT 40
        """,
        (g.user["id"], pattern),
    ).fetchall()
    for row in photo_message_rows:
        add_result(
            ("photo-message", row["message_id"]),
            {
                "kind": "Message",
                "title": row["item_title"] or "Photo message",
                "context": f"Photo message by {message_author_name(row)}",
                "preview": short_preview(row["body"], 180),
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
            te.entry_date AS item_date,
            u.username,
            u.first_name,
            u.last_name
        FROM text_entry_messages tem
        JOIN text_entries te ON te.id = tem.entry_id
        JOIN users u ON u.id = tem.user_id
        WHERE te.user_id = ? AND lower(tem.body) LIKE ?
        ORDER BY tem.created_at DESC, tem.id DESC
        LIMIT 40
        """,
        (g.user["id"], pattern),
    ).fetchall()
    for row in text_message_rows:
        add_result(
            ("text-message", row["message_id"]),
            {
                "kind": "Message",
                "title": "Text entry message",
                "context": f"Text entry message by {message_author_name(row)}",
                "preview": short_preview(row["body"], 180),
                "url": timeline_item_link(g.user["id"], row["year"], row["month"], "text", row["item_id"]),
            },
        )

    chapter_rows = db.execute(
        """
        SELECT
            c.id,
            c.title,
            c.description,
            c.created_at,
            COUNT(ci.id) AS item_count
        FROM chapters c
        LEFT JOIN chapter_items ci ON ci.chapter_id = c.id
        WHERE c.user_id = ?
          AND (
            lower(c.title) LIKE ?
            OR lower(COALESCE(c.description, '')) LIKE ?
          )
        GROUP BY c.id
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT 40
        """,
        (g.user["id"], pattern, pattern),
    ).fetchall()
    for row in chapter_rows:
        item_word = "item" if row["item_count"] == 1 else "items"
        add_result(
            ("chapter", row["id"]),
            {
                "kind": "Chapter",
                "title": row["title"],
                "context": f"{row['item_count']} {item_word}",
                "preview": short_preview(row["description"] or "Chapter title matched.", 180),
                "url": url_for("chapter_detail", chapter_id=row["id"]),
            },
        )

    return results[:80]


def build_timeline_map_items(db):
    photo_rows = db.execute(
        """
        SELECT id, year, month, original_filename, photo_date, location_name, latitude, longitude, created_at
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
            "title": row["original_filename"] or "Photo",
            "preview": "Photo",
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
        SELECT id, original_filename, photo_date, location_name, latitude, longitude, created_at
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
    items = []
    for photo in photo_rows:
        tags = photo_tags.get(photo["id"], [])
        if not tags_visible_to_connection(tags, allowed_tags):
            continue
        items.append(
            {
                "kind": "photo",
                "id": photo["id"],
                "year": year,
                "month": month,
                "original_filename": photo["original_filename"],
                "display_date": photo["photo_date"],
                **timeline_location_payload(photo),
                "created_at": photo["created_at"],
                "image_url": image_url_builder(photo["id"]),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **privacy_payload_for_tags(tags),
            }
        )
    for entry in text_rows:
        tags = text_tags.get(entry["id"], [])
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
                **privacy_payload_for_tags(tags),
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
):
    query_suffix = ""
    query_params = [owner_id]
    if selected_year is not None:
        query_suffix = " AND year = ?"
        query_params.append(selected_year)

    photo_rows = db.execute(
        f"""
        SELECT id, year, month, original_filename, photo_date, location_name, latitude, longitude, created_at
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
    if photo_ids:
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
    if text_ids:
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
        messages_url = message_url_builder("photo", photo["id"]) if message_url_builder else ""
        items.append(
            {
                "kind": "photo",
                "id": photo["id"],
                "year": photo["year"],
                "month": photo["month"],
                "display_date": display_date,
                "date_label": format_timeline_date_label(photo["year"], photo["month"], display_date),
                **timeline_location_payload(photo),
                "created_at": photo["created_at"],
                "image_url": image_url_builder(photo["id"]),
                "messages": messages_by_photo.get(photo["id"], []),
                "messages_url": messages_url,
                "can_message": can_message and bool(messages_url),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **privacy_payload_for_tags(tags),
                "title": photo["original_filename"] or "Photo",
            }
        )

    for entry in visible_text_rows:
        display_date = entry["entry_date"]
        tags = text_tags.get(entry["id"], [])
        messages_url = message_url_builder("text", entry["id"]) if message_url_builder else ""
        items.append(
            {
                "kind": "text",
                "id": entry["id"],
                "year": entry["year"],
                "month": entry["month"],
                "display_date": display_date,
                "date_label": format_timeline_date_label(entry["year"], entry["month"], display_date),
                **timeline_location_payload(entry),
                "created_at": entry["created_at"],
                "body": entry["body"],
                "messages": messages_by_text.get(entry["id"], []),
                "messages_url": messages_url,
                "can_message": can_message and bool(messages_url),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **privacy_payload_for_tags(tags),
                "title": "Text entry",
            }
        )

    items.sort(key=timeline_item_sort_key)
    return items


def build_pdf_export_items(db, owner_id, year, month=None):
    photo_query = """
        SELECT id, year, month, original_filename, mime_type, image_data, photo_date, created_at
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

    items = []
    for photo in photo_rows:
        tags = photo_tags.get(photo["id"], [])
        items.append(
            {
                "kind": "photo",
                "id": photo["id"],
                "year": photo["year"],
                "month": photo["month"],
                "title": photo["original_filename"] or "Photo",
                "display_date": photo["photo_date"],
                "date_label": format_timeline_date_label(photo["year"], photo["month"], photo["photo_date"]),
                "created_at": photo["created_at"],
                "mime_type": photo["mime_type"],
                "image_data": photo["image_data"],
                "messages": load_messages_for_timeline_item(db, "photo", photo["id"]),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                **privacy_payload_for_tags(tags),
            }
        )

    for entry in text_rows:
        tags = text_tags.get(entry["id"], [])
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
                **privacy_payload_for_tags(tags),
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
        meta = f"{item['date_label']} | Visible: {item['privacy_label']}"
        story.append(Paragraph(pdf_paragraph(item_title), item_title_style))
        story.append(Paragraph(pdf_paragraph(meta), meta_style))

        if item["kind"] == "photo":
            try:
                story.append(pdf_image_flowable(item["image_data"], max_image_width, max_image_height))
                story.append(Spacer(1, 8))
            except Exception:
                story.append(Paragraph("Photo could not be rendered in this PDF.", body_style))
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
        SELECT id, year, month, original_filename, mime_type, photo_date, created_at
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
                    ("id", "year", "month", "original_filename", "mime_type", "photo_date", "created_at"),
                ),
                "tags": photo_tags.get(row["id"], [DEFAULT_TAG]),
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

    image_data = archive.read(info)
    mime_type = backup_text(photo.get("mime_type"), "image/png", 64)
    if mime_type not in ALLOWED_IMAGE_MIMES:
        raise ValueError("Backup contains an unsupported photo type.")

    cursor = db.execute(
        """
        INSERT INTO photos (
            user_id, year, month, original_filename, mime_type, image_data, photo_date, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["id"],
            year,
            month,
            secure_filename(backup_text(photo.get("original_filename"), "", 255)) or None,
            mime_type,
            image_data,
            photo.get("photo_date") or None,
            backup_created_at(photo.get("created_at")),
        ),
    )
    set_tags_for_item(db, "photo", cursor.lastrowid, photo.get("tags", [DEFAULT_TAG]))
    return cursor.lastrowid


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
            imported_messages = 0
            imported_reactions = 0

            for photo in backup_list(manifest, "photos"):
                new_id = import_photo_from_backup(db, archive, photo)
                photo_id_map[photo.get("id")] = new_id
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
        "photos": len(photo_id_map),
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
    flash(
        (
            "Imported backup: "
            f"{counts['photos']} photos, "
            f"{counts['text_entries']} text entries, "
            f"{counts['messages']} messages, "
            f"{counts['chapters']} chapters."
        ),
        "success",
    )
    return redirect(url_for("profile"))


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
    years = list(user_years())
    year_counts = get_year_counts(db)
    return render_template("timeline.html", years=years, year_counts=year_counts)


@app.route("/timeline/search")
@birthday_required
def timeline_search():
    db = get_db()
    query = request.args.get("q", "").strip()
    return render_template(
        "timeline_search.html",
        query=query,
        results=search_timeline_content(db, query),
        has_query=bool(query),
    )


@app.route("/timeline/map")
@birthday_required
def timeline_map():
    items = build_timeline_map_items(get_db())
    return render_template(
        "timeline_map.html",
        items=items,
        mapped_items=[item for item in items if item["has_coordinates"]],
        named_items=[item for item in items if not item["has_coordinates"]],
    )


@app.route("/year/<int:year>")
@birthday_required
def year_view(year):
    validate_year_month(year, 1)
    db = get_db()
    month_counts = get_month_counts(db, year)
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
        tags = parse_tags(request.form.get("tags", ""))

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

        for image in images:
            if image.mimetype not in ALLOWED_IMAGE_MIMES:
                skipped_count += 1
                continue

            image_data = image.read()
            if not image_data:
                skipped_count += 1
                continue

            normalized_photo_date, used_auto_date, ignored_exif_date = photo_date_from_upload(
                image_data,
                year,
                month,
                manual_photo_date,
            )
            insert_uploaded_photo(
                db,
                image,
                image_data,
                year,
                month,
                normalized_photo_date,
                tags,
                location,
            )
            uploaded_count += 1
            if used_auto_date:
                auto_dated_count += 1
            if ignored_exif_date:
                ignored_exif_count += 1

        if uploaded_count == 0:
            db.rollback()
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
            ),
            "success",
        )
        return redirect(url_for("month_view", year=year, month=month))

    items = build_month_items(
        db,
        g.user["id"],
        year,
        month,
        lambda photo_id: url_for("photo_image", photo_id=photo_id),
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
    db.commit()
    flash("Text entry saved.", "success")
    return redirect(url_for("month_view", year=year, month=month))


@app.route("/api/timeline-items")
@birthday_required
def timeline_items():
    db = get_db()
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
            message_url_builder=lambda item_kind, item_id: url_for(
                "timeline_item_messages",
                item_kind=item_kind,
                item_id=item_id,
            ),
            can_message=True,
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

    return render_template("chapters.html", chapters=get_chapters_with_counts(db))


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
        )
    )


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
    return redirect(url_for("notifications"))


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


@app.route("/api/photo/<int:photo_id>", methods=("DELETE",))
@birthday_required
def delete_photo(photo_id):
    get_owned_photo(photo_id)
    db = get_db()
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

    return jsonify(timeline_location_payload(photo))


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
        db.commit()
        entry = get_owned_text_entry(entry_id)

    tags = get_tags_for_item(db, "text", entry_id)
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
            **privacy_payload_for_tags(tags),
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
