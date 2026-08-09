/* Библиотека знаний: чтение всем, правка — kb_editor/admin, роли — admin. */
(function () {
  "use strict";
  const P = window.APP_PREFIX || "";
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));

  let me = { role: "user", can_edit_kb: false };
  let items = [];
  let current = null; // slug

  const FIELDS = ["cardName", "cardAliases", "cardTagline", "cardBlock1", "cardBlock2", "cardBlock3"];

  async function jget(url) {
    const r = await fetch(`${P}${url}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  // Сеть рвётся посреди запроса (рестарт VM, обрыв wifi) — тогда fetch кидает,
  // а не отдаёт ответ. Отдаём null, как post() в creatives.js: вызывающий сам
  // решает, в каком статусе показать «нет связи». Без этого «Сохраняю…» висело
  // бы вечно, а галка ролей расходилась бы с сервером молча.
  async function jsend(url, method, body) {
    try {
      return await fetch(`${P}${url}`, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (_) { return null; }
  }

  // 409 на этих роутах значит разное: занятый slug при создании против гонки
  // редакторов при правке. Общего текста у них нет, поэтому его приносит
  // вызывающий — как в creatives.js, где errText остаётся картой статусов.
  const CONFLICT_EDIT = "карточку в этот момент правил кто-то ещё — обновите страницу";
  function errText(status, conflict) {
    if (!status) return "Нет соединения с сервером — проверь сеть и попробуй ещё раз.";
    if (status === 403) return "нет прав на правку";
    if (status === 404) return "карточка не найдена";
    if (status === 409) return conflict || "кто-то опередил — обновите страницу";
    if (status === 422) return "проверьте поля — что-то не прошло валидацию";
    return `Ошибка ${status}`;
  }

  // Защита от двойного клика (приём setBusy из creatives.js): второй PUT по
  // «Сохранить» — это вторая версия карточки, а не повтор безобидного чтения.
  function setBusy(busy) {
    ["saveBtn", "archiveBtn", "newBtn"].forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.disabled = busy;
      el.classList.toggle("is-busy", busy);
    });
  }

  // ── список продуктов ─────────────────────────────────
  async function loadList() {
    const q = $("showArchived").checked ? "?include_archived=true" : "";
    try {
      items = await jget(`/api/kb/products${q}`);
    } catch (_) {
      $("listStatus").innerHTML = `<span class="err">Не удалось загрузить каталог.</span>`;
      return;
    }
    // Статус относился к прошлой загрузке: без сброса одна неудача светилась бы
    // красным через все последующие удачные перезагрузки списка.
    $("listStatus").textContent = "";
    $("kbList").innerHTML = items
      .map(
        (p) =>
          `<button class="task-item" data-slug="${escapeHtml(p.slug)}">` +
          `<b>${escapeHtml(p.name)}</b> <span class="page-sub">v${p.version}` +
          `${p.archived ? " · архив" : ""}</span></button>`
      )
      .join("");
    $("kbList").querySelectorAll("[data-slug]").forEach((b) => {
      b.addEventListener("click", () => openCard(b.dataset.slug));
    });
    // Открытая карточка могла выпасть из свежего списка (сняли «показывать
    // архивные», убрали в архив). Оставить current — значит держать на экране
    // панель карточки, которой в этом каталоге уже нет, и врать её версией.
    if (current && !items.some((p) => p.slug === current)) {
      current = null;
      $("cardPanel").classList.add("hidden");
      $("emptyState").classList.remove("hidden");
    }
  }

  // ── карточка ─────────────────────────────────────────
  function openCard(slug) {
    const p = items.find((x) => x.slug === slug);
    if (!p) return;
    current = slug;
    $("cardTitle").textContent = p.name;
    $("cardMeta").textContent =
      `${p.slug} · версия ${p.version}` +
      (p.updated_by ? ` · правил ${p.updated_by}` : "") +
      (p.archived ? " · в архиве" : "");
    $("cardName").value = p.name;
    $("cardAliases").value = (p.aliases || []).join("\n");
    $("cardTagline").value = p.tagline || "";
    $("cardBlock1").value = p.block1 || "";
    $("cardBlock2").value = p.block2 || "";
    $("cardBlock3").value = p.block3 || "";
    // Архив — не удаление: вернуть карточку должно быть так же просто, как
    // убрать, иначе возврат остаётся операцией для SQL-консоли. Красной
    // (.btn--danger) кнопка остаётся только пока она выключает карточку.
    $("archiveBtn").textContent = p.archived ? "Вернуть из архива" : "В архив";
    $("archiveBtn").classList.toggle("btn--danger", !p.archived);
    $("cardStatus").textContent = "";
    $("cardPanel").classList.remove("hidden");
    $("emptyState").classList.add("hidden");
    // История доступна только редакторам — читателю не показываем и блок,
    // иначе он раскрывает пустой details и видит ошибку доступа.
    if (me.can_edit_kb) loadHistory(slug);
  }

  async function loadHistory(slug) {
    try {
      const rows = await jget(`/api/kb/products/${slug}/history`);
      $("cardHistory").innerHTML = rows
        .map(
          (v) =>
            `<div class="page-sub">v${v.version} · ${escapeHtml(v.updated_at || "")}` +
            ` · ${escapeHtml(v.updated_by || "seed")}${v.archived ? " · архив" : ""}</div>`
        )
        .join("");
    } catch (_) {
      $("cardHistory").innerHTML = `<span class="err">История недоступна.</span>`;
    }
  }

  // Форма всегда шлёт все шесть полей и не несёт номер прочитанной версии.
  // Это осознанно: версионный хэндшейк — это expected_version в контракте API,
  // правка за рамками этой страницы. Цена гонки двух редакторов здесь не
  // «данные пропали», а «надо заметить и восстановить»: каждое сохранение
  // кладёт новую строку, прежняя целиком лежит в истории и видна в #historyBox.
  function payload() {
    return {
      name: $("cardName").value.trim(),
      aliases: $("cardAliases").value.split("\n").map((s) => s.trim()).filter(Boolean),
      tagline: $("cardTagline").value.trim(),
      block1: $("cardBlock1").value,
      block2: $("cardBlock2").value,
      block3: $("cardBlock3").value,
    };
  }

  $("saveBtn").addEventListener("click", async () => {
    if (!current) return;
    const slug = current;
    setBusy(true);
    $("cardStatus").textContent = "Сохраняю…";
    const r = await jsend(`/api/kb/products/${slug}`, "PUT", payload());
    setBusy(false);
    if (!r || !r.ok) {
      $("cardStatus").innerHTML =
        `<span class="err">Не сохранилось: ` +
        `${escapeHtml(errText(r ? r.status : 0, CONFLICT_EDIT))}</span>`;
      return;
    }
    const p = await r.json();
    await loadList();
    if (current) openCard(current);
    // Правка уже уехала в граф — говорим об этом прямо, это не косметика.
    // Статус ставим после перерисовки: openCard чистит его.
    $("cardStatus").textContent = `Сохранено, версия ${p.version}. Следующий запуск возьмёт её.`;
  });

  $("archiveBtn").addEventListener("click", async () => {
    if (!current) return;
    const p = items.find((x) => x.slug === current);
    if (!p) return;
    const toArchive = !p.archived;
    // Архив выключает карточку для ВСЕХ будущих запусков пайплайна — это не
    // косметика списка, поэтому переспрашиваем. Возврат ничего не выключает,
    // его не переспрашиваем.
    if (toArchive && !window.confirm(
      "Убрать карточку в архив? Пайплайн перестанет брать из неё факты — " +
      "она выпадет из всех будущих запусков."
    )) return;
    setBusy(true);
    const r = await jsend(`/api/kb/products/${current}`, "PUT", { archived: toArchive });
    setBusy(false);
    if (!r || !r.ok) {
      $("cardStatus").innerHTML =
        `<span class="err">Не вышло: ` +
        `${escapeHtml(errText(r ? r.status : 0, CONFLICT_EDIT))}</span>`;
      return;
    }
    await loadList();
    // Перерисовываем карточку: иначе cardMeta продолжает показывать версию до
    // архивирования и молчит про «в архиве».
    if (current) openCard(current);
    $("listStatus").textContent = toArchive
      ? "Карточка в архиве. Пайплайн её больше не увидит."
      : "Карточка вернулась из архива — следующий запуск снова её увидит.";
  });

  $("newBtn").addEventListener("click", async () => {
    const slug = (prompt("slug новой карточки (латиница, дефисы)") || "").trim();
    if (!slug) return;
    const name = (prompt("Название продукта") || "").trim();
    if (!name) return;
    setBusy(true);
    const r = await jsend("/api/kb/products", "POST", { slug, name });
    setBusy(false);
    if (!r || !r.ok) {
      $("listStatus").innerHTML =
        `<span class="err">Не создалось: ` +
        `${escapeHtml(errText(r ? r.status : 0, "такой slug уже есть"))}</span>`;
      return;
    }
    await loadList();
    $("listStatus").textContent = "Карточка создана — заполните блоки.";
    openCard(slug);
  });

  $("showArchived").addEventListener("change", loadList);

  // ── роли (только admin) ──────────────────────────────
  async function loadRoles() {
    let rows;
    // Технические учётки (прогоны, e2e, нагрузка) сервер по умолчанию не
    // отдаёт: см. is_technical_email в routes_admin.py. Переключатель нужен,
    // чтобы фильтр не превратился в ложь о составе БД.
    const technical = $("showTechnical") && $("showTechnical").checked;
    try {
      rows = await jget(`/api/admin/roles${technical ? "?technical=true" : ""}`);
    } catch (_) {
      // Пустой список без объяснения читается как «других пользователей нет» —
      // админ решит, что выдавать доступ некому.
      $("rolesList").innerHTML = "";
      $("rolesStatus").innerHTML =
        `<span class="err">Не удалось загрузить список доступов.</span>`;
      return;
    }
    $("rolesStatus").textContent = "";
    $("rolesList").innerHTML = rows
      .map((r) => {
        // У админа право править библиотеку есть всегда (can_edit_kb = admin ||
        // kb_editor), отдельный флаг ему просто не выставляют. Рисовать пустую
        // галку значило врать: список утверждал бы, что админ карточки не
        // правит, — и админ шёл выдавать себе доступ, которого у него и так нет
        // способа лишиться. Галку показываем полной и запертой.
        const checked = r.kb_editor || r.role === "admin";
        return (
          `<div class="task-item"><b>${escapeHtml(r.email)}</b> ` +
          `<span class="page-sub">${escapeHtml(r.role)}</span> ` +
          `<label class="page-sub"><input type="checkbox" data-email="${escapeHtml(r.email)}"` +
          `${checked ? " checked" : ""}${r.role === "admin" ? " disabled" : ""}>` +
          ` правит библиотеку</label></div>`
        );
      })
      .join("");
    $("rolesList").querySelectorAll("[data-email]").forEach((cb) => {
      cb.addEventListener("change", async () => {
        const want = cb.checked;
        const r = await jsend("/api/admin/roles", "PUT", {
          email: cb.dataset.email, role: "user", kb_editor: want,
        });
        if (r && r.ok) {
          // «Сохранено» не отвечает на вопрос, который админ задавал галкой:
          // получил человек доступ или потерял. Называем обоих — кого и что —
          // и берём это из ответа сервера, а не из намерения: email там уже
          // нормализован, а kb_editor — то, что реально легло в базу.
          let saved = null;
          try { saved = await r.json(); } catch (_) { /* см. фолбэк ниже */ }
          // Без escapeHtml намеренно: текст кладём через textContent, а он
          // разметку не исполняет — экранирование здесь дало бы «&amp;» в почте.
          const who = saved ? saved.email : cb.dataset.email;
          const granted = saved ? !!saved.kb_editor : want;
          if (saved) cb.checked = granted;
          $("rolesStatus").textContent = granted
            ? `Доступ выдан: ${who} правит карточки библиотеки.`
            : `Доступ отозван: ${who} больше не правит карточки библиотеки.`;
          return;
        }
        // Галку переключил браузер, сервер об этом не узнал. Без отката админ
        // уходит уверенным, что выдал право править библиотеку, которого не
        // выдал — и узнает об этом от редактора, получившего 403.
        cb.checked = !want;
        $("rolesStatus").innerHTML =
          `<span class="err">Не сохранилось: ` +
          `${escapeHtml(errText(r ? r.status : 0))}</span>`;
      });
    });
  }

  // ── отмеченный опыт ──────────────────────────────────
  // Забракованное подписано «не идёт в промпт» и набрано приглушённо: в общей
  // ленте вперемешку с принятым оно читается как «модели сказали, что так не
  // надо», а решение ровно обратное — в промпт уходит только «пошло в работу».
  const OUTCOME_RU = {
    shipped: "пошло в работу",
    rejected: "забраковано · не идёт в промпт",
  };
  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  }
  // Пустое поле не рисуем строкой «якорь: —»: в опыте половина полей пустая по
  // природе (метафора есть только у сценария render), и прочерки читались бы
  // как потерянные данные.
  const kv = (k, v) => (v ? `<div class="kv"><b>${k}:</b> ${escapeHtml(v)}</div>` : "");

  async function loadExperience() {
    let rows;
    try {
      rows = await jget("/api/kb/experience");
    } catch (_) {
      // Молча оставить панель пустой — значит соврать: пустой список читается
      // как «никто ничего не отмечал», хотя отметки есть и уходят в промпт.
      $("experienceList").innerHTML =
        `<span class="err">Не удалось загрузить отмеченный опыт.</span>`;
      return;
    }
    $("experienceList").innerHTML = rows.length
      ? rows
          .map((r) => {
            const label = escapeHtml(OUTCOME_RU[r.outcome] || r.outcome);
            const head =
              r.outcome === "shipped"
                ? `<b>${label}</b>`
                : `<span class="page-sub">${label}</span>`;
            // Дата — та, по которой лента отсортирована (updated_at: человек
            // мог передумать). Без неё вчерашняя отметка неотличима от
            // прошлогодней, а порядок строк ничего не объясняет.
            const when = fmtDate(r.updated_at || r.created_at);
            // Что из строки реально доезжает до генерации, видно только в коде
            // (experience_block: слоган+якорь+комментарий копирайтеру, метафора
            // — в промпт картинки, и только по shipped и тому же продукту).
            // Подписываем прямо в развороте: без этого человек правит опыт
            // наугад и не понимает, почему удаление что-то изменило.
            // Удаление меняет то, из чего строятся предложения, — право то же,
            // что на правку карточки, и проверяется оно на сервере.
            const canDelete = me.can_edit_kb;
            const goesToPrompt = r.outcome === "shipped";
            const rows =
              kv("якорь персоны", r.anchor) +
              kv("что человек получит", r.desired_outcome) +
              kv("образ картинки", r.metaphor) +
              kv("сегмент ЦА", r.persona_segment) +
              kv("комментарий", r.comment) +
              kv("отмечено впервые", fmtDate(r.created_at)) +
              `<p class="page-sub">${goesToPrompt
                ? "Уходит в промпты по этому продукту: слоган с якорем и комментарий — копирайтеру, образ — в список уже снятых метафор."
                : "В промпты не уходит: копирайтер видит только «пошло в работу»."
              }</p>` +
              (canDelete
                ? `<div class="btn-row"><button class="btn btn--danger" data-del-exp="${r.id}"` +
                  ` data-what="${escapeHtml(r.slogan || r.slug || "")}">Удалить отметку</button></div>`
                : "");
            return (
              `<div class="task-item">${head}` +
              ` · ${escapeHtml(r.slug || "без продукта")} · «${escapeHtml(r.slogan)}»` +
              (when ? ` <span class="page-sub">· ${escapeHtml(when)}</span>` : "") +
              (r.comment ? `<div class="page-sub">${escapeHtml(r.comment)}</div>` : "") +
              `<details class="cand-why"><summary>Что записано в опыт</summary>${rows}</details>` +
              `</div>`
            );
          })
          .join("")
      : `<p class="page-sub">Пока никто не отмечал исходы. Отметка ставится на экране результата.</p>`;
  }

  // Делегирование, а не слушатель на кнопку: лента перерисовывается целиком
  // после каждого удаления, и навешенные обработчики умирали бы вместе с ней.
  $("experienceList").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-del-exp]");
    if (!btn) return;
    const what = btn.dataset.what || "";
    // Удаление отметки необратимо и меняет будущие промпты — спрашиваем, но
    // называем в вопросе сам текст, а не «эту запись»: лента однотипная, и
    // подтвердить не глядя здесь слишком легко.
    if (!window.confirm(`Удалить отметку «${what}»? Она перестанет влиять на промпты, вернуть её можно только новой отметкой исхода.`)) return;
    btn.disabled = true;
    const r = await jsend(`/api/kb/experience/${btn.dataset.delExp}`, "DELETE");
    if (r && r.ok) {
      await loadExperience();
      return;
    }
    btn.disabled = false;
    // 404 здесь не ошибка человека: строку уже удалил кто-то другой, и
    // правильная реакция — показать актуальную ленту, а не красный текст.
    if (r && r.status === 404) { await loadExperience(); return; }
    btn.insertAdjacentHTML(
      "afterend",
      `<span class="err"> Не удалилось: ${escapeHtml(errText(r ? r.status : 0))}</span>`
    );
  });

  // ── старт ────────────────────────────────────────────
  (async function init() {
    let meFailed = false;
    try {
      me = await jget("/api/me");
    } catch (_) {
      meFailed = true;
    }
    if (me.can_edit_kb) {
      FIELDS.forEach((id) => { $(id).disabled = false; });
      $("editRow").classList.remove("hidden");
      $("createRow").classList.remove("hidden");
      $("historyBox").classList.remove("hidden");
    }
    if (me.role === "admin") {
      $("rolesPanel").classList.remove("hidden");
      $("showTechnical").addEventListener("change", loadRoles);
      loadRoles();
    }
    await loadList();
    await loadExperience();
    // После loadList: удачная загрузка списка гасит #listStatus, и сообщение,
    // поставленное до неё, исчезло бы вместе с красным следом прошлой ошибки.
    if (meFailed) {
      $("listStatus").innerHTML =
        `<span class="err">Не удалось определить пользователя.</span>`;
    }
  })();
})();
