import io
import json
import re
import zipfile
import hashlib
from datetime import date

from PIL import Image


def test_auth_registration_birthday_login_and_logout(client, helpers):
    response = client.get("/timeline")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")

    user_id = helpers.create_user(client, "alice")
    assert user_id

    timeline = client.get("/timeline")
    assert timeline.status_code == 200
    assert b"Timeline" in timeline.data

    assert client.post(
        "/logout",
        data=helpers.csrf_form_data(client, "/timeline"),
    ).status_code == 302

    bad_login = client.post(
        "/login",
        data={
            **helpers.csrf_form_data(client, "/login"),
            "username": "alice",
            "password": "wrong",
        },
    )
    assert bad_login.status_code == 200
    assert b"Invalid username or password." in bad_login.data

    good_login = helpers.login(client, "alice")
    assert good_login.status_code == 302


def test_csrf_token_required_for_unsafe_requests(client, helpers):
    helpers.create_user(client, "alice")

    response = client.post(
        "/year/2020/5/text",
        data={
            "body": "Missing a token",
            "entry_date": "2020-05-03",
            "tags": "private",
        },
    )

    assert response.status_code == 400


def test_oversized_upload_redirects_with_flash(app, client, helpers):
    helpers.create_user(client, "owner")
    previous_limit = app.config["MAX_CONTENT_LENGTH"]
    app.config["MAX_CONTENT_LENGTH"] = 1024
    try:
        response = client.post(
            "/year/2020/5",
            data={
                **helpers.csrf_form_data(client, "/year/2020/5"),
                "photo": (io.BytesIO(b"x" * 2048), "too-large.png", "image/png"),
                "photo_date": "2020-05-04",
                "tags": "private",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    finally:
        app.config["MAX_CONTENT_LENGTH"] = previous_limit

    assert response.status_code == 200
    assert b"That upload is too large." in response.data
    assert b"The current limit is 1 KB per request." in response.data


def test_password_reset_link_is_local_dev_only(app, client, helpers):
    helpers.create_user(client, "alice")
    app.config["LOCAL_PASSWORD_RESET_LINKS"] = False

    response = client.post(
        "/forgot-password",
        data={
            **helpers.csrf_form_data(client, "/forgot-password"),
            "identifier": "alice",
        },
    )

    assert response.status_code == 200
    assert b"/reset-password/" not in response.data
    assert helpers.row("SELECT COUNT(*) AS token_count FROM password_reset_tokens")["token_count"] == 0

    app.config["LOCAL_PASSWORD_RESET_LINKS"] = True
    response = client.post(
        "/forgot-password",
        data={
            **helpers.csrf_form_data(client, "/forgot-password"),
            "identifier": "alice",
        },
    )

    assert response.status_code == 200
    assert b"/reset-password/" in response.data
    assert helpers.row("SELECT COUNT(*) AS token_count FROM password_reset_tokens")["token_count"] == 1


def test_password_reset_token_changes_password_and_cannot_be_reused(app, client, helpers):
    helpers.create_user(client, "alice", password="old-password")
    app.config["LOCAL_PASSWORD_RESET_LINKS"] = True

    response = client.post(
        "/forgot-password",
        data={
            **helpers.csrf_form_data(client, "/forgot-password"),
            "identifier": "alice@example.com",
        },
    )
    assert response.status_code == 200
    match = re.search(rb"/reset-password/([^\"<\s]+)", response.data)
    assert match
    token = match.group(1).decode()

    reset_page = client.get(f"/reset-password/{token}")
    assert reset_page.status_code == 200
    assert b"Reset password" in reset_page.data

    mismatched_password = client.post(
        f"/reset-password/{token}",
        data={
            **helpers.csrf_form_data(client, f"/reset-password/{token}"),
            "password": "new-password",
            "confirm_password": "different-password",
        },
    )
    assert mismatched_password.status_code == 200
    assert b"The passwords do not match." in mismatched_password.data

    reset_response = client.post(
        f"/reset-password/{token}",
        data={
            **helpers.csrf_form_data(client, f"/reset-password/{token}"),
            "password": "new-password",
            "confirm_password": "new-password",
        },
    )
    assert reset_response.status_code == 302
    assert reset_response.headers["Location"].endswith("/login")

    assert helpers.login(client, "alice", password="old-password").status_code == 200
    assert helpers.login(client, "alice", password="new-password").status_code == 302
    reused = client.get(f"/reset-password/{token}", follow_redirects=True)
    assert reused.status_code == 200
    assert b"invalid or expired" in reused.data


def test_uploads_text_entries_and_pdf_exports(client, helpers):
    helpers.create_user(client, "owner")

    month_page = client.get("/year/2020/5")
    assert month_page.status_code == 200
    assert b"data-upload-progress" in month_page.data
    assert b"data-upload-progress-bar" in month_page.data

    photo_id = helpers.upload_photo(
        client,
        filename="public-photo.png",
        title="Park picnic",
        caption="Blanket by the old willow",
        tag="public",
    )
    duplicate_response = client.post(
        "/year/2020/5",
        data={
            **helpers.csrf_form_data(client, "/year/2020/5"),
            "photo": (io.BytesIO(helpers.png_bytes()), "public-photo-copy.png", "image/png"),
            "photo_date": "2020-05-04",
            "title": "Duplicate picnic",
            "caption": "This should not create another row",
            "tags": "public",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert duplicate_response.status_code == 200
    assert b"Skipped 1 duplicate photo already in your timeline." in duplicate_response.data
    assert helpers.row("SELECT COUNT(*) AS count FROM photos")["count"] == 1
    assert helpers.row("SELECT image_hash FROM photos WHERE id = ?", (photo_id,))["image_hash"]
    text_id = helpers.create_text(client, "A private journal note", tag="private")

    image_response = client.get(f"/photo/{photo_id}/image")
    assert image_response.status_code == 200
    assert image_response.mimetype == "image/jpeg"
    assert image_response.data.startswith(b"\xff\xd8")
    stored_photo = helpers.row("SELECT mime_type, image_data FROM photos WHERE id = ?", (photo_id,))
    assert stored_photo["mime_type"] == "image/jpeg"
    assert stored_photo["image_data"].startswith(b"\xff\xd8")
    oversized_png = io.BytesIO()
    Image.new("RGB", (2200, 1200), color=(31, 126, 116)).save(oversized_png, format="PNG")
    oversized_response = client.post(
        "/year/2020/5",
        data={
            **helpers.csrf_form_data(client, "/year/2020/5"),
            "photo": (io.BytesIO(oversized_png.getvalue()), "oversized.png", "image/png"),
            "photo_date": "2020-05-06",
            "tags": "private",
        },
        content_type="multipart/form-data",
    )
    assert oversized_response.status_code == 302
    oversized_photo = helpers.row("SELECT image_data FROM photos WHERE original_filename = ?", ("oversized.png",))
    with Image.open(io.BytesIO(oversized_photo["image_data"])) as stored_image:
        assert stored_image.format == "JPEG"
        assert max(stored_image.size) == 1200

    text_response = client.get(f"/api/text-entry/{text_id}")
    assert text_response.status_code == 200
    assert text_response.get_json()["body"] == "A private journal note"

    photo_response = client.get(f"/api/photo/{photo_id}")
    assert photo_response.status_code == 200
    assert photo_response.get_json()["display_title"] == "Park picnic"

    photo_update = client.patch(
        f"/api/photo/{photo_id}",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={
            "title": "Updated picnic",
            "caption": "A caption that timeline search can find",
        },
    )
    assert photo_update.status_code == 200
    assert photo_update.get_json()["caption"] == "A caption that timeline search can find"

    search_response = client.get("/timeline/search?q=timeline%20search")
    assert search_response.status_code == 200
    assert b"Updated picnic" in search_response.data

    timeline_response = client.get("/api/timeline-items?year=2020")
    assert timeline_response.status_code == 200
    assert {item["kind"] for item in timeline_response.get_json()} == {"photo", "text"}

    year_pdf = client.get("/year/2020/export.pdf")
    assert year_pdf.status_code == 200
    assert year_pdf.mimetype == "application/pdf"
    assert year_pdf.data.startswith(b"%PDF")

    month_pdf = client.get("/year/2020/5/export.pdf")
    assert month_pdf.status_code == 200
    assert month_pdf.mimetype == "application/pdf"
    assert month_pdf.data.startswith(b"%PDF")


def test_photo_date_detection_uses_exif_and_filename_fallbacks(client, helpers):
    helpers.create_user(client, "owner")

    detected_filenames = {
        "IMG_20210709_153012.jpg": "2021-07-09",
        "vacation 9 July 2021.png": "2021-07-09",
        "scan-31.07.2021.webp": "2021-07-31",
        "memory_07-30-2021.jpeg": "2021-07-30",
    }
    for filename, expected in detected_filenames.items():
        assert helpers.app_module.filename_date_candidate(filename).isoformat() == expected

    exif_buffer = io.BytesIO()
    exif_image = Image.new("RGB", (12, 12), color=(88, 104, 132))
    exif = exif_image.getexif()
    exif[36867] = "2021-07-08 13:45:12"
    exif_image.save(exif_buffer, format="JPEG", exif=exif)

    exif_response = client.post(
        "/year/2021/7",
        data={
            **helpers.csrf_form_data(client, "/year/2021/7"),
            "photo": (io.BytesIO(exif_buffer.getvalue()), "camera-no-date-name.jpg", "image/jpeg"),
            "tags": "private",
        },
        content_type="multipart/form-data",
    )
    assert exif_response.status_code == 302
    exif_photo = helpers.row(
        "SELECT photo_date FROM photos WHERE original_filename = ?",
        ("camera-no-date-name.jpg",),
    )
    assert exif_photo["photo_date"] == "2021-07-08"

    fallback_png = io.BytesIO()
    Image.new("RGB", (12, 12), color=(144, 61, 55)).save(fallback_png, format="PNG")
    fallback_response = client.post(
        "/year/2021/7",
        data={
            **helpers.csrf_form_data(client, "/year/2021/7"),
            "photo": (io.BytesIO(fallback_png.getvalue()), "PXL_20210710_153012.png", "image/png"),
            "tags": "private",
        },
        content_type="multipart/form-data",
    )
    assert fallback_response.status_code == 302
    fallback_photo = helpers.row(
        "SELECT photo_date FROM photos WHERE original_filename = ?",
        ("PXL_20210710_153012.png",),
    )
    assert fallback_photo["photo_date"] == "2021-07-10"


def test_bulk_upload_can_leave_photos_without_dates(client, helpers):
    helpers.create_user(client, "owner")

    files = []
    for filename, color in (
        ("loose-memory-a.png", (31, 77, 126)),
        ("loose-memory-b.png", (126, 77, 31)),
    ):
        image_buffer = io.BytesIO()
        Image.new("RGB", (12, 12), color=color).save(image_buffer, format="PNG")
        image_buffer.seek(0)
        files.append((image_buffer, filename, "image/png"))

    response = client.post(
        "/year/2022/3",
        data={
            **helpers.csrf_form_data(client, "/year/2022/3"),
            "photo": files,
            "tags": "private",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302

    rows = helpers.rows(
        "SELECT original_filename, photo_date FROM photos ORDER BY original_filename"
    )
    assert [row["original_filename"] for row in rows] == [
        "loose-memory-a.png",
        "loose-memory-b.png",
    ]
    assert [row["photo_date"] for row in rows] == [None, None]

    year_page = client.get("/year/2022")
    assert year_page.status_code == 200
    assert b"No date" in year_page.data
    assert b"/year/2022/no-date" in year_page.data

    no_date_page = client.get("/year/2022/no-date")
    assert no_date_page.status_code == 200
    assert b"data-splash-selectable" in no_date_page.data
    assert b"/year/2022/no-date/photos" in no_date_page.data
    assert b"/year/2022/no-date/accept-suggestions" in no_date_page.data

    no_date_photos = client.get("/year/2022/no-date/photos?seed=test-seed&page=0&page_size=10")
    assert no_date_photos.status_code == 200
    payload = no_date_photos.get_json()
    assert payload["total"] == 2
    assert {photo["title"] for photo in payload["photos"]} == {
        "loose-memory-a.png",
        "loose-memory-b.png",
    }
    assert {photo["suggestion"]["label"] for photo in payload["photos"]} == {"March 2022"}

    month_page = client.get("/year/2022/3")
    assert month_page.status_code == 200
    assert b"loose-memory-a.png" not in month_page.data
    assert b"loose-memory-b.png" not in month_page.data

    assign_response = client.post(
        "/year/2022/no-date/assign",
        json={
            "photo_ids": [photo["id"] for photo in payload["photos"]],
            "month": 6,
            "year": 2023,
        },
        headers=helpers.csrf_headers(client, "/year/2022/no-date"),
    )
    assert assign_response.status_code == 200
    assert assign_response.get_json()["moved_count"] == 2

    moved_rows = helpers.rows(
        "SELECT year, month, photo_date FROM photos ORDER BY original_filename"
    )
    assert [(row["year"], row["month"], row["photo_date"]) for row in moved_rows] == [
        (2023, 6, "2023-06-01"),
        (2023, 6, "2023-06-01"),
    ]

    no_date_after_assign = client.get("/year/2022/no-date/photos?seed=test-seed&page=0&page_size=10")
    assert no_date_after_assign.get_json()["total"] == 0

    target_month_page = client.get("/year/2023/6")
    assert target_month_page.status_code == 200
    assert b"loose-memory-a.png" in target_month_page.data
    assert b"loose-memory-b.png" in target_month_page.data


def test_no_date_photo_suggestions_can_be_accepted(client, helpers):
    helpers.create_user(client, "owner")

    uploads = [
        ("PXL_20210710_153012.png", (144, 61, 55), "/year/2022/3"),
        ("loose-scan.png", (31, 126, 77), "/year/2022/4"),
    ]
    for filename, color, path in uploads:
        image_buffer = io.BytesIO()
        Image.new("RGB", (12, 12), color=color).save(image_buffer, format="PNG")
        image_buffer.seek(0)
        response = client.post(
            path,
            data={
                **helpers.csrf_form_data(client, path),
                "photo": (image_buffer, filename, "image/png"),
                "tags": "private",
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 302

    no_date_photos = client.get("/year/2022/no-date/photos?seed=test-seed&page=0&page_size=10")
    assert no_date_photos.status_code == 200
    photos = {photo["title"]: photo for photo in no_date_photos.get_json()["photos"]}
    assert photos["PXL_20210710_153012.png"]["suggestion"] == {
        "year": 2021,
        "month": 7,
        "label": "July 2021",
        "source": "filename",
        "source_label": "Filename",
    }
    assert photos["loose-scan.png"]["suggestion"] == {
        "year": 2022,
        "month": 4,
        "label": "April 2022",
        "source": "upload_bucket",
        "source_label": "Upload month",
    }

    response = client.post(
        "/year/2022/no-date/accept-suggestions",
        json={"photo_ids": [photo["id"] for photo in photos.values()]},
        headers=helpers.csrf_headers(client, "/year/2022/no-date"),
    )
    assert response.status_code == 200
    assert response.get_json() == {"moved_count": 2, "skipped_count": 0}

    moved_rows = helpers.rows(
        """
        SELECT original_filename, year, month, photo_date
        FROM photos
        ORDER BY original_filename
        """
    )
    assert [dict(row) for row in moved_rows] == [
        {
            "original_filename": "PXL_20210710_153012.png",
            "year": 2021,
            "month": 7,
            "photo_date": "2021-07-01",
        },
        {
            "original_filename": "loose-scan.png",
            "year": 2022,
            "month": 4,
            "photo_date": "2022-04-01",
        },
    ]
    assert client.get("/year/2022/no-date/photos?seed=test-seed&page=0&page_size=10").get_json()["total"] == 0


def test_no_date_photo_quick_edit_sets_single_photo_date(client, helpers):
    helpers.create_user(client, "owner")

    for filename, color in (
        ("quick-loose-a.png", (44, 88, 132)),
        ("quick-loose-b.png", (132, 88, 44)),
    ):
        image_buffer = io.BytesIO()
        Image.new("RGB", (12, 12), color=color).save(image_buffer, format="PNG")
        image_buffer.seek(0)
        response = client.post(
            "/year/2022/3",
            data={
                **helpers.csrf_form_data(client, "/year/2022/3"),
                "photo": (image_buffer, filename, "image/png"),
                "tags": "private",
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 302

    no_date_page = client.get("/year/2022/no-date")
    assert no_date_page.status_code == 200
    assert b"/year/2022/no-date/update-date" in no_date_page.data

    photos_payload = client.get("/year/2022/no-date/photos?seed=test-seed&page=0&page_size=10").get_json()
    photos = {photo["title"]: photo for photo in photos_payload["photos"]}

    exact_response = client.post(
        "/year/2022/no-date/update-date",
        json={
            "photo_id": photos["quick-loose-a.png"]["id"],
            "photo_date": "2021-08-14",
        },
        headers=helpers.csrf_headers(client, "/year/2022/no-date"),
    )
    assert exact_response.status_code == 200
    assert exact_response.get_json()["moved_count"] == 1

    month_response = client.post(
        "/year/2022/no-date/update-date",
        json={
            "photo_id": photos["quick-loose-b.png"]["id"],
            "year": 2023,
            "month": 6,
        },
        headers=helpers.csrf_headers(client, "/year/2022/no-date"),
    )
    assert month_response.status_code == 200
    assert month_response.get_json()["moved_count"] == 1

    moved_rows = helpers.rows(
        """
        SELECT original_filename, year, month, photo_date
        FROM photos
        ORDER BY original_filename
        """
    )
    assert [dict(row) for row in moved_rows] == [
        {
            "original_filename": "quick-loose-a.png",
            "year": 2021,
            "month": 8,
            "photo_date": "2021-08-14",
        },
        {
            "original_filename": "quick-loose-b.png",
            "year": 2023,
            "month": 6,
            "photo_date": "2023-06-01",
        },
    ]
    assert client.get("/year/2022/no-date/photos?seed=test-seed&page=0&page_size=10").get_json()["total"] == 0


def test_photo_delete_removes_related_data_and_month_card(app, client, helpers):
    user_id = helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="delete-me.png",
        title="Delete me",
        caption="This photo has related rows",
        tag="friends",
        people="Alice Example",
    )

    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Delete test chapter",
            "description": "Contains the photo being deleted",
            "visibility": "private",
        },
    )
    assert chapter_response.status_code == 302
    chapter_id = helpers.row("SELECT id FROM chapters WHERE title = ?", ("Delete test chapter",))["id"]
    add_response = client.post(
        "/chapters/items",
        data={
            **helpers.csrf_form_data(client, f"/chapters/{chapter_id}"),
            "chapter_id": chapter_id,
            "item_kind": "photo",
            "item_id": photo_id,
        },
    )
    assert add_response.status_code == 302

    with app.app_context():
        db = helpers.app_module.get_db()
        message_id = db.execute(
            "INSERT INTO messages (photo_id, user_id, body) VALUES (?, ?, ?)",
            (photo_id, user_id, "A message on the photo"),
        ).lastrowid
        reaction_id = db.execute(
            "INSERT INTO item_reactions (user_id, item_kind, item_id, reaction) VALUES (?, 'photo', ?, 'love')",
            (user_id, photo_id),
        ).lastrowid
        db.execute(
            "INSERT INTO message_notification_reads (user_id, message_kind, message_id) VALUES (?, 'photo', ?)",
            (user_id, message_id),
        )
        db.execute(
            "INSERT INTO reaction_notification_reads (user_id, reaction_id) VALUES (?, ?)",
            (user_id, reaction_id),
        )
        db.commit()

    month_page = client.get("/year/2020/5")
    assert month_page.status_code == 200
    assert f'data-photo-id="{photo_id}"'.encode() in month_page.data

    delete_response = client.delete(
        f"/api/photo/{photo_id}",
        headers=helpers.csrf_headers(client, "/year/2020/5"),
    )
    assert delete_response.status_code == 204

    assert helpers.row("SELECT COUNT(*) AS count FROM photos WHERE id = ?", (photo_id,))["count"] == 0
    assert helpers.row("SELECT COUNT(*) AS count FROM messages WHERE photo_id = ?", (photo_id,))["count"] == 0
    assert helpers.row("SELECT COUNT(*) AS count FROM photo_tags WHERE photo_id = ?", (photo_id,))["count"] == 0
    assert helpers.row("SELECT COUNT(*) AS count FROM photo_people WHERE photo_id = ?", (photo_id,))["count"] == 0
    assert helpers.row(
        "SELECT COUNT(*) AS count FROM chapter_items WHERE item_kind = 'photo' AND item_id = ?",
        (photo_id,),
    )["count"] == 0
    assert helpers.row(
        "SELECT COUNT(*) AS count FROM item_reactions WHERE item_kind = 'photo' AND item_id = ?",
        (photo_id,),
    )["count"] == 0
    assert helpers.row("SELECT COUNT(*) AS count FROM message_notification_reads")["count"] == 0
    assert helpers.row("SELECT COUNT(*) AS count FROM reaction_notification_reads")["count"] == 0

    refreshed_month_page = client.get("/year/2020/5")
    assert refreshed_month_page.status_code == 200
    assert f'data-photo-id="{photo_id}"'.encode() not in refreshed_month_page.data


def test_month_bulk_actions_removes_selected_photos(app, client, helpers):
    user_id = helpers.create_user(client, "owner")

    def insert_test_photo(filename, title, year, month, photo_date, color):
        buffer = io.BytesIO()
        Image.new("RGB", (12, 12), color=color).save(buffer, format="JPEG")
        image_data = buffer.getvalue()
        with app.app_context():
            db = helpers.app_module.get_db()
            cursor = db.execute(
                """
                INSERT INTO photos (
                    user_id, year, month, original_filename, title, caption,
                    image_hash, mime_type, image_data, photo_date
                )
                VALUES (?, ?, ?, ?, ?, '', ?, 'image/jpeg', ?, ?)
                """,
                (
                    user_id,
                    year,
                    month,
                    filename,
                    title,
                    hashlib.sha256(image_data).hexdigest(),
                    image_data,
                    photo_date,
                ),
            )
            db.commit()
            return cursor.lastrowid

    first_photo_id = insert_test_photo("bulk-delete-first.jpg", "Bulk delete first", 2020, 5, "2020-05-02", (22, 109, 103))
    second_photo_id = insert_test_photo("bulk-delete-second.jpg", "Bulk delete second", 2020, 5, "2020-05-03", (184, 79, 62))
    kept_photo_id = insert_test_photo("bulk-delete-keep.jpg", "Keep me", 2020, 6, "2020-06-04", (48, 87, 180))

    with app.app_context():
        db = helpers.app_module.get_db()
        message_id = db.execute(
            "INSERT INTO messages (photo_id, user_id, body) VALUES (?, ?, ?)",
            (first_photo_id, user_id, "Bulk delete message"),
        ).lastrowid
        reaction_id = db.execute(
            "INSERT INTO item_reactions (user_id, item_kind, item_id, reaction) VALUES (?, 'photo', ?, 'like')",
            (user_id, second_photo_id),
        ).lastrowid
        db.execute(
            "INSERT INTO message_notification_reads (user_id, message_kind, message_id) VALUES (?, 'photo', ?)",
            (user_id, message_id),
        )
        db.execute(
            "INSERT INTO reaction_notification_reads (user_id, reaction_id) VALUES (?, ?)",
            (user_id, reaction_id),
        )
        db.commit()

    month_page = client.get("/year/2020/5")
    assert month_page.status_code == 200
    assert b"Bulk actions" in month_page.data
    assert b"/year/2020/5/bulk-actions" in month_page.data

    legacy_redirect = client.get("/year/2020/5/bulk-delete")
    assert legacy_redirect.status_code == 302
    assert legacy_redirect.headers["Location"].endswith("/year/2020/5/bulk-actions")

    bulk_page = client.get("/year/2020/5/bulk-actions")
    assert bulk_page.status_code == 200
    assert b"data-month-bulk-actions" in bulk_page.data
    assert b"data-month-bulk-visibility-select" in bulk_page.data
    assert b"data-month-bulk-chapter-select" in bulk_page.data
    assert b"New chapter" in bulk_page.data
    assert b"/api/year/2020/5/photos" in bulk_page.data
    assert b"/api/year/2020/5/photos/delete" in bulk_page.data
    assert b"/api/year/2020/5/photos/visibility" in bulk_page.data
    assert b"/api/chapters/bulk-add" in bulk_page.data
    assert b"/api/chapters/bulk-create" in bulk_page.data

    payload = client.get("/api/year/2020/5/photos?page=0&page_size=20").get_json()
    payload_ids = {photo["id"] for photo in payload["photos"]}
    assert first_photo_id in payload_ids
    assert second_photo_id in payload_ids
    assert kept_photo_id not in payload_ids

    delete_response = client.post(
        "/api/year/2020/5/photos/delete",
        headers=helpers.csrf_headers(client, "/year/2020/5/bulk-actions"),
        json={"photo_ids": [first_photo_id, second_photo_id, kept_photo_id]},
    )

    assert delete_response.status_code == 200
    delete_payload = delete_response.get_json()
    assert delete_payload["deleted_count"] == 2
    assert set(delete_payload["deleted_photo_ids"]) == {first_photo_id, second_photo_id}
    assert helpers.row("SELECT COUNT(*) AS count FROM photos WHERE id IN (?, ?)", (first_photo_id, second_photo_id))["count"] == 0
    assert helpers.row("SELECT COUNT(*) AS count FROM photos WHERE id = ?", (kept_photo_id,))["count"] == 1
    assert helpers.row("SELECT COUNT(*) AS count FROM messages WHERE photo_id = ?", (first_photo_id,))["count"] == 0
    assert helpers.row(
        "SELECT COUNT(*) AS count FROM item_reactions WHERE item_kind = 'photo' AND item_id = ?",
        (second_photo_id,),
    )["count"] == 0
    assert helpers.row("SELECT COUNT(*) AS count FROM message_notification_reads")["count"] == 0
    assert helpers.row("SELECT COUNT(*) AS count FROM reaction_notification_reads")["count"] == 0

    refreshed_payload = client.get("/api/year/2020/5/photos?page=0&page_size=20").get_json()
    refreshed_ids = {photo["id"] for photo in refreshed_payload["photos"]}
    assert first_photo_id not in refreshed_ids
    assert second_photo_id not in refreshed_ids


def test_month_bulk_actions_updates_selected_photo_visibility(app, client, helpers):
    user_id = helpers.create_user(client, "owner")

    def insert_test_photo(filename, title, year, month, photo_date, color):
        buffer = io.BytesIO()
        Image.new("RGB", (12, 12), color=color).save(buffer, format="JPEG")
        image_data = buffer.getvalue()
        with app.app_context():
            db = helpers.app_module.get_db()
            cursor = db.execute(
                """
                INSERT INTO photos (
                    user_id, year, month, original_filename, title, caption,
                    image_hash, mime_type, image_data, photo_date
                )
                VALUES (?, ?, ?, ?, ?, '', ?, 'image/jpeg', ?, ?)
                """,
                (
                    user_id,
                    year,
                    month,
                    filename,
                    title,
                    hashlib.sha256(image_data).hexdigest(),
                    image_data,
                    photo_date,
                ),
            )
            photo_id = cursor.lastrowid
            db.execute("INSERT OR IGNORE INTO tags (user_id, name) VALUES (?, 'private')", (user_id,))
            tag_id = db.execute(
                "SELECT id FROM tags WHERE user_id = ? AND name = 'private'",
                (user_id,),
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO photo_tags (photo_id, tag_id) VALUES (?, ?)",
                (photo_id, tag_id),
            )
            db.commit()
            return photo_id

    first_photo_id = insert_test_photo("bulk-actions-visible-first.jpg", "Visible first", 2020, 5, "2020-05-02", (22, 109, 103))
    second_photo_id = insert_test_photo("bulk-actions-visible-second.jpg", "Visible second", 2020, 5, "2020-05-03", (184, 79, 62))
    kept_photo_id = insert_test_photo("bulk-actions-visible-keep.jpg", "Keep private", 2020, 6, "2020-06-04", (48, 87, 180))

    response = client.post(
        "/api/year/2020/5/photos/visibility",
        headers=helpers.csrf_headers(client, "/year/2020/5/bulk-actions"),
        json={"photo_ids": [first_photo_id, second_photo_id, kept_photo_id], "visibility": "friends"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["updated_count"] == 2
    assert set(payload["updated_photo_ids"]) == {first_photo_id, second_photo_id}

    def photo_tag(photo_id):
        return helpers.row(
            """
            SELECT t.name
            FROM photo_tags pt
            JOIN tags t ON t.id = pt.tag_id
            WHERE pt.photo_id = ?
            """,
            (photo_id,),
        )["name"]

    assert photo_tag(first_photo_id) == "friends"
    assert photo_tag(second_photo_id) == "friends"
    assert photo_tag(kept_photo_id) == "private"


def test_month_card_can_create_new_chapter_for_photo(client, helpers):
    helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="chapter-card-photo.png",
        title="Chapter card photo",
        caption="Created from the month card",
    )

    month_page = client.get("/year/2020/5")
    assert month_page.status_code == 200
    assert b"data-chapter-card-form" in month_page.data
    assert b"data-chapter-add-url" in month_page.data
    assert b"data-chapter-card-new-form" in month_page.data
    assert b"New chapter" in month_page.data

    response = client.post(
        "/api/chapters/create-with-item",
        headers=helpers.csrf_headers(client, "/year/2020/5"),
        data={
            "title": "Month card chapter",
            "description": "Created while looking at a photo",
            "item_kind": "photo",
            "item_id": photo_id,
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["status"] == "created"
    assert payload["chapter"]["title"] == "Month card chapter"
    chapter = helpers.row(
        "SELECT id, description, visibility FROM chapters WHERE title = ?",
        ("Month card chapter",),
    )
    assert chapter["description"] == "Created while looking at a photo"
    assert chapter["visibility"] == "private"
    item = helpers.row(
        """
        SELECT item_kind, item_id, position
        FROM chapter_items
        WHERE chapter_id = ?
        """,
        (chapter["id"],),
    )
    assert dict(item) == {"item_kind": "photo", "item_id": photo_id, "position": 1}


def test_splash_page_api_and_thumbnails_are_owned(app, client, helpers):
    helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="splash-photo.png",
        title="Splash photo",
        caption="A wall-ready memory",
    )

    other_client = app.test_client()
    helpers.create_user(other_client, "other")
    other_photo_id = helpers.upload_photo(
        other_client,
        filename="other-splash-photo.png",
        title="Other splash photo",
    )

    page = client.get("/splash")
    assert page.status_code == 200
    assert b"data-splash" in page.data
    assert b'data-splash-size="0.5"' in page.data
    assert b'data-splash-size="1"' in page.data
    assert b'data-splash-size="1.5"' in page.data

    response = client.get("/api/splash-photos?seed=test-seed&page=0&page_size=1")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 1
    assert payload["total_pages"] == 1
    assert payload["photos"][0]["id"] == photo_id
    assert payload["photos"][0]["title"] == "Splash photo"
    assert payload["photos"][0]["full_url"] == f"/photo/{photo_id}/image"

    thumbnail = client.get(payload["photos"][0]["thumbnail_url"])
    assert thumbnail.status_code == 200
    assert thumbnail.mimetype == "image/jpeg"
    assert thumbnail.data.startswith(b"\xff\xd8")
    assert client.get(f"/photo/{other_photo_id}/thumbnail").status_code == 404


def test_splash_modal_can_add_photo_to_existing_chapter(client, helpers):
    helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="chapter-splash-add.png",
        title="Chapter splash add",
    )
    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Favorites",
            "description": "Saved from Splash",
            "visibility": "private",
        },
    )
    assert chapter_response.status_code == 302
    chapter_id = helpers.row("SELECT id FROM chapters WHERE title = ?", ("Favorites",))["id"]

    page = client.get("/splash")
    assert page.status_code == 200
    assert b"data-splash-chapter-form" in page.data
    assert b"data-splash-chapter-api" in page.data
    assert b"data-splash-chapter-photo-id" in page.data
    assert b"data-splash-chapter-select" in page.data
    assert b"data-splash-chapter-submit" not in page.data
    assert b"Add to chapter" in page.data
    assert b"Favorites" in page.data

    response = client.post(
        "/api/chapters/items",
        data={
            **helpers.csrf_form_data(client, "/splash"),
            "chapter_id": chapter_id,
            "item_kind": "photo",
            "item_id": photo_id,
        },
    )
    assert response.status_code == 201
    assert response.get_json()["status"] == "added"
    assert helpers.row(
        """
        SELECT id
        FROM chapter_items
        WHERE chapter_id = ? AND item_kind = 'photo' AND item_id = ?
        """,
        (chapter_id, photo_id),
    )
    duplicate = client.post(
        "/api/chapters/items",
        data={
            **helpers.csrf_form_data(client, "/splash"),
            "chapter_id": chapter_id,
            "item_kind": "photo",
            "item_id": photo_id,
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.get_json()["status"] == "exists"


def test_admin_page_is_daniel_only_and_converts_existing_images(app, client, helpers):
    helpers.create_user(client, "alice")

    response = client.get("/admin")
    assert response.status_code == 404

    assert client.post(
        "/logout",
        data=helpers.csrf_form_data(client, "/timeline"),
    ).status_code == 302

    daniel_id = helpers.create_user(client, "Daniel")
    admin_page = client.get("/admin")
    assert admin_page.status_code == 200
    assert b"Convert all images to JPEG" in admin_page.data

    legacy_png = io.BytesIO()
    Image.new("RGB", (1800, 900), color=(31, 126, 116)).save(legacy_png, format="PNG")
    with app.app_context():
        db = helpers.app_module.get_db()
        db.execute(
            """
            INSERT INTO photos (
                user_id, year, month, original_filename, mime_type, image_data, image_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                daniel_id,
                2020,
                5,
                "legacy.png",
                "image/png",
                legacy_png.getvalue(),
                "legacy-hash",
            ),
        )
        db.commit()

    convert_response = client.post(
        "/admin/images/convert-jpeg",
        data=helpers.csrf_form_data(client, "/admin"),
        follow_redirects=True,
    )
    assert convert_response.status_code == 200
    assert b"Converted" in convert_response.data
    convert_job = helpers.row("SELECT * FROM jobs WHERE kind = ?", ("convert_images",))
    assert convert_job["status"] == "succeeded"
    assert convert_job["progress_current"] == convert_job["progress_total"] == 1
    job_response = client.get(f"/admin/jobs/{convert_job['id']}")
    assert job_response.status_code == 200
    assert job_response.get_json()["result_summary"].startswith("Converted 1 image rows")

    converted = helpers.row("SELECT mime_type, image_data, image_hash FROM photos WHERE original_filename = ?", ("legacy.png",))
    assert converted["mime_type"] == "image/jpeg"
    assert converted["image_data"].startswith(b"\xff\xd8")
    assert converted["image_hash"] != "legacy-hash"
    with Image.open(io.BytesIO(converted["image_data"])) as stored_image:
        assert stored_image.format == "JPEG"
        assert max(stored_image.size) == 1200


def test_admin_maintenance_compacts_existing_jpegs_and_vacuums_database(app, client, helpers):
    daniel_id = helpers.create_user(client, "Daniel")

    large_jpeg = io.BytesIO()
    Image.new("RGB", (2200, 1600), color=(140, 74, 49)).save(
        large_jpeg,
        format="JPEG",
        quality=95,
    )
    original_bytes = large_jpeg.getvalue()
    with app.app_context():
        db = helpers.app_module.get_db()
        db.execute(
            """
            INSERT INTO photos (
                user_id, year, month, original_filename, mime_type, image_data, image_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                daniel_id,
                2020,
                5,
                "large-existing.jpg",
                "image/jpeg",
                original_bytes,
                helpers.app_module.photo_image_hash(original_bytes),
            ),
        )
        db.commit()

    admin_page = client.get("/admin")
    assert admin_page.status_code == 200
    assert b"Stored image data" in admin_page.data
    assert b"Reclaim database space" in admin_page.data
    assert b"Compact image storage" in admin_page.data

    compact_response = client.post(
        "/admin/images/compact",
        data=helpers.csrf_form_data(client, "/admin"),
        follow_redirects=True,
    )
    assert compact_response.status_code == 200
    assert b"Compacted" in compact_response.data
    compact_job = helpers.row("SELECT * FROM jobs WHERE kind = ?", ("compact_images",))
    assert compact_job["status"] == "succeeded"
    assert compact_job["progress_current"] == compact_job["progress_total"]

    compacted = helpers.row(
        "SELECT image_data, image_hash FROM photos WHERE original_filename = ?",
        ("large-existing.jpg",),
    )
    assert len(compacted["image_data"]) < len(original_bytes)
    assert compacted["image_hash"] != helpers.app_module.photo_image_hash(original_bytes)
    with Image.open(io.BytesIO(compacted["image_data"])) as stored_image:
        assert stored_image.format == "JPEG"
        assert max(stored_image.size) == 1200

    vacuum_response = client.post(
        "/admin/database/vacuum",
        data=helpers.csrf_form_data(client, "/admin"),
        follow_redirects=True,
    )
    assert vacuum_response.status_code == 200
    assert b"Database is now" in vacuum_response.data
    vacuum_job = helpers.row("SELECT * FROM jobs WHERE kind = ?", ("vacuum_database",))
    assert vacuum_job["status"] == "succeeded"
    assert client.get(f"/admin/jobs/{vacuum_job['id']}").get_json()["progress_percent"] == 100


def test_manual_people_tagging_for_items_search_and_updates(app, client, helpers):
    helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="people-photo.png",
        title="Tagged photo",
        people="Alice Example, Bob Friend",
    )
    text_id = helpers.create_text(
        client,
        "Dinner after the show",
        people="Carol Cousin, alice example",
    )

    month = client.get("/year/2020/5")
    assert month.status_code == 200
    assert b"Alice Example" in month.data
    assert b"Bob Friend" in month.data
    assert b"Carol Cousin" in month.data

    timeline_items = client.get("/api/timeline-items?year=2020").get_json()
    photo = next(item for item in timeline_items if item["kind"] == "photo" and item["id"] == photo_id)
    text = next(item for item in timeline_items if item["kind"] == "text" and item["id"] == text_id)
    assert photo["people"] == ["Alice Example", "Bob Friend"]
    assert text["people"] == ["Alice Example", "Carol Cousin"]

    search = client.get("/timeline/search?q=alice")
    assert search.status_code == 200
    assert b"Tagged photo" in search.data
    assert b"Dinner after the show" in search.data

    message_response = client.post(
        f"/api/timeline-item/text/{text_id}/messages",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={"body": "Alice remembered the dessert"},
    )
    assert message_response.status_code == 201
    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Alice Story",
            "description": "Memories with Alice",
            "visibility": "private",
        },
    )
    assert chapter_response.status_code == 302
    chapter_id = helpers.row("SELECT id FROM chapters WHERE title = ?", ("Alice Story",))["id"]
    for item_kind, item_id in (("photo", photo_id), ("text", text_id)):
        assert client.post(
            "/chapters/items",
            data={
                **helpers.csrf_form_data(client, f"/chapters/{chapter_id}"),
                "chapter_id": chapter_id,
                "item_kind": item_kind,
                "item_id": item_id,
            },
        ).status_code == 302

    people_index = client.get("/timeline/people")
    assert people_index.status_code == 200
    assert b"Alice Example" in people_index.data
    assert b"Bob Friend" in people_index.data
    assert b"Carol Cousin" in people_index.data
    assert b"2 memories" in people_index.data
    assert b"Tagged memories" in people_index.data
    assert b"/chapters/draft?person=Alice+Example" in people_index.data

    alice = helpers.row(
        "SELECT id FROM people WHERE user_id = ? AND name = ?",
        (helpers.user_id("owner"), "Alice Example"),
    )
    alice_page = client.get(f"/timeline/people/{alice['id']}")
    assert alice_page.status_code == 200
    assert b"Tagged photo" in alice_page.data
    assert b"Dinner after the show" in alice_page.data
    assert b"Related chapters" in alice_page.data
    assert b"Alice Story" in alice_page.data
    assert b"Messages" in alice_page.data
    assert b"Draft chapter" in alice_page.data

    other = app.test_client()
    helpers.create_user(other, "other")
    helpers.create_text(other, "Hidden people memory", people="Forbidden Person")
    hidden_search = client.get("/timeline/search?q=forbidden")
    assert hidden_search.status_code == 200
    assert b"Hidden people memory" not in hidden_search.data
    forbidden_person = helpers.row(
        "SELECT id FROM people WHERE user_id = ? AND name = ?",
        (helpers.user_id("other"), "Forbidden Person"),
    )
    assert client.get(f"/timeline/people/{forbidden_person['id']}").status_code == 404

    photo_people_update = client.patch(
        f"/api/photo/{photo_id}/people",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={"people": "Dana New, Bob Friend"},
    )
    assert photo_people_update.status_code == 200
    assert photo_people_update.get_json()["people"] == ["Bob Friend", "Dana New"]

    text_update = client.patch(
        f"/api/text-entry/{text_id}",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={
            "body": "Dinner after the show",
            "entry_date": "2020-05-03",
            "tags": "private",
            "people": "Eve Mentor",
        },
    )
    assert text_update.status_code == 200
    assert text_update.get_json()["people"] == ["Eve Mentor"]


def test_guided_memory_prompts_surface_missing_context_and_refresh(client, helpers):
    helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="prompt-photo.png",
        title="Prompt photo",
    )
    text_id = helpers.create_text(client, "Short memory", people="")

    photo = client.get(f"/api/photo/{photo_id}").get_json()
    photo_prompt_targets = {prompt["target"] for prompt in photo["guided_prompts"]}
    assert {"caption", "people", "location"}.issubset(photo_prompt_targets)
    assert any(prompt["label"] == "What do you remember most?" for prompt in photo["guided_prompts"])

    photo_update = client.patch(
        f"/api/photo/{photo_id}",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={
            "title": "Prompt photo",
            "caption": "A richer caption from a prompt",
        },
    ).get_json()
    assert not any(
        prompt["label"] == "What do you remember most?"
        for prompt in photo_update["guided_prompts"]
    )

    people_update = client.patch(
        f"/api/photo/{photo_id}/people",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={"people": "Avery Guide"},
    ).get_json()
    assert "people" not in {prompt["target"] for prompt in people_update["guided_prompts"]}

    timeline_items = client.get("/api/timeline-items?year=2020").get_json()
    prompt_photo = next(item for item in timeline_items if item["kind"] == "photo" and item["id"] == photo_id)
    assert "guided_prompts" in prompt_photo

    text_entry = client.get(f"/api/text-entry/{text_id}").get_json()
    text_prompt_targets = {prompt["target"] for prompt in text_entry["guided_prompts"]}
    assert {"body", "people", "location"}.issubset(text_prompt_targets)

    text_update = client.patch(
        f"/api/text-entry/{text_id}",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={
            "body": "This memory now has more context about what happened and why it mattered.",
            "entry_date": "2020-05-03",
            "tags": "private",
            "people": "Riley Reader",
        },
    ).get_json()
    assert "people" not in {prompt["target"] for prompt in text_update["guided_prompts"]}


def test_timeline_import_assistant_reviews_detected_dates_before_saving(client, helpers):
    helpers.create_user(client, "owner")

    import_page = client.get("/timeline/import")
    assert import_page.status_code == 200
    assert b"data-upload-progress" in import_page.data
    assert b"data-upload-progress-bar" in import_page.data

    upload_response = client.post(
        "/timeline/import",
        data={
            **helpers.csrf_form_data(client, "/timeline/import"),
            "photo": (
                io.BytesIO(helpers.png_bytes()),
                "memory-20200504.png",
                "image/png",
            ),
        },
        content_type="multipart/form-data",
    )

    assert upload_response.status_code == 302
    review_path = upload_response.headers["Location"]
    item = helpers.row(
        """
        SELECT pii.*
        FROM photo_import_items pii
        JOIN photo_import_batches pib ON pib.id = pii.batch_id
        """
    )
    assert item["detected_date"] == "2020-05-04"
    assert item["detected_source"] == "filename"

    review_response = client.get(review_path)
    assert review_response.status_code == 200
    assert b"Review import" in review_response.data
    assert b"2020-05-04" in review_response.data
    assert f"{review_path}/items/{item['id']}/thumbnail".encode() in review_response.data
    thumbnail_response = client.get(f"{review_path}/items/{item['id']}/thumbnail")
    assert thumbnail_response.status_code == 200
    assert thumbnail_response.mimetype == "image/jpeg"
    assert thumbnail_response.data.startswith(b"\xff\xd8")

    save_response = client.post(
        review_path,
        data={
            **helpers.csrf_form_data(client, review_path),
            f"photo_date_{item['id']}": "2020-06-07",
            f"tags_{item['id']}": "friends",
        },
    )

    assert save_response.status_code == 302
    assert save_response.headers["Location"].endswith("/year/2020/6")
    photo = helpers.row("SELECT * FROM photos WHERE original_filename = ?", ("memory-20200504.png",))
    assert photo["year"] == 2020
    assert photo["month"] == 6
    assert photo["photo_date"] == "2020-06-07"
    assert helpers.row(
        """
        SELECT t.name
        FROM photo_tags pt
        JOIN tags t ON t.id = pt.tag_id
        WHERE pt.photo_id = ?
        """,
        (photo["id"],),
    )["name"] == "friends"
    assert helpers.row("SELECT COUNT(*) AS count FROM photo_import_batches")["count"] == 0
    assert helpers.row("SELECT COUNT(*) AS count FROM photo_import_items")["count"] == 0


def test_timeline_import_review_is_paginated_and_saves_page_edits(client, helpers):
    helpers.create_user(client, "owner")
    photos = [
        (
            io.BytesIO(helpers.png_bytes()),
            f"memory-20200504-{index:02d}.png",
            "image/png",
        )
        for index in range(51)
    ]

    upload_response = client.post(
        "/timeline/import",
        data={
            **helpers.csrf_form_data(client, "/timeline/import"),
            "photo": photos,
        },
        content_type="multipart/form-data",
    )

    assert upload_response.status_code == 302
    review_path = upload_response.headers["Location"]
    page_one = client.get(review_path)
    assert page_one.status_code == 200
    assert b"Showing 1-50" in page_one.data
    assert b"of 51 photos" in page_one.data
    assert b"memory-20200504-00.png" in page_one.data
    assert b"memory-20200504-50.png" not in page_one.data

    first_item = helpers.row("SELECT * FROM photo_import_items ORDER BY id ASC LIMIT 1")
    next_response = client.post(
        f"{review_path}?page=2",
        data={
            **helpers.csrf_form_data(client, review_path),
            "page": "1",
            "action": "page",
            f"photo_date_{first_item['id']}": "2020-06-07",
            f"tags_{first_item['id']}": "friends",
        },
    )

    assert next_response.status_code == 302
    assert next_response.headers["Location"].endswith("?page=2")
    saved_item = helpers.row("SELECT review_date, review_tag, review_skip FROM photo_import_items WHERE id = ?", (first_item["id"],))
    assert dict(saved_item) == {"review_date": "2020-06-07", "review_tag": "friends", "review_skip": 0}

    page_two = client.get(next_response.headers["Location"])
    assert page_two.status_code == 200
    assert b"Showing 51-51" in page_two.data
    assert b"of 51 photos" in page_two.data
    assert b"memory-20200504-50.png" in page_two.data
    last_item = helpers.row("SELECT * FROM photo_import_items ORDER BY id DESC LIMIT 1")
    assert f'name="photo_date_{last_item["id"]}"'.encode() in page_two.data
    assert f'name="photo_date_{first_item["id"]}"'.encode() not in page_two.data


def test_timeline_import_assistant_reviews_duplicate_photos(client, helpers):
    helpers.create_user(client, "owner")
    existing_photo_id = helpers.upload_photo(
        client,
        filename="existing-memory.png",
        title="Existing memory",
        photo_date="2020-05-04",
        tag="private",
    )

    upload_response = client.post(
        "/timeline/import",
        data={
            **helpers.csrf_form_data(client, "/timeline/import"),
            "photo": (
                io.BytesIO(helpers.png_bytes()),
                "duplicate-20200504.png",
                "image/png",
            ),
        },
        content_type="multipart/form-data",
    )

    assert upload_response.status_code == 302
    review_path = upload_response.headers["Location"]
    item = helpers.row("SELECT * FROM photo_import_items")
    assert item["duplicate_photo_id"] == existing_photo_id

    review_response = client.get(review_path)
    assert review_response.status_code == 200
    assert b"Possible duplicate" in review_response.data
    assert b"Already in timeline" in review_response.data
    assert b"Existing memory" in review_response.data
    assert f"/photo/{existing_photo_id}/thumbnail".encode() in review_response.data
    assert b"Skip duplicate" in review_response.data
    assert b"checked" in review_response.data

    save_response = client.post(
        review_path,
        data={
            **helpers.csrf_form_data(client, review_path),
            f"photo_date_{item['id']}": "2020-05-04",
            f"tags_{item['id']}": "private",
        },
    )

    assert save_response.status_code == 302
    assert helpers.row("SELECT COUNT(*) AS count FROM photos")["count"] == 2
    assert helpers.row("SELECT COUNT(*) AS count FROM photo_import_batches")["count"] == 0


def test_full_account_backup_export_and_import(client, helpers):
    owner_id = helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="family-trip.png",
        title="Family trip",
        caption="Standing together beside the lake",
        photo_date="2020-05-04",
        tag="family",
        people="Maya Lake, Theo Lake",
    )
    text_id = helpers.create_text(
        client,
        "A text memory to preserve",
        entry_date="2020-05-05",
        tag="friends",
        people="Nora Notes",
    )

    photo_message = client.post(
        f"/api/timeline-item/photo/{photo_id}/messages",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={"body": "Photo message"},
    )
    assert photo_message.status_code == 201
    text_message = client.post(
        f"/api/timeline-item/text/{text_id}/messages",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={"body": "Text message"},
    )
    assert text_message.status_code == 201
    reaction = client.put(
        f"/api/timeline-item/text/{text_id}/reaction",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={"reaction": "love"},
    )
    assert reaction.status_code == 200

    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Chapter One",
            "description": "A restored story",
        },
    )
    assert chapter_response.status_code == 302
    chapter_id = helpers.row(
        "SELECT id FROM chapters WHERE user_id = ?",
        (owner_id,),
    )["id"]
    for item_kind, item_id in (("photo", photo_id), ("text", text_id)):
        add_response = client.post(
            "/chapters/items",
            data={
                **helpers.csrf_form_data(client, f"/chapters/{chapter_id}"),
                "chapter_id": chapter_id,
                "item_kind": item_kind,
                "item_id": item_id,
            },
        )
        assert add_response.status_code == 302

    export_response = client.get("/account/export.zip")
    assert export_response.status_code == 200
    assert export_response.mimetype == "application/zip"

    with zipfile.ZipFile(io.BytesIO(export_response.data)) as archive:
        manifest = json.loads(archive.read("evertimeline-backup.json").decode("utf-8"))
        assert manifest["format"] == "evertimeline.account_backup"
        assert manifest["user"]["username"] == "owner"
        assert manifest["photos"][0]["title"] == "Family trip"
        assert manifest["photos"][0]["caption"] == "Standing together beside the lake"
        assert manifest["photos"][0]["tags"] == ["family"]
        assert manifest["photos"][0]["people"] == ["Maya Lake", "Theo Lake"]
        assert manifest["text_entries"][0]["people"] == ["Nora Notes"]
        assert manifest["photos"][0]["messages"][0]["body"] == "Photo message"
        assert manifest["text_entries"][0]["reactions"][0]["reaction"] == "love"
        assert archive.read(manifest["photos"][0]["image_path"]).startswith(b"\xff\xd8")

    assert client.post(
        "/logout",
        data=helpers.csrf_form_data(client, "/timeline"),
    ).status_code == 302
    importer_id = helpers.create_user(client, "importer", birthday="1999-02-03")

    import_response = client.post(
        "/account/import",
        data={
            **helpers.csrf_form_data(client, "/profile"),
            "backup": (
                io.BytesIO(export_response.data),
                "evertimeline-backup.zip",
                "application/zip",
            ),
        },
        content_type="multipart/form-data",
    )
    assert import_response.status_code == 302

    assert helpers.row(
        "SELECT birthday FROM users WHERE id = ?",
        (importer_id,),
    )["birthday"] == "2000-01-15"
    assert helpers.row(
        "SELECT COUNT(*) AS count FROM photos WHERE user_id = ?",
        (importer_id,),
    )["count"] == 1
    assert helpers.row(
        "SELECT COUNT(*) AS count FROM text_entries WHERE user_id = ?",
        (importer_id,),
    )["count"] == 1
    assert helpers.row(
        """
        SELECT COUNT(*) AS count
        FROM messages m
        JOIN photos p ON p.id = m.photo_id
        WHERE p.user_id = ?
        """,
        (importer_id,),
    )["count"] == 1
    assert helpers.row(
        """
        SELECT COUNT(*) AS count
        FROM text_entry_messages tem
        JOIN text_entries te ON te.id = tem.entry_id
        WHERE te.user_id = ?
        """,
        (importer_id,),
    )["count"] == 1
    assert helpers.row(
        """
        SELECT COUNT(*) AS count
        FROM chapters c
        JOIN chapter_items ci ON ci.chapter_id = c.id
        WHERE c.user_id = ?
        """,
        (importer_id,),
    )["count"] == 2
    assert helpers.row(
        """
        SELECT t.name
        FROM text_entries te
        JOIN text_entry_tags tet ON tet.entry_id = te.id
        JOIN tags t ON t.id = tet.tag_id
        WHERE te.user_id = ?
        """,
        (importer_id,),
    )["name"] == "friends"
    imported_photo = helpers.row(
        "SELECT title, caption FROM photos WHERE user_id = ?",
        (importer_id,),
    )
    assert imported_photo["title"] == "Family trip"
    assert imported_photo["caption"] == "Standing together beside the lake"
    assert [row["name"] for row in helpers.rows(
        """
        SELECT p.name
        FROM photo_people pp
        JOIN people p ON p.id = pp.person_id
        JOIN photos ph ON ph.id = pp.photo_id
        WHERE ph.user_id = ?
        ORDER BY p.name
        """,
        (importer_id,),
    )] == ["Maya Lake", "Theo Lake"]
    assert [row["name"] for row in helpers.rows(
        """
        SELECT p.name
        FROM text_entry_people tep
        JOIN people p ON p.id = tep.person_id
        JOIN text_entries te ON te.id = tep.entry_id
        WHERE te.user_id = ?
        ORDER BY p.name
        """,
        (importer_id,),
    )] == ["Nora Notes"]

    duplicate_import_response = client.post(
        "/account/import",
        data={
            **helpers.csrf_form_data(client, "/profile"),
            "backup": (
                io.BytesIO(export_response.data),
                "evertimeline-backup-again.zip",
                "application/zip",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert duplicate_import_response.status_code == 200
    assert b"Skipped 1 duplicate photo already in your timeline." in duplicate_import_response.data
    assert helpers.row(
        "SELECT COUNT(*) AS count FROM photos WHERE user_id = ?",
        (importer_id,),
    )["count"] == 1


def test_chapter_items_can_be_reordered_with_json_api(client, helpers):
    helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="chapter-photo.png",
        photo_date="2020-05-04",
        tag="private",
    )
    first_text_id = helpers.create_text(
        client,
        "First text entry",
        entry_date="2020-05-05",
        tag="private",
    )
    second_text_id = helpers.create_text(
        client,
        "Second text entry",
        entry_date="2020-05-06",
        tag="private",
    )

    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Drag story",
            "description": "",
            "visibility": "private",
        },
    )
    assert chapter_response.status_code == 302
    chapter_id = helpers.row("SELECT id FROM chapters WHERE title = ?", ("Drag story",))["id"]

    for item_kind, item_id in (
        ("photo", photo_id),
        ("text", first_text_id),
        ("text", second_text_id),
    ):
        response = client.post(
            "/chapters/items",
            data={
                **helpers.csrf_form_data(client, f"/chapters/{chapter_id}"),
                "chapter_id": chapter_id,
                "item_kind": item_kind,
                "item_id": item_id,
            },
        )
        assert response.status_code == 302

    original_items = helpers.rows(
        """
        SELECT id
        FROM chapter_items
        WHERE chapter_id = ?
        ORDER BY position ASC
        """,
        (chapter_id,),
    )
    reordered_ids = [row["id"] for row in reversed(original_items)]

    response = client.post(
        f"/api/chapters/{chapter_id}/items/reorder",
        headers=helpers.csrf_headers(client, f"/chapters/{chapter_id}"),
        json={"item_ids": reordered_ids},
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"

    saved_order = helpers.rows(
        """
        SELECT id, position
        FROM chapter_items
        WHERE chapter_id = ?
        ORDER BY position ASC
        """,
        (chapter_id,),
    )
    assert [row["id"] for row in saved_order] == reordered_ids
    assert [row["position"] for row in saved_order] == [1, 2, 3]

    bad_response = client.post(
        f"/api/chapters/{chapter_id}/items/reorder",
        headers=helpers.csrf_headers(client, f"/chapters/{chapter_id}"),
        json={"item_ids": reordered_ids[:-1]},
    )
    assert bad_response.status_code == 400

def test_chapter_draft_suggests_filtered_items_and_creates_chapter(client, helpers):
    helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="draft-photo.png",
        title="Harbor arrival",
        caption="Walking into the harbor",
        photo_date="2020-05-04",
        people="Maya Lake",
        location_name="Lisbon",
        tag="friends",
    )
    text_id = helpers.create_text(
        client,
        "Dinner with Maya after the ferry ride",
        entry_date="2020-05-06",
        people="Maya Lake",
        location_name="Lisbon",
        tag="friends",
    )
    helpers.create_text(
        client,
        "Unrelated cabin weekend",
        entry_date="2020-05-07",
        people="Noah Woods",
        tag="private",
    )

    draft = client.get("/chapters/draft?person=Maya&place=Lisbon&visibility=friends")
    assert draft.status_code == 200
    assert b"Maya Memories" in draft.data
    assert b"Harbor arrival" in draft.data
    assert b"Dinner with Maya" in draft.data
    assert b"Unrelated cabin weekend" not in draft.data

    response = client.post(
        "/chapters/draft?person=Maya&place=Lisbon&visibility=friends",
        data={
            **helpers.csrf_form_data(client, "/chapters/draft?person=Maya&place=Lisbon&visibility=friends"),
            "title": "Maya in Lisbon",
            "description": "Edited draft description",
            "visibility": "friends",
            "item_refs": [f"photo:{photo_id}", f"text:{text_id}"],
        },
    )
    assert response.status_code == 302
    chapter = helpers.row("SELECT id, title, description, visibility FROM chapters WHERE title = ?", ("Maya in Lisbon",))
    assert chapter["description"] == "Edited draft description"
    assert chapter["visibility"] == "friends"
    items = helpers.rows(
        """
        SELECT item_kind, item_id, position
        FROM chapter_items
        WHERE chapter_id = ?
        ORDER BY position ASC
        """,
        (chapter["id"],),
    )
    assert [(row["item_kind"], row["item_id"], row["position"]) for row in items] == [
        ("photo", photo_id, 1),
        ("text", text_id, 2),
    ]


def test_chapter_visual_bulk_select_creates_chapter_from_selected_photos(app, client, helpers):
    helpers.create_user(client, "owner")

    def upload_colored_photo(filename, color, title):
        buffer = io.BytesIO()
        Image.new("RGB", (14, 14), color=color).save(buffer, format="PNG")
        response = client.post(
            "/year/2020/5",
            data={
                **helpers.csrf_form_data(client, "/year/2020/5"),
                "photo": (io.BytesIO(buffer.getvalue()), filename, "image/png"),
                "photo_date": "2020-05-04",
                "title": title,
                "caption": "",
                "tags": "private",
                "people": "",
                "location_name": "",
                "latitude": "",
                "longitude": "",
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 302
        return helpers.row(
            "SELECT id FROM photos WHERE original_filename = ?",
            (filename,),
        )["id"]

    first_photo_id = upload_colored_photo("bulk-first.png", (22, 109, 103), "First bulk")
    second_photo_id = upload_colored_photo("bulk-second.png", (184, 79, 62), "Second bulk")

    other_client = app.test_client()
    helpers.create_user(other_client, "other")
    other_photo_id = helpers.upload_photo(
        other_client,
        filename="not-your-bulk-photo.png",
        title="Not yours",
    )

    chapters_page = client.get("/chapters")
    assert chapters_page.status_code == 200
    assert b"Visual bulk select" in chapters_page.data

    picker_page = client.get("/chapters/bulk-select")
    assert picker_page.status_code == 200
    assert b"data-chapter-bulk-select" in picker_page.data
    assert b"/api/splash-photos" in picker_page.data
    assert b"data-chapter-bulk-chapter-select" in picker_page.data
    assert b"New chapter" in picker_page.data
    assert b"Save to chapter" not in picker_page.data

    selected_ids = [second_photo_id, first_photo_id]
    api_create_response = client.post(
        "/api/chapters/bulk-create",
        data={
            **helpers.csrf_form_data(client, "/chapters/bulk-select"),
            "selected_photo_ids": json.dumps(selected_ids),
            "title": "API visual story",
            "description": "Made immediately",
        },
    )
    assert api_create_response.status_code == 201
    api_create_payload = api_create_response.get_json()
    assert api_create_payload["status"] == "created"
    assert api_create_payload["added_count"] == 2
    api_chapter = helpers.row(
        "SELECT id, description, visibility FROM chapters WHERE title = ?",
        ("API visual story",),
    )
    assert api_chapter["description"] == "Made immediately"
    assert api_chapter["visibility"] == "private"
    api_items = helpers.rows(
        """
        SELECT item_kind, item_id, position
        FROM chapter_items
        WHERE chapter_id = ?
        ORDER BY position ASC
        """,
        (api_chapter["id"],),
    )
    assert [(row["item_kind"], row["item_id"], row["position"]) for row in api_items] == [
        ("photo", second_photo_id, 1),
        ("photo", first_photo_id, 2),
    ]

    existing_response = client.post(
        "/api/chapters/bulk-add",
        data={
            **helpers.csrf_form_data(client, "/chapters/bulk-select"),
            "selected_photo_ids": json.dumps([first_photo_id, second_photo_id]),
            "chapter_id": api_chapter["id"],
        },
    )
    assert existing_response.status_code == 200
    existing_payload = existing_response.get_json()
    assert existing_payload["added_count"] == 0
    assert existing_payload["existing_count"] == 2

    review_page = client.post(
        "/chapters/bulk-review",
        data={
            **helpers.csrf_form_data(client, "/chapters/bulk-select"),
            "selected_photo_ids": json.dumps(selected_ids),
        },
    )
    assert review_page.status_code == 200
    assert b"Save chapter" in review_page.data
    assert b"Second bulk" in review_page.data
    assert b"First bulk" in review_page.data

    create_response = client.post(
        "/chapters/bulk-create",
        data={
            **helpers.csrf_form_data(client, "/chapters/bulk-review"),
            "selected_photo_ids": json.dumps(selected_ids),
            "title": "Visual story",
            "description": "Made from the visual selector",
            "visibility": "friends",
        },
    )
    assert create_response.status_code == 302
    chapter = helpers.row(
        "SELECT id, description, visibility FROM chapters WHERE title = ?",
        ("Visual story",),
    )
    assert chapter["description"] == "Made from the visual selector"
    assert chapter["visibility"] == "friends"
    items = helpers.rows(
        """
        SELECT item_kind, item_id, position
        FROM chapter_items
        WHERE chapter_id = ?
        ORDER BY position ASC
        """,
        (chapter["id"],),
    )
    assert [(row["item_kind"], row["item_id"], row["position"]) for row in items] == [
        ("photo", second_photo_id, 1),
        ("photo", first_photo_id, 2),
    ]

    invalid_review = client.post(
        "/chapters/bulk-review",
        data={
            **helpers.csrf_form_data(client, "/chapters/bulk-select"),
            "selected_photo_ids": json.dumps([other_photo_id]),
        },
    )
    assert invalid_review.status_code == 302
    assert invalid_review.headers["Location"].endswith("/chapters/bulk-select")


def test_chapter_splash_view_pages_photo_thumbnails(app, client, helpers):
    helpers.create_user(client, "owner")

    def upload_colored_photo(filename, color, title, photo_date):
        buffer = io.BytesIO()
        Image.new("RGB", (14, 14), color=color).save(buffer, format="PNG")
        response = client.post(
            "/year/2020/5",
            data={
                **helpers.csrf_form_data(client, "/year/2020/5"),
                "photo": (io.BytesIO(buffer.getvalue()), filename, "image/png"),
                "photo_date": photo_date,
                "title": title,
                "caption": "",
                "tags": "private",
                "people": "",
                "location_name": "",
                "latitude": "",
                "longitude": "",
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 302
        return helpers.row("SELECT id FROM photos WHERE original_filename = ?", (filename,))["id"]

    first_photo_id = upload_colored_photo(
        "chapter-splash-first.png",
        (22, 109, 103),
        "First splash photo",
        "2020-05-04",
    )
    text_id = helpers.create_text(client, "Text should not appear in splash", entry_date="2020-05-05")
    second_photo_id = upload_colored_photo(
        "chapter-splash-second.png",
        (184, 79, 62),
        "Second splash photo",
        "2020-05-06",
    )
    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Splash Chapter",
            "description": "A visual chapter wall",
            "visibility": "private",
        },
    )
    assert chapter_response.status_code == 302
    chapter_id = helpers.row("SELECT id FROM chapters WHERE title = ?", ("Splash Chapter",))["id"]
    for item_kind, item_id in (("photo", first_photo_id), ("text", text_id), ("photo", second_photo_id)):
        assert client.post(
            "/chapters/items",
            data={
                **helpers.csrf_form_data(client, f"/chapters/{chapter_id}"),
                "chapter_id": chapter_id,
                "item_kind": item_kind,
                "item_id": item_id,
            },
        ).status_code == 302

    chapter_page = client.get(f"/chapters/{chapter_id}")
    assert chapter_page.status_code == 200
    assert f"/chapters/{chapter_id}/splash".encode() in chapter_page.data

    splash_page = client.get(f"/chapters/{chapter_id}/splash")
    assert splash_page.status_code == 200
    assert b"data-splash" in splash_page.data
    assert b'data-splash-tile-size="123"' in splash_page.data
    assert f"/api/chapters/{chapter_id}/splash-photos".encode() in splash_page.data

    first_payload = client.get(f"/api/chapters/{chapter_id}/splash-photos?page_size=1&page=0").get_json()
    second_payload = client.get(f"/api/chapters/{chapter_id}/splash-photos?page_size=1&page=1").get_json()
    assert first_payload["total"] == 2
    assert first_payload["total_pages"] == 2
    assert first_payload["photos"][0]["id"] == first_photo_id
    assert second_payload["photos"][0]["id"] == second_photo_id
    assert client.get(first_payload["photos"][0]["thumbnail_url"]).status_code == 200
    assert client.get(first_payload["photos"][0]["full_url"]).status_code == 200

    other_client = app.test_client()
    helpers.create_user(other_client, "other")
    assert other_client.get(f"/chapters/{chapter_id}/splash").status_code == 404
    assert other_client.get(f"/api/chapters/{chapter_id}/splash-photos").status_code == 404


def test_privacy_preview_filters_owner_timeline_views(client, helpers):
    helpers.create_user(client, "owner")
    helpers.create_text(
        client,
        "private memory",
        entry_date="2020-05-01",
        tag="private",
    )
    helpers.create_text(
        client,
        "family memory",
        entry_date="2020-05-02",
        tag="family",
    )
    helpers.create_text(
        client,
        "friends memory",
        entry_date="2020-05-03",
        tag="friends",
    )
    helpers.create_text(
        client,
        "public memory",
        entry_date="2020-05-04",
        tag="public",
    )

    timeline = client.get("/timeline?preview=friend")
    assert timeline.status_code == 200
    assert b"Friend view" in timeline.data
    assert b'href="/year/2020?preview=friend"' in timeline.data
    assert b'<span class="count-badge">2</span>' in timeline.data

    year = client.get("/year/2020?preview=public")
    assert year.status_code == 200
    assert b"All connections view" in year.data
    assert b'href="/year/2020/5?preview=public"' in year.data
    assert b'<span class="count-badge">1</span>' in year.data

    friend_month = client.get("/year/2020/5?preview=friend")
    assert friend_month.status_code == 200
    assert b"friends memory" in friend_month.data
    assert b"public memory" in friend_month.data
    assert b"private memory" not in friend_month.data
    assert b"family memory" not in friend_month.data
    assert b"Add timeline entries" not in friend_month.data
    assert b"readonly-text-modal" in friend_month.data

    family_items = client.get("/api/timeline-items?year=2020&preview=family").get_json()
    family_bodies = {item["body"] for item in family_items if item["kind"] == "text"}
    assert family_bodies == {"family memory", "friends memory", "public memory"}

    public_items = client.get("/api/timeline-items?year=2020&preview=public").get_json()
    public_bodies = {item["body"] for item in public_items if item["kind"] == "text"}
    assert public_bodies == {"public memory"}


def test_timeline_search_finds_owned_content_types_and_excludes_other_users(app, client, helpers):
    helpers.create_user(client, "owner")
    helpers.upload_photo(
        client,
        filename="summit-aurora.png",
        photo_date="2020-05-04",
        location_name="Mountain overlook",
        tag="public",
    )
    text_id = helpers.create_text(
        client,
        "A copper lantern memory from the cabin",
        tag="private",
    )
    message_response = client.post(
        f"/api/timeline-item/text/{text_id}/messages",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={"body": "silver echo from a message"},
    )
    assert message_response.status_code == 201

    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Harbor years",
            "description": "Storm glass notes",
        },
    )
    assert chapter_response.status_code == 302

    other = app.test_client()
    helpers.create_user(other, "other")
    helpers.create_text(other, "forbidden galaxy", tag="private")

    expectations = {
        "summit": b"summit-aurora.png",
        "2020-05-04": b"Matched photo filename, date, or person.",
        "copper": b"A copper lantern memory",
        "silver": b"silver echo from a message",
        "harbor": b"Harbor years",
        "storm": b"Storm glass notes",
    }
    for query, expected in expectations.items():
        response = client.get(f"/timeline/search?q={query}")
        assert response.status_code == 200
        assert expected in response.data

    filtered_response = client.get(
        "/timeline/search?kind=photo&visibility=public&caption=without&location=with"
    )
    assert filtered_response.status_code == 200
    assert b"summit-aurora.png" in filtered_response.data
    assert b"A copper lantern memory" not in filtered_response.data

    message_filter_response = client.get("/timeline/search?messages=with")
    assert message_filter_response.status_code == 200
    assert b"A copper lantern memory" in message_filter_response.data
    assert b"summit-aurora.png" not in message_filter_response.data

    chapter_filter_response = client.get("/timeline/search?chapter=out")
    assert chapter_filter_response.status_code == 200
    assert b"summit-aurora.png" in chapter_filter_response.data
    assert b"A copper lantern memory" in chapter_filter_response.data

    empty_visibility_response = client.get("/timeline/search?visibility=family")
    assert empty_visibility_response.status_code == 200
    assert b"summit-aurora.png" not in empty_visibility_response.data
    assert b"A copper lantern memory" not in empty_visibility_response.data

    response = client.get("/timeline/search?q=forbidden")
    assert response.status_code == 200
    assert b"forbidden galaxy" not in response.data
    assert b"No timeline matches." in response.data


def test_anniversary_mode_surfaces_today_upcoming_and_birthday(app, client, helpers):
    app.config["ANNIVERSARY_TODAY"] = date(2026, 5, 4)
    helpers.create_user(client, "owner", birthday="2000-05-06")
    helpers.upload_photo(
        client,
        filename="anniversary-picnic.png",
        photo_date="2020-05-04",
        title="Park picnic",
        caption="Blanket by the old willow",
        people="Avery Guide",
        location_name="Harbor Park",
    )
    helpers.create_text(
        client,
        "Graduation week dinner",
        year=2019,
        month=5,
        entry_date="2019-05-06",
        people="Riley Reader",
    )
    helpers.create_text(
        client,
        "Outside window",
        year=2020,
        month=7,
        entry_date="2020-07-04",
    )

    timeline = client.get("/timeline")
    assert timeline.status_code == 200
    assert b"/timeline/anniversaries" in timeline.data
    assert b"Anniversaries" in timeline.data

    response = client.get("/timeline/anniversaries")
    assert response.status_code == 200
    assert b"Anniversaries" in response.data
    assert b"On this day" in response.data
    assert b"Coming this week" in response.data
    assert b"Park picnic" in response.data
    assert b"6 years ago" in response.data
    assert b"Graduation week dinner" in response.data
    assert b"7 years ago" in response.data
    assert b"Your birthday" in response.data
    assert b"You turn 26." in response.data
    assert b"Outside window" not in response.data


def test_memory_review_queue_prioritizes_incomplete_memories(client, helpers):
    helpers.create_user(client, "owner")
    helpers.upload_photo(
        client,
        filename="public-uncaptioned.png",
        photo_date="2020-05-04",
        tag="public",
        location_name="Harbor",
    )
    helpers.create_text(
        client,
        "A short memory without tags or place",
        entry_date="2020-05-05",
        tag="private",
    )

    timeline_response = client.get("/timeline")
    assert timeline_response.status_code == 200
    assert b"Review queue" in timeline_response.data

    response = client.get("/timeline/review")
    assert response.status_code == 200
    assert b"Review queue" in response.data
    assert b"Start here" in response.data
    assert b"Photos need captions" in response.data
    assert b"Memories need places" in response.data
    assert b"Memories need people" in response.data
    assert b"Memories not in chapters" in response.data
    assert b"Public photos need polish" in response.data
    assert b"public-uncaptioned.png" in response.data
    assert b"A short memory without tags or place" in response.data
    assert b'data-review-action="caption"' in response.data
    assert b'data-review-action="people"' in response.data
    assert b'data-review-action="location"' in response.data
    assert b"/timeline/search?kind=photo&amp;caption=without" in response.data
    assert b"/timeline/search?location=without" in response.data
    assert b"/chapters/draft" in response.data
    assert b"/timeline/people" in response.data


def test_memory_review_queue_empty_and_complete_states(client, helpers):
    helpers.create_user(client, "owner")

    empty_response = client.get("/timeline/review")
    assert empty_response.status_code == 200
    assert b"Add memories to start building your review queue." in empty_response.data

    photo_id = helpers.upload_photo(
        client,
        filename="complete-photo.png",
        photo_date="2020-05-04",
        caption="A complete caption",
        tag="private",
        people="Alice Example",
        location_name="Lisbon",
    )
    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Complete story",
            "description": "A polished memory",
            "visibility": "private",
        },
    )
    assert chapter_response.status_code == 302
    chapter_id = helpers.row("SELECT id FROM chapters WHERE title = ?", ("Complete story",))["id"]
    assert client.post(
        "/chapters/items",
        data={
            **helpers.csrf_form_data(client, f"/chapters/{chapter_id}"),
            "chapter_id": chapter_id,
            "item_kind": "photo",
            "item_id": photo_id,
        },
    ).status_code == 302

    complete_response = client.get("/timeline/review")
    assert complete_response.status_code == 200
    assert b"Your timeline has no review issues right now." in complete_response.data


