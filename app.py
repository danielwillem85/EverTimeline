from datetime import date, datetime, timedelta, timezone
from functools import wraps
import calendar
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3

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
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "evertimeline.sqlite3"
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
TAG_CHOICES = ("private", "family", "friends", "public")
DEFAULT_TAG = "private"
CONNECTION_RELATIONS = ("friend", "family")
PASSWORD_RESET_TTL = timedelta(hours=1)
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


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
)


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
    return {
        "tag_choices": TAG_CHOICES,
        "default_tag": DEFAULT_TAG,
        "notification_count": notification_count,
    }


def init_db():
    with sqlite3.connect(DATABASE) as db:
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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
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
            """
        )
        ensure_user_profile_columns(db)
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


def user_years():
    birthday = parse_iso_date(g.user["birthday"], "Birthday")
    return range(birthday.year, date.today().year + 1)


def validate_year_month(year, month):
    years = user_years()
    if year not in years:
        abort(404)
    if month < 1 or month > 12:
        abort(404)


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


def load_tags_for_items(db, kind, item_ids):
    if not item_ids:
        return {}

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
        (g.user["id"], *item_ids),
    ).fetchall()
    tags_by_item = {item_id: [] for item_id in item_ids}
    for row in rows:
        tag = normalize_tag_choice(row["name"])
        if tag and tag not in tags_by_item[row["item_id"]]:
            tags_by_item.setdefault(row["item_id"], []).append(tag)
    return {item_id: parse_tags(tags) for item_id, tags in tags_by_item.items()}


def get_tags_for_item(db, kind, item_id):
    return load_tags_for_items(db, kind, [item_id]).get(item_id, [])


def get_all_tags(db):
    return list(TAG_CHOICES)


def get_year_counts(db):
    counts = {}
    for table in ("photos", "text_entries"):
        rows = db.execute(
            f"""
            SELECT year, COUNT(*) AS item_count
            FROM {table}
            WHERE user_id = ?
            GROUP BY year
            """,
            (g.user["id"],),
        ).fetchall()
        for row in rows:
            counts[row["year"]] = counts.get(row["year"], 0) + row["item_count"]
    return counts


def get_month_counts(db, year):
    counts = {}
    for table in ("photos", "text_entries"):
        rows = db.execute(
            f"""
            SELECT month, COUNT(*) AS item_count
            FROM {table}
            WHERE user_id = ? AND year = ?
            GROUP BY month
            """,
            (g.user["id"], year),
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


def get_notification_count(db):
    row = db.execute(
        """
        SELECT COUNT(*) AS notification_count
        FROM connection_requests
        WHERE recipient_id = ? AND status = 'pending'
        """,
        (g.user["id"],),
    ).fetchone()
    return row["notification_count"] if row is not None else 0


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


def current_profile_form_values():
    return {
        "first_name": g.user["first_name"] or "",
        "last_name": g.user["last_name"] or "",
        "email": g.user["email"] or "",
    }


@app.route("/")
def index():
    if g.user is None:
        return redirect(url_for("login"))
    if not g.user["birthday"]:
        return redirect(url_for("birthday"))
    return redirect(url_for("timeline"))


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
        if user is not None:
            token = create_password_reset_token(db, user["id"])
            db.commit()
            reset_url = url_for("reset_password", token=token, _external=True)

        flash("If an account matches that information, a reset link has been created.", "success")

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
        db.execute(
            "UPDATE users SET birthday = ? WHERE id = ?",
            (birthday_date.isoformat(), g.user["id"]),
        )
        db.commit()
        return redirect(url_for("timeline"))

    return render_template("birthday.html")


@app.route("/timeline")
@birthday_required
def timeline():
    db = get_db()
    years = list(user_years())
    year_counts = get_year_counts(db)
    return render_template("timeline.html", years=years, year_counts=year_counts)


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


@app.route("/year/<int:year>/<int:month>", methods=("GET", "POST"))
@birthday_required
def month_view(year, month):
    validate_year_month(year, month)
    db = get_db()
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])

    if request.method == "POST":
        image = request.files.get("photo")
        photo_date = request.form.get("photo_date", "")
        tags = parse_tags(request.form.get("tags", ""))

        if image is None or image.filename == "":
            flash("Choose an image to upload.", "error")
            return redirect(url_for("month_view", year=year, month=month))

        if image.mimetype not in ALLOWED_IMAGE_MIMES:
            flash("Upload a JPG, PNG, GIF, or WebP image.", "error")
            return redirect(url_for("month_view", year=year, month=month))

        try:
            normalized_photo_date = normalize_month_date(
                photo_date,
                "Photo date",
                year,
                month,
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("month_view", year=year, month=month))

        image_data = image.read()
        if not image_data:
            flash("The selected image is empty.", "error")
            return redirect(url_for("month_view", year=year, month=month))

        cursor = db.execute(
            """
            INSERT INTO photos (
                user_id, year, month, original_filename, mime_type, image_data, photo_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                g.user["id"],
                year,
                month,
                secure_filename(image.filename),
                image.mimetype,
                image_data,
                normalized_photo_date,
            ),
        )
        set_tags_for_item(db, "photo", cursor.lastrowid, tags)
        db.commit()
        flash("Photo uploaded.", "success")
        return redirect(url_for("month_view", year=year, month=month))

    photo_rows = db.execute(
        """
        SELECT id, original_filename, photo_date, created_at
        FROM photos
        WHERE user_id = ? AND year = ? AND month = ?
        ORDER BY COALESCE(photo_date, created_at) DESC, id DESC
        """,
        (g.user["id"], year, month),
    ).fetchall()
    text_rows = db.execute(
        """
        SELECT id, body, entry_date, created_at, updated_at
        FROM text_entries
        WHERE user_id = ? AND year = ? AND month = ?
        ORDER BY COALESCE(entry_date, created_at) DESC, id DESC
        """,
        (g.user["id"], year, month),
    ).fetchall()
    photo_tags = load_tags_for_items(db, "photo", [photo["id"] for photo in photo_rows])
    text_tags = load_tags_for_items(db, "text", [entry["id"] for entry in text_rows])
    items = []
    for photo in photo_rows:
        tags = photo_tags.get(photo["id"], [])
        items.append(
            {
                "kind": "photo",
                "id": photo["id"],
                "year": year,
                "month": month,
                "original_filename": photo["original_filename"],
                "display_date": photo["photo_date"],
                "created_at": photo["created_at"],
                "tags": tags,
                "tags_text": tags_to_text(tags),
            }
        )
    for entry in text_rows:
        tags = text_tags.get(entry["id"], [])
        items.append(
            {
                "kind": "text",
                "id": entry["id"],
                "year": year,
                "month": month,
                "body": entry["body"],
                "display_date": entry["entry_date"],
                "created_at": entry["created_at"],
                "tags": tags,
                "tags_text": tags_to_text(tags),
            }
        )
    items.sort(key=timeline_item_sort_key)
    return render_template(
        "month.html",
        year=year,
        month=month,
        month_name=MONTH_NAMES[month - 1],
        month_start=month_start.isoformat(),
        month_end=month_end.isoformat(),
        items=items,
    )


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

    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO text_entries (user_id, year, month, body, entry_date)
        VALUES (?, ?, ?, ?, ?)
        """,
        (g.user["id"], year, month, body, normalized_entry_date),
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
    query_suffix = ""
    query_params = [g.user["id"]]
    if selected_year is not None:
        if selected_year not in user_years():
            abort(404)
        query_suffix = " AND year = ?"
        query_params.append(selected_year)

    photo_rows = db.execute(
        f"""
        SELECT id, year, month, original_filename, photo_date, created_at
        FROM photos
        WHERE user_id = ?{query_suffix}
        """,
        query_params,
    ).fetchall()
    text_rows = db.execute(
        f"""
        SELECT id, year, month, body, entry_date, created_at, updated_at
        FROM text_entries
        WHERE user_id = ?{query_suffix}
        """,
        query_params,
    ).fetchall()
    photo_tags = load_tags_for_items(db, "photo", [photo["id"] for photo in photo_rows])
    text_tags = load_tags_for_items(db, "text", [entry["id"] for entry in text_rows])
    messages_by_photo = {}
    photo_ids = [photo["id"] for photo in photo_rows]
    if photo_ids:
        placeholders = ",".join(["?"] * len(photo_ids))
        message_rows = db.execute(
            f"""
            SELECT photo_id, body, created_at
            FROM messages
            WHERE user_id = ? AND photo_id IN ({placeholders})
            ORDER BY created_at ASC, id ASC
            """,
            (g.user["id"], *photo_ids),
        ).fetchall()
        for message in message_rows:
            messages_by_photo.setdefault(message["photo_id"], []).append(
                {
                    "body": message["body"],
                    "created_at": message["created_at"],
                }
            )

    items = []
    for photo in photo_rows:
        display_date = photo["photo_date"]
        tags = photo_tags.get(photo["id"], [])
        items.append(
            {
                "kind": "photo",
                "id": photo["id"],
                "year": photo["year"],
                "month": photo["month"],
                "display_date": display_date,
                "date_label": format_timeline_date_label(
                    photo["year"],
                    photo["month"],
                    display_date,
                ),
                "created_at": photo["created_at"],
                "image_url": url_for("photo_image", photo_id=photo["id"]),
                "messages": messages_by_photo.get(photo["id"], []),
                "tags": tags,
                "tags_text": tags_to_text(tags),
                "title": photo["original_filename"] or "Photo",
            }
        )

    for entry in text_rows:
        display_date = entry["entry_date"]
        tags = text_tags.get(entry["id"], [])
        items.append(
            {
                "kind": "text",
                "id": entry["id"],
                "year": entry["year"],
                "month": entry["month"],
                "display_date": display_date,
                "date_label": format_timeline_date_label(
                    entry["year"],
                    entry["month"],
                    display_date,
                ),
                "created_at": entry["created_at"],
                "body": entry["body"],
                "tags": tags,
                "tags_text": tags_to_text(tags),
                "title": "Text entry",
            }
        )

    items.sort(key=timeline_item_sort_key)
    return jsonify(items)


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
    connections_rows = db.execute(
        """
        SELECT
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
    return render_template(
        "connections.html",
        incoming_requests=get_incoming_connection_requests(db),
        connections=[
            {
                "username": row["username"],
                "full_name": user_full_name(row),
                "email": row["email"] or "",
            }
            for row in connections_rows
        ],
    )


