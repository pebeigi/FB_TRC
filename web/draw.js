const campus = [38.89935, -77.04935];
const streets = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
  { attribution: "Tiles &copy; Esri", maxZoom: 20 }
);
const satellite = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { attribution: "Tiles &copy; Esri", maxZoom: 20 }
);

const map = L.map("map", { zoomControl: false, layers: [satellite] }).setView(campus, 17);
map.doubleClickZoom.disable();
L.control.zoom({ position: "bottomright" }).addTo(map);

const savedLayer = L.layerGroup().addTo(map);
const draftLayer = L.layerGroup().addTo(map);
const pinLayer = L.layerGroup().addTo(map);

let sites = [];
let areas = [];
let draft = [];
let cursor = null;
let placing = null;
let closed = false;
let suppressClick = false;

const siteSelect = document.getElementById("site");
const channelSelect = document.getElementById("channel");
const cameraChecks = document.getElementById("camera-checks");
const pointCount = document.getElementById("point-count");
const message = document.getElementById("draw-message");
const savedList = document.getElementById("saved-list");

function currentKey() {
  const channel = channelSelect.value ? Number(channelSelect.value) : null;
  return `${siteSelect.value}-${channel === null ? "building" : channel}`;
}

function setMessage(text) {
  message.textContent = text;
}

function snapToAxis(prev, lat, lng) {
  const latMeters = Math.abs(lat - prev[0]) * 111320;
  const lngMeters = Math.abs(lng - prev[1]) * 111320 * Math.cos((prev[0] * Math.PI) / 180);
  if (lngMeters >= latMeters) return [prev[0], lng];
  return [lat, prev[1]];
}

function samePoint(a, b) {
  return Math.abs(a[0] - b[0]) < 1e-7 && Math.abs(a[1] - b[1]) < 1e-7;
}

function orthogonalRing(points) {
  if (points.length < 3) return points.map((point) => [point[0], point[1]]);
  const ring = points.map((point) => [point[0], point[1]]);
  const first = ring[0];
  const last = ring[ring.length - 1];
  const prev = ring[ring.length - 2];
  const sharesLat = Math.abs(last[0] - first[0]) < 1e-7;
  const sharesLng = Math.abs(last[1] - first[1]) < 1e-7;
  if (sharesLat || sharesLng) return ring;
  const lastHorizontal = Math.abs(last[0] - prev[0]) < 1e-7;
  const corner = lastHorizontal ? [first[0], last[1]] : [last[0], first[1]];
  if (!samePoint(corner, last) && !samePoint(corner, first)) ring.push(corner);
  return ring;
}

function nearFirstCorner(latlng) {
  if (!draft.length) return false;
  const first = map.latLngToContainerPoint(draft[0]);
  const click = map.latLngToContainerPoint(latlng);
  return first.distanceTo(click) < 16;
}

function closeShape() {
  draft = orthogonalRing(draft);
  closed = true;
  cursor = null;
  renderDraft();
  setMessage("Drag the shape to move it. Drag an edge to slide it, then save.");
}

function watchDrag(startEvent, onMove) {
  L.DomEvent.stop(startEvent.originalEvent);
  map.dragging.disable();
  suppressClick = true;
  function move(event) {
    onMove(event);
    renderDraft();
  }
  function stop() {
    map.dragging.enable();
    map.off("mousemove", move);
    map.off("mouseup", stop);
  }
  map.on("mousemove", move);
  map.on("mouseup", stop);
}

