const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "..", "static", "pvp_match.js"), "utf8");

class Element {
    constructor(tagName = "div") {
        this.tagName = tagName.toUpperCase();
        this.children = [];
        this.attributes = {};
        this.dataset = {};
        this.textContent = "";
        this.className = "";
        this.hidden = false;
        this.style = {};
        this.handlers = {};
    }
    append(...nodes) { this.children.push(...nodes); }
    appendChild(node) { this.children.push(node); return node; }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, callback) { this.handlers[name] = callback; }
}

function setup(fetch) {
    const ids = [
        "match-status", "match-player-count", "match-message", "guess-feedback", "match-countdown",
        "countdown-value", "match-round", "round-label", "round-status", "match-word",
        "match-clock", "match-mode-note", "match-players", "match-board", "match-keyboard", "match-leave-form",
    ];
    const elements = Object.fromEntries(ids.map((id) => [id, new Element()]));
    elements["match-countdown"].hidden = true;
    elements["match-round"].hidden = true;
    const root = new Element("main");
    root.dataset = {
        stateUrl: "/pvp/match/code/state",
        guessUrl: "/pvp/match/code/guess",
        csrfToken: "csrf-match",
    };
    const timers = new Map();
    const listeners = {};
    let timerId = 0;
    let confirmResult = true;
    const confirmations = [];
    const window = {
        confirm(message) { confirmations.push(message); return confirmResult; },
        setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay, interval: false }); return id; },
        clearTimeout(id) { timers.delete(id); },
        setInterval(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay, interval: true }); return id; },
        clearInterval(id) { timers.delete(id); },
        addEventListener(name, callback) { listeners[name] = callback; },
    };
    const document = {
        getElementById(id) { return id === "pvp-match" ? root : elements[id]; },
        createElement(tag) { return new Element(tag); },
    };
    vm.runInNewContext(script, { document, window, fetch, AbortController, URLSearchParams, Date, Math, Set });
    return { elements, timers, listeners, confirmations, setConfirmResult(value) { confirmResult = value; } };
}

const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload });
const waitTurn = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };

function countdownState(now) {
    return {
        public_id: "a".repeat(32), status: "countdown", game_mode: "timer", words_count: 3,
        attempts_per_word: 6, current_round: 1, scheduled_start_at: new Date(now + 5000).toISOString(),
        server_now: new Date(now).toISOString(), player_count: 2,
        round: { number: 1, status: "countdown", word: null, deadline_at: null, seconds_remaining: null },
        players: [], my_progress: [], my_attempts: [], my_attempts_used: 0, my_has_solved: false, my_status: "active",
    };
}

function playingState(now, myAttempts = [], status = "active") {
    const myProgress = myAttempts.map((attempt) => attempt.colors);
    return {
        ...countdownState(now), status: "in_progress", started_at: new Date(now).toISOString(),
        round: { number: 1, status: "in_progress", word: "CIGAR", started_at: new Date(now).toISOString(), deadline_at: new Date(now + 120000).toISOString(), seconds_remaining: 120 },
        my_attempts: myAttempts, my_progress: myProgress, my_attempts_used: myAttempts.length,
        my_has_solved: status === "solved", my_status: status,
        players: [{
            name: "Player", avatar: null, is_online: true, current_round: 1,
            attempts_used: myAttempts.length, has_solved: status === "solved", round_status: status,
            progress: myProgress,
        }],
    };
}

function sendKey(handler, key, target = null) {
    let prevented = false;
    handler({ key, target, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, preventDefault() { prevented = true; } });
    return prevented;
}

function findButton(keyboard, key) {
    for (const row of keyboard.children) {
        const button = row.children.find((child) => child.dataset.key === key);
        if (button) return button;
    }
    return null;
}

