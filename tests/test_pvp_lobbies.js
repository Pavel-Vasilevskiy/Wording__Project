const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "..", "static", "pvp_lobbies.js"), "utf8");

function element(tag = "div") {
    return {
        tagName: tag.toUpperCase(), textContent: "", value: "", hidden: false,
        disabled: false, className: "", dataset: {}, children: [], attributes: {}, handlers: {},
        append(...children) { this.children.push(...children); },
        appendChild(child) { this.children.push(child); },
        replaceChildren(...children) { this.children = children; },
        addEventListener(name, callback) { this.handlers[name] = callback; },
        setAttribute(name, value) { this.attributes[name] = value; },
    };
}

function load(fetch) {
    const page = element("section");
    page.dataset = { refreshUrl: "/pvp/list-state", createUrl: "/pvp/create", csrfToken: "csrf-test" };
    const els = Object.fromEntries([
        "pvp-open-lobbies", "pvp-empty-lobbies", "pvp-current-action",
        "pvp-refresh-lobbies", "pvp-refresh-status",
    ].map((id) => [id, element(id.includes("button") ? "button" : "div")]));
    const timers = new Map();
    let nextId = 1;
    const window = {
        setTimeout(callback, delay) { const id = nextId++; timers.set(id, { callback, delay }); return id; },
        clearTimeout(id) { timers.delete(id); },
        addEventListener() {},
    };
    const document = {
        getElementById: (id) => id === "pvp-lobby-list-page" ? page : els[id],
        createElement: (tag) => element(tag),
    };
    vm.runInNewContext(script, { document, window, fetch, URL, URLSearchParams, AbortController });
    return { els, page, timers };
}

const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

test("list polling replaces lobby cards, avoids duplicates, and manual refresh runs immediately", async () => {
    const requests = [];
    const fetch = async (url, options) => {
        requests.push({ url, options });
        const lobby = requests.length === 1
            ? { id: "abc", leader: "Leader", member_count: 1, max_members: 20, created_at: "today", can_join: true, detail_url: "/pvp/abc", join_url: "/pvp/abc/join" }
            : { id: "def", leader: "Other", member_count: 2, max_members: 20, created_at: "later", can_join: true, detail_url: "/pvp/def", join_url: "/pvp/def/join" };
        return response({ my_lobby_id: null, my_lobby_public_id: null, lobbies: [lobby] });
    };
    const { els, timers } = load(fetch);
    await new Promise(setImmediate);
    await new Promise(setImmediate);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].options.method, "POST");
    assert.match(requests[0].options.body.toString(), /csrf-test/);
    assert.equal(els["pvp-open-lobbies"].children.length, 1);

    const tick = [...timers.entries()].find(([, item]) => item.delay === 5000);
    assert.ok(tick);
    timers.delete(tick[0]);
    tick[1].callback();
    await new Promise(setImmediate);
    await new Promise(setImmediate);
    assert.equal(requests.length, 2);
    assert.equal(els["pvp-open-lobbies"].children.length, 1);
    assert.equal(els["pvp-open-lobbies"].children[0].dataset.lobbyId, "def");

    els["pvp-refresh-lobbies"].handlers.click();
    await new Promise(setImmediate);
    await new Promise(setImmediate);
    assert.equal(requests.length, 3);
});

test("list polling preserves the page and shows a quiet retry message on network failure", async () => {
    const fetch = async () => { throw new TypeError("offline"); };
    const { els, timers } = load(fetch);
    await new Promise(setImmediate);
    await new Promise(setImmediate);
    assert.match(els["pvp-refresh-status"].textContent, /Не вдалося оновити список/);
    assert.equal(timers.size, 1);
});
