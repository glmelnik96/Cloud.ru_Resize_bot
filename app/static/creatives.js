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
    hideHitl(); hide($("progressPanel")); show($("resultsPanel"));
    const url = d.result_url ? `${P}${d.result_url}` : null;
    $("resultMsg").innerHTML = url
      ? `<a class="dl" href="${url}" download>⬇ Скачать ZIP</a>`
      : "Готово, но файл результата не найден.";
  }
  function onError(e) {
    if (es) es.close();
    let msg = "Сбой генерации.";
    try { msg = JSON.parse(e.data).message || msg; } catch {}
    $("progress").innerHTML = `<span class="err">${escapeHtml(msg)}</span>`;
  }
  function onCancelled(d) {
    if (es) es.close();
    hideHitl(); hide($("progressPanel"));
    $("briefStatus").textContent = d.reason === "timeout" ? "Время истекло, сессия отменена." : "Отменено.";
    show($("briefPanel")); $("startBtn").disabled = false;
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
})();
