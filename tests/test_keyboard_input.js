const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const projectRoot = path.resolve(__dirname, '..');
const script = fs.readFileSync(path.join(projectRoot, 'static/script.js'), 'utf8');
const styles = fs.readFileSync(path.join(projectRoot, 'static/style.css'), 'utf8');
let elements;

class FakeClassList {
    constructor(element) { this.element = element; }
    values() { return new Set(this.element.className.split(/\s+/).filter(Boolean)); }
    add(name) { const values = this.values(); values.add(name); this.element.className = [...values].join(' '); }
    remove(...names) { const values = this.values(); names.forEach(name => values.delete(name)); this.element.className = [...values].join(' '); }
    contains(name) { return this.values().has(name); }
    toggle(name, force) {
        const has = this.contains(name);
        if (force === true || (!has && force !== false)) this.add(name);
        else if (has) this.remove(name);
        return this.contains(name);
    }
}

class FakeElement {
    constructor(id = '') {
        this._id = '';
        this.id = id;
        this.className = '';
        this.classList = new FakeClassList(this);
        this.children = [];
        this.listeners = {};
        this.textContent = '';
        this.dataset = {};
        this.parentNode = null;
        this.disabled = false;
    }
    set id(value) { this._id = value; if (value) elements.set(value, this); }
    get id() { return this._id; }
    set innerHTML(value) { this.children = []; }
    addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    getAttribute(name) { return name === 'data-key' ? this.dataset.key : null; }
    closest(selector) {
        if (selector.includes('button') && this.classList.contains('key')) return this;
        return this.matchesUnrelatedControl ? this : null;
    }
    get offsetWidth() { return 100; }
    dispatch(type, event = {}) {
        const emitted = {
            target: this,
            currentTarget: this,
            key: undefined,
            detail: 0,
            ctrlKey: false,
            metaKey: false,
            altKey: false,
            defaultPrevented: false,
            preventDefault() { this.defaultPrevented = true; },
            ...event,
        };
        if (type === 'click' && emitted.detail > 0) this.focused = true;
        for (const listener of this.listeners[type] || []) listener(emitted);
        return emitted;
    }
    blur() { this.focused = false; }
}

function makeGame(initialState = { guesses: [], tries: 0, game_over: false, won: false, target_word: null }) {
    elements = new Map();
    const documentListeners = {};
    const ids = [
        'new-game', 'game-status', 'tries-count', 'game-board',
        'mobile-menu-btn', 'sidebar', 'games-count', 'win-rate', 'current-streak',
        'max-streak', 'key-enter', 'key-backspace',
    ];
    ids.forEach(id => new FakeElement(id));
    const keyButtons = 'QWERTYUIOPASDFGHJKLZXCVBNM'.split('').map(letter => {
        const button = new FakeElement();
        button.className = 'key';
        button.dataset.key = letter;
        return button;
    });
    const sent = [];
    let guessResponses = [];
    let gameState = initialState;
    const document = {
        body: new FakeElement('body'),
        head: new FakeElement('head'),
        addEventListener(type, callback) { (documentListeners[type] ||= []).push(callback); },
        getElementById(id) { return elements.get(id) || null; },
        createElement() { return new FakeElement(); },
        querySelectorAll(selector) {
            if (selector === '.guess-row') return elements.get('game-board').children;
            if (selector === '.key[data-key]') return keyButtons;
            if (selector === '.user-stats span') return [];
            return [];
        },
        querySelector(selector) {
            const match = selector.match(/^\[data-key="([A-Z])"\]$/);
            return match ? keyButtons.find(key => key.dataset.key === match[1]) : null;
        },
    };
    const context = {
        document,
        window: { innerWidth: 1024 },
        console,
        setTimeout,
        fetch(url, options = {}) {
            sent.push({ url, options });
            if (url === '/game_state') {
                return Promise.resolve({ ok: true, json: async () => gameState });
            }
            if (url === '/new_game') {
                gameState = { guesses: [], tries: 0, game_over: false, won: false, target_word: null };
                return Promise.resolve({ ok: true, json: async () => ({ message: 'New game started' }) });
            }
            const result = guessResponses.shift() || {
                result: 'CRANE'.split('').map(letter => ({ letter, status: 'gray' })),
                tries: 1,
                game_over: false,
                won: false,
                target_word: null,
            };
            return Promise.resolve({ ok: true, json: async () => result });
        },
    };
    vm.runInNewContext(script, context, { filename: 'static/script.js' });

    const flush = () => new Promise(resolve => setImmediate(resolve));
    const keydown = (key, modifiers = {}) => {
        const event = document.body.dispatch('keydown', { key, ...modifiers });
        for (const listener of documentListeners.keydown || []) listener(event);
        return event;
    };
    const tileText = row => Array.from({ length: 5 }, (_, i) => elements.get(`row-${row}-letter-${i}`).textContent).join('');
    return {
        document, elements, keyButtons, sent, keydown, flush, tileText,
        setGuessResponses(responses) { guessResponses = [...responses]; },
    };
}

