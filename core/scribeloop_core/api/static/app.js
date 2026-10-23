'use strict';
const $ = id => document.getElementById(id);
let key = new URLSearchParams(location.hash.slice(1)).get('token') || '';
history.replaceState(null, '', location.pathname);
const emptyTemplate = $('transcript').firstElementChild.cloneNode(true);
let stopPending = false, authenticated = false, replayAvailable = true, replayEnabled = true, recordingAvailable = false, recordPending = false;
let transcription = {cloud:false,configured:true}, cloudUsage = new Map();
let notes = new Map(), languageLocks = new Map(), speakerTurns = new Map(), liveProgress = new Map();
const partials = new Map();
let socket, activeId = null, selectedId = null, sessions = new Map(), segments = new Map();
const liveStates = new Set(['loading', 'replaying', 'preparing', 'recording', 'stopping']);
let discovery = {status:'disabled', apps:[], microphones:[], candidates:[]};
let sourceRequest = 0, pendingSource = null, chosenSource = null;
let renderedCandidates = '', renderedApps = '', renderedMicrophones = '';
let microphoneNeedsChoice = false, connecting = false;
const historyNodes = new Map(), transcriptNodes = new Map();
let transcriptView = '', rosterView = '';
const languageNames={en:'English',vi:'Tiếng Việt',es:'Spanish',fr:'French',de:'German',hi:'Hindi',ru:'Russian',pt:'Portuguese',ja:'Japanese',it:'Italian',nl:'Dutch'};
function languageName(code) { return languageNames[code] || code; }
function voiceNumber(label) { return Math.max(0,(Number(/Speaker (\d+)/.exec(label || '')?.[1]) || 1)-1); }
function avatar(label) { const el=document.createElement('span'); el.className='speaker-avatar'; el.textContent=/Speaker (\d+)/.exec(label || '')?.[1] || '?'; el.setAttribute('data-voice',String(voiceNumber(label)%6)); el.setAttribute('aria-hidden','true'); return el; }
function renderSpeakers(session,rows) {
  $('speaker-roster').hidden = session != null && session.source !== 'meeting';
  const speakers=new Map();
  rows.forEach(row=>{if(row.speaker_label) speakers.set(row.speaker_label,row.stream === 'mic' ? 'Microphone' : 'Meeting audio');});
  const signature=JSON.stringify([selectedId,Boolean(rows.length),[...speakers]]);
  if(signature === rosterView) return;
  rosterView=signature; $('speaker-list').replaceChildren();
  for(const [label,source] of speakers) { const chip=document.createElement('div'); chip.className='speaker-chip'; chip.setAttribute('role','listitem'); const name=document.createElement('span'); name.textContent=label; const scope=document.createElement('small'); scope.textContent=source; chip.append(avatar(label),name,scope); $('speaker-list').append(chip); }
  text('speaker-count',speakers.size ? `${speakers.size} SPEAKER LABEL${speakers.size === 1 ? '' : 'S'}` : rows.length ? 'NO SPEAKER LABELS' : 'AWAITING SPEECH');
}
function text(id, value) { if ($(id).textContent !== value) $(id).textContent = value; }
function scopeDescription(source) {
  if (!source?.scope) return 'Capture scope unavailable';
  if (source.scope === 'system') return 'All system audio · all apps';
  const kind = source.kind || discovery.apps.find(app=>app.id === source.app_id)?.kind;
  return kind === 'browser' ? `All tabs in ${source.name}` : `All audio from ${source.name || 'the selected app'}`;
}
function providerName() { return transcription.provider === 'deepgram' ? 'Deepgram' : 'Groq'; }
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
    transcription.cloud && !transcription.configured ? `Configure ${transcription.provider === 'deepgram' ? 'DEEPGRAM_API_KEY' : 'GROQ_API_KEY'} in your local .env, then restart ScribeLoop.` :
    processing ? 'Wait for transcription to finish.' :
    recordPending ? 'Preparing your request…' :
    discovery.permission !== 'authorized' ? 'Enable microphone access before recording.' :
    !$('title').value.trim() ? 'Add a meeting title.' :
    !chosenSource ? 'Choose an audio source and microphone, then press Confirm source.' :
    !$('record-consent').checked ? 'Confirm participant permission above to enable Record.' :
    transcription.cloud && !$('cloud-consent').checked ? `Confirm sending this meeting audio to ${providerName()} before Record.` :
    'Ready. Start your call, then press Record meeting.';
  $('record-meeting').disabled = !authenticated || !recordingAvailable || !chosenSource ||
    !$('record-consent').checked || (transcription.cloud && (!transcription.configured || !$('cloud-consent').checked)) || locked || processing || discovery.permission !== 'authorized' || !$('title').value.trim();
  text('record-help',reason);
  text('record-meeting',recordPending ? 'Preparing…' : 'Record meeting ↗');
  $('recording-bar').hidden = authenticated && !activeId && !processing && !recordPending;
  text('recording-state', !authenticated ? connecting ? 'Connecting…' : activeId || processing ? 'Connection lost · recording status unverified' : 'Disconnected · reconnect to restore your notebook' :
    active ? (active.source === 'meeting' ? active.status.toUpperCase() : 'REPLAYING PUBLIC AUDIO') :
    processing ? 'TRANSCRIBING · audio capture stopped' : recordPending ? 'Preparing request…' : 'Recording off');
  text('elapsed-time', active && authenticated ? formatDuration(Date.now()-active.started_at) : '');
  text('active-scope', !authenticated ? 'Reconnect to verify capture status. Disconnecting this page does not stop an active recording.' : active?.source === 'meeting' ?
    `${scopeDescription(active.capture)} · ${active.capture?.microphone_name || 'Microphone identity unavailable'}` :
    active ? 'Public audio replay · microphone is not open' : 'Transcription processing · no new audio capture');
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
  if(active?.capture?.language) $('meeting-language').value=active.capture.language;
  $('meeting-language').disabled = !authenticated || locked;
  text('meeting-language-help',$('meeting-language').value === 'auto' || !$('meeting-language').value ? 'Auto recognizes English, Spanish, French, German, Hindi, Russian, Portuguese, Japanese, Italian and Dutch. For Vietnamese, choose Tiếng Việt.' : `Fixed language: ${languageName($('meeting-language').value)}. Select Auto to follow supported language changes.`);
  $('title').disabled = locked;
  $('record-consent').disabled = !authenticated || locked;
  $('cloud-consent-row').hidden = !transcription.cloud;
  $('cloud-consent').disabled = !authenticated || locked;
  text('cloud-consent-copy',`Send this meeting’s microphone and selected app audio to ${providerName()} for cloud transcription.`);
  text('saved-cloud-consent-copy',`Send this selected saved meeting’s audio to ${providerName()} for this transcription/resume request.`);
  text('cloud-provider',transcription.cloud ? `${providerName()} · ${transcription.model} · ${transcription.configured ? 'key configured' : 'key missing'} · estimated audio cap $${Number(transcription.budget_usd || .05).toFixed(2)} per saved meeting. No local speech models.` : '');
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
      button.onclick=()=>{ selectedId=session.id; $('delete-consent').checked=false; $('retention-consent').checked=false; $('saved-cloud-consent').checked=false; render(); };
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
function speakerLabel(row, turns) {
  if (row.speaker_label) return `${row.stream === 'mic' ? 'Microphone' : 'Meeting audio'} · ${row.speaker_label}`;
  if (row.stream === 'mic') return 'Microphone · speaker unknown';
  const overlaps=turns.filter(t=>t.start_ms < row.end_ms && t.end_ms > row.start_ms);
  if (!overlaps.length) return 'Meeting audio · speaker unknown';
  const labels=new Set(overlaps.map(t=>t.label));
  if (labels.size !== 1 || labels.has(null)) return 'Meeting audio · overlapping / uncertain speakers';
  const intervals=overlaps.map(t=>[Math.max(t.start_ms,row.start_ms),Math.min(t.end_ms,row.end_ms)]).sort((a,b)=>a[0]-b[0]);
  let covered=0,end=-1;
  intervals.forEach(([start,stop])=>{ covered+=Math.max(0,stop-Math.max(start,end)); end=Math.max(end,stop); });
  return covered >= .8*(row.end_ms-row.start_ms) ? `Meeting audio · ${overlaps[0].label}` : 'Meeting audio · speaker unknown';
}
function renderTranscript(session, rows, emptyMessage) {
  const signature=JSON.stringify([selectedId,emptyMessage,rows,speakerTurns.get(selectedId)]);
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
      const body=document.createElement('div'); body.className='segment-content';
      const meta=document.createElement('div'); meta.className='speaker-meta';
      const badge=avatar(row.speaker_label), name=document.createElement('span'), source=document.createElement('span'), language=document.createElement('span');
      name.className='speaker-name'; source.className='speaker-source'; language.className='segment-language';
      meta.append(badge,name,source,language);
      const p=document.createElement('p'); body.append(meta,p); article.append(time,body);
      node={article,time,p,meta,badge,name,source,language}; transcriptNodes.set(id,node); }
    const timestamp=formatDuration(row.start_ms);
    if (node.time.textContent !== timestamp) node.time.textContent=timestamp;
    node.meta.hidden=session?.source !== 'meeting';
    const label=speakerLabel(row,speakerTurns.get(selectedId)||[]);
    const name=row.speaker_label || (/Speaker \d+/.exec(label)?.[0]) || (label.includes('overlapping') ? 'Overlapping / uncertain speakers' : 'Speaker unknown');
    if(node.name.textContent !== name) node.name.textContent=name;
    node.badge.textContent=/Speaker (\d+)/.exec(name || '')?.[1] || '?';
    node.badge.setAttribute('data-voice',String(voiceNumber(name)%6));
    node.source.textContent=row.stream === 'mic' ? 'Microphone' : 'Meeting audio';
    node.language.textContent=row.language ? languageName(row.language) : '';
    if (node.p.textContent !== row.text) node.p.textContent=row.text;
    if ($('transcript').children[index] !== node.article) $('transcript').insertBefore(node.article,$('transcript').children[index] || null);
  });
}

