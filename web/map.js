const campus = [38.89935, -77.04935];
const streets = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
  { attribution: "Tiles &copy; Esri", maxZoom: 20 }
);
const satellite = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { attribution: "Tiles &copy; Esri", maxZoom: 20 }
);

const map = L.map("map", { zoomControl: false, layers: [streets] }).setView(campus, 16);
L.control.zoom({ position: "bottomright" }).addTo(map);

map.createPane("shade");
map.getPane("shade").style.zIndex = 350;
map.getPane("shade").style.pointerEvents = "none";
map.createPane("coverage");
map.getPane("coverage").style.zIndex = 360;
map.getPane("coverage").style.pointerEvents = "none";

const buildingLayer = L.layerGroup().addTo(map);
const cameraLayer = L.layerGroup().addTo(map);
const shadeLayer = L.layerGroup().addTo(map);
const coverageLayer = L.layerGroup().addTo(map);

let sites = [];
let areas = [];
let selectedId = null;
let selectedCamera = null;
let access = { unlocked: false };

const list = document.getElementById("site-list");
const backBtn = document.getElementById("back-btn");
const streamPanel = document.getElementById("stream-panel");
const streamImage = document.getElementById("stream-image");
const streamStatus = document.getElementById("stream-status");
const accessMessage = document.getElementById("access-message");
const accessForm = document.getElementById("access-form");
const accessError = document.getElementById("access-error");
const refreshButton = document.getElementById("refresh-btn");
let warningDismissed = false;

const labelSide = {
  shenkman: "bottom",
  fulbright: "left",
  himmelfarb: "right",
  jbko: "top",
  gelman: "right",
  duques: "bottom",
  police: "bottom",
  mfa: "top",
};

function buildingLabel(site) {
  const side = labelSide[site.id] || "top";
  return L.divIcon({
    className: "building-label-wrap",
    iconSize: [0, 0],
    iconAnchor: [0, 0],
    html: `<div class="building-anchor"><span class="building-label ${side}"><i style="background:${site.color}"></i>${site.name}</span></div>`,
  });
}

function cameraPin(channel, offline) {
  return L.divIcon({
    className: "",
    iconSize: [34, 34],
    iconAnchor: [17, 30],
    html: `<div class="camera-pin${offline ? " offline" : ""}"><span>${channel}</span></div>`,
  });
}

function renderList() {
  list.innerHTML = "";
  sites.forEach((site) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `site-btn${site.id === selectedId ? " active" : ""}`;
    button.innerHTML = `<div><strong><i class="swatch" style="background:${site.color}"></i>${site.name}</strong><span>${site.working} of ${site.total} cameras live</span></div><div class="badge${site.working ? "" : " empty"}">${site.working}/${site.total}</div>`;
    button.addEventListener("click", () => selectBuilding(site.id));
    list.appendChild(button);
  });
}

function drawBuildings() {
  buildingLayer.clearLayers();
  sites.forEach((site) => {
    const marker = L.marker([site.lat, site.lng], {
      icon: buildingLabel(site),
      zIndexOffset: 400,
    });
    marker.on("click", () => selectBuilding(site.id));
    marker.addTo(buildingLayer);
  });
}

function drawCoverage() {
  shadeLayer.clearLayers();
  coverageLayer.clearLayers();
  const holes = areas
    .filter((area) => area.ring && area.ring.length >= 3)
    .map((area) => area.ring.slice().reverse());
  const world = [[85, -180], [85, 180], [-85, 180], [-85, -180]];
  L.polygon([world, ...holes], {
    pane: "shade",
    stroke: false,
    fillColor: "#1c2430",
    fillOpacity: 0.42,
    interactive: false,
  }).addTo(shadeLayer);

  areas.forEach((area) => {
    const site = sites.find((item) => item.id === area.site);
    if (!site || !area.ring || area.ring.length < 3) return;
    L.polygon(area.ring, {
      pane: "coverage",
      color: site.color,
      weight: 2,
      fillColor: site.color,
      fillOpacity: 0.34,
      interactive: false,
    }).addTo(coverageLayer);
  });
}

function selectBuilding(id) {
  const site = sites.find((item) => item.id === id);
  if (!site) return;
  selectedId = id;
  buildingLayer.clearLayers();
  cameraLayer.clearLayers();
  renderList();
  map.flyTo([site.lat, site.lng], 19, { duration: 0.8 });
  drawCameraMarkers(site);
}

function drawCameraMarkers(site) {
  cameraLayer.clearLayers();
  site.cameras.forEach((camera) => {
    const marker = L.marker([camera.lat, camera.lng], {
      icon: cameraPin(camera.channel, !camera.working),
    });
    marker.bindTooltip(`${site.name} · camera ${camera.channel}`, {
      direction: "top",
      offset: [0, -24],
    });
    marker.on("click", () => openStream(site, camera));
    marker.addTo(cameraLayer);
  });
}