def test_memory_review_inline_actions_complete_photo(client, helpers):
    helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="inline-review.png",
        photo_date="2020-05-04",
        tag="private",
    )
    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Inline fixes",
            "description": "",
            "visibility": "private",
        },
    )
    assert chapter_response.status_code == 302
    chapter_id = helpers.row("SELECT id FROM chapters WHERE title = ?", ("Inline fixes",))["id"]

    review_response = client.get("/timeline/review")
    assert review_response.status_code == 200
    assert b"inline-review.png" in review_response.data
    assert b"Save caption" in review_response.data
    assert b"Save people" in review_response.data
    assert b"Save place" in review_response.data
    assert b"Choose chapter" in review_response.data

    headers = helpers.csrf_headers(client, "/timeline/review")
    caption_response = client.patch(
        f"/api/photo/{photo_id}",
        headers=headers,
        json={"title": "", "caption": "Fixed from the review queue"},
    )
    assert caption_response.status_code == 200
    people_response = client.patch(
        f"/api/photo/{photo_id}/people",
        headers=headers,
        json={"people": "Alice Review"},
    )
    assert people_response.status_code == 200
    location_response = client.patch(
        f"/api/photo/{photo_id}/location",
        headers=headers,
        json={"location_name": "Review Harbor", "latitude": "", "longitude": ""},
    )
    assert location_response.status_code == 200
    chapter_item_response = client.post(
        f"/api/timeline-review/photo/{photo_id}/chapter",
        headers=headers,
        json={"chapter_id": chapter_id},
    )
    assert chapter_item_response.status_code == 200
    assert chapter_item_response.get_json()["chapter_title"] == "Inline fixes"

    assert helpers.row(
        """
        SELECT id
        FROM chapter_items
        WHERE chapter_id = ? AND item_kind = 'photo' AND item_id = ?
        """,
        (chapter_id, photo_id),
    )
    fixed_response = client.get("/timeline/review")
    assert fixed_response.status_code == 200
    assert b"Your timeline has no review issues right now." in fixed_response.data