test('physical letters fill the active row, uppercase, cap at five, and wait for Enter', async () => {
    const game = makeGame();
    await game.flush();
    for (const letter of 'cRaNeX') game.keydown(letter);
    assert.equal(game.tileText(0), 'CRANE');
    assert.equal(game.sent.filter(item => item.url === '/guess').length, 0);
    game.keydown('Enter');
    await game.flush();
    assert.equal(game.sent.filter(item => item.url === '/guess').length, 1);
    assert.equal(JSON.parse(game.sent.find(item => item.url === '/guess').options.body).guess, 'CRANE');
    assert.equal(game.tileText(0), 'CRANE');
    assert.equal(game.elements.get('tries-count').textContent, 1);
    assert.equal(game.elements.get('row-0-letter-1').classList.contains('gray'), true);
    assert.equal(game.keyButtons.find(key => key.dataset.key === 'C').classList.contains('absent'), true);
    game.keydown('Backspace');
    assert.equal(game.tileText(0), 'CRANE');
    assert.equal(game.tileText(1), '');
});

test('Backspace edits only the current row, and Enter with fewer than five does not submit', async () => {
    const game = makeGame();
    await game.flush();
    game.keydown('Backspace');
    assert.equal(game.tileText(0), '');
    assert.equal(game.sent[0].url, '/game_state');
    for (const letter of 'ABCD') game.keydown(letter);
    const enter = game.keydown('Enter');
    await game.flush();
    assert.equal(enter.defaultPrevented, true);
    assert.equal(game.tileText(0), 'ABCD');
    assert.equal(game.sent.filter(item => item.url === '/guess').length, 0);
    assert.match(game.elements.get('game-status').textContent, /5-letter/);
});

test('on-screen keys share the current guess and Enter submits once', async () => {
    const game = makeGame();
    await game.flush();
    game.keyButtons.find(key => key.dataset.key === 'C').dispatch('click', { detail: 1 });
    game.keydown('r');
    game.keyButtons.find(key => key.dataset.key === 'A').dispatch('click', { detail: 1 });
    game.keyButtons.find(key => key.dataset.key === 'N').dispatch('click', { detail: 1 });
    game.keyButtons.find(key => key.dataset.key === 'E').dispatch('click', { detail: 1 });
    assert.equal(game.tileText(0), 'CRANE');
    game.elements.get('key-enter').dispatch('click', { detail: 1 });
    await game.flush();
    assert.equal(game.sent.filter(item => item.url === '/guess').length, 1);
    assert.equal(game.elements.get('key-enter').focused, false);
});

test('clicking Backspace does not leave focus that captures physical Enter', async () => {
    const game = makeGame();
    await game.flush();
    for (const letter of 'CRANE') game.keydown(letter);

    const backspaceButton = game.elements.get('key-backspace');
    backspaceButton.dispatch('click', { detail: 1 });
    assert.equal(backspaceButton.focused, false);
    assert.equal(game.tileText(0), 'CRAN');

    const shortEnter = game.keydown('Enter');
    await game.flush();
    assert.equal(shortEnter.defaultPrevented, true);
    assert.equal(game.tileText(0), 'CRAN');
    assert.equal(game.sent.filter(item => item.url === '/guess').length, 0);

    game.keydown('E');
    game.keydown('Enter');
    await game.flush();
    assert.equal(game.sent.filter(item => item.url === '/guess').length, 1);
    assert.equal(game.tileText(0), 'CRANE');
});

