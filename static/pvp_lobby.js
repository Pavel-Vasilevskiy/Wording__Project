(() => {
    const lobbyPage = document.querySelector("[data-pvp-lobby-state]");
    if (!lobbyPage) return;

    const stateUrl = lobbyPage.dataset.stateUrl;
    const heartbeatUrl = lobbyPage.dataset.heartbeatUrl;
    const kickUrl = lobbyPage.dataset.kickUrl;
    const csrfToken = lobbyPage.dataset.csrfToken;
    const pollInterval = 3000;
    const heartbeatInterval = Number(lobbyPage.dataset.heartbeatIntervalMs) || 10000;
    const requestTimeoutMs = 10000;
    const memberList = document.getElementById("pvp-members");
    const memberCount = document.getElementById("pvp-member-count");
    const onlineCount = document.getElementById("pvp-online-count");
    const leaderName = document.getElementById("pvp-lobby-leader");
    const lobbyStatus = document.getElementById("pvp-lobby-status");
    const syncMessage = document.getElementById("pvp-sync-message");
    const unavailableMessage = document.getElementById("pvp-unavailable-message");
    const lobbyListLink = document.getElementById("pvp-lobbies-link");
    const settingsForm = document.getElementById("pvp-settings-form");
    const settingsSaveStatus = document.getElementById("pvp-settings-save-status");
    const settingsVersion = document.getElementById("pvp-settings-version");
    const settingsView = document.getElementById("pvp-settings-view");
    const wordsInput = document.getElementById("pvp-words-setting");
    const modeInput = document.getElementById("pvp-mode-setting");
    const timerInput = document.getElementById("pvp-timer-setting");
    const timerInputWrap = document.getElementById("pvp-timer-setting-wrap");
    const wordsView = document.getElementById("pvp-words-view");
    const modeView = document.getElementById("pvp-mode-view");
    const timerView = document.getElementById("pvp-timer-view");
    const timerViewLabel = document.getElementById("pvp-timer-view-label");
    const timerViewWrap = document.getElementById("pvp-timer-view-wrap");
    const readyForm = document.getElementById("pvp-ready-form");
    const readyValue = document.getElementById("pvp-ready-value");
    const readyButton = document.getElementById("pvp-ready-button");
    const startForm = document.getElementById("pvp-start-form");
    const startButton = document.getElementById("pvp-start-button");
    const startExplanation = document.getElementById("pvp-start-explanation");

    let active = false;
    let pollStarted = false;
    let pollInFlight = false;
    let heartbeatInFlight = false;
    let pollTimer = null;
    let heartbeatTimer = null;
    let pollTimeout = null;
    let heartbeatTimeout = null;
    let pollController = null;
    let heartbeatController = null;
    let settingsSaving = false;
    let settingsSavePending = false;
    let settingsDebounce = null;

    function showUnavailable(message) {
        if (syncMessage) syncMessage.textContent = "";
        if (unavailableMessage) {
            unavailableMessage.textContent = message;
            unavailableMessage.hidden = false;
        }
        if (lobbyListLink) lobbyListLink.hidden = false;
        stopMonitoring();
    }

    function syncTimerVisibility(mode, canEdit) {
        const usesTimer = mode === "timer";
        if (timerInputWrap) timerInputWrap.hidden = !usesTimer;
        if (timerInput) timerInput.disabled = !usesTimer || !canEdit;
        if (timerViewLabel) timerViewLabel.hidden = !usesTimer;
        if (timerViewWrap) timerViewWrap.hidden = !usesTimer;
    }

    function setIfNotFocused(element, value) {
        if (element && document.activeElement !== element) element.value = String(value);
    }

    function renderState(state) {
        if (!Array.isArray(state.members)) return;

        const nextMembers = state.members.map((member) => {
            const item = document.createElement("li");
            const name = document.createElement(member.is_leader ? "strong" : "span");
            name.className = member.is_leader ? "pvp-leader" : "pvp-member-name";
            name.textContent = member.is_leader ? `${member.name} · Лідер` : member.name;
            item.appendChild(name);

            const presence = document.createElement("span");
            presence.className = `pvp-status ${member.is_online ? "online" : "offline"}`;
            presence.textContent = member.is_online ? "Online" : "Offline";
            item.appendChild(presence);

            const ready = document.createElement("span");
            ready.className = `pvp-status ${member.is_ready ? "ready" : "not-ready"}`;
            ready.textContent = member.is_ready ? "Ready" : "Not ready";
            item.appendChild(ready);
            if (state.current_user_is_leader && member.kick_token) {
                const kick = document.createElement("button");
                kick.type = "button";
                kick.className = "pvp-button pvp-kick-button";
                kick.dataset.kickToken = member.kick_token;
                kick.textContent = "Вигнати";
                item.appendChild(kick);
            }
            return item;
        });
        memberList.replaceChildren(...nextMembers);
        memberCount.textContent = `${state.member_count}/${state.max_members} гравців`;
        onlineCount.textContent = String(state.online_count);
        leaderName.textContent = state.leader;

        const lobbyIsOpen = state.status === "open";
        lobbyStatus.textContent = lobbyIsOpen ? "Відкрите лобі" : "Підготовка матчу";
        lobbyStatus.className = `pvp-status ${lobbyIsOpen ? "online" : "ready"}`;

        const settings = state.settings;
        if (settingsVersion) settingsVersion.value = String(settings.version);
        wordsView.textContent = String(settings.words_per_match);
        modeView.textContent = settings.mode === "timer" ? "Timer" : "No time limit";
        timerView.textContent = String(settings.timer_seconds);
        setIfNotFocused(wordsInput, settings.words_per_match);
        setIfNotFocused(modeInput, settings.mode);
        setIfNotFocused(timerInput, settings.timer_seconds);
        const isLeader = Boolean(state.current_user_is_leader);
        const canEditSettings = isLeader && lobbyIsOpen;
        if (settingsForm) {
            settingsForm.hidden = !canEditSettings;
            for (const control of settingsForm.elements) {
                if (control.name !== "csrf_token") control.disabled = !canEditSettings;
            }
            if (modeInput) modeInput.disabled = !canEditSettings;
            syncTimerVisibility(settings.mode, canEditSettings);
        }
        if (settingsView) settingsView.hidden = isLeader && lobbyIsOpen;

        const isReady = Boolean(state.current_user_is_ready);
        if (readyForm) readyForm.hidden = !lobbyIsOpen;
        if (readyValue) readyValue.value = isReady ? "false" : "true";
        if (readyButton) readyButton.textContent = isReady ? "Скасувати готовність" : "Готовий";
        if (startForm) startForm.hidden = !isLeader || !lobbyIsOpen;
        if (startButton) startButton.disabled = !state.can_start;
        if (startExplanation) startExplanation.textContent = state.start_explanation;
    }

    function stopMonitoring() {
        active = false;
        pollStarted = false;
        for (const timer of [pollTimer, heartbeatTimer, pollTimeout, heartbeatTimeout]) {
            if (timer !== null) window.clearTimeout(timer);
        }
        pollTimer = heartbeatTimer = pollTimeout = heartbeatTimeout = null;
        if (pollController) pollController.abort();
        if (heartbeatController) heartbeatController.abort();
    }

    async function poll() {
        pollTimer = null;
        if (!active || pollInFlight) return;
        pollInFlight = true;
        const controller = new AbortController();
        pollController = controller;
        let timedOut = false;
        pollTimeout = window.setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, requestTimeoutMs);
        try {
            const response = await fetch(stateUrl, {
                headers: { Accept: "application/json" },
                cache: "no-store",
                signal: controller.signal,
            });
            if (response.status === 404) {
                showUnavailable("Це лобі більше не існує або ви більше не є його учасником.");
                if (lobbyListLink) lobbyListLink.href = "/pvp/?notice=unavailable";
                window.setTimeout(() => { window.location.href = "/pvp/?notice=unavailable"; }, 1400);
                return;
            }
            if (response.status === 410) {
                showUnavailable("Лідер видалив вас із цього лобі. Повертаємо до списку лобі.");
                if (lobbyListLink) lobbyListLink.href = "/pvp/?kicked=1";
                window.setTimeout(() => { window.location.href = "/pvp/?kicked=1"; }, 1400);
                return;
            }
            if (response.redirected && new URL(response.url).pathname === "/login") {
                showUnavailable("Сесію завершено. Увійдіть знову, щоб продовжити.");
                return;
            }
            if (!response.ok) throw new Error("Lobby state request failed");

            const state = await response.json();
            renderState(state);
            if (state.status === "closed") {
                showUnavailable("Лобі закрите.");
                return;
            }
            if (syncMessage) syncMessage.textContent = "";
        } catch (error) {
            if ((error.name !== "AbortError" || timedOut) && syncMessage) {
                syncMessage.textContent = "Не вдалося оновити стан лобі. Повторимо спробу автоматично.";
            }
        } finally {
            if (pollTimeout !== null) window.clearTimeout(pollTimeout);
            pollTimeout = null;
            pollInFlight = false;
            pollController = null;
            if (active) pollTimer = window.setTimeout(poll, pollInterval);
        }
    }

    async function heartbeat() {
        heartbeatTimer = null;
        if (!active || heartbeatInFlight) return;
        heartbeatInFlight = true;
        const controller = new AbortController();
        heartbeatController = controller;
        let timedOut = false;
        heartbeatTimeout = window.setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, requestTimeoutMs);
        try {
            const response = await fetch(heartbeatUrl, {
                method: "POST",
                headers: {
                    Accept: "application/json",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                body: new URLSearchParams({ csrf_token: csrfToken }),
                cache: "no-store",
                signal: controller.signal,
            });
            if (response.status === 404) {
                showUnavailable("Ви більше не є учасником цього лобі.");
                return;
            }
            if (response.redirected && new URL(response.url).pathname === "/login") {
                showUnavailable("Сесію завершено. Увійдіть знову, щоб продовжити.");
                return;
            }
            if (!response.ok) throw new Error("Lobby heartbeat failed");
            if (!pollStarted) {
                pollStarted = true;
                poll();
            }
            if (syncMessage) syncMessage.textContent = "";
        } catch (error) {
            if ((error.name !== "AbortError" || timedOut) && syncMessage) {
                syncMessage.textContent = "Зв’язок із лобі перервався. Повторимо спробу автоматично.";
            }
        } finally {
            if (heartbeatTimeout !== null) window.clearTimeout(heartbeatTimeout);
            heartbeatTimeout = null;
            heartbeatInFlight = false;
            heartbeatController = null;
            if (active) heartbeatTimer = window.setTimeout(heartbeat, heartbeatInterval);
        }
    }

    async function saveSettings() {
        if (!settingsForm || settingsForm.hidden || !active) return;
        if (settingsSaving) {
            settingsSavePending = true;
            return;
        }
        settingsSaving = true;
        settingsSavePending = false;
        if (settingsSaveStatus) settingsSaveStatus.textContent = "Збереження…";
        const body = new URLSearchParams(new FormData(settingsForm));
        try {
            const response = await fetch(settingsForm.action, {
                method: "POST",
                headers: {
                    Accept: "application/json",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                body,
                cache: "no-store",
            });
            if (response.redirected && new URL(response.url).pathname === "/login") {
                if (settingsSaveStatus) settingsSaveStatus.textContent = "Сесію завершено. Увійдіть знову.";
                return;
            }
            const result = await response.json();
            if (response.status === 409 && result.error === "settings_conflict") {
                if (settingsVersion) settingsVersion.value = String(result.settings.version);
                if (settingsSaveStatus) settingsSaveStatus.textContent = "Налаштування змінилися в іншій вкладці; синхронізуємо актуальні значення.";
                if (!settingsSavePending) {
                    if (wordsInput) setIfNotFocused(wordsInput, result.settings.words_per_match);
                    if (modeInput) setIfNotFocused(modeInput, result.settings.mode);
                    if (timerInput) setIfNotFocused(timerInput, result.settings.timer_seconds);
                }
                poll();
                return;
            }
            if (!response.ok) throw new Error("Settings save failed");
            if (settingsSaveStatus) settingsSaveStatus.textContent = "Збережено";
            if (settingsVersion) settingsVersion.value = String(result.settings.version);
            if (!settingsSavePending) {
                if (wordsInput) setIfNotFocused(wordsInput, result.settings.words_per_match);
                if (modeInput) setIfNotFocused(modeInput, result.settings.mode);
                if (timerInput) setIfNotFocused(timerInput, result.settings.timer_seconds);
            }
            poll();
        } catch (_error) {
            if (settingsSaveStatus) settingsSaveStatus.textContent = "Не вдалося зберегти налаштування. Спробуйте ще раз.";
        } finally {
            settingsSaving = false;
            if (settingsSavePending && active) saveSettings();
        }
    }

    function scheduleSettingsSave(debounce = false) {
        if (settingsDebounce !== null) window.clearTimeout(settingsDebounce);
        settingsDebounce = null;
        if (debounce) settingsDebounce = window.setTimeout(saveSettings, 400);
        else saveSettings();
    }

    async function kickMember(button) {
        const token = button.dataset.kickToken;
        if (!token || !window.confirm("Вигнати цього гравця з лобі?")) return;
        button.disabled = true;
        try {
            const response = await fetch(kickUrl, {
                method: "POST",
                headers: {
                    Accept: "application/json",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                body: new URLSearchParams({ csrf_token: csrfToken, member_token: token }),
                cache: "no-store",
            });
            if (!response.ok) throw new Error("Member removal failed");
            await poll();
        } catch (_error) {
            if (syncMessage) syncMessage.textContent = "Не вдалося видалити гравця. Спробуйте ще раз.";
            button.disabled = false;
        }
    }

    function startMonitoring() {
        if (active) return;
        active = true;
        heartbeat();
    }

    if (modeInput) {
        modeInput.addEventListener("change", () => syncTimerVisibility(modeInput.value, !modeInput.disabled));
    }
    if (settingsForm) {
        settingsForm.addEventListener("submit", (event) => event.preventDefault());
        for (const control of [wordsInput, modeInput]) {
            control?.addEventListener("change", () => scheduleSettingsSave(false));
        }
        timerInput?.addEventListener("input", () => scheduleSettingsSave(true));
        timerInput?.addEventListener("change", () => scheduleSettingsSave(false));
    }
    memberList?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-kick-token]");
        if (button) kickMember(button);
    });
    window.addEventListener("pagehide", stopMonitoring);
    window.addEventListener("pageshow", (event) => {
        if (event.persisted) startMonitoring();
    });
    startMonitoring();
})();