test("the lobby-to-match polling page restores a countdown then reveals only the active word", async () => {
    const now = Date.now();
    const requests = [];
    const states = [countdownState(now), playingState(now + 5000)];
    const fetch = async (url, options) => {
        requests.push({ url, options });
        return response(states.shift());
    };
    const { elements, timers } = setup(fetch);
    await waitTurn();
    assert.equal(requests[0].url, "/pvp/match/code/state");
    assert.equal(requests[0].options.method, "POST");
    assert.match(requests[0].options.body.toString(), /csrf_token=csrf-match/);
    assert.equal(elements["match-status"].textContent, "Матч скоро почнеться");
    assert.equal(elements["match-round"].hidden, true);

    const next = [...timers.entries()].find(([, timer]) => timer.delay === 700);
    assert.ok(next);
    timers.delete(next[0]);
    next[1].callback();
    await waitTurn();
    assert.equal(elements["match-word"].textContent, "CIGAR");
    assert.equal(elements["match-board"].hidden, false);
    assert.equal(elements["match-keyboard"].hidden, false);
});

test("leaving a match requires confirmation and cancellation prevents the POST form", async () => {
    const { elements, setConfirmResult, confirmations } = setup(async () => response(countdownState(Date.now())));
    await waitTurn();
    let prevented = false;
    const cancelEvent = { preventDefault() { prevented = true; } };
    setConfirmResult(false);
    elements["match-leave-form"].handlers.submit(cancelEvent);
    assert.equal(prevented, true);
    assert.match(confirmations[0], /спроби та рахунок будуть збережені/);

    prevented = false;
    setConfirmResult(true);
    elements["match-leave-form"].handlers.submit({ preventDefault() { prevented = true; } });
    assert.equal(prevented, false);
    assert.equal(confirmations.length, 2);
});

test("physical input fills five tiles, waits for Enter, and restores the accepted attempt", async () => {
    const now = Date.now();
    const attempts = [];
    const requests = [];
    let gameState = playingState(now);
    const fetch = async (url, options) => {
        requests.push({ url, options });
        if (url.endsWith("/state")) return response(gameState);
        const body = new URLSearchParams(options.body);
        const colors = body.get("guess") === "CIGAR" ? ["green", "green", "green", "green", "green"] : ["gray", "yellow", "gray", "green", "gray"];
        attempts.push({ attempt_number: attempts.length + 1, request_id: body.get("request_id"), guess: body.get("guess"), colors, is_correct: body.get("guess") === "CIGAR" });
        gameState = playingState(now + attempts.length * 10, attempts, attempts.at(-1).is_correct ? "solved" : "active");
        return response({ ...gameState, duplicate: false, attempt_number: attempts.length, colors });
    };
    const { elements, listeners } = setup(fetch);
    await waitTurn();
    const keydown = listeners.keydown;
    for (const letter of "ARRAY") sendKey(keydown, letter);
    assert.equal(elements["match-board"].children[0].children.map((cell) => cell.textContent).join(""), "ARRAY");
    sendKey(keydown, "Z");
    assert.equal(elements["match-board"].children[0].children.map((cell) => cell.textContent).join(""), "ARRAY");
    assert.equal(requests.filter((request) => request.url.endsWith("/guess")).length, 0);

    assert.equal(sendKey(keydown, "Enter"), true);
    await waitTurn();
    const guessRequests = requests.filter((request) => request.url.endsWith("/guess"));
    assert.equal(guessRequests.length, 1);
    assert.equal(new URLSearchParams(guessRequests[0].options.body).get("guess"), "ARRAY");
    assert.equal(elements["match-board"].children[0].children[0].textContent, "A");
    assert.equal(elements["match-board"].children[0].children[0].className.includes("gray"), true);
    assert.equal(elements["match-board"].children[0].children[0].className.includes("reveal"), true);

    const reloaded = setup(async () => response(gameState));
    await waitTurn();
    assert.equal(reloaded.elements["match-board"].children[0].children.map((cell) => cell.textContent).join(""), "ARRAY");
    assert.equal(reloaded.elements["match-board"].children[1].children[0].textContent, "");
});

