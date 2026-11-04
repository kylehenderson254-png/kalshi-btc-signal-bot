
"""QUEEN'S EDGE | 254 — Live BTC Chart + Directional Reversal Watch.

Clean and Advanced chart modes.
Directional bullish/bearish reversal markers.
Coinbase BTC spot is indicative, not Kalshi settlement data.
PAPER ONLY — no orders.
"""

import json
import math
import streamlit.components.v1 as components


def render_live_chart(target=None, height=740):
    try:
        strike = float(target) if target is not None else None
        if strike is not None and not math.isfinite(strike):
            strike = None
    except (TypeError, ValueError):
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
#notice{padding:10px;border:1px solid #694b84;border-radius:8px;color:#ffcb83;background:#1d1430;line-height:1.5;font-weight:650}
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
<span class="muted">Clean: candles + EMA 9/21 + target + volume + RSI + reversal watch</span>
</div>

<div id="extras">
<label><input type="checkbox" data-key="ema50"> EMA 50</label>
<label><input type="checkbox" data-key="vwap"> VWAP</label>
<label><input type="checkbox" data-key="bb"> Bollinger</label>
<label><input type="checkbox" data-key="levels"> Support / resistance</label>
<label><input type="checkbox" data-key="macd"> MACD</label>
<label><input type="checkbox" data-key="markers" checked> Reversal watch</label>
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
<span style="color:#75f2be">▲ WATCH UP · possible bullish reversal</span>
<span style="color:#ff91a9">▼ WATCH DOWN · possible bearish reversal</span>
</div>

<div id="notice">WAIT · Monitoring for possible reversals. No validated trade entry.</div>
<div id="help">Reversal arrows show possible direction, not certainty. UP means potential upward movement; DOWN means potential downward movement. The live chart is separate from V3's server-side candle feed. Coinbase spot is not Kalshi's verified settlement reference.</div>

<script>
const TARGET=__TARGET__;
const $=id=>document.getElementById(id);

const fmt=x=>Number.isFinite(x)?x.toLocaleString('en-US',{
 style:'currency',currency:'USD',minimumFractionDigits:2
}):'—';

$('target').textContent=TARGET===null?'Unavailable':fmt(TARGET);

const flags={
 ema50:false,vwap:false,bb:false,
 levels:false,macd:false,markers:true
};

let bars=[];
let lastTick=0;
let socket=null;
let retry=1000;
let charts=[];
let series={};
let calculationTimer=null;
let connecting=false;
let reconnectTimer=null;

const bg='#0b091b';
const fg='#cbb8e6';
const grid='#281e3c';

function chartFor(id){
 const e=$(id);
 const chart=LightweightCharts.createChart(e,{
  width:e.clientWidth,
  height:e.clientHeight,
  layout:{background:{color:bg},textColor:fg},
  grid:{vertLines:{color:grid},horzLines:{color:grid}},
  rightPriceScale:{borderColor:'#513478'},
  timeScale:{
   timeVisible:true,
   secondsVisible:false,
   borderColor:'#513478'
  },
  crosshair:{mode:LightweightCharts.CrosshairMode.Normal}
 });

 new ResizeObserver(()=>chart.applyOptions({
  width:e.clientWidth,
  height:e.clientHeight
 })).observe(e);

 charts.push(chart);
 return chart;
}

function line(chart,color,width=1,style=0){
 return chart.addLineSeries({
  color,
  lineWidth:width,
  lineStyle:style,
  priceLineVisible:false,
  lastValueVisible:false,
  crosshairMarkerVisible:false
 });
}

function points(values){
 return bars.map((b,i)=>({
  time:b.time,
  value:values[i]
 })).filter(x=>Number.isFinite(x.value));
}

function ema(values,n){
 const out=Array(values.length).fill(null);
 if(values.length<n)return out;

 let seed=0;
 for(let i=0;i<n;i++)seed+=values[i];
 out[n-1]=seed/n;

 const k=2/(n+1);
 for(let i=n;i<values.length;i++){
  out[i]=out[i-1]+k*(values[i]-out[i-1]);
 }
 return out;
}

