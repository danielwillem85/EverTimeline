from datetime import date
from functools import wraps
import calendar
import os
from pathlib import Path
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


def init_db():
    with sqlite3.connect(DATABASE) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
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
            """
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


def user_years():
    birthday = parse_iso_date(g.user["birthday"], "Birthday")
    return range(birthday.year, date.today().year + 1)


def validate_year_month(year, month):
    years = user_years()
    if year not in years:
        abort(404)
    if month < 1 or month > 12:
        abort(404)


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
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("register.html")

        try:
            db = get_db()
            cursor = db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password)),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("That username is already taken.", "error")
            return render_template("register.html")

        session.clear()
        session["user_id"] = cursor.lastrowid
        return redirect(url_for("birthday"))

    return render_template("register.html")


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
    years = list(user_years())
    return render_template("timeline.html", years=years)


@app.route("/year/<int:year>")
@birthday_required
def year_view(year):
    validate_year_month(year, 1)
    months = [(index + 1, month) for index, month in enumerate(MONTH_NAMES)]
    return render_template("months.html", year=year, months=months)


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

        db.execute(
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
    items = []
    for photo in photo_rows:
        items.append(
            {
                "kind": "photo",
                "id": photo["id"],
                "original_filename": photo["original_filename"],
                "display_date": photo["photo_date"],
                "sort_value": photo["photo_date"] or photo["created_at"],
            }
        )
    for entry in text_rows:
        items.append(
            {
                "kind": "text",
                "id": entry["id"],
                "body": entry["body"],
                "display_date": entry["entry_date"],
                "sort_value": entry["entry_date"] or entry["created_at"],
            }
        )
    items.sort(key=lambda item: (item["sort_value"], item["id"]), reverse=True)
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
    db.execute(
        """
        INSERT INTO text_entries (user_id, year, month, body, entry_date)
        VALUES (?, ?, ?, ?, ?)
        """,
        (g.user["id"], year, month, body, normalized_entry_date),
    )
    db.commit()
    flash("Text entry saved.", "success")
    return redirect(url_for("month_view", year=year, month=month))


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
        db.commit()
        entry = get_owned_text_entry(entry_id)

    return jsonify(
        {
            "id": entry["id"],
            "body": entry["body"],
            "entry_date": entry["entry_date"],
            "created_at": entry["created_at"],
            "updated_at": entry["updated_at"],
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
