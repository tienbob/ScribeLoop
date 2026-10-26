// Run the actual UI script against a minimal DOM/socket harness. No browser or
// dependencies required; these regressions cover observable client state only.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor() {
    this.children=[]; this.value=''; this.disabled=false; this.hidden=false;
    this.textContent=''; this.open=false; this.parentElement=null; this.attributes={}; this.classList={toggle() {}};
  }
  append(...nodes) { nodes.forEach(node=>{ if(node.parentElement) node.remove(); this.children.push(node); node.parentElement=this; }); }
  insertBefore(node,reference) { if(node.parentElement) node.remove(); const i=reference ? this.children.indexOf(reference) : this.children.length; this.children.splice(i,0,node); node.parentElement=this; }
  remove() { if(this.parentElement) { const p=this.parentElement; p.children.splice(p.children.indexOf(this),1); this.parentElement=null; } }
  setAttribute(key,value) { this.attributes[key]=value; }
  replaceChildren(...nodes) { this.children.forEach(node=>{node.parentElement=null;}); this.children=[]; this.append(...nodes); }
  cloneNode() { return new Element(); }
  showModal() { this.open=true; }
  focus() {}
  click() { this.clicked=true; }
  set innerHTML(value) { throw new Error('Transcript must not use innerHTML'); }
}
const elements=new Map();
const get=id => { if (!elements.has(id)) elements.set(id,new Element()); return elements.get(id); };
function transcriptLine(index) {
  const body=get('transcript').children.at(index).children[1];
  const meta=body.children[0], value=body.children[1].textContent;
  if(meta.hidden) return value;
  return `${meta.children[2].textContent} · ${meta.children[1].textContent}: ${value}`;
}
get('transcript').firstElementChild=new Element();
get('fixture').value='public';
get('start').disabled=true;
get('title').value='Meeting conversation';
get('replay-title').value='Public sample replay';
let fragmentRemoved=false;
const sockets=[];
class Socket {
  static OPEN=1;
  constructor() { this.readyState=0; this.sent=[]; sockets.push(this); }
  send(value) { this.sent.push(value); }
}
vm.runInNewContext(fs.readFileSync('core/scribeloop_core/api/static/app.js','utf8'),{
  document:{getElementById:get,createElement:()=>new Element(),createTextNode:text=>({textContent:text})},
  WebSocket:Socket,location:{hash:'#token=test-launch-key',pathname:'/',protocol:'http:',host:'127.0.0.1:8757'},
  URL:{createObjectURL:()=> 'blob:audio',revokeObjectURL(){}},Blob,atob,
  history:{replaceState(){fragmentRemoved=true;}}, setTimeout, setInterval:()=>null, URLSearchParams,
});
const socket=sockets[0];
socket.readyState=Socket.OPEN;
socket.onopen();
assert.equal(socket.sent[0],'test-launch-key');
assert.equal(fragmentRemoved,true);
get('fixture').onchange();
assert.equal(get('start').disabled,true,'Open socket is not authenticated yet');
const session=(id,status)=>({id,status,title:id,started_at:id==='active'?2:1});
const deliver=data=>socket.onmessage({data:JSON.stringify(data)});
const snapshot=(discovery,active=true)=>deliver({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v1'},fixtures:[{id:'public',duration_s:20}],
  sessions:[session('active',active ? 'replaying' : 'completed'),session('past','completed')],
  active_id:active ? 'active' : null,replay_available:true,storage:'public-demo',segments:{active:[],past:[]},discovery});
snapshot();
const originalHistoryButton=get('history').children[0];
assert.equal(get('storage-status').textContent,'Public demo · unencrypted');
deliver({type:'partial',session_id:'active',segment:{text:'ephemeral words'}});
assert.equal(get('partial').textContent,'ephemeral words');
get('history').children[1].onclick();
assert.equal(get('partial').textContent,'','Switching history must hide another session’s partial');
get('history').children[0].onclick();
assert.equal(get('partial').textContent,'ephemeral words');
deliver({type:'segment',session_id:'active',segment:{id:'segment1',start_ms:0,text:'<script>literal text</script>'}});
assert.equal(get('partial').textContent,'');
assert.equal(transcriptLine(0),'<script>literal text</script>');
assert.equal(get('history').children[0],originalHistoryButton,'Preserve focused history buttons');
const originalTranscript=get('transcript').children[0];
deliver({type:'language_locked',session_id:'active',mode:'manual',language:'en'});
assert.equal(get('transcript').children[0],originalTranscript,'Status updates must preserve transcript nodes');
socket.readyState=3;
socket.onclose({code:1006});
assert.equal(get('start').disabled,true);
assert.equal(get('stop').disabled,true,'Disconnected replay must not offer a working Stop button');
const sent=socket.sent.length;
get('stop').onclick();
assert.equal(socket.sent.length,sent);
assert.equal(get('stop').textContent,'Stop replay');
socket.readyState=Socket.OPEN;
snapshot();
deliver({type:'replay_unavailable',session_id:'active',message:'Storage failed'});
assert.equal(get('start').disabled,true);
assert.equal(get('stop').hidden,true);
assert.equal(get('session-status').textContent,'STORAGE UNAVAILABLE');
assert.equal(get('notice').textContent,'Storage failed');
const candidate={id:'server-epoch:teams:1',app_id:'teams',name:'<script>Teams</script>',kind:'meeting',
  evidence:['input_active','output_active'],confidence:'possible',selectable:true};
