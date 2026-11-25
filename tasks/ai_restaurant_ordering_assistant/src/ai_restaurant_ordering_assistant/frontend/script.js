"use strict";

// Praveen's Kitchen web UI: plain JavaScript over the same-origin REST API.
//
// Security notes:
// - The Basic credentials live only in this page's memory (never in storage,
//   cookies or URLs); reloading the page signs you out.
// - Everything from the server, including chat replies and document excerpts,
//   is rendered with textContent, never as HTML.

const $ = (selector) => document.querySelector(selector);

const STATUS_FLOW = ["CONFIRMED", "PREPARING", "OUT_FOR_DELIVERY", "DELIVERED"];
const NEXT_STATUSES = {
  CONFIRMED: ["PREPARING", "CANCELLED"],
  PREPARING: ["OUT_FOR_DELIVERY"],
  OUT_FOR_DELIVERY: ["DELIVERED"],
  DELIVERED: [],
  CANCELLED: [],
};
const MAX_QUANTITY = 10;
const CHAT_SUGGESTIONS = [
  "What vegetarian dishes do you have under ₹150?",
  "Does Paneer Biryani contain nuts?",
  "Can I get a refund after my order is being prepared?",
  "Order 2 Masala Dosa and a Mango Lassi",
];

const state = {
  auth: null, // "Basic …" header value, memory only
  me: null,
  view: "menu",
  categories: [],
  filters: { category: "", keyword: "", vegetarian: false, maxPrice: null },
  menu: [],
  cart: new Map(), // item id -> { item, quantity }
  orderKey: null, // Idempotency-Key reused until the order succeeds
  chat: { sessions: [], currentId: null, messages: [], busy: false, open: false },
};

// ------------------------------------------------------------------ helpers

class ApiError extends Error {
  constructor(status, code, message, requestId) {
    super(message);
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

async function api(path, { method = "GET", body, form, headers = {} } = {}) {
  const init = { method, headers: { Accept: "application/json", ...headers } };
  if (state.auth) init.headers.Authorization = state.auth;
  if (form) {
    init.body = form;
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "NETWORK", "Can't reach the server. Is it running?");
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      response.status,
      data?.code ?? `HTTP_${response.status}`,
      data?.message ?? response.statusText,
      data?.request_id,
    );
  }
  return data;
}

function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  const { className, text, attrs, on } = options;
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  for (const [name, value] of Object.entries(attrs ?? {})) {
    if (value === false || value === null || value === undefined) continue;
    node.setAttribute(name, value === true ? "" : String(value));
  }
  for (const [event, handler] of Object.entries(on ?? {})) node.addEventListener(event, handler);
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

const rupees = (value) => {
  const amount = Number(value);
  return `₹${Number.isInteger(amount) ? amount : amount.toFixed(2)}`;
};
const label = (status) => status.toLowerCase().replaceAll("_", " ");
const statusBadge = (status) =>
  el("span", { className: `status-badge s-${status.toLowerCase()}`, text: label(status) });

function parseServerDate(value) {
  // The API sends UTC timestamps without an offset.
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
}
const formatDate = (value) =>
  parseServerDate(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

function dishEmoji(title, category) {
  const name = title.toLowerCase();
  const table = [
    ["dosa", "🫓"], ["idli", "🍙"], ["vada", "🍩"], ["biryani", "🍛"], ["thali", "🍱"],
    ["bhature", "🫓"], ["chicken", "🍗"], ["naan", "🫓"], ["fish", "🐟"], ["jamun", "🍡"],
    ["kesari", "🍮"], ["lassi", "🥭"], ["coffee", "☕"], ["tea", "🍵"], ["paneer", "🧀"],
  ];
  for (const [word, emoji] of table) if (name.includes(word)) return emoji;
  return { Breakfast: "🥞", "Main course": "🍛", Dessert: "🍨", Beverages: "🥤" }[category] ?? "🍽️";
}
const categoryClass = (category) =>
  ["Breakfast", "Main course", "Dessert", "Beverages"].includes(category)
    ? category.toLowerCase().replace(" ", "-")
    : "other";

function toast(message, kind = "info") {
  const node = el("div", { className: `toast ${kind}`, text: message });
  $("#toasts").append(node);
  setTimeout(() => node.remove(), kind === "error" ? 6500 : 3500);
}

function showError(error, prefix = "") {
  const reference = error.requestId ? ` (ref ${error.requestId.slice(0, 8)})` : "";
  toast(`${prefix}${error.message}${reference}`, "error");
}

// ------------------------------------------------------------------ views & auth

function setView(view) {
  if ((view === "orders" && !state.me) || (view === "admin" && state.me?.role !== "ADMIN")) {
    view = "menu";
  }
  state.view = view;
  for (const name of ["menu", "orders", "admin"]) $(`#view-${name}`).hidden = name !== view;
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.toggleAttribute("aria-current", link.dataset.viewLink === view);
    if (link.dataset.viewLink === view) link.setAttribute("aria-current", "page");
  });
  if (view === "orders") loadOrders();
  if (view === "admin") showAdminTab(currentAdminTab);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderAccount() {
  const signedIn = Boolean(state.me);
  $("#sign-in-button").hidden = signedIn;
  $("#user-chip").hidden = !signedIn;
  document.querySelectorAll("[data-requires]").forEach((node) => {
    node.hidden = node.dataset.requires === "admin" ? state.me?.role !== "ADMIN" : !signedIn;
  });
  if (signedIn) {
    $("#user-name").textContent = state.me.username;
    $("#user-role").textContent = state.me.role.toLowerCase();
    $("#user-avatar").textContent = state.me.username.slice(0, 1);
  }
  renderCart();
  renderChatGate();
}