function rsi(values,n=14){
 const out=Array(values.length).fill(null);
 if(values.length<=n)return out;

 let gain=0,loss=0;
 for(let i=1;i<=n;i++){
  const d=values[i]-values[i-1];
  gain+=Math.max(0,d);
  loss+=Math.max(0,-d);
 }

 gain/=n;
 loss/=n;
 out[n]=loss===0?(gain===0?50:100):100-100/(1+gain/loss);

 for(let i=n+1;i<values.length;i++){
  const d=values[i]-values[i-1];
  gain=(gain*(n-1)+Math.max(0,d))/n;
  loss=(loss*(n-1)+Math.max(0,-d))/n;
  out[i]=loss===0?(gain===0?50:100):100-100/(1+gain/loss);
 }
 return out;
}

function macd(values){
 const fast=ema(values,12);
 const slow=ema(values,26);

 const m=values.map((_,i)=>
  fast[i]===null||slow[i]===null
   ?null:fast[i]-slow[i]
 );

 const valid=m.filter(x=>x!==null);
 const signalValid=ema(valid,9);
 const signal=Array(values.length).fill(null);
 const hist=Array(values.length).fill(null);

 for(let i=0;i<signalValid.length;i++){
  if(signalValid[i]!==null){
   signal[i+25]=signalValid[i];
   hist[i+25]=m[i+25]-signalValid[i];
  }
 }

 return {m,signal,hist};
}

function bollinger(values,n=20){
 const high=Array(values.length).fill(null);
 const low=Array(values.length).fill(null);

 for(let i=n-1;i<values.length;i++){
  const a=values.slice(i-n+1,i+1);
  const mean=a.reduce((x,y)=>x+y,0)/n;
  const sd=Math.sqrt(
   a.reduce((x,y)=>x+(y-mean)**2,0)/n
  );
  high[i]=mean+2*sd;
  low[i]=mean-2*sd;
 }
 return {high,low};
}

function vwap(){
 const out=[];
 let day='',pv=0,vol=0;

 for(const b of bars){
  const d=new Date(b.time*1000).toISOString().slice(0,10);
  if(d!==day){
   day=d;
   pv=0;
   vol=0;
  }
  const q=Math.max(0,b.volume||0);
  pv+=(b.high+b.low+b.close)/3*q;
  vol+=q;
  out.push(vol?pv/vol:null);
 }
 return out;
}

function levels(n=35){
 const low=[],high=[];

 for(let i=0;i<bars.length;i++){
  if(i<n){
   low.push(null);
   high.push(null);
   continue;
  }
  const a=bars.slice(i-n,i);
  low.push(Math.min(...a.map(x=>x.low)));
  high.push(Math.max(...a.map(x=>x.high)));
 }
 return {low,high};
}

function setStatus(value){
 $('status').textContent=value;
 $('status').style.color=
  value==='LIVE'?'#75f2be':'#ffbc88';
}

function updatePrice(price){
 $('price').textContent=fmt(price);

 if(TARGET!==null){
  const distance=price-TARGET;
  $('distance').textContent=
   (distance>=0?'+':'')+fmt(distance);
  $('distance').style.color=
   distance>=0?'#75f2be':'#ff91a9';
 }
}

function applyFlags(){
 const groups={
  ema50:['ema50'],
  vwap:['vwap'],
  bb:['bbHigh','bbLow'],
  levels:['support','resistance']
 };

 for(const [key,names] of Object.entries(groups)){
  for(const name of names){
   if(series[name]){
    series[name].applyOptions({visible:flags[key]});
   }
  }
 }

 $('macdPanel').style.display=flags.macd?'block':'none';

 if(series.candles){
  series.candles.setMarkers(
   flags.markers?(series.markers||[]):[]
  );
 }
}