const discovery={status:'available',recording_enabled:false,
  apps:[{id:'teams',name:'Teams',kind:'meeting'},{id:'browser',name:'Browser',kind:'browser'}],
  microphones:[{id:'default',name:'System default microphone',verified:false}],candidates:[candidate]};
snapshot(discovery,false);
assert.equal(get('record-meeting').disabled,true);
assert.equal(get('select-source').disabled,true,'No implicit app/system selection');
assert.equal(get('meeting-candidates').children[0].children[0].children[0].textContent,candidate.name);
get('meeting-candidates').children[0].children[1].children[0].onclick();
let selected=JSON.parse(socket.sent.at(-1));
assert.equal(selected.action,'select_source');
assert.equal(selected.candidate_id,candidate.id);
assert.equal(get('select-source').disabled,true,'Wait for source validation');
deliver({type:'source_selected',request_id:selected.request_id,source:{name:'Teams',candidate_id:candidate.id}});
assert.match(get('source-status').textContent,/No recording started/);
assert.equal(get('record-meeting').disabled,true);
get('meeting-candidates').children[0].children[1].children[1].onclick();
assert.equal(JSON.parse(socket.sent.at(-1)).action,'dismiss_candidate');
deliver({type:'discovery',discovery:{...discovery,status:'unavailable'}});
assert.equal(get('meeting-candidates').children[0].children[1].children[0].disabled,true);
assert.match(get('source-status').textContent,/no longer current/);
get('meeting-source').value='system'; get('meeting-source').onchange();
assert.equal(get('select-source').disabled,false,'Manual choice survives observer failure');
get('source-form').onsubmit({preventDefault(){}});
const system=JSON.parse(socket.sent.at(-1));
assert.equal(system.scope,'system'); assert.equal(system.app_id,null);
// An earlier acknowledgement must not replace a newer source preference.
deliver({type:'source_selected',request_id:selected.request_id,source:{name:'Old Teams'}});
assert.equal(get('select-source').disabled,true);
deliver({type:'source_selected',request_id:system.request_id,source:{name:'All system audio'}});
assert.match(get('source-status').textContent,/All system audio/);
get('meeting-source').value='app:teams'; get('meeting-source').onchange();
get('source-form').onsubmit({preventDefault(){}});
selected=JSON.parse(socket.sent.at(-1));
deliver({type:'error',context:'meeting',request_id:selected.request_id,message:'Choose again manually.'});
assert.equal(get('source-status').textContent,'Choose again manually.');
assert.equal(get('select-source').disabled,false);
deliver({type:'discovery',discovery:{...discovery,apps:[],candidates:[]}});
assert.equal(get('meeting-source').value,'');
assert.equal(get('select-source').disabled,true);
deliver({type:'discovery',discovery:{...discovery,permission:'denied',
  microphones:[...discovery.microphones,{id:'usb-mic',name:'USB <literal>',enumerated:true}]}});
assert.match(get('microphone-permission').textContent,/denied/);
get('meeting-source').value='system'; get('meeting-source').onchange();
get('meeting-microphone').value='usb-mic'; get('meeting-microphone').onchange();
get('source-form').onsubmit({preventDefault(){}});
assert.equal(JSON.parse(socket.sent.at(-1)).microphone_id,'usb-mic');
deliver({type:'discovery',discovery});
assert.equal(get('meeting-microphone').value,'','Disconnected mic must not silently become the default');
assert.equal(get('select-source').disabled,true);
const sourceOption = get('meeting-source').children[1];
const micOption = get('meeting-microphone').children[1];
deliver({type:'discovery',discovery});
assert.equal(get('meeting-source').children[1],sourceOption);
assert.equal(get('meeting-microphone').children[1],micOption);
assert.equal(get('meeting-candidates').children[0].children[1].children[0].disabled,true);
const sentBeforeBlankMic = socket.sent.length;
get('source-form').onsubmit({preventDefault(){}});
assert.equal(socket.sent.length,sentBeforeBlankMic);
assert.equal(get('meeting-microphone').value,'','Require a new choice after subsequent refreshes too');
socket.readyState=3; socket.onclose({code:1006});
assert.equal(get('meeting-source').disabled,true);
assert.equal(get('select-source').disabled,true);
assert.equal(get('record-meeting').disabled,true);
assert.ok(socket.sent.slice(1).every(text=>!['record','start_recording'].includes(JSON.parse(text).action)));
// Encrypted recording is an explicit, consented action; discovery never starts it.
const sentBeforeRecording = socket.sent.length;
socket.readyState=Socket.OPEN;
snapshot({...discovery,permission:'authorized'},false);
assert.equal(get('record-meeting').disabled,true,'Old snapshots cannot enable recording');
deliver({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v1'},fixtures:[],sessions:[],active_id:null,segments:{},notes:{},
  recording_available:true,storage:'encrypted',discovery:{...discovery,permission:'authorized'}});
