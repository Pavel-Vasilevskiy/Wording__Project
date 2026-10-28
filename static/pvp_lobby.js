(() => {
    const lobbyPage = document.querySelector("[data-pvp-lobby-state]");
    if (!lobbyPage) return;

    const stateUrl = lobbyPage.dataset.stateUrl;
    const pollInterval = 3000;
    const memberList = document.getElementById("pvp-members");
    const memberCount = document.getElementById("pvp-member-count");
    const leaderName = document.getElementById("pvp-lobby-leader");
    const lobbyStatus = document.getElementById("pvp-lobby-status");
    const syncMessage = document.getElementById("pvp-sync-message");
    const unavailableMessage = document.getElementById("pvp-unavailable-message");
    const lobbyListLink = document.getElementById("pvp-lobbies-link");

    let active = false;
    let requestInFlight = false;
    let timer = null;
    let requestTimeout = null;
    let requestTimedOut = false;
    let controller = null;

    function showUnavailable(message) {
        if (syncMessage) syncMessage.textContent = "";
        if (unavailableMessage) {
            unavailableMessage.textContent = message;
            unavailableMessage.hidden = false;
        }
        if (lobbyListLink) lobbyListLink.hidden = false;
        stopPolling();
    }

    function renderState(state) {
        if (!Array.isArray(state.members)) return;

        const nextMembers = state.members.map((member) => {
            const item = document.createElement("li");
            if (member.is_leader) {
                const leader = document.createElement("span");
                leader.className = "pvp-leader";
                leader.textContent = `${member.name} · Лідер`;
                item.appendChild(leader);
            } else {
                item.textContent = member.name;
            }
            return item;
        });

        memberList.replaceChildren(...nextMembers);
        memberCount.textContent = `${state.member_count}/${state.max_members} гравців`;
        leaderName.textContent = state.leader;
        lobbyStatus.textContent = state.status === "open" ? "Відкрите лобі" : "Лобі закрите";
    }

    async function poll() {
        timer = null;
        if (!active || requestInFlight) return;

        requestInFlight = true;
        requestTimedOut = false;
        controller = new AbortController();
        requestTimeout = window.setTimeout(() => {
            requestTimedOut = true;
            controller.abort();
        }, 10000);
        try {
            const response = await fetch(stateUrl, {
                headers: { Accept: "application/json" },
                cache: "no-store",
                signal: controller.signal,
            });

            if (response.status === 404) {
                showUnavailable("Це лобі більше недоступне або ви вже не є його учасником.");
                return;
            }
            if (response.redirected && new URL(response.url).pathname === "/login") {
                showUnavailable("Сесію завершено. Увійдіть знову, щоб продовжити.");
                return;
            }
            if (!response.ok) throw new Error("Lobby state request failed");

            const state = await response.json();
            renderState(state);
            if (state.status !== "open") {
                showUnavailable("Лобі закрите.");
                return;
            }
            if (syncMessage) syncMessage.textContent = "";
        } catch (error) {
            if ((error.name !== "AbortError" || requestTimedOut) && syncMessage) {
                syncMessage.textContent = "Не вдалося оновити список учасників. Повторимо спробу автоматично.";
            }
        } finally {
            if (requestTimeout !== null) {
                window.clearTimeout(requestTimeout);
                requestTimeout = null;
            }
            requestInFlight = false;
            controller = null;
            if (active) timer = window.setTimeout(poll, pollInterval);
        }
    }

    function startPolling() {
        if (active) return;
        active = true;
        poll();
    }

    function stopPolling() {
        active = false;
        if (timer !== null) {
            window.clearTimeout(timer);
            timer = null;
        }
        if (requestTimeout !== null) {
            window.clearTimeout(requestTimeout);
            requestTimeout = null;
        }
        if (controller) controller.abort();
    }

    window.addEventListener("pagehide", stopPolling);
    window.addEventListener("pageshow", (event) => {
        if (event.persisted) startPolling();
    });
    startPolling();
})();
