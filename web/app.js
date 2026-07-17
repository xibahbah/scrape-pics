const state = {
  items: [],
  stats: { counts: {}, handles: [], tags: [] },
  jobs: [],
  config: {},
  selectedId: null,
  filter: "all",
  handle: "",
  query: "",
  view: "grid",
  noteTimer: null,
  titleTimer: null,
  dragging: null,
  shoots: [],
  currentShootId: "",
  shootTargetId: "",
  contextItemId: "",
  shootViewId: "",
  focusId: "",
  focusZoom: 1,
  focusDrag: null,
  colorFilter: "",
  ratingFilter: "",
  ratingSort: "",
  panelDrag: null,
  cullSelections: new Set(),
  libraryName: "Palette Studio",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const panelStorageKeys = {
  "--sidebar-width": "palette-studio.sidebar-width.v2",
  "--inspector-width": "palette-studio.inspector-width.v2",
};

function iconRefresh() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatBytes(bytes = 0) {
  if (!bytes) return "0 KB";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function selectedItem() {
  return state.items.find((item) => item.id === state.selectedId) || state.items[0] || null;
}

function reviewQueue() {
  const items = state.shootViewId
    ? state.items.filter((item) => (item.shoot_assignments || {})[state.shootViewId])
    : state.items;
  return items.filter((item) => item.status === "unreviewed");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function loadConfig() {
  state.config = await api("/api/config");
  if (state.config.chrome_cookiefile) {
    $("#cookieFile").value = state.config.chrome_cookiefile;
  }
}

async function loadLibrary({ keepSelection = true } = {}) {
  const params = new URLSearchParams();
  params.set("filter", state.filter);
  if (state.query) params.set("q", state.query);
  if (state.handle) params.set("handle", state.handle);

  const data = await api(`/api/library?${params.toString()}`);
  state.items = data.items || [];
  state.stats = data.stats || { counts: {}, handles: [], tags: [] };
  if (!keepSelection || !state.items.some((item) => item.id === state.selectedId)) {
    state.selectedId = state.items[0]?.id || null;
  }
  render();
}

async function loadShoots() {
  const data = await api("/api/shoots");
  state.shoots = data.shoots || [];
  state.currentShootId = data.current_shoot_id || "";
  if (!state.shoots.some((shoot) => shoot.id === state.shootTargetId)) {
    state.shootTargetId = state.currentShootId;
  }
}

async function loadJobs() {
  const data = await api("/api/jobs");
  state.jobs = data.jobs || [];
  renderJobs();
  if (state.jobs.some((job) => job.state === "running")) {
    loadLibrary({ keepSelection: true }).catch(console.error);
  }
}

function render() {
  renderSidebar();
  renderMode();
  renderColorFilters();
  renderGrid();
  renderReview();
  renderFocus();
  renderInspector();
  iconRefresh();
}

function renderSidebar() {
  const counts = state.stats.counts || {};
  $("#libraryName").textContent = state.libraryName;
  $("#libraryMark").textContent = state.libraryName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase() || "PS";
  $("#librarySubtitle").textContent = `${counts.all || 0} assets`;
  $$("[data-count]").forEach((node) => {
    node.textContent = counts[node.dataset.count] || 0;
  });
  $$("#statusNav .nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.filter === state.filter);
  });
  $("#cullMode").classList.toggle("active", state.view === "review");

  const handles = state.stats.handles || [];
  $("#handleList").innerHTML =
    `<button class="handle-button ${state.handle ? "" : "active"}" data-handle="">
      <i data-lucide="folder"></i><span>All Handles</span><strong>${counts.all || 0}</strong>
    </button>` +
    handles
      .map(
        ([handle, count]) => `<button class="handle-button ${state.handle === handle ? "active" : ""}" data-handle="${escapeHtml(handle)}">
          <i data-lucide="folder"></i><span>${escapeHtml(handle)}</span><strong>${count}</strong>
        </button>`,
      )
      .join("");

  const shoots = state.shoots || [];
  $("#shootList").innerHTML = shoots
    .map(
      (shoot) => `<button class="shoot-button ${shoot.id === state.shootViewId ? "active" : ""}" data-shoot="${escapeHtml(shoot.id)}">
        <i data-lucide="clapperboard"></i><span>${escapeHtml(shoot.name)}</span><strong>${shoot.total || 0}</strong>
      </button>`,
    )
    .join("");
}

function renderMode() {
  const focusing = Boolean(state.focusId);
  $(".workspace").classList.toggle("focusing", focusing);
  $("#gridView").classList.toggle("active", state.view === "grid" && !focusing);
  $("#reviewView").classList.toggle("active", state.view === "review" && !focusing);
  $("#focusView").classList.toggle("active", focusing);
}

function swatches(colors = []) {
  return `<div class="swatch-row">${colors
    .slice(0, 5)
    .map((color) => `<span class="swatch" title="${escapeHtml(color)}" style="background:${escapeHtml(color)}"></span>`)
    .join("")}</div>`;
}

function toneBands(toneColors = {}) {
  const rows = [
    ["Highlights", toneColors.highlights || []],
    ["Midtones", toneColors.midtones || []],
    ["Shadows", toneColors.shadows || []],
  ];
  if (!rows.some(([, colors]) => colors.length)) return "";
  return `<div class="tone-palette">${rows
    .map(
      ([label, colors]) => `<div class="tone-row">
        <span class="tone-label">${escapeHtml(label)}</span>
        ${swatches(colors)}
      </div>`,
    )
    .join("")}</div>`;
}

function contextBands(contextColors = {}) {
  const rows = [
    ["Skin suggestion", contextColors.skin || []],
    ["Background", contextColors.background || []],
  ];
  if (!rows.some(([, colors]) => colors.length)) return "";
  return `<div class="context-palette">${rows
    .map(
      ([label, colors]) => `<div class="context-row">
        <span class="tone-label">${escapeHtml(label)}</span>
        ${colors.length ? swatches(colors) : `<span class="tone-label">No suggestion</span>`}
      </div>`,
    )
    .join("")}</div>`;
}

const colorFilters = [
  ["red", "Red", "#d9484a"],
  ["orange", "Orange", "#e8873c"],
  ["yellow", "Yellow", "#e7c653"],
  ["green", "Green", "#57a976"],
  ["cyan", "Cyan", "#4baec0"],
  ["blue", "Blue", "#5c83c9"],
  ["violet", "Violet", "#9973bd"],
  ["magenta", "Magenta", "#c76593"],
  ["neutral", "Neutral", "#85878b"],
];

function colorGroup(hex = "") {
  const match = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!match) return "neutral";
  const rgb = [1, 2, 3].map((index) => Number.parseInt(match[index], 16) / 255);
  const maximum = Math.max(...rgb);
  const minimum = Math.min(...rgb);
  const delta = maximum - minimum;
  const saturation = maximum === 0 ? 0 : delta / maximum;
  if (saturation < 0.17) return "neutral";
  let hue = 0;
  if (maximum === rgb[0]) hue = ((rgb[1] - rgb[2]) / delta + 6) % 6;
  else if (maximum === rgb[1]) hue = (rgb[2] - rgb[0]) / delta + 2;
  else hue = (rgb[0] - rgb[1]) / delta + 4;
  hue *= 60;
  if (hue < 18 || hue >= 340) return "red";
  if (hue < 48) return "orange";
  if (hue < 72) return "yellow";
  if (hue < 160) return "green";
  if (hue < 198) return "cyan";
  if (hue < 250) return "blue";
  if (hue < 292) return "violet";
  return "magenta";
}

function filteredItems(items = state.items) {
  const filtered = items.filter((item) => {
    const ratingMatches = !state.ratingFilter || String(item.rating || 0) === state.ratingFilter;
    const colorMatches = !state.colorFilter || (item.colors || []).some((color) => colorGroup(color) === state.colorFilter);
    return ratingMatches && colorMatches;
  });
  if (state.ratingSort === "high") return [...filtered].sort((a, b) => (b.rating || 0) - (a.rating || 0));
  if (state.ratingSort === "low") return [...filtered].sort((a, b) => (a.rating || 0) - (b.rating || 0));
  return filtered;
}

function renderColorFilters() {
  const bar = $("#colorFilterBar");
  bar.innerHTML = `<button class="color-filter-chip color-filter-all ${state.colorFilter ? "" : "active"}" data-color-filter="">All</button>` +
    colorFilters
      .map(
        ([key, label, color]) => `<button class="color-filter-chip ${state.colorFilter === key ? "active" : ""}" data-color-filter="${key}" title="${label}" aria-label="${label}">
          <span style="background:${color}"></span>
        </button>`,
      )
      .join("");
  $("#colorFilterToggle").classList.toggle("active", Boolean(state.colorFilter));
  $("#ratingFilterToggle").classList.toggle("active", Boolean(state.ratingFilter));
  $("#ratingFilter").value = state.ratingFilter;
  $("#ratingSort").value = state.ratingSort;
}

function closeFilterPopovers() {
  $("#colorPopover").hidden = true;
  $("#ratingPopover").hidden = true;
}

function toggleFilterPopover(name) {
  const target = name === "color" ? $("#colorPopover") : $("#ratingPopover");
  const other = name === "color" ? $("#ratingPopover") : $("#colorPopover");
  const isOpen = !target.hidden;
  other.hidden = true;
  target.hidden = isOpen;
}

function renderGrid() {
  const grid = $("#gridView");
  if (state.shootViewId) {
    renderShootGrid(grid);
    return;
  }
  const items = filteredItems();
  if (!items.length) {
    grid.innerHTML = `<div class="empty-state">No assets</div>`;
    return;
  }
  grid.innerHTML = items.map((item) => assetCard(item)).join("");
}

const shootCollectionMeta = [
  ["color", "Color", "palette"],
  ["lighting", "Lighting", "sun"],
  ["art_design", "Art Design", "paintbrush"],
  ["pose", "Pose", "person-standing"],
  ["reference", "Reference", "bookmark"],
];

function assetCard(item, inShoot = false) {
  return `<article class="asset-card ${inShoot ? "shoot-asset-card" : ""} ${item.id === state.selectedId ? "selected" : ""} ${escapeHtml(item.status)}" data-id="${escapeHtml(item.id)}">
    <button class="asset-open" data-id="${escapeHtml(item.id)}" title="Open image">
    <div class="asset-thumb-wrap">
      <img class="asset-thumb" src="${escapeHtml(item.thumb_url)}" alt="${escapeHtml(item.title)}" loading="lazy" />
    </div>
    <div class="asset-title">${escapeHtml(item.title || item.filename)}</div>
    <div class="asset-meta">${item.width || 0} x ${item.height || 0}</div>
    ${inShoot ? `<div class="asset-note">${escapeHtml(item.notes || "No note")}</div>` : ""}
    ${swatches(item.colors)}
    </button>
    <button class="asset-menu-button" data-asset-menu="${escapeHtml(item.id)}" title="Image options"><i data-lucide="ellipsis"></i></button>
  </article>`;
}

function renderShootGrid(grid) {
  const shoot = state.shoots.find((entry) => entry.id === state.shootViewId);
  if (!shoot) {
    state.shootViewId = "";
    renderGrid();
    return;
  }
  grid.innerHTML = shootCollectionMeta
    .map(([key, label, icon]) => {
      const assets = filteredItems(state.items.filter((item) => (item.shoot_assignments || {})[shoot.id] === key));
      return `<div class="shoot-group-heading"><i data-lucide="${icon}"></i><span>${escapeHtml(label)}</span><strong>${assets.length}</strong></div>${
        assets.length ? assets.map((item) => assetCard(item, true)).join("") : `<div class="shoot-group-empty">No images yet</div>`
      }`;
    })
    .join("");
}

function shootControls(item) {
  if (!state.shoots.length) return "";
  const selectedShootId = state.shootTargetId || state.currentShootId || state.shoots[0].id;
  const assignment = (item.shoot_assignments || {})[selectedShootId] || "";
  const selectedShoot = state.shoots.find((shoot) => shoot.id === selectedShootId);
  return `<div class="detail-section">
    <div class="detail-title">Add To Shoot</div>
    <select class="shoot-target" id="shootTarget" aria-label="Target shoot">
      ${state.shoots
        .map(
          (shoot) => `<option value="${escapeHtml(shoot.id)}" ${shoot.id === selectedShootId ? "selected" : ""}>${escapeHtml(shoot.name)}${shoot.id === state.currentShootId ? " (current)" : ""}</option>`,
        )
        .join("")}
    </select>
    <div class="shoot-actions">${shootCollectionMeta
      .map(
        ([key, label, icon]) => `<button class="shoot-action ${assignment === key ? "active" : ""}" data-shoot-collection="${key}" title="Add to ${escapeHtml(label)}">
          <i data-lucide="${icon}"></i><span>${escapeHtml(label)}</span>
        </button>`,
      )
      .join("")}</div>
    <div class="shoot-assignment">${assignment ? `In ${escapeHtml(selectedShoot?.name || "shoot")} - ${escapeHtml(shootCollectionMeta.find(([key]) => key === assignment)?.[1] || assignment)}` : "Choose a collection for this image."}</div>
  </div>`;
}

function removeFromShootControl(item) {
  if (!state.shootViewId || !(item.shoot_assignments || {})[state.shootViewId]) return "";
  return `<button class="remove-shoot-button" data-remove-from-shoot title="Remove from this shoot only">
    <i data-lucide="trash-2"></i><span>Remove from Shoot</span>
  </button>`;
}

function ratingStars(rating = 0) {
  return `<div class="rating-stars" role="group" aria-label="Rating">${[1, 2, 3, 4, 5]
    .map(
      (value) => `<button class="rating-star ${value <= rating ? "active" : ""}" data-rating="${value}" title="${value} star${value === 1 ? "" : "s"}">
        <i data-lucide="star"></i>
      </button>`,
    )
    .join("")}${rating ? `<button class="rating-clear" data-rating="0" title="Clear rating"><i data-lucide="x"></i></button>` : ""}</div>`;
}

function renderInspector() {
  const item = selectedItem();
  if (!item) {
    $("#inspector").innerHTML = `<div class="inspector-empty">No selection</div>`;
    return;
  }

  $("#inspector").innerHTML = `
    <div class="preview-wrap">
      <img src="${escapeHtml(item.media_url)}" alt="${escapeHtml(item.title)}" />
      <span class="file-type">${escapeHtml(item.type || "IMG")}</span>
    </div>
    ${swatches(item.colors)}
    ${toneBands(item.tone_colors)}
    ${contextBands(item.context_colors)}
    <input class="name-input" id="titleInput" value="${escapeHtml(item.title || "")}" />
    <textarea class="notes-input" id="notesInput" placeholder="Notes...">${escapeHtml(item.notes || "")}</textarea>
    <input class="url-input" value="${escapeHtml(item.source_url || "")}" readonly />
    <div class="detail-section rating-section">
      <div class="detail-title">Rating</div>
      ${ratingStars(item.rating || 0)}
    </div>
    ${shootControls(item)}
    ${removeFromShootControl(item)}
    <div class="detail-section">
      <div class="detail-title">Tags</div>
      <div class="tag-row">${(item.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
    </div>
    <div class="detail-section">
      <div class="detail-title">Properties</div>
      <dl class="meta-list">
        <div class="meta-row"><dt>Dimensions</dt><dd>${item.width || 0} x ${item.height || 0}</dd></div>
        <div class="meta-row"><dt>Size</dt><dd>${formatBytes(item.bytes)}</dd></div>
        <div class="meta-row"><dt>Type</dt><dd>${escapeHtml(item.type || "")}</dd></div>
        <div class="meta-row"><dt>Handle</dt><dd>${escapeHtml(item.handle || "")}</dd></div>
        <div class="meta-row"><dt>Post</dt><dd>${escapeHtml(item.post_code || "")}</dd></div>
      </dl>
    </div>`;
}

function cullItems() {
  const scoped = state.shootViewId
    ? state.items.filter((item) => (item.shoot_assignments || {})[state.shootViewId])
    : state.items;
  return filteredItems(scoped).filter((item) => item.status !== "reject");
}

function cullCard(item) {
  const selected = state.cullSelections.has(item.id);
  return `<button class="cull-card ${selected ? "selected" : ""}" data-cull-id="${escapeHtml(item.id)}" aria-pressed="${selected}">
    <div class="cull-thumb-wrap"><img src="${escapeHtml(item.thumb_url)}" alt="${escapeHtml(item.title)}" loading="lazy" />
      <span class="cull-check"><i data-lucide="x"></i></span>
    </div>
    <span class="cull-title">${escapeHtml(item.title || item.filename)}</span>
  </button>`;
}

function renderReview() {
  if (state.view !== "review") {
    $("#cullGrid").innerHTML = "";
    return;
  }
  const items = cullItems();
  const validIds = new Set(items.map((item) => item.id));
  state.cullSelections = new Set([...state.cullSelections].filter((id) => validIds.has(id)));
  const count = state.cullSelections.size;
  $("#reviewMeta").textContent = count ? `${count} selected for rejection` : `${items.length} images. Click any image to mark it for rejection.`;
  $("#confirmReject").disabled = count === 0;
  $("#confirmRejectLabel").textContent = count ? `Confirm reject (${count})` : "Confirm reject";
  $("#cullGrid").innerHTML = items.length ? items.map(cullCard).join("") : `<div class="empty-state">No images available</div>`;
}

function focusItems() {
  if (!state.shootViewId) return filteredItems();
  return filteredItems(state.items.filter((item) => (item.shoot_assignments || {})[state.shootViewId]));
}

function applyFocusZoom() {
  const stage = $("#focusStage");
  const image = $("#focusImage");
  if (!stage || !image?.naturalWidth || !image?.naturalHeight) return;
  const fit = Math.min(
    (stage.clientWidth - 48) / image.naturalWidth,
    (stage.clientHeight - 48) / image.naturalHeight,
    1,
  );
  image.style.width = `${Math.max(1, image.naturalWidth * fit * state.focusZoom)}px`;
  image.style.height = `${Math.max(1, image.naturalHeight * fit * state.focusZoom)}px`;
}

function beginFocusDrag(event) {
  const stage = event.target.closest("#focusStage");
  if (!stage || event.button !== 0) return;
  state.focusDrag = {
    stage,
    startX: event.clientX,
    startY: event.clientY,
    left: stage.scrollLeft,
    top: stage.scrollTop,
  };
  stage.setPointerCapture?.(event.pointerId);
  stage.classList.add("dragging");
}

function moveFocusDrag(event) {
  const drag = state.focusDrag;
  if (!drag) return false;
  drag.stage.scrollLeft = drag.left - (event.clientX - drag.startX);
  drag.stage.scrollTop = drag.top - (event.clientY - drag.startY);
  event.preventDefault();
  return true;
}

function endFocusDrag() {
  if (!state.focusDrag) return false;
  state.focusDrag.stage.classList.remove("dragging");
  state.focusDrag = null;
  return true;
}

function beginPanelResize(event) {
  const side = event.currentTarget.dataset.resize;
  const panel = side === "sidebar" ? $(".sidebar") : $("#inspector");
  state.panelDrag = {
    side,
    handle: event.currentTarget,
    startX: event.clientX,
    width: panel.getBoundingClientRect().width,
  };
  event.currentTarget.classList.add("dragging");
  event.currentTarget.setPointerCapture?.(event.pointerId);
}

function movePanelResize(event) {
  const drag = state.panelDrag;
  if (!drag) return false;
  const delta = event.clientX - drag.startX;
  const next = drag.side === "sidebar" ? drag.width + delta : drag.width - delta;
  const width = Math.max(drag.side === "sidebar" ? 180 : 260, Math.min(drag.side === "sidebar" ? 360 : 460, next));
  const variable = drag.side === "sidebar" ? "--sidebar-width" : "--inspector-width";
  document.documentElement.style.setProperty(variable, `${width}px`);
  return true;
}

function endPanelResize() {
  if (!state.panelDrag) return false;
  const drag = state.panelDrag;
  drag.handle.classList.remove("dragging");
  const variable = drag.side === "sidebar" ? "--sidebar-width" : "--inspector-width";
  localStorage.setItem(panelStorageKeys[variable], getComputedStyle(document.documentElement).getPropertyValue(variable).trim());
  state.panelDrag = null;
  return true;
}

function renderFocus() {
  const focusView = $("#focusView");
  const items = focusItems();
  const currentIndex = items.findIndex((item) => item.id === state.focusId);
  const item = currentIndex >= 0 ? items[currentIndex] : null;
  if (!state.focusId || !item) {
    focusView.innerHTML = "";
    return;
  }
  $("#focusCount").textContent = `${currentIndex + 1} / ${items.length}`;
  $("#focusZoomInput").value = String(Math.round(state.focusZoom * 100));
  $("#focusZoomValue").textContent = `${Math.round(state.focusZoom * 100)}%`;
  $("#focusDecision").value = item.status || "unreviewed";
  $("#focusPrevious").disabled = currentIndex <= 0;
  $("#focusNext").disabled = currentIndex >= items.length - 1;
  focusView.innerHTML = `<div class="focus-stage" id="focusStage"><img id="focusImage" src="${escapeHtml(item.media_url)}" alt="${escapeHtml(item.title || item.filename)}" /></div>`;
  const image = $("#focusImage");
  image.addEventListener("load", applyFocusZoom, { once: true });
  if (image.complete) requestAnimationFrame(applyFocusZoom);
}

function renderJobs() {
  const activeJobs = state.jobs.filter((job) => job.state === "running" || job.state === "error").slice(0, 2);
  const strip = $("#jobStrip");
  if (!activeJobs.length) {
    strip.classList.remove("active");
    strip.textContent = "";
    return;
  }
  strip.classList.add("active");
  strip.textContent = activeJobs
    .map((job) => {
      const total = job.total ? `/${job.total}` : "";
      const tail = job.state === "error" ? `Error: ${job.error}` : job.message || job.kind;
      return `${job.target}: ${job.done}${total} ${tail}`;
    })
    .join("  |  ");
}

function updateCounts(previousStatus, nextStatus) {
  if (!previousStatus || !nextStatus || previousStatus === nextStatus) return;
  const counts = state.stats.counts || {};
  counts[previousStatus] = Math.max(0, (counts[previousStatus] || 0) - 1);
  counts[nextStatus] = (counts[nextStatus] || 0) + 1;
  if (previousStatus === "unreviewed" && nextStatus !== "unreviewed") {
    counts.reviewed = (counts.reviewed || 0) + 1;
  }
  if (previousStatus !== "unreviewed" && nextStatus === "unreviewed") {
    counts.reviewed = Math.max(0, (counts.reviewed || 0) - 1);
  }
}

function itemMatchesCurrentFilter(item) {
  if (!item) return false;
  if (state.filter === "all") return true;
  if (state.filter === "reviewed") return item.status !== "unreviewed";
  return item.status === state.filter;
}

async function patchItem(id, patch) {
  const previous = state.items.find((asset) => asset.id === id);
  const data = await api(`/api/items/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  updateCounts(previous?.status, data.item.status);
  const index = state.items.findIndex((asset) => asset.id === id);
  if (index >= 0) state.items[index] = data.item;
  return data.item;
}

async function patchSelected(patch) {
  const item = selectedItem();
  if (!item) return null;
  return patchItem(item.id, patch);
}

async function setCurrentShoot(shootId, showCollections = false) {
  if (showCollections) {
    state.focusId = "";
    state.shootViewId = shootId;
    state.filter = "all";
    state.handle = "";
    state.query = "";
    state.view = "grid";
    state.cullSelections.clear();
    render();
  }
  const data = await api(`/api/shoots/${shootId}`, {
    method: "PATCH",
    body: JSON.stringify({ current: true }),
  });
  state.shoots = data.shoots || state.shoots;
  state.currentShootId = data.current_shoot_id || shootId;
  state.shootTargetId = state.currentShootId;
  if (showCollections) {
    state.shootViewId = shootId;
    await loadLibrary({ keepSelection: true });
    return;
  }
  render();
}

async function createShoot() {
  const name = $("#shootName").value.trim();
  if (!name) return;
  const data = await api("/api/shoots", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  state.shoots = data.shoots || state.shoots;
  state.currentShootId = data.current_shoot_id || data.shoot?.id || "";
  state.shootTargetId = state.currentShootId;
  $("#shootDialog").close();
  render();
}

async function assignSelectedToShoot(collection) {
  const item = selectedItem();
  const shootId = $("#shootTarget")?.value || state.shootTargetId || state.currentShootId;
  if (!item) return;
  await assignItemToShoot(item.id, collection, shootId);
}

async function assignItemToShoot(itemId, collection, shootId = state.currentShootId) {
  if (!itemId || !shootId) return;
  const data = await api(`/api/items/${itemId}/shoot-assignment`, {
    method: "POST",
    body: JSON.stringify({ shoot_id: shootId, collection }),
  });
  state.shoots = data.shoots || state.shoots;
  state.currentShootId = data.current_shoot_id || state.currentShootId;
  state.shootTargetId = shootId;
  const index = state.items.findIndex((asset) => asset.id === itemId);
  if (index >= 0) state.items[index] = data.item;
  hideAssetMenu();
  render();
}

async function removeItemFromShoot(itemId, shootId = state.shootViewId, shouldRender = true) {
  if (!itemId || !shootId) return null;
  const data = await api(`/api/items/${itemId}/shoot-assignment?shoot_id=${encodeURIComponent(shootId)}`, {
    method: "DELETE",
  });
  state.shoots = data.shoots || state.shoots;
  state.currentShootId = data.current_shoot_id || state.currentShootId;
  const index = state.items.findIndex((asset) => asset.id === itemId);
  if (index >= 0) state.items[index] = data.item;
  if (shouldRender) render();
  return data.item;
}

async function setStatus(status) {
  const item = selectedItem();
  const updated = await patchSelected({ status });
  if (status === "reject" && item && state.shootViewId) {
    await removeItemFromShoot(item.id, state.shootViewId, false);
    if (state.focusId === item.id) state.focusId = "";
  }
  if (updated && !itemMatchesCurrentFilter(updated)) {
    state.items = state.items.filter((item) => item.id !== updated.id);
    state.selectedId = state.items[0]?.id || null;
  }
  render();
}

async function rejectItem(itemId) {
  if (!itemId) return;
  state.selectedId = itemId;
  await setStatus("reject");
}

async function confirmCullReject() {
  const ids = [...state.cullSelections];
  if (!ids.length) return;
  const confirmed = window.confirm(`Reject ${ids.length} selected image${ids.length === 1 ? "" : "s"}?`);
  if (!confirmed) return;
  const button = $("#confirmReject");
  button.disabled = true;
  $("#confirmRejectLabel").textContent = "Rejecting...";
  try {
    for (const id of ids) {
      await patchItem(id, { status: "reject" });
      if (state.shootViewId) await removeItemFromShoot(id, state.shootViewId, false);
    }
    state.cullSelections.clear();
    render();
  } catch (error) {
    render();
    throw error;
  }
}

function selectItem(id) {
  state.selectedId = id;
  renderGrid();
  renderReview();
  renderInspector();
  iconRefresh();
}

function openImageFocus(id) {
  const item = state.items.find((asset) => asset.id === id);
  if (!item) return;
  state.selectedId = id;
  state.focusId = id;
  state.focusZoom = 1;
  render();
}

function closeImageFocus() {
  state.focusId = "";
  render();
}

function stepFocus(direction) {
  const items = focusItems();
  const index = items.findIndex((item) => item.id === state.focusId);
  const next = items[index + direction];
  if (!next) return;
  state.focusId = next.id;
  state.selectedId = next.id;
  state.focusZoom = 1;
  render();
}

function hideAssetMenu() {
  const menu = $("#assetMenu");
  menu.hidden = true;
  menu.innerHTML = "";
  state.contextItemId = "";
}

function showAssetMenu(id, x, y) {
  const item = state.items.find((asset) => asset.id === id);
  if (!item) return;
  state.selectedId = id;
  state.contextItemId = id;
  const menu = $("#assetMenu");
  const currentShootId = state.currentShootId || state.shoots[0]?.id || "";
  const shootOptions = state.shoots.length
    ? `<div class="asset-menu-title">Add to shoot</div>
      <select class="asset-menu-target" id="assetMenuTarget" aria-label="Target shoot">
        ${state.shoots
          .map(
            (shoot) => `<option value="${escapeHtml(shoot.id)}" ${shoot.id === currentShootId ? "selected" : ""}>${escapeHtml(shoot.name)}${shoot.id === state.currentShootId ? " (current)" : ""}</option>`,
          )
          .join("")}
      </select>
      <div class="asset-menu-actions">${shootCollectionMeta
        .map(
          ([key, label, icon]) => `<button class="asset-menu-action" data-menu-collection="${key}"><i data-lucide="${icon}"></i><span>${escapeHtml(label)}</span></button>`,
        )
        .join("")}</div>`
    : "";
  menu.innerHTML = `${shootOptions}<div class="asset-menu-divider"></div><button class="asset-menu-action asset-menu-reject" data-menu-reject><i data-lucide="x"></i><span>Reject image</span></button>`;
  menu.hidden = false;
  const maxX = Math.max(10, window.innerWidth - menu.offsetWidth - 10);
  const maxY = Math.max(10, window.innerHeight - menu.offsetHeight - 10);
  menu.style.left = `${Math.min(x, maxX)}px`;
  menu.style.top = `${Math.min(y, maxY)}px`;
  renderGrid();
  renderInspector();
  iconRefresh();
}

function openImportDialog() {
  const handle = state.handle || "elizavetaporodina";
  $("#importHandle").value = handle ? `https://www.instagram.com/${handle}/` : "";
  $("#folderPath").value = `downloads/${handle}/originals`;
  if (state.config.chrome_cookiefile) $("#cookieFile").value = state.config.chrome_cookiefile;
  $("#importDialog").showModal();
}

function openShootDialog() {
  const nextNumber = state.shoots.length + 1;
  $("#shootName").value = `Shoot ${nextNumber}`;
  $("#shootDialog").showModal();
  $("#shootName").focus();
}

async function startFolderScan() {
  const handle = $("#importHandle").value.trim();
  const path = $("#folderPath").value.trim();
  await api("/api/import-folder", {
    method: "POST",
    body: JSON.stringify({ handle, path }),
  });
  $("#importDialog").close();
  await loadJobs();
}

async function startInstagramImport(profileValue = null) {
  const maxPages = $("#importPages").value.trim();
  await api("/api/import-instagram", {
    method: "POST",
    body: JSON.stringify({
      profile: (profileValue || $("#importHandle").value).trim(),
      browser: $("#importBrowser").value,
      cookiefile: $("#cookieFile").value.trim(),
      max_pages: maxPages ? Number(maxPages) : null,
    }),
  });
  if ($("#importDialog").open) $("#importDialog").close();
  await loadJobs();
}

function bindEvents() {
  $("#libraryName").addEventListener("click", () => {
    const name = window.prompt("Rename library", state.libraryName);
    if (name === null) return;
    const nextName = name.trim();
    if (!nextName) return;
    state.libraryName = nextName;
    localStorage.setItem("palette-studio.library-name", nextName);
    document.title = nextName;
    renderSidebar();
  });

  $("#statusNav").addEventListener("click", (event) => {
    const button = event.target.closest("[data-filter]");
    if (!button) return;
    state.focusId = "";
    state.shootViewId = "";
    state.view = "grid";
    state.cullSelections.clear();
    state.filter = button.dataset.filter;
    loadLibrary({ keepSelection: false }).catch(console.error);
  });

  $("#handleList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-handle]");
    if (!button) return;
    state.focusId = "";
    state.shootViewId = "";
    state.view = "grid";
    state.cullSelections.clear();
    state.handle = button.dataset.handle;
    loadLibrary({ keepSelection: false }).catch(console.error);
  });

  $("#shootList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-shoot]");
    if (button) setCurrentShoot(button.dataset.shoot, true).catch((error) => alert(error.message));
  });

  $("#cullMode").addEventListener("click", () => {
    state.focusId = "";
    state.cullSelections.clear();
    state.view = "review";
    render();
  });

  $("#gridView").addEventListener("click", (event) => {
    const menuButton = event.target.closest("[data-asset-menu]");
    if (menuButton) {
      event.preventDefault();
      const rect = menuButton.getBoundingClientRect();
      showAssetMenu(menuButton.dataset.assetMenu, rect.right, rect.bottom);
      return;
    }
    const card = event.target.closest(".asset-open");
    if (card) openImageFocus(card.dataset.id);
  });

  $("#gridView").addEventListener("contextmenu", (event) => {
    const card = event.target.closest(".asset-card");
    if (!card) return;
    event.preventDefault();
    showAssetMenu(card.dataset.id, event.clientX, event.clientY);
  });

  $("#ratingFilter").addEventListener("change", (event) => {
    state.ratingFilter = event.target.value;
    closeFilterPopovers();
    render();
  });
  $("#ratingSort").addEventListener("change", (event) => {
    state.ratingSort = event.target.value;
    render();
  });
  $("#colorFilterBar").addEventListener("click", (event) => {
    const button = event.target.closest("[data-color-filter]");
    if (!button) return;
    state.colorFilter = button.dataset.colorFilter;
    closeFilterPopovers();
    render();
  });
  $("#colorFilterToggle").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleFilterPopover("color");
  });
  $("#ratingFilterToggle").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleFilterPopover("rating");
  });
  $$("[data-close-filter]").forEach((button) => button.addEventListener("click", closeFilterPopovers));

  $("#openImport").addEventListener("click", openImportDialog);
  $("#newShoot").addEventListener("click", openShootDialog);
  $("#createShoot").addEventListener("click", (event) => {
    event.preventDefault();
    createShoot().catch((error) => alert(error.message));
  });
  $("#shootDialog").addEventListener("submit", (event) => {
    event.preventDefault();
    createShoot().catch((error) => alert(error.message));
  });
  $("#scanFolder").addEventListener("click", () => startFolderScan().catch((error) => alert(error.message)));
  $("#startImport").addEventListener("click", () => startInstagramImport().catch((error) => alert(error.message)));

  $("#cullGrid").addEventListener("click", (event) => {
    const card = event.target.closest("[data-cull-id]");
    if (!card) return;
    const id = card.dataset.cullId;
    if (state.cullSelections.has(id)) state.cullSelections.delete(id);
    else state.cullSelections.add(id);
    renderReview();
    iconRefresh();
  });
  $("#cancelCull").addEventListener("click", () => {
    state.cullSelections.clear();
    state.view = "grid";
    render();
  });
  $("#confirmReject").addEventListener("click", () => confirmCullReject().catch((error) => alert(error.message)));

  $("#inspector").addEventListener("click", (event) => {
    const ratingButton = event.target.closest("[data-rating]");
    if (ratingButton) {
      const item = selectedItem();
      const requestedRating = Number(ratingButton.dataset.rating);
      const rating = requestedRating && requestedRating === (item?.rating || 0) ? 0 : requestedRating;
      patchSelected({ rating }).then(render).catch(console.error);
    }
    const shootButton = event.target.closest("[data-shoot-collection]");
    if (shootButton) assignSelectedToShoot(shootButton.dataset.shootCollection).catch((error) => alert(error.message));
    if (event.target.closest("[data-remove-from-shoot]")) {
      const item = selectedItem();
      if (item) removeItemFromShoot(item.id).catch((error) => alert(error.message));
    }
  });

  $("#inspector").addEventListener("change", (event) => {
    if (event.target.id !== "shootTarget") return;
    state.shootTargetId = event.target.value;
    renderInspector();
    iconRefresh();
  });

  $("#assetMenu").addEventListener("click", (event) => {
    event.stopPropagation();
    if (event.target.closest("[data-menu-reject]")) {
      rejectItem(state.contextItemId).then(hideAssetMenu).catch((error) => alert(error.message));
      return;
    }
    const action = event.target.closest("[data-menu-collection]");
    if (!action) return;
    const shootId = $("#assetMenuTarget")?.value || state.currentShootId;
    assignItemToShoot(state.contextItemId, action.dataset.menuCollection, shootId).catch((error) => alert(error.message));
  });

  $("#assetMenu").addEventListener("contextmenu", (event) => event.preventDefault());
  $("#exitFocus").addEventListener("click", closeImageFocus);
  $("#focusPrevious").addEventListener("click", () => stepFocus(-1));
  $("#focusNext").addEventListener("click", () => stepFocus(1));
  $("#focusZoomInput").addEventListener("input", (event) => {
    state.focusZoom = Number(event.target.value) / 100;
    $("#focusZoomValue").textContent = `${event.target.value}%`;
    applyFocusZoom();
  });
  $("#focusDecision").addEventListener("change", (event) => {
    if (event.target.value !== "unreviewed") setStatus(event.target.value).catch((error) => alert(error.message));
  });
  window.addEventListener("resize", applyFocusZoom);
  window.addEventListener("click", (event) => {
    if (!event.target.closest("#assetMenu")) hideAssetMenu();
    if (!event.target.closest(".filter-popover") && !event.target.closest(".library-filter-actions")) closeFilterPopovers();
  });

  $("#inspector").addEventListener("input", (event) => {
    if (event.target.id === "notesInput") {
      const value = event.target.value;
      const item = selectedItem();
      if (item) item.notes = value;
      clearTimeout(state.noteTimer);
      const itemId = item?.id;
      state.noteTimer = setTimeout(() => {
        if (itemId) patchItem(itemId, { notes: value }).catch(console.error);
      }, 450);
    }
    if (event.target.id === "titleInput") {
      const value = event.target.value;
      const item = selectedItem();
      if (item) item.title = value;
      clearTimeout(state.titleTimer);
      const itemId = item?.id;
      state.titleTimer = setTimeout(() => {
        if (itemId) patchItem(itemId, { title: value }).catch(console.error);
      }, 450);
      const active = selectedItem();
      if (active) {
        const title = $(`.asset-card[data-id="${CSS.escape(active.id)}"] .asset-title`);
        if (title) title.textContent = value;
      }
    }
  });

  $("#focusView").addEventListener("pointerdown", beginFocusDrag);
  $("#sidebarResize").addEventListener("pointerdown", beginPanelResize);
  $("#inspectorResize").addEventListener("pointerdown", beginPanelResize);
  window.addEventListener("pointermove", (event) => {
    if (!movePanelResize(event) && !moveFocusDrag(event)) moveDrag(event);
  });
  window.addEventListener("pointerup", () => {
    if (!endPanelResize() && !endFocusDrag()) endDrag();
  });

  window.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select")) return;
    if (state.focusId) {
      if (event.key === "Escape") closeImageFocus();
      if (event.key === "ArrowRight") stepFocus(1);
      if (event.key === "ArrowLeft") stepFocus(-1);
      return;
    }
    if (event.key === "Escape") hideAssetMenu();
  });
}

