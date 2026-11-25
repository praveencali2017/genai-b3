"use strict";

// Annapoorna Kitchen web UI. Plain JavaScript over the same-origin REST API.
//
// Security notes:
// - Credentials live only in this page's memory (never localStorage); a reload
//   signs you out.
// - All server and user text is rendered with textContent, never innerHTML.

// --------------------------------------------------------------------- state

const state = {
  credentials: null, // { username, password }
  me: null, // { id, username, role }
  menu: [], // items from the last search
  cart: new Map(), // itemId -> { item, quantity }
  // Reused when placing the same cart again after a network failure, so a
  // retry can't create a second order. Reset whenever the cart changes.
  cartIdempotencyKey: null,
};

// Admin status changes the server allows (it re-checks them).
const NEXT_STATUSES = {
  CONFIRMED: ["PREPARING", "CANCELLED"],
  PREPARING: ["OUT_FOR_DELIVERY"],
  OUT_FOR_DELIVERY: ["DELIVERED"],
  DELIVERED: [],
  CANCELLED: [],
};

const $ = (selector) => document.querySelector(selector);

// ---------------------------------------------------------------- API client

class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || `Request failed (HTTP ${status})`);
    this.status = status;
    this.code = body?.code;
    this.requestId = body?.request_id;
    this.details = body?.details;
  }
}

async function api(path, { method = "GET", body, headers = {}, auth = true } = {}) {
  const init = { method, headers: { Accept: "application/json", ...headers } };
  if (auth && state.credentials) {
    const { username, password } = state.credentials;
    init.headers.Authorization = "Basic " + btoa(unescape(encodeURIComponent(`${username}:${password}`)));
  }
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, { message: "Can't reach the server. Check your connection and try again." });
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}

function describeError(error) {
  if (!(error instanceof ApiError)) return String(error);
  let text = error.message;
  if (error.details?.length) {
    text += " " + error.details.map((d) => `${d.location}: ${d.message}`).join("; ");
  }
  if (error.requestId) text += ` (request ${error.requestId})`;
  return text;
}

// ------------------------------------------------------------------- helpers

const money = (value) => "₹" + Number(value).toFixed(2);

function el(tag, { text, className, attrs } = {}, children = []) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
  for (const child of children) node.append(child);
  return node;
}

let noticeTimer;
function notify(message, kind = "info") {
  const notice = $("#notice");
  notice.textContent = message;
  notice.className = `notice ${kind}`;
  notice.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => (notice.hidden = true), kind === "error" ? 12000 : 5000);
}

function newIdempotencyKey() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Disable `button` while `task` runs, so a double click can't send twice. */
async function withBusy(button, task) {
  button.disabled = true;
  try {
    return await task();
  } finally {
    button.disabled = false;
  }
}

// ------------------------------------------------------------------- session

async function signIn(username, password) {
  state.credentials = { username, password };
  try {
    state.me = await api("/api/auth/me");
  } catch (error) {
    state.credentials = null;
    throw error;
  }
  renderSession();
  notify(`Welcome, ${state.me.username}.`, "ok");
  await refreshMenu();
}

function signOut() {
  state.credentials = null;
  state.me = null;
  state.cart.clear();
  state.cartIdempotencyKey = null;
  renderSession();
  renderCart();
  showTab("menu");
  renderMenu();
}

function renderSession() {
  const signedIn = Boolean(state.me);
  $("#login-form").hidden = signedIn;
  $("#session").hidden = !signedIn;
  if (signedIn) {
    $("#session-user").textContent = state.me.username;
    $("#session-role").textContent = state.me.role;
  }
  for (const tab of document.querySelectorAll(".tab[data-requires]")) {
    const needs = tab.dataset.requires;
    tab.hidden = needs === "admin" ? state.me?.role !== "ADMIN" : false;
    tab.disabled = needs === "user" && !signedIn;
  }
  document.dispatchEvent(new CustomEvent("session-changed"));
}

// ---------------------------------------------------------------------- tabs

function showTab(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
  }
  for (const panel of document.querySelectorAll("main .panel")) {
    panel.hidden = panel.id !== `tab-${name}`;
  }
  if (name === "orders") refreshOrders();
  if (name === "admin") refreshAdmin();
}

