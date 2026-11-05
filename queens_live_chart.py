"""QUEEN'S EDGE | 254 — streaming BTC chart.

PAPER ONLY. This module never places orders or changes tracker history.
Coinbase BTC/USD is indicative, not Kalshi settlement data.
"""

import json
import math
from datetime import datetime, timezone

import streamlit.components.v1 as components


def render_live_chart(
    target=None, height=510, *, close_time=None, market_ticker=None
):
    """Optional close_time must be the official contract close."""

    try:
        strike = float(target) if target is not None else None

        if strike is not None:
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

        if close_ms is not None:
            if not math.isfinite(close_ms):
                close_ms = None

    except (
        TypeError,
        ValueError,
        OverflowError,
        AttributeError,
        OSError,
    ):
        close_ms = None

    config = json.dumps(
        {
            "target": strike,
            "closeMs": close_ms,
            "ticker": str(market_ticker or ""),
        },
        allow_nan=False,
    ).replace("<", "\\u003c")

    html = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">

<style>
*{box-sizing:border-box}

html,body{
 margin:0;
 padding:0;
 background:#0b091b;
 color:#eee5ff;
 font:12px system-ui,Segoe UI,Arial,sans-serif;
 overflow-x:hidden;
}

#summary{
 display:grid;
 grid-template-columns:repeat(7,minmax(0,1fr));
 gap:7px;
 padding:8px;
 background:#160f2c;
 border:1px solid #654098;
 border-radius:12px;
}

.tile{
 min-width:0;
 background:#23163c;
 border-radius:8px;
 padding:8px;
}

.muted{
 color:#b9a5d4;
 font-size:11px;
}

.value{
 font-size:15px;
 font-weight:750;
 margin-top:4px;
 overflow-wrap:anywhere;
}

#toolbar,#extras,#legend{
 display:flex;
 gap:8px;
 align-items:center;
 flex-wrap:wrap;
 margin:8px 0;
}

button{
 cursor:pointer;
 background:#25183d;
 color:#eee5ff;
 border:1px solid #654098;
 border-radius:8px;
 padding:7px 11px;
 font:600 11px system-ui;
}

button.active{
 background:#55318c;
 border-color:#a77aff;
}

button:focus-visible,
a:focus-visible,
input:focus-visible{
 outline:2px solid #d4b5ff;
 outline-offset:2px;
}

#extras{display:none}

label{
 cursor:pointer;
 background:#24183d;
 border:1px solid #543779;
 border-radius:7px;
 padding:5px 7px;
 white-space:nowrap;
}

input{accent-color:#b77dff}

.panel{
 border:1px solid #513478;
 border-radius:10px;
 overflow:hidden;
 margin-bottom:7px;
 position:relative;
}

.panelTitle{
 position:absolute;
 top:6px;
 left:8px;
 z-index:2;
 background:#110d22db;
 border-radius:4px;
 padding:3px 7px;
 color:#d9c5ff;
 pointer-events:none;
 font-size:10px;
}

#main{height:355px}
#volume{height:78px}
#rsi,#macd{height:105px}
.optionalPanel{display:none}

#legend{
 font-size:11px;
 color:#cbb5e8;
}

#notice{
 padding:9px 10px;
 border:1px solid #694b84;
 border-radius:8px;
 color:#ffcb83;
 background:#1d1430;
 line-height:1.45;
 font-weight:650;
}

#help{
 color:#bda9d5;
 line-height:1.5;
 padding:7px 2px 3px;
 font-size:10px;
}

#help a{color:#cba7ff}
#contract{margin-left:auto}

@media(max-width:900px){
 #summary{
  grid-template-columns:repeat(4,minmax(0,1fr));
 }
}

@media(max-width:520px){
 #summary{
  grid-template-columns:repeat(2,minmax(0,1fr));
 }

 #main{height:320px}
 #contract{margin-left:0}
}
</style>
</head>

<body>

<div id="summary">
 <div class="tile">
  <div class="muted">BTC/USD · Coinbase</div>
  <div class="value" id="price">Connecting…</div>
 </div>

 <div class="tile">
  <div class="muted">Kalshi target · snapshot</div>
  <div class="value" id="target">—</div>
 </div>

 <div class="tile">
  <div class="muted">Distance to target</div>
  <div class="value" id="distance">—</div>
 </div>

 <div class="tile">
  <div class="muted">EMA 9 / EMA 21</div>
  <div class="value" id="trend">Collecting…</div>
 </div>

 <div class="tile">
  <div class="muted">RSI 14 · 1 minute</div>
  <div class="value" id="rsiValue">—</div>
 </div>

 <div class="tile">
  <div class="muted">Spot change · 20s</div>
  <div class="value" id="momentum">Collecting…</div>
 </div>

 <div class="tile">
  <div class="muted">Feed / trade age</div>
  <div class="value" id="status">Connecting…</div>
 </div>
