import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {bindStrategyView} from './strategy-families.mjs';

// Run the real asynchronous handlers against deferred fetches. The DOM and
// render function are isolated; no provider or broker connection is available.
const source=(await readFile(new URL('./app.js',import.meta.url),'utf8'))
  .replace(/^import .*?;\n/gm,'')
  .replace('refresh();setInterval(refresh,10000);','');
function harness(enabled=false) {
  let now=1000;
  const elements=new Map(), calls=[], badge={dataset:{}};
  const element=id=>{
    if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',disabled:false,checked:true,
      open:id==='live-dialog',closeCount:0,addEventListener(){},close(){this.open=false;this.closeCount++;}});
    return elements.get(id);
  };
  const context=vm.createContext({URL,bindStrategyView:(document,options)=>bindStrategyView(document,{...options,storage:()=>null}),location:{port:'',hostname:'example.test'},
    document:{baseURI:'https://example.test/pivot/',getElementById:element,querySelector:()=>badge},
    Date:{now:()=>now},AbortSignal:{timeout:milliseconds=>({milliseconds})},
    fetch:(url,options)=>new Promise((resolve,reject)=>calls.push({url:String(url),options,resolve,reject}))});
  vm.runInContext(source+`\nrenderStrategyFamilies=()=>{};render=()=>{ $('status-text').textContent=liveError || 'Current app status'; };
    snapshot={live_enabled:${enabled},execution_policy:{version:'test-policy'},settings:{target_dollars:'5.00'}};
    globalThis.app={refresh,changeLive,state:()=>({snapshot,liveError,pendingLive,toggling,generation})};`,context);
  return {app:context.app,calls,element,advance:ms=>{now+=ms;}};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function answer(call,enabled,{ok=true,detail}={}) {
  call.resolve({ok,json:async()=>detail?{detail}:{live_enabled:enabled,execution_policy:{version:'test-policy'}}});
}
function timeout(call) {call.reject(Object.assign(new Error('Request timed out'),{name:'TimeoutError'}));}
const writes=h=>h.calls.filter(call=>call.options.method==='PUT');

for(const enabled of [true,false])test(`uncertain ${enabled?'On':'Off'} reconciles only after a new matching snapshot`,async()=>{
  const h=harness(!enabled),changing=h.app.changeLive(enabled);
  timeout(h.calls[0]);await changing;
  assert.match(h.app.state().liveError,/result is uncertain/);
  assert.equal(h.element('live-dialog').open,true);
  assert.equal(h.calls.length,2);
  answer(h.calls[1],enabled);await flush();
  assert.equal(h.app.state().snapshot.live_enabled,enabled);
  assert.equal(h.app.state().liveError,'');
  assert.equal(h.app.state().pendingLive,null);
  assert.equal(h.element('live-error').textContent,'');
  assert.equal(h.element('live-dialog').open,false);
  assert.equal(writes(h).length,1);
});

test('a nonmatching snapshot retains uncertainty and never repeats the control request',async()=>{
  const h=harness(),changing=h.app.changeLive(true);
  timeout(h.calls[0]);await changing;
  answer(h.calls[1],false);await flush();
  assert.match(h.app.state().liveError,/result is uncertain/);
  assert.equal(h.element('live-dialog').open,true);
  assert.equal(h.app.state().snapshot.live_enabled,false);
  assert.equal(writes(h).length,1);
});

test('server rejection remains visible while the requested setting is unconfirmed',async()=>{
  const h=harness(),changing=h.app.changeLive(true);
  answer(h.calls[0],undefined,{ok:false,detail:'Account review required'});await changing;
  answer(h.calls[1],false);await flush();
  assert.equal(h.app.state().liveError,'Account review required');
  assert.equal(h.element('live-error').textContent,'Account review required');
  assert.equal(h.element('live-dialog').open,true);
  assert.equal(writes(h).length,1);
});

test('an older in-flight poll cannot falsely confirm a timed-out toggle',async()=>{
  const h=harness(),oldPoll=h.app.refresh(),changing=h.app.changeLive(true);
  timeout(h.calls[1]);await changing;
  assert.equal(h.calls.length,2); // Existing poll is still in flight.
  answer(h.calls[0],true);await oldPoll;
  assert.equal(h.app.state().snapshot.live_enabled,false);
  assert.match(h.app.state().liveError,/result is uncertain/);
  assert.equal(h.element('live-dialog').open,true);
  assert.equal(h.calls.length,3); // Read-only catch-up after discarding the old poll.
  answer(h.calls[2],true);await flush();
  assert.equal(h.app.state().liveError,'');
  assert.equal(h.element('live-dialog').open,false);
  assert.equal(writes(h).length,1);
});

test('failure of a pre-toggle poll cannot overwrite a confirmed control response',async()=>{
  const h=harness(),oldPoll=h.app.refresh(),changing=h.app.changeLive(true);
  answer(h.calls[1],true);await changing;
  h.calls[0].reject(new Error('Old poll failed'));await oldPoll;
  assert.equal(h.app.state().snapshot.live_enabled,true);
  assert.equal(h.app.state().liveError,'');
  assert.notEqual(h.element('status-title').textContent,'App connection unavailable');
  assert.doesNotMatch(h.element('status-text').textContent,/outdated|Reconnecting/);
  assert.equal(h.element('live-dialog').open,false);
  answer(h.calls[2],true);await flush();
  assert.equal(writes(h).length,1);
});

test('expired pending intent cannot close a later dialog or attribute a later state change to the request',async()=>{
  const h=harness(),changing=h.app.changeLive(true);
  timeout(h.calls[0]);await changing;
  h.advance(60001);
  answer(h.calls[1],true);await flush();
  assert.equal(h.app.state().pendingLive,null);
  assert.equal(h.app.state().snapshot.live_enabled,true);
  assert.match(h.app.state().liveError,/result is uncertain/);
  assert.equal(h.element('live-dialog').open,true);
  assert.equal(writes(h).length,1);
});

test('a malformed truthy saved setting cannot confirm the request',async()=>{
  const h=harness(),changing=h.app.changeLive(true);
  timeout(h.calls[0]);await changing;
  answer(h.calls[1],'true');await flush();
  assert.match(h.app.state().liveError,/result is uncertain/);
  assert.equal(h.element('live-dialog').open,true);
  assert.equal(writes(h).length,1);
});

test('a successful HTTP response with the opposite saved setting does not claim success',async()=>{
  const h=harness(),changing=h.app.changeLive(true);
  answer(h.calls[0],false);await changing;
  assert.match(h.app.state().liveError,/not confirmed/);
  assert.equal(h.element('live-dialog').open,true);
  answer(h.calls[1],true);await flush();
  assert.equal(h.app.state().liveError,'');
  assert.equal(h.element('live-dialog').open,false);
  assert.equal(writes(h).length,1);
});
