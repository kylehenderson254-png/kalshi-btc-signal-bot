
import json
import math
import streamlit.components.v1 as components


def render_live_chart(target=None, height=820):
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
#toggles{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0;align-items:center}
label{cursor:pointer;background:#24183d;border:1px solid #543779;border-radius:8px;padding:6px 8px;white-space:nowrap}
input{accent-color:#b77dff}
.panel{border:1px solid #513478;border-radius:10px;overflow:hidden;margin-bottom:7px;position:relative}
.panelTitle{position:absolute;top:7px;left:9px;z-index:2;background:#110d22ce;border-radius:4px;padding:2px 6px;color:#d9c5ff;pointer-events:none}
#main{height:360px}
#volume{height:95px}
#rsi{height:110px}
#macd{height:110px}
#legend{display:flex;flex-wrap:wrap;gap:12px;color:#cbb5e8;margin:7px 2px}
#notice{padding:9px 10px;border:1px solid #694b84;border-radius:8px;color:#ffcb83;background:#1d1430;line-height:1.5}
#help{color:#bda9d5;line-height:1.5;padding:8px 2px}
</style>
</head>
<body>

<div id="summary">
<div class="tile"><div class="muted">BTC/USD · Coinbase spot</div><div class="value" id="price">Connecting…</div></div>
<div class="tile"><div class="muted">Kalshi target · snapshot</div><div class="value" id="target">—</div></div>
<div class="tile"><div class="muted">Distance to target</div><div class="value" id="distance">—</div></div>
<div class="tile"><div class="muted">EMA 9 / 21 / 50</div><div class="value" id="trend">Collecting…</div></div>
<div class="tile"><div class="muted">Market feed</div><div class="value" id="status">Connecting…</div></div>
</div>

<div id="toggles">
<label><input type="checkbox" data-key="ema" checked> EMA 9/21/50</label>
<label><input type="checkbox" data-key="vwap" checked> VWAP</label>
<label><input type="checkbox" data-key="bb" checked> Bollinger</label>
<label><input type="checkbox" data-key="levels" checked> Support / resistance</label>
<label><input type="checkbox" data-key="volume" checked> Volume</label>
<label><input type="checkbox" data-key="rsi" checked> RSI</label>
<label><input type="checkbox" data-key="macd" checked> MACD</label>
<label><input type="checkbox" data-key="markers" checked> Reversal watch</label>
</div>

<div class="panel">
<div class="panelTitle">BTC · 1-minute candles / Kalshi target</div>
<div id="main"></div>
</div>

<div class="panel" id="volumePanel">
<div class="panelTitle">Coinbase traded BTC volume (1m)</div>
<div id="volume"></div>
</div>

<div class="panel" id="rsiPanel">
<div class="panelTitle">RSI (14) · 70 / 30 reference</div>
<div id="rsi"></div>
</div>

<div class="panel" id="macdPanel">
<div class="panelTitle">MACD (12, 26, 9)</div>
<div id="macd"></div>
</div>

<div id="legend">
<span>🟣 EMA 9</span>
<span>🔵 EMA 21</span>
<span>🟠 EMA 50</span>
<span>🟢 VWAP</span>
<span>⚪ Bollinger bands</span>
<span>🟡 Kalshi target</span>
<span>🔺 Reversal watch</span>
</div>

<div id="notice">WAIT · observation only. Indicators are not verified trade signals.</div>
<div id="help">
EMA = trend direction; VWAP = volume-weighted average since UTC midnight;
Bollinger = volatility envelope; RSI = momentum strength;
MACD = momentum changes. Support/resistance are recent rolling highs/lows,
not guaranteed barriers. Coinbase spot and volume are not Kalshi settlement data.
</div>

<script>
const TARGET=__TARGET__;
const $=id=>document.getElementById(id);
const fmt=x=>Number.isFinite(x)?x.toLocaleString('en-US',{
 style:'currency',currency:'USD',minimumFractionDigits:2
}):'—';

$('target').textContent=TARGET===null?'Unavailable':fmt(TARGET);

const flags={
 ema:true,vwap:true,bb:true,levels:true,
 volume:true,rsi:true,macd:true,markers:true
};

let bars=[],lastTick=0,socket=null,retry=1000;
let chartSet=[],series={},historyReady=false;
let lastPrice=null,lastPriceTime=0;

const bg='#0b091b',fg='#cbb8e6',grid='#281e3c';

function makeChart(id,scale={}){
 const element=$(id);
 const chart=LightweightCharts.createChart(element,{
  width:element.clientWidth,
  height:element.clientHeight,
  layout:{background:{color:bg},textColor:fg},
  grid:{
   vertLines:{color:grid},
   horzLines:{color:grid}
  },
  rightPriceScale:{
   borderColor:'#513478',
   ...scale
  },
  timeScale:{
   timeVisible:true,
   secondsVisible:false,
   borderColor:'#513478'
  },
  crosshair:{
   mode:LightweightCharts.CrosshairMode.Normal
  }
 });

 new ResizeObserver(()=>{
  chart.applyOptions({
   width:element.clientWidth,
   height:element.clientHeight
  });
 }).observe(element);

 chartSet.push(chart);
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

function setData(s,rows){
 s.setData(rows.filter(x=>Number.isFinite(x.value)));
}

function seriesValues(values){
 return bars.map((b,i)=>({
  time:b.time,
  value:values[i]
 }));
}

function ema(vals,n){
 const out=Array(vals.length).fill(null);
 let seed=0;
 const k=2/(n+1);

 for(let i=0;i<vals.length;i++){
  if(i<n){
   seed+=vals[i];
   if(i===n-1)out[i]=seed/n;
  }else{
   out[i]=out[i-1]+k*(vals[i]-out[i-1]);
  }
 }
 return out;
}

function rsi(vals,n=14){
 let out=Array(vals.length).fill(null);
 if(vals.length<=n)return out;

 let gain=0,loss=0;

 for(let i=1;i<=n;i++){
  let d=vals[i]-vals[i-1];
  gain+=Math.max(d,0);
  loss+=Math.max(-d,0);
 }

 gain/=n;
 loss/=n;

 out[n]=loss===0?(gain===0?50:100):100-100/(1+gain/loss);

 for(let i=n+1;i<vals.length;i++){
  let d=vals[i]-vals[i-1];
  gain=(gain*(n-1)+Math.max(d,0))/n;
  loss=(loss*(n-1)+Math.max(-d,0))/n;

  out[i]=loss===0?(gain===0?50:100):100-100/(1+gain/loss);
 }

 return out;
}

function macd(vals){
 let a=ema(vals,12);
 let b=ema(vals,26);

 let m=vals.map((_,i)=>
  a[i]===null||b[i]===null
   ?null
   :a[i]-b[i]
 );

 let valid=m.filter(v=>v!==null);
 let sig=ema(valid,9);

 let signal=Array(vals.length).fill(null);
 let hist=Array(vals.length).fill(null);

 for(let i=0;i<sig.length;i++){
  if(sig[i]!==null){
   signal[i+25]=sig[i];
   hist[i+25]=m[i+25]-sig[i];
  }
 }

 return {m,signal,hist};
}

function bands(vals,n=20){
 let hi=Array(vals.length).fill(null);
 let lo=hi.slice();

 for(let i=n-1;i<vals.length;i++){
  let a=vals.slice(i-n+1,i+1);
  let mean=a.reduce((x,y)=>x+y,0)/n;
  let sd=Math.sqrt(
   a.reduce((s,v)=>s+(v-mean)**2,0)/n
  );

  hi[i]=mean+2*sd;
  lo[i]=mean-2*sd;
 }

 return {hi,lo};
}

function vwap(){
 let out=[],sumPV=0,sumV=0,day='';

 for(const b of bars){
  let d=new Date(b.time*1000).toISOString().slice(0,10);

  if(d!==day){
   day=d;
   sumPV=0;
   sumV=0;
  }

  let v=Math.max(0,b.volume||0);
  let typ=(b.high+b.low+b.close)/3;

  sumPV+=typ*v;
  sumV+=v;

  out.push(sumV>0?sumPV/sumV:null);
 }

 return out;
}

function levels(n=35){
 let s=[],r=[];

 for(let i=0;i<bars.length;i++){
  if(i<n){
   s.push(null);
   r.push(null);
   continue;
  }

  let slice=bars.slice(i-n,i);

  s.push(Math.min(...slice.map(b=>b.low)));
  r.push(Math.max(...slice.map(b=>b.high)));
 }

 return {s,r};
}

function show(id,on){
 $(id).style.display=on?'block':'none';
}

function applyFlags(){
 for(let k of ['ema','vwap','bb','levels']){
  let keys=k==='ema'
   ?['ema9','ema21','ema50']
   :k==='bb'
   ?['bbHi','bbLo']
   :k==='levels'
   ?['support','resistance']
   :[k];

  for(let name of keys){
   if(series[name]){
    series[name].applyOptions({
     visible:flags[k]
    });
   }
  }
 }

 show('volumePanel',flags.volume);
 show('rsiPanel',flags.rsi);
 show('macdPanel',flags.macd);

 if(series.candles){
  series.candles.setMarkers(
   flags.markers?(series._markers||[]):[]
  );
 }
}

function calculate(){
 if(!bars.length)return;

 const close=bars.map(b=>b.close);

 const e9=ema(close,9);
 const e21=ema(close,21);
 const e50=ema(close,50);
 const bb=bands(close);
 const v=vwap();
 const lev=levels();
 const rv=rsi(close);
 const mc=macd(close);

 const indicators={
  ema9:e9,
  ema21:e21,
  ema50:e50,
  vwap:v,
  bbHi:bb.hi,
  bbLo:bb.lo,
  support:lev.s,
  resistance:lev.r,
  rsi:rv,
  macd:mc.m,
  signal:mc.signal
 };

 for(let [name,vals] of Object.entries(indicators)){
  setData(series[name],seriesValues(vals));
 }

 series.candles.setData(
  bars.map(({time,open,high,low,close})=>({
   time,open,high,low,close
  }))
 );

 series.volume.setData(
  bars.map(b=>({
   time:b.time,
   value:b.volume||0,
   color:b.close>=b.open?'#269a80':'#ad4766'
  }))
 );

 series.hist.setData(
  bars.map((b,i)=>({
   time:b.time,
   value:mc.hist[i]||0,
   color:(mc.hist[i]||0)>=0?'#2ab68d':'#cf547a'
  }))
 );

 const last=bars.length-1;
 const price=close[last];

 $('trend').textContent=e9[last]===null
  ?'Collecting…'
  :(e9[last]>e21[last]?'9↑ ':'9↓ ')
   +(e21[last]!==null&&e50[last]!==null
     ?(e21[last]>e50[last]?'21↑':'21↓')
     :'');

 const marks=[];

 for(let i=52;i<bars.length;i++){
  if(
   TARGET===null||
   rv[i]===null||
   e9[i]===null||
   e21[i]===null||
   mc.hist[i]===null||
   mc.hist[i-1]===null
  )continue;

  let near=Math.abs(close[i]-TARGET)
   <Math.max(40,close[i]*.001);

  let toward=(close[i]-TARGET)
   *(close[i]-close[i-3])<0;

  let bearish=
   close[i]>TARGET&&
   e9[i]<e21[i]&&
   mc.hist[i]<mc.hist[i-1];

  let bullish=
   close[i]<TARGET&&
   e9[i]>e21[i]&&
   mc.hist[i]>mc.hist[i-1];

  if(
   near&&toward&&(bearish||bullish)&&
   (!marks.length||
    i-(marks[marks.length-1]._idx||0)>=8)
  ){
   marks.push({
    time:bars[i].time,
    position:bearish?'aboveBar':'belowBar',
    color:'#ffbd65',
    shape:bearish?'arrowDown':'arrowUp',
    text:'WATCH',
    _idx:i
   });
  }
 }

 series._markers=marks.map(({_idx,...m})=>m);

 series.candles.setMarkers(
  flags.markers?series._markers:[]
 );

 let latest=marks.length&&
  marks[marks.length-1].time>=bars[last].time-180;

 $('notice').textContent=latest
  ?'⚠ REVERSAL WATCH · price near target with opposing EMA/MACD momentum. Observation only.'
  :'WAIT · no validated entry. Indicators are educational, not trade signals.';

 applyFlags();
}

function updatePrice(p){
 lastPrice=p;
 lastPriceTime=Date.now();

 $('price').textContent=fmt(p);

 if(TARGET!==null){
  let d=p-TARGET;

  $('distance').textContent=
   (d>=0?'+':'')+fmt(d);

  $('distance').style.color=
   d>=0?'#74efc3':'#ff91a9';
 }
}

function setStatus(t){
 $('status').textContent=t;
 $('status').style.color=
  t==='LIVE'?'#75f2be':'#ffbc88';
}

let recalcScheduled=false;

function schedule(){
 if(recalcScheduled)return;

 recalcScheduled=true;

 setTimeout(()=>{
  recalcScheduled=false;
  calculate();
 },700);
}

function onTrade(p,t,qty){
 if(
  !Number.isFinite(p)||
  !Number.isFinite(t)||
  !Number.isFinite(qty)||
  qty<0
 )return;

 let minute=Math.floor(t/60)*60;
 let old=bars[bars.length-1];

 if(old&&minute<old.time)return;

 if(old&&minute===old.time){
  old.high=Math.max(old.high,p);
  old.low=Math.min(old.low,p);
  old.close=p;
  old.volume+=qty;
 }else{
  if(old&&minute>old.time+60){
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
   open:old?old.close:p,
   high:p,
   low:p,
   close:p,
   volume:qty
  });

  if(bars.length>360){
   bars=bars.slice(-360);
  }
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

  if(!Array.isArray(data)){
   throw Error('invalid history');
  }

  bars=data
   .filter(x=>
    Array.isArray(x)&&
    x.length>=6&&
    x.every(v=>Number.isFinite(Number(v)))
   )
   .map(x=>({
    time:Number(x[0]),
    low:Number(x[1]),
    high:Number(x[2]),
    open:Number(x[3]),
    close:Number(x[4]),
    volume:Number(x[5])
   }))
   .sort((a,b)=>a.time-b.time);

  bars=bars.filter(
   (b,i)=>i===0||b.time!==bars[i-1].time
  );

  if(bars.length>360){
   bars=bars.slice(-360);
  }

  historyReady=true;
  calculate();

  if(bars.length){
   updatePrice(bars[bars.length-1].close);
   chartSet[0].timeScale().fitContent();
  }
 }catch(e){
  $('notice').textContent=
   'Historical candles or volume unavailable. Indicators may be incomplete. '
   +String(e.message);
 }
}

function connect(){
 try{
  socket=new WebSocket(
   'wss://ws-feed.exchange.coinbase.com'
  );

  socket.onopen=()=>{
   retry=1000;

   socket.send(JSON.stringify({
    type:'subscribe',
    product_ids:['BTC-USD'],
    channels:['matches','heartbeat']
   }));

   setStatus('CONNECTED · awaiting trades');
  };

  socket.onmessage=ev=>{
   let x;

   try{
    x=JSON.parse(ev.data);
   }catch{
    return;
   }

   if(
    x.type==='match'||
    x.type==='last_match'
   ){
    const p=Number(x.price);
    const t=Date.parse(x.time)/1000;
    const q=Number(x.size);

    if(Number.isFinite(p)&&Number.isFinite(t)){
     onTrade(
      p,
      t,
      Number.isFinite(q)?q:0
     );
    }
   }
  };

  socket.onerror=()=>{
   setStatus('RECONNECTING');
  };

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

if(!window.LightweightCharts){
 setStatus('CHART LIBRARY UNAVAILABLE');

 $('notice').textContent=
  'Chart library could not load. Existing Streamlit backup chart is unaffected.';
}else{
 const main=makeChart('main');

 series.candles=main.addCandlestickSeries({
  upColor:'#25d9a4',
  downColor:'#ff547c',
  wickUpColor:'#25d9a4',
  wickDownColor:'#ff547c',
  borderVisible:false
 });

 for(let [name,color,width] of [
  ['ema9','#c68bff',2],
  ['ema21','#58a7ff',2],
  ['ema50','#ffac61',2],
  ['vwap','#57e5aa',2],
  ['bbHi','#b9b6c7',1],
  ['bbLo','#b9b6c7',1],
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

 const vol=makeChart('volume',{
  scaleMargins:{top:.15,bottom:.05}
 });

 series.volume=vol.addHistogramSeries({
  priceFormat:{type:'volume'},
  priceScaleId:''
 });

 const r=makeChart('rsi');

 series.rsi=line(r,'#bd89ff',2);

 series.rsi.createPriceLine({
  price:70,
  color:'#8e628c',
  lineStyle:2,
  axisLabelVisible:true,
  title:'70'
 });

 series.rsi.createPriceLine({
  price:30,
  color:'#8e628c',
  lineStyle:2,
  axisLabelVisible:true,
  title:'30'
 });

 const m=makeChart('macd');

 series.hist=m.addHistogramSeries({
  priceLineVisible:false,
  lastValueVisible:false
 });

 series.macd=line(m,'#6bb4ff',2);
 series.signal=line(m,'#ffac69',2);

 for(const c of chartSet.slice(1)){
  main.timeScale().subscribeVisibleLogicalRangeChange(
   range=>{
    if(range){
     c.timeScale().setVisibleLogicalRange(range);
    }
   }
  );
 }

 document.querySelectorAll(
  'input[data-key]'
 ).forEach(el=>{
  el.addEventListener('change',()=>{
   flags[el.dataset.key]=el.checked;
   applyFlags();
  });
 });

 applyFlags();
 load().then(connect);
}

setInterval(()=>{
 if(lastTick&&Date.now()-lastTick>20000){
  setStatus('STALE · no recent trades');
 }
},5000);
</script>
</body>
</html>'''

    components.html(
        html.replace('__TARGET__', json.dumps(strike)),
        height=height,
        scrolling=False
    )
