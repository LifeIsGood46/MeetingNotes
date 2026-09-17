/* meetingnotes UI — client state, SSE updates, settings, drag/drop queue */

(() => {
  const $ = (sel) => document.querySelector(sel);
  const jobs = new Map(); // id -> job DOM element

  // ---------------- settings dialog ----------------
  function openSettings() { $("#settings-overlay").hidden = false; }
  function closeSettings() { $("#settings-overlay").hidden = true; }
  $("#settings-btn").addEventListener("click", openSettings);
  $("#settings-close-btn").addEventListener("click", closeSettings);
  $("#settings-overlay").addEventListener("click", (e) => {
    if (e.target.id === "settings-overlay") closeSettings();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#settings-overlay").hidden) closeSettings();
  });

  // ---------------- first-launch onboarding ----------------
  const onboardSlides = () => [...document.querySelectorAll(".onboard-slide")];
  let onboardIdx = 0;
  function showOnboard(idx) {
    const slides = onboardSlides();
    onboardIdx = Math.max(0, Math.min(idx, slides.length - 1));
    slides.forEach((s, i) => { s.hidden = i !== onboardIdx; });
    const dots = $("#onboard-dots");
    dots.innerHTML = "";
    slides.forEach((_, i) => {
      const d = document.createElement("span");
      if (i === onboardIdx) d.className = "on";
      dots.appendChild(d);
    });
    $("#onboard-back").disabled = onboardIdx === 0;
    $("#onboard-next").textContent = onboardIdx === slides.length - 1 ? "Finish" : "Next";
  }
  function openOnboard() {
    $("#onboard-overlay").hidden = false;
    showOnboard(0);
  }
  async function finishOnboard() {
    $("#onboard-overlay").hidden = true;
    try {
      await fetch("/api/settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ onboarded: true }),
      });
    } catch (_) { /* flag syncs next launch; guide itself is done */ }
  }
  $("#replay-guide-btn").addEventListener("click", () => {
    closeSettings();
    openOnboard();
  });
  $("#onboard-skip").addEventListener("click", finishOnboard);
  $("#onboard-back").addEventListener("click", () => showOnboard(onboardIdx - 1));
  $("#onboard-next").addEventListener("click", async () => {
    if (onboardIdx >= onboardSlides().length - 1) await finishOnboard();
    else showOnboard(onboardIdx + 1);
  });
  // Hello CTA reuses the guarded dropzone opener.
  $("#hello-add-btn").addEventListener("click", () => $("#dropzone").click());

  // ---------------- dropdown population ----------------
  let taskPrompts = {};
  let profileList = [];   // [{name, description}]
  let taskList = [];      // ["raw", "clean", ...]

  async function loadProfiles() {
    const r = await fetch("/api/profiles").then((r) => r.json());
    taskPrompts = r.task_prompts || {};
    profileList = r.profiles || [];
    taskList = r.tasks || [];

    // Settings: default profile / default task selectors
    const defProf = $("#default-profile");
    defProf.innerHTML = "";
    profileList.forEach((p) =>
      defProf.insertAdjacentHTML("beforeend", `<option value="${p.name}">${p.name}</option>`)
    );
    const defTask = $("#default-task");
    defTask.innerHTML = "";
    taskList.forEach((t) =>
      defTask.insertAdjacentHTML("beforeend", `<option value="${t}">${t}</option>`)
    );
  }

  // Helpers for LLM model select + custom
  function getLlmModelValue() {
    const sel = $("#llm-model");
    if (sel.value === "__custom__") {
      return $("#llm-model-custom").value.trim();
    }
    return sel.value;
  }
  function setLlmModelValue(model) {
    const sel = $("#llm-model");
    const custom = $("#llm-model-custom");
    // If model exists in options, select it
    const hasOption = [...sel.options].some((o) => o.value === model);
    if (model && !hasOption && model !== "__custom__") {
      // Add it as an option (for previously saved custom values)
      const opt = document.createElement("option");
      opt.value = model;
      opt.textContent = model;
      sel.insertBefore(opt, sel.querySelector('option[value="__custom__"]'));
    }
    if (hasOption || [...sel.options].some((o) => o.value === model)) {
      sel.value = model;
      custom.style.display = "none";
    } else if (!model) {
      sel.value = sel.options[0] ? sel.options[0].value : "";
      custom.style.display = "none";
    } else {
      sel.value = "__custom__";
      custom.value = model;
      custom.style.display = "block";
    }
  }
  // Show/hide custom input when select changes
  document.addEventListener("change", (e) => {
    if (e.target.id === "llm-model") {
      const custom = $("#llm-model-custom");
      custom.style.display = e.target.value === "__custom__" ? "block" : "none";
      if (e.target.value === "__custom__") custom.focus();
    }
  });

  // ---------------- settings ----------------
  async function loadSettings() {
    const s = await fetch("/api/settings").then((r) => r.json());
    $("#model-size").value = s.model_size;
    // GPU-only builds: a stale "cpu" value has no matching option — coerce.
    $("#model-device").value = s.device === "cpu" ? "cuda" : s.device;
    $("#model-compute").value = s.compute_type;
    // Language dropdown is populated from the model build itself,
    // so only valid codes can ever be selected.
    const langs = await fetch("/api/languages").then((r) => r.json()).catch(() => ({ languages: [] }));
    const langSel = $("#language");
    langSel.innerHTML = "";
    langs.languages.forEach((l) =>
      langSel.insertAdjacentHTML("beforeend", `<option value="${l.code}">${l.name} (${l.code})</option>`)
    );
    langSel.value = s.language || "ru";
    if (!langSel.value && langSel.options.length) langSel.selectedIndex = 0;
    $("#output-dir").value = s.output_dir || "";
    $("#default-profile").value = s.default_profile || "generic";
    $("#default-task").value = s.default_task || "raw";
    $("#llm-provider").value = s.llm.provider;
    // Defer setting LLM model until after model list is populated
    // Store it for refreshLlmModels to pick up
    window._pendingLlmModel = s.llm.model || "";
    $("#ollama-host").value = s.llm.ollama_host || "";
    $("#ollama-key").value = s.llm.ollama_api_key || "";
    $("#compat-url").value = s.llm.compat_base_url || "";
    $("#compat-key").value = s.llm.compat_api_key || "";
    $("#cloud-key").value =
      s.llm.provider === "anthropic" ? s.llm.anthropic_api_key
      : s.llm.provider === "openrouter" ? s.llm.openrouter_api_key
      : s.llm.openai_api_key;
    toggleLlmFields();
    updateFoldersPaths();
    refreshModelStatus();
    return s;
  }

  function updateModelPill(size, installed, loaded) {
    const pill = $("#model-pill");
    if (!pill) return;
    const state = !installed ? "not downloaded" : loaded ? "in memory" : "installed";
    pill.textContent = `${size} · ${state}`;
  }

  function updateFoldersPaths() {
    const el = $("#folders-paths");
    if (!el) return;
    const out = ($("#output-dir").value.trim() || "results").replace(/[/\\]+$/, "");
    el.innerHTML = `Models: <code>models/</code><br>Results: <code>${escapeHtml(out)}/&lt;name&gt;/</code><br>State: <code>data/</code> &nbsp; Temp: <code>data/work/</code>`;
  }
  function toggleLlmFields() {
    const prov2 = $("#llm-provider").value;
    $("#llm-ollama-fields").hidden = prov2 !== "ollama";
    $("#llm-compat-fields").hidden = prov2 !== "compat";
    $("#llm-cloud-fields").hidden = !(prov2 === "anthropic" || prov2 === "openai" || prov2 === "openrouter");
    // No fields at all for "none" — that's the point of it.
  }
  document.addEventListener("change", (e) => {
    if (e.target.id === "llm-provider") {
      toggleLlmFields();
      refreshLlmModels(); // list belongs to the provider; never show stale text
    }
  });

  $("#save-settings-btn").addEventListener("click", async () => {
    const prov = $("#llm-provider").value;
    const body = {
      model_size: $("#model-size").value,
      device: $("#model-device").value,
      compute_type: $("#model-compute").value,
      language: $("#language").value,
      default_profile: $("#default-profile").value,
      default_task: $("#default-task").value,
      output_dir: $("#output-dir").value.trim(),
      llm: {
        provider: prov,
        model: prov === "none" ? "" : getLlmModelValue(),
        ollama_host: $("#ollama-host").value,
        ollama_api_key: $("#ollama-key").value,
        compat_base_url: $("#compat-url").value,
        compat_api_key: $("#compat-key").value,
        anthropic_api_key: prov === "anthropic" ? $("#cloud-key").value : undefined,
        openai_api_key: prov === "openai" ? $("#cloud-key").value : undefined,
        openrouter_api_key: prov === "openrouter" ? $("#cloud-key").value : undefined,
      },
    };
    const r = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) {
      $("#settings-saved").hidden = false;
      setTimeout(() => ($("#settings-saved").hidden = true), 2000);
      updateFoldersPaths();
      refreshLlmModels(); // new key/host/provider saved -> re-list immediately
    }
  });
  $("#output-dir").addEventListener("input", updateFoldersPaths);

  // ---------------- model management ----------------
  const modelBtns = ["download-model-btn", "load-model-btn", "unload-model-btn", "delete-model-btn"];
  function hideModelBtns() {
    modelBtns.forEach((id) => { $("#" + id).hidden = true; });
  }

  async function refreshModelStatus() {
    const size = $("#model-size").value;
    const s = await fetch(`/api/models/${size}/status`).then((r) => r.json());
    updateModelPill(size, s.installed, s.loaded);
    const el = $("#model-status");
    hideModelBtns();
    $("#model-progress").hidden = true;
    const sizeTxt = s.size_mb ? ` · ${s.size_mb >= 1000 ? (s.size_mb / 1000).toFixed(1) + " GB" : s.size_mb + " MB"}` : "";
    const loadedTxt = s.loaded ? ` <span class="badge badge-running">in memory</span>` : "";
    if (s.installed) {
      el.innerHTML = `<span class="badge badge-done">installed</span> ${size}${sizeTxt}${loadedTxt}`;
      $("#model-progress-text").textContent = s.error
        ? `Load failed: ${s.error.replace(/^error:\s*/, "")}`
        : "";
      if (s.loaded) {
        $("#unload-model-btn").hidden = false;
      } else {
        $("#load-model-btn").hidden = false;
      }
      $("#delete-model-btn").hidden = false;
    } else if (s.downloading) {
      el.innerHTML = `<span class="badge badge-running">downloading</span> ${size}`;
      $("#model-progress").hidden = false;
    } else if (s.loading) {
      el.innerHTML = `<span class="badge badge-running">loading</span> ${size}${sizeTxt}`;
      $("#model-progress").hidden = false;
      $("#model-progress-fill").style.width = `100%`;
      $("#model-progress-text").textContent = `Loading ${size} into memory...`;
    } else {
      el.innerHTML = `<span class="badge badge-queued">not installed</span> ${size}`;
      $("#model-progress-text").textContent = s.error ? `Download failed: ${s.error}` : "";
      const dlBtn = $("#download-model-btn");
      dlBtn.hidden = false;
      dlBtn.innerHTML = `<svg class="ic ic-sm" width="15" height="15"><use href="#i-download"/></svg> ${s.error ? "Retry download" : "Download"}`;
    }
  }

  $("#model-size").addEventListener("change", refreshModelStatus);
  $("#download-model-btn").addEventListener("click", async () => {
    const size = $("#model-size").value;
    await fetch(`/api/models/${size}/download`, { method: "POST" });
    refreshModelStatus();
  });
  $("#load-model-btn").addEventListener("click", async () => {
    const size = $("#model-size").value;
    await fetch(`/api/models/${size}/load`, { method: "POST" });
    refreshModelStatus();
  });
  $("#unload-model-btn").addEventListener("click", async () => {
    const size = $("#model-size").value;
    await fetch(`/api/models/${size}/unload`, { method: "POST" });
    refreshModelStatus();
  });
  $("#delete-model-btn").addEventListener("click", async () => {
    const size = $("#model-size").value;
    if (!await modalConfirm({ title: "Delete model?", message: `Delete the downloaded "${size}" model from disk?\n\nIt will download again automatically if a job needs it.`, okText: "Delete", danger: true })) return;
    const r = await fetch(`/api/models/${size}/delete`, { method: "POST" });
    const data = await r.json().catch(() => ({}));
    if (!data.deleted) await modalAlert("Delete failed", "Could not delete the model folder.");
    refreshModelStatus();
  });
  $("#models-folder-btn").addEventListener("click", async () => {
    const r = await fetch(`/api/models/open-folder`, { method: "POST" });
    if (!r.ok) await modalAlert("No model folder", "Model folder does not exist yet — download a model first.");
  });

  // ---------------- queue ----------------
  $("#file-input").addEventListener("change", (e) => {
    uploadFiles([...e.target.files]);
    e.target.value = ""; // allow picking the same file again
  });

  const dz = $("#dropzone");
  // Click-to-browse: explicit programmatic click on the input. The previous
  // "input stretched over the dropzone" pattern queued a chooser per click
  // and stole click targets from drag handlers; explicit click + debounce
  // is predictable in every browser.
  let chooserPending = false;
  dz.addEventListener("click", async () => {
    if (chooserPending) return; // a chooser is already open — ignore extra clicks
    chooserPending = true;
    try {
      $("#file-input").click();
    } finally {
      // The chooser blocks JS until dismissed; this runs after it closes.
      setTimeout(() => (chooserPending = false), 300);
    }
  });
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.remove("dragover");
    })
  );
  dz.addEventListener("drop", (e) => uploadFiles([...e.dataTransfer.files]));

  async function uploadFiles(fileList) {
    if (!fileList.length) return;
    const fd = new FormData();
    fileList.forEach((f) => fd.append("files", f)); // multi-file: repeated 'files'
    await fetch("/api/jobs", { method: "POST", body: fd });
    $("#file-input").value = ""; // allow re-adding the same file
  }

  // ---------------- jobs rendering ----------------
  async function refreshJobs() {
    const data = await fetch("/api/jobs").then((r) => r.json());
    const staged = data.jobs.filter((j) => j.status === "staged").length;
    const other = data.jobs.length - staged;
    $("#jobs-count").textContent = staged
      ? `${staged} to start, ${other} processed`
      : `${other} job(s)`;
    const list = $("#jobs-list");
    list.innerHTML = "";
    // Empty queue shows a plain hello instead of a tutorial.
    const empty = data.jobs.length === 0;
    $("#landing-hello").hidden = !empty;
    $("#jobs-pane").hidden = empty;
    data.jobs.forEach((j) => list.appendChild(jobCard(j)));
  }

  function progressBarHtml(fraction) {
    // Determinate when the backend reports 0..1, indeterminate otherwise.
    if (typeof fraction === "number" && isFinite(fraction)) {
      const pct = Math.max(0, Math.min(100, fraction * 100)).toFixed(1);
      return `<div class="progress-bar job-progress"><div class="progress-fill" style="width:${pct}%"></div></div>`;
    }
    return `<div class="progress-bar job-progress"><div class="progress-fill indeterminate"></div></div>`;
  }

  function setProgressFill(el, fraction) {
    const fill = el.querySelector(".job-progress .progress-fill");
    if (!fill || typeof fraction !== "number" || !isFinite(fraction)) return;
    fill.classList.remove("indeterminate");
    fill.style.width = `${Math.max(0, Math.min(100, fraction * 100)).toFixed(1)}%`;
  }

  function jobCard(j) {
    const el = document.createElement("article");
    el.className = "job card";
    el.dataset.status = j.status; // status accent border via CSS
    el.id = `job-${j.id}`;

    if (j.status === "staged") {
      // ---- staged: editable settings + start/remove ----
      const profileOptions = profileList.map(
        (p) => `<option value="${p.name}" ${p.name === j.profile ? "selected" : ""}>${p.name} — ${escapeHtml(p.description)}</option>`
      ).join("");
      const taskOptions = taskList.map(
        (t) => `<option value="${t}" ${t === j.task ? "selected" : ""}>${t}</option>`
      ).join("");
      const showPrompt = j.task !== "raw";
      el.innerHTML = `
        <div class="job-head">
          <strong class="job-name">${escapeHtml(j.filename)}</strong>
          <span class="badge badge-staged">ready to start</span>
        </div>
        <div class="staged-controls">
          <label class="ctl">
            <span class="muted">Profile</span>
            <select class="job-profile">${profileOptions}</select>
          </label>
          <label class="ctl">
            <span class="muted">Output</span>
            <select class="job-task">${taskOptions}</select>
          </label>
        </div>
        ${showPrompt ? `
        <details class="prompt-details">
          <summary>Prompt (editable preset — or type your own)</summary>
          <label class="ctl" style="margin-top:0.5rem">
            <textarea class="job-prompt" rows="3" placeholder="Describe what you want from the transcript">${escapeHtml(j.custom_prompt || "")}</textarea>
          </label>
        </details>` : ""}
        <div class="job-actions">
          <button class="btn btn-small start-btn" data-id="${j.id}"><svg class="ic ic-sm" width="15" height="15"><use href="#i-play"/></svg> Start</button>
          <button class="btn btn-ghost btn-small remove-btn" data-id="${j.id}"><svg class="ic ic-sm" width="15" height="15"><use href="#i-trash"/></svg> Remove</button>
        </div>
      `;
      el.querySelector(".job-profile").addEventListener("change", async (e) => {
        await fetch(`/api/jobs/${j.id}/update`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile: e.target.value }),
        });
      });
      el.querySelector(".job-task").addEventListener("change", async (e) => {
        const task = e.target.value;
        // Server resets the prompt for the new task; re-render the card to show it
        await fetch(`/api/jobs/${j.id}/update`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ task }),
        });
        refreshJobs();
      });
      const promptEl = el.querySelector(".job-prompt");
      if (promptEl) {
        promptEl.addEventListener("change", async (e) => {
          await fetch(`/api/jobs/${j.id}/update`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ custom_prompt: e.target.value }),
          });
        });
      }
      el.querySelector(".start-btn").addEventListener("click", async () => {
        await fetch(`/api/jobs/${j.id}/start`, { method: "POST" });
        refreshJobs();
      });
      el.querySelector(".remove-btn").addEventListener("click", async () => {
        await fetch(`/api/jobs/${j.id}/delete?delete_artifacts=false`, { method: "POST" });
        refreshJobs();
      });
      jobs.set(j.id, el);
      return el;
    }

    // ---- processed (queued/running/done/failed) ----
    el.innerHTML = `
      <div class="job-head">
        <strong class="job-name">${escapeHtml(j.filename)}</strong>
        <span class="badge badge-${j.status}">${j.status}</span>
        <span class="muted small job-meta">${escapeHtml(j.profile)} &middot; ${escapeHtml(j.task)}</span>
      </div>
      <div class="job-stage muted">${escapeHtml(j.stage || "")}</div>
      ${(j.status === "queued" || j.status === "running") ? progressBarHtml(j.fraction) : ""}
      ${j.error ? `<pre class="error">${escapeHtml(j.error)}</pre>` : ""}
      ${j.out_dir ? `<div class="saved-path">Saved to: <code>${escapeHtml(j.out_dir)}</code></div>` : ""}
      <div class="job-actions">
        ${j.out_dir ? `<button class="btn btn-small open-folder-btn" data-id="${j.id}"><svg class="ic ic-sm" width="15" height="15"><use href="#i-folder"/></svg> Open folder</button>` : ""}
        ${j.status === "failed" ? `<button class="btn btn-small retry-btn" data-id="${j.id}"><svg class="ic ic-sm" width="15" height="15"><use href="#i-play"/></svg> Retry</button>` : ""}
        ${j.status === "done" || j.status === "failed" ? `<button class="btn btn-ghost btn-small rm-btn" data-id="${j.id}"><svg class="ic ic-sm" width="15" height="15"><use href="#i-trash"/></svg> Remove</button>` : ""}
        ${j.status === "done" && j.out_dir ? `<button class="btn btn-ghost btn-small btn-danger del-btn" data-id="${j.id}"><svg class="ic ic-sm" width="15" height="15"><use href="#i-trash"/></svg> Delete + files</button>` : ""}
      </div>
    `;
    const openBtn = el.querySelector(".open-folder-btn");
    if (openBtn) {
      openBtn.addEventListener("click", async () => {
          try {
            const r = await fetch(`/api/jobs/${j.id}/open-folder`, { method: "POST" });
            if (!r.ok) {
              // FastAPI wraps unhandled 500s as {"detail":"Internal Server Error"};
              // ask the text body for anything more specific.
              const text = await r.text().catch(() => "");
              let msg = "unknown";
              try { msg = JSON.parse(text).detail || text || msg; } catch (_) { msg = text || msg; }
              await modalAlert("Could not open folder", `HTTP ${r.status}: ${msg}`);
            }
          } catch (e) {
            await modalAlert("Could not open folder", String(e));
          }
      });
    }
    const retryBtn = el.querySelector(".retry-btn");
    if (retryBtn) retryBtn.addEventListener("click", async () => {
      const r = await fetch(`/api/jobs/${j.id}/retry`, { method: "POST" });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        let msg = "unknown";
        try { msg = JSON.parse(text).detail || text || msg; } catch (_) { msg = text || msg; }
        await modalAlert("Could not restart job", `HTTP ${r.status}: ${msg}`);
        return;
      }
      refreshJobs();
    });
    const rmBtn = el.querySelector(".rm-btn");
    if (rmBtn) rmBtn.addEventListener("click", async () => {
      if (!await modalConfirm({ title: "Remove job?", message: "Remove this job from the list? Files stay on disk.", okText: "Remove" })) return;
      await fetch(`/api/jobs/${j.id}/delete?delete_artifacts=false`, { method: "POST" });
      refreshJobs();
    });
    const delBtn = el.querySelector(".del-btn");
    if (delBtn) delBtn.addEventListener("click", async () => {
      if (!await modalConfirm({ title: "Delete job and files?", message: `Delete this job AND its output folder?\n\n${j.out_dir}\n\nOnly the folder created by meetingnotes is deleted.`, okText: "Delete", danger: true })) return;
      await fetch(`/api/jobs/${j.id}/delete?delete_artifacts=true`, { method: "POST" });
      refreshJobs();
    });
    jobs.set(j.id, el);
    return el;
  }

  function updateJobInPlace(payload) {
    const el = jobs.get(payload.id);
    if (!el) return refreshJobs();
    if (payload.status) el.dataset.status = payload.status;
    const badge = el.querySelector(".badge");
    if (payload.status && badge) {
      badge.className = `badge badge-${payload.status}`;
      badge.textContent = payload.status;
    }
    const bar = el.querySelector(".job-progress");
    if (bar && payload.status) bar.hidden = !(payload.status === "queued" || payload.status === "running");
    setProgressFill(el, payload.fraction);
    const stageEl = el.querySelector(".job-stage");
    if (payload.stage && stageEl) stageEl.textContent = payload.stage;
    if (payload.out_dir) {
      let pathEl = el.querySelector(".saved-path");
      if (!pathEl) {
        const anchor = el.querySelector(".job-actions");
        if (anchor) {
          anchor.insertAdjacentHTML(
            "beforebegin",
            `<div class="saved-path">Saved to: <code>${escapeHtml(payload.out_dir)}</code></div>`
          );
        }
      } else {
        pathEl.querySelector("code").textContent = payload.out_dir;
      }
    }
    if (payload.error) {
      el.querySelectorAll(".error").forEach((e) => e.remove());
      el.insertAdjacentHTML("beforeend", `<pre class="error">${escapeHtml(payload.error)}</pre>`);
    }
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  // ---------------- in-page modal (no origin prefix like native dialogs) ----------------
  function modalPrompt({ title, message, okText = "OK", danger = false, hideCancel = false }) {
    return new Promise((resolve) => {
      const overlay = $("#modal-overlay");
      const titleEl = $("#modal-title");
      const msgEl = $("#modal-message");
      const okBtn = $("#modal-ok");
      const cancelBtn = $("#modal-cancel");
      titleEl.textContent = title;
      msgEl.textContent = message;
      okBtn.textContent = okText;
      okBtn.classList.toggle("btn-danger-solid", danger);
      cancelBtn.hidden = hideCancel;
      overlay.hidden = false;
      const done = (value) => {
        overlay.hidden = true;
        okBtn.onclick = cancelBtn.onclick = overlay.onclick = null;
        document.removeEventListener("keydown", onKey);
        resolve(value);
      };
      const onKey = (e) => { if (e.key === "Escape" && !hideCancel) done(false); };
      okBtn.onclick = () => done(true);
      cancelBtn.onclick = () => done(false);
      overlay.onclick = (e) => { if (e.target === overlay && !hideCancel) done(false); };
      document.addEventListener("keydown", onKey);
      (hideCancel ? okBtn : cancelBtn).focus();
    });
  }
  const modalConfirm = (opts) => modalPrompt(opts);
  const modalAlert = (title, message) =>
    modalPrompt({ title, message, okText: "OK", hideCancel: true });

  // ---------------- events (SSE) ----------------
  // Single live stream per tab. The old code spawned a new EventSource on
  // every error WITHOUT closing the failed one: each zombie kept a socket
  // in CONNECTING state (plus its own native auto-retry). exe restarts /
  // sleep-wake therefore accumulated streams until the browser's ~6
  // connections-per-host pool was exhausted and pages stopped loading.
  let eventStream = null;
  let eventRetryMs = 1000;
  function subscribeEvents() {
    if (eventStream) {
      try { eventStream.close(); } catch (_) { /* already dead */ }
      eventStream = null;
    }
    const es = new EventSource("/api/events");
    eventStream = es;
    es.onopen = () => { eventRetryMs = 1000; }; // healthy again: reset backoff
    es.addEventListener("job", (e) => {
      const data = JSON.parse(e.data);
      if (data.status === "deleted") return refreshJobs();
      const card = jobs.get(data.id);
      // Same-state progress ticks (stage/ETA text) update in place — smooth,
      // no rebuild. A lifecycle change re-renders the card for its new UI.
      if (card && data.status && card.dataset.status === data.status) {
        return updateJobInPlace(data);
      }
      return refreshJobs();
    });
    es.addEventListener("model", (e) => {
      const data = JSON.parse(e.data);
      const size = $("#model-size").value;
      if (data.size !== size) return;
      // Terminal states re-render fully (buttons change); otherwise live progress.
      if (data.installed || (!data.downloading && !data.loading)) return refreshModelStatus();
      if (data.downloading) {
        $("#model-progress").hidden = false;
        $("#model-progress-fill").style.width = `${(data.progress * 100).toFixed(1)}%`;
        $("#model-progress-text").textContent =
          `Downloading ${size}: ${(data.progress * 100).toFixed(0)}%`;
      } else if (data.loading) {
        $("#model-progress").hidden = false;
        $("#model-progress-fill").style.width = `100%`;
        $("#model-progress-text").textContent = `Loading ${size} into memory...`;
      }
    });
    // Close the failed stream BEFORE resubscribing so a flapping server
    // can never stack zombie connections. Backoff caps at 30 s.
    es.onerror = () => {
      if (eventStream === es) {
        try { es.close(); } catch (_) { /* already dead */ }
        eventStream = null;
      }
      const wait = eventRetryMs + Math.random() * 500;
      eventRetryMs = Math.min(eventRetryMs * 2, 30000);
      setTimeout(subscribeEvents, wait);
    };
  }
  // Release the socket on tab close / navigation (bfcache included).
  window.addEventListener("pagehide", () => {
    if (eventStream) {
      try { eventStream.close(); } catch (_) { /* already dead */ }
      eventStream = null;
    }
  });

  // ---------------- LLM model list (Ollama) ----------------
  async function refreshLlmModels() {
    const hint = $("#llm-model-hint");
    const btn = $("#refresh-llm-models");
    if (btn.disabled) return; // a fetch is already in flight — no overlapping rebuilds
    btn.disabled = true;
    hint.textContent = "Loading...";
    hint.style.color = "";
    // Query with the CURRENT form values so switching provider/host/key
    // refreshes immediately without requiring Save first.
    const prov = $("#llm-provider").value;
    const params = new URLSearchParams({ provider: prov });
    if (prov === "ollama") {
      params.set("host", $("#ollama-host").value.trim());
      params.set("key", $("#ollama-key").value);
    } else if (prov === "compat") {
      params.set("host", $("#compat-url").value.trim());
      params.set("key", $("#compat-key").value);
    } else {
      params.set("key", $("#cloud-key").value);
    }
    try {
      const r = await fetch("/api/llm/models?" + params.toString()).then((r) => r.json());
      const sel = $("#llm-model");
      const current = getLlmModelValue(); // preserve the user's selection across rebuilds
      sel.innerHTML = "";
      if (r.key_invalid) {
        // Bad key: show it clearly, keep Custom option
        const pending = window._pendingLlmModel || "";
        if (pending) {
          const opt = document.createElement("option");
          opt.value = pending;
          opt.textContent = pending;
          sel.appendChild(opt);
          sel.value = pending;
        }
        const customOpt = document.createElement("option");
        customOpt.value = "__custom__";
        customOpt.textContent = "Custom...";
        sel.appendChild(customOpt);
        hint.textContent = r.error;
        hint.style.color = "var(--fail)";
        if (window._pendingLlmModel !== undefined) delete window._pendingLlmModel;
        return;
      }
      if (r.models && r.models.length) {
        r.models.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m;
          opt.textContent = m;
          sel.appendChild(opt);
        });
        // Add Custom option
        const customOpt = document.createElement("option");
        customOpt.value = "__custom__";
        customOpt.textContent = "Custom...";
        sel.appendChild(customOpt);
        // Restore selection: explicit pending from settings wins; otherwise
        // keep whatever was selected before the rebuild (never reset to option 0).
        if (window._pendingLlmModel !== undefined) {
          setLlmModelValue(window._pendingLlmModel);
          delete window._pendingLlmModel;
        } else {
          setLlmModelValue(current);
        }
        if (r.warning) {
          // Transient issue (e.g. rate limit): list is shown, key may still be fine.
          hint.textContent = `${r.models.length} models available. Note: ${r.warning}`;
        } else {
          hint.textContent = `${r.models.length} models available`;
        }
        // A cloud key against a local daemon is silently unused — say so.
        if (prov === "ollama" && $("#ollama-key").value.trim()
            && /localhost|127\.0\.0\.1/.test($("#ollama-host").value.trim())) {
          hint.textContent += " — key is unused against a local daemon; use the Cloud host for cloud models.";
        }
      } else {
        // No models fetched — still provide Custom and any pending value
        const pending = window._pendingLlmModel || "";
        if (pending) {
          const opt = document.createElement("option");
          opt.value = pending;
          opt.textContent = pending;
          sel.appendChild(opt);
          sel.value = pending;
        }
        const customOpt = document.createElement("option");
        customOpt.value = "__custom__";
        customOpt.textContent = "Custom...";
        sel.appendChild(customOpt);
        hint.textContent = r.error || "No models found. Check host/key and save settings first.";
        if (window._pendingLlmModel !== undefined) delete window._pendingLlmModel;
      }
    } catch (e) {
      $("#llm-model-hint").textContent = "Failed to fetch models";
    } finally {
      btn.disabled = false;
    }
  }

  $("#refresh-llm-models").addEventListener("click", refreshLlmModels);

  $("#browse-folder-btn").addEventListener("click", async () => {
    const btn = $("#browse-folder-btn");
    if (btn.disabled) return; // picker already open
    btn.disabled = true;
    try {
      const r = await fetch("/api/settings/browse-folder", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current: $("#output-dir").value.trim() }),
      });
      if (!r.ok) {
        const text = await r.text().catch(() => "");
        let msg = "unknown";
        try { msg = JSON.parse(text).detail || text || msg; } catch (_) { msg = text || msg; }
        await modalAlert("Folder picker failed", `HTTP ${r.status}: ${msg}`);
        return;
      }
      const data = await r.json();
      if (data.path) {
        $("#output-dir").value = data.path;
        updateFoldersPaths();
      }
    } catch (e) {
      await modalAlert("Folder picker failed", String(e));
    } finally {
      btn.disabled = false;
    }
  });

  // Host preset chips (Ollama Local/Cloud, LM Studio/llama.cpp/vLLM):
  // one click fills the host and re-lists immediately.
  document.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip[data-host]");
    if (!chip) return;
    const input = document.getElementById(chip.dataset.target);
    if (!input) return;
    input.value = chip.dataset.host;
    refreshLlmModels();
  });

  // ---------------- init ----------------
  $("#quit-btn").addEventListener("click", async () => {
    if (!await modalConfirm({ title: "Shut down MeetingNotes?", message: "The server stops and the window can be closed.", okText: "Shut down", danger: true })) return;
    try { await fetch("/api/shutdown", { method: "POST" }); } catch (_) {}
    document.body.innerHTML = "<main style='padding:2rem;font-family:sans-serif;color:#8b93a3'>Shutting down&hellip; you can close this tab.</main>";
  });

  (async () => {
    try {
      const v = await fetch("/api/version").then((r) => r.json());
      $("#app-version").textContent = `v${v.version} · started ${v.started_at}`;
    } catch (_) {}
    const models = await fetch("/api/models").then((r) => r.json());
    const sel = $("#model-size");
    sel.innerHTML = "";
    models.models.forEach((m) => sel.insertAdjacentHTML("beforeend", `<option value="${m.size}">${m.size}</option>`));
    await loadProfiles();
    const settings = await loadSettings();
    // Fire-and-forget: the LLM model list must NEVER block the queue UI,
    // even if the cloud endpoint is slow or rate-limiting us.
    refreshLlmModels();
    await refreshJobs();
    subscribeEvents();
    // First launch ever: slide-through guide. Reopenable via Settings.
    if (settings && !settings.onboarded) openOnboard();
  })();
})();
