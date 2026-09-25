/* Race map — /race/<slug>/map/.

   Leaflet map with organizer-only live team positions (polled every 20 s)
   and on-demand per-team tracks (fetched on click, multi-select). Reads
   endpoint URLs from <script id="raceMapConfig"> — no inline template vars
   elsewhere in this file. */
(function () {
  "use strict";

  function readJsonObj(id) {
    var el = document.getElementById(id);
    if (!el) return {};
    try {
      var data = JSON.parse(el.textContent || "{}");
      return data && typeof data === "object" && !Array.isArray(data) ? data : {};
    } catch (e) {
      return {};
    }
  }

  var config = readJsonObj("raceMapConfig");
  if (!config.positionsUrl || !config.trackUrlTemplate) return;

  var mapEl = document.getElementById("rmMap");
  if (!mapEl) return;

  var POLL_INTERVAL_MS = 20000;
  var STALE_MS = 10 * 60 * 1000;

  function trackStyle(color) {
    return { color: color, weight: 3, opacity: 0.85 };
  }

  var DEFAULT_CENTER = [55.751244, 37.618423];
  var DEFAULT_ZOOM = 5;
  var COLOR_PALETTE = [
    "#2a5288", "#c75a26", "#02b875", "#d93644", "#8a4fd9",
    "#0f9db0", "#c9891e", "#5c6ac4", "#2f9e44", "#b0388a"
  ];

  /* ── DOM refs ─────────────────────────────────────────────── */
  var counterEl = document.getElementById("rmCounter");
  var searchEl = document.getElementById("rmSearch");
  var listEl = document.getElementById("rmTeamList");
  var emptyHintEl = document.getElementById("rmEmptyHint");

  /* ── Leaflet init ─────────────────────────────────────────── */
  var map = L.map(mapEl).setView(DEFAULT_CENTER, DEFAULT_ZOOM);

  // Tile URLs come from the config island: in production they point at the
  // nginx pull-through cache (/tiles/…, deploy/nginx.conf) so the map works
  // offline at a race site; local runserver overrides them with the direct
  // upstream URLs via MAP_TILE_URL_* env vars. Fallbacks keep a stale cached
  // page working. The gray errorTileUrl pixel renders an unwarmed tile in
  // offline mode as a clean gray cell instead of a broken image.
  var GRAY_TILE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGN49+4dAAWYAsspex20AAAAAElFTkSuQmCC";
  var tileUrls = config.tileUrls || {};

  var osmLayer = L.tileLayer(tileUrls.osm || "https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    errorTileUrl: GRAY_TILE,
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(map);

  var topoLayer = L.tileLayer(tileUrls.topo || "https://tile.opentopomap.org/{z}/{x}/{y}.png", {
    maxZoom: 17,
    errorTileUrl: GRAY_TILE,
    attribution: "&copy; OpenTopoMap (CC-BY-SA)"
  });

  L.control.layers({ "OpenStreetMap": osmLayer, "OpenTopoMap": topoLayer }).addTo(map);

  /* ── State ────────────────────────────────────────────────── */
  var teams = {}; // team_id -> latest position row
  var markers = {}; // team_id -> L.Marker
  var selected = {}; // team_id -> true
  var teamColors = {}; // team_id -> color
  var nextColorIdx = 0;
  // team_id -> { lines: [{line, installId}], bySessionKey, devices }
  // (a device entry carries its own `hidden` flag for the per-phone toggle)
  var tracks = {};
  var trackRequestId = {}; // team_id -> token of the most recently issued fetchTrack call
  var boundsFitted = false;
  var pollTimer = null;
  var pollInFlight = false;
  var searchQuery = "";

  function colorFor(teamId) {
    if (!teamColors[teamId]) {
      teamColors[teamId] = COLOR_PALETTE[nextColorIdx % COLOR_PALETTE.length];
      nextColorIdx += 1;
    }
    return teamColors[teamId];
  }

  // JSON-encoded so a "|" (or any other separator) inside install_id/segment_id
  // can't make two distinct pairs collide on the same string key — both are
  // opaque client-supplied strings with no charset restriction.
  function sessionKey(installId, segmentId) {
    if (!installId) return null;
    return JSON.stringify([installId, segmentId]);
  }

  function isStale(row) {
    if (!row || !row.received_at) return false;
    var receivedMs = new Date(row.received_at).getTime();
    return Date.now() - receivedMs > STALE_MS;
  }

  /* ── Markers ──────────────────────────────────────────────── */
  function markerIcon(row, teamId) {
    var classes = "rm-marker";
    if (isStale(row)) classes += " is-stale";
    if (selected[teamId]) classes += " is-selected";
    var style = selected[teamId] ? ' style="--rm-color:' + colorFor(teamId) + '"' : "";
    return L.divIcon({
      className: "",
      html: '<div class="' + classes + '"' + style + ">" + escapeHtml(row.number) + "</div>",
      iconSize: [26, 26],
      iconAnchor: [13, 13]
    });
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function renderMarkers() {
    Object.keys(teams).forEach(function (teamId) {
      var row = teams[teamId];
      if (row.lat == null || row.lon == null) {
        if (markers[teamId]) {
          map.removeLayer(markers[teamId]);
          delete markers[teamId];
        }
        return;
      }
      var latlng = [row.lat, row.lon];
      if (markers[teamId]) {
        markers[teamId].setLatLng(latlng);
        markers[teamId].setIcon(markerIcon(row, teamId));
      } else {
        var marker = L.marker(latlng, { icon: markerIcon(row, teamId) });
        marker.on("click", function () {
          toggleTeam(teamId);
        });
        marker.addTo(map);
        markers[teamId] = marker;
      }
    });
  }

  function updateEmptyHint() {
    var hasAny = Object.keys(teams).some(function (teamId) {
      var row = teams[teamId];
      return row.lat != null && row.lon != null;
    });
    emptyHintEl.hidden = hasAny;
  }

  function fitBoundsOnce() {
    if (boundsFitted) return;
    var latlngs = [];
    Object.keys(teams).forEach(function (teamId) {
      var row = teams[teamId];
      if (row.lat != null && row.lon != null) latlngs.push([row.lat, row.lon]);
    });
    if (latlngs.length) {
      map.fitBounds(latlngs, { padding: [40, 40], maxZoom: 14 });
      boundsFitted = true;
    }
  }

  /* ── Sidebar ──────────────────────────────────────────────── */
  function matchesSearch(row) {
    if (!searchQuery) return true;
    var haystack = (String(row.name || "") + " " + String(row.number || "")).toLowerCase();
    return haystack.indexOf(searchQuery) !== -1;
  }

  function renderSidebar() {
    var all = Object.keys(teams).map(function (id) {
      return teams[id];
    });
    var withPos = all.filter(function (row) {
      return row.lat != null && row.lon != null;
    });
    var withoutPos = all.filter(function (row) {
      return row.lat == null || row.lon == null;
    });

    counterEl.textContent = "трек шлют " + withPos.length + " из " + all.length;

    var html = "";
    html += renderGroup(withPos, null);
    if (withoutPos.length) {
      html += '<div class="rm-group-label">Не шлют трек</div>';
      html += renderGroup(withoutPos, "не шлёт");
    }
    if (!html) {
      html = '<div class="rm-empty">Нет команд.</div>';
    }
    // The 20 s poll rebuilds the list: keep keyboard focus on a device
    // checkbox across the rebuild instead of dropping it to <body>.
    var focused = document.activeElement;
    var focusSelector = null;
    if (focused && listEl.contains(focused) && focused.matches(".rm-device input")) {
      focusSelector =
        '.rm-device input[data-team-id="' + focused.getAttribute("data-team-id") +
        '"][data-device-idx="' + focused.getAttribute("data-device-idx") + '"]';
    }
    listEl.innerHTML = html;

    listEl.querySelectorAll(".rm-row").forEach(function (rowEl) {
      rowEl.addEventListener("click", function () {
        toggleTeam(rowEl.getAttribute("data-team-id"));
      });
    });
    listEl.querySelectorAll(".rm-device input").forEach(function (inputEl) {
      inputEl.addEventListener("change", function () {
        toggleDevice(
          inputEl.getAttribute("data-team-id"),
          Number(inputEl.getAttribute("data-device-idx"))
        );
      });
    });
    if (focusSelector) {
      var refocus = listEl.querySelector(focusSelector);
      if (refocus) refocus.focus();
    }
  }

  function pluralRu(n, forms) {
    var mod10 = n % 10;
    var mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return forms[0];
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
    return forms[2];
  }

  // Stats are those of track load, advanced by the fixes the positions poll
  // delivers (appendLivePoint). The poll carries only the team's single
  // newest fix, so while two phones both send, the one whose fix isn't the
  // newest gets no updates and its age can still grow: "N мин назад" is the
  // age of the newest fix *this page has seen* from that phone, and points
  // counts load-time raw points plus poll-delivered new fixes (a strictly
  // newer gps_time_ms — not every upload, not a re-delivered row).
  // last_gps_time_ms is the phone's clock (may run ahead), hence the clamp.
  function renderDevices(teamId) {
    var track = selected[teamId] ? tracks[teamId] : null;
    if (!track || track.devices.length < 2) return "";
    var now = Date.now();
    var items = track.devices.map(function (device, idx) {
      var ageMs = Math.max(0, now - device.last_gps_time_ms);
      var parts = ["Устр. " + escapeHtml(device.index)];
      if (device.platform) parts.push(escapeHtml(device.platform));
      parts.push(Math.floor(ageMs / 60000) + " мин назад");
      parts.push(device.points + " " + pluralRu(device.points, ["точка", "точки", "точек"]));
      var classes = "rm-device" + (ageMs > STALE_MS ? " is-stale" : "");
      var checked = device.hidden ? "" : " checked";
      return (
        '<label class="' + classes + '">' +
        '<input type="checkbox" data-team-id="' + escapeHtml(teamId) + '"' +
        ' data-device-idx="' + idx + '"' + checked + ">" +
        "<span>" + parts.join(" · ") + "</span>" +
        "</label>"
      );
    });
    return '<div class="rm-devices">' + items.join("") + "</div>";
  }

  function toggleDevice(teamId, idx) {
    var track = tracks[teamId];
    if (!track || !track.devices[idx]) return;
    var device = track.devices[idx];
    device.hidden = !device.hidden;
    track.lines.forEach(function (rec) {
      if (rec.installId !== device.install_id) return;
      if (device.hidden) {
        map.removeLayer(rec.line);
      } else {
        rec.line.addTo(map);
      }
    });
  }

  function renderGroup(rows, flagText) {
    return rows
      .filter(matchesSearch)
      .map(function (row) {
        var classes = "rm-row";
        if (row.lat == null) classes += " is-empty";
        if (selected[row.team_id]) classes += " is-selected";
        if (isStale(row)) classes += " is-stale";
        var style = selected[row.team_id] ? ' style="--rm-color:' + colorFor(row.team_id) + '"' : "";
        return (
          '<div class="' + classes + '" data-team-id="' + row.team_id + '"' + style + ">" +
          '<div class="rm-row-num">' + escapeHtml(row.number) + "</div>" +
          '<div class="rm-row-name">' + escapeHtml(row.name) + "</div>" +
          (flagText ? '<div class="rm-row-flag">' + flagText + "</div>" : "") +
          "</div>" +
          renderDevices(String(row.team_id))
        );
      })
      .join("");
  }

  /* ── Track selection ──────────────────────────────────────── */
  function toggleTeam(teamId) {
    teamId = String(teamId);
    if (selected[teamId]) {
      deselectTeam(teamId);
    } else {
      selectTeam(teamId);
    }
    renderMarkers();
    renderSidebar();
    restyleMarks();
  }

  function selectTeam(teamId) {
    selected[teamId] = true;
    colorFor(teamId);
    fetchTrack(teamId);
  }

  function deselectTeam(teamId) {
    delete selected[teamId];
    var track = tracks[teamId];
    if (track) {
      track.lines.forEach(function (rec) {
        map.removeLayer(rec.line);
      });
    }
    delete tracks[teamId];
  }

  function fetchTrack(teamId) {
    var requestId = (trackRequestId[teamId] || 0) + 1;
    trackRequestId[teamId] = requestId;
    var url = config.trackUrlTemplate.replace("{team_id}", teamId);
    fetch(url, { credentials: "same-origin" })
      .then(function (resp) {
        if (!resp.ok) throw new Error("track fetch failed");
        return resp.json();
      })
      .then(function (data) {
        // Bail if deselected, or if a newer fetchTrack call for this team
        // was issued after this one (a stale response arriving after a
        // deselect+reselect must not overlay a second, unreferenced set of
        // polylines on the map).
        if (!selected[teamId] || trackRequestId[teamId] !== requestId) return;
        drawTrack(teamId, data.segments || [], data.devices || []);
        renderSidebar();
      })
      .catch(function () {
        /* leave marker selected but without a drawn track on failure */
      });
  }

  /* Each segment carries its own (install_id, segment_id) identity, so the
     per-session polyline is looked up by that key — never assumed from
     array position — keeping live-poll appends attached to the correct
     phone's line even when a stale/short session sorts after a still-active
     one in the server's by-first-point-time segment order. */
  function drawTrack(teamId, segments, devices) {
    var existing = tracks[teamId];
    if (existing) {
      existing.lines.forEach(function (rec) {
        map.removeLayer(rec.line);
      });
    }
    var color = colorFor(teamId);
    var bySessionKey = {};
    var lines = segments.map(function (segment) {
      var line = L.polyline(segment.points, trackStyle(color)).addTo(map);
      bySessionKey[sessionKey(segment.install_id, segment.segment_id)] = line;
      return { line: line, installId: segment.install_id };
    });
    tracks[teamId] = { lines: lines, bySessionKey: bySessionKey, devices: devices };
  }

  function findDevice(track, installId) {
    for (var i = 0; i < track.devices.length; i++) {
      if (track.devices[i].install_id === installId) return track.devices[i];
    }
    return null;
  }

  function appendLivePoint(teamId, row) {
    var track = tracks[teamId];
    if (!track || row.lat == null || row.lon == null) return;
    var key = sessionKey(row.install_id, row.segment_id);
    if (!key) return;
    var device = findDevice(track, row.install_id);
    if (device) {
      // Freshness and the fix count are updated independently of the
      // polyline dedup below: a stationary phone keeps sending new fixes at
      // identical coordinates and must not turn stale. The positions poll
      // re-delivers the same row every 20 s until a new fix lands, so only a
      // strictly newer gps_time_ms counts as a new fix.
      if (row.gps_time_ms > device.last_gps_time_ms) {
        device.points += 1;
      }
      device.last_gps_time_ms = Math.max(device.last_gps_time_ms, row.gps_time_ms);
    } else {
      device = {
        install_id: row.install_id,
        index: track.devices.length + 1,
        platform: "",
        first_gps_time_ms: row.gps_time_ms,
        last_gps_time_ms: row.gps_time_ms,
        points: 1,
        hidden: false
      };
      track.devices.push(device);
    }
    var line = track.bySessionKey[key];
    if (line) {
      // Compare against the polyline's own last drawn vertex — the actual
      // rendered state — rather than separately-tracked bookkeeping, which
      // can go stale relative to what's on screen (e.g. a positions poll
      // resolving while a track fetch is still in flight) and can't tell
      // apart two fixes that legitimately share a gps_time_ms with
      // different coordinates (the backend's own thinning treats those as
      // distinct — see RaceMapTrackView._thin_session).
      var pts = line.getLatLngs();
      var last = pts.length ? pts[pts.length - 1] : null;
      if (last && last.lat === row.lat && last.lng === row.lon) {
        return; // same coordinates as what's already drawn — nothing to append
      }
    }
    if (!line) {
      var color = colorFor(teamId);
      line = L.polyline([], trackStyle(color));
      if (!device.hidden) line.addTo(map);
      track.bySessionKey[key] = line;
      track.lines.push({ line: line, installId: row.install_id });
    }
    line.addLatLng([row.lat, row.lon]);
  }

  /* ── Marks layer («Взятия КП») ────────────────────────────── */
  // All located checkpoint takes, fetched once on first toggle. Colored by
  // checkpoint (its own palette cursor — team colors are assigned lazily on
  // selection, so sharing one cursor would make mark colors depend on click
  // history). Verified takes are filled dots, unverified — hollow dashed.
  // When any team is selected, other teams' marks dim so the selected team's
  // takes read on top of its track.
  var marksToggleEl = document.getElementById("rmMarksToggle");
  var marksFiltersEl = document.getElementById("rmMarksFilters");
  var marksFilterEls = {
    verified: document.getElementById("rmMarksVerified"),
    unverified: document.getElementById("rmMarksUnverified"),
    nfc: document.getElementById("rmMarksNfc"),
    photo: document.getElementById("rmMarksPhoto")
  };
  var marksLayer = L.layerGroup();
  var markEntries = []; // [{row, marker}]
  var marksFetched = false;
  var cpColors = {}; // checkpoint_id -> color
  var cpNextColorIdx = 0;

  function colorForCp(checkpointId) {
    if (!cpColors[checkpointId]) {
      cpColors[checkpointId] = COLOR_PALETTE[cpNextColorIdx % COLOR_PALETTE.length];
      cpNextColorIdx += 1;
    }
    return cpColors[checkpointId];
  }

  function formatMarkTime(timeMs) {
    if (timeMs == null) return "—";
    var d = new Date(timeMs);
    if (isNaN(d.getTime())) return "—";
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }

  function markTooltip(row) {
    var cp = row.cp_number != null
      ? "КП " + escapeHtml(row.cp_number)
      : "КП? (id " + escapeHtml(row.checkpoint_id) + ")";
    var parts = [cp, formatMarkTime(row.time_ms)];
    if (row.accuracy != null) parts.push("±" + Math.round(row.accuracy) + " м");
    parts.push(escapeHtml(row.method) + (row.verified ? " ✓" : " ✗"));
    var team = row.team_number
      ? "№" + escapeHtml(row.team_number) + " " + escapeHtml(row.team_name)
      : escapeHtml(row.team_name);
    return parts.join(" · ") + "<br>" + "<b>" + team + "</b>";
  }

  function markStyle(row) {
    var anySelected = Object.keys(selected).length > 0;
    var isOwn = !!selected[row.team_id];
    var dim = anySelected && !isOwn;
    return {
      color: colorForCp(row.checkpoint_id),
      radius: isOwn ? 8 : 6,
      weight: 2,
      dashArray: row.verified ? null : "3,3",
      opacity: dim ? 0.15 : 0.9,
      fillOpacity: row.verified ? (dim ? 0.1 : 0.75) : 0
    };
  }

  function markPassesFilters(row) {
    if (!(row.verified ? marksFilterEls.verified.checked : marksFilterEls.unverified.checked)) {
      return false;
    }
    if (row.method === "nfc") return marksFilterEls.nfc.checked;
    if (row.method === "photo") return marksFilterEls.photo.checked;
    return true;
  }

  function applyMarksFilters() {
    markEntries.forEach(function (entry) {
      if (markPassesFilters(entry.row)) {
        marksLayer.addLayer(entry.marker);
      } else {
        marksLayer.removeLayer(entry.marker);
      }
    });
  }

  function restyleMarks() {
    // CircleMarker.setStyle applies `radius` too.
    markEntries.forEach(function (entry) {
      entry.marker.setStyle(markStyle(entry.row));
    });
  }

  function fetchMarks() {
    fetch(config.marksUrl, { credentials: "same-origin" })
      .then(function (resp) {
        if (!resp.ok) throw new Error("marks fetch failed");
        return resp.json();
      })
      .then(function (rows) {
        markEntries = rows
          .filter(function (row) {
            return row.lat != null && row.lon != null;
          })
          .map(function (row) {
            var marker = L.circleMarker([row.lat, row.lon], markStyle(row));
            marker.bindTooltip(markTooltip(row));
            return { row: row, marker: marker };
          });
        applyMarksFilters();
      })
      .catch(function () {
        // allow a retry on the next toggle
        marksFetched = false;
      });
  }

  if (marksToggleEl && config.marksUrl) {
    marksToggleEl.addEventListener("change", function () {
      if (marksToggleEl.checked) {
        marksFiltersEl.hidden = false;
        marksLayer.addTo(map);
        if (!marksFetched) {
          marksFetched = true;
          fetchMarks();
        }
      } else {
        marksFiltersEl.hidden = true;
        map.removeLayer(marksLayer);
      }
    });
    Object.keys(marksFilterEls).forEach(function (key) {
      marksFilterEls[key].addEventListener("change", applyMarksFilters);
    });
  } else if (marksToggleEl) {
    marksToggleEl.closest(".rm-marks-toggle").hidden = true;
  }

  /* ── Polling ──────────────────────────────────────────────── */
  function fetchPositions() {
    if (pollInFlight) return;
    pollInFlight = true;
    fetch(config.positionsUrl, { credentials: "same-origin" })
      .then(function (resp) {
        if (!resp.ok) throw new Error("positions fetch failed");
        return resp.json();
      })
      .then(function (rows) {
        rows.forEach(function (row) {
          teams[String(row.team_id)] = row;
        });
        renderMarkers();
        updateEmptyHint();
        fitBoundsOnce();
        Object.keys(selected).forEach(function (teamId) {
          appendLivePoint(teamId, teams[teamId]);
        });
        renderSidebar();
      })
      .catch(function () {
        /* keep last known state on transient failure */
      })
      .then(function () {
        pollInFlight = false;
      });
  }

  /* ── Wiring ───────────────────────────────────────────────── */
  searchEl.addEventListener("input", function () {
    searchQuery = searchEl.value.trim().toLowerCase();
    renderSidebar();
  });

  fetchPositions();
  pollTimer = setInterval(fetchPositions, POLL_INTERVAL_MS);
})();