def test_saved_timeline_collections_filter_save_and_delete(app, client, helpers):
    helpers.create_user(client, "owner")
    helpers.create_text(
        client,
        "Family trail notes",
        entry_date="2020-05-03",
        tag="friends",
        people="Alice Walker",
        location_name="Trailhead",
    )
    helpers.upload_photo(
        client,
        filename="trail-photo.png",
        title="Trail photo",
        photo_date="2020-05-04",
        tag="friends",
        people="Alice Walker",
        location_name="Trailhead",
    )
    helpers.create_text(
        client,
        "Wrong person memory",
        entry_date="2020-05-05",
        tag="friends",
        people="Bob Walker",
        location_name="Trailhead",
    )

    other = app.test_client()
    helpers.create_user(other, "other")
    helpers.create_text(
        other,
        "Forbidden collection memory",
        tag="friends",
        people="Alice Walker",
        location_name="Trailhead",
    )

    response = client.get(
        "/timeline/collections?"
        "people=Alice%20Walker&item_kind=text&privacy_tag=friends&"
        "location=Trailhead&date_start=2020-05-01&date_end=2020-05-31"
    )
    assert response.status_code == 200
    assert b"Family trail notes" in response.data
    assert b"Trail photo" not in response.data
    assert b"Wrong person memory" not in response.data
    assert b"Forbidden collection memory" not in response.data

    save_response = client.post(
        "/timeline/collections",
        data={
            **helpers.csrf_form_data(client, "/timeline/collections"),
            "title": "Alice trail notes",
            "people": "Alice Walker",
            "item_kind": "text",
            "privacy_tag": "friends",
            "location": "Trailhead",
            "date_start": "2020-05-01",
            "date_end": "2020-05-31",
        },
    )
    assert save_response.status_code == 302
    view = helpers.row("SELECT * FROM saved_timeline_views WHERE title = ?", ("Alice trail notes",))
    assert view["people_text"] == "Alice Walker"
    assert view["item_kind"] == "text"

    saved_page = client.get("/timeline/collections")
    assert saved_page.status_code == 200
    assert b"Alice trail notes" in saved_page.data

    delete_response = client.post(
        f"/timeline/collections/{view['id']}/delete",
        data=helpers.csrf_form_data(client, "/timeline/collections"),
    )
    assert delete_response.status_code == 302
    assert helpers.row("SELECT COUNT(*) AS count FROM saved_timeline_views")["count"] == 0