function openSignIn() {
  $("#sign-in-error").hidden = true;
  $("#sign-in-form").reset();
  $("#sign-in-dialog").showModal();
  $("#sign-in-username").focus();
}

async function signIn(event) {
  event.preventDefault();
  const username = $("#sign-in-username").value.trim();
  const password = $("#sign-in-password").value;
  const submit = $("#sign-in-submit");
  submit.disabled = true;
  state.auth = `Basic ${btoa(String.fromCharCode(...new TextEncoder().encode(`${username}:${password}`)))}`;
  try {
    state.me = await api("/api/auth/me");
    $("#sign-in-dialog").close();
    toast(`Welcome, ${state.me.username}!`, "success");
    renderAccount();
    if (state.chat.open) loadSessions();
  } catch (error) {
    state.auth = null;
    state.me = null;
    $("#sign-in-error").textContent =
      error.status === 401 ? "Wrong username or password." : error.message;
    $("#sign-in-error").hidden = false;
  } finally {
    submit.disabled = false;
    $("#sign-in-password").value = "";
  }
}

function signOut() {
  state.auth = null;
  state.me = null;
  state.chat = { sessions: [], currentId: null, messages: [], busy: false, open: state.chat.open };
  renderAccount();
  renderChat();
  setView("menu");
  toast("Signed out.");
}

function requireSignIn() {
  if (state.me) return true;
  openSignIn();
  return false;
}

// ------------------------------------------------------------------ menu

function menuQuery() {
  const params = new URLSearchParams();
  const { category, keyword, vegetarian, maxPrice } = state.filters;
  if (category) params.set("category", category);
  if (keyword) params.set("keyword", keyword);
  if (vegetarian) params.set("vegetarian", "true");
  if (maxPrice) params.set("max_price", String(maxPrice));
  return params.toString();
}

async function loadCategories() {
  try {
    const items = await api("/api/menu_items");
    state.categories = [...new Set(items.map((item) => item.category))];
  } catch {
    state.categories = [];
  }
  renderCategoryChips();
}

function renderCategoryChips() {
  const chips = ["", ...state.categories].map((category) =>
    el("button", {
      className: "chip",
      text: category || "All",
      attrs: { type: "button", "aria-pressed": String(state.filters.category === category) },
      on: {
        click: () => {
          state.filters.category = category;
          renderCategoryChips();
          loadMenu();
        },
      },
    }),
  );
  $("#category-chips").replaceChildren(...chips);
}

async function loadMenu() {
  const grid = $("#menu-grid");
  if (!state.menu.length) {
    grid.replaceChildren(...Array.from({ length: 6 }, () => el("li", { className: "skeleton" })));
  }
  $("#menu-status").textContent = "";
  try {
    state.menu = await api(`/api/menu_items?${menuQuery()}`);
    renderMenu();
  } catch (error) {
    grid.replaceChildren();
    $("#menu-status").textContent = `Couldn't load the menu: ${error.message}`;
  }
}