/*
 Directional reversal watch.

 These are historical and current heuristic warnings.
 They are NOT validated forecasts.

 DOWN:
 Price above target, approaching target,
 bearish EMA alignment, weakening MACD.

 UP:
 Price below target, approaching target,
 bullish EMA alignment, improving MACD.
*/
function detectReversals(close,e9,e21,rv,mc){
 const markers=[];
 if(TARGET===null)return markers;

 for(let i=52;i<bars.length;i++){
  if(
   !Number.isFinite(rv[i])||
   !Number.isFinite(mc.hist[i])||
   !Number.isFinite(mc.hist[i-1])||
   !Number.isFinite(e9[i])||
   !Number.isFinite(e21[i])
  )continue;

  const near=
   Math.abs(close[i]-TARGET)<
   Math.max(40,close[i]*0.001);

  const toward=
   (close[i]-TARGET)*(close[i]-close[i-3])<0;

  const bearish=
   close[i]>TARGET&&
   e9[i]<e21[i]&&
   mc.hist[i]<mc.hist[i-1];

  const bullish=
   close[i]<TARGET&&
   e9[i]>e21[i]&&
   mc.hist[i]>mc.hist[i-1];

  if(!near||!toward||(!bearish&&!bullish)){
   continue;
  }

  if(
   markers.length&&
   bars[i].time-markers[markers.length-1].time<480
  )continue;

  markers.push({
   time:bars[i].time,
   position:bearish?'aboveBar':'belowBar',
   color:bearish?'#ff547c':'#25d9a4',
   shape:bearish?'arrowDown':'arrowUp',
   text:bearish?'WATCH ↓ DOWN':'WATCH ↑ UP',
   direction:bearish?'DOWN':'UP'
  });
 }

 return markers;
}

function updateReversalNotice(markers){
 const notice=$('notice');

 if(!bars.length){
  notice.textContent='WAIT · Collecting chart data.';
  return;
 }

 if(lastTick&&Date.now()-lastTick>20000){
  notice.textContent=
   'DATA STALE · Reversal direction unavailable until live feed resumes.';
  notice.style.color='#ffcb83';
  return;
 }

 const lastBar=bars[bars.length-1];
 const latest=markers.length?markers[markers.length-1]:null;

 const recent=
  latest&&latest.time>=lastBar.time-180;

 if(!recent){
  notice.textContent=
   'WAIT · No recent directional reversal warning. No validated trade entry.';
  notice.style.color='#ffcb83';
  return;
 }

 if(latest.direction==='UP'){
  notice.textContent=
   '▲ REVERSAL WATCH · POSSIBLE UPWARD MOVE · Bullish momentum is developing near the target. Not a confirmed trade.';
  notice.style.color='#75f2be';
 }else{
  notice.textContent=
   '▼ REVERSAL WATCH · POSSIBLE DOWNWARD MOVE · Bearish momentum is developing near the target. Not a confirmed trade.';
  notice.style.color='#ff91a9';
 }
}

function calculate(){
 if(!bars.length)return;

 const close=bars.map(b=>b.close);
 const e9=ema(close,9);
 const e21=ema(close,21);
 const e50=ema(close,50);
 const rv=rsi(close);
 const mc=macd(close);
 const bb=bollinger(close);
 const vw=vwap();
 const lev=levels();

 const values={
  ema9:e9,
  ema21:e21,
  ema50:e50,
  vwap:vw,
  bbHigh:bb.high,
  bbLow:bb.low,
  support:lev.low,
  resistance:lev.high,
  rsi:rv,
  macd:mc.m,
  signal:mc.signal
 };

 for(const [name,value] of Object.entries(values)){
  series[name].setData(points(value));
 }

 series.candles.setData(bars.map(b=>({
  time:b.time,
  open:b.open,
  high:b.high,
  low:b.low,
  close:b.close
 })));

 series.volume.setData(bars.map(b=>({
  time:b.time,
  value:b.volume||0,
  color:b.close>=b.open?'#2bb88d':'#cc5079'
 })));

 series.hist.setData(bars.map((b,i)=>({
  time:b.time,
  value:mc.hist[i]||0,
  color:(mc.hist[i]||0)>=0?'#2bb88d':'#cc5079'
 })));

 const last=close.length-1;

 if(e9[last]!==null&&e21[last]!==null){
  const upward=e9[last]>e21[last];
  $('trend').textContent=upward?'UPWARD':'DOWNWARD';
  $('trend').style.color=
   upward?'#75f2be':'#ff91a9';
 }

 const markers=detectReversals(close,e9,e21,rv,mc);
 series.markers=markers;

 applyFlags();
 updateReversalNotice(markers);
}

