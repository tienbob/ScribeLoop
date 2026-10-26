'use strict';
const $ = id => document.getElementById(id);
const requireLaunchKey = document.documentElement?.dataset?.requireLaunchKey !== 'false';
let key = new URLSearchParams(location.hash.slice(1)).get('token') || '';
history.replaceState(null, '', location.pathname);
const emptyTemplate = $('transcript').firstElementChild.cloneNode(true);
let stopPending = false, authenticated = false, replayAvailable = true, replayEnabled = true, recordingAvailable = false, recordPending = false;
let transcription = {cloud:false,configured:true}, cloudUsage = new Map();
let modelProfiles = {};
let people=[], personPending=false, summaryBudgets=new Map(), rollingDrafts=new Map(), transcriptPageStart=null, transcriptPageSession=null;
let audioStates=new Map(), audioPending=false, audioURL=null, audioSession=null;
let agreement={accepted:false,version:null};
let promptCandidate=null, offeredCalls=new Set(), autoCallState=null;
let summaryProvider={configured:false}, savedSummaries=new Map(), summaryActiveId=null, summaryPending=false, exportPending=false, summarySession=null;
let savedModelSession = null;
let notes = new Map(), languageLocks = new Map(), speakerTurns = new Map(), liveProgress = new Map();
const partials = new Map();
let socket, activeId = null, selectedId = null, sessions = new Map(), segments = new Map();
const liveStates = new Set(['loading', 'replaying', 'preparing', 'recording', 'stopping']);
let discovery = {status:'disabled', apps:[], microphones:[], candidates:[]};
let sourceRequest = 0, pendingSource = null, chosenSource = null;
let renderedCandidates = '', renderedApps = '', renderedMicrophones = '';
let microphoneNeedsChoice = false, connecting = false;
const historyNodes = new Map(), transcriptNodes = new Map();
let transcriptView = '', rosterView = '', speakerPending = false;
const languageNames={en:'English',vi:'Tiếng Việt',zh:'Chinese',es:'Spanish',fr:'French',de:'German',hi:'Hindi',ru:'Russian',pt:'Portuguese',ja:'Japanese',it:'Italian',nl:'Dutch'};
function selectedModel(saved=false) { return $(saved ? 'saved-model' : 'meeting-model').value || 'deepgram'; }
function profile(saved=false) { return modelProfiles[selectedModel(saved)] || transcription; }
function modelHelp(p) { return 'Cloud · Deepgram Nova-3. Live transcription with speaker labels. Stop finalizes this stream; full saved-audio reruns happen only when requested.'; }
function modelLanguages(id,p) {
  if (!p.languages) return;
  const select=$(id);
  for (const option of select.options || []) {
    option.disabled=!(option.value in p.languages);
    if (!option.disabled) option.textContent=p.languages[option.value];
  }
  if (!(select.value in p.languages)) select.value=p.default_language || 'auto';
}
function languageName(code) { return languageNames[code] || code; }
function voiceNumber(label) { return Math.max(0,(Number(/Speaker (\d+)/.exec(label || '')?.[1]) || 1)-1); }
function avatar(label) { const el=document.createElement('span'); el.className='speaker-avatar'; el.textContent=/Speaker (\d+)/.exec(label || '')?.[1] || '?'; el.setAttribute('data-voice',String(voiceNumber(label)%6)); el.setAttribute('aria-hidden','true'); return el; }
function correctionRows() {
  return [...new Map((segments.get(selectedId)||[]).filter(r=>r.display_speaker_id || r.local_speaker_id).map(r=>[r.display_speaker_id || r.local_speaker_id,r])).values()];
}
function renderMergeTargets() {
  const identity=$('edit-speaker').value;
  const rows=correctionRows(), row=rows.find(r=>(r.display_speaker_id || r.local_speaker_id) === identity);
  $('speaker-name').value=row?.speaker_label || '';
  $('merge-target').replaceChildren();
  const placeholder=document.createElement('option'); placeholder.value=''; placeholder.textContent='Choose a matching label…'; $('merge-target').append(placeholder);
  for(const target of rows) {
    const id=target.display_speaker_id || target.local_speaker_id;
    if(id === identity || id.slice(0,id.lastIndexOf(':')) !== identity.slice(0,identity.lastIndexOf(':'))) continue;
    const option=document.createElement('option'); option.value=id; option.textContent=target.speaker_label; $('merge-target').append(option);
  }
  $('merge-target').value=''; correctionButtons();
}
function correctionButtons() {
  const disabled=$('speaker-editor').hidden || speakerPending;
  $('rename-speaker').disabled=disabled || !$('edit-speaker').value || !$('speaker-name').value.trim();
  $('merge-speaker').disabled=disabled || !$('merge-target').value;
}
function renderSpeakers(session,rows) {
  $('speaker-roster').hidden = session != null && session.source !== 'meeting';
  const editable=Boolean(authenticated && !activeId && !recordPending && !speakerPending &&
    ![...sessions.values()].some(s=>s.status === 'processing') && session?.source === 'meeting' &&
    ['completed','failed','interrupted'].includes(session.status) && correctionRows().length);
  $('speaker-editor').hidden=!editable;
  const speakers=new Map();
  rows.forEach(row=>{if(row.speaker_label) speakers.set(row.display_speaker_id || row.local_speaker_id || `${row.stream}:${row.speaker_label}`,row);});
  if(speakers.size) text('speaker-status','Anonymous speaker labels are scoped to each track and model generation. Names and merges are manual corrections for this transcript.');
  const signature=JSON.stringify([selectedId,editable,[...speakers]]);
  if(signature === rosterView) { correctionButtons(); return; }
  rosterView=signature; $('speaker-list').replaceChildren();
  for(const row of speakers.values()) { const chip=document.createElement('div'); chip.className='speaker-chip'; chip.setAttribute('role','listitem'); const name=document.createElement('span'); name.textContent=row.speaker_label; const scope=document.createElement('small'); scope.textContent=row.stream === 'mic' ? 'Microphone' : 'Meeting audio'; chip.append(avatar(row.speaker_label),name,scope); $('speaker-list').append(chip); }
  text('speaker-count',speakers.size ? `${speakers.size} SPEAKER LABEL${speakers.size === 1 ? '' : 'S'}` : rows.length ? 'NO SPEAKER LABELS' : 'AWAITING SPEECH');
  const previous=$('edit-speaker').value;
  $('edit-speaker').replaceChildren();
  for(const row of correctionRows()) { const option=document.createElement('option'); option.value=row.display_speaker_id || row.local_speaker_id; option.textContent=`${row.speaker_label} · ${row.stream === 'mic' ? 'Microphone' : 'Meeting audio'}`; $('edit-speaker').append(option); }
  $('edit-speaker').value=correctionRows().some(r=>(r.display_speaker_id || r.local_speaker_id) === previous) ? previous : (correctionRows()[0]?.display_speaker_id || correctionRows()[0]?.local_speaker_id || '');
  renderMergeTargets();
}
function text(id, value) { if ($(id).textContent !== value) $(id).textContent = value; }
function scopeDescription(source) {
  if (!source?.scope) return 'Capture scope unavailable';
  if (source.scope === 'microphone') return 'Microphone only · no app or system audio';
  if (source.scope === 'system') return 'All system audio · all apps';
  const kind = source.kind || discovery.apps.find(app=>app.id === source.app_id)?.kind;
  return kind === 'browser' ? `All tabs in ${source.name}` : `All audio from ${source.name || 'the selected app'}`;
}
function providerName() { return transcription.provider === 'deepgram' ? 'Deepgram' : 'Groq'; }
function busy() { return Boolean(activeId) || recordPending || audioPending; }
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
    scope:value === 'microphone' ? 'microphone' : value === 'system' ? 'system' : 'application',
    app_id:['system','microphone'].includes(value) ? null : value.slice(4),
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
    option($('meeting-source'),'','Choose a recording source');
    option($('meeting-source'),'microphone','Microphone only · no app audio');
    discovery.apps.forEach(app => option($('meeting-source'),`app:${app.id}`,app.name + (app.kind === 'browser' ? ' · all browser audio' : '') + (app.running === false ? ' · not running' : '')));
    option($('meeting-source'),'system','All system audio · includes other apps');
  }
  const validSource = ['system','microphone'].includes(oldSource) || discovery.apps.some(app => `app:${app.id}` === oldSource);
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
function closeCallPrompt() {
  if($('call-permission').close) $('call-permission').close(); else $('call-permission').open=false;
  promptCandidate=null;
}
function renderCallAssistant() {
  const processing=[...sessions.values()].some(s=>s.status === 'processing');
  const current=discovery.candidates.find(c=>c.id === promptCandidate?.id && c.selectable);
  if(promptCandidate && (!current || !agreement.accepted || !authenticated || busy() || processing || discovery.status !== 'available')) closeCallPrompt();
  text('watch-status',!authenticated ? 'Reconnect to watch for calls.' : activeId ? 'Taking notes for your conversation.' : processing ? 'Finishing your transcript…' : !recordingAvailable ? 'Recording needs the encrypted macOS app.' : discovery.permission !== 'authorized' ? 'Allow microphone access in recording preferences to include your voice.' : !profile().configured ? 'Choose and configure your transcription model in recording preferences.' : discovery.status === 'available' ? 'Watching for call activity. Nothing is being recorded.' : 'Call detection is unavailable. Open recording preferences to record manually.');
  const ending=autoCallState?.session_id === activeId && autoCallState?.state === 'ending';
  $('keep-recording').hidden=!ending;
  $('keep-recording').disabled=!authenticated;
  text('auto-end-status',ending ? `App audio is inactive. Ending in ${autoCallState.remaining_s}s.` : autoCallState?.session_id === activeId && autoCallState?.state === 'unknown' ? 'Call detection uncertain · recording continues' : '');
  if(!agreement.accepted || !authenticated || !recordingAvailable || busy() || processing || discovery.status !== 'available' || discovery.permission !== 'authorized' || !profile().configured) return;
  if(!promptCandidate) {
    const candidate=discovery.candidates.find(c=>c.selectable && !offeredCalls.has(c.id));
    if(!candidate) return;
    promptCandidate=candidate;
  }
  const p=profile(), candidate=promptCandidate;
  text('call-permission-heading',`Take notes for ${candidate.name}?`);
  text('call-permission-copy',`${candidate.kind === 'browser' ? 'Capture audio from all tabs in '+candidate.name : 'Capture audio from '+candidate.name}, plus ${$('meeting-microphone').value === 'default' || !$('meeting-microphone').value ? 'your system default microphone' : 'your selected microphone'}.`);
  text('call-permission-provider',`${$('meeting-duration').value==='10800' ? '3-hour limit · $8 shared audio cap. ' : $('meeting-duration').value==='3600' ? '1-hour limit · $3 shared audio cap. ' : '5-minute development limit · $'+Number(p.duration_profiles?.['300']?.budget_usd || p.budget_usd || .25).toFixed(2)+' shared audio cap. '}${p.cloud ? 'Audio will be sent to ' : 'Audio will be transcribed locally with '}${p.provider} · ${p.model}. ${summaryProvider.configured ? `After the call, transcript text and speaker labels will be sent to ${summaryProvider.provider} · ${summaryProvider.model} at ${summaryProvider.endpoint} for notes and a title.` : 'Automatic notes are unavailable until a summary provider is configured. The transcript will still be saved.'}`);
  text('accept-call',summaryProvider.configured ? 'Record & take notes' : 'Record conversation');
  $('accept-call').disabled=recordPending;
  if(!$('call-permission').open) { $('call-permission').showModal(); $('decline-call').focus(); }
}
function renderDiscovery() {
  renderCallAssistant();
  const processing = [...sessions.values()].some(s => s.source === 'meeting' && s.status === 'processing');
  const active = sessions.get(activeId);
  const locked = busy();
  $('meeting-model').value='deepgram';
  transcription=profile();
  $('meeting-model').disabled=!authenticated || locked || processing || !Object.keys(modelProfiles).length;
  modelLanguages('meeting-language',transcription);
  text('model-help',modelHelp(transcription));
  text('transcript-feature','Live transcript');
  text('transcript-feature-help','Words arrive while you speak');
  const reason = !authenticated ? 'Reconnect to set up recording.' :
    !recordingAvailable ? 'Meeting recording requires the encrypted macOS app.' :
    activeId ? 'Stop the active session before starting another.' :
    !transcription.configured ? (transcription.cloud ? `Configure ${transcription.provider === 'deepgram' ? 'DEEPGRAM_API_KEY' : 'GROQ_API_KEY'} in your local .env, then restart ScribeLoop.` : transcription.setup || 'Install the local model before recording.') :
    processing ? 'Wait for transcription to finish.' :
    recordPending ? 'Preparing your request…' :
    discovery.permission !== 'authorized' ? 'Enable microphone access before recording.' :
    !$('title').value.trim() ? 'Add a meeting title.' :
    !chosenSource ? 'Choose an audio source and microphone, then press Confirm source.' :
    !agreement.accepted ? 'Accept the first-use agreement to enable Record.' :
    chosenSource?.scope === 'microphone' ? 'Ready. Press Record microphone to capture your selected mic.' :
    'Ready. Start your call, then press Record meeting.';
  $('record-meeting').disabled = !authenticated || !recordingAvailable || !chosenSource ||
    !agreement.accepted || !transcription.configured || locked || processing || discovery.permission !== 'authorized' || !$('title').value.trim();
  text('record-help',reason);
  text('record-meeting',recordPending ? 'Preparing…' : chosenSource?.scope === 'microphone' ? 'Record microphone ↗' : 'Record meeting ↗');
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
  $('meeting-duration').disabled=!authenticated || locked;
  const duration=$('meeting-duration').value || '300';
  const cap=profile().duration_profiles?.[duration]?.budget_usd || (duration==='10800' ? 8 : duration==='3600' ? 3 : profile().budget_usd || .15);
  text('duration-budget',`Shared estimated audio cap $${Number(cap).toFixed(2)} across both tracks, resumes and manual reruns. Summary requests have a separate limit of 128 per saved meeting.`);
  text('meeting-language-help',$('meeting-language').value === 'auto' || !$('meeting-language').value ? 'Auto recognizes English, Spanish, French, German, Hindi, Russian, Portuguese, Japanese, Italian and Dutch. For Vietnamese, choose Tiếng Việt.' : `Fixed language: ${languageName($('meeting-language').value)}. Select Auto to follow supported language changes.`);
  $('title').disabled = locked;
  text('cloud-provider',transcription.cloud ? `${providerName()} · ${transcription.model} · ${transcription.configured ? 'key configured' : 'key missing'} · estimated audio cap $${Number(transcription.budget_usd || .05).toFixed(2)} per saved meeting. No local speech models.` : '');
  $('select-source').disabled = !authenticated || locked || pendingSource !== null || !$('meeting-source').value || !$('meeting-microphone').value;
  text('select-source', pendingSource !== null ? 'Checking source…' : 'Confirm source ↗');
  text('scope-warning', chosenSource?.scope === 'microphone' ? `Only ${chosenSource.microphone_name || 'the selected microphone'} will be captured. No app/system audio or Screen Recording permission is needed. Cloud processes speech live.` : chosenSource ? `${scopeDescription(chosenSource)} will be captured, plus ${chosenSource.microphone_name || 'the selected microphone'}.` :
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
      button.onclick=()=>{ selectedId=session.id; $('delete-consent').checked=false; $('retention-consent').checked=false;  render(); };
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
  if(transcriptPageSession!==selectedId) { transcriptPageSession=selectedId; transcriptPageStart=null; }
  const total=rows.length, start=transcriptPageStart===null ? Math.max(0,total-500) : Math.min(transcriptPageStart,Math.max(0,total-1));
  $('transcript-pagination').hidden=total<=500;
  $('transcript-previous').disabled=start===0;
  $('transcript-next').disabled=start+500>=total;
  text('transcript-window',total>500 ? `Showing ${start+1}–${Math.min(total,start+500)} of ${total} entries. Export includes the full transcript.` : '');
  rows=rows.slice(start,start+500);
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
function renderSummary(session) {
  const rows=segments.get(selectedId)||[], saved=savedSummaries.get(selectedId);
  const valid=session?.source === 'meeting' && rows.length && ['completed','failed','interrupted'].includes(session.status);
  $('summary-section').hidden=!valid;
  if(summarySession !== selectedId) { summarySession=selectedId;  text('summary-status',''); }
  const destination=summaryProvider.configured ? `${summaryProvider.cloud ? 'Cloud' : 'Local'} · ${summaryProvider.provider} · ${summaryProvider.model} · ${summaryProvider.endpoint}` : 'Set OPENAI_API_KEY in .env for OpenAI gpt-5.4-mini, or configure SUMMARY_BASE_URL, SUMMARY_MODEL and SUMMARY_API_KEY for another provider. Restart after changing settings.';
  text('summary-provider',destination);
  const budget=summaryBudgets.get(selectedId);
  text('summary-request-usage',budget ? `Summary requests ${budget.requests}/${budget.max_requests} · ${(budget.input_bytes/1000000).toFixed(2)} MB of ${(budget.max_input_bytes/1000000).toFixed(0)} MB request limit.` : 'Each request allows up to 4,000 output tokens; provider charges apply.');
  const blocked=!authenticated || !valid || Boolean(activeId) || recordPending || [...sessions.values()].some(s=>s.status === 'processing');
  $('generate-summary').disabled=blocked || !summaryProvider.configured || Boolean(summaryActiveId) || summaryPending || !agreement.accepted;
  $('download-markdown').disabled=blocked || exportPending;
  if(summaryActiveId === selectedId) text('summary-status','Generating summary. Previous notes remain available.');
  $('summary-content').replaceChildren();
  text('summary-source',saved ? `Saved draft: ${saved.provider} · ${saved.model} · transcript revision ${saved.revision ?? rows[0]?.revision}` : '');
  if(!saved) return;
  if(saved.stale) { text('summary-status','Saved summary is outdated. Generate again for this transcript; export will omit it.'); return; }
  for(const [title,items] of [['Overview',[saved.summary.overview]],['Key points',saved.summary.key_points],['Decisions',saved.summary.decisions],['Action items',saved.summary.action_items],['Open questions',saved.summary.open_questions],['Uncertainties',saved.summary.uncertainties]]) {
    const heading=document.createElement('h4'); heading.textContent=title; $('summary-content').append(heading);
    for(const item of items) {
      const p=document.createElement('p'); p.textContent=item.text || `${item.task} · Owner: ${item.owner || 'not stated'} · Due: ${item.due || 'not stated'}`; $('summary-content').append(p);
      for(const id of item.segment_ids) { const source=rows.find(r=>r.id === id); if(!source) continue;
        const button=document.createElement('button'); button.type='button'; button.className='summary-citation'; button.textContent=`Source ${formatDuration(source.start_ms)} · ${source.speaker_label || 'Speaker unknown'}`;
        button.onclick=()=>{ const all=segments.get(selectedId)||[]; const index=all.findIndex(r=>r.id===id); transcriptPageStart=Math.floor(Math.max(0,index)/500)*500; render(); const article=transcriptNodes.get(`${selectedId}:${id}`)?.article; article?.scrollIntoView?.({block:'center'}); article?.setAttribute('tabindex','-1'); article?.focus(); }; $('summary-content').append(button);
      }
    }
    if(!items.length) { const p=document.createElement('p'); p.textContent='None stated.'; $('summary-content').append(p); }
  }
}
function render() {
  const dialog=$('app-agreement');
  text('agreement-status',agreement.accepted ? 'App agreement accepted. Each call still waits for Record.' : 'Accept the app agreement before recording or processing.');
  $('revoke-agreement').disabled=!authenticated || !agreement.accepted;
  text('agreement-providers',`Transcription: ${profile().provider || 'not configured'} · ${profile().model || ''}. Notes: ${summaryProvider.endpoint || 'not configured'}.`);
  if(authenticated && !agreement.accepted && !dialog.open) { closeCallPrompt(); dialog.showModal(); $('agree-app').focus(); }
  if(agreement.accepted && dialog.open) { if(dialog.close) dialog.close(); else dialog.open=false; }
  renderDiscovery();
  renderHistory();
  const session = sessions.get(selectedId);
  text('new-session',activeId ? 'View active session' : 'Recording preferences');
  renderSummary(session);
  $('rolling-section').hidden=!rollingDrafts.has(selectedId);
  text('rolling-copy',rollingDrafts.get(selectedId)?.summary?.overview?.text || '');
  text('rolling-provider',rollingDrafts.has(selectedId) ? `Draft: ${rollingDrafts.get(selectedId).provider} · ${rollingDrafts.get(selectedId).model}` : '');
  renderAudio(session);
  const currentRevision=(segments.get(selectedId)||[])[0]?.revision || session?.transcript_revision;
  text('revision-status',session?.source === 'meeting' && currentRevision ? `Saved transcript · revision ${currentRevision}${session.status === 'processing' ? ' · retained while processing' : ''}` : '');
  $('session-heading').textContent = session ? session.title : 'The conversation';
  $('session-status').textContent = session ? session.status.toUpperCase() : 'READY WHEN YOU ARE';
  $('activity-dot').classList.toggle('active', Boolean(activeId));
  $('start').disabled = !agreement.accepted || Boolean(activeId) || !authenticated || !replayAvailable || !replayEnabled || !$('fixture').value;
  $('partial').textContent = partialText(partials.get(selectedId));
  if (!replayAvailable) $('session-status').textContent = 'STORAGE UNAVAILABLE';
  if (activeId) $('start').textContent = sessions.get(activeId)?.source === 'meeting' ? 'Meeting recording active' : sessions.get(activeId)?.status === 'loading' ? 'Loading model…' : 'Replay in progress';
  else $('start').textContent = replayEnabled ? 'Start replay ↗' : 'Local replay disabled in cloud mode';
  const languageLock = languageLocks.get(selectedId);
  if (session && session.id !== savedModelSession && Object.keys(modelProfiles).length) {
    savedModelSession=session.id;
    $('saved-model').value='deepgram';

  }
  const savedProfile=profile(true);
  $('saved-model').disabled=!authenticated || busy() || [...sessions.values()].some(s=>s.status === 'processing') || !Object.keys(modelProfiles).length;
  modelLanguages('reprocess-language',savedProfile);
  text('saved-model-help',modelHelp(savedProfile));
  text('session-model',session?.source === 'meeting' ? `Transcript model: ${(session.transcript_provider || session.capture?.provider) === 'local-moss' ? 'Local · MOSS Q6_K' : (session.transcript_provider || session.capture?.provider) === 'deepgram' ? 'Cloud · Deepgram Nova-3' : session.capture?.model || 'Legacy / unknown'}` : '');
  const detected=[...new Set((segments.get(selectedId)||[]).map(r=>r.language).filter(Boolean))];
  text('language-status',(session?.transcript_provider || session?.capture?.provider) === 'local-moss' ? `Local MOSS · English / Chinese${detected.length ? ' · Transcript: '+detected.map(languageName).join(', ') : ''}` : languageLock?.mode === 'multilingual' ? `Auto · multilingual${detected.length ? ' · Recognized: '+detected.map(languageName).join(', ') : (segments.get(selectedId)||[]).length ? ' · language not reported' : ' · waiting for speech'}` :  languageLock ? `${languageLock.mode === 'auto' ? 'Detected' : 'Selected'}: ${languageName(languageLock.language)} · fixed for this conversation` :
    session?.source === 'meeting' ? (session.transcript_provider || session.capture?.provider) === 'local-moss' ? 'Local MOSS · English / Chinese · recognized languages shown with transcript rows.' : 'Auto recognizes supported languages as the conversation unfolds. Choose a fixed language for Vietnamese.' : '');
  text('meeting-notes',notes.get(selectedId) || '');
  $('notes-section').hidden = !notes.get(selectedId);
  $('reprocess-controls').hidden = session?.source !== 'meeting';
  text('reprocess-help',`A manual rerun uploads this saved audio under your app agreement. Your saved transcript stays available until the replacement completes. ${summaryProvider.configured ? 'Then notes are regenerated once using '+summaryProvider.provider+' · '+summaryProvider.model+' at '+summaryProvider.endpoint+'.' : 'Configure a summary provider to regenerate notes too.'}`);
  $('reprocess-meeting').hidden = session?.source !== 'meeting';
  text('reprocess-meeting',summaryProvider.configured ? 'Rerun transcription & notes' : 'Transcribe again');
  $('reprocess-meeting').disabled = Boolean(summaryActiveId) || !audioStates.get(selectedId)?.available || audioPending || !authenticated || !recordingAvailable || Boolean(activeId) || recordPending || !savedProfile.configured || !agreement.accepted ||
    [...sessions.values()].some(s=>s.source === 'meeting' && s.status === 'processing');
  $('reprocess-language').disabled = !authenticated || Boolean(activeId) || recordPending || [...sessions.values()].some(s=>s.status === 'processing');
  text('session-progress', session?.source === 'meeting' ? session.status === 'processing' ?
    'Transcribing saved audio. Previous text stays visible until the new pass completes.' :
    session.status === 'preparing' ? 'Preparing encrypted storage and selected audio sources…' :
    session.status === 'stopping' ? 'Stopping capture and finishing the encrypted audio…' :
    session.status === 'recording' ? `Recording encrypted audio. ${providerName()} ${transcription.streaming ? 'streams text and anonymous speaker labels' : 'sends text after short batches'}; Stop remains available.` :
    session.status === 'failed' ? 'Recording or transcription failed. Check the message above; saved audio remains encrypted.' : '' : '');
  const idle = authenticated && !busy() && ![...sessions.values()].some(s=>s.status === 'processing');
  $('saved-tools').hidden = session?.source !== 'meeting';
  $('recover-meeting').disabled = !audioStates.get(selectedId)?.available || audioPending || !idle || !['failed','interrupted'].includes(session?.status);
  $('resume-meeting').disabled = !audioStates.get(selectedId)?.available || audioPending || !idle || !['failed','interrupted'].includes(session?.status) ||
    (Boolean((segments.get(selectedId)||[]).length) && !session?.has_checkpoints) ||
    !savedProfile.configured || !agreement.accepted ||
    (Boolean((segments.get(selectedId)||[]).length) && Boolean(session?.transcript_provider || session?.capture?.provider) &&
      (session.transcript_provider || session.capture.provider) !== savedProfile.provider);
  text('resume-meeting','Resume cloud transcription');
  $('delete-meeting').disabled = !idle || !$('delete-consent').checked;
  $('apply-retention').disabled = !idle || !$('retention-consent').checked;
  const usage=cloudUsage.get(selectedId);
  text('cloud-usage',usage ? `${usage.providers?.join(', ') || providerName()} upload reservations: ${usage.requests} · reserved estimate $${Number(usage.estimated_usd).toFixed(4)}. Actual billing depends on your provider account.` : '');
  const progress=liveProgress.get(selectedId);
  text('live-progress',progress ? `${progress.mode === 'catching_up' ? 'Catching up' : 'Cloud streaming'} · ${formatDuration(progress.lag_ms)} behind recorded audio` : '');
  if (session?.recovery) text('session-progress','Recovered / interrupted audio. Only authenticated complete frames were retained; missing tail duration is unknown. Capture was not restarted.');
  if (session?.status === 'failed' && session.failure) text('session-progress',`${session.failure.message} [${session.failure.code}]`);
  const rows = [...(segments.get(selectedId) || [])].sort((a,b)=>a.start_ms-b.start_ms);
  const emptyMessage = !replayAvailable ? 'Storage is unavailable. Reconnect or restart the local server.' :
    session?.status === 'processing' ? 'Audio capture stopped. Transcribing the remaining audio batches…' :
    session?.source === 'meeting' && liveStates.has(session.status) ? 'Recording audio. Waiting for early speech and the first stable words…' :
    session?.status === 'failed' ? 'This session failed. Check the message above.' :
    liveStates.has(session?.status) ? 'Listening for the first confirmed words…' : 'No speech has been transcribed in this session.';
  renderSpeakers(session,rows);
  renderPeople();
  renderTranscript(session,rows,rows.length ? '' : emptyMessage);

}
function connect() {
  if (connecting || authenticated) return;
  if (requireLaunchKey && !key) { if (!$('unlock').open) $('unlock').showModal(); return; }
  connecting=true; $('reconnect').disabled=true;
  $('connection').textContent = 'Connecting…';
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  const connection=socket;
  socket.onopen = () => { if (socket === connection && requireLaunchKey) connection.send(key); };
  socket.onmessage = event => {
    if (socket !== connection) return;
    const data = JSON.parse(event.data);
    if(data.type==='people_changed') {
      people=data.people; personPending=false;
      for(const session of data.sessions || []) sessions.set(session.id,session);
      for(const [sid,rows] of Object.entries(data.segments || {})) { segments.set(sid,rows); rollingDrafts.delete(sid); }
      for(const [sid,value] of Object.entries(data.notes || {})) notes.set(sid,value);
      for(const [sid,value] of Object.entries(data.grounded_summaries || {})) { if(value) savedSummaries.set(sid,value); else savedSummaries.delete(sid); }
      $('forget-person-confirm').checked=false; render(); return;
    }
    if(data.type==='rolling_notes') { rollingDrafts.set(data.session_id,{summary:data.summary,provider:data.provider,model:data.model}); summaryBudgets.set(data.session_id,data.budget); render(); return; }
    if(data.type==='rolling_notes_error') { text('summary-status',data.message); return; }
    if (data.type === 'audio_state') {
      audioStates.set(data.session_id,data.audio); render(); return;
    }
    if (data.type === 'audio_cleanup_error') { notice(data.message); return; }
    if (data.type === 'audio_playback') {
      audioPending=false;
      if(data.session_id === selectedId) {
        clearPlayback(); audioSession=selectedId;
        const bytes=Uint8Array.from(atob(data.wav_base64),c=>c.charCodeAt(0));
        audioURL=URL.createObjectURL(new Blob([bytes],{type:'audio/wav'}));
        $('audio-player').src=audioURL; $('audio-player').hidden=false;
        text('audio-playback-status','Ready to play. No cloud request was made.');
      }
      render(); return;
    }
    if (data.type === 'agreement') {
      agreement=data.agreement; render(); return;
    }
    if (data.type === 'snapshot') {
      agreement=data.agreement || {accepted:false,version:null};
      audioStates=new Map(Object.entries(data.audio_states || {})); audioPending=false;
      people=data.people || []; personPending=false;
      summaryBudgets=new Map(Object.entries(data.summary_budgets || {}));
      rollingDrafts=new Map(Object.entries(data.rolling_notes || {}).filter(([,v])=>v));
      speakerPending=false; summaryPending=false; exportPending=false;
      summaryProvider=data.summary_provider || {configured:false};
      summaryActiveId=data.summary_active_id || null; savedSummaries=new Map(Object.entries(data.grounded_summaries || {}));
      connecting=false; notice('');
      authenticated = true; recordingAvailable = data.recording_available === true; recordPending=false;
      notes = new Map(Object.entries(data.notes || {}));
      speakerTurns = new Map(Object.entries(data.speaker_turns || {})); liveProgress.clear();
      transcription=data.transcription || {cloud:false,configured:true};
      modelProfiles=data.transcription_models || {};
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
      clearSource();
      $('storage-status').textContent = {encrypted:'Encrypted on this machine', 'public-demo':'Public demo · unencrypted', memory:'Temporary memory'}[data.storage] || 'Storage status unavailable';
      $('connection').textContent = '● Connected locally';
      if (!replayAvailable) notice('Replay storage is unavailable. Restart the local server.');
      sessions = new Map(data.sessions.map(s => [s.id,s]));
      segments = new Map(Object.entries(data.segments)); activeId = data.active_id;
      selectedId = activeId || (sessions.has(selectedId) ? selectedId : data.sessions[0]?.id) || null;
      updateDiscovery(data.discovery);
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
    } else if (data.type === 'auto_call_status') {
      autoCallState=data; renderDiscovery();
    } else if (data.type === 'summary_started') {
      summaryPending=false; summaryActiveId=data.session_id;  render();
    } else if (data.type === 'summary_ready' || data.type === 'summary_error') {
      summaryPending=false; summaryActiveId=null;
      if(data.budget) summaryBudgets.set(data.session_id,data.budget);
      if(data.summary) savedSummaries.set(data.session_id,data.summary);
      if(data.session) sessions.set(data.session.id,data.session);
      if(data.session_id === selectedId) text('summary-status',data.message || 'Summary saved. Review its sources.'); render();
    } else if (data.type === 'markdown_export') {
      exportPending=false;
      const url=URL.createObjectURL(new Blob([data.content],{type:'text/markdown;charset=utf-8'}));
      const link=document.createElement('a'); link.href=url; link.download=data.filename; link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000); text('summary-status','Plaintext Markdown download prepared.'); render();
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
      audioPending=false; recordPending=false; notice(data.message); render();
    } else if (data.type === 'session_deleted') {
      savedSummaries.delete(data.session_id); sessions.delete(data.session_id); segments.delete(data.session_id); notes.delete(data.session_id);
      speakerTurns.delete(data.session_id); languageLocks.delete(data.session_id); liveProgress.delete(data.session_id);
      recordPending=false; if (selectedId === data.session_id) selectedId=null;
      $('delete-consent').checked=false; render();
    } else if (data.type === 'retention_complete') {
      recordPending=false; $('retention-consent').checked=false; notice(`Removed ${data.deleted} expired meetings.`); render();
    } else if (data.type === 'language_locked') {
      languageLocks.set(data.session_id,data); render();
    } else if (data.type === 'speakers_corrected') {
      speakerPending=false; segments.set(data.session_id,data.segments); notes.set(data.session_id,data.notes);
      savedSummaries.set(data.session_id,data.summary || null);
      text('speaker-edit-status','Speaker corrections saved.'); render();
    } else if (data.type === 'transcript_replaced') {
      segments.set(data.session_id,data.segments); if(savedSummaries.get(data.session_id)) savedSummaries.get(data.session_id).stale=true; text('transcript-update','Transcript replaced with the completed pass.'); render();
    } else if (data.type === 'notes') {
      notes.set(data.session_id,data.notes); render();
    } else if (data.type === 'recording_error') {
      audioPending=false; recordPending=false; notice(data.message); render();
    } else if (data.type === 'session') {
      if (data.session.source === 'meeting') { recordPending=false; }
      sessions.set(data.session.id,data.session);
      if (['completed','failed','interrupted'].includes(data.session.status)) liveProgress.delete(data.session.id);
      if (liveStates.has(data.session.status)) { const newActive=activeId !== data.session.id; activeId=data.session.id; if (newActive || !selectedId) selectedId=activeId; }
      else { if (activeId === data.session.id) { activeId=null; stopPending=false; } partials.delete(data.session.id); }
      render();
    } else if (data.type === 'segment') {
      if(savedSummaries.get(data.session_id)) savedSummaries.get(data.session_id).stale=true;
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
      audioPending=false; personPending=false;
      speakerPending=false; summaryPending=false; exportPending=false;
      if (data.context === 'meeting') {
        audioPending=false; recordPending=false; notice(data.message);
        if (data.request_id === pendingSource || data.request_id == null) { clearSource(data.message); }
        render();
      } else { stopPending=false; notice(data.message); render(); }
    }
    else if (data.type === 'reconnect_required') notice('The connection fell behind. Reconnecting restores saved text.');
  };
  socket.onclose = event => {
    if (socket !== connection) return;
    connecting=false; authenticated=false; personPending=false; audioPending=false; stopPending=false; recordPending=false; partials.clear();
    clearSource('Connection lost. Reconnect and choose the source again.'); render();
    $('connection').textContent='Disconnected';
    if (event.code === 1008) { key=''; notice('Launch key rejected. Enter the current key from your server terminal.'); if (!$('unlock').open) $('unlock').showModal(); }
    else notice('Connection lost. Recording may continue. Press Reconnect to restore status and Stop controls.');
  };
  socket.onerror = () => { if (socket === connection) notice('Could not connect to the local server. Check that it is running, then press Reconnect.'); };
  renderDiscovery();
}
$('unlock-form').onsubmit = () => { key=$('launch-key').value.trim(); $('launch-key').value=''; setTimeout(connect,0); };
$('replay-form').onsubmit = event => { event.preventDefault(); notice(''); send({action:'start',fixture:$('fixture').value,title:$('replay-title').value,consent_confirmed:agreement.accepted}); };
$('stop').onclick = () => { stopPending=send({action:'stop',session_id:activeId}); render(); };
$('new-session').onclick = () => { if (activeId) { selectedId=activeId; render(); return; } selectedId=null; $('delete-consent').checked=false; $('retention-consent').checked=false;  $('title').value='Meeting conversation';   $('partial').textContent=''; notice(''); render(); $('manual-recording').open=true; $('meeting-model').focus(); };
$('fixture').onchange = render;
$('source-form').onsubmit = event => { event.preventDefault(); chooseSource(); };
$('meeting-source').onchange = () => { if (busy()) return; clearSource(); renderDiscovery(); };
$('meeting-microphone').onchange = () => { if (busy()) return; microphoneNeedsChoice = !$('meeting-microphone').value; clearSource(); renderDiscovery(); };
renderDiscovery();
connect();

$('record-meeting').onclick = () => {
  if ($('record-meeting').disabled || !chosenSource) return;
  recordPending=send({action:'record',request_id:++sourceRequest,scope:chosenSource.scope,
    app_id:chosenSource.app_id,microphone_id:chosenSource.microphone_id,
    candidate_id:null,title:$('title').value.trim() || 'Meeting test',
    model:selectedModel(),duration_s:Number($('meeting-duration').value || 300),consent_confirmed:agreement.accepted,cloud_consent:transcription.cloud && agreement.accepted,language:$('meeting-language').value || 'auto'});
  renderDiscovery();
};

$('enable-microphone').onclick = () => send({action:'microphone_permission'});

setInterval(() => { if (activeId) renderDiscovery(); },1000);

$('reprocess-meeting').onclick = () => {
  if ($('reprocess-meeting').disabled) return;
  recordPending=send({action:'reprocess',session_id:selectedId,model:selectedModel(true),language:$('reprocess-language').value || 'auto',cloud_consent:profile(true).cloud && agreement.accepted});

  render();
};

$('title').oninput = renderDiscovery;
$('reconnect').onclick = () => connect();

function savedAction(action) {
  const button=$(action === 'recover' ? 'recover-meeting' : 'resume-meeting');
  if (button.disabled) return;
  recordPending=send({action,session_id:selectedId,model:selectedModel(true),language:$('reprocess-language').value || 'auto',cloud_consent:profile(true).cloud && agreement.accepted});  render();
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


$('meeting-language').onchange=renderDiscovery;
$('reprocess-language').onchange=render;
$('meeting-model').onchange=()=>{  render(); };
$('saved-model').onchange=()=>{  render(); };

$('edit-speaker').onchange=renderMergeTargets;
$('speaker-name').oninput=correctionButtons;
$('merge-target').onchange=correctionButtons;
function correctSpeaker(action) {
  const button=$(action === 'rename_speaker' ? 'rename-speaker' : 'merge-speaker');
  if(button.disabled) return;
  const rows=segments.get(selectedId)||[];
  speakerPending=send({action,session_id:selectedId,revision:rows[0]?.revision,
    speaker_id:$('edit-speaker').value,...(action === 'rename_speaker' ?
    {label:$('speaker-name').value.trim()} : {merge_target:$('merge-target').value})});
  text('speaker-edit-status',speakerPending ? 'Saving speaker corrections…' : 'Reconnect to save corrections.'); render();
}
$('rename-speaker').onclick=()=>correctSpeaker('rename_speaker');
$('merge-speaker').onclick=()=>correctSpeaker('merge_speaker');

$('generate-summary').onclick=()=>{
  if($('generate-summary').disabled) return;
  summaryPending=send({action:'summarize',session_id:selectedId,revision:(segments.get(selectedId)||[])[0]?.revision,
    provider:summaryProvider.provider,model:summaryProvider.model,text_consent:agreement.accepted});
   render();
};
$('download-markdown').onclick=()=>{
  if($('download-markdown').disabled) return;
  exportPending=send({action:'export_markdown',session_id:selectedId,revision:(segments.get(selectedId)||[])[0]?.revision}); render();
};

$('accept-call').onclick=()=>{
  if(!promptCandidate || $('accept-call').disabled) return;
  const candidate=promptCandidate; offeredCalls.add(candidate.id);
  recordPending=send({action:'accept_call',candidate_id:candidate.id,app_id:candidate.app_id,
    microphone_id:$('meeting-microphone').value || 'default',model:selectedModel(),duration_s:Number($('meeting-duration').value || 300),language:$('meeting-language').value || 'auto',
    consent_confirmed:true,automatic_summary:Boolean(summaryProvider.configured),
    ...(summaryProvider.configured ? {summary_provider:summaryProvider.provider,summary_model:summaryProvider.model,summary_endpoint:summaryProvider.endpoint} : {})});
  closeCallPrompt(); render();
};
function declineCall() {
  if(promptCandidate) { offeredCalls.add(promptCandidate.id); send({action:'dismiss_candidate',candidate_id:promptCandidate.id}); }
  closeCallPrompt(); $('watch-heading').focus();
}
$('decline-call').onclick=declineCall;
$('call-permission').oncancel=event=>{event.preventDefault();declineCall();};
$('keep-recording').onclick=()=>{if(!$('keep-recording').disabled && activeId) send({action:'keep_recording',session_id:activeId});};

$('agree-app').onclick=()=>{if(agreement.version) send({action:'accept_agreement',version:agreement.version});};
$('app-agreement').oncancel=event=>event.preventDefault();
$('revoke-agreement').onclick=()=>send({action:'revoke_agreement'});

function clearPlayback() {
  const player=$('audio-player');
  if(player.pause) player.pause();
  player.src=''; player.hidden=true;
  if(player.load) player.load();
  if(audioURL) URL.revokeObjectURL(audioURL);
  audioURL=null; text('audio-playback-status','');
}
function renderAudio(session) {
  const saved=session?.source === 'meeting', state=audioStates.get(selectedId);
  $('saved-audio').hidden=!saved;
  if(audioSession !== selectedId || !state?.available) { clearPlayback(); audioSession=selectedId; }
  const idle=authenticated && !busy() && ![...sessions.values()].some(s=>s.status === 'processing');
  text('audio-retention-status',!state?.available ? state?.expired_at ? 'Audio expired. Your transcript and notes are preserved.' : 'Audio is unavailable. Saved text and notes remain readable.' : state.archived ? 'Archived · excluded from automatic audio expiry.' : `Audio expires ${new Date(state.expires_at).toLocaleString()}. Archive to keep it.`);
  text('archive-audio',state?.archived ? 'Unarchive audio' : 'Archive audio');
  $('archive-audio').disabled=!idle || !state?.available;
  $('load-audio').disabled=!idle || !state?.available;
  $('playback-track').disabled=!idle || !state?.available;
  for(const option of $('playback-track').options || []) option.disabled=!state?.tracks?.includes(option.value);
  if(state?.tracks?.length && !state.tracks.includes($('playback-track').value)) $('playback-track').value=state.tracks[0];
}
$('load-audio').onclick=()=>{
  if($('load-audio').disabled) return;
  audioPending=send({action:'play_audio',session_id:selectedId,stream:$('playback-track').value || 'mic',start_ms:Math.round(Number($('playback-minute').value || 0)*60000)});
  text('audio-playback-status','Authenticating and loading saved audio…'); render();
};
$('archive-audio').onclick=()=>{
  if(!$('archive-audio').disabled) send({action:'archive_audio',session_id:selectedId,archived:!audioStates.get(selectedId)?.archived});
};
$('playback-track').onchange=()=>clearPlayback();

$('meeting-duration').onchange=renderDiscovery;
$('transcript-previous').onclick=()=>{const total=(segments.get(selectedId)||[]).length;transcriptPageStart=Math.max(0,(transcriptPageStart===null ? Math.max(0,total-500) : transcriptPageStart)-500);render();};
$('transcript-next').onclick=()=>{const total=(segments.get(selectedId)||[]).length;const next=(transcriptPageStart || 0)+500;transcriptPageStart=next+500>=total ? null : next;render();};
function renderPeople() {
  const previous=$('person-select').value;
  $('person-select').replaceChildren();
  const empty=document.createElement('option');empty.value='';empty.textContent='Choose a person';$('person-select').append(empty);
  for(const person of people) {const option=document.createElement('option');option.value=person.id;option.textContent=person.canonical_name;$('person-select').append(option);}
  $('person-select').value=people.some(p=>p.id===previous) ? previous : '';
  const person=people.find(p=>p.id===$('person-select').value), idle=authenticated && !busy() && !personPending && !summaryActiveId && ![...sessions.values()].some(s=>s.status==='processing');
  $('person-learning').checked=Boolean(person?.learn_enabled);$('person-learning').disabled=!idle || !person;
  $('create-person').disabled=!idle || !$('person-name').value.trim();
  $('link-person').disabled=!idle || !person || !$('edit-speaker').value;
  $('unlink-person').disabled=!idle || !$('edit-speaker').value;
  $('forget-person').disabled=!idle || !person || !$('forget-person-confirm').checked;
}
$('person-select').onchange=()=>{ $('forget-person-confirm').checked=false;renderPeople(); };
$('person-name').oninput=renderPeople;
$('forget-person-confirm').onchange=renderPeople;
$('create-person').onclick=()=>{if(!$('create-person').disabled) personCommand({action:'create_person',name:$('person-name').value.trim()});};
$('person-learning').onchange=()=>personCommand({action:'set_person_learning',person_id:$('person-select').value,enabled:$('person-learning').checked});
function linkPerson(person_id) {personCommand({action:'link_person',person_id,session_id:selectedId,revision:(segments.get(selectedId)||[])[0]?.revision,speaker_id:$('edit-speaker').value});}
$('link-person').onclick=()=>{if(!$('link-person').disabled) linkPerson($('person-select').value);};
$('unlink-person').onclick=()=>{if(!$('unlink-person').disabled) linkPerson(null);};
$('forget-person').onclick=()=>{if(!$('forget-person').disabled) personCommand({action:'forget_person',person_id:$('person-select').value,confirmed:true});};

function personCommand(command) { personPending=send(command); renderPeople(); }
