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