def test_timeline_stories_can_save_and_delete_from_search_filters(app, client, helpers):
    helpers.create_user(client, "owner")
    helpers.upload_photo(
        client,
        filename="story-photo.png",
        title="Weekend walk",
        caption="Summer walk by the river",
        tag="private",
    )
    helpers.create_text(
        client,
        "Family picnic notes",
        entry_date="2020-05-06",
        tag="private",
    )
    other = app.test_client()
    helpers.create_user(other, "other")
    helpers.upload_photo(
        other,
        filename="other-story-photo.png",
        title="Other photo walk",
        tag="private",
    )

    save_response = client.post(
        "/timeline/stories",
        data={
            **helpers.csrf_form_data(client, "/timeline/stories"),
            "source_mode": "search",
            "q": "walk",
            "kind": "all",
            "title": "Weekend memories",
            "subtitle": "An easy one",
        },
    )
    assert save_response.status_code == 302
    story_id = save_response.headers["Location"].rsplit("/", 1)[-1]

    stories = helpers.row(
        "SELECT * FROM timeline_stories WHERE title = ?",
        ("Weekend memories",),
    )
    assert stories
    assert stories["source_mode"] == "search"
    assert json.loads(stories["filter_payload"])["query"] == "walk"

    story_page = client.get(f"/timeline/stories/{story_id}")
    assert story_page.status_code == 200
    assert b"Weekend memories" in story_page.data
    assert b"Weekend walk" in story_page.data
    assert b"Other photo walk" not in story_page.data

    saved_stories_page = client.get("/timeline/stories")
    assert saved_stories_page.status_code == 200
    assert b"Weekend memories" in saved_stories_page.data

    delete_response = client.post(
        f"/timeline/stories/{story_id}/delete",
        data=helpers.csrf_form_data(client, "/timeline/stories"),
    )
    assert delete_response.status_code == 302
    assert helpers.row("SELECT COUNT(*) AS count FROM timeline_stories")["count"] == 0


