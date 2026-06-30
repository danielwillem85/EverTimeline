"""Chapter route registration for EverTimeline.

This module keeps the chapter HTTP surface out of app.py while preserving the
existing endpoint names used throughout templates and JavaScript.
"""

import secrets

from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, url_for


def register_chapter_routes(app, core):
    DEFAULT_TAG = core.DEFAULT_TAG
    accepted_connection_between = core.accepted_connection_between
    birthday_required = core.birthday_required
    build_chapter_draft = core.build_chapter_draft
    build_chapter_items = core.build_chapter_items
    build_chapter_pdf_export_items = core.build_chapter_pdf_export_items
    build_chapter_splash_photo_page = core.build_chapter_splash_photo_page
    chapter_cover_exists = core.chapter_cover_exists
    chapter_draft_filters = core.chapter_draft_filters
    chapter_invites_for_chapter = core.chapter_invites_for_chapter
    chapter_pdf_filename = core.chapter_pdf_filename
    chapter_pdf_subtitle = core.chapter_pdf_subtitle
    chapter_visibility = core.chapter_visibility
    compact_chapter_positions = core.compact_chapter_positions
    create_timeline_item_message = core.create_timeline_item_message
    get_chapter_cover = core.get_chapter_cover
    get_chapter_options = core.get_chapter_options
    get_chapters_with_counts = core.get_chapters_with_counts
    get_db = core.get_db
    get_owned_chapter = core.get_owned_chapter
    get_owned_chapter_item = core.get_owned_chapter_item
    get_owned_timeline_item = core.get_owned_timeline_item
    get_shared_chapter = core.get_shared_chapter
    get_shared_chapter_item_access = core.get_shared_chapter_item_access
    get_shared_chapters = core.get_shared_chapters
    invitable_chapter_connections = core.invitable_chapter_connections
    load_messages_for_timeline_item = core.load_messages_for_timeline_item
    move_chapter_item = core.move_chapter_item
    next_chapter_position = core.next_chapter_position
    parse_chapter_bulk_photo_ids = core.parse_chapter_bulk_photo_ids
    parse_chapter_cover_ref = core.parse_chapter_cover_ref
    parse_chapter_draft_refs = core.parse_chapter_draft_refs
    pdf_export_response = core.pdf_export_response
    privacy_payload_for_tags = core.privacy_payload_for_tags
    redirect_back = core.redirect_back
    request_includes_messages = core.request_includes_messages
    selected_chapter_bulk_photos = core.selected_chapter_bulk_photos
    user_full_name = core.user_full_name
    user_years = core.user_years

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
        db = get_db()
        return render_template(
            "chapter_bulk_select.html",
            seed=secrets.token_hex(8),
            chapters=get_chapter_options(db),
        )
    
    
    def add_photos_to_chapter(db, chapter_id, photo_ids):
        chapter = get_owned_chapter(chapter_id)
        photos = selected_chapter_bulk_photos(db, photo_ids)
        if not photos:
            return None, jsonify({"error": "Choose at least one photo."}), 400
        if len(photos) != len(photo_ids):
            return None, jsonify({"error": "Some selected photos could not be found."}), 400
    
        added_count = 0
        existing_count = 0
        for photo_id in photo_ids:
            existing = db.execute(
                """
                SELECT id
                FROM chapter_items
                WHERE chapter_id = ? AND item_kind = 'photo' AND item_id = ?
                """,
                (chapter_id, photo_id),
            ).fetchone()
            if existing is not None:
                existing_count += 1
                continue
            db.execute(
                """
                INSERT INTO chapter_items (chapter_id, item_kind, item_id, position)
                VALUES (?, 'photo', ?, ?)
                """,
                (chapter_id, photo_id, next_chapter_position(db, chapter_id)),
            )
            added_count += 1
    
        if added_count:
            db.execute(
                "UPDATE chapters SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (chapter_id,),
            )
        db.commit()
        return {
            "status": "ok",
            "chapter": {"id": chapter["id"], "title": chapter["title"]},
            "added_count": added_count,
            "existing_count": existing_count,
            "selected_count": len(photo_ids),
            "message": f"Added {added_count} photo{'s' if added_count != 1 else ''} to {chapter['title']}.",
        }, None, None
    
    
    @app.route("/api/chapters/bulk-add", methods=("POST",))
    @birthday_required
    def api_chapter_bulk_add():
        db = get_db()
        photo_ids = parse_chapter_bulk_photo_ids(request.form.get("selected_photo_ids"))
        chapter_id = request.form.get("chapter_id", type=int)
        if chapter_id is None:
            return jsonify({"error": "Choose a chapter."}), 400
    
        payload, error_response, status = add_photos_to_chapter(db, chapter_id, photo_ids)
        if error_response is not None:
            return error_response, status
        return jsonify(payload)
    
    
    @app.route("/api/chapters/bulk-create", methods=("POST",))
    @birthday_required
    def api_chapter_bulk_create():
        db = get_db()
        photo_ids = parse_chapter_bulk_photo_ids(request.form.get("selected_photo_ids"))
        photos = selected_chapter_bulk_photos(db, photo_ids)
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        visibility = chapter_visibility(request.form.get("visibility"))
    
        if not photos:
            return jsonify({"error": "Choose at least one photo."}), 400
        if len(photos) != len(photo_ids):
            return jsonify({"error": "Some selected photos could not be found."}), 400
        if not title:
            return jsonify({"error": "Chapter title is required."}), 400
    
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
        return jsonify(
            {
                "status": "created",
                "chapter": {"id": chapter_id, "title": title},
                "added_count": len(photo_ids),
                "selected_count": len(photo_ids),
                "message": f"Created {title} with {len(photo_ids)} photo{'s' if len(photo_ids) != 1 else ''}.",
            }
        ), 201
    
    
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


    @app.route("/chapters/<int:chapter_id>/export.pdf")
    @birthday_required
    def export_chapter_pdf(chapter_id):
        db = get_db()
        chapter = dict(get_owned_chapter(chapter_id))
        owner = user_full_name(g.user) or g.user["username"]
        items = build_chapter_pdf_export_items(db, chapter_id, g.user["id"])
        return pdf_export_response(
            f"EverTimeline Chapter: {chapter['title']}",
            chapter_pdf_subtitle(chapter, owner),
            chapter_pdf_filename(chapter),
            items,
        )
    
    
    @app.route("/chapters/<int:chapter_id>/splash")
    @birthday_required
    def chapter_splash(chapter_id):
        chapter = dict(get_owned_chapter(chapter_id))
        return render_template(
            "chapter_splash.html",
            chapter=chapter,
            seed=secrets.token_hex(8),
        )
    
    
    @app.route("/api/chapters/<int:chapter_id>/splash-photos")
    @birthday_required
    def chapter_splash_photos(chapter_id):
        db = get_db()
        get_owned_chapter(chapter_id)
        return jsonify(build_chapter_splash_photo_page(db, chapter_id))
    
    
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
    
    
    def create_chapter_item_assignment(db, chapter_id, item_kind, item_id):
        chapter = get_owned_chapter(chapter_id)
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
            return {
                "status": "exists",
                "message": "That item is already in this chapter.",
                "chapter": {"id": chapter["id"], "title": chapter["title"]},
            }, False
    
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
        return {
            "status": "added",
            "message": "Added item to chapter.",
            "chapter": {"id": chapter["id"], "title": chapter["title"]},
        }, True
    
    
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
    
        result, created = create_chapter_item_assignment(db, chapter_id, item_kind, item_id)
        flash(result["message"], "success" if created else "error")
        return redirect_back("chapter_detail", chapter_id=chapter_id)
    
    
    @app.route("/api/chapters/items", methods=("POST",))
    @birthday_required
    def api_add_chapter_item():
        db = get_db()
        chapter_id = request.form.get("chapter_id", type=int)
        item_kind = request.form.get("item_kind", "")
        item_id = request.form.get("item_id", type=int)
        if chapter_id is None or item_id is None or item_kind not in ("photo", "text"):
            return jsonify({"error": "Choose a chapter and item."}), 400
    
        result, created = create_chapter_item_assignment(db, chapter_id, item_kind, item_id)
        return jsonify(result), 201 if created else 200
    
    
    @app.route("/api/chapters/create-with-item", methods=("POST",))
    @birthday_required
    def api_create_chapter_with_item():
        db = get_db()
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        item_kind = request.form.get("item_kind", "")
        item_id = request.form.get("item_id", type=int)
        visibility = chapter_visibility(request.form.get("visibility"))
    
        if item_id is None or item_kind not in ("photo", "text"):
            return jsonify({"error": "Choose an item for the chapter."}), 400
        if not title:
            return jsonify({"error": "Chapter name is required."}), 400
    
        get_owned_timeline_item(item_kind, item_id)
        cursor = db.execute(
            """
            INSERT INTO chapters (user_id, title, description, visibility)
            VALUES (?, ?, ?, ?)
            """,
            (g.user["id"], title, description or None, visibility),
        )
        chapter_id = cursor.lastrowid
        result, _created = create_chapter_item_assignment(db, chapter_id, item_kind, item_id)
        result.update(
            {
                "status": "created",
                "message": f"Created {title} and added this item.",
                "chapter": {"id": chapter_id, "title": title},
            }
        )
        return jsonify(result), 201
    
    
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


    @app.route("/shared/chapters/<int:chapter_id>/export.pdf")
    @birthday_required
    def export_shared_chapter_pdf(chapter_id):
        db = get_db()
        chapter = get_shared_chapter(chapter_id)
        items = build_chapter_pdf_export_items(db, chapter_id, chapter["user_id"])
        return pdf_export_response(
            f"EverTimeline Chapter: {chapter['title']}",
            chapter_pdf_subtitle(chapter, chapter["owner_name"]),
            chapter_pdf_filename(chapter),
            items,
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
        return core.timeline_item_reaction_response(get_db(), item_kind, item_id)
