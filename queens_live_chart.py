
"""QUEEN'S EDGE | 254 — compact live BTC chart.

PAPER ONLY. No orders or changes to saved paper results.
Coinbase BTC/USD is indicative, not Kalshi settlement data.
"""

import json
import math
from datetime import datetime, timezone

import streamlit.components.v1 as components


def render_live_chart(
    target=None, height=510, *, close_time=None, market_ticker=None
):
    try:
        strike = float(target)
        if not math.isfinite(strike) or strike <= 0:
            strike = None
    except (TypeError, ValueError, OverflowError):
        strike = None

    close_ms = None
    try:
        if isinstance(close_time, (int, float)):
            close_ms = float(close_time)
            if close_ms < 1_000_000_000_000:
                close_ms *= 1000
        elif close_time is not None:
            dt = close_time
            if isinstance(dt, str):
                dt = datetime.fromisoformat(
                    dt.replace("Z", "+00:00")
                )
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            close_ms = dt.timestamp() * 1000
        if close_ms is not None and not math.isfinite(close_ms):
            close_ms = None
    except (TypeError, ValueError, OverflowError, AttributeError):
        close_ms = None

    config = json.dumps({
        "target": strike,
        "closeMs": close_ms,
        "ticker": str(market_ticker or "")
    }, allow_nan=False).replace("<", "\\u003c")

    html = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}
html,body{
 margin:0;padding:0;background:#0b091b;color:#eee5ff;
 font:13px system-ui,Arial,sans-serif;overflow-x:hidden
}
#summary{
 display:grid;grid-template-columns:repeat(4,minmax(0,1fr));
 gap:7px;margin-bottom:8px
}
.tile{
 background:#211537;border:1px solid #49316d;
 border-radius:9px;padding:9px;min-width:0
}
.label{font-size:11px;color:#bda8d9}
.value{font-size:17px;font-weight:800;margin-top:3px;overflow-wrap:anywhere}
#toolbar{
 display:flex;flex-wrap:wrap;align-items:center;
 gap:7px;margin:7px 0
}
button,select{
 background:#271941;color:#f2e8ff;border:1px solid #65428e;
 border-radius:8px;padding:7px 10px;font:600 12px system-ui
}
button{cursor:pointer}
button.active{background:#62419a;border-color:#b18aff}
button:focus-visible,select:focus-visible{
 outline:2px solid #d6b5ff
}
#main{
 height:340px;border:1px solid #553878;
 border-radius:10px;overflow:hidden
}
#rsiPanel{
 display:none;height:100px;margin-top:6px;
 border:1px solid #553878;border-radius:9px;overflow:hidden
}
#notice{
 margin-top:7px;padding:9px 11px;
 background:#211537;border:1px solid #614183;
 border-radius:8px;font-weight:700;color:#ffcb83
}
#legend{
 display:flex;flex-wrap:wrap;gap:13px;
 color:#d6c4ed;font-size:11px;margin:8px 2px
}
#foot{font-size:10px;color:#b9a5d4;line-height:1.5}
#foot a{color:#d5b5ff}
@media(max-width:650px){
 #summary{grid-template-columns:repeat(2,minmax(0,1fr))}
 .value{font-size:15px}
 #main{height:310px}
}
</style>
</head>
<body>

<div id="summary">
 <div class="tile"><div class="label">BTC / USD · LIVE</div>
  <div class="value" id="price">Connecting…</div></div>
 <div class="tile"><div class="label">KALSHI TARGET</div>
  <div class="value" id="target">—</div></div>
 <div class="tile"><div class="label">DISTANCE</div>
  <div class="value" id="distance">—</div></div>
 <div class="tile"><div class="label">EMA 9 / 21</div>
  <div class="value" id="trend">WAIT</div></div>
</div>

<div id="toolbar">
 <button id="follow" class="active">✓ FOLLOW LIVE</button>
 <select id="range" aria-label="Chart time range">
  <option value="30">30 MIN</option>
  <option value="60" selected>1 HOUR</option>
  <option value="120">2 HOURS</option>
  <option value="240">4 HOURS</option>
 </select>
 <button id="rsiButton">SHOW RSI</button>
 <button id="reset">RESET VIEW</button>
 <span id="feed" class="label">Connecting…</span>
</div>

<div id="main"></div>
<div id="rsiPanel"></div>

<div id="legend">
 <span>🟣 EMA 9</span>
 <span>🔵 EMA 21</span>
 <span>🟡 Kalshi target</span>
 <span style="color:#75f2be">▲ WATCH UP</span>
 <span style="color:#ff91a9">▼ WATCH DOWN</span>
</div>