// ---------------------------------------------------------------------- menu

function currentFilters() {
  const form = new FormData($("#filters"));
  const params = new URLSearchParams();
  const keyword = (form.get("keyword") || "").trim();
  if (keyword) params.set("keyword", keyword);
  if (form.get("category")) params.set("category", form.get("category"));
  if (form.get("max_price")) params.set("max_price", form.get("max_price"));
  if (form.get("vegetarian")) params.set("vegetarian", "true");
  return params;
}

async function refreshMenu() {
  const status = $("#menu-status");
  status.textContent = "Loading the menu…";
  try {
    state.menu = await api(`/api/menu_items?${currentFilters()}`, { auth: false });
    status.textContent = state.menu.length ? "" : "No dishes match these filters.";
  } catch (error) {
    state.menu = [];
    status.textContent = "Couldn't load the menu: " + describeError(error);
  }
  renderMenu();
}

async function loadCategories() {
  try {
    const items = await api("/api/menu_items", { auth: false });
    const select = $("#filters select[name=category]");
    for (const category of [...new Set(items.map((i) => i.category))].sort()) {
      select.append(el("option", { text: category, attrs: { value: category } }));
    }
  } catch {
    // The filter still works without the category list.
  }
}

function renderMenu() {
  const grid = $("#menu-grid");
  const template = $("#menu-card-template");
  grid.replaceChildren();
  for (const item of state.menu) {
    const card = template.content.firstElementChild.cloneNode(true);
    card.querySelector(".card-title").textContent = item.title;
    const veg = card.querySelector(".veg-badge");
    veg.textContent = item.vegetarian ? "Veg" : "Non-veg";
    veg.classList.add(item.vegetarian ? "veg" : "nonveg");
    card.querySelector(".card-category").textContent = item.category;
    card.querySelector(".card-description").textContent = item.description || "";
    card.querySelector(".card-price").textContent = money(item.price);
    const stock = card.querySelector(".card-stock");
    stock.textContent = item.capacity === 0 ? "Sold out" : `${item.capacity} left`;
    stock.classList.toggle("low", item.capacity <= 3);

    const qty = card.querySelector(".card-qty");
    qty.max = String(Math.min(10, Math.max(item.capacity, 1)));
    qty.id = `qty-${item.id}`;
    qty.setAttribute("aria-label", `Quantity of ${item.title}`);
    const add = card.querySelector(".card-add");
    add.disabled = !state.me || item.capacity === 0;
    add.title = state.me ? "" : "Sign in to order";
    add.addEventListener("click", () => addToCart(item, Number(qty.value)));
    grid.append(card);
  }
}

// ---------------------------------------------------------------------- cart

function addToCart(item, quantity) {
  if (!Number.isInteger(quantity) || quantity < 1) {
    notify("Choose a quantity of at least 1.", "error");
    return;
  }
  const existing = state.cart.get(item.id);
  const total = Math.min((existing?.quantity || 0) + quantity, 10);
  state.cart.set(item.id, { item, quantity: total });
  state.cartIdempotencyKey = null;
  renderCart();
  notify(`${item.title} × ${total} in your cart.`, "ok");
}

function removeFromCart(itemId) {
  state.cart.delete(itemId);
  state.cartIdempotencyKey = null;
  renderCart();
}

function renderCart() {
  const list = $("#cart-lines");
  list.replaceChildren();
  let total = 0;
  for (const { item, quantity } of state.cart.values()) {
    total += Number(item.price) * quantity;
    const remove = el("button", {
      text: "Remove",
      className: "secondary",
      attrs: { type: "button", "aria-label": `Remove ${item.title}` },
    });
    remove.addEventListener("click", () => removeFromCart(item.id));
    list.append(
      el("li", {}, [
        el("span", { text: `${item.title} × ${quantity}` }),
        el("span", { text: money(Number(item.price) * quantity) }),
        remove,
      ]),
    );
  }
  $("#cart-empty").hidden = state.cart.size > 0;
  $("#cart-total").textContent = money(total);
  $("#place-order").disabled = state.cart.size === 0 || !state.me;
}

