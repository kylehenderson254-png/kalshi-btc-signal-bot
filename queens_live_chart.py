
"""QUEEN'S EDGE | 254 — live BTC chart. Paper-only, no trade execution."""
import json
import streamlit.components.v1 as components


def render_live_chart(target=None, height=445):
    target_json = json.dumps(float(target) if target is not None else None)

    page = r'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0b091b;color:#f1e8ff;font:13px system-ui,Segoe UI,sans-serif}
#top{display:flex;flex-wrap:wrap;gap:9px;padding:12px;background:#150e2b;border:1px solid #5f3b99;border-radius:12px}
.pill{padding:7px 12px;background:#201437;border-radius:9px;min-width:145px}
.lab{font-size:11px;color:#bfa8d9}
.val{font-size:18px;font-weight:750;margin-top:3px}
#chart{height:310px;width:100%;margin-top:8px;border:1px solid #513380;border-radius:12px;overflow:hidden}
#bottom{padding:8px 3px;display:flex;flex-wrap:wrap;gap:12px;color:#cbb7e8;font-size:12px}
#warn{color:#ffadba;font-weight:bold}
</style>
</head>
<body>
<div id="top">
<div class="pill"><div class="lab">BTC/USD · Coinbase spot</div><div class="val" id="price">Connecting…</div></div>
<div class="pill"><div class="lab">Kalshi target · snapshot</div><div class="val" id="target">—</div></div>
<div class="pill"><div class="lab">Distance to target</div><div class="val" id="distance">—</div></div>
<div class="pill"><div class="lab">EMA 9 / EMA 21</div><div class="val" id="ema">Collecting…</div></div>
<div class="pill"><div class="lab">Feed status</div><div class="val" id="status">Connecting…</div></div>
</div>
<div id="chart"></div>
<div id="bottom">
<span id="warn">WAIT · no validated entry</span>
<span id="detail">Coinbase spot is indicative, not Kalshi's official settlement reference.</span>
</div>
<script>
const target=__TARGET__;
const el=id=>document.getElementById(id);
let candles=[],lastTrade=0,ws=null,retry=1000,chart=null,series=null;
const money=v=>Number(v).toLocaleString('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2});
el('target').textContent=target==null?'Unavailable':money(target);

function ema(data,n){
 if(data.length<n)return null;
 let v=data[0].close,k=2/(n+1);
 for(let i=1;i<data.length;i++)v=data[i].close*k+v*(1-k);
 return v;
}

function updateMetrics(price){
 el('price').textContent=money(price);
 lastTrade=Date.now();
 if(target!=null){
  let d=price-target;
  el('distance').textContent=(d>=0?'+':'')+money(d);
  el('distance').style.color=d>=0?'#79ffd3':'#ff9cac';
 }
 let a=ema(candles,9),b=ema(candles,21);
 if(a!=null&&b!=null){
  el('ema').textContent=money(a)+' / '+money(b);
  el('ema').style.color=a>=b?'#79ffd3':'#ff9cac';
 }
 let warning='WAIT · no validated entry';
 if(target!=null&&candles.length>=22){
  let prev=candles[Math.max(0,candles.length-6)].close;
  let move=price-prev;
  let distance=Math.abs(price-target);
  let toward=(price-target)*move<0;
  let weak=a!=null&&b!=null&&(
   (price>target&&a<b)||(price<target&&a>b)
  );
  if(toward&&weak&&distance<Math.max(50,price*.0015)){
   warning='⚠ REVERSAL WATCH · approaching target, EMA disagreement';
  }else if(toward&&distance<Math.max(25,price*.0006)){
   warning='⚠ TARGET PRESSURE · approaching strike';
  }
 }
 el('warn').textContent=warning;
}

function setStatus(t){
 el('status').textContent=t;
 el('status').style.color=t==='LIVE'?'#76ffd0':'#ffbc87';
}

function upsert(p,t){
 let minute=Math.floor(t/60)*60;
 let old=candles[candles.length-1];
 if(old&&old.time===minute){
  old.high=Math.max(old.high,p);
  old.low=Math.min(old.low,p);
  old.close=p;
  series.update(old);
 }else if(!old||minute>old.time){
  let bar={time:minute,open:old?old.close:p,high:p,low:p,close:p};
  candles.push(bar);
  if(candles.length>600)candles.shift();
  series.update(bar);
 }
 updateMetrics(p);
}

async function load(){
 try{
  let r=await fetch(
   'https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60',
   {cache:'no-store'}
  );
  if(!r.ok)throw Error('history unavailable');
  let rows=await r.json();
  candles=rows.filter(x=>Array.isArray(x)&&x.length>=5)
   .map(x=>({
    time:x[0],low:x[1],high:x[2],open:x[3],close:x[4]
   }))
   .sort((a,b)=>a.time-b.time);
  series.setData(candles);
  chart.timeScale().fitContent();
  if(candles.length)updateMetrics(candles[candles.length-1].close);
 }catch(e){
  el('detail').textContent='History unavailable; waiting for trade stream.';
 }
}

function connect(){
 try{
  ws=new WebSocket('wss://ws-feed.exchange.coinbase.com');
  ws.onopen=()=>{
   retry=1000;
   ws.send(JSON.stringify({
    type:'subscribe',
    product_ids:['BTC-USD'],
    channels:['matches','heartbeat']
   }));
   setStatus('LIVE');
  };
  ws.onmessage=e=>{
   let x;
   try{x=JSON.parse(e.data)}catch{return}
   if(x.type==='match'||x.type==='last_match'){
    let p=Number(x.price),t=Date.parse(x.time)/1000;
    if(Number.isFinite(p)&&Number.isFinite(t))upsert(p,t);
   }
  };
  ws.onerror=()=>setStatus('RECONNECTING');
  ws.onclose=()=>{
   setStatus('RECONNECTING');
   setTimeout(connect,retry);
   retry=Math.min(30000,retry*2);
  };
 }catch(e){setStatus('UNAVAILABLE');}
}

if(window.LightweightCharts){
 chart=LightweightCharts.createChart(el('chart'),{
  layout:{background:{color:'#0b091b'},textColor:'#cbb8e6'},
  grid:{
   vertLines:{color:'#241b38'},
   horzLines:{color:'#241b38'}
  },
  timeScale:{timeVisible:true,secondsVisible:false},
  rightPriceScale:{borderColor:'#543c78'},
  crosshair:{mode:0},
  width:el('chart').clientWidth,
  height:310
 });
 series=chart.addCandlestickSeries({
  upColor:'#2ee5a1',
  downColor:'#ff557d',
  borderVisible:false,
  wickUpColor:'#2ee5a1',
  wickDownColor:'#ff557d'
 });
 if(target!=null){
  series.createPriceLine({
   price:target,
   color:'#f5d45c',
   lineWidth:2,
   lineStyle:2,
   axisLabelVisible:true,
   title:'KALSHI TARGET (snapshot)'
  });
 }
 new ResizeObserver(()=>{
  chart.applyOptions({width:el('chart').clientWidth});
 }).observe(el('chart'));
 load().then(connect);
}else{
 setStatus('CHART LIBRARY UNAVAILABLE');
 el('detail').textContent='Existing Streamlit chart remains available.';
}
setInterval(()=>{
 if(lastTrade&&Date.now()-lastTrade>20000)setStatus('STALE');
},5000);
</script>
</body>
</html>'''

    components.html(
        page.replace("__TARGET__", target_json),
        height=height,
        scrolling=False,
    )
