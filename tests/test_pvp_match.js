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
    }
    append(...nodes) { this.children.push(...nodes); }
    appendChild(node) { this.children.push(node); return node; }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = value; }
}

function setup(fetch) {
    const ids = [
        "match-status", "match-player-count", "match-message", "match-countdown",
        "countdown-value", "match-round", "round-label", "round-status", "match-word",
        "match-clock", "match-mode-note", "match-players",
    ];
    const elements = Object.fromEntries(ids.map((id) => [id, new Element()]));
    elements["match-countdown"].hidden = true;
    elements["match-round"].hidden = true;
    const root = new Element("main");
    root.dataset = { stateUrl: "/pvp/match/code/state", csrfToken: "csrf-match" };
    const timers = new Map();
    const listeners = {};
    let timerId = 0;
    const window = {
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
    return { elements, timers };
}

const response = (payload) => ({ ok: true, json: async () => payload });
const waitTurn = () => new Promise(setImmediate);

function countdownState(now) {
    return {
        public_id: "a".repeat(32), status: "countdown", game_mode: "timer", words_count: 3,
        attempts_per_word: 6, current_round: 1, scheduled_start_at: new Date(now + 5000).toISOString(),
        server_now: new Date(now).toISOString(), player_count: 2,
        round: { number: 1, status: "countdown", word: null, deadline_at: null, seconds_remaining: null },
        players: [], my_progress: [], my_attempts_used: 0, my_has_solved: false,
    };
}

function progressState(now) {
    return {
        ...countdownState(now), status: "in_progress", started_at: new Date(now).toISOString(),
        round: { number: 1, status: "in_progress", word: "CRANE", started_at: new Date(now).toISOString(), deadline_at: new Date(now + 120000).toISOString(), seconds_remaining: 120 },
        players: [{
            name: "Player", is_online: true, current_round: 1, attempts_used: 1, has_solved: false,
            progress: [["green", "yellow", "gray", "gray", "green"], ["CRANE", "secret", "guess", "text", "leak"]],
        }],
    };
}

test("match page polls the server, keeps countdown stable, and renders only safe color progress", async () => {
    const requests = [];
    const now = Date.now();
    const payloads = [countdownState(now), progressState(now + 5000)];
    const fetch = async (url, options) => {
        requests.push({ url, options });
        return response(payloads.shift());
    };
    const { elements, timers } = setup(fetch);

    await waitTurn();
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "/pvp/match/code/state");
    assert.equal(requests[0].options.method, "POST");
    assert.match(requests[0].options.body.toString(), /csrf_token=csrf-match/);
    assert.equal(elements["match-status"].textContent, "Матч скоро почнеться");
    assert.equal(elements["match-countdown"].hidden, false);
    assert.equal(elements["match-round"].hidden, true);
    assert.equal(elements["match-word"].textContent, "");

    const nextPoll = [...timers.entries()].find(([, timer]) => timer.delay === 700);
    assert.ok(nextPoll, "countdown should schedule another poll");
    timers.delete(nextPoll[0]);
    nextPoll[1].callback();
    await waitTurn();

    assert.equal(elements["match-status"].textContent, "Матч триває");
    assert.equal(elements["round-label"].textContent, "Слово 1/3");
    assert.equal(elements["match-word"].textContent, "CRANE");
    assert.equal(elements["match-clock"].hidden, false);
    assert.equal(elements["match-players"].children.length, 1);
    const renderedText = JSON.stringify(elements["match-players"].children, (_key, value) => value instanceof Element ? {
        tagName: value.tagName, textContent: value.textContent, className: value.className, children: value.children,
    } : value);
    assert.match(renderedText, /green/);
    assert.doesNotMatch(renderedText, /CRANE|secret|guess|leak/);
    assert.equal(elements["match-players"].children[0].children[2].children.length, 1);
});