</div>

<div id="toolbar">
 <button id="clean" class="active">✓ CLEAN MODE</button>
 <button id="advanced">ADVANCED MODE</button>

 <button id="resetView"
         title="Follow the latest 60 candles">
  FOLLOW LIVE
 </button>

 <span class="muted" id="modeDescription">
  EMA 9/21 · target · reversal arrows
 </span>

 <span class="muted" id="contract"></span>
</div>

<div id="extras">
 <label>
  <input type="checkbox" data-key="volume"> Volume
 </label>

 <label>
  <input type="checkbox" data-key="rsi"> RSI (14)
 </label>

 <label>
  <input type="checkbox" data-key="macd"> MACD
 </label>

 <label>
  <input type="checkbox" data-key="ema50"> EMA 50
 </label>

 <label>
  <input type="checkbox" data-key="vwap"> VWAP*
 </label>

 <label>
  <input type="checkbox" data-key="bb"> Bollinger
 </label>

 <label>
  <input type="checkbox" data-key="levels">
  Support / resistance
 </label>

 <label>
  <input type="checkbox" data-key="markers" checked>
  Reversal arrows
 </label>
</div>

<div class="panel">
 <div class="panelTitle">
  BTC · 1-minute candles / Kalshi target snapshot
 </div>
 <div id="main"></div>
</div>

<div class="panel optionalPanel" id="volumePanel">
 <div class="panelTitle">
  Coinbase volume · may be incomplete after feed gaps
 </div>
 <div id="volume"></div>
</div>

<div class="panel optionalPanel" id="rsiPanel">
 <div class="panelTitle">
  RSI 14 · 30 / 50 / 70
 </div>
 <div id="rsi"></div>
</div>

<div class="panel optionalPanel" id="macdPanel">
 <div class="panelTitle">
  MACD 12 / 26 / 9
 </div>
 <div id="macd"></div>
</div>

<div id="legend">
 <span>🟣 EMA 9</span>
 <span>🔵 EMA 21</span>
 <span>🟡 Target snapshot</span>
 <span style="color:#75f2be">▲ WATCH UP</span>
 <span style="color:#ff91a9">▼ WATCH DOWN</span>
</div>

<div id="notice" role="status" aria-live="polite">
 WAIT · Connecting to live trades.
</div>

<div id="help">
 PAPER ONLY. Warnings are experimental observations,
 not BUY/SELL or settlement-lock confirmations.
 WATCH labels mark EMA-slope turns with RSI and MACD confirmation.
 Historical labels are retrospective chart-pattern matches,
 not records of live alerts. The current candle can change.
 Target and optional expiry come from the dashboard;
 this chart does not fetch a new Kalshi contract.
 Coinbase spot is not Kalshi settlement data.
 *VWAP uses the loaded portion of the UTC day.
 <a href="https://www.tradingview.com/"
    target="_blank"
    rel="noopener noreferrer">TradingView Lightweight Charts™</a>
 · Copyright © TradingView, Inc.
</div>

<script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>

<script>
const CONFIG=__CONFIG__;
const TARGET=CONFIG.target;

const $=id=>document.getElementById(id);

const fmt=x=>Number.isFinite(x)
 ?x.toLocaleString('en-US',{
   style:'currency',
   currency:'USD',
   minimumFractionDigits:2,
   maximumFractionDigits:2
  })
 :'—';

const green='#75f2be';
const red='#ff91a9';
const amber='#ffcb83';

$('target').textContent=
 TARGET===null?'Unavailable':fmt(TARGET);

const flags={
 volume:false,
 rsi:false,
 macd:false,
 ema50:false,
 vwap:false,
 bb:false,
 levels:false,
 markers:true
};

let advancedMode=false;
let bars=[];
let ticks=[];
let charts=[];
let series={};
let socket=null;

let lastTick=0;
let lastEvent=0;
let lastMessage=0;
let lastTradeId=null;
let retry=1000;
let feed='WS';

let calculationTimer=null;
let reconnectTimer=null;
let loading=false;
let dead=false;

let needsReset=true;
let chartReady=false;
let userAdjustedView=false;
let restoringView=false;

let historyError='';
let cached=null;
let markers=[];
let renderedTime=0;

const viewKey='queens-edge-254-btc-view-v3';
const settingsKey='queens-edge-254-chart-settings-v1';

