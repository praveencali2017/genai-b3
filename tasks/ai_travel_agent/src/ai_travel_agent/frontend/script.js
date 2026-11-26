"use strict";

// Praveen's Travels web UI: plain JavaScript over the same-origin REST API.
//
// Security notes:
// - The Basic credentials live only in this page's memory (never in storage,
//   cookies or URLs); reloading the page signs you out.
// - Everything from the server, including chat replies and document excerpts,
//   is rendered with textContent, never as HTML.

const $ = (selector) => document.querySelector(selector);

const ADMIN_NEXT_STATUSES = {
  CONFIRMED: ["COMPLETED", "CANCELLED"],
  COMPLETED: [],
  CANCELLED: [],
};
const MAX_TRAVELLERS = 10;
const CHAT_SUGGESTIONS = [
  "Suggest a five-day Kashmir itinerary.",
  "Find Kashmir packages under ₹25000.",
  "What is the cancellation policy?",
  "Find a five-day Kashmir package below ₹25000, explain cancellation and book it.",
];

const state = {
  auth: null, // "Basic …" header value, memory only
  me: null,
  view: "packages",
  destinations: [],
  filters: { destination: "", keyword: "", durationDays: "", startDate: "", maxPrice: null },
  packages: [],
  booking: null, // { pkg, key } while the booking dialog is open
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
  const text = Number.isInteger(amount)
    ? amount.toLocaleString("en-IN")
    : amount.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `₹${text}`;
};
const label = (status) => status.toLowerCase().replaceAll("_", " ");
const statusBadge = (status) =>
  el("span", { className: `status-badge s-${status.toLowerCase()}`, text: label(status) });
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

