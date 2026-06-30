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
    const csrfToken = document.querySelector("meta[name='csrf-token']")?.content || "";
    const csrfFetch = (url, options = {}) => {
        const method = (options.method || "GET").toUpperCase();
        if (csrfToken && !["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
            const headers = new Headers(options.headers || {});
            headers.set("X-CSRF-Token", csrfToken);
            return fetch(url, {...options, headers});
        }
        return fetch(url, options);
    };
    const actionTrackUrl = document.body.dataset.actionTrackUrl || "";
    const pageContext = document.body.dataset.pageContext || window.location.pathname;
    const actionTargetSelector = [
        "button",
        "summary.button",
        "[role='button']",
        "input[type='button']",
        "input[type='submit']",
        "input[type='reset']",
        "a.button",
        "a.topbar-link",
        "a.account-menu-link",
        "a.back-link",
        "a.timeline-button",
        "a.month-button",
        "a.icon-button",
    ].join(",");
    const compactText = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const actionButtonText = (element) => compactText(
        element.innerText
        || element.textContent
        || element.value
        || element.getAttribute("aria-label")
        || element.title
        || element.name
        || element.type,
    );
    const headingText = (container) => compactText(container?.querySelector("h1, h2, h3")?.innerText);
    const actionContext = (element) => {
        const explicit = element.closest("[data-action-context]");
        if (explicit?.dataset.actionContext) {
            return compactText(explicit.dataset.actionContext);
        }
        const nav = element.closest("nav[aria-label]");
        if (nav) {
            return compactText(nav.getAttribute("aria-label"));
        }
        const dialog = element.closest("[role='dialog'][aria-labelledby]");
        if (dialog) {
            const label = document.getElementById(dialog.getAttribute("aria-labelledby"));
            if (label) {
                return compactText(label.innerText);
            }
        }
        const form = element.closest("form");
        if (form) {
            const formRegion = form.closest("section, article, aside, main");
            const label = headingText(formRegion) || compactText(formRegion?.getAttribute("aria-label"));
            return label ? `${label} form` : "Form";
        }
        const region = element.closest("section, article, aside, header, main");
        const regionLabel = headingText(region) || compactText(region?.getAttribute("aria-label"));
        if (regionLabel) {
            return regionLabel;
        }
        const pageLabel = compactText(document.querySelector(".page-heading h1, main h1")?.innerText);
        return pageLabel ? `${pageLabel} page` : pageContext;
    };
    const trackActionClick = (element) => {
        if (!actionTrackUrl || element.closest("[data-no-action-track]")) {
            return;
        }
        const buttonText = actionButtonText(element);
        if (!buttonText) {
            return;
        }
        csrfFetch(actionTrackUrl, {
            method: "POST",
            keepalive: true,
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                button_text: buttonText,
                context: actionContext(element),
            }),
        }).catch(() => {});
    };
    document.addEventListener("click", (event) => {
        if (!(event.target instanceof Element)) {
            return;
        }
        const actionElement = event.target.closest(actionTargetSelector);
        if (actionElement && !actionElement.disabled) {
            trackActionClick(actionElement);
        }
    }, true);
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
    const parsePeopleText = (value) => String(value || "")
        .split(/[;,]/)
        .map((person) => person.trim())
        .filter(Boolean);
    const peopleToText = (people) => parsePeopleText(Array.isArray(people) ? people.join(", ") : people).join(", ");
    const setPeopleSummary = (element, people) => {
        if (!element) {
            return;
        }
        const peopleText = peopleToText(people);
        element.textContent = peopleText ? `People: ${peopleText}` : "";
        element.hidden = !peopleText;
    };
    const updatePeopleChips = (thumbnail, people) => {
        if (!thumbnail) {
            return;
        }
        const card = thumbnail.closest(".entry-card");
        if (!card) {
            return;
        }
        const parsedPeople = parsePeopleText(Array.isArray(people) ? people.join(", ") : people);
        let list = card.querySelector(".people-chip-list");
        if (!parsedPeople.length) {
            if (list) {
                list.remove();
            }
            return;
        }
        if (!list) {
            list = document.createElement("div");
            list.className = "people-chip-list";
            list.setAttribute("aria-label", "People");
            const meta = card.querySelector(".photo-card-meta");
            (meta || thumbnail).insertAdjacentElement("afterend", list);
        }
        list.innerHTML = "";
        parsedPeople.forEach((person) => {
            const chip = document.createElement("span");
            chip.className = "people-chip";
            chip.textContent = person;
            list.appendChild(chip);
        });
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
            event.stopImmediatePropagation();
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
            if (typeof form.requestSubmit === "function") {
                form.requestSubmit();
            } else {
                HTMLFormElement.prototype.submit.call(form);
            }
        });
    });

    const reviewRefresh = () => {
        window.setTimeout(() => {
            window.location.reload();
        }, 550);
    };

    const reviewCardPayload = (card) => ({
        title: card.dataset.title || "",
        caption: card.dataset.caption || "",
        body: card.dataset.body || "",
        entry_date: card.dataset.entryDate || "",
        tags: card.dataset.tags || "private",
        people: card.dataset.people || "",
        location_name: card.dataset.locationName || "",
        latitude: card.dataset.latitude || "",
        longitude: card.dataset.longitude || "",
    });

    const setReviewStatus = (card, message, isError = false) => {
        const status = card.querySelector("[data-review-status]");
        if (!status) {
            return;
        }
        status.textContent = message;
        status.classList.toggle("is-error", isError);
    };

    const chapterCardNewModal = document.getElementById("chapter-card-new-modal");
    const chapterCardNewForm = document.querySelector("[data-chapter-card-new-form]");
    const chapterCardNewContext = document.querySelector("[data-chapter-card-new-context]");
    const chapterCardNewStatus = document.querySelector("[data-chapter-card-new-status]");
    let pendingChapterCardForm = null;

    const setChapterCardStatus = (form, message, isError = false) => {
        const status = form?.querySelector("[data-chapter-card-status]");
        if (!status) {
            return;
        }
        status.textContent = message;
        status.classList.toggle("is-error", isError);
    };

    const reviewJsonFetch = async (url, payload, method = "PATCH") => {
        const response = await csrfFetch(url, {
            method,
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            let message = "Could not save this change.";
            try {
                const errorPayload = await response.json();
                message = errorPayload.error || message;
            } catch (error) {
                message = response.statusText || message;
            }
            throw new Error(message);
        }
        if (response.status === 204) {
            return {};
        }
        return response.json();
    };

    document.querySelectorAll("[data-review-card]").forEach((card) => {
        card.querySelectorAll("[data-review-action]").forEach((form) => {
            form.addEventListener("submit", async (event) => {
                event.preventDefault();
                const action = form.dataset.reviewAction;
                const submitButton = form.querySelector("button[type='submit']");
                const payload = reviewCardPayload(card);
                let url = card.dataset.apiUrl;
                let method = "PATCH";

                if (action === "caption" || action === "prompt-caption") {
                    payload.caption = form.elements.caption.value;
                } else if (action === "prompt-body") {
                    payload.body = form.elements.body.value;
                } else if (action === "people" || action === "prompt-people") {
                    payload.people = form.elements.people.value;
                    url = card.dataset.peopleUrl;
                } else if (action === "location" || action === "prompt-location") {
                    payload.location_name = form.elements.location_name.value;
                    payload.latitude = form.elements.latitude.value;
                    payload.longitude = form.elements.longitude.value;
                    url = card.dataset.locationUrl;
                } else if (action === "chapter") {
                    payload.chapter_id = form.elements.chapter_id.value;
                    url = card.dataset.chapterUrl;
                    method = "POST";
                }

                if (submitButton) {
                    submitButton.disabled = true;
                }
                setReviewStatus(card, "Saving...");
                try {
                    await reviewJsonFetch(url, payload, method);
                    setReviewStatus(card, "Saved. Updating queue...");
                    reviewRefresh();
                } catch (error) {
                    setReviewStatus(card, error.message, true);
                    if (submitButton) {
                        submitButton.disabled = false;
                    }
                }
            });
        });

        const deleteButton = card.querySelector("[data-review-delete]");
        if (deleteButton) {
            deleteButton.addEventListener("click", async () => {
                const confirmed = await requestConfirmation({
                    title: deleteButton.dataset.confirmTitle || "Delete photo?",
                    message: deleteButton.dataset.confirmMessage || "This permanently removes this photo.",
                    confirmLabel: "Delete",
                    danger: true,
                });
                if (!confirmed) {
                    return;
                }
                deleteButton.disabled = true;
                setReviewStatus(card, "Deleting...");
                try {
                    await reviewJsonFetch(card.dataset.apiUrl, {}, "DELETE");
                    setReviewStatus(card, "Deleted. Updating queue...");
                    reviewRefresh();
                } catch (error) {
                    setReviewStatus(card, error.message, true);
                    deleteButton.disabled = false;
                }
            });
        }
    });

    const addChapterCardOption = (chapter) => {
        if (!chapter || !chapter.id) {
            return;
        }
        document.querySelectorAll("[data-chapter-card-select]").forEach((select) => {
            if (select.querySelector(`option[value="${chapter.id}"]`)) {
                return;
            }
            const option = document.createElement("option");
            option.value = String(chapter.id);
            option.textContent = chapter.title || "New chapter";
            const newOption = select.querySelector("option[value='__new__']");
            select.insertBefore(option, newOption);
        });
    };

    const closeChapterCardNewModal = () => {
        if (!chapterCardNewModal) {
            return;
        }
        chapterCardNewModal.hidden = true;
        if (chapterCardNewForm) {
            chapterCardNewForm.reset();
            delete chapterCardNewForm.dataset.submitting;
        }
        if (chapterCardNewStatus) {
            chapterCardNewStatus.textContent = "";
            chapterCardNewStatus.classList.remove("is-error");
        }
        pendingChapterCardForm = null;
        syncModalOpenState();
    };

    const openChapterCardNewModal = (form) => {
        if (!chapterCardNewModal || !chapterCardNewForm || !form) {
            return;
        }
        pendingChapterCardForm = form;
        setChapterCardStatus(form, "");
        const select = form.querySelector("[data-chapter-card-select]");
        if (select) {
            select.value = "";
        }
        const itemKind = form.querySelector("input[name='item_kind']")?.value === "text" ? "text entry" : "photo";
        if (chapterCardNewContext) {
            chapterCardNewContext.textContent = `Add this ${itemKind} to a new chapter.`;
        }
        if (chapterCardNewStatus) {
            chapterCardNewStatus.textContent = "";
            chapterCardNewStatus.classList.remove("is-error");
        }
        chapterCardNewForm.reset();
        chapterCardNewModal.hidden = false;
        syncModalOpenState();
        chapterCardNewForm.querySelector("input[name='title']")?.focus();
    };

    const setChapterCardSubmitting = (form, submitting) => {
        if (!form) {
            return;
        }
        const select = form.querySelector("[data-chapter-card-select]");
        form.dataset.submitting = submitting ? "true" : "false";
        if (select) {
            select.disabled = submitting;
        }
    };

    const assignChapterCardItem = async (form, chapterId) => {
        if (!form || !chapterId || form.dataset.submitting === "true") {
            return;
        }

        const select = form.querySelector("[data-chapter-card-select]");
        setChapterCardSubmitting(form, true);
        setChapterCardStatus(form, "Adding...");
        try {
            const formData = new FormData(form);
            formData.set("chapter_id", chapterId);
            const response = await csrfFetch(form.dataset.chapterAddUrl, {
                method: "POST",
                headers: {"Accept": "application/json"},
                body: formData,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(payload.error || "Could not add to chapter.");
            }
            setChapterCardStatus(
                form,
                payload.message || "Added item to chapter.",
                payload.status === "exists"
            );
            if (select) {
                select.value = "";
            }
        } catch (error) {
            setChapterCardStatus(form, error.message || "Could not add to chapter.", true);
            if (select) {
                select.value = "";
            }
        } finally {
            setChapterCardSubmitting(form, false);
        }
    };

    document.querySelectorAll("[data-chapter-card-form]").forEach((form) => {
        const select = form.querySelector("[data-chapter-card-select]");
        if (!select) {
            return;
        }

        select.addEventListener("change", () => {
            if (select.value === "__new__") {
                openChapterCardNewModal(form);
                return;
            }
            if (select.value) {
                assignChapterCardItem(form, select.value);
                return;
            }
            setChapterCardStatus(form, "");
        });

        form.addEventListener("submit", (event) => {
            event.preventDefault();
            if (select.value === "__new__") {
                openChapterCardNewModal(form);
                return;
            }
            if (select.value) {
                assignChapterCardItem(form, select.value);
            }
        });
    });

    if (chapterCardNewModal) {
        chapterCardNewModal.querySelectorAll("[data-close-chapter-card-new]").forEach((button) => {
            button.addEventListener("click", closeChapterCardNewModal);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key !== "Escape" || chapterCardNewModal.hidden) {
                return;
            }
            event.preventDefault();
            event.stopImmediatePropagation();
            closeChapterCardNewModal();
        });
    }

    if (chapterCardNewForm) {
        chapterCardNewForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (!pendingChapterCardForm || chapterCardNewForm.dataset.submitting === "true") {
                return;
            }

            chapterCardNewForm.dataset.submitting = "true";
            if (chapterCardNewStatus) {
                chapterCardNewStatus.textContent = "Creating...";
                chapterCardNewStatus.classList.remove("is-error");
            }

            try {
                const formData = new FormData(chapterCardNewForm);
                formData.set("item_kind", pendingChapterCardForm.querySelector("input[name='item_kind']")?.value || "");
                formData.set("item_id", pendingChapterCardForm.querySelector("input[name='item_id']")?.value || "");
                const response = await csrfFetch(pendingChapterCardForm.dataset.chapterCreateUrl, {
                    method: "POST",
                    headers: {"Accept": "application/json"},
                    body: formData,
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(payload.error || "Could not create chapter.");
                }
                const completedForm = pendingChapterCardForm;
                addChapterCardOption(payload.chapter);
                closeChapterCardNewModal();
                setChapterCardStatus(completedForm, payload.message || "Created chapter and added item.");
            } catch (error) {
                if (chapterCardNewStatus) {
                    chapterCardNewStatus.textContent = error.message || "Could not create chapter.";
                    chapterCardNewStatus.classList.add("is-error");
                }
            } finally {
                delete chapterCardNewForm.dataset.submitting;
            }
        });
    }

    document.querySelectorAll("form[data-upload-progress]").forEach((form) => {
        const progressPanel = form.querySelector("[data-upload-progress-panel]");
        const progressBar = form.querySelector("[data-upload-progress-bar]");
        const progressPercent = form.querySelector("[data-upload-progress-percent]");
        const progressLabel = form.querySelector("[data-upload-progress-label]");
        const processingOverlay = form.querySelector("[data-upload-processing]");
        const processingProgress = form.querySelector("[data-upload-processing-progress]");
        const processingPercent = form.querySelector("[data-upload-processing-percent]");
        const processingStatus = form.querySelector("[data-upload-processing-status]");
        const uploadSizeError = form.querySelector("[data-upload-size-error]");
        const submitButton = form.querySelector("button[type='submit']");
        const fileInput = form.querySelector("input[type='file']");
        const maxUploadBytes = Number.parseInt(form.dataset.maxUploadBytes || "0", 10);
        let processingTimer = null;
        let processingValue = 0;

        if (!progressPanel || !progressBar || !progressPercent || !window.FormData || !window.XMLHttpRequest) {
            return;
        }

        const formatUploadBytes = (byteCount) => {
            const units = ["bytes", "KB", "MB", "GB"];
            let value = Number(byteCount || 0);
            let unitIndex = 0;
            while (value >= 1024 && unitIndex < units.length - 1) {
                value /= 1024;
                unitIndex += 1;
            }
            if (unitIndex === 0) {
                return `${Math.round(value)} ${units[unitIndex]}`;
            }
            return `${Number(value.toFixed(1))} ${units[unitIndex]}`;
        };

        const setUploadSizeError = (message = "") => {
            if (!uploadSizeError) {
                return;
            }
            uploadSizeError.textContent = message;
            uploadSizeError.hidden = !message;
        };

        const setUploadProgress = (percent, label = "Uploading...") => {
            const normalizedPercent = Math.max(0, Math.min(100, Math.round(percent)));
            progressPanel.hidden = false;
            progressBar.value = normalizedPercent;
            progressPercent.textContent = `${normalizedPercent}%`;
            if (progressLabel) {
                progressLabel.textContent = label;
            }
        };

        const clearProcessingTimer = () => {
            if (processingTimer) {
                window.clearInterval(processingTimer);
                processingTimer = null;
            }
        };

        const setProcessingProgress = (percent, status = "Preparing images...") => {
            processingValue = Math.max(0, Math.min(100, Math.round(percent)));
            if (processingProgress) {
                processingProgress.value = processingValue;
            }
            if (processingPercent) {
                processingPercent.textContent = `${processingValue}%`;
            }
            if (processingStatus) {
                processingStatus.textContent = status;
            }
        };

        const resetUploadProcessing = () => {
            clearProcessingTimer();
            processingValue = 0;
            setProcessingProgress(0);
            if (processingOverlay) {
                processingOverlay.hidden = true;
            }
        };

        const showUploadProcessing = () => {
            setUploadProgress(100, "Processing...");
            if (processingOverlay) {
                processingOverlay.hidden = false;
            }
            if (!processingProgress) {
                return;
            }
            if (processingValue < 8) {
                setProcessingProgress(8);
            }
            if (!processingTimer) {
                processingTimer = window.setInterval(() => {
                    const nextValue = Math.min(96, processingValue + Math.max(1, Math.round((96 - processingValue) * 0.12)));
                    setProcessingProgress(nextValue);
                }, 420);
            }
        };

        const completeUploadProcessing = () => {
            clearProcessingTimer();
            if (processingOverlay) {
                processingOverlay.hidden = false;
            }
            setProcessingProgress(100, "Review ready.");
        };

        if (fileInput) {
            fileInput.addEventListener("change", () => {
                setUploadSizeError();
                progressPanel.hidden = true;
                resetUploadProcessing();
                progressBar.value = 0;
                progressPercent.textContent = "0%";
            });
        }

        form.addEventListener("submit", (event) => {
            if (form.dataset.nativeSubmit === "true") {
                delete form.dataset.nativeSubmit;
                return;
            }

            event.preventDefault();
            setUploadSizeError();

            const selectedFiles = fileInput && fileInput.files ? fileInput.files.length : 0;
            const selectedBytes = fileInput && fileInput.files
                ? Array.from(fileInput.files).reduce((total, file) => total + file.size, 0)
                : 0;

            if (maxUploadBytes > 0 && selectedBytes > maxUploadBytes) {
                progressPanel.hidden = true;
                resetUploadProcessing();
                if (submitButton) {
                    submitButton.disabled = false;
                }
                setUploadSizeError(
                    `Selected files total ${formatUploadBytes(selectedBytes)}. The upload limit is ${formatUploadBytes(maxUploadBytes)}. Choose fewer or smaller photos.`
                );
                if (uploadSizeError) {
                    uploadSizeError.focus({preventScroll: true});
                }
                return;
            }

            if (submitButton) {
                submitButton.disabled = true;
            }

            const uploadLabel = form.dataset.uploadLabel
                || (selectedFiles > 1 ? `Uploading ${selectedFiles} photos...` : "Uploading photo...");
            setUploadProgress(0, uploadLabel);

            const request = new XMLHttpRequest();
            request.open((form.method || "POST").toUpperCase(), form.action || window.location.href);
            request.setRequestHeader("X-Requested-With", "XMLHttpRequest");
            if (csrfToken) {
                request.setRequestHeader("X-CSRF-Token", csrfToken);
            }

            request.upload.addEventListener("progress", (progressEvent) => {
                if (!progressEvent.lengthComputable) {
                    return;
                }
                setUploadProgress((progressEvent.loaded / progressEvent.total) * 100, uploadLabel);
                if (progressEvent.loaded >= progressEvent.total) {
                    showUploadProcessing();
                }
            });

            request.upload.addEventListener("load", () => {
                showUploadProcessing();
            });

            request.addEventListener("load", () => {
                completeUploadProcessing();

                window.requestAnimationFrame(() => {
                    window.setTimeout(() => {
                        const responseUrl = request.responseURL || window.location.href;
                        if (responseUrl) {
                            window.history.replaceState({}, "", responseUrl);
                        }
                        document.open();
                        document.write(request.responseText);
                        document.close();
                    }, processingOverlay ? 120 : 0);
                });
            });

            request.addEventListener("error", () => {
                if (submitButton) {
                    submitButton.disabled = false;
                }
                resetUploadProcessing();
                setUploadProgress(progressBar.value || 0, "Upload failed.");
            });

            request.addEventListener("abort", () => {
                if (submitButton) {
                    submitButton.disabled = false;
                }
                resetUploadProcessing();
                setUploadProgress(progressBar.value || 0, "Upload canceled.");
            });

            request.send(new FormData(form));
        });
    });

    const startProgressAnimation = (progressBar, label, message = "Working...") => {
        if (!progressBar) {
            return () => {};
        }

        progressBar.max = 100;
        progressBar.value = 8;
        if (label) {
            label.textContent = message;
        }

        let progressValue = 8;
        const timer = window.setInterval(() => {
            progressValue = Math.min(94, progressValue + Math.max(1, Math.round((94 - progressValue) * 0.14)));
            progressBar.value = progressValue;
        }, 360);

        return ({complete = false, completeMessage = "Done."} = {}) => {
            window.clearInterval(timer);
            if (complete) {
                progressBar.value = 100;
                if (label) {
                    label.textContent = completeMessage;
                }
            }
        };
    };

    const progressPanelMarkup = (labelText) => {
        const panel = document.createElement("div");
        panel.className = "upload-progress submit-progress";
        panel.dataset.dynamicProgressPanel = "true";
        panel.hidden = true;

        const row = document.createElement("div");
        row.className = "upload-progress-row";

        const label = document.createElement("span");
        label.dataset.submitProgressLabel = "true";
        label.textContent = labelText;

        const value = document.createElement("span");
        value.textContent = "";

        const progress = document.createElement("progress");
        progress.max = 100;
        progress.value = 0;
        progress.dataset.submitProgressBar = "true";

        row.append(label, value);
        panel.append(row, progress);
        return panel;
    };

    document.querySelectorAll("form[data-submit-progress]").forEach((form) => {
        let panel = form.querySelector("[data-submit-progress-panel]");
        if (!panel) {
            panel = progressPanelMarkup(form.dataset.progressLabel || "Working...");
            form.appendChild(panel);
        }
        const progressBar = panel.querySelector("[data-submit-progress-bar], progress");
        const label = panel.querySelector("[data-submit-progress-label], [data-submit-progress-label='true']");

        form.addEventListener("submit", (event) => {
            const submitter = event.submitter && event.submitter.matches("button[type='submit'], input[type='submit']")
                ? event.submitter
                : null;
            const message = submitter?.dataset.progressLabel || form.dataset.progressLabel || "Working...";
            form.querySelectorAll("[data-submit-progress-submitter]").forEach((input) => input.remove());
            if (submitter) {
                if (submitter.name) {
                    const submitterInput = document.createElement("input");
                    submitterInput.type = "hidden";
                    submitterInput.name = submitter.name;
                    submitterInput.value = submitter.value;
                    submitterInput.dataset.submitProgressSubmitter = "true";
                    form.appendChild(submitterInput);
                }
                if (submitter.dataset.targetPage) {
                    const pageInput = document.createElement("input");
                    pageInput.type = "hidden";
                    pageInput.name = "target_page";
                    pageInput.value = submitter.dataset.targetPage;
                    pageInput.dataset.submitProgressSubmitter = "true";
                    form.appendChild(pageInput);
                }
                if (submitter.formAction) {
                    form.action = submitter.formAction;
                }
                if (submitter.formMethod) {
                    form.method = submitter.formMethod;
                }
            }
            panel.hidden = false;
            form.querySelectorAll("button[type='submit'], input[type='submit']").forEach((button) => {
                button.disabled = true;
            });
            startProgressAnimation(progressBar, label, message);
        });
    });

    let downloadProgressHideTimer = null;
    const showDownloadProgress = (message) => {
        let panel = document.querySelector("[data-floating-progress]");
        if (!panel) {
            panel = progressPanelMarkup(message);
            panel.classList.add("floating-progress");
            panel.dataset.floatingProgress = "true";
            document.body.appendChild(panel);
        }

        panel.hidden = false;
        const progressBar = panel.querySelector("[data-submit-progress-bar], progress");
        const label = panel.querySelector("[data-submit-progress-label], [data-submit-progress-label='true']");
        const stop = startProgressAnimation(progressBar, label, message);
        window.clearTimeout(downloadProgressHideTimer);
        downloadProgressHideTimer = window.setTimeout(() => {
            stop({complete: true, completeMessage: "Download should begin."});
            downloadProgressHideTimer = window.setTimeout(() => {
                panel.hidden = true;
            }, 1800);
        }, 4500);
    };

    document.querySelectorAll("[data-download-progress]").forEach((link) => {
        link.addEventListener("click", () => {
            showDownloadProgress(link.dataset.progressLabel || "Preparing download...");
        });
    });

    const adminJobCards = Array.from(document.querySelectorAll("[data-admin-job]"));
    if (adminJobCards.length) {
        const terminalStatuses = new Set(["succeeded", "failed"]);
        const updateJobCard = (card, job) => {
            card.classList.remove("is-queued", "is-running", "is-succeeded", "is-failed");
            card.classList.add(`is-${job.status}`);

            const status = card.querySelector("[data-job-status]");
            if (status) {
                status.textContent = job.status;
            }

            const progress = card.querySelector("[data-job-progress]");
            if (progress) {
                progress.max = job.progress_total || 1;
                progress.value = job.progress_total
                    ? job.progress_current || 0
                    : (job.status === "succeeded" ? 1 : 0);
            }

            const message = card.querySelector("[data-job-message]");
            if (message) {
                message.textContent = job.message || job.result_summary || "Waiting to start.";
            }

            const result = card.querySelector("[data-job-result]");
            if (result) {
                result.textContent = job.result_summary || "";
                result.hidden = !job.result_summary;
            }

            const error = card.querySelector("[data-job-error]");
            if (error) {
                error.textContent = job.error ? job.error.split("\n")[0] : "";
                error.hidden = !job.error;
            }
        };

        const refreshAdminJobs = async () => {
            const activeCards = adminJobCards.filter((card) => {
                const status = card.querySelector("[data-job-status]")?.textContent || "";
                return !terminalStatuses.has(status.trim());
            });
            if (!activeCards.length) {
                return;
            }

            await Promise.all(activeCards.map(async (card) => {
                if (!card.dataset.jobUrl) {
                    return;
                }
                try {
                    const response = await csrfFetch(card.dataset.jobUrl, {
                        headers: {"Accept": "application/json"},
                    });
                    if (!response.ok) {
                        return;
                    }
                    updateJobCard(card, await response.json());
                } catch (error) {
                    // The next poll will retry.
                }
            }));
        };

        refreshAdminJobs();
        window.setInterval(refreshAdminJobs, 1500);
    }

    const navToggle = document.querySelector("[data-nav-toggle]");
    const primaryNavigation = document.querySelector("[data-primary-navigation]");
    if (navToggle && primaryNavigation) {
        const setNavOpen = (isOpen) => {
            navToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
            navToggle.setAttribute("aria-label", isOpen ? "Close navigation" : "Open navigation");
            primaryNavigation.classList.toggle("is-open", isOpen);
            document.body.classList.toggle("nav-open", isOpen);
        };

        navToggle.addEventListener("click", () => {
            setNavOpen(navToggle.getAttribute("aria-expanded") !== "true");
        });

        primaryNavigation.querySelectorAll("a, button").forEach((item) => {
            item.addEventListener("click", () => setNavOpen(false));
        });

        document.addEventListener("click", (event) => {
            if (
                navToggle.getAttribute("aria-expanded") === "true"
                && !primaryNavigation.contains(event.target)
                && !navToggle.contains(event.target)
            ) {
                setNavOpen(false);
            }
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                setNavOpen(false);
                navToggle.focus();
            }
        });

        window.addEventListener("resize", () => {
            if (window.matchMedia("(min-width: 761px)").matches) {
                setNavOpen(false);
            }
        });
    }

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
                const response = await csrfFetch("/api/notifications/count", {
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

    const splashPage = document.querySelector("[data-splash]");
    if (splashPage) {
        const splashGrid = splashPage.querySelector("[data-splash-grid]");
        const splashStatus = splashPage.querySelector("[data-splash-status]");
        const splashCount = splashPage.querySelector("[data-splash-count]");
        const splashPrevButton = splashPage.querySelector("[data-splash-prev]");
        const splashNextButton = splashPage.querySelector("[data-splash-next]");
        const splashSizeButtons = splashPage.querySelectorAll("[data-splash-size]");
        const splashPhotoModal = document.getElementById("splash-photo-modal");
        const splashPhotoImage = document.getElementById("splash-photo-modal-image");
        const splashPhotoTitle = document.getElementById("splash-photo-modal-title");
        const splashPhotoDate = document.getElementById("splash-photo-modal-date");
        const splashChapterPhotoInput = document.querySelector("[data-splash-chapter-photo-id]");
        const splashChapterForm = document.querySelector("[data-splash-chapter-form]");
        const splashChapterSelect = document.querySelector("[data-splash-chapter-select]");
        const splashChapterStatus = document.querySelector("[data-splash-chapter-status]");
        const splashSeed = splashPage.dataset.splashSeed || String(Date.now());
        const splashUrl = splashPage.dataset.splashUrl || "/api/splash-photos";
        const splashSelectable = splashPage.hasAttribute("data-splash-selectable");
        const splashAssignUrl = splashPage.dataset.splashAssignUrl || "";
        const splashAcceptSuggestionsUrl = splashPage.dataset.splashAcceptSuggestionsUrl || "";
        const splashQuickEditUrl = splashPage.dataset.splashQuickEditUrl || "";
        const noDateAssignForm = document.querySelector("[data-no-date-assign-form]");
        const noDateAssignButton = document.querySelector("[data-no-date-assign-button]");
        const noDateAcceptSuggestionsButton = document.querySelector("[data-no-date-accept-suggestions-button]");
        const noDateSelectedCount = document.querySelector("[data-no-date-selected-count]");
        const defaultTileSize = 82;
        const configuredTileSize = Number.parseInt(splashPage.dataset.splashTileSize || "", 10);
        const baseTileSize = Number.isFinite(configuredTileSize) && configuredTileSize > 0 ? configuredTileSize : defaultTileSize;
        let minTileSize = baseTileSize;
        const noDateEditModal = document.getElementById("no-date-edit-modal");
        const noDateQuickEditForm = document.querySelector("[data-no-date-quick-edit-form]");
        const noDateEditTitle = document.getElementById("no-date-edit-title");
        const noDateEditSuggestion = document.querySelector("[data-no-date-edit-suggestion]");
        let splashPageIndex = 0;
        let splashPageSize = 0;
        let splashTotalPages = 0;
        let splashResizeTimer = null;
        const selectedSplashPhotoIds = new Set();
        const splashSuggestions = new Map();

        const setSplashStatus = (message) => {
            if (!splashStatus) {
                return;
            }
            splashStatus.textContent = message;
            splashStatus.hidden = !message;
        };

        const updateSplashControls = () => {
            const hasPages = splashTotalPages > 1;
            if (splashPrevButton) {
                splashPrevButton.hidden = !hasPages;
            }
            if (splashNextButton) {
                splashNextButton.hidden = !hasPages;
            }
            if (splashCount) {
                splashCount.hidden = splashTotalPages <= 0;
                splashCount.textContent = splashTotalPages > 0 ? `${splashPageIndex + 1} of ${splashTotalPages}` : "";
            }
        };

        const updateSplashSelectionState = () => {
            if (!splashSelectable) {
                return;
            }
            const selectedCount = selectedSplashPhotoIds.size;
            if (noDateSelectedCount) {
                noDateSelectedCount.textContent = `${selectedCount} selected`;
            }
            if (noDateAssignButton) {
                noDateAssignButton.disabled = selectedCount === 0;
            }
            const selectedSuggestionCount = Array.from(selectedSplashPhotoIds)
                .filter((photoId) => splashSuggestions.has(photoId))
                .length;
            if (noDateAcceptSuggestionsButton) {
                noDateAcceptSuggestionsButton.disabled = selectedSuggestionCount === 0;
            }
            splashGrid.querySelectorAll(".splash-thumb").forEach((button) => {
                const photoId = Number(button.dataset.photoId);
                const selected = selectedSplashPhotoIds.has(photoId);
                button.classList.toggle("is-selected", selected);
                button.setAttribute("aria-pressed", selected ? "true" : "false");
            });
        };

        const splashGridSize = () => {
            const bounds = splashGrid.getBoundingClientRect();
            const columns = Math.max(1, Math.floor(bounds.width / minTileSize));
            const rows = Math.max(1, Math.floor(bounds.height / minTileSize));
            splashGrid.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
            splashGrid.style.gridTemplateRows = `repeat(${rows}, minmax(0, 1fr))`;
            return columns * rows;
        };

        const closeSplashPhotoModal = () => {
            if (!splashPhotoModal) {
                return;
            }
            splashPhotoModal.hidden = true;
            if (splashPhotoImage) {
                splashPhotoImage.removeAttribute("src");
                splashPhotoImage.alt = "";
            }
            if (splashChapterPhotoInput) {
                splashChapterPhotoInput.value = "";
            }
            if (splashChapterSelect) {
                splashChapterSelect.value = "";
                splashChapterSelect.removeAttribute("aria-disabled");
            }
            if (splashChapterStatus) {
                splashChapterStatus.textContent = "";
                splashChapterStatus.classList.remove("is-error");
            }
            if (splashChapterForm) {
                delete splashChapterForm.dataset.submitting;
            }
            syncModalOpenState();
        };

        const openSplashPhotoModal = (photo) => {
            if (!splashPhotoModal || !splashPhotoImage) {
                return;
            }
            if (splashPhotoTitle) {
                splashPhotoTitle.textContent = photo.title || "Picture";
            }
            if (splashPhotoDate) {
                splashPhotoDate.textContent = photo.display_date || "";
            }
            splashPhotoImage.src = photo.full_url;
            splashPhotoImage.alt = photo.title || "Splash picture";
            if (splashChapterPhotoInput) {
                splashChapterPhotoInput.value = String(photo.id || "");
            }
            if (splashChapterSelect) {
                splashChapterSelect.value = "";
                splashChapterSelect.removeAttribute("aria-disabled");
            }
            if (splashChapterStatus) {
                splashChapterStatus.textContent = "";
                splashChapterStatus.classList.remove("is-error");
            }
            if (splashChapterForm) {
                delete splashChapterForm.dataset.submitting;
            }
            splashPhotoModal.hidden = false;
            syncModalOpenState();
        };

        const closeNoDateQuickEdit = () => {
            if (!noDateEditModal) {
                return;
            }
            noDateEditModal.hidden = true;
            syncModalOpenState();
        };

        const setSelectValue = (select, value) => {
            if (!select) {
                return;
            }
            select.value = String(value);
        };

        const openNoDateQuickEdit = (photo) => {
            if (!noDateEditModal || !noDateQuickEditForm) {
                return;
            }
            const suggestion = photo.suggestion || null;
            const monthSelect = noDateQuickEditForm.elements.month;
            const yearSelect = noDateQuickEditForm.elements.year;
            const exactDateInput = noDateQuickEditForm.elements.photo_date;
            noDateQuickEditForm.elements.photo_id.value = String(photo.id);
            if (noDateEditTitle) {
                noDateEditTitle.textContent = photo.title || "Set date";
            }
            if (noDateEditSuggestion) {
                noDateEditSuggestion.textContent = suggestion
                    ? `Suggested ${suggestion.label} from ${suggestion.source_label || "date clues"}.`
                    : "No suggestion available.";
            }
            setSelectValue(monthSelect, suggestion ? suggestion.month : photo.month);
            setSelectValue(yearSelect, suggestion ? suggestion.year : photo.year);
            if (exactDateInput) {
                exactDateInput.value = "";
            }
            noDateEditModal.hidden = false;
            syncModalOpenState();
            if (monthSelect && typeof monthSelect.focus === "function") {
                monthSelect.focus({preventScroll: true});
            }
        };

        const renderSplashPhotos = (photos) => {
            splashGrid.innerHTML = "";
            photos.forEach((photo) => {
                if (photo.suggestion) {
                    splashSuggestions.set(Number(photo.id), photo.suggestion);
                }
                const tile = splashSelectable ? document.createElement("div") : null;
                if (tile) {
                    tile.className = "splash-thumb-wrap";
                }
                const button = document.createElement("button");
                button.className = "splash-thumb";
                button.type = "button";
                button.dataset.photoId = String(photo.id);
                const suggestionLabel = photo.suggestion ? `Suggested: ${photo.suggestion.label}` : "";
                button.title = suggestionLabel || (photo.display_date ? `${photo.title} - ${photo.display_date}` : photo.title);
                button.setAttribute("aria-label", splashSelectable ? `Select ${photo.title || "photo"}` : `Open ${photo.title || "photo"}`);
                if (splashSelectable) {
                    button.setAttribute("aria-pressed", selectedSplashPhotoIds.has(Number(photo.id)) ? "true" : "false");
                }

                const image = document.createElement("img");
                image.src = photo.thumbnail_url;
                image.alt = photo.title || "Splash photo";
                image.loading = "lazy";
                image.decoding = "async";

                button.appendChild(image);
                if (photo.suggestion) {
                    const suggestion = document.createElement("span");
                    suggestion.className = "splash-suggestion-chip";
                    suggestion.textContent = photo.suggestion.label;
                    suggestion.title = `Suggested from ${photo.suggestion.source_label || "date clues"}`;
                    button.appendChild(suggestion);
                }
                button.addEventListener("click", () => {
                    if (splashSelectable) {
                        const photoId = Number(photo.id);
                        if (selectedSplashPhotoIds.has(photoId)) {
                            selectedSplashPhotoIds.delete(photoId);
                        } else {
                            selectedSplashPhotoIds.add(photoId);
                        }
                        updateSplashSelectionState();
                        return;
                    }
                    openSplashPhotoModal(photo);
                });
                if (tile) {
                    const editButton = document.createElement("button");
                    editButton.className = "no-date-photo-edit-button";
                    editButton.type = "button";
                    editButton.textContent = "Edit";
                    editButton.setAttribute("aria-label", `Edit date for ${photo.title || "photo"}`);
                    editButton.addEventListener("click", (event) => {
                        event.stopPropagation();
                        openNoDateQuickEdit(photo);
                    });
                    tile.appendChild(button);
                    tile.appendChild(editButton);
                    splashGrid.appendChild(tile);
                } else {
                    splashGrid.appendChild(button);
                }
            });
            updateSplashSelectionState();
        };

        const loadSplashPage = async (page) => {
            const nextPageSize = splashGridSize();
            splashPageSize = nextPageSize;
            setSplashStatus("Loading photos...");
            const url = new URL(splashUrl, window.location.href);
            url.searchParams.set("seed", splashSeed);
            url.searchParams.set("page", String(page));
            url.searchParams.set("page_size", String(splashPageSize));

            try {
                const response = await csrfFetch(url.toString());
                if (!response.ok) {
                    throw new Error("Splash photos could not be loaded.");
                }
                const payload = await response.json();
                splashPageIndex = payload.page || 0;
                splashTotalPages = payload.total_pages || 0;
                if (splashSelectable) {
                    selectedSplashPhotoIds.forEach((photoId) => {
                        const stillExists = (payload.photos || []).some((photo) => Number(photo.id) === photoId);
                        if (!stillExists && payload.total === 0) {
                            selectedSplashPhotoIds.delete(photoId);
                        }
                    });
                }
                renderSplashPhotos(payload.photos || []);
                setSplashStatus(payload.total ? "" : "No photos yet.");
                updateSplashControls();
            } catch (error) {
                renderSplashPhotos([]);
                splashTotalPages = 0;
                setSplashStatus("Photos could not be loaded.");
                updateSplashControls();
            }
        };

        const moveSplashPage = (delta) => {
            if (splashTotalPages <= 0) {
                return;
            }
            loadSplashPage(splashPageIndex + delta);
        };

        if (splashPrevButton) {
            splashPrevButton.addEventListener("click", () => moveSplashPage(-1));
        }
        if (splashNextButton) {
            splashNextButton.addEventListener("click", () => moveSplashPage(1));
        }

        const updateSplashSizeButtons = (selectedScale) => {
            splashSizeButtons.forEach((button) => {
                button.setAttribute("aria-pressed", button.dataset.splashSize === selectedScale ? "true" : "false");
            });
        };

        splashSizeButtons.forEach((button) => {
            button.addEventListener("click", () => {
                const nextScale = Number.parseFloat(button.dataset.splashSize || "1");
                if (!Number.isFinite(nextScale) || nextScale <= 0) {
                    return;
                }
                minTileSize = Math.max(24, Math.round(baseTileSize * nextScale));
                updateSplashSizeButtons(button.dataset.splashSize || "1");
                loadSplashPage(0);
            });
        });
        updateSplashSizeButtons("1");

        if (splashChapterForm && splashChapterSelect && splashChapterPhotoInput) {
            splashChapterSelect.addEventListener("change", async () => {
                if (
                    !splashChapterSelect.value ||
                    !splashChapterPhotoInput.value ||
                    splashChapterForm.dataset.submitting === "true"
                ) {
                    return;
                }
                splashChapterForm.dataset.submitting = "true";
                splashChapterSelect.setAttribute("aria-disabled", "true");
                if (splashChapterStatus) {
                    splashChapterStatus.textContent = "Adding...";
                    splashChapterStatus.classList.remove("is-error");
                }
                try {
                    const response = await csrfFetch(splashChapterForm.dataset.splashChapterApi || splashChapterForm.action, {
                        method: "POST",
                        headers: {"Accept": "application/json"},
                        body: new FormData(splashChapterForm),
                    });
                    const payload = await response.json().catch(() => ({}));
                    if (!response.ok) {
                        throw new Error(payload.error || "Could not add to chapter.");
                    }
                    if (splashChapterStatus) {
                        splashChapterStatus.textContent = payload.message || "Added item to chapter.";
                    }
                    splashChapterSelect.value = "";
                } catch (error) {
                    if (splashChapterStatus) {
                        splashChapterStatus.textContent = error.message || "Could not add to chapter.";
                        splashChapterStatus.classList.add("is-error");
                    }
                    splashChapterSelect.value = "";
                } finally {
                    delete splashChapterForm.dataset.submitting;
                    splashChapterSelect.removeAttribute("aria-disabled");
                }
            });
        }

        if (noDateAssignForm && splashSelectable) {
            noDateAssignForm.addEventListener("submit", async (event) => {
                event.preventDefault();
                if (!selectedSplashPhotoIds.size || !splashAssignUrl) {
                    return;
                }
                const formData = new FormData(noDateAssignForm);
                if (noDateAssignButton) {
                    noDateAssignButton.disabled = true;
                }
                setSplashStatus("Saving selected photos...");
                try {
                    const response = await csrfFetch(splashAssignUrl, {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({
                            photo_ids: Array.from(selectedSplashPhotoIds),
                            month: formData.get("month"),
                            year: formData.get("year"),
                        }),
                    });
                    const payload = await response.json().catch(() => ({}));
                    if (!response.ok) {
                        throw new Error(payload.error || "Selected photos could not be saved.");
                    }
                    selectedSplashPhotoIds.clear();
                    setSplashStatus(`Moved ${payload.moved_count || 0} photos.`);
                    await loadSplashPage(splashPageIndex);
                } catch (error) {
                    setSplashStatus(error.message || "Selected photos could not be saved.");
                    updateSplashSelectionState();
                } finally {
                    updateSplashSelectionState();
                }
            });
        }

        if (noDateAcceptSuggestionsButton && splashSelectable) {
            noDateAcceptSuggestionsButton.addEventListener("click", async () => {
                const suggestedPhotoIds = Array.from(selectedSplashPhotoIds)
                    .filter((photoId) => splashSuggestions.has(photoId));
                if (!suggestedPhotoIds.length || !splashAcceptSuggestionsUrl) {
                    return;
                }
                noDateAcceptSuggestionsButton.disabled = true;
                if (noDateAssignButton) {
                    noDateAssignButton.disabled = true;
                }
                setSplashStatus("Accepting suggestions...");
                try {
                    const response = await csrfFetch(splashAcceptSuggestionsUrl, {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({photo_ids: suggestedPhotoIds}),
                    });
                    const payload = await response.json().catch(() => ({}));
                    if (!response.ok) {
                        throw new Error(payload.error || "Suggestions could not be accepted.");
                    }
                    suggestedPhotoIds.forEach((photoId) => {
                        selectedSplashPhotoIds.delete(photoId);
                        splashSuggestions.delete(photoId);
                    });
                    setSplashStatus(`Moved ${payload.moved_count || 0} photos.`);
                    await loadSplashPage(splashPageIndex);
                } catch (error) {
                    setSplashStatus(error.message || "Suggestions could not be accepted.");
                    updateSplashSelectionState();
                }
            });
        }

        if (noDateQuickEditForm && splashSelectable) {
            const exactDateInput = noDateQuickEditForm.elements.photo_date;
            if (exactDateInput) {
                exactDateInput.addEventListener("change", () => {
                    if (!exactDateInput.value) {
                        return;
                    }
                    const [yearValue, monthValue] = exactDateInput.value.split("-");
                    setSelectValue(noDateQuickEditForm.elements.year, yearValue);
                    setSelectValue(noDateQuickEditForm.elements.month, Number(monthValue));
                });
            }

            noDateQuickEditForm.addEventListener("submit", async (event) => {
                event.preventDefault();
                if (!splashQuickEditUrl) {
                    return;
                }
                const formData = new FormData(noDateQuickEditForm);
                const photoId = Number(formData.get("photo_id"));
                setSplashStatus("Saving photo date...");
                try {
                    const response = await csrfFetch(splashQuickEditUrl, {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({
                            photo_id: photoId,
                            month: formData.get("month"),
                            year: formData.get("year"),
                            photo_date: formData.get("photo_date") || "",
                        }),
                    });
                    const payload = await response.json().catch(() => ({}));
                    if (!response.ok) {
                        throw new Error(payload.error || "Photo date could not be saved.");
                    }
                    selectedSplashPhotoIds.delete(photoId);
                    splashSuggestions.delete(photoId);
                    closeNoDateQuickEdit();
                    setSplashStatus("Moved 1 photo.");
                    await loadSplashPage(splashPageIndex);
                } catch (error) {
                    setSplashStatus(error.message || "Photo date could not be saved.");
                    updateSplashSelectionState();
                }
            });

            if (noDateEditModal) {
                noDateEditModal.querySelectorAll("[data-close-no-date-edit-modal]").forEach((button) => {
                    button.addEventListener("click", closeNoDateQuickEdit);
                });
                document.addEventListener("keydown", (event) => {
                    if (event.key === "Escape" && !noDateEditModal.hidden) {
                        closeNoDateQuickEdit();
                    }
                });
            }
        }

        if (splashPhotoModal) {
            splashPhotoModal.querySelectorAll("[data-close-splash-photo-modal]").forEach((button) => {
                button.addEventListener("click", closeSplashPhotoModal);
            });
        }

        document.addEventListener("keydown", (event) => {
            if (splashSelectable) {
                return;
            }
            if (splashPhotoModal && !splashPhotoModal.hidden && event.key === "Escape") {
                closeSplashPhotoModal();
                return;
            }
            if (splashPhotoModal && !splashPhotoModal.hidden) {
                return;
            }
            if (event.key === "ArrowLeft") {
                moveSplashPage(-1);
            } else if (event.key === "ArrowRight") {
                moveSplashPage(1);
            }
        });

        window.addEventListener("resize", () => {
            window.clearTimeout(splashResizeTimer);
            splashResizeTimer = window.setTimeout(() => {
                const nextPageSize = splashGridSize();
                if (nextPageSize !== splashPageSize) {
                    loadSplashPage(splashPageIndex);
                }
            }, 120);
        });

        loadSplashPage(0);
    }

    const chapterBulkPage = document.querySelector("[data-chapter-bulk-select]");
    if (chapterBulkPage) {
        const bulkGrid = chapterBulkPage.querySelector("[data-chapter-bulk-grid]");
        const bulkStatus = chapterBulkPage.querySelector("[data-chapter-bulk-status]");
        const bulkPageCountWrap = chapterBulkPage.querySelector("[data-chapter-bulk-page-count-wrap]");
        const bulkPageCount = chapterBulkPage.querySelector("[data-chapter-bulk-page-count]");
        const bulkPageProgress = chapterBulkPage.querySelector("[data-chapter-bulk-page-progress]");
        const bulkPrevButton = chapterBulkPage.querySelector("[data-chapter-bulk-prev]");
        const bulkNextButton = chapterBulkPage.querySelector("[data-chapter-bulk-next]");
        const bulkSelectedCount = chapterBulkPage.querySelector("[data-chapter-bulk-selected-count]");
        const bulkTotalCount = chapterBulkPage.querySelector("[data-chapter-bulk-total-count]");
        const bulkSelectedProgress = chapterBulkPage.querySelector("[data-chapter-bulk-selected-progress]");
        const bulkSelectedInput = chapterBulkPage.querySelector("[data-chapter-bulk-selected-input]");
        const bulkAssignmentForm = chapterBulkPage.querySelector("[data-chapter-bulk-assignment]");
        const bulkChapterSelect = chapterBulkPage.querySelector("[data-chapter-bulk-chapter-select]");
        const bulkActionStatus = chapterBulkPage.querySelector("[data-chapter-bulk-action-status]");
        const bulkNewModal = document.getElementById("chapter-bulk-new-modal");
        const bulkNewForm = document.querySelector("[data-chapter-bulk-new-form]");
        const bulkNewCount = document.querySelector("[data-chapter-bulk-new-count]");
        const bulkNewStatus = document.querySelector("[data-chapter-bulk-new-status]");
        const bulkUrl = chapterBulkPage.dataset.photoUrl || "/api/splash-photos";
        const bulkSeed = chapterBulkPage.dataset.photoSeed || String(Date.now());
        const selectedPhotoIds = [];
        const selectedPhotoSet = new Set();
        const minBulkTileSize = 82;
        let bulkPageIndex = 0;
        let bulkPageSize = 0;
        let bulkTotalPages = 0;
        let bulkTotalPhotos = 0;
        let bulkResizeTimer = null;

        const setBulkStatus = (message) => {
            if (!bulkStatus) {
                return;
            }
            bulkStatus.textContent = message;
            bulkStatus.hidden = !message;
        };

        const bulkGridSize = () => {
            const bounds = bulkGrid.getBoundingClientRect();
            const columns = Math.max(1, Math.floor(bounds.width / minBulkTileSize));
            const rows = Math.max(1, Math.floor(bounds.height / minBulkTileSize));
            bulkGrid.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
            bulkGrid.style.gridTemplateRows = `repeat(${rows}, minmax(0, 1fr))`;
            return columns * rows;
        };

        const updateBulkSelection = () => {
            const count = selectedPhotoIds.length;
            if (bulkSelectedCount) {
                bulkSelectedCount.textContent = `${count} selected`;
            }
            if (bulkTotalCount) {
                bulkTotalCount.textContent = bulkTotalPhotos === 1 ? "1 photo total" : `${bulkTotalPhotos} photos total`;
            }
            if (bulkSelectedProgress) {
                bulkSelectedProgress.max = bulkTotalPhotos || 1;
                bulkSelectedProgress.value = bulkTotalPhotos ? count : 0;
            }
            if (bulkSelectedInput) {
                bulkSelectedInput.value = JSON.stringify(selectedPhotoIds);
            }
            if (bulkChapterSelect) {
                bulkChapterSelect.disabled = count === 0 || bulkAssignmentForm?.dataset.submitting === "true";
                if (count === 0) {
                    bulkChapterSelect.value = "";
                }
            }
        };

        const setBulkActionStatus = (message, isError = false) => {
            if (!bulkActionStatus) {
                return;
            }
            bulkActionStatus.textContent = message;
            bulkActionStatus.classList.toggle("is-error", isError);
        };

        const clearBulkSelection = () => {
            selectedPhotoIds.splice(0, selectedPhotoIds.length);
            selectedPhotoSet.clear();
            bulkGrid.querySelectorAll(".chapter-bulk-thumb").forEach((button) => {
                setBulkThumbSelected(button, false);
            });
            if (bulkChapterSelect) {
                bulkChapterSelect.value = "";
            }
            updateBulkSelection();
        };

        const updateBulkControls = () => {
            const hasPages = bulkTotalPages > 1;
            if (bulkPrevButton) {
                bulkPrevButton.hidden = !hasPages;
            }
            if (bulkNextButton) {
                bulkNextButton.hidden = !hasPages;
            }
            if (bulkPageCount) {
                bulkPageCount.textContent = bulkTotalPages > 0 ? `${bulkPageIndex + 1} of ${bulkTotalPages}` : "";
            }
            if (bulkPageCountWrap) {
                bulkPageCountWrap.hidden = bulkTotalPages <= 0;
            }
            if (bulkPageProgress) {
                bulkPageProgress.max = bulkTotalPages || 1;
                bulkPageProgress.value = bulkTotalPages > 0 ? bulkPageIndex + 1 : 0;
            }
        };

        const setBulkThumbSelected = (button, selected) => {
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-pressed", selected ? "true" : "false");
        };

        const toggleBulkPhoto = (photoId, button) => {
            const normalizedId = String(photoId);
            if (selectedPhotoSet.has(normalizedId)) {
                selectedPhotoSet.delete(normalizedId);
                const selectedIndex = selectedPhotoIds.indexOf(normalizedId);
                if (selectedIndex >= 0) {
                    selectedPhotoIds.splice(selectedIndex, 1);
                }
                setBulkThumbSelected(button, false);
            } else {
                selectedPhotoSet.add(normalizedId);
                selectedPhotoIds.push(normalizedId);
                setBulkThumbSelected(button, true);
            }
            updateBulkSelection();
        };

        const renderBulkPhotos = (photos) => {
            bulkGrid.innerHTML = "";
            photos.forEach((photo) => {
                const photoId = String(photo.id);
                const button = document.createElement("button");
                button.className = "splash-thumb chapter-bulk-thumb";
                button.type = "button";
                button.title = photo.display_date ? `${photo.title} - ${photo.display_date}` : photo.title;
                button.setAttribute("aria-label", `Select ${photo.title || "photo"}`);

                const image = document.createElement("img");
                image.src = photo.thumbnail_url;
                image.alt = photo.title || "Chapter photo";
                image.loading = "lazy";
                image.decoding = "async";

                const check = document.createElement("span");
                check.className = "chapter-bulk-check";
                check.textContent = "OK";
                check.setAttribute("aria-hidden", "true");

                button.append(image, check);
                setBulkThumbSelected(button, selectedPhotoSet.has(photoId));
                button.addEventListener("click", () => toggleBulkPhoto(photoId, button));
                bulkGrid.appendChild(button);
            });
        };

        const loadBulkPage = async (page) => {
            bulkPageSize = bulkGridSize();
            setBulkStatus("Loading photos...");
            const url = new URL(bulkUrl, window.location.href);
            url.searchParams.set("seed", bulkSeed);
            url.searchParams.set("page", String(page));
            url.searchParams.set("page_size", String(bulkPageSize));

            try {
                const response = await csrfFetch(url.toString());
                if (!response.ok) {
                    throw new Error("Photos could not be loaded.");
                }
                const payload = await response.json();
                bulkPageIndex = payload.page || 0;
                bulkTotalPages = payload.total_pages || 0;
                bulkTotalPhotos = payload.total || 0;
                renderBulkPhotos(payload.photos || []);
                setBulkStatus(payload.total ? "" : "No photos yet.");
                updateBulkControls();
                updateBulkSelection();
            } catch (error) {
                renderBulkPhotos([]);
                bulkTotalPages = 0;
                bulkTotalPhotos = 0;
                setBulkStatus("Photos could not be loaded.");
                updateBulkControls();
                updateBulkSelection();
            }
        };

        const moveBulkPage = (delta) => {
            if (bulkTotalPages <= 0) {
                return;
            }
            loadBulkPage(bulkPageIndex + delta);
        };

        if (bulkPrevButton) {
            bulkPrevButton.addEventListener("click", () => moveBulkPage(-1));
        }
        if (bulkNextButton) {
            bulkNextButton.addEventListener("click", () => moveBulkPage(1));
        }

        const closeBulkNewModal = () => {
            if (!bulkNewModal) {
                return;
            }
            bulkNewModal.hidden = true;
            if (bulkChapterSelect) {
                bulkChapterSelect.value = "";
            }
            if (bulkNewForm) {
                bulkNewForm.reset();
                delete bulkNewForm.dataset.submitting;
            }
            if (bulkNewStatus) {
                bulkNewStatus.textContent = "";
                bulkNewStatus.classList.remove("is-error");
            }
            syncModalOpenState();
        };

        const openBulkNewModal = () => {
            if (!bulkNewModal || selectedPhotoIds.length === 0) {
                return;
            }
            if (bulkNewCount) {
                bulkNewCount.textContent = selectedPhotoIds.length === 1 ? "1 selected photo" : `${selectedPhotoIds.length} selected photos`;
            }
            if (bulkNewStatus) {
                bulkNewStatus.textContent = "";
                bulkNewStatus.classList.remove("is-error");
            }
            bulkNewModal.hidden = false;
            syncModalOpenState();
            const titleInput = bulkNewForm?.querySelector("input[name='title']");
            if (titleInput) {
                titleInput.focus();
            }
        };

        const assignBulkPhotosToChapter = async (chapterId) => {
            if (!bulkAssignmentForm || !chapterId || selectedPhotoIds.length === 0) {
                return;
            }
            bulkAssignmentForm.dataset.submitting = "true";
            updateBulkSelection();
            setBulkActionStatus("Adding...");
            try {
                const formData = new FormData();
                formData.set("chapter_id", chapterId);
                formData.set("selected_photo_ids", JSON.stringify(selectedPhotoIds));
                const response = await csrfFetch(bulkAssignmentForm.dataset.bulkAddUrl, {
                    method: "POST",
                    headers: {"Accept": "application/json"},
                    body: formData,
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(payload.error || "Could not add selected photos.");
                }
                setBulkActionStatus(payload.message || "Selected photos added.");
                clearBulkSelection();
            } catch (error) {
                setBulkActionStatus(error.message || "Could not add selected photos.", true);
                if (bulkChapterSelect) {
                    bulkChapterSelect.value = "";
                }
            } finally {
                delete bulkAssignmentForm.dataset.submitting;
                updateBulkSelection();
            }
        };

        if (bulkChapterSelect) {
            bulkChapterSelect.addEventListener("change", () => {
                if (!bulkChapterSelect.value) {
                    return;
                }
                if (bulkChapterSelect.value === "__new__") {
                    openBulkNewModal();
                    return;
                }
                assignBulkPhotosToChapter(bulkChapterSelect.value);
            });
        }

        if (bulkNewModal) {
            bulkNewModal.querySelectorAll("[data-close-chapter-bulk-new]").forEach((button) => {
                button.addEventListener("click", closeBulkNewModal);
            });
        }

        if (bulkNewForm && bulkAssignmentForm) {
            bulkNewForm.addEventListener("submit", async (event) => {
                event.preventDefault();
                if (selectedPhotoIds.length === 0 || bulkNewForm.dataset.submitting === "true") {
                    return;
                }
                bulkNewForm.dataset.submitting = "true";
                if (bulkNewStatus) {
                    bulkNewStatus.textContent = "Creating...";
                    bulkNewStatus.classList.remove("is-error");
                }
                try {
                    const formData = new FormData(bulkNewForm);
                    formData.set("selected_photo_ids", JSON.stringify(selectedPhotoIds));
                    const response = await csrfFetch(bulkAssignmentForm.dataset.bulkCreateUrl, {
                        method: "POST",
                        headers: {"Accept": "application/json"},
                        body: formData,
                    });
                    const payload = await response.json().catch(() => ({}));
                    if (!response.ok) {
                        throw new Error(payload.error || "Could not create chapter.");
                    }
                    setBulkActionStatus(payload.message || "Chapter created.");
                    closeBulkNewModal();
                    clearBulkSelection();
                    if (payload.chapter && bulkChapterSelect) {
                        const option = document.createElement("option");
                        option.value = String(payload.chapter.id);
                        option.textContent = payload.chapter.title;
                        const newOption = bulkChapterSelect.querySelector("option[value='__new__']");
                        bulkChapterSelect.insertBefore(option, newOption);
                    }
                } catch (error) {
                    if (bulkNewStatus) {
                        bulkNewStatus.textContent = error.message || "Could not create chapter.";
                        bulkNewStatus.classList.add("is-error");
                    }
                } finally {
                    delete bulkNewForm.dataset.submitting;
                }
            });
        }

        document.addEventListener("keydown", (event) => {
            if (bulkNewModal && !bulkNewModal.hidden && event.key === "Escape") {
                closeBulkNewModal();
                return;
            }
            if (bulkNewModal && !bulkNewModal.hidden) {
                return;
            }
            if (event.key === "ArrowLeft") {
                moveBulkPage(-1);
            } else if (event.key === "ArrowRight") {
                moveBulkPage(1);
            }
        });

        window.addEventListener("resize", () => {
            window.clearTimeout(bulkResizeTimer);
            bulkResizeTimer = window.setTimeout(() => {
                const nextPageSize = bulkGridSize();
                if (nextPageSize !== bulkPageSize) {
                    loadBulkPage(bulkPageIndex);
                }
            }, 120);
        });

        updateBulkSelection();
        loadBulkPage(0);
    }

    const monthBulkActionsPage = document.querySelector("[data-month-bulk-actions]");
    if (monthBulkActionsPage) {
        const monthBulkGrid = monthBulkActionsPage.querySelector("[data-month-bulk-grid]");
        const monthBulkStatus = monthBulkActionsPage.querySelector("[data-month-bulk-status]");
        const monthBulkPageCountWrap = monthBulkActionsPage.querySelector("[data-month-bulk-page-count-wrap]");
        const monthBulkPageCount = monthBulkActionsPage.querySelector("[data-month-bulk-page-count]");
        const monthBulkPageProgress = monthBulkActionsPage.querySelector("[data-month-bulk-page-progress]");
        const monthBulkPrevButton = monthBulkActionsPage.querySelector("[data-month-bulk-prev]");
        const monthBulkNextButton = monthBulkActionsPage.querySelector("[data-month-bulk-next]");
        const monthBulkSelectedCount = monthBulkActionsPage.querySelector("[data-month-bulk-selected-count]");
        const monthBulkTotalCount = monthBulkActionsPage.querySelector("[data-month-bulk-total-count]");
        const monthBulkSelectedProgress = monthBulkActionsPage.querySelector("[data-month-bulk-selected-progress]");
        const monthBulkForm = monthBulkActionsPage.querySelector("[data-month-bulk-delete-form]");
        const monthBulkDeleteButton = monthBulkActionsPage.querySelector("[data-month-bulk-delete-button]");
        const monthBulkVisibilitySelect = monthBulkActionsPage.querySelector("[data-month-bulk-visibility-select]");
        const monthBulkChapterSelect = monthBulkActionsPage.querySelector("[data-month-bulk-chapter-select]");
        const monthBulkActionStatus = monthBulkActionsPage.querySelector("[data-month-bulk-action-status]");
        const monthBulkNewModal = document.getElementById("month-bulk-new-modal");
        const monthBulkNewForm = document.querySelector("[data-month-bulk-new-form]");
        const monthBulkNewCount = document.querySelector("[data-month-bulk-new-count]");
        const monthBulkNewStatus = document.querySelector("[data-month-bulk-new-status]");
        const monthBulkUrl = monthBulkActionsPage.dataset.photoUrl;
        const monthBulkDeleteUrl = monthBulkActionsPage.dataset.deleteUrl;
        const monthBulkVisibilityUrl = monthBulkActionsPage.dataset.visibilityUrl;
        const monthBulkAddUrl = monthBulkActionsPage.dataset.bulkAddUrl;
        const monthBulkCreateUrl = monthBulkActionsPage.dataset.bulkCreateUrl;
        const selectedMonthPhotoIds = [];
        const selectedMonthPhotoSet = new Set();
        const minMonthBulkTileSize = 82;
        let monthBulkPageIndex = 0;
        let monthBulkPageSize = 0;
        let monthBulkTotalPages = 0;
        let monthBulkTotalPhotos = 0;
        let monthBulkResizeTimer = null;

        const setMonthBulkStatus = (message) => {
            if (!monthBulkStatus) {
                return;
            }
            monthBulkStatus.textContent = message;
            monthBulkStatus.hidden = !message;
        };

        const setMonthBulkActionStatus = (message, isError = false) => {
            if (!monthBulkActionStatus) {
                return;
            }
            monthBulkActionStatus.textContent = message;
            monthBulkActionStatus.classList.toggle("is-error", isError);
        };

        const monthBulkGridSize = () => {
            const bounds = monthBulkGrid.getBoundingClientRect();
            const columns = Math.max(1, Math.floor(bounds.width / minMonthBulkTileSize));
            const rows = Math.max(1, Math.floor(bounds.height / minMonthBulkTileSize));
            monthBulkGrid.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
            monthBulkGrid.style.gridTemplateRows = `repeat(${rows}, minmax(0, 1fr))`;
            return columns * rows;
        };

        const updateMonthBulkSelection = () => {
            const count = selectedMonthPhotoIds.length;
            const submitting = monthBulkActionsPage.dataset.submitting === "true";
            if (monthBulkSelectedCount) {
                monthBulkSelectedCount.textContent = `${count} selected`;
            }
            if (monthBulkTotalCount) {
                monthBulkTotalCount.textContent = monthBulkTotalPhotos === 1 ? "1 photo total" : `${monthBulkTotalPhotos} photos total`;
            }
            if (monthBulkSelectedProgress) {
                monthBulkSelectedProgress.max = monthBulkTotalPhotos || 1;
                monthBulkSelectedProgress.value = monthBulkTotalPhotos ? count : 0;
            }
            if (monthBulkDeleteButton) {
                monthBulkDeleteButton.disabled = count === 0 || submitting;
            }
            if (monthBulkVisibilitySelect) {
                monthBulkVisibilitySelect.disabled = count === 0 || submitting;
                if (count === 0) {
                    monthBulkVisibilitySelect.value = "";
                }
            }
            if (monthBulkChapterSelect) {
                monthBulkChapterSelect.disabled = count === 0 || submitting;
                if (count === 0) {
                    monthBulkChapterSelect.value = "";
                }
            }
        };

        const updateMonthBulkControls = () => {
            const hasPages = monthBulkTotalPages > 1;
            if (monthBulkPrevButton) {
                monthBulkPrevButton.hidden = !hasPages;
            }
            if (monthBulkNextButton) {
                monthBulkNextButton.hidden = !hasPages;
            }
            if (monthBulkPageCount) {
                monthBulkPageCount.textContent = monthBulkTotalPages > 0 ? `${monthBulkPageIndex + 1} of ${monthBulkTotalPages}` : "";
            }
            if (monthBulkPageCountWrap) {
                monthBulkPageCountWrap.hidden = monthBulkTotalPages <= 0;
            }
            if (monthBulkPageProgress) {
                monthBulkPageProgress.max = monthBulkTotalPages || 1;
                monthBulkPageProgress.value = monthBulkTotalPages > 0 ? monthBulkPageIndex + 1 : 0;
            }
        };

        const setMonthBulkThumbSelected = (button, selected) => {
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-pressed", selected ? "true" : "false");
        };

        const toggleMonthBulkPhoto = (photoId, button) => {
            const normalizedId = String(photoId);
            if (selectedMonthPhotoSet.has(normalizedId)) {
                selectedMonthPhotoSet.delete(normalizedId);
                const selectedIndex = selectedMonthPhotoIds.indexOf(normalizedId);
                if (selectedIndex >= 0) {
                    selectedMonthPhotoIds.splice(selectedIndex, 1);
                }
                setMonthBulkThumbSelected(button, false);
            } else {
                selectedMonthPhotoSet.add(normalizedId);
                selectedMonthPhotoIds.push(normalizedId);
                setMonthBulkThumbSelected(button, true);
            }
            setMonthBulkActionStatus("");
            updateMonthBulkSelection();
        };

        const clearMonthBulkSelection = () => {
            selectedMonthPhotoIds.splice(0, selectedMonthPhotoIds.length);
            selectedMonthPhotoSet.clear();
            monthBulkGrid.querySelectorAll(".chapter-bulk-thumb").forEach((button) => {
                setMonthBulkThumbSelected(button, false);
            });
            if (monthBulkVisibilitySelect) {
                monthBulkVisibilitySelect.value = "";
            }
            if (monthBulkChapterSelect) {
                monthBulkChapterSelect.value = "";
            }
            updateMonthBulkSelection();
        };

        const renderMonthBulkPhotos = (photos) => {
            monthBulkGrid.innerHTML = "";
            photos.forEach((photo) => {
                const photoId = String(photo.id);
                const button = document.createElement("button");
                button.className = "splash-thumb chapter-bulk-thumb";
                button.type = "button";
                button.dataset.photoId = photoId;
                button.title = photo.display_date ? `${photo.title} - ${photo.display_date}` : photo.title;
                button.setAttribute("aria-label", `Select ${photo.title || "photo"} for bulk actions`);

                const image = document.createElement("img");
                image.src = photo.thumbnail_url;
                image.alt = photo.title || "Month photo";
                image.loading = "lazy";
                image.decoding = "async";

                const check = document.createElement("span");
                check.className = "chapter-bulk-check";
                check.textContent = "OK";
                check.setAttribute("aria-hidden", "true");

                button.append(image, check);
                setMonthBulkThumbSelected(button, selectedMonthPhotoSet.has(photoId));
                button.addEventListener("click", () => toggleMonthBulkPhoto(photoId, button));
                monthBulkGrid.appendChild(button);
            });
        };

        const loadMonthBulkPage = async (page) => {
            monthBulkPageSize = monthBulkGridSize();
            setMonthBulkStatus("Loading photos...");
            const url = new URL(monthBulkUrl, window.location.href);
            url.searchParams.set("page", String(page));
            url.searchParams.set("page_size", String(monthBulkPageSize));

            try {
                const response = await csrfFetch(url.toString());
                if (!response.ok) {
                    throw new Error("Photos could not be loaded.");
                }
                const payload = await response.json();
                monthBulkPageIndex = payload.page || 0;
                monthBulkTotalPages = payload.total_pages || 0;
                monthBulkTotalPhotos = payload.total || 0;
                renderMonthBulkPhotos(payload.photos || []);
                setMonthBulkStatus(payload.total ? "" : "No photos in this month.");
                updateMonthBulkControls();
                updateMonthBulkSelection();
            } catch (error) {
                renderMonthBulkPhotos([]);
                monthBulkTotalPages = 0;
                monthBulkTotalPhotos = 0;
                setMonthBulkStatus("Photos could not be loaded.");
                updateMonthBulkControls();
                updateMonthBulkSelection();
            }
        };

        const moveMonthBulkPage = (delta) => {
            if (monthBulkTotalPages <= 0) {
                return;
            }
            loadMonthBulkPage(monthBulkPageIndex + delta);
        };

        const removeDeletedMonthSelections = (deletedIds) => {
            const deletedSet = new Set((deletedIds || []).map((photoId) => String(photoId)));
            for (let index = selectedMonthPhotoIds.length - 1; index >= 0; index -= 1) {
                if (deletedSet.has(selectedMonthPhotoIds[index])) {
                    selectedMonthPhotoSet.delete(selectedMonthPhotoIds[index]);
                    selectedMonthPhotoIds.splice(index, 1);
                }
            }
        };

        const setMonthBulkSubmitting = (isSubmitting) => {
            if (isSubmitting) {
                monthBulkActionsPage.dataset.submitting = "true";
            } else {
                delete monthBulkActionsPage.dataset.submitting;
            }
            updateMonthBulkSelection();
        };

        const closeMonthBulkNewModal = () => {
            if (!monthBulkNewModal) {
                return;
            }
            monthBulkNewModal.hidden = true;
            if (monthBulkChapterSelect) {
                monthBulkChapterSelect.value = "";
            }
            if (monthBulkNewForm) {
                monthBulkNewForm.reset();
                delete monthBulkNewForm.dataset.submitting;
            }
            if (monthBulkNewStatus) {
                monthBulkNewStatus.textContent = "";
                monthBulkNewStatus.classList.remove("is-error");
            }
            syncModalOpenState();
        };

        const openMonthBulkNewModal = () => {
            if (!monthBulkNewModal || selectedMonthPhotoIds.length === 0) {
                return;
            }
            if (monthBulkNewCount) {
                monthBulkNewCount.textContent = selectedMonthPhotoIds.length === 1 ? "1 selected photo" : `${selectedMonthPhotoIds.length} selected photos`;
            }
            if (monthBulkNewStatus) {
                monthBulkNewStatus.textContent = "";
                monthBulkNewStatus.classList.remove("is-error");
            }
            monthBulkNewModal.hidden = false;
            syncModalOpenState();
            const titleInput = monthBulkNewForm?.querySelector("input[name='title']");
            if (titleInput) {
                titleInput.focus();
            }
        };

        const assignMonthBulkPhotosToChapter = async (chapterId) => {
            if (!chapterId || selectedMonthPhotoIds.length === 0 || !monthBulkAddUrl) {
                return;
            }
            setMonthBulkSubmitting(true);
            setMonthBulkActionStatus("Adding to chapter...");
            try {
                const formData = new FormData();
                formData.set("chapter_id", chapterId);
                formData.set("selected_photo_ids", JSON.stringify(selectedMonthPhotoIds));
                const response = await csrfFetch(monthBulkAddUrl, {
                    method: "POST",
                    headers: {"Accept": "application/json"},
                    body: formData,
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(payload.error || "Could not add selected photos.");
                }
                setMonthBulkActionStatus(payload.message || "Selected photos added.");
                clearMonthBulkSelection();
            } catch (error) {
                setMonthBulkActionStatus(error.message || "Could not add selected photos.", true);
                if (monthBulkChapterSelect) {
                    monthBulkChapterSelect.value = "";
                }
            } finally {
                setMonthBulkSubmitting(false);
            }
        };

        const applyMonthBulkVisibility = async (visibility) => {
            if (!visibility || selectedMonthPhotoIds.length === 0 || !monthBulkVisibilityUrl) {
                return;
            }
            const count = selectedMonthPhotoIds.length;
            const confirmed = await requestConfirmation({
                title: "Apply visibility change?",
                message: `This changes visibility for ${count} selected photo${count === 1 ? "" : "s"}.`,
                confirmLabel: "Apply visibility",
            });
            if (!confirmed) {
                monthBulkVisibilitySelect.value = "";
                return;
            }

            setMonthBulkSubmitting(true);
            setMonthBulkActionStatus("Updating visibility...");
            try {
                const response = await csrfFetch(monthBulkVisibilityUrl, {
                    method: "POST",
                    headers: {
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({photo_ids: selectedMonthPhotoIds, visibility}),
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(payload.error || "Could not update visibility.");
                }
                setMonthBulkActionStatus(payload.message || "Visibility updated.");
                clearMonthBulkSelection();
            } catch (error) {
                setMonthBulkActionStatus(error.message || "Could not update visibility.", true);
                if (monthBulkVisibilitySelect) {
                    monthBulkVisibilitySelect.value = "";
                }
            } finally {
                setMonthBulkSubmitting(false);
            }
        };

        if (monthBulkPrevButton) {
            monthBulkPrevButton.addEventListener("click", () => moveMonthBulkPage(-1));
        }
        if (monthBulkNextButton) {
            monthBulkNextButton.addEventListener("click", () => moveMonthBulkPage(1));
        }

        if (monthBulkVisibilitySelect) {
            monthBulkVisibilitySelect.addEventListener("change", () => {
                if (!monthBulkVisibilitySelect.value) {
                    return;
                }
                applyMonthBulkVisibility(monthBulkVisibilitySelect.value);
            });
        }

        if (monthBulkChapterSelect) {
            monthBulkChapterSelect.addEventListener("change", () => {
                if (!monthBulkChapterSelect.value) {
                    return;
                }
                if (monthBulkChapterSelect.value === "__new__") {
                    openMonthBulkNewModal();
                    return;
                }
                assignMonthBulkPhotosToChapter(monthBulkChapterSelect.value);
            });
        }

        if (monthBulkNewModal) {
            monthBulkNewModal.querySelectorAll("[data-close-month-bulk-new]").forEach((button) => {
                button.addEventListener("click", closeMonthBulkNewModal);
            });
        }

        if (monthBulkNewForm) {
            monthBulkNewForm.addEventListener("submit", async (event) => {
                event.preventDefault();
                if (selectedMonthPhotoIds.length === 0 || monthBulkNewForm.dataset.submitting === "true" || !monthBulkCreateUrl) {
                    return;
                }
                monthBulkNewForm.dataset.submitting = "true";
                if (monthBulkNewStatus) {
                    monthBulkNewStatus.textContent = "Creating...";
                    monthBulkNewStatus.classList.remove("is-error");
                }
                try {
                    const formData = new FormData(monthBulkNewForm);
                    formData.set("selected_photo_ids", JSON.stringify(selectedMonthPhotoIds));
                    const response = await csrfFetch(monthBulkCreateUrl, {
                        method: "POST",
                        headers: {"Accept": "application/json"},
                        body: formData,
                    });
                    const payload = await response.json().catch(() => ({}));
                    if (!response.ok) {
                        throw new Error(payload.error || "Could not create chapter.");
                    }
                    setMonthBulkActionStatus(payload.message || "Chapter created.");
                    closeMonthBulkNewModal();
                    clearMonthBulkSelection();
                    if (payload.chapter && monthBulkChapterSelect) {
                        const option = document.createElement("option");
                        option.value = String(payload.chapter.id);
                        option.textContent = payload.chapter.title;
                        const newOption = monthBulkChapterSelect.querySelector("option[value='__new__']");
                        monthBulkChapterSelect.insertBefore(option, newOption);
                    }
                } catch (error) {
                    if (monthBulkNewStatus) {
                        monthBulkNewStatus.textContent = error.message || "Could not create chapter.";
                        monthBulkNewStatus.classList.add("is-error");
                    }
                } finally {
                    delete monthBulkNewForm.dataset.submitting;
                }
            });
        }

        if (monthBulkForm) {
            monthBulkForm.addEventListener("submit", async (event) => {
                event.preventDefault();
                if (selectedMonthPhotoIds.length === 0 || monthBulkActionsPage.dataset.submitting === "true") {
                    return;
                }

                const count = selectedMonthPhotoIds.length;
                const confirmed = await requestConfirmation({
                    title: "Delete selected photos?",
                    message: `This permanently removes ${count} selected photo${count === 1 ? "" : "s"}, messages, chapter placements, tags, likes, loves, and related notifications.`,
                    confirmLabel: "Delete selected",
                    danger: true,
                });
                if (!confirmed) {
                    return;
                }

                setMonthBulkSubmitting(true);
                setMonthBulkActionStatus("Deleting...");
                try {
                    const response = await csrfFetch(monthBulkDeleteUrl, {
                        method: "POST",
                        headers: {
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify({photo_ids: selectedMonthPhotoIds}),
                    });
                    const payload = await response.json().catch(() => ({}));
                    if (!response.ok) {
                        throw new Error(payload.error || "Could not delete selected photos.");
                    }
                    removeDeletedMonthSelections(payload.deleted_photo_ids || []);
                    setMonthBulkActionStatus(payload.message || "Deleted selected photos.");
                    await loadMonthBulkPage(monthBulkPageIndex);
                } catch (error) {
                    setMonthBulkActionStatus(error.message || "Could not delete selected photos.", true);
                } finally {
                    setMonthBulkSubmitting(false);
                }
            });
        }

        document.addEventListener("keydown", (event) => {
            if (monthBulkNewModal && !monthBulkNewModal.hidden && event.key === "Escape") {
                closeMonthBulkNewModal();
                return;
            }
            if (monthBulkNewModal && !monthBulkNewModal.hidden) {
                return;
            }
            if (event.key === "ArrowLeft") {
                moveMonthBulkPage(-1);
            } else if (event.key === "ArrowRight") {
                moveMonthBulkPage(1);
            }
        });

        window.addEventListener("resize", () => {
            window.clearTimeout(monthBulkResizeTimer);
            monthBulkResizeTimer = window.setTimeout(() => {
                const nextPageSize = monthBulkGridSize();
                if (nextPageSize !== monthBulkPageSize) {
                    loadMonthBulkPage(monthBulkPageIndex);
                }
            }, 120);
        });

        updateMonthBulkSelection();
        loadMonthBulkPage(0);
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
        const carouselPhotoModal = document.getElementById("carousel-photo-modal");
        const carouselPhotoModalImage = document.getElementById("carousel-photo-modal-image");
        const carouselPhotoModalTitle = document.getElementById("carousel-photo-modal-title");
        const carouselPhotoModalDate = document.getElementById("carousel-photo-modal-date");
        const carouselPhotoModalCaption = document.getElementById("carousel-photo-modal-caption");
        const carouselPlaybackProgress = document.getElementById("carousel-playback-progress");
        const carouselPlaybackBar = document.getElementById("carousel-playback-bar");
        const carouselPlaybackFill = document.getElementById("carousel-playback-fill");
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

        const stopCarouselProgress = () => {
            if (carouselPlaybackBar) {
                carouselPlaybackBar.value = 0;
            }
            if (carouselPlaybackFill) {
                carouselPlaybackFill.style.transition = "none";
                carouselPlaybackFill.style.width = "0%";
            }
            if (carouselPlaybackProgress) {
                carouselPlaybackProgress.hidden = true;
            }
        };

        const startCarouselProgress = (durationMs) => {
            stopCarouselProgress();
            if (!carouselPlaybackBar || !carouselPlaybackProgress || !carouselPlaybackFill || durationMs <= 0) {
                return;
            }

            carouselPlaybackProgress.hidden = false;
            carouselPlaybackBar.max = 100;
            carouselPlaybackBar.value = 0;
            carouselPlaybackFill.style.transition = "none";
            carouselPlaybackFill.style.width = "0%";
            window.requestAnimationFrame(() => {
                if (carouselPaused || allItemsModal.hidden) {
                    return;
                }
                carouselPlaybackBar.value = 100;
                carouselPlaybackFill.style.transition = `width ${durationMs}ms linear`;
                carouselPlaybackFill.style.width = "100%";
            });
        };

        const clearCarouselTimers = () => {
            carouselTimers.forEach((timer) => clearTimeout(timer));
            carouselTimers = [];
            stopCarouselProgress();
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

        const closeCarouselPhotoModal = ({keepBodyOpen = false, restorePlayback = true} = {}) => {
            if (!carouselPhotoModal) {
                return;
            }

            carouselPhotoModal.hidden = true;
            if (carouselPhotoModalImage) {
                carouselPhotoModalImage.removeAttribute("src");
                carouselPhotoModalImage.alt = "";
            }
            if (restorePlayback && !allItemsModal.hidden) {
                resumeCarousel();
            }
            if (!keepBodyOpen && allItemsModal.hidden) {
                document.body.classList.remove("modal-open");
            }
        };

        const openCarouselPhotoModal = (item) => {
            if (!carouselPhotoModal || !carouselPhotoModalImage) {
                return;
            }

            if (!carouselPaused) {
                pauseCarousel();
            }
            carouselPhotoModalImage.src = item.image_url;
            carouselPhotoModalImage.alt = item.title || "Timeline photo";
            if (carouselPhotoModalTitle) {
                carouselPhotoModalTitle.textContent = item.title || "Picture";
            }
            if (carouselPhotoModalDate) {
                carouselPhotoModalDate.textContent = item.date_label || "";
            }
            if (carouselPhotoModalCaption) {
                carouselPhotoModalCaption.textContent = item.caption || "";
                carouselPhotoModalCaption.hidden = !item.caption;
            }
            carouselPhotoModal.hidden = false;
            document.body.classList.add("modal-open");
        };

        const closeAllItemsModal = () => {
            clearCarouselTimers();
            closeCarouselPhotoModal({keepBodyOpen: true, restorePlayback: false});
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

        const carouselItemsUrl = () => {
            const url = new URL(allItemsModal.dataset.itemsUrl || "/api/timeline-items", window.location.href);
            url.searchParams.set("include_messages", "0");
            return url.toString();
        };

        const renderCarouselMessages = (list, messages, status = "ready") => {
            list.innerHTML = "";
            if (status === "loading") {
                const loading = document.createElement("p");
                loading.className = "empty-state compact";
                loading.textContent = "Loading messages...";
                list.appendChild(loading);
                return;
            }
            if (status === "error") {
                const error = document.createElement("p");
                error.className = "empty-state compact";
                error.textContent = "Messages could not be loaded.";
                list.appendChild(error);
                return;
            }

            const visibleMessages = Array.isArray(messages) ? messages : [];
            if (visibleMessages.length === 0) {
                const empty = document.createElement("p");
                empty.className = "empty-state compact";
                empty.textContent = "No messages yet.";
                list.appendChild(empty);
                return;
            }

            visibleMessages.forEach((message) => {
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

        const loadCarouselMessages = (item, list) => {
            if (Array.isArray(item.messages)) {
                item.messagesLoaded = true;
                renderCarouselMessages(list, item.messages);
                return;
            }

            if (!item.messages_url) {
                item.messages = [];
                item.messagesLoaded = true;
                renderCarouselMessages(list, item.messages);
                return;
            }

            renderCarouselMessages(list, [], "loading");

            if (!item.messagesPromise) {
                item.messagesPromise = csrfFetch(item.messages_url)
                    .then((response) => {
                        if (!response.ok) {
                            throw new Error("Messages could not be loaded.");
                        }
                        return response.json();
                    })
                    .then((messages) => {
                        item.messages = Array.isArray(messages) ? messages : [];
                        item.messagesLoaded = true;
                        item.messagesLoadError = false;
                        return item.messages;
                    })
                    .catch(() => {
                        item.messages = [];
                        item.messagesLoaded = true;
                        item.messagesLoadError = true;
                        return item.messages;
                    })
                    .finally(() => {
                        item.messagesPromise = null;
                    });
            }

            item.messagesPromise.then(() => {
                renderCarouselMessages(list, item.messages, item.messagesLoadError ? "error" : "ready");
            });
        };

        const renderCarouselMessagePanel = (item) => {
            const messagePanel = document.createElement("aside");
            messagePanel.className = "carousel-message-panel";

            const heading = document.createElement("h3");
            heading.textContent = "Messages";

            const list = document.createElement("div");
            list.className = "carousel-message-list";
            loadCarouselMessages(item, list);

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
                const response = await csrfFetch(item.messages_url, {
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
                item.messagesLoaded = true;
                item.messagesLoadError = false;
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
            if (item.kind === "photo" && item.title) {
                const title = document.createElement("span");
                title.className = "carousel-item-photo-title";
                title.textContent = item.title;
                meta.appendChild(title);
            }
            if (item.kind === "photo" && item.caption) {
                const caption = document.createElement("span");
                caption.className = "carousel-item-caption";
                caption.textContent = item.caption;
                meta.appendChild(caption);
            }

            const media = document.createElement("div");
            media.className = "carousel-window-media";

            if (item.kind === "photo") {
                const button = document.createElement("button");
                button.className = "carousel-image-button";
                button.type = "button";
                button.setAttribute("aria-label", `Open ${item.title || "photo"} full size`);

                const image = document.createElement("img");
                image.className = "carousel-image";
                image.decoding = "async";
                image.loading = slotIndex === 0 ? "eager" : "lazy";
                if ("fetchPriority" in image) {
                    image.fetchPriority = slotIndex === 0 ? "high" : "low";
                }
                image.src = item.image_url;
                image.alt = item.title || "Timeline photo";
                button.appendChild(image);
                button.addEventListener("click", () => openCarouselPhotoModal(item));
                media.appendChild(button);
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
            startCarouselProgress(visibleDelay);
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

            const itemsUrl = carouselItemsUrl();
            const response = await csrfFetch(itemsUrl);
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

        if (carouselPhotoModal) {
            carouselPhotoModal.querySelectorAll("[data-close-carousel-photo-modal]").forEach((button) => {
                button.addEventListener("click", () => closeCarouselPhotoModal({keepBodyOpen: true}));
            });
        }

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && carouselPhotoModal && !carouselPhotoModal.hidden) {
                event.preventDefault();
                closeCarouselPhotoModal({keepBodyOpen: true});
                return;
            }
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

        document.addEventListener("click", (event) => {
            const button = event.target instanceof Element ? event.target.closest(".connect-open-button") : null;
            if (!button) {
                return;
            }
            connectionRecipientInput.value = button.dataset.recipientId || "";
            connectionTarget.textContent = button.dataset.recipientName || "";
            connectionRelationInputs.forEach((input) => {
                input.checked = input.value === "friend";
            });
            connectionModal.hidden = false;
            document.body.classList.add("modal-open");
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

    document.addEventListener("click", async (event) => {
        const button = event.target instanceof Element ? event.target.closest("[data-reaction-button]") : null;
        if (!button) {
            return;
        }

        event.preventDefault();
        event.stopPropagation();

        const bar = button.closest("[data-reaction-bar]");
        if (!bar || !bar.dataset.reactionUrl) {
            return;
        }

        const reactionValue = button.dataset.reactionValue;
        const alreadySelected = bar.dataset.userReaction === reactionValue;
        const response = await csrfFetch(bar.dataset.reactionUrl, {
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

    document.querySelectorAll("[data-chapter-sequence]").forEach((sequence) => {
        const reorderUrl = sequence.dataset.reorderUrl;
        const status = document.querySelector("[data-reorder-status]");
        let draggedItem = null;

        const chapterItems = () => Array.from(sequence.querySelectorAll(".chapter-sequence-item"));

        const setReorderStatus = (message) => {
            if (status) {
                status.textContent = message;
            }
        };

        const updateChapterPositions = () => {
            chapterItems().forEach((item, index) => {
                const position = item.querySelector(".chapter-position");
                if (position) {
                    position.textContent = String(index + 1);
                }
            });
        };

        const saveChapterOrder = async () => {
            if (!reorderUrl) {
                return;
            }

            const itemIds = chapterItems().map((item) => Number(item.dataset.chapterItemId));
            setReorderStatus("Saving order...");
            try {
                const response = await csrfFetch(reorderUrl, {
                    method: "POST",
                    headers: {
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({item_ids: itemIds}),
                });
                if (!response.ok) {
                    throw new Error("Order could not be saved.");
                }
                setReorderStatus("Order saved.");
            } catch (error) {
                setReorderStatus("Order could not be saved. Refresh and try again.");
            }
        };

        sequence.addEventListener("dragstart", (event) => {
            const handle = event.target.closest(".chapter-drag-handle");
            if (!handle) {
                event.preventDefault();
                return;
            }

            draggedItem = handle.closest(".chapter-sequence-item");
            if (!draggedItem) {
                event.preventDefault();
                return;
            }

            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", draggedItem.dataset.chapterItemId || "");
            window.setTimeout(() => {
                draggedItem.classList.add("is-dragging");
            }, 0);
        });

        sequence.addEventListener("dragover", (event) => {
            if (!draggedItem) {
                return;
            }

            const target = event.target.closest(".chapter-sequence-item");
            if (!target || target === draggedItem || !sequence.contains(target)) {
                return;
            }

            event.preventDefault();
            chapterItems().forEach((item) => item.classList.remove("is-drop-target"));
            target.classList.add("is-drop-target");
            const rect = target.getBoundingClientRect();
            const shouldMoveAfter = event.clientY > rect.top + rect.height / 2;
            sequence.insertBefore(draggedItem, shouldMoveAfter ? target.nextSibling : target);
            updateChapterPositions();
        });

        sequence.addEventListener("drop", async (event) => {
            if (!draggedItem) {
                return;
            }

            event.preventDefault();
            chapterItems().forEach((item) => item.classList.remove("is-drop-target", "is-dragging"));
            draggedItem = null;
            updateChapterPositions();
            await saveChapterOrder();
        });

        sequence.addEventListener("dragend", () => {
            chapterItems().forEach((item) => item.classList.remove("is-drop-target", "is-dragging"));
            draggedItem = null;
            updateChapterPositions();
        });
    });

    const homePhotoModal = document.getElementById("home-photo-modal");
    if (homePhotoModal) {
        const homePhotoSection = document.querySelector("[data-home-refresh-url]");
        const homePhotoGrid = homePhotoSection ? homePhotoSection.querySelector("[data-home-photo-grid]") : null;
        const homeEmptyState = homePhotoSection ? homePhotoSection.querySelector("[data-home-empty-state]") : null;
        const homePhotoImage = document.getElementById("home-photo-modal-image");
        const homePhotoTitle = document.getElementById("home-photo-modal-title");
        const homePhotoOwner = document.getElementById("home-photo-modal-owner");
        const homePhotoDate = document.getElementById("home-photo-modal-date");
        const homePhotoCaption = document.getElementById("home-photo-modal-caption");
        const homePhotoMessageList = document.getElementById("home-photo-message-list");
        const homePhotoMessageForm = document.getElementById("home-photo-message-form");
        const homePhotoMessageInput = homePhotoMessageForm ? homePhotoMessageForm.querySelector("textarea") : null;
        let activeHomePhotoId = null;
        let activeHomePhotoMessagesUrl = "";
        let homeRefreshTimer = null;

        const homeText = (value) => value == null ? "" : String(value);

        const buildHomeReactionBar = (photo) => {
            const reactions = photo.reactions || {};
            const bar = document.createElement("div");
            bar.className = "reaction-bar ";
            bar.dataset.reactionBar = "true";
            bar.dataset.reactionKind = photo.kind || "photo";
            bar.dataset.reactionId = String(photo.id);
            bar.dataset.reactionUrl = reactions.reaction_url || "";
            bar.dataset.userReaction = reactions.user_reaction || "";

            ["like", "love"].forEach((reaction) => {
                const button = document.createElement("button");
                const isActive = reactions.user_reaction === reaction;
                button.className = `reaction-button${isActive ? " is-active" : ""}`;
                button.type = "button";
                button.dataset.reactionButton = "true";
                button.dataset.reactionValue = reaction;
                button.title = reaction === "like" ? "Like" : "Love";
                button.setAttribute("aria-label", button.title);
                button.setAttribute("aria-pressed", isActive ? "true" : "false");

                const icon = document.createElement("span");
                icon.className = "reaction-icon";
                icon.setAttribute("aria-hidden", "true");
                icon.textContent = reaction === "like" ? "\u{1F44D}" : "\u2665";

                const count = document.createElement("span");
                count.className = "reaction-count";
                count.dataset.reactionCount = reaction;
                count.textContent = String(reactions[`${reaction}_count`] || 0);

                button.append(icon, count);
                bar.appendChild(button);
            });

            return bar;
        };

        const buildHomePhotoCard = (photo) => {
            const item = document.createElement("figure");
            item.className = "public-photo-item";

            const button = document.createElement("button");
            button.className = "public-photo-card";
            button.type = "button";
            button.title = `${homeText(photo.owner_name)}${photo.display_date ? ` - ${photo.display_date}` : ""}`;
            button.dataset.fullSrc = homeText(photo.image_url);
            button.dataset.photoTitle = homeText(photo.title);
            button.dataset.photoCaption = homeText(photo.caption);
            button.dataset.photoOwner = homeText(photo.owner_name);
            button.dataset.photoDate = homeText(photo.display_date);
            button.dataset.messagesUrl = homeText(photo.messages_url);
            button.dataset.photoId = homeText(photo.id);

            const image = document.createElement("img");
            image.src = homeText(photo.image_url);
            image.alt = homeText(photo.title) || "Public photo";
            image.loading = "lazy";
            button.appendChild(image);

            const caption = document.createElement("figcaption");
            caption.className = "public-photo-meta";

            const ownerRow = document.createElement("div");
            ownerRow.className = "public-photo-owner-row";

            const owner = document.createElement("strong");
            owner.textContent = homeText(photo.owner_name);
            ownerRow.appendChild(owner);

            const connectionState = photo.connection_state || {};
            if (connectionState.can_request) {
                const connectButton = document.createElement("button");
                connectButton.className = "button secondary small connect-open-button";
                connectButton.type = "button";
                connectButton.dataset.recipientId = homeText(photo.owner_id);
                connectButton.dataset.recipientName = homeText(photo.owner_name);
                connectButton.textContent = "Add connection";
                ownerRow.appendChild(connectButton);
            } else {
                const badge = document.createElement("span");
                badge.className = "status-badge";
                badge.textContent = homeText(connectionState.label);
                ownerRow.appendChild(badge);
            }

            const date = document.createElement("span");
            date.textContent = homeText(photo.display_date) || "No date";
            const title = document.createElement("span");
            title.textContent = homeText(photo.title);
            const comments = document.createElement("span");
            const messageCount = Number(photo.message_count || 0);
            comments.dataset.publicMessageCount = "true";
            comments.dataset.photoId = homeText(photo.id);
            comments.textContent = `${messageCount} ${messageCount === 1 ? "comment" : "comments"}`;

            caption.append(ownerRow, date, title, comments);
            item.append(button, buildHomeReactionBar(photo), caption);
            return item;
        };

        const renderHomePhotoGrid = (photos) => {
            if (!homePhotoGrid) {
                return;
            }
            homePhotoGrid.innerHTML = "";
            photos.forEach((photo) => {
                homePhotoGrid.appendChild(buildHomePhotoCard(photo));
            });
            if (homeEmptyState) {
                homeEmptyState.hidden = photos.length > 0;
            }
        };

        const refreshHomePhotoGrid = async () => {
            if (!homePhotoSection || !homePhotoGrid || !homePhotoSection.dataset.homeRefreshUrl || !homePhotoModal.hidden || document.hidden) {
                return;
            }

            homePhotoGrid.classList.add("is-refreshing");
            window.setTimeout(async () => {
                try {
                    const response = await csrfFetch(homePhotoSection.dataset.homeRefreshUrl, {
                        headers: {"Accept": "application/json"},
                    });
                    if (response.ok) {
                        const payload = await response.json();
                        renderHomePhotoGrid(Array.isArray(payload.photos) ? payload.photos : []);
                    }
                } finally {
                    requestAnimationFrame(() => {
                        homePhotoGrid.classList.remove("is-refreshing");
                    });
                }
            }, 320);
        };

        if (homePhotoSection && homePhotoGrid) {
            const interval = Math.max(Number(homePhotoSection.dataset.homeRefreshInterval || 12000), 3000);
            homeRefreshTimer = window.setInterval(refreshHomePhotoGrid, interval);
        }

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

        const updateHomePhotoMessageCount = (photoId, count) => {
            if (!photoId) {
                return;
            }
            document.querySelectorAll(`[data-public-message-count][data-photo-id="${photoId}"]`).forEach((counter) => {
                counter.textContent = `${count} ${count === 1 ? "comment" : "comments"}`;
            });
        };

        const closeHomePhotoModal = () => {
            homePhotoModal.hidden = true;
            homePhotoImage.removeAttribute("src");
            homePhotoMessageList.innerHTML = "";
            activeHomePhotoId = null;
            activeHomePhotoMessagesUrl = "";
            if (homePhotoMessageInput) {
                homePhotoMessageInput.value = "";
            }
            document.body.classList.remove("modal-open");
        };

        const openHomePhotoModal = async (button) => {
            activeHomePhotoId = button.dataset.photoId || "";
            activeHomePhotoMessagesUrl = button.dataset.messagesUrl || "";
            homePhotoImage.src = button.dataset.fullSrc;
            homePhotoImage.alt = button.dataset.photoTitle || "Selected public photo";
            homePhotoTitle.textContent = button.dataset.photoTitle || "Public photo";
            homePhotoOwner.textContent = button.dataset.photoOwner || "";
            homePhotoDate.textContent = button.dataset.photoDate || "";
            if (homePhotoMessageInput) {
                homePhotoMessageInput.value = "";
            }
            homePhotoCaption.textContent = button.dataset.photoCaption || "";
            renderHomePhotoMessages([]);
            homePhotoModal.hidden = false;
            document.body.classList.add("modal-open");

            if (!activeHomePhotoMessagesUrl) {
                return;
            }

            try {
                const response = await csrfFetch(activeHomePhotoMessagesUrl);
                const messages = response.ok ? await response.json() : [];
                renderHomePhotoMessages(messages);
                updateHomePhotoMessageCount(activeHomePhotoId, messages.length);
            } catch (error) {
                renderHomePhotoMessages([]);
            }
        };

        if (homePhotoGrid) {
            homePhotoGrid.addEventListener("click", (event) => {
                const button = event.target instanceof Element ? event.target.closest(".public-photo-card") : null;
                if (button) {
                    openHomePhotoModal(button);
                }
            });
        }

        homePhotoModal.querySelectorAll("[data-close-home-photo-modal]").forEach((button) => {
            button.addEventListener("click", closeHomePhotoModal);
        });

        if (homePhotoMessageForm && homePhotoMessageInput) {
            homePhotoMessageForm.addEventListener("submit", async (event) => {
                event.preventDefault();
                const body = homePhotoMessageInput.value.trim();
                if (!body || !activeHomePhotoMessagesUrl) {
                    return;
                }

                const submit = homePhotoMessageForm.querySelector("button[type='submit']");
                if (submit) {
                    submit.disabled = true;
                }
                try {
                    const response = await csrfFetch(activeHomePhotoMessagesUrl, {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({body}),
                    });

                    if (!response.ok) {
                        return;
                    }

                    const message = await response.json();
                    const existingMessages = Array.from(homePhotoMessageList.querySelectorAll(".message-item")).map((item) => ({
                        author_name: item.querySelector(".message-author")?.textContent || "",
                        body: item.querySelector("p")?.textContent || "",
                        created_at: item.querySelector("time")?.textContent || "",
                    }));
                    const messages = [...existingMessages, message];
                    homePhotoMessageInput.value = "";
                    renderHomePhotoMessages(messages);
                    updateHomePhotoMessageCount(activeHomePhotoId, messages.length);
                } finally {
                    if (submit) {
                        submit.disabled = false;
                    }
                }
            });
        }

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
        const readonlyPhotoTitle = document.getElementById("readonly-photo-modal-title");
        const readonlyPhotoDate = document.getElementById("readonly-photo-modal-date");
        const readonlyPhotoCaption = document.getElementById("readonly-photo-caption-view");
        const readonlyMessageList = document.getElementById("readonly-message-list");
        const readonlyTextDate = document.getElementById("readonly-text-modal-date");
        const readonlyPhotoPeopleSummary = document.getElementById("readonly-photo-people-summary");
        const readonlyTextPeopleSummary = document.getElementById("readonly-text-people-summary");
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
            readonlyModalImage.alt = button.dataset.photoDisplayTitle || "Selected timeline picture";
            readonlyPhotoTitle.textContent = button.dataset.photoDisplayTitle || "Picture";
            readonlyPhotoDate.textContent = button.dataset.photoDate || "";
            readonlyPhotoCaption.textContent = button.dataset.photoCaption || "";
            setPeopleSummary(readonlyPhotoPeopleSummary, button.dataset.photoPeople || "");
            setPrivacySummary(
                readonlyPhotoPrivacySummary,
                button.dataset.privacyLabel,
                button.dataset.privacyHelp
            );
            readonlyPhotoModal.hidden = false;
            setReadonlyModalOpenState();

            try {
                const response = await csrfFetch(button.dataset.messagesUrl);
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
            setPeopleSummary(readonlyTextPeopleSummary, button.dataset.entryPeople || "");
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
    const modalTitle = document.getElementById("modal-title");
    const modalDate = document.getElementById("modal-date");
    const photoLocationSummary = document.getElementById("photo-location-summary");
    const photoPeopleSummary = document.getElementById("photo-people-summary");
    const photoCaptionView = document.getElementById("photo-caption-view");
    const photoPrivacySummary = document.getElementById("photo-privacy-summary");
    const messageList = document.getElementById("message-list");
    const messageForm = document.getElementById("message-form");
    const messageInput = messageForm.querySelector("textarea");
    const deletePhotoButton = document.getElementById("delete-photo-button");
    const photoDetailsForm = document.getElementById("photo-details-form");
    const photoDetailsTitle = photoDetailsForm.querySelector("input[name='title']");
    const photoDetailsCaption = photoDetailsForm.querySelector("textarea[name='caption']");
    const photoGuidedPrompts = document.getElementById("photo-guided-prompts");
    const photoTagsForm = document.getElementById("photo-tags-form");
    const photoTagInputs = Array.from(photoTagsForm.querySelectorAll("input[name='tags']"));
    const photoPeopleForm = document.getElementById("photo-people-form");
    const photoPeopleInput = photoPeopleForm.querySelector("input[name='people']");
    const photoLocationForm = document.getElementById("photo-location-form");
    const photoLocationName = photoLocationForm.querySelector("input[name='location_name']");
    const photoLatitude = photoLocationForm.querySelector("input[name='latitude']");
    const photoLongitude = photoLocationForm.querySelector("input[name='longitude']");

    const textModalDate = document.getElementById("text-modal-date");
    const textLocationSummary = document.getElementById("text-location-summary");
    const textPeopleSummary = document.getElementById("text-people-summary");
    const textPrivacySummary = document.getElementById("text-privacy-summary");
    const textEntryView = document.getElementById("text-entry-view");
    const textGuidedPrompts = document.getElementById("text-guided-prompts");
    const textEntryEditForm = document.getElementById("text-entry-edit-form");
    const textEntryEditBody = textEntryEditForm.querySelector("textarea");
    const textEntryEditDate = textEntryEditForm.querySelector("input[name='entry_date']");
    const textEntryPeopleInput = textEntryEditForm.querySelector("input[name='people']");
    const textEntryLocationName = textEntryEditForm.querySelector("input[name='location_name']");
    const textEntryLatitude = textEntryEditForm.querySelector("input[name='latitude']");
    const textEntryLongitude = textEntryEditForm.querySelector("input[name='longitude']");
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

    const removePhotoThumbnailById = (photoId, preferredThumbnail = null) => {
        const thumbnail = preferredThumbnail && preferredThumbnail.isConnected
            ? preferredThumbnail
            : document.querySelector(`.photo-thumb[data-photo-id="${photoId}"]`);
        removeActiveThumbnail(thumbnail);
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

    const locationPayloadFromFields = (nameInput, latitudeInput, longitudeInput) => ({
        location_name: nameInput.value.trim(),
        latitude: latitudeInput.value.trim(),
        longitude: longitudeInput.value.trim(),
    });

    const setLocationFields = (nameInput, latitudeInput, longitudeInput, location) => {
        nameInput.value = location.location_name || "";
        latitudeInput.value = location.latitude ?? "";
        longitudeInput.value = location.longitude ?? "";
    };

    const formatLocation = (location) => {
        const name = location.location_name || "";
        const hasCoordinates = location.latitude !== null
            && location.latitude !== undefined
            && location.longitude !== null
            && location.longitude !== undefined
            && location.latitude !== ""
            && location.longitude !== "";
        if (name && hasCoordinates) {
            return `${name} (${location.latitude}, ${location.longitude})`;
        }
        if (name) {
            return name;
        }
        if (hasCoordinates) {
            return `${location.latitude}, ${location.longitude}`;
        }
        return "";
    };

    const setLocationSummary = (element, location) => {
        if (!element) {
            return;
        }
        const label = formatLocation(location);
        element.textContent = label;
        element.hidden = !label;
    };

    const parseGuidedPrompts = (value) => {
        if (!value) {
            return [];
        }
        try {
            const prompts = JSON.parse(value);
            return Array.isArray(prompts) ? prompts : [];
        } catch (error) {
            return [];
        }
    };

    const setThumbnailGuidedPrompts = (thumbnail, prompts, datasetKey) => {
        if (!thumbnail || !datasetKey) {
            return;
        }
        thumbnail.dataset[datasetKey] = JSON.stringify(Array.isArray(prompts) ? prompts : []);
    };

    const appendPromptText = (field, text) => {
        if (!field || !text) {
            return;
        }
        const current = field.value || "";
        const separator = current.trim() && !text.startsWith("\n") ? " " : "";
        field.value = `${current}${separator}${text}`;
        field.focus();
        field.selectionStart = field.value.length;
        field.selectionEnd = field.value.length;
    };

    const renderGuidedPrompts = (panel, prompts, handlers) => {
        if (!panel) {
            return;
        }
        const list = panel.querySelector(".guided-prompt-list");
        list.innerHTML = "";
        const usablePrompts = Array.isArray(prompts) ? prompts : [];
        panel.hidden = usablePrompts.length === 0;
        usablePrompts.forEach((prompt) => {
            const button = document.createElement("button");
            button.className = "guided-prompt-button";
            button.type = "button";

            const label = document.createElement("strong");
            label.textContent = prompt.label || "Memory prompt";
            const text = document.createElement("span");
            text.textContent = prompt.text || "";
            button.append(label, text);

            button.addEventListener("click", () => {
                const handler = handlers[prompt.target];
                if (handler) {
                    handler(prompt);
                }
            });
            list.appendChild(button);
        });
    };

    const renderPhotoGuidedPrompts = (prompts) => {
        renderGuidedPrompts(photoGuidedPrompts, prompts, {
            caption: (prompt) => appendPromptText(photoDetailsCaption, prompt.text || ""),
            people: () => photoPeopleInput.focus(),
            location: () => photoLocationName.focus(),
        });
    };

    const renderTextGuidedPrompts = (prompts) => {
        renderGuidedPrompts(textGuidedPrompts, prompts, {
            body: (prompt) => {
                showTextEditForm();
                appendPromptText(textEntryEditBody, prompt.text || "");
            },
            people: () => {
                showTextEditForm();
                textEntryPeopleInput.focus();
            },
            location: () => {
                showTextEditForm();
                textEntryLocationName.focus();
            },
        });
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

    const updatePhotoThumbnailPeople = (people, peopleText) => {
        if (!activePhotoThumbnail) {
            return;
        }
        const normalizedPeopleText = peopleText || peopleToText(people);
        activePhotoThumbnail.dataset.photoPeople = normalizedPeopleText;
        updatePeopleChips(activePhotoThumbnail, normalizedPeopleText);
    };

    const updatePhotoThumbnailDetails = (photo) => {
        if (!activePhotoThumbnail) {
            return;
        }

        const title = photo.title || "";
        const displayTitle = photo.display_title || title || "Photo";
        const caption = photo.caption || "";
        activePhotoThumbnail.dataset.photoTitle = title;
        activePhotoThumbnail.dataset.photoDisplayTitle = displayTitle;
        activePhotoThumbnail.dataset.photoCaption = caption;
        setThumbnailGuidedPrompts(activePhotoThumbnail, photo.guided_prompts, "photoPrompts");

        const image = activePhotoThumbnail.querySelector("img");
        if (image) {
            image.alt = displayTitle;
        }

        const card = activePhotoThumbnail.closest(".entry-card");
        if (!card) {
            return;
        }

        let meta = card.querySelector(".photo-card-meta");
        if (!title && !caption) {
            if (meta) {
                meta.remove();
            }
            return;
        }
        if (!meta) {
            meta = document.createElement("div");
            meta.className = "photo-card-meta";
            activePhotoThumbnail.insertAdjacentElement("afterend", meta);
        }

        meta.innerHTML = "";
        if (title) {
            const titleEl = document.createElement("strong");
            titleEl.textContent = title;
            meta.appendChild(titleEl);
        }
        if (caption) {
            const captionEl = document.createElement("span");
            captionEl.textContent = caption;
            meta.appendChild(captionEl);
        }
    };

    const renderPhotoDetails = (photo) => {
        const displayTitle = photo.display_title || photo.title || "Picture";
        modalTitle.textContent = displayTitle;
        modalImage.alt = displayTitle;
        photoCaptionView.textContent = photo.caption || "";
        photoDetailsTitle.value = photo.title || "";
        photoDetailsCaption.value = photo.caption || "";
        renderPhotoGuidedPrompts(photo.guided_prompts || []);
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

    const updateTextThumbnailPeople = (people, peopleText) => {
        if (!activeTextThumbnail) {
            return;
        }
        const normalizedPeopleText = peopleText || peopleToText(people);
        activeTextThumbnail.dataset.entryPeople = normalizedPeopleText;
        updatePeopleChips(activeTextThumbnail, normalizedPeopleText);
    };

    const updatePhotoThumbnailLocation = (location) => {
        if (!activePhotoThumbnail) {
            return;
        }
        activePhotoThumbnail.dataset.locationName = location.location_name || "";
        activePhotoThumbnail.dataset.latitude = location.latitude ?? "";
        activePhotoThumbnail.dataset.longitude = location.longitude ?? "";
    };

    const updateTextThumbnailLocation = (location) => {
        if (!activeTextThumbnail) {
            return;
        }
        activeTextThumbnail.dataset.locationName = location.location_name || "";
        activeTextThumbnail.dataset.latitude = location.latitude ?? "";
        activeTextThumbnail.dataset.longitude = location.longitude ?? "";
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
        const response = await csrfFetch(`/api/photo/${activePhotoId}/messages`);
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
        renderPhotoDetails({
            title: button.dataset.photoTitle || "",
            display_title: button.dataset.photoDisplayTitle || button.dataset.photoTitle || "Picture",
            caption: button.dataset.photoCaption || "",
            guided_prompts: parseGuidedPrompts(button.dataset.photoPrompts || "[]"),
        });
        modalDate.textContent = button.dataset.photoDate || "";
        const location = {
            location_name: button.dataset.locationName || "",
            latitude: button.dataset.latitude || "",
            longitude: button.dataset.longitude || "",
        };
        setLocationSummary(photoLocationSummary, location);
        setLocationFields(photoLocationName, photoLatitude, photoLongitude, location);
        setPeopleSummary(photoPeopleSummary, button.dataset.photoPeople || "");
        photoPeopleInput.value = button.dataset.photoPeople || "";
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
        renderPhotoDetails({title: "", display_title: "Picture", caption: ""});
        renderPhotoGuidedPrompts([]);
        setPeopleSummary(photoPeopleSummary, "");
        photoPeopleInput.value = "";
        setModalOpenState();
    };

    const renderTextEntry = (entry) => {
        activeTextEntry = entry;
        textModalDate.textContent = entry.entry_date || "";
        setLocationSummary(textLocationSummary, entry);
        setPeopleSummary(textPeopleSummary, entry.people_text || "");
        textEntryView.textContent = entry.body;
        textEntryEditBody.value = entry.body;
        textEntryEditDate.value = entry.entry_date || "";
        textEntryPeopleInput.value = entry.people_text || "";
        setLocationFields(textEntryLocationName, textEntryLatitude, textEntryLongitude, entry);
        setPrivacySummary(
            textPrivacySummary,
            entry.privacy_label || privacyLabelFromTag(entry.tags_text || "private"),
            entry.privacy_help || privacyHelpFromTag(entry.tags_text || "private")
        );
        setSelectedTagValue(textEntryEditTagInputs, entry.tags_text || "private");
        renderTextGuidedPrompts(entry.guided_prompts || []);
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
        updateTextThumbnailPeople(entry.people || [], entry.people_text || "");
        updateTextThumbnailLocation(entry);
        setThumbnailGuidedPrompts(activeTextThumbnail, entry.guided_prompts, "entryPrompts");
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

        const response = await csrfFetch(`/api/text-entry/${activeTextEntryId}`);
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
        renderTextGuidedPrompts([]);
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

        const response = await csrfFetch(`/api/photo/${activePhotoId}/messages`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({body}),
        });

        if (response.ok) {
            messageInput.value = "";
            await loadMessages();
        }
    });

    photoDetailsForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (activePhotoId === null) {
            return;
        }

        const response = await csrfFetch(`/api/photo/${activePhotoId}`, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                title: photoDetailsTitle.value,
                caption: photoDetailsCaption.value,
            }),
        });

        if (response.ok) {
            const payload = await response.json();
            renderPhotoDetails(payload);
            updatePhotoThumbnailDetails(payload);
        }
    });

    photoTagsForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (activePhotoId === null) {
            return;
        }

        const response = await csrfFetch(`/api/photo/${activePhotoId}/tags`, {
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

    photoPeopleForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (activePhotoId === null) {
            return;
        }

        const response = await csrfFetch(`/api/photo/${activePhotoId}/people`, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({people: photoPeopleInput.value}),
        });

        if (response.ok) {
            const payload = await response.json();
            photoPeopleInput.value = payload.people_text || "";
            setPeopleSummary(photoPeopleSummary, payload.people_text || "");
            updatePhotoThumbnailPeople(payload.people || [], payload.people_text || "");
            renderPhotoGuidedPrompts(payload.guided_prompts || []);
            setThumbnailGuidedPrompts(activePhotoThumbnail, payload.guided_prompts, "photoPrompts");
        }
    });

    photoLocationForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (activePhotoId === null) {
            return;
        }

        const response = await csrfFetch(`/api/photo/${activePhotoId}/location`, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(locationPayloadFromFields(photoLocationName, photoLatitude, photoLongitude)),
        });

        if (response.ok) {
            const location = await response.json();
            setLocationFields(photoLocationName, photoLatitude, photoLongitude, location);
            setLocationSummary(photoLocationSummary, location);
            updatePhotoThumbnailLocation(location);
            renderPhotoGuidedPrompts(location.guided_prompts || []);
            setThumbnailGuidedPrompts(activePhotoThumbnail, location.guided_prompts, "photoPrompts");
        }
    });

    deletePhotoButton.addEventListener("click", async () => {
        if (activePhotoId === null) {
            return;
        }

        const photoIdToDelete = activePhotoId;
        const thumbnailToDelete = activePhotoThumbnail;
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
        const response = await csrfFetch(`/api/photo/${photoIdToDelete}`, {
            method: "DELETE",
        });

        if (response.ok) {
            removePhotoThumbnailById(photoIdToDelete, thumbnailToDelete);
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

        const response = await csrfFetch(`/api/text-entry/${activeTextEntryId}`, {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                body,
                entry_date: textEntryEditDate.value,
                ...locationPayloadFromFields(
                    textEntryLocationName,
                    textEntryLatitude,
                    textEntryLongitude
                ),
                people: textEntryPeopleInput.value,
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
        const response = await csrfFetch(`/api/text-entry/${activeTextEntryId}`, {
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
