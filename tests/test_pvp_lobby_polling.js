const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "..", "static", "pvp_lobby.js"), "utf8");

function createElement() {
    return {
        textContent: "",
        value: "",
        hidden: false,
        className: "",
        children: [],
        elements: [],
        disabled: false,
        handlers: {},
        appendChild(child) { this.children.push(child); },
        replaceChildren(...children) { this.children = children; },
        addEventListener(name, callback) { this.handlers[name] = callback; },
    };
}

function loadPollingScript(fetch, withSettings = false) {
    const elements = Object.fromEntries([
        "pvp-members",
        "pvp-member-count",
        "pvp-online-count",
        "pvp-lobby-leader",
        "pvp-lobby-status",
        "pvp-sync-message",
        "pvp-unavailable-message",
        "pvp-lobbies-link",
        "pvp-settings-form",
        "pvp-settings-save-status",
        "pvp-settings-version",
        "pvp-settings-view",
        "pvp-words-setting",
        "pvp-mode-setting",
        "pvp-timer-setting",
        "pvp-timer-setting-wrap",
        "pvp-words-view",
        "pvp-mode-view",
        "pvp-timer-view",
        "pvp-timer-view-label",
        "pvp-timer-view-wrap",
        "pvp-ready-form",
        "pvp-ready-value",
        "pvp-ready-button",
        "pvp-start-form",
        "pvp-start-button",
        "pvp-start-explanation",
    ].map((id) => [id, createElement()]));
    elements["pvp-unavailable-message"].hidden = true;
    elements["pvp-lobbies-link"].hidden = true;
    elements["pvp-settings-form"].hidden = !withSettings;
    elements["pvp-settings-form"].action = "/pvp/test/settings";
    elements["pvp-settings-form"].elements = [
        { name: "csrf_token", value: "test-csrf", disabled: false },
        elements["pvp-settings-version"],
        elements["pvp-words-setting"],
        elements["pvp-mode-setting"],
        elements["pvp-timer-setting"],
    ];
    elements["pvp-words-setting"].name = "words_per_match";
    elements["pvp-words-setting"].value = "5";
    elements["pvp-mode-setting"].name = "mode";
    elements["pvp-mode-setting"].value = "timer";
    elements["pvp-timer-setting"].name = "timer_seconds";
    elements["pvp-timer-setting"].value = "120";
    elements["pvp-settings-version"].name = "settings_version";
    elements["pvp-settings-version"].value = "0";

    const timers = new Map();
    const listeners = {};
    let nextTimerId = 1;
    const document = {
        activeElement: null,
        querySelector: () => ({ dataset: {
            stateUrl: "/pvp/test/state",
            heartbeatUrl: "/pvp/test/heartbeat",
            kickUrl: "/pvp/test/kick",
            heartbeatIntervalMs: "10000",
            csrfToken: "test-csrf",
        } }),
        getElementById: (id) => elements[id],
        createElement,
    };
    const location = { href: "" };
    const window = {
        location,
        setTimeout(callback, delay) {
            const id = nextTimerId++;
            timers.set(id, { callback, delay });
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
        addEventListener(name, callback) { listeners[name] = callback; },
    };
    class FormDataMock {
        constructor(form) {
            return form.elements.filter((item) => item.name && !item.disabled).map((item) => [item.name, item.value]);
        }
    }
    vm.runInNewContext(script, { document, window, fetch, AbortController, URL, URLSearchParams, FormData: FormDataMock });
    function runTimer(delay) {
        const entry = [...timers.entries()].find(([, timer]) => timer.delay === delay);
        assert.ok(entry, `expected timer with delay ${delay}`);
        timers.delete(entry[0]);
        entry[1].callback();
    }
    return { elements, timers, runTimer, location };
}

function jsonResponse(status, body) {
    return { status, ok: status >= 200 && status < 300, json: async () => body };
}

test("polling refreshes members once per request and stops when the lobby is unavailable", async () => {
    let finishFirstRequest;
    const requests = [];
    const fetch = (url, options) => {
        requests.push({ url, options });
        if (url.endsWith("/heartbeat")) return Promise.resolve(jsonResponse(200, { online: true }));
        if (requests.filter((request) => request.url.endsWith("/state")).length === 1) {
            return new Promise((resolve) => { finishFirstRequest = resolve; });
        }
        return Promise.resolve(jsonResponse(404, { error: "lobby_unavailable" }));
    };
    const { elements, timers, runTimer } = loadPollingScript(fetch);
    assert.equal(requests.length, 1); // State polling starts after the first heartbeat.
    assert.equal(requests[0].options.method, "POST");
    assert.match(requests[0].options.body.toString(), /test-csrf/);
    await new Promise(setImmediate);
    assert.equal(requests.length, 2);

    finishFirstRequest(jsonResponse(200, {
        status: "open",
        leader: "Leader",
        member_count: 2,
        online_count: 2,
        max_members: 20,
        settings: { words_per_match: 5, mode: "timer", timer_seconds: 120, attempts_per_word: 6, version: 0 },
        current_user_is_leader: true,
        current_user_is_ready: false,
        can_start: false,
        start_explanation: "Ready needed",
        members: [
            { name: "Leader", is_leader: true, is_online: true, is_ready: false },
            { name: "Guest", is_leader: false, is_online: true, is_ready: false },
        ],
    }));
    await new Promise(setImmediate);
    await new Promise(setImmediate);

    assert.equal(elements["pvp-members"].children.length, 2);
    assert.equal(elements["pvp-member-count"].textContent, "2/20 гравців");
    assert.equal(elements["pvp-online-count"].textContent, "2");
    assert.equal(elements["pvp-lobby-leader"].textContent, "Leader");
    assert.equal(elements["pvp-words-setting"].value, "5");
    assert.equal(elements["pvp-mode-setting"].value, "timer");
    assert.equal(elements["pvp-timer-setting"].value, "120");
    assert.equal(elements["pvp-ready-button"].textContent, "Готовий");
    assert.equal(elements["pvp-start-button"].disabled, true);
    assert.equal(timers.size, 2);

    runTimer(3000);
    await new Promise(setImmediate);
    await new Promise(setImmediate);

    assert.equal(requests.length, 3);
    assert.equal(elements["pvp-unavailable-message"].hidden, false);
    assert.equal(elements["pvp-lobbies-link"].hidden, false);
    assert.equal(elements["pvp-lobbies-link"].href, "/pvp/?notice=unavailable");
    assert.deepEqual([...timers.values()].map((timer) => timer.delay), [1400]);
});

test("temporary network errors are reported and retried without overlapping requests", async () => {
    const requests = [];
    const fetch = (url, options) => {
        requests.push({ url, options });
        if (url.endsWith("/heartbeat")) return Promise.resolve(jsonResponse(200, { online: true }));
        if (requests.filter((request) => request.url.endsWith("/state")).length === 1) {
            return Promise.reject(new TypeError("network unavailable"));
        }
        return Promise.resolve(jsonResponse(200, {
            status: "open",
            leader: "Leader",
            member_count: 1,
            online_count: 1,
            max_members: 20,
            settings: { words_per_match: 5, mode: "timer", timer_seconds: 120, attempts_per_word: 6, version: 0 },
            current_user_is_leader: true,
            current_user_is_ready: false,
            can_start: false,
            start_explanation: "Ready needed",
            members: [{ name: "Leader", is_leader: true, is_online: true, is_ready: false }],
        }));
    };
    const { elements, timers, runTimer } = loadPollingScript(fetch);
    await new Promise(setImmediate);
    await new Promise(setImmediate);

    assert.equal(requests.filter((request) => request.url.endsWith("/state")).length, 1);
    assert.match(elements["pvp-sync-message"].textContent, /Повторимо спробу/);
    assert.equal(timers.size, 2);

    runTimer(3000);
    await new Promise(setImmediate);
    await new Promise(setImmediate);

    assert.equal(requests.filter((request) => request.url.endsWith("/state")).length, 2);
    assert.equal(elements["pvp-sync-message"].textContent, "");
    assert.equal(timers.size, 2);
});

test("settings save automatically and serialize newer values after an in-flight save", async () => {
    const requests = [];
    const pending = [];
    const fetch = (url, options) => {
        requests.push({ url, options });
        if (url.endsWith("/heartbeat")) return Promise.resolve(jsonResponse(200, { online: true }));
        if (url.endsWith("/state")) return Promise.resolve(jsonResponse(200, {
            status: "open", leader: "Leader", member_count: 1, online_count: 1, max_members: 20,
            settings: { words_per_match: 5, mode: "timer", timer_seconds: 120, version: 0 },
            current_user_is_leader: true, current_user_is_ready: false, can_start: false,
            start_explanation: "Ready needed", members: [{ name: "Leader", is_leader: true, is_online: true, is_ready: false }],
        }));
        return new Promise((resolve) => pending.push(resolve));
    };
    const { elements } = loadPollingScript(fetch, true);
    await new Promise(setImmediate);

    const words = elements["pvp-words-setting"];
    words.value = "10";
    words.handlers.change();
    assert.equal(requests.filter((request) => request.url.endsWith("/settings")).length, 1);
    assert.match(requests.at(-1).options.body.toString(), /words_per_match=10/);
    assert.match(elements["pvp-settings-save-status"].textContent, /Збереження/);

    words.value = "3";
    words.handlers.change();
    assert.equal(requests.filter((request) => request.url.endsWith("/settings")).length, 1);
    pending[0](jsonResponse(200, { saved: true, changed: true, settings: { words_per_match: 10, mode: "timer", timer_seconds: 120, version: 1 } }));
    await new Promise(setImmediate);
    await new Promise(setImmediate);
    const settingRequests = requests.filter((request) => request.url.endsWith("/settings"));
    assert.equal(settingRequests.length, 2);
    assert.match(settingRequests[1].options.body.toString(), /words_per_match=3/);
    assert.match(settingRequests[1].options.body.toString(), /settings_version=1/);
    pending[1](jsonResponse(200, { saved: true, changed: true, settings: { words_per_match: 3, mode: "timer", timer_seconds: 120, version: 2 } }));
    await new Promise(setImmediate);
    assert.equal(elements["pvp-settings-save-status"].textContent, "Збережено");
});


test("lobby polling redirects members to the created match", async () => {
    const requests = [];
    const fetch = (url, options) => {
        requests.push({ url, options });
        if (url.endsWith("/heartbeat")) return Promise.resolve(jsonResponse(200, { online: true }));
        return Promise.resolve(jsonResponse(200, { match_url: "/pvp/match/shared-match" }));
    };
    const { location, timers } = loadPollingScript(fetch);
    await new Promise(setImmediate);
    await new Promise(setImmediate);

    assert.equal(requests.filter((request) => request.url.endsWith("/state")).length, 1);
    assert.equal(location.href, "/pvp/match/shared-match");
    assert.equal(timers.size, 0);
});