get('meeting-source').value='system'; get('meeting-source').onchange();
get('meeting-microphone').value='default'; get('meeting-microphone').onchange();
get('source-form').onsubmit({preventDefault(){}});
const request=JSON.parse(socket.sent.at(-1));
deliver({type:'source_selected',request_id:request.request_id,source:{scope:'system',app_id:null,
  microphone_id:'default',name:'All system audio'}});
assert.equal(get('record-meeting').disabled,false);
get('record-meeting').onclick();
assert.equal(JSON.parse(socket.sent.at(-1)).action,'record');
assert.equal(JSON.parse(socket.sent.at(-1)).consent_confirmed,true);
assert.equal(JSON.parse(socket.sent.at(-1)).language,'auto');
assert.equal(get('record-meeting').disabled,true,'Prevent double Record');
assert.equal(get('meeting-source').disabled,true);
assert.equal(get('meeting-microphone').disabled,true);
assert.match(get('scope-warning').textContent,/All system audio.*all apps/);
deliver({type:'session',session:{id:'meeting',source:'meeting',title:'Call',status:'recording',started_at:Date.now(),capture:{scope:'application',kind:'browser',name:'Brave',microphone_name:'USB mic'}}});
assert.equal(get('stop').textContent,'Stop recording');
assert.equal(get('recording-bar').hidden,false);
assert.match(get('active-scope').textContent,/All tabs in Brave.*USB mic/);
assert.equal(get('recording-state').textContent,'RECORDING','Timer must not be inside the announcing status');
assert.equal(get('meeting-source').disabled,true);
const beforeLockedChoice=socket.sent.length;
get('source-form').onsubmit({preventDefault(){}});
assert.equal(socket.sent.length,beforeLockedChoice);
deliver({type:'discovery',discovery:{...discovery,status:'unavailable',permission:'authorized'}});
assert.doesNotMatch(get('discovery-status').textContent,/no audio is being captured/);
assert.match(get('active-scope').textContent,/All tabs in Brave.*USB mic/);
socket.readyState=3; socket.onclose({code:1006});
assert.equal(get('recording-bar').hidden,false);
assert.equal(get('reconnect').hidden,false);
assert.match(get('recording-state').textContent,/unverified/);
assert.equal(get('stop').disabled,true);
socket.readyState=Socket.OPEN;
deliver({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v1'},fixtures:[],sessions:[{id:'meeting',source:'meeting',title:'Call',status:'recording',started_at:Date.now(),capture:{scope:'application',kind:'browser',name:'Brave',microphone_name:'USB mic'}}],
 active_id:'meeting',segments:{meeting:[]},notes:{},storage:'encrypted',recording_available:true,discovery:{...discovery,permission:'authorized'}});
assert.match(get('active-scope').textContent,/All tabs in Brave.*USB mic/);
deliver({type:'session',session:{id:'meeting',source:'meeting',title:'Call',status:'processing',started_at:Date.now()}});
assert.equal(get('stop').hidden,true);
assert.equal(get('record-meeting').disabled,true);
deliver({type:'notes',session_id:'meeting',notes:'<script>literal notes</script>'});
assert.equal(get('meeting-notes').textContent,'<script>literal notes</script>');
deliver({type:'session',session:{id:'meeting',source:'meeting',title:'Call',status:'completed',started_at:Date.now()}});
deliver({type:'audio_state',session_id:'meeting',audio:{available:true,tracks:['mic','loopback'],expires_at:Date.now()+86400000}});
assert.equal(get('reprocess-meeting').disabled,false);
get('reprocess-meeting').onclick();
assert.equal(JSON.parse(socket.sent.at(-1)).action,'reprocess');
assert.equal(JSON.parse(socket.sent.at(-1)).language,'auto');
deliver({type:'transcript_replaced',session_id:'meeting',segments:[{id:'revised',text:'Tiếng Việt',stream:'mic',start_ms:0}]});
assert.equal(transcriptLine(-1),'Microphone · Speaker unknown: Tiếng Việt');
deliver({type:'language_locked',session_id:'meeting',mode:'auto',language:'vi',confidence:.95});
assert.match(get('language-status').textContent,/Detected: Tiếng Việt.*fixed/);
assert.ok(socket.sent.length > sentBeforeRecording);
socket.readyState=3; socket.onclose({code:1006});
get('reconnect').onclick();
assert.equal(sockets.length,2);
const recovered=sockets[1]; recovered.readyState=Socket.OPEN; recovered.onopen();
assert.equal(recovered.sent[0],'test-launch-key');
recovered.onmessage({data:JSON.stringify({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v1'},fixtures:[],sessions:[],active_id:null,
 segments:{},notes:{},recording_available:true,storage:'encrypted',discovery})});
assert.equal(get('reconnect').hidden,true);
socket.onclose({code:1006});
assert.equal(get('reconnect').hidden,true,'Ignore late events from the previous socket');
console.log('Frontend replay and meeting discovery regressions passed');
// Live streams retain each other's provisional text; labels preserve ambiguity.
recovered.onmessage({data:JSON.stringify({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v1'},fixtures:[],sessions:[{id:'live',source:'meeting',title:'Live',status:'recording',started_at:Date.now(),capture:{scope:'system'}}],active_id:'live',
 segments:{live:[]},notes:{},recording_available:true,storage:'encrypted',discovery})});