function partialText(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  return Object.entries(value).filter(([,v])=>v).map(([stream,v])=>`${stream === 'mic' ? 'Microphone' : 'Meeting audio'} (provisional): ${v}`).join('\n');
}
function render() {
  renderDiscovery();
  renderHistory();
  const session = sessions.get(selectedId);
  text('new-session',activeId ? 'View active session' : '＋ New meeting');
  $('session-heading').textContent = session ? session.title : 'The conversation';
  $('session-status').textContent = session ? session.status.toUpperCase() : 'READY WHEN YOU ARE';
  $('activity-dot').classList.toggle('active', Boolean(activeId));
  $('start').disabled = Boolean(activeId) || !authenticated || !replayAvailable || !replayEnabled || !$('fixture').value;
  $('partial').textContent = partialText(partials.get(selectedId));
  if (!replayAvailable) $('session-status').textContent = 'STORAGE UNAVAILABLE';
  if (activeId) $('start').textContent = sessions.get(activeId)?.source === 'meeting' ? 'Meeting recording active' : sessions.get(activeId)?.status === 'loading' ? 'Loading model…' : 'Replay in progress';
  else $('start').textContent = replayEnabled ? 'Start replay ↗' : 'Local replay disabled in cloud mode';
  const languageLock = languageLocks.get(selectedId);
  const detected=[...new Set((segments.get(selectedId)||[]).map(r=>r.language).filter(Boolean))];
  text('language-status',languageLock?.mode === 'multilingual' ? `Auto · multilingual${detected.length ? ' · Recognized: '+detected.map(languageName).join(', ') : (segments.get(selectedId)||[]).length ? ' · language not reported' : ' · waiting for speech'}` :  languageLock ? `${languageLock.mode === 'auto' ? 'Detected' : 'Selected'}: ${languageName(languageLock.language)} · fixed for this conversation` :
    session?.source === 'meeting' ? 'Auto recognizes supported languages as the conversation unfolds. Choose a fixed language for Vietnamese.' : '');
  text('meeting-notes',notes.get(selectedId) || '');
  $('notes-section').hidden = !notes.get(selectedId);
  $('reprocess-controls').hidden = session?.source !== 'meeting';
  $('saved-cloud-consent-row').hidden = !transcription.cloud;
  $('reprocess-meeting').hidden = session?.source !== 'meeting';
  $('reprocess-meeting').disabled = !authenticated || !recordingAvailable || Boolean(activeId) || recordPending || (transcription.cloud && (!transcription.configured || !$('saved-cloud-consent').checked)) ||
    [...sessions.values()].some(s=>s.source === 'meeting' && s.status === 'processing');
  $('reprocess-language').disabled = !authenticated || Boolean(activeId) || recordPending || [...sessions.values()].some(s=>s.status === 'processing');
  text('session-progress', session?.source === 'meeting' ? session.status === 'processing' ?
    'Transcribing saved audio. Previous text stays visible until the new pass completes.' :
    session.status === 'preparing' ? 'Preparing encrypted storage and selected audio sources…' :
    session.status === 'stopping' ? 'Stopping capture and finishing the encrypted audio…' :
    session.status === 'recording' ? `Recording encrypted audio. ${providerName()} ${transcription.streaming ? 'streams text and anonymous speaker labels' : 'sends text after short batches'}; Stop remains available.` :
    session.status === 'failed' ? 'Recording or transcription failed. Check the message above; saved audio remains encrypted.' : '' : '');
  const idle = authenticated && !activeId && !recordPending && ![...sessions.values()].some(s=>s.status === 'processing');
  $('saved-tools').hidden = session?.source !== 'meeting';
  $('recover-meeting').disabled = !idle || !['failed','interrupted'].includes(session?.status);
  $('resume-meeting').disabled = !idle || !['failed','interrupted'].includes(session?.status) ||
    (Boolean((segments.get(selectedId)||[]).length) && !session?.has_checkpoints) ||
    (transcription.cloud && (!transcription.configured || !$('saved-cloud-consent').checked));
  $('delete-meeting').disabled = !idle || !$('delete-consent').checked;
  $('apply-retention').disabled = !idle || !$('retention-consent').checked;
  const usage=cloudUsage.get(selectedId);
  text('cloud-usage',usage ? `${usage.providers?.join(', ') || providerName()} upload reservations: ${usage.requests} · reserved estimate $${Number(usage.estimated_usd).toFixed(4)}. Actual billing depends on your provider account.` : '');
  const progress=liveProgress.get(selectedId);
  text('live-progress',progress ? progress.mode === 'finalizing' ? 'Refining the saved transcript from the complete recording. Live text remains visible until the final pass succeeds.' : progress.mode === 'deferred' ? 'Live transcription paused at the lag limit. Encrypted recording continues; saved audio will be transcribed after Stop.' : `${progress.mode === 'catching_up' ? 'Catching up' : progress.mode === 'streaming' ? 'Streaming' : 'Cloud batch updates'} · ${formatDuration(progress.lag_ms)} behind recorded audio` : '');
  if (session?.recovery) text('session-progress','Recovered / interrupted audio. Only authenticated complete frames were retained; missing tail duration is unknown. Capture was not restarted.');
  const rows = [...(segments.get(selectedId) || [])].sort((a,b)=>a.start_ms-b.start_ms);
  const emptyMessage = !replayAvailable ? 'Storage is unavailable. Reconnect or restart the local server.' :
    session?.status === 'processing' ? 'Audio capture stopped. Transcribing the remaining audio batches…' :
    session?.source === 'meeting' && liveStates.has(session.status) ? 'Recording audio. Waiting for early speech and the first stable words…' :
    session?.status === 'failed' ? 'This session failed. Check the message above.' :
    liveStates.has(session?.status) ? 'Listening for the first confirmed words…' : 'No speech has been transcribed in this session.';
  renderSpeakers(session,rows);
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
      speakerTurns = new Map(Object.entries(data.speaker_turns || {})); liveProgress.clear();
      transcription=data.transcription || {cloud:false,configured:true};
      for (const id of ['meeting-language','reprocess-language']) {
        const select=$(id);
        for (const option of select.options || []) if (option.value === 'auto') option.disabled=transcription.auto_language === false;
        if (transcription.auto_language === false && (!select.value || select.value === 'auto')) select.value=transcription.default_language || 'en';
      }
      text('speaker-status',transcription.speaker_labels ? 'Anonymous speaker labels are provided per audio track. Names are unknown; labels may be mistaken during overlap and are not linked across new connections.' : 'Microphone and meeting audio are separate sources. Individual speaker labels are unavailable.');
      cloudUsage=new Map(Object.entries(data.cloud_usage || {}));
      languageLocks = new Map(Object.entries(data.language_locks || {}));
      replayEnabled = data.replay_enabled !== false;
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
    } else if (data.type === 'cloud_usage') {
      cloudUsage.set(data.session_id,data); render();
    } else if (data.type === 'speaker_turns') {
      speakerTurns.set(data.session_id,data.turns); render();
    } else if (data.type === 'transcription_stage') {
      liveProgress.set(data.session_id,{mode:'finalizing',lag_ms:0,streams:{}}); render();
    } else if (data.type === 'transcription_progress') {
      const state=liveProgress.get(data.session_id) || {streams:{}};
      state.streams[data.stream || 'loopback']=data;
      state.lag_ms=Math.max(...Object.values(state.streams).map(p=>p.lag_ms));
      state.mode=Object.values(state.streams).some(p=>p.mode === 'deferred') ? 'deferred' : state.lag_ms > 12000 ? 'catching_up' : 'live';
      liveProgress.set(data.session_id,state); render();
    } else if (['transcription_error','speaker_warning','recovery_complete'].includes(data.type)) {
      recordPending=false; notice(data.message); render();
    } else if (data.type === 'session_deleted') {
      sessions.delete(data.session_id); segments.delete(data.session_id); notes.delete(data.session_id);
      speakerTurns.delete(data.session_id); languageLocks.delete(data.session_id); liveProgress.delete(data.session_id);
      recordPending=false; if (selectedId === data.session_id) selectedId=null;
      $('delete-consent').checked=false; render();
    } else if (data.type === 'retention_complete') {
      recordPending=false; $('retention-consent').checked=false; notice(`Removed ${data.deleted} expired meetings.`); render();
    } else if (data.type === 'language_locked') {
      languageLocks.set(data.session_id,data); render();
    } else if (data.type === 'transcript_replaced') {
      segments.set(data.session_id,data.segments); text('transcript-update','Transcript replaced with the completed pass.'); render();
    } else if (data.type === 'notes') {
      notes.set(data.session_id,data.notes); render();
    } else if (data.type === 'recording_error') {
      recordPending=false; notice(data.message); render();
    } else if (data.type === 'session') {
      if (data.session.source === 'meeting') { recordPending=false; $('record-consent').checked=false; $('cloud-consent').checked=false; }
      sessions.set(data.session.id,data.session);
      if (['completed','failed','interrupted'].includes(data.session.status)) liveProgress.delete(data.session.id);
      if (liveStates.has(data.session.status)) { const newActive=activeId !== data.session.id; activeId=data.session.id; if (newActive || !selectedId) selectedId=activeId; }
      else { if (activeId === data.session.id) { activeId=null; stopPending=false; } partials.delete(data.session.id); }
      render();
    } else if (data.type === 'segment') {
      const rows=segments.get(data.session_id)||[];
      if (!rows.some(r => r.id === data.segment.id)) rows.push(data.segment);
      segments.set(data.session_id,rows);
      const pending=partials.get(data.session_id); if (pending && typeof pending === 'object') delete pending[data.segment.stream || 'loopback']; else partials.delete(data.session_id);
      if (selectedId === data.session_id) text('transcript-update',`${rows.length} confirmed transcript segments available.`); render();
    } else if (data.type === 'partial') {
      if (sessions.get(data.session_id)?.source !== 'meeting') { partials.set(data.session_id,data.segment.text); if (selectedId === data.session_id) $('partial').textContent=data.segment.text; return; }
      const previous=partials.get(data.session_id);
      const values=typeof previous === 'object' ? previous : {};
      values[data.stream || 'loopback']=data.segment.text; partials.set(data.session_id,values);
      if (selectedId === data.session_id) $('partial').textContent=partialText(values);
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
$('new-session').onclick = () => { if (activeId) { selectedId=activeId; render(); return; } selectedId=null; $('delete-consent').checked=false; $('retention-consent').checked=false; $('saved-cloud-consent').checked=false; $('title').value='Meeting conversation'; $('record-consent').checked=false; $('cloud-consent').checked=false; $('partial').textContent=''; notice(''); render(); $('title').focus(); };
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
    consent_confirmed:$('record-consent').checked,cloud_consent:$('cloud-consent').checked,language:$('meeting-language').value || 'auto'});
  renderDiscovery();
};