function renderMenu() {
  const grid = $("#menu-grid");
  if (!state.menu.length) {
    grid.replaceChildren(
      el("li", { className: "empty-state" }, [
        el("span", { text: "🍽️", attrs: { "aria-hidden": "true" } }),
        el("p", { text: "No dishes match these filters." }),
      ]),
    );
    $("#menu-status").textContent = "";
    return;
  }
  $("#menu-status").textContent = `${state.menu.length} dish${state.menu.length === 1 ? "" : "es"}`;
  grid.replaceChildren(...state.menu.map(dishCard));
}

function dishCard(item) {
  const inCart = state.cart.get(item.id)?.quantity ?? 0;
  const soldOut = item.capacity === 0;
  const stockText = soldOut ? "Sold out" : item.capacity <= 5 ? `Only ${item.capacity} left` : `${item.capacity} left`;
  const stockClass = soldOut ? "stock out" : item.capacity <= 5 ? "stock low" : "stock";

  const action = inCart
    ? el("div", { className: "stepper", attrs: { role: "group", "aria-label": `${item.title} quantity` } }, [
        el("button", { text: "−", attrs: { type: "button", "aria-label": `Remove one ${item.title}` }, on: { click: () => changeCart(item, -1) } }),
        el("span", { text: inCart, attrs: { "aria-live": "polite" } }),
        el("button", {
          text: "+",
          attrs: { type: "button", "aria-label": `Add one ${item.title}`, disabled: inCart >= Math.min(MAX_QUANTITY, item.capacity) },
          on: { click: () => changeCart(item, 1) },
        }),
      ])
    : el("button", {
        className: "button button-primary button-small",
        text: soldOut ? "Sold out" : "Add",
        attrs: { type: "button", disabled: soldOut, "aria-label": `Add ${item.title} to cart` },
        on: { click: () => changeCart(item, 1) },
      });

  return el("li", { className: "dish" }, [
    el("div", { className: `dish-art ${categoryClass(item.category)}`, attrs: { "aria-hidden": "true" } }, [
      el("span", { className: `diet ${item.vegetarian ? "veg" : "nonveg"}` }),
      el("span", { text: dishEmoji(item.title, item.category) }),
      el("span", { className: stockClass, text: stockText }),
    ]),
    el("div", { className: "dish-body" }, [
      el("h3", { className: "dish-title", text: item.title }),
      el("p", { className: "dish-desc", text: item.description ?? item.category }),
      el("p", { className: "sr-only", text: `${item.vegetarian ? "Vegetarian" : "Non-vegetarian"}. ${stockText}.` }),
      el("div", { className: "dish-foot" }, [el("span", { className: "price", text: rupees(item.price) }), action]),
    ]),
  ]);
}

// ------------------------------------------------------------------ cart

function changeCart(item, delta) {
  const line = state.cart.get(item.id) ?? { item, quantity: 0 };
  const quantity = Math.max(0, Math.min(line.quantity + delta, MAX_QUANTITY, item.capacity));
  if (quantity === 0) state.cart.delete(item.id);
  else state.cart.set(item.id, { item, quantity });
  state.orderKey = null; // a different cart is a different order
  renderCart();
  renderMenu();
}

function renderCart() {
  const lines = [...state.cart.values()];
  const count = lines.reduce((sum, line) => sum + line.quantity, 0);
  const total = lines.reduce((sum, line) => sum + Number(line.item.price) * line.quantity, 0);
  $("#cart-count").hidden = count === 0;
  $("#cart-count").textContent = String(count);
  $("#cart-empty").hidden = count > 0;
  $("#cart-total").textContent = rupees(total);
  const button = $("#place-order");
  button.disabled = count === 0;
  button.textContent = state.me ? "Place order" : "Sign in to order";
  $("#cart-lines").replaceChildren(
    ...lines.map(({ item, quantity }) =>
      el("li", { className: "cart-line" }, [
        el("span", { className: "cart-line-emoji", text: dishEmoji(item.title, item.category), attrs: { "aria-hidden": "true" } }),
        el("div", {}, [
          el("div", { className: "cart-line-title", text: item.title }),
          el("div", { className: "cart-line-price", text: `${rupees(item.price)} × ${quantity} = ${rupees(Number(item.price) * quantity)}` }),
        ]),
        el("div", { className: "stepper" }, [
          el("button", { text: "−", attrs: { type: "button", "aria-label": `Remove one ${item.title}` }, on: { click: () => changeCart(item, -1) } }),
          el("span", { text: quantity }),
          el("button", {
            text: "+",
            attrs: { type: "button", "aria-label": `Add one ${item.title}`, disabled: quantity >= Math.min(MAX_QUANTITY, item.capacity) },
            on: { click: () => changeCart(item, 1) },
          }),
        ]),
      ]),
    ),
  );
}