@app.route("/notifications")
@login_required
def notifications():
    db = get_db()
    return render_template(
        "notifications.html",
        incoming_requests=get_incoming_connection_requests(db),
    )


@app.route("/api/notifications/count")
@login_required
def notification_count():
    return jsonify({"count": get_notification_count(get_db())})


@app.route("/connections/request", methods=("POST",))
@login_required
def create_connection_request():
    db = get_db()
    query = request.form.get("q", "").strip()
    recipient_id = request.form.get("recipient_id", type=int)
    relation = request.form.get("relation", "")

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
        return redirect(url_for("search", q=query))

    if relation not in CONNECTION_RELATIONS:
        flash("Choose whether this connection is a friend or family.", "error")
        return redirect(url_for("search", q=query))

    state = connection_state_for_user(db, recipient["id"])
    if state["status"] == "connected":
        flash("You are already connected.", "error")
        return redirect(url_for("search", q=query))
    if state["status"] == "pending_sent":
        flash("A connection request has already been sent.", "error")
        return redirect(url_for("search", q=query))
    if state["status"] == "pending_received":
        flash("This user already sent you a request. Accept it on the Connections page.", "error")
        return redirect(url_for("search", q=query))

    db.execute(
        """
        INSERT INTO connection_requests (requester_id, recipient_id, relation)
        VALUES (?, ?, ?)
        """,
        (g.user["id"], recipient["id"], relation),
    )
    db.commit()
    flash("Connection request sent.", "success")
    return redirect(url_for("search", q=query))


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


