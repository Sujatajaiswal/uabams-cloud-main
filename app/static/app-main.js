window.closeFullscreenMap = function() {
  console.log('Close map clicked');
  document.body.classList.remove('fullscreen-map-mode');
  document.querySelectorAll('.map-card').forEach(card => card.classList.remove('hidden'));
  const btn = document.getElementById('showAllMapsBtn');
  if (btn) btn.style.display = 'none';
  selectTab(window.lastActiveTabBeforeMap || 'alerts');
  window.scrollTo({ top: 0 });
  setTimeout(() => { Object.values(maps).forEach(m => m?.invalidateSize()); }, 200);
};
const state = { dashboard: null, rmsPoints: [], mapAlerts: [], selectedGateway: '', selectedDateFilter: null };
const defaultGatewayIds = ['GW_UABAMS_BOGIE_01', 'GW_UABAMS_BOGIE_02'];
const gatewayIds = defaultGatewayIds;
let dashboardGatewayIds = [...defaultGatewayIds];
const maps = {};
const layers = {};
const trainMarkers = {};
let autoRefreshTimer = null;
let lastLoadedTrainNo = "";
const recentTrainStorageKey = 'uabams_recent_train_numbers';
let chartXInstance = null;
let chartYInstance = null;
let chartZInstance = null;

const $ = (id) => document.getElementById(id);

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function setHtml(id, value) {
  const el = $(id);
  if (el) el.innerHTML = value;
}

function setClass(id, value) {
  const el = $(id);
  if (el) el.className = value;
}

function setStatus(text, mode = '') {
  setText('apiStatus', text);
  setClass('apiStatus', `status-pill ${mode}`.trim());
}

async function logClientEvent(action, details = {}) {
  try {
    await fetch('/api/v1/logs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        page: location.pathname + location.hash,
        action,
        message: details.message || null,
        errorMessage: details.errorMessage || null,
        latitude: details.latitude ?? null,
        longitude: details.longitude ?? null,
      }),
    });
  } catch {
    // Logging must never break the dashboard.
  }
}

function logBrowserLocation(action) {
  if (!navigator.geolocation) {
    logClientEvent(action);
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (position) => logClientEvent(action, { latitude: position.coords.latitude, longitude: position.coords.longitude }),
    () => logClientEvent(action),
    { maximumAge: 300000, timeout: 3000 }
  );
}

function selectedGatewayValue() {
  return $('dashboardGateway')?.value || '';
}

function visibleGatewayIds() {
  const selected = selectedGatewayValue();
  return selected ? [selected] : dashboardGatewayIds;
}

function gatewayLabel(gatewayId) {
  const index = dashboardGatewayIds.indexOf(gatewayId);
  if (index >= 0) return `GW${index + 1}`;
  const fallbackIndex = defaultGatewayIds.indexOf(gatewayId);
  return fallbackIndex >= 0 ? `GW${fallbackIndex + 1}` : gatewayId;
}

function trainNoValue() {
  let rawVal = $('trainNo')?.value.trim() || '';
  if (rawVal.includes(' - ')) {
    rawVal = rawVal.split(' - ')[0].trim();
  }
  if (/^\d{3}$/.test(rawVal)) {
    return 'TR_' + rawVal;
  }
  return rawVal;
}


function recentTrainNumbers() {
  try {
    const values = JSON.parse(localStorage.getItem(recentTrainStorageKey) || '[]');
    if (!Array.isArray(values)) return [];
    return values.map(val => {
      let no = '';
      if (typeof val === 'object' && val !== null) {
        no = val.trainNo || '';
      } else {
        no = String(val || '');
      }
      if (no.startsWith('TR_')) {
        no = no.replace('TR_', '');
      }
      return no;
    })
    .filter(val => val && val !== 'object Object' && val !== '[object Object]');
  } catch {
    return [];
  }
}

function applyDatalistFix(input) {
  if (!input || input.dataset.hasDatalistFix) return;
  input.dataset.hasDatalistFix = 'true';
  let tempVal = '';
  
  const onFocus = function() {
    tempVal = this.value;
    this.value = '';
  };
  
  const onBlur = function() {
    setTimeout(() => {
      if (this.value === '') {
        this.value = tempVal;
      }
    }, 200);
  };
  
  input.addEventListener('focus', onFocus);
  input.addEventListener('click', onFocus);
  input.addEventListener('blur', onBlur);
}

function renderRecentTrainNumbers() {
  const list = $('recentTrainNos');
  const input = $('trainNo');

  if (!list) return;

  document.querySelectorAll('input[list="recentTrainNos"]').forEach(applyDatalistFix);

  const localTrainNos = recentTrainNumbers();
  
  const renderList = (trainObjects) => {
    list.innerHTML = trainObjects
      .map((t) => {
        const no = (typeof t === 'object' && t !== null) ? (t.trainNo || '') : String(t);
        const name = (typeof t === 'object' && t !== null) ? (t.trainName || '') : '';
        
        if (no === '[object Object]' || no === 'object Object' || !no) return '';
        
        const label = name ? `${no} - ${name}` : no;
        return `<option value="${escapeHtml(label)}"></option>`;
      })
      .join('');
  };

  if (!input.value) {
    if (localTrainNos.length > 0) {
      input.value = localTrainNos[0];
    } else {
      input.value = '019456';
    }
  }

  fetch('/api/v1/trains')
    .then((res) => res.json())
    .then((serverTrains) => {
      if (Array.isArray(serverTrains)) {
        const standardizedServerTrains = serverTrains.map(t => {
          if (typeof t === 'object' && t !== null) {
            return t;
          }
          const no = String(t);
          let name = 'Express Train';
          if (no === '019456') {
            name = 'Gatimaan Express';
          } else if (no.startsWith('TR_')) {
            try {
              const num = parseInt(no.split('_')[1], 10);
              const names_pool = [
                "Vinay Express", "Rajdhani Express", "Shatabdi Express", "Duronto Express", 
                "Garib Rath", "HumSafar Express", "Vande Bharat Express", 
                "Tejas Express", "Jan Shatabdi", "Sampark Kranti", "Superfast Mail"
              ];
              name = names_pool[num % names_pool.length];
            } catch (e) {}
          }
          return { trainNo: no, trainName: name };
        });

        const map = new Map();
        localTrainNos.forEach(no => map.set(no, { trainNo: no, trainName: '' }));
        standardizedServerTrains.forEach(t => map.set(t.trainNo, t));
        const combined = Array.from(map.values());
        
        combined.forEach(item => {
          if (!item.trainName) {
            const serverMatch = standardizedServerTrains.find(s => s.trainNo === item.trainNo);
            if (serverMatch) item.trainName = serverMatch.trainName;
          }
        });
        
        renderList(combined);
      }
    })
    .catch((err) => console.error('Failed to load train list:', err));
}

function rememberTrainNumber(trainNo) {
  const cleanTrainNo = String(trainNo || '').trim();
  if (!cleanTrainNo) return;

  const currentRecents = recentTrainNumbers();
  const updated = [cleanTrainNo, ...currentRecents.filter(x => x !== cleanTrainNo)].slice(0, 10);

  localStorage.setItem(recentTrainStorageKey, JSON.stringify(updated));
  renderRecentTrainNumbers();
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function collectGatewayIds(data) {
  const trainIds = Array.isArray(data.train?.gateways) ? data.train.gateways : [];
  const statusIds = Array.isArray(data.gateways) ? data.gateways.map((gw) => gw.gatewayId) : [];
  const ids = [...trainIds, ...statusIds].filter(Boolean);
  return [...new Set(ids)];
}

function updateGatewaySelector(data) {
  const select = $('dashboardGateway');
  if (!select) return;
  const previous = select.value;
  const ids = collectGatewayIds(data);
  dashboardGatewayIds = ids.length ? ids : [...defaultGatewayIds];
  const optionsHtml = [
    '<option value="">All Gateways</option>',
    ...dashboardGatewayIds.map((gatewayId) => `<option value="${escapeHtml(gatewayId)}">${escapeHtml(`${gatewayLabel(gatewayId)} - ${gatewayId}`)}</option>`),
  ].join('');
  select.innerHTML = optionsHtml;
  select.value = dashboardGatewayIds.includes(previous) ? previous : '';

  const cleanup = $('cleanupGateway');
  if (cleanup) {
    const cleanupPrevious = cleanup.value;
    cleanup.innerHTML = optionsHtml;
    cleanup.value = dashboardGatewayIds.includes(cleanupPrevious) ? cleanupPrevious : '';
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : response.statusText;
    logClientEvent('fetch_error', { message: url, errorMessage: `${response.status} ${detail}` });
    throw new Error(`${response.status} ${detail}`);
  }
  return data;
}

function formatDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function bytes(value) {
  const size = Number(value);
  if (!Number.isFinite(size)) return '-';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function alertColor(alertType) {
  if (alertType === 'RED') return '#dc2626';
  if (alertType === 'YELLOW') return '#f59e0b';
  return '#16a34a';
}

function normalizeAlert(value) {
  return String(value || 'GREEN').toUpperCase();
}

function lastDataTime(train, gateways, alerts, archives) {
  return train.updatedAt || archives[0]?.receivedAt || gateways.find((gw) => gw.lastHeartbeat)?.lastHeartbeat || alerts[0]?.createdAt;
}

function latestAlertFor(alerts, gatewayId) {
  const gwAlerts = alerts.filter((alert) => alert.gatewayId === gatewayId);
  if (!gwAlerts.length) return null;
  
  const hasRed = gwAlerts.find(a => normalizeAlert(a.alert) === 'RED');
  if (hasRed) return hasRed;
  
  const hasYellow = gwAlerts.find(a => normalizeAlert(a.alert) === 'YELLOW');
  if (hasYellow) return hasYellow;
  
  return gwAlerts[0];
}

function archiveCountFor(archives, gatewayId) {
  return archives.filter((archive) => archive.gatewayId === gatewayId).length;
}

function renderGatewayCards(gatewayIdsToShow, gateways = [], train = {}, alerts = [], archives = []) {
  setHtml('gatewayList', gatewayIdsToShow.map((gatewayId) => {
    const gw = gateways.find((item) => item.gatewayId === gatewayId) || { gatewayId, trainId: train.trainNo, online: false };
    const latest = latestAlertFor(alerts, gatewayId);
    const alertStatus = normalizeAlert(latest?.alert);
    const statusClass = gw.online ? 'online-box' : 'offline-box';
    return `
      <article class="gateway-card ${statusClass}">
        <div class="gateway-title">
          <span>${gatewayLabel(gatewayId)} - ${gatewayId}</span>
          <span class="badge ${gw.online ? 'online' : 'offline'}">${gw.online ? 'Online' : 'Offline'}</span>
        </div>
        <div class="gateway-kpis">
          <div><span>Train</span><strong>${train.trainNo || gw.trainId || '-'}</strong></div>
          <div><span>Latest Peak</span><strong>${latest ? `${latest.peakValueG} G` : '-'}</strong></div>
          <div class="alert-kpi ${latest ? alertStatus : ''}"><span>Alert</span><strong>${latest ? alertStatus : '-'}</strong></div>
          <div><span>Archives</span><strong>${archiveCountFor(archives, gatewayId)}</strong></div>
        </div>
        <div>Last heartbeat: ${formatDate(gw.lastHeartbeat)}</div>
        <div>Last alert location: ${latest ? `${latest.latitude}, ${latest.longitude}` : '-'}</div>
      </article>
    `;
  }).join(''));
}

function initializeMaps() {
  if (!window.L) {
    ['mapGw1', 'mapGw2'].forEach((target) => {
      setHtml(target, '<div class="empty-state">Leaflet map failed to load</div>');
    });
    return;
  }

  [
    { slotIndex: 0, target: 'mapGw1' },
    { slotIndex: 1, target: 'mapGw2' },
  ].forEach(({ slotIndex, target }) => {
    if (!$(target)) return;
    if (maps[slotIndex]) {
      maps[slotIndex].remove();
    }
    maps[slotIndex] = L.map(target, { zoomControl: true }).setView([22.9734, 78.6569], 5);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 19,
    }).addTo(maps[slotIndex]);
    layers[slotIndex] = L.layerGroup().addTo(maps[slotIndex]);

  });
}

function refreshVisibleMaps(delay = 120) {
  setTimeout(() => {
    dashboardGatewayIds.forEach((gatewayId, index) => {
      if (visibleGatewayIds().includes(gatewayId)) {
        maps[index]?.invalidateSize();
      }
    });
  }, delay);
}

function dashboardAlertToMapPoint(alert) {
  return {
    gateway_id: alert.gatewayId,
    lat: alert.latitude,
    lon: alert.longitude,
    color: alert.alert,
    peak_g: alert.peakValueG,
    speed_kmph: alert.speedKmph,
    position_mm: alert.positionMm,
    created_at: alert.createdAt,
    source: 'alert',
  };
}

function jitterPoint(lat, lon, index) {
  if (!index || index === 0) return [Number(lat), Number(lon)];
  // Very tiny spiral offset (approx 2 to 10 meters) so overlapping markers fan out slightly
  // but stay glued to the thick route line
  const angle = index * 2.4; 
  const offset = 0.00005 + (0.00001 * (index % 5)); 
  return [Number(lat) + offset * Math.sin(angle), Number(lon) + offset * Math.cos(angle)];
}

function snapToRoute(lat, lon, routePoints) {
  if (!routePoints || routePoints.length === 0) {
    return [Number(lat), Number(lon)];
  }
  let closestPoint = routePoints[0];
  let minDistance = Infinity;
  const targetLat = Number(lat);
  const targetLon = Number(lon);
  
  for (const pt of routePoints) {
    const ptLat = Number(pt.lat);
    const ptLon = Number(pt.lon);
    const dist = Math.pow(ptLat - targetLat, 2) + Math.pow(ptLon - targetLon, 2);
    if (dist < minDistance) {
      minDistance = dist;
      closestPoint = pt;
    }
  }
  return [Number(closestPoint.lat), Number(closestPoint.lon)];
}

function routePopup(point) {
  const positionKm = Number.isFinite(Number(point.position_mm)) ? `${(Number(point.position_mm) / 1000).toFixed(2)} km` : '-';
  return `
    <div class="leaflet-popup-content-box">
      <strong>${normalizeAlert(point.color)} - ${gatewayLabel(point.gateway_id)}</strong><br>
      <span>Session:</span> ${point.session || '-'}<br>
      <span>Peak:</span> ${point.peak_g ?? '-'} G<br>
      <span>Position:</span> ${positionKm}<br>
      <span>Location:</span> ${point.lat}, ${point.lon}
    </div>
  `;
}

