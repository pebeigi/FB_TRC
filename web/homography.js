const palette = [
  "#ff6b6b", "#ffd166", "#06d6a0", "#4cc9f0", "#b892ff", "#ff8fab",
  "#90be6d", "#f9844a", "#43aa8b", "#577590", "#f72585", "#a7c957",
];

const state = {
  payload: null,
  camera: null,
  points: [],
  pendingCameraPoint: null,
  calibration: null,
  dirty: false,
  pointsSaved: false,
  pointRevision: Date.now(),
};

let pointSaveChain = Promise.resolve();

const elements = {
  cameraSelect: document.querySelector("#camera-select"),
  capture: document.querySelector("#capture-btn"),
  savedBadge: document.querySelector("#saved-badge"),
  frameModeBadge: document.querySelector("#frame-mode-badge"),
  cameraEmpty: document.querySelector("#camera-empty"),
  mapLoading: document.querySelector("#map-loading"),
  referenceName: document.querySelector("#reference-name"),
  cameraCoordinates: document.querySelector("#camera-coordinates"),
  mapCoordinates: document.querySelector("#map-coordinates"),
  pairPrompt: document.querySelector("#pair-prompt"),
  pointCount: document.querySelector("#point-count"),
  pointTable: document.querySelector("#point-table"),
  inliers: document.querySelector("#inlier-value"),
  rmse: document.querySelector("#rmse-value"),
  maxError: document.querySelector("#max-error-value"),
  matrix: document.querySelector("#matrix-value"),
  status: document.querySelector("#status-message"),
  save: document.querySelector("#save-btn"),
  clear: document.querySelector("#clear-btn"),
  undo: document.querySelector("#undo-btn"),
  delete: document.querySelector("#delete-btn"),
};