def test_timeline_stories_can_save_and_delete_from_collections_filters(app, client, helpers):
    helpers.create_user(client, "owner")
    helpers.create_text(
        client,
        "Family trail notes",
        entry_date="2020-05-03",
        tag="friends",
        people="Alice Walker",
        location_name="Trailhead",
    )
    helpers.upload_photo(
        client,
        filename="trail-photo.png",
        title="Trail photo",
        photo_date="2020-05-04",
        tag="friends",
        people="Alice Walker",
        location_name="Trailhead",
    )

    save_response = client.post(
        "/timeline/stories",
        data={
            **helpers.csrf_form_data(client, "/timeline/stories"),
            "source_mode": "collections",
            "item_kind": "photo",
            "people": "Alice Walker",
            "location": "Trailhead",
            "privacy_tag": "friends",
            "title": "Trail stories",
            "subtitle": "Friendship routes",
            "date_start": "2020-05-01",
            "date_end": "2020-05-31",
        },
    )
    assert save_response.status_code == 302
    story_id = save_response.headers["Location"].rsplit("/", 1)[-1]

    story = helpers.row("SELECT * FROM timeline_stories WHERE id = ?", (story_id,))
    assert story["source_mode"] == "collections"

    story_page = client.get(f"/timeline/stories/{story_id}")
    assert story_page.status_code == 200
    assert b"Trail stories" in story_page.data
    assert b"Trail photo" in story_page.data
    assert b"Family trail notes" not in story_page.data

    delete_response = client.post(
        f"/timeline/stories/{story_id}/delete",
        data=helpers.csrf_form_data(client, "/timeline/stories"),
    )
    assert delete_response.status_code == 302


