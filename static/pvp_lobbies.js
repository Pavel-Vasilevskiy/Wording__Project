(() => {
    const page = document.getElementById("pvp-lobby-list-page");
    if (!page) return;

    const refreshUrl = page.dataset.refreshUrl;
    const csrfToken = page.dataset.csrfToken;
    const list = document.getElementById("pvp-open-lobbies");
    const emptyMessage = document.getElementById("pvp-empty-lobbies");
    const currentAction = document.getElementById("pvp-current-action");
    const refreshButton = document.getElementById("pvp-refresh-lobbies");
    const refreshStatus = document.getElementById("pvp-refresh-status");
    let inFlight = false;
    let pendingManualRefresh = false;
    let timer = null;
    let requestTimeout = null;
    let controller = null;
    let active = true;

    function make(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    }

    function updateCurrentAction(state) {
        currentAction.replaceChildren();
        if (state.my_lobby_public_id) {
            const wrapper = make("div", undefined, "pvp-current-lobby");
            const message = make("p");
            message.append("Ви вже в лобі ");
            message.append(make("strong", state.my_lobby_leader || ""));
            message.append(".");
            const link = make("a", "Відкрити моє лобі", "pvp-button pvp-action");
            link.href = state.my_lobby_detail_url;
            wrapper.append(message, link);
            currentAction.append(wrapper);
        } else {
            const form = make("form", undefined, "pvp-create-form");
            form.method = "post";
            form.action = page.dataset.createUrl;
            const csrf = make("input");
            csrf.type = "hidden";
            csrf.name = "csrf_token";
            csrf.value = csrfToken;
            const button = make("button", "Створити лобі", "pvp-button pvp-action");
            button.type = "submit";
            form.append(csrf, button);
            currentAction.append(form);
        }
    }

    function renderLobby(lobby, myLobbyPublicId) {
        const row = make("li", undefined, "pvp-lobby-row");
        row.dataset.lobbyId = lobby.id;
        const summary = make("div");
        const heading = make("h2");
        heading.append("Лідер: ", make("span", lobby.leader));
        const details = make("div", `${lobby.member_count}/${lobby.max_members} гравців · ${lobby.created_at}`, "pvp-muted");
        summary.append(heading, details);

        if (lobby.id === myLobbyPublicId) {
            const link = make("a", "Відкрити лобі", "pvp-button");
            link.href = lobby.detail_url;
            row.append(summary, link);
        } else if (lobby.can_join) {
            const form = make("form");
            form.method = "post";
            form.action = lobby.join_url;
            const csrf = make("input");
            csrf.type = "hidden";
            csrf.name = "csrf_token";
            csrf.value = csrfToken;
            const button = make("button", "Приєднатися", "pvp-button pvp-action");
            button.type = "submit";
            form.append(csrf, button);
            row.append(summary, form);
        } else {
            row.append(summary, make("span", `${lobby.member_count}/${lobby.max_members} · зайнято`, "pvp-muted"));
        }
        return row;
    }

    function render(state) {
        updateCurrentAction(state);
        list.replaceChildren(...state.lobbies.map((lobby) => renderLobby(lobby, state.my_lobby_public_id)));
        emptyMessage.hidden = state.lobbies.length !== 0;
    }

    async function refresh(manual = false) {
        if (!active) return;
        if (inFlight) {
            if (manual) pendingManualRefresh = true;
            return;
        }
        inFlight = true;
        list.setAttribute("aria-busy", "true");
        if (refreshButton) refreshButton.disabled = true;
        if (refreshStatus) refreshStatus.textContent = "Оновлення…";
        controller = new AbortController();
        const requestController = controller;
        requestTimeout = window.setTimeout(() => requestController.abort(), 10000);
        try {
            const response = await fetch(refreshUrl, {
                method: "POST",
                headers: {
                    Accept: "application/json",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                body: new URLSearchParams({ csrf_token: csrfToken }),
                cache: "no-store",
                signal: requestController.signal,
            });
            if (response.redirected && new URL(response.url).pathname === "/login") {
                active = false;
                if (refreshStatus) refreshStatus.textContent = "Сесію завершено. Увійдіть знову.";
                return;
            }
            if (!response.ok) throw new Error("List refresh failed");
            render(await response.json());
            if (refreshStatus) refreshStatus.textContent = "";
        } catch (_error) {
            if (refreshStatus) refreshStatus.textContent = "Не вдалося оновити список. Повторимо автоматично.";
        } finally {
            if (requestTimeout !== null) window.clearTimeout(requestTimeout);
            requestTimeout = null;
            controller = null;
            inFlight = false;
            list.setAttribute("aria-busy", "false");
            if (refreshButton) refreshButton.disabled = false;
            if (active) {
                if (pendingManualRefresh) {
                    pendingManualRefresh = false;
                    refresh(true);
                } else {
                    timer = window.setTimeout(() => refresh(false), 5000);
                }
            }
        }
    }

    refreshButton?.addEventListener("click", () => {
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        refresh(true);
    });
    window.addEventListener("pagehide", () => {
        active = false;
        if (timer !== null) window.clearTimeout(timer);
        if (requestTimeout !== null) window.clearTimeout(requestTimeout);
        if (controller) controller.abort();
    });
    window.addEventListener("pageshow", (event) => {
        if (event.persisted && !active) {
            active = true;
            refresh(false);
        }
    });
    refresh(false);
})();
