'use strict';
const $ = id => document.getElementById(id);
let key = new URLSearchParams(location.hash.slice(1)).get('token') || '';
history.replaceState(null, '', location.pathname);
const emptyTemplate = $('transcript').firstElementChild.cloneNode(true);
let stopPending = false, authenticated = false, replayAvailable = true, recordingAvailable = false, recordPending = false;
let notes = new Map(), languageLocks = new Map();
const partials = new Map();
let socket, activeId = null, selectedId = null, sessions = new Map(), segments = new Map();
const liveStates = new Set(['loading', 'replaying', 'preparing', 'recording', 'stopping']);
let discovery = {status:'disabled', apps:[], microphones:[], candidates:[]};
let sourceRequest = 0, pendingSource = null, chosenSource = null;
let renderedCandidates = '', renderedApps = '', renderedMicrophones = '';
let microphoneNeedsChoice = false, connecting = false;
const historyNodes = new Map(), transcriptNodes = new Map();
let transcriptView = '';
function text(id, value) { if ($(id).textContent !== value) $(id).textContent = value; }
function scopeDescription(source) {
  if (!source?.scope) return 'Capture scope unavailable';
  if (source.scope === 'system') return 'All system audio · all apps';
  const kind = source.kind || discovery.apps.find(app=>app.id === source.app_id)?.kind;
  return kind === 'browser' ? `All tabs in ${source.name}` : `All audio from ${source.name || 'the selected app'}`;
}
function busy() { return Boolean(activeId) || recordPending; }
function formatDuration(ms) {
  const seconds = Math.max(0,Math.floor(ms/1000));
  return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
}
function send(value) {
  if (authenticated && socket?.readyState === WebSocket.OPEN) { socket.send(JSON.stringify(value)); return true; }
  return false;
}
function notice(text) { $('notice').textContent = text; $('notice').hidden = !text; }
function clearSource(message = 'Choose a source, then confirm it.') {
  pendingSource = null; chosenSource = null;
  $('source-status').textContent = message;
}
function chooseSource(candidate = null) {
  const value = $('meeting-source').value;
  if (busy() || !value || !authenticated || !$('meeting-microphone').value || pendingSource !== null) return;
  const command = {action:'select_source', request_id:++sourceRequest,
    scope:value === 'system' ? 'system' : 'application',
    app_id:value === 'system' ? null : value.slice(4),
    microphone_id:$('meeting-microphone').value,
    candidate_id:candidate?.id || null};
  if (send(command)) {
    pendingSource = command.request_id; chosenSource = null;
    $('source-status').textContent = 'Checking source selection…';
    renderDiscovery();
  }
}
function updateDiscovery(value) {
  discovery = value || {status:'disabled', apps:[], microphones:[], candidates:[]};
  const oldSource = $('meeting-source').value;
  const oldMic = $('meeting-microphone').value;
  const appsState = JSON.stringify(discovery.apps);
  const microphonesState = JSON.stringify(discovery.microphones);
  const option = (element,value,text) => {
    const item=document.createElement('option'); item.value=value; item.textContent=text; element.append(item);
  };
  if (appsState !== renderedApps) {
    renderedApps = appsState;
    $('meeting-source').replaceChildren();
    option($('meeting-source'),'','Choose an app or audio scope');
    discovery.apps.forEach(app => option($('meeting-source'),`app:${app.id}`,app.name + (app.kind === 'browser' ? ' · all browser audio' : '') + (app.running === false ? ' · not running' : '')));
    option($('meeting-source'),'system','All system audio · includes other apps');
  }
  const validSource = oldSource === 'system' || discovery.apps.some(app => `app:${app.id}` === oldSource);
  $('meeting-source').value = validSource ? oldSource : '';
  if (microphonesState !== renderedMicrophones) {
    renderedMicrophones = microphonesState;
    $('meeting-microphone').replaceChildren();
    option($('meeting-microphone'),'','Choose a microphone');
    discovery.microphones.forEach(mic => option($('meeting-microphone'),mic.id,mic.name + (mic.enumerated ? '' : ' · not checked')));
  }
  const missingMic = oldMic && !discovery.microphones.some(mic => mic.id === oldMic);
  if (missingMic) microphoneNeedsChoice = true;
  $('meeting-microphone').value = microphoneNeedsChoice ? '' :
    discovery.microphones.some(mic => mic.id === oldMic) ? oldMic : discovery.microphones[0]?.id || '';
  if (oldSource && !validSource) clearSource('The selected app is no longer available. Choose another source.');
  if (missingMic) clearSource('The selected microphone is unavailable. Confirm another microphone.');
  if (chosenSource?.candidate_id && (discovery.status !== 'available' || !discovery.candidates.some(candidate => candidate.id === chosenSource.candidate_id && candidate.selectable))) {
    clearSource('That suggestion is no longer current. Choose a source manually.');
  }
  renderDiscovery();
}
function renderDiscovery() {
  const processing = [...sessions.values()].some(s => s.source === 'meeting' && s.status === 'processing');
  const active = sessions.get(activeId);
  const locked = busy();
  const reason = !authenticated ? 'Reconnect to set up recording.' :
    !recordingAvailable ? 'Meeting recording requires the encrypted macOS app.' :
    activeId ? 'Stop the active session before starting another.' :
    processing ? 'Wait for local transcription to finish.' :
    recordPending ? 'Preparing your request…' :
    discovery.permission !== 'authorized' ? 'Enable microphone access before recording.' :
    !$('title').value.trim() ? 'Add a meeting title.' :
    !chosenSource ? 'Choose an audio source and microphone, then press Confirm source.' :
    !$('record-consent').checked ? 'Confirm participant permission above to enable Record.' :
    'Ready. Start your call, then press Record meeting.';
  $('record-meeting').disabled = !authenticated || !recordingAvailable || !chosenSource ||
    !$('record-consent').checked || locked || processing || discovery.permission !== 'authorized' || !$('title').value.trim();
  text('record-help',reason);
  text('record-meeting',recordPending ? 'Preparing…' : 'Record meeting ↗');
  $('recording-bar').hidden = authenticated && !activeId && !processing && !recordPending;
  text('recording-state', !authenticated ? connecting ? 'Connecting…' : activeId || processing ? 'Connection lost · recording status unverified' : 'Disconnected · reconnect to restore your notebook' :
    active ? (active.source === 'meeting' ? active.status.toUpperCase() : 'REPLAYING PUBLIC AUDIO') :
    processing ? 'TRANSCRIBING · audio capture stopped' : recordPending ? 'Preparing request…' : 'Recording off');
  text('elapsed-time', active && authenticated ? formatDuration(Date.now()-active.started_at) : '');
  text('active-scope', !authenticated ? 'Reconnect to verify capture status. Disconnecting this page does not stop an active recording.' : active?.source === 'meeting' ?
    `${scopeDescription(active.capture)} · ${active.capture?.microphone_name || 'Microphone identity unavailable'}` :
    active ? 'Public audio replay · microphone is not open' : 'Local processing · no new audio capture');
  $('stop').hidden = !activeId;
  $('stop').disabled = stopPending || !authenticated;
  text('stop',stopPending ? 'Stopping…' : active?.source === 'meeting' ? 'Stop recording' : 'Stop replay');
  $('reconnect').hidden = authenticated;
  $('reconnect').disabled = connecting;
  $('enable-microphone').hidden = !recordingAvailable || discovery.permission !== 'not_determined';
  $('enable-microphone').disabled = !authenticated || locked;
  text('microphone-permission', {
    authorized:'Microphone permission granted. Capture starts only when you press Record.',
    denied:'Microphone access is denied. In macOS System Settings → Privacy & Security → Microphone, enable the app that launched ScribeLoop.',
    restricted:'Microphone access is restricted. Check your macOS privacy settings.',
    not_determined:'Enable microphone access to record your side of the conversation.'
  }[discovery.permission] || 'Microphone permission has not been checked.');
  $('meeting-source').disabled = !authenticated || locked;
  $('meeting-microphone').disabled = !authenticated || locked;
  $('meeting-language').disabled = !authenticated || locked;
  $('title').disabled = locked;
  $('record-consent').disabled = !authenticated || locked;
  $('select-source').disabled = !authenticated || locked || pendingSource !== null || !$('meeting-source').value || !$('meeting-microphone').value;
  text('select-source', pendingSource !== null ? 'Checking source…' : 'Confirm source ↗');
  text('scope-warning', chosenSource ? `${scopeDescription(chosenSource)} will be captured, plus ${chosenSource.microphone_name || 'the selected microphone'}.` :
    'Confirm a source to see exactly what will be captured.');
  const statuses = {
    available:'Watching app audio activity. Suggestions may also be media playback or microphone tests.',
    unavailable:'Automatic discovery is unavailable. You can still choose a source manually.',
    disabled:'Automatic discovery is off. You can still choose a source manually.'
  };
  text('discovery-status', !authenticated ? 'Connect to see meeting sources.' : statuses[discovery.status] || statuses.unavailable);
  const candidateState=JSON.stringify([authenticated,locked,pendingSource !== null,!!$('meeting-microphone').value,discovery.status,discovery.candidates]);
  if (candidateState === renderedCandidates) return; // Keep keyboard focus during transcript updates.
  renderedCandidates=candidateState;
  $('meeting-candidates').replaceChildren();
  discovery.candidates.forEach(candidate => {
    const row=document.createElement('div'); row.className='meeting-candidate';
    const content=document.createElement('div');
    const title=document.createElement('strong'); title.textContent=candidate.name;
    const evidence=document.createElement('p');
    const signals=candidate.evidence.map(item => item === 'input_active' ? 'microphone activity' : 'output activity').join(' + ');
    evidence.textContent = candidate.kind === 'browser' ? `${signals} · meeting tab unknown` :
      `${signals} · ${candidate.confidence === 'ambiguous' ? 'unconfirmed activity' : 'possible call'}`;
    if (!candidate.selectable || discovery.status !== 'available') evidence.textContent += ' · activity no longer current';
    content.append(title,evidence);
    const actions=document.createElement('div'); actions.className='candidate-actions';
    const choose=document.createElement('button'); choose.type='button'; choose.textContent='Use source';
    choose.disabled=locked || !$('meeting-microphone').value || !authenticated || !candidate.selectable || discovery.status !== 'available' || pendingSource !== null;
    choose.onclick=() => { if (busy()) return; $('meeting-source').value=`app:${candidate.app_id}`; clearSource(); chooseSource(candidate); };
    const dismiss=document.createElement('button'); dismiss.type='button'; dismiss.textContent='Dismiss';
    dismiss.disabled=!authenticated;
    dismiss.onclick=() => send({action:'dismiss_candidate',candidate_id:candidate.id});
    actions.append(choose,dismiss); row.append(content,actions); $('meeting-candidates').append(row);
  });
}
function renderHistory() {
  const rows = [...sessions.values()].sort((a,b)=>b.started_at-a.started_at);
  const current = new Set(rows.map(row=>row.id));
  for (const [id,node] of historyNodes) if (!current.has(id)) { node.button.remove(); historyNodes.delete(id); }
  rows.forEach((session,index)=>{
    let node = historyNodes.get(session.id);
    if (!node) {
      const button=document.createElement('button'); button.type='button';
      const title=document.createElement('span'), meta=document.createElement('small');
      button.append(title,meta);
      button.onclick=()=>{ selectedId=session.id; render(); };
      node={button,title,meta}; historyNodes.set(session.id,node);
    }
    if (node.title.textContent !== session.title) node.title.textContent=session.title;
    const when=new Date(session.started_at).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
    const duration=session.source === 'meeting' ? session.audio_duration_ms : session.ended_at ? session.ended_at-session.started_at : null;
    const detail=`${when} · ${session.capture?.name || (session.source === 'meeting' ? 'Meeting' : 'Replay')} · ${session.status}${duration != null ? ' · '+formatDuration(duration)+' audio' : ''}`;
    if (node.meta.textContent !== detail) node.meta.textContent=detail;
    node.button.classList.toggle('selected',session.id === selectedId);
    node.button.setAttribute('aria-current',session.id === selectedId ? 'true' : 'false');
    if ($('history').children[index] !== node.button) $('history').insertBefore(node.button,$('history').children[index] || null);
  });
}
function renderTranscript(session, rows, emptyMessage) {
  const signature=JSON.stringify([selectedId,emptyMessage,rows]);
  if (signature === transcriptView) return;
  transcriptView=signature;
  if (!rows.length) {
    transcriptNodes.clear();
    if (!session) $('transcript').replaceChildren(emptyTemplate.cloneNode(true));
    else { const p=document.createElement('p'); p.className='partial'; p.textContent=emptyMessage; $('transcript').replaceChildren(p); }
    return;
  }
  const desired=new Set(rows.map(row=>`${selectedId}:${row.id}`));
  for (const [id,node] of transcriptNodes) if (!desired.has(id)) { node.article.remove(); transcriptNodes.delete(id); }
  for (const child of [...$('transcript').children]) if (![...transcriptNodes.values()].some(node=>node.article === child)) child.remove();
  rows.forEach((row,index)=>{
    const id=`${selectedId}:${row.id}`;
    let node=transcriptNodes.get(id);
    if (!node) { const article=document.createElement('article'); article.className='segment';
      const time=document.createElement('span'); time.className='timestamp';
      const p=document.createElement('p'); article.append(time,p); node={article,time,p}; transcriptNodes.set(id,node); }
    const timestamp=formatDuration(row.start_ms);
    if (node.time.textContent !== timestamp) node.time.textContent=timestamp;
    const value=session?.source === 'meeting' ? `${row.stream === 'mic' ? 'Microphone' : 'Meeting audio'}: ${row.text}` : row.text;
    if (node.p.textContent !== value) node.p.textContent=value;
    if ($('transcript').children[index] !== node.article) $('transcript').insertBefore(node.article,$('transcript').children[index] || null);
  });
}

