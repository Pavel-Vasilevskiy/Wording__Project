const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "..", "static", "site_navigation.js"), "utf8");

function makeNavigation() {
    const listeners = {};
    const attributes = { "aria-expanded": "false", "aria-label": "Відкрити меню" };
    const firstLink = { focused: false, focus() { this.focused = true; } };
    const menu = {
        handlers: {},
        addEventListener(type, callback) { this.handlers[type] = callback; },
        querySelector() { return firstLink; },
    };
    const toggle = {
        handlers: {},
        addEventListener(type, callback) { this.handlers[type] = callback; },
        setAttribute(name, value) { attributes[name] = value; },
        getAttribute(name) { return attributes[name]; },
        focus() { this.focused = true; },
    };
    const root = {
        dataset: { open: "false" },
        handlers: {},
        contains(target) { return target === this || target === toggle || target === menu || target === firstLink; },
        addEventListener(type, callback) { this.handlers[type] = callback; },
        querySelector(selector) {
            if (selector === "[data-site-menu-toggle]") return toggle;
            if (selector === "[data-site-menu]") return menu;
            return null;
        },
    };
    const document = {
        handlers: {},
        querySelectorAll() { return [root]; },
        addEventListener(type, callback) { this.handlers[type] = callback; },
    };
    vm.runInNewContext(script, { document, window: { matchMedia: () => ({ matches: true }) } });
    return { document, root, toggle, menu, firstLink, attributes };
}

test("mobile menu opens, updates aria state and focuses first link", () => {
    const nav = makeNavigation();
    nav.toggle.handlers.click();
    assert.equal(nav.root.dataset.open, "true");
    assert.equal(nav.attributes["aria-expanded"], "true");
    assert.equal(nav.firstLink.focused, true);
});

test("mobile menu closes when a link is selected or outside is clicked", () => {
    const nav = makeNavigation();
    nav.toggle.handlers.click();
    nav.menu.handlers.click({ target: { closest: (selector) => selector === "a" ? {} : null } });
    assert.equal(nav.root.dataset.open, "false");
    nav.toggle.handlers.click();
    nav.document.handlers.click({ target: {} });
    assert.equal(nav.root.dataset.open, "false");
    assert.equal(nav.attributes["aria-expanded"], "false");
});

test("Escape closes the menu and returns focus to its toggle", () => {
    const nav = makeNavigation();
    nav.toggle.handlers.click();
    nav.root.handlers.keydown({ key: "Escape" });
    assert.equal(nav.root.dataset.open, "false");
    assert.equal(nav.toggle.focused, true);
});