function renderDraft() {
  draftLayer.clearLayers();
  if (!draft.length) {
    pointCount.textContent = "No points yet. Click the map to start.";
    return;
  }
  if (closed) {
    pointCount.textContent = "Drag the shape to move it, or drag an edge to slide it.";
  } else if (draft.length >= 3) {
    pointCount.textContent = "Click the first corner to finish the shape.";
  } else {
    pointCount.textContent = `${draft.length} point${draft.length === 1 ? "" : "s"}. Click to add another corner.`;
  }
  const site = sites.find((item) => item.id === siteSelect.value);
  const color = site ? site.color : "#1f6b45";
  const shape = closed ? draft : orthogonalRing(draft);
  if (shape.length >= 3) {
    const polygon = L.polygon(shape, {
      color,
      weight: 2,
      fillColor: color,
      fillOpacity: 0.28,
      className: closed ? "draft-shape" : "",
    }).addTo(draftLayer);
    if (closed) {
      polygon.on("mousedown", (event) => {
        const start = event.latlng;
        const origin = draft.map((point) => [point[0], point[1]]);
        watchDrag(event, (moveEvent) => {
          const dLat = moveEvent.latlng.lat - start.lat;
          const dLng = moveEvent.latlng.lng - start.lng;
          draft = origin.map((point) => [point[0] + dLat, point[1] + dLng]);
        });
      });
    }
  } else if (draft.length >= 2) {
    L.polyline(draft, { color, weight: 2 }).addTo(draftLayer);
  }
  if (!closed && cursor) {
    L.polyline([draft[draft.length - 1], cursor], {
      color,
      weight: 2,
      dashArray: "6 6",
    }).addTo(draftLayer);
  }
  if (!closed) {
    draft.forEach((point, index) => {
      const marker = L.circleMarker(point, {
        radius: index === 0 && draft.length >= 3 ? 8 : 5,
        color: "#172033",
        weight: 2,
        fillColor: index === 0 && draft.length >= 3 ? color : "#ffffff",
        fillOpacity: 1,
      }).addTo(draftLayer);
      if (index === 0 && draft.length >= 3) {
        marker.on("click", (event) => {
          L.DomEvent.stop(event.originalEvent);
          closeShape();
        });
      }
    });
    return;
  }
  shape.forEach((point, index) => {
    const next = shape[(index + 1) % shape.length];
    const horizontal = Math.abs(point[0] - next[0]) <= Math.abs(point[1] - next[1]);
    const midpoint = [(point[0] + next[0]) / 2, (point[1] + next[1]) / 2];
    const handle = L.circleMarker(midpoint, {
      radius: 7,
      color: "#172033",
      weight: 2,
      fillColor: "#ffffff",
      fillOpacity: 1,
      className: horizontal ? "edge-handle horizontal" : "edge-handle vertical",
    }).addTo(draftLayer);
    handle.on("mousedown", (event) => {
      watchDrag(event, (moveEvent) => {
        if (horizontal) {
          draft[index][0] = moveEvent.latlng.lat;
          draft[(index + 1) % draft.length][0] = moveEvent.latlng.lat;
        } else {
          draft[index][1] = moveEvent.latlng.lng;
          draft[(index + 1) % draft.length][1] = moveEvent.latlng.lng;
        }
      });
    });
  });
}

function renderSaved() {
  savedLayer.clearLayers();
  savedList.innerHTML = "";
  areas.forEach((area) => {
    const site = sites.find((item) => item.id === area.site);
    if (!site) return;
    L.polygon(area.ring, {
      color: site.color,
      weight: 2,
      fillColor: site.color,
      fillOpacity: 0.18,
    }).addTo(savedLayer);

    const row = document.createElement("button");
    row.type = "button";
    row.className = "site-btn";
    const label = area.channel ? `Camera ${area.channel}` : "Whole building";
    row.innerHTML = `<div><strong><i class="swatch" style="background:${site.color}"></i>${site.name}</strong><span>${label}</span></div>`;
    row.addEventListener("click", () => {
      siteSelect.value = area.site;
      channelSelect.value = area.channel ? String(area.channel) : "";
      draft = area.ring.map((point) => [point[0], point[1]]);
      closed = true;
      cursor = null;
      map.fitBounds(L.polygon(area.ring).getBounds(), { padding: [40, 40] });
      renderDraft();
      setMessage("Loaded this outline. Save replaces it.");
    });
    const remove = document.createElement("span");
    remove.textContent = "Delete";
    remove.className = "badge empty";
    remove.addEventListener("click", async (event) => {
      event.stopPropagation();
      const response = await fetch(`/api/coverage/${area.id}`, { method: "DELETE" });
      if (!response.ok) {
        setMessage("Could not delete that area.");
        return;
      }
      const data = await response.json();
      areas = data.areas || [];
      if (currentKey() === area.id) {
        draft = [];
        closed = false;
      }
      renderSaved();
      renderDraft();
      setMessage("Deleted.");
    });
    row.appendChild(remove);
    savedList.appendChild(row);
  });
}

function currentSite() {
  return sites.find((item) => item.id === siteSelect.value);
}

function renderPins() {
  pinLayer.clearLayers();
  const site = currentSite();
  if (!site) return;
  site.cameras.forEach((camera) => {
    L.circleMarker([camera.lat, camera.lng], {
      radius: 8,
      color: site.color,
      weight: 2,
      fillColor: camera.placed ? site.color : "#ffffff",
      fillOpacity: 1,
    })
      .bindTooltip(
        camera.placed
          ? `${site.name} camera ${camera.channel}`
          : `Camera ${camera.channel} is not placed yet`,
        { direction: "top" }
      )
      .addTo(pinLayer);
  });
}

async function persistCameras(cameras) {
  const response = await fetch("/api/cameras", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ site: siteSelect.value, cameras }),
  });
  const data = await response.json();
  if (!response.ok) {
    setMessage(data.error || "Could not save cameras.");
    return false;
  }
  sites = data.sites;
  syncCameraControls();
  return true;
}