class ImageViewport {
  constructor(canvas, coordinateElement) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.coordinateElement = coordinateElement;
    this.image = null;
    this.tileSource = null;
    this.tileCache = new Map();
    this.logicalWidth = 1;
    this.logicalHeight = 1;
    this.scale = 1;
    this.fitScale = 1;
    this.offsetX = 0;
    this.offsetY = 0;
    this.markers = [];
    this.pendingMarker = null;
    this.polygon = null;
    this.clickHandler = null;
    this.drag = null;
    this.moved = false;

    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    canvas.addEventListener("wheel", (event) => this.onWheel(event), { passive: false });
    canvas.addEventListener("pointerdown", (event) => this.onPointerDown(event));
    canvas.addEventListener("pointermove", (event) => this.onPointerMove(event));
    canvas.addEventListener("pointerup", (event) => this.onPointerUp(event));
    canvas.addEventListener("pointercancel", () => this.cancelPointer());
    canvas.addEventListener("pointerleave", () => {
      coordinateElement.textContent = "x —, y —";
    });
  }

  async setImage(url, logicalWidth = null, logicalHeight = null) {
    if (!url) {
      this.image = null;
      this.draw();
      return;
    }
    const image = new Image();
    image.decoding = "async";
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = () => reject(new Error(`Could not load ${url}`));
      image.src = url;
    });
    this.image = image;
    this.logicalWidth = logicalWidth || image.naturalWidth;
    this.logicalHeight = logicalHeight || image.naturalHeight;
    this.fit();
  }

  setTileSource(source) {
    this.tileSource = source;
    this.tileCache.clear();
    this.draw();
  }

  tileUrl(zoom, x, y) {
    return this.tileSource.urlTemplate
      .replace("{z}", zoom)
      .replace("{x}", x)
      .replace("{y}", y);
  }

  requestTile(zoom, x, y) {
    const key = `${zoom}/${x}/${y}`;
    if (this.tileCache.has(key)) return this.tileCache.get(key);
    const entry = { image: new Image(), loaded: false, failed: false };
    this.tileCache.set(key, entry);
    entry.image.decoding = "async";
    entry.image.onload = () => {
      entry.loaded = true;
      this.draw();
    };
    entry.image.onerror = () => {
      entry.failed = true;
    };
    entry.image.src = this.tileUrl(zoom, x, y);
    return entry;
  }

  drawDetailTiles(viewWidth, viewHeight) {
    const source = this.tileSource;
    if (!source || this.scale < source.activationScale) return;

    const zoom = this.scale >= 0.7 ? source.maxZoom : source.minZoom;
    const factor = 2 ** (source.maxZoom - zoom);
    const logicalTileSpan = source.tileSize * factor;
    const columns = Math.ceil(this.logicalWidth / logicalTileSpan);
    const rows = Math.ceil(this.logicalHeight / logicalTileSpan);
    const left = Math.max(0, -this.offsetX / this.scale);
    const top = Math.max(0, -this.offsetY / this.scale);
    const right = Math.min(this.logicalWidth, (viewWidth - this.offsetX) / this.scale);
    const bottom = Math.min(this.logicalHeight, (viewHeight - this.offsetY) / this.scale);
    if (right <= left || bottom <= top) return;

    const firstX = Math.max(0, Math.floor(left / logicalTileSpan));
    const lastX = Math.min(columns - 1, Math.floor((right - 1e-6) / logicalTileSpan));
    const firstY = Math.max(0, Math.floor(top / logicalTileSpan));
    const lastY = Math.min(rows - 1, Math.floor((bottom - 1e-6) / logicalTileSpan));

    for (let y = firstY; y <= lastY; y += 1) {
      for (let x = firstX; x <= lastX; x += 1) {
        const entry = this.requestTile(zoom, x, y);
        if (!entry.loaded) continue;
        this.ctx.drawImage(
          entry.image,
          x * logicalTileSpan,
          y * logicalTileSpan,
          entry.image.naturalWidth * factor,
          entry.image.naturalHeight * factor,
        );
      }
    }
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(rect.width * ratio));
    const height = Math.max(1, Math.round(rect.height * ratio));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
      if (this.image && !this.fitScale) this.fit();
      else this.draw();
    }
  }

  fit() {
    if (!this.image) return;
    const rect = this.canvas.getBoundingClientRect();
    this.fitScale = Math.min(rect.width / this.logicalWidth, rect.height / this.logicalHeight);
    this.scale = this.fitScale;
    this.offsetX = (rect.width - this.logicalWidth * this.scale) / 2;
    this.offsetY = (rect.height - this.logicalHeight * this.scale) / 2;
    this.draw();
  }

  zoom(factor, screenX = null, screenY = null) {
    if (!this.image) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = screenX ?? rect.width / 2;
    const y = screenY ?? rect.height / 2;
    const logical = this.toLogical(x, y);
    const next = Math.max(this.fitScale * 0.5, Math.min(this.fitScale * 80, this.scale * factor));
    this.scale = next;
    this.offsetX = x - logical.x * this.scale;
    this.offsetY = y - logical.y * this.scale;
    this.draw();
  }

  toLogical(screenX, screenY) {
    return {
      x: (screenX - this.offsetX) / this.scale,
      y: (screenY - this.offsetY) / this.scale,
    };
  }

  eventPosition(event) {
    const rect = this.canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  onWheel(event) {
    event.preventDefault();
    const position = this.eventPosition(event);
    this.zoom(Math.exp(-event.deltaY * 0.0015), position.x, position.y);
  }

  onPointerDown(event) {
    if (!this.image || event.button !== 0) return;
    this.canvas.setPointerCapture(event.pointerId);
    const position = this.eventPosition(event);
    this.drag = {
      pointerId: event.pointerId,
      x: position.x,
      y: position.y,
      offsetX: this.offsetX,
      offsetY: this.offsetY,
    };
    this.moved = false;
  }

  onPointerMove(event) {
    const position = this.eventPosition(event);
    if (this.image) {
      const logical = this.toLogical(position.x, position.y);
      this.coordinateElement.textContent = `x ${logical.x.toFixed(1)}, y ${logical.y.toFixed(1)}`;
    }
    if (!this.drag || this.drag.pointerId !== event.pointerId) return;
    const dx = position.x - this.drag.x;
    const dy = position.y - this.drag.y;
    if (Math.hypot(dx, dy) > 4) this.moved = true;
    if (this.moved) {
      this.offsetX = this.drag.offsetX + dx;
      this.offsetY = this.drag.offsetY + dy;
      this.canvas.classList.add("dragging");
      this.draw();
    }
  }

  onPointerUp(event) {
    if (!this.drag || this.drag.pointerId !== event.pointerId) return;
    const position = this.eventPosition(event);
    if (!this.moved && this.clickHandler) {
      const point = this.toLogical(position.x, position.y);
      if (point.x >= 0 && point.y >= 0 && point.x < this.logicalWidth && point.y < this.logicalHeight) {
        this.clickHandler(point);
      }
    }
    this.cancelPointer();
  }

  cancelPointer() {
    this.drag = null;
    this.moved = false;
    this.canvas.classList.remove("dragging");
  }

  setOverlays(markers, pendingMarker = null, polygon = null) {
    this.markers = markers;
    this.pendingMarker = pendingMarker;
    this.polygon = polygon;
    this.draw();
  }

  draw() {
    const ratio = window.devicePixelRatio || 1;
    const width = this.canvas.width / ratio;
    const height = this.canvas.height / ratio;
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.ctx.clearRect(0, 0, width, height);
    this.ctx.fillStyle = "#080b0f";
    this.ctx.fillRect(0, 0, width, height);
    if (!this.image) return;

    this.ctx.save();
    this.ctx.translate(this.offsetX, this.offsetY);
    this.ctx.scale(this.scale, this.scale);
    this.ctx.drawImage(this.image, 0, 0, this.logicalWidth, this.logicalHeight);
    this.drawDetailTiles(width, height);

    if (this.polygon?.length) {
      this.ctx.beginPath();
      this.polygon.forEach((point, index) => {
        if (index === 0) this.ctx.moveTo(point.x, point.y);
        else this.ctx.lineTo(point.x, point.y);
      });
      this.ctx.closePath();
      this.ctx.fillStyle = "rgba(93, 214, 176, .18)";
      this.ctx.fill();
      this.ctx.strokeStyle = "#5dd6b0";
      this.ctx.lineWidth = 3 / this.scale;
      this.ctx.stroke();
    }

    const drawMarker = (marker, pending = false) => {
      const radius = 9 / this.scale;
      this.ctx.beginPath();
      this.ctx.arc(marker.x, marker.y, radius, 0, Math.PI * 2);
      this.ctx.fillStyle = pending ? "#ffffff" : marker.color;
      this.ctx.fill();
      this.ctx.strokeStyle = "#071019";
      this.ctx.lineWidth = 3 / this.scale;
      this.ctx.stroke();
      this.ctx.fillStyle = "#071019";
      this.ctx.font = `bold ${11 / this.scale}px system-ui`;
      this.ctx.textAlign = "center";
      this.ctx.textBaseline = "middle";
      this.ctx.fillText(String(marker.label), marker.x, marker.y);
    };
    this.markers.forEach((marker) => drawMarker(marker));
    if (this.pendingMarker) drawMarker(this.pendingMarker, true);
    this.ctx.restore();
  }
}

