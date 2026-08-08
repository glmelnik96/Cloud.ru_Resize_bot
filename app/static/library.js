/* Библиотека знаний: чтение всем, правка — kb_editor/admin, роли — admin. */
(function () {
  "use strict";
  const P = window.APP_PREFIX || "";
  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
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

  async function jsend(url, method, body) {
    return fetch(`${P}${url}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function errText(status) {
    if (status === 403) return "нет прав на правку";
    if (status === 404) return "карточка не найдена";
    if (status === 409) return "такой slug уже есть";
    if (status === 422) return "проверьте поля — что-то не прошло валидацию";
    return `ошибка ${status}`;
  }

  // ── список продуктов ─────────────────────────────────
  async function loadList() {
    const q = $("showArchived").checked ? "?include_archived=true" : "";
    try {
      items = await jget(`/api/kb/products${q}`);
    } catch (e) {
      $("listStatus").innerHTML = `<span class="err">Не удалось загрузить каталог.</span>`;
      return;
    }
    $("kbList").innerHTML = items
      .map(
        (p) =>
          `<button class="task-item" data-slug="${esc(p.slug)}">` +
          `<b>${esc(p.name)}</b> <span class="page-sub">v${p.version}` +
          `${p.archived ? " · архив" : ""}</span></button>`
      )
      .join("");
    $("kbList").querySelectorAll("[data-slug]").forEach((b) => {
      b.addEventListener("click", () => openCard(b.dataset.slug));
    });
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
            `<div class="page-sub">v${v.version} · ${esc(v.updated_at || "")}` +
            ` · ${esc(v.updated_by || "seed")}${v.archived ? " · архив" : ""}</div>`
        )
        .join("");
    } catch (e) {
      $("cardHistory").innerHTML = `<span class="err">История недоступна.</span>`;
    }
  }

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
    $("cardStatus").textContent = "Сохраняю…";
    const r = await jsend(`/api/kb/products/${current}`, "PUT", payload());
    if (!r.ok) {
      $("cardStatus").innerHTML = `<span class="err">Не сохранилось: ${esc(errText(r.status))}</span>`;
      return;
    }
    const p = await r.json();
    // Правка уже уехала в граф — говорим об этом прямо, это не косметика.
    $("cardStatus").textContent = `Сохранено, версия ${p.version}. Следующий запуск возьмёт её.`;
    await loadList();
    openCard(current);
  });

  $("archiveBtn").addEventListener("click", async () => {
    if (!current) return;
    const r = await jsend(`/api/kb/products/${current}`, "PUT", { archived: true });
    if (!r.ok) {
      $("cardStatus").innerHTML = `<span class="err">Не вышло: ${esc(errText(r.status))}</span>`;
      return;
    }
    $("cardStatus").textContent = "Карточка в архиве. Пайплайн её больше не увидит.";
    await loadList();
  });

  $("newBtn").addEventListener("click", async () => {
    const slug = (prompt("slug новой карточки (латиница, дефисы)") || "").trim();
    if (!slug) return;
    const name = (prompt("Название продукта") || "").trim();
    if (!name) return;
    const r = await jsend("/api/kb/products", "POST", { slug, name });
    if (!r.ok) {
      $("listStatus").innerHTML = `<span class="err">Не создалось: ${esc(errText(r.status))}</span>`;
      return;
    }
    $("listStatus").textContent = "Карточка создана — заполните блоки.";
    await loadList();
    openCard(slug);
  });

  $("showArchived").addEventListener("change", loadList);

  // ── роли (только admin) ──────────────────────────────
  async function loadRoles() {
    let rows;
    try {
      rows = await jget("/api/admin/roles");
    } catch (e) {
      return;
    }
    $("rolesList").innerHTML = rows
      .map(
        (r) =>
          `<div class="task-item"><b>${esc(r.email)}</b> ` +
          `<span class="page-sub">${esc(r.role)}</span> ` +
          `<label class="page-sub"><input type="checkbox" data-email="${esc(r.email)}"` +
          `${r.kb_editor ? " checked" : ""}${r.role === "admin" ? " disabled" : ""}>` +
          ` правит библиотеку</label></div>`
      )
      .join("");
    $("rolesList").querySelectorAll("[data-email]").forEach((cb) => {
      cb.addEventListener("change", async () => {
        const r = await jsend("/api/admin/roles", "PUT", {
          email: cb.dataset.email, role: "user", kb_editor: cb.checked,
        });
        $("rolesStatus").textContent = r.ok
          ? "Сохранено."
          : `Не сохранилось: ${errText(r.status)}`;
      });
    });
  }

  // ── старт ────────────────────────────────────────────
  (async function init() {
    try {
      me = await jget("/api/me");
    } catch (e) {
      $("listStatus").innerHTML = `<span class="err">Не удалось определить пользователя.</span>`;
    }
    if (me.can_edit_kb) {
      FIELDS.forEach((id) => { $(id).disabled = false; });
      $("editRow").classList.remove("hidden");
      $("createRow").classList.remove("hidden");
      $("historyBox").classList.remove("hidden");
    }
    if (me.role === "admin") {
      $("rolesPanel").classList.remove("hidden");
      loadRoles();
    }
    loadList();
  })();
})();
