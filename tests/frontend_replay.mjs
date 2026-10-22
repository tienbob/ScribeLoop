// Run the actual UI script against a minimal DOM/socket harness. No browser or
// dependencies required; these regressions cover observable client state only.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor() {
    this.children=[]; this.value=''; this.disabled=false; this.hidden=false;
    this.textContent=''; this.open=false; this.classList={toggle() {}};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=[...nodes]; }
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
  history:{replaceState(){fragmentRemoved=true;}}, setTimeout, URLSearchParams,
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
const snapshot=(discovery)=>deliver({type:'snapshot',fixtures:[{id:'public',duration_s:20}],
  sessions:[session('active','replaying'),session('past','completed')],
  active_id:'active',replay_available:true,storage:'public-demo',segments:{active:[],past:[]},discovery});
snapshot();
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
snapshot(discovery);
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
socket.readyState=3; socket.onclose({code:1006});
assert.equal(get('meeting-source').disabled,true);
assert.equal(get('select-source').disabled,true);
assert.equal(get('record-meeting').disabled,true);
assert.ok(socket.sent.slice(1).every(text=>!['record','start_recording'].includes(JSON.parse(text).action)));
console.log('Frontend replay and meeting discovery regressions passed');
