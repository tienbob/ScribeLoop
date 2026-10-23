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
  set innerHTML(value) { throw new Error('Transcript must not use innerHTML'); }
}
const elements=new Map();
const get=id => { if (!elements.has(id)) elements.set(id,new Element()); return elements.get(id); };
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
const snapshot=(discovery,active=true)=>deliver({type:'snapshot',fixtures:[{id:'public',duration_s:20}],
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
assert.equal(get('transcript').children[0].children[1].textContent,'<script>literal text</script>');
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
deliver({type:'snapshot',fixtures:[],sessions:[],active_id:null,segments:{},notes:{},
  recording_available:true,storage:'encrypted',discovery:{...discovery,permission:'authorized'}});
get('meeting-source').value='system'; get('meeting-source').onchange();
get('meeting-microphone').value='default'; get('meeting-microphone').onchange();
get('source-form').onsubmit({preventDefault(){}});
const request=JSON.parse(socket.sent.at(-1));
deliver({type:'source_selected',request_id:request.request_id,source:{scope:'system',app_id:null,
  microphone_id:'default',name:'All system audio'}});
assert.equal(get('record-meeting').disabled,true);
get('record-consent').checked=true; get('record-consent').onchange();
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
deliver({type:'snapshot',fixtures:[],sessions:[{id:'meeting',source:'meeting',title:'Call',status:'recording',started_at:Date.now(),capture:{scope:'application',kind:'browser',name:'Brave',microphone_name:'USB mic'}}],
 active_id:'meeting',segments:{meeting:[]},notes:{},storage:'encrypted',recording_available:true,discovery:{...discovery,permission:'authorized'}});
assert.match(get('active-scope').textContent,/All tabs in Brave.*USB mic/);
assert.equal(get('record-consent').checked,false);
deliver({type:'session',session:{id:'meeting',source:'meeting',title:'Call',status:'processing',started_at:Date.now()}});
assert.equal(get('stop').hidden,true);
assert.equal(get('record-meeting').disabled,true);
deliver({type:'notes',session_id:'meeting',notes:'<script>literal notes</script>'});
assert.equal(get('meeting-notes').textContent,'<script>literal notes</script>');
deliver({type:'session',session:{id:'meeting',source:'meeting',title:'Call',status:'completed',started_at:Date.now()}});
assert.equal(get('reprocess-meeting').disabled,false);
get('reprocess-meeting').onclick();
assert.equal(JSON.parse(socket.sent.at(-1)).action,'reprocess');
assert.equal(JSON.parse(socket.sent.at(-1)).language,'auto');
deliver({type:'transcript_replaced',session_id:'meeting',segments:[{id:'revised',text:'Tiếng Việt',stream:'mic',start_ms:0}]});
assert.equal(get('transcript').children.at(-1).children[1].textContent,'Microphone · speaker unknown: Tiếng Việt');
deliver({type:'language_locked',session_id:'meeting',mode:'auto',language:'vi',confidence:.95});
assert.match(get('language-status').textContent,/Detected: Tiếng Việt.*fixed/);
assert.ok(socket.sent.length > sentBeforeRecording);
socket.readyState=3; socket.onclose({code:1006});
get('reconnect').onclick();
assert.equal(sockets.length,2);
const recovered=sockets[1]; recovered.readyState=Socket.OPEN; recovered.onopen();
assert.equal(recovered.sent[0],'test-launch-key');
recovered.onmessage({data:JSON.stringify({type:'snapshot',fixtures:[],sessions:[],active_id:null,
 segments:{},notes:{},recording_available:true,storage:'encrypted',discovery})});
assert.equal(get('reconnect').hidden,true);
socket.onclose({code:1006});
assert.equal(get('reconnect').hidden,true,'Ignore late events from the previous socket');
console.log('Frontend replay and meeting discovery regressions passed');
// Live streams retain each other's provisional text; labels preserve ambiguity.
recovered.onmessage({data:JSON.stringify({type:'snapshot',fixtures:[],sessions:[{id:'live',source:'meeting',title:'Live',status:'recording',started_at:Date.now(),capture:{scope:'system'}}],active_id:'live',
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
assert.match(get('transcript').children[1].children[1].textContent,/overlapping \/ uncertain speakers/);
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
// Default cloud UI requires separate new/saved upload consent; replay does not
// load a local model or masquerade as storage failure when disabled.
liveEvent({type:'snapshot',fixtures:[],sessions:[],active_id:null,segments:{},notes:{},
 recording_available:true,replay_available:true,replay_enabled:false,storage:'encrypted',
 transcription:{cloud:true,configured:true,provider:'groq',model:'whisper-large-v3-turbo',budget_usd:.05},
 discovery:{...discovery,permission:'authorized'}});
get('meeting-source').value='system';get('meeting-source').onchange();
get('source-form').onsubmit({preventDefault(){}});
const cloudSelection=JSON.parse(recovered.sent.at(-1));
liveEvent({type:'source_selected',request_id:cloudSelection.request_id,source:{scope:'system',name:'All system audio',microphone_id:'default'}});
get('record-consent').checked=true;get('record-consent').onchange();
assert.equal(get('record-meeting').disabled,true,'Recording consent does not authorize cloud upload');
assert.match(get('record-help').textContent,/Groq/);
get('cloud-consent').checked=true;get('cloud-consent').onchange();
assert.equal(get('record-meeting').disabled,false);
get('record-meeting').onclick();
assert.equal(JSON.parse(recovered.sent.at(-1)).cloud_consent,true);
liveEvent({type:'session',session:{id:'cloud',title:'Cloud',source:'meeting',status:'completed',started_at:Date.now(),has_checkpoints:true}});
get('history').children[0].onclick();
assert.equal(get('reprocess-meeting').disabled,true,'Saved audio needs its own explicit upload consent');
get('saved-cloud-consent').checked=true;get('saved-cloud-consent').onchange();
assert.equal(get('reprocess-meeting').disabled,false);
get('reprocess-meeting').onclick();
assert.equal(JSON.parse(recovered.sent.at(-1)).cloud_consent,true);
assert.equal(get('saved-cloud-consent').checked,false,'Do not retain consent after dispatch');
assert.notEqual(get('session-status').textContent,'STORAGE UNAVAILABLE');
console.log('Cloud upload consent and disabled-local-replay regressions passed');

// Speaker identities are persisted with words, independently of delayed turns.
liveEvent({type:'snapshot',fixtures:[],sessions:[{id:'people',source:'meeting',title:'People',status:'completed',started_at:Date.now()}],active_id:null,recording_available:true,replay_enabled:false,transcription:{provider:'deepgram',cloud:true,configured:true,speaker_labels:true,streaming:true,auto_language:false,default_language:'en',model:'nova-3',budget_usd:.15},segments:{people:[{id:'a',stream:'loopback',start_ms:0,end_ms:300,text:'Hello',speaker_label:'Speaker 1'},{id:'b',stream:'loopback',start_ms:300,end_ms:500,text:'Hi',speaker_label:'Speaker 2'}]}});
assert.equal(get('meeting-language').value,'en');
assert.match(get('cloud-provider').textContent,/Deepgram/);
assert.match(get('speaker-status').textContent,/Anonymous speaker labels/);
assert.match(get('transcript').children[0].children[1].textContent,/Speaker 1: Hello/);
assert.match(get('transcript').children[1].children[1].textContent,/Speaker 2: Hi/);
assert.match(get('cloud-consent-copy').textContent,/Deepgram/);
console.log('Deepgram streaming speaker labels and explicit language regressions passed');