const cameraView = new ImageViewport(
  document.querySelector("#camera-canvas"),
  elements.cameraCoordinates,
);
const mapView = new ImageViewport(
  document.querySelector("#map-canvas"),
  elements.mapCoordinates,
);

function setStatus(message, type = "") {
  elements.status.textContent = message;
  elements.status.className = `status-message ${type}`.trim();
}

function transformedPoint(matrix, x, y) {
  const denominator = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2];
  if (Math.abs(denominator) < 1e-12) return null;
  return {
    x: (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / denominator,
    y: (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / denominator,
  };
}

function cameraFootprint() {
  if (!state.calibration?.homography || !cameraView.image) return null;
  const w = cameraView.logicalWidth;
  const h = cameraView.logicalHeight;
  return [[0, 0], [w, 0], [w, h], [0, h]]
    .map(([x, y]) => transformedPoint(state.calibration.homography, x, y))
    .filter(Boolean);
}

function updateOverlays() {
  const cameraMarkers = state.points.map((pair, index) => ({
    x: pair.camera[0], y: pair.camera[1], label: index + 1, color: palette[index % palette.length],
  }));
  const mapMarkers = state.points.map((pair, index) => ({
    x: pair.map[0], y: pair.map[1], label: index + 1, color: palette[index % palette.length],
  }));
  const pending = state.pendingCameraPoint ? {
    x: state.pendingCameraPoint[0],
    y: state.pendingCameraPoint[1],
    label: state.points.length + 1,
  } : null;
  cameraView.setOverlays(cameraMarkers, pending, null);
  mapView.setOverlays(mapMarkers, null, cameraFootprint());
}

function updatePanel() {
  elements.pointCount.textContent = state.points.length;
  elements.pointTable.innerHTML = "";
  state.points.forEach((pair, index) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td style="color:${palette[index % palette.length]}">${index + 1}</td>
      <td>${pair.camera[0].toFixed(1)}, ${pair.camera[1].toFixed(1)}</td>
      <td>${pair.map[0].toFixed(1)}, ${pair.map[1].toFixed(1)}</td>
      <td><button class="point-delete" type="button" data-index="${index}" aria-label="Delete pair ${index + 1}">×</button></td>`;
    elements.pointTable.append(row);
  });
  elements.pointTable.querySelectorAll(".point-delete").forEach((button) => {
    button.addEventListener("click", () => removePoint(Number(button.dataset.index)));
  });

  const waiting = Boolean(state.pendingCameraPoint);
  elements.pairPrompt.textContent = waiting
    ? `Now select matching map point ${state.points.length + 1}.`
    : "Select a point in the camera frame.";
  elements.pairPrompt.classList.toggle("waiting", waiting);
  elements.undo.disabled = !waiting && state.points.length === 0;
  elements.clear.disabled = !waiting && state.points.length === 0;
  elements.save.disabled = state.points.length < 4 || waiting || !cameraView.image;

  const calibration = state.calibration;
  elements.inliers.textContent = calibration ? `${calibration.inlier_count}/${calibration.point_count}` : "—";
  elements.rmse.textContent = calibration ? `${calibration.rmse_reference_pixels.toFixed(2)} px` : "—";
  elements.maxError.textContent = calibration ? `${calibration.maximum_error_reference_pixels.toFixed(2)} px` : "—";
  elements.matrix.textContent = calibration?.homography
    ? calibration.homography.map((row) => row.map((value) => value.toExponential(7)).join("  ")).join("\n")
    : "Not computed";
  const calibrationSaved = Boolean(calibration && !state.dirty);
  const draftSaved = state.pointsSaved && state.points.length > 0;
  elements.savedBadge.hidden = !calibrationSaved && !draftSaved;
  elements.savedBadge.textContent = calibrationSaved ? "Calibration saved" : "Points saved";
  elements.delete.hidden = !state.payload?.calibrations?.[state.camera?.id];
  updateOverlays();
}

function markDirty() {
  state.dirty = true;
  state.calibration = null;
  state.pointsSaved = false;
  state.pointRevision = Math.max(Date.now(), state.pointRevision + 1);
  setStatus(state.points.length >= 4 ? "Ready to compute. Add more spread-out points for better accuracy." : "");
  updatePanel();
  queuePointSave();
}

function pointSavePayload(camera = state.camera, points = state.points, revision = state.pointRevision) {
  if (!camera || !cameraView.image) return null;
  return {
    camera_id: camera.id,
    source_image: camera.frame_name,
    source_width: cameraView.logicalWidth,
    source_height: cameraView.logicalHeight,
    points: points.map((pair) => ({ camera: [...pair.camera], map: [...pair.map] })),
    revision,
  };
}

function queuePointSave() {
  const cameraId = state.camera?.id;
  const revision = state.pointRevision;
  const payload = pointSavePayload();
  if (!payload) return pointSaveChain;

  pointSaveChain = pointSaveChain.catch(() => {}).then(async () => {
    const response = await fetch("/api/homography/points", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save point pairs");
    state.payload.point_sets[cameraId] = result;
    if (state.camera?.id === cameraId && state.pointRevision === revision) {
      state.pointsSaved = true;
      updatePanel();
    }
  }).catch((error) => {
    if (state.camera?.id === cameraId && state.pointRevision === revision) {
      state.pointsSaved = false;
      setStatus(`Point autosave failed: ${error.message}`, "error");
      updatePanel();
    }
  });
  return pointSaveChain;
}

function removePoint(index) {
  state.points.splice(index, 1);
  markDirty();
}

cameraView.clickHandler = (point) => {
  state.pendingCameraPoint = [point.x, point.y];
  setStatus("Camera point selected; click the identical ground point on the map.");
  updatePanel();
};

mapView.clickHandler = (point) => {
  if (!state.pendingCameraPoint) {
    setStatus("Select the point in the camera frame first.", "error");
    return;
  }
  state.points.push({ camera: state.pendingCameraPoint, map: [point.x, point.y] });
  state.pendingCameraPoint = null;
  markDirty();
};

async function loadSelectedCamera() {
  const cameraId = elements.cameraSelect.value;
  state.camera = state.payload.cameras.find((camera) => camera.id === cameraId);
  state.pendingCameraPoint = null;
  const savedCalibration = state.payload.calibrations[cameraId] || null;
  const savedPointSet = state.payload.point_sets?.[cameraId] || null;
  const savedPoints = savedPointSet?.points || savedCalibration?.points || [];
  state.points = savedPoints.map((pair) => ({ camera: [...pair.camera], map: [...pair.map] }));
  const calibrationMatchesPoints = savedCalibration
    && JSON.stringify(savedCalibration.points) === JSON.stringify(savedPoints);
  state.calibration = calibrationMatchesPoints ? savedCalibration : null;
  state.dirty = Boolean(savedCalibration && !calibrationMatchesPoints);
  state.pointsSaved = Boolean(savedPointSet || savedCalibration);
  state.pointRevision = Math.max(Date.now(), Number(savedPointSet?.revision || 0));
  elements.capture.disabled = !state.camera?.can_capture;
  const coordinateSpace = state.camera?.coordinate_space || "raw";
  elements.frameModeBadge.textContent = coordinateSpace === "undistorted" ? "Undistorted frame" : "Raw frame";
  elements.frameModeBadge.classList.toggle("raw", coordinateSpace !== "undistorted");
  elements.cameraEmpty.hidden = Boolean(state.camera?.frame_url);
  setStatus("");

  if (state.camera?.frame_url) {
    try {
      await cameraView.setImage(state.camera.frame_url);
      elements.cameraEmpty.hidden = true;
    } catch (error) {
      cameraView.image = null;
      elements.cameraEmpty.hidden = false;
      setStatus(error.message, "error");
    }
  } else {
    cameraView.image = null;
    cameraView.draw();
  }
  updatePanel();
}

async function saveCalibration() {
  if (state.points.length < 4 || !state.camera || !cameraView.image) return;
  elements.save.disabled = true;
  setStatus("Computing homography…");
  try {
    const response = await fetch("/api/homography", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        camera_id: state.camera.id,
        source_image: state.camera.frame_name,
        source_width: cameraView.logicalWidth,
        source_height: cameraView.logicalHeight,
        points: state.points,
        revision: state.pointRevision,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not save homography");
    state.calibration = result;
    state.payload.calibrations[state.camera.id] = result;
    state.payload.point_sets[state.camera.id] = {
      camera_id: state.camera.id,
      source_image: state.camera.frame_name,
      source_width: cameraView.logicalWidth,
      source_height: cameraView.logicalHeight,
      points: state.points.map((pair) => ({ camera: [...pair.camera], map: [...pair.map] })),
      revision: state.pointRevision,
      updated_at: result.updated_at,
    };
    state.dirty = false;
    state.pointsSaved = true;
    setStatus(`Saved ${result.inlier_count}/${result.point_count} inliers with ${result.rmse_reference_pixels.toFixed(2)} px map RMSE.`, "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
  updatePanel();
}

async function captureFrame() {
  if (!state.camera) return;
  const previousWidth = cameraView.image ? cameraView.logicalWidth : null;
  const previousHeight = cameraView.image ? cameraView.logicalHeight : null;
  const previousCoordinateSpace = state.camera.coordinate_space;
  elements.capture.disabled = true;
  setStatus("Capturing a full-resolution frame…");
  try {
    const response = await fetch("/api/homography/snapshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site: state.camera.site, channel: state.camera.channel }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not capture frame");
    state.camera.frame_url = result.frame_url;
    state.camera.frame_name = result.frame_name;
    state.camera.coordinate_space = result.coordinate_space;
    state.pendingCameraPoint = null;
    await cameraView.setImage(result.frame_url);
    elements.cameraEmpty.hidden = true;
    elements.frameModeBadge.textContent = result.coordinate_space === "undistorted" ? "Undistorted frame" : "Raw frame";
    elements.frameModeBadge.classList.toggle("raw", result.coordinate_space !== "undistorted");
    const geometryChanged = previousWidth !== null && (
      previousWidth !== cameraView.logicalWidth
      || previousHeight !== cameraView.logicalHeight
      || previousCoordinateSpace !== result.coordinate_space
    );
    if (geometryChanged) {
      state.calibration = null;
      state.dirty = true;
      state.pointsSaved = false;
      state.pointRevision = Math.max(Date.now(), state.pointRevision + 1);
      queuePointSave();
    }
    const preserved = state.points.length === 1 ? "1 saved point pair" : `${state.points.length} saved point pairs`;
    setStatus(`Captured ${result.width}×${result.height} frame; preserved ${preserved}.`, "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
  elements.capture.disabled = !state.camera.can_capture;
  updatePanel();
}

async function deleteSavedCalibration() {
  if (!state.camera || !confirm(`Delete the saved homography for ${state.camera.label}?`)) return;
  try {
    const response = await fetch(`/api/homography/${encodeURIComponent(state.camera.id)}`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not delete calibration");
    delete state.payload.calibrations[state.camera.id];
    state.pendingCameraPoint = null;
    state.calibration = null;
    state.dirty = state.points.length > 0;
    setStatus("Saved calibration deleted; point pairs were kept.", "success");
    updatePanel();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

elements.cameraSelect.addEventListener("change", loadSelectedCamera);
elements.capture.addEventListener("click", captureFrame);
elements.save.addEventListener("click", saveCalibration);
elements.delete.addEventListener("click", deleteSavedCalibration);
elements.undo.addEventListener("click", () => {
  if (state.pendingCameraPoint) {
    state.pendingCameraPoint = null;
    setStatus("");
    updatePanel();
  } else {
    state.points.pop();
    markDirty();
  }
});
elements.clear.addEventListener("click", () => {
  const hadPoints = state.points.length > 0;
  state.points = [];
  state.pendingCameraPoint = null;
  if (hadPoints) markDirty();
  else updatePanel();
});

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => {
    const viewport = button.dataset.view === "camera" ? cameraView : mapView;
    if (button.dataset.action === "fit") viewport.fit();
    else viewport.zoom(button.dataset.action === "in" ? 1.5 : 1 / 1.5);
  });
});

async function initialize() {
  setStatus("Loading cameras and reference metadata…");
  try {
    const response = await fetch("/api/homography");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Could not load homography data");
    state.payload = payload;
    state.payload.point_sets ||= {};
    elements.referenceName.textContent = payload.reference.name;
    mapView.setTileSource({
      urlTemplate: payload.reference.tile_url_template,
      tileSize: payload.reference.tile_size,
      minZoom: payload.reference.tile_min_zoom,
      maxZoom: payload.reference.tile_max_zoom,
      activationScale: payload.reference.tile_activation_scale,
    });
    elements.cameraSelect.innerHTML = payload.cameras.map((camera) => {
      const suffix = camera.frame_url ? "" : " (no saved frame)";
      return `<option value="${camera.id}">${camera.label}${suffix}</option>`;
    }).join("");
    await Promise.all([
      mapView.setImage(
        payload.reference.preview_url,
        payload.reference.width,
        payload.reference.height,
      ).then(() => { elements.mapLoading.hidden = true; }),
      loadSelectedCamera(),
    ]);
    setStatus("");
  } catch (error) {
    setStatus(error.message, "error");
    elements.mapLoading.textContent = error.message;
  }
}

initialize();

window.addEventListener("pagehide", () => {
  const payload = pointSavePayload();
  if (!payload) return;
  navigator.sendBeacon(
    "/api/homography/points",
    new Blob([JSON.stringify(payload)], { type: "application/json" }),
  );
});
