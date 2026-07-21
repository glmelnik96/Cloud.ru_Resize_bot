/* App3 / webinar — variant + texts + one hero, manually fitted in a reference
   frame (рамка + сетка + линия головы), then a LangGraph-free compose loop.
   The fit is a Transform(scale, x, y) in reference-frame units, sent verbatim
   to the server (infra.hero_fit.bake_frame reproduces it exactly). */
(function () {
  "use strict";
  const P = window.APP_PREFIX || "";
  const $ = (id) => document.getElementById(id);
  const show = (el) => { el.classList.remove("hidden"); updateEmpty(); };
  const hide = (el) => { el.classList.add("hidden"); updateEmpty(); };

  const OUTPUT_PANELS = ["progressPanel", "resultsPanel", "tasksPanel"];
  function updateEmpty() {
    const empty = $("emptyState");
    if (!empty) return;
    const hasContent = OUTPUT_PANELS.some((id) => {
      const p = $(id);
      return p && !p.classList.contains("hidden");
    });
    empty.classList.toggle("hidden", hasContent);
  }

  const ALPHA_THRESHOLD = 32;
  const HEAD_LINE_FRAC = 0.06; // where the top of a speaker's head should sit
  const SLOT_LABELS = {
    title: "Заголовок", subtitle: "Подзаголовок",
    name: "Имя спикера", position: "Должность",
    date: "Дата", time: "Время",
  };
  const SLOT_PH = {
    title: "Тема вебинара", subtitle: "Короткий подзаголовок",
    name: "Михаил Безобразов", position: "архитектор решений",
    date: "08 сентября", time: "11:00",
  };

  let META = null;         // { speaker:{frame,box,mode,anchor_v,slots,formats}, visual:{…} }
  let variant = "speaker";
  let hero = null;         // HTMLImageElement (uploaded)
  let heroBlob = null;     // File/Blob to POST
  let alphaBox = null;     // {x0,y0,x1,y1} in hero px (threshold 32)
  const T = { scale: 1, x: 0, y: 0 }; // Transform in ref-frame units

  let taskUid = null, es = null;
  const LS_KEY = "app3_webinar_task";
  const ACTIVE = ["queued", "running"];
  const saveActive = (uid) => { try { localStorage.setItem(LS_KEY, uid); } catch (_) {} };
  const clearActive = () => { try { localStorage.removeItem(LS_KEY); } catch (_) {} };

  // Per-task result PNGs (out of the DOM until a row is expanded), shared by the
  // just-finished grid and the recent-tasks grids so the lightbox can page them.
  const imagesByUid = {};
  const fileNameOf = (u) => String(u).split("/").pop();

  const canvas = $("fitCanvas");
  const ctx = canvas.getContext("2d");

  // ── meta + variant ─────────────────────────────────────
  async function loadMeta() {
    try {
      const r = await fetch(`${P}/api/webinar/meta`);
      if (!r.ok) return;
      META = await r.json();
    } catch (_) { /* leave META null; submit will surface the error */ }
    renderVariant();
  }

  $("variantSeg").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".seg__btn");
    if (!btn) return;
    variant = btn.dataset.variant;
    document.querySelectorAll("#variantSeg .seg__btn").forEach((b) =>
      b.classList.toggle("is-active", b === btn));
    renderVariant();
    if (hero && fitOn()) { alphaBox = computeAlphaBox(hero); resetTransform(); drawStage(); }
  });

  function meta() { return (META && META[variant]) || null; }
  // Whether the hand-fit canvas applies. Speaker: yes. Visual (metaphor): no —
  // the generated full-bleed metaphor is positioned by each format itself.
  function fitOn() { const m = meta(); return m ? !!m.fit : variant === "speaker"; }

  function renderVariant() {
    const m = meta();
    const slots = (m && m.slots) || ["title", "date", "time"];
    $("slotFields").innerHTML = slots.map((s) =>
      `<label for="slot_${s}">${SLOT_LABELS[s] || s}</label>` +
      `<input type="text" id="slot_${s}" data-slot="${s}" placeholder="${SLOT_PH[s] || ""}">`
    ).join("");
    const n = m ? m.formats : "";
    $("variantHint").textContent = variant === "speaker"
      ? `Фото спикера. Соберём ${n} форматов семейства «спикер».`
      : `Рендер-метафора (без фона). Соберём ${n} форматов семейства «метафора».`;
    $("fitHint").textContent = fitOn()
      ? "Выровняй лицо: линия сверху — где должна быть макушка. Перетаскивай — двигать, колесо — масштаб."
      : "Метафора генерируется под квадрат и впишется в каждый формат автоматически — кадрировать не нужно.";
    $("fitPanel").querySelector("h2").textContent = fitOn()
      ? "03 · Изображение и кадрирование"
      : "03 · Изображение";
    // Toggle the fit canvas to match the variant even if a hero is loaded.
    if (hero && fitOn()) show($("fitStage")); else hide($("fitStage"));
    // Metaphor variant: offer prompt-based generation when App1 is wired.
    if (canGenerate()) show($("promptRow")); else hide($("promptRow"));
    updateStartBtn();
  }

  // Whether this variant can generate its hero from a prompt (App1 wired).
  function canGenerate() { const m = meta(); return !!(m && m.can_generate); }
  const promptText = () => (($("metaphorPrompt") || {}).value || "").trim();

  // Build is allowed when there's something to compose from: an uploaded hero,
  // or (metaphor) a prompt to generate one.
  function updateStartBtn() {
    const ok = !!heroBlob || (canGenerate() && promptText().length > 0);
    $("startBtn").disabled = !ok;
  }

  // ── hero upload ────────────────────────────────────────
  $("heroFile").addEventListener("change", (ev) => {
    const f = ev.target.files[0];
    ev.target.value = "";
    if (!f) return;
    heroBlob = f;
    const img = new Image();
    img.onload = () => {
      hero = img;
      updateStartBtn();
      if (!fitOn()) { hide($("fitStage")); return; }
      alphaBox = computeAlphaBox(img);
      show($("fitStage"));
      resetTransform();
      drawStage();
    };
    img.onerror = () => { $("fitStatus").innerHTML = `<span class="err">Не удалось открыть изображение.</span>`; };
    img.src = URL.createObjectURL(f);
  });

  // Metaphor prompt: typing a prompt is enough to build (no upload required).
  const _promptEl = $("metaphorPrompt");
  if (_promptEl) _promptEl.addEventListener("input", updateStartBtn);

  // Alpha bbox at threshold 32 (mirrors infra.hero_fit._alpha_bbox). Opaque
  // images (JPEG speaker photos) → full-frame box.
  function computeAlphaBox(img) {
    const w = img.naturalWidth, h = img.naturalHeight;
    const off = document.createElement("canvas");
    off.width = w; off.height = h;
    const octx = off.getContext("2d");
    octx.drawImage(img, 0, 0);
    let data;
    try { data = octx.getImageData(0, 0, w, h).data; }
    catch (_) { return { x0: 0, y0: 0, x1: w, y1: h }; }
    let x0 = w, y0 = h, x1 = 0, y1 = 0, found = false;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (data[(y * w + x) * 4 + 3] >= ALPHA_THRESHOLD) {
          found = true;
          if (x < x0) x0 = x; if (x > x1) x1 = x;
          if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
      }
    }
    if (!found) return { x0: 0, y0: 0, x1: w, y1: h };
    return { x0, y0, x1: x1 + 1, y1: y1 + 1 };
  }

  // Server default placement (mirrors infra.hero_fit.auto_transform) so the
  // canvas opens on the same framing the server would pick if untouched.
  function resetTransform() {
    const m = meta();
    if (!hero || !m) return;
    const [bx0, by0, bx1, by1] = m.box;
    const boxW = bx1 - bx0, boxH = by1 - by0;
    const b = alphaBox || { x0: 0, y0: 0, x1: hero.naturalWidth, y1: hero.naturalHeight };
    const objW = b.x1 - b.x0, objH = b.y1 - b.y0;
    const sx = boxW / objW, sy = boxH / objH;
    const scale = m.mode === "contain" ? Math.min(sx, sy) : Math.max(sx, sy);
    T.scale = scale;
    T.x = bx0 + (boxW - objW * scale) / 2 - b.x0 * scale;
    T.y = m.anchor_v === "top"
      ? by0 - b.y0 * scale
      : by0 + (boxH - objH * scale) / 2 - b.y0 * scale;
    syncZoom();
  }
  $("fitReset").addEventListener("click", () => { resetTransform(); drawStage(); });

  // ── canvas draw ────────────────────────────────────────
  // Canvas shows the whole reference frame; D maps ref units → display px.
  function dispScale() {
    const m = meta();
    if (!m) return 1;
    const [rw, rh] = m.frame;
    return Math.min(canvas.width / rw, canvas.height / rh);
  }
  function frameOrigin(D) {
    const m = meta();
    const [rw, rh] = m.frame;
    return { ox: (canvas.width - rw * D) / 2, oy: (canvas.height - rh * D) / 2 };
  }

  function drawStage() {
    const m = meta();
    if (!hero || !m) return;
    const [rw, rh] = m.frame;
    const D = dispScale();
    const { ox, oy } = frameOrigin(D);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // checkerboard behind the frame (so transparent PNGs read as transparent)
    drawChecker(ox, oy, rw * D, rh * D);

    // the hero, placed per Transform (ref units → display)
    ctx.save();
    ctx.beginPath();
    ctx.rect(ox, oy, rw * D, rh * D);
    ctx.clip();
    const hw = hero.naturalWidth * T.scale * D;
    const hh = hero.naturalHeight * T.scale * D;
    ctx.drawImage(hero, ox + T.x * D, oy + T.y * D, hw, hh);
    ctx.restore();

    drawGuides(ox, oy, rw * D, rh * D);
  }

  function drawChecker(x, y, w, h) {
    const s = 10;
    for (let j = 0; j * s < h; j++) {
      for (let i = 0; i * s < w; i++) {
        ctx.fillStyle = (i + j) % 2 ? "#e9edf2" : "#f7f9fb";
        ctx.fillRect(x + i * s, y + j * s, Math.min(s, w - i * s), Math.min(s, h - j * s));
      }
    }
  }

  function drawGuides(x, y, w, h) {
    // grid: thirds
    ctx.strokeStyle = "rgba(40,50,70,0.18)";
    ctx.lineWidth = 1;
    for (let k = 1; k < 3; k++) {
      const gx = x + (w * k) / 3, gy = y + (h * k) / 3;
      line(gx, y, gx, y + h);
      line(x, gy, x + w, gy);
    }
    // head line (speaker only)
    if (variant === "speaker") {
      ctx.strokeStyle = "rgba(38,208,124,0.9)";
      ctx.lineWidth = 1.5;
      const hy = y + h * HEAD_LINE_FRAC;
      line(x, hy, x + w, hy);
    }
    // frame outline
    ctx.strokeStyle = "#26D07C";
    ctx.lineWidth = 2;
    ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
  }
  function line(x0, y0, x1, y1) {
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
  }

  // ── interaction: drag to pan, wheel to zoom ────────────
  let dragging = false, lastX = 0, lastY = 0;
  canvas.addEventListener("pointerdown", (ev) => {
    if (!hero) return;
    dragging = true; lastX = ev.clientX; lastY = ev.clientY;
    canvas.setPointerCapture(ev.pointerId);
  });
  canvas.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    const D = dispScale();
    T.x += (ev.clientX - lastX) / D;
    T.y += (ev.clientY - lastY) / D;
    lastX = ev.clientX; lastY = ev.clientY;
    drawStage();
  });
  const endDrag = () => { dragging = false; };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  canvas.addEventListener("wheel", (ev) => {
    if (!hero) return;
    ev.preventDefault();
    zoomAt(ev.offsetX, ev.offsetY, Math.exp(-ev.deltaY * 0.0015));
  }, { passive: false });

  // Zoom about a display point so the pixel under the cursor stays put.
  function zoomAt(px, py, factor) {
    const m = meta();
    const D = dispScale();
    const { ox, oy } = frameOrigin(D);
    // ref coords under the cursor before zoom
    const rx = (px - ox) / D, ry = (py - oy) / D;
    // hero-space point under cursor: (rx - T.x)/T.scale
    const hx = (rx - T.x) / T.scale, hy = (ry - T.y) / T.scale;
    const next = clampScale(T.scale * factor);
    T.scale = next;
    T.x = rx - hx * next;
    T.y = ry - hy * next;
    syncZoom();
    drawStage();
  }
  function clampScale(s) { return Math.max(0.02, Math.min(20, s)); }

  // The slider maps to scale relative to the "reset" (auto) scale ×[0.05..5].
  $("zoom").addEventListener("input", (ev) => {
    if (!hero) return;
    const m = meta();
    const D = dispScale();
    const [rw, rh] = m.frame;
    // zoom about the frame centre
    zoomTo(parseFloat(ev.target.value), rw / 2, rh / 2, D);
  });
  function zoomTo(scale, rcx, rcy, D) {
    const s = clampScale(scale);
    const hx = (rcx - T.x) / T.scale, hy = (rcy - T.y) / T.scale;
    T.scale = s;
    T.x = rcx - hx * s;
    T.y = rcy - hy * s;
    drawStage();
  }
  function syncZoom() {
    const z = $("zoom");
    z.value = String(Math.max(0.05, Math.min(5, T.scale)));
  }

  // ── submit ─────────────────────────────────────────────
  $("startBtn").addEventListener("click", async () => {
    const prompt = promptText();
    const willGenerate = !heroBlob && canGenerate() && prompt.length > 0;
    if (!heroBlob && !willGenerate) {
      $("fitStatus").textContent = canGenerate()
        ? "Опиши метафору или загрузи изображение."
        : "Загрузи изображение.";
      return;
    }
    const slots = {};
    document.querySelectorAll("#slotFields [data-slot]").forEach((el) => {
      slots[el.dataset.slot] = el.value.trim();
    });
    if (!slots.title) { $("fitStatus").textContent = "Заполни заголовок."; return; }

    const fd = new FormData();
    fd.append("variant", variant);
    Object.keys(slots).forEach((k) => fd.append(k, slots[k]));
    if (fitOn()) {
      fd.append("fit_scale", String(T.scale));
      fd.append("fit_x", String(T.x));
      fd.append("fit_y", String(T.y));
    }
    if (heroBlob) {
      fd.append("file", heroBlob, heroBlob.name || "hero.png");
    } else if (willGenerate) {
      fd.append("prompt", prompt);
    }

    $("startBtn").disabled = true;
    $("fitStatus").textContent = "Создаю задачу…";
    try {
      const r = await fetch(`${P}/api/webinar/tasks`, { method: "POST", body: fd });
      if (!r.ok) throw new Error(errText(r.status));
      const d = await r.json();
      taskUid = d.task_uid;
      saveActive(taskUid);
      $("fitStatus").textContent = "";
      $("progress").innerHTML =
        '<span class="step"><span class="dot"></span><span id="stepLabel">Запуск…</span></span>';
      hide($("resultsPanel"));
      show($("progressPanel"));
      subscribe();
    } catch (e) {
      $("fitStatus").innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
      $("startBtn").disabled = false;
    }
  });

  // ── SSE ────────────────────────────────────────────────
  let esBackoff = 1000; const ES_BACKOFF_MAX = 15000;
  function subscribe() {
    if (es) es.close();
    es = new EventSource(`${P}/api/tasks/${taskUid}/events`);
    es.onopen = () => { esBackoff = 1000; };
    es.addEventListener("queued", () => setStep("В очереди…"));
    es.addEventListener("start", (e) => setStep(stepOf(e)));
    es.addEventListener("step", (e) => setStep(stepOf(e)));
    es.addEventListener("done", (e) => onDone(JSON.parse(e.data)));
    es.addEventListener("error", (e) => {
      if (typeof e.data === "undefined") { onStreamDrop(); return; }
      onError(e);
    });
  }
  const stepOf = (e) => { try { return JSON.parse(e.data).step || "…"; } catch { return "…"; } };
  function setStep(t) { const el = $("stepLabel"); if (el) el.textContent = t; show($("progressPanel")); }

  function onStreamDrop() {
    if (!taskUid) return;
    if (es) es.close();
    setStep("Соединение потеряно, переподключаюсь…");
    const delay = esBackoff;
    esBackoff = Math.min(esBackoff * 2, ES_BACKOFF_MAX);
    setTimeout(resync, delay);
  }
  async function resync() {
    if (!taskUid) return;
    try {
      const r = await fetch(`${P}/api/tasks/${taskUid}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const t = await r.json();
      if (ACTIVE.includes(t.status)) { subscribe(); return; }
      if (t.status === "done") onDone({ result_url: t.result_url });
      else onError({ data: JSON.stringify({ message: (t && t.error) || "Сбой сборки." }) });
    } catch (_) { onStreamDrop(); }
  }

  function onDone(d) {
    if (es) es.close();
    clearActive();
    hide($("progressPanel")); show($("resultsPanel"));
    const url = d.result_url ? `${P}${d.result_url}` : null;
    $("resultMsg").innerHTML = url
      ? `<a class="dl" href="${url}" download>Скачать ZIP</a>`
      : "Готово, но файл результата не найден.";
    $("resultGrid").innerHTML = "";
    if (taskUid) showResultGrid(taskUid);
    $("startBtn").disabled = false;
    loadRecentTasks();
  }

  // Fetch the finished task's PNGs and render them inline under the ZIP link,
  // exactly like the Креативы grid. Falls back silently if the detail call fails.
  async function showResultGrid(uid) {
    try {
      const r = await fetch(`${P}/api/tasks/${uid}`);
      if (!r.ok) return;
      const t = await r.json();
      const imgs = Array.isArray(t.images) ? t.images : [];
      if (!imgs.length) return;
      imagesByUid[uid] = imgs;
      $("resultGrid").innerHTML = thumbsHtml(uid, imgs);
    } catch (_) { /* leave just the ZIP link */ }
  }
  function onError(e) {
    if (es) es.close();
    clearActive();
    let msg = "Сбой сборки.";
    try { msg = JSON.parse(e.data).message || msg; } catch {}
    $("progress").innerHTML = `<span class="err">${escapeHtml(msg)}</span>`;
    $("startBtn").disabled = false;
    loadRecentTasks();
  }

  function errText(status) {
    if (!status) return "Нет соединения с сервером — проверь сеть и попробуй ещё раз.";
    if (status === 429) return "Сервер занят — попробуй ещё раз через пару минут.";
    if (status === 422) return "Проверь поля формы и изображение.";
    if (status === 503) return "Сборка ресайзов временно недоступна.";
    return `Ошибка ${status}`;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ── recent tasks (webinar only) ────────────────────────
  const STATUS_LABEL = {
    queued: "В очереди", running: "В работе",
    done: "Готово", failed: "Ошибка", cancelled: "Отменено",
  };
  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  }
  // A thumbnail grid for one task's PNGs — each thumb opens the lightbox and
  // carries its own download link (mirrors the Креативы grid).
  function thumbsHtml(uid, imgs) {
    const thumbs = imgs.map((u, i) =>
      `<figure class="task-thumb" data-uid="${escapeHtml(uid)}" data-idx="${i}">` +
      `<img src="${P}${u}" alt="Формат ${i + 1}" loading="lazy">` +
      `<a class="thumb-dl" href="${P}${u}" download="${escapeHtml(fileNameOf(u))}" ` +
      `title="Скачать формат ${i + 1}">⬇</a></figure>`
    ).join("");
    return `<div class="task-thumbs">${thumbs}</div>`;
  }
  function taskRow(t) {
    const label = STATUS_LABEL[t.status] || t.status;
    const title = escapeHtml(t.prompt || "(без названия)");
    const imgs = Array.isArray(t.images) ? t.images : [];
    const expandable = imgs.length > 0;
    if (expandable) imagesByUid[t.task_uid] = imgs;
    let action = "";
    if (t.status === "done" && t.result_url)
      action = `<a class="task-dl" href="${P}${t.result_url}" download>ZIP</a>`;
    else if (t.status === "failed" && t.error)
      action = `<span class="task-err">${escapeHtml(t.error)}</span>`;
    const cls = expandable ? "task-row is-expandable" : "task-row";
    const uidAttr = expandable ? ` data-uid="${escapeHtml(t.task_uid)}"` : "";
    const grid = expandable
      ? `<div class="task-grid hidden" data-grid="${escapeHtml(t.task_uid)}"></div>`
      : "";
    return `<div class="${cls}"${uidAttr}><span class="task-title">${title}</span>` +
      `<span class="task-meta"><span class="task-badge is-${t.status}">${label}</span>` +
      `<span class="task-date">${fmtDate(t.created_at)}</span>${action}</span></div>` + grid;
  }
  function toggleGrid(row) {
    const uid = row.getAttribute("data-uid");
    if (!uid) return;
    const grid = row.parentElement.querySelector(`[data-grid="${uid}"]`);
    if (!grid) return;
    if (grid.classList.contains("hidden") && !grid.childElementCount)
      grid.innerHTML = thumbsHtml(uid, imagesByUid[uid] || []);
    grid.classList.toggle("hidden");
    row.classList.toggle("is-open");
  }
  async function loadRecentTasks() {
    try {
      const r = await fetch(`${P}/api/tasks`);
      if (!r.ok) return;
      const tasks = (await r.json()).filter((t) => t.workflow === "webinar");
      if (!tasks.length) return;
      $("tasksList").innerHTML = tasks.map(taskRow).join("");
      show($("tasksPanel"));
    } catch (_) { /* leave hidden */ }
  }

  // Delegated clicks: a thumbnail opens the lightbox; an expandable row toggles
  // its grid; download links keep their default behaviour.
  function onGalleryClick(ev) {
    if (ev.target.closest("a")) return;
    const thumb = ev.target.closest(".task-thumb");
    if (thumb) { openLightbox(thumb.dataset.uid, +thumb.dataset.idx); return; }
    const row = ev.target.closest(".task-row.is-expandable");
    if (row) toggleGrid(row);
  }
  $("tasksList").addEventListener("click", onGalleryClick);
  $("resultGrid").addEventListener("click", onGalleryClick);

  // ── lightbox gallery ───────────────────────────────────
  let lbUid = null, lbIdx = 0;
  function ensureLightbox() {
    let lb = $("lightbox");
    if (lb) return lb;
    lb = document.createElement("div");
    lb.id = "lightbox";
    lb.className = "lightbox hidden";
    lb.innerHTML =
      `<button class="lb-close" data-lb="close" aria-label="Закрыть">×</button>` +
      `<button class="lb-nav lb-prev" data-lb="prev" aria-label="Назад">‹</button>` +
      `<img id="lbImg" class="lb-img" alt="">` +
      `<button class="lb-nav lb-next" data-lb="next" aria-label="Вперёд">›</button>` +
      `<div class="lb-bar"><span id="lbCount"></span>` +
      `<a id="lbDl" class="lb-dl" download>Скачать</a></div>`;
    document.body.appendChild(lb);
    lb.addEventListener("click", (ev) => {
      const act = ev.target.getAttribute("data-lb");
      if (act === "next") lbNav(1);
      else if (act === "prev") lbNav(-1);
      else if (act === "close" || ev.target === lb) closeLightbox();
    });
    return lb;
  }
  function renderLightbox() {
    const imgs = imagesByUid[lbUid] || [];
    const u = imgs[lbIdx];
    if (!u) return;
    $("lbImg").src = `${P}${u}`;
    $("lbImg").alt = `Формат ${lbIdx + 1}`;
    $("lbCount").textContent = `${lbIdx + 1} / ${imgs.length}`;
    const dl = $("lbDl"); dl.href = `${P}${u}`; dl.download = fileNameOf(u);
  }
  function openLightbox(uid, idx) {
    lbUid = uid; lbIdx = idx || 0;
    ensureLightbox(); renderLightbox(); show($("lightbox"));
  }
  function closeLightbox() { const lb = $("lightbox"); if (lb) hide(lb); }
  function lbNav(delta) {
    const imgs = imagesByUid[lbUid] || [];
    if (!imgs.length) return;
    lbIdx = (lbIdx + delta + imgs.length) % imgs.length;
    renderLightbox();
  }
  document.addEventListener("keydown", (ev) => {
    const lb = $("lightbox");
    if (!lb || lb.classList.contains("hidden")) return;
    if (ev.key === "Escape") closeLightbox();
    else if (ev.key === "ArrowRight") lbNav(1);
    else if (ev.key === "ArrowLeft") lbNav(-1);
  });

  // ── rehydrate active webinar task ──────────────────────
  async function rehydrate() {
    const uid = (() => { try { return localStorage.getItem(LS_KEY); } catch (_) { return null; } })();
    if (!uid) return;
    taskUid = uid;
    try {
      const r = await fetch(`${P}/api/tasks/${uid}`);
      if (!r.ok) { clearActive(); return; }
      const t = await r.json();
      if (ACTIVE.includes(t.status)) { saveActive(uid); setStep("Продолжаю…"); subscribe(); }
      else { clearActive(); if (t.status === "done") onDone({ result_url: t.result_url }); }
    } catch (_) { /* ignore */ }
  }

  loadMeta();
  rehydrate();
  loadRecentTasks();
})();
