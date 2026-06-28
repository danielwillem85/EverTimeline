document.addEventListener("DOMContentLoaded", () => {
    const privacyLabels = {
        private: "Only you",
        family: "Family",
        friends: "Friends",
        public: "All connections",
    };
    const privacyHelp = {
        private: "Only your account can see this item.",
        family: "Family connections can see this item.",
        friends: "Friend and family connections can see this item.",
        public: "All accepted connections can see this item.",
    };
    const privacyLabelFromTag = (tag) => privacyLabels[tag] || privacyLabels.private;
    const privacyHelpFromTag = (tag) => privacyHelp[tag] || privacyHelp.private;
    const setPrivacySummary = (summary, label, help) => {
        if (!summary) {
            return;
        }
        summary.textContent = `Visible to: ${label || privacyLabels.private}`;
        summary.title = help || "";
    };
    const setPrivacyBadge = (container, label, help) => {
        if (!container) {
            return;
        }
        const normalizedLabel = label || privacyLabels.private;
        let badge = container.querySelector(".privacy-badge");
        if (!badge) {
            badge = document.createElement("span");
            badge.className = "privacy-badge";
            const content = container.querySelector(".text-thumb-content");
            if (content) {
                content.prepend(badge);
            } else {
                container.appendChild(badge);
            }
        }
        badge.textContent = `Visible: ${normalizedLabel}`;
        badge.title = help || "";
        container.dataset.privacyLabel = normalizedLabel;
        container.dataset.privacyHelp = help || "";
    };

    const syncModalOpenState = () => {
        const hasOpenModal = Array.from(document.querySelectorAll(".modal")).some((modal) => {
            return !modal.hidden;
        });
        document.body.classList.toggle("modal-open", hasOpenModal);
    };

    const confirmationModal = document.getElementById("confirmation-modal");
    const confirmationTitle = document.getElementById("confirmation-modal-title");
    const confirmationMessage = document.getElementById("confirmation-modal-message");
    const confirmationAcceptButton = confirmationModal ? confirmationModal.querySelector("[data-confirm-accept]") : null;
    const confirmationCancelButtons = confirmationModal ? Array.from(confirmationModal.querySelectorAll("[data-confirm-cancel]")) : [];
    let confirmationResolve = null;
    let confirmationTrigger = null;

    const closeConfirmation = (confirmed) => {
        if (!confirmationModal || !confirmationResolve) {
            return;
        }

        const resolve = confirmationResolve;
        const trigger = confirmationTrigger;
        confirmationResolve = null;
        confirmationTrigger = null;
        confirmationModal.hidden = true;
        syncModalOpenState();
        resolve(confirmed);

        if (trigger && typeof trigger.focus === "function") {
            trigger.focus({preventScroll: true});
        }
    };

    const requestConfirmation = ({
        title = "Confirm action",
        message = "This action needs confirmation.",
        confirmLabel = "Confirm",
        danger = false,
    } = {}) => {
        if (!confirmationModal || !confirmationTitle || !confirmationMessage || !confirmationAcceptButton) {
            return Promise.resolve(true);
        }

        if (confirmationResolve) {
            closeConfirmation(false);
        }

        confirmationTitle.textContent = title;
        confirmationMessage.textContent = message;
        confirmationAcceptButton.textContent = confirmLabel;
        confirmationAcceptButton.className = `button ${danger ? "danger" : "primary"}`;
        confirmationTrigger = document.activeElement;
        confirmationModal.hidden = false;
        syncModalOpenState();
        confirmationAcceptButton.focus();

        return new Promise((resolve) => {
            confirmationResolve = resolve;
        });
    };

    if (confirmationModal) {
        confirmationAcceptButton.addEventListener("click", () => closeConfirmation(true));
        confirmationCancelButtons.forEach((button) => {
            button.addEventListener("click", () => closeConfirmation(false));
        });

        document.addEventListener("keydown", (event) => {
            if (event.key !== "Escape" || confirmationModal.hidden) {
                return;
            }

            event.preventDefault();
            event.stopImmediatePropagation();
            closeConfirmation(false);
        });
    }

    document.querySelectorAll("form[data-confirm]").forEach((form) => {
        form.addEventListener("submit", async (event) => {
            if (form.dataset.confirmed === "true") {
                delete form.dataset.confirmed;
                return;
            }

            event.preventDefault();
            const confirmed = await requestConfirmation({
                title: form.dataset.confirmTitle || "Confirm action",
                message: form.dataset.confirmMessage || "This action needs confirmation.",
                confirmLabel: form.dataset.confirmAction || "Confirm",
                danger: form.dataset.confirmDanger === "true",
            });
            if (!confirmed) {
                return;
            }

            form.dataset.confirmed = "true";
            HTMLFormElement.prototype.submit.call(form);
        });
    });

    const birthdayConfirmInput = document.querySelector("[data-birthday-confirm-input]");
    const birthdayConfirmButton = document.querySelector("[data-birthday-confirm-button]");
    if (birthdayConfirmInput && birthdayConfirmButton) {
        const updateBirthdayConfirmButton = () => {
            birthdayConfirmButton.disabled = birthdayConfirmInput.value.trim().toLowerCase() !== "proceed";
        };
        birthdayConfirmInput.addEventListener("input", updateBirthdayConfirmButton);
        updateBirthdayConfirmButton();
    }

    const birthdayReviewInput = document.querySelector("[data-birthday-review-input]");
    const birthdayReviewButton = document.querySelector("[data-birthday-review-button]");
    if (birthdayReviewInput && birthdayReviewButton) {
        const currentBirthday = birthdayReviewInput.dataset.currentBirthday || "";
        const updateBirthdayReviewButton = () => {
            birthdayReviewButton.disabled = Boolean(currentBirthday) && birthdayReviewInput.value === currentBirthday;
        };
        birthdayReviewInput.addEventListener("input", updateBirthdayReviewButton);
        birthdayReviewInput.addEventListener("change", updateBirthdayReviewButton);
        updateBirthdayReviewButton();
    }

    const localSettingsMenus = Array.from(document.querySelectorAll(".local-settings-menu"));
    if (localSettingsMenus.length > 0) {
        const closeLocalSettingsMenu = (menu) => {
            menu.open = false;
        };

        localSettingsMenus.forEach((menu) => {
            const closeButton = menu.querySelector("[data-close-local-settings]");
            const summary = menu.querySelector("summary");

            if (closeButton) {
                closeButton.addEventListener("click", (event) => {
                    event.preventDefault();
                    closeLocalSettingsMenu(menu);
                    if (summary) {
                        summary.focus();
                    }
                });
            }
        });

        document.addEventListener("click", (event) => {
            localSettingsMenus.forEach((menu) => {
                if (menu.open && !menu.contains(event.target)) {
                    closeLocalSettingsMenu(menu);
                }
            });
        });

        document.addEventListener("keydown", (event) => {
            if (event.key !== "Escape") {
                return;
            }
            localSettingsMenus.forEach(closeLocalSettingsMenu);
        });
    }

    const notificationBadge = document.querySelector("[data-notification-count]");
    if (notificationBadge) {
        const updateNotificationBadge = (count) => {
            const normalizedCount = Number.isFinite(count) ? count : 0;
            notificationBadge.textContent = String(normalizedCount);
            notificationBadge.classList.toggle("is-empty", normalizedCount === 0);
            notificationBadge.setAttribute(
                "aria-label",
                `${normalizedCount} unread notifications`
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
        const carouselStepControls = document.getElementById("carousel-step-controls");
        const carouselStepLeftButton = document.getElementById("carousel-step-left");
        const carouselStepRightButton = document.getElementById("carousel-step-right");
        const carouselSpeedDownButton = document.getElementById("carousel-speed-down");
        const carouselSpeedUpButton = document.getElementById("carousel-speed-up");
        const carouselSpeedValue = document.getElementById("carousel-speed-value");
        const skipCarouselTagFilter = allItemsModal.dataset.skipTagFilter === "true";
        const viewAllTitle = viewAllButton.dataset.carouselTitle || "View all";
        const viewRandomTitle = viewRandomButton ? viewRandomButton.dataset.carouselTitle || "View random" : "View random";
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
            if (carouselStepControls) {
                carouselStepControls.hidden = shouldHide || !carouselPaused || allItems.length <= 1;
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
            carouselCard.classList.remove("is-visible", "is-fading", "is-shifting");
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

        const renderCarouselMessages = (list, messages) => {
            list.innerHTML = "";
            if (messages.length === 0) {
                const empty = document.createElement("p");
                empty.className = "empty-state compact";
                empty.textContent = "No messages yet.";
                list.appendChild(empty);
                return;
            }

            messages.forEach((message) => {
                const article = document.createElement("article");
                article.className = "message-item";

                if (message.author_name) {
                    const author = document.createElement("strong");
                    author.className = "message-author";
                    author.textContent = message.author_name;
                    article.appendChild(author);
                }

                const body = document.createElement("p");
                body.textContent = message.body;

                const stamp = document.createElement("time");
                stamp.textContent = message.created_at;

                article.append(body, stamp);
                list.appendChild(article);
            });
        };

        const renderCarouselMessagePanel = (item) => {
            const messagePanel = document.createElement("aside");
            messagePanel.className = "carousel-message-panel";

            const heading = document.createElement("h3");
            heading.textContent = "Messages";

            const list = document.createElement("div");
            list.className = "carousel-message-list";
            renderCarouselMessages(list, item.messages || []);

            const form = document.createElement("form");
            form.className = "carousel-message-form";
            form.dataset.canMessage = item.can_message && item.messages_url ? "true" : "false";
            form.hidden = !(carouselPaused && item.can_message && item.messages_url);

            const textarea = document.createElement("textarea");
            textarea.name = "body";
            textarea.rows = 3;
            textarea.placeholder = "Add a message";
            textarea.setAttribute("aria-label", "Message");

            const error = document.createElement("p");
            error.className = "form-error carousel-message-error";
            error.hidden = true;

            const submit = document.createElement("button");
            submit.className = "button primary small";
            submit.type = "submit";
            submit.textContent = "Save";

            form.append(textarea, submit, error);
            form.addEventListener("submit", async (event) => {
                event.preventDefault();
                error.hidden = true;

                const body = textarea.value.trim();
                if (!carouselPaused || !item.can_message || !item.messages_url || !body) {
                    return;
                }

                submit.disabled = true;
                const response = await fetch(item.messages_url, {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({body}),
                });
                submit.disabled = false;

                if (!response.ok) {
                    error.textContent = "Message could not be saved.";
                    error.hidden = false;
                    return;
                }

                const message = await response.json();
                item.messages = [...(item.messages || []), message];
                textarea.value = "";
                renderCarouselMessages(list, item.messages);
            });

            messagePanel.append(heading, list, form);
            return messagePanel;
        };

        const updateCarouselMessageForm = () => {
            carouselCard.querySelectorAll(".carousel-message-form").forEach((form) => {
                const canMessage = form.dataset.canMessage === "true";
                form.hidden = !(carouselPaused && canMessage);
                form.querySelectorAll("textarea, button").forEach((control) => {
                    control.disabled = !(carouselPaused && canMessage);
                });
            });
        };

        const visibleCarouselEntries = () => {
            const visibleCount = Math.min(3, allItems.length);
            return Array.from({length: visibleCount}, (_, slotIndex) => {
                const itemIndex = (carouselIndex + slotIndex) % allItems.length;
                return {
                    item: allItems[itemIndex],
                    itemIndex,
                    slotIndex,
                };
            });
        };

        const renderCarouselPanel = ({item, itemIndex, slotIndex}) => {
            const article = document.createElement("article");
            article.className = "carousel-window-item";
            if (slotIndex === 0) {
                article.classList.add("is-current");
            }

            const meta = document.createElement("div");
            meta.className = "carousel-item-meta";

            const counter = document.createElement("span");
            counter.className = "carousel-item-counter";
            counter.textContent = `${itemIndex + 1} of ${allItems.length}`;

            const dateLabel = document.createElement("span");
            dateLabel.className = "carousel-item-date";
            dateLabel.textContent = item.date_label;

            meta.append(counter, dateLabel);
            if (item.privacy_label) {
                const privacy = document.createElement("span");
                privacy.className = "carousel-item-privacy";
                privacy.textContent = item.privacy_label;
                privacy.title = item.privacy_help || "";
                meta.appendChild(privacy);
            }

            const media = document.createElement("div");
            media.className = "carousel-window-media";

            if (item.kind === "photo") {
                const image = document.createElement("img");
                image.className = "carousel-image";
                image.src = item.image_url;
                image.alt = item.title || "Timeline photo";
                media.appendChild(image);
            } else {
                const text = document.createElement("div");
                text.className = "carousel-text";
                text.textContent = item.body;
                media.appendChild(text);
            }

            article.append(meta, media, renderCarouselMessagePanel(item));
            return article;
        };

        const renderCarouselWindow = () => {
            carouselCard.innerHTML = "";
            carouselCard.classList.remove("is-visible", "is-fading", "is-shifting");

            const visibleEntries = visibleCarouselEntries();
            const counter = document.createElement("span");
            counter.className = "carousel-counter";
            counter.textContent = `${visibleEntries.map((entry) => entry.itemIndex + 1).join(", ")} of ${allItems.length}`;
            allItemsMeta.replaceChildren(counter);

            const window = document.createElement("div");
            window.className = "carousel-window";
            visibleEntries.forEach((entry) => {
                window.appendChild(renderCarouselPanel(entry));
            });
            carouselCard.appendChild(window);
        };

        const scheduleCarouselAdvance = (visibleDelay = carouselDisplayMs) => {
            setCarouselTimer(() => {
                if (carouselPaused || allItemsModal.hidden) {
                    return;
                }
                carouselCard.classList.add("is-shifting");
            }, visibleDelay);

            setCarouselTimer(() => {
                if (carouselPaused || allItemsModal.hidden) {
                    return;
                }
                carouselIndex = (carouselIndex + 1) % allItems.length;
                showCarouselItem();
            }, visibleDelay + 450);
        };

        const showCarouselItem = () => {
            if (allItems.length === 0 || allItemsModal.hidden) {
                return;
            }

            renderCarouselWindow();

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
            carouselCard.classList.remove("is-fading", "is-shifting");
            renderCarouselWindow();
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
            carouselFilterActive = !skipCarouselTagFilter;
            pendingRandomize = randomize;
            allItems = [];
            allItemsModal.hidden = false;
            document.body.classList.add("modal-open");
            carouselCard.innerHTML = "";
            carouselCard.classList.remove("is-visible", "is-fading", "is-shifting");
            if (carouselStage) {
                carouselStage.hidden = true;
            }
            carouselEmpty.hidden = true;
            allItemsTitle.textContent = randomize ? viewRandomTitle : viewAllTitle;
            allItemsMeta.textContent = "";
            if (carouselFilterPanel) {
                carouselFilterPanel.hidden = skipCarouselTagFilter;
            }
            if (allItemsPanel) {
                allItemsPanel.classList.toggle("is-filtering", !skipCarouselTagFilter);
            }
            updateCarouselControl();
            if (skipCarouselTagFilter) {
                startFilteredCarousel({skipTagFilter: true});
            }
        };

        const startFilteredCarousel = async ({skipTagFilter = skipCarouselTagFilter} = {}) => {
            clearCarouselTimers();
            carouselIndex = 0;
            carouselPaused = false;
            carouselFilterActive = false;
            const selectedTags = skipTagFilter ? null : getSelectedCarouselTags();
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
            const filteredItems = skipTagFilter
                ? fetchedItems
                : fetchedItems.filter((item) => itemMatchesSelectedTags(item, selectedTags));
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
            carouselCard.classList.remove("is-fading", "is-shifting");
            carouselCard.classList.add("is-visible");
            updateCarouselControl();
            updateCarouselMessageForm();
        };

        const resumeCarousel = () => {
            carouselPaused = false;
            carouselCard.classList.remove("is-fading", "is-shifting");
            carouselCard.classList.add("is-visible");
            updateCarouselControl();
            updateCarouselMessageForm();
            scheduleCarouselAdvance();
        };

        const changeCarouselSpeed = (deltaMs) => {
            carouselDisplayMs = Math.max(minDisplayMs, carouselDisplayMs + deltaMs);
            rescheduleCarouselIfPlaying();
        };

        const stepPausedCarousel = (delta) => {
            if (!carouselPaused || allItemsModal.hidden || allItems.length <= 1) {
                return;
            }

            clearCarouselTimers();
            carouselIndex = (carouselIndex + delta + allItems.length) % allItems.length;
            renderCarouselWindow();
            carouselCard.classList.add("is-visible");
            updateCarouselControl();
            updateCarouselMessageForm();
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
        if (carouselStepLeftButton) {
            carouselStepLeftButton.addEventListener("click", () => stepPausedCarousel(-1));
        }
        if (carouselStepRightButton) {
            carouselStepRightButton.addEventListener("click", () => stepPausedCarousel(1));
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

    const updateReactionBars = (kind, itemId, payload) => {
        document.querySelectorAll("[data-reaction-bar]").forEach((bar) => {
            if (bar.dataset.reactionKind !== kind || bar.dataset.reactionId !== String(itemId)) {
                return;
            }

            const userReaction = payload.user_reaction || "";
            bar.dataset.userReaction = userReaction;
            bar.querySelectorAll("[data-reaction-button]").forEach((button) => {
                const reactionValue = button.dataset.reactionValue;
                const isActive = reactionValue === userReaction;
                button.classList.toggle("is-active", isActive);
                button.setAttribute("aria-pressed", isActive ? "true" : "false");
            });
            bar.querySelectorAll("[data-reaction-count]").forEach((count) => {
                const reactionValue = count.dataset.reactionCount;
                count.textContent = String(payload[`${reactionValue}_count`] || 0);
            });
        });
    };

    document.querySelectorAll("[data-reaction-button]").forEach((button) => {
        button.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();

            const bar = button.closest("[data-reaction-bar]");
            if (!bar || !bar.dataset.reactionUrl) {
                return;
            }

            const reactionValue = button.dataset.reactionValue;
            const alreadySelected = bar.dataset.userReaction === reactionValue;
            const response = await fetch(bar.dataset.reactionUrl, {
                method: alreadySelected ? "DELETE" : "PUT",
                headers: {"Content-Type": "application/json"},
                body: alreadySelected ? null : JSON.stringify({reaction: reactionValue}),
            });
            if (!response.ok) {
                return;
            }

            const payload = await response.json();
            updateReactionBars(bar.dataset.reactionKind, bar.dataset.reactionId, payload);
        });
    });

    const homePhotoModal = document.getElementById("home-photo-modal");
    if (homePhotoModal) {
        const homePhotoImage = document.getElementById("home-photo-modal-image");
        const homePhotoOwner = document.getElementById("home-photo-modal-owner");
        const homePhotoDate = document.getElementById("home-photo-modal-date");
        const homePhotoMessageList = document.getElementById("home-photo-message-list");

        const renderHomePhotoMessages = (messages) => {
            homePhotoMessageList.innerHTML = "";
            if (messages.length === 0) {
                const empty = document.createElement("p");
                empty.className = "empty-state compact";
                empty.textContent = "No messages yet.";
                homePhotoMessageList.appendChild(empty);
                return;
            }

            messages.forEach((message) => {
                const item = document.createElement("article");
                item.className = "message-item";

                if (message.author_name) {
                    const author = document.createElement("strong");
                    author.className = "message-author";
                    author.textContent = message.author_name;
                    item.appendChild(author);
                }

                const body = document.createElement("p");
                body.textContent = message.body;

                const stamp = document.createElement("time");
                stamp.textContent = message.created_at;

                item.append(body, stamp);
                homePhotoMessageList.appendChild(item);
            });
        };

        const closeHomePhotoModal = () => {
            homePhotoModal.hidden = true;
            homePhotoImage.removeAttribute("src");
            homePhotoMessageList.innerHTML = "";
            document.body.classList.remove("modal-open");
        };

        const openHomePhotoModal = async (button) => {
            homePhotoImage.src = button.dataset.fullSrc;
            homePhotoImage.alt = button.dataset.photoTitle || "Selected public photo";
            homePhotoOwner.textContent = button.dataset.photoOwner || "";
            homePhotoDate.textContent = button.dataset.photoDate || "";
            renderHomePhotoMessages([]);
            homePhotoModal.hidden = false;
            document.body.classList.add("modal-open");

            try {
                const response = await fetch(button.dataset.messagesUrl);
                renderHomePhotoMessages(response.ok ? await response.json() : []);
            } catch (error) {
                renderHomePhotoMessages([]);
            }
        };

        document.querySelectorAll(".public-photo-card").forEach((button) => {
            button.addEventListener("click", () => openHomePhotoModal(button));
        });

        homePhotoModal.querySelectorAll("[data-close-home-photo-modal]").forEach((button) => {
            button.addEventListener("click", closeHomePhotoModal);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !homePhotoModal.hidden) {
                closeHomePhotoModal();
            }
        });
    }

    const readonlyPhotoModal = document.getElementById("readonly-photo-modal");
    const readonlyTextModal = document.getElementById("readonly-text-modal");
    if (readonlyPhotoModal && readonlyTextModal) {
        const readonlyModalImage = document.getElementById("readonly-modal-image");
        const readonlyPhotoDate = document.getElementById("readonly-photo-modal-date");
        const readonlyMessageList = document.getElementById("readonly-message-list");
        const readonlyTextDate = document.getElementById("readonly-text-modal-date");
        const readonlyPhotoPrivacySummary = document.getElementById("readonly-photo-privacy-summary");
        const readonlyTextPrivacySummary = document.getElementById("readonly-text-privacy-summary");
        const readonlyTextEntryView = document.getElementById("readonly-text-entry-view");

        const setReadonlyModalOpenState = () => {
            const isOpen = !readonlyPhotoModal.hidden || !readonlyTextModal.hidden;
            document.body.classList.toggle("modal-open", isOpen);
        };

        const renderReadonlyMessages = (messages) => {
            readonlyMessageList.innerHTML = "";
            if (messages.length === 0) {
                const empty = document.createElement("p");
                empty.className = "empty-state compact";
                empty.textContent = "No messages yet.";
                readonlyMessageList.appendChild(empty);
                return;
            }

            messages.forEach((message) => {
                const item = document.createElement("article");
                item.className = "message-item";

                if (message.author_name) {
                    const author = document.createElement("strong");
                    author.className = "message-author";
                    author.textContent = message.author_name;
                    item.appendChild(author);
                }

                const body = document.createElement("p");
                body.textContent = message.body;

                const stamp = document.createElement("time");
                stamp.textContent = message.created_at;

                item.append(body, stamp);
                readonlyMessageList.appendChild(item);
            });
        };

        const openReadonlyPhotoModal = async (button) => {
            readonlyModalImage.src = button.dataset.fullSrc;
            readonlyPhotoDate.textContent = button.dataset.photoDate || "";
            setPrivacySummary(
                readonlyPhotoPrivacySummary,
                button.dataset.privacyLabel,
                button.dataset.privacyHelp
            );
            readonlyPhotoModal.hidden = false;
            setReadonlyModalOpenState();

            try {
                const response = await fetch(button.dataset.messagesUrl);
                renderReadonlyMessages(response.ok ? await response.json() : []);
            } catch (error) {
                renderReadonlyMessages([]);
            }
        };

        const closeReadonlyPhotoModal = () => {
            readonlyPhotoModal.hidden = true;
            readonlyModalImage.removeAttribute("src");
            setReadonlyModalOpenState();
        };

        const openReadonlyTextModal = (button) => {
            readonlyTextDate.textContent = button.dataset.entryDate || "";
            setPrivacySummary(
                readonlyTextPrivacySummary,
                button.dataset.privacyLabel,
                button.dataset.privacyHelp
            );
            readonlyTextEntryView.textContent = button.dataset.entryBody || "";
            readonlyTextModal.hidden = false;
            setReadonlyModalOpenState();
        };

        const closeReadonlyTextModal = () => {
            readonlyTextModal.hidden = true;
            setReadonlyModalOpenState();
        };

        document.querySelectorAll(".readonly-photo-thumb").forEach((button) => {
            button.addEventListener("click", () => openReadonlyPhotoModal(button));
        });

        document.querySelectorAll(".readonly-text-thumb").forEach((button) => {
            button.addEventListener("click", () => openReadonlyTextModal(button));
        });

        readonlyPhotoModal.querySelectorAll("[data-close-readonly-photo-modal]").forEach((button) => {
            button.addEventListener("click", closeReadonlyPhotoModal);
        });

        readonlyTextModal.querySelectorAll("[data-close-readonly-text-modal]").forEach((button) => {
            button.addEventListener("click", closeReadonlyTextModal);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key !== "Escape") {
                return;
            }
            if (!readonlyPhotoModal.hidden) {
                closeReadonlyPhotoModal();
            }
            if (!readonlyTextModal.hidden) {
                closeReadonlyTextModal();
            }
        });
    }

    const focusEntryFromUrl = () => {
        const focus = new URLSearchParams(window.location.search).get("focus");
        if (!focus) {
            return;
        }
        const target = Array.from(document.querySelectorAll("[data-entry-ref]")).find((entry) => {
            return entry.dataset.entryRef === focus;
        });
        if (target) {
            target.scrollIntoView({block: "center"});
            window.setTimeout(() => target.click(), 100);
        }
    };

    const photoModal = document.getElementById("photo-modal");
    const textModal = document.getElementById("text-modal");
    if (!photoModal || !textModal) {
        focusEntryFromUrl();
        return;
    }

    const modalImage = document.getElementById("modal-image");
    const modalDate = document.getElementById("modal-date");
    const photoPrivacySummary = document.getElementById("photo-privacy-summary");
    const messageList = document.getElementById("message-list");
    const messageForm = document.getElementById("message-form");
    const messageInput = messageForm.querySelector("textarea");
    const deletePhotoButton = document.getElementById("delete-photo-button");
    const photoTagsForm = document.getElementById("photo-tags-form");
    const photoTagInputs = Array.from(photoTagsForm.querySelectorAll("input[name='tags']"));

    const textModalDate = document.getElementById("text-modal-date");
    const textPrivacySummary = document.getElementById("text-privacy-summary");
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
        const card = thumbnail.closest(".entry-card");
        if (card) {
            card.remove();
        } else {
            thumbnail.remove();
        }
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

    const updatePhotoThumbnailTags = (tags, tagsText, privacyLabel, privacyHelpText) => {
        if (!activePhotoThumbnail) {
            return;
        }

        activePhotoThumbnail.dataset.photoTags = tagsText;
        setPrivacyBadge(
            activePhotoThumbnail,
            privacyLabel || privacyLabelFromTag(tagsText),
            privacyHelpText || privacyHelpFromTag(tagsText)
        );
    };

    const updateTextThumbnailTags = (tags, tagsText, privacyLabel, privacyHelpText) => {
        if (!activeTextThumbnail) {
            return;
        }

        activeTextThumbnail.dataset.entryTags = tagsText;
        setPrivacyBadge(
            activeTextThumbnail,
            privacyLabel || privacyLabelFromTag(tagsText),
            privacyHelpText || privacyHelpFromTag(tagsText)
        );
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

            if (message.author_name) {
                const author = document.createElement("strong");
                author.className = "message-author";
                author.textContent = message.author_name;
                item.appendChild(author);
            }

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
        setPrivacySummary(
            photoPrivacySummary,
            button.dataset.privacyLabel || privacyLabelFromTag(button.dataset.photoTags || "private"),
            button.dataset.privacyHelp || privacyHelpFromTag(button.dataset.photoTags || "private")
        );
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
        setPrivacySummary(
            textPrivacySummary,
            entry.privacy_label || privacyLabelFromTag(entry.tags_text || "private"),
            entry.privacy_help || privacyHelpFromTag(entry.tags_text || "private")
        );
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
        updateTextThumbnailTags(
            entry.tags || [],
            entry.tags_text || "",
            entry.privacy_label,
            entry.privacy_help
        );
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
            setPrivacySummary(
                photoPrivacySummary,
                payload.privacy_label || privacyLabelFromTag(payload.tags_text),
                payload.privacy_help || privacyHelpFromTag(payload.tags_text)
            );
            updatePhotoThumbnailTags(
                payload.tags,
                payload.tags_text,
                payload.privacy_label,
                payload.privacy_help
            );
        }
    });

    deletePhotoButton.addEventListener("click", async () => {
        if (activePhotoId === null) {
            return;
        }

        const confirmed = await requestConfirmation({
            title: "Delete picture?",
            message: "This permanently removes the picture, messages, chapter placements, tags, likes, loves, and related notifications.",
            confirmLabel: "Delete picture",
            danger: true,
        });
        if (!confirmed) {
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

        const confirmed = await requestConfirmation({
            title: "Delete text entry?",
            message: "This permanently removes the text entry, chapter placements, tags, likes, loves, and related notifications.",
            confirmLabel: "Delete text",
            danger: true,
        });
        if (!confirmed) {
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

    focusEntryFromUrl();
});