function gatewayMatches(item, gatewayId) {
  return !gatewayId || item.gatewayId === gatewayId || item.gateway_id === gatewayId;
}

function setGatewayDetailVisible(visible) {
  const section = $('dashboardGatewayDetails');
  if (section) section.classList.toggle('hidden', !visible);
}

function syncCleanupGateway() {
  const selected = selectedGatewayValue();
  const cleanup = $('cleanupGateway');
  if (cleanup) cleanup.value = selected;
}
function syncCalibrationGateway() {
  const visibleIds = visibleGatewayIds();
  document.querySelectorAll('.calibration-card').forEach((card) => {
    card.classList.toggle('hidden', !visibleIds.includes(card.dataset.gateway));
  });
  setText('loadAllCalibrationBtn', selectedGatewayValue() ? 'Load Selected' : 'Load All');
}

function renderDashboard(data) {
  const userRole = (data.userRole || 'operator').toLowerCase();
  const perms = data.permissions || {
    can_configure_thresholds: false,
    can_manage_users: false,
    can_view_alerts: true
  };
  
  const swaggerBtn = document.getElementById('swaggerBtn') || document.querySelector('a[href="/docs"]');
  if (swaggerBtn) {
    swaggerBtn.style.display = (userRole === 'admin') ? '' : 'none';
  }
  const usersLink = document.getElementById('dropdownUsersLink');
  if (usersLink) {
    usersLink.style.display = perms.can_manage_users ? '' : 'none';
  }

  document.querySelectorAll('.tab').forEach((button) => {
    const tabId = button.dataset.tab;
    
    // Hide tabs based on RBAC permissions
    if (['calibration', 'archives', 'reset', 'logs'].includes(tabId) && userRole !== 'admin') {
      button.style.display = 'none'; // Strictly Admin only based on user request
    } else if (tabId === 'users' && !perms.can_manage_users) {
      button.style.display = 'none';
    } else if (tabId === 'alerts' && !perms.can_view_alerts) {
      button.style.display = 'none';
    } else {
      button.style.display = '';
    }
  });

  const activeTabBtn = document.querySelector('.tab.active');
  const activeTabId = activeTabBtn ? activeTabBtn.dataset.tab : '';
  
  // Redirect to overview if they are on a tab they shouldn't see
  if (activeTabId === 'users' && !perms.can_manage_users) selectTab('alerts');
  if (activeTabId === 'calibration' && !perms.can_configure_thresholds) selectTab('overview');
  if (activeTabId === 'alerts' && !perms.can_view_alerts) selectTab('overview');
  if (['calibration', 'archives', 'reset', 'logs'].includes(activeTabId) && userRole !== 'admin') {
    selectTab('overview');
  }

  const oldGatewayIds = [...dashboardGatewayIds];
  state.dashboard = data;
  updateGatewaySelector(data);
  const gatewaysChanged = oldGatewayIds.join(',') !== dashboardGatewayIds.join(',');
  const selectedGateway = selectedGatewayValue();
  state.selectedGateway = selectedGateway;
  const train = data.train || {};
  const gateways = data.gateways || [];
  let alerts = data.lastAlerts || [];
  const archives = data.archives || [];
  const activeSession = data.activeSession;
  const rmsPoints = data.rmsPoints || [];
  let mapAlerts = data.mapAlerts || alerts.map(dashboardAlertToMapPoint);

  if (state.selectedDateFilter) {
    alerts = alerts.filter(item => getItemDateStr(item) === state.selectedDateFilter);
    mapAlerts = mapAlerts.filter(item => getItemDateStr(item) === state.selectedDateFilter);
  }

  if (state.filterZone) {
    alerts = alerts.filter(item => (item.zone || 'NCR') === state.filterZone);
    mapAlerts = mapAlerts.filter(item => (item.zone || 'NCR') === state.filterZone);
  }
  if (state.filterDivision) {
    alerts = alerts.filter(item => (item.division || 'Prayagraj') === state.filterDivision);
    mapAlerts = mapAlerts.filter(item => (item.division || 'Prayagraj') === state.filterDivision);
  }
  if (state.filterSection) {
    alerts = alerts.filter(item => (item.section || 'ABC-XYZ') === state.filterSection);
    mapAlerts = mapAlerts.filter(item => (item.section || 'ABC-XYZ') === state.filterSection);
  }
  const summaryAlerts = alerts.filter((alert) => gatewayMatches(alert, selectedGateway));

  if (state.filterLevel) {
    alerts = alerts.filter(item => item.alert === state.filterLevel);
    mapAlerts = mapAlerts.filter(item => item.color === state.filterLevel);
  }

  const allGatewayIds = dashboardGatewayIds;
  const viewGatewayIds = visibleGatewayIds();
  const allGateways = gateways.filter((gw) => allGatewayIds.includes(gw.gatewayId));
  const viewAlerts = alerts.filter((alert) => gatewayMatches(alert, selectedGateway));
  const viewArchives = archives.filter((archive) => gatewayMatches(archive, selectedGateway));
  const viewRmsPoints = rmsPoints.filter((point) => gatewayMatches(point, selectedGateway));
  const viewMapAlerts = mapAlerts.filter((point) => gatewayMatches(point, selectedGateway));
  const onlineCount = allGatewayIds.filter((gatewayId) => gateways.find((gw) => gw.gatewayId === gatewayId)?.online).length;
  const criticalCount = alerts.filter((alert) => alert.alert === 'RED').length;
  const trainDisplayName = train.trainName 
    ? `${train.trainNo} - ${train.trainName}`
    : train.trainNo || '-';
  setText('summaryTrain', trainDisplayName);
  let latestTripStr = '-';
  if (state.selectedTrip) {
    latestTripStr = `${state.selectedTrip.startTimeStr}<br>to<br>${state.selectedTrip.endTimeStr}`;
  } else if (archives && archives.length > 0) {
    const latestArch = archives[0];
    const endTimeVal = new Date(latestArch.receivedAt);
    const startTimeVal = new Date(endTimeVal.getTime() - 60 * 60 * 1000);
    latestTripStr = `${formatDate(startTimeVal)}<br>to<br>${formatDate(endTimeVal)}`;
  }
  setHtml('summaryLatestTrip', latestTripStr);
  
  let health = 'GREEN';
  let healthText = 'Normal';
  if (alerts.some(a => normalizeAlert(a.alert) === 'RED')) { health = 'RED'; healthText = 'Critical'; }
  else if (alerts.some(a => normalizeAlert(a.alert) === 'YELLOW')) { health = 'YELLOW'; healthText = 'Warning'; }
  
  const isOnline = onlineCount > 0;
  setHtml('summaryStatus', `
    <div style="display: flex; flex-direction: column; gap: 4px; align-items: flex-start;">
      <span class="badge ${health}">${healthText} Health</span>
      <span class="badge ${isOnline ? 'online' : 'offline'}">${isOnline ? 'Online' : 'Offline'}</span>
    </div>
  `);

  setText('summaryGateways', `${onlineCount}/${allGatewayIds.length || 0}`);
  setText('summaryLastData', formatDate(lastDataTime(train, allGateways, alerts, archives)));
  setText('summaryArchives', archives.length);
  setText('summaryCritical', criticalCount);

  setHtml('gatewayList', allGatewayIds.map((gatewayId) => {
    const gw = gateways.find((item) => item.gatewayId === gatewayId) || { gatewayId, trainId: train.trainNo, online: false };
    const latest = latestAlertFor(alerts, gatewayId);
    
    const latestPeakG = latest ? latest.peakValueG : ((gw.latestPeakG !== undefined && gw.latestPeakG !== null) ? gw.latestPeakG : null);
    const latestAlertVal = latest ? latest.alert : (gw.latestAlert ? gw.latestAlert : null);
    const latestLat = latest ? latest.latitude : ((gw.latestLatitude !== undefined && gw.latestLatitude !== null) ? gw.latestLatitude : null);
    const latestLon = latest ? latest.longitude : ((gw.latestLongitude !== undefined && gw.latestLongitude !== null) ? gw.latestLongitude : null);

    const alertStatus = normalizeAlert(latestAlertVal);
    const gatewayAlerts = alerts.filter((a) => a.gatewayId === gatewayId);
    const severityCount = latestAlertVal ? (gatewayAlerts.length ? gatewayAlerts.filter((a) => normalizeAlert(a.alert) === alertStatus).length : 1) : 0;
    const alertDisplay = latestAlertVal ? `${alertStatus} (${severityCount})` : '-';
    const statusClass = gw.online ? 'online-box' : 'offline-box';
    
    return `
      <article class="gateway-card ${statusClass}">
        <div class="gateway-title">
          <span>${gatewayLabel(gatewayId)} - ${gatewayId}</span>
          <span class="badge ${gw.online ? 'online' : 'offline'}">${gw.online ? 'Online' : 'Offline'}</span>
        </div>
        <div class="gateway-kpis">
          <div><span>Train</span><strong>${train.trainNo || gw.trainId || '-'}</strong></div>
          <div><span>Latest Peak</span><strong>${latestPeakG !== null ? `${latestPeakG} G` : '-'}</strong></div>
          <div class="alert-kpi ${latestAlertVal ? alertStatus : ''}"><span>Alert</span><strong>${alertDisplay}</strong></div>
          <div><span>Archives</span><strong>${archiveCountFor(archives, gatewayId)}</strong></div>
        </div>
        <div>Last heartbeat: ${formatDate(gw.lastHeartbeat)}</div>
        <div>Last alert location: ${latestLat && latestLon ? `${latestLat}, ${latestLon}` : '-'}</div>
      </article>
    `;
  }).join(''));

  setGatewayDetailVisible(false);
  syncCleanupGateway();
  if (gatewaysChanged) buildCalibrationCards();
  syncCalibrationGateway();
  renderAlertSummary(summaryAlerts);
  renderAlerts(viewAlerts);
  renderArchives(viewArchives);
  renderSession(activeSession, train.trainNo);
  renderMaps(viewAlerts, gateways, viewRmsPoints, viewMapAlerts);
}

function getItemDateStr(item) {
  if (!item) return null;
  const rawDate = item.createdAt || item.created_at || item.receivedAt || item.received_at;
  if (!rawDate) return null;
  try {
    const parts = String(rawDate).split('T');
    const datePart = parts[0];
    if (datePart.match(/^\d{4}-\d{2}-\d{2}$/)) {
      return datePart;
    }
  } catch (e) {}
  
  try {
    const d = new Date(rawDate);
    if (!isNaN(d.getTime())) {
      const year = d.getFullYear();
      const month = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${year}-${month}-${day}`;
    }
  } catch (e) {}
  return null;
}
function renderAlertSummary(alerts) {
  const red = alerts.filter((alert) => alert.alert === 'RED').length;
  const yellow = alerts.filter((alert) => alert.alert === 'YELLOW').length;
  const green = alerts.filter((alert) => alert.alert === 'GREEN').length;
  setText('alertTotal', alerts.length);
  setText('alertRed', red);
  setText('alertYellow', yellow);
  setText('alertGreen', green);

  // Sync the active card styling based on current filter state
  ['RED', 'YELLOW', 'GREEN', 'TOTAL'].forEach(lvl => {
    const card = document.getElementById('card-' + lvl);
    if (card) {
      if (state.filterLevel === lvl || (state.filterLevel === null && lvl === 'TOTAL')) {
        card.classList.add('card-active');
      } else {
        card.classList.remove('card-active');
      }
    }
  });
}

window.focusAlertOnMap = function(lat, lon, gatewayId) {
  if (lat == null || lon == null) return;
  window.lastActiveTabBeforeMap = document.querySelector('.tab.active')?.dataset?.tab || 'alerts';
  selectTab('alerts');
  window.scrollTo({ top: 0, behavior: 'smooth' });
  setTimeout(() => {
    const showAllBtn = document.getElementById('showAllMapsBtn');
    document.body.classList.add('fullscreen-map-mode');
    document.querySelectorAll('.map-card').forEach(card => {
      if (!gatewayId || card.getAttribute('data-map-gateway') === gatewayId) card.classList.remove('hidden');
      else card.classList.add('hidden');
    });
    if (showAllBtn) {
      showAllBtn.classList.remove('hidden'); showAllBtn.style.display = 'block';
      showAllBtn.innerHTML = '<i class="bi bi-x-circle"></i> Close Map';
      showAllBtn.onclick = window.closeFullscreenMap;
    }
    setTimeout(() => { Object.values(maps).forEach(m => { if(m) { m.invalidateSize(); m.flyTo([lat, lon], 17, {duration: 1.5}); } }); }, 200);
  }, 150);
};

function renderAlerts(alerts) {
  setHtml('alertsTable', alerts.length ? alerts.map((alert) => `
    <tr>
      <td>${formatDate(alert.createdAt)}</td>
      <td>${alert.gatewayId || '-'}</td>
      <td>${alert.zone || 'NCR'}</td>
      <td>${alert.division || 'Prayagraj'}</td>
      <td>${alert.section || 'ABC-XYZ'}</td>
      <td>${alert.peakValueG ?? '-'}</td>
      <td><span class="badge ${alert.alert}">${alert.alert || '-'}</span></td>
      <td>
        ${alert.latitude && alert.longitude ? 
          `<button class="secondary" style="padding: 4px 8px; font-size: 12px;" onclick="focusAlertOnMap(${alert.latitude}, ${alert.longitude}, '${alert.gatewayId}')"><i class="bi bi-geo-alt-fill"></i> View on Map</button>` 
          : '-'}
      </td>
    </tr>
  `).join('') : '<tr><td colspan="8">No alerts found.</td></tr>');
}

function pointInRouteBounds(point, routePoints, padding = 0.035) {
  if (!routePoints.length) return true;
  const lats = routePoints.map((item) => Number(item.lat));
  const lons = routePoints.map((item) => Number(item.lon));
  const minLat = Math.min(...lats) - padding;
  const maxLat = Math.max(...lats) + padding;
  const minLon = Math.min(...lons) - padding;
  const maxLon = Math.max(...lons) + padding;
  const lat = Number(point.lat);
  const lon = Number(point.lon);
  return lat >= minLat && lat <= maxLat && lon >= minLon && lon <= maxLon;
}

function routeBearing(previous, current) {
  const lat1 = Number(previous.lat) * Math.PI / 180;
  const lat2 = Number(current.lat) * Math.PI / 180;
  const deltaLon = (Number(current.lon) - Number(previous.lon)) * Math.PI / 180;
  const y = Math.sin(deltaLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(deltaLon);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

function addDirectionArrow(layer, previous, current, severity) {
  const midLat = (Number(previous.lat) + Number(current.lat)) / 2;
  const midLon = (Number(previous.lon) + Number(current.lon)) / 2;
  const bearing = routeBearing(previous, current);
  L.marker([midLat, midLon], {
    interactive: false,
    icon: L.divIcon({
      className: 'direction-arrow',
      html: `<span style="transform: rotate(${bearing}deg); color: ${alertColor(severity)}">&#9650;</span>`,
      iconSize: [18, 18],
      iconAnchor: [9, 9],
    }),
  }).addTo(layer);
}