$('enable-microphone').onclick = () => send({action:'microphone_permission'});

setInterval(() => { if (activeId) renderDiscovery(); },1000);

$('reprocess-meeting').onclick = () => {
  if ($('reprocess-meeting').disabled) return;
  recordPending=send({action:'reprocess',session_id:selectedId,language:$('reprocess-language').value || 'auto',cloud_consent:$('saved-cloud-consent').checked});
  $('saved-cloud-consent').checked=false;
  render();
};

$('title').oninput = renderDiscovery;
$('reconnect').onclick = () => connect();

function savedAction(action) {
  const button=$(action === 'recover' ? 'recover-meeting' : 'resume-meeting');
  if (button.disabled) return;
  recordPending=send({action,session_id:selectedId,language:$('reprocess-language').value || 'auto',cloud_consent:$('saved-cloud-consent').checked}); $('saved-cloud-consent').checked=false; render();
}
$('recover-meeting').onclick=()=>savedAction('recover');
$('resume-meeting').onclick=()=>savedAction('resume');
$('delete-consent').onchange=render;
$('retention-consent').onchange=render;
$('delete-meeting').onclick=()=>{
  if ($('delete-meeting').disabled) return;
  recordPending=send({action:'delete',session_id:selectedId,confirmed:$('delete-consent').checked}); render();
};
$('apply-retention').onclick=()=>{
  if ($('apply-retention').disabled) return;
  const days=Number($('retention-days').value);
  if (!Number.isInteger(days) || days<1 || days>3650) { notice('Choose retention between 1 and 3650 days.'); return; }
  recordPending=send({action:'retention',days,confirmed:$('retention-consent').checked}); render();
};

$('cloud-consent').onchange=renderDiscovery;
$('saved-cloud-consent').onchange=render;

$('meeting-language').onchange=renderDiscovery;
$('reprocess-language').onchange=render;