def test_timeline_map_shows_owned_locations_and_updates_photo_place(app, client, helpers):
    helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="lisbon-photo.png",
        location_name="Lisbon",
        latitude="38.7223",
        longitude="-9.1393",
        tag="private",
    )
    text_id = helpers.create_text(
        client,
        "A quiet walk near the old station",
        location_name="Old station",
        tag="private",
    )
    lisbon_text_id = helpers.create_text(
        client,
        "A second Lisbon memory",
        entry_date="2020-05-05",
        location_name="Lisbon",
        latitude="38.7223",
        longitude="-9.1393",
        tag="private",
    )
    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Lisbon Chapter",
            "description": "Place hub chapter",
            "visibility": "private",
        },
    )
    assert chapter_response.status_code == 302
    chapter_id = helpers.row("SELECT id FROM chapters WHERE title = ?", ("Lisbon Chapter",))["id"]
    for item_kind, item_id in (("photo", photo_id), ("text", lisbon_text_id)):
        assert client.post(
            "/chapters/items",
            data={
                **helpers.csrf_form_data(client, f"/chapters/{chapter_id}"),
                "chapter_id": chapter_id,
                "item_kind": item_kind,
                "item_id": item_id,
            },
        ).status_code == 302

    other = app.test_client()
    helpers.create_user(other, "other")
    helpers.create_text(
        other,
        "hidden place",
        location_name="Forbidden harbor",
        latitude="12.34",
        longitude="56.78",
    )

    map_response = client.get("/timeline/map")
    assert map_response.status_code == 200
    assert b"Lisbon" in map_response.data
    assert b"2 memories" in map_response.data
    assert b"Old station" in map_response.data
    assert b"Needs coordinates" in map_response.data
    assert b"Forbidden harbor" not in map_response.data

    place_response = client.get("/timeline/map/place?name=Lisbon")
    assert place_response.status_code == 200
    assert b"A second Lisbon memory" in place_response.data
    assert b"lisbon-photo.png" in place_response.data
    assert b"2 memories" in place_response.data
    assert b"Lisbon Chapter" in place_response.data
    assert b"Related chapters" in place_response.data
    assert b"/chapters/draft?place=Lisbon" in place_response.data

    photo_filter_response = client.get("/timeline/map?type=photo&year=2020")
    assert photo_filter_response.status_code == 200
    assert b"lisbon-photo.png" in photo_filter_response.data
    assert b"A quiet walk near the old station" not in photo_filter_response.data

    timeline_response = client.get("/api/timeline-items?year=2020")
    assert timeline_response.status_code == 200
    items = timeline_response.get_json()
    photo = next(item for item in items if item["kind"] == "photo" and item["id"] == photo_id)
    text = next(item for item in items if item["kind"] == "text" and item["id"] == text_id)
    assert photo["location_name"] == "Lisbon"
    assert photo["latitude"] == 38.7223
    assert photo["longitude"] == -9.1393
    assert text["location_name"] == "Old station"
    assert text["latitude"] is None
    assert text["longitude"] is None

    invalid_response = client.patch(
        f"/api/photo/{photo_id}/location",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={"location_name": "Incomplete", "latitude": "10", "longitude": ""},
    )
    assert invalid_response.status_code == 400
    assert invalid_response.get_json()["error"] == "Latitude and longitude must be provided together."

    update_response = client.patch(
        f"/api/photo/{photo_id}/location",
        headers=helpers.csrf_headers(client, "/timeline"),
        json={"location_name": "Porto", "latitude": "41.1579", "longitude": "-8.6291"},
    )
    assert update_response.status_code == 200
    update_payload = update_response.get_json()
    assert {
        key: update_payload[key]
        for key in ("location_name", "latitude", "longitude")
    } == {
        "location_name": "Porto",
        "latitude": 41.1579,
        "longitude": -8.6291,
    }
    assert "guided_prompts" in update_payload

    updated_map = client.get("/timeline/map")
    assert b"Porto" in updated_map.data
    assert b"Lisbon" in updated_map.data


