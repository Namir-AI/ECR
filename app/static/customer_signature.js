"use strict";

const initializeCustomerSignature = () => {
  const card = document.querySelector("[data-signature-card]");
  if (!card) return;
  const canvas = card.querySelector("[data-signature-pad]");
  const context = canvas.getContext("2d");
  const feedback = card.querySelector("[data-signature-status]");
  const saveButton = card.querySelector("[data-signature-save]");
  const clearButton = card.querySelector("[data-signature-clear-drawing]");
  const removeButton = card.querySelector("[data-signature-remove]");
  const dialog = card.querySelector("[data-signature-remove-dialog]");
  const csrf = document.querySelector('[name="csrf_token"]').value;
  let strokes = [];
  let activeStroke = null;
  let pointerId = null;
  let busy = false;
  let savedPresent = card.dataset.signaturePresent === "true";

  const showStatus = (message, state) => {
    feedback.textContent = message;
    feedback.dataset.state = state;
  };
  const paint = (ctx, width, height) => {
    ctx.fillStyle = "white";
    ctx.fillRect(0, 0, width, height);
    ctx.strokeStyle = "#17212b";
    ctx.fillStyle = "#17212b";
    ctx.lineWidth = width / 300;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    strokes.forEach((stroke) => {
      ctx.beginPath();
      stroke.forEach((point, index) => {
        const x = point.x * width, y = point.y * height;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      if (stroke.length === 1) {
        ctx.beginPath();
        ctx.arc(stroke[0].x * width, stroke[0].y * height, ctx.lineWidth / 2, 0, Math.PI * 2);
        ctx.fill();
      }
    });
  };
  const redraw = () => {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    canvas.width = Math.max(1, Math.round(rect.width * ratio));
    canvas.height = Math.max(1, Math.round(rect.height * ratio));
    paint(context, canvas.width, canvas.height);
  };
  // Normalized vector coordinates, not a stretched previous bitmap, survive
  // resizing/orientation changes. Export uses a stable print-quality resolution.
  new ResizeObserver(redraw).observe(canvas);
  const point = (event) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    };
  };
  canvas.addEventListener("pointerdown", (event) => {
    if (busy || !event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    pointerId = event.pointerId;
    activeStroke = [point(event)];
    strokes.push(activeStroke);
    canvas.setPointerCapture(pointerId);
    redraw();
    showStatus("Drawing not saved — choose Save Signature", "pending");
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!activeStroke || event.pointerId !== pointerId) return;
    event.preventDefault();
    activeStroke.push(point(event));
    paint(context, canvas.width, canvas.height);
  });
  const finishStroke = (event) => {
    if (event.pointerId !== pointerId) return;
    activeStroke = null;
    pointerId = null;
  };
  ["pointerup", "pointercancel", "lostpointercapture"].forEach((event) => canvas.addEventListener(event, finishStroke));

  const applyState = (state) => {
    if (typeof state.present !== "boolean" || (state.present && (typeof state.signed_at !== "string" || !state.url))) {
      throw new Error("Server did not confirm signature state");
    }
    savedPresent = state.present;
    card.querySelector("[data-saved-signature]").hidden = !savedPresent;
    card.querySelector("[data-no-signature]").hidden = savedPresent;
    removeButton.hidden = !savedPresent;
    const image = card.querySelector("[data-signature-image]");
    if (savedPresent) {
      // Use the fixed report route, never a client/server supplied filesystem key.
      image.src = `${card.dataset.signatureUrl}?at=${encodeURIComponent(state.signed_at)}`;
      card.querySelector("[data-signed-at]").textContent = `Signed on: ${state.signed_at.replace("T", " ").replace("Z", " UTC")}`;
    } else {
      image.removeAttribute("src");
      card.querySelector("[data-signed-at]").textContent = "";
    }
  };
  const persist = async (method) => {
    if (busy) return;
    if (method === "POST" && strokes.length === 0) {
      showStatus("Draw a customer signature before saving", "error");
      return;
    }
    const body = method === "POST" ? (() => {
      const exportCanvas = document.createElement("canvas");
      exportCanvas.width = 1800;
      exportCanvas.height = 600;
      paint(exportCanvas.getContext("2d"), 1800, 600);
      return JSON.stringify({ signature_png: exportCanvas.toDataURL("image/png") });
    })() : undefined;
    busy = true;
    [saveButton, clearButton, removeButton].forEach(button => { button.disabled = true; });
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 15000);
    showStatus(method === "POST" ? "Saving signature…" : "Removing signature…", "saving");
    try {
      const response = await fetch(card.dataset.signatureUrl, {
        method, body, credentials: "same-origin", signal: controller.signal,
        headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRF-Token": csrf },
      });
      const result = await response.json();
      if (!response.ok || response.redirected || result.ok !== true || !result.signature) {
        throw new Error("Signature save was not confirmed");
      }
      applyState(result.signature);
      strokes = [];
      activeStroke = null;
      redraw();
      showStatus(method === "POST" ? "Signature saved" : "Saved signature removed", "saved");
    } catch (_error) {
      // A dropped response can follow a commit. Reconcile display, but never
      // claim a successful save merely because the network request was started.
      try {
        const response = await fetch(card.dataset.signatureStateUrl, {
          credentials: "same-origin", cache: "no-store", signal: controller.signal,
        });
        if (response.ok && !response.redirected) applyState(await response.json());
      } catch (_stateError) { /* Keep unsaved drawing; reload remains safe. */ }
      showStatus("Unable to save signature — drawing kept. Retry or reload to verify saved state.", "error");
    } finally {
      clearTimeout(timer);
      busy = false;
      [saveButton, clearButton, removeButton].forEach(button => { button.disabled = false; });
    }
  };
  clearButton.addEventListener("click", () => {
    if (busy) return;
    strokes = [];
    activeStroke = null;
    redraw();
    showStatus(savedPresent ? "Drawing cleared; saved signature kept" : "Signature is optional", "neutral");
  });
  saveButton.addEventListener("click", () => persist("POST"));
  removeButton.addEventListener("click", () => dialog.showModal());
  const closeDialog = () => { dialog.close(); removeButton.focus(); };
  card.querySelector("[data-signature-remove-cancel]").addEventListener("click", closeDialog);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); closeDialog(); });
  card.querySelector("[data-signature-remove-confirm]").addEventListener("click", () => {
    closeDialog();
    persist("DELETE");
  });
  window.addEventListener("beforeunload", (event) => {
    if (strokes.length || busy) { event.preventDefault(); event.returnValue = ""; }
  });
};

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initializeCustomerSignature);
else initializeCustomerSignature();
