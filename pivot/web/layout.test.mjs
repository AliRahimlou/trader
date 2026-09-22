import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {MIN_CHART_WIDTH} from './chart.mjs';

// Static layout checks for a 390 px phone (16 px gutters leave 358 px of content). A browser
// is not available to the test suite, so these pin the rules that keep wide content inside
// its own scrolling box instead of widening the page.
const css=await readFile(new URL('./style.css',import.meta.url),'utf8');
const html=await readFile(new URL('./index.html',import.meta.url),'utf8');
const PHONE_CONTENT=358;
const rules=text=>[...text.replace(/\/\*[^]*?\*\//g,'').matchAll(/([^{}@]+)\{([^{}]*)\}/g)].map(([,selector,body])=>({selector:selector.trim(),body}));
const block=(source,query)=>{
  const start=source.indexOf(query);assert.ok(start>=0,`missing ${query}`);
  let depth=0,open=source.indexOf('{',start);
  for(let i=open;i<source.length;i++){if(source[i]==='{')depth++;else if(source[i]==='}'&&--depth===0)return source.slice(open+1,i);}
  throw Error('unbalanced');
};

test('no rule forces a width wider than a phone, except content that scrolls in its own box',()=>{
  const allowed=new Set(['.levels-table']);
  for(const {selector,body} of rules(css)){
    for(const [,property,value] of body.matchAll(/(?:^|;)\s*((?:min-)?width)\s*:\s*(\d+)px/g)){
      if(Number(value)<=PHONE_CONTENT)continue;
      assert.ok(allowed.has(selector),`${selector} sets ${property}:${value}px`);
    }
  }
  // The wide table and both canvases live inside a horizontally scrolling box.
  for(const id of ['levels-table','qqq-chart','vix-chart'])
    assert.match(html,new RegExp(`<div class="chart-scroll"[^>]*>\\s*<(?:table|canvas) id="${id}"`),`${id} sits in .chart-scroll`);
  const scroll=rules(css).find(rule=>rule.selector==='.chart-scroll');
  assert.match(scroll.body,/max-width:100%/);assert.match(scroll.body,/overflow-x:auto/);
  assert.ok(MIN_CHART_WIDTH>PHONE_CONTENT,'the chart minimum is why the scroll box exists');
});

test('grid columns can shrink to the phone width',()=>{
  // A bare 1fr column takes its content's minimum width (the 560 px canvas); minmax(0,…) does not.
  const phone=block(css,'@media (max-width:860px)');
  assert.match(phone,/\.grid\{grid-template-columns:minmax\(0,1fr\)/);
  for(const {selector,body} of rules(css)){
    const columns=body.match(/grid-template-columns:([^;]+)/)?.[1];
    if(!columns)continue;
    for(const [,px] of columns.matchAll(/minmax\((\d+)px/g))
      assert.ok(Number(px)<=PHONE_CONTENT||selector==='.grid',`${selector} has a ${px}px column minimum`);
  }
  assert.match(css,/main>\*,\.grid>\*,\.workspace-main>\*\{min-width:0\}/);
});

test('every light theme token has a dark value in both the system and explicit dark themes',()=>{
  const tokens=body=>new Set([...body.matchAll(/(--[a-z0-9-]+)\s*:/g)].map(match=>match[1]));
  const light=tokens(block(css,':root{'));
  const system=tokens(block(block(css,'@media (prefers-color-scheme:dark)'),':root:not([data-theme="light"])'));
  const explicit=tokens(block(css,':root[data-theme="dark"]'));
  assert.ok(light.size>30);
  for(const token of light){assert.ok(system.has(token),`system dark ${token}`);assert.ok(explicit.has(token),`explicit dark ${token}`);}
  assert.match(html,/<meta name="color-scheme" content="light dark">/);
});

test('crypto sections stay in the page but out of the Socrates view, grayed when paused',()=>{
  assert.match(css,/#strategy-workspace\[data-view="socrates"\]~\.crypto-area\{display:none\}/);
  assert.match(css,/\.crypto-area\.is-paused \.crypto-section:not\(\.has-exposure\)\{opacity:[^;]+;filter:grayscale/,'an open crypto position or incident is never grayed out');
  assert.match(css,/\.family-card\.is-paused\{opacity:/);
  for(const id of ['range-family','crypto-watchlist','crypto-review','crypto-management'])
    assert.ok(html.indexOf(`id="${id}"`)>html.indexOf('id="crypto-area"')&&html.indexOf('id="crypto-area"')>html.indexOf('id="strategy-workspace"'),`${id} sits below the Socrates workspace`);
  assert.match(html,/<option value="range_reversal">Crypto \(paused\)<\/option>/);
});

test('page copy follows the 4.5 rules and names buttons by what they do',()=>{
  const text=html.replace(/<[^>]+>/g,' ');
  for(const stale of [/15-minute reaction/i,/180 minutes/i,/180-minute/i,/five of seven/i,/5 of 7/,/last at most 15 minutes/i])assert.doesNotMatch(text,stale);
  for(const current of [/4 of the 7/,/60 minutes/,/end of the next session/,/within 0\.4%/,/1R minimum/,/PSQ/])assert.match(text,current);
  assert.match(html,/id="confirm-live" class="primary" type="submit" disabled>Turn Live money On</);
  assert.match(html,/id="save-size" type="submit" disabled>Save purchase size</);
});