let savedView=null;

try{
 savedView=JSON.parse(
  sessionStorage.getItem(viewKey)||'null'
 );

 const prefs=JSON.parse(
  sessionStorage.getItem(settingsKey)||'null'
 );

 if(prefs){
  advancedMode=prefs.advanced===true;

  for(const k of Object.keys(flags)){
   if(typeof prefs.flags?.[k]==='boolean'){
    flags[k]=prefs.flags[k];
   }
  }
 }
}catch(e){}

if(
 savedView&&
 Number.isFinite(savedView.from)&&
 Number.isFinite(savedView.to)&&
 savedView.to>savedView.from
){
 userAdjustedView=true;
}else{
 savedView=null;
}

function saveSettings(){
 try{
  sessionStorage.setItem(
   settingsKey,
   JSON.stringify({
    advanced:advancedMode,
    flags
   })
  );
 }catch(e){}
}

function resizeFrame(){
 try{
  window.parent.postMessage({
   isStreamlitMessage:true,
   type:'streamlit:setFrameHeight',
   height:Math.ceil(
    document.documentElement.scrollHeight+4
   )
  },'*');
 }catch(e){}
}

function requestResize(){
 requestAnimationFrame(
  ()=>requestAnimationFrame(resizeFrame)
 );
}

function chartFor(id){
 const e=$(id);

 const chart=LightweightCharts.createChart(e,{
  width:Math.max(1,e.clientWidth),
  height:Math.max(1,e.clientHeight),

  layout:{
   background:{color:'#0b091b'},
   textColor:'#cbb8e6'
  },

  grid:{
   vertLines:{color:'#281e3c'},
   horzLines:{color:'#281e3c'}
  },

  rightPriceScale:{
   borderColor:'#513478'
  },

  timeScale:{
   timeVisible:true,
   secondsVisible:false,
   borderColor:'#513478',
   rightOffset:4
  },

  crosshair:{
   mode:LightweightCharts.CrosshairMode.Normal
  }
 });

 new ResizeObserver(()=>{
  if(e.clientWidth>0&&e.clientHeight>0){
   chart.applyOptions({
    width:e.clientWidth,
    height:e.clientHeight
   });
  }
 }).observe(e);

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

 out[n-1]=values.slice(0,n)
  .reduce((a,b)=>a+b,0)/n;

 const k=2/(n+1);

 for(let i=n;i<values.length;i++){
  out[i]=out[i-1]+k*(values[i]-out[i-1]);
 }

 return out;
}

function rsi(values,n=14){
 const out=Array(values.length).fill(null);

 if(values.length<=n)return out;

 let gain=0;
 let loss=0;

 for(let i=1;i<=n;i++){
  const d=values[i]-values[i-1];

  gain+=Math.max(0,d)/n;
  loss+=Math.max(0,-d)/n;
 }

 const value=()=>loss===0
  ?(gain===0?50:100)
  :100-100/(1+gain/loss);

 out[n]=value();

 for(let i=n+1;i<values.length;i++){
  const d=values[i]-values[i-1];

  gain=(gain*(n-1)+Math.max(0,d))/n;
  loss=(loss*(n-1)+Math.max(0,-d))/n;

  out[i]=value();
 }

 return out;
}