test('keyboard activation keeps its focus indicator available', async () => {
    const game = makeGame();
    await game.flush();
    const letterKey = game.keyButtons.find(key => key.dataset.key === 'A');
    letterKey.focused = true;
    letterKey.dispatch('click', { detail: 0 });
    assert.equal(letterKey.focused, true);
    assert.match(styles, /\.key:focus-visible\s*\{[^}]*outline:/s);
});

test('invalid word remains editable and an unrelated modifier shortcut is ignored', async () => {
    const game = makeGame();
    await game.flush();
    game.setGuessResponses([{ error: 'Not in word list' }]);
    for (const letter of 'QZXJK') game.keydown(letter);
    game.keydown('Enter');
    await game.flush();
    assert.equal(game.tileText(0), 'QZXJK');
    assert.equal(game.elements.get('row-0').classList.contains('shake'), true);
    game.keydown('Backspace');
    game.keydown('E');
    assert.equal(game.tileText(0), 'QZXJE');

    const shortcut = game.keydown('r', { ctrlKey: true });
    assert.equal(shortcut.defaultPrevented, false);
    assert.equal(game.tileText(0), 'QZXJE');
});

test('editable controls are ignored while a focused on-screen key can be followed by physical input', async () => {
    const game = makeGame();
    await game.flush();
    const editable = new FakeElement();
    editable.matchesUnrelatedControl = true;
    game.keydown('A', { target: editable });
    assert.equal(game.tileText(0), '');

    const focusedGameKey = game.keyButtons.find(key => key.dataset.key === 'C');
    game.keydown('r', { target: focusedGameKey });
    assert.equal(game.tileText(0), 'R');
});

test('restored game state is rendered and game-over ignores keyboard input', async () => {
    const savedResult = 'CRANE'.split('').map((letter, index) => ({ letter, status: index === 1 ? 'green' : 'gray' }));
    const game = makeGame({ guesses: [savedResult], tries: 1, game_over: true, won: false, target_word: 'ARISE' });
    await game.flush();
    assert.equal(game.tileText(0), 'CRANE');
    assert.equal(game.elements.get('tries-count').textContent, 1);
    assert.match(game.elements.get('game-status').textContent, /ARISE/);
    assert.equal(game.keyButtons.find(key => key.dataset.key === 'R').classList.contains('correct'), true);
    assert.equal(game.elements.get('row-1').classList.contains('active'), false);
    game.keydown('A');
    game.keydown('Enter');
    await game.flush();
    assert.equal(game.tileText(1), '');
    assert.equal(game.sent.filter(item => item.url === '/guess').length, 0);
});

test('a winning guess still reveals the answer using the normal result response', async () => {
    const game = makeGame();
    await game.flush();
    game.setGuessResponses([{
        result: 'CRANE'.split('').map(letter => ({ letter, status: 'green' })),
        tries: 1,
        game_over: true,
        won: true,
        target_word: 'CRANE',
    }]);
    for (const letter of 'CRANE') game.keydown(letter);
    game.keydown('Enter');
    await game.flush();
    assert.match(game.elements.get('game-status').textContent, /won in 1 tries/);
    assert.equal(game.elements.get('row-0-letter-0').classList.contains('green'), true);
});

test('New Game clears the active row and keyboard colors', async () => {
    const result = 'CRANE'.split('').map((letter, index) => ({ letter, status: index === 0 ? 'green' : 'gray' }));
    const game = makeGame({ guesses: [result], tries: 1, game_over: false, won: false, target_word: null });
    await game.flush();
    game.keydown('A');
    assert.equal(game.tileText(1), 'A');
    game.elements.get('new-game').dispatch('click');
    await game.flush();
    assert.equal(game.tileText(0), '');
    assert.equal(game.tileText(1), '');
    assert.equal(game.keyButtons.find(key => key.dataset.key === 'C').classList.contains('unused'), true);
});
