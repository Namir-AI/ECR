"use strict";

const initializeDraftAutosave = () => {
  const form = document.querySelector("[data-autosave-form]");
  if (!form || form.dataset.autosaveInitialized === "true") {
    return;
  }
  form.dataset.autosaveInitialized = "true";
  const status = form.querySelector("[data-autosave-status]");
  let debounceTimer;
  let retryTimer;
  let saving = false;
  let saveRequested = false;
  let revision = 0;
  let dirty = false;
  const seriesSelect = form.querySelector("[data-series-select]");
  let lastSavedSeries = form.dataset.storedSeries;
  let needsReload = false;
  const seriesRules = JSON.parse(form.dataset.seriesRules || "{}");
  const updateSections = () => {
    const applicable = seriesRules[seriesSelect ? seriesSelect.value : form.dataset.storedSeries] || [];
    form.querySelectorAll("[data-conditional-section]").forEach((section) => {
      const active = applicable.includes(section.dataset.conditionalSection);
      section.hidden = !active;
      section.querySelectorAll("input, select").forEach((field) => { field.disabled = !active; });
      section.querySelectorAll("[data-final-required]").forEach((marker) => { marker.hidden = !active; });
    });
  };
  updateSections();

  // Compare exact decimal strings without binary floating-point rounding.
  const decimalKey = (value) => {
    const match = value.match(/^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
    if (!match || (!match[2] && !match[3])) return value;
    const exponent = Number(match[4] || 0);
    if (Math.abs(exponent) > 100) return value;
    const digits = match[2] + (match[3] || "");
    const point = match[2].length + exponent;
    const whole = (point <= 0 ? "0" : digits.slice(0, point).padEnd(point, "0")).replace(/^0+(?=\d)/, "");
    const fraction = (point < 0 ? "0".repeat(-point) + digits : digits.slice(Math.max(0, point))).replace(/0+$/, "");
    const key = whole + (fraction ? `.${fraction}` : "");
    return match[1] === "-" && key !== "0" ? `-${key}` : key;
  };
  const currentValues = () => {
    const values = {
      erection_start_date: form.elements.namedItem("erection_start_date").value || null,
      erection_completion_date: form.elements.namedItem("erection_completion_date").value,
    };
    if (seriesSelect) values.cooling_tower_series = seriesSelect.value;
    ["page1", "page2"].forEach((page) => {
      if (!form.elements.namedItem(`${page}_present`)) return;
      values[page] = {};
      form.querySelectorAll(`[data-${page}-field]:not(:disabled)`).forEach((field) => {
        const value = field.value.trim();
        values[page][field.name] = field.hasAttribute("data-decimal") || field.hasAttribute("data-integer") ? decimalKey(value) : value;
      });
    });
    if (values.page1) {
      values.page1.blade_serials = Array.from(form.querySelectorAll('[name="blade_serials"]'))
        .map((field) => field.value.trim()).filter(Boolean);
    }
    return values;
  };
  const sameValues = (left, right) => (
    left.erection_start_date === right.erection_start_date &&
    left.erection_completion_date === right.erection_completion_date &&
    (!Object.hasOwn(right, "cooling_tower_series") || left.cooling_tower_series === right.cooling_tower_series) &&
    ["page1", "page2"].every((page) => !right[page] || (left[page] && Object.keys(right[page]).every((name) =>
      JSON.stringify(left[page][name]) === JSON.stringify(right[page][name])
    )))
  );

  const showStatus = (message, state) => {
    status.textContent = message;
    status.dataset.state = state;
  };

  const restoreSeries = async () => {
    // A failed response may follow a committed write: read the server again,
    // rather than guessing that the last browser acknowledgement is authoritative.
    let authoritative = lastSavedSeries;
    let editable = false;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(form.dataset.seriesStateUrl, {
        credentials: "same-origin", cache: "no-store", signal: controller.signal,
        headers: { "Accept": "application/json" },
      });
      if (!response.ok || response.redirected) throw new Error("Unable to reload Series");
      const state = await response.json();
      if (typeof state.series !== "string" || typeof state.editable !== "boolean") {
        throw new Error("Series was not confirmed");
      }
      authoritative = state.series;
      editable = state.editable;
      lastSavedSeries = authoritative;
      form.dataset.storedSeries = authoritative;
    } catch (_error) {
      // Preserve unsaved technical inputs, but require a reload before more writes.
      needsReload = true;
    } finally {
      clearTimeout(timeout);
    }
    if (!Array.from(seriesSelect.options).some(option => option.value === authoritative)) {
      const legacy = new Option(authoritative, authoritative);
      legacy.disabled = true;
      seriesSelect.add(legacy);
    }
    seriesSelect.value = authoritative;
    seriesSelect.disabled = !editable;
    seriesSelect.dispatchEvent(new Event("series-authoritative-restored"));
    updateSections();
  };

  const save = async () => {
    clearTimeout(debounceTimer);
    clearTimeout(retryTimer);
    dirty = true;
    if (needsReload) {
      showStatus("Unable to save — reload to verify the saved Series", "error");
      return;
    }
    if (saving) {
      saveRequested = true;
      return;
    }
    if (!form.reportValidity()) {
      showStatus("Unable to save — check the entered fields", "error");
      return;
    }

    saving = true;
    saveRequested = false;
    const sentRevision = revision;
    const sentValues = currentValues();
    const seriesChange = seriesSelect && sentValues.cooling_tower_series !== lastSavedSeries;
    let restoredAfterFailure = false;
    // Capture one immutable snapshot. Never send overlapping save requests.
    const body = new FormData(form);
    // A disabled legacy display option is not a new selection; preserve it until
    // the owner explicitly selects an approved value, including in confirmations.
    if (seriesSelect) body.set("cooling_tower_series", sentValues.cooling_tower_series);
    const controller = new AbortController();
    const requestTimeout = window.setTimeout(() => controller.abort(), 15000);
    showStatus("Saving…", "saving");
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body,
        credentials: "same-origin",
        signal: controller.signal,
        headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
      });
      if (response.redirected) {
        const error = new Error("session expired; sign in again");
        error.retryable = false;
        throw error;
      }
      let result;
      try {
        result = await response.json();
      } catch (_parseError) {
        const error = new Error("server did not confirm the save");
        error.retryable = response.status >= 500;
        throw error;
      }
      if (!response.ok || result.ok !== true) {
        const error = new Error(result.message || "save was rejected; reload and sign in if necessary");
        error.retryable = response.status >= 500;
        throw error;
      }
      if (!result.values || !sameValues(result.values, sentValues)) {
        const error = new Error("server did not confirm the current values");
        error.retryable = false;
        throw error;
      }
      if (seriesSelect) {
        lastSavedSeries = result.values.cooling_tower_series;
        form.dataset.storedSeries = lastSavedSeries;
      }
      // An old acknowledgement cannot mark a newer edit as saved.
      if (sentRevision === revision && sameValues(sentValues, currentValues())) {
        dirty = false;
        showStatus("Saved", "saved");
      } else {
        saveRequested = true;
        showStatus("Saving…", "saving");
      }
    } catch (error) {
      if (seriesChange) {
        restoredAfterFailure = true;
        await restoreSeries();
        saveRequested = false;
        clearTimeout(debounceTimer);
        clearTimeout(retryTimer);
        showStatus(needsReload ? "Unable to save — reload to verify the saved Series" :
          `Unable to save — ${error.message}. Saved Series restored; review your changes.`, "error");
      } else if (error.retryable === false) {
        showStatus(`Unable to save — ${error.message}`, "error");
      } else {
        showStatus("Unable to save — retrying", "error");
        retryTimer = window.setTimeout(save, 3000);
      }
    } finally {
      clearTimeout(requestTimeout);
      saving = false;
      if (!restoredAfterFailure && (saveRequested || sentRevision !== revision || !sameValues(sentValues, currentValues()))) {
        debounceTimer = window.setTimeout(save, 0);
      }
    }
  };

  const scheduleSave = () => {
    revision += 1;
    dirty = true;
    clearTimeout(debounceTimer);
    clearTimeout(retryTimer);
    if (saving) {
      saveRequested = true;
      showStatus("Saving…", "saving");
    } else {
      showStatus("Unsaved changes", "pending");
      debounceTimer = window.setTimeout(save, 700);
    }
  };

  // Delegation includes newly added blade rows in the same save controller.
  ["input", "change"].forEach((type) => form.addEventListener(type, (event) => {
    if (event.target === seriesSelect) updateSections();
    if (event.target.matches('input:not([type="hidden"]), select')) scheduleSave();
  }));
  form.addEventListener("click", (event) => {
    if (event.target.closest("[data-add-blade]")) {
      const row = form.querySelector("[data-blade-template]").content.cloneNode(true);
      const input = row.querySelector("input");
      form.querySelector("[data-blade-list]").append(row);
      input.focus();
      scheduleSave();
    }
    const remove = event.target.closest("[data-remove-blade]");
    if (remove) {
      remove.closest(".blade-serial-row").remove();
      scheduleSave();
    }
  });
  // Save now and keyboard form submission share the exact autosave path.
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    save();
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirty || saving) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeDraftAutosave);
} else {
  initializeDraftAutosave();
}