<div id="notice" role="status">WAIT · Loading candles.</div>
<div id="foot">
 PAPER ONLY · Experimental momentum warnings, not guaranteed entries
 or settlement predictions. Coinbase spot is not Kalshi settlement data.
 Historical arrows are retrospective pattern matches.
 <a href="https://www.tradingview.com/" target="_blank"
 rel="noopener noreferrer">TradingView Lightweight Charts™</a>
 · Copyright © TradingView, Inc.
</div>

<script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
const CONFIG=__CONFIG__;
const $=id=>document.getElementById(id);
const green='#75f2be',red='#ff91a9',amber='#ffcb83';
const fmt=n=>Number.isFinite(n)
 ?n.toLocaleString('en-US',{
   style:'currency',currency:'USD',
   minimumFractionDigits:2,maximumFractionDigits:2
 }):'—';

let bars=[],socket=null,chart=null,candles=null;
let ema9Line=null,ema21Line=null,targetLine=null;
let rsiChart=null,rsiLine=null;
let follow=true,zoom=60,rsiVisible=false;
let ready=false,restoring=false,lastTrade=0;
let reconnectTimer=null,lastMinute=0;
let lastPrice=null;

const storeKey='qe254-live-chart-v5';
try{
 const saved=JSON.parse(sessionStorage.getItem(storeKey)||'{}');
 if([30,60,120,240].includes(saved.zoom))zoom=saved.zoom;
 if(typeof saved.follow==='boolean')follow=saved.follow;
 rsiVisible=saved.rsi===true;
}catch(e){}

function save(){
 try{
  sessionStorage.setItem(storeKey,JSON.stringify({
   zoom,follow,rsi:rsiVisible
  }));
 }catch(e){}
}

function setNotice(text,color=amber){
 $('notice').textContent=text;
 $('notice').style.color=color;
}

function setFollow(value){
 follow=value;
 $('follow').classList.toggle('active',follow);
 $('follow').textContent=follow?'✓ FOLLOW LIVE':'FOLLOW PAUSED';
 save();
}

function makeChart(element,height){
 return LightweightCharts.createChart(element,{
  width:Math.max(1,element.clientWidth),
  height,
  layout:{
   background:{color:'#0b091b'},textColor:'#d0bde8'
  },
  grid:{
   vertLines:{color:'#281e3c'},
   horzLines:{color:'#281e3c'}
  },
  rightPriceScale:{borderColor:'#513478'},
  timeScale:{
   timeVisible:true,secondsVisible:false,
   borderColor:'#513478',rightOffset:4,
   shiftVisibleRangeOnNewBar:true
  },
  crosshair:{mode:LightweightCharts.CrosshairMode.Normal}
 });
}

function ema(values,n){
 const out=Array(values.length).fill(null);
 if(values.length<n)return out;
 let current=values.slice(0,n).reduce((a,b)=>a+b,0)/n;
 out[n-1]=current;
 const k=2/(n+1);
 for(let i=n;i<values.length;i++){
  current+=(values[i]-current)*k;
  out[i]=current;
 }
 return out;
}

function rsi(values,n=14){
 const out=Array(values.length).fill(null);
 if(values.length<=n)return out;
 let gain=0,loss=0;
 for(let i=1;i<=n;i++){
  const d=values[i]-values[i-1];
  gain+=Math.max(0,d)/n;
  loss+=Math.max(0,-d)/n;
 }
 function score(){
  return loss===0?(gain===0?50:100):100-100/(1+gain/loss);
 }
 out[n]=score();
 for(let i=n+1;i<values.length;i++){
  const d=values[i]-values[i-1];
  gain=(gain*(n-1)+Math.max(0,d))/n;
  loss=(loss*(n-1)+Math.max(0,-d))/n;
  out[i]=score();
 }
 return out;
}

function visibleWidth(){
 if(!chart)return zoom;
 const range=chart.timeScale().getVisibleLogicalRange();
 if(range&&Number.isFinite(range.to-range.from)){
  return Math.max(15,Math.min(300,range.to-range.from));
 }
 return zoom;
}

function moveToLive(width){
 if(!chart||!bars.length)return;
 restoring=true;
 try{
  const right=bars.length+3;
  chart.timeScale().setVisibleLogicalRange({
   from:right-width,to:right
  });
 }catch(e){}
 restoring=false;
}

