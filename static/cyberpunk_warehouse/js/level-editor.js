(() => {
  const form = document.getElementById('level-editor-form');
  if (!form) return;
  const mode = document.getElementById('level-mode');
  let modeConfigurations = {};
  try { modeConfigurations = JSON.parse(form.dataset.modeConfigurations || '{}'); } catch (_) { modeConfigurations = {}; }
  const writeModeConfig = (key) => {
    const panel = document.querySelector(`.cw-mode-fields[data-mode="${key}"]`);
    if (!panel) return;
    const values = {};
    panel.querySelectorAll('[name]').forEach((input) => {
      if (input.multiple) values[input.name] = Array.from(input.selectedOptions).map((option) => option.value);
      else if (input.type === 'checkbox') values[input.name] = input.checked;
      else values[input.name] = input.value;
    });
    modeConfigurations[key] = values;
  };
  const applyModeConfig = (key) => {
    const panel = document.querySelector(`.cw-mode-fields[data-mode="${key}"]`);
    const values = modeConfigurations[key];
    if (!panel || !values) return;
    const mappings = key === 'classic'
      ? [['objective','classic_objective'],['time_limit_seconds','classic_time_limit_seconds'],['lives','classic_lives'],['scoring_rules.base_points','base_points']]
      : key === 'timed'
        ? [['time_limit_seconds','timed_time_limit_seconds'],['lives','timed_lives'],['failure_conditions','failure_conditions'],['scoring_rules.base_points','base_points']]
        : key === 'unlimited'
          ? [['move_limit','move_limit'],['scoring_rules.base_points','base_points']]
          : [['match_duration_seconds','match_duration_seconds'],['competitive_scoring.points_per_order','points_per_order'],['synchronization_settings.turn_based','turn_based'],['completion_conditions','completion_conditions']];
    mappings.forEach(([path,name]) => {
      const input = form.elements.namedItem(name);
      if (!input || input.closest('.cw-mode-fields') !== panel) return;
      const value = path.split('.').reduce((item, part) => item?.[part], values);
      if (input.multiple && Array.isArray(value)) Array.from(input.options).forEach((option) => { option.selected = value.includes(option.value); });
      else if (input.type === 'checkbox') input.checked = Boolean(value);
      else if (value !== undefined && value !== null) input.value = value;
    });
  };
  let currentModeKey = mode?.selectedOptions[0]?.dataset.modeKey;
  const updateMode = () => {
    const key = mode?.selectedOptions[0]?.dataset.modeKey;
    if (currentModeKey && currentModeKey !== key) writeModeConfig(currentModeKey);
    if (key && currentModeKey !== key) applyModeConfig(key);
    currentModeKey = key;
    document.querySelectorAll('.cw-mode-fields').forEach((section) => {
      section.hidden = section.dataset.mode !== key;
      section.setAttribute('aria-hidden', section.hidden ? 'true' : 'false');
    });
  };
  mode?.addEventListener('change', updateMode);
  updateMode();
  const rows = document.getElementById('layout-rows');
  const columns = document.getElementById('layout-columns');
  const updateGrid = () => {
    const rowCount = Math.min(8, Math.max(1, Number(rows.value) || 1));
    const columnCount = Math.min(12, Math.max(1, Number(columns.value) || 1));
    document.querySelectorAll('[data-shelf-row]').forEach((row, index) => {
      row.hidden = index >= rowCount;
      row.querySelectorAll('[data-cell-col]').forEach((cell) => { cell.hidden = Number(cell.dataset.cellCol) >= columnCount; });
    });
  };
  rows?.addEventListener('input', updateGrid);
  columns?.addEventListener('input', updateGrid);
  updateGrid();
  const warehouseGrid = document.getElementById('warehouse-grid');
  if (warehouseGrid) {
    const clearButton = document.createElement('button');
    clearButton.type = 'button';
    clearButton.className = 'ds-button ds-button--secondary cw-clear-layout';
    clearButton.textContent = 'Clear warehouse layout';
    clearButton.addEventListener('click', () => {
      warehouseGrid.querySelectorAll('.cw-layout-cell').forEach((cell) => {
        const select = cell.querySelector('select');
        const quantity = cell.querySelector('input[type="number"]');
        select.value = '';
        quantity.value = '';
        select.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
    warehouseGrid.insertAdjacentElement('beforebegin', clearButton);
  }
  const updateProductPreview = (select) => {
    const container = select.closest('.cw-layout-cell')?.querySelector('.cw-cell-product-preview');
    if (!container) return;
    container.replaceChildren();
    const option = select.selectedOptions[0];
    if (!option?.value) return;
    const imageUrl = option.dataset.image;
    if (imageUrl) {
      const image = document.createElement('img');
      image.src = imageUrl;
      image.alt = '';
      container.append(image);
    }
    const name = document.createElement('span');
    name.textContent = option.textContent;
    container.append(name);
  };
  document.querySelectorAll('.cw-layout-cell select').forEach((select) => {
    select.addEventListener('change', () => updateProductPreview(select));
    updateProductPreview(select);
  });
  const productOptions = Array.from(document.querySelector('.cw-layout-cell select')?.options || []);
  document.querySelectorAll('.cw-product-catalog-card').forEach((card, index) => {
    const option = productOptions[index + 1];
    if (!option) return;
    card.draggable = true;
    card.dataset.productId = option.value;
    card.addEventListener('dragstart', (event) => {
      event.dataTransfer.setData('application/x-cw-product', option.value);
      event.dataTransfer.effectAllowed = 'copy';
    });
  });
  document.querySelectorAll('.cw-layout-cell').forEach((cell) => {
    const select = cell.querySelector('select');
    const quantity = cell.querySelector('input[type="number"]');
    const coord = select.name.match(/cell_product_(\d+)_(\d+)/);
    if (!coord) return;
    const [row, column] = coord.slice(1);
    const handle = document.createElement('span');
    handle.className = 'cw-cell-drag-handle';
    handle.textContent = '↕ Move';
    handle.draggable = true;
    handle.tabIndex = 0;
    handle.setAttribute('aria-label', `Drag cell ${Number(row) + 1}.${Number(column) + 1} to move its product`);
    handle.addEventListener('dragstart', (event) => {
      if (!select.value) { event.preventDefault(); return; }
      event.dataTransfer.setData('application/x-cw-cell', `${row}:${column}`);
      event.dataTransfer.effectAllowed = 'move';
    });
    cell.append(handle);
    cell.addEventListener('dragover', (event) => { event.preventDefault(); cell.classList.add('cw-drop-target'); });
    cell.addEventListener('dragleave', () => cell.classList.remove('cw-drop-target'));
    cell.addEventListener('drop', (event) => {
      event.preventDefault(); cell.classList.remove('cw-drop-target');
      const productId = event.dataTransfer.getData('application/x-cw-product');
      if (productId) {
        select.value = productId;
        if (!quantity.value) quantity.value = '3';
        select.dispatchEvent(new Event('change', { bubbles: true }));
        return;
      }
      const origin = event.dataTransfer.getData('application/x-cw-cell');
      if (!origin) return;
      const [sourceRow, sourceColumn] = origin.split(':');
      const source = document.querySelector(`[name="cell_product_${sourceRow}_${sourceColumn}"]`)?.closest('.cw-layout-cell');
      if (!source || source === cell) return;
      const sourceSelect = source.querySelector('select');
      const sourceQuantity = source.querySelector('input[type="number"]');
      select.value = sourceSelect.value; quantity.value = sourceQuantity.value;
      sourceSelect.value = ''; sourceQuantity.value = '';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      sourceSelect.dispatchEvent(new Event('change', { bubbles: true }));
    });
  });
  const customer = document.getElementById('level-customer');
  const customerPreview = document.getElementById('selected-customer-preview');
  const updateCustomer = () => {
    if (!customerPreview) return;
    customerPreview.replaceChildren();
    const option = customer?.selectedOptions[0];
    if (!option?.value) { customerPreview.textContent = 'Select a customer to see their artwork and waiting time.'; return; }
    if (option.dataset.image) {
      const image = document.createElement('img'); image.src = option.dataset.image; image.alt = ''; customerPreview.append(image);
    }
    const label = document.createElement('span');
    label.textContent = `${option.textContent.trim()} · default wait ${option.dataset.wait} seconds`;
    customerPreview.append(label);
  };
  customer?.addEventListener('change', updateCustomer);
  updateCustomer();
  const search = document.getElementById('product-search');
  const category = document.getElementById('product-category');
  const rarity = document.getElementById('product-rarity');
  const filterCatalog = () => document.querySelectorAll('.cw-product-catalog-card').forEach((card) => {
    card.hidden = !(card.dataset.name.includes((search?.value || '').trim().toLowerCase()) &&
      (!category?.value || card.dataset.category === category.value) &&
      (!rarity?.value || card.dataset.rarity === rarity.value));
  });
  [search, category, rarity].forEach((control) => control?.addEventListener('input', filterCatalog));
  [category, rarity].forEach((control) => control?.addEventListener('change', filterCatalog));
  const orderLines = document.getElementById('order-lines');
  const orderCount = document.querySelector('[name="order_items_count"]');
  const lineTemplate = document.getElementById('order-line-template');
  const updateOrderLines = () => {
    const lines = Array.from(orderLines.querySelectorAll('.cw-order-line'));
    orderCount.value = String(lines.length);
    lines.forEach((line, index) => {
      line.querySelector('select').name = `order_product_${index}`;
      line.querySelector('input').name = `order_quantity_${index}`;
    });
  };
  const updateOrderPreview = (select) => {
    const target = select.closest('.cw-order-line')?.querySelector('.cw-order-product-preview');
    if (!target) return;
    target.replaceChildren();
    const option = select.selectedOptions[0];
    if (option?.value && option.dataset.image) {
      const img = document.createElement('img'); img.src = option.dataset.image; img.alt = '';
      const text = document.createElement('span'); text.textContent = option.textContent;
      target.append(img, text);
    }
  };
  document.getElementById('add-order-line')?.addEventListener('click', () => {
    const fragment = lineTemplate.content.cloneNode(true);
    orderLines.append(fragment);
    updateOrderLines();
  });
  orderLines?.addEventListener('change', (event) => {
    if (event.target.matches('select')) updateOrderPreview(event.target);
  });
  orderLines?.addEventListener('click', (event) => {
    if (!event.target.closest('.cw-remove-order-line')) return;
    event.target.closest('.cw-order-line').remove(); updateOrderLines();
  });
  orderLines?.querySelectorAll('.cw-order-line select').forEach((select) => {
    select.addEventListener('change', () => updateOrderPreview(select));
    updateOrderPreview(select);
  });
  updateOrderLines();
  let dirty = false;
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; });
  form.addEventListener('submit', (event) => {
    if (event.submitter?.value === 'archive' && !window.confirm('Archive this level?')) event.preventDefault();
    else dirty = false;
  });
  window.addEventListener('beforeunload', (event) => {
    if (dirty) { event.preventDefault(); event.returnValue = ''; }
  });
})();