function trainIconHtml(bearing) {
  const rotation = Number.isFinite(Number(bearing)) ? Number(bearing) : 0;
  return `
    <div style="background: #ffffff; border: 2.5px solid #1d70b8; border-radius: 50%; width: 34px; height: 34px; display: flex; align-items: center; justify-content: center; box-shadow: 0 3px 8px rgba(0,0,0,0.35); transform: rotate(${rotation}deg); transition: transform 0.2s ease;">
      <i class="bi bi-train-front-fill" style="font-size: 20px; color: #1d70b8; display: block; line-height: 1;"></i>
    </div>
  `;
}

function drawColoredRoute(layer, points) {
  if (!layer || points.length < 2) return;

  const latlngs = [];
  for (let i = 1; i < points.length; i += 1) {
    const previous = points[i - 1];
    const current = points[i];
    const severity = normalizeAlert(current.color);
    const p1 = [Number(previous.lat), Number(previous.lon)];
    const p2 = [Number(current.lat), Number(current.lon)];
    latlngs.push(p1);
    if (i === points.length - 1) latlngs.push(p2);

    L.polyline([p1, p2], {
      color: alertColor(severity),
      weight: 6,
      opacity: 0.9,
      lineCap: 'round',
      lineJoin: 'round',
      smoothFactor: 1.2,
      className: 'map-route-polyline',
    }).addTo(layer);
  }

  if (window.L.polylineDecorator && latlngs.length > 1) {
    L.polylineDecorator(L.polyline(latlngs), {
      patterns: [
        {
          offset: '5%',
          repeat: '100px',
          symbol: L.Symbol.arrowHead({
            pixelSize: 14,
            polygon: false,
            pathOptions: { stroke: true, weight: 3, color: '#1f2937', opacity: 0.8 }
          })
        }
      ]
    }).addTo(layer);
  }
}