function render() {
  renderDiscovery();
  renderHistory();
  const session = sessions.get(selectedId);
  text('new-session',activeId ? 'View active session' : '＋ New meeting');
  $('session-heading').textContent = session ? session.title : 'The conversation';
  $('session-status').textContent = session ? session.status.toUpperCase() : 'READY WHEN YOU ARE';
  $('activity-dot').classList.toggle('active', Boolean(activeId));
  $('start').disabled = Boolean(activeId) || !authenticated || !replayAvailable || !$('fixture').value;
  $('partial').textContent = partials.get(selectedId) || '';
  if (!replayAvailable) $('session-status').textContent = 'STORAGE UNAVAILABLE';
  if (activeId) $('start').textContent = sessions.get(activeId)?.source === 'meeting' ? 'Meeting recording active' : sessions.get(activeId)?.status === 'loading' ? 'Loading model…' : 'Replay in progress';
  else $('start').textContent = 'Start replay ↗';
  const languageLock = languageLocks.get(selectedId);
  text('language-status', languageLock ? `${languageLock.mode === 'auto' ? 'Detected' : 'Selected'}: ${languageLock.language === 'vi' ? 'Tiếng Việt' : 'English'} · fixed for this conversation` :
    session?.source === 'meeting' ? 'Automatic mode detects early speech once after Stop, before transcription.' : '');
  text('meeting-notes',notes.get(selectedId) || '');
  $('notes-section').hidden = !notes.get(selectedId);
  $('reprocess-controls').hidden = session?.source !== 'meeting';
  $('reprocess-meeting').hidden = session?.source !== 'meeting';
  $('reprocess-meeting').disabled = !authenticated || !recordingAvailable || Boolean(activeId) || recordPending ||
    [...sessions.values()].some(s=>s.source === 'meeting' && s.status === 'processing');
  $('reprocess-language').disabled = $('reprocess-meeting').disabled;
  text('session-progress', session?.source === 'meeting' ? session.status === 'processing' ?
    'Transcribing locally. Previous text stays visible until the new pass completes.' :
    session.status === 'preparing' ? 'Preparing encrypted storage and selected audio sources…' :
    session.status === 'stopping' ? 'Stopping capture and finishing the encrypted audio…' :
    session.status === 'recording' ? 'Recording audio. Your transcript appears after Stop.' :
    session.status === 'failed' ? 'Recording or transcription failed. Check the message above; saved audio remains encrypted.' : '' : '');
  const rows = [...(segments.get(selectedId) || [])].sort((a,b)=>a.start_ms-b.start_ms);
  const emptyMessage = !replayAvailable ? 'Storage is unavailable. Reconnect or restart the local server.' :
    session?.status === 'processing' ? 'Audio capture stopped. Transcribing locally…' :
    session?.source === 'meeting' && liveStates.has(session.status) ? 'Recording audio. Words appear after Stop.' :
    session?.status === 'failed' ? 'This session failed. Check the message above.' :
    liveStates.has(session?.status) ? 'Listening for the first confirmed words…' : 'No speech has been transcribed in this session.';
  renderTranscript(session,rows,rows.length ? '' : emptyMessage);

}
function connect() {
  if (connecting || authenticated) return;
  if (!key) { if (!$('unlock').open) $('unlock').showModal(); return; }
  connecting=true; $('reconnect').disabled=true;
  $('connection').textContent = 'Connecting…';
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  const connection=socket;
  socket.onopen = () => { if (socket === connection) connection.send(key); };
  socket.onmessage = event => {
    if (socket !== connection) return;
    const data = JSON.parse(event.data);
    if (data.type === 'snapshot') {
      connecting=false; notice('');
      authenticated = true; recordingAvailable = data.recording_available === true; recordPending=false;
      notes = new Map(Object.entries(data.notes || {}));
      languageLocks = new Map(Object.entries(data.language_locks || {}));
      replayAvailable = data.replay_available !== false; partials.clear();
      clearSource(); updateDiscovery(data.discovery);
      $('storage-status').textContent = {encrypted:'Encrypted on this machine', 'public-demo':'Public demo · unencrypted', memory:'Temporary memory'}[data.storage] || 'Storage status unavailable';
      $('connection').textContent = '● Connected locally';
      if (!replayAvailable) notice('Replay storage is unavailable. Restart the local server.');
      sessions = new Map(data.sessions.map(s => [s.id,s]));
      segments = new Map(Object.entries(data.segments)); activeId = data.active_id;
      selectedId = activeId || (sessions.has(selectedId) ? selectedId : data.sessions[0]?.id) || null;
      $('fixture').replaceChildren();
      data.fixtures.forEach(f => { const option = document.createElement('option'); option.value=f.id; option.textContent=`${f.id} · ${Math.round(f.duration_s)}s`; $('fixture').append(option); });
      if (!data.fixtures.length) $('start').disabled=true;
      render();
    } else if (data.type === 'discovery') {
      updateDiscovery(data.discovery);
    } else if (data.type === 'source_selected') {
      if (data.request_id !== pendingSource) return;
      if (data.source.candidate_id && (discovery.status !== 'available' ||
          !discovery.candidates.some(candidate => candidate.id === data.source.candidate_id && candidate.selectable))) {
        clearSource('That suggestion is no longer current. Choose a source manually.'); renderDiscovery(); return;
      }
      pendingSource=null; chosenSource=data.source;
      $('source-status').textContent=`Selected: ${data.source.name} + ${data.source.microphone_name || 'system default microphone'}. No recording started.`;
      renderDiscovery();
    } else if (data.type === 'language_locked') {
      languageLocks.set(data.session_id,data); render();
    } else if (data.type === 'transcript_replaced') {
      segments.set(data.session_id,data.segments); text('transcript-update','Transcript replaced with the completed pass.'); render();
    } else if (data.type === 'notes') {
      notes.set(data.session_id,data.notes); render();
    } else if (data.type === 'recording_error') {
      recordPending=false; notice(data.message); render();
    } else if (data.type === 'session') {
      if (data.session.source === 'meeting') { recordPending=false; $('record-consent').checked=false; }
      sessions.set(data.session.id,data.session);
      if (liveStates.has(data.session.status)) { const newActive=activeId !== data.session.id; activeId=data.session.id; if (newActive || !selectedId) selectedId=activeId; }
      else { if (activeId === data.session.id) { activeId=null; stopPending=false; } partials.delete(data.session.id); }
      render();
    } else if (data.type === 'segment') {
      const rows=segments.get(data.session_id)||[];
      if (!rows.some(r => r.id === data.segment.id)) rows.push(data.segment);
      segments.set(data.session_id,rows); partials.delete(data.session_id);
      if (selectedId === data.session_id) text('transcript-update',`${rows.length} confirmed transcript segments available.`); render();
    } else if (data.type === 'partial') {
      partials.set(data.session_id, data.segment.text);
      if (selectedId === data.session_id) $('partial').textContent=data.segment.text;
    } else if (data.type === 'replay_unavailable') {
      replayAvailable=false; activeId=null; stopPending=false; partials.clear(); notice(data.message); render();
    } else if (data.type === 'error') {
      if (data.context === 'meeting') {
        recordPending=false; notice(data.message);
        if (data.request_id === pendingSource || data.request_id == null) { clearSource(data.message); }
        renderDiscovery();
      } else { stopPending=false; notice(data.message); render(); }
    }
    else if (data.type === 'reconnect_required') notice('The connection fell behind. Reconnecting restores saved text.');
  };
  socket.onclose = event => {
    if (socket !== connection) return;
    connecting=false; authenticated=false; stopPending=false; recordPending=false; partials.clear();
    clearSource('Connection lost. Reconnect and choose the source again.'); render();
    $('connection').textContent='Disconnected';
    if (event.code === 1008) { key=''; notice('Launch key rejected. Enter the current key from your server terminal.'); if (!$('unlock').open) $('unlock').showModal(); }
    else notice('Connection lost. Recording may continue. Press Reconnect to restore status and Stop controls.');
  };
  socket.onerror = () => { if (socket === connection) notice('Could not connect to the local server. Check that it is running, then press Reconnect.'); };
  renderDiscovery();
}
$('unlock-form').onsubmit = () => { key=$('launch-key').value.trim(); $('launch-key').value=''; setTimeout(connect,0); };
$('replay-form').onsubmit = event => { event.preventDefault(); notice(''); send({action:'start',fixture:$('fixture').value,title:$('replay-title').value,consent_confirmed:$('consent').checked}); };
$('stop').onclick = () => { stopPending=send({action:'stop',session_id:activeId}); render(); };
$('new-session').onclick = () => { if (activeId) { selectedId=activeId; render(); return; } selectedId=null; $('title').value='Meeting conversation'; $('record-consent').checked=false; $('partial').textContent=''; notice(''); render(); $('title').focus(); };
$('fixture').onchange = render;
$('source-form').onsubmit = event => { event.preventDefault(); chooseSource(); };
$('meeting-source').onchange = () => { if (busy()) return; clearSource(); renderDiscovery(); };
$('meeting-microphone').onchange = () => { if (busy()) return; microphoneNeedsChoice = !$('meeting-microphone').value; clearSource(); renderDiscovery(); };
renderDiscovery();
connect();

$('record-consent').onchange = renderDiscovery;
$('record-meeting').onclick = () => {
  if ($('record-meeting').disabled || !chosenSource) return;
  recordPending=send({action:'record',request_id:++sourceRequest,scope:chosenSource.scope,
    app_id:chosenSource.app_id,microphone_id:chosenSource.microphone_id,
    candidate_id:null,title:$('title').value.trim() || 'Meeting test',
    consent_confirmed:$('record-consent').checked,language:$('meeting-language').value || 'auto'});
  renderDiscovery();
};

$('enable-microphone').onclick = () => send({action:'microphone_permission'});

setInterval(() => { if (activeId) renderDiscovery(); },1000);

$('reprocess-meeting').onclick = () => {
  if ($('reprocess-meeting').disabled) return;
  recordPending=send({action:'reprocess',session_id:selectedId,language:$('reprocess-language').value || 'auto'});
  render();
};

$('title').oninput = renderDiscovery;
$('reconnect').onclick = () => connect();
