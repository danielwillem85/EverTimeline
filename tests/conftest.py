import io
import os
import sqlite3

import pytest
from PIL import Image


os.environ.setdefault("EVERTIMELINE_SKIP_DB_INIT", "1")

import app as app_module


@pytest.fixture
def app(tmp_path):
    previous_database = app_module.DATABASE
    app_module.DATABASE = tmp_path / "evertimeline-test.sqlite3"
    app_module.app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
    )
    app_module.init_db()

    yield app_module.app

    app_module.DATABASE = previous_database


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def helpers():
    return TestHelpers(app_module)


class TestHelpers:
    def __init__(self, app_module):
        self.app_module = app_module

    def row(self, sql, params=()):
        db = sqlite3.connect(self.app_module.DATABASE)
        try:
            db.row_factory = sqlite3.Row
            return db.execute(sql, params).fetchone()
        finally:
            db.close()

    def rows(self, sql, params=()):
        db = sqlite3.connect(self.app_module.DATABASE)
        try:
            db.row_factory = sqlite3.Row
            return db.execute(sql, params).fetchall()
        finally:
            db.close()

    def user_id(self, username):
        return self.row("SELECT id FROM users WHERE username = ?", (username,))["id"]

    def csrf_token(self, client, path="/login"):
        client.get(path)
        with client.session_transaction() as session:
            token = session.get(self.app_module.CSRF_SESSION_KEY)
        assert token
        return token

    def csrf_headers(self, client, path="/timeline"):
        return {"X-CSRF-Token": self.csrf_token(client, path)}

    def csrf_form_data(self, client, path):
        return {"csrf_token": self.csrf_token(client, path)}

    def register(
        self,
        client,
        username,
        *,
        first_name=None,
        last_name="Tester",
        email=None,
        password="password123",
    ):
        first_name = first_name or username.title()
        email = email or f"{username}@example.com"
        return client.post(
            "/register",
            data={
                **self.csrf_form_data(client, "/register"),
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "password": password,
            },
        )

    def create_user(self, client, username, *, birthday="2000-01-15", password="password123"):
        response = self.register(client, username, password=password)
        assert response.status_code == 302
        response = client.post(
            "/birthday",
            data={
                **self.csrf_form_data(client, "/birthday"),
                "birthday": birthday,
            },
        )
        assert response.status_code == 302
        return self.user_id(username)

    def login(self, client, username, password="password123"):
        return client.post(
            "/login",
            data={
                **self.csrf_form_data(client, "/login"),
                "username": username,
                "password": password,
            },
        )

    def create_text(
        self,
        client,
        body,
        *,
        year=2020,
        month=5,
        entry_date="2020-05-03",
        tag="private",
        location_name="",
        latitude="",
        longitude="",
    ):
        response = client.post(
            f"/year/{year}/{month}/text",
            data={
                **self.csrf_form_data(client, f"/year/{year}/{month}"),
                "body": body,
                "entry_date": entry_date,
                "tags": tag,
                "location_name": location_name,
                "latitude": latitude,
                "longitude": longitude,
            },
        )
        assert response.status_code == 302
        return self.row(
            "SELECT id FROM text_entries WHERE body = ? ORDER BY id DESC",
            (body,),
        )["id"]

    def upload_photo(
        self,
        client,
        *,
        year=2020,
        month=5,
        filename="photo.png",
        photo_date="2020-05-04",
        tag="private",
        location_name="",
        latitude="",
        longitude="",
    ):
        response = client.post(
            f"/year/{year}/{month}",
            data={
                **self.csrf_form_data(client, f"/year/{year}/{month}"),
                "photo": (io.BytesIO(self.png_bytes()), filename, "image/png"),
                "photo_date": photo_date,
                "tags": tag,
                "location_name": location_name,
                "latitude": latitude,
                "longitude": longitude,
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 302
        return self.row(
            "SELECT id FROM photos WHERE original_filename = ? ORDER BY id DESC",
            (filename,),
        )["id"]

    def request_connection(self, client, recipient_id, *, relation="friend"):
        response = client.post(
            "/connections/request",
            data={
                **self.csrf_form_data(client, "/search"),
                "recipient_id": recipient_id,
                "relation": relation,
                "q": "",
            },
        )
        assert response.status_code == 302
        return self.row(
            "SELECT id FROM connection_requests ORDER BY id DESC"
        )["id"]

    def accept_connection(self, client, request_id):
        response = client.post(
            f"/connections/{request_id}/accept",
            data=self.csrf_form_data(client, "/connections"),
        )
        assert response.status_code == 302

    @staticmethod
    def png_bytes():
        buffer = io.BytesIO()
        image = Image.new("RGB", (12, 12), color=(22, 109, 103))
        image.save(buffer, format="PNG")
        return buffer.getvalue()
