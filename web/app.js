// Simple LiveKit web UI (ES module)
// Import the LiveKit ESM bundle directly from jsDelivr (pin to a known good version).
// jsDelivr provides CORS headers for the ESM bundle.
import { Room } from 'https://cdn.jsdelivr.net/npm/livekit-client@2.18.1/dist/livekit-client.esm.mjs';

const connectBtn = document.getElementById('connectBtn');
const disconnectBtn = document.getElementById('disconnectBtn');
const serverUrlInput = document.getElementById('serverUrl');
const tenantIdInput = document.getElementById('tenantId');
const viewerIdInput = document.getElementById('viewerId');
const alertIdInput = document.getElementById('alertId');
const openEvidenceBtn = document.getElementById('openEvidenceBtn');
const evidenceUrlDiv = document.getElementById('evidenceUrl');
const statusDiv = document.getElementById('status');
const videosDiv = document.getElementById('videos');
const tracksDiv = document.getElementById('tracks');
const activeTrackDiv = document.getElementById('activeTrack');

let room = null;
let activeTrackKey = null;
const trackEntries = new Map();

function setStatus(s) { statusDiv.textContent = s; }

function setEvidenceText(text) {
  evidenceUrlDiv.textContent = text || '';
}

function setActiveTrackLabel(label) {
  activeTrackDiv.textContent = label || 'None';
}

function toParticipantList(collection) {
  if (!collection) return [];
  if (typeof collection.values === 'function') {
    return Array.from(collection.values());
  }
  if (Array.isArray(collection)) {
    return collection;
  }
  return [];
}

function makeTrackKey(track, publication, participant) {
  return publication?.trackSid || publication?.sid || track?.sid || `${getParticipantSid(participant, publication)}:${publication?.trackName || track?.name || 'track'}`;
}

function makeTrackLabel(track, publication, participant) {
  const participantLabel = participant?.identity || getParticipantSid(participant, publication);
  const trackLabel = publication?.trackName || track?.name || publication?.kind || 'track';
  return `${participantLabel} / ${trackLabel}`;
}

function renderTrackButtons() {
  tracksDiv.innerHTML = '';
  for (const [key, entry] of trackEntries.entries()) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = entry.label;
    button.style.padding = '6px 10px';
    button.style.border = key === activeTrackKey ? '2px solid #111' : '1px solid #bbb';
    button.style.background = key === activeTrackKey ? '#111' : '#fff';
    button.style.color = key === activeTrackKey ? '#fff' : '#111';
    button.addEventListener('click', () => selectTrack(key));
    tracksDiv.appendChild(button);
  }
}

function selectTrack(trackKey) {
  activeTrackKey = trackKey;
  for (const [key, entry] of trackEntries.entries()) {
    entry.el.style.display = key === trackKey ? '' : 'none';
  }
  setActiveTrackLabel(trackEntries.get(trackKey)?.label || 'None');
  renderTrackButtons();
}

async function getToken(tenantId, viewerId) {
  const resp = await fetch('/api/v1/livekit/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tenant_id: tenantId, viewer_id: viewerId || 'viewer' }),
  });
  if (!resp.ok) throw new Error('Token request failed: ' + resp.statusText);
  const data = await resp.json();
  return data.token;
}

function attachTrack(track, publication, participant) {
  try {
    const el = track.attach();
    const participantSid = getParticipantSid(participant, publication);
    const key = makeTrackKey(track, publication, participant);
    el.dataset.participant = participantSid;
    el.dataset.trackKey = key;
    el.dataset.trackName = publication?.trackName || track?.name || 'track';
    el.autoplay = true;
    el.playsInline = true;
    videosDiv.appendChild(el);

    const label = makeTrackLabel(track, publication, participant);
    trackEntries.set(key, { el, participantSid, label });
    if (!activeTrackKey) {
      selectTrack(key);
    } else {
      el.style.display = key === activeTrackKey ? '' : 'none';
      renderTrackButtons();
    }
  } catch (e) {
    console.warn('attach error', e);
  }
}

