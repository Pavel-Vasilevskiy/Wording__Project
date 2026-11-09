(() => {
  const form = document.getElementById('level-editor-form');
  if (!form) return;
  let products = [];
  let layout = {};
  try { products = JSON.parse(form.dataset.products || '[]'); } catch (_) { products = []; }
  try { layout = JSON.parse(form.dataset.layout || '{}'); } catch (_) { layout = {}; }

  const mode = document.getElementById('level-mode');
  let modeConfigurations = {};
  try { modeConfigurations = JSON.parse(form.dataset.modeConfigurations || '{}'); } catch (_) { modeConfigurations = {}; }
  const writeModeConfig = (key) => {
    const panel = document.querySelector(`.cw-mode-fields[data-mode="${key}"]`);
    if (!panel) return;
    const values = {};
    panel.querySelectorAll('[name]').forEach((input) => {
      values[input.name] = input.multiple ? Array.from(input.selectedOptions).map((option) => option.value) :
        input.type === 'checkbox' ? input.checked : input.value;
    });
    modeConfigurations[key] = values;
  };
  const applyModeConfig = (key) => {
    const panel = document.querySelector(`.cw-mode-fields[data-mode="${key}"]`);
    const values = modeConfigurations[key];
    if (!panel || !values) return;
    const map = key === 'classic' ? [['objective','classic_objective'],['time_limit_seconds','classic_time_limit_seconds'],['lives','classic_lives']] :
      key === 'timed' ? [['time_limit_seconds','timed_time_limit_seconds'],['lives','timed_lives'],['failure_conditions','failure_conditions']] :
      key === 'unlimited' ? [['move_limit','move_limit']] :
      [['match_duration_seconds','match_duration_seconds'],['competitive_scoring.points_per_order','points_per_order'],['synchronization_settings.turn_based','turn_based'],['completion_conditions','completion_conditions']];
    map.forEach(([path,name]) => {
      const input = form.elements.namedItem(name);
      if (!input || input.closest('.cw-mode-fields') !== panel) return;
      const value = path.split('.').reduce((item, part) => item?.[part], values);
      if (input.multiple && Array.isArray(value)) Array.from(input.options).forEach((option) => { option.selected = value.includes(option.value); });
      else if (input.type === 'checkbox') input.checked = Boolean(value);
      else if (value !== undefined && value !== null) input.value = value;
    });
  };
  let currentMode = mode?.selectedOptions[0]?.dataset.modeKey;
  const updateMode = () => {
    const key = mode?.selectedOptions[0]?.dataset.modeKey;
    if (currentMode && currentMode !== key) writeModeConfig(currentMode);
    if (key && currentMode !== key) applyModeConfig(key);
    currentMode = key;
    document.querySelectorAll('.cw-mode-fields').forEach((panel) => {
      panel.hidden = panel.dataset.mode !== key;
      panel.setAttribute('aria-hidden', panel.hidden ? 'true' : 'false');
    });
  };
  mode?.addEventListener('change', updateMode);
  updateMode();

  const rowsInput = document.getElementById('layout-rows');
  const columnsInput = document.getElementById('layout-columns');
  const grid = document.getElementById('warehouse-grid');
  const copyStatus = document.getElementById('cell-copy-status');
  let copiedCell = null;
  const productById = new Map(products.map((product) => [String(product.id), product]));
  const cloneCells = (source) => {
    const result = new Map();
    (source.shelves || []).forEach((shelf) => (shelf.cells || []).forEach((cell) => {
      if (Number.isInteger(cell.row) && Number.isInteger(cell.column)) {
        const slots = Array.isArray(cell.slots) ? cell.slots.slice(0,3) :
          (cell.product_id ? [cell.product_id,cell.product_id,cell.product_id] : [null,null,null]);
        while (slots.length < 3) slots.push(null);
        result.set(`${cell.row}:${cell.column}`, slots);
      }
    }));
    return result;
  };
  let cellValues = cloneCells(layout);
  const readGrid = () => {
    grid.querySelectorAll('.cw-layout-cell').forEach((card) => {
      const key = `${card.dataset.row}:${card.dataset.column}`;
      cellValues.set(key, Array.from(card.querySelectorAll('.cw-slot-select')).map((select) => select.value ? Number(select.value) : null));
    });
  };
  const optionFor = (product, selected) => {
    const option = document.createElement('option');
    option.value = product ? String(product.id) : '';
    option.textContent = product ? product.name : 'Empty slot';
    if (product?.image_url) option.dataset.image = product.image_url;
    option.selected = selected === (product ? Number(product.id) : null);
    return option;
  };
  const refreshCell = (card) => {
    const selects = Array.from(card.querySelectorAll('.cw-slot-select'));
    const values = selects.map((select) => select.value);
    const filled = values.filter(Boolean);
    const triple = filled.length === 3 && values.every((value) => value === values[0]);
    card.classList.toggle('is-triple', triple);
    card.setAttribute('data-triple', triple ? 'true' : 'false');
    card.querySelectorAll('.cw-slot-preview').forEach((preview, index) => {
      preview.replaceChildren();
      const product = productById.get(values[index]);
      if (!product) return;
      if (product.image_url) {
        const image = document.createElement('img'); image.src = product.image_url; image.alt = '';
        preview.append(image);
      }
      const label = document.createElement('span'); label.textContent = product.name;
      preview.append(label);
    });
  };
  const readDimensions = () => ({
    rows: Math.max(1, Math.min(20, Number.parseInt(rowsInput.value, 10) || 1)),
    columns: Math.max(1, Math.min(20, Number.parseInt(columnsInput.value, 10) || 1)),
  });
  const renderGrid = () => {
    const {rows,columns} = readDimensions();
    grid.replaceChildren();
    for (let row = 0; row < rows; row += 1) {
      const shelf = document.createElement('section'); shelf.className = 'cw-shelf-row';
      const title = document.createElement('h4'); title.textContent = `Shelf ${row + 1}`; shelf.append(title);
      const cells = document.createElement('div'); cells.className = 'cw-shelf-cells';
      for (let column = 0; column < columns; column += 1) {
        const key = `${row}:${column}`;
        const initial = cellValues.get(key) || [null,null,null];
        const card = document.createElement('article'); card.className = 'cw-layout-cell';
        card.dataset.row = String(row); card.dataset.column = String(column);
        const heading = document.createElement('strong'); heading.className = 'cw-cell-heading'; heading.textContent = `Cell ${row + 1}.${column + 1}`; card.append(heading);
        for (let slot = 0; slot < 3; slot += 1) {
          const wrapper = document.createElement('div'); wrapper.className = 'cw-product-slot';
          const label = document.createElement('label'); label.textContent = `Slot ${slot + 1}`;
          const select = document.createElement('select'); select.className = 'cw-slot-select';
          select.name = `cell_${row}_${column}_slot_${slot}`;
          select.append(optionFor(null, initial[slot] ?? null));
          products.forEach((product) => select.append(optionFor(product, initial[slot] ?? null)));
          select.addEventListener('change', () => { refreshCell(card); readGrid(); });
          label.append(select); wrapper.append(label);
          const preview = document.createElement('div'); preview.className = 'cw-slot-preview'; wrapper.append(preview);
          const drag = document.createElement('span'); drag.className = 'cw-slot-drag'; drag.textContent = '↕ Move';
          drag.draggable = true; drag.tabIndex = 0; drag.setAttribute('aria-label', `Move slot ${slot + 1} from cell ${row + 1}.${column + 1}`);
          drag.addEventListener('dragstart', (event) => {
            if (!select.value) { event.preventDefault(); return; }
            event.dataTransfer.setData('application/x-cw-slot', `${row}:${column}:${slot}`);
            event.dataTransfer.effectAllowed = 'move';
          });
          wrapper.append(drag); card.append(wrapper);
          select.addEventListener('dragover', (event) => event.preventDefault());
          select.addEventListener('drop', (event) => {
            event.preventDefault();
            const productId = event.dataTransfer.getData('application/x-cw-product');
            if (productId) { event.stopPropagation(); select.value = productId; select.dispatchEvent(new Event('change',{bubbles:true})); return; }
            const origin = event.dataTransfer.getData('application/x-cw-slot');
            if (!origin) return;
            event.stopPropagation();
            const [sourceRow,sourceColumn,sourceSlot] = origin.split(':').map(Number);
            const source = grid.querySelector(`[name="cell_${sourceRow}_${sourceColumn}_slot_${sourceSlot}"]`);
            if (!source || source === select) return;
            const moving = source.value;
            const displaced = select.value;
            source.value = displaced;
            select.value = moving;
            source.dispatchEvent(new Event('change',{bubbles:true})); select.dispatchEvent(new Event('change',{bubbles:true}));
          });
        }
        const actions = document.createElement('div'); actions.className = 'cw-cell-actions';
        const clear = document.createElement('button'); clear.type = 'button'; clear.className = 'ds-button ds-button--ghost ds-button--small'; clear.textContent = 'Clear cell';
        clear.addEventListener('click', () => { card.querySelectorAll('.cw-slot-select').forEach((select) => { select.value = ''; }); refreshCell(card); readGrid(); });
        const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'ds-button ds-button--ghost ds-button--small'; copy.textContent = 'Copy cell';
        copy.addEventListener('click', () => { copiedCell = Array.from(card.querySelectorAll('.cw-slot-select')).map((select) => select.value ? Number(select.value) : null); if (copyStatus) copyStatus.textContent = `Copied cell ${row + 1}.${column + 1}. Choose Paste on another cell.`; grid.querySelectorAll('.cw-paste-cell').forEach((button) => { button.disabled = false; }); });
        const paste = document.createElement('button'); paste.type = 'button'; paste.className = 'ds-button ds-button--ghost ds-button--small cw-paste-cell'; paste.textContent = 'Paste'; paste.disabled = !copiedCell;
        paste.addEventListener('click', () => { if (!copiedCell) return; card.querySelectorAll('.cw-slot-select').forEach((select,index) => { select.value = copiedCell[index] ? String(copiedCell[index]) : ''; }); refreshCell(card); readGrid(); });
        actions.append(clear,copy,paste); card.append(actions);
        card.addEventListener('dragover', (event) => event.preventDefault());
        card.addEventListener('drop', (event) => {
          const productId = event.dataTransfer.getData('application/x-cw-product');
          if (!productId) return;
          if (event.target.closest('.cw-product-slot')) return;
          event.preventDefault();
          const emptySlot = Array.from(card.querySelectorAll('.cw-slot-select')).find((select) => !select.value);
          if (emptySlot) { emptySlot.value = productId; emptySlot.dispatchEvent(new Event('change',{bubbles:true})); }
        });
        refreshCell(card); cells.append(card);
      }
      shelf.append(cells); grid.append(shelf);
    }
    readGrid();
  };
  rowsInput.addEventListener('change', () => { readGrid(); renderGrid(); });
  columnsInput.addEventListener('change', () => { readGrid(); renderGrid(); });
  renderGrid();
  document.getElementById('clear-layout').addEventListener('click', () => {
    cellValues.clear(); grid.querySelectorAll('.cw-slot-select').forEach((select) => { select.value = ''; });
    grid.querySelectorAll('.cw-layout-cell').forEach(refreshCell);
    readGrid();
  });
  document.querySelectorAll('.cw-product-catalog-card').forEach((card) => {
    card.draggable = true;
    card.addEventListener('dragstart', (event) => {
      event.dataTransfer.setData('application/x-cw-product', card.dataset.productId);
      event.dataTransfer.effectAllowed = 'copy';
    });
  });
  const filterCatalog = () => {
    const q = (document.getElementById('product-search').value || '').trim().toLowerCase();
    const category = document.getElementById('product-category').value;
    const rarity = document.getElementById('product-rarity').value;
    document.querySelectorAll('.cw-product-catalog-card').forEach((card) => {
      card.hidden = !(card.dataset.name.includes(q) && (!category || card.dataset.category === category) && (!rarity || card.dataset.rarity === rarity));
    });
  };
  ['product-search','product-category','product-rarity'].forEach((id) => {
    const input = document.getElementById(id); input.addEventListener('input',filterCatalog); input.addEventListener('change',filterCatalog);
  });

  const customer = document.getElementById('level-customer');
  const customerPreview = document.getElementById('selected-customer-preview');
  const updateCustomer = () => {
    customerPreview.replaceChildren();
    const option = customer.selectedOptions[0];
    if (!option?.value) { customerPreview.textContent = 'Select a customer to see artwork and waiting time.'; return; }
    if (option.dataset.image) { const image = document.createElement('img'); image.src = option.dataset.image; image.alt = ''; customerPreview.append(image); }
    const info = document.createElement('span'); info.textContent = `${option.textContent.trim()} · default wait ${option.dataset.wait} seconds`; customerPreview.append(info);
  };
  customer.addEventListener('change',updateCustomer); updateCustomer();

  const orderLines = document.getElementById('order-lines');
  const orderCount = document.getElementById('order-items-count');
  const orderTemplate = document.getElementById('order-line-template');
  const updateOrderLines = () => {
    const lines = Array.from(orderLines.querySelectorAll('.cw-order-line'));
    orderCount.value = String(lines.length);
    lines.forEach((line,index) => {
      line.querySelector('select').name = `order_product_${index}`;
      line.querySelector('input').name = `order_quantity_${index}`;
    });
  };
  const previewOrderProduct = (select) => {
    const target = select.closest('.cw-order-line').querySelector('.cw-order-product-preview'); target.replaceChildren();
    const option = select.selectedOptions[0];
    if (option?.dataset.image) { const image = document.createElement('img'); image.src = option.dataset.image; image.alt = ''; target.append(image); }
    if (option?.value) { const label = document.createElement('span'); label.textContent = option.textContent; target.append(label); }
  };
  orderLines.addEventListener('change',(event) => { if (event.target.matches('select')) previewOrderProduct(event.target); });
  orderLines.addEventListener('click',(event) => { if (event.target.closest('.cw-remove-order-line')) { event.target.closest('.cw-order-line').remove(); updateOrderLines(); } });
  orderLines.querySelectorAll('select').forEach(previewOrderProduct);
  document.getElementById('add-order-line').addEventListener('click',() => { orderLines.append(orderTemplate.content.cloneNode(true)); updateOrderLines(); });
  updateOrderLines();

  let dirty = false;
  form.addEventListener('input',() => { dirty = true; });
  form.addEventListener('change',() => { dirty = true; });
  form.addEventListener('submit',(event) => { if (event.submitter?.value === 'archive' && !window.confirm('Archive this level?')) event.preventDefault(); else dirty = false; });
  window.addEventListener('beforeunload',(event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
})();
