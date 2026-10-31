
"""QUEEN'S EDGE | 254 — Clean live BTC chart.
Coinbase spot is indicative, not Kalshi settlement data.
Paper-only. No orders are placed.
"""
import json
import math
import streamlit.components.v1 as components


def render_live_chart(target=None, height=690):
    try:
        strike = float(target) if target is not None else None
        if strike is not None and not math.isfinite(strike):
            strike = None
    except (ValueError, TypeError):
        strike = None

    html = r'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0b091b;color:#eee5ff;font:12px system-ui,Segoe UI,Arial,sans-serif}
#summary{display:flex;gap:8px;flex-wrap:wrap;padding:8px;background:#160f2c;border:1px solid #654098;border-radius:12px}
.tile{min-width:135px;flex:1;background:#23163c;border-radius:8px;padding:8px}
.muted{color:#b9a5d4;font-size:11px}
.value{font-size:17px;font-weight:750;margin-top:4px}
#toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0}
button{cursor:pointer;background:#25183d;color:#eee5ff;border:1px solid #654098;border-radius:8px;padding:7px 12px;font-weight:650}
button.active{background:#55318c;border-color:#a77aff}
#extras{display:none;flex-wrap:wrap;gap:7px;margin-bottom:8px}
label{cursor:pointer;background:#24183d;border:1px solid #543779;border-radius:8px;padding:6px 8px;white-space:nowrap}
input{accent-color:#b77dff}
.panel{border:1px solid #513478;border-radius:10px;overflow:hidden;margin-bottom:7px;position:relative}
.panelTitle{position:absolute;top:7px;left:9px;z-index:2;background:#110d22d9;border-radius:4px;padding:3px 7px;color:#d9c5ff;pointer-events:none}
#main{height:355px}
#volume{height:78px}
#rsi{height:105px}
#macd{height:105px}
#legend{display:flex;flex-wrap:wrap;gap:12px;color:#cbb5e8;margin:8px 2px}
#notice{padding:9px 10px;border:1px solid #694b84;border-radius:8px;color:#ffcb83;background:#1d1430;line-height:1.4}
#help{color:#bda9d5;line-height:1.5;padding:7px 2px}
</style>
</head>
<body>
<div id="summary">
<div class="tile"><div class="muted">BTC/USD · Coinbase spot</div><div class="value" id="price">Connecting…</div></div>
<div class="tile"><div class="muted">Kalshi target · snapshot</div><div class="value" id="target">—</div></div>
<div class="tile"><div class="muted">Distance to target</div><div class="value" id="distance">—</div></div>
<div class="tile"><div class="muted">EMA 9 / EMA 21</div><div class="value" id="trend">Collecting…</div></div>
<div class="tile"><div class="muted">Feed</div><div class="value" id="status">Connecting…</div></div>
</div>

<div id="toolbar">
<button id="clean" class="active">✓ CLEAN MODE</button>
<button id="advanced">ADVANCED MODE</button>
<span class="muted">Clean: price + EMA 9/21 + target + volume + RSI</span>
</div>

<div id="extras">
<label><input type="checkbox" data-key="ema50"> EMA 50</label>
<label><input type="checkbox" data-key="vwap"> VWAP</label>
<label><input type="checkbox" data-key="bb"> Bollinger</label>
<label><input type="checkbox" data-key="levels"> Support / resistance</label>
<label><input type="checkbox" data-key="macd"> MACD</label>
<label><input type="checkbox" data-key="markers"> Reversal watch</label>
</div>

<div class="panel">
<div class="panelTitle">BTC · 1-minute candles / Kalshi target</div>
<div id="main"></div>
</div>
<div class="panel">
<div class="panelTitle">Coinbase BTC trading volume · 1 minute</div>
<div id="volume"></div>
</div>
<div class="panel">
<div class="panelTitle">RSI (14) · momentum · 70 / 30</div>
<div id="rsi"></div>
</div>
<div class="panel" id="macdPanel" style="display:none">
<div class="panelTitle">MACD (12, 26, 9)</div>
<div id="macd"></div>
</div>

<div id="legend">
<span>🟣 EMA 9 · fast trend</span>
<span>🔵 EMA 21 · slower trend</span>
<span>🟡 Kalshi target</span>
<span>🟢 Green candles · rising</span>
<span>🔴 Red candles · falling</span>
</div>
<div id="notice">WAIT · learning/observation only. No validated trade entry.</div>
<div id="help">Read the candles first, then compare EMA 9 with EMA 21. Volume shows participation; RSI shows momentum. Coinbase spot is not Kalshi's verified settlement reference.</div>

<script>
const TARGET=__TARGET__;
const $=id=>document.getElementById(id);
const fmt=x=>Number.isFinite(x)?x.toLocaleString('en-US',{
 style:'currency',currency:'USD',minimumFractionDigits:2
}):'—';
$('target').textContent=TARGET===null?'Unavailable':fmt(TARGET);

const flags={
 ema50:false,vwap:false,bb:false,
 levels:false,macd:false,markers:false
};
let bars=[],lastTick=0,socket=null,retry=1000;
let charts=[],series={},calculationTimer=null;
let lastTime=0;
const bg='#0b091b',fg='#cbb8e6',grid='#281e3c';

function chartFor(id){
 const e=$(id);
 const chart=LightweightCharts.createChart(e,{
  width:e.clientWidth,height:e.clientHeight,
  layout:{background:{color:bg},textColor:fg},
  grid:{vertLines:{color:grid},horzLines:{color:grid}},
  rightPriceScale:{borderColor:'#513478'},
  timeScale:{timeVisible:true,secondsVisible:false,borderColor:'#513478'},
  crosshair:{mode:LightweightCharts.CrosshairMode.Normal}
 });
 new ResizeObserver(()=>chart.applyOptions({
  width:e.clientWidth,height:e.clientHeight
 })).observe(e);
 charts.push(chart);
 return chart;
}
function line(c,color,width=1,style=0){
 return c.addLineSeries({
  color,lineWidth:width,lineStyle:style,
  priceLineVisible:false,lastValueVisible:false,
  crosshairMarkerVisible:false
 });
}
function points(v){
 return bars.map((b,i)=>({time:b.time,value:v[i]}))
  .filter(x=>Number.isFinite(x.value));
}
function ema(v,n){
 const out=Array(v.length).fill(null);
 if(v.length<n)return out;
 let seed=0;
 for(let i=0;i<n;i++)seed+=v[i];
 out[n-1]=seed/n;
 const k=2/(n+1);
 for(let i=n;i<v.length;i++)
  out[i]=out[i-1]+k*(v[i]-out[i-1]);
 return out;
}
function rsi(v,n=14){
 const out=Array(v.length).fill(null);
 if(v.length<=n)return out;
 let g=0,l=0;
 for(let i=1;i<=n;i++){
  const d=v[i]-v[i-1];
  g+=Math.max(0,d);l+=Math.max(0,-d);
 }
 g/=n;l/=n;
 out[n]=l===0?(g===0?50:100):100-100/(1+g/l);
 for(let i=n+1;i<v.length;i++){
  const d=v[i]-v[i-1];
  g=(g*(n-1)+Math.max(0,d))/n;
  l=(l*(n-1)+Math.max(0,-d))/n;
  out[i]=l===0?(g===0?50:100):100-100/(1+g/l);
 }
 return out;
}
function macd(v){
 const a=ema(v,12),b=ema(v,26);
 const m=v.map((_,i)=>
  a[i]===null||b[i]===null?null:a[i]-b[i]);
 const s=ema(m.filter(x=>x!==null),9);
 const signal=Array(v.length).fill(null);
 const hist=Array(v.length).fill(null);
 for(let i=0;i<s.length;i++){
  if(s[i]!==null){
   signal[i+25]=s[i];
   hist[i+25]=m[i+25]-s[i];
  }
 }
 return {m,signal,hist};
}
function bollinger(v,n=20){
 const high=Array(v.length).fill(null);
 const low=high.slice();
 for(let i=n-1;i<v.length;i++){
  const a=v.slice(i-n+1,i+1);
  const mean=a.reduce((x,y)=>x+y,0)/n;
  const sd=Math.sqrt(a.reduce((x,y)=>x+(y-mean)**2,0)/n);
  high[i]=mean+2*sd;low[i]=mean-2*sd;
 }
 return {high,low};
}
function vwap(){
 let out=[],day='',pv=0,vol=0;
 for(const b of bars){
  const d=new Date(b.time*1000).toISOString().slice(0,10);
  if(d!==day){day=d;pv=0;vol=0;}
  const q=Math.max(0,b.volume||0);
  pv+=(b.high+b.low+b.close)/3*q;
  vol+=q;
  out.push(vol?pv/vol:null);
 }
 return out;
}
function levels(n=35){
 let low=[],high=[];
 for(let i=0;i<bars.length;i++){
  if(i<n){low.push(null);high.push(null);continue;}
  const a=bars.slice(i-n,i);
  low.push(Math.min(...a.map(x=>x.low)));
  high.push(Math.max(...a.map(x=>x.high)));
 }
 return {low,high};
}
function setStatus(t){
 $('status').textContent=t;
 $('status').style.color=t==='LIVE'?'#75f2be':'#ffbc88';
}
function updatePrice(p){
 $('price').textContent=fmt(p);
 if(TARGET!==null){
  const d=p-TARGET;
  $('distance').textContent=(d>=0?'+':'')+fmt(d);
  $('distance').style.color=d>=0?'#75f2be':'#ff91a9';
 }
}
function applyFlags(){
 const groups={
  ema50:['ema50'],vwap:['vwap'],
  bb:['bbHigh','bbLow'],
  levels:['support','resistance']
 };
 for(const [k,names] of Object.entries(groups))
  for(const name of names)
   if(series[name])series[name].applyOptions({visible:flags[k]});
 $('macdPanel').style.display=flags.macd?'block':'none';
 if(series.candles)
  series.candles.setMarkers(flags.markers?(series.markers||[]):[]);
}
function calculate(){
 if(!bars.length)return;
 const close=bars.map(b=>b.close);
 const e9=ema(close,9),e21=ema(close,21),e50=ema(close,50);
 const rv=rsi(close),mc=macd(close),bb=bollinger(close);
 const vw=vwap(),lev=levels();
 const values={
  ema9:e9,ema21:e21,ema50:e50,vwap:vw,
  bbHigh:bb.high,bbLow:bb.low,
  support:lev.low,resistance:lev.high,
  rsi:rv,macd:mc.m,signal:mc.signal
 };
 for(const [name,v] of Object.entries(values))
  series[name].setData(points(v));

 series.candles.setData(bars.map(b=>({
  time:b.time,open:b.open,high:b.high,low:b.low,close:b.close
 })));
 series.volume.setData(bars.map(b=>({
  time:b.time,value:b.volume||0,
  color:b.close>=b.open?'#2bb88d':'#cc5079'
 })));
 series.hist.setData(bars.map((b,i)=>({
  time:b.time,value:mc.hist[i]||0,
  color:(mc.hist[i]||0)>=0?'#2bb88d':'#cc5079'
 })));

 const last=close.length-1;
 if(e9[last]!==null&&e21[last]!==null){
  const direction=e9[last]>e21[last]?'UPWARD':'DOWNWARD';
  $('trend').textContent=direction;
  $('trend').style.color=e9[last]>e21[last]?'#75f2be':'#ff91a9';
 }
 const markers=[];
 for(let i=52;i<bars.length;i++){
  if(TARGET===null||rv[i]===null||mc.hist[i]===null||
     mc.hist[i-1]===null)continue;
  const near=Math.abs(close[i]-TARGET)<Math.max(40,close[i]*.001);
  const toward=(close[i]-TARGET)*(close[i]-close[i-3])<0;
  const bearish=close[i]>TARGET&&e9[i]<e21[i]&&
   mc.hist[i]<mc.hist[i-1];
  const bullish=close[i]<TARGET&&e9[i]>e21[i]&&
   mc.hist[i]>mc.hist[i-1];
  if(near&&toward&&(bearish||bullish)&&
    (!markers.length||bars[i].time-markers[markers.length-1].time>=480)){
   markers.push({
    time:bars[i].time,
    position:bearish?'aboveBar':'belowBar',
    color:'#ffbd65',
    shape:bearish?'arrowDown':'arrowUp',
    text:'WATCH'
   });
  }
 }
 series.markers=markers;
 applyFlags();
 const recent=markers.length&&
  markers[markers.length-1].time>=bars[last].time-180;
 $('notice').textContent=recent
  ?'⚠ REVERSAL WATCH · target proximity and opposing momentum. Not a trade signal.'
  :'WAIT · observation only. No validated trade entry.';
}
function schedule(){
 if(calculationTimer)return;
 calculationTimer=setTimeout(()=>{
  calculationTimer=null;
  calculate();
 },750);
}
function onTrade(p,t,q){
 if(!Number.isFinite(p)||!Number.isFinite(t)||!Number.isFinite(q)||q<0)return;
 const minute=Math.floor(t/60)*60;
 let old=bars[bars.length-1];
 if(old&&minute<old.time)return;
 if(old&&minute===old.time){
  old.high=Math.max(old.high,p);
  old.low=Math.min(old.low,p);
  old.close=p;
  old.volume+=q;
 }else{
  if(old){
   for(let x=old.time+60;x<minute;x+=60){
    bars.push({
     time:x,open:old.close,high:old.close,
     low:old.close,close:old.close,volume:0
    });
    old=bars[bars.length-1];
   }
  }
  bars.push({
   time:minute,open:old?old.close:p,
   high:p,low:p,close:p,volume:q
  });
  if(bars.length>360)bars=bars.slice(-360);
 }
 updatePrice(p);
 lastTick=Date.now();
 setStatus('LIVE');
 schedule();
}
async function load(){
 try{
  const r=await fetch(
   'https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60',
   {cache:'no-store'}
  );
  if(!r.ok)throw Error('HTTP '+r.status);
  const data=await r.json();
  bars=data.filter(x=>Array.isArray(x)&&x.length>=6)
   .map(x=>({
    time:Number(x[0]),low:Number(x[1]),
    high:Number(x[2]),open:Number(x[3]),
    close:Number(x[4]),volume:Number(x[5])
   }))
   .filter(b=>Object.values(b).every(Number.isFinite))
   .sort((a,b)=>a.time-b.time);
  bars=bars.filter((b,i)=>i===0||b.time!==bars[i-1].time);
  bars=bars.slice(-360);
  calculate();
  if(bars.length){
   updatePrice(bars[bars.length-1].close);
   charts[0].timeScale().fitContent();
  }
 }catch(e){
  $('notice').textContent=
   'Historical candle data unavailable. Indicators may be incomplete.';
 }
}
function connect(){
 try{
  socket=new WebSocket('wss://ws-feed.exchange.coinbase.com');
  socket.onopen=()=>{
   retry=1000;
   socket.send(JSON.stringify({
    type:'subscribe',
    product_ids:['BTC-USD'],
    channels:['matches','heartbeat']
   }));
   setStatus('CONNECTED · awaiting trades');
  };
  socket.onmessage=e=>{
   let x;try{x=JSON.parse(e.data)}catch{return;}
   if(x.type==='match'||x.type==='last_match'){
    const p=Number(x.price),t=Date.parse(x.time)/1000,q=Number(x.size);
    if(Number.isFinite(p)&&Number.isFinite(t))
     onTrade(p,t,Number.isFinite(q)?q:0);
   }
  };
  socket.onerror=()=>setStatus('RECONNECTING');
  socket.onclose=()=>{
   setStatus('RECONNECTING');
   setTimeout(connect,retry);
   retry=Math.min(30000,retry*2);
  };
 }catch(e){
  setStatus('UNAVAILABLE');
  setTimeout(connect,10000);
 }
}

if(window.LightweightCharts){
 const main=chartFor('main');
 series.candles=main.addCandlestickSeries({
  upColor:'#25d9a4',downColor:'#ff547c',
  wickUpColor:'#25d9a4',wickDownColor:'#ff547c',
  borderVisible:false
 });
 for(const [name,color,width] of [
  ['ema9','#c68bff',2],['ema21','#58a7ff',2],
  ['ema50','#ffac61',2],['vwap','#57e5aa',2],
  ['bbHigh','#b9b6c7',1],['bbLow','#b9b6c7',1],
  ['support','#44bca9',1],['resistance','#f47e99',1]
 ])series[name]=line(main,color,width,name.startsWith('bb')?2:0);

 if(TARGET!==null)
  series.candles.createPriceLine({
   price:TARGET,color:'#f4d45d',lineWidth:2,
   lineStyle:2,axisLabelVisible:true,
   title:'KALSHI TARGET · snapshot'
  });

 const vol=chartFor('volume');
 series.volume=vol.addHistogramSeries({
  priceFormat:{type:'volume'},priceScaleId:''
 });
 const r=chartFor('rsi');
 series.rsi=line(r,'#bd89ff',2);
 for(const v of [30,70])
  series.rsi.createPriceLine({
   price:v,color:'#8e628c',lineStyle:2,
   axisLabelVisible:true,title:String(v)
  });
 const m=chartFor('macd');
 series.hist=m.addHistogramSeries({
  priceLineVisible:false,lastValueVisible:false
 });
 series.macd=line(m,'#6bb4ff',2);
 series.signal=line(m,'#ffac69',2);

 for(const c of charts.slice(1)){
  main.timeScale().subscribeVisibleLogicalRangeChange(range=>{
   if(range)c.timeScale().setVisibleLogicalRange(range);
  });
 }

 $('clean').onclick=()=>{
  $('clean').classList.add('active');
  $('advanced').classList.remove('active');
  $('extras').style.display='none';
  Object.keys(flags).forEach(k=>flags[k]=false);
  document.querySelectorAll('input[data-key]').forEach(x=>x.checked=false);
  applyFlags();
 };
 $('advanced').onclick=()=>{
  $('advanced').classList.add('active');
  $('clean').classList.remove('active');
  $('extras').style.display='flex';
 };
 document.querySelectorAll('input[data-key]').forEach(x=>{
  x.addEventListener('change',()=>{
   flags[x.dataset.key]=x.checked;
   applyFlags();
  });
 });
 applyFlags();
 load().then(connect);
}else{
 setStatus('CHART LIBRARY UNAVAILABLE');
 $('notice').textContent='Chart library unavailable; original backup remains.';
}
setInterval(()=>{
 if(lastTick&&Date.now()-lastTick>20000)
  setStatus('STALE · no recent trades');
},5000);
</script>
</body>
</html>'''

    components.html(
        html.replace("__TARGET__", json.dumps(strike)),
        height=height,
        scrolling=False,
    )