test("on-screen keys use the same guess state and solved players cannot submit again", async () => {
    const now = Date.now();
    const attempts = [];
    const requests = [];
    let gameState = playingState(now);
    const fetch = async (url, options) => {
        requests.push({ url, options });
        if (url.endsWith("/state")) return response(gameState);
        const body = new URLSearchParams(options.body);
        const guess = body.get("guess");
        const colors = guess === "CIGAR" ? ["green", "green", "green", "green", "green"] : ["gray", "yellow", "gray", "green", "gray"];
        attempts.push({ attempt_number: attempts.length + 1, request_id: body.get("request_id"), guess, colors, is_correct: guess === "CIGAR" });
        gameState = playingState(now + attempts.length * 10, attempts, guess === "CIGAR" ? "solved" : "active");
        return response({ ...gameState, duplicate: false, attempt_number: attempts.length, colors });
    };
    const { elements } = setup(fetch);
    await waitTurn();
    const keyboard = elements["match-keyboard"];
    findButton(keyboard, "C").handlers.click();
    findButton(keyboard, "R").handlers.click();
    findButton(keyboard, "A").handlers.click();
    findButton(keyboard, "N").handlers.click();
    findButton(keyboard, "E").handlers.click();
    assert.equal(elements["match-board"].children[0].children.map((cell) => cell.textContent).join(""), "CRANE");
    findButton(keyboard, "Backspace").handlers.click();
    assert.equal(elements["match-board"].children[0].children.map((cell) => cell.textContent).join(""), "CRAN");
    findButton(keyboard, "E").handlers.click();
    findButton(keyboard, "Enter").handlers.click();
    await waitTurn();
    assert.equal(requests.filter((request) => request.url.endsWith("/guess")).length, 1);
    assert.equal(new URLSearchParams(requests.find((request) => request.url.endsWith("/guess")).options.body).get("guess"), "CRANE");

    for (const letter of "CIGAR") findButton(keyboard, letter).handlers.click();
    findButton(keyboard, "Enter").handlers.click();
    await waitTurn();
    assert.equal(requests.filter((request) => request.url.endsWith("/guess")).length, 2);
    assert.equal(elements["match-keyboard"].hidden, true);
    assert.equal(elements["match-message"].textContent, "Ви відгадали слово. Очікуйте на інших гравців.");
});
test("invalid server response preserves editable tiles and retry creates a fresh request", async () => {
    const now = Date.now();
    const requests = [];
    let submitted = 0;
    const fetch = async (url, options) => {
        requests.push({ url, options });
        if (url.endsWith("/state")) return response(playingState(now));
        submitted += 1;
        const body = new URLSearchParams(options.body);
        if (submitted === 1) return response({ error: "invalid_guess", message: "Not in word list" }, 400);
        const attempt = {
            attempt_number: 1, request_id: body.get("request_id"), guess: body.get("guess"),
            colors: ["gray", "yellow", "gray", "green", "gray"], is_correct: false,
        };
        return response({ ...playingState(now, [attempt]), duplicate: false, attempt_number: 1, colors: attempt.colors });
    };
    const { elements, listeners } = setup(fetch);
    await waitTurn();
    const keydown = listeners.keydown;
    for (const letter of "ZZZZZ") sendKey(keydown, letter);
    sendKey(keydown, "Enter");
    await waitTurn();
    assert.equal(elements["match-board"].children[0].children.map((cell) => cell.textContent).join(""), "ZZZZZ");
    assert.equal(elements["guess-feedback"].textContent, "Not in word list");

    sendKey(keydown, "Backspace");
    sendKey(keydown, "Y");
    assert.equal(elements["match-board"].children[0].children.map((cell) => cell.textContent).join(""), "ZZZZY");
    sendKey(keydown, "Enter");
    await waitTurn();
    const guessRequests = requests.filter((request) => request.url.endsWith("/guess"));
    assert.equal(guessRequests.length, 2);
    assert.notEqual(
        new URLSearchParams(guessRequests[0].options.body).get("request_id"),
        new URLSearchParams(guessRequests[1].options.body).get("request_id"),
    );
    assert.equal(elements["match-board"].children[0].children.map((cell) => cell.textContent).join(""), "ZZZZY");
});
