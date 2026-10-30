(() => {
    const root = document.getElementById("pvp-match");
    if (!root) return;

    const stateUrl = root.dataset.stateUrl;
    const csrfToken = root.dataset.csrfToken;
    const statusNode = document.getElementById("match-status");
    const countNode = document.getElementById("match-player-count");
    const messageNode = document.getElementById("match-message");
    const countdownPanel = document.getElementById("match-countdown");
    const countdownValue = document.getElementById("countdown-value");
    const roundPanel = document.getElementById("match-round");
    const roundLabel = document.getElementById("round-label");
    const roundStatus = document.getElementById("round-status");
    const wordNode = document.getElementById("match-word");
    const clockNode = document.getElementById("match-clock");
    const modeNote = document.getElementById("match-mode-note");
    const playersNode = document.getElementById("match-players");

    let active = true;
    let inFlight = false;
    let pollTimer = null;
    let displayTimer = null;
    let requestTimeout = null;
    let controller = null;
    let serverOffsetMs = 0;
    let scheduledStartMs = null;
    let deadlineMs = null;
    let lastState = null;

    function toEpoch(value) {
        return value ? Date.parse(value) : null;
    }

    function renderProgress(player) {
        const rows = document.createElement("div");
        rows.className = "player-progress";
        const validColors = new Set(["green", "yellow", "gray"]);
        const safeRows = Array.isArray(player.progress) ? player.progress : [];
        safeRows.forEach((colors) => {
            if (!Array.isArray(colors) || colors.length !== 5 || !colors.every((color) => validColors.has(color))) return;
            const row = document.createElement("div");
            row.className = "progress-row";
            row.setAttribute("aria-label", "Завершена спроба");
            colors.forEach((color) => {
                const cell = document.createElement("span");
                cell.className = `progress-cell ${color}`;
                cell.setAttribute("aria-label", color);
                row.appendChild(cell);
            });
            rows.appendChild(row);
        });
        if (!rows.children.length) {
            const empty = document.createElement("span");
            empty.className = "match-muted";
            empty.textContent = "Спроб ще немає";
            rows.appendChild(empty);
        }
        return rows;
    }

    function renderPlayers(state) {
        const cards = state.players.map((player) => {
            const card = document.createElement("li");
            card.className = "player-card";
            const head = document.createElement("div");
            head.className = "player-head";
            const avatar = document.createElement(player.avatar && /^https:\/\//i.test(player.avatar) ? "img" : "span");
            avatar.className = "player-avatar";
            if (avatar.tagName === "IMG") {
                avatar.src = player.avatar;
                avatar.alt = "";
                avatar.referrerPolicy = "no-referrer";
            } else {
                avatar.textContent = (player.name || "?").trim().slice(0, 1).toUpperCase();
                avatar.setAttribute("aria-hidden", "true");
            }
            const meta = document.createElement("div");
            meta.className = "player-meta";
            const name = document.createElement("strong");
            name.textContent = player.name;
            const presence = document.createElement("span");
            presence.className = `presence ${player.is_online ? "online" : "offline"}`;
            presence.textContent = player.is_online ? "Online" : "Offline";
            meta.append(name, presence);
            head.append(avatar, meta);
            const round = document.createElement("p");
            round.className = "player-round";
            round.textContent = `Слово ${player.current_round}/${state.words_count} · ${player.attempts_used}/${state.attempts_per_word} спроб`;
            const result = document.createElement("p");
            result.className = "player-result";
            result.textContent = player.has_solved ? "Слово відгадано" : (player.is_online ? "Грає" : "Offline");
            card.append(head, round, renderProgress(player), result);
            return card;
        });
        playersNode.replaceChildren(...cards);
    }

    function renderClock() {
        if (!lastState) return;
        const estimatedServerNow = Date.now() + serverOffsetMs;
        if (lastState.status === "countdown" && scheduledStartMs !== null) {
            const remaining = Math.max(0, Math.ceil((scheduledStartMs - estimatedServerNow) / 1000));
            countdownValue.textContent = remaining > 0 ? String(remaining) : "Починаємо";
        }
        if (lastState.status === "in_progress" && deadlineMs !== null && lastState.game_mode === "timer") {
            const remaining = Math.max(0, Math.ceil((deadlineMs - estimatedServerNow) / 1000));
            const minutes = Math.floor(remaining / 60);
            const seconds = String(remaining % 60).padStart(2, "0");
            clockNode.textContent = `${minutes}:${seconds}`;
        }
    }

    function render(state) {
        lastState = state;
        const serverNow = toEpoch(state.server_now);
        if (serverNow !== null) serverOffsetMs = serverNow - Date.now();
        scheduledStartMs = toEpoch(state.scheduled_start_at);
        deadlineMs = toEpoch(state.round.deadline_at);
        countNode.textContent = `${state.player_count} гравців`;
        renderPlayers(state);

        if (state.status === "countdown") {
            statusNode.textContent = "Матч скоро почнеться";
            countdownPanel.hidden = false;
            roundPanel.hidden = true;
            messageNode.textContent = "Усі гравці мають однаковий серверний час старту.";
        } else if (state.status === "in_progress") {
            statusNode.textContent = "Матч триває";
            countdownPanel.hidden = true;
            roundPanel.hidden = false;
            roundLabel.textContent = `Слово ${state.current_round}/${state.words_count}`;
            roundStatus.textContent = state.round.status === "expired" ? "Час вичерпано" : "Раунд активний";
            wordNode.textContent = state.round.word || "";
            clockNode.hidden = state.game_mode !== "timer";
            modeNote.textContent = state.game_mode === "timer" ? "Спільний час раунду" : "Без обмеження часу";
            messageNode.textContent = "Ввід відповідей з’явиться в наступній ітерації.";
        } else if (state.status === "completed") {
            statusNode.textContent = "Матч завершено";
            countdownPanel.hidden = true;
            roundPanel.hidden = true;
            messageNode.textContent = "Матч завершено.";
        } else if (state.status === "cancelled") {
            statusNode.textContent = "Матч скасовано";
            countdownPanel.hidden = true;
            roundPanel.hidden = true;
            messageNode.textContent = "Матч було скасовано.";
        }
        renderClock();
    }

    function stop() {
        active = false;
        if (pollTimer !== null) window.clearTimeout(pollTimer);
        if (displayTimer !== null) window.clearInterval(displayTimer);
        if (requestTimeout !== null) window.clearTimeout(requestTimeout);
        pollTimer = displayTimer = requestTimeout = null;
        if (controller) controller.abort();
    }

    async function poll() {
        pollTimer = null;
        if (!active || inFlight) return;
        inFlight = true;
        controller = new AbortController();
        requestTimeout = window.setTimeout(() => controller?.abort(), 10000);
        try {
            const response = await fetch(stateUrl, {
                method: "POST",
                headers: {
                    Accept: "application/json",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                body: new URLSearchParams({ csrf_token: csrfToken }),
                cache: "no-store",
                signal: controller.signal,
            });
            if (!response.ok) throw new Error("Match state unavailable");
            render(await response.json());
        } catch (_error) {
            if (messageNode) messageNode.textContent = "Не вдалося оновити матч. Повторимо спробу.";
        } finally {
            if (requestTimeout !== null) window.clearTimeout(requestTimeout);
            requestTimeout = null;
            controller = null;
            inFlight = false;
            if (active) pollTimer = window.setTimeout(poll, lastState?.status === "countdown" ? 700 : 1500);
        }
    }

    window.addEventListener("pagehide", stop);
    window.addEventListener("pageshow", (event) => {
        if (event.persisted && !active) {
            active = true;
            poll();
            displayTimer = window.setInterval(renderClock, 100);
        }
    });
    displayTimer = window.setInterval(renderClock, 100);
    poll();
})();
