(() => {
    const root = document.getElementById("pvp-match");
    if (!root) return;

    const stateUrl = root.dataset.stateUrl;
    const guessUrl = root.dataset.guessUrl;
    const csrfToken = root.dataset.csrfToken;
    const statusNode = document.getElementById("match-status");
    const countNode = document.getElementById("match-player-count");
    const messageNode = document.getElementById("match-message");
    const feedbackNode = document.getElementById("guess-feedback");
    const countdownPanel = document.getElementById("match-countdown");
    const countdownValue = document.getElementById("countdown-value");
    const roundPanel = document.getElementById("match-round");
    const roundLabel = document.getElementById("round-label");
    const roundStatus = document.getElementById("round-status");
    const wordNode = document.getElementById("match-word");
    const clockNode = document.getElementById("match-clock");
    const modeNote = document.getElementById("match-mode-note");
    const playersNode = document.getElementById("match-players");
    const boardNode = document.getElementById("match-board");
    const keyboardNode = document.getElementById("match-keyboard");
    const leaveForm = document.getElementById("match-leave-form");

    let active = true;
    let inFlight = false;
    let submitInFlight = false;
    let submissionUnconfirmed = false;
    let pollTimer = null;
    let displayTimer = null;
    let requestTimeout = null;
    let controller = null;
    let serverOffsetMs = 0;
    let scheduledStartMs = null;
    let deadlineMs = null;
    let lastState = null;
    let currentGuess = "";
    let pendingRequestId = null;
    let transientMessage = "";
    let animateAttemptNumber = null;
    let lastAnimatedAttempt = null;

    function toEpoch(value) {
        return value ? Date.parse(value) : null;
    }

    function buildBoard() {
        const rows = [];
        for (let rowIndex = 0; rowIndex < 6; rowIndex += 1) {
            const row = document.createElement("div");
            row.className = "guess-row";
            row.setAttribute("role", "row");
            const cells = [];
            for (let column = 0; column < 5; column += 1) {
                const cell = document.createElement("span");
                cell.className = "guess-tile";
                cell.setAttribute("role", "gridcell");
                row.appendChild(cell);
                cells.push(cell);
            }
            boardNode.appendChild(row);
            rows.push(cells);
        }
        return rows;
    }

    const board = buildBoard();

    function setFeedback(message) {
        transientMessage = message;
        feedbackNode.textContent = message;
    }

    function isRoundOpen() {
        return Boolean(lastState
            && lastState.status === "in_progress"
            && lastState.round.status === "in_progress"
            && lastState.my_status === "active"
            && lastState.my_attempts_used < lastState.attempts_per_word);
    }

    function renderMyBoard(state) {
        const attempts = Array.isArray(state.my_attempts) ? state.my_attempts : [];
        const canType = isRoundOpen() && !submitInFlight;
        board.forEach((cells, rowIndex) => {
            const attempt = attempts[rowIndex];
            const letters = attempt && typeof attempt.guess === "string" ? attempt.guess.toUpperCase() :
                (canType && rowIndex === attempts.length ? currentGuess : "");
            const colors = attempt && Array.isArray(attempt.colors) ? attempt.colors : [];
            cells.forEach((cell, column) => {
                const letter = letters[column] || "";
                const color = colors[column];
                cell.textContent = letter;
                const animate = attempt && attempt.attempt_number === animateAttemptNumber && attempt.attempt_number !== lastAnimatedAttempt;
                cell.className = `guess-tile${letter ? " filled" : ""}${["green", "yellow", "gray"].includes(color) ? ` ${color}` : ""}${animate ? " reveal" : ""}`;
                if (animate) cell.style.animationDelay = `${column * 90}ms`;
                cell.setAttribute("aria-label", letter ? `Літера ${letter}` : "Порожня клітинка");
            });
            if (attempt && attempt.attempt_number === animateAttemptNumber && attempt.attempt_number !== lastAnimatedAttempt) {
                lastAnimatedAttempt = attempt.attempt_number;
            }
        });
        boardNode.setAttribute("aria-label", `Ваші спроби: ${attempts.length} з ${state.attempts_per_word}`);
        keyboardNode.hidden = !canType;
        boardNode.hidden = state.status !== "in_progress";
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

    function playerStatusLabel(player) {
        if (player.round_status === "left") return "Left";
        if (player.round_status === "solved") return "Solved";
        if (player.round_status === "failed") return "Failed";
        if (player.round_status === "expired") return "Час вичерпано";
        if (!player.is_online) return "Offline";
        if (player.attempts_used === 0) return "Waiting";
        return "In progress";
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
            result.textContent = playerStatusLabel(player);
            card.append(head, round, renderProgress(player), result);
            return card;
        });
        playersNode.replaceChildren(...cards);
    }

    function roundMessage(state) {
        if (state.round.status === "expired") return "Час раунду вичерпано. Відповіді більше не приймаються.";
        if (state.round.status === "completed") return "Усі гравці завершили перше слово. Очікуємо наступну ітерацію.";
        if (state.my_status === "solved") return "Ви відгадали слово. Очікуйте на інших гравців.";
        if (state.my_status === "failed") return "Спроби закінчилися. Очікуйте на інших гравців.";
        if (state.my_status === "expired") return "Час вашого раунду вичерпано. Очікуйте на інших гравців.";
        return "Введіть слово та натисніть Enter.";
    }

    function renderClock() {
        if (!lastState) return;
        const estimatedServerNow = Date.now() + serverOffsetMs;
        if (lastState.status === "countdown" && scheduledStartMs !== null) {
            const remaining = Math.max(0, Math.ceil((scheduledStartMs - estimatedServerNow) / 1000));
            countdownValue.textContent = remaining > 0 ? String(remaining) : "Починаємо";
        }
        if (lastState.status === "in_progress" && lastState.round.status !== "completed"
            && deadlineMs !== null && lastState.game_mode === "timer") {
            const remaining = Math.max(0, Math.ceil((deadlineMs - estimatedServerNow) / 1000));
            const minutes = Math.floor(remaining / 60);
            const seconds = String(remaining % 60).padStart(2, "0");
            clockNode.textContent = `${minutes}:${seconds}`;
        }
    }

    function render(state) {
        if (pendingRequestId && Array.isArray(state.my_attempts)
            && state.my_attempts.some((attempt) => attempt.request_id === pendingRequestId)) {
            currentGuess = "";
            pendingRequestId = null;
            submissionUnconfirmed = false;
            transientMessage = "";
        }
        lastState = state;
        const serverNow = toEpoch(state.server_now);
        if (serverNow !== null) serverOffsetMs = serverNow - Date.now();
        scheduledStartMs = toEpoch(state.scheduled_start_at);
        deadlineMs = toEpoch(state.round.deadline_at);
        countNode.textContent = `${state.player_count} гравців`;
        renderPlayers(state);
        renderMyBoard(state);

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
            roundStatus.textContent = state.round.status === "expired" ? "Час вичерпано" :
                (state.round.status === "completed" ? "Раунд завершено" : "Раунд активний");
            wordNode.textContent = state.round.word || "";
            clockNode.hidden = state.game_mode !== "timer" || state.round.status === "completed";
            modeNote.textContent = state.game_mode === "timer" ? "Спільний час раунду" : "Без обмеження часу";
            messageNode.textContent = roundMessage(state);
            if (!transientMessage) feedbackNode.textContent = "";
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

    function buildKeyboard() {
        const keyRows = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"];
        keyRows.forEach((letters, rowIndex) => {
            const row = document.createElement("div");
            row.className = "key-row";
            if (rowIndex === 2) {
                const enter = document.createElement("button");
                enter.type = "button";
                enter.className = "match-key wide";
                enter.textContent = "Enter";
                enter.dataset.key = "Enter";
                enter.addEventListener("click", () => submitGuess());
                row.appendChild(enter);
            }
            for (const letter of letters) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "match-key";
                button.textContent = letter;
                button.dataset.key = letter;
                button.setAttribute("aria-label", letter);
                button.addEventListener("mousedown", (event) => event.preventDefault());
                button.addEventListener("click", () => addLetter(letter));
                row.appendChild(button);
            }
            if (rowIndex === 2) {
                const backspace = document.createElement("button");
                backspace.type = "button";
                backspace.className = "match-key wide";
                backspace.textContent = "⌫";
                backspace.dataset.key = "Backspace";
                backspace.setAttribute("aria-label", "Backspace");
                backspace.addEventListener("mousedown", (event) => event.preventDefault());
                backspace.addEventListener("click", removeLetter);
                row.appendChild(backspace);
            }
            keyboardNode.appendChild(row);
        });
    }

    function updateActiveRow() {
        if (!lastState) return;
        renderMyBoard(lastState);
    }

    function addLetter(letter) {
        if (!isRoundOpen() || submitInFlight || submissionUnconfirmed || currentGuess.length >= 5 || !/^[a-z]$/i.test(letter)) return;
        currentGuess += letter.toUpperCase();
        setFeedback("");
        pendingRequestId = null;
        updateActiveRow();
    }

    function removeLetter() {
        if (!isRoundOpen() || submitInFlight || submissionUnconfirmed || !currentGuess.length) return;
        currentGuess = currentGuess.slice(0, -1);
        setFeedback("");
        pendingRequestId = null;
        updateActiveRow();
    }

    function makeRequestId() {
        if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
            const random = Math.random() * 16 | 0;
            return (character === "x" ? random : (random & 3 | 8)).toString(16);
        });
    }

    async function submitGuess() {
        if (!isRoundOpen() || submitInFlight) return;
        if (currentGuess.length !== 5) {
            setFeedback("Потрібно ввести 5 літер.");
            return;
        }
        if (!pendingRequestId) pendingRequestId = makeRequestId();
        submitInFlight = true;
        let acceptedStateRendered = false;
        const requestController = new AbortController();
        const timeout = window.setTimeout(() => requestController.abort(), 10000);
        try {
            const response = await fetch(guessUrl, {
                method: "POST",
                headers: {
                    Accept: "application/json",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                body: new URLSearchParams({ csrf_token: csrfToken, request_id: pendingRequestId, guess: currentGuess }),
                cache: "no-store",
                signal: requestController.signal,
            });
            const payload = await response.json();
            if (!response.ok) {
                if (response.status === 400) {
                    submissionUnconfirmed = false;
                    pendingRequestId = null;
                    setFeedback(payload.message || "Не вдалося прийняти слово.");
                } else if (response.status === 409) {
                    submissionUnconfirmed = false;
                    pendingRequestId = null;
                    setFeedback(payload.error === "round_expired" ? "Час раунду вичерпано." : "Раунд більше не приймає відповіді.");
                    schedulePoll(0);
                } else {
                    submissionUnconfirmed = true;
                    setFeedback("Не вдалося підтвердити спробу. Натисніть Enter, щоб безпечно повторити.");
                }
                return;
            }
            submissionUnconfirmed = false;
            currentGuess = "";
            pendingRequestId = null;
            animateAttemptNumber = payload.attempt_number;
            transientMessage = "";
            render(payload);
            acceptedStateRendered = true;
            feedbackNode.textContent = "";
        } catch (_error) {
            submissionUnconfirmed = true;
            setFeedback("Зв’язок перервався. Натисніть Enter, щоб перевірити спробу повторно.");
        } finally {
            window.clearTimeout(timeout);
            submitInFlight = false;
            if (acceptedStateRendered) keyboardNode.hidden = !isRoundOpen();
            else updateActiveRow();
        }
    }

    function isEditableTarget(target) {
        return Boolean(target && (target.isContentEditable || target.closest?.("input, textarea, select, [contenteditable='true']")));
    }

    function onKeyDown(event) {
        if (event.ctrlKey || event.metaKey || event.altKey || isEditableTarget(event.target)) return;
        if (event.key === "Enter" && event.target?.closest?.(".match-key")) return;
        if (event.key === "Enter") {
            if (!isRoundOpen()) return;
            event.preventDefault();
            submitGuess();
        } else if (event.key === "Backspace") {
            if (!isRoundOpen()) return;
            event.preventDefault();
            removeLetter();
        } else if (/^[a-z]$/i.test(event.key) && isRoundOpen()) {
            event.preventDefault();
            addLetter(event.key);
        }
    }

    function stop() {
        active = false;
        if (pollTimer !== null) window.clearTimeout(pollTimer);
        if (displayTimer !== null) window.clearInterval(displayTimer);
        if (requestTimeout !== null) window.clearTimeout(requestTimeout);
        pollTimer = displayTimer = requestTimeout = null;
        if (controller) controller.abort();
    }

    function schedulePoll(delay) {
        if (!active || pollTimer !== null) return;
        pollTimer = window.setTimeout(poll, delay);
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
            const state = await response.json();
            if (response.status === 410 && state.error === "match_left") {
                stop();
                window.location.href = "/pvp/";
                return;
            }
            if (!response.ok) throw new Error("Match state unavailable");
            render(state);
        } catch (_error) {
            if (messageNode) messageNode.textContent = "Не вдалося оновити матч. Повторимо спробу.";
        } finally {
            if (requestTimeout !== null) window.clearTimeout(requestTimeout);
            requestTimeout = null;
            controller = null;
            inFlight = false;
            if (active) {
                const terminalRound = lastState?.round?.status === "completed" || lastState?.round?.status === "expired";
                schedulePoll(lastState?.status === "countdown" ? 700 : (terminalRound ? 5000 : 1500));
            }
        }
    }

    buildKeyboard();
    leaveForm?.addEventListener("submit", (event) => {
        const confirmed = window.confirm("Покинути поточний матч? Ваші спроби та рахунок будуть збережені.");
        if (!confirmed) event.preventDefault();
    });
    window.addEventListener("keydown", onKeyDown);
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
