// Nexa LiveKit Multi-Camera Viewer
// Subscribes to one tenant room and renders ALL published video tracks in a grid.
// Each track tile is labelled by the publishing camera (track name = camera_id).

import { Room, RoomEvent, Track } from 'https://cdn.jsdelivr.net/npm/livekit-client@2.18.1/dist/livekit-client.esm.mjs';

// --- DOM refs ---
const connectBtn = document.getElementById('connectBtn');
const disconnectBtn = document.getElementById('disconnectBtn');
const serverUrlInput = document.getElementById('serverUrl');
const tenantIdInput = document.getElementById('tenantId');
const viewerIdInput = document.getElementById('viewerId');
const tokenInput = document.getElementById('tokenInput');
const alertIdInput = document.getElementById('alertId');
const openEvidenceBtn = document.getElementById('openEvidenceBtn');
const evidenceUrlDiv = document.getElementById('evidenceUrl');
const statusDiv = document.getElementById('status');
const grid = document.getElementById('grid');
const emptyState = document.getElementById('empty');

// --- State ---
let room = null;
// Key = `${participant.sid}:${publication.trackSid}`, Value = { tile, videoEl }
const tiles = new Map();

function setStatus(s) { statusDiv.textContent = s; }
function setEvidenceText(t) { evidenceUrlDiv.textContent = t || ''; }

function refreshEmptyState() {
  emptyState.style.display = tiles.size === 0 ? '' : 'none';
}

function makeTrackKey(participant, publication) {
  return `${participant?.sid || 'unknown'}:${publication?.trackSid || publication?.sid || 'track'}`;
}

function makeTrackLabel(publication, participant) {
  const cameraId = publication?.trackName || 'unknown_camera';
  const identity = participant?.identity || 'unknown_publisher';
  return { cameraId, identity };
}

// --- Tile creation / removal ---

function addTrack(track, publication, participant) {
  if (track.kind !== Track.Kind.Video) return;

  const key = makeTrackKey(participant, publication);
  if (tiles.has(key)) return;  // already attached

  const { cameraId, identity } = makeTrackLabel(publication, participant);

  const tile = document.createElement('div');
  tile.className = 'tile';

  const labelEl = document.createElement('div');
  labelEl.className = 'tile-label';
  labelEl.textContent = cameraId;
  tile.appendChild(labelEl);

  const videoEl = track.attach();
  videoEl.autoplay = true;
  videoEl.playsInline = true;
  videoEl.muted = true;
  tile.appendChild(videoEl);

  const metaEl = document.createElement('div');
  metaEl.className = 'tile-meta';
  metaEl.textContent = `participant: ${identity}`;
  tile.appendChild(metaEl);

  grid.appendChild(tile);
  tiles.set(key, { tile, videoEl, track });
  refreshEmptyState();
}

function removeTrack(track, publication, participant) {
  const key = makeTrackKey(participant, publication);
  const entry = tiles.get(key);
  if (!entry) return;

  try { entry.track?.detach(); } catch (e) { /* ignore */ }
  entry.tile.remove();
  tiles.delete(key);
  refreshEmptyState();
}

function removeAllTracksForParticipant(participantSid) {
  const toRemove = [];
  for (const [key, entry] of tiles.entries()) {
    if (key.startsWith(`${participantSid}:`)) {
      toRemove.push({ key, entry });
    }
  }
  for (const { key, entry } of toRemove) {
    try { entry.track?.detach(); } catch (e) { /* ignore */ }
    entry.tile.remove();
    tiles.delete(key);
  }
  refreshEmptyState();
}

function clearAllTiles() {
  for (const entry of tiles.values()) {
    try { entry.track?.detach(); } catch (e) { /* ignore */ }
    entry.tile.remove();
  }
  tiles.clear();
  refreshEmptyState();
}

// --- Token fetching ---