function parseServerDate(value) {
  // The API sends UTC timestamps without an offset.
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
}
const formatDateTime = (value) =>
  parseServerDate(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
// Calendar dates ("2026-12-25") are not instants: format them without a timezone shift.
const formatDay = (isoDate) =>
  new Date(`${isoDate}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
const tripDates = (start, end) => (start === end ? formatDay(start) : `${formatDay(start)} – ${formatDay(end)}`);

const DESTINATION_ART = [
  [["srinagar", "leh", "darjeeling", "shillong", "manali"], "🏔️", "mountain"],
  [["goa", "port blair", "andaman"], "🏖️", "beach"],
  [["alleppey", "kerala", "kumarakom"], "🛶", "water"],
  [["rishikesh"], "🚣", "water"],
  [["jaipur", "udaipur"], "🏰", "heritage"],
  [["hampi"], "🛕", "heritage"],
  [["madikeri", "coorg"], "☕", "forest"],
];
function destinationArt(destination) {
  const place = destination.toLowerCase();
  for (const [names, emoji, theme] of DESTINATION_ART) {
    if (names.some((name) => place.includes(name))) return { emoji, theme };
  }
  return { emoji: "🧭", theme: "other" };
}

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
  if ((view === "bookings" && !state.me) || (view === "admin" && state.me?.role !== "ADMIN")) {
    view = "packages";
  }
  state.view = view;
  for (const name of ["packages", "bookings", "admin"]) $(`#view-${name}`).hidden = name !== view;
  document.querySelectorAll(".nav-link").forEach((link) => {
    if (link.dataset.viewLink === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (view === "bookings") loadBookings();
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
  setView("packages");
  toast("Signed out.");
}

function requireSignIn() {
  if (state.me) return true;
  openSignIn();
  return false;
}

// ------------------------------------------------------------------ catalog

function packagesQuery() {
  const params = new URLSearchParams();
  const { destination, keyword, durationDays, startDate, maxPrice } = state.filters;
  if (destination) params.set("destination", destination);
  if (keyword) params.set("keyword", keyword);
  if (durationDays) params.set("duration_days", durationDays);
  if (startDate) params.set("start_date", startDate);
  if (maxPrice) params.set("max_price", String(maxPrice));
  return params.toString();
}

async function loadDestinations() {
  try {
    const packages = await api("/api/packages");
    state.destinations = [...new Set(packages.map((pkg) => pkg.destination))].sort();
  } catch {
    state.destinations = [];
  }
  renderDestinationChips();
}

function renderDestinationChips() {
  const chips = ["", ...state.destinations].map((destination) =>
    el("button", {
      className: "chip",
      text: destination || "All",
      attrs: { type: "button", "aria-pressed": String(state.filters.destination === destination) },
      on: {
        click: () => {
          state.filters.destination = destination;
          renderDestinationChips();
          loadPackages();
        },
      },
    }),
  );
  $("#destination-chips").replaceChildren(...chips);
}

async function loadPackages() {
  const grid = $("#package-grid");
  if (!state.packages.length) {
    grid.replaceChildren(...Array.from({ length: 6 }, () => el("li", { className: "skeleton" })));
  }
  $("#packages-status").textContent = "";
  try {
    state.packages = await api(`/api/packages?${packagesQuery()}`);
    renderPackages();
  } catch (error) {
    grid.replaceChildren();
    $("#packages-status").textContent = `Couldn't load packages: ${error.message}`;
  }
}

function renderPackages() {
  const grid = $("#package-grid");
  if (!state.packages.length) {
    grid.replaceChildren(
      el("li", { className: "empty-state" }, [
        el("span", { text: "🗺️", attrs: { "aria-hidden": "true" } }),
        el("p", { text: "No upcoming packages match these filters." }),
      ]),
    );
    $("#packages-status").textContent = "";
    return;
  }
  $("#packages-status").textContent = plural(state.packages.length, "package");
  grid.replaceChildren(...state.packages.map(packageCard));
}

function seatsText(seats) {
  if (seats === 0) return { text: "Sold out", className: "seats out" };
  if (seats <= 3) return { text: `Only ${seats} left`, className: "seats low" };
  return { text: `${seats} seats`, className: "seats" };
}

function packageCard(pkg) {
  const art = destinationArt(pkg.destination);
  const seats = seatsText(pkg.capacity);
  const soldOut = pkg.capacity === 0;
  return el("li", { className: "package" }, [
    el("div", { className: `package-art ${art.theme}`, attrs: { "aria-hidden": "true" } }, [
      el("span", { className: "duration-tag", text: plural(pkg.duration_days, "day") }),
      el("span", { text: art.emoji }),
      el("span", { className: seats.className, text: seats.text }),
    ]),
    el("div", { className: "package-body" }, [
      el("p", { className: "package-place", text: `📍 ${pkg.destination}` }),
      el("h3", { className: "package-title", text: pkg.title }),
      el("p", { className: "package-dates", text: `🗓️ ${tripDates(pkg.start_date, pkg.end_date)}` }),
      el("p", { className: "package-desc", text: pkg.description ?? "" }),
      el("p", { className: "sr-only", text: `${plural(pkg.duration_days, "day")}. ${seats.text}.` }),
      el("div", { className: "package-foot" }, [
        el("span", { className: "price" }, [
          document.createTextNode(rupees(pkg.price)),
          el("small", { text: " / traveller" }),
        ]),
        el("button", {
          className: "button button-primary button-small",
          text: soldOut ? "Sold out" : "Book",
          attrs: { type: "button", disabled: soldOut, "aria-label": `Book ${pkg.title}` },
          on: { click: () => openBooking(pkg) },
        }),
      ]),
    ]),
  ]);
}

// ------------------------------------------------------------------ booking

function openBooking(pkg) {
  if (!requireSignIn()) return;
  // One Idempotency-Key per dialog: a retry after a network error replays the
  // same booking instead of creating a second one.
  state.booking = { pkg, key: crypto.randomUUID() };
  const travellers = $("#book-travellers");
  travellers.max = String(Math.min(MAX_TRAVELLERS, pkg.capacity));
  travellers.value = "1";
  $("#book-mark").textContent = destinationArt(pkg.destination).emoji;
  $("#book-title").textContent = pkg.title;
  $("#book-summary").textContent =
    `${pkg.destination} · ${plural(pkg.duration_days, "day")} · ${tripDates(pkg.start_date, pkg.end_date)} · ${rupees(pkg.price)} per traveller`;
  $("#book-error").hidden = true;
  updateBookingTotal();
  $("#book-dialog").showModal();
  travellers.focus();
}

function updateBookingTotal() {
  if (!state.booking) return;
  const travellers = Number($("#book-travellers").value) || 0;
  $("#book-total").textContent = rupees(Number(state.booking.pkg.price) * travellers);
  // A different traveller count is a different booking: it gets a new key.
  if (state.booking.travellers !== travellers) {
    state.booking.travellers = travellers;
    state.booking.key = crypto.randomUUID();
  }
}

async function submitBooking(event) {
  event.preventDefault();
  const { pkg, key } = state.booking;
  const quantity = Number($("#book-travellers").value);
  const submit = $("#book-submit");
  submit.disabled = true;
  submit.textContent = "Booking…";
  try {
    const booking = await api("/api/bookings", {
      method: "POST",
      body: { lines: [{ package_id: pkg.id, quantity }] },
      headers: { "Idempotency-Key": key },
    });
    $("#book-dialog").close();
    state.booking = null;
    toast(`Booking #${booking.id} confirmed. Total ${rupees(booking.total)}.`, "success");
    await loadPackages();
  } catch (error) {
    $("#book-error").textContent = error.message;
    $("#book-error").hidden = false;
    if (error.status === 409) loadPackages();
  } finally {
    submit.disabled = false;
    submit.textContent = "Confirm booking";
  }
}

// ------------------------------------------------------------------ my bookings

function refundLine(booking) {
  if (booking.status === "CANCELLED") {
    return el("p", { className: "refund-note", text: `Refunded ${rupees(booking.refund_amount ?? 0)} of ${rupees(booking.total)}.` });
  }
  const quote = booking.cancellation_quote;
  if (!quote) return null;
  const percent = quote.refund_percent === null ? "" : ` (${quote.refund_percent}%)`;
  return el("p", {
    className: "refund-note",
    text: `If cancelled today: refund ${rupees(quote.refund_amount)}${percent}, ${plural(quote.days_before_departure, "day")} before departure.`,
  });
}

function bookingCard(booking, { admin = false } = {}) {
  const actions = el("div", { className: "booking-actions" });
  if (admin) {
    for (const next of ADMIN_NEXT_STATUSES[booking.status]) {
      actions.append(
        el("button", {
          className: `button button-small ${next === "CANCELLED" ? "button-danger" : "button-ghost"}`,
          text: next === "CANCELLED" ? "Cancel (full refund)" : `Mark ${label(next)}`,
          attrs: { type: "button" },
          on: { click: (event) => updateBookingStatus(booking.id, next, event.currentTarget) },
        }),
      );
    }
  } else if (booking.cancellation_quote) {
    actions.append(cancelButton(booking));
  }

  return el("li", { className: "booking-card" }, [
    el("div", { className: "booking-top" }, [
      el("span", { className: "booking-id", text: `Booking #${booking.id}` }),
      statusBadge(booking.status),
      el("span", { className: "booking-date", text: `Booked ${formatDateTime(booking.created_at)}${admin ? ` · traveller #${booking.user_id}` : ""}` }),
      el("span", { className: "booking-total", text: rupees(booking.total) }),
    ]),
    el(
      "ul",
      { className: "booking-lines" },
      booking.lines.map((line) =>
        el("li", { className: "trip-line" }, [
          el("span", { className: "trip-emoji", text: destinationArt(line.destination).emoji, attrs: { "aria-hidden": "true" } }),
          el("div", {}, [
            el("strong", { text: line.title }),
            el("div", { className: "muted small", text: `${line.destination} · departs ${formatDay(line.start_date)} · ${plural(line.quantity, "traveller")} × ${rupees(line.unit_price)}` }),
          ]),
        ]),
      ),
    ),
    refundLine(booking),
    actions.childElementCount ? actions : null,
  ]);
}

// Two-step cancel: the first click shows the refund, the second cancels.
function cancelButton(booking) {
  const quote = booking.cancellation_quote;
  const button = el("button", {
    className: "button button-danger button-small",
    text: "Cancel booking",
    attrs: { type: "button" },
  });
  let armed = false;
  button.addEventListener("click", () => {
    if (!armed) {
      armed = true;
      button.textContent = `Confirm cancel · refund ${rupees(quote.refund_amount)}`;
      setTimeout(() => {
        armed = false;
        if (button.isConnected && !button.disabled) button.textContent = "Cancel booking";
      }, 6000);
      return;
    }
    cancelBooking(booking.id, button);
  });
  return button;
}

async function loadBookings() {
  if (!state.me) return;
  $("#bookings-status").textContent = "Loading…";
  try {
    const bookings = await api("/api/bookings");
    $("#bookings-status").textContent = "";
    $("#bookings-list").replaceChildren(
      ...(bookings.length
        ? bookings.map((booking) => bookingCard(booking))
        : [el("li", { className: "empty-state" }, [el("span", { text: "🧳" }), el("p", { text: "No bookings yet. Your next adventure awaits!" })])]),
    );
  } catch (error) {
    $("#bookings-status").textContent = `Couldn't load bookings: ${error.message}`;
  }
}

async function cancelBooking(bookingId, button) {
  button.disabled = true;
  try {
    const booking = await api(`/api/bookings/${bookingId}/cancel`, { method: "POST" });
    toast(`Booking #${bookingId} cancelled. Refund ${rupees(booking.refund_amount)}.`, "success");
    await Promise.all([loadBookings(), loadPackages()]);
  } catch (error) {
    showError(error);
    button.disabled = false;
  }
}

// ------------------------------------------------------------------ admin

let currentAdminTab = "packages";
const ADMIN_TABS = { packages: loadAdminPackages, bookings: loadAdminBookings, docs: loadDocuments };

function showAdminTab(tab) {
  currentAdminTab = tab;
  for (const name of Object.keys(ADMIN_TABS)) {
    $(`#admin-tab-${name}`).setAttribute("aria-selected", String(name === tab));
    $(`#admin-${name}`).hidden = name !== tab;
  }
  ADMIN_TABS[tab]();
}

async function loadAdminPackages() {
  try {
    const packages = await api("/api/packages?include_inactive=true");
    $("#admin-package-rows").replaceChildren(...packages.map(adminPackageRow));
  } catch (error) {
    showError(error);
  }
}

function adminPackageRow(pkg) {
  const startDate = el("input", { attrs: { type: "date", value: pkg.start_date, "aria-label": `${pkg.title} departure` } });
  const price = el("input", { attrs: { type: "number", min: "1", step: "0.01", value: pkg.price, "aria-label": `${pkg.title} price` } });
  const capacity = el("input", { attrs: { type: "number", min: "0", step: "1", value: pkg.capacity, "aria-label": `${pkg.title} seats` } });
  const save = el("button", {
    className: "button button-ghost button-small",
    text: "Save",
    attrs: { type: "button" },
    on: {
      click: () => {
        const changes = { price: price.value, capacity: Number(capacity.value) };
        if (startDate.value !== pkg.start_date) changes.start_date = startDate.value;
        savePackage(pkg, changes);
      },
    },
  });
  const toggle = pkg.active
    ? el("button", { className: "button button-danger button-small", text: "Disable", attrs: { type: "button" }, on: { click: () => disablePackage(pkg) } })
    : el("button", { className: "button button-ghost button-small", text: "Enable", attrs: { type: "button" }, on: { click: () => savePackage(pkg, { active: true }) } });
  return el("tr", {}, [
    el("td", {}, [
      el("strong", { text: `${destinationArt(pkg.destination).emoji} ${pkg.title}` }),
      el("div", { className: "muted small", text: `${pkg.destination} · ${plural(pkg.duration_days, "day")}` }),
    ]),
    el("td", {}, [startDate]),
    el("td", {}, [price]),
    el("td", {}, [capacity]),
    el("td", {}, [statusBadge(pkg.active ? "ACTIVE" : "DISABLED")]),
    el("td", {}, [el("div", { className: "row-actions" }, [save, toggle])]),
  ]);
}

async function savePackage(pkg, changes) {
  try {
    await api(`/api/packages/${pkg.id}`, { method: "PUT", body: changes });
    toast(`${pkg.title} updated.`, "success");
    await Promise.all([loadAdminPackages(), loadPackages()]);
  } catch (error) {
    showError(error);
  }
}

async function disablePackage(pkg) {
  try {
    await api(`/api/packages/${pkg.id}`, { method: "DELETE" });
    toast(`${pkg.title} disabled.`);
    await Promise.all([loadAdminPackages(), loadPackages()]);
  } catch (error) {
    showError(error);
  }
}

async function createPackage(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const body = {
    title: data.get("title").trim(),
    destination: data.get("destination").trim(),
    duration_days: Number(data.get("duration_days")),
    price: data.get("price"),
    capacity: Number(data.get("capacity")),
    start_date: data.get("start_date"),
  };
  const description = data.get("description").trim();
  if (description) body.description = description;
  try {
    const pkg = await api("/api/packages", { method: "POST", body });
    toast(`${pkg.title} added to the catalog.`, "success");
    form.reset();
    await Promise.all([loadAdminPackages(), loadPackages(), loadDestinations()]);
  } catch (error) {
    showError(error);
  }
}

async function loadAdminBookings() {
  const filter = $("#admin-booking-filter").value;
  try {
    const bookings = await api(`/api/admin/bookings${filter ? `?status=${filter}` : ""}`);
    $("#admin-booking-list").replaceChildren(
      ...(bookings.length
        ? bookings.map((booking) => bookingCard(booking, { admin: true }))
        : [el("li", { className: "empty-state" }, [el("span", { text: "📭" }), el("p", { text: "No bookings here." })])]),
    );
  } catch (error) {
    showError(error);
  }
}

async function updateBookingStatus(bookingId, status, button) {
  button.disabled = true;
  try {
    await api(`/api/admin/bookings/${bookingId}/status`, { method: "PATCH", body: { status } });
    toast(`Booking #${bookingId} is now ${label(status)}.`, "success");
    await loadAdminBookings();
    if (status === "CANCELLED") loadPackages();
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
              el("td", { text: formatDateTime(doc.created_at) }),
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
    el("h3", { text: "Hi! Where would you like to go?" }),
    el("p", { className: "muted small", text: "I can plan itineraries, search live packages, explain our policies with sources, and prepare bookings for you to confirm." }),
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
  const isBooking = action.action === "CREATE_BOOKING";
  const pending = action.status === "PENDING";
  const terms = action.terms;
  const card = el("div", { className: `action-card${pending ? " pending" : ""}` });
  card.append(
    el("div", { className: "action-head" }, [
      el("span", { text: isBooking ? "🧳 Booking proposal" : "↩️ Cancellation proposal" }),
      statusBadge(action.status),
    ]),
  );
  card.append(
    el(
      "ul",
      { className: "action-lines" },
      (terms.lines ?? []).map((line) =>
        el("li", {}, [
          el("span", { text: `${line.title} · ${formatDay(line.start_date)} · ${plural(line.quantity, "traveller")}` }),
          el("span", { text: line.line_total ? rupees(line.line_total) : "" }),
        ]),
      ),
    ),
  );
  if (!isBooking && terms.impact) card.append(el("p", { className: "action-note", text: terms.impact }));
  const [totalLabel, totalValue] = isBooking
    ? ["Total", terms.total]
    : [`Refund${terms.refund_percent === null || terms.refund_percent === undefined ? "" : ` (${terms.refund_percent}%)`}`, terms.refund_amount];
  card.append(el("div", { className: "action-total" }, [el("span", { text: totalLabel }), el("span", { text: rupees(totalValue) })]));

  if (pending) {
    const confirm = el("button", { className: "button button-success button-small", text: isBooking ? "Confirm booking" : "Confirm cancel", attrs: { type: "button" } });
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
    const result = await api(`/api/chat/actions/${actionId}/confirm`, { method: "POST", body: { decision } });
    if (result.action.status === "CONFIRMED") toast(result.message.content, "success");
    else if (result.action.status === "FAILED") toast(result.message.content, "error");
    await loadMessages();
    if (result.action.status === "CONFIRMED") {
      loadPackages();
      if (state.view === "bookings") loadBookings();
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
    loadPackages();
  });
  $("#search-input").addEventListener("search", () => {
    state.filters.keyword = $("#search-input").value.trim();
    loadPackages();
  });
  $("#duration-filter").addEventListener("change", (event) => {
    state.filters.durationDays = event.target.value;
    loadPackages();
  });
  $("#start-filter").addEventListener("change", (event) => {
    state.filters.startDate = event.target.value;
    loadPackages();
  });
  const maxPrice = $("#max-price");
  maxPrice.addEventListener("input", () => {
    const value = Number(maxPrice.value);
    $("#max-price-label").textContent = value >= Number(maxPrice.max) ? "any" : rupees(value);
  });
  maxPrice.addEventListener("change", () => {
    const value = Number(maxPrice.value);
    state.filters.maxPrice = value >= Number(maxPrice.max) ? null : value;
    loadPackages();
  });

  $("#book-travellers").addEventListener("input", updateBookingTotal);
  $("#book-form").addEventListener("submit", submitBooking);
  $("#book-cancel").addEventListener("click", () => $("#book-dialog").close());
  $("#book-dialog").addEventListener("close", () => (state.booking = null));
  $("#bookings-refresh").addEventListener("click", loadBookings);

  for (const tab of Object.keys(ADMIN_TABS)) {
    $(`#admin-tab-${tab}`).addEventListener("click", () => showAdminTab(tab));
  }
  $("#package-form").addEventListener("submit", createPackage);
  $("#admin-booking-filter").addEventListener("change", loadAdminBookings);
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
    const dialogOpen = $("#sign-in-dialog").open || $("#book-dialog").open;
    if (event.key === "Escape" && state.chat.open && !dialogOpen) toggleChat(false);
  });
}

wire();
renderAccount();
loadDestinations();
loadPackages();