function beginDrag(event) {
  const card = event.target.closest("#swipeCard");
  if (!card) return;
  state.dragging = {
    card,
    startX: event.clientX,
    startY: event.clientY,
    dx: 0,
    dy: 0,
  };
  card.setPointerCapture?.(event.pointerId);
}

function moveDrag(event) {
  if (!state.dragging) return;
  const drag = state.dragging;
  drag.dx = event.clientX - drag.startX;
  drag.dy = event.clientY - drag.startY;
  const rotate = Math.max(-12, Math.min(12, drag.dx / 18));
  drag.card.style.transform = `translate(${drag.dx}px, ${drag.dy}px) rotate(${rotate}deg)`;
}

function endDrag() {
  if (!state.dragging) return;
  const drag = state.dragging;
  state.dragging = null;
  if (drag.dx > 110) {
    drag.card.style.transform = "translateX(110vw) rotate(12deg)";
    setTimeout(() => setStatus("keep").catch(console.error), 120);
    return;
  }
  if (drag.dx < -110) {
    drag.card.style.transform = "translateX(-110vw) rotate(-12deg)";
    setTimeout(() => setStatus("reject").catch(console.error), 120);
    return;
  }
  drag.card.style.transform = "";
}

async function init() {
  state.libraryName = localStorage.getItem("palette-studio.library-name") || "Palette Studio";
  document.title = state.libraryName;
  ["--sidebar-width", "--inspector-width"].forEach((variable) => {
    const width = localStorage.getItem(panelStorageKeys[variable]);
    if (width) document.documentElement.style.setProperty(variable, width);
  });
  bindEvents();
  await loadConfig().catch(console.error);
  await loadShoots().catch(console.error);
  await loadLibrary({ keepSelection: false }).catch(console.error);
  await loadJobs().catch(console.error);
  setInterval(() => loadJobs().catch(console.error), 2200);
  iconRefresh();
}

document.addEventListener("DOMContentLoaded", init);