async function placeOrder() {
  if (!requireSignIn()) return;
  const lines = [...state.cart.values()].map(({ item, quantity }) => ({ item_id: item.id, quantity }));
  state.orderKey ??= crypto.randomUUID();
  const button = $("#place-order");
  button.disabled = true;
  button.textContent = "Placing order…";
  try {
    const order = await api("/api/orders", {
      method: "POST",
      body: { lines },
      headers: { "Idempotency-Key": state.orderKey },
    });
    state.cart.clear();
    state.orderKey = null;
    $("#cart-panel").classList.remove("open");
    toast(`Order #${order.id} placed. Total ${rupees(order.total)}.`, "success");
    await loadMenu();
  } catch (error) {
    showError(error, "Order not placed: ");
    if (error.status === 409) {
      state.orderKey = null;
      await loadMenu();
    }
  } finally {
    renderCart();
  }
}

// ------------------------------------------------------------------ orders

function orderCard(order, { admin = false } = {}) {
  const reached = STATUS_FLOW.indexOf(order.status);
  const tracker =
    order.status === "CANCELLED"
      ? null
      : el(
          "div",
          { className: "tracker", attrs: { "aria-label": `Progress: ${label(order.status)}` } },
          STATUS_FLOW.map((step, index) =>
            el("span", { className: `tracker-step${index <= reached ? " done" : ""}`, text: label(step) }),
          ),
        );

  const actions = el("div", { className: "order-actions" });
  if (admin) {
    for (const next of NEXT_STATUSES[order.status]) {
      actions.append(
        el("button", {
          className: `button button-small ${next === "CANCELLED" ? "button-danger" : "button-ghost"}`,
          text: `Mark ${label(next)}`,
          attrs: { type: "button" },
          on: { click: (event) => updateOrderStatus(order.id, next, event.currentTarget) },
        }),
      );
    }
  } else if (order.status === "CONFIRMED") {
    actions.append(
      el("button", {
        className: "button button-danger button-small",
        text: "Cancel order",
        attrs: { type: "button" },
        on: { click: (event) => cancelOrder(order.id, event.currentTarget) },
      }),
    );
  }

  return el("li", { className: "order-card" }, [
    el("div", { className: "order-top" }, [
      el("span", { className: "order-id", text: `Order #${order.id}` }),
      statusBadge(order.status),
      el("span", { className: "order-date", text: `${formatDate(order.created_at)}${admin ? ` · customer #${order.user_id}` : ""}` }),
      el("span", { className: "order-total", text: rupees(order.total) }),
    ]),
    el(
      "div",
      { className: "order-lines" },
      order.lines.map((line) => el("span", { className: "line-pill", text: `${line.quantity} × ${line.title}` })),
    ),
    tracker,
    actions.childElementCount ? actions : null,
  ]);
}

async function loadOrders() {
  if (!state.me) return;
  $("#orders-status").textContent = "Loading…";
  try {
    const orders = await api("/api/orders");
    $("#orders-status").textContent = "";
    $("#orders-list").replaceChildren(
      ...(orders.length
        ? orders.map((order) => orderCard(order))
        : [el("li", { className: "empty-state" }, [el("span", { text: "🧾" }), el("p", { text: "No orders yet. Your first dosa awaits!" })])]),
    );
  } catch (error) {
    $("#orders-status").textContent = `Couldn't load orders: ${error.message}`;
  }
}

async function cancelOrder(orderId, button) {
  button.disabled = true;
  try {
    await api(`/api/orders/${orderId}/cancel`, { method: "POST" });
    toast(`Order #${orderId} cancelled.`, "success");
    await Promise.all([loadOrders(), loadMenu()]);
  } catch (error) {
    showError(error);
    button.disabled = false;
  }
}

// ------------------------------------------------------------------ admin

let currentAdminTab = "items";
const ADMIN_TABS = { items: loadAdminItems, orders: loadAdminOrders, docs: loadDocuments };

function showAdminTab(tab) {
  currentAdminTab = tab;
  for (const name of Object.keys(ADMIN_TABS)) {
    $(`#admin-tab-${name}`).setAttribute("aria-selected", String(name === tab));
    $(`#admin-${name}`).hidden = name !== tab;
  }
  ADMIN_TABS[tab]();
}

async function loadAdminItems() {
  try {
    const items = await api("/api/menu_items?include_inactive=true");
    $("#admin-item-rows").replaceChildren(...items.map(adminItemRow));
  } catch (error) {
    showError(error);
  }
}

function adminItemRow(item) {
  const price = el("input", { attrs: { type: "number", min: "1", step: "0.01", value: item.price, "aria-label": `${item.title} price` } });
  const capacity = el("input", { attrs: { type: "number", min: "0", step: "1", value: item.capacity, "aria-label": `${item.title} portions` } });
  const save = el("button", {
    className: "button button-ghost button-small",
    text: "Save",
    attrs: { type: "button" },
    on: { click: () => saveItem(item, { price: price.value, capacity: Number(capacity.value) }) },
  });
  const toggle = item.active
    ? el("button", { className: "button button-danger button-small", text: "Disable", attrs: { type: "button" }, on: { click: () => disableItem(item) } })
    : el("button", { className: "button button-ghost button-small", text: "Enable", attrs: { type: "button" }, on: { click: () => saveItem(item, { active: true }) } });
  return el("tr", {}, [
    el("td", {}, [el("strong", { text: `${dishEmoji(item.title, item.category)} ${item.title}` }), el("div", { className: "muted small", text: item.category })]),
    el("td", {}, [price]),
    el("td", {}, [capacity]),
    el("td", {}, [statusBadge(item.active ? "ACTIVE" : "DISABLED")]),
    el("td", {}, [el("div", { className: "row-actions" }, [save, toggle])]),
  ]);
}

async function saveItem(item, changes) {
  try {
    await api(`/api/menu_items/${item.id}`, { method: "PUT", body: changes });
    toast(`${item.title} updated.`, "success");
    await Promise.all([loadAdminItems(), loadMenu()]);
  } catch (error) {
    showError(error);
  }
}

async function disableItem(item) {
  try {
    await api(`/api/menu_items/${item.id}`, { method: "DELETE" });
    toast(`${item.title} disabled.`);
    await Promise.all([loadAdminItems(), loadMenu()]);
  } catch (error) {
    showError(error);
  }
}

async function createItem(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const body = {
    title: data.get("title").trim(),
    category: data.get("category"),
    price: data.get("price"),
    capacity: Number(data.get("capacity")),
    vegetarian: data.get("vegetarian") === "on",
  };
  const description = data.get("description").trim();
  if (description) body.description = description;
  try {
    const item = await api("/api/menu_items", { method: "POST", body });
    toast(`${item.title} added to the menu.`, "success");
    form.reset();
    await Promise.all([loadAdminItems(), loadMenu(), loadCategories()]);
  } catch (error) {
    showError(error);
  }
}

async function loadAdminOrders() {
  const filter = $("#admin-order-filter").value;
  try {
    const orders = await api(`/api/admin/orders${filter ? `?status=${filter}` : ""}`);
    $("#admin-order-list").replaceChildren(
      ...(orders.length
        ? orders.map((order) => orderCard(order, { admin: true }))
        : [el("li", { className: "empty-state" }, [el("span", { text: "📭" }), el("p", { text: "No orders here." })])]),
    );
  } catch (error) {
    showError(error);
  }
}

async function updateOrderStatus(orderId, status, button) {
  button.disabled = true;
  try {
    await api(`/api/admin/orders/${orderId}/status`, { method: "PATCH", body: { status } });
    toast(`Order #${orderId} is now ${label(status)}.`, "success");
    await loadAdminOrders();
    if (status === "CANCELLED") loadMenu();
  } catch (error) {
    showError(error);
    button.disabled = false;
  }
}

async function loadDocuments() {
  try {
    const documents = await api("/api/documents");
    $("#doc-rows").replaceChildren(
      ...(documents.length
        ? documents.map((doc) =>
            el("tr", {}, [
              el("td", { text: doc.filename }),
              el("td", { text: `v${doc.version}` }),
              el("td", { text: doc.chunk_count }),
              el("td", {}, [statusBadge(doc.status)]),
              el("td", { text: formatDate(doc.created_at) }),
            ]),
          )
        : [el("tr", {}, [el("td", { text: "No documents indexed yet.", attrs: { colspan: "5" } })])]),
    );
  } catch (error) {
    showError(error);
  }
}

function pickDocument(file) {
  $("#doc-file-name").textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(0)} KB` : "";
  $("#doc-upload").disabled = !file;
}

async function uploadDocument(event) {
  event.preventDefault();
  const file = $("#doc-file").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  const button = $("#doc-upload");
  button.disabled = true;
  button.textContent = "Indexing…";
  try {
    const doc = await api("/api/documents", { method: "POST", form });
    toast(`${doc.filename} v${doc.version} indexed (${doc.chunk_count} chunks).`, "success");
    $("#doc-form").reset();
    pickDocument(null);
    await loadDocuments();
  } catch (error) {
    showError(error, "Upload failed: ");
  } finally {
    button.textContent = "Upload & index";
    button.disabled = !$("#doc-file").files[0];
  }
}

// ------------------------------------------------------------------ chat

function toggleChat(open = !state.chat.open) {
  state.chat.open = open;
  $("#chat-drawer").hidden = !open;
  $("#chat-fab").hidden = open;
  $("#chat-fab").setAttribute("aria-expanded", String(open));
  if (open) {
    renderChatGate();
    if (state.me && !state.chat.sessions.length) loadSessions();
    else renderChat();
    $(state.me ? "#chat-input" : "#chat-close").focus();
  } else {
    $("#chat-fab").focus();
  }
}

function renderChatGate() {
  const signedIn = Boolean(state.me);
  $("#chat-signed-out").hidden = signedIn;
  $("#chat-log").hidden = !signedIn;
  $("#chat-form").hidden = !signedIn;
  $(".chat-sessions").hidden = !signedIn;
}

async function loadSessions() {
  try {
    state.chat.sessions = await api("/api/chat/sessions");
    if (!state.chat.sessions.some((session) => session.id === state.chat.currentId)) {
      state.chat.currentId = state.chat.sessions[0]?.id ?? null;
    }
    renderSessionSelect();
    await loadMessages();
  } catch (error) {
    showError(error, "Chat: ");
  }
}

function renderSessionSelect() {
  const select = $("#session-select");
  const options = state.chat.sessions.map((session) =>
    el("option", { text: session.title, attrs: { value: session.id, selected: session.id === state.chat.currentId } }),
  );
  if (!options.length) options.push(el("option", { text: "New conversation", attrs: { value: "" } }));
  select.replaceChildren(...options);
}

async function ensureSession() {
  if (state.chat.currentId) return state.chat.currentId;
  const session = await api("/api/chat/sessions", { method: "POST" });
  state.chat.sessions.unshift(session);
  state.chat.currentId = session.id;
  renderSessionSelect();
  return session.id;
}

async function newChat() {
  try {
    const session = await api("/api/chat/sessions", { method: "POST" });
    state.chat.sessions.unshift(session);
    state.chat.currentId = session.id;
    state.chat.messages = [];
    renderSessionSelect();
    renderChat();
    $("#chat-input").focus();
  } catch (error) {
    showError(error);
  }
}

async function loadMessages() {
  if (!state.chat.currentId) {
    state.chat.messages = [];
    renderChat();
    return;
  }
  try {
    state.chat.messages = await api(`/api/chat/sessions/${state.chat.currentId}/messages`);
  } catch (error) {
    showError(error);
    state.chat.messages = [];
  }
  renderChat();
}

function renderChat() {
  const log = $("#chat-log");
  const nodes = state.chat.messages.length ? state.chat.messages.map(messageNode) : [chatWelcome()];
  if (state.chat.busy) {
    nodes.push(el("div", { className: "typing", attrs: { "aria-label": "Assistant is typing" } }, [el("span"), el("span"), el("span")]));
  }
  log.replaceChildren(...nodes);
  log.scrollTop = log.scrollHeight;
  updateSendButton();
}

function chatWelcome() {
  return el("div", { className: "chat-welcome" }, [
    el("span", { className: "big", text: "👋", attrs: { "aria-hidden": "true" } }),
    el("h3", { text: "Hi! What are you craving?" }),
    el("p", { className: "muted small", text: "I can check the live menu, explain our policies with sources, and prepare orders for you to confirm." }),
    el(
      "div",
      { className: "suggestions" },
      CHAT_SUGGESTIONS.map((text) =>
        el("button", { className: "suggestion", text, attrs: { type: "button" }, on: { click: () => sendMessage(text) } }),
      ),
    ),
  ]);
}

function messageNode(message) {
  const isUser = message.role === "user";
  const classes = ["msg", isUser ? "user" : "assistant"];
  if (message.error) classes.push("error");
  const bubble = isUser
    ? el("div", { className: "bubble", text: message.content })
    : el("div", { className: "bubble rich" }, richText(message.content));
  const node = el("div", { className: classes.join(" ") }, [bubble]);

  for (const action of message.pending_actions) node.append(actionCard(action));

  const meta = el("div", { className: "msg-meta" });
  if (!isUser && message.mode) meta.append(el("span", { className: `mode mode-${message.mode}`, text: message.mode }));
  for (const citation of message.citations) {
    const page = citation.page ? ` · p.${citation.page}` : "";
    meta.append(
      el("span", { className: "source", text: `📄 ${citation.filename}${page}`, attrs: { title: `Version ${citation.version}, chunk ${citation.chunk_id}` } }),
    );
  }
  if (message.error && !isUser) meta.append(el("span", { text: message.error.code }));
  meta.append(el("time", { text: parseServerDate(message.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), attrs: { datetime: message.created_at } }));
  node.append(meta);
  return node;
}

// A tiny, safe subset of Markdown for replies: paragraphs, "-"/"1." lists and
// **bold**. It builds DOM nodes with textContent only; no HTML is parsed.
function richText(content) {
  const blocks = [];
  let list = null;
  for (const raw of content.split("\n")) {
    const line = raw.trim();
    const item = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (item) {
      const ordered = /^\d/.test(line);
      if (!list || list.ordered !== ordered) {
        list = { ordered, node: el(ordered ? "ol" : "ul") };
        blocks.push(list.node);
      }
      list.node.append(el("li", {}, inline(item[1])));
      continue;
    }
    list = null;
    if (line) blocks.push(el("p", {}, inline(line)));
  }
  return blocks;
}

function inline(text) {
  return text
    .split(/\*\*(.+?)\*\*/g)
    .map((part, index) => (index % 2 ? el("strong", { text: part }) : document.createTextNode(part)));
}

function actionCard(action) {
  const isOrder = action.action === "CREATE_ORDER";
  const pending = action.status === "PENDING";
  const card = el("div", { className: `action-card${pending ? " pending" : ""}` });
  card.append(
    el("div", { className: "action-head" }, [
      el("span", { text: isOrder ? "🧾 Order proposal" : "↩️ Cancellation proposal" }),
      statusBadge(action.status),
    ]),
  );
  const lines = action.terms.lines ?? [];
  card.append(
    el(
      "ul",
      { className: "action-lines" },
      lines.map((line) =>
        el("li", {}, [
          el("span", { text: `${line.quantity} × ${line.title}` }),
          el("span", { text: line.line_total ? rupees(line.line_total) : "" }),
        ]),
      ),
    ),
  );
  if (!isOrder && action.terms.impact) card.append(el("p", { className: "action-note", text: action.terms.impact }));
  card.append(el("div", { className: "action-total" }, [el("span", { text: isOrder ? "Total" : "Refund" }), el("span", { text: rupees(action.terms.total) })]));

  if (pending) {
    const confirm = el("button", { className: "button button-success button-small", text: isOrder ? "Confirm order" : "Confirm cancel", attrs: { type: "button" } });
    const reject = el("button", { className: "button button-ghost button-small", text: "Reject", attrs: { type: "button" } });
    confirm.addEventListener("click", () => decide(action.id, "CONFIRM", [confirm, reject]));
    reject.addEventListener("click", () => decide(action.id, "REJECT", [confirm, reject]));
    card.append(el("div", { className: "action-buttons" }, [reject, confirm]));
    const minutes = Math.max(0, Math.round((parseServerDate(action.expires_at) - Date.now()) / 60000));
    card.append(el("p", { className: "action-note", text: `Nothing happens until you confirm. Expires in ${minutes} min.` }));
  }
  return card;
}

async function decide(actionId, decision, buttons) {
  buttons.forEach((button) => (button.disabled = true));
  try {
    const result = await api(`/api/chat/actions/${actionId}/decision`, { method: "POST", body: { decision } });
    if (result.action.status === "CONFIRMED") toast(result.message.content, "success");
    else if (result.action.status === "FAILED") toast(result.message.content, "error");
    await loadMessages();
    if (result.action.status === "CONFIRMED") {
      loadMenu();
      if (state.view === "orders") loadOrders();
    }
  } catch (error) {
    showError(error);
    buttons.forEach((button) => (button.disabled = false));
  }
}

function updateSendButton() {
  $("#chat-send").disabled = state.chat.busy || !$("#chat-input").value.trim();
}

async function sendMessage(text) {
  const content = text.trim();
  if (!content || state.chat.busy || !requireSignIn()) return;
  state.chat.busy = true;
  $("#chat-input").value = "";
  autosize();
  // Optimistic bubble; replaced by the stored history when the reply arrives.
  state.chat.messages.push({
    id: `local-${Date.now()}`,
    role: "user",
    content,
    mode: null,
    citations: [],
    pending_actions: [],
    error: null,
    created_at: new Date().toISOString(),
  });
  renderChat();
  try {
    const sessionId = await ensureSession();
    await api(`/api/chat/sessions/${sessionId}/messages`, { method: "POST", body: { content } });
  } catch (error) {
    if (error.status === 429) toast(error.message, "error");
    else if (error.status !== 503) showError(error);
  } finally {
    state.chat.busy = false;
    await loadMessages();
    loadSessions();
    $("#chat-input").focus();
  }
}

function autosize() {
  const input = $("#chat-input");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
}

// ------------------------------------------------------------------ wiring

function wire() {
  document.querySelectorAll("[data-view-link]").forEach((link) =>
    link.addEventListener("click", (event) => {
      event.preventDefault();
      setView(link.dataset.viewLink);
    }),
  );

  $("#sign-in-button").addEventListener("click", openSignIn);
  document.querySelectorAll("[data-open-sign-in]").forEach((b) => b.addEventListener("click", openSignIn));
  $("#sign-in-cancel").addEventListener("click", () => $("#sign-in-dialog").close());
  $("#sign-in-form").addEventListener("submit", signIn);
  $("#sign-out-button").addEventListener("click", signOut);

  $("#search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.filters.keyword = $("#search-input").value.trim();
    loadMenu();
  });
  $("#search-input").addEventListener("search", () => {
    state.filters.keyword = $("#search-input").value.trim();
    loadMenu();
  });
  $("#veg-only").addEventListener("change", (event) => {
    state.filters.vegetarian = event.target.checked;
    loadMenu();
  });
  const maxPrice = $("#max-price");
  maxPrice.addEventListener("input", () => {
    const value = Number(maxPrice.value);
    $("#max-price-label").textContent = value >= Number(maxPrice.max) ? "any" : rupees(value);
  });
  maxPrice.addEventListener("change", () => {
    const value = Number(maxPrice.value);
    state.filters.maxPrice = value >= Number(maxPrice.max) ? null : value;
    loadMenu();
  });

  $("#cart-button").addEventListener("click", () => {
    setView("menu");
    $("#cart-panel").classList.add("open");
    $("#cart-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  $("#cart-close").addEventListener("click", () => $("#cart-panel").classList.remove("open"));
  $("#place-order").addEventListener("click", placeOrder);
  $("#orders-refresh").addEventListener("click", loadOrders);

  for (const tab of Object.keys(ADMIN_TABS)) {
    $(`#admin-tab-${tab}`).addEventListener("click", () => showAdminTab(tab));
  }
  $("#item-form").addEventListener("submit", createItem);
  $("#admin-order-filter").addEventListener("change", loadAdminOrders);
  $("#doc-file").addEventListener("change", (event) => pickDocument(event.target.files[0]));
  $("#doc-form").addEventListener("submit", uploadDocument);
  const dropzone = $("#doc-form");
  dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropzone.classList.add("dragging");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragging");
    const file = event.dataTransfer.files[0];
    if (!file) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    $("#doc-file").files = transfer.files;
    pickDocument(file);
  });

  $("#chat-fab").addEventListener("click", () => toggleChat(true));
  $("#chat-close").addEventListener("click", () => toggleChat(false));
  $("#new-chat").addEventListener("click", newChat);
  $("#session-select").addEventListener("change", (event) => {
    state.chat.currentId = Number(event.target.value) || null;
    loadMessages();
  });
  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage($("#chat-input").value);
  });
  $("#chat-input").addEventListener("input", () => {
    autosize();
    updateSendButton();
  });
  $("#chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendMessage($("#chat-input").value);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.chat.open && !$("#sign-in-dialog").open) toggleChat(false);
  });
}

wire();
renderAccount();
loadCategories();
loadMenu();
