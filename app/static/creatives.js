/* App3 / creatives — бриф в рельсе, задача в плитке, решения в лайтбоксе.
   Все адреса идут через префикс шлюза (window.APP_PREFIX).

   Канон v11: пяти «экранов» пайплайна больше нет. Прогон живёт одной плиткой
   ленты от запуска до баннеров; остановка (HITL) — это состояние плитки, а ввод
   к ней открывается лайтбоксом. Поэтому здесь нет ни empty-state, ни панелей
   прогресса/результата: их работу делают плитка и лента. */
(function () {
  "use strict";
  const P = window.APP_PREFIX || "";
  const $ = (id) => document.getElementById(id);
  const show = (el) => { if (el) el.classList.remove("hidden"); };
  const hide = (el) => { if (el) el.classList.add("hidden"); };

  // ── состояние прогона ──────────────────────────────────
  let taskUid = null;
  let es = null; // EventSource
  // Выбранный победитель: ranked[0] по умолчанию (скоринговый порядок), пока
  // человек не ткнул в другую карточку.
  let winnerId = null;
  // Задача кончилась — набор кандидатов больше не актуален, а вместе с ним и
  // победитель: без сброса он утёк бы в следующий запуск на той же вкладке.
  const resetWinner = () => { winnerId = null; };
  // Active-task handle survives the full page reload that canon-header
  // navigation does (links to /images /slides /creatives are absolute). The
  // run keeps going server-side; on load we rehydrate from here or /api/tasks.
  const LS_KEY = "app3_active_task";
  const AWAITING = ["awaiting_persona", "awaiting_text", "awaiting_image"];
  const ACTIVE = ["queued", "running"].concat(AWAITING);
  const saveActive = (uid) => { try { localStorage.setItem(LS_KEY, uid); } catch (_) {} };
  const clearActive = () => { try { localStorage.removeItem(LS_KEY); } catch (_) {} };

  // ── состояние ленты ────────────────────────────────────
  // tasks — то, что отдал сервер (свежие первыми); плитки строятся из него, а
  // не из отдельной модели: две копии правды разъезжались бы на каждом событии.
  let tasks = [];
  const byUid = (u) => tasks.find((t) => t.task_uid === u);
  const stateEls = new Map(); // uid -> части служебной плитки (обновляются на месте)
  let views = [];             // плоский список картинок ленты — по нему листает лайтбокс
  let liveStep = "";          // текст состояния активной задачи (из SSE)
  // Остановка маршрута активной задачи. Число приходит с сервера рядом с
  // подписью шага: сопоставлять русские подписи здесь значило бы гасить полосу
  // при каждом переименовании шага на сервере. 0 — маршрут в покое.
  let liveStage = 0;

  const feedList = $("feedList");
  const feedCount = $("feedCount");
  const feedEmpty = $("feedEmpty");
  const feedFoot = $("feedFoot");
  // Окно хранения приходит с сервера через шаблон: константа в JS разъехалась
  // бы с RETENTION_TTL_SEC при первой же правке конфига.
  const RETENTION_H = Number((feedList && feedList.dataset.retentionHours) || 24);
  const routeStops = Array.prototype.slice.call(
    document.querySelectorAll("#route .route__stop")
  );

  const STATUS_LABEL = {
    queued: "В очереди", running: "В работе",
    awaiting_persona: "Ждёт персону",
    awaiting_text: "Ждёт решения", awaiting_image: "Ждёт картинку",
    done: "Готово", failed: "Ошибка", cancelled: "Отменено",
  };
  const STATUS_OF_PHASE = {
    persona_approve: "awaiting_persona",
    text_approve: "awaiting_text",
    image_upload: "awaiting_image",
  };
  // Запасной номер остановки, когда события шага ещё не было: сразу после POST
  // (queued) и на восстановлении после перезагрузки. Бриф (01) уже позади —
  // задача существует, значит маршрут начался со второй остановки.
  const STAGE_OF_STATUS = {
    queued: 2, running: 2,
    awaiting_persona: 2, awaiting_text: 3, awaiting_image: 4,
  };
  const OUTCOME_LABEL = { shipped: "Пошёл в кампанию", rejected: "Отклонили" };
  const BRIEF_LABELS = {
    product: "Что рекламируем", audience: "Целевая аудитория", emotion: "Эмоция / образ",
    notes: "Свободное поле", source_url: "Ссылка на страницу",
  };

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
  const escapeHtml = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const kv = (k, v) => v ? `<div class="kv"><b>${k}</b> ${escapeHtml(v)}</div>` : "";
  const fileNameOf = (u) => String(u).split("/").pop();

  // ── лента ──────────────────────────────────────────────
  function stateText(t) {
    if (t.task_uid === taskUid && liveStep) return liveStep;
    if (t.status === "failed") return t.error ? "Ошибка: " + t.error : "Ошибка";
    return STATUS_LABEL[t.status] || t.status;
  }
  const rowClass = (t) =>
    "work work--" + t.status + (AWAITING.indexOf(t.status) >= 0 ? " work--await" : "");
  const actsKeyOf = (t) => (AWAITING.indexOf(t.status) >= 0 ? "await" : t.status);
  // Отметка исхода стоит на строке, а не только внутри ящика: иначе, чтобы
  // понять, что из прошлых запусков ушло в кампанию, пришлось бы открыть все.
  const markClass = (t) =>
    "work__mark" + (t.outcome === "rejected" ? " work__mark--rejected" : "");

  // Остаток срока хранения. Дата создания человеку не говорит ничего: он
  // спрашивает «успею ли скачать», а не «когда это было».
  // Наивный UTC: сервер отдаёт created_at без суффикса, и new Date() читает
  // такую строку как местное время — в Москве это ошибка на три часа.
  function keepText(t) {
    if (!t.created_at || ACTIVE.indexOf(t.status) >= 0) return "";
    const raw = String(t.created_at);
    const iso = /(Z|[+-]\d\d:?\d\d)$/.test(raw) ? raw : raw + "Z";
    const left = RETENTION_H - (Date.now() - new Date(iso).getTime()) / 3600000;
    if (!isFinite(left) || left <= 0) return "";
    return "хранится ещё " + Math.max(1, Math.round(left)) + " ч";
  }

  // Работа — одна строка на 48, независимо от того, сколько у неё баннеров.
  // Двенадцать креативов это один предмет, а не двенадцать: сетка одинаковых
  // квадратов и была той кашей, ради которой всё затевалось.
  // Каркас строится один раз: события шага приходят часто, и пересборка гасила
  // бы кнопку прямо под курсором.
  function workRow(t) {
    const wrap = el("article", rowClass(t));
    const head = el("div", "work__head");
    const hit = el("button", "work__hit");
    hit.type = "button";
    hit.setAttribute("aria-expanded", "false");
    const name = el("span", "work__name", t.prompt || "(без названия)");
    name.title = t.prompt || "";
    hit.appendChild(name);
    const line = el("span", "work__state", stateText(t));
    hit.appendChild(line);
    const mark = el("span", markClass(t), OUTCOME_LABEL[t.outcome] || "");
    mark.hidden = !t.outcome;
    hit.appendChild(mark);
    const keep = el("span", "work__keep", keepText(t));
    hit.appendChild(keep);
    hit.addEventListener("click", () => toggleRow(t.task_uid));
    head.appendChild(hit);
    const acts = el("div", "work__acts");
    head.appendChild(acts);
    const bar = el("div", "work__bar is-idle");
    bar.appendChild(document.createElement("i"));
    bar.hidden = !(t.status === "queued" || t.status === "running");
    head.appendChild(bar);
    const panel = el("div", "work__panel");
    wrap.appendChild(head);
    wrap.appendChild(panel);
    stateEls.set(t.task_uid, { wrap, hit, line, mark, keep, acts, bar, panel, actsKey: "" });
    fillActs(t);
    return wrap;
  }

  // Что лежит в ящике — зависит от состояния работы. Идущий прогон не
  // открывается: показывать нечего, живой шаг стоит на самой строке.
  const STOP_PANEL = {
    awaiting_persona: "personaPanel",
    awaiting_text: "textPanel",
    awaiting_image: "imagePanel",
  };
  const HITL_PANELS = ["personaPanel", "textPanel", "imagePanel"];

  function toggleRow(uid) {
    const t = byUid(uid);
    const p = stateEls.get(uid);
    if (!t || !p) return;
    if (t.status === "queued" || t.status === "running") return;
    if (t.status === "failed" || t.status === "cancelled") return;
    if (p.wrap.classList.contains("is-open")) { closeRow(uid); return; }
    openRow(uid);
  }

  function openRow(uid) {
    const t = byUid(uid);
    const p = stateEls.get(uid);
    if (!t || !p) return;
    // Открыт всегда один ящик: два раскрытых прогона рядом — это снова каша.
    stateEls.forEach((_, other) => { if (other !== uid) closeRow(other); });
    p.panel.innerHTML = "";
    const stop = STOP_PANEL[t.status];
    if (stop) p.panel.appendChild($(stop));   // перемещение узла, а не копия
    else if (t.status === "done") fillDoneDrawer(t, p.panel);
    else return;                              // открывать нечего — строка молчит
    p.wrap.classList.add("is-open");
    p.hit.setAttribute("aria-expanded", "true");
  }

  function closeRow(uid) {
    const p = stateEls.get(uid);
    if (!p || !p.wrap.classList.contains("is-open")) return;
    // Панель возвращается в хранилище живой: узел тот же, обработчики те же.
    HITL_PANELS.forEach((id) => {
      const node = $(id);
      if (node && p.panel.contains(node)) $("stops").appendChild(node);
    });
    p.panel.innerHTML = "";
    p.wrap.classList.remove("is-open");
    p.hit.setAttribute("aria-expanded", "false");
  }

  // Готовая работа: ряд превью с прокруткой вбок и отметка исхода. Ряд, а не
  // сетка: двенадцать квадратов в сетке — ровно то, от чего ушли.
  function fillDoneDrawer(t, box) {
    const body = el("div", "work__done");
    const imgs = Array.isArray(t.images) ? t.images : [];
    if (imgs.length) {
      const strip = el("div", "strip");
      imgs.forEach((u, i) => {
        const b = el("button", "strip__item");
        b.type = "button";
        const img = el("img");
        img.loading = "lazy";
        img.src = P + u;
        img.alt = "Баннер " + (i + 1);
        b.appendChild(img);
        const pos = views.findIndex((v) => v.uid === t.task_uid && v.idx === i);
        b.addEventListener("click", () => openView(pos < 0 ? 0 : pos));
        strip.appendChild(b);
      });
      body.appendChild(strip);
    }
    body.appendChild(outcomeGroup(t));
    box.appendChild(body);
  }

  function fillActs(t) {
    const p = stateEls.get(t.task_uid);
    if (!p) return;
    p.actsKey = actsKeyOf(t);
    p.acts.innerHTML = "";
    if (AWAITING.indexOf(t.status) >= 0) {
      // Единственное, ради чего сюда вернулись: остановка ждёт человека.
      p.acts.appendChild(tBtn("Открыть решение", () => attach(t.task_uid), "t-btn--key"));
    } else if (t.status === "done" && t.result_url) {
      // Готовый прогон без картинок — файлы подчистила ретенция, но архив ещё жив.
      const a = el("a", "t-btn", "Скачать ZIP");
      a.href = P + t.result_url;
      a.setAttribute("download", "");
      p.acts.appendChild(a);
    }
  }

  function renderFeed() {
    feedList.innerHTML = "";
    stateEls.clear();
    views = [];
    for (const t of tasks) {
      feedList.appendChild(workRow(t));
      // Просмотр листает по всем баннерам ленты, поэтому плоский список
      // собирается независимо от того, раскрыта строка или нет.
      const imgs = Array.isArray(t.images) ? t.images : [];
      const cards = Array.isArray(t.cards) ? t.cards : [];
      imgs.forEach((u, i) => views.push({
        uid: t.task_uid, idx: i, url: u,
        caption: (cards[i] && cards[i].slogan) || "Баннер " + (i + 1),
      }));
    }
    // Счёт теперь считает работы, а не картинки: предмет ленты — работа.
    feedCount.textContent = tasks.length;
    feedEmpty.hidden = tasks.length > 0;
    // Сноска про срок хранения нужна, только когда есть что хранить.
    feedFoot.hidden = !tasks.length;
    paintRoute();
  }

  // Маршрут метит остановки активной задачи. Без задачи полоса гаснет целиком и
  // читается как оглавление — это её работа в покое, а не «состояние ноль».
  // Провалившийся и отменённый прогон полосу тоже гасят: замереть на середине
  // значило бы врать, что путь продолжается.
  function paintRoute() {
    if (!routeStops.length) return;
    const t = taskUid ? byUid(taskUid) : null;
    const live = t && ACTIVE.indexOf(t.status) >= 0;
    const stage = live ? (liveStage || STAGE_OF_STATUS[t.status] || 1) : 0;
    const waiting = live && AWAITING.indexOf(t.status) >= 0;
    const finished = !!t && t.status === "done";
    routeStops.forEach((li, i) => {
      const n = i + 1;
      let state = "";
      if (finished) state = " is-done";
      else if (!stage) state = "";
      else if (n < stage) state = " is-done";
      else if (n === stage) state = waiting ? " is-wait" : " is-live";
      li.className = "route__stop" + state;
    });
  }

  // Точечное обновление строки под поток событий. Пересобираем ленту целиком
  // только тогда, когда строки для этой задачи в ней нет — то есть задача
  // появилась только что.
  // className переписывается целиком, поэтому раскрытие пришлось бы схлопнуть:
  // is-open возвращаем руками, иначе ящик закрывался бы на каждом событии шага
  // прямо под руками у человека, который в нём читает.
  function syncState(uid) {
    const t = byUid(uid);
    if (!t) return;
    const p = stateEls.get(uid);
    if (!p) { renderFeed(); return; }
    const open = p.wrap.classList.contains("is-open");
    p.wrap.className = rowClass(t) + (open ? " is-open" : "");
    p.line.textContent = stateText(t);
    p.mark.className = markClass(t);
    p.mark.textContent = OUTCOME_LABEL[t.outcome] || "";
    p.mark.hidden = !t.outcome;
    p.keep.textContent = keepText(t);
    p.bar.hidden = !(t.status === "queued" || t.status === "running");
    paintRoute();
    if (p.actsKey !== actsKeyOf(t)) fillActs(t);
  }

  async function loadTasks() {
    try {
      const r = await fetch(`${P}/api/tasks`);
      if (!r.ok) return;
      const list = await r.json();
      tasks = Array.isArray(list) ? list : [];
      renderFeed();
    } catch (_) { /* на пустой ленте молчим: сеть вернётся — вернётся и список */ }
  }

  // ── запуск ─────────────────────────────────────────────
  $("briefForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const product = $("product").value.trim();
    const audience = $("audience").value.trim();
    const emotion = $("emotion").value.trim();
    const notes = $("notes").value.trim();
    const source_url = $("sourceUrl").value.trim();
    const product_slug = $("productSlug").value || "auto";
    if (!product || !audience || !emotion) {
      $("briefStatus").textContent = "Заполни продукт, аудиторию и эмоцию.";
      return;
    }
    $("startBtn").disabled = true;
    $("briefStatus").textContent = "Создаю задачу…";
    try {
      const r = await fetch(`${P}/api/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product, audience, emotion, notes, source_url, product_slug }),
      });
      if (!r.ok) throw new Error(errText(r.status));
      const data = await r.json();
      taskUid = data.task_uid;
      saveActive(taskUid);
      $("briefStatus").textContent = "";
      // Плитка появляется сразу, до первого события: между POST и первым шагом
      // проходит секунда-другая, и без неё лента выглядела бы не отреагировавшей.
      tasks.unshift({
        task_uid: taskUid, status: "queued", prompt: product,
        images: [], cards: [], brief: {}, created_at: new Date().toISOString(),
      });
      liveStep = "Запуск…";
      liveStage = 0; // маршрут начинается заново — до первого события его ведёт статус
      renderFeed();
      subscribe();
    } catch (e) {
      $("briefStatus").innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
      $("startBtn").disabled = false;
    }
  });

  // ── библиотека знаний: карточки в селект брифа ──────────
  // Сбой не должен ломать бриф — остаются "auto"/"none".
  async function loadProducts() {
    try {
      const r = await fetch(`${P}/api/kb/products`);
      if (!r.ok) return;
      const items = await r.json();
      const sel = $("productSlug");
      items.forEach((p) => {
        const o = document.createElement("option");
        o.value = p.slug;
        o.textContent = p.tagline ? `${p.name} — ${p.tagline}` : p.name;
        sel.appendChild(o);
      });
    } catch (_) {}
  }

  // ── SSE ────────────────────────────────────────────────
  // A dropped connection (gateway restart, wifi blip) also fires an "error"
  // event, but WITHOUT data — that must NOT be rendered as a task failure.
  // Instead we resync state via /pending and reattach with backoff.
  let esBackoff = 1000;
  const ES_BACKOFF_MAX = 15000;
  function subscribe() {
    if (es) es.close();
    es = new EventSource(`${P}/api/tasks/${taskUid}/events`);
    es.onopen = () => { esBackoff = 1000; };
    es.addEventListener("queued", () => setStep("В очереди…", "queued"));
    es.addEventListener("start", (e) => onStep(e));
    es.addEventListener("step", (e) => onStep(e));
    es.addEventListener("awaiting_input", (e) => onAwaiting(JSON.parse(e.data)));
    es.addEventListener("resumed", () => { closeHitl(); setStep("Продолжаю…"); });
    es.addEventListener("done", (e) => onDone(JSON.parse(e.data)));
    es.addEventListener("error", (e) => {
      if (typeof e.data === "undefined") { onStreamDrop(); return; } // connection, not task
      onError(e);
    });
    es.addEventListener("cancelled", (e) => onCancelled(JSON.parse(e.data)));
  }
  // Событие шага несёт подпись для плитки и номер остановки для маршрута.
  // stage === null значит «шаг остановку не меняет» — полосу оставляем как есть,
  // иначе продолжение сегмента сбрасывало бы подсветку назад.
  function onStep(e) {
    let d = {};
    try { d = JSON.parse(e.data) || {}; } catch (_) {}
    if (typeof d.stage === "number") liveStage = d.stage;
    setStep(d.step || "…");
  }

  function setStep(text, status) {
    liveStep = text;
    const t = byUid(taskUid);
    if (t) t.status = status || "running";
    syncState(taskUid);
  }

  function onStreamDrop() {
    if (!taskUid) return;
    if (es) es.close();
    setStep("Связь потеряна, переподключаюсь…");
    const delay = esBackoff;
    esBackoff = Math.min(esBackoff * 2, ES_BACKOFF_MAX);
    setTimeout(resync, delay);
  }

  // Re-fetch where the task actually is (events may have been missed while
  // offline), re-render that state, then resubscribe.
  async function resync() {
    if (!taskUid) return;
    try {
      const r = await fetch(`${P}/api/tasks/${taskUid}/pending`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      if (d.phase) { onAwaiting(d); subscribe(); return; }
      if (ACTIVE.indexOf(d.status) >= 0) { subscribe(); return; }
      // went terminal while we were offline
      const tr = await fetch(`${P}/api/tasks/${taskUid}`);
      const t = tr.ok ? await tr.json() : null;
      if (t && t.status === "done") onDone({ result_url: t.result_url });
      else if (t && t.status === "cancelled") onCancelled({ reason: "" });
      else onError({ data: JSON.stringify({ message: (t && t.error) || "Сбой генерации." }) });
    } catch (_) {
      onStreamDrop(); // network still down → keep backing off
    }
  }

  // ── остановки пайплайна (HITL) ─────────────────────────
  // Остановка — состояние задачи, а не отдельный экран: строка метится и
  // раскрывается ящиком на месте, лента при этом остаётся на экране.
  function onAwaiting(d, silent) {
    const status = STATUS_OF_PHASE[d.phase];
    const t = byUid(taskUid);
    if (t && status) t.status = status;
    liveStep = STATUS_LABEL[status] || "Ждёт решения";
    // Фаза — источник правды о том, где задача встала: восстановление после
    // перезагрузки приходит сюда без единого события шага.
    liveStage = STAGE_OF_STATUS[status] || liveStage;
    syncState(taskUid);
    if (d.phase === "persona_approve") {
      renderPersona(d.persona || {}, d.kb_match);
      setBusy($("personaPanel"), false);
      openStop(silent);
    } else if (d.phase === "text_approve") {
      renderCandidates(d.candidates || []);
      setBusy($("textPanel"), false);
      openStop(silent);
    } else if (d.phase === "image_upload") {
      $("imagePrompt").textContent = d.image_prompt || "(пусто)";
      renderMetaphor(d);
      if (d.can_generate) show($("genBtn")); else hide($("genBtn"));
      let msg = "";
      // gen_error приходит уже человеческой фразой (app/tasks/errors.py),
      // поэтому обрамлять её ещё раз не нужно — только добавить второй выход.
      if (d.gen_error) msg = `${d.gen_error} Либо загрузи свою картинку.`;
      else if (!d.can_generate) msg = "Автогенерация hero на сервере недоступна — загрузи свою картинку.";
      $("imageStatus").textContent = msg;
      setBusy($("imagePanel"), false);
      openStop(silent);
    }
  }

  // Остановка открывает СВОЮ строку. Раньше здесь выскакивало окно поверх
  // ленты; окно закрывали — и работа терялась из виду, хотя ждала человека.
  function openStop(silent) {
    if (silent) return;   // восстановление после перезагрузки: строка ждёт клика
    openRow(taskUid);
  }

  // Клик по ждущей строке: перецепляемся к этой задаче и открываем её разбор.
  async function attach(uid) {
    taskUid = uid;
    saveActive(uid);
    try {
      const r = await fetch(`${P}/api/tasks/${uid}/pending`);
      if (!r.ok) return;
      const d = await r.json();
      if (d.phase) { onAwaiting(d); subscribe(); }
      else { setStep("Продолжаю…"); subscribe(); }
    } catch (_) {}
  }

  // ── лайтбокс: один режим — просмотр баннера ────────────
  // Остановки отсюда ушли в ящик строки, поэтому переключателя режимов больше
  // нет: рама показывает картинку и колонку действий к ней, и только их.
  const lightbox = $("lightbox");
  let lbOpen = false;

  function openLb() {
    show($("lbView"));
    show($("lbSide"));
    lbOpen = true;
    show(lightbox);
    document.body.style.overflow = "hidden";
  }
  function closeLb() {
    hide(lightbox);
    lbOpen = false;
    document.body.style.overflow = "";
  }
  // Пайплайн поехал дальше — ящик закрываем, просмотр баннера не трогаем.
  function closeHitl() { if (taskUid) closeRow(taskUid); }

  lightbox.addEventListener("click", (ev) => {
    const act = ev.target.getAttribute && ev.target.getAttribute("data-lb");
    if (act === "close") closeLb();
    else if (act === "prev") viewNav(-1);
    else if (act === "next") viewNav(1);
  });
  // Свернуть ящик можно из самой панели остановки: она лежит в строке, и
  // «закрывать» там нечего — только сложить обратно.
  document.addEventListener("click", (ev) => {
    const el0 = ev.target;
    if (!el0 || !el0.getAttribute || el0.getAttribute("data-stop") !== "close") return;
    if (taskUid) closeRow(taskUid);
  });
  document.addEventListener("keydown", (ev) => {
    if (!lbOpen) return;
    if (ev.key === "Escape") { closeLb(); return; }
    if (ev.key === "ArrowRight") viewNav(1);
    else if (ev.key === "ArrowLeft") viewNav(-1);
  });

  // ── просмотр баннера ───────────────────────────────────
  let viewPos = 0;
  function openView(pos) {
    if (!views.length) return;
    viewPos = (pos + views.length) % views.length;
    renderView();
    openLb();
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
  function details(summary, bodyHtml) {
    const d = el("details", "tool-details");
    d.appendChild(el("summary", null, summary));
    const b = el("div", "tool-details__body");
    b.innerHTML = bodyHtml;
    d.appendChild(b);
    return d;
  }

  function buildViewActions(t, v) {
    const box = $("lbActions");
    box.innerHTML = "";

    const save = group("");
    const dl = el("a", "t-btn t-btn--key", "Скачать баннер");
    dl.href = P + v.url;
    dl.download = fileNameOf(v.url);
    save._row.appendChild(dl);
    if (t.result_url) {
      const zip = el("a", "t-btn", "Скачать ZIP");
      zip.href = P + t.result_url;
      zip.setAttribute("download", "");
      save._row.appendChild(zip);
    }
    box.appendChild(save);

    // Карточка этого баннера (Block 3), выровнена по индексу с файлами 01_…, 02_…
    const card = (Array.isArray(t.cards) ? t.cards : [])[v.idx];
    if (card) {
      const rows = kv("cta", card.cta) + kv("hook", card.hook_angle) +
        kv("почему зайдёт ЦА", card.reason) + kv("идея", card.body);
      if (rows) box.appendChild(details("Что в этом баннере", rows));
    }

    const brief = t.brief || {};
    const briefRows = Object.keys(BRIEF_LABELS)
      .filter((k) => brief[k]).map((k) => kv(BRIEF_LABELS[k], brief[k])).join("");
    if (briefRows) box.appendChild(details("Бриф запуска", briefRows));

    box.appendChild(recipeDetails(t));
  }

  // Рецепт лежит в задаче, а не в списке — дочитываем его при первом раскрытии.
  function recipeDetails(t) {
    const d = el("details", "tool-details");
    d.appendChild(el("summary", null, "Как сделан этот баннер"));
    const b = el("div", "tool-details__body");
    b.innerHTML = '<p class="muted">Читаю…</p>';
    d.appendChild(b);
    let loaded = false;
    d.addEventListener("toggle", async () => {
      if (!d.open || loaded) return;
      loaded = true;
      try {
        const r = await fetch(`${P}/api/tasks/${t.task_uid}`);
        const j = r.ok ? await r.json() : null;
        b.innerHTML = recipeHtml(j && j.recipe) || '<p class="muted">Рецепт не сохранён.</p>';
      } catch (_) {
        b.innerHTML = '<p class="muted">Не удалось прочитать рецепт.</p>';
      }
    });
    return d;
  }
  function recipeHtml(rec) {
    if (!rec || !Object.keys(rec).length) return "";
    const kbs = rec.kb_source;
    const HERO = { generated: "сгенерирован на сервере", uploaded: "загружен вручную", none: "нет" };
    const comments = Array.isArray(rec.metaphor_comments) ? rec.metaphor_comments.join("; ") : "";
    return (
      kv("карточка знаний", kbs ? `${kbs.name} (версия ${kbs.version})` : "не использовалась") +
      kv("персона", rec.persona_segment) +
      kv("ведущий текст", rec.slogan) +
      kv("якорь персоны", rec.anchor) +
      kv("что человек получит", rec.desired_outcome) +
      kv("образ", rec.metaphor) +
      kv("читатель должен подумать", rec.intended_inference) +
      kv("не должно читаться как", rec.anti_reading) +
      kv("комментарии к образу", comments) +
      kv("hero", HERO[rec.hero_source] || rec.hero_source)
    );
  }

  // Исход почти никогда не известен в момент финиша: баннеры уносят
  // согласовывать, а вкладку закрывают. Отметка стоит на РАБОТЕ, а не на
  // каждом баннере: в кампанию уходит запуск целиком, и двенадцать одинаковых
  // вопросов «что стало с этим баннером» были той же кашей, что и плитки.
  function outcomeGroup(t) {
    const g = el("div", "lb-group");
    g.appendChild(el("p", "lb-group__label", "Что стало с этим баннером"));
    g.appendChild(el("p", "muted", "Ответ попадёт в опыт и повлияет на следующие запуски по этому продукту."));
    const form = el("div", "gen-form");
    const lab = document.createElement("label");
    lab.appendChild(el("span", "field-label", "Комментарий"));
    const ta = document.createElement("textarea");
    ta.placeholder = "Почему взяли или почему нет";
    ta.value = t.outcome_comment || "";
    lab.appendChild(ta);
    form.appendChild(lab);
    g.appendChild(form);
    const row = el("div", "lb-group__row");
    const st = el("div", "status");
    const mk = (val, text, key) => {
      const b = el("button", "t-btn" + (key ? " t-btn--key" : "") + (t.outcome === val ? " t-btn--on" : ""), text);
      b.type = "button";
      b.dataset.outcome = val;
      b.addEventListener("click", () => sendOutcome(t, val, ta.value.trim(), st, row));
      return b;
    };
    row.appendChild(mk("shipped", "Пошёл в кампанию", true));
    row.appendChild(mk("rejected", "Отклонили", false));
    g.appendChild(row);
    g.appendChild(st);
    return g;
  }

  async function sendOutcome(t, outcome, comment, statusEl, row) {
    setBusy(row, true);
    const r = await post(`${P}/api/tasks/${t.task_uid}/outcome`, { outcome, comment });
    setBusy(row, false);
    if (!(r && r.ok)) {
      statusEl.innerHTML = `<span class="err">Не удалось записать: ${escapeHtml(errText(r ? r.status : 0))}</span>`;
      return;
    }
    const d = await r.json();
    statusEl.textContent = d.recorded ? "Записано в опыт." : "Исход обновлён.";
    t.outcome = outcome;
    t.outcome_comment = comment;
    row.querySelectorAll("[data-outcome]").forEach((b) => {
      b.classList.toggle("t-btn--on", b.dataset.outcome === outcome);
    });
    // Исход теперь у работы, а не у каждого баннера: перерисовывать всю ленту
    // незачем, меняется одна строка.
    syncState(t.task_uid);
  }

  // ── 03 · предложения ───────────────────────────────────
  const PICK_ON = "Главный — это баннер №1";
  const PICK_OFF = "Сделать главным";

  function renderCandidates(list) {
    winnerId = list.length ? (list[0].id || null) : null;
    const box = $("candidates");
    if (!list.length) { box.innerHTML = '<p class="muted">Нет предложений.</p>'; return; }
    box.innerHTML = list.map((c, i) => {
      const score = (typeof c.score === "number") ? c.score.toFixed(1) : "";
      const id = c.id || "";
      const head = `<div class="cand__head"><span class="cand__rank">#${i + 1}</span>` +
        `<span class="cand__slogan">${escapeHtml(c.slogan || "")}</span>` +
        (i === 0 ? `<span class="cand__badge">главный</span>` : "") +
        (score ? `<span class="cand__score">${score}</span>` : "") + `</div>`;
      // Флажки линта (блок 3): информируют, не гейтят — решает человек.
      const flags = (Array.isArray(c.lint_flags) && c.lint_flags.length)
        ? `<div class="cand__flags">${c.lint_flags.map((f) => `<span class="cand__flag">${escapeHtml(f)}</span>`).join("")}</div>`
        : "";
      // Обоснование под раскрытием: якорь персоны и обещанный результат —
      // то, из чего кандидат вырос, а не пересказ слогана.
      const why = (c.anchor || c.desired_outcome || c.reason)
        ? `<details class="tool-details"><summary>Почему такой текст</summary>` +
          `<div class="tool-details__body">` +
          kv("якорь персоны", c.anchor) + kv("что человек получит", c.desired_outcome) +
          kv("почему зайдёт ЦА", c.reason) + `</div></details>`
        : "";
      // «Ведёт эта / Взять эту» не отвечало на вопрос «ведёт куда»: баннер
      // получат все двенадцать. Называем последствие — главный идёт первым.
      const pick = id
        ? `<button type="button" class="t-btn${i === 0 ? " t-btn--key" : ""}" data-pick="${escapeHtml(id)}">` +
          `${i === 0 ? PICK_ON : PICK_OFF}</button>`
        : "";
      return `<div class="cand${i === 0 ? " is-winner" : ""}">${head}` +
        kv("cta", c.cta) + kv("hook", c.hook_angle) +
        kv("идея", c.body) + why + flags + pick + `</div>`;
    }).join("");
  }

  // Делегированный выбор победителя: перекрашиваем карточки, ничего не шлём —
  // решение уходит одним запросом по «Принять».
  $("candidates").addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-pick]");
    if (!btn) return;
    winnerId = btn.dataset.pick;
    $("candidates").querySelectorAll("[data-pick]").forEach((b) => {
      const on = b.dataset.pick === winnerId;
      b.classList.toggle("t-btn--key", on);
      b.textContent = on ? PICK_ON : PICK_OFF;
      // Планка и бейдж переезжают на выбранную карточку целиком: кнопка одна
      // среди двенадцати одинаковых, и одной её подсветки человек не находил.
      const card = b.closest(".cand");
      card.classList.toggle("is-winner", on);
      const badge = card.querySelector(".cand__badge");
      if (on && !badge) {
        // Строго перед оценкой, а не в конец шапки: при вставке в конец бейдж
        // и оценка менялись бы местами относительно первой отрисовки.
        const head = card.querySelector(".cand__head");
        const score = head.querySelector(".cand__score");
        const html = `<span class="cand__badge">главный</span>`;
        if (score) score.insertAdjacentHTML("beforebegin", html);
        else head.insertAdjacentHTML("beforeend", html);
      } else if (!on && badge) {
        badge.remove();
      }
    });
  });

  document.querySelectorAll("#textPanel [data-act]").forEach((btn) => {
    btn.addEventListener("click", () => sendText(btn.dataset.act));
  });

  async function sendText(action) {
    if (action === "cancel" && !window.confirm("Отменить задачу? Прогресс будет потерян.")) return;
    const panel = $("textPanel");
    $("textStatus").textContent = "";
    setBusy(panel, true);
    const body = { action };
    if (action === "approve" && winnerId) body.winner_id = winnerId;
    const r = await post(`${P}/api/tasks/${taskUid}/decision/text`, body);
    if (r && r.ok) {
      setBusy(panel, false);
      closeHitl();
      setStep("Применяю решение…");
      return;
    }
    setBusy(panel, false); // stay on the panel so the user can retry
    $("textStatus").innerHTML = `<span class="err">${escapeHtml(errText(r ? r.status : 0))}</span>`;
  }

  // ── 02 · персона ───────────────────────────────────────
  // Списки якорей редактируются как многострочный текст — одна строка = один
  // якорь. Это ровно та форма, в которой их читает промпт.
  const linesOf = (v) => (Array.isArray(v) ? v.join("\n") : "");
  const listOf = (id) => $(id).value.split("\n").map((s) => s.trim()).filter(Boolean);

  function renderPersona(p, kb) {
    $("personaSegment").value = p.segment || "";
    $("personaAge").value = p.age_range || "";
    $("personaPains").value = linesOf(p.pain_points);
    $("personaMotivations").value = linesOf(p.motivations);
    $("personaObjections").value = linesOf(p.objections);
    $("personaStyle").value = p.communication_style || "";
    $("personaKb").textContent = kb && kb.slug
      ? `Карточка знаний: ${kb.name} (версия ${kb.version})`
      : "Карточка знаний не подобрана — тексты опираются только на бриф.";
  }

  document.querySelectorAll("#personaPanel [data-pact]").forEach((btn) => {
    btn.addEventListener("click", () => sendPersona(btn.dataset.pact));
  });

  async function sendPersona(action) {
    if (action === "cancel" && !window.confirm("Отменить задачу? Прогресс будет потерян.")) return;
    const pains = listOf("personaPains");
    const motivations = listOf("personaMotivations");
    if (action === "approve" && (!pains.length || !motivations.length)) {
      $("personaStatus").innerHTML = '<span class="err">Нужна хотя бы одна боль и одна мотивация — из них собираются тексты.</span>';
      return;
    }
    const body = { action };
    if (action === "approve") {
      body.persona = {
        segment: $("personaSegment").value.trim(),
        age_range: $("personaAge").value.trim(),
        pain_points: pains,
        motivations: motivations,
        objections: listOf("personaObjections"),
        communication_style: $("personaStyle").value.trim(),
      };
    }
    const panel = $("personaPanel");
    $("personaStatus").textContent = "";
    setBusy(panel, true);
    const r = await post(`${P}/api/tasks/${taskUid}/decision/persona`, body);
    if (r && r.ok) {
      setBusy(panel, false);
      // regenerate возвращает граф на эту же остановку, а не двигает дальше.
      closeHitl();
      setStep(action === "approve" ? "Пишу тексты…" : "Обновляю персону…");
      return;
    }
    setBusy(panel, false);
    $("personaStatus").innerHTML = `<span class="err">${escapeHtml(errText(r ? r.status : 0))}</span>`;
  }

  // ── 04 · hero ──────────────────────────────────────────
  async function sendImage(fd, progressLabel) {
    const panel = $("imagePanel");
    $("imageStatus").textContent = "";
    setBusy(panel, true);
    const r = await postForm(`${P}/api/tasks/${taskUid}/decision/image`, fd);
    if (r && r.ok) {
      setBusy(panel, false);
      closeHitl();
      setStep(progressLabel);
      return;
    }
    setBusy(panel, false); // stay on the panel so the user can retry
    $("imageStatus").innerHTML = `<span class="err">${escapeHtml(errText(r ? r.status : 0))}</span>`;
  }

  // Задумка образа. Блока нет, если generate_image_prompt не отдал meta —
  // экран тогда прежний: один промпт без разговора.
  function renderMetaphor(d) {
    const box = $("metaphorBox");
    if (!d.metaphor) { hide(box); return; }
    $("mIdea").textContent = d.metaphor;
    $("mInfer").textContent = d.intended_inference || "—";
    $("mAnti").textContent = d.anti_reading || "—";
    // Обрыв SSE тоже приводит сюда (resync), а набранный комментарий — весь
    // смысл этой остановки: чистим поле, только когда образ реально сменился.
    if (box.dataset.shown !== d.metaphor) {
      $("metaphorComment").value = "";
      box.dataset.shown = d.metaphor;
    }
    show(box);
  }

  $("metaphorBtn").addEventListener("click", () => {
    const comment = $("metaphorComment").value.trim();
    if (!comment) {
      $("imageStatus").innerHTML = '<span class="err">Напиши, что не так с образом — иначе переделывать нечего.</span>';
      return;
    }
    const fd = new FormData();
    fd.append("action", "metaphor");
    fd.append("comment", comment);
    sendImage(fd, "Переделываю образ…");
  });
  $("genBtn").addEventListener("click", () => {
    const fd = new FormData(); fd.append("action", "generate");
    sendImage(fd, "Генерирую 12 hero…");
  });
  $("heroFile").addEventListener("change", (ev) => {
    const f = ev.target.files[0]; if (!f) return;
    ev.target.value = ""; // re-selecting the same file must re-trigger change
    const fd = new FormData(); fd.append("action", "upload"); fd.append("file", f);
    sendImage(fd, "Загружаю картинку…");
  });
  $("imgCancel").addEventListener("click", () => {
    if (!window.confirm("Отменить задачу? Прогресс будет потерян.")) return;
    const fd = new FormData(); fd.append("action", "cancel");
    sendImage(fd, "Отменяю…");
  });

  // ── терминальные состояния ─────────────────────────────
  async function onDone(d) {
    if (es) es.close();
    clearActive(); resetWinner();
    liveStep = ""; liveStage = 0;
    closeHitl();
    $("startBtn").disabled = false;
    $("briefStatus").textContent = d.result_url
      ? "Готово — баннеры в ленте."
      : "Готово, но файл результата не найден.";
    await loadTasks();
  }

  function onError(e) {
    if (es) es.close();
    clearActive(); resetWinner();
    let msg = "Сбой генерации.";
    try { msg = JSON.parse(e.data).message || msg; } catch (_) {}
    liveStep = ""; liveStage = 0;
    const t = byUid(taskUid);
    if (t) { t.status = "failed"; t.error = msg; }
    closeHitl();
    syncState(taskUid);
    $("startBtn").disabled = false;
    $("briefStatus").innerHTML = `<span class="err">${escapeHtml(msg)}</span>`;
    loadTasks();
  }

  function onCancelled(d) {
    if (es) es.close();
    clearActive(); resetWinner();
    liveStep = ""; liveStage = 0;
    const t = byUid(taskUid);
    if (t) t.status = "cancelled";
    closeHitl();
    syncState(taskUid);
    $("briefStatus").textContent = d.reason === "timeout"
      ? "Время истекло, сессия отменена." : "Отменено.";
    $("startBtn").disabled = false;
    loadTasks();
  }

  // ── helpers ────────────────────────────────────────────
  // Both return the Response (or null on network failure); the caller renders
  // the error in ITS panel and decides whether to restore the HITL UI.
  async function post(url, body) {
    try {
      return await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    } catch (_) { return null; }
  }
  async function postForm(url, fd) {
    try {
      return await fetch(url, { method: "POST", body: fd });
    } catch (_) { return null; }
  }
  // Double-click protection: freeze every control in a HITL panel while its
  // request is in flight; unfreeze if the request fails (panel stays visible).
  // label.t-btn — кнопка загрузки файла: свойства disabled у <label> нет, её
  // держит только класс is-busy.
  function setBusy(scope, busy) {
    scope.querySelectorAll("button, input, textarea, label.t-btn").forEach((e) => {
      if ("disabled" in e) e.disabled = busy;
      e.classList.toggle("is-busy", busy);
    });
  }
  function errText(status) {
    if (!status) return "Нет соединения с сервером — проверь сеть и попробуй ещё раз.";
    if (status === 429) return "Сервер занят — попробуй ещё раз через пару минут.";
    if (status === 409) return "Задача уже в другом состоянии — обнови страницу.";
    if (status === 501) return "Генерация недоступна — загрузи картинку.";
    // 422 на этих роутах приезжает от валидации тела: пустой или слишком
    // длинный комментарий, чужой action. Без ветки пользователь видел голое
    // «Ошибка 422» и не понимал, что поправить надо у себя в поле.
    if (status === 422) return "Проверь поля — комментарий пустой или слишком длинный.";
    return `Ошибка ${status}`;
  }

  // ── восстановление активной задачи при загрузке ────────
  // Canon-header navigation is a full reload that drops JS state + the
  // EventSource, but the run lives on server-side (detached create_task). Find
  // the active task (localStorage, else /api/tasks), snapshot its state via
  // /pending (EventBus has no replay, so snapshot BEFORE resubscribing), then
  // reattach the stream.
  function findActiveUid() {
    const stored = (() => { try { return localStorage.getItem(LS_KEY); } catch (_) { return null; } })();
    if (stored) return stored;
    const t = tasks.find((x) => ACTIVE.indexOf(x.status) >= 0);
    return t ? t.task_uid : null;
  }

  async function rehydrate() {
    const uid = findActiveUid();
    if (!uid) return;
    taskUid = uid;
    try {
      const r = await fetch(`${P}/api/tasks/${uid}/pending`);
      if (!r.ok) { clearActive(); return; }
      const d = await r.json();
      if (d.phase) {
        // parked at a HITL pause → плитка метится, разбор ждёт клика
        saveActive(uid);
        $("startBtn").disabled = true;
        onAwaiting(d, true);
        subscribe();
      } else if (ACTIVE.indexOf(d.status) >= 0) {
        // still computing → show progress and reattach the stream
        saveActive(uid);
        $("startBtn").disabled = true;
        setStep("Продолжаю…", d.status);
        subscribe();
      } else {
        clearActive();
      }
    } catch (_) { /* leave the brief form as-is on any error */ }
  }

  (async function boot() {
    await loadTasks();   // лента до восстановления: rehydrate правит уже готовую плитку
    await rehydrate();
    loadProducts();
  })();
})();
