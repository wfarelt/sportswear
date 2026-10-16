(() => {
  const form = document.getElementById("saleForm");
  if (!form) return;
  const search = document.getElementById("productSearch");
  const resultBox = document.getElementById("searchResults");
  const branchSelect = document.getElementById("id_branch");
  const cartRows = document.getElementById("cartRows");
  const cartField = document.getElementById("id_cart_json");
  const paymentsField = document.getElementById("id_payments_json");
  const paymentRows = document.getElementById("paymentRows");
  const discount = document.getElementById("id_discount_amount");
  const submit = document.getElementById("confirmSale");
  const money = (value) => new Intl.NumberFormat("es-BO", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
  const cart = new Map();
  let debounce;
  let primaryAutofill = true;

  function paymentInputs() {
    return [...paymentRows.querySelectorAll(".payment-row")];
  }

  function renderCart() {
    cartRows.replaceChildren();
    const items = [...cart.values()].sort((left, right) => left.id - right.id);
    let subtotal = 0;
    let tax = 0;
    let units = 0;
    const grossLines = items.map((item) => Math.round(item.price * item.quantity * 100) / 100);
    for (const item of items) {
      const gross = item.price * item.quantity;
      subtotal += Math.round(gross * 100) / 100;
      units += item.quantity;
    }
    const discountAmount = Math.max(0, Number(discount.value || 0));
    const actualDiscount = Math.min(discountAmount, subtotal);
    const discountCents = Math.round(actualDiscount * 100);
    const allocated = [];
    const remainders = [];
    let allocatedCents = 0;
    for (const [index, gross] of grossLines.entries()) {
      const exactCents = subtotal ? discountCents * gross / subtotal : 0;
      const cents = Math.floor(exactCents);
      allocated.push(cents);
      remainders.push({ index, fraction: exactCents - cents });
      allocatedCents += cents;
    }
    remainders.sort((left, right) => right.fraction - left.fraction || right.index - left.index);
    for (const remainder of remainders.slice(0, discountCents - allocatedCents)) {
      allocated[remainder.index] += 1;
    }
    for (const [index, item] of items.entries()) {
      const gross = grossLines[index];
      const share = allocated[index] / 100;
      const perUnit = (gross - share) / item.quantity;
      tax += Math.round((perUnit * item.taxRate / 100) * 100) / 100 * item.quantity;
      const row = document.createElement("tr");
      const description = document.createElement("td");
      const name = document.createElement("strong");
      name.textContent = item.name;
      const details = document.createElement("small");
      details.className = "table-subtext";
      details.textContent = `${item.sku} · ${item.size} · ${item.color} · Stock ${item.stock}`;
      description.append(name, details);

      const priceCell = document.createElement("td");
      priceCell.textContent = `Bs ${money(item.price)}`;
      const quantityCell = document.createElement("td");
      const quantityInput = document.createElement("input");
      quantityInput.className = "form-control cart-quantity";
      quantityInput.type = "number";
      quantityInput.min = "1";
      quantityInput.max = String(item.stock);
      quantityInput.value = String(item.quantity);
      quantityInput.setAttribute("aria-label", `Cantidad de ${item.name}`);
      quantityInput.addEventListener("change", () => {
        const quantity = Number.parseInt(quantityInput.value, 10);
        if (!Number.isInteger(quantity) || quantity < 1 || quantity > item.stock) {
          quantityInput.value = String(item.quantity);
          return;
        }
        item.quantity = quantity;
        renderCart();
      });
      quantityCell.append(quantityInput);
      const lineTotal = document.createElement("td");
      lineTotal.textContent = `Bs ${money(gross)}`;
      const actionCell = document.createElement("td");
      const remove = document.createElement("button");
      remove.className = "table-action remove-cart-item";
      remove.type = "button";
      remove.setAttribute("aria-label", `Quitar ${item.name}`);
      remove.innerHTML = '<i class="bi bi-x-lg"></i>';
      remove.addEventListener("click", () => { cart.delete(item.id); renderCart(); });
      actionCell.append(remove);
      row.append(description, priceCell, quantityCell, lineTotal, actionCell);
      cartRows.append(row);
    }
    if (!cart.size) {
      const row = document.createElement("tr");
      row.innerHTML = '<td colspan="5"><div class="table-empty compact-empty"><i class="bi bi-bag"></i><strong>El carrito está vacío</strong><span>Busca un producto y selecciónalo para empezar.</span></div></td>';
      cartRows.append(row);
    }
    const total = Math.max(0, subtotal - actualDiscount + tax);
    document.getElementById("subtotalDisplay").textContent = `Bs ${money(subtotal)}`;
    document.getElementById("totalDisplay").textContent = `Bs ${money(total)}`;
    document.getElementById("cartCount").textContent = `${units} unidad${units === 1 ? "" : "es"} · ${cart.size} variante${cart.size === 1 ? "" : "s"}`;
    cartField.value = JSON.stringify([...cart.values()].map(({ id, quantity }) => ({ variant_id: id, quantity })));
    recalculatePayments(total);
  }

  function recalculatePayments(total) {
    const rows = paymentInputs();
    if (primaryAutofill && rows.length) {
      const otherTotal = rows.slice(1).reduce((sum, row) => sum + Number(row.querySelector(".payment-amount").value || 0), 0);
      rows[0].querySelector(".payment-amount").value = Math.max(0, total - otherTotal).toFixed(2);
    }
    const payments = [];
    let paid = 0;
    for (const row of rows) {
      const method = row.querySelector(".payment-method");
      const amount = Number(row.querySelector(".payment-amount").value || 0);
      if (method?.value && amount > 0) payments.push({ method_id: method.value, amount: amount.toFixed(2) });
      paid += amount;
    }
    paymentsField.value = JSON.stringify(payments);
    document.getElementById("paidDisplay").textContent = `Bs ${money(paid)}`;
    document.getElementById("balanceDisplay").textContent = `Bs ${money(Math.max(0, total - paid))}`;
    const valid = total > 0 && Math.abs(paid - total) < 0.005 && cart.size > 0 && branchSelect.value && payments.length > 0;
    submit.disabled = !valid;
  }

  function addResult(item) {
    const current = cart.get(item.id);
    if (current) {
      if (current.quantity >= current.stock) return;
      current.quantity += 1;
    } else {
      cart.set(item.id, { ...item, price: Number(item.price), taxRate: Number(item.tax_rate || 0), quantity: 1 });
    }
    search.value = "";
    resultBox.replaceChildren();
    search.focus();
    renderCart();
  }

  function showResults(items, error) {
    resultBox.replaceChildren();
    if (error) {
      const message = document.createElement("div");
      message.className = "search-empty";
      message.textContent = error;
      resultBox.append(message);
      return;
    }
    if (!items.length) return;
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "search-result";
      const main = document.createElement("span");
      main.className = "search-result-main";
      const name = document.createElement("strong");
      name.textContent = item.name;
      const details = document.createElement("small");
      details.textContent = `${item.sku} · ${item.size} · ${item.color}`;
      main.append(name, details);
      const info = document.createElement("span");
      info.className = "search-result-info";
      info.innerHTML = `<strong>Bs ${money(Number(item.price))}</strong><small>Stock ${item.stock}</small>`;
      button.append(main, info);
      button.addEventListener("click", () => addResult(item));
      resultBox.append(button);
    }
  }

  async function searchProducts() {
    const query = search.value.trim();
    if (!query || !branchSelect.value) { resultBox.replaceChildren(); return; }
    try {
      const url = new URL(form.dataset.searchUrl, window.location.origin);
      url.searchParams.set("q", query);
      url.searchParams.set("branch", branchSelect.value);
      const response = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
      const data = await response.json();
      showResults(data.results || [], data.error || (response.ok ? "" : "No se pudo buscar productos."));
      if (search.value.trim() && data.results?.length === 1 && data.results[0].barcode === query) addResult(data.results[0]);
    } catch (_error) {
      showResults([], "No se pudo conectar con la búsqueda de productos.");
    }
  }

  search?.addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(searchProducts, 220); });
  search?.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); const first = resultBox.querySelector(".search-result"); if (first) first.click(); else searchProducts(); } });
  branchSelect?.addEventListener("change", () => { cart.clear(); renderCart(); resultBox.replaceChildren(); search.value = ""; });
  discount?.addEventListener("input", renderCart);
  document.getElementById("clearCart")?.addEventListener("click", () => { cart.clear(); renderCart(); });
  document.getElementById("addPayment")?.addEventListener("click", () => {
    const first = paymentRows.querySelector(".payment-method");
    if (!first) return;
    const row = document.createElement("div");
    row.className = "payment-row";
    const method = first.cloneNode(true);
    method.value = [...first.options].find((option) => !paymentInputs().some((existing) => existing.querySelector(".payment-method").value === option.value))?.value || first.value;
    method.className = "form-select payment-method";
    method.addEventListener("change", () => recalculatePayments(getTotal()));
    const amount = document.createElement("input");
    amount.className = "form-control payment-amount";
    amount.type = "number"; amount.min = "0"; amount.step = "0.01"; amount.placeholder = "Importe"; amount.setAttribute("aria-label", "Importe de pago");
    amount.addEventListener("input", () => recalculatePayments(getTotal()));
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "table-action"; remove.setAttribute("aria-label", "Quitar medio de pago"); remove.innerHTML = '<i class="bi bi-x-lg"></i>';
    remove.addEventListener("click", () => { row.remove(); recalculatePayments(getTotal()); });
    row.append(method, amount, remove);
    paymentRows.append(row);
    recalculatePayments(getTotal());
  });
  paymentRows.addEventListener("input", (event) => {
    if (event.target.classList.contains("payment-amount")) {
      if (event.target === paymentInputs()[0]?.querySelector(".payment-amount")) primaryAutofill = false;
      recalculatePayments(getTotal());
    }
  });
  paymentRows.addEventListener("change", (event) => { if (event.target.classList.contains("payment-method")) recalculatePayments(getTotal()); });

  function getTotal() {
    const text = document.getElementById("totalDisplay").textContent.replace("Bs", "").trim();
    return Number(text.replace(/\./g, "").replace(",", ".")) || 0;
  }

  form.addEventListener("submit", (event) => {
    if (!cart.size || submit.disabled) { event.preventDefault(); return; }
    submit.disabled = true;
    submit.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Procesando venta…';
  });
  renderCart();
})();