def test_connection_requests_and_privacy_visibility_for_friend_and_family(app, client, helpers):
    owner_id = helpers.create_user(client, "owner")
    helpers.create_text(client, "private memory", tag="private")
    helpers.create_text(client, "family memory", tag="family")
    helpers.create_text(client, "friends memory", tag="friends")
    helpers.create_text(client, "public memory", tag="public")

    stranger = app.test_client()
    helpers.create_user(stranger, "stranger")
    assert stranger.get(f"/connections/{owner_id}/api/timeline-items?year=2020").status_code == 404

    friend = app.test_client()
    helpers.create_user(friend, "friend")
    friend_request_id = helpers.request_connection(friend, owner_id, relation="friend")
    helpers.accept_connection(client, friend_request_id)

    request_row = helpers.row(
        "SELECT status, relation FROM connection_requests WHERE id = ?",
        (friend_request_id,),
    )
    assert dict(request_row) == {"status": "accepted", "relation": "friend"}

    friend_items = friend.get(f"/connections/{owner_id}/api/timeline-items?year=2020").get_json()
    friend_bodies = {item["body"] for item in friend_items if item["kind"] == "text"}
    assert friend_bodies == {"friends memory", "public memory"}

    family = app.test_client()
    helpers.create_user(family, "family")
    family_request_id = helpers.request_connection(family, owner_id, relation="family")
    helpers.accept_connection(client, family_request_id)

    family_items = family.get(f"/connections/{owner_id}/api/timeline-items?year=2020").get_json()
    family_bodies = {item["body"] for item in family_items if item["kind"] == "text"}
    assert family_bodies == {"family memory", "friends memory", "public memory"}


