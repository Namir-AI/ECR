"use strict";
(() => {
  const card = document.querySelector("[data-package-attachments]");
  if (!card) return;
  const status = card.querySelector("[data-attachment-status]");
  const csrf = card.querySelector('[name="csrf_token"]')?.value;
  let busy = false;
  const endpoint = location.pathname;
  const feedback = (message, state) => { status.textContent = message; status.dataset.state = state; };
  card.querySelectorAll('.attachment-picker input[type="file"]').forEach(input => input.addEventListener("change", () => {
    input.closest(".attachment-picker").querySelector("[data-selected-filenames]").textContent = [...input.files].map(file => file.name).join(", ") || "No file selected";
  }));
  async function save(data) {
    if (busy) return;
    busy = true;
    const controls = [...card.querySelectorAll("button")];
    const previous = controls.map(c => c.disabled);
    controls.forEach(c => { c.disabled = true; });
    feedback("Saving package attachments…", "saving");
    try {
      const response = await fetch(endpoint, {method: "POST", body: data, headers: {"X-CSRF-Token": csrf}, credentials: "same-origin"});
      const result = await response.json();
      if (!response.ok || result.ok !== true) throw new Error(result.message || result.detail || "Unable to save attachments.");
      feedback("Package attachments saved.", "saved");
      location.reload();
    } catch (error) { feedback(error.message || "Unable to save attachments. Reload and retry.", "error"); }
    finally { busy = false; controls.forEach((c, i) => { c.disabled = previous[i]; }); }
  }
  card.querySelectorAll("[data-attachment-form]").forEach(form => form.addEventListener("submit", event => {
    event.preventDefault();
    const data = new FormData(form);
    if (event.submitter?.name === "replace_jcc") data.set("action", "JCC_REPLACE");
    const files = [...form.querySelector('[name="files"]').files];
    if (form.dataset.kind === "TOWER_PHOTO") {
      const replacing = data.get("action") === "TOWER_PHOTO_REPLACE";
      const replaced = replacing ? card.querySelector(`[data-file-id="${data.get("target_id")}"]`) : null;
      const count = Number(card.dataset.photoCount) - (replacing ? 1 : 0) + files.length;
      const bytes = Number(card.dataset.photoBytes) - Number(replaced?.dataset.byteSize || 0) + files.reduce((n, f) => n + f.size, 0);
      if (count > 5 || bytes > Number(card.dataset.photoLimit)) { feedback("Maximum 5 Tower Photos and 5 MB combined. Existing photos were not changed.", "error"); return; }
    }
    save(data);
  }));
  card.querySelectorAll("[data-attachment-action]").forEach(button => button.addEventListener("click", () => {
    const data = new FormData(); data.set("action", button.dataset.attachmentAction);
    if (button.dataset.targetId) data.set("target_id", button.dataset.targetId);
    save(data);
  }));
  card.querySelectorAll("[data-reorder]").forEach(button => button.addEventListener("click", () => {
    const items = [...button.closest("ol").querySelectorAll("[data-file-id]")];
    const index = items.indexOf(button.closest("li")), next = index + Number(button.dataset.direction);
    if (next < 0 || next >= items.length) return;
    [items[index], items[next]] = [items[next], items[index]];
    const data = new FormData(); data.set("action", button.dataset.reorder + "_REORDER");
    data.set("order", JSON.stringify(items.map(item => Number(item.dataset.fileId)))); save(data);
  }));
})();