function schedule(){
 if(calculationTimer)return;

 calculationTimer=setTimeout(()=>{
  calculationTimer=null;
  calculate();
 },750);
}

function onTrade(price,time,quantity){
 if(
  !Number.isFinite(price)||
  !Number.isFinite(time)||
  !Number.isFinite(quantity)||
  quantity<0
 )return;

 const minute=Math.floor(time/60)*60;
 let old=bars[bars.length-1];

 if(old&&minute<old.time)return;

 if(old&&minute===old.time){
  old.high=Math.max(old.high,price);
  old.low=Math.min(old.low,price);
  old.close=price;
  old.volume+=quantity;
 }else{
  if(old){
   for(let x=old.time+60;x<minute;x+=60){
    bars.push({
     time:x,
     open:old.close,
     high:old.close,
     low:old.close,
     close:old.close,
     volume:0
    });
    old=bars[bars.length-1];
   }
  }

  bars.push({
   time:minute,
   open:old?old.close:price,
   high:price,
   low:price,
   close:price,
   volume:quantity
  });

  if(bars.length>360){
   bars=bars.slice(-360);
  }
 }

 updatePrice(price);
 lastTick=Date.now();
 setStatus('LIVE');
 schedule();
}

async function load(){
 try{
  const response=await fetch(
   'https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60',
   {cache:'no-store'}
  );

  if(!response.ok){
   throw Error('HTTP '+response.status);
  }

  const data=await response.json();

  bars=data
   .filter(x=>Array.isArray(x)&&x.length>=6)
   .map(x=>({
    time:Number(x[0]),
    low:Number(x[1]),
    high:Number(x[2]),
    open:Number(x[3]),
    close:Number(x[4]),
    volume:Number(x[5])
   }))
   .filter(b=>Object.values(b).every(Number.isFinite))
   .sort((a,b)=>a.time-b.time);

  bars=bars.filter(
   (b,i)=>i===0||b.time!==bars[i-1].time
  );

  bars=bars.slice(-360);
  calculate();

  if(bars.length){
   updatePrice(bars[bars.length-1].close);
   charts[0].timeScale().fitContent();
  }
 }catch(error){
  $('notice').textContent=
   'Historical candle data unavailable. Indicators may be incomplete.';
 }
}

function reconnect(){
 if(reconnectTimer)return;

 reconnectTimer=setTimeout(()=>{
  reconnectTimer=null;
  connect();
 },retry);

 retry=Math.min(30000,retry*2);
}

function connect(){
 if(connecting)return;
 connecting=true;

 try{
  socket=new WebSocket(
   'wss://ws-feed.exchange.coinbase.com'
  );

  socket.onopen=()=>{
   connecting=false;
   retry=1000;

   socket.send(JSON.stringify({
    type:'subscribe',
    product_ids:['BTC-USD'],
    channels:['matches','heartbeat']
   }));

   setStatus('CONNECTED · awaiting trades');
  };

  socket.onmessage=event=>{
   let data;

   try{
    data=JSON.parse(event.data);
   }catch(error){
    return;
   }

   if(
    data.type==='match'||
    data.type==='last_match'
   ){
    const price=Number(data.price);
    const time=Date.parse(data.time)/1000;
    const quantity=Number(data.size);

    if(
     Number.isFinite(price)&&
     Number.isFinite(time)
    ){
     onTrade(
      price,
      time,
      Number.isFinite(quantity)?quantity:0
     );
    }
   }
  };

  socket.onerror=()=>{
   setStatus('RECONNECTING');
  };

  socket.onclose=()=>{
   connecting=false;
   setStatus('RECONNECTING');
   reconnect();
  };

 }catch(error){
  connecting=false;
  setStatus('UNAVAILABLE');
  reconnect();
 }
}