const liveEvent=data=>recovered.onmessage({data:JSON.stringify(data)});
liveEvent({type:'partial',session_id:'live',stream:'mic',segment:{text:'local draft'}});
liveEvent({type:'partial',session_id:'live',stream:'loopback',segment:{text:'remote draft'}});
assert.match(get('partial').textContent,/local draft/);
assert.match(get('partial').textContent,/remote draft/);
liveEvent({type:'segment',session_id:'live',segment:{id:'live1',stream:'mic',text:'confirmed local',start_ms:0,end_ms:1000}});
assert.match(get('partial').textContent,/remote draft/);
assert.doesNotMatch(get('partial').textContent,/local draft/);
liveEvent({type:'segment',session_id:'live',segment:{id:'live2',stream:'loopback',text:'remote words',start_ms:0,end_ms:1000}});
liveEvent({type:'speaker_turns',session_id:'live',turns:[{start_ms:0,end_ms:1000,label:'Speaker 1'},{start_ms:500,end_ms:1000,label:'Speaker 2'}]});
assert.match(transcriptLine(1),/Overlapping \/ uncertain speakers/);
liveEvent({type:'transcription_progress',session_id:'live',mode:'catching_up',lag_ms:15000});
assert.match(get('live-progress').textContent,/Catching up.*0:15/);
assert.equal(get('stop').disabled,false,'Backlog must not disable Stop');
assert.equal(get('delete-meeting').disabled,true,'No deletion during capture');
liveEvent({type:'session',session:{id:'live',source:'meeting',title:'Live',status:'interrupted',started_at:Date.now()}});
get('delete-consent').checked=true; get('delete-consent').onchange();
assert.equal(get('delete-meeting').disabled,false);
get('history').children[0].onclick();
assert.equal(get('delete-consent').checked,false,'Changing selected meeting clears permanent-delete consent');
assert.equal(get('delete-meeting').disabled,true);
console.log('Live transcript, overlap labels and saved-session control regressions passed');
// Accepted app agreement authorizes new/saved uploads; replay does not
// load a local model or masquerade as storage failure when disabled.
liveEvent({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v1'},fixtures:[],sessions:[],active_id:null,segments:{},notes:{},
 recording_available:true,replay_available:true,replay_enabled:false,storage:'encrypted',
 transcription:{cloud:true,configured:true,provider:'groq',model:'whisper-large-v3-turbo',budget_usd:.05},
 discovery:{...discovery,permission:'authorized'}});
get('meeting-source').value='system';get('meeting-source').onchange();
get('source-form').onsubmit({preventDefault(){}});
const cloudSelection=JSON.parse(recovered.sent.at(-1));
liveEvent({type:'source_selected',request_id:cloudSelection.request_id,source:{scope:'system',name:'All system audio',microphone_id:'default'}});
assert.equal(get('record-meeting').disabled,false);
get('record-meeting').onclick();
assert.equal(JSON.parse(recovered.sent.at(-1)).cloud_consent,true);
liveEvent({type:'session',session:{id:'cloud',title:'Cloud',source:'meeting',status:'completed',started_at:Date.now(),has_checkpoints:true}});
get('history').children[0].onclick();
liveEvent({type:'audio_state',session_id:'cloud',audio:{available:true,tracks:['mic','loopback'],expires_at:Date.now()+86400000}});
assert.equal(get('reprocess-meeting').disabled,false);
get('reprocess-meeting').onclick();
assert.equal(JSON.parse(recovered.sent.at(-1)).cloud_consent,true);
assert.notEqual(get('session-status').textContent,'STORAGE UNAVAILABLE');
console.log('App-authorized cloud uploads and disabled-local-replay regressions passed');