function macd(values){
 const fast=ema(values,12);
 const slow=ema(values,26);

 const m=values.map((_,i)=>
  fast[i]===null||slow[i]===null
   ?null
   :fast[i]-slow[i]
 );

 const signal=Array(values.length).fill(null);
 const hist=Array(values.length).fill(null);

 const s=ema(
  m.filter(Number.isFinite),
  9
 );

 for(let i=0;i<s.length;i++){
  if(s[i]!==null){
   signal[i+25]=s[i];
   hist[i+25]=m[i+25]-s[i];
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

 let day='';
 let pv=0;
 let vol=0;

 for(const b of bars){
  const d=new Date(b.time*1000)
   .toISOString().slice(0,10);

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
 return {
  low:bars.map((_,i)=>
   i<n
    ?null
    :Math.min(
      ...bars.slice(i-n,i).map(x=>x.low)
     )
  ),

  high:bars.map((_,i)=>
   i<n
    ?null
    :Math.max(
      ...bars.slice(i-n,i).map(x=>x.high)
     )
  )
 };
}

function fresh(){
 const age=Date.now()-lastTick;

 return lastTick>0&&
  age>=-3000&&
  age<=10000;
}

function expired(){
 return CONFIG.closeMs!==null&&
  Date.now()>=CONFIG.closeMs;
}

function recentGap(){
 return bars.slice(-60).some(
  (b,i,a)=>i>0&&b.time-a[i-1].time>60
 );
}

function setStatus(text,color=amber){
 $('status').textContent=text;
 $('status').style.color=color;
}

function updatePrice(price){
 $('price').textContent=fmt(price);

 if(TARGET!==null){
  const d=price-TARGET;

  $('distance').textContent=
   (d>=0?'+':'−')+fmt(Math.abs(d));

  $('distance').style.color=
   d>=0?green:red;
 }
}

function syncPanels(){
 $('clean').classList.toggle(
  'active',
  !advancedMode
 );

 $('advanced').classList.toggle(
  'active',
  advancedMode
 );

 $('extras').style.display=
  advancedMode?'flex':'none';

 $('modeDescription').textContent=
  advancedMode
   ?'Choose your extra indicators'
   :'EMA 9/21 · target · reversal arrows';

 const panels={
  volume:'volumePanel',
  rsi:'rsiPanel',
  macd:'macdPanel'
 };

 for(const [key,id] of Object.entries(panels)){
  $(id).style.display=
   advancedMode&&flags[key]?'block':'none';
 }

 const groups={
  ema50:['ema50'],
  vwap:['vwap'],
  bb:['bbHigh','bbLow'],
  levels:['support','resistance']
 };

 for(const [key,names] of Object.entries(groups)){
  for(const name of names){
   series[name]?.applyOptions({
    visible:advancedMode&&flags[key]
   });
  }
 }

 series.candles?.setMarkers(
  flags.markers?markers:[]
 );

 document.querySelectorAll(
  'input[data-key]'
 ).forEach(input=>{
  input.checked=flags[input.dataset.key];
 });

 saveSettings();
 requestResize();
}

function detectReversals(close,e9,e21,rv,mc){
 const out=[];

 if(expired())return out;

 let lastUp=-1000;
 let lastDown=-1000;

 for(let i=52;i<bars.length;i++){
  const values=[
   close[i],
   close[i-1],
   e9[i],
   e9[i-1],
   e9[i-2],
   rv[i],
   rv[i-1],
   mc.hist[i],
   mc.hist[i-1]
  ];

  if(!values.every(Number.isFinite))continue;

  if(bars[i].time-bars[i-2].time!==120)continue;

  const slope=e9[i]-e9[i-1];
  const previousSlope=e9[i-1]-e9[i-2];

  const up=
   previousSlope<=0&&
   slope>0&&
   close[i]>close[i-1]&&
   rv[i]>rv[i-1]&&
   mc.hist[i]>mc.hist[i-1];

  const down=
   previousSlope>=0&&
   slope<0&&
   close[i]<close[i-1]&&
   rv[i]<rv[i-1]&&
   mc.hist[i]<mc.hist[i-1];

  if(up&&i-lastUp>=5){
   out.push({
    time:bars[i].time,
    position:'belowBar',
    color:'#25d9a4',
    shape:'arrowUp',
    text:'WATCH UP',
    direction:'UP'
   });

   lastUp=i;
  }

  if(down&&i-lastDown>=5){
   out.push({
    time:bars[i].time,
    position:'aboveBar',
    color:'#ff547c',
    shape:'arrowDown',
    text:'WATCH DOWN',
    direction:'DOWN'
   });

   lastDown=i;
  }
 }

 return out;
}

function updateNotice(){
 const notice=$('notice');

 let text='';
 let color=amber;

 if(!fresh()){
  text=
   'WAIT · BTC trade timestamp is stale or unavailable. '+
   'New reversal warnings are paused.';
 }else if(expired()){
  text=
   'TARGET EXPIRED · Refresh the dashboard for the new '+
   'Kalshi contract. Reversal warnings paused.';
 }else if(TARGET===null){
  text=
   'WAIT · Kalshi target unavailable. '+
   'No target-based warning can be evaluated.';
 }else if(bars.length<60||!cached){
  text=
   'WAIT · Need 60 real, recent candles before '+
   'evaluating reversal warnings.';
 }else if(recentGap()){
  text=
   'WAIT · Recent candle history has a gap. '+
   'Indicators may be incomplete; warnings paused.';
 }else if(
  Date.now()/1000-bars.at(-1).time>120
 ){
  text=
   'WAIT · Latest candle is stale. Warnings paused.';
 }else{
  const current=ticks.at(-1);

  const old=current
   ?ticks.filter(
     t=>t.time<=current.time-20
    ).at(-1)
   :null;

  const distance=bars.at(-1).close-TARGET;

  const loss=old&&current
   ?Math.abs(old.price-TARGET)-
    Math.abs(current.price-TARGET)
   :0;

  const shrinking=old&&
   loss>Math.max(
    8,
    Math.abs(old.price-TARGET)*.20
   );

  const i=bars.length-1;

  const cross=i>0&&
   (cached.e9[i]-cached.e21[i])*
   (cached.e9[i-1]-cached.e21[i-1])<0;

  const latest=markers.at(-1);

  const recent=latest&&
   latest.time>=bars.at(-1).time-120;

  if(shrinking){
   text=
    '⚠ CUSHION SHRINKING · Spot moved '+
    fmt(loss)+
    ' toward the target in about 20 seconds. '+
    'Review reversal risk.';
  }else if(cross){
   text=
    '⚠ EMA FLIP · EMA 9 crossed EMA 21 on the '+
    'developing candle. Confirmation can change '+
    'before close.';
  }else if(recent){
   const up=latest.direction==='UP';

   text=
    (up?'▲ WATCH UP':'▼ WATCH DOWN')+
    ' · Possible momentum turn on the 1-minute chart. '+
    'Experimental warning, not a confirmed entry.';

   color=up?green:red;
  }else if(Math.abs(distance)<25){
   text=
    '⚠ TARGET DANGER ZONE · Spot is within $25 of '+
    'the target. Small moves can change the apparent side.';
  }else{
   text=
    'WAIT · No active reversal warning. This does not '+
    'confirm that the current side will settle.';
  }
 }

 notice.textContent=text;
 notice.style.color=color;

 const current=ticks.at(-1);

 const old=current
  ?ticks.filter(
    t=>t.time<=current.time-20
   ).at(-1)
  :null;

 if(old&&current){
  const d=current.price-old.price;

  $('momentum').textContent=
   (d>=0?'+':'−')+fmt(Math.abs(d));

  $('momentum').style.color=
   d>=0?green:red;
 }else{
  $('momentum').textContent='Collecting…';
 }
}

function put(name,data,full){
 if(full){
  series[name].setData(data);
 }else if(data.length){
  series[name].update(data.at(-1));
 }
}

function restoreRange(){
 if(!savedView||!chartReady)return;

 restoringView=true;

 try{
  charts[0].timeScale().setVisibleRange(savedView);
 }catch(e){}

 restoringView=false;
}

function followLive(){
 userAdjustedView=false;
 savedView=null;

 try{
  sessionStorage.removeItem(viewKey);
 }catch(e){}

 if(!bars.length)return;

 restoringView=true;

 charts[0].applyOptions({
  timeScale:{
   shiftVisibleRangeOnNewBar:true
  }
 });

 charts[0].timeScale().setVisibleLogicalRange({
  from:Math.max(0,bars.length-60),
  to:bars.length+4
 });

 restoringView=false;
}

function calculate(){
 if(!bars.length||!series.candles)return;

 const close=bars.map(b=>b.close);

 const e9=ema(close,9);
 const e21=ema(close,21);
 const rv=rsi(close);
 const mc=macd(close);
 const bb=bollinger(close);
 const lev=levels();

 cached={e9,e21,rv,mc};

 const full=needsReset||!chartReady;

 const values={
  ema9:e9,
  ema21:e21,
  ema50:ema(close,50),
  vwap:vwap(),
  bbHigh:bb.high,
  bbLow:bb.low,
  support:lev.low,
  resistance:lev.high,
  rsi:rv,
  macd:mc.m,
  signal:mc.signal
 };

 restoringView=true;

 for(const [name,value] of Object.entries(values)){
  put(name,points(value),full);
 }

 put(
  'candles',
  bars.map(b=>({
   time:b.time,
   open:b.open,
   high:b.high,
   low:b.low,
   close:b.close
  })),
  full
 );

 put(
  'volume',
  bars.map(b=>({
   time:b.time,
   value:b.volume||0,
   color:b.close>=b.open
    ?'#2bb88d'
    :'#cc5079'
  })),
  full
 );

 put(
  'hist',
  bars.map((b,i)=>
   Number.isFinite(mc.hist[i])
    ?{
      time:b.time,
      value:mc.hist[i],
      color:mc.hist[i]>=0
       ?'#2bb88d'
       :'#cc5079'
     }
    :{time:b.time}
  ),
  full
 );

 restoringView=false;

 const wasReady=chartReady;

 chartReady=true;
 needsReset=false;
 renderedTime=bars.at(-1).time;

 if(full){
  if(userAdjustedView&&savedView){
   restoreRange();
  }else if(!wasReady){
   followLive();
  }
 }

 const i=bars.length-1;

 if(
  Number.isFinite(e9[i])&&
  Number.isFinite(e21[i])
 ){
  const d=e9[i]-e21[i];

  $('trend').textContent=
   d>0?'UPWARD':d<0?'DOWNWARD':'NEUTRAL';

  $('trend').style.color=
   d>0?green:d<0?red:amber;
 }

 $('rsiValue').textContent=
  Number.isFinite(rv[i])
   ?rv[i].toFixed(1)
   :'—';

 markers=
  fresh()&&!recentGap()&&bars.length>=60
   ?detectReversals(close,e9,e21,rv,mc)
   :[];

 series.candles.setMarkers(
  flags.markers?markers:[]
 );

 updateNotice();
}

function schedule(){
 if(calculationTimer||dead)return;

 calculationTimer=setTimeout(()=>{
  calculationTimer=null;
  calculate();
 },180);
}

function onTrade(
 price,
 time,
 quantity=0,
 tradeId=null,
 source='WS'
){
 if(
  !Number.isFinite(price)||
  price<=0||
  !Number.isFinite(time)||
  !Number.isFinite(quantity)||
  quantity<0||
  time*1000>Date.now()+3000||
  time<lastEvent
 )return;

 if(
  tradeId!==null&&
  lastTradeId!==null&&
  tradeId<=lastTradeId
 )return;

 if(tradeId!==null){
  lastTradeId=tradeId;
 }

 const minute=Math.floor(time/60)*60;
 const old=bars.at(-1);

 if(old&&minute<old.time)return;

 if(old&&minute===old.time){
  old.high=Math.max(old.high,price);
  old.low=Math.min(old.low,price);
  old.close=price;
  old.volume+=quantity;
 }else{
  bars.push({
   time:minute,
   open:price,
   high:price,
   low:price,
   close:price,
   volume:quantity
  });

  if(bars.length>360){
   bars=bars.slice(-360);
   needsReset=true;
  }

  if(old&&minute-old.time>60){
   needsReset=true;
   load();
  }
 }

 lastEvent=time;
 lastTick=time*1000;
 feed=source;

 const prev=ticks.at(-1);

 if(
  !prev||
  prev.time!==time||
  prev.price!==price
 ){
  ticks.push({time,price});
 }

 ticks=ticks.filter(
  t=>time-t.time<=180
 ).slice(-4000);

 updatePrice(price);

 if(fresh()){
  setStatus(
   source==='WS'?'LIVE · WS':'LIVE · REST',
   green
  );
 }else{
  setStatus('STALE TRADE');
 }

 schedule();
}

async function fetchJSON(url){
 const controller=new AbortController();

 const timer=setTimeout(
  ()=>controller.abort(),
  8000
 );

 try{
  const r=await fetch(url,{
   cache:'no-store',
   signal:controller.signal
  });

  if(!r.ok){
   throw Error('HTTP '+r.status);
  }

  return await r.json();
 }finally{
  clearTimeout(timer);
 }
}

async function load(){
 if(loading||dead)return;

 loading=true;

 const started=Date.now()/1000;

 try{
  const data=await fetchJSON(
   'https://api.exchange.coinbase.com/'+
   'products/BTC-USD/candles?granularity=60'
  );

  if(!Array.isArray(data)){
   throw Error('Invalid candles');
  }

  const map=new Map();

  for(const x of data){
   if(!Array.isArray(x)||x.length<6)continue;

   const b={
    time:Number(x[0]),
    low:Number(x[1]),
    high:Number(x[2]),
    open:Number(x[3]),
    close:Number(x[4]),
    volume:Number(x[5])
   };

   if(
    Object.values(b).every(Number.isFinite)&&
    b.time>0&&
    b.time<=Date.now()/1000&&
    b.low>0&&
    b.high>=Math.max(b.open,b.close,b.low)&&
    b.low<=Math.min(b.open,b.close)&&
    b.volume>=0
   ){
    map.set(b.time,b);
   }
  }

  for(const b of bars){
   const h=map.get(b.time);

   if(
    b.time>=Math.floor(started/60)*60||
    !h
   ){
    map.set(
     b.time,
     h
      ?{
        ...h,
        high:Math.max(h.high,b.high),
        low:Math.min(h.low,b.low),
        close:b.close,
        volume:Math.max(h.volume,b.volume)
       }
      :b
    );
   }
  }

  bars=[...map.values()]
   .sort((a,b)=>a.time-b.time)
   .slice(-360);

  if(!bars.length){
   throw Error('Empty history');
  }

  needsReset=true;
  historyError='';

  if(!lastTick){
   updatePrice(bars.at(-1).close);
  }

  calculate();
 }catch(e){
  historyError=
   'Historical candles unavailable; waiting for '+
   'enough real live candles.';

  updateNotice();
 }finally{
  loading=false;
 }
}

function reconnect(){
 if(reconnectTimer||dead)return;

 reconnectTimer=setTimeout(()=>{
  reconnectTimer=null;
  connect();
 },retry);

 retry=Math.min(30000,retry*2);
}

function connect(){
 if(
  dead||
  socket&&(
   socket.readyState===0||
   socket.readyState===1
  )
 )return;

 try{
  const ws=new WebSocket(
   'wss://ws-feed.exchange.coinbase.com'
  );

  socket=ws;

  ws.onopen=()=>{
   if(dead)return;

   lastMessage=Date.now();

   ws.send(JSON.stringify({
    type:'subscribe',
    product_ids:['BTC-USD'],
    channels:['matches','heartbeat']
   }));

   setStatus('CONNECTED · waiting');

   if(chartReady&&recentGap()){
    load();
   }
  };

  ws.onmessage=event=>{
   if(dead||socket!==ws)return;

   lastMessage=Date.now();

   let d;

   try{
    d=JSON.parse(event.data);
   }catch(e){
    return;
   }

   if(d.type==='error'){
    setStatus('FEED ERROR');
    ws.close();
    return;
   }

   if(
    d.type==='match'||
    d.type==='last_match'
   ){
    const t=Date.parse(d.time)/1000;

    onTrade(
     Number(d.price),
     t,
     d.type==='last_match'
      ?0
      :(Number(d.size)||0),
     Number.isFinite(Number(d.trade_id))
      ?Number(d.trade_id)
      :null
    );

    if(fresh()){
     retry=1000;
    }
   }
  };

  ws.onerror=()=>{
   setStatus('RECONNECTING');
   ws.close();
  };

  ws.onclose=()=>{
   if(dead||socket!==ws)return;

   socket=null;
   setStatus('RECONNECTING');
   reconnect();
  };
 }catch(e){
  setStatus('FEED UNAVAILABLE');
  reconnect();
 }
}

let polling=false;

async function pollFallback(){
 if(dead||polling||fresh())return;

 polling=true;

 try{
  const d=await fetchJSON(
   'https://api.exchange.coinbase.com/'+
   'products/BTC-USD/ticker'
  );

  onTrade(
   Number(d.price),
   Date.parse(d.time)/1000,
   0,
   Number.isFinite(Number(d.trade_id))
    ?Number(d.trade_id)
    :null,
   'REST'
  );
 }catch(e){
 }finally{
  polling=false;
 }
}

function health(){
 if(dead)return;

 const age=lastTick
  ?(Date.now()-lastTick)/1000
  :null;

 if(fresh()){
  setStatus(
   'LIVE · '+
   feed+
   ' · '+
   Math.max(0,age).toFixed(0)+
   's',
   green
  );
 }else{
  setStatus(
   lastTick
    ?'STALE · '+Math.max(0,age).toFixed(0)+'s'
    :'WAITING FOR TRADE'
  );

  series.candles?.setMarkers([]);
 }

 if(CONFIG.closeMs!==null){
  const sec=Math.max(
   0,
   Math.floor(
    (CONFIG.closeMs-Date.now())/1000
   )
  );

  $('contract').textContent=
   (CONFIG.ticker?CONFIG.ticker+' · ':'')+
   (
    expired()
     ?'TARGET EXPIRED'
     :String(Math.floor(sec/60)).padStart(2,'0')+
      ':'+
      String(sec%60).padStart(2,'0')+
      ' remaining'
   );
 }else{
  $('contract').textContent=
   CONFIG.ticker||
   'Target snapshot · confirm current contract';
 }

 if(expired()){
  series.candles?.setMarkers([]);
 }

 updateNotice();

 if(
  socket&&
  lastMessage&&
  Date.now()-lastMessage>20000
 ){
  socket.close();
 }
}

let healthTimer=null;
let pollTimer=null;

function start(){
 if(!window.LightweightCharts){
  setStatus('CHART LIBRARY UNAVAILABLE');

  $('notice').textContent=
   'Chart library could not load. '+
   'Retry the page or check your connection.';

  return;
 }

 const main=chartFor('main');

 main.timeScale().subscribeVisibleTimeRangeChange(
  range=>{
   if(
    !chartReady||
    !userAdjustedView||
    restoringView||
    !range||
    !Number.isFinite(range.from)||
    !Number.isFinite(range.to)
   )return;

   savedView={
    from:range.from,
    to:range.to
   };

   try{
    sessionStorage.setItem(
     viewKey,
     JSON.stringify(savedView)
    );
   }catch(e){}
  }
 );

 const manualView=()=>{
  userAdjustedView=true;

  main.applyOptions({
   timeScale:{
    shiftVisibleRangeOnNewBar:false
   }
  });
 };

 for(
  const event of [
   'pointerdown',
   'wheel',
   'touchstart'
  ]
 ){
  $('main').addEventListener(
   event,
   manualView,
   {passive:true}
  );
 }

 if(userAdjustedView){
  main.applyOptions({
   timeScale:{
    shiftVisibleRangeOnNewBar:false
   }
  });
 }

 series.candles=main.addCandlestickSeries({
  upColor:'#25d9a4',
  downColor:'#ff547c',
  wickUpColor:'#25d9a4',
  wickDownColor:'#ff547c',
  borderVisible:false
 });

 const overlays=[
  ['ema9','#c68bff',2],
  ['ema21','#58a7ff',2],
  ['ema50','#ffac61',2],
  ['vwap','#57e5aa',2],
  ['bbHigh','#b9b6c7',1],
  ['bbLow','#b9b6c7',1],
  ['support','#44bca9',1],
  ['resistance','#f47e99',1]
 ];

 for(const [name,color,width] of overlays){
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

 const vc=chartFor('volume');

 series.volume=vc.addHistogramSeries({
  priceFormat:{type:'volume'},
  priceScaleId:''
 });

 const rc=chartFor('rsi');

 series.rsi=line(
  rc,
  '#bd89ff',
  2
 );

 for(const value of [30,50,70]){
  series.rsi.createPriceLine({
   price:value,
   color:'#8e628c',
   lineStyle:2,
   axisLabelVisible:true,
   title:String(value)
  });
 }

 const mc=chartFor('macd');

 series.hist=mc.addHistogramSeries({
  priceLineVisible:false,
  lastValueVisible:false
 });

 series.macd=line(mc,'#6bb4ff',2);
 series.signal=line(mc,'#ffac69',2);

 main.timeScale().subscribeVisibleLogicalRangeChange(
  range=>{
   if(range){
    for(const c of charts.slice(1)){
     c.timeScale().setVisibleLogicalRange(range);
    }
   }
  }
 );

 $('resetView').onclick=followLive;

 $('clean').onclick=()=>{
  advancedMode=false;
  syncPanels();
 };

 $('advanced').onclick=()=>{
  advancedMode=true;
  syncPanels();
 };

 document.querySelectorAll(
  'input[data-key]'
 ).forEach(input=>{
  input.addEventListener('change',()=>{
   flags[input.dataset.key]=input.checked;
   syncPanels();
  });
 });

 syncPanels();

 load().finally(connect);

 healthTimer=setInterval(health,1000);
 pollTimer=setInterval(pollFallback,4000);

 health();
}

window.addEventListener('pagehide',()=>{
 dead=true;

 if(reconnectTimer){
  clearTimeout(reconnectTimer);
 }

 if(calculationTimer){
  clearTimeout(calculationTimer);
 }

 clearInterval(healthTimer);
 clearInterval(pollTimer);

 socket?.close();
});

window.addEventListener('load',requestResize);
window.addEventListener('resize',requestResize);

if(window.ResizeObserver){
 new ResizeObserver(requestResize)
  .observe(document.body);
}

start();
requestResize();

</script>
</body>
</html>'''

    # Keep the live WATCH status above the main chart.
    notice_start = html.find('<div id="notice"')

    if notice_start >= 0:
        notice_end = html.find("</div>", notice_start)

        if notice_end >= 0:
            notice_end += len("</div>")

            notice_html = html[notice_start:notice_end]

            html = (
                html[:notice_start]
                + html[notice_end:]
            )

            html = html.replace(
                '<div class="panel">',
                notice_html + '\n<div class="panel">',
                1,
            )

    html = html.replace(
        "</head>",
        '<style>#notice{margin:8px 0}'
        '#notice::before{content:"REVERSAL WATCH STATUS";'
        'display:block;font-size:10px;letter-spacing:1px;'
        'color:#cbb5e8;margin-bottom:4px}</style></head>',
        1,
    )

    try:
        frame_height = max(
            680,
            min(2000, int(height)),
        )
    except (TypeError, ValueError, OverflowError):
        frame_height = 680

    components.html(
        html.replace("__CONFIG__", config),
        height=frame_height,
        scrolling=True,
    )