async function placeOrder() {
  if (!state.cartIdempotencyKey) state.cartIdempotencyKey = newIdempotencyKey();
  const lines = [...state.cart.values()].map(({ item, quantity }) => ({ item_id: item.id, quantity }));
  try {
    const order = await api("/api/orders", {
      method: "POST",
      body: { lines },
      headers: { "Idempotency-Key": state.cartIdempotencyKey },
    });
    state.cart.clear();
    state.cartIdempotencyKey = null;
    renderCart();
    notify(`Order #${order.id} confirmed. Total ${money(order.total)}.`, "ok");
    await refreshMenu();
  } catch (error) {
    // Keep the key on a network failure (status 0) so a retry is safe; any
    // answer from the server means this attempt is settled.
    if (!(error instanceof ApiError) || error.status !== 0) state.cartIdempotencyKey = null;
    notify("Order not placed: " + describeError(error), "error");
    if (error instanceof ApiError && error.status === 409) await refreshMenu();
  }
}

// -------------------------------------------------------------------- orders

function orderCard(order, actions) {
  const lines = el(
    "ul",
    { className: "order-lines" },
    order.lines.map((line) =>
      el("li", { text: `${line.title} × ${line.quantity} @ ${money(line.unit_price)} = ${money(line.line_total)}` }),
    ),
  );
  const placed = new Date(order.created_at + "Z").toLocaleString();
  return el("li", { className: "order" }, [
    el("div", { className: "order-head" }, [
      el("strong", { text: `Order #${order.id}` }),
      el("span", { text: order.status.replaceAll("_", " "), className: `status status-${order.status}` }),
      el("span", { text: money(order.total) }),
      el("span", { text: placed, className: "muted small" }),
    ]),
    lines,
    el("div", { className: "order-actions" }, actions),
  ]);
}

async function refreshOrders() {
  const status = $("#orders-status");
  const list = $("#orders-list");
  status.textContent = "Loading your orders…";
  try {
    const orders = await api("/api/orders");
    status.textContent = orders.length ? "" : "You have no orders yet.";
    list.replaceChildren(
      ...orders.map((order) => {
        const actions = [];
        if (order.status === "CONFIRMED") {
          const cancel = el("button", { text: "Cancel order", className: "danger", attrs: { type: "button" } });
          cancel.addEventListener("click", () => withBusy(cancel, () => cancelOrder(order.id)));
          actions.push(cancel);
        }
        return orderCard(order, actions);
      }),
    );
  } catch (error) {
    status.textContent = "Couldn't load your orders: " + describeError(error);
  }
}

async function cancelOrder(orderId) {
  try {
    await api(`/api/orders/${orderId}/cancel`, { method: "POST" });
    notify(`Order #${orderId} cancelled.`, "ok");
  } catch (error) {
    notify("Couldn't cancel: " + describeError(error), "error");
  }
  await refreshOrders();
}

// --------------------------------------------------------------------- admin

async function refreshAdmin() {
  await Promise.all([refreshAdminItems(), refreshAdminOrders()]);
}

async function refreshAdminItems() {
  const body = $("#admin-items");
  try {
    const items = await api("/api/menu_items?include_inactive=true");
    body.replaceChildren(...items.map(adminItemRow));
  } catch (error) {
    notify("Couldn't load the menu editor: " + describeError(error), "error");
  }
}