def test_shared_chapter_invite_allows_album_comments_without_timeline_access(app, client, helpers):
    owner_id = helpers.create_user(client, "owner")
    text_id = helpers.create_text(client, "Private chapter-only memory", tag="private")

    friend = app.test_client()
    friend_id = helpers.create_user(friend, "friend")
    request_id = helpers.request_connection(friend, owner_id, relation="friend")
    helpers.accept_connection(client, request_id)

    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Private album",
            "description": "Invite-only story",
            "visibility": "private",
        },
    )
    assert chapter_response.status_code == 302
    chapter_id = helpers.row(
        "SELECT id FROM chapters WHERE user_id = ? ORDER BY id DESC",
        (owner_id,),
    )["id"]
    add_response = client.post(
        "/chapters/items",
        data={
            **helpers.csrf_form_data(client, f"/chapters/{chapter_id}"),
            "chapter_id": chapter_id,
            "item_kind": "text",
            "item_id": text_id,
        },
    )
    assert add_response.status_code == 302

    assert friend.get(f"/shared/chapters/{chapter_id}").status_code == 404
    invite_response = client.post(
        f"/chapters/{chapter_id}/invites",
        data={
            **helpers.csrf_form_data(client, f"/chapters/{chapter_id}"),
            "recipient_id": friend_id,
        },
    )
    assert invite_response.status_code == 302
    invite = helpers.row(
        "SELECT id, status FROM chapter_invites WHERE chapter_id = ? AND recipient_id = ?",
        (chapter_id, friend_id),
    )
    assert invite["status"] == "pending"
    assert friend.get("/api/notifications/count").get_json()["count"] == 1
    assert friend.get(f"/shared/chapters/{chapter_id}").status_code == 404

    accept_response = friend.post(
        f"/chapter-invites/{invite['id']}/accept",
        data=helpers.csrf_form_data(friend, "/notifications"),
    )
    assert accept_response.status_code == 302

    shared_page = friend.get(f"/shared/chapters/{chapter_id}")
    assert shared_page.status_code == 200
    assert b"Private album" in shared_page.data
    assert b"Private chapter-only memory" in shared_page.data

    connection_items = friend.get(f"/connections/{owner_id}/api/timeline-items?year=2020")
    assert connection_items.status_code == 200
    assert all(
        item.get("body") != "Private chapter-only memory"
        for item in connection_items.get_json()
    )
    assert friend.get(f"/api/timeline-item/text/{text_id}/messages").status_code == 404

    shared_items = friend.get(f"/shared/chapters/{chapter_id}/api/items")
    assert shared_items.status_code == 200
    item_payload = shared_items.get_json()[0]
    assert item_payload["body"] == "Private chapter-only memory"
    assert item_payload["can_message"] is True

    message_response = friend.post(
        f"/shared/chapters/{chapter_id}/api/timeline-item/text/{text_id}/messages",
        headers=helpers.csrf_headers(friend, f"/shared/chapters/{chapter_id}"),
        json={"body": "I can see just this album."},
    )
    assert message_response.status_code == 201

    reaction_response = friend.put(
        f"/shared/chapters/{chapter_id}/api/timeline-item/text/{text_id}/reaction",
        headers=helpers.csrf_headers(friend, f"/shared/chapters/{chapter_id}"),
        json={"reaction": "love"},
    )
    assert reaction_response.status_code == 200
    assert reaction_response.get_json()["love_count"] == 1

    assert client.get("/api/notifications/count").get_json()["count"] == 2
    notifications = client.get("/notifications")
    assert b"I can see just this album." in notifications.data
    assert b"loved your text entry" in notifications.data


def test_reactions_messages_and_notifications_for_connection(app, client, helpers):
    owner_id = helpers.create_user(client, "owner")
    text_id = helpers.create_text(client, "A memory worth sharing", tag="friends")

    friend = app.test_client()
    helpers.create_user(friend, "friend")
    request_id = helpers.request_connection(friend, owner_id, relation="friend")
    helpers.accept_connection(client, request_id)

    message_response = friend.post(
        f"/connections/{owner_id}/api/timeline-item/text/{text_id}/messages",
        headers=helpers.csrf_headers(friend, f"/connections/{owner_id}/timeline"),
        json={"body": "This made me smile."},
    )
    assert message_response.status_code == 201
    assert message_response.get_json()["body"] == "This made me smile."

    reaction_response = friend.put(
        f"/api/timeline-item/text/{text_id}/reaction",
        headers=helpers.csrf_headers(friend, f"/connections/{owner_id}/timeline"),
        json={"reaction": "love"},
    )
    assert reaction_response.status_code == 200
    assert reaction_response.get_json()["love_count"] == 1

    notification_count = client.get("/api/notifications/count").get_json()["count"]
    assert notification_count == 2

    notifications = client.get("/notifications")
    assert notifications.status_code == 200
    assert b"This made me smile." in notifications.data
    assert b"loved your text entry" in notifications.data

    assert client.get("/api/notifications/count").get_json()["count"] == 0

    messages = client.get(f"/api/timeline-item/text/{text_id}/messages")
    assert messages.status_code == 200
    assert [message["body"] for message in messages.get_json()] == ["This made me smile."]

    lazy_items = client.get("/api/timeline-items?include_messages=0").get_json()
    lazy_text = next(item for item in lazy_items if item["kind"] == "text" and item["id"] == text_id)
    assert "messages" not in lazy_text
    assert lazy_text["messages_url"] == f"/api/timeline-item/text/{text_id}/messages"

    eager_items = client.get("/api/timeline-items").get_json()
    eager_text = next(item for item in eager_items if item["kind"] == "text" and item["id"] == text_id)
    assert [message["body"] for message in eager_text["messages"]] == ["This made me smile."]


def test_activity_history_shows_uploads_chapters_connections_comments_and_reactions(app, client, helpers):
    owner_id = helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="activity-photo.png",
        title="Activity photo",
        caption="A visible upload",
        tag="friends",
    )

    chapter_response = client.post(
        "/chapters",
        data={
            **helpers.csrf_form_data(client, "/chapters"),
            "title": "Activity chapter",
            "description": "Original chapter note",
            "visibility": "private",
        },
    )
    assert chapter_response.status_code == 302
    chapter_id = helpers.row(
        "SELECT id FROM chapters WHERE user_id = ? ORDER BY id DESC",
        (owner_id,),
    )["id"]
    add_response = client.post(
        "/chapters/items",
        data={
            **helpers.csrf_form_data(client, f"/chapters/{chapter_id}"),
            "chapter_id": chapter_id,
            "item_kind": "photo",
            "item_id": photo_id,
        },
    )
    assert add_response.status_code == 302
    update_response = client.post(
        f"/chapters/{chapter_id}/settings",
        data={
            **helpers.csrf_form_data(client, f"/chapters/{chapter_id}"),
            "title": "Activity chapter updated",
            "description": "Edited chapter note",
            "visibility": "private",
            "cover_ref": "",
        },
    )
    assert update_response.status_code == 302

    friend = app.test_client()
    helpers.create_user(friend, "friend")
    request_id = helpers.request_connection(friend, owner_id, relation="friend")
    helpers.accept_connection(client, request_id)

    message_response = friend.post(
        f"/connections/{owner_id}/api/timeline-item/photo/{photo_id}/messages",
        headers=helpers.csrf_headers(friend, f"/connections/{owner_id}/timeline"),
        json={"body": "Activity comment"},
    )
    assert message_response.status_code == 201
    reaction_response = friend.put(
        f"/api/timeline-item/photo/{photo_id}/reaction",
        headers=helpers.csrf_headers(friend, f"/connections/{owner_id}/timeline"),
        json={"reaction": "like"},
    )
    assert reaction_response.status_code == 200

    activity = client.get("/activity")
    assert activity.status_code == 200
    expectations = [
        b"History",
        b"Upload",
        b"Activity photo",
        b"Chapter",
        b"You updated chapter",
        b"You added a photo",
        b"Connection",
        b"You accepted",
        b"Comment",
        b"Activity comment",
        b"Reaction",
        b"liked your photo",
    ]
    for expected in expectations:
        assert expected in activity.data