// Speaker identities are persisted with words, independently of delayed turns.
liveEvent({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v1'},fixtures:[],sessions:[{id:'people',source:'meeting',title:'People',status:'completed',started_at:Date.now()}],active_id:null,recording_available:true,replay_enabled:false,transcription:{provider:'deepgram',cloud:true,configured:true,speaker_labels:true,streaming:true,auto_language:false,default_language:'en',model:'nova-3',budget_usd:.15},segments:{people:[{id:'a',stream:'loopback',start_ms:0,end_ms:300,text:'Hello',speaker_label:'Speaker 1'},{id:'b',stream:'loopback',start_ms:300,end_ms:500,text:'Hi',speaker_label:'Speaker 2'}]}});
assert.equal(get('meeting-language').value,'en');
assert.match(get('cloud-provider').textContent,/Deepgram/);
assert.match(get('speaker-status').textContent,/Anonymous speaker labels/);
assert.match(transcriptLine(0),/Speaker 1: Hello/);
assert.match(transcriptLine(1),/Speaker 2: Hi/);
console.log('Deepgram streaming speaker labels and explicit language regressions passed');

liveEvent({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v1'},fixtures:[],sessions:[{id:'multi',source:'meeting',title:'Languages',status:'recording',started_at:Date.now()}],active_id:'multi',recording_available:true,transcription:{provider:'deepgram',cloud:true,configured:true,speaker_labels:true,streaming:true,auto_language:true,default_language:'auto',model:'nova-3'},segments:{multi:[{id:'one',stream:'loopback',start_ms:0,end_ms:500,text:'Hello',speaker_label:'Speaker 1',language:'en'},{id:'two',stream:'mic',start_ms:500,end_ms:1000,text:'Hola',speaker_label:'Speaker 2',language:'es'}]},language_locks:{multi:{language:'multi',mode:'multilingual'}}});
assert.match(get('language-status').textContent,/Auto.*Recognized: English, Spanish/);
assert.equal(get('speaker-list').children.length,2);
assert.match(get('speaker-count').textContent,/2 SPEAKER LABELS/);
assert.equal(get('transcript').children[0].children[1].children[1].textContent,'Hello');
assert.equal(get('transcript').children[1].children[1].children[0].children[3].textContent,'Spanish');
assert.equal(get('meeting-language').disabled,true,'Language stays stable while recording');
console.log('Speaker roster, separate speech text and automatic multilingual status passed');

const cloudProfile={cloud:true,configured:true,provider:'deepgram',model:'nova-3',streaming:true,auto_language:true,languages:{auto:'Auto',en:'English',vi:'Vietnamese'},default_language:'auto'};
get('meeting-model').value='deepgram';get('saved-model').value='deepgram';
get('meeting-language').options=['auto','en','vi'].map(value=>({value}));
get('reprocess-language').options=['auto','en','vi'].map(value=>({value}));
liveEvent({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v2'},fixtures:[],sessions:[],active_id:null,segments:{},notes:{},recording_available:true,
 transcription:cloudProfile,transcription_models:{deepgram:cloudProfile},discovery:{...discovery,permission:'authorized'}});
assert.match(get('model-help').textContent,/Cloud.*Stop finalizes/);
assert.equal(get('transcript-feature').textContent,'Live transcript');
assert.ok(!fs.readFileSync('core/scribeloop_core/api/static/index.html','utf8').includes('value="moss-q6-k"'));
console.log('Cloud-only selection and explicit rerun copy passed');

liveEvent({type:'session',session:{id:'local',source:'meeting',title:'Local',status:'completed',started_at:Date.now(),capture:{provider:'local-moss'}}});
get('new-session').onclick();
get('meeting-model').value='deepgram';get('meeting-model').onchange();
get('meeting-microphone').value='default';get('meeting-microphone').onchange();
get('meeting-source').value='microphone';get('meeting-source').onchange();
get('source-form').onsubmit({preventDefault(){}});
const micSelection=JSON.parse(recovered.sent.at(-1));
assert.equal(micSelection.scope,'microphone');
assert.equal(micSelection.app_id,null);
liveEvent({type:'source_selected',request_id:micSelection.request_id,source:{scope:'microphone',name:'Microphone only',microphone_id:'default',microphone_name:'Built-in microphone'}});
assert.match(get('scope-warning').textContent,/Only Built-in microphone/);
assert.equal(get('record-meeting').textContent,'Record microphone ↗');
assert.equal(get('record-meeting').disabled,false);
get('record-meeting').onclick();
assert.equal(JSON.parse(recovered.sent.at(-1)).scope,'microphone');
assert.equal(JSON.parse(recovered.sent.at(-1)).model,'deepgram');
liveEvent({type:'session',session:{id:'mic',source:'meeting',title:'Mic',status:'recording',started_at:Date.now(),capture:{scope:'microphone',engine:'deepgram'}}});
liveEvent({type:'segment',session_id:'mic',segment:{id:'mic1',stream:'mic',start_ms:0,end_ms:500,text:'Live microphone words',speaker_label:'Speaker 1'}});
assert.match(transcriptLine(0),/Microphone.*Live microphone words/);
assert.match(get('active-scope').textContent,/Microphone only/);
assert.equal(get('stop').disabled,false);
console.log('Microphone-only source, live transcript and upload authorization passed');
liveEvent({type:'session',session:{id:'mic',source:'meeting',title:'Mic',status:'failed',started_at:Date.now(),failure:{code:'capture_clock_discontinuity',stage:'capture',message:'Native audio timestamps jumped.'}}});
assert.match(get('session-progress').textContent,/Native audio timestamps jumped.*capture_clock_discontinuity/);

const demoElements=new Map();
const demoGet=id=>{if(!demoElements.has(id))demoElements.set(id,new Element());return demoElements.get(id);};
demoGet('transcript').firstElementChild=new Element();
const demoSockets=[], demoDownloads=[], demoCreated=[];
class DemoSocket extends Socket { constructor(){super();demoSockets.push(this);} }
vm.runInNewContext(fs.readFileSync('core/scribeloop_core/api/static/app.js','utf8'),{
 document:{documentElement:{dataset:{requireLaunchKey:'false'}},getElementById:demoGet,createElement:tag=>{const e=new Element(); e.tag=tag; demoCreated.push(e); return e;},createTextNode:text=>({textContent:text})},
 URL:{createObjectURL(blob){demoDownloads.push(blob);return 'blob:demo';},revokeObjectURL(){}},Blob,
 WebSocket:DemoSocket,location:{hash:'',pathname:'/',protocol:'http:',host:'127.0.0.1:8757'},
 atob,history:{replaceState(){}},setTimeout,setInterval:()=>null,URLSearchParams,
});
assert.equal(demoSockets.length,1,'Demo opens connection without asking for a key');
demoSockets[0].readyState=Socket.OPEN;demoSockets[0].onopen();
assert.equal(demoSockets[0].sent.length,0,'No launch key or fake auth frame is sent');
assert.equal(demoGet('unlock').open,false);
console.log('Keyless local demo connects directly without an unlock dialog passed');

const ds=demoSockets[0], demoEvent=data=>ds.onmessage({data:JSON.stringify(data)});
const sid='d'.repeat(32), gen='a'.repeat(32), other='b'.repeat(32);
const speakerId=(generation,stream,n)=>`${sid}:deepgram:${generation}:${stream}:${n}`;
const correctedRows=[['loopback',gen,0],['loopback',gen,1],['mic',gen,0],['loopback',other,0]].map(([stream,generation,n],i)=>({
 id:`correct-${i}`,stream,start_ms:i*100,end_ms:(i+1)*100,text:`words ${i}`,revision:1,
 local_speaker_id:speakerId(generation,stream,n),display_speaker_id:speakerId(generation,stream,n),speaker_label:i<2 ? 'Alex' : `Speaker ${i+1}`,
}));
demoEvent({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v1'},fixtures:[],sessions:[{id:sid,source:'meeting',status:'completed',title:'Corrections',started_at:1}],
 active_id:null,replay_available:true,storage:'public-demo',segments:{[sid]:correctedRows},notes:{[sid]:'original notes'}});
assert.equal(demoGet('speaker-editor').hidden,false);
assert.match(demoGet('speaker-status').textContent,/manual corrections/);
assert.equal(demoGet('speaker-list').children.length,4,'Equal names must not collapse distinct identities');
assert.equal(demoGet('merge-target').children.length,2,'Only the placeholder and same-generation/track target are offered');
demoGet('speaker-name').value='<script>Minh</script>';demoGet('speaker-name').oninput();
demoGet('rename-speaker').onclick();
let correction=JSON.parse(ds.sent.at(-1));
assert.equal(correction.action,'rename_speaker');assert.equal(correction.revision,1);
assert.equal(correction.label,'<script>Minh</script>');
assert.equal(demoGet('speaker-editor').hidden,true,'Disable editing while waiting for persistence');
const renamed=correctedRows.map((r,i)=>({...r,speaker_label:i===0 ? correction.label : r.speaker_label}));
demoEvent({type:'speakers_corrected',session_id:sid,segments:renamed,notes:'updated notes'});
assert.equal(demoGet('speaker-editor').hidden,false);
assert.equal(demoGet('speaker-list').children[0].children[1].textContent,'<script>Minh</script>');
assert.equal(demoGet('meeting-notes').textContent,'updated notes');
demoGet('merge-target').value=correctedRows[1].display_speaker_id;demoGet('merge-target').onchange();demoGet('merge-speaker').onclick();
correction=JSON.parse(ds.sent.at(-1));assert.equal(correction.action,'merge_speaker');
assert.equal(correction.merge_target,correctedRows[1].display_speaker_id);
demoEvent({type:'error',context:'meeting',message:'stale request'});
assert.equal(demoGet('speaker-editor').hidden,false,'Rejected edits can be retried');
demoEvent({type:'session',session:{id:sid,source:'meeting',status:'processing',title:'Corrections',started_at:1}});
assert.equal(demoGet('speaker-editor').hidden,true,'Processing locks speaker corrections');
console.log('Revision-scoped speaker correction, merge boundaries and safe names passed');

const sumItem={text:'<script>Review plan</script>',segment_ids:[correctedRows[0].id]};
const summaryDraft={overview:sumItem,key_points:[sumItem],decisions:[],action_items:[],open_questions:[],uncertainties:[]};
const savedDraft={summary:summaryDraft,stale:false,provider:'Configured provider',model:'chosen-model'};
demoEvent({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v1'},fixtures:[],sessions:[{id:sid,source:'meeting',status:'completed',title:'Notes',started_at:1}],
 active_id:null,replay_available:true,storage:'public-demo',segments:{[sid]:correctedRows},
 grounded_summaries:{[sid]:savedDraft},summary_provider:{configured:true,cloud:true,provider:'Configured provider',model:'chosen-model',endpoint:'https://summary.example/v1'}});
assert.match(demoGet('summary-provider').textContent,/chosen-model.*https:\/\/summary.example/);
assert.equal(demoGet('summary-content').children[1].textContent,'<script>Review plan</script>','Model output stays inert text');
assert.match(demoGet('summary-content').children[2].textContent,/Source/);
assert.equal(demoGet('generate-summary').disabled,false);
demoGet('generate-summary').onclick();
let requested=JSON.parse(ds.sent.at(-1));assert.equal(requested.action,'summarize');assert.equal(requested.text_consent,true);
assert.equal(requested.model,'chosen-model');
demoEvent({type:'summary_started',session_id:sid});
assert.equal(demoGet('generate-summary').disabled,true);
demoEvent({type:'summary_error',session_id:sid,message:'Provider failed'});
assert.equal(demoGet('summary-content').children[1].textContent,'<script>Review plan</script>','Provider failure preserves saved notes');
demoGet('download-markdown').onclick();assert.equal(JSON.parse(ds.sent.at(-1)).action,'export_markdown');
demoEvent({type:'markdown_export',session_id:sid,content:'# Safe export',filename:'unique.md'});
assert.equal(demoDownloads.length,1);assert.equal(demoCreated.find(e=>e.download === 'unique.md').clicked,true);
demoEvent({type:'transcript_replaced',session_id:sid,segments:correctedRows});
assert.match(demoGet('summary-status').textContent,/outdated/);assert.equal(demoGet('summary-content').children.length,0);
console.log('Grounded summary authorization, source references, stale drafts and plaintext download passed');

const detected={id:'automatic-episode',app_id:'com.brave.Browser',name:'Brave',kind:'browser',selectable:true,evidence:['input_active','output_active'],confidence:'ambiguous'};
const automaticDiscovery={status:'available',permission:'authorized',devices_available:true,
 apps:[{id:'com.brave.Browser',name:'Brave',kind:'browser',running:true}],microphones:[{id:'default',name:'Default microphone'}],candidates:[detected]};
const asr={configured:true,cloud:true,provider:'deepgram',model:'nova-3',streaming:true};
demoEvent({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v1'},fixtures:[],sessions:[],active_id:null,replay_available:true,recording_available:true,storage:'encrypted',segments:{},
 transcription:asr,transcription_models:{deepgram:asr},discovery:automaticDiscovery,
 summary_provider:{configured:true,cloud:true,provider:'Text provider',model:'notes-model',endpoint:'https://summary.example/v1'}});
assert.equal(demoGet('call-permission').open,true,'Fresh call activity asks permission');
assert.match(demoGet('call-permission-copy').textContent,/all tabs in Brave/);
assert.match(demoGet('call-permission-provider').textContent,/notes-model.*https:\/\/summary.example/);
assert.equal(ds.sent.filter(x=>JSON.parse(x).action === 'accept_call').length,0,'Detection alone never starts capture');
demoGet('decline-call').onclick();
assert.equal(demoGet('call-permission').open,false);
demoEvent({type:'discovery',discovery:automaticDiscovery});
assert.equal(demoGet('call-permission').open,false,'Declined episode does not nag');
demoEvent({type:'discovery',discovery:{...automaticDiscovery,candidates:[{...detected,id:'second-episode'}]}});
assert.equal(demoGet('call-permission').open,true);
demoGet('accept-call').onclick();
const accepted=JSON.parse(ds.sent.at(-1));
assert.equal(accepted.action,'accept_call');assert.equal(accepted.automatic_summary,true);
assert.equal(accepted.consent_confirmed,true);assert.equal(accepted.summary_model,'notes-model');
assert.equal(demoGet('call-permission').open,false);
const autoSession={id:sid,title:'Brave · date/time',source:'meeting',status:'recording',started_at:Date.now(),capture:{auto_workflow:true,scope:'application',name:'Brave'}};
demoEvent({type:'session',session:autoSession});
demoEvent({type:'auto_call_status',session_id:sid,state:'ending',remaining_s:30});
assert.equal(demoGet('keep-recording').hidden,false);assert.match(demoGet('auto-end-status').textContent,/30s/);
demoGet('keep-recording').onclick();assert.equal(JSON.parse(ds.sent.at(-1)).action,'keep_recording');
demoEvent({type:'session',session:{...autoSession,status:'completed'}});
demoEvent({type:'summary_ready',session_id:sid,session:{...autoSession,status:'completed',title:'Garden project launch'},summary:savedDraft});
assert.equal(demoGet('session-heading').textContent,'Garden project launch');
console.log('Detected-call permission, decline suppression, end countdown and topic titles passed');
demoEvent({type:'segment',session_id:sid,segment:{id:'acceptance-segment',revision:3,stream:'loopback',start_ms:0,end_ms:1000,text:'finalization check'}});
assert.match(demoGet('revision-status').textContent,/revision 3/,'Revision remains visible without a generated summary');

// First-use agreement does not start recording; explicit call choice remains.
const beforeAgreement=ds.sent.length;
demoEvent({type:'agreement',agreement:{accepted:false,version:'2026-10-26-v1'}});
assert.equal(demoGet('app-agreement').open,true);
assert.equal(demoGet('record-meeting').disabled,true);
demoGet('agree-app').onclick();
assert.deepEqual(JSON.parse(ds.sent.at(-1)),{action:'accept_agreement',version:'2026-10-26-v1'});
assert.equal(ds.sent.length,beforeAgreement+1,'Agreement acceptance never sends Record');
demoEvent({type:'agreement',agreement:{accepted:true,version:'2026-10-26-v1'}});
assert.equal(demoGet('app-agreement').open,false);
demoGet('revoke-agreement').onclick();
assert.equal(JSON.parse(ds.sent.at(-1)).action,'revoke_agreement');
console.log('First-use agreement, explicit recording choice and revocation passed');

// Available, archived and expired audio preserve the saved transcript workflow.
demoEvent({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v2'},fixtures:[],
 sessions:[{id:sid,source:'meeting',status:'completed',title:'Retained',started_at:1}],active_id:null,
 recording_available:true,segments:{[sid]:correctedRows},transcription:{cloud:true,configured:true,provider:'deepgram'},
 audio_states:{[sid]:{available:true,tracks:['mic'],archived:false,expires_at:Date.now()+86400000}}});
assert.equal(demoGet('load-audio').disabled,false);
demoGet('archive-audio').onclick();
assert.deepEqual(JSON.parse(ds.sent.at(-1)),{action:'archive_audio',session_id:sid,archived:true});
demoEvent({type:'audio_state',session_id:sid,audio:{available:true,tracks:['mic'],archived:true}});
assert.match(demoGet('audio-retention-status').textContent,/Archived/);
demoGet('load-audio').onclick();
assert.equal(JSON.parse(ds.sent.at(-1)).action,'play_audio');
demoEvent({type:'audio_playback',session_id:sid,stream:'mic',wav_base64:btoa('RIFF-test')});
assert.equal(demoGet('audio-player').hidden,false);
demoEvent({type:'audio_state',session_id:sid,audio:{available:false,tracks:[],expired_at:Date.now()}});
assert.equal(demoGet('audio-player').hidden,true);
assert.equal(demoGet('reprocess-meeting').disabled,true);
assert.equal(demoGet('load-audio').disabled,true);
assert.match(demoGet('audio-retention-status').textContent,/transcript and notes are preserved/);
console.log('Saved playback, archive and audio expiry controls passed');

// Long-session bounds, page navigation, retained rolling notes and explicit people.
const personId='e'.repeat(32);
const longRows=Array.from({length:1200},(_,i)=>({...correctedRows[0],id:`long-${i}`,start_ms:i*1000,end_ms:i*1000+900,text:`Entry ${i}`}));
demoEvent({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v3'},fixtures:[],
 sessions:[{id:sid,source:'meeting',status:'completed',title:'Long notes',started_at:1}],active_id:null,
 recording_available:true,segments:{[sid]:longRows},transcription:{provider:'deepgram',configured:true,cloud:true},
 people:[{id:personId,canonical_name:'Alex',learn_enabled:0}],rolling_notes:{[sid]:{summary:summaryDraft,provider:'OpenAI',model:'gpt-5.4-mini'}},
 summary_budgets:{[sid]:{requests:12,max_requests:128,input_bytes:100000,max_input_bytes:8000000}}});
assert.equal(demoGet('transcript').children.length,500);
assert.match(demoGet('transcript-window').textContent,/701–1200 of 1200/);
demoGet('transcript-previous').onclick();
assert.match(demoGet('transcript-window').textContent,/201–700/);
assert.equal(demoGet('rolling-section').hidden,false);
assert.match(demoGet('rolling-provider').textContent,/OpenAI.*gpt-5.4-mini/);
demoGet('person-select').value=personId;demoGet('person-select').onchange();
assert.equal(demoGet('person-learning').checked,false);
demoGet('link-person').onclick();
assert.equal(JSON.parse(ds.sent.at(-1)).action,'link_person');
assert.equal(JSON.parse(ds.sent.at(-1)).person_id,personId);
demoEvent({type:'people_changed',people:[{id:personId,canonical_name:'Alex',learn_enabled:0}],sessions:[],segments:{},notes:{},grounded_summaries:{}});
demoGet('forget-person-confirm').checked=true;demoGet('forget-person-confirm').onchange();
demoGet('forget-person').onclick();
assert.equal(JSON.parse(ds.sent.at(-1)).action,'forget_person');
assert.equal(JSON.parse(ds.sent.at(-1)).confirmed,true);
console.log('Long transcript pagination, rolling-note restore and explicit people controls passed');

// Replacing a long transcript cannot leave a short result on a hidden last page.
demoEvent({type:'snapshot',agreement:{accepted:true,version:'2026-10-26-v3'},fixtures:[],
 sessions:[{id:sid,source:'meeting',status:'completed',title:'Replace',started_at:1}],active_id:null,
 segments:{[sid]:longRows},rolling_notes:{[sid]:{summary:summaryDraft,provider:'OpenAI',model:'gpt-5.4-mini'}}});
demoGet('transcript-previous').onclick();
const shortRows=longRows.slice(0,4).map(r=>({...r,revision:2}));
demoEvent({type:'transcript_replaced',session_id:sid,segments:shortRows});
assert.equal(demoGet('transcript').children.length,4);
assert.equal(demoGet('transcript-pagination').hidden,true);
assert.equal(demoGet('rolling-section').hidden,true,'Old source revision cannot retain live notes');
demoEvent({type:'rolling_notes',session_id:sid,summary:summaryDraft,provider:'OpenAI',model:'gpt-5.4-mini',budget:{}});
assert.equal(demoGet('rolling-section').hidden,false);
demoEvent({type:'speakers_corrected',session_id:sid,segments:shortRows,notes:'Updated',summary:null});
assert.equal(demoGet('rolling-section').hidden,true,'Speaker corrections invalidate old named drafts');
demoEvent({type:'session_deleted',session_id:sid});
assert.equal(demoGet('saved-audio').hidden,true);
assert.equal(demoGet('rolling-section').hidden,true);
console.log('Revision pagination and stale/deleted rolling-note cleanup passed');
