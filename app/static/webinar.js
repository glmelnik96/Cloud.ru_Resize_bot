/* App3 / вебинарные ресайзы — v11.
   Каркас тот же, что у «Креативов»: рельс собирает задачу, лента показывает
   форматы плитками, лайтбокс работает в двух режимах — просмотр формата и
   подгонка кадра. Подгонка вынесена в лайтбокс не для красоты: холст 360
   в рельс шириной 264 не влезает.

   Движок подгонки не тронут — это Transform(scale, x, y) в единицах опорной
   рамки, который уходит на сервер как есть (infra.hero_fit.bake_frame
   воспроизводит его точь-в-точь). */
(function () {
  "use strict";
  const P = window.APP_PREFIX || "";
  const $ = (id) => document.getElementById(id);
  const show = (el) => el && el.classList.remove("hidden");
  const hide = (el) => el && el.classList.add("hidden");

  const ALPHA_THRESHOLD = 32;
  const HEAD_LINE_FRAC = 0.06; // где должна стоять макушка спикера
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
  const STATUS_LABEL = {
    queued: "В очереди", running: "В работе",
    done: "Готово", failed: "Ошибка", cancelled: "Отменено",
  };

  let META = null;         // { speaker:{frame,box,mode,anchor_v,slots,formats}, visual:{…} }
  let variant = "speaker";
  let hero = null;         // HTMLImageElement (загруженный)
  let heroBlob = null;     // File/Blob для POST
  let alphaBox = null;     // {x0,y0,x1,y1} в пикселях исходника (порог 32)
  const T = { scale: 1, x: 0, y: 0 }; // Transform в единицах опорной рамки

  let taskUid = null, es = null;
  // Занятость: от нажатия «Собрать» до done/ошибки. Одна задача на страницу —
  // лента показывает прогресс в одной плитке, и вторая сборка её бы перебила.
  let busy = false;
  const LS_KEY = "app3_webinar_task";
  const ACTIVE = ["queued", "running"];
  const saveActive = (uid) => { try { localStorage.setItem(LS_KEY, uid); } catch (_) {} };
  const clearActive = () => { try { localStorage.removeItem(LS_KEY); } catch (_) {} };

  // Состояние ленты. tasks — сырой список сервера (свежие первыми), views —
  // плоский список форматов для листания в лайтбоксе.
  let tasks = [];
  let views = [];
  const stateEls = new Map();   // uid → {wrap, line, acts, bar, actsKey}
  let liveStep = "";            // текст шага активной задачи (из SSE)
  const byUid = (uid) => tasks.find((t) => t.task_uid === uid) || null;

  const feedGrid = $("feedGrid");
  const feedCount = $("feedCount");
  const feedEmpty = $("feedEmpty");
  const feedFoot = $("feedFoot");

  const canvas = $("fitCanvas");
  const ctx = canvas.getContext("2d");
  const thumb = $("fitThumb");
  const thumbCtx = thumb.getContext("2d");

  // ── мелкие конструкторы DOM ────────────────────────────
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function tBtn(label, fn, cls) {
    const b = el("button", "t-btn" + (cls ? " " + cls : ""), label);
    b.type = "button";
    b.addEventListener("click", fn);
    return b;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  const fileNameOf = (u) => String(u).split("/").pop();
  // Имя файла формата уже несёт название ресайза (vk_1200x600.png) — оно
  // информативнее порядкового «Формат 7», ради которого пришлось бы держать
  // отдельный справочник на клиенте.
  const captionOf = (u) => fileNameOf(u).replace(/\.[a-z0-9]+$/i, "");

  // ── лента ──────────────────────────────────────────────
  function stateText(t) {
    if (t.task_uid === taskUid && liveStep) return liveStep;
    if (t.status === "failed") return t.error ? "Ошибка: " + t.error : "Ошибка";
    return STATUS_LABEL[t.status] || t.status;
  }
  const tileClass = (t) => "work work--" + t.status;
  const actsKeyOf = (t) => t.status;

  // Служебная плитка: задача, у которой форматов ещё (или уже) нет. Каркас
  // строится один раз — события шага приходят часто, и пересборка гасила бы
  // ссылку прямо под курсором.
  function stateTile(t) {
    const wrap = el("article", tileClass(t));
    const body = el("div", "work__body");
    const line = el("div", "work__state", stateText(t));
    const acts = el("div", "work__acts");
    const bar = el("div", "work__bar is-idle");
    bar.appendChild(document.createElement("i"));
    bar.hidden = !(t.status === "queued" || t.status === "running");
    body.appendChild(line);
    body.appendChild(acts);
    body.appendChild(bar);
    wrap.appendChild(body);
    const cap = el("div", "work__cap", t.prompt || "(без названия)");
    cap.title = t.prompt || "";
    wrap.appendChild(cap);
    stateEls.set(t.task_uid, { wrap, line, acts, bar, actsKey: "" });
    fillActs(t);
    return wrap;
  }

  function fillActs(t) {
    const p = stateEls.get(t.task_uid);
    if (!p) return;
    p.actsKey = actsKeyOf(t);
    p.acts.innerHTML = "";
    // Готовая сборка без картинок — PNG подчистила ретенция, но архив ещё жив.
    if (t.status === "done" && t.result_url) {
      const a = el("a", "t-btn", "Скачать ZIP");
      a.href = P + t.result_url;
      a.setAttribute("download", "");
      p.acts.appendChild(a);
    }
  }

  // Плитка формата. Один ресайз — одна плитка: группировать двадцать шесть штук
  // в «сборку» незачем, человек выбирает формат, а не прогон.
  function imgTile(t, url, i) {
    const caption = captionOf(url);
    const wrap = el("article", "work work--done");
    const body = el("div", "work__body");
    const open = el("button", "work__open");
    open.type = "button";
    open.title = caption;
    const img = el("img");
    img.loading = "lazy";
    img.src = P + url;
    img.alt = caption;
    open.appendChild(img);
    const pos = views.length;
    open.addEventListener("click", () => openView(pos));
    body.appendChild(open);
    wrap.appendChild(body);
    const cap = el("div", "work__cap", caption);
    cap.title = caption;
    wrap.appendChild(cap);
    views.push({ uid: t.task_uid, idx: i, url: url, caption: caption });
    return wrap;
  }

  function renderFeed() {
    feedGrid.innerHTML = "";
    stateEls.clear();
    views = [];
    for (const t of tasks) {
      const imgs = Array.isArray(t.images) ? t.images : [];
      if (t.status === "done" && imgs.length) {
        imgs.forEach((u, i) => feedGrid.appendChild(imgTile(t, u, i)));
      } else {
        feedGrid.appendChild(stateTile(t));
      }
    }
    const n = feedGrid.childElementCount;
    feedCount.textContent = n;
    feedEmpty.hidden = n > 0;
    // Сноска про срок хранения нужна, только когда есть что хранить.
    feedFoot.hidden = !tasks.length;
  }

  // Точечное обновление плитки под поток событий. Пересобираем целиком только
  // тогда, когда плитки для этой задачи в ленте нет (её ещё не было или она
  // сменила род — из служебной стала набором форматов).
  function syncState(uid) {
    const t = byUid(uid);
    if (!t) return;
    const p = stateEls.get(uid);
    if (!p) { renderFeed(); return; }
    p.wrap.className = tileClass(t);
    p.line.textContent = stateText(t);
    p.bar.hidden = !(t.status === "queued" || t.status === "running");
    if (p.actsKey !== actsKeyOf(t)) fillActs(t);
  }

  // Лента этой страницы — только вебинарные сборки: «Креативы» живут в своей.
  async function loadTasks() {
    try {
      const r = await fetch(`${P}/api/tasks`);
      if (!r.ok) return;
      const list = await r.json();
      tasks = (Array.isArray(list) ? list : []).filter((t) => t.workflow === "webinar");
      renderFeed();
    } catch (_) { /* сеть вернётся — вернётся и список */ }
  }

  // ── семейство форматов и вариант ───────────────────────
  async function loadMeta() {
    try {
      const r = await fetch(`${P}/api/webinar/meta`);
      if (!r.ok) return;
      META = await r.json();
    } catch (_) { /* META остаётся null; ошибку покажет отправка */ }
    renderVariant();
  }

  $("variantSeg").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".seg__btn");
    if (!btn || btn.dataset.variant === variant) return;
    variant = btn.dataset.variant;
    document.querySelectorAll("#variantSeg .seg__btn").forEach((b) =>
      b.classList.toggle("is-active", b === btn));
    // Фото спикера и рендер-метафора — разные ассеты, поэтому смена варианта
    // сбрасывает изображение. Без этого загруженный спикер оставался в heroBlob
    // и сборка метафоры брала его вместо генерации по описанию.
    clearHero();
    renderVariant();
  });

  // Полный сброс состояния изображения: следующий вариант начинает с чистого.
  function clearHero() {
    hero = null; heroBlob = null; alphaBox = null;
    T.scale = 1; T.x = 0; T.y = 0;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    thumbCtx.clearRect(0, 0, thumb.width, thumb.height);
    hide(thumb);
    hide($("fitOpen"));
    $("fitStatus").textContent = "";
    updateStartBtn();
  }

  function meta() { return (META && META[variant]) || null; }
  // Нужна ли ручная подгонка. Спикер — да. Метафора — нет: сгенерированный
  // рендер каждый формат размещает сам.
  function fitOn() { const m = meta(); return m ? !!m.fit : variant === "speaker"; }

  function renderVariant() {
    const m = meta();
    const slots = (m && m.slots) || ["title", "date", "time"];
    // Поля текстов — те же .field-group, что в брифе «Креативов»: подпись 12,
    // зазор 12, поле 48. Иначе рельс уехал бы с модуля на этой одной секции.
    $("slotFields").innerHTML = slots.map((s) =>
      `<label class="field-group" for="slot_${s}">` +
      `<span class="field-label">${escapeHtml(SLOT_LABELS[s] || s)}</span>` +
      `<input type="text" id="slot_${s}" data-slot="${escapeHtml(s)}"` +
      ` placeholder="${escapeHtml(SLOT_PH[s] || "")}"></label>`
    ).join("");
    const n = m ? m.formats : "";
    $("variantHint").textContent = variant === "speaker"
      ? `Фото спикера. Соберём ${n} форматов семейства «спикер».`
      : `Рендер-метафора без фона. Соберём ${n} форматов семейства «метафора».`;
    $("fitHint").textContent = fitOn()
      ? "Загрузите фото спикера и выровняйте лицо по кадру."
      : "Метафора собирается под квадрат и впишется в каждый формат сама — кадрировать не нужно.";
    // Кирпич подгонки и превью показываем, только когда есть что подгонять.
    if (hero && fitOn()) { show($("fitOpen")); show(thumb); drawThumb(); }
    else { hide($("fitOpen")); hide(thumb); }
    // Метафора: генерация по описанию доступна, когда App1 подключён.
    if (canGenerate()) show($("promptRow")); else hide($("promptRow"));
    updateStartBtn();
  }

  // Может ли этот вариант собрать изображение по описанию (App1 подключён).
  function canGenerate() { const m = meta(); return !!(m && m.can_generate); }
  const promptText = () => (($("metaphorPrompt") || {}).value || "").trim();

  // Собирать можно, когда есть из чего: загруженное изображение либо (метафора)
  // описание, по которому его сгенерируют.
  function updateStartBtn() {
    const ok = !!heroBlob || (canGenerate() && promptText().length > 0);
    // busy держится не «пока летит POST», а всю жизнь задачи: правка описания
    // или смена варианта во время сборки иначе включили бы кнопку обратно, и
    // одним кликом уехала бы вторая задача поверх первой.
    $("startBtn").disabled = !ok || busy;
  }

  // ── загрузка изображения ───────────────────────────────
  $("heroFile").addEventListener("change", (ev) => {
    const f = ev.target.files[0];
    ev.target.value = "";
    if (!f) return;
    heroBlob = f;
    const img = new Image();
    img.onload = () => {
      hero = img;
      updateStartBtn();
      if (!fitOn()) { hide($("fitOpen")); hide(thumb); return; }
      alphaBox = computeAlphaBox(img);
      resetTransform();
      show($("fitOpen"));
      show(thumb);
      drawStage();
      drawThumb();
      // Кадр открываем сразу: загрузка фото и есть заявка на подгонку, и лишний
      // клик по «Кадрировать» здесь ничего не решает.
      openLb("fitPanel");
    };
    img.onerror = () => { $("fitStatus").innerHTML = `<span class="err">Не удалось открыть изображение.</span>`; };
    img.src = URL.createObjectURL(f);
  });

  $("fitOpen").addEventListener("click", () => {
    if (!hero || !fitOn()) return;
    drawStage();
    openLb("fitPanel");
  });

  // Метафора: описания достаточно, чтобы собрать (загрузка не обязательна).
  const _promptEl = $("metaphorPrompt");
  if (_promptEl) _promptEl.addEventListener("input", updateStartBtn);

  // Альфа-бокс с порогом 32 (зеркалит infra.hero_fit._alpha_bbox). Непрозрачные
  // изображения (JPEG-фото спикера) дают бокс во весь кадр.
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

  // Размещение по умолчанию (зеркалит infra.hero_fit.auto_transform), чтобы
  // холст открывался на том же кадре, который выбрал бы сервер без правок.
  function resetTransform() {
    const m = meta();
    if (!hero || !m) return;
    const [bx0, by0, bx1, by1] = m.box;
    const boxW = bx1 - bx0, boxH = by1 - by0;
    const b = alphaBox || { x0: 0, y0: 0, x1: hero.naturalWidth, y1: hero.naturalHeight };
    // Страховка от вырожденного альфа-бокса (нулевая сторона): без неё objW/objH
    // становятся 0 → scale Infinity → T.x/T.y NaN, а сервер отвечает 422 без
    // объяснения. Прижимаем к 1px, чтобы трансформ остался конечным.
    const objW = Math.max(1, b.x1 - b.x0), objH = Math.max(1, b.y1 - b.y0);
    const sx = boxW / objW, sy = boxH / objH;
    const scale = m.mode === "contain" ? Math.min(sx, sy) : Math.max(sx, sy);
    T.scale = scale;
    T.x = bx0 + (boxW - objW * scale) / 2 - b.x0 * scale;
    T.y = m.anchor_v === "top"
      ? by0 - b.y0 * scale
      : by0 + (boxH - objH * scale) / 2 - b.y0 * scale;
    syncZoom();
  }
  $("fitReset").addEventListener("click", () => { resetTransform(); drawStage(); drawThumb(); });

  // ── отрисовка кадра ────────────────────────────────────
  // Холст показывает всю опорную рамку; D переводит её единицы в пиксели.
  function dispScale(cv) {
    const m = meta();
    if (!m) return 1;
    const [rw, rh] = m.frame;
    return Math.min(cv.width / rw, cv.height / rh);
  }
  function frameOrigin(cv, D) {
    const m = meta();
    const [rw, rh] = m.frame;
    return { ox: (cv.width - rw * D) / 2, oy: (cv.height - rh * D) / 2 };
  }

  // Один и тот же кадр рисуется в два холста: большой в лайтбоксе и превью 48
  // в рельсе. Разница только в направляющих — на превью они были бы шумом.
  function drawInto(cv, c, guides) {
    const m = meta();
    if (!hero || !m) return;
    const [rw, rh] = m.frame;
    const D = dispScale(cv);
    const { ox, oy } = frameOrigin(cv, D);
    c.clearRect(0, 0, cv.width, cv.height);

    // шахматка под рамкой — чтобы прозрачный PNG читался прозрачным
    drawChecker(c, ox, oy, rw * D, rh * D);

    c.save();
    c.beginPath();
    c.rect(ox, oy, rw * D, rh * D);
    c.clip();
    const hw = hero.naturalWidth * T.scale * D;
    const hh = hero.naturalHeight * T.scale * D;
    c.drawImage(hero, ox + T.x * D, oy + T.y * D, hw, hh);
    c.restore();

    if (guides) drawGuides(c, ox, oy, rw * D, rh * D);
  }
  function drawStage() { drawInto(canvas, ctx, true); }
  function drawThumb() { drawInto(thumb, thumbCtx, false); }

  // Шахматка — единственное место, где на экране светлое: она изображает
  // прозрачность и подчиняется общей конвенции, а не палитре портала.
  function drawChecker(c, x, y, w, h) {
    const s = 10;
    for (let j = 0; j * s < h; j++) {
      for (let i = 0; i * s < w; i++) {
        c.fillStyle = (i + j) % 2 ? "#1E2523" : "#0F1312";
        c.fillRect(x + i * s, y + j * s, Math.min(s, w - i * s), Math.min(s, h - j * s));
      }
    }
  }

  function drawGuides(c, x, y, w, h) {
    // трети
    c.strokeStyle = "rgba(242,245,244,0.16)";
    c.lineWidth = 1;
    for (let k = 1; k < 3; k++) {
      const gx = x + (w * k) / 3, gy = y + (h * k) / 3;
      line(c, gx, y, gx, y + h);
      line(c, x, gy, x + w, gy);
    }
    // линия макушки — только у спикера
    if (variant === "speaker") {
      c.strokeStyle = "rgba(63,182,124,0.9)";
      c.lineWidth = 1.5;
      const hy = y + h * HEAD_LINE_FRAC;
      line(c, x, hy, x + w, hy);
    }
    // контур рамки
    c.strokeStyle = "#3FB67C";
    c.lineWidth = 2;
    c.strokeRect(x + 1, y + 1, w - 2, h - 2);
  }
  function line(c, x0, y0, x1, y1) {
    c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke();
  }

  // ── взаимодействие: тянуть — двигать, колесо — масштаб ──
  let dragging = false, lastX = 0, lastY = 0;
  canvas.addEventListener("pointerdown", (ev) => {
    if (!hero) return;
    dragging = true; lastX = ev.clientX; lastY = ev.clientY;
    canvas.setPointerCapture(ev.pointerId);
  });
  canvas.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    const D = dispScale(canvas);
    T.x += (ev.clientX - lastX) / D;
    T.y += (ev.clientY - lastY) / D;
    lastX = ev.clientX; lastY = ev.clientY;
    drawStage(); drawThumb();
  });
  const endDrag = () => { dragging = false; };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  canvas.addEventListener("wheel", (ev) => {
    if (!hero) return;
    ev.preventDefault();
    zoomAt(ev.offsetX, ev.offsetY, Math.exp(-ev.deltaY * 0.0015));
  }, { passive: false });

  // Масштабируем вокруг точки экрана, чтобы пиксель под курсором остался на месте.
  function zoomAt(px, py, factor) {
    const D = dispScale(canvas);
    const { ox, oy } = frameOrigin(canvas, D);
    const rx = (px - ox) / D, ry = (py - oy) / D;
    const hx = (rx - T.x) / T.scale, hy = (ry - T.y) / T.scale;
    const next = clampScale(T.scale * factor);
    T.scale = next;
    T.x = rx - hx * next;
    T.y = ry - hy * next;
    syncZoom();
    drawStage(); drawThumb();
  }
  function clampScale(s) { return Math.max(0.02, Math.min(20, s)); }

  $("zoom").addEventListener("input", (ev) => {
    if (!hero) return;
    const m = meta();
    if (!m) return;
    const [rw, rh] = m.frame;
    zoomTo(parseFloat(ev.target.value), rw / 2, rh / 2);
  });
  function zoomTo(scale, rcx, rcy) {
    const s = clampScale(scale);
    const hx = (rcx - T.x) / T.scale, hy = (rcy - T.y) / T.scale;
    T.scale = s;
    T.x = rcx - hx * s;
    T.y = rcy - hy * s;
    drawStage(); drawThumb();
  }
  function syncZoom() {
    const z = $("zoom");
    z.value = String(Math.max(0.05, Math.min(5, T.scale)));
  }

  // ── отправка ───────────────────────────────────────────
  $("buildForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (busy) return;
    const prompt = promptText();
    const willGenerate = !heroBlob && canGenerate() && prompt.length > 0;
    if (!heroBlob && !willGenerate) {
      $("fitStatus").textContent = canGenerate()
        ? "Опишите метафору или загрузите изображение."
        : "Загрузите изображение.";
      return;
    }
    const slots = {};
    document.querySelectorAll("#slotFields [data-slot]").forEach((e) => {
      slots[e.dataset.slot] = e.value.trim();
    });
    if (!slots.title) { $("fitStatus").textContent = "Заполните заголовок."; return; }

    const fd = new FormData();
    fd.append("variant", variant);
    Object.keys(slots).forEach((k) => fd.append(k, slots[k]));
    if (fitOn() && [T.scale, T.x, T.y].every(Number.isFinite)) {
      fd.append("fit_scale", String(T.scale));
      fd.append("fit_x", String(T.x));
      fd.append("fit_y", String(T.y));
    }
    if (heroBlob) {
      fd.append("file", heroBlob, heroBlob.name || "hero.png");
    } else if (willGenerate) {
      fd.append("prompt", prompt);
    }

    busy = true;
    updateStartBtn();
    $("fitStatus").textContent = "Создаю задачу…";
    try {
      const r = await fetch(`${P}/api/webinar/tasks`, { method: "POST", body: fd });
      if (!r.ok) throw new Error(errText(r.status));
      const d = await r.json();
      taskUid = d.task_uid;
      saveActive(taskUid);
      $("fitStatus").textContent = "";
      closeLb();
      // Плитка появляется сразу, до первого события: между POST и первым шагом
      // проходит секунда-другая, и без неё лента выглядела бы не отреагировавшей.
      tasks.unshift({
        task_uid: taskUid, status: "queued", workflow: "webinar",
        prompt: slots.title, images: [], created_at: new Date().toISOString(),
      });
      liveStep = "Запуск…";
      renderFeed();
      subscribe();
    } catch (e) {
      // Задача не создана — занятость снимаем сразу, иначе форма замрёт
      // навсегда: события, которое её отпустит, уже не будет.
      busy = false;
      updateStartBtn();
      $("fitStatus").innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
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
    es.addEventListener("done", () => onDone());
    // Штатный обрыв соединения и событие «задача упала» носят одно имя. Отличает
    // их только наличие data: у обрыва его нет.
    es.addEventListener("error", (e) => {
      if (typeof e.data === "undefined") { onStreamDrop(); return; }
      onError(e);
    });
  }
  const stepOf = (e) => { try { return JSON.parse(e.data).step || "…"; } catch { return "…"; } };
  function setStep(t) {
    liveStep = t;
    if (taskUid) syncState(taskUid);
  }

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
      if (t.status === "done") onDone();
      else onError({ data: JSON.stringify({ message: (t && t.error) || "Сбой сборки." }) });
    } catch (_) { onStreamDrop(); }
  }

  async function onDone() {
    if (es) es.close();
    clearActive();
    liveStep = "";
    busy = false;
    updateStartBtn();
    // Перечитываем список целиком: у готовой задачи появились форматы, и
    // служебная плитка должна смениться набором плиток-ресайзов.
    await loadTasks();
  }

  function onError(e) {
    if (es) es.close();
    clearActive();
    liveStep = "";
    busy = false;
    let msg = "Сбой сборки.";
    try { msg = JSON.parse(e.data).message || msg; } catch {}
    $("fitStatus").innerHTML = `<span class="err">${escapeHtml(msg)}</span>`;
    updateStartBtn();
    loadTasks();
  }

  function errText(status) {
    if (!status) return "Нет соединения с сервером — проверьте сеть и попробуйте ещё раз.";
    if (status === 429) return "Сервер занят — попробуйте ещё раз через пару минут.";
    if (status === 422) return "Проверьте поля формы и изображение.";
    if (status === 503) return "Сборка ресайзов временно недоступна.";
    return `Ошибка ${status}`;
  }

  // ── лайтбокс: два режима в одной раме ──────────────────
  const lightbox = $("lightbox");
  const lbPanel = $("lbPanel");
  const LB_PARTS = ["lbView", "lbSide", "fitPanel"];
  const LB_MODE = {
    view: { cls: "", parts: ["lbView", "lbSide"] },
    fitPanel: { cls: " lightbox__panel--form", parts: ["fitPanel"] },
  };
  let lbMode = null;

  function openLb(mode) {
    const m = LB_MODE[mode];
    if (!m) return;
    lbMode = mode;
    LB_PARTS.forEach((id) => {
      const p = $(id);
      if (p) p.classList.toggle("hidden", m.parts.indexOf(id) < 0);
    });
    lbPanel.className = "lightbox__panel" + m.cls;
    show(lightbox);
    document.body.style.overflow = "hidden";
  }
  function closeLb() {
    hide(lightbox);
    lbMode = null;
    document.body.style.overflow = "";
  }

  lightbox.addEventListener("click", (ev) => {
    const act = ev.target.getAttribute && ev.target.getAttribute("data-lb");
    if (act === "close") closeLb();
    else if (act === "prev") viewNav(-1);
    else if (act === "next") viewNav(1);
  });
  document.addEventListener("keydown", (ev) => {
    if (!lbMode) return;
    if (ev.key === "Escape") { closeLb(); return; }
    // Стрелки листают форматы — в подгонке они значат другое (ничего) и заперты.
    if (lbMode !== "view") return;
    if (ev.key === "ArrowRight") viewNav(1);
    else if (ev.key === "ArrowLeft") viewNav(-1);
  });

  // ── просмотр формата ───────────────────────────────────
  let viewPos = 0;
  function openView(pos) {
    if (!views.length) return;
    viewPos = (pos + views.length) % views.length;
    renderView();
    openLb("view");
  }
  function viewNav(delta) {
    if (!views.length) return;
    viewPos = (viewPos + delta + views.length) % views.length;
    renderView();
  }
  function renderView() {
    const v = views[viewPos];
    if (!v) return;
    const t = byUid(v.uid) || {};
    $("lbImg").src = P + v.url;
    $("lbImg").alt = v.caption;
    $("lbCount").textContent = `${viewPos + 1} / ${views.length}`;
    $("lbCap").textContent = v.caption;
    buildViewActions(t, v);
  }

  function group(label) {
    const g = el("div", "lb-group");
    if (label) g.appendChild(el("p", "lb-group__label", label));
    const row = el("div", "lb-group__row");
    g.appendChild(row);
    g._row = row;
    return g;
  }

  function buildViewActions(t, v) {
    const box = $("lbActions");
    box.innerHTML = "";
    const save = group("");
    const dl = el("a", "t-btn t-btn--key", "Скачать формат");
    dl.href = P + v.url;
    dl.download = fileNameOf(v.url);
    save._row.appendChild(dl);
    if (t.result_url) {
      // Один формат нужен редко: чаще забирают всё семейство целиком.
      const zip = el("a", "t-btn", "Скачать все форматы");
      zip.href = P + t.result_url;
      zip.setAttribute("download", "");
      save._row.appendChild(zip);
    }
    box.appendChild(save);
  }

  // ── восстановление активной сборки ─────────────────────
  // Контракт с gateway требует поднимать активную задачу при загрузке страницы.
  // localStorage тут только ускоряет: у него своя область на браузер, и после
  // смены устройства или очистки хранилища задача нашлась бы «пропавшей».
  // Поэтому есть второй источник — уже загруженная лента.
  function findActiveUid() {
    let uid = null;
    try { uid = localStorage.getItem(LS_KEY); } catch (_) { uid = null; }
    if (uid) return uid;
    const live = tasks.find((t) => ACTIVE.includes(t.status));
    return live ? live.task_uid : null;
  }

  async function rehydrate() {
    const uid = findActiveUid();
    if (!uid) return;
    taskUid = uid;
    try {
      const r = await fetch(`${P}/api/tasks/${uid}`);
      if (!r.ok) { clearActive(); taskUid = null; return; }
      const t = await r.json();
      if (ACTIVE.includes(t.status)) {
        saveActive(uid);
        setStep("Продолжаю…");
        busy = true;
        updateStartBtn();
        subscribe();
      } else {
        clearActive();
        taskUid = null;
      }
    } catch (_) { /* сеть вернётся — вернётся и подписка */ }
  }

  (async function boot() {
    await loadMeta();
    await loadTasks();
    await rehydrate();
  })();
})();