async function getToken(tenantId, viewerId) {
  // 1) If user pasted a token, use it directly.
  const pasted = tokenInput.value.trim();
  if (pasted) return pasted;

  // 2) Otherwise try the backend endpoint (may not exist in this deployment).
  const resp = await fetch('/api/v1/livekit/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tenant_id: tenantId, viewer_id: viewerId || 'viewer' }),
  });
  if (!resp.ok) {
    throw new Error(`Token endpoint not reachable (${resp.status}). Paste a subscriber token manually.`);
  }
  const data = await resp.json();
  return data.token;
}

// --- Room lifecycle ---

connectBtn.addEventListener('click', async () => {
  const url = serverUrlInput.value.trim();
  const tenant = tenantIdInput.value.trim();
  const viewer = viewerIdInput.value.trim() || 'viewer';

  if (!url || !tenant) {
    setStatus('Please fill server url and tenant id.');
    return;
  }

  setStatus('Requesting token...');
  let token;
  try {
    token = await getToken(tenant, viewer);
  } catch (e) {
    setStatus('Token error: ' + e.message);
    return;
  }

  setStatus(`Connecting to ${url} as ${viewer}...`);
  try {
    room = new Room({ adaptiveStream: true, dynacast: true });

    // Subscribe events for already-publishing participants and new arrivals.
    room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
      addTrack(track, publication, participant);
    });

    room.on(RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
      removeTrack(track, publication, participant);
    });

    room.on(RoomEvent.ParticipantDisconnected, (participant) => {
      removeAllTracksForParticipant(participant.sid);
    });

    room.on(RoomEvent.Disconnected, () => {
      setStatus('Disconnected');
      clearAllTiles();
      disconnectBtn.disabled = true;
      connectBtn.disabled = false;
    });

    await room.connect(url, token);

    setStatus(`Connected to room "${room.name}" as ${room.localParticipant?.identity || viewer}.`);
    disconnectBtn.disabled = false;
    connectBtn.disabled = true;

    // Attach any tracks already published before we joined.
    for (const participant of room.remoteParticipants.values()) {
      for (const publication of participant.trackPublications.values()) {
        if (publication.isSubscribed && publication.track) {
          addTrack(publication.track, publication, participant);
        }
      }
    }
  } catch (e) {
    console.error(e);
    setStatus('Connection failed: ' + (e?.message || e));
    if (room) {
      try { await room.disconnect(); } catch (_) { /* ignore */ }
      room = null;
    }
  }
});

disconnectBtn.addEventListener('click', async () => {
  if (room) {
    try { await room.disconnect(); } catch (_) { /* ignore */ }
    room = null;
  }
  clearAllTiles();
  setStatus('Disconnected');
  disconnectBtn.disabled = true;
  connectBtn.disabled = false;
});

// --- Alert evidence (best-effort; backend may not exist) ---

async function openAlertEvidence() {
  const alertId = alertIdInput.value.trim();
  if (!alertId) {
    setStatus('Enter an alert id first.');
    return;
  }

  setStatus(`Loading evidence for ${alertId}...`);
  setEvidenceText('');

  try {
    const resp = await fetch(`/api/v1/alerts/${encodeURIComponent(alertId)}/evidence?expires_minutes=60`);
    if (!resp.ok) {
      throw new Error(`Evidence endpoint not reachable (${resp.status}).`);
    }
    const data = await resp.json();
    const links = [];
    if (data.snapshot_url) {
      window.open(data.snapshot_url, '_blank', 'noopener,noreferrer');
      links.push(`snapshot: ${data.snapshot_url}`);
    }
    if (data.clip_url) {
      window.open(data.clip_url, '_blank', 'noopener,noreferrer');
      links.push(`clip: ${data.clip_url}`);
    }
    if (links.length === 0) {
      setStatus('Alert found, but no evidence URLs are stored.');
      setEvidenceText('No snapshot or clip URL available.');
      return;
    }
    setStatus(`Opened evidence for ${alertId}.`);
    setEvidenceText(links.join(' | '));
  } catch (e) {
    setStatus(`Evidence load failed: ${e.message}`);
    setEvidenceText('Tip: this needs a backend at /api/v1/alerts/<alert_id>/evidence. Open MinIO directly if no backend.');
  }
}

openEvidenceBtn.addEventListener('click', openAlertEvidence);

refreshEmptyState();
