(() => {
  const root = document.getElementById('cyberpunk-game');
  if (!root) return;
  const page = document.body;
  const actionUrl = page.dataset.actionUrl;
  const csrf = page.dataset.csrf;
  const warehouseElement = document.getElementById('game-warehouse');
  const inventoryElement = document.getElementById('game-inventory');
  const orderElement = document.getElementById('customer-order');
  const message = document.getElementById('game-message');
  const selectedLabel = document.getElementById('selected-slot-label');
  let state;
  let revision = Number(root.dataset.revision || 0);
  let selected = null;
  let timerInterval = null;
  let timerExpiredSent = false;
  let timerRetryAt = 0;
  try { state = JSON.parse(root.dataset.state || '{}'); } catch (_) { state = {}; }
  const nameFor = (id) => state.level.products[String(id)]?.name || 'Unknown product';
  const imageFor = (id) => state.level.products[String(id)]?.image_url;
  const textNode = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const imgNode = (id, className) => {
    const url = imageFor(id);
    if (!url) return null;
    const image = document.createElement('img');
    image.src = url;
    image.alt = '';
    image.className = className || '';
    image.onerror = () => image.remove();
    return image;
  };
  const makeActionId = () => globalThis.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

  const send = async (action) => {
    message.textContent = '';
    try {
      const response = await fetch(actionUrl, {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify({action_id: makeActionId(), revision, action}),
      });
      const payload = await response.json();
      if (response.status === 409 && payload.current) {
        state = payload.current.state;
        revision = payload.current.revision;
        render();
        message.textContent = payload.error;
        return;
      }
      if (!response.ok) {
        if (payload.current) { state = payload.current.state; revision = payload.current.revision; render(); }
        throw new Error(payload.error || 'The action could not be completed.');
      }
      if (action.type === 'tick' && payload.status === 'active') {
        timerExpiredSent = false;
        timerRetryAt = Date.now() + 700;
      } else if (JSON.stringify(payload.state?.timers) !== JSON.stringify(state.timers)) {
        timerExpiredSent = false;
        timerRetryAt = 0;
      }
      state = payload.state;
      revision = payload.revision;
      render();
    } catch (error) {
      message.textContent = error.message || 'The connection failed. Your game state has been kept.';
    }
  };

  const renderWarehouse = () => {
    warehouseElement.replaceChildren();
    const layout = state.warehouse;
    const columns = layout.columns || 10;
    (layout.shelves || []).forEach((shelf, row) => {
      const shelfElement = textNode('section', 'cw-game-shelf');
      shelfElement.append(textNode('h3', '', shelf.name || `Shelf ${row + 1}`));
      const cellsElement = textNode('div', 'cw-game-cells');
      cellsElement.style.setProperty('--cw-columns', columns);
      const cellWidth = window.matchMedia('(max-width: 430px)').matches ? 52 :
        window.matchMedia('(max-width: 650px)').matches ? 58 : 66;
      cellsElement.style.minWidth = `${columns * cellWidth}px`;
      (shelf.cells || []).forEach((cell, column) => {
        const cellElement = textNode('div', 'cw-game-cell');
        cellElement.setAttribute('aria-label', `Cell ${row + 1}, ${column + 1}`);
        const products = Array.isArray(cell.slots) ? cell.slots : [null, null, null];
        const triple = products.length === 3 && products[0] !== null && products.every((value) => value === products[0]);
        cellElement.classList.toggle('is-triple', triple);
        for (let slot = 0; slot < 3; slot += 1) {
          const productId = products[slot] ?? null;
          const button = textNode('button', `cw-game-slot${productId === null ? ' is-empty' : ''}${selected && selected.row === row && selected.column === column && selected.slot === slot ? ' is-selected' : ''}`);
          button.type = 'button';
          button.dataset.row = String(row); button.dataset.column = String(column); button.dataset.slot = String(slot);
          button.setAttribute('aria-label', productId === null ? `Empty slot, cell ${row + 1}, ${column + 1}` : `${nameFor(productId)}, cell ${row + 1}, ${column + 1}, slot ${slot + 1}`);
          button.setAttribute('aria-pressed', String(Boolean(selected && selected.row === row && selected.column === column && selected.slot === slot)));
          button.disabled = state.status !== 'active';
          if (productId !== null) {
            const image = imgNode(productId);
            if (image) button.append(image);
            else button.append(textNode('span', '', nameFor(productId)));
            button.draggable = true;
            button.addEventListener('dragstart', (event) => event.dataTransfer.setData('application/x-cw-game-slot', JSON.stringify({row,column,slot})));
          }
          button.addEventListener('click', () => {
            if (productId !== null || selected) selectOrMove({row,column,slot});
          });
          button.addEventListener('dragover', (event) => event.preventDefault());
          button.addEventListener('drop', (event) => {
            event.preventDefault();
            try { const from = JSON.parse(event.dataTransfer.getData('application/x-cw-game-slot')); send({type:'move',source:from,target:{row,column,slot}}); selected = null; } catch (_) { /* Ignore malformed drag payloads. */ }
          });
          cellElement.append(button);
        }
        cellsElement.append(cellElement);
      });
      shelfElement.append(cellsElement);
      warehouseElement.append(shelfElement);
    });
  };

  const selectOrMove = (target) => {
    if (!selected) { selected = target; renderWarehouse(); selectedLabel.textContent = `Selected ${nameFor(state.warehouse.shelves[target.row].cells[target.column].slots[target.slot])}`; return; }
    if (selected.row === target.row && selected.column === target.column && selected.slot === target.slot) {
      selected = null; selectedLabel.textContent = 'No product selected'; renderWarehouse(); return;
    }
    const source = selected; selected = null; selectedLabel.textContent = 'No product selected';
    send({type:'move', source, target});
  };

  const renderOrder = () => {
    orderElement.replaceChildren();
    const orders = state.level.orders || [];
    const order = orders[state.order_index];
    const customerName = document.getElementById('customer-name');
    const customerDisplay = document.getElementById('customer-display');
    customerDisplay.replaceChildren();
    if (!order) { customerName.textContent = 'No active customer'; return; }
    customerName.textContent = order.customer?.name || order.name;
    if (order.customer?.image_url) {
      const image = document.createElement('img'); image.src = order.customer.image_url; image.alt = ''; image.onerror = () => image.remove(); customerDisplay.append(image);
    }
    const info = textNode('p', '', order.customer ? `Order ${state.order_index + 1} of ${orders.length} · waiting time setting ${order.customer.waiting_seconds}s` : `Order ${state.order_index + 1} of ${orders.length}`);
    customerDisplay.append(info);
    order.items.forEach((item) => {
      const key = String(item.product_id);
      const remaining = state.order_remaining[key] || 0;
      const row = textNode('div', 'cw-order-item');
      const product = textNode('div', 'cw-order-product');
      const image = imgNode(item.product_id); if (image) product.append(image);
      product.append(textNode('span', '', nameFor(item.product_id)));
      const quantity = textNode('span', 'cw-order-quantity', `${item.quantity - remaining} / ${item.quantity}`);
      row.append(product, quantity); orderElement.append(row);
    });
  };

  const renderInventory = () => {
    inventoryElement.replaceChildren();
    let count = 0;
    Object.entries(state.inventory || {}).forEach(([key,value]) => {
      const quantity = Number(value);
      if (!quantity) return;
      count += quantity;
      const row = textNode('div', 'cw-inventory-item');
      const product = textNode('div', 'cw-inventory-product');
      const image = imgNode(Number(key)); if (image) product.append(image);
      product.append(textNode('span', '', nameFor(Number(key))));
      const control = textNode('div', 'cw-delivery-control');
      const remaining = Number(state.order_remaining[key] || 0);
      if (remaining > 0 && state.status === 'active') {
        const input = document.createElement('input'); input.type = 'number'; input.min = '1'; input.max = String(Math.min(quantity,remaining)); input.step = '1'; input.value = input.max; input.setAttribute('aria-label',`Quantity of ${nameFor(Number(key))} to deliver`);
        const button = textNode('button', '', 'Give'); button.type = 'button'; button.addEventListener('click', () => send({type:'deliver',product_id:Number(key),quantity:Number.parseInt(input.value,10)}));
        control.append(input,button);
      } else {
        control.append(textNode('span', 'cw-game-status', `×${quantity}`));
      }
      row.append(product,control); inventoryElement.append(row);
    });
    document.getElementById('inventory-count').textContent = `${count} item${count === 1 ? '' : 's'}`;
    if (!count) inventoryElement.append(textNode('p', 'cw-game-help', 'No products collected yet. Make a matching triple in one cell.'));
  };

  const render = () => {
    renderWarehouse(); renderOrder(); renderInventory();
    document.getElementById('move-count').textContent = `${state.moves} move${state.moves === 1 ? '' : 's'}`;
    document.getElementById('game-progress').textContent = `Customer ${Math.min(state.order_index + (state.status === 'completed' ? 0 : 1),state.level.orders.length)} of ${state.level.orders.length}`;
    updateTimer();
    const pause = document.getElementById('pause-game');
    pause.textContent = state.status === 'paused' ? 'Resume' : 'Pause';
    pause.disabled = ['completed','game_over'].includes(state.status);
    pause.onclick = () => send({type:state.status === 'paused' ? 'resume' : 'pause'});
    const result = document.getElementById('game-result');
    result.hidden = !['completed','game_over'].includes(state.status);
    if (state.status === 'completed') {
      document.getElementById('result-title').textContent = 'Level Completed';
      document.getElementById('result-copy').textContent = `All customer orders are complete. You sorted the warehouse in ${state.moves} moves.`;
    } else if (state.status === 'game_over') {
      document.getElementById('result-title').textContent = 'Game Over';
      document.getElementById('result-copy').textContent = state.timers?.game_over_reason === 'customer_wait_expired' ? 'The customer ran out of waiting time.' : state.timers?.game_over_reason === 'level_time_expired' ? 'The level time limit expired.' : 'This run has ended.';
    }
    if (state.status === 'paused') message.textContent = 'Game paused. Resume when you are ready.';
  };

  const updateTimer = () => {
    const timerElement = document.getElementById('game-timer');
    const timers = state.timers || {};
    const deadlines = [timers.level_deadline_at, timers.customer_deadline_at].filter(Boolean);
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
    if (!deadlines.length) { timerElement.textContent = ''; timerElement.hidden = true; return; }
    timerElement.hidden = false;
    const tick = () => {
      const now = Date.now();
      const levelLeft = timers.level_deadline_at ? Math.max(0,Math.ceil((Date.parse(timers.level_deadline_at) - now) / 1000)) : null;
      const customerLeft = timers.customer_deadline_at ? Math.max(0,Math.ceil((Date.parse(timers.customer_deadline_at) - now) / 1000)) : null;
      timerElement.textContent = [levelLeft === null ? null : `Level ${levelLeft}s`,customerLeft === null ? null : `Customer ${customerLeft}s`].filter(Boolean).join(' · ');
      const expired = (levelLeft !== null && levelLeft === 0) || (customerLeft !== null && customerLeft === 0);
      if (expired && state.status === 'active' && !timerExpiredSent && Date.now() >= timerRetryAt) {
        timerExpiredSent = true;
        send({type:'tick'});
      }
      if (state.status !== 'active' || expired) { clearInterval(timerInterval); timerInterval = null; }
    };
    if (state.status === 'paused') {
      const pausedAt = Date.parse(timers.paused_at || '') || Date.now();
      timerElement.textContent = deadlines.map((deadline) => {
        const left = Math.max(0,Math.ceil((Date.parse(deadline) - pausedAt) / 1000));
        return `${deadline === timers.level_deadline_at ? 'Level' : 'Customer'} ${left}s`;
      }).join(' · ') + ' · paused';
      return;
    }
    if (['completed','game_over'].includes(state.status)) { timerElement.textContent = ''; timerElement.hidden = true; return; }
    tick();
    if (state.status === 'active') timerInterval = setInterval(tick,500);
  };

  render();
})();
