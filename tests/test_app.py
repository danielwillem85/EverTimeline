import io
import json
import zipfile


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

    photo_id = helpers.upload_photo(client, filename="public-photo.png", tag="public")
    text_id = helpers.create_text(client, "A private journal note", tag="private")

    image_response = client.get(f"/photo/{photo_id}/image")
    assert image_response.status_code == 200
    assert image_response.mimetype == "image/png"
    assert image_response.data.startswith(b"\x89PNG")

    text_response = client.get(f"/api/text-entry/{text_id}")
    assert text_response.status_code == 200
    assert text_response.get_json()["body"] == "A private journal note"

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


def test_full_account_backup_export_and_import(client, helpers):
    owner_id = helpers.create_user(client, "owner")
    photo_id = helpers.upload_photo(
        client,
        filename="family-trip.png",
        photo_date="2020-05-04",
        tag="family",
    )
    text_id = helpers.create_text(
        client,
        "A text memory to preserve",
        entry_date="2020-05-05",
        tag="friends",
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
        assert manifest["photos"][0]["tags"] == ["family"]
        assert manifest["photos"][0]["messages"][0]["body"] == "Photo message"
        assert manifest["text_entries"][0]["reactions"][0]["reaction"] == "love"
        assert archive.read(manifest["photos"][0]["image_path"]).startswith(b"\x89PNG")

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


def test_timeline_search_finds_owned_content_types_and_excludes_other_users(app, client, helpers):
    helpers.create_user(client, "owner")
    helpers.upload_photo(
        client,
        filename="summit-aurora.png",
        photo_date="2020-05-04",
        tag="private",
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
        "2020-05-04": b"Matched photo filename or date.",
        "copper": b"A copper lantern memory",
        "silver": b"silver echo from a message",
        "harbor": b"Harbor years",
        "storm": b"Storm glass notes",
    }
    for query, expected in expectations.items():
        response = client.get(f"/timeline/search?q={query}")
        assert response.status_code == 200
        assert expected in response.data

    response = client.get("/timeline/search?q=forbidden")
    assert response.status_code == 200
    assert b"forbidden galaxy" not in response.data
    assert b"No timeline matches." in response.data


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
    assert b"Old station" in map_response.data
    assert b"Needs coordinates" in map_response.data
    assert b"Forbidden harbor" not in map_response.data

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
    assert update_response.get_json() == {
        "location_name": "Porto",
        "latitude": 41.1579,
        "longitude": -8.6291,
    }

    updated_map = client.get("/timeline/map")
    assert b"Porto" in updated_map.data
    assert b"Lisbon" not in updated_map.data


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
