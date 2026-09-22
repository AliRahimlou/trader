// Look-left charts: pure functions from /api/chart data to draw calls on a 2D context.
// Video rule: levels are hand-drawn lines at repeated four-hour swing points, kept on the
// hourly chart; VIX is read on fifteen-minute candles. App interpretation: the bands are the
// app's repeated-interaction areas with touch counts; stop/target lines are app execution
// choices the recordings never show. No DOM, no fetch, no timers live here.
export const MIN_CHART_WIDTH=560;
export const TIMEFRAMES={'60':'1h','240':'4h'};
export const NY='America/New_York';
const PAD={left:8,right:62,top:12,bottom:22};
const finite=n=>typeof n==='number'&&Number.isFinite(n);
export function chartWidth(containerWidth,minimum=MIN_CHART_WIDTH){const width=Math.floor(Number(containerWidth));return Math.max(Number.isFinite(width)?width:0,minimum);}
export function defaultTheme(){return {bg:'#ffffff',grid:'#edf0e8',text:'#7a8478',up:'#517b46',down:'#b0563f',band:'#dbe4d466',bandEdge:'#a7bc9f',event:'#f0d98a55',eventEdge:'#b8952f',stop:'#b0563f',target:'#3f7a5a',prev:'#7c8779',vixZone:'#c9d4e666',vixEdge:'#5b6e8c',reaction:'#e0b84e88',labelBg:'#ffffffd9',font:'10px Inter, -apple-system, sans-serif'};}
export function validBars(rows){
  return Array.isArray(rows)?rows.filter(b=>b&&['o','h','l','c'].every(k=>finite(b[k])&&b[k]>0)&&b.l<=Math.min(b.o,b.c)&&Math.max(b.o,b.c)<=b.h&&Number.isFinite(Date.parse(b.t))):[];
}
export function validLevels(rows){
  return Array.isArray(rows)?rows.filter(z=>z&&finite(z.low)&&finite(z.high)&&z.low>0&&z.low<=z.high):[];
}
export function priceScale(low,high,top,bottom,pad=0.05){
  if(!(finite(low)&&finite(high)))return null;
  if(high<low)[low,high]=[high,low];
  let span=high-low;if(!(span>0)){span=Math.max(Math.abs(low)*0.002,0.01);}
  const min=low-span*pad,max=high+span*pad,range=max-min,pixels=bottom-top;
  return {min,max,top,bottom,y:price=>bottom-(price-min)/range*pixels,price:y=>min+(bottom-y)/pixels*range};
}
export function ticks(min,max,count=5){
  if(!(finite(min)&&finite(max)&&max>min&&count>0))return [];
  const raw=(max-min)/count,magnitude=10**Math.floor(Math.log10(raw));
  const step=[1,2,2.5,5,10].map(m=>m*magnitude).find(s=>s>=raw)||10*magnitude;
  // A 2.5×magnitude step needs one more decimal than the magnitude itself (0.25, 2.5).
  const decimals=Math.max(0,-Math.floor(Math.log10(magnitude)))+(Math.abs(step/magnitude-2.5)<1e-9?1:0);
  const values=[];for(let v=Math.ceil(min/step)*step;v<=max+step*1e-9;v+=step)values.push(Number(v.toFixed(decimals)));
  return values;
}
export function candleLayout(count,left,right,gap=0.32){
  const slot=count>0?(right-left)/count:0,width=Math.max(1,Math.floor(slot*(1-gap)));
  return {slot,width,x:i=>left+slot*(i+0.5)};
}
const dayFormat=new Map();
export function dayLabel(iso,timeZone=NY){
  if(!dayFormat.has(timeZone))dayFormat.set(timeZone,new Intl.DateTimeFormat('en-US',{timeZone,month:'short',day:'numeric'}));
  const ms=Date.parse(iso);return Number.isFinite(ms)?dayFormat.get(timeZone).format(new Date(ms-1000)):'';
}
export function sessionMarks(bars,timeZone=NY){
  const marks=[];let previous=null;
  bars.forEach((bar,index)=>{const label=dayLabel(bar.t,timeZone);if(label&&label!==previous){marks.push({index,label});previous=label;}});
  return marks;
}
export function levelLabel(level){
  const touches=Number.isInteger(level?.touches)&&level.touches>=0?level.touches:null;
  return `${level?.source||'area'}${touches===null?'':` · ${touches} touch${touches===1?'':'es'}`}`;
}
export function bandsInRange(levels,scale){return validLevels(levels).filter(z=>z.low<=scale.max&&z.high>=scale.min).sort((a,b)=>a.low-b.low||a.high-b.high);}
function bounds(bars,extra){
  let low=Infinity,high=-Infinity;
  for(const b of bars){low=Math.min(low,b.l);high=Math.max(high,b.h);}
  for(const v of extra)if(finite(v)&&v>0){low=Math.min(low,v);high=Math.max(high,v);}
  return finite(low)&&finite(high)&&low!==Infinity?{low,high}:null;
}
export function fmt(price){return finite(price)?price.toFixed(2):'—';}
// One model shared by the QQQ and VIX charts: everything the draw step needs, nothing DOM-bound.
export function chartModel({bars,levels=[],lines=[],event=null,width,height,timeZone=NY}){
  const rows=validBars(bars),left=PAD.left,right=Math.max(left+10,width-PAD.right),top=PAD.top,bottom=Math.max(top+10,height-PAD.bottom);
  const extra=[];
  if(event){extra.push(event.stop,event.target);if(event.zone){extra.push(event.zone.low,event.zone.high);}}
  for(const line of lines)extra.push(line.price);
  const range=bounds(rows,extra);
  if(!range)return {bars:[],scale:null,layout:null,bands:[],lines:[],event:null,marks:[],ticks:[],left,right,top,bottom,width,height,message:'Waiting for candles'};
  const scale=priceScale(range.low,range.high,top,bottom),layout=candleLayout(rows.length,left,right);
  // Session labels need room to read; keep a mark only when 56px past the last kept one.
  const marks=[];for(const mark of sessionMarks(rows,timeZone)){if(!marks.length||layout.x(mark.index)-layout.x(marks[marks.length-1].index)>=56)marks.push(mark);}
  const sameBar=(bar,iso)=>Number.isFinite(Date.parse(iso))&&Date.parse(bar.t)===Date.parse(iso);
  const eventModel=event&&event.zone?{...event,originIndex:rows.findIndex(b=>sameBar(b,event.origin_at)),retestIndex:rows.findIndex(b=>sameBar(b,event.retest_at))}:null;
  return {bars:rows,scale,layout,bands:bandsInRange(levels,scale),lines:lines.filter(l=>finite(l.price)&&l.price>=scale.min&&l.price<=scale.max),
    event:eventModel,marks,ticks:ticks(scale.min,scale.max),left,right,top,bottom,width,height,message:''};
}
function line(ctx,x1,y1,x2,y2){ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();}
// A label that would sit on another moves one line below (then above) its line; if both
// are taken it is skipped (the levels table lists every area). Kept labels get a soft
// backing so candles and grid lines do not cross the text. Returns a y or false per label.
export const LABEL_GAP=13;
export function labelSlots(positions,gap=LABEL_GAP){
  const kept=[];
  return positions.map(y=>{
    if(!Number.isFinite(y))return false;
    const slot=[y,y+gap,y-gap].find(candidate=>kept.every(k=>Math.abs(k-candidate)>=gap));
    if(slot===undefined)return false;kept.push(slot);return slot;
  });
}
// Labels are drawn after the candles with a halo in the chart background colour, so a
// candle never covers the words and the words stay readable over bands and wicks.
function label(ctx,theme,text,x,y,align,color){
  ctx.textAlign=align;ctx.textBaseline='bottom';ctx.setLineDash([]);
  ctx.strokeStyle=theme.labelBg;ctx.lineWidth=3;ctx.lineJoin='round';ctx.strokeText(text,x,y-1);ctx.lineWidth=1;
  ctx.fillStyle=color;ctx.fillText(text,x,y-1);ctx.textBaseline='middle';
}
export function drawModel(ctx,model,theme=defaultTheme()){
  const {width,height,left,right,top,bottom}=model;
  ctx.save();ctx.font=theme.font;ctx.fillStyle=theme.bg;ctx.clearRect(0,0,width,height);ctx.fillRect(0,0,width,height);
  if(!model.scale){ctx.fillStyle=theme.text;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(model.message,width/2,height/2);ctx.restore();return model;}
  const {scale,layout}=model;
  ctx.lineWidth=1;ctx.textBaseline='middle';
  for(const value of model.ticks){const y=Math.round(scale.y(value))+0.5;ctx.strokeStyle=theme.grid;ctx.setLineDash([]);line(ctx,left,y,right,y);ctx.fillStyle=theme.text;ctx.textAlign='left';ctx.fillText(fmt(value),right+6,y);}
  for(const band of model.bands){
    const yTop=scale.y(band.high),yBottom=scale.y(band.low),h=Math.max(2,yBottom-yTop);
    ctx.fillStyle=band.fill||theme.band;ctx.fillRect(left,yTop,right-left,h);
    ctx.strokeStyle=band.edge||theme.bandEdge;ctx.setLineDash([]);line(ctx,left,Math.round(yTop)+0.5,right,Math.round(yTop)+0.5);line(ctx,left,Math.round(yBottom)+0.5,right,Math.round(yBottom)+0.5);
  }
  // Every label sits at the right, next to the price axis (the part a phone shows first); stop and target, then lines, then bands claim space in that order.
  const rightLabels=[];
  for(const row of model.lines){const y=Math.round(scale.y(row.price))+0.5;ctx.strokeStyle=row.color||theme.prev;ctx.setLineDash(row.dash||[5,4]);line(ctx,left,y,right,y);ctx.setLineDash([]);rightLabels.push({y,text:`${row.label} ${fmt(row.price)}`,color:row.color||theme.prev});}
  const event=model.event;
  if(event){
    const yTop=scale.y(event.zone.high),yBottom=scale.y(event.zone.low),h=Math.max(3,yBottom-yTop);
    ctx.fillStyle=theme.event;ctx.fillRect(left,yTop,right-left,h);
    ctx.strokeStyle=theme.eventEdge;ctx.setLineDash([]);ctx.lineWidth=1.5;ctx.strokeRect(left+0.5,yTop+0.5,right-left-1,h-1);ctx.lineWidth=1;
    rightLabels.push({y:yBottom+13,text:`${event.label||'event'} · ${String(event.state||'').replaceAll('_',' ').toLowerCase()}${event.direction?` · ${event.direction}`:''}`,color:theme.eventEdge});
    for(const [key,name,color] of [['stop','Stop',theme.stop],['target','Target',theme.target]]){
      const price=event[key];if(!finite(price)||price<scale.min||price>scale.max)continue;
      const y=Math.round(scale.y(price))+0.5;ctx.strokeStyle=color;ctx.setLineDash([2,3]);line(ctx,left,y,right,y);ctx.setLineDash([]);
      // Stop and target come first so a nearby previous-day label yields to them.
      rightLabels.unshift({y,text:`${name} ${fmt(price)} · app choice`,color});
    }
  }
  for(const mark of model.marks){const x=Math.round(layout.x(mark.index)-layout.slot/2)+0.5;ctx.strokeStyle=theme.grid;ctx.setLineDash([2,4]);line(ctx,x,top,x,bottom);ctx.setLineDash([]);ctx.fillStyle=theme.text;ctx.textAlign='left';ctx.fillText(mark.label,x+3,bottom+10);}
  model.bars.forEach((bar,index)=>{
    const x=Math.round(layout.x(index))+0.5,up=bar.c>=bar.o,color=up?theme.up:theme.down;
    ctx.strokeStyle=color;ctx.fillStyle=color;ctx.setLineDash([]);line(ctx,x,scale.y(bar.h),x,scale.y(bar.l));
    const bodyTop=scale.y(Math.max(bar.o,bar.c)),bodyHeight=Math.max(1,scale.y(Math.min(bar.o,bar.c))-bodyTop);
    ctx.fillRect(x-layout.width/2,bodyTop,layout.width,bodyHeight);
  });
  // A label never starts above the plot: the first text line sits inside the top edge.
  const inside=y=>Math.max(top+11,Math.min(bottom,y));
  for(const band of model.bands)rightLabels.push({y:scale.y(band.high),text:levelLabel(band),color:theme.text});
  const rightKeep=labelSlots(rightLabels.map(row=>inside(row.y)));
  rightLabels.forEach((row,index)=>{if(rightKeep[index]!==false)label(ctx,theme,row.text,right-4,rightKeep[index],'right',row.color);});
  if(event){
    for(const [index,label] of [[event.originIndex,'break'],[event.retestIndex,'retest']]){
      if(!(index>=0))continue;const bar=model.bars[index];ctx.fillStyle=theme.eventEdge;ctx.textAlign='center';ctx.textBaseline='bottom';ctx.fillText(label,layout.x(index),scale.y(bar.h)-3);ctx.textBaseline='middle';
    }
  }
  ctx.restore();return model;
}
// QQQ: hourly or four-hour candles, the app's established areas, previous-day lines and the
// leading event with its stop/target (both app choices).
export function priceChartModel(data,timeframe,width,height,timeZone=NY){
  const key=timeframe in TIMEFRAMES?timeframe:'60';
  const previous=data?.previous_day||{};
  const lines=[['high','Prev-day high'],['low','Prev-day low']].filter(([k])=>finite(previous[k])&&previous[k]>0).map(([k,label])=>({price:previous[k],label}));
  const events=Array.isArray(data?.events)?data.events.filter(e=>e&&e.zone&&finite(e.zone.low)&&finite(e.zone.high)):[];
  const event=events.find(e=>e.selected)||events[0]||null;
  return chartModel({bars:data?.bars?.[key],levels:data?.levels,lines,event,width,height,timeZone});
}
export function drawPriceChart(ctx,data,{timeframe='60',width,height,theme=defaultTheme(),timeZone=NY}={}){
  return drawModel(ctx,priceChartModel(data,timeframe,width,height,timeZone),theme);
}
// VIX: fifteen-minute candles with the app's swing clusters; the reaction area is highlighted.
export function vixChartModel(data,width,height,timeZone=NY,theme=defaultTheme()){
  const vix=data?.vix||{};
  const reaction=vix.reaction?.zone;
  const zones=validLevels(vix.zones).map(z=>({...z,fill:theme.vixZone,edge:theme.vixEdge}));
  if(reaction&&finite(reaction.low)&&finite(reaction.high)&&!zones.some(z=>z.low===reaction.low&&z.high===reaction.high))zones.push({...reaction,fill:theme.reaction,edge:theme.vixEdge});
  else for(const z of zones)if(reaction&&z.low===reaction.low&&z.high===reaction.high)z.fill=theme.reaction;
  const model=chartModel({bars:vix.bars15,levels:zones,width,height,timeZone});
  model.message=model.scale?'':'Waiting for actual VIX candles';
  model.reaction=vix.reaction||null;
  return model;
}
export function drawVixChart(ctx,data,{width,height,theme=defaultTheme(),timeZone=NY}={}){
  return drawModel(ctx,vixChartModel(data,width,height,timeZone,theme),theme);
}
// Levels table: nearest first, distances from the latest hourly close.
export function levelsTableRows(levels,reference,timeZone=NY){
  const rows=validLevels(levels).map(z=>{
    const distance=finite(reference)&&reference>0?(z.low>reference?z.low-reference:z.high<reference?z.high-reference:0):null;
    const established=Number.isFinite(Date.parse(z.established_at))?dayLabel(z.established_at,timeZone):'—';
    return {band:z.low===z.high?`$${fmt(z.low)}`:`$${fmt(z.low)} – $${fmt(z.high)}`,source:z.source||'area',established,
      touches:Number.isInteger(z.touches)&&z.touches>=0?String(z.touches):'—',
      distance:distance===null?'—':distance===0?'at level':`${distance>0?'+':'−'}${(Math.abs(distance)/reference*100).toFixed(2)}%`,
      sort:distance===null?z.low:Math.abs(distance)};
  });
  return rows.sort((a,b)=>a.sort-b.sort).map(({sort,...row})=>row);
}
export function chartStatus(data,now=Date.now()){
  if(!data||data.available===false)return {tone:'wait',text:data?.detail||'Chart data is not available yet. Waiting for the first completed analysis.'};
  const at=Date.parse(data.at),ageMinutes=Number.isFinite(at)?Math.max(0,now-at)/60000:Infinity;
  const hourly=validBars(data.bars?.['60']).length,levels=validLevels(data.levels).length;
  if(!hourly)return {tone:'wait',text:'Waiting for completed QQQ candles.'};
  const stale=ageMinutes>5;
  return {tone:stale?'wait':'pass',text:`${hourly} hourly candles · ${levels} established area${levels===1?'':'s'} · analysis ${ageMinutes<1?'under a minute':`${Math.floor(ageMinutes)} min`} old${stale?' · waiting for a fresh analysis':''}.`};
}
