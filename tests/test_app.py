import io
import json
import zipfile

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


def test_uploads_text_entries_and_pdf_exports(client, helpers):
    helpers.create_user(client, "owner")

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

    converted = helpers.row("SELECT mime_type, image_data, image_hash FROM photos WHERE original_filename = ?", ("legacy.png",))
    assert converted["mime_type"] == "image/jpeg"
    assert converted["image_data"].startswith(b"\xff\xd8")
    assert converted["image_hash"] != "legacy-hash"
    with Image.open(io.BytesIO(converted["image_data"])) as stored_image:
        assert stored_image.format == "JPEG"
        assert max(stored_image.size) == 1200


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

    people_index = client.get("/timeline/people")
    assert people_index.status_code == 200
    assert b"Alice Example" in people_index.data
    assert b"Bob Friend" in people_index.data
    assert b"Carol Cousin" in people_index.data
    assert b"2 memories" in people_index.data

    alice = helpers.row(
        "SELECT id FROM people WHERE user_id = ? AND name = ?",
        (helpers.user_id("owner"), "Alice Example"),
    )
    alice_page = client.get(f"/timeline/people/{alice['id']}")
    assert alice_page.status_code == 200
    assert b"Tagged photo" in alice_page.data
    assert b"Dinner after the show" in alice_page.data

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


def test_on_this_day_shows_matching_dated_memories(client, helpers):
    helpers.create_user(client, "owner")
    helpers.upload_photo(
        client,
        year=2020,
        month=5,
        filename="picnic.png",
        photo_date="2020-05-04",
        title="Park picnic",
        tag="private",
    )
    helpers.create_text(
        client,
        "A same-day text memory",
        year=2021,
        month=5,
        entry_date="2021-05-04",
        tag="friends",
    )
    helpers.create_text(
        client,
        "A different-day text memory",
        year=2021,
        month=5,
        entry_date="2021-05-05",
        tag="friends",
    )

    response = client.get("/on-this-day?date=2026-05-04")

    assert response.status_code == 200
    assert b"On this day" in response.data
    assert b"May 4 across your timeline." in response.data
    assert b"Park picnic" in response.data
    assert b"A same-day text memory" in response.data
    assert b"A different-day text memory" not in response.data


def test_timeline_import_assistant_reviews_detected_dates_before_saving(client, helpers):
    helpers.create_user(client, "owner")

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

    selected_ids = [second_photo_id, first_photo_id]
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
    helpers.create_text(
        client,
        "A second Lisbon memory",
        entry_date="2020-05-05",
        location_name="Lisbon",
        latitude="38.7223",
        longitude="-9.1393",
        tag="private",
    )

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