function renderMaps(alerts, gateways, rmsPoints = [], mapAlerts = []) {
  const selectedGateway = selectedGatewayValue();
  const visibleIds = visibleGatewayIds();
  const validRmsPoints = rmsPoints
    .filter((point) => Number.isFinite(Number(point.lat)) && Number.isFinite(Number(point.lon)));

  const slots = [
    { target: 'mapGw1', defaultId: 'GW_UABAMS_BOGIE_01', stateId: 'gw1MapState' },
    { target: 'mapGw2', defaultId: 'GW_UABAMS_BOGIE_02', stateId: 'gw2MapState' },
  ];

  slots.forEach((slot, index) => {
    const card = document.querySelector(`[data-map-gateway="${slot.defaultId}"]`);
    const gatewayId = dashboardGatewayIds[index];

    if (!gatewayId) {
      if (card) card.classList.add('hidden');
      return;
    }

    if (card) {
      card.classList.toggle('hidden', !visibleIds.includes(gatewayId));
      const titleSpan = card.querySelector('.gateway-title span:first-child');
      if (titleSpan) {
        titleSpan.textContent = `${gatewayLabel(gatewayId)} Route Map`;
      }
    }

    const map = maps[index];
    const layer = layers[index];
    if (!map || !layer || !window.L) return;
    layer.clearLayers();
    if (!visibleIds.includes(gatewayId)) return;

    const routePoints = validRmsPoints.filter((point) => point.gateway_id === gatewayId);
    const rawAlertPoints = (mapAlerts.length ? mapAlerts : alerts.map(dashboardAlertToMapPoint))
      .filter((point) => point.gateway_id === gatewayId)
      .filter((point) => ['RED', 'YELLOW', 'GREEN'].includes(normalizeAlert(point.color)))
      .filter((point) => Number.isFinite(Number(point.lat)) && Number.isFinite(Number(point.lon)))
      .slice()
      .reverse();
    const alertPoints = rawAlertPoints.filter((point) => Number(point.lat) !== 0 && Number(point.lon) !== 0);

    const gw = gateways.find((item) => item.gatewayId === gatewayId);
    setText(slot.stateId, gw?.online ? 'Online' : 'Offline');
    setClass(slot.stateId, `badge ${gw?.online ? 'online' : 'offline'}`);

    if (!routePoints.length && !alertPoints.length) {
      map.setView([22.9734, 78.6569], 5);
      return;
    }

    drawColoredRoute(layer, routePoints);


    // Draw Heatmap (Deferred to avoid Canvas 0 width error when unhiding map)
    if (window.L.heatLayer && alertPoints.length > 0) {
      setTimeout(() => {
        if (!visibleIds.includes(gatewayId)) return;
        const heatPoints = alertPoints.map(p => {
          const snapped = snapToRoute(p.lat, p.lon, routePoints);
          const severity = normalizeAlert(p.color);
          const intensity = severity === 'RED' ? 1.0 : severity === 'YELLOW' ? 0.5 : 0.2;
          return [snapped[0], snapped[1], intensity];
        });
        L.heatLayer(heatPoints, {
          radius: 35,
          blur: 25,
          maxZoom: 14,
          gradient: {0.2: 'lime', 0.5: 'yellow', 1.0: 'red'}
        }).addTo(layer);
      }, 150);
    }
    
    alertPoints.forEach((point, index) => {
      const severity = normalizeAlert(point.color);
      if (severity !== 'RED' && severity !== 'YELLOW' && severity !== 'GREEN') return;
      const snapped = snapToRoute(point.lat, point.lon, routePoints);
      const markerPoint = jitterPoint(snapped[0], snapped[1], index);
      const iconClass = severity === 'RED' ? 'bi-lightning-fill' : severity === 'YELLOW' ? 'bi-exclamation-triangle-fill' : 'bi-info-circle-fill';
      const iconColor = severity === 'RED' ? '#ef4444' : severity === 'YELLOW' ? '#f59e0b' : '#10b981';
      
      const customIcon = L.divIcon({
        className: 'custom-alert-icon',
        html: `<div style="color: ${iconColor}; font-size: 20px; text-shadow: 1px 1px 2px rgba(0,0,0,0.8); display: flex; align-items: center; justify-content: center;"><i class="bi ${iconClass}"></i></div>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12],
      });

      L.marker(markerPoint, { icon: customIcon })
        .addTo(layer)
        .bindPopup(routePopup(point));
    });

    let bounds;
    if (alertPoints.length > 0) {
      bounds = L.latLngBounds(
        alertPoints.map((point, index) => {
          const snapped = snapToRoute(point.lat, point.lon, routePoints);
          return jitterPoint(snapped[0], snapped[1], index);
        })
      );
    } else {
      bounds = L.latLngBounds(
        routePoints.map((point) => [Number(point.lat), Number(point.lon)])
      );
    }

    if (bounds.isValid()) {
      map.fitBounds(bounds.pad(selectedGateway ? 0.35 : 0.25), { maxZoom: 17 });
    }
  });

  refreshVisibleMaps();
}
function clearHiddenTrainMarkers(visibleIds) {
  Object.entries(trainMarkers).forEach(([slotIndex, marker]) => {
    const gatewayId = dashboardGatewayIds[Number(slotIndex)];
    if (!gatewayId || !visibleIds.includes(gatewayId)) {
      marker.remove();
      delete trainMarkers[slotIndex];
    }
  });
}

async function renderGatewayTrainPosition(trainNo, gatewayId) {
  try {
    const data = await requestJson(`/api/v1/trains/${encodeURIComponent(trainNo)}/position?gateway_id=${encodeURIComponent(gatewayId)}`);
    const point = data.position;
    const slotIndex = dashboardGatewayIds.indexOf(data.gatewayId);
    if (!point || slotIndex < 0 || !maps[slotIndex]) return;
    const map = maps[slotIndex];
    
    const routePoints = (state.rmsPoints || []).filter(pt => pt.gateway_id === data.gatewayId);
    const snapped = snapToRoute(point.latitude, point.longitude, routePoints);
    
    const oldMarker = trainMarkers[slotIndex];
    if (oldMarker) oldMarker.remove();
    trainMarkers[slotIndex] = L.marker(snapped, {
      icon: L.divIcon({
        className: '',
        html: trainIconHtml(point.bearing),
        iconSize: [34, 34],
        iconAnchor: [17, 17],
      }),
      zIndexOffset: 900,
    }).addTo(map).bindPopup(`Current train position<br>Gateway: ${data.gatewayId}<br>Speed: ${point.speedKmph ?? '-'} kmph<br>Position: ${point.positionMm ?? '-'} mm`);
  } catch (error) {
    logClientEvent('position_error', { message: gatewayId, errorMessage: error.message });
  }
}

async function renderTrainPosition(trainNo) {
  if (!trainNo || !window.L) return;
  const visibleIds = visibleGatewayIds();
  clearHiddenTrainMarkers(visibleIds);
  await Promise.all(visibleIds.map((gatewayId) => renderGatewayTrainPosition(trainNo, gatewayId)));
}

function startAutoRefresh() {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  autoRefreshTimer = setInterval(() => {
    if (lastLoadedTrainNo) loadDashboard({ silent: true });
  }, 8000);
}

async function loadLogs() {
  try {
    const data = await requestJson('/api/v1/logs?limit=100&_t=' + Date.now());
    const rows = data.logs || [];
    
    const getLogSeverity = (log) => {
      const act = String(log.action || '').toLowerCase();
      const err = String(log.errorMessage || '').toLowerCase();
      const hasError = (err && err !== '-' && err !== 'none' && err !== 'null');
      if (act.includes('delete') || act.includes('remove') || act.includes('reset') || act.includes('failed') || act.includes('unauthorized')) return 'CRITICAL';
      if (act.includes('login') || act.includes('logout') || act.includes('calibrate') || act.includes('export')) return hasError ? 'WARNING' : 'NORMAL';
      if (hasError) return 'CRITICAL';
      return 'NORMAL';
    };

    setHtml('logsTable', rows.length ? rows.map((log) => {
      const severity = getLogSeverity(log);
      let badgeStyle = '';
      if (severity === 'CRITICAL') {
        badgeStyle = 'background: rgba(239, 68, 68, 0.12); color: #dc2626; border: 1px solid rgba(239, 68, 68, 0.35);';
      } else if (severity === 'WARNING') {
        badgeStyle = 'background: rgba(245, 158, 11, 0.12); color: #d97706; border: 1px solid rgba(245, 158, 11, 0.35);';
      } else {
        badgeStyle = 'background: rgba(16, 185, 129, 0.12); color: #059669; border: 1px solid rgba(16, 185, 129, 0.35);';
      }
      
      const badgeHtml = `<span style="padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; display: inline-block; text-transform: uppercase; ${badgeStyle}">${severity}</span>`;
      
      const errHtml = log.errorMessage && log.errorMessage !== '-' ? `
        <span style="color: #ef4444; font-weight: bold;">${escapeHtml(log.errorMessage)}</span>
      ` : '-';

      return `
        <tr>
          <td>${formatDate(log.createdAt)}</td>
          <td>${escapeHtml(log.username || '-')}</td>
          <td>${escapeHtml(log.page || '-')}</td>
          <td>${escapeHtml(log.action || '-')}</td>
          <td>${badgeHtml}</td>
          <td>${errHtml}</td>
          <td>${escapeHtml(log.ipAddress || '-')}</td>
          <td>${log.latitude && log.longitude ? `${log.latitude}, ${log.longitude}` : '-'}</td>
        </tr>
      `;
    }).join('') : '<tr><td colspan="8">No logs found.</td></tr>');
  } catch (error) {
    setHtml('logsTable', `<tr><td colspan="8" class="error-text">${escapeHtml(error.message)}</td></tr>`);
  }
}

window.viewTripOnDashboard = function(startTimeStr, endTimeStr) {
  state.selectedTrip = { startTimeStr, endTimeStr };
  selectTab('overview');
  setHtml('summaryLatestTrip', `${startTimeStr}<br>to<br>${endTimeStr}`);
};

function renderArchives(archives) {
  setHtml('archiveTable', archives.length ? archives.map((archive) => {
    const endTimeVal = new Date(archive.receivedAt);
    const startTimeVal = new Date(endTimeVal.getTime() - 60 * 60 * 1000);
    const startTimeStr = formatDate(startTimeVal);
    const endTimeStr = formatDate(endTimeVal);
    const alertCount = archive.peakAlertCount ?? archive.faultRecordCount ?? 0;
    
    return `
      <tr onclick="viewTripOnDashboard('${startTimeStr}', '${endTimeStr}')" style="cursor: pointer;" class="clickable-row">
        <td>${startTimeStr}</td>
        <td>${endTimeStr}</td>
        <td>${bytes(archive.sizeBytes)}</td>
        <td>${archive.rmsRecordCount ?? 0}</td>
        <td>${archive.peakRecordCount ?? 0}</td>
        <td>${alertCount}</td>
        <td>${archive.status || '-'}</td>
      </tr>
    `;
  }).join('') : '<tr><td colspan="7">No archives uploaded.</td></tr>');
}

function renderGatewayDetails(data) {
  const status = data.status || {};
  const summary = data.summary || {};
  const location = summary.latestLocation || {};
  setText('detailGatewayId', data.gatewayId || '-');
  setText('detailStatus', status.online ? 'Online' : 'Offline');
  setText('detailHeartbeat', formatDate(status.lastHeartbeat));
  setText('detailAlert', summary.latestAlert ? `${summary.latestAlert}${summary.latestPeakG ? ` (${summary.latestPeakG} G)` : ''}` : '-');
  setText('detailRms', summary.rmsRecords ?? '-');
  setText('detailPeak', summary.peakRecords ?? '-');
  setText('detailFaults', summary.faultRecords ?? '-');
  setText('detailArchives', summary.archives ?? '-');

  setHtml('detailAlertsTable', data.alerts?.length ? data.alerts.map((alert) => `
    <tr>
      <td>${formatDate(alert.createdAt)}</td>
      <td>${alert.peakValueG ?? '-'}</td>
      <td><span class="badge ${alert.alert}">${alert.alert || '-'}</span></td>
      <td>
        ${alert.latitude && alert.longitude ? 
          `<button class="secondary" style="padding: 4px 8px; font-size: 12px;" onclick="focusLocationOnMap(${alert.latitude}, ${alert.longitude}, '${alert.gatewayId}')"><i class="bi bi-geo-alt-fill"></i> View on Map</button>` 
          : '-'}
      </td>
    </tr>
  `).join('') : '<tr><td colspan="4">No alerts for selected gateway.</td></tr>');

  setHtml('detailArchivesTable', data.archives?.length ? data.archives.map((archive) => `
    <tr>
      <td>${formatDate(archive.receivedAt)}</td>
      <td>${bytes(archive.sizeBytes)}</td>
      <td>${archive.rmsRecordCount ?? 0}</td>
      <td>${archive.peakRecordCount ?? 0}</td>
      <td>${archive.faultRecordCount ?? 0}</td>
      <td>${archive.status || '-'}</td>
    </tr>
  `).join('') : '<tr><td colspan="6">No archives for selected gateway.</td></tr>');
}

async function loadGatewayDetails() {
  const trainNo = trainNoValue();
  const gatewayId = selectedGatewayValue();
  if (!gatewayId) { setGatewayDetailVisible(false); return; }
  try {
    if (!options.silent) setStatus('Loading');
    const data = await requestJson(`/api/v1/trains/${encodeURIComponent(trainNo)}/gateways/${encodeURIComponent(gatewayId)}/details`);
    renderGatewayDetails(data);
    setStatus('Live', 'ok');
  } catch (error) {
    setStatus('Error', 'error');
    setHtml('detailAlertsTable', `<tr><td colspan="4" class="error-text">${error.message}</td></tr>`);
  }
}

function localDateTimeToIso(value) {
  return value ? new Date(value).toISOString() : null;
}

async function cleanupData() {
  const trainNo = trainNoValue();
  const latitudeText = $('cleanupLat')?.value.trim();
  const longitudeText = $('cleanupLon')?.value.trim();
  const payload = {
    trainNo,
    gatewayId: $('cleanupGateway')?.value || null,
    startTime: localDateTimeToIso($('cleanupStart')?.value),
    endTime: localDateTimeToIso($('cleanupEnd')?.value),
    latitude: latitudeText ? Number(latitudeText) : null,
    longitude: longitudeText ? Number(longitudeText) : null,
    radiusMeters: Number($('cleanupRadius')?.value || 100),
    reason: $('cleanupReason')?.value.trim() || null,
  };
  if (!payload.startTime && !payload.endTime && (payload.latitude === null || payload.longitude === null)) {
    setText('resetOutput', 'Provide a time range or latitude/longitude before deleting data.');
    return;
  }
  if (!confirm(`Delete matching data for train ${trainNo}?`)) return;
  
  const adminPwd = prompt(`Enter admin password for targeted cleanup of train ${trainNo}:`);
  if (adminPwd === null) return; // user cancelled
  if (!adminPwd.trim()) {
    alert('Admin password is required.');
    return;
  }

  try {
    const data = await requestJson('/api/v1/data/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': adminPwd.trim() },
      body: JSON.stringify(payload),
    });
    setText('resetOutput', JSON.stringify(data, null, 2));
    setStatus('Cleaned', 'ok');
    await loadDashboard();
    await loadGatewayDetails();
  } catch (error) {
    setStatus('Error', 'error');
    setText('resetOutput', error.message);
  }
}
function renderSession(session, trainNo) {
  setText('sessionText', session
    ? `Active session ${session.sessionId} for train ${trainNo}.`
    : `No active session for train ${trainNo || '-'}.`);
}

function calibrationCard(gatewayId) {
  const label = gatewayLabel(gatewayId);
  return `
    <article class="calibration-card" data-gateway="${gatewayId}">
      <div class="gateway-title">
        <span>${label} Calibration</span>
      </div>

      <div class="adxl-grid">
        <div class="adxl-col">
          <div class="cal-section-title">1. ADXL Left Offsets</div>
          <div class="cal-form-col">
            <label>ADXL X<input data-field="adxlLeftX" type="number" placeholder="Enter offset of X-axis"></label>
            <label>ADXL Y<input data-field="adxlLeftY" type="number" placeholder="Enter offset of Y-axis"></label>
            <label>ADXL Z<input data-field="adxlLeftZ" type="number" placeholder="Enter offset of Z-axis"></label>
          </div>
        </div>
        <div class="adxl-col">
          <div class="cal-section-title">2. ADXL Right Offsets</div>
          <div class="cal-form-col">
            <label>ADXL X<input data-field="adxlRightX" type="number" placeholder="Enter offset of X-axis"></label>
            <label>ADXL Y<input data-field="adxlRightY" type="number" placeholder="Enter offset of Y-axis"></label>
            <label>ADXL Z<input data-field="adxlRightZ" type="number" placeholder="Enter offset of Z-axis"></label>
          </div>
        </div>
      </div>

      <div class="cal-section-title">3. Bogie Sensor Offsets</div>
      <div class="bogie-section">
        <div class="sub-label">IIS Vibration</div>
        <div class="row-3col">
          <label>IIS Offset X<input data-field="iisX" type="number" placeholder="Enter IIS offset of X-axis"></label>
          <label>IIS Offset Y<input data-field="iisY" type="number" placeholder="Enter IIS offset of Y-axis"></label>
          <label>IIS Offset Z<input data-field="iisZ" type="number" placeholder="Enter IIS offset of Z-axis"></label>
        </div>

        <div class="sub-label" style="margin-top: 8px;">IMU Accelerometer</div>
        <div class="row-3col">
          <label>IMU Accel Offset X<input data-field="imuAccelX" type="number" placeholder="Enter IMU Accel offset of X-axis"></label>
          <label>IMU Accel Offset Y<input data-field="imuAccelY" type="number" placeholder="Enter IMU Accel offset of Y-axis"></label>
          <label>IMU Accel Offset Z<input data-field="imuAccelZ" type="number" placeholder="Enter IMU Accel offset of Z-axis"></label>
        </div>

        <div class="sub-label" style="margin-top: 8px;">IMU Gyroscope</div>
        <div class="row-3col">
          <label>IMU Gyro Offset X<input data-field="imuGyroX" type="number" placeholder="Enter IMU Gyro offset of X-axis"></label>
          <label>IMU Gyro Offset Y<input data-field="imuGyroY" type="number" placeholder="Enter IMU Gyro offset of Y-axis"></label>
          <label>IMU Gyro Offset Z<input data-field="imuGyroZ" type="number" placeholder="Enter IMU Gyro offset of Z-axis"></label>
        </div>
      </div>

      <div class="cal-section-title">4. Encoder Settings</div>
      <div class="row-4col">
        <label>Wheel Diameter (m)<input data-field="wheelDiameterM" type="number" step="0.001" placeholder="Enter wheel diameter (default 0.915)"></label>
        <label>Encoder PPR<input data-field="encoderPpr" type="number" placeholder="Enter encoder PPR (default 100)"></label>
        <label>Spatial Interval (mm)<input data-field="spatialIntervalMm" type="number" placeholder="Enter spatial interval in mm (default 250)"></label>
        <label>Trigger Start Speed (km/h)<input data-field="triggerStartSpeedKmph" type="number" step="0.1" placeholder="Enter trigger start speed in km/h (default 20.0)"></label>
      </div>

      <div class="button-row" style="margin-top: 18px;">
        <button type="button" data-action="load" data-gateway="${gatewayId}">Load ${label}</button>
        <button type="button" class="primary" data-action="save" data-gateway="${gatewayId}">Save & Send ${label}</button>
      </div>
      <span data-role="calStatus"></span>
      <pre class="output compact" data-role="calOutput"></pre>
      <div class="cal-section-title" style="margin-top:16px">📡 Command History</div>
      <div data-role="cmdHistory" style="margin-top:6px;min-height:24px"><em style="opacity:0.5">Load calibration to view history</em></div>
    </article>
  `;
}

function buildCalibrationCards() {
  const pair = $('calibrationPair');
  if (!pair) return;
  pair.innerHTML = dashboardGatewayIds.map(calibrationCard).join('');
  pair.onclick = async (event) => {
    const action = event.target.dataset.action;
    const gatewayId = event.target.dataset.gateway;
    if (!action || !gatewayId) return;
    if (action === 'load') await loadCalibration(gatewayId);
    if (action === 'save') await saveCalibration(gatewayId);
  };
}

function cardFor(gatewayId) {
  return document.querySelector(`.calibration-card[data-gateway="${gatewayId}"]`);
}

function field(card, name) {
  return card?.querySelector(`[data-field="${name}"]`);
}

function setCalibrationValues(gatewayId, data) {
  const card = cardFor(gatewayId);
  if (!card) return;

  const adxlL = data.adxl_left || data.adxlLeft || {};
  const adxlR = data.adxl_right || data.adxlRight || {};
  const bogie = data.bogie || {};
  const encoder = data.encoder || {};

  // 1. ADXL Left & Right
  if (field(card, 'adxlLeftX')) field(card, 'adxlLeftX').value = adxlL.offset_x ?? '';
  if (field(card, 'adxlLeftY')) field(card, 'adxlLeftY').value = adxlL.offset_y ?? '';
  if (field(card, 'adxlLeftZ')) field(card, 'adxlLeftZ').value = adxlL.offset_z ?? '';

  if (field(card, 'adxlRightX')) field(card, 'adxlRightX').value = adxlR.offset_x ?? '';
  if (field(card, 'adxlRightY')) field(card, 'adxlRightY').value = adxlR.offset_y ?? '';
  if (field(card, 'adxlRightZ')) field(card, 'adxlRightZ').value = adxlR.offset_z ?? '';

  // 2. Bogie Sensor (IIS & IMU Offsets)
  if (field(card, 'iisX')) field(card, 'iisX').value = bogie.iis_offset_x ?? '';
  if (field(card, 'iisY')) field(card, 'iisY').value = bogie.iis_offset_y ?? '';
  if (field(card, 'iisZ')) field(card, 'iisZ').value = bogie.iis_offset_z ?? '';

  if (field(card, 'imuAccelX')) field(card, 'imuAccelX').value = bogie.imu_accel_offset_x ?? '';
  if (field(card, 'imuAccelY')) field(card, 'imuAccelY').value = bogie.imu_accel_offset_y ?? '';
  if (field(card, 'imuAccelZ')) field(card, 'imuAccelZ').value = bogie.imu_accel_offset_z ?? '';

  if (field(card, 'imuGyroX')) field(card, 'imuGyroX').value = bogie.imu_gyro_offset_x ?? '';
  if (field(card, 'imuGyroY')) field(card, 'imuGyroY').value = bogie.imu_gyro_offset_y ?? '';
  if (field(card, 'imuGyroZ')) field(card, 'imuGyroZ').value = bogie.imu_gyro_offset_z ?? '';

  // 3. Encoder Settings
  if (field(card, 'wheelDiameterM')) field(card, 'wheelDiameterM').value = encoder.wheel_diameter_m ?? 0.915;
  if (field(card, 'encoderPpr')) field(card, 'encoderPpr').value = encoder.encoder_ppr ?? 100;
  if (field(card, 'spatialIntervalMm')) field(card, 'spatialIntervalMm').value = encoder.spatial_interval_mm ?? 250;
  if (field(card, 'triggerStartSpeedKmph')) field(card, 'triggerStartSpeedKmph').value = encoder.trigger_start_speed_kmph ?? 20.0;
}

async function loadCalibration(gatewayId) {
  const card = cardFor(gatewayId);
  const output = card?.querySelector('[data-role="calOutput"]');

  try {
    const data = await requestJson(`/api/v1/calibration/${encodeURIComponent(gatewayId)}`);
    setCalibrationValues(gatewayId, data);
    if (output) output.textContent = JSON.stringify(data, null, 2);
    const status = card?.querySelector('[data-role="calStatus"]');
    if (status) {
      status.textContent = 'Loaded';
      status.className = 'badge online';
    }
    await loadCommandHistory(gatewayId, card);
  } catch (error) {
    if (output) output.textContent = error.message;
  }
}

async function loadAllCalibration() {
  for (const gatewayId of visibleGatewayIds()) {
    await loadCalibration(gatewayId);
  }
}

async function saveCalibration(gatewayId) {
  const card = cardFor(gatewayId);
  const output = card?.querySelector('[data-role="calOutput"]');

  const payload = {
    adxlLeft: {
      offset_x: Number(field(card, 'adxlLeftX')?.value ?? 0),
      offset_y: Number(field(card, 'adxlLeftY')?.value ?? 0),
      offset_z: Number(field(card, 'adxlLeftZ')?.value ?? 0),
    },
    adxlRight: {
      offset_x: Number(field(card, 'adxlRightX')?.value ?? 0),
      offset_y: Number(field(card, 'adxlRightY')?.value ?? 0),
      offset_z: Number(field(card, 'adxlRightZ')?.value ?? 0),
    },
    bogie: {
      iis_offset_x: Number(field(card, 'iisX')?.value ?? 0),
      iis_offset_y: Number(field(card, 'iisY')?.value ?? 0),
      iis_offset_z: Number(field(card, 'iisZ')?.value ?? 0),
      imu_accel_offset_x: Number(field(card, 'imuAccelX')?.value ?? 0),
      imu_accel_offset_y: Number(field(card, 'imuAccelY')?.value ?? 0),
      imu_accel_offset_z: Number(field(card, 'imuAccelZ')?.value ?? 0),
      imu_gyro_offset_x: Number(field(card, 'imuGyroX')?.value ?? 0),
      imu_gyro_offset_y: Number(field(card, 'imuGyroY')?.value ?? 0),
      imu_gyro_offset_z: Number(field(card, 'imuGyroZ')?.value ?? 0),
    },
    encoder: {
      wheel_diameter_m: Number(field(card, 'wheelDiameterM')?.value ?? 0.915),
      encoder_ppr: Number(field(card, 'encoderPpr')?.value ?? 100),
      spatial_interval_mm: Number(field(card, 'spatialIntervalMm')?.value ?? 250),
      trigger_start_speed_kmph: Number(field(card, 'triggerStartSpeedKmph')?.value ?? 20.0),
    },
  };

  try {
    const data = await requestJson(`/api/v1/calibration/${encodeURIComponent(gatewayId)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (output) output.textContent = JSON.stringify(data, null, 2);
    const status = card?.querySelector('[data-role="calStatus"]');
    if (status) {
      const cmd = data.command;
      if (cmd) {
        status.textContent = `Saved & Queued (v${cmd.version}, ${cmd.commandId})`;
      } else {
        status.textContent = 'Saved';
      }
      status.className = 'badge online';
    }
    await loadCommandHistory(gatewayId, card);
  } catch (error) {
    if (output) output.textContent = error.message;
  }
}

async function loadCommandHistory(gatewayId, card) {
  if (!card) return;
  let historyEl = card.querySelector('[data-role="cmdHistory"]');
  if (!historyEl) return;
  try {
    const cmds = await requestJson(`/api/v1/commands/${encodeURIComponent(gatewayId)}`);
    if (!cmds || !cmds.length) {
      historyEl.innerHTML = '<em style="opacity:0.5">No commands yet</em>';
      return;
    }
    const statusBadge = (s) => {
      const color = s === 'success' ? '#28a745' : s === 'failed' ? '#dc3545' : s === 'delivered' ? '#007bff' : '#6c757d';
      return `<span style="background:${color};color:#fff;padding:1px 7px;border-radius:10px;font-size:0.78em">${s}</span>`;
    };
    historyEl.innerHTML = `<table style="width:100%;font-size:0.82em;border-collapse:collapse">
      <thead><tr style="border-bottom:1px solid #444">
        <th style="text-align:left;padding:3px 6px">ID</th>
        <th style="text-align:left;padding:3px 6px">Type</th>
        <th style="text-align:left;padding:3px 6px">Ver</th>
        <th style="text-align:left;padding:3px 6px">Status</th>
        <th style="text-align:left;padding:3px 6px">Created</th>
      </tr></thead>
      <tbody>${cmds.slice(0, 10).map(c => `<tr style="border-bottom:1px solid #333">
        <td style="padding:3px 6px;font-family:monospace;font-size:0.85em">${(c.commandId || c.command_id || '').slice(-16)}</td>
        <td style="padding:3px 6px">${c.type || '-'}</td>
        <td style="padding:3px 6px">${c.version || '-'}</td>
        <td style="padding:3px 6px">${statusBadge(c.status || 'unknown')}</td>
        <td style="padding:3px 6px">${c.createdAt || c.created_at ? new Date(c.createdAt || c.created_at).toLocaleString() : '-'}</td>
      </tr>`).join('')}</tbody>
    </table>`;
  } catch (e) {
    historyEl.innerHTML = `<em style="color:#dc3545">Failed to load: ${e.message}</em>`;
  }
}

async function loadDashboard(options = {}) {
  const trainNo = trainNoValue();
  if (lastLoadedTrainNo !== trainNo) {
    state.selectedTrip = null;
  }
  if (!trainNo) {
    setStatus('Enter train number', 'error');
    renderGatewayCards(dashboardGatewayIds);
    return;
  }
  if (!options.silent) setStatus('Loading');
  try {
    const data = await requestJson(`/api/v1/trains/${encodeURIComponent(trainNo)}/dashboard`);
    const [rmsPoints, mapAlerts] = await Promise.all([
      requestJson(`/api/v1/map/rms?train_id=${encodeURIComponent(trainNo)}`).catch(() => []),
      requestJson(`/api/v1/map/alerts?train_id=${encodeURIComponent(trainNo)}`).catch(() => []),
    ]);
    data.rmsPoints = rmsPoints;
    data.mapAlerts = mapAlerts;
    state.rmsPoints = rmsPoints;
    state.mapAlerts = mapAlerts;
    renderDashboard(data);
    await renderTrainPosition(trainNo);
    rememberTrainNumber(data.train?.trainNo || trainNo);
    lastLoadedTrainNo = trainNo;
    startAutoRefresh();
    setStatus('Live', 'ok');
  } catch (error) {
    setStatus('Error', 'error');
    setHtml('gatewayList', `<p class="error-text">${error.message}</p>`);
  }
}

async function resetSession() {
  const trainNo = trainNoValue();
  if (!trainNo) {
    alert('Please load a train first.');
    return;
  }

  // Step 1: ask for admin password
  const adminPwd = prompt(`Enter admin password to reset session for train ${trainNo}:`);
  if (adminPwd === null) return; // user cancelled
  if (!adminPwd.trim()) {
    alert('Admin password is required.');
    return;
  }

  // Step 2: confirm
  if (!confirm(`⚠️ This will reset the session for train ${trainNo} and send a RESET command to all its gateways.\n\nProceed?`)) return;

  try {
    const data = await requestJson('/api/v1/sessions/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trainNo, adminPassword: adminPwd }),
    });

    const cmds = data.resetCommands || [];
    const cmdText = cmds.length
      ? `Reset commands queued for:\n${cmds.map(c => `  • ${c.gatewayId} → ${c.commandId}`).join('\n')}`
      : 'No gateways found for this train (commands not queued).';

    setText('resetOutput',
      `✅ ${data.message}\n\n${cmdText}\n\nGateways will receive the reset command on next heartbeat.`
    );
    setStatus('Reset', 'ok');
    await loadDashboard();
  } catch (error) {
    setStatus('Error', 'error');
    setText('resetOutput', `❌ Reset failed: ${error.message}`);
  }
}

function selectTab(tabId) {
  localStorage.setItem('activeTab', tabId);
  document.querySelectorAll('.tab').forEach((button) => button.classList.toggle('active', button.dataset.tab === tabId));
  document.querySelectorAll('.panel').forEach((panel) => panel.classList.toggle('active', panel.id === tabId));
  if (tabId === 'alerts') {
    setTimeout(() => {
      Object.values(maps).forEach((map) => map?.invalidateSize());
    }, 120);
  }
  if (tabId === 'logs') loadLogs();
  if (tabId === 'users') loadUsersView();
  if (tabId === 'rolling_stock_graph') {
    const currentTrain = trainNoValue();
    const graphRidEl = document.getElementById('graphRid');
    if (currentTrain && graphRidEl && !graphRidEl.value) {
      graphRidEl.value = currentTrain;
    }
  }
}

async function applyRoleBasedAccess() {
  try {
    const res = await fetch('/api/v1/auth/me');
    if (res.ok) {
      const data = await res.json();
      
      const usernameEl = document.getElementById('displayUsername');
      if (usernameEl && data.username) {
        usernameEl.textContent = data.username.toUpperCase();
      }

      // Hide tabs immediately on load based on role/permissions
      const userRole = (data.role || 'operator').toLowerCase();
      const perms = data.permissions || {};
      
      const swaggerBtn = document.getElementById('swaggerBtn') || document.querySelector('a[href="/docs"]');
      if (swaggerBtn) swaggerBtn.style.display = (userRole === 'admin') ? '' : 'none';
      
      const usersLink = document.getElementById('dropdownUsersLink');
      if (usersLink) usersLink.style.display = perms.can_manage_users ? '' : 'none';

      document.querySelectorAll('.tab').forEach((button) => {
        const tabId = button.dataset.tab;
        if (['calibration', 'archives', 'reset', 'logs'].includes(tabId) && userRole !== 'admin') {
          button.style.display = 'none';
        } else if (tabId === 'users' && !perms.can_manage_users) {
          button.style.display = 'none';
        } else if (tabId === 'alerts' && !perms.can_view_alerts) {
          button.style.display = 'none';
        }
      });
    }
  } catch (err) {
    console.error('Failed to fetch user role', err);
  }
}

function boot() {
  applyRoleBasedAccess();
  initializeMaps();
  buildCalibrationCards();
  updateGatewaySelector({});
  renderRecentTrainNumbers();
  setStatus('Live', 'ok');
  $('searchBtn')?.addEventListener('click', loadDashboard);
  $('dashboardGateway')?.addEventListener('change', () => {
    
    if (state.dashboard) renderDashboard(state.dashboard);
    refreshVisibleMaps(180);
    if (lastLoadedTrainNo) renderTrainPosition(lastLoadedTrainNo);
  });
  $('trainNo')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') loadDashboard();
  });
  $('loadAllCalibrationBtn')?.addEventListener('click', loadAllCalibration);
  $('resetBtn')?.addEventListener('click', resetSession);
  $('cleanupBtn')?.addEventListener('click', cleanupData);
  $('loadLogsBtn')?.addEventListener('click', loadLogs);
  document.querySelectorAll('.tab').forEach((button) => button.addEventListener('click', () => {
    
    selectTab(button.dataset.tab);
  }));
  $('filterZone')?.addEventListener('change', (e) => {
    state.filterZone = e.target.value || null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
  $('filterDivision')?.addEventListener('change', (e) => {
    state.filterDivision = e.target.value || null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
  $('filterSection')?.addEventListener('change', (e) => {
    state.filterSection = e.target.value || null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
  $('filterLevel')?.addEventListener('change', (e) => {
    state.filterLevel = e.target.value || null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });

  $('mapDateFilter')?.addEventListener('change', () => {
    state.selectedDateFilter = $('mapDateFilter').value || null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
  $('mapDateTodayBtn')?.addEventListener('click', () => {
    const d = new Date();
    const todayStr = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    $('mapDateFilter').value = todayStr;
    state.selectedDateFilter = todayStr;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
  $('mapDateYesterdayBtn')?.addEventListener('click', () => {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    const yesterdayStr = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    $('mapDateFilter').value = yesterdayStr;
    state.selectedDateFilter = yesterdayStr;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
  $('mapDateAllBtn')?.addEventListener('click', () => {
    $('mapDateFilter').value = '';
    state.selectedDateFilter = null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });

  renderGatewayCards(dashboardGatewayIds);
  initializeReports();
  
}

window.addEventListener('error', (event) => {
  logClientEvent('javascript_error', { errorMessage: `${event.message} at ${event.filename}:${event.lineno}` });
});

window.addEventListener('unhandledrejection', (event) => {
  logClientEvent('promise_error', { errorMessage: String(event.reason?.message || event.reason || 'Unhandled promise rejection') });
});


// =====================================================================
// REPORTING MODULES IMPLEMENTATION
// =====================================================================
const APP_CONSTANTS = {
  DATE: {
    DEFAULT_DAYS_BACK: 60,
    MAX_RANGE_DAYS: 365
  },
  API: {
    CONTENT_TYPE_JSON: "application/json"
  },
  TABLE: {
    EMPTY_VALUE: "-"
  },
  ERROR_MESSAGES: {
    EXPORT_CSV_ERROR: "Failed to export CSV file.",
    EXPORT_EXCEL_ERROR: "Failed to export Excel file."
  }
};

globalThis.ApiClient = Object.freeze({
  async get(url) {
    const response = await fetch(url);
    return handleResponse(response);
  },
  async post(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": APP_CONSTANTS.API.CONTENT_TYPE_JSON
      },
      body: JSON.stringify(payload)
    });
    return handleResponse(response);
  }
});

async function handleResponse(response) {
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    const message = data?.detail || data?.message || "Server Error";
    throw new Error(message);
  }
  return data;
}

globalThis.DateUtils = Object.freeze({
  formatDateTimeLocal,
  formatDisplayDateTime,
  formatDisplayDate,
  initializeDefaultDates,
  applyDateRangeConstraints,
  validateDateRange
});

function initializeDefaultDates(fromId, toId) {
  const fromInput = $(fromId);
  const toInput = $(toId);
  if (!fromInput || !toInput) return;
  
  const now = new Date();
  const fromDate = new Date(now);
  fromDate.setDate(now.getDate() - APP_CONSTANTS.DATE.DEFAULT_DAYS_BACK);
  
  fromInput.value = formatDateTimeLocal(fromDate);
  toInput.value = formatDateTimeLocal(now);
}

function applyDateRangeConstraints(fromId, toId) {
  const fromInput = $(fromId);
  const toInput = $(toId);
  if (!fromInput || !toInput) return;
  
  const fromValue = fromInput.value;
  if (!fromValue) return;
  
  const fromDate = new Date(fromValue);
  const maxDate = new Date(fromDate);
  maxDate.setDate(maxDate.getDate() + APP_CONSTANTS.DATE.MAX_RANGE_DAYS);
  
  toInput.min = formatDateTimeLocal(fromDate);
  toInput.max = formatDateTimeLocal(maxDate);
  
  if (toInput.value && new Date(toInput.value) > maxDate) {
    toInput.value = formatDateTimeLocal(maxDate);
  }
}

function validateDateRange(fromId, toId) {
  const fromInput = $(fromId);
  const toInput = $(toId);
  if (!fromInput || !toInput) return false;
  
  const fromValue = fromInput.value;
  const toValue = toInput.value;
  if (!fromValue || !toValue) return false;
  
  const fromDate = new Date(fromValue);
  const toDate = new Date(toValue);
  const diffDays = (toDate - fromDate) / (1000 * 60 * 60 * 24);
  
  return toDate >= fromDate && diffDays <= APP_CONSTANTS.DATE.MAX_RANGE_DAYS;
}

function formatDateTimeLocal(date) {
  const offset = date.getTimezoneOffset();
  const localDate = new Date(date.getTime() - offset * 60000);
  return localDate.toISOString().slice(0, 16);
}

function formatDisplayDateTime(dateString) {
  if (!dateString) return "-";
  const date = new Date(dateString);
  if (isNaN(date.getTime())) return dateString;
  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const year = date.getFullYear();
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${day}-${month}-${year} ${hours}:${minutes}:${seconds}`;
}

function formatDisplayDate(dateString) {
  if (!dateString) return "-";
  if (typeof dateString === 'string' && /^\d{2}-\d{2}-\d{4}$/.test(dateString)) {
    return dateString;
  }
  const date = new Date(dateString);
  if (isNaN(date.getTime())) return dateString;
  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const year = date.getFullYear();
  return `${day}-${month}-${year}`;
}

globalThis.ExportUtils = Object.freeze({
  downloadBlob(blob, fileName) {
    const url = globalThis.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    globalThis.URL.revokeObjectURL(url);
  },
  downloadCsv(csvContent, fileName) {
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    this.downloadBlob(blob, fileName);
  },
  extractFilename(response, fallbackName) {
    const disposition = response.headers.get("Content-Disposition");
    if (!disposition) return fallbackName;
    const match = /filename="?([^"]+)"?/.exec(disposition);
    return match?.[1] || fallbackName;
  },
  async downloadResponse(response, fallbackName) {
      if (!response.ok) { const text = await response.text(); alert('Error downloading file: ' + text); throw new Error(text); }
      const blob = await response.blob();
    const fileName = this.extractFilename(response, fallbackName);
    this.downloadBlob(blob, fileName);
  }
});

globalThis.TableSorter = (function () {
  function sort(records, options = {}) {
    const { direction = "asc", extractor } = options;
    if (!Array.isArray(records)) return [];
    if (typeof extractor !== "function") return [...records];
    return [...records].sort((left, right) => {
      const a = extractor(left);
      const b = extractor(right);
      return compareValues(a, b, direction);
    });
  }

  function compareValues(a, b, direction) {
    const nullResult = compareNulls(a, b);
    if (nullResult !== null) return nullResult;
    const type = detectType(a, b);
    let result = 0;
    switch (type) {
      case "number":
        result = Number(a) - Number(b);
        break;
      case "datetime":
        result = parseDateTime(a) - parseDateTime(b);
        break;
      case "date":
        result = parseDate(a) - parseDate(b);
        break;
      case "time":
        result = parseTime(a) - parseTime(b);
        break;
      default:
        result = String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
    }
    return direction === "desc" ? result * -1 : result;
  }

  function compareNulls(a, b) {
    const emptyA = isEmpty(a);
    const emptyB = isEmpty(b);
    if (emptyA && emptyB) return 0;
    if (emptyA) return 1;
    if (emptyB) return -1;
    return null;
  }

  function isEmpty(value) {
    if (value == null) return true;
    const normalized = String(value).trim().toLowerCase();
    return (
      normalized === "" ||
      normalized === " " ||
      normalized === "-" ||
      normalized === "null" ||
      normalized === "n/a" ||
      normalized === "feedback not updated" ||
      normalized === "action not taken"
    );
  }

  function detectType(a, b) {
    const sample = a ?? b;
    if (sample == null) return "string";
    if (typeof sample === "number") return "number";
    const value = String(sample).trim();
    if (/^-?\d+(\.\d+)?$/.test(value)) return "number";
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)) return "datetime";
    if (/^\d{2}:\d{2}(:\d{2})?$/.test(value)) return "time";
    if (/^\d{2}[-/]\d{2}[-/]\d{4}$/.test(value)) return "date";
    if (/^\d{2}[-/]\d{2}[-/]\d{4}\s+/.test(value)) return "datetime";
    return "string";
  }

  function parseDate(value) {
    const parts = value.replaceAll("/", "-").split("-");
    return new Date(parts[2], parts[1] - 1, parts[0]).getTime();
  }

  function parseTime(value) {
    const parts = value.split(":");
    return Number(parts[0]) * 3600 + Number(parts[1]) * 60 + Number(parts[2] || 0);
  }

  function parseDateTime(value) {
    if (value.includes("T")) return new Date(value).getTime();
    return new Date(value.replace(/^(\d{2})-(\d{2})-(\d{4})/, "$3-$2-$1")).getTime();
  }

  return { sort };
})();

globalThis.AlarmLogSort = (function () {
  let currentSort = {
    field: "alarmDate",
    direction: "desc",
  };
  return {
    getCurrentSort: () => currentSort,
    toggleSort: (field) => {
      if (currentSort.field === field) {
        currentSort.direction = currentSort.direction === "asc" ? "desc" : "asc";
      } else {
        currentSort.field = field;
        currentSort.direction = "asc";
      }
    },
    resetSort: () => {
      currentSort = { field: "alarmDate", direction: "desc" };
    },
    applySorting: (rows) => {
      const sort = currentSort;
      return TableSorter.sort(rows, {
        direction: sort.direction,
        extractor: (row) => row?.[sort.field]
      });
    }
  };
})();

globalThis.ValidationUtils = Object.freeze({
  isBlank: (value) => value == null || String(value).trim() === "",
  validateRequired(fields) {
    for (const [fieldName, value] of Object.entries(fields)) {
      if (this.isBlank(value)) {
        return { valid: false, message: `${fieldName} is required.` };
      }
    }
    return { valid: true, message: null };
  },
  validateRid(rid) {
    if (this.isBlank(rid)) {
      return { valid: false, message: "RID is required." };
    }
    return { valid: true, message: null };
  },
  validateDateRange(fromDateId, toDateId) {
    const valid = DateUtils.validateDateRange(fromDateId, toDateId);
    if (!valid) {
      return { valid: false, message: `Date range cannot exceed ${APP_CONSTANTS.DATE.MAX_RANGE_DAYS} days.` };
    }
    return { valid: true, message: null };
  }
});

let repeatedAlarmsData = [];
let allRows = [];
let currentRows = [];

async function loadRepeatedAlarmReport() {
  const rid = $('repRidInput').value.trim();
  if (!rid) {
    alert("Please enter a Train No");
    return;
  }
  const fromDate = $('repFromDate').value;
  const toDate = $('repToDate').value;
  try {
    const data = await ApiClient.post('/api/reports/repeated-alarm/load', { rid, fromDate, toDate });
    if ($('repTotalStocksCard')) $('repTotalStocksCard').textContent = data.totalRollingStocks ?? 0;
    repeatedAlarmsData = data.rows || [];
    renderRepeatedAlarmsTable(repeatedAlarmsData);
  } catch (error) {
    alert("Repeated Alarm Load Error: " + error.message);
  }
}

function renderRepeatedAlarmsTable(rows) {
  const tbody = $('repeatedAlarmsTableBody');
  if (!tbody) return;
  const filtered = rows;
  
  tbody.innerHTML = filtered.length ? filtered.map(row => {
    const locLink = row.location && row.location !== '-'
      ? `<a href="javascript:void(0)" onclick="focusLocationOnMap(${row.location})" style="color: #0d6efd; text-decoration: underline;">${escapeHtml(row.location)}</a>`
      : '-';
    return `
      <tr>
        <td><strong>${escapeHtml(row.rid)}</strong></td>
        <td>${row.count}</td>
        <td>${locLink}</td>
        <td>
          <div style="position: relative; display: inline-block;">
            <button class="dropdown-action-btn" onclick="toggleRowDropdown(event, '${row.rid}')"><i class="bi bi-eye"></i> View <i class="bi bi-chevron-down"></i></button>
            <div class="export-menu" id="dropdown-${row.rid}" style="top: 28px; min-width: 120px;">
              <button onclick="openAlarmLogFor('${row.rid}')" style="font-size:12px; padding:8px 12px; font-weight:600; text-align:left;">Alarm Log</button>
            </div>
          </div>
        </td>
      </tr>
    `;
  }).join('') : '<tr><td colspan="4">No results found.</td></tr>';
}

function toggleRowDropdown(event, rid) {
  event.stopPropagation();
  document.querySelectorAll('.export-menu').forEach(el => {
    if (el.id !== `dropdown-${rid}`) el.classList.remove('show');
  });
  const dropdown = $(`dropdown-${rid}`);
  if (dropdown) dropdown.classList.toggle('show');
}

function openAlarmLogFor(rid) {
  $('ridInput').value = rid;
  $('fromDate').value = $('repFromDate').value;
  $('toDate').value = $('repToDate').value;
  selectTab('alarm_log_reports');
}

async function loadAlarmLogReport() {
  const rid = $('ridInput').value.trim();
  if (!rid) {
    alert("Please enter a Train No");
    return;
  }
  const request = {
    rid: rid,
    fromDate: $('fromDate').value,
    toDate: $('toDate').value,
    alarmType: $('alarmTypeFilter').value,
    feedbackStatus: null
  };
  try {
    const data = await ApiClient.post('/api/reports/alarm-log/load', request);
    renderSummary(data.summary);
    renderCurrentResultSet();
    renderBanner(data);
    
    allRows = data.rows || [];
    currentRows = AlarmLogSort.applySorting([...allRows]);
    updateSortIndicators();
    renderTable(currentRows);
    
    $('summarySection').style.display = "block";
    $('tableSection').style.display = "block";
    $('exportToolbar').style.display = "block";
  } catch (error) {
    alert("Alarm Log Load Error: " + error.message);
  }
}

function renderSummary(summary) {
  if ($('totalAlarmCard')) $('totalAlarmCard').textContent = summary.totalAlarmCount ?? 0;
  if ($('criticalAlarmCard')) $('criticalAlarmCard').textContent = summary.criticalAlarmCount ?? 0;
  if ($('WarningAlarmCard')) $('WarningAlarmCard').textContent = summary.WarningAlarmCount ?? 0;
  if ($('normalAlarmCard')) $('normalAlarmCard').textContent = summary.normalAlarmCount ?? 0;
}

function renderCurrentResultSet() {
  $('currentResultSection').style.display = "block";
  const rid = $('ridInput').value.trim() || "ALL";
  const alarmType = $('alarmTypeFilter').value || "ALL";
  const fromDate = $('fromDate').value;
  const toDate = $('toDate').value;
  
  $('currentRid').textContent = rid;
  $('currentAlarmType').textContent = alarmType;
  $('currentDateRange').textContent = `${DateUtils.formatDisplayDateTime(fromDate)} → ${DateUtils.formatDisplayDateTime(toDate)}`;
}

function renderBanner(data) {
  const banner = $('recordBanner');
  if (!banner) return;
  if (data.recordsTruncated) {
    banner.style.display = "block";
    banner.innerHTML = `Displaying first ${data.rows.length} records out of ${data.totalRecords} records. You may continue browsing these records or export the complete dataset using CSV or Excel.`;
  } else {
    banner.style.display = "none";
  }
}

function renderTable(rows) {
  const tbody = $('alarmLogTableBody');
  if (!tbody) return;
  tbody.innerHTML = rows.length ? rows.map(row => {
    const locLink = row.location && row.location !== '-'
      ? `<a href="javascript:void(0)" onclick="focusLocationOnMap(${row.location})" style="color: #0d6efd; text-decoration: underline;">${escapeHtml(row.location)}</a>`
      : '-';
    return `
      <tr>
        <td>${DateUtils.formatDisplayDate(row.alarmDate)}</td>
        <td>${row.alarmTime}</td>
        <td>${escapeHtml(row.machineName)}</td>
        <td><strong>${escapeHtml(row.train)}</strong></td>
        <td>${locLink}</td>
      </tr>
    `;
  }).join('') : '<tr><td colspan="5">No results found.</td></tr>';
}

function refreshTable() {
  currentRows = [...allRows];
  currentRows = AlarmLogSort.applySorting(currentRows);
  renderTable(currentRows);
}

async function exportRepeatedAlarmCsv() {
  const rid = $('repRidInput').value.trim();
  if (!rid) { alert('Please enter a Train No'); return; }
  const payload = { rid, fromDate: $('repFromDate').value, toDate: $('repToDate').value };
  try {
    const response = await fetch('/api/reports/repeated-alarm/export/csv', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    await ExportUtils.downloadResponse(response, "RepeatedAlarms.csv");
  } catch (error) {
    alert("Failed to export Repeated Alarms CSV.");
  }
}

async function exportRepeatedAlarmExcel() {
  const rid = $('repRidInput').value.trim();
  if (!rid) { alert('Please enter a Train No'); return; }
  const payload = { rid, fromDate: $('repFromDate').value, toDate: $('repToDate').value };
  try {
    const response = await fetch('/api/reports/repeated-alarm/export/excel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    await ExportUtils.downloadResponse(response, "RepeatedAlarms.xls");
  } catch (error) {
    alert("Failed to export Repeated Alarms Excel.");
  }
}

async function exportRepeatedAlarmPdf() {
  const rid = $('repRidInput').value.trim();
  if (!rid) { alert('Please enter a Train No'); return; }
  const payload = { rid, fromDate: $('repFromDate').value, toDate: $('repToDate').value };
  try {
    const response = await fetch('/api/reports/repeated-alarm/export/pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    await ExportUtils.downloadResponse(response, "RepeatedAlarms.pdf");
  } catch (error) {
    alert("Failed to export Repeated Alarms PDF.");
  }
}

async function exportCsv() {
  const rid = $('ridInput').value.trim();
  if (!rid) { alert('Please enter a Train No'); return; }
  const payload = {
    rid: rid,
    fromDate: $('fromDate').value,
    toDate: $('toDate').value,
    alarmType: $('alarmTypeFilter').value,
    feedbackStatus: null
  };
  try {
    const response = await fetch('/api/reports/alarm-log/export/csv', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    await ExportUtils.downloadResponse(response, "AlarmLog.csv");
  } catch (error) {
    alert("Failed to export Alarm Log CSV.");
  }
}

async function exportExcel() {
  const rid = $('ridInput').value.trim();
  if (!rid) { alert('Please enter a Train No'); return; }
  const payload = {
    rid: rid,
    fromDate: $('fromDate').value,
    toDate: $('toDate').value,
    alarmType: $('alarmTypeFilter').value,
    feedbackStatus: null
  };
  try {
    const response = await fetch('/api/reports/alarm-log/export/excel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    await ExportUtils.downloadResponse(response, "AlarmLog.xls");
  } catch (error) {
    alert("Failed to export Alarm Log Excel.");
  }
}

async function exportPdf() {
  const rid = $('ridInput').value.trim();
  if (!rid) { alert('Please enter a Train No'); return; }
  const payload = {
    rid: rid,
    fromDate: $('fromDate').value,
    toDate: $('toDate').value,
    alarmType: $('alarmTypeFilter').value,
    feedbackStatus: null
  };
  try {
    const response = await fetch('/api/reports/alarm-log/export/pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    await ExportUtils.downloadResponse(response, "AlarmLog.pdf");
  } catch (error) {
    alert("Failed to export Alarm Log PDF.");
  }
}

function showFeedbackModal(alertId, enrouteDiagnosis, enrouteAction, depotDiagnosis) {
  $('feedbackAlertId').value = alertId;
  $('feedbackEnrouteDiagnosis').value = enrouteDiagnosis === 'Feedback Not Updated' ? '' : enrouteDiagnosis;
  $('feedbackEnrouteAction').value = enrouteAction === 'Action Not Taken' ? '' : enrouteAction;
  $('feedbackDepotDiagnosis').value = depotDiagnosis === 'Feedback Not Updated' ? '' : depotDiagnosis;
  $('feedbackModal').classList.remove('hidden');
}

function closeFeedbackModal() {
  $('feedbackModal').classList.add('hidden');
}

async function submitFeedback() {
  const alertId = $('feedbackAlertId').value;
  const payload = {
    enrouteDiagnosis: $('feedbackEnrouteDiagnosis').value.trim() || 'Feedback Not Updated',
    enrouteAction: $('feedbackEnrouteAction').value.trim() || 'Action Not Taken',
    depotDiagnosis: $('feedbackDepotDiagnosis').value.trim() || 'Feedback Not Updated'
  };
  try {
    await ApiClient.post(`/api/reports/alerts/${alertId}/feedback`, payload);
    closeFeedbackModal();
    loadAlarmLogReport();
  } catch (error) {
    alert("Failed to update feedback: " + error.message);
  }
}

function initializeTableSorting() {
  document.querySelectorAll("#alarmLogTable th[data-field]").forEach((header) => {
    header.classList.add("sortable-header");
    header.addEventListener("click", () => {
      const field = header.dataset.field;
      AlarmLogSort.toggleSort(field);
      updateSortIndicators();
      refreshTable();
    });
  });
}

function updateSortIndicators() {
  document.querySelectorAll("#alarmLogTable th[data-field]").forEach((header) => {
    header.classList.remove("sort-asc", "sort-desc");
  });
  const sort = AlarmLogSort.getCurrentSort();
  const activeHeader = document.querySelector(`#alarmLogTable th[data-field="${sort.field}"]`);
  if (activeHeader) {
    activeHeader.classList.add(sort.direction === "asc" ? "sort-asc" : "sort-desc");
  }
}

function initializeReports() {
  DateUtils.initializeDefaultDates("repFromDate", "repToDate");
  DateUtils.initializeDefaultDates("fromDate", "toDate");
  
  DateUtils.applyDateRangeConstraints("repFromDate", "repToDate");
  DateUtils.applyDateRangeConstraints("fromDate", "toDate");
  
  $('repFromDate')?.addEventListener("change", () => DateUtils.applyDateRangeConstraints("repFromDate", "repToDate"));
  $('repToDate')?.addEventListener("change", () => DateUtils.applyDateRangeConstraints("repFromDate", "repToDate"));
  $('fromDate')?.addEventListener("change", () => DateUtils.applyDateRangeConstraints("fromDate", "toDate"));
  $('toDate')?.addEventListener("change", () => DateUtils.applyDateRangeConstraints("fromDate", "toDate"));
  
  $('repLoadReportBtn')?.addEventListener("click", loadRepeatedAlarmReport);
  $('loadReportBtn')?.addEventListener("click", loadAlarmLogReport);
  
  
  
  $('repExportBtn')?.addEventListener("click", (event) => {
    event.stopPropagation();
    $('repExportMenu').classList.toggle('show');
  });
  
  const alarmLogExportBtn = document.querySelector('#exportToolbar .export-btn');
  const alarmLogExportMenu = document.querySelector('#exportToolbar .export-menu');
  alarmLogExportBtn?.addEventListener("click", (event) => {
    event.stopPropagation();
    alarmLogExportMenu?.classList.toggle('show');
  });
  
  document.addEventListener("click", () => {
    $('repExportMenu')?.classList.remove('show');
    alarmLogExportMenu?.classList.remove('show');
    document.querySelectorAll('.export-menu').forEach(el => {
      if (!el.id.startsWith('repExport') && !el.closest('#exportToolbar')) el.classList.remove('show');
    });
  });
  
  $('repExportCsvBtn')?.addEventListener("click", exportRepeatedAlarmCsv);
  $('repExportExcelBtn')?.addEventListener("click", exportRepeatedAlarmExcel);
  $('repExportPdfBtn')?.addEventListener("click", exportRepeatedAlarmPdf);
  $('exportCsvBtn')?.addEventListener("click", exportCsv);
  $('exportExcelBtn')?.addEventListener("click", exportExcel);
  $('exportPdfBtn')?.addEventListener("click", exportPdf);
  
  $('closeFeedbackModalBtn')?.addEventListener("click", closeFeedbackModal);
  $('submitFeedbackBtn')?.addEventListener("click", submitFeedback);
  
  DateUtils.initializeDefaultDates("graphFromDate", "graphToDate");
  DateUtils.applyDateRangeConstraints("graphFromDate", "graphToDate");
  $('graphFromDate')?.addEventListener("change", () => DateUtils.applyDateRangeConstraints("graphFromDate", "graphToDate"));
  $('graphToDate')?.addEventListener("change", () => DateUtils.applyDateRangeConstraints("graphFromDate", "graphToDate"));
  $('loadGraphBtn')?.addEventListener("click", loadGraphData);
  
  initializeTableSorting();
}

window.openAlarmLogFor = openAlarmLogFor;
window.toggleRowDropdown = toggleRowDropdown;
window.showFeedbackModal = showFeedbackModal;
window.focusLocationOnMap = focusLocationOnMap;

async function loadGraphData() {
  let rid = $('graphRid').value.trim();
  if (!rid) {
    alert("Please enter a Train No");
    return;
  }
  // Extract real ID before " - " if present
  const idPart = rid.split(" - ")[0].trim();
  if (/^\d{3}$/.test(idPart)) {
    rid = "TR_" + idPart;
  } else {
    rid = idPart;
  }
  const fromDate = $('graphFromDate').value;
  const toDate = $('graphToDate').value;
  const metric = $('graphMetricFilter').value;
  
  try {
    const data = await ApiClient.post('/api/reports/graph/load', { rid, fromDate, toDate, metric });
     if (!data.points || data.points.length === 0) {
      alert("No telemetry records found for this train and date range.");
      if (chartXInstance) { chartXInstance.destroy(); chartXInstance = null; }
      if (chartYInstance) { chartYInstance.destroy(); chartYInstance = null; }
      if (chartZInstance) { chartZInstance.destroy(); chartZInstance = null; }
      $('graphMetadataSection').style.display = "none";
      $('graphMainContent').style.display = "none";
      return;
    }
    
    $('metaRollingStockId').textContent = data.rollingStockId;
    $('metaGraphDateRange').textContent = `${DateUtils.formatDisplayDateTime(fromDate)} → ${DateUtils.formatDisplayDateTime(toDate)}`;
    
    $('graphMetadataSection').style.display = "block";
    $('graphMainContent').style.display = "grid";
    
    renderRollingStockChart(data);
  } catch (error) {
    alert("Load Graph Error: " + error.message);
  }
}

function createAxisChart(canvasId, titleId, titleText, labels, dataPoints, dataColor, speeds, thresholdRed, thresholdYellow, thresholdGreen, hasDistance, rawPoints) {
  const canvas = $(canvasId);
  if (!canvas) return null;
  const ctx = canvas.getContext('2d');
  
  const titleElem = $(titleId);
  if (titleElem) {
    titleElem.textContent = titleText;
    titleElem.style.color = dataColor;
  }
  
  const pointsCount = dataPoints.length;
  
  // High critical peaks marker style (red dots with white border)
  const pointRadii = dataPoints.map(val => (val >= thresholdRed ? 5 : 0));
  const pointBackgroundColors = dataPoints.map(val => (val >= thresholdRed ? '#ef4444' : 'transparent'));
  const pointBorderColors = dataPoints.map(val => (val >= thresholdRed ? '#ffffff' : 'transparent'));
  const pointBorderWidths = dataPoints.map(val => (val >= thresholdRed ? 2 : 0));
  
  const chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'G-Force',
          data: dataPoints,
          borderColor: dataColor,
          backgroundColor: 'rgba(255, 255, 255, 0.02)',
          yAxisID: 'y',
          tension: 0.3,
          borderWidth: 2.5,
          pointRadius: pointRadii,
          pointBackgroundColor: pointBackgroundColors,
          pointBorderColor: pointBorderColors,
          pointBorderWidth: pointBorderWidths,
          fill: false
        },
        {
          label: 'Speed (km/h)',
          data: speeds,
          borderColor: '#1f2937',
          backgroundColor: 'transparent',
          yAxisID: 'y1',
          tension: 0.3,
          borderWidth: 1.5,
          borderDash: [5, 5],
          pointRadius: 0,
          fill: false
        },
        {
          label: 'Critical Threshold',
          data: new Array(pointsCount).fill(thresholdRed),
          borderColor: '#ef4444',
          borderWidth: 1.5,
          borderDash: [4, 4],
          pointRadius: 0,
          yAxisID: 'y',
          fill: false
        },
        {
          label: 'Warning Threshold',
          data: new Array(pointsCount).fill(thresholdYellow),
          borderColor: '#f59e0b',
          borderWidth: 1.5,
          borderDash: [4, 4],
          pointRadius: 0,
          yAxisID: 'y',
          fill: false
        },
        {
          label: 'Normal Threshold',
          data: new Array(pointsCount).fill(thresholdGreen),
          borderColor: '#10b981',
          borderWidth: 1.5,
          borderDash: [4, 4],
          pointRadius: 0,
          yAxisID: 'y',
          fill: false
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: {
            color: '#1f2937',
            boxWidth: 10,
            font: { family: 'Outfit, Inter, sans-serif', size: 9 }
          }
        },
        zoom: {
          zoom: {
            drag: {
              enabled: true,
              backgroundColor: 'rgba(29, 112, 184, 0.25)',
              borderColor: 'rgba(29, 112, 184, 0.6)',
              borderWidth: 1
            },
            mode: 'x'
          }
        },
        tooltip: {
          callbacks: {
            afterLabel: function(context) {
              if (context.datasetIndex === 0) {
                const pt = rawPoints[context.dataIndex];
                return `Time: ${pt.timestamp}`;
              }
              return '';
            }
          }
        }
      },
      scales: {
        y: {
          type: 'linear',
          display: true,
          position: 'left',
          title: {
            display: true,
            text: 'G-Force (G)',
            color: '#1f2937',
            font: { family: 'Outfit, Inter, sans-serif', size: 9, weight: 'bold' }
          },
          grid: {
            color: 'rgba(73, 80, 87, 0.15)',
          },
          ticks: {
            color: '#1f2937',
            font: { family: 'Outfit, Inter, sans-serif', size: 8 }
          }
        },
        y1: {
          type: 'linear',
          display: true,
          position: 'right',
          title: {
            display: true,
            text: 'Speed (km/h)',
            color: '#1f2937',
            font: { family: 'Outfit, Inter, sans-serif', size: 9, weight: 'bold' }
          },
          grid: {
            drawOnChartArea: false,
          },
          ticks: {
            color: '#1f2937',
            font: { family: 'Outfit, Inter, sans-serif', size: 8 }
          }
        },
        x: {
          title: {
            display: true,
            text: hasDistance ? 'Distance along route (KM)' : 'Time of Log',
            color: '#1f2937',
            font: { family: 'Outfit, Inter, sans-serif', size: 9, weight: 'bold' }
          },
          grid: {
            color: 'rgba(73, 80, 87, 0.15)',
          },
          ticks: {
            color: '#1f2937',
            font: { family: 'Outfit, Inter, sans-serif', size: 8 }
          }
        }
      }
    }
  });

  canvas.addEventListener('dblclick', () => {
    if (chartInstance && typeof chartInstance.resetZoom === 'function') {
      chartInstance.resetZoom();
    }
  });

  return chartInstance;
}

function zoomChart(axis, amount) {
  const chart = axis === 'X' ? chartXInstance : axis === 'Y' ? chartYInstance : chartZInstance;
  if (chart && typeof chart.zoom === 'function') {
    chart.zoom(amount);
  }
}

function resetChartZoom(axis) {
  const chart = axis === 'X' ? chartXInstance : axis === 'Y' ? chartYInstance : chartZInstance;
  if (chart && typeof chart.resetZoom === 'function') {
    chart.resetZoom();
  }
}

window.zoomChart = zoomChart;
window.resetChartZoom = resetChartZoom;

function renderRollingStockChart(data) {
  if (chartXInstance) { chartXInstance.destroy(); chartXInstance = null; }
  if (chartYInstance) { chartYInstance.destroy(); chartYInstance = null; }
  if (chartZInstance) { chartZInstance.destroy(); chartZInstance = null; }

  const selectedAxisValue = $('graphAxisFilter').value;
  const prefix = selectedAxisValue.startsWith('al') ? 'al' : selectedAxisValue.startsWith('ar') ? 'ar' : 'bg';
  const metric = $('graphMetricFilter').value;

  let cumulativeDist = 0.0;
  const labels = [];
  for (let i = 0; i < data.points.length; i++) {
    const pt = data.points[i];
    if (i > 0) {
      const prev = data.points[i - 1];
      const p1 = prev.positionKm || 0.0;
      const p2 = pt.positionKm || 0.0;
      if (p1 > 0 || p2 > 0) {
        cumulativeDist = pt.positionKm;
      } else if (prev.latitude !== null && prev.longitude !== null && pt.latitude !== null && pt.longitude !== null) {
        const d = haversineDistance(prev.latitude, prev.longitude, pt.latitude, pt.longitude);
        cumulativeDist += d;
      }
    } else {
      cumulativeDist = pt.positionKm || 0.0;
    }
    labels.push(cumulativeDist);
  }
  
  const hasDistance = labels.some(v => v > 0);
  const formattedLabels = data.points.map((pt, idx) => {
    if (hasDistance) {
      return `${labels[idx].toFixed(3)} KM`;
    }
    return pt.timestamp.split(" ")[1] || `${idx + 1}`;
  });
  
  const speeds = data.points.map(p => p.speed);

  // Set up thresholds based on Peak vs RMS metric type
  const isPeak = (metric === "Peak");
  const thresholdRed = isPeak ? 8.0 : 4.0;
  const thresholdYellow = isPeak ? 5.0 : 2.5;
  const thresholdGreen = isPeak ? 2.0 : 1.0;

  // Extract X, Y, and Z data arrays
  const xData = data.points.map(p => p.axes[`${prefix}_x`] ?? 0.0);
  const yData = data.points.map(p => p.axes[`${prefix}_y`] ?? 0.0);
  const zData = data.points.map(p => p.axes[`${prefix}_z`] ?? 0.0);

  // Render X Axis Chart (Red line)
  chartXInstance = createAxisChart(
    'chartX', 'chartXTitle', `X Axis — ${metric} Acceleration (${prefix}_x)`,
    formattedLabels, xData, '#ef4444', speeds, thresholdRed, thresholdYellow, thresholdGreen, hasDistance, data.points
  );

  // Render Y Axis Chart (Green line)
  chartYInstance = createAxisChart(
    'chartY', 'chartYTitle', `Y Axis — ${metric} Acceleration (${prefix}_y)`,
    formattedLabels, yData, '#10b981', speeds, thresholdRed, thresholdYellow, thresholdGreen, hasDistance, data.points
  );

  // Render Z Axis Chart (Blue line)
  chartZInstance = createAxisChart(
    'chartZ', 'chartZTitle', `Z Axis — ${metric} Acceleration (${prefix}_z)`,
    formattedLabels, zData, '#3b82f6', speeds, thresholdRed, thresholdYellow, thresholdGreen, hasDistance, data.points
  );

  // Calculate alert counts for each axis separately
  let critX = 0, warnX = 0, normX = 0;
  let critY = 0, warnY = 0, normY = 0;
  let critZ = 0, warnZ = 0, normZ = 0;

  for (let i = 0; i < data.points.length; i++) {
    const pt = data.points[i];
    const x = pt.axes[`${prefix}_x`] ?? 0.0;
    const y = pt.axes[`${prefix}_y`] ?? 0.0;
    const z = pt.axes[`${prefix}_z`] ?? 0.0;
    
    // X Axis Alerts
    if (x >= thresholdRed) critX++;
    else if (x >= thresholdYellow) warnX++;
    else normX++;

    // Y Axis Alerts
    if (y >= thresholdRed) critY++;
    else if (y >= thresholdYellow) warnY++;
    else normY++;

    // Z Axis Alerts
    if (z >= thresholdRed) critZ++;
    else if (z >= thresholdYellow) warnZ++;
    else normZ++;
  }

  // Populate sidebar indicators
  setText('sbCriticalX', critX);
  setText('sbWarningX', warnX);
  setText('sbNormalX', normX);

  setText('sbCriticalY', critY);
  setText('sbWarningY', warnY);
  setText('sbNormalY', normY);

  setText('sbCriticalZ', critZ);
  setText('sbWarningZ', warnZ);
  setText('sbNormalZ', normZ);
}

function haversineDistance(lat1, lon1, lat2, lon2) {
  if (lat1 === undefined || lon1 === undefined || lat2 === undefined || lon2 === undefined) return 0;
  if (lat1 === null || lon1 === null || lat2 === null || lon2 === null) return 0;
  const R = 6371; // km
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = 
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * 
    Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

function focusLocationOnMap(lat, lon, gatewayId) {
  window.lastActiveTabBeforeMap = document.querySelector('.tab.active')?.dataset?.tab || 'alerts';
  selectTab('alerts');
  setTimeout(() => {
    const showAllBtn = document.getElementById('showAllMapsBtn');
    document.body.classList.add('fullscreen-map-mode');
    document.querySelectorAll('.map-card').forEach(card => {
      if (!gatewayId || card.getAttribute('data-map-gateway') === gatewayId) card.classList.remove('hidden');
      else card.classList.add('hidden');
    });
    if (showAllBtn) {
      showAllBtn.classList.remove('hidden'); showAllBtn.style.display = 'block';
      showAllBtn.innerHTML = '<i class="bi bi-x-circle"></i> Close Map';
      showAllBtn.onclick = window.closeFullscreenMap;
    }
    setTimeout(() => { Object.values(maps).forEach(m => { if(m) { m.invalidateSize(); m.setView([lat, lon], 17); } }); }, 200);
  }, 150);
}
window.focusLocationOnMap = focusLocationOnMap;


boot();
// ==========================================
// User Management Logic
// ==========================================
const usersTable = document.getElementById('usersTable');
const addUserBtn = document.getElementById('addUserBtn');
const userModal = document.getElementById('userModal');
const closeUserModal = document.getElementById('closeUserModal');
const userForm = document.getElementById('userForm');
let currentEditingUser = null;

async function loadUsersView() {
  if (!usersTable) return;
  try {
    const users = await requestJson('/api/v1/users');
    let html = '';
    users.forEach(u => {
      const perms = [];
      if (u.can_view_alerts) perms.push('View Alerts');
      if (u.can_configure_thresholds) perms.push('Calibration');
      if (u.can_manage_users) perms.push('Manage Users');
      
      const badgeClass = u.is_active ? 'badge-ok' : 'badge-warn';
      const statusText = u.is_active ? 'Active' : 'Inactive';
      
      html += `<tr>
        <td><strong>${escapeHtml(u.username)}</strong></td>
        <td><span class="badge" style="background:var(--border)">${u.role.toUpperCase()}</span></td>
        <td><span class="badge ${badgeClass}">${statusText}</span></td>
        <td style="font-size:12px; color:var(--text-muted)">${perms.join(', ') || 'None'}</td>
        <td>${new Date(u.created_at).toLocaleDateString()}</td>
        <td>
          <button class="secondary" onclick='openEditUser(${JSON.stringify(u)})' style="padding: 4px 8px; font-size: 12px;">Edit</button>
          ${u.username !== 'admin' ? `<button class="danger" onclick='deleteUser(${u.id}, "${escapeHtml(u.username)}")' style="padding: 4px 8px; font-size: 12px; margin-left:5px;">Delete</button>` : ''}
        </td>
      </tr>`;
    });
    usersTable.innerHTML = html || '<tr><td colspan="6">No users found.</td></tr>';
  } catch (err) {
    console.error('Failed to load users', err);
    usersTable.innerHTML = `<tr><td colspan="6" class="error-text">Failed to load users: ${err.message}</td></tr>`;
  }
}

if (addUserBtn) {
  addUserBtn.addEventListener('click', () => {
    currentEditingUser = null;
    document.getElementById('userModalTitle').textContent = 'Add User';
    userForm.reset();
    document.getElementById('userId').value = '';
    document.getElementById('userUsername').readOnly = false;
    document.getElementById('userPassword').required = true;
    userModal.style.display = 'block';
  });
}

if (closeUserModal) {
  closeUserModal.addEventListener('click', () => {
    userModal.style.display = 'none';
  });
}

window.addEventListener('click', (e) => {
  if (e.target === userModal) {
    userModal.style.display = 'none';
  }
});

if (userForm) {
  userForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('userId').value;
    const username = document.getElementById('userUsername').value.trim();
    const password = document.getElementById('userPassword').value;
    const role = document.getElementById('userRole').value;
    const canViewAlerts = document.getElementById('userPermViewAlerts').checked;
    const canConfigureThresholds = document.getElementById('userPermConfigureThresholds').checked;
    const canManageUsers = document.getElementById('userPermManageUsers').checked;
    const isActive = document.getElementById('userIsActive').checked;

    try {
      if (id) {
        // Update existing user
        const payload = {
          role,
          can_view_alerts: canViewAlerts,
          can_configure_thresholds: canConfigureThresholds,
          can_manage_users: canManageUsers,
          is_active: isActive
        };
        if (password) payload.password = password;

          if (currentEditingUser) {
            const noChanges = !password && 
              currentEditingUser.role.toLowerCase() === role &&
              currentEditingUser.can_view_alerts === canViewAlerts &&
              currentEditingUser.can_configure_thresholds === canConfigureThresholds &&
              currentEditingUser.can_manage_users === canManageUsers &&
              currentEditingUser.is_active === isActive;
              
            if (noChanges) {
              alert('No changes were made.');
              userModal.style.display = 'none';
              return;
            }
          }
        
        await fetch(`/api/v1/users/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        }).then(async res => {
          if (!res.ok) throw new Error(await res.text());
        });
        alert('User updated successfully');
      } else {
        // Create new user
        if (!password) throw new Error("Password is required for new users");
        await fetch('/api/v1/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username,
            password,
            role,
            can_view_alerts: canViewAlerts,
            can_configure_thresholds: canConfigureThresholds,
            can_manage_users: canManageUsers
          })
        }).then(async res => {
          if (!res.ok) throw new Error(await res.text());
        });
        alert('User created successfully');
      }
      userModal.style.display = 'none';
      loadUsersView();
    } catch (err) {
      alert(`Error saving user: ${err.message}`);
    }
  });
}

  window.openEditUser = function(user) {
    currentEditingUser = user;
    document.getElementById('userModalTitle').textContent = 'Edit User';
  userForm.reset();
  
  document.getElementById('userId').value = user.id;
  document.getElementById('userUsername').value = user.username;
  document.getElementById('userUsername').readOnly = true;
  document.getElementById('userPassword').required = false;
  document.getElementById('userRole').value = user.role.toLowerCase();
  
  document.getElementById('userPermViewAlerts').checked = user.can_view_alerts;
  document.getElementById('userPermConfigureThresholds').checked = user.can_configure_thresholds;
  document.getElementById('userPermManageUsers').checked = user.can_manage_users;
  document.getElementById('userIsActive').checked = user.is_active;
  
  userModal.style.display = 'block';
};

window.deleteUser = async function(id, username) {
  if (confirm(`Are you sure you want to delete user "${username}"? This cannot be undone.`)) {
    try {
      const res = await fetch(`/api/v1/users/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(await res.text());
      loadUsersView();
    } catch (err) {
      alert(`Error deleting user: ${err.message}`);
    }
  }
};

// ==========================================
// Synchronize Train Dropdowns
// ==========================================
(function() {
  const mainTrainInput = document.getElementById('trainNo');
  const ridInput = document.getElementById('ridInput');
  const graphRid = document.getElementById('graphRid');

  function syncTrainId(source) {
    if (!source) return;
    const val = source.value;
    if (source !== mainTrainInput && mainTrainInput && mainTrainInput.value !== val) mainTrainInput.value = val;
    if (source !== ridInput && ridInput && ridInput.value !== val) ridInput.value = val;
    if (source !== graphRid && graphRid && graphRid.value !== val) graphRid.value = val;
  }

  [mainTrainInput, ridInput, graphRid].forEach(input => {
    if (input) {
      input.addEventListener('input', (e) => syncTrainId(e.target));
      input.addEventListener('change', (e) => syncTrainId(e.target));
    }
  });
})();

// Close the profile dropdown if the user clicks outside of it
window.addEventListener('click', function(event) {
  if (!event.target.closest('.profile-dropdown')) {
    const dropdown = document.getElementById('profileDropdownContent');
    if (dropdown && dropdown.classList.contains('show')) {
      dropdown.classList.remove('show');
    }
  }
});

// Global function to handle alert summary card clicks
function filterByAlertLevel(level) {
  if (level === 'TOTAL' || state.filterLevel === level) {
    state.filterLevel = null;
  } else {
    state.filterLevel = level;
  }
  
  // Sync the dropdown if it exists so they stay consistent
  const dropdown = document.getElementById('filterLevel');
  if (dropdown) {
    dropdown.value = state.filterLevel || '';
  }

  // Update UI active states
  ['RED', 'YELLOW', 'GREEN', 'TOTAL'].forEach(lvl => {
    const card = document.getElementById('card-' + lvl);
    if (card) {
      if (state.filterLevel === lvl || (state.filterLevel === null && lvl === 'TOTAL')) {
        card.classList.add('card-active');
      } else {
        card.classList.remove('card-active');
      }
    }
  });

  // Re-render dashboard with new filter
  if (state.dashboard) {
    renderDashboard(state.dashboard);
  }
}
window.filterByAlertLevel = filterByAlertLevel;


async function loadHierarchyData(type, parentCode = null) {
  try {
    let url = `/api/v1/hierarchy/${type}`;
    if (parentCode) {
      const param = type === 'divisions' ? 'zone_code' : 'division_code';
      url += `?${param}=${encodeURIComponent(parentCode)}`;
    }
    const token = localStorage.getItem('uabams_token') || sessionStorage.getItem('uabams_token');
    const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
    const res = await fetch(url, { headers });
    if (!res.ok) return [];
    return await res.json();
  } catch (e) {
    console.error(`Failed to load ${type}`, e);
    return [];
  }
}

async function populateDropdown(selectId, type, parentCode = null, defaultText) {
  const select = document.getElementById(selectId);
  if (!select) return;
  select.innerHTML = `<option value="">${defaultText}</option>`;
  const data = await loadHierarchyData(type, parentCode);
  data.forEach(item => {
    const opt = document.createElement('option');
    opt.value = item.code;
    opt.textContent = item.name;
    select.appendChild(opt);
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const savedTab = localStorage.getItem('activeTab') || 'overview';
  selectTab(savedTab);
  initializeMaps();
  loadTrainList();

  window.closeFullscreenMap = function() {
    document.body.classList.remove('fullscreen-map-mode');
    document.querySelectorAll('.map-card').forEach(card => card.classList.remove('hidden'));
    const showAllBtn = document.getElementById('showAllMapsBtn');
    if (showAllBtn) showAllBtn.classList.add('hidden');
    selectTab(window.lastActiveTabBeforeMap || 'alerts');
    window.scrollTo({ top: 0 });
    setTimeout(() => {
      Object.values(maps).forEach((map) => {
        if (map) map.invalidateSize();
      });
    }, 200);
  };

  // Poll gateway status periodically
  setInterval(checkGatewayStatus, 30000);
});

document.addEventListener('DOMContentLoaded', async () => {
  await populateDropdown('filterZone', 'zones', null, 'All Zones');
  await populateDropdown('filterDivision', 'divisions', null, 'All Divisions');
  await populateDropdown('filterSection', 'sections', null, 'All Sections');
  
  document.getElementById('filterZone')?.addEventListener('change', async (e) => {
    const val = e.target.value;
    await populateDropdown('filterDivision', 'divisions', val || null, 'All Divisions');
    await populateDropdown('filterSection', 'sections', null, 'All Sections');
    state.filterZone = val || null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
  
  document.getElementById('filterDivision')?.addEventListener('change', async (e) => {
    const val = e.target.value;
    await populateDropdown('filterSection', 'sections', val || null, 'All Sections');
    state.filterDivision = val || null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
  
  document.getElementById('filterSection')?.addEventListener('change', (e) => {
    state.filterSection = e.target.value || null;
    if (state.dashboard) renderDashboard(state.dashboard);
  });
});
























