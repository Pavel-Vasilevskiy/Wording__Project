const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "..", "static", "pvp_lobby.js"), "utf8");

function createElement() {
    return {
        textContent: "",
        hidden: false,
        className: "",
        children: [],
        appendChild(child) { this.children.push(child); },
        replaceChildren(...children) { this.children = children; },
    };
}

function loadPollingScript(fetch) {
    const elements = Object.fromEntries([
        "pvp-members",
        "pvp-member-count",
        "pvp-lobby-leader",
        "pvp-lobby-status",
        "pvp-sync-message",
        "pvp-unavailable-message",
        "pvp-lobbies-link",
    ].map((id) => [id, createElement()]));
    elements["pvp-unavailable-message"].hidden = true;
    elements["pvp-lobbies-link"].hidden = true;

    const timers = new Map();
    const listeners = {};
    let nextTimerId = 1;
    const document = {
        querySelector: () => ({ dataset: { stateUrl: "/pvp/test/state" } }),
        getElementById: (id) => elements[id],
        createElement,
    };
    const window = {
        setTimeout(callback) {
            const id = nextTimerId++;
            timers.set(id, callback);
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
        addEventListener(name, callback) { listeners[name] = callback; },
    };
    vm.runInNewContext(script, { document, window, fetch, AbortController, URL });
    return { elements, timers };
}

function jsonResponse(status, body) {
    return { status, ok: status >= 200 && status < 300, json: async () => body };
}

test("polling refreshes members once per request and stops when the lobby is unavailable", async () => {
    let finishFirstRequest;
    const requests = [];
    const fetch = (_url, options) => {
        requests.push(options);
        if (requests.length === 1) {
            return new Promise((resolve) => { finishFirstRequest = resolve; });
        }
        return Promise.resolve(jsonResponse(404, { error: "lobby_unavailable" }));
    };
    const { elements, timers } = loadPollingScript(fetch);
    assert.equal(requests.length, 1);

    finishFirstRequest(jsonResponse(200, {
        status: "open",
        leader: "Leader",
        member_count: 2,
        max_members: 20,
        members: [
            { name: "Leader", is_leader: true },
            { name: "Guest", is_leader: false },
        ],
    }));
    await new Promise(setImmediate);
    await new Promise(setImmediate);

    assert.equal(elements["pvp-members"].children.length, 2);
    assert.equal(elements["pvp-member-count"].textContent, "2/20 гравців");
    assert.equal(elements["pvp-lobby-leader"].textContent, "Leader");
    assert.equal(timers.size, 1);

    const [timerId, pollAgain] = timers.entries().next().value;
    timers.delete(timerId);
    pollAgain();
    await new Promise(setImmediate);
    await new Promise(setImmediate);

    assert.equal(requests.length, 2);
    assert.equal(elements["pvp-unavailable-message"].hidden, false);
    assert.equal(elements["pvp-lobbies-link"].hidden, false);
    assert.equal(timers.size, 0);
});

test("temporary network errors are reported and retried without overlapping requests", async () => {
    let requests = 0;
    const fetch = () => {
        requests += 1;
        if (requests === 1) return Promise.reject(new TypeError("network unavailable"));
        return Promise.resolve(jsonResponse(200, {
            status: "open",
            leader: "Leader",
            member_count: 1,
            max_members: 20,
            members: [{ name: "Leader", is_leader: true }],
        }));
    };
    const { elements, timers } = loadPollingScript(fetch);
    await new Promise(setImmediate);
    await new Promise(setImmediate);

    assert.equal(requests, 1);
    assert.match(elements["pvp-sync-message"].textContent, /Повторимо спробу/);
    assert.equal(timers.size, 1);

    const [timerId, pollAgain] = timers.entries().next().value;
    timers.delete(timerId);
    pollAgain();
    await new Promise(setImmediate);
    await new Promise(setImmediate);

    assert.equal(requests, 2);
    assert.equal(elements["pvp-sync-message"].textContent, "");
    assert.equal(timers.size, 1);
});
