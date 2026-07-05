/* App3 / creatives — brief → SSE progress → 2 HITL pauses → results.
   All URLs go through the gateway prefix (window.APP_PREFIX). */
(function () {
  "use strict";
  const P = window.APP_PREFIX || "";
  const $ = (id) => document.getElementById(id);
  const show = (el) => { el.classList.remove("hidden"); updateEmpty(); };
  const hide = (el) => { el.classList.add("hidden"); updateEmpty(); };

  // Канон v4: empty-state в правой колонке виден только пока там нет контента
  // (прогресс/HITL/результаты/история). Пересчитывается на каждом show/hide.
  const OUTPUT_PANELS = ["progressPanel", "textPanel", "imagePanel", "resultsPanel", "tasksPanel"];
  function updateEmpty() {
    const empty = $("emptyState");
    if (!empty) return;
    const hasContent = OUTPUT_PANELS.some((id) => {
      const p = $(id);
      return p && !p.classList.contains("hidden");
    });
    empty.classList.toggle("hidden", hasContent);
  }

  let taskUid = null;
  let es = null; // EventSource
  // Active-task handle survives the full page reload that canon-header
  // navigation does (links to /images /slides /creatives are absolute). The
  // run keeps going server-side; on load we rehydrate from here or /api/tasks.
  const LS_KEY = "app3_active_task";
  const ACTIVE = ["queued", "running", "awaiting_text", "awaiting_image"];
  const saveActive = (uid) => { try { localStorage.setItem(LS_KEY, uid); } catch (_) {} };
  const clearActive = () => { try { localStorage.removeItem(LS_KEY); } catch (_) {} };

  // ── start ──────────────────────────────────────────────
  $("startBtn").addEventListener("click", async () => {
    const product = $("product").value.trim();
    const audience = $("audience").value.trim();
    const emotion = $("emotion").value.trim();
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
        body: JSON.stringify({ product, audience, emotion }),
      });
      if (!r.ok) throw new Error(errText(r.status));
      const data = await r.json();
      taskUid = data.task_uid;
      saveActive(taskUid);
      $("briefStatus").textContent = "";
      // restore the step line (a previous onError replaces this markup)
      $("progress").innerHTML =
        '<span class="step"><span class="dot"></span><span id="stepLabel">Запуск…</span></span>';
      hide($("resultsPanel"));
      show($("progressPanel"));
      subscribe();
    } catch (e) {
      $("briefStatus").innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
      $("startBtn").disabled = false;
    }
  });

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
    es.addEventListener("queued", () => setStep("В очереди…"));
    es.addEventListener("start", (e) => setStep(stepOf(e)));
    es.addEventListener("step", (e) => setStep(stepOf(e)));
    es.addEventListener("awaiting_input", (e) => onAwaiting(JSON.parse(e.data)));
    es.addEventListener("resumed", () => { hideHitl(); setStep("Продолжаю…"); });
    es.addEventListener("done", (e) => onDone(JSON.parse(e.data)));
    es.addEventListener("error", (e) => {
      if (typeof e.data === "undefined") { onStreamDrop(); return; } // connection, not task
      onError(e);
    });
    es.addEventListener("cancelled", (e) => onCancelled(JSON.parse(e.data)));
  }
  const stepOf = (e) => { try { return JSON.parse(e.data).step || "…"; } catch { return "…"; } };
  function setStep(t) {
    const el = $("stepLabel");
    if (el) el.textContent = t;
    show($("progressPanel"));
  }

  function onStreamDrop() {
    if (!taskUid) return;
    if (es) es.close();
    setStep("Соединение потеряно, переподключаюсь…");
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
      if (ACTIVE.includes(d.status)) { subscribe(); return; }
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

  // ── awaiting (HITL) ────────────────────────────────────
  // A HITL pause means the pipeline is BLOCKED on the user. Hide the running
  // progress bar (its indeterminate animation + pulsing dot read as "still
  // working") so it's unambiguous that the user must now act, then scroll the
  // decision panel into view.
  function onAwaiting(d) {
    hide($("progressPanel"));
    if (d.phase === "text_approve") {
      renderCandidates(d.candidates || []);
      setBusy($("textPanel"), false);
      hide($("imagePanel")); show($("textPanel"));
      focusPanel($("textPanel"));
    } else if (d.phase === "image_upload") {
      $("imagePrompt").textContent = d.image_prompt || "(пусто)";
      if (d.can_generate) show($("genBtn")); else hide($("genBtn"));
      let msg = "";
      if (d.gen_error) msg = `Генерация не удалась: ${d.gen_error}. Загрузи картинку.`;
      else if (!d.can_generate) msg = "Автогенерация hero на сервере недоступна — загрузи свою картинку.";
      $("imageStatus").textContent = msg;
      setBusy($("imagePanel"), false);
      hide($("textPanel")); show($("imagePanel"));
      focusPanel($("imagePanel"));
    }
  }
  function focusPanel(el) {
    try { el.scrollIntoView({ behavior: "smooth", block: "start" }); } catch (_) {}
  }
  function renderCandidates(list) {
    if (!list.length) { $("candidates").innerHTML = "<p class=\"page-sub\">Нет предложений.</p>"; return; }
    $("candidates").innerHTML = list.map((c, i) => {
      const rank = i + 1;
      const score = (typeof c.score === "number") ? c.score.toFixed(1) : "";
      const head = `<div class="cand-head"><span class="cand-rank">#${rank}</span>` +
        `<span class="cand-slogan">${escapeHtml(c.slogan || "")}</span>` +
        (score ? `<span class="cand-score">${score}</span>` : "") + `</div>`;
      return `<div class="cand-card">${head}` +
        kv("cta", c.cta) + kv("hook", c.hook_angle) +
        kv("почему зайдёт ЦА", c.reason) + kv("идея", c.body) + `</div>`;
    }).join("");
  }
  const kv = (k, v) => v ? `<div class="kv"><b>${k}:</b> ${escapeHtml(v)}</div>` : "";

  function hideHitl() { hide($("textPanel")); hide($("imagePanel")); }

  // Double-click protection: freeze every control in a HITL panel while its
  // request is in flight; unfreeze if the request fails (panel stays visible).
  function setBusy(panel, busy) {
    panel.querySelectorAll("button, input, label.btn").forEach((el) => {
      if ("disabled" in el) el.disabled = busy;
      el.classList.toggle("is-busy", busy);
    });
  }
  function errText(status) {
    if (!status) return "Нет соединения с сервером — проверь сеть и попробуй ещё раз.";
    if (status === 429) return "Сервер занят — попробуй ещё раз через пару минут.";
    if (status === 409) return "Задача уже в другом состоянии — обнови страницу.";
    if (status === 501) return "Генерация недоступна — загрузи картинку.";
    return `Ошибка ${status}`;
  }

  // text decisions (approve all / regenerate all / cancel)
  document.querySelectorAll("#textPanel [data-act]").forEach((btn) => {
    btn.addEventListener("click", () => sendText(btn.dataset.act));
  });

  async function sendText(action) {
    if (action === "cancel" && !window.confirm("Отменить задачу? Прогресс будет потерян.")) return;
    const panel = $("textPanel");
    $("textStatus").textContent = "";
    setBusy(panel, true);
    const r = await post(`${P}/api/tasks/${taskUid}/decision/text`, { action });
    if (r && r.ok) {
      setBusy(panel, false);
      hideHitl(); setStep("Применяю решение…");
      return;
    }
    setBusy(panel, false); // stay on the panel so the user can retry
    $("textStatus").innerHTML = `<span class="err">${escapeHtml(errText(r ? r.status : 0))}</span>`;
  }

  // image decisions
  async function sendImage(fd, progressLabel) {
    const panel = $("imagePanel");
    $("imageStatus").textContent = "";
    setBusy(panel, true);
    const r = await postForm(`${P}/api/tasks/${taskUid}/decision/image`, fd);
    if (r && r.ok) {
      setBusy(panel, false);
      hideHitl(); setStep(progressLabel);
      return;
    }
    setBusy(panel, false); // stay on the panel so the user can retry
    $("imageStatus").innerHTML = `<span class="err">${escapeHtml(errText(r ? r.status : 0))}</span>`;
    show($("imagePanel"));
  }
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

  // ── terminal ───────────────────────────────────────────
  function onDone(d) {
    if (es) es.close();
    clearActive();
    hideHitl(); hide($("progressPanel")); show($("resultsPanel"));
    const url = d.result_url ? `${P}${d.result_url}` : null;
    $("resultMsg").innerHTML = url
      ? `<a class="dl" href="${url}" download>⬇ Скачать ZIP</a>`
      : "Готово, но файл результата не найден.";
    $("startBtn").disabled = false;
    loadRecentTasks();
  }
  function onError(e) {
    if (es) es.close();
    clearActive();
    let msg = "Сбой генерации.";
    try { msg = JSON.parse(e.data).message || msg; } catch {}
    $("progress").innerHTML = `<span class="err">${escapeHtml(msg)}</span>`;
    $("startBtn").disabled = false;
    loadRecentTasks();
  }
  function onCancelled(d) {
    if (es) es.close();
    clearActive();
    hideHitl(); hide($("progressPanel"));
    $("briefStatus").textContent = d.reason === "timeout" ? "Время истекло, сессия отменена." : "Отменено.";
    show($("briefPanel")); $("startBtn").disabled = false;
    loadRecentTasks();
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
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ── recent tasks (history within the 24h retention window) ─
  // GET /api/tasks does not carry an in-flight run's progress (that's what
  // rehydrate() restores) — this list just makes finished creatives reachable
  // again so a completed ZIP can be re-downloaded after a reload.
  const STATUS_LABEL = {
    queued: "В очереди", running: "В работе",
    awaiting_text: "Ждёт решения", awaiting_image: "Ждёт картинку",
    done: "Готово", failed: "Ошибка", cancelled: "Отменено",
  };
  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  }
  // Per-task banner URLs + brief, kept out of the DOM so the grid's <img>
  // elements are built lazily on first expand (a full history of 100×12 images
  // at once would hammer the DOM and the network).
  const imagesByUid = {};
  const briefByUid = {};
  const BRIEF_LABELS = {
    product: "Что рекламируем", audience: "Целевая аудитория", emotion: "Эмоция / образ",
  };
  const fileNameOf = (u) => String(u).split("/").pop();
  function briefHtml(brief) {
    if (!brief) return "";
    const rows = Object.keys(BRIEF_LABELS)
      .filter((k) => brief[k])
      .map((k) => `<div class="kv"><b>${BRIEF_LABELS[k]}:</b> ${escapeHtml(brief[k])}</div>`)
      .join("");
    return rows ? `<div class="task-brief">${rows}</div>` : "";
  }
  function taskRow(t) {
    const label = STATUS_LABEL[t.status] || t.status;
    const title = escapeHtml(t.prompt || "(без названия)");
    const imgs = Array.isArray(t.images) ? t.images : [];
    const expandable = imgs.length > 0;
    if (expandable) { imagesByUid[t.task_uid] = imgs; briefByUid[t.task_uid] = t.brief || {}; }
    let action = "";
    if (t.status === "done" && t.result_url) {
      action = `<a class="task-dl" href="${P}${t.result_url}" download>⬇ ZIP</a>`;
    } else if (t.status === "failed" && t.error) {
      action = `<span class="task-err">${escapeHtml(t.error)}</span>`;
    }
    const cls = expandable ? "task-row is-expandable" : "task-row";
    const uidAttr = expandable ? ` data-uid="${escapeHtml(t.task_uid)}"` : "";
    const grid = expandable
      ? `<div class="task-grid hidden" data-grid="${escapeHtml(t.task_uid)}"></div>`
      : "";
    return (
      `<div class="${cls}"${uidAttr}>` +
      `<span class="task-title">${title}</span>` +
      `<span class="task-meta"><span class="task-badge is-${t.status}">${label}</span>` +
      `<span class="task-date">${fmtDate(t.created_at)}</span>${action}</span>` +
      `</div>` + grid
    );
  }
  // Build the brief + thumbnail grid the first time its row is opened. Each
  // thumb carries an index (→ lightbox) and its own download link.
  function fillGrid(grid, uid) {
    const imgs = imagesByUid[uid] || [];
    const thumbs = imgs.map((u, i) =>
      `<figure class="task-thumb" data-uid="${escapeHtml(uid)}" data-idx="${i}">` +
      `<img src="${P}${u}" alt="Баннер ${i + 1}" loading="lazy">` +
      `<a class="thumb-dl" href="${P}${u}" download="${escapeHtml(fileNameOf(u))}" ` +
      `title="Скачать баннер ${i + 1}">⬇</a></figure>`
    ).join("");
    grid.innerHTML = briefHtml(briefByUid[uid]) + `<div class="task-thumbs">${thumbs}</div>`;
  }
  function toggleGrid(row) {
    const uid = row.getAttribute("data-uid");
    if (!uid) return;
    const grid = row.parentElement.querySelector(`[data-grid="${uid}"]`);
    if (!grid) return;
    if (grid.classList.contains("hidden") && !grid.childElementCount) fillGrid(grid, uid);
    grid.classList.toggle("hidden");
    row.classList.toggle("is-open");
  }
  async function loadRecentTasks() {
    try {
      const r = await fetch(`${P}/api/tasks`);
      if (!r.ok) return;
      const tasks = await r.json();
      if (!tasks || !tasks.length) return;
      $("tasksList").innerHTML = tasks.map(taskRow).join("");
      show($("tasksPanel"));
    } catch (_) { /* leave the panel hidden on any error */ }
  }
  // Delegated: download links keep their default; a thumbnail opens the
  // lightbox; an expandable row header toggles its grid.
  $("tasksList").addEventListener("click", (ev) => {
    if (ev.target.closest("a")) return; // ZIP + per-thumb download links
    const thumb = ev.target.closest(".task-thumb");
    if (thumb) { openLightbox(thumb.dataset.uid, +thumb.dataset.idx); return; }
    const row = ev.target.closest(".task-row.is-expandable");
    if (row) toggleGrid(row);
  });

  // ── lightbox gallery ───────────────────────────────────
  // Page through a task's banners full-size without leaving the page.
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
      `<a id="lbDl" class="lb-dl" download>⬇ Скачать</a></div>`;
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
    $("lbImg").alt = `Баннер ${lbIdx + 1}`;
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
    if (ev.key === "ArrowRight") lbNav(1);
    else if (ev.key === "ArrowLeft") lbNav(-1);
    else if (ev.key === "Escape") closeLightbox();
  });

  // ── rehydrate active task on page load ─────────────────
  // Canon-header navigation is a full reload that drops JS state + the
  // EventSource, but the run lives on server-side (detached create_task). Find
  // the active task (localStorage, else /api/tasks), snapshot its state via
  // /pending (EventBus has no replay, so snapshot BEFORE resubscribing), then
  // reattach the stream.
  async function findActiveUid() {
    const stored = (() => { try { return localStorage.getItem(LS_KEY); } catch (_) { return null; } })();
    if (stored) return stored;
    try {
      const r = await fetch(`${P}/api/tasks`);
      if (!r.ok) return null;
      const tasks = await r.json();
      const t = (tasks || []).find((x) => ACTIVE.includes(x.status));
      return t ? t.task_uid : null;
    } catch (_) { return null; }
  }

  async function rehydrate() {
    const uid = await findActiveUid();
    if (!uid) return;
    taskUid = uid;
    try {
      const r = await fetch(`${P}/api/tasks/${uid}/pending`);
      if (!r.ok) { clearActive(); return; }
      const d = await r.json();
      if (d.phase) {
        // parked at a HITL pause → re-render the decision UI
        saveActive(uid);
        show($("progressPanel"));
        onAwaiting(d);
        subscribe();
      } else if (ACTIVE.includes(d.status)) {
        // still computing → show progress and reattach the stream
        saveActive(uid);
        setStep("Продолжаю…");
        subscribe();
      } else {
        // terminal (done/failed/cancelled) since we last saw it
        clearActive();
      }
    } catch (_) { /* leave the brief form as-is on any error */ }
  }

  rehydrate();
  loadRecentTasks();
})();