function openStream(site, camera) {
  if (!access.unlocked) {
    showAccessMessage();
    return;
  }
  selectedCamera = `${site.id}-${camera.channel}`;
  streamPanel.hidden = false;
  document.getElementById("stream-site").textContent = site.name;
  document.getElementById("stream-title").textContent = `Camera ${camera.channel}`;
  const viewer = document.querySelector(".viewer");
  viewer.style.backgroundImage = camera.still ? `url("${camera.still}")` : "none";
  viewer.style.backgroundSize = "contain";
  viewer.style.backgroundRepeat = "no-repeat";
  viewer.style.backgroundPosition = "center";
  if (!camera.stream) {
    streamStatus.textContent = "No stream address for this building yet";
    streamImage.removeAttribute("src");
    return;
  }
  streamStatus.textContent = camera.working ? "Connecting…" : "Last check failed. Trying again…";
  streamImage.onload = () => {
    streamStatus.textContent = "Live";
  };
  streamImage.onerror = () => {
    streamStatus.textContent = "Could not open this stream";
  };
  streamImage.src = `/stream/${site.id}/${camera.channel}?t=${Date.now()}`;
}

function closeStream() {
  streamPanel.hidden = true;
  streamImage.onload = null;
  streamImage.onerror = null;
  streamImage.src = "";
  selectedCamera = null;
}

function showCampus() {
  selectedId = null;
  cameraLayer.clearLayers();
  drawBuildings();
  renderList();
  map.flyTo(campus, 16, { duration: 0.7 });
}

document.getElementById("refresh-btn").addEventListener("click", async () => {
  const button = document.getElementById("refresh-btn");
  button.disabled = true;
  button.textContent = "Checking…";
  try {
    const response = await fetch("/api/refresh", { method: "POST" });
    if (!response.ok) throw new Error("refresh failed");
    sites = await response.json();
    renderList();
    if (selectedId) {
      const site = sites.find((item) => item.id === selectedId);
      if (site) drawCameraMarkers(site);
    }
    button.textContent = "Refresh cameras";
  } catch (error) {
    button.textContent = "Try again";
  }
  button.disabled = false;
});

function showAccessMessage() {
  if (!warningDismissed) accessMessage.hidden = false;
  accessForm.hidden = access.unlocked;
}

function applyAccess(nextAccess) {
  access = nextAccess;
  refreshButton.disabled = !access.unlocked;
  showAccessMessage();
}

accessForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  accessError.hidden = true;
  const password = document.getElementById("camera-password").value;
  try {
    const response = await fetch("/api/access", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not unlock camera access");
    document.getElementById("camera-password").value = "";
    applyAccess(data);
    await loadCameraData();
  } catch (error) {
    accessError.textContent = error.message;
    accessError.hidden = false;
  }
});

document.getElementById("access-message-close").addEventListener("click", () => {
  warningDismissed = true;
  accessMessage.hidden = true;
});

const cameraWall = document.getElementById("camera-wall");
const wallGrid = document.getElementById("wall-grid");
const allCamerasBtn = document.getElementById("all-cameras-btn");

function closeCameraWall() {
  wallGrid.querySelectorAll("img").forEach((image) => {
    image.src = "";
  });
  wallGrid.innerHTML = "";
  cameraWall.hidden = true;
  allCamerasBtn.classList.remove("active");
}

function setWallStatus(live, total) {
  document.getElementById("wall-status").textContent = `${live} out of ${total} LIVE`;
}

function openCameraWall() {
  if (!access.unlocked) {
    showAccessMessage();
    return;
  }
  wallGrid.innerHTML = "";
  const tiles = [];
  sites.forEach((site) => {
    site.cameras.filter((camera) => camera.stream).forEach((camera) => {
      tiles.push({ site, camera });
    });
  });
  setWallStatus(0, tiles.length);
  tiles.forEach(({ site, camera }) => {
    const tile = document.createElement("figure");
    tile.className = "camera-tile";
    const image = document.createElement("img");
    image.alt = `${site.name} camera ${camera.channel}`;
    image.addEventListener("load", () => {
      if (image.dataset.counted === "1") return;
      image.dataset.counted = "1";
      const live = wallGrid.querySelectorAll("img[data-counted='1']").length;
      setWallStatus(live, tiles.length);
    });
    image.src = `/stream/${site.id}/${camera.channel}?view=wall&t=${Date.now()}`;
    const caption = document.createElement("p");
    caption.textContent = `${site.name} · camera ${camera.channel}`;
    tile.append(image, caption);
    tile.addEventListener("click", () => openStream(site, camera));
    wallGrid.appendChild(tile);
  });
  if (!tiles.length) {
    wallGrid.textContent = "No cameras with a stream address yet.";
  }
  cameraWall.hidden = false;
  allCamerasBtn.classList.add("active");
}

allCamerasBtn.addEventListener("click", () => {
  if (cameraWall.hidden) openCameraWall();
  else closeCameraWall();
});
document.getElementById("wall-close").addEventListener("click", closeCameraWall);

document.getElementById("stream-close").addEventListener("click", closeStream);
backBtn.addEventListener("click", showCampus);

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

async function loadCameraData() {
  const [siteResponse, coverageResponse] = await Promise.all([
    fetch("/api/sites"),
    fetch("/api/coverage"),
  ]);
  if (!siteResponse.ok || !coverageResponse.ok) throw new Error("Could not load camera locations");
  const [siteData, coverage] = await Promise.all([siteResponse.json(), coverageResponse.json()]);
  sites = siteData;
  areas = coverage.areas || [];
  renderList();
  drawBuildings();
  drawCoverage();
}

Promise.all([
  fetch("/api/access").then((response) => response.json()),
  loadCameraData(),
])
  .then(([accessData]) => {
    applyAccess(accessData);
  })
  .catch(() => {
    list.textContent = "Could not load camera locations.";
  });
