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
const snapshot=()=>deliver({type:'snapshot',fixtures:[{id:'public',duration_s:20}],
  sessions:[session('active','replaying'),session('past','completed')],
  active_id:'active',replay_available:true,storage:'public-demo',segments:{active:[],past:[]}});
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
console.log('Frontend replay regressions passed');