function getParticipantSid(participant, publication) {
  return participant?.sid || publication?.participant?.sid || publication?.participantSid || 'unknown';
}

function detachParticipant(participantSid) {
  const removedKeys = [];
  for (const [key, entry] of trackEntries.entries()) {
    if (entry.participantSid === participantSid) {
      entry.el.remove();
      trackEntries.delete(key);
      removedKeys.push(key);
    }
  }
  if (removedKeys.length > 0 && removedKeys.includes(activeTrackKey)) {
    const nextKey = trackEntries.keys().next().value || null;
    activeTrackKey = null;
    if (nextKey) {
      selectTrack(nextKey);
    } else {
      setActiveTrackLabel('None');
      renderTrackButtons();
    }
  }
}

async function openAlertEvidence() {
  const alertId = alertIdInput.value.trim();
  if (!alertId) {
    setStatus('Enter an alert id first');
    return;
  }

  setStatus('Loading alert evidence...');
  setEvidenceText('');

  try {
    const resp = await fetch(`/api/v1/alerts/${encodeURIComponent(alertId)}/evidence?expires_minutes=60`);
    if (!resp.ok) {
      throw new Error(`Evidence request failed: ${resp.status} ${resp.statusText}`);
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
      setStatus('Alert found, but no evidence URLs are stored');
      setEvidenceText('No snapshot or clip URL available for this alert');
      return;
    }

    setStatus(`Opened evidence for alert ${alertId}`);
    setEvidenceText(links.join(' | '));
  } catch (e) {
    setStatus(`Evidence load failed: ${e.message}`);
    setEvidenceText('');
  }
}

function handleParticipant(participant) {
  // subscribe to existing subscribed tracks
  toParticipantList(participant.tracks).forEach(pub => {
    if (pub.isSubscribed && pub.track) {
      attachTrack(pub.track, pub, participant);
    }
  });

  participant.on('trackSubscribed', (track, publication) => {
    attachTrack(track, publication, participant);
  });

  participant.on('trackUnsubscribed', (track, publication) => {
    // remove elements for this participant
    detachParticipant(getParticipantSid(participant, publication));
  });
}

connectBtn.addEventListener('click', async () => {
  const url = serverUrlInput.value;
  const tenant = tenantIdInput.value.trim();
  const viewer = viewerIdInput.value.trim() || 'viewer';

  setStatus('Requesting token...');
  let token;
  try {
    token = await getToken(tenant, viewer);
  } catch (e) {
    setStatus('Token request failed: ' + e.message);
    return;
  }

  setStatus('Connecting to LiveKit...');
  try {
    room = new Room();

    room.on('participantConnected', p => handleParticipant(p));
    room.on('participantDisconnected', p => detachParticipant(getParticipantSid(p)));
    room.on('trackSubscribed', (track, publication, participant) => {
      attachTrack(track, publication, participant);
    });
    room.on('trackUnsubscribed', (track, publication, participant) => {
      detachParticipant(getParticipantSid(participant, publication));
    });
    room.on('disconnected', () => {
      setStatus('Disconnected');
      disconnectBtn.disabled = true;
      connectBtn.disabled = false;
    });

    await room.connect(url, token);
    setStatus('Connected: ' + (room.localParticipant ? room.localParticipant.identity : 'local'));

    // existing participants
    toParticipantList(room.remoteParticipants || room.participants).forEach(handleParticipant);

    disconnectBtn.disabled = false;
    connectBtn.disabled = true;
  } catch (e) {
    console.error(e);
    setStatus('Connection failed: ' + e);
  }
});

disconnectBtn.addEventListener('click', () => {
  if (room) {
    room.disconnect();
    room = null;
    setStatus('Disconnected');
    disconnectBtn.disabled = true;
    connectBtn.disabled = false;
  }
});

openEvidenceBtn.addEventListener('click', openAlertEvidence);