if(window.LightweightCharts){
 const main=chartFor('main');

 series.candles=main.addCandlestickSeries({
  upColor:'#25d9a4',
  downColor:'#ff547c',
  wickUpColor:'#25d9a4',
  wickDownColor:'#ff547c',
  borderVisible:false
 });

 for(const [name,color,width] of [
  ['ema9','#c68bff',2],
  ['ema21','#58a7ff',2],
  ['ema50','#ffac61',2],
  ['vwap','#57e5aa',2],
  ['bbHigh','#b9b6c7',1],
  ['bbLow','#b9b6c7',1],
  ['support','#44bca9',1],
  ['resistance','#f47e99',1]
 ]){
  series[name]=line(
   main,
   color,
   width,
   name.startsWith('bb')?2:0
  );
 }

 if(TARGET!==null){
  series.candles.createPriceLine({
   price:TARGET,
   color:'#f4d45d',
   lineWidth:2,
   lineStyle:2,
   axisLabelVisible:true,
   title:'KALSHI TARGET · snapshot'
  });
 }

 const vol=chartFor('volume');

 series.volume=vol.addHistogramSeries({
  priceFormat:{type:'volume'},
  priceScaleId:''
 });

 const r=chartFor('rsi');
 series.rsi=line(r,'#bd89ff',2);

 for(const value of [30,70]){
  series.rsi.createPriceLine({
   price:value,
   color:'#8e628c',
   lineStyle:2,
   axisLabelVisible:true,
   title:String(value)
  });
 }

 const m=chartFor('macd');

 series.hist=m.addHistogramSeries({
  priceLineVisible:false,
  lastValueVisible:false
 });

 series.macd=line(m,'#6bb4ff',2);
 series.signal=line(m,'#ffac69',2);

 for(const chart of charts.slice(1)){
  main.timeScale().subscribeVisibleLogicalRangeChange(range=>{
   if(range){
    chart.timeScale().setVisibleLogicalRange(range);
   }
  });
 }

 $('clean').onclick=()=>{
  $('clean').classList.add('active');
  $('advanced').classList.remove('active');
  $('extras').style.display='none';

  Object.keys(flags).forEach(key=>{
   flags[key]=false;
  });

  flags.markers=true;

  document.querySelectorAll('input[data-key]').forEach(input=>{
   input.checked=input.dataset.key==='markers';
  });

  applyFlags();
 };

 $('advanced').onclick=()=>{
  $('advanced').classList.add('active');
  $('clean').classList.remove('active');
  $('extras').style.display='flex';
 };

 document.querySelectorAll('input[data-key]').forEach(input=>{
  input.addEventListener('change',()=>{
   flags[input.dataset.key]=input.checked;
   applyFlags();
  });
 });

 applyFlags();
 load().then(connect);

}else{
 setStatus('CHART LIBRARY UNAVAILABLE');
 $('notice').textContent=
  'Chart library unavailable; original backup remains.';
}

setInterval(()=>{
 if(lastTick&&Date.now()-lastTick>20000){
  setStatus('STALE · no recent trades');
  updateReversalNotice(series.markers||[]);
 }
},5000);

</script>
</body>
</html>'''

    components.html(
        html.replace("__TARGET__", json.dumps(strike)),
        height=height,
        scrolling=False,
    )