function render(){
 if(!bars.length||!candles)return;
 const width=visibleWidth();
 const close=bars.map(b=>b.close);
 const e9=ema(close,9),e21=ema(close,21);
 const rv=rsi(close);

 const points=a=>a.map((v,i)=>({
  time:bars[i].time,value:v
 })).filter(p=>Number.isFinite(p.value));

 const candleData=bars.map(b=>({
  time:b.time,open:b.open,high:b.high,
  low:b.low,close:b.close
 }));

 candles.setData(candleData);
 ema9Line.setData(points(e9));
 ema21Line.setData(points(e21));
 rsiLine.setData(points(rv));

 const markers=[];
 for(let i=25;i<bars.length-1;i++){
  if(bars[i].time-bars[i-2].time!==120)continue;
  if(![e9[i],e9[i-1],e9[i-2],rv[i],rv[i-1]]
     .every(Number.isFinite))continue;

  const previous=e9[i-1]-e9[i-2];
  const slope=e9[i]-e9[i-1];
  const up=previous<=0&&slope>0&&
   close[i]>close[i-1]&&rv[i]>rv[i-1]&&rv[i]>=50;
  const down=previous>=0&&slope<0&&
   close[i]<close[i-1]&&rv[i]<rv[i-1]&&rv[i]<=50;

  if(up||down){
   const last=markers.at(-1);
   if(last&&bars[i].time-last.time<300)continue;
   markers.push({
    time:bars[i].time,
    position:up?'belowBar':'aboveBar',
    color:up?green:red,
    shape:up?'arrowUp':'arrowDown',
    text:up?'WATCH UP':'WATCH DOWN'
   });
  }
 }
 candles.setMarkers(markers);

 const i=bars.length-1;
 const difference=e9[i]-e21[i];
 if(Number.isFinite(difference)){
  $('trend').textContent=difference>0?'▲ UP':
   difference<0?'▼ DOWN':'NEUTRAL';
  $('trend').style.color=difference>0?green:
   difference<0?red:amber;
 }

 if(!ready){
  ready=true;
  moveToLive(zoom);
 }else if(follow){
  moveToLive(width);
 }
 updateNotice();
}

function updatePrice(price){
 lastPrice=price;
 $('price').textContent=fmt(price);
 if(CONFIG.target!==null){
  const d=price-CONFIG.target;
  $('distance').textContent=(d>=0?'+':'−')+fmt(Math.abs(d));
  $('distance').style.color=d>=0?green:red;
 }
}

function updateNotice(){
 if(Date.now()-lastTrade>12000){
  $('feed').textContent='FEED STALE';
  setNotice('WAIT · Live trade feed is stale.');
  return;
 }
 $('feed').textContent='● LIVE · COINBASE';
 if(CONFIG.closeMs!==null&&Date.now()>=CONFIG.closeMs){
  setNotice('CONTRACT EXPIRED · Refresh for the new target.');
  return;
 }
 if(CONFIG.target===null){
  setNotice('WAIT · Kalshi target unavailable.');
  return;
 }
 if(bars.length<30){
  setNotice('WAIT · Collecting enough candles.');
  return;
 }
 const distance=lastPrice-CONFIG.target;
 if(Math.abs(distance)<25){
  setNotice('⚠ TARGET DANGER · BTC is within $25 of target.');
 }else{
  setNotice(
   'MONITOR · '+(distance>=0?'BTC ABOVE TARGET':'BTC BELOW TARGET')+
   ' · WATCH arrows are experimental, not trade instructions.',
   distance>=0?green:red
  );
 }
}

function addTrade(price,time,volume=0){
 if(!Number.isFinite(price)||price<=0)return;
 const minute=Math.floor(time/60)*60;
 if(!Number.isFinite(minute))return;
 if(bars.length&&minute<bars.at(-1).time)return;

 const last=bars.at(-1);
 if(last&&last.time===minute){
  last.high=Math.max(last.high,price);
  last.low=Math.min(last.low,price);
  last.close=price;
  last.volume+=(volume||0);
 }else{
  if(last&&minute-last.time>60){
   // Do not manufacture missing candles.
   setNotice('WAIT · Candle gap detected. Reloading history.');
   loadHistory();
   return;
  }
  bars.push({
   time:minute,open:price,high:price,
   low:price,close:price,volume:volume||0
  });
  if(bars.length>600)bars.shift();
 }
 lastTrade=Date.now();
 updatePrice(price);
 render();
}

async function loadHistory(){
 try{
  const end=Math.floor(Date.now()/1000);
  const start=end-300*60;
  const url='https://api.exchange.coinbase.com/products/BTC-USD/candles'
   +'?granularity=60&start='+new Date(start*1000).toISOString()
   +'&end='+new Date(end*1000).toISOString();

  const response=await fetch(url,{cache:'no-store'});
  if(!response.ok)throw Error('Coinbase history HTTP '+response.status);
  const raw=await response.json();
  if(!Array.isArray(raw))throw Error('Invalid candle response');

  const history=raw.filter(x=>Array.isArray(x)&&x.length>=5)
   .map(x=>({
    time:Number(x[0]),low:Number(x[1]),high:Number(x[2]),
    open:Number(x[3]),close:Number(x[4]),
    volume:Number(x[5]||0)
   }))
   .filter(b=>[b.time,b.open,b.high,b.low,b.close]
    .every(Number.isFinite))
   .sort((a,b)=>a.time-b.time);

  if(history.length<30)throw Error('Not enough history');
  const unique=new Map(history.map(b=>[b.time,b]));
  bars=[...unique.values()];
  ready=false;
  render();
 }catch(e){
  setNotice('WAIT · Candle history unavailable: '+String(e.message||e));
 }
}

