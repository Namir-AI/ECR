"use strict";

const initializeCustomerSignature = () => {
  const card = document.querySelector("[data-signature-card]");
  if (!card || card.dataset.signatureInitialized === "true") return;
  card.dataset.signatureInitialized = "true";
  let canvas = card.querySelector("[data-signature-pad]");
  let context = canvas ? canvas.getContext("2d") : null;
  const editor = card.querySelector("[data-signature-editor]");
  const savedActions = card.querySelector("[data-signature-saved-actions]");
  const replaceButton = card.querySelector("[data-signature-replace]");
  const cancelButton = card.querySelector("[data-signature-replace-cancel]");
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
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    canvas.width = Math.max(1, Math.round(rect.width * ratio));
    canvas.height = Math.max(1, Math.round(rect.height * ratio));
    paint(context, canvas.width, canvas.height);
  };
  // Normalized vector coordinates, not a stretched previous bitmap, survive
  // resizing/orientation changes. Export uses a stable print-quality resolution.
  const resizeObserver = new ResizeObserver(redraw);
  const point = (event) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    };
  };
  const bindPad = () => {
    canvas.addEventListener("pointerdown", (event) => {
      if (busy || !event.isPrimary || event.button !== 0) return;
      event.preventDefault();
      pointerId = event.pointerId;
      activeStroke = [point(event)];
      strokes.push(activeStroke);
      canvas.setPointerCapture(pointerId);
      redraw();
      showStatus("Sign not saved — choose Save Sign", "pending");
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
    resizeObserver.observe(canvas);
    redraw();
  };
  const resetStrokes = () => {
    strokes = [];
    activeStroke = null;
    pointerId = null;
  };
  const showEditor = (editing) => {
    editor.hidden = !editing;
    card.querySelector("[data-saved-signature]").hidden = editing || !savedPresent;
    savedActions.hidden = editing || !savedPresent;
    cancelButton.hidden = !savedPresent;
    if (editing && !canvas) {
      canvas = document.createElement("canvas");
      canvas.className = "signature-pad";
      canvas.dataset.signaturePad = "";
      canvas.setAttribute("aria-label", "Optional Customer Sign area");
      canvas.textContent = "Customer Sign can be entered using a pointer; it is optional.";
      card.querySelector("[data-signature-pad-container]").append(canvas);
      context = canvas.getContext("2d");
      bindPad();
    } else if (!editing && canvas) {
      resizeObserver.disconnect();
      canvas.remove();
      canvas = null;
      context = null;
    }
  };
  if (canvas) bindPad();

  const applyState = (state, keepEditor = false) => {
    if (typeof state.present !== "boolean" || (state.present && (typeof state.signed_at !== "string" || !state.url))) {
      throw new Error("Server did not confirm signature state");
    }
    savedPresent = state.present;
    showEditor(!savedPresent || keepEditor);
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
      showStatus("Enter a Customer Sign before saving", "error");
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
    [saveButton, clearButton, removeButton, replaceButton, cancelButton].forEach(button => { button.disabled = true; });
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 15000);
    showStatus(method === "POST" ? "Saving sign…" : "Removing sign…", "saving");
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
      resetStrokes();
      redraw();
      showStatus(method === "POST" ? "Sign saved" : "Saved sign removed", "saved");
    } catch (_error) {
      // A dropped response can follow a commit. Reconcile display, but never
      // claim a successful save merely because the network request was started.
      try {
        const response = await fetch(card.dataset.signatureStateUrl, {
          credentials: "same-origin", cache: "no-store", signal: controller.signal,
        });
        if (response.ok && !response.redirected) applyState(await response.json(), method === "POST");
      } catch (_stateError) { /* Keep unsaved sign; reload remains safe. */ }
      showStatus(method === "POST"
        ? "Unable to save sign — unsaved sign kept. Retry or reload to verify saved state."
        : "Unable to remove sign. Retry or reload to verify saved state.", "error");
    } finally {
      clearTimeout(timer);
      busy = false;
      [saveButton, clearButton, removeButton, replaceButton, cancelButton].forEach(button => { button.disabled = false; });
      if (feedback.dataset.state === "saved") (savedPresent ? replaceButton : clearButton).focus();
    }
  };
  clearButton.addEventListener("click", () => {
    if (busy) return;
    resetStrokes();
    redraw();
    showStatus(savedPresent ? "Sign cleared; saved sign kept" : "Sign cleared", "neutral");
  });
  replaceButton.addEventListener("click", () => {
    if (busy) return;
    resetStrokes();
    showEditor(true);
    showStatus("Replacement sign not saved; saved sign kept", "neutral");
    clearButton.focus();
  });
  cancelButton.addEventListener("click", () => {
    if (busy) return;
    resetStrokes();
    showEditor(false);
    showStatus("Saved sign kept", "neutral");
    replaceButton.focus();
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
