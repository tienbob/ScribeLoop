'use strict';
const $ = id => document.getElementById(id);
let key = new URLSearchParams(location.hash.slice(1)).get('token') || '';
history.replaceState(null, '', location.pathname);
const emptyTemplate = $('transcript').firstElementChild.cloneNode(true);
let stopPending = false;
let socket, activeId = null, selectedId = null, sessions = new Map(), segments = new Map();
const liveStates = new Set(['loading', 'replaying']);
function send(value) { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value)); }
function notice(text) { $('notice').textContent = text; $('notice').hidden = !text; }
function renderHistory() {
  $('history').replaceChildren();
  [...sessions.values()].sort((a,b) => b.started_at-a.started_at).forEach(session => {
    const button = document.createElement('button');
    button.type = 'button'; button.classList.toggle('selected', session.id === selectedId);
    button.append(document.createTextNode(session.title));
    const meta = document.createElement('small');
    meta.textContent = new Date(session.started_at).toLocaleDateString(undefined,{month:'short',day:'numeric'}) + ' · ' + session.status;
    button.append(meta); button.onclick = () => { selectedId = session.id; render(); };
    $('history').append(button);
  });
}
function render() {
  renderHistory();
  const session = sessions.get(selectedId);
  $('session-heading').textContent = session ? session.title : 'The conversation';
  $('session-status').textContent = session ? session.status.toUpperCase() : 'READY WHEN YOU ARE';
  $('activity-dot').classList.toggle('active', Boolean(activeId));
  $('stop').hidden = !activeId;
  $('stop').disabled = stopPending;
  $('stop').textContent = stopPending ? 'Stopping…' : 'Stop replay';
  $('start').disabled = Boolean(activeId) || socket?.readyState !== WebSocket.OPEN || !$('fixture').value;
  if (activeId) $('start').textContent = sessions.get(activeId)?.status === 'loading' ? 'Loading model…' : 'Replay in progress';
  else $('start').textContent = 'Start replay ↗';
  const rows = segments.get(selectedId) || [];
  if (!rows.length && !session) $('transcript').replaceChildren(emptyTemplate.cloneNode(true));
  if (rows.length || session) {
    $('transcript').replaceChildren();
    if (!rows.length) {
      const p = document.createElement('p'); p.className = 'partial';
      p.textContent = session?.status === 'failed' ? 'Replay failed. Verify that the local ASR model is cached; no download is started automatically.' : liveStates.has(session?.status) ? 'Listening for the first confirmed words…' : 'No confirmed words in this replay.';
      $('transcript').append(p);
    }
    rows.forEach(row => {
      const article = document.createElement('article'); article.className = 'segment';
      const time = document.createElement('span'); time.className = 'timestamp';
      const seconds = Math.floor(row.start_ms/1000); time.textContent = `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
      const p = document.createElement('p'); p.textContent = row.text;
      article.append(time,p); $('transcript').append(article);
    });
  }
}
function connect() {
  if (!key) { $('unlock').showModal(); return; }
  $('connection').textContent = 'Connecting…';
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  socket.onopen = () => socket.send(key);
  socket.onmessage = event => {
    const data = JSON.parse(event.data);
    if (data.type === 'snapshot') {
      $('connection').textContent = '● Connected locally';
      sessions = new Map(data.sessions.map(s => [s.id,s]));
      segments = new Map(Object.entries(data.segments)); activeId = data.active_id;
      selectedId = activeId || (sessions.has(selectedId) ? selectedId : data.sessions[0]?.id) || null;
      $('fixture').replaceChildren();
      data.fixtures.forEach(f => { const option = document.createElement('option'); option.value=f.id; option.textContent=`${f.id} · ${Math.round(f.duration_s)}s`; $('fixture').append(option); });
      if (!data.fixtures.length) notice('No public test fixture is configured on this server.');
      render();
    } else if (data.type === 'session') {
      sessions.set(data.session.id,data.session);
      if (liveStates.has(data.session.status)) { activeId=data.session.id; selectedId=activeId; }
      else { if (activeId === data.session.id) { activeId=null; stopPending=false; } $('partial').textContent=''; }
      render();
    } else if (data.type === 'segment') {
      const rows=segments.get(data.session_id)||[];
      if (!rows.some(r => r.id === data.segment.id)) rows.push(data.segment);
      segments.set(data.session_id,rows); $('partial').textContent=''; render();
    } else if (data.type === 'partial') {
      if (selectedId === data.session_id) $('partial').textContent=data.segment.text;
    } else if (data.type === 'error') notice(data.message);
    else if (data.type === 'reconnect_required') notice('The connection fell behind. Reconnecting restores saved text.');
  };
  socket.onclose = event => {
    $('connection').textContent='Disconnected'; $('start').disabled=true;
    if (event.code === 1008) { key=''; notice('Launch key rejected. Enter the current key from your server terminal.'); $('unlock').showModal(); }
    else notice('Connection lost. Reload this page and enter your launch key to restore saved sessions.');
  };
  socket.onerror = () => notice('Could not connect to the local server. Check that it is running.');
}
$('unlock-form').onsubmit = () => { key=$('launch-key').value.trim(); $('launch-key').value=''; setTimeout(connect,0); };
$('replay-form').onsubmit = event => { event.preventDefault(); notice(''); send({action:'start',fixture:$('fixture').value,title:$('title').value,consent_confirmed:$('consent').checked}); };
$('stop').onclick = () => { send({action:'stop',session_id:activeId}); stopPending=true; render(); };
$('new-session').onclick = () => { if (activeId) { selectedId=activeId; render(); return; } selectedId=null; $('title').value='Development listening session'; $('consent').checked=false; $('partial').textContent=''; notice(''); render(); $('title').focus(); };
$('fixture').onchange = render;
connect();