function syncCameraControls() {
  const site = currentSite();
  if (!site) return;
  const enabled = new Set(site.cameras.map((camera) => camera.channel));
  const previous = channelSelect.value;
  cameraChecks.innerHTML = "";
  channelSelect.innerHTML = '<option value="">Whole building</option>';
  for (let channel = 1; channel <= 4; channel += 1) {
    const label = document.createElement("label");
    label.className = "check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = String(channel);
    input.checked = enabled.has(channel);
    input.addEventListener("change", () => {
      const siteNow = currentSite();
      const selected = [...cameraChecks.querySelectorAll("input:checked")].map((box) => Number(box.value));
      persistCameras(selected.map((number) => {
        const existing = siteNow.cameras.find((camera) => camera.channel === number);
        return {
          channel: number,
          lat: existing && existing.placed ? existing.lat : null,
          lng: existing && existing.placed ? existing.lng : null,
        };
      }));
    });
    label.append(input, document.createTextNode(String(channel)));
    cameraChecks.appendChild(label);
    if (!enabled.has(channel)) continue;
    const option = document.createElement("option");
    option.value = String(channel);
    option.textContent = `Camera ${channel}`;
    channelSelect.appendChild(option);
  }
  if ([...channelSelect.options].some((option) => option.value === previous)) {
    channelSelect.value = previous;
  }
  renderPins();
}

function fillSites() {
  siteSelect.innerHTML = "";
  sites.forEach((site) => {
    const option = document.createElement("option");
    option.value = site.id;
    option.textContent = site.name;
    siteSelect.appendChild(option);
  });
}

map.on("mousemove", (event) => {
  if (placing || closed || !draft.length) return;
  cursor = snapToAxis(draft[draft.length - 1], event.latlng.lat, event.latlng.lng);
  renderDraft();
});

document.getElementById("place-camera").addEventListener("click", () => {
  if (!channelSelect.value) {
    setMessage("Check a camera, choose it above, then place it.");
    return;
  }
  placing = Number(channelSelect.value);
  setMessage(`Click the map to place camera ${placing}.`);
});

map.on("click", async (event) => {
  if (suppressClick) {
    suppressClick = false;
    return;
  }
  if (closed) return;
  if (placing) {
    const site = currentSite();
    const placedChannel = placing;
    const cameras = site.cameras.map((camera) => ({
      channel: camera.channel,
      lat: camera.channel === placedChannel ? event.latlng.lat : (camera.placed ? camera.lat : null),
      lng: camera.channel === placedChannel ? event.latlng.lng : (camera.placed ? camera.lng : null),
    }));
    placing = null;
    const saved = await persistCameras(cameras);
    if (saved) setMessage(`Saved the location of camera ${placedChannel}.`);
    return;
  }
  if (draft.length >= 3 && nearFirstCorner(event.latlng)) {
    closeShape();
    return;
  }
  let point = [event.latlng.lat, event.latlng.lng];
  if (draft.length) {
    point = snapToAxis(draft[draft.length - 1], point[0], point[1]);
    const prev = draft[draft.length - 1];
    const latMeters = Math.abs(point[0] - prev[0]) * 111320;
    const lngMeters = Math.abs(point[1] - prev[1]) * 111320 * Math.cos((prev[0] * Math.PI) / 180);
    if (latMeters + lngMeters < 3) return;
  }
  draft.push(point);
  cursor = null;
  renderDraft();
  setMessage("");
});

document.getElementById("undo").addEventListener("click", () => {
  closed = false;
  draft.pop();
  cursor = null;
  renderDraft();
});

document.getElementById("clear").addEventListener("click", () => {
  draft = [];
  cursor = null;
  closed = false;
  renderDraft();
  setMessage("");
});

document.getElementById("save").addEventListener("click", async () => {
  if (draft.length < 3) {
    setMessage("Add at least 3 corners before saving.");
    return;
  }
  const channel = channelSelect.value ? Number(channelSelect.value) : null;
  const response = await fetch("/api/coverage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      site: siteSelect.value,
      channel,
      ring: closed ? draft : orthogonalRing(draft),
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    setMessage(data.error || "Could not save.");
    return;
  }
  areas = data.areas || [];
  renderSaved();
  setMessage("Saved. It is on the main map now.");
});

siteSelect.addEventListener("change", () => {
  placing = null;
  syncCameraControls();
  const existing = areas.find((area) => area.id === currentKey());
  draft = existing ? existing.ring.map((point) => [point[0], point[1]]) : [];
  closed = draft.length >= 3;
  cursor = null;
  renderDraft();
  setMessage("");
});
channelSelect.addEventListener("change", () => siteSelect.dispatchEvent(new Event("change")));

document.getElementById("streets-btn").addEventListener("click", () => {
  map.removeLayer(satellite);
  streets.addTo(map);
  document.getElementById("streets-btn").classList.add("active");
  document.getElementById("satellite-btn").classList.remove("active");
});
document.getElementById("satellite-btn").addEventListener("click", () => {
  map.removeLayer(streets);
  satellite.addTo(map);
  document.getElementById("satellite-btn").classList.add("active");
  document.getElementById("streets-btn").classList.remove("active");
});

Promise.all([
  fetch("/api/sites").then((response) => response.json()),
  fetch("/api/coverage").then((response) => response.json()),
])
  .then(([siteData, coverage]) => {
    sites = siteData;
    areas = coverage.areas || [];
    fillSites();
    syncCameraControls();
    renderSaved();
    renderDraft();
  })
  .catch(() => {
    setMessage("Could not load buildings.");
  });