function adminItemRow(item) {
  const input = (name, value, attrs = {}) =>
    el("input", { attrs: { name, value: String(value), "aria-label": `${name} of ${item.title}`, ...attrs } });
  const title = input("title", item.title, { type: "text", minlength: "3", maxlength: "120" });
  const category = input("category", item.category, { type: "text", minlength: "2", maxlength: "50" });
  const price = input("price", item.price, { type: "number", min: "0.01", step: "0.01" });
  const capacity = input("capacity", item.capacity, { type: "number", min: "0", step: "1" });
  const vegetarian = el("input", { attrs: { type: "checkbox", "aria-label": `Vegetarian: ${item.title}` } });
  vegetarian.checked = item.vegetarian;
  const active = el("input", { attrs: { type: "checkbox", "aria-label": `Active: ${item.title}` } });
  active.checked = item.active;

  const save = el("button", { text: "Save", attrs: { type: "button" } });
  save.addEventListener("click", () =>
    withBusy(save, async () => {
      try {
        await api(`/api/menu_items/${item.id}`, {
          method: "PUT",
          body: {
            title: title.value,
            category: category.value,
            price: price.value,
            capacity: Number(capacity.value),
            vegetarian: vegetarian.checked,
            active: active.checked,
          },
        });
        notify(`Saved ${title.value}.`, "ok");
        await Promise.all([refreshAdminItems(), refreshMenu()]);
      } catch (error) {
        notify("Couldn't save: " + describeError(error), "error");
      }
    }),
  );

  const cell = (child) => el("td", {}, [child]);
  return el("tr", { className: item.active ? "" : "inactive" }, [
    cell(title), cell(category), cell(price), cell(capacity), cell(vegetarian), cell(active), cell(save),
  ]);
}

async function createItem(form) {
  const data = new FormData(form);
  try {
    const item = await api("/api/menu_items", {
      method: "POST",
      body: {
        title: data.get("title"),
        category: data.get("category"),
        price: data.get("price"),
        capacity: Number(data.get("capacity")),
        description: data.get("description") || null,
        vegetarian: data.get("vegetarian") === "on",
      },
    });
    form.reset();
    notify(`Added ${item.title} (item ${item.id}).`, "ok");
    await Promise.all([refreshAdminItems(), refreshMenu()]);
  } catch (error) {
    notify("Couldn't add the dish: " + describeError(error), "error");
  }
}

async function refreshAdminOrders() {
  const list = $("#admin-orders");
  const filter = $("#admin-order-filter").value;
  try {
    const orders = await api(`/api/admin/orders${filter ? `?status=${filter}` : ""}`);
    list.replaceChildren(
      ...orders.map((order) => {
        const next = NEXT_STATUSES[order.status] || [];
        const actions = [el("span", { text: `Customer #${order.user_id}`, className: "muted small" })];
        if (next.length) {
          const select = el(
            "select",
            { attrs: { "aria-label": `New status for order ${order.id}` } },
            next.map((s) => el("option", { text: s.replaceAll("_", " "), attrs: { value: s } })),
          );
          const update = el("button", { text: "Update status", attrs: { type: "button" } });
          update.addEventListener("click", () =>
            withBusy(update, () => changeStatus(order.id, select.value)),
          );
          actions.push(select, update);
        }
        return orderCard(order, actions);
      }),
    );
  } catch (error) {
    notify("Couldn't load orders: " + describeError(error), "error");
  }
}

async function changeStatus(orderId, status) {
  try {
    await api(`/api/admin/orders/${orderId}/status`, { method: "PATCH", body: { status } });
    notify(`Order #${orderId} is now ${status.replaceAll("_", " ")}.`, "ok");
  } catch (error) {
    notify("Couldn't update the status: " + describeError(error), "error");
  }
  await refreshAdminOrders();
}

// ---------------------------------------------------------------- wiring

function wireEvents() {
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    await withBusy(button, async () => {
      try {
        await signIn(form.username.value.trim(), form.password.value);
        form.reset();
      } catch (error) {
        notify("Sign-in failed: " + describeError(error), "error");
      }
    });
  });
  $("#logout").addEventListener("click", signOut);

  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => showTab(tab.dataset.tab));
  }

  $("#filters").addEventListener("submit", (event) => {
    event.preventDefault();
    refreshMenu();
  });
  $("#filters").addEventListener("reset", () => setTimeout(refreshMenu));

  // withBusy re-enables the button; renderCart then disables it again if the
  // order emptied the cart.
  $("#place-order").addEventListener("click", (event) =>
    withBusy(event.currentTarget, placeOrder).then(renderCart),
  );
  $("#item-create").addEventListener("submit", (event) => {
    event.preventDefault();
    createItem(event.currentTarget);
  });
  $("#admin-order-filter").addEventListener("change", refreshAdminOrders);
}

wireEvents();
renderSession();
renderCart();
loadCategories();
refreshMenu();
