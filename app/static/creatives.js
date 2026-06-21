/* App3 / creatives — brief → SSE progress → 2 HITL pauses → results.
   All URLs go through the gateway prefix (window.APP_PREFIX). */
(function () {
  "use strict";
  const P = window.APP_PREFIX || "";
  const $ = (id) => document.getElementById(id);
  const show = (el) => el.classList.remove("hidden");
  const hide = (el) => el.classList.add("hidden");

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
    const goal = $("goal").value;
    const audience = $("audience").value.trim();
    if (!product || !audience) {
      $("briefStatus").textContent = "Заполни продукт и аудиторию.";
      return;
    }
    $("startBtn").disabled = true;
    $("briefStatus").textContent = "Создаю задачу…";
    try {
      const r = await fetch(`${P}/api/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product, goal, audience }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      taskUid = data.task_uid;
      saveActive(taskUid);
      $("briefStatus").textContent = "";
      show($("progressPanel"));
      subscribe();
    } catch (e) {
      $("briefStatus").innerHTML = `<span class="err">Ошибка: ${e.message}</span>`;
      $("startBtn").disabled = false;
    }
  });

  // ── SSE ────────────────────────────────────────────────
  function subscribe() {
    if (es) es.close();
    es = new EventSource(`${P}/api/tasks/${taskUid}/events`);
    es.addEventListener("queued", () => setStep("В очереди…"));
    es.addEventListener("start", (e) => setStep(stepOf(e)));
    es.addEventListener("step", (e) => setStep(stepOf(e)));
    es.addEventListener("awaiting_input", (e) => onAwaiting(JSON.parse(e.data)));
    es.addEventListener("resumed", () => { hideHitl(); setStep("Продолжаю…"); });
    es.addEventListener("done", (e) => onDone(JSON.parse(e.data)));
    es.addEventListener("error", (e) => onError(e));
    es.addEventListener("cancelled", (e) => onCancelled(JSON.parse(e.data)));
  }
  const stepOf = (e) => { try { return JSON.parse(e.data).step || "…"; } catch { return "…"; } };
  function setStep(t) { $("stepLabel").textContent = t; show($("progressPanel")); }

  // ── awaiting (HITL) ────────────────────────────────────
  function onAwaiting(d) {
    if (d.phase === "text_approve") {
      renderCandidate(d.candidate || {});
      hide($("imagePanel")); show($("textPanel"));
    } else if (d.phase === "image_upload") {
      $("imagePrompt").textContent = d.image_prompt || "(пусто)";
      if (d.can_generate) show($("genBtn")); else hide($("genBtn"));
      $("imageStatus").textContent = d.gen_error ? `Генерация не удалась: ${d.gen_error}. Загрузи картинку.` : "";
      hide($("textPanel")); show($("imagePanel"));
    }
  }
  function renderCandidate(c) {
    $("candidate").innerHTML =
      kv("slogan", c.slogan) + kv("body", c.body) + kv("cta", c.cta) + kv("hook", c.hook_angle);
  }
  const kv = (k, v) => v ? `<div class="kv"><b>${k}:</b> ${escapeHtml(v)}</div>` : "";

  function hideHitl() { hide($("textPanel")); hide($("imagePanel")); hide($("refineBox")); }

  // text decisions
  document.querySelectorAll("#textPanel [data-act]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const act = btn.dataset.act;
      if (act === "refine") { show($("refineBox")); return; }
      sendText(act, null);
    });
  });
  $("refineSend").addEventListener("click", () => sendText("refine", $("refineComment").value.trim()));

  async function sendText(action, comment) {
    hideHitl(); setStep("Применяю решение…");
    await post(`${P}/api/tasks/${taskUid}/decision/text`, { action, comment });
  }

  // image decisions
  $("genBtn").addEventListener("click", async () => {
    $("imageStatus").textContent = "Генерирую…"; hideHitl(); setStep("Генерирую hero…");
    const fd = new FormData(); fd.append("action", "generate");
    await postForm(`${P}/api/tasks/${taskUid}/decision/image`, fd);
  });
  $("heroFile").addEventListener("change", async (ev) => {
    const f = ev.target.files[0]; if (!f) return;
    hideHitl(); setStep("Загружаю картинку…");
    const fd = new FormData(); fd.append("action", "upload"); fd.append("file", f);
    await postForm(`${P}/api/tasks/${taskUid}/decision/image`, fd);
  });
  $("imgCancel").addEventListener("click", async () => {
    hideHitl();
    const fd = new FormData(); fd.append("action", "cancel");
    await postForm(`${P}/api/tasks/${taskUid}/decision/image`, fd);
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
    loadRecentTasks();
  }
  function onError(e) {
    if (es) es.close();
    clearActive();
    let msg = "Сбой генерации.";
    try { msg = JSON.parse(e.data).message || msg; } catch {}
    $("progress").innerHTML = `<span class="err">${escapeHtml(msg)}</span>`;
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
  async function post(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) $("progress").innerHTML = `<span class="err">Ошибка ${r.status}</span>`;
  }
  async function postForm(url, fd) {
    const r = await fetch(url, { method: "POST", body: fd });
    if (!r.ok) {
      const t = r.status === 501 ? "Генерация недоступна — загрузи картинку." : `Ошибка ${r.status}`;
      $("imageStatus").innerHTML = `<span class="err">${t}</span>`; show($("imagePanel"));
    }
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
  function taskRow(t) {
    const label = STATUS_LABEL[t.status] || t.status;
    const title = escapeHtml(t.prompt || "(без названия)");
    let action = "";
    if (t.status === "done" && t.result_url) {
      action = `<a class="task-dl" href="${P}${t.result_url}" download>⬇ ZIP</a>`;
    } else if (t.status === "failed" && t.error) {
      action = `<span class="task-err">${escapeHtml(t.error)}</span>`;
    }
    return (
      `<div class="task-row">` +
      `<span class="task-title">${title}</span>` +
      `<span class="task-meta"><span class="task-badge is-${t.status}">${label}</span>` +
      `<span class="task-date">${fmtDate(t.created_at)}</span>${action}</span>` +
      `</div>`
    );
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