@app.route("/photo/<int:photo_id>/image")
@birthday_required
def photo_image(photo_id):
    photo = get_owned_photo(photo_id)
    return Response(photo["image_data"], mimetype=photo["mime_type"])


@app.route("/api/photo/<int:photo_id>", methods=("DELETE",))
@birthday_required
def delete_photo(photo_id):
    get_owned_photo(photo_id)
    db = get_db()
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
    return jsonify({"tags": tags, "tags_text": tags_to_text(tags)})


@app.route("/api/text-entry/<int:entry_id>", methods=("GET", "PATCH", "DELETE"))
@birthday_required
def text_entry(entry_id):
    entry = get_owned_text_entry(entry_id)
    db = get_db()

    if request.method == "DELETE":
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

        db.execute(
            """
            UPDATE text_entries
            SET body = ?, entry_date = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
            """,
            (body, normalized_entry_date, entry_id, g.user["id"]),
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
            "created_at": entry["created_at"],
            "updated_at": entry["updated_at"],
            "tags": tags,
            "tags_text": tags_to_text(tags),
        }
    )


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

        cursor = db.execute(
            """
            INSERT INTO messages (photo_id, user_id, body)
            VALUES (?, ?, ?)
            """,
            (photo_id, g.user["id"], body),
        )
        db.commit()
        message = db.execute(
            """
            SELECT id, body, created_at
            FROM messages
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        return jsonify(dict(message)), 201

    messages = db.execute(
        """
        SELECT id, body, created_at
        FROM messages
        WHERE photo_id = ? AND user_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (photo_id, g.user["id"]),
    ).fetchall()
    return jsonify([dict(message) for message in messages])


init_db()


if __name__ == "__main__":
    app.run(debug=True)