function connect(){
 if(socket){
  try{socket.close()}catch(e){}
 }
 socket=new WebSocket('wss://ws-feed.exchange.coinbase.com');
 socket.onopen=()=>{
  socket.send(JSON.stringify({
   type:'subscribe',
   product_ids:['BTC-USD'],
   channels:['matches']
  }));
 };
 socket.onmessage=event=>{
  try{
   const d=JSON.parse(event.data);
   if(d.type!=='match'&&d.type!=='last_match')return;
   if(d.product_id!=='BTC-USD')return;
   const price=Number(d.price);
   const time=Date.parse(d.time)/1000;
   if(!Number.isFinite(time))return;
   addTrade(price,time,Number(d.size||0));
  }catch(e){}
 };
 socket.onclose=()=>{
  $('feed').textContent='RECONNECTING…';
  clearTimeout(reconnectTimer);
  reconnectTimer=setTimeout(connect,3000);
 };
 socket.onerror=()=>socket.close();
}

function init(){
 $('target').textContent=CONFIG.target===null
  ?'Unavailable':fmt(CONFIG.target);

 chart=makeChart($('main'),340);
 candles=chart.addCandlestickSeries({
  upColor:'#37d7a3',downColor:'#ff6488',
  borderUpColor:'#37d7a3',borderDownColor:'#ff6488',
  wickUpColor:'#37d7a3',wickDownColor:'#ff6488'
 });
 ema9Line=chart.addLineSeries({
  color:'#bb77ff',lineWidth:2,
  priceLineVisible:false,lastValueVisible:false
 });
 ema21Line=chart.addLineSeries({
  color:'#54b9ff',lineWidth:2,
  priceLineVisible:false,lastValueVisible:false
 });

 if(CONFIG.target!==null){
  targetLine=candles.createPriceLine({
   price:CONFIG.target,color:'#ffd36e',
   lineWidth:2,
   lineStyle:LightweightCharts.LineStyle.Dashed,
   axisLabelVisible:true,title:'KALSHI TARGET'
  });
 }

 rsiChart=makeChart($('rsiPanel'),100);
 rsiLine=rsiChart.addLineSeries({
  color:'#bb77ff',lineWidth:2,
  priceLineVisible:false
 });
 for(const level of [30,50,70]){
  rsiLine.createPriceLine({
   price:level,color:'#6a527e',
   lineWidth:1,
   lineStyle:LightweightCharts.LineStyle.Dashed,
   axisLabelVisible:true
  });
 }

 $('range').value=String(zoom);
 $('rsiPanel').style.display=rsiVisible?'block':'none';
 $('rsiButton').textContent=rsiVisible?'HIDE RSI':'SHOW RSI';
 setFollow(follow);

 chart.timeScale().subscribeVisibleLogicalRangeChange(()=>{
  if(restoring||!ready)return;
  const width=visibleWidth();
  zoom=width;
  // User zoom/pan pauses automatic following.
  // FOLLOW LIVE resumes with this same width.
  setFollow(false);
  save();
 });

 $('follow').onclick=()=>{
  setFollow(true);
  moveToLive(visibleWidth());
 };

 $('reset').onclick=()=>{
  zoom=60;
  $('range').value='60';
  setFollow(true);
  moveToLive(60);
  save();
 };

 $('range').onchange=()=>{
  zoom=Number($('range').value);
  setFollow(true);
  moveToLive(zoom);
  save();
 };

 $('rsiButton').onclick=()=>{
  rsiVisible=!rsiVisible;
  $('rsiPanel').style.display=rsiVisible?'block':'none';
  $('rsiButton').textContent=rsiVisible?'HIDE RSI':'SHOW RSI';
  save();
  resize();
 };

 function resize(){
  chart.applyOptions({width:Math.max(1,$('main').clientWidth)});
  rsiChart.applyOptions({
   width:Math.max(1,$('rsiPanel').clientWidth)
  });
  try{
   window.parent.postMessage({
    isStreamlitMessage:true,
    type:'streamlit:setFrameHeight',
    height:document.documentElement.scrollHeight+8
   },'*');
  }catch(e){}
 }
 new ResizeObserver(resize).observe(document.body);
 resize();

 loadHistory().then(connect);
 setInterval(updateNotice,3000);
 // Periodically reconcile websocket data with Coinbase history.
 setInterval(loadHistory,180000);
}

if(typeof LightweightCharts==='undefined'){
 setNotice('WAIT · Chart library unavailable.');
}else{
 init();
}
</script>
</body>
</html>
"""
    html = html.replace("__CONFIG__", config)
    components.html(html, height=max(int(height), 510), scrolling=True)
