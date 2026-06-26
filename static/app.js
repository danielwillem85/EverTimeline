document.addEventListener("DOMContentLoaded", () => {
    const notificationBadge = document.querySelector("[data-notification-count]");
    if (notificationBadge) {
        const updateNotificationBadge = (count) => {
            const normalizedCount = Number.isFinite(count) ? count : 0;
            notificationBadge.textContent = String(normalizedCount);
            notificationBadge.classList.toggle("is-empty", normalizedCount === 0);
            notificationBadge.setAttribute(
                "aria-label",
                `${normalizedCount} pending connection requests`
            );
        };

        const refreshNotificationCount = async () => {
            try {
                const response = await fetch("/api/notifications/count", {
                    cache: "no-store",
                    headers: {"Accept": "application/json"},
                });
                const contentType = response.headers.get("content-type") || "";
                if (!response.ok || !contentType.includes("application/json")) {
                    return;
                }
                const payload = await response.json();
                updateNotificationBadge(Number(payload.count) || 0);
            } catch (error) {
                // The next poll will retry; avoid interrupting the current page.
            }
        };

        refreshNotificationCount();
        window.setInterval(refreshNotificationCount, 5000);
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden) {
                refreshNotificationCount();
            }
        });
    }

    const viewAllButton = document.getElementById("view-all-button");
    const viewRandomButton = document.getElementById("view-random-button");
    const allItemsModal = document.getElementById("all-items-modal");

    if (viewAllButton && allItemsModal) {
        const allItemsTitle = document.getElementById("all-items-title");
        const allItemsMeta = document.getElementById("all-items-meta");
        const carouselCard = document.getElementById("carousel-card");
        const carouselStage = document.getElementById("carousel-stage");
        const carouselEmpty = document.getElementById("carousel-empty");
        const carouselFilterPanel = document.getElementById("carousel-filter-panel");
        const carouselFilterForm = document.getElementById("carousel-filter-form");
        const carouselTagInputs = carouselFilterForm ? Array.from(carouselFilterForm.querySelectorAll("input[name='carousel_tags']")) : [];
        const allItemsPanel = allItemsModal.querySelector(".all-items-panel");
        const carouselSpeedControl = allItemsModal.querySelector(".carousel-speed-control");
        const carouselPauseButton = document.getElementById("carousel-pause-button");
        const carouselSpeedDownButton = document.getElementById("carousel-speed-down");
        const carouselSpeedUpButton = document.getElementById("carousel-speed-up");
        const carouselSpeedValue = document.getElementById("carousel-speed-value");
        const speedStepMs = 250;
        const minDisplayMs = 250;
        let allItems = [];
        let carouselIndex = 0;
        let carouselPaused = false;
        let carouselFilterActive = false;
        let pendingRandomize = false;
        let carouselDisplayMs = 1500;
        let carouselTimers = [];

        const shuffleItems = (items) => {
            const shuffled = [...items];
            for (let index = shuffled.length - 1; index > 0; index -= 1) {
                const randomIndex = Math.floor(Math.random() * (index + 1));
                [shuffled[index], shuffled[randomIndex]] = [shuffled[randomIndex], shuffled[index]];
            }
            return shuffled;
        };

        const clearCarouselTimers = () => {
            carouselTimers.forEach((timer) => clearTimeout(timer));
            carouselTimers = [];
        };

        const setCarouselTimer = (callback, delay) => {
            const timer = setTimeout(callback, delay);
            carouselTimers.push(timer);
        };

        const updateCarouselControl = () => {
            if (!carouselPauseButton) {
                return;
            }

            const shouldHide = allItemsModal.hidden || allItems.length === 0 || carouselFilterActive;
            carouselPauseButton.hidden = shouldHide;
            carouselPauseButton.textContent = carouselPaused ? "Resume" : "Pause";
            if (carouselSpeedControl) {
                carouselSpeedControl.hidden = shouldHide;
            }
            if (carouselSpeedDownButton) {
                carouselSpeedDownButton.disabled = carouselDisplayMs <= minDisplayMs;
            }
            if (carouselSpeedValue) {
                carouselSpeedValue.textContent = `${(carouselDisplayMs / 1000).toFixed(2)}s`;
            }
        };

        const closeAllItemsModal = () => {
            clearCarouselTimers();
            allItemsModal.hidden = true;
            carouselCard.classList.remove("is-visible", "is-fading");
            carouselPaused = false;
            carouselFilterActive = false;
            if (carouselStage) {
                carouselStage.hidden = true;
            }
            if (carouselFilterPanel) {
                carouselFilterPanel.hidden = true;
            }
            if (allItemsPanel) {
                allItemsPanel.classList.remove("is-filtering");
            }
            updateCarouselControl();
            document.body.classList.remove("modal-open");
        };

        const renderCarouselItem = (item) => {
            carouselCard.innerHTML = "";
            carouselCard.classList.remove("is-visible", "is-fading");

            const counter = document.createElement("span");
            counter.className = "carousel-counter";
            counter.textContent = `${carouselIndex + 1} of ${allItems.length}`;

            const dateLabel = document.createElement("span");
            dateLabel.className = "carousel-date-label";
            dateLabel.textContent = item.date_label;

            allItemsMeta.replaceChildren(counter, dateLabel);

            if (item.kind === "photo") {
                const layout = document.createElement("div");
                layout.className = "carousel-photo-layout";

                const media = document.createElement("div");
                media.className = "carousel-photo-media";

                const image = document.createElement("img");
                image.className = "carousel-image";
                image.src = item.image_url;
                image.alt = item.title || "Timeline photo";
                media.appendChild(image);

                const messagePanel = document.createElement("aside");
                messagePanel.className = "carousel-message-panel";

                const heading = document.createElement("h3");
                heading.textContent = "Messages";
                messagePanel.appendChild(heading);

                const messages = item.messages || [];
                if (messages.length === 0) {
                    const empty = document.createElement("p");
                    empty.className = "empty-state compact";
                    empty.textContent = "No messages yet.";
                    messagePanel.appendChild(empty);
                } else {
                    const list = document.createElement("div");
                    list.className = "carousel-message-list";
                    messages.forEach((message) => {
                        const article = document.createElement("article");
                        article.className = "message-item";

                        const body = document.createElement("p");
                        body.textContent = message.body;

                        const stamp = document.createElement("time");
                        stamp.textContent = message.created_at;

                        article.append(body, stamp);
                        list.appendChild(article);
                    });
                    messagePanel.appendChild(list);
                }

                layout.append(media, messagePanel);
                carouselCard.appendChild(layout);
            } else {
                const text = document.createElement("div");
                text.className = "carousel-text";
                text.textContent = item.body;
                carouselCard.appendChild(text);
            }
        };

        const scheduleCarouselAdvance = (visibleDelay = carouselDisplayMs) => {
            setCarouselTimer(() => {
                if (carouselPaused || allItemsModal.hidden) {
                    return;
                }
                carouselCard.classList.add("is-fading");
                carouselCard.classList.remove("is-visible");
            }, visibleDelay);

            setCarouselTimer(() => {
                if (carouselPaused || allItemsModal.hidden) {
                    return;
                }
                carouselIndex = (carouselIndex + 1) % allItems.length;
                showCarouselItem();
            }, visibleDelay + 1700);
        };

        const showCarouselItem = () => {
            if (allItems.length === 0 || allItemsModal.hidden) {
                return;
            }

            const item = allItems[carouselIndex];
            renderCarouselItem(item);

            setCarouselTimer(() => {
                carouselCard.classList.add("is-visible");
            }, 50);

            scheduleCarouselAdvance();
        };

        const rescheduleCarouselIfPlaying = () => {
            if (allItemsModal.hidden || carouselPaused || allItems.length === 0) {
                updateCarouselControl();
                return;
            }

            clearCarouselTimers();
            carouselCard.classList.remove("is-fading");
            carouselCard.classList.add("is-visible");
            updateCarouselControl();
            scheduleCarouselAdvance();
        };

        const itemMatchesSelectedTags = (item, selectedTags) => {
            const tags = Array.isArray(item.tags) && item.tags.length > 0 ? item.tags : ["private"];
            return tags.some((tag) => selectedTags.has(tag));
        };

        const getSelectedCarouselTags = () => {
            return new Set(
                carouselTagInputs
                    .filter((input) => input.checked)
                    .map((input) => input.value)
            );
        };

        const openAllItemsModal = ({randomize = false} = {}) => {
            clearCarouselTimers();
            carouselIndex = 0;
            carouselPaused = false;
            carouselFilterActive = true;
            pendingRandomize = randomize;
            allItems = [];
            allItemsModal.hidden = false;
            document.body.classList.add("modal-open");
            carouselCard.innerHTML = "";
            carouselCard.classList.remove("is-visible", "is-fading");
            if (carouselStage) {
                carouselStage.hidden = true;
            }
            carouselEmpty.hidden = true;
            allItemsTitle.textContent = randomize ? "View random" : "View all";
            allItemsMeta.textContent = "";
            if (carouselFilterPanel) {
                carouselFilterPanel.hidden = false;
            }
            if (allItemsPanel) {
                allItemsPanel.classList.add("is-filtering");
            }
            updateCarouselControl();
        };

        const startFilteredCarousel = async () => {
            clearCarouselTimers();
            carouselIndex = 0;
            carouselPaused = false;
            carouselFilterActive = false;
            const selectedTags = getSelectedCarouselTags();
            if (carouselFilterPanel) {
                carouselFilterPanel.hidden = true;
            }
            if (carouselStage) {
                carouselStage.hidden = false;
            }
            if (allItemsPanel) {
                allItemsPanel.classList.remove("is-filtering");
            }
            carouselCard.innerHTML = "";
            carouselCard.classList.remove("is-visible", "is-fading");
            carouselEmpty.hidden = true;
            allItemsMeta.textContent = "Loading...";
            updateCarouselControl();

            const itemsUrl = allItemsModal.dataset.itemsUrl || "/api/timeline-items";
            const response = await fetch(itemsUrl);
            const fetchedItems = response.ok ? await response.json() : [];
            const filteredItems = fetchedItems.filter((item) => itemMatchesSelectedTags(item, selectedTags));
            allItems = pendingRandomize ? shuffleItems(filteredItems) : filteredItems;

            if (allItems.length === 0) {
                allItemsMeta.textContent = "";
                carouselEmpty.hidden = false;
                updateCarouselControl();
                return;
            }

            carouselEmpty.hidden = true;
            updateCarouselControl();
            showCarouselItem();
        };

        const pauseCarousel = () => {
            clearCarouselTimers();
            carouselPaused = true;
            carouselCard.classList.remove("is-fading");
            carouselCard.classList.add("is-visible");
            updateCarouselControl();
        };

        const resumeCarousel = () => {
            carouselPaused = false;
            carouselCard.classList.remove("is-fading");
            carouselCard.classList.add("is-visible");
            updateCarouselControl();
            scheduleCarouselAdvance();
        };

        const changeCarouselSpeed = (deltaMs) => {
            carouselDisplayMs = Math.max(minDisplayMs, carouselDisplayMs + deltaMs);
            rescheduleCarouselIfPlaying();
        };

        viewAllButton.addEventListener("click", () => openAllItemsModal());
        if (viewRandomButton) {
            viewRandomButton.addEventListener("click", () => openAllItemsModal({randomize: true}));
        }
        if (carouselFilterForm) {
            carouselFilterForm.addEventListener("submit", (event) => {
                event.preventDefault();
                startFilteredCarousel();
            });
        }
        if (carouselPauseButton) {
            carouselPauseButton.addEventListener("click", () => {
                if (carouselPaused) {
                    resumeCarousel();
                } else {
                    pauseCarousel();
                }
            });
        }
        if (carouselSpeedDownButton) {
            carouselSpeedDownButton.addEventListener("click", () => changeCarouselSpeed(-speedStepMs));
        }
        if (carouselSpeedUpButton) {
            carouselSpeedUpButton.addEventListener("click", () => changeCarouselSpeed(speedStepMs));
        }

        allItemsModal.querySelectorAll("[data-close-all-items]").forEach((button) => {
            button.addEventListener("click", closeAllItemsModal);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !allItemsModal.hidden) {
                closeAllItemsModal();
            }
        });
    }

    const connectionModal = document.getElementById("connection-modal");
    if (connectionModal) {
        const connectionRecipientInput = document.getElementById("connection-recipient-id");
        const connectionTarget = document.getElementById("connection-modal-target");
        const connectionRelationInputs = Array.from(connectionModal.querySelectorAll("input[name='relation']"));

        const closeConnectionModal = () => {
            connectionModal.hidden = true;
            document.body.classList.remove("modal-open");
        };

        document.querySelectorAll(".connect-open-button").forEach((button) => {
            button.addEventListener("click", () => {
                connectionRecipientInput.value = button.dataset.recipientId || "";
                connectionTarget.textContent = button.dataset.recipientName || "";
                connectionRelationInputs.forEach((input) => {
                    input.checked = input.value === "friend";
                });
                connectionModal.hidden = false;
                document.body.classList.add("modal-open");
            });
        });

        connectionModal.querySelectorAll("[data-close-connection-modal]").forEach((button) => {
            button.addEventListener("click", closeConnectionModal);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !connectionModal.hidden) {
                closeConnectionModal();
            }
        });
    }

    const photoModal = document.getElementById("photo-modal");
    const textModal = document.getElementById("text-modal");
    if (!photoModal || !textModal) {
        return;
    }

    const modalImage = document.getElementById("modal-image");
    const modalDate = document.getElementById("modal-date");
    const messageList = document.getElementById("message-list");
    const messageForm = document.getElementById("message-form");
    const messageInput = messageForm.querySelector("textarea");
    const deletePhotoButton = document.getElementById("delete-photo-button");
    const photoTagsForm = document.getElementById("photo-tags-form");
    const photoTagInputs = Array.from(photoTagsForm.querySelectorAll("input[name='tags']"));

    const textModalDate = document.getElementById("text-modal-date");
    const textEntryView = document.getElementById("text-entry-view");
    const textEntryEditForm = document.getElementById("text-entry-edit-form");
    const textEntryEditBody = textEntryEditForm.querySelector("textarea");
    const textEntryEditDate = textEntryEditForm.querySelector("input[name='entry_date']");
    const textEntryEditTagInputs = Array.from(textEntryEditForm.querySelectorAll("input[name='tags']"));
    const editTextButton = document.getElementById("edit-text-button");
    const deleteTextButton = document.getElementById("delete-text-button");
    const cancelTextEditButton = document.getElementById("cancel-text-edit-button");

    let activePhotoId = null;
    let activePhotoThumbnail = null;
    let activeTextEntryId = null;
    let activeTextThumbnail = null;
    let activeTextEntry = null;

    const setModalOpenState = () => {
        const isOpen = !photoModal.hidden || !textModal.hidden;
        document.body.classList.toggle("modal-open", isOpen);
    };

    const showEmptyStateIfNeeded = (grid) => {
        if (!grid || grid.querySelector(".entry-thumb")) {
            return;
        }

        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "No entries yet.";
        grid.appendChild(empty);
    };

    const removeActiveThumbnail = (thumbnail) => {
        if (!thumbnail) {
            return;
        }

        const photoGrid = thumbnail.closest(".photo-grid");
        thumbnail.remove();
        showEmptyStateIfNeeded(photoGrid);
    };

    const selectedTagValue = (inputs) => {
        const checked = inputs.find((input) => input.checked);
        return checked ? checked.value : "private";
    };

    const setSelectedTagValue = (inputs, value) => {
        const allowedValues = inputs.map((input) => input.value);
        const candidates = String(value || "")
            .split(/[;,]/)
            .map((tag) => tag.trim().toLowerCase())
            .filter(Boolean);
        const selected = candidates.find((tag) => allowedValues.includes(tag)) || "private";
        inputs.forEach((input) => {
            input.checked = input.value === selected;
        });
    };

    const renderTagChips = (tags) => {
        const list = document.createElement("div");
        list.className = "tag-list thumb-tag-list";
        tags.forEach((tag) => {
            const chip = document.createElement("span");
            chip.className = "tag-chip";
            chip.textContent = tag;
            list.appendChild(chip);
        });
        return list;
    };

    const updatePhotoThumbnailTags = (tags, tagsText) => {
        if (!activePhotoThumbnail) {
            return;
        }

        activePhotoThumbnail.dataset.photoTags = tagsText;
        const existing = activePhotoThumbnail.querySelector(".thumb-tags");
        if (!tagsText) {
            if (existing) {
                existing.remove();
            }
            return;
        }

        const tagBadge = existing || document.createElement("span");
        tagBadge.className = "thumb-tags";
        tagBadge.textContent = tagsText;
        if (!existing) {
            activePhotoThumbnail.appendChild(tagBadge);
        }
    };

    const updateTextThumbnailTags = (tags, tagsText) => {
        if (!activeTextThumbnail) {
            return;
        }

        activeTextThumbnail.dataset.entryTags = tagsText;
        const content = activeTextThumbnail.querySelector(".text-thumb-content");
        const existing = activeTextThumbnail.querySelector(".thumb-tag-list");
        if (existing) {
            existing.remove();
        }
        if (content && tags.length > 0) {
            content.appendChild(renderTagChips(tags));
        }
    };

    const renderMessages = (messages) => {
        messageList.innerHTML = "";
        if (messages.length === 0) {
            const empty = document.createElement("p");
            empty.className = "empty-state compact";
            empty.textContent = "No messages yet.";
            messageList.appendChild(empty);
            return;
        }

        messages.forEach((message) => {
            const item = document.createElement("article");
            item.className = "message-item";

            const body = document.createElement("p");
            body.textContent = message.body;

            const stamp = document.createElement("time");
            stamp.textContent = message.created_at;

            item.append(body, stamp);
            messageList.appendChild(item);
        });
    };

    const loadMessages = async () => {
        const response = await fetch(`/api/photo/${activePhotoId}/messages`);
        if (!response.ok) {
            renderMessages([]);
            return;
        }
        renderMessages(await response.json());
    };

    const openPhotoModal = async (button) => {
        activePhotoId = button.dataset.photoId;
        activePhotoThumbnail = button;
        modalImage.src = button.dataset.fullSrc;
        modalDate.textContent = button.dataset.photoDate || "";
        setSelectedTagValue(photoTagInputs, button.dataset.photoTags || "private");
        messageInput.value = "";
        photoModal.hidden = false;
        setModalOpenState();
        await loadMessages();
    };

    const closePhotoModal = () => {
        photoModal.hidden = true;
        modalImage.removeAttribute("src");
        activePhotoId = null;
        activePhotoThumbnail = null;
        setModalOpenState();
    };

    const renderTextEntry = (entry) => {
        activeTextEntry = entry;
        textModalDate.textContent = entry.entry_date || "";
        textEntryView.textContent = entry.body;
        textEntryEditBody.value = entry.body;
        textEntryEditDate.value = entry.entry_date || "";
        setSelectedTagValue(textEntryEditTagInputs, entry.tags_text || "private");
    };

    const updateTextThumbnail = (entry) => {
        if (!activeTextThumbnail) {
            return;
        }

        const preview = activeTextThumbnail.querySelector(".text-thumb-preview");
        if (preview) {
            preview.textContent = entry.body;
        }

        let dateBadge = activeTextThumbnail.querySelector(".thumb-date");
        if (entry.entry_date) {
            if (!dateBadge) {
                dateBadge = document.createElement("span");
                dateBadge.className = "thumb-date";
                activeTextThumbnail.appendChild(dateBadge);
            }
            dateBadge.textContent = entry.entry_date;
        } else if (dateBadge) {
            dateBadge.remove();
        }

        const label = entry.entry_date ? `Text entry from ${entry.entry_date}` : "Text entry";
        activeTextThumbnail.setAttribute("aria-label", label);
        updateTextThumbnailTags(entry.tags || [], entry.tags_text || "");
    };

    const showTextView = () => {
        textEntryView.hidden = false;
        textEntryEditForm.hidden = true;
    };

    const showTextEditForm = () => {
        textEntryView.hidden = true;
        textEntryEditForm.hidden = false;
        textEntryEditBody.focus();
    };

    const openTextModal = async (button) => {
        activeTextEntryId = button.dataset.entryId;
        activeTextThumbnail = button;

        const response = await fetch(`/api/text-entry/${activeTextEntryId}`);
        if (!response.ok) {
            return;
        }

        renderTextEntry(await response.json());
        showTextView();
        textModal.hidden = false;
        setModalOpenState();
    };

    const closeTextModal = () => {
        textModal.hidden = true;
        activeTextEntryId = null;
        activeTextThumbnail = null;
        activeTextEntry = null;
        showTextView();
        setModalOpenState();
    };

    document.querySelectorAll(".photo-thumb").forEach((button) => {
        button.addEventListener("click", () => openPhotoModal(button));
    });

    document.querySelectorAll(".text-thumb").forEach((button) => {
        button.addEventListener("click", () => openTextModal(button));
    });

    photoModal.querySelectorAll("[data-close-modal]").forEach((button) => {
        button.addEventListener("click", closePhotoModal);
    });

    textModal.querySelectorAll("[data-close-text-modal]").forEach((button) => {
        button.addEventListener("click", closeTextModal);
    });

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") {
            return;
        }

        if (!photoModal.hidden) {
            closePhotoModal();
        }
        if (!textModal.hidden) {
            closeTextModal();
        }
    });

    messageForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const body = messageInput.value.trim();
        if (!body || activePhotoId === null) {
            return;
        }

        const response = await fetch(`/api/photo/${activePhotoId}/messages`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({body}),
        });

        if (response.ok) {
            messageInput.value = "";
            await loadMessages();
        }
    });

    photoTagsForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (activePhotoId === null) {
            return;
        }

        const response = await fetch(`/api/photo/${activePhotoId}/tags`, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({tags: selectedTagValue(photoTagInputs)}),
        });

        if (response.ok) {
            const payload = await response.json();
            setSelectedTagValue(photoTagInputs, payload.tags_text);
            updatePhotoThumbnailTags(payload.tags, payload.tags_text);
        }
    });

    deletePhotoButton.addEventListener("click", async () => {
        if (activePhotoId === null) {
            return;
        }

        deletePhotoButton.disabled = true;
        const response = await fetch(`/api/photo/${activePhotoId}`, {
            method: "DELETE",
        });

        if (response.ok) {
            removeActiveThumbnail(activePhotoThumbnail);
            closePhotoModal();
        }

        deletePhotoButton.disabled = false;
    });

    editTextButton.addEventListener("click", () => {
        if (!activeTextEntry) {
            return;
        }
        showTextEditForm();
    });

    cancelTextEditButton.addEventListener("click", () => {
        if (activeTextEntry) {
            renderTextEntry(activeTextEntry);
        }
        showTextView();
    });

    textEntryEditForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (activeTextEntryId === null) {
            return;
        }

        const body = textEntryEditBody.value;
        if (!body.trim()) {
            return;
        }

        const response = await fetch(`/api/text-entry/${activeTextEntryId}`, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                body,
                entry_date: textEntryEditDate.value,
                tags: selectedTagValue(textEntryEditTagInputs),
            }),
        });

        if (response.ok) {
            const entry = await response.json();
            renderTextEntry(entry);
            updateTextThumbnail(entry);
            showTextView();
        }
    });

    deleteTextButton.addEventListener("click", async () => {
        if (activeTextEntryId === null) {
            return;
        }

        deleteTextButton.disabled = true;
        const response = await fetch(`/api/text-entry/${activeTextEntryId}`, {
            method: "DELETE",
        });

        if (response.ok) {
            removeActiveThumbnail(activeTextThumbnail);
            closeTextModal();
        }

        deleteTextButton.disabled = false;
    });
});
