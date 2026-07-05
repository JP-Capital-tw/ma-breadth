"""
互動式均線廣度儀表板 (靜態網站, GitHub Actions 每日建置)
- 全上市櫃普通股, 還原價 (etl:adj_*)
- 均線 5/10/20/60/120/240, 勾選 AND 篩選
- 點個股 -> K 線 (預設 60 日, 滾輪縮放 20~240)
輸出: dist/index.html
環境變數: FINLAB_API_TOKEN (必要, CI 從 secret 注入)
          FINLAB_DB_DIR   (選用, 本機指到既有快取加速)
"""
import os
import json
import re
import numpy as np
import pandas as pd

from finlab import data

db_dir = os.environ.get('FINLAB_DB_DIR')
if not db_dir and os.path.isdir('/Users/lz/finlab_db'):
    db_dir = '/Users/lz/finlab_db'   # 本機開發用既有快取
if db_dir:
    data.set_storage(data.FileStorage(db_dir))

# ---------- 1. 資料 ----------
adj_c = data.get('etl:adj_close')
adj_o = data.get('etl:adj_open')
adj_h = data.get('etl:adj_high')
adj_l = data.get('etl:adj_low')
vol = data.get('price:成交股數')
info = data.get('company_basic_info')

common = info[info['市場別'].isin(['sii', 'otc']) & info['stock_id'].str.match(r'^\d{4}$')]
name_map = dict(zip(common['stock_id'], common['公司簡稱']))
board_map = dict(zip(common['stock_id'], common['市場別']))  # sii / otc
univ = [c for c in adj_c.columns if c in name_map]

adj_c = adj_c[univ].astype(float)
adj_o = adj_o[univ].astype(float)
adj_h = adj_h[univ].astype(float)
adj_l = adj_l[univ].astype(float)
vol = vol.reindex(index=adj_c.index, columns=univ).astype(float)

MAS = [5, 10, 20, 60, 120, 240]
ma = {n: adj_c.rolling(n).mean() for n in MAS}

N_DAYS = 5
dates = list(adj_c.index[-N_DAYS:])
latest = dates[-1]

# ---------- 2. 條件 (向量化, 全期間) ----------
gt = {n: adj_c > ma[n] for n in MAS}                    # 站上
lt = {n: adj_c < ma[n] for n in MAS}                    # 跌破
def prev_bool(df):
    return df.shift(1).fillna(False).astype(bool)

new_gt = {n: gt[n] & ~prev_bool(gt[n]) for n in MAS}    # 新站上
new_lt = {n: lt[n] & ~prev_bool(lt[n]) for n in MAS}    # 新跌破
ma_up = {n: ma[n] > ma[n].shift(1) for n in MAS}        # 上揚
ma_dn = {n: ma[n] < ma[n].shift(1) for n in MAS}        # 下彎

def align_bull(ns):
    s = pd.DataFrame(True, index=adj_c.index, columns=adj_c.columns)
    for a, b in zip(ns[:-1], ns[1:]):
        s &= ma[a] > ma[b]
    return s

def align_bear(ns):
    s = pd.DataFrame(True, index=adj_c.index, columns=adj_c.columns)
    for a, b in zip(ns[:-1], ns[1:]):
        s &= ma[a] < ma[b]
    return s

bull3, bull4, bull5 = align_bull([5,10,20]), align_bull([5,10,20,60]), align_bull([5,10,20,60,120])
bear3, bear4, bear5 = align_bear([5,10,20]), align_bear([5,10,20,60]), align_bear([5,10,20,60,120])

# flag 順序 (與 JS 同步): 每均線 6 種 x 6 條 + 排列 6 種 = 42
FLAGS = []
for n in MAS: FLAGS.append((f"gt{n}",  gt[n]))
for n in MAS: FLAGS.append((f"lt{n}",  lt[n]))
for n in MAS: FLAGS.append((f"ngt{n}", new_gt[n]))
for n in MAS: FLAGS.append((f"nlt{n}", new_lt[n]))
for n in MAS: FLAGS.append((f"up{n}",  ma_up[n]))
for n in MAS: FLAGS.append((f"dn{n}",  ma_dn[n]))
FLAGS += [("bull3", bull3), ("bull4", bull4), ("bull5", bull5),
          ("bear3", bear3), ("bear4", bear4), ("bear5", bear5)]
FLAG_KEYS = [k for k, _ in FLAGS]

# ---------- 3. 五日廣度表 ----------
valid = adj_c.notna()
totals = {d: int(valid.loc[d].sum()) for d in dates}

TABLE_ROWS = [  # (組名, [(顯示, key), ...])
    ("站上均線 (收盤 > MA)",  [(f"收盤 > MA{n}", f"gt{n}") for n in MAS]),
    ("跌破均線 (收盤 < MA)",  [(f"收盤 < MA{n}", f"lt{n}") for n in MAS]),
    ("新站上 (今日站上、前一根未站上)", [(f"新站上 MA{n}", f"ngt{n}") for n in MAS]),
    ("新跌破 (今日跌破、前一根未跌破)", [(f"新跌破 MA{n}", f"nlt{n}") for n in MAS]),
    ("均線多排", [("三線多排 5>10>20", "bull3"), ("四線多排 5>10>20>60", "bull4"),
                 ("全線多排 5>10>20>60>120", "bull5")]),
    ("均線空排", [("三線空排 5<10<20", "bear3"), ("四線空排 5<10<20<60", "bear4"),
                 ("全線空排 5<10<20<60<120", "bear5")]),
    ("均線上揚", [(f"MA{n} 上揚", f"up{n}") for n in MAS]),
    ("均線下彎", [(f"MA{n} 下彎", f"dn{n}") for n in MAS]),
]
flag_df = dict(FLAGS)
table = {}   # key -> {date -> (cnt, pct)}
for _, rows in TABLE_ROWS:
    for _, key in rows:
        table[key] = {}
        for d in dates:
            cnt = int((flag_df[key].loc[d] & valid.loc[d]).sum())
            table[key][d] = (cnt, cnt / totals[d] * 100)

# ---------- 4. 每檔資料 (最新日 flags bitmask + 240 日圖表, 前端滾輪縮放) ----------
CHART_N = 240
idx30 = adj_c.index[-CHART_N:]
dates30 = [d.strftime('%y/%m/%d') for d in idx30]

def arr(df, sid, nd=2):
    a = df.loc[idx30, sid].round(nd)
    return [None if pd.isna(x) else float(x) for x in a]

stocks = {}
lat_flags = {k: flag_df[k].loc[latest] for k in FLAG_KEYS}
valid_latest = valid.loc[latest]
for sid in univ:
    if not valid_latest.get(sid, False):
        continue
    mask = 0
    for i, k in enumerate(FLAG_KEYS):
        if bool(lat_flags[k].get(sid, False)):
            mask |= (1 << i)
    v30 = vol.loc[idx30, sid] / 1000  # 張
    stocks[sid] = {
        "n": name_map[sid],
        "b": board_map[sid],
        "f": mask,
        "o": arr(adj_o, sid), "h": arr(adj_h, sid),
        "l": arr(adj_l, sid), "c": arr(adj_c, sid),
        "v": [None if pd.isna(x) else int(x) for x in v30],
        "m": {str(n): arr(ma[n], sid) for n in MAS},
    }

payload = {
    "date": pd.Timestamp(latest).strftime('%Y-%m-%d'),
    "dates30": dates30,
    "flagKeys": FLAG_KEYS,
    "total": totals[latest],
    "stocks": stocks,
}
data_json = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))

# ---------- 5. 五日表 HTML ----------
date_hdr = "".join(f"<th>{pd.Timestamp(d).strftime('%m/%d')}</th>" for d in dates)
tbl_html = f"<table><thead><tr><th style='text-align:left'>分類</th>{date_hdr}</tr></thead><tbody>"
tbl_html += ("<tr class='total'><td>整體市場 (有成交家數)</td>" +
             "".join(f"<td>{totals[d]:,}</td>" for d in dates) + "</tr>")
for grp, rows in TABLE_ROWS:
    tbl_html += f"<tr class='grp'><td colspan='{N_DAYS+1}'>{grp}</td></tr>"
    for label, key in rows:
        cells = "".join(
            f"<td>{table[key][d][0]:,}<br><span class='pct'>{table[key][d][1]:.1f}%</span></td>"
            for d in dates)
        tbl_html += (f"<tr><td class='lbl clickable' data-key='{key}' "
                     f"title='點擊套用此條件'>{label}</td>{cells}</tr>")
tbl_html += "</tbody></table>"

# ---------- 6. 篩選器 checkbox HTML ----------
def cb(key, label):
    return (f"<label class='cb'><input type='checkbox' data-flag='{key}'>"
            f"<span>{label}</span></label>")

def cb_row(title, keys_labels):
    boxes = "".join(cb(k, l) for k, l in keys_labels)
    return f"<div class='cbrow'><div class='cbtitle'>{title}</div><div class='cbs'>{boxes}</div></div>"

filter_html = ""
filter_html += cb_row("站上", [(f"gt{n}", f"MA{n}") for n in MAS])
filter_html += cb_row("跌破", [(f"lt{n}", f"MA{n}") for n in MAS])
filter_html += cb_row("新站上", [(f"ngt{n}", f"MA{n}") for n in MAS])
filter_html += cb_row("新跌破", [(f"nlt{n}", f"MA{n}") for n in MAS])
filter_html += cb_row("多排", [("bull3", "三線 5>10>20"), ("bull4", "四線 >60"), ("bull5", "全線 >120")])
filter_html += cb_row("空排", [("bear3", "三線 5<10<20"), ("bear4", "四線 <60"), ("bear5", "全線 <120")])
filter_html += cb_row("上揚", [(f"up{n}", f"MA{n}") for n in MAS])
filter_html += cb_row("下彎", [(f"dn{n}", f"MA{n}") for n in MAS])

HTML = """<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>均線廣度儀表板 __DATE__</title>
<style>
:root{--bg:#0f1115;--panel:#141821;--line:#232a37;--txt:#e2e8f0;--dim:#8a93a2;
 --acc:#5fd0c8;--red:#f45b69;--green:#3ecf8e;}
*{box-sizing:border-box}
body{font-family:-apple-system,'PingFang TC','Microsoft JhengHei',sans-serif;
 background:var(--bg);color:var(--txt);margin:0;padding:18px 22px;}
h1{font-size:19px;margin:0 0 2px}
.sub{color:var(--dim);font-size:12.5px;margin-bottom:14px}
details.sec{background:var(--panel);border:1px solid var(--line);border-radius:10px;
 padding:10px 14px;margin-bottom:14px;}
details.sec>summary{cursor:pointer;font-size:15px;font-weight:600;color:var(--acc);}
table{border-collapse:collapse;font-size:12.5px;margin-top:8px;min-width:520px;}
th,td{padding:5px 10px;text-align:center;border-bottom:1px solid var(--line);}
th{color:var(--dim);}
td.lbl{text-align:left;white-space:nowrap;}
td.clickable{cursor:pointer;} td.clickable:hover{color:var(--acc);text-decoration:underline;}
.pct{color:#6b7688;font-size:10.5px;}
tr.grp td{background:#12202b;color:var(--acc);text-align:left;font-weight:600;padding:4px 10px;}
tr.total td{color:#c9a24b;font-weight:600;}
/* filter */
.cbrow{display:flex;align-items:center;gap:10px;padding:4px 0;border-bottom:1px dashed #1c2230;}
.cbtitle{width:52px;color:var(--dim);font-size:13px;flex:none;text-align:right;}
.cbs{display:flex;flex-wrap:wrap;gap:6px;}
.cb{display:inline-flex;align-items:center;gap:4px;background:#1a2130;border:1px solid var(--line);
 border-radius:6px;padding:3px 9px;font-size:12.5px;cursor:pointer;user-select:none;}
.cb:has(input:checked){background:#12414d;border-color:var(--acc);color:var(--acc);}
.cb input{display:none;}
.bar{display:flex;align-items:center;gap:12px;margin:10px 0 6px;flex-wrap:wrap;}
.count{font-size:15px;}.count b{color:var(--acc);font-size:19px;}
button{background:#1a2130;border:1px solid var(--line);color:var(--txt);border-radius:6px;
 padding:5px 12px;cursor:pointer;font-size:12.5px;}
button:hover{border-color:var(--acc);color:var(--acc);}
input[type=search]{background:#1a2130;border:1px solid var(--line);color:var(--txt);
 border-radius:6px;padding:5px 10px;font-size:13px;width:170px;}
.bsel{display:flex;gap:6px;}
/* chips */
#chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px;max-height:46vh;overflow:auto;}
.chip{background:#1c2230;border:1px solid #2a3242;border-radius:5px;padding:3px 8px;
 font-size:12.5px;color:#c4ccd8;cursor:pointer;}
.chip:hover{border-color:var(--acc);color:var(--acc);}
.chip.sii{border-left:3px solid #4a90d9;}.chip.otc{border-left:3px solid #d98b4a;}
/* modal */
#modal{position:fixed;inset:0;background:rgba(0,0,0,.72);display:none;
 align-items:center;justify-content:center;z-index:50;}
#modal.show{display:flex;}
.mbox{background:#12161f;border:1px solid #2a3242;border-radius:12px;padding:14px 16px;
 width:min(860px,94vw);}
.mhead{display:flex;align-items:center;gap:10px;margin-bottom:6px;}
.mhead .t{font-size:16px;font-weight:600;}
.mhead .chg{font-size:13px;}
.mhead .sp{flex:1}
canvas{width:100%;height:auto;display:block;}
.legend{display:flex;gap:12px;font-size:11.5px;color:var(--dim);margin-top:4px;flex-wrap:wrap;}
.legend i{display:inline-block;width:14px;height:3px;margin-right:3px;vertical-align:middle;}
.hint{color:#5a6372;font-size:11.5px;margin-top:6px;}
</style></head><body>
<h1>均線廣度儀表板 · 上市櫃普通股 <span style='font-size:13px;background:#12414d;color:#5fd0c8;border:1px solid #5fd0c8;border-radius:5px;padding:2px 8px;vertical-align:middle'>還原價</span></h1>
<div class='sub'>全部價格與均線皆為<b style='color:#5fd0c8'>還原價 (adjusted)</b> · 均線 5/10/20/60/120/240 · 資料日 <b>__DATE__</b> · 母數 __TOTAL__ 檔
 · <span style='color:#4a90d9'>▌</span>上市 <span style='color:#d98b4a'>▌</span>上櫃</div>

<details class='sec'><summary>五日廣度表（點分類名稱可直接套用篩選）</summary>
<div style='overflow-x:auto'>__TABLE__</div></details>

<details class='sec' open><summary>條件篩選（勾選 = AND 全部符合）</summary>
__FILTERS__
<div class='bar'>
  <div class='bsel'>
    <label class='cb'><input type='checkbox' id='bsii' checked><span>上市</span></label>
    <label class='cb'><input type='checkbox' id='botc' checked><span>上櫃</span></label>
  </div>
  <input type='search' id='q' placeholder='搜尋代號 / 名稱'>
  <button id='clear'>清除全部條件</button>
  <div class='count'>符合 <b id='cnt'>0</b> 家 <span id='pctTxt' class='pct'></span></div>
</div>
<div id='chips'></div>
<div class='hint'>點個股開 K 線圖（預設 60 交易日，滑鼠滾輪縮放 20~240 日）；← → 鍵或按鈕切換上一檔/下一檔，Esc 關閉。</div>
</details>

<div id='modal'><div class='mbox'>
 <div class='mhead'>
   <span class='t' id='mt'></span><span class='chg' id='mchg'></span><span class='sp'></span>
   <button id='prev'>◀ 上一檔</button><button id='next'>下一檔 ▶</button><button id='close'>✕</button>
 </div>
 <canvas id='cv' width='1660' height='840'></canvas>
 <div class='legend' id='mlegend'></div>
</div></div>

<script>
const DATA = __DATA__;
const FK = DATA.flagKeys, BIT = {}; FK.forEach((k,i)=>BIT[k]=i);
// 42 個 flag 超過 JS 32-bit 位元運算範圍, 用除法取位
const hasFlag=(s,f)=>Math.floor(s.f/Math.pow(2,BIT[f]))%2===1;
const IDS = Object.keys(DATA.stocks).sort();
const MAS=[5,10,20,60,120,240];
const MACOL={5:'#ffd166',10:'#ef476f',20:'#06d6a0',60:'#118ab2',120:'#9b5de5',240:'#8d99ae'};

let curList=[], curIdx=-1;
let viewN=60;                      // 圖表顯示天數, 滾輪 20~240
const VIEW_MIN=20, VIEW_MAX=__CHARTN__;

function activeFlags(){
  return [...document.querySelectorAll('input[data-flag]:checked')].map(e=>e.dataset.flag);
}
function refresh(){
  const flags=activeFlags();
  const q=document.getElementById('q').value.trim();
  const sii=document.getElementById('bsii').checked, otc=document.getElementById('botc').checked;
  curList=IDS.filter(id=>{
    const s=DATA.stocks[id];
    if(s.b==='sii'&&!sii) return false;
    if(s.b==='otc'&&!otc) return false;
    for(const f of flags){ if(!hasFlag(s,f)) return false; }
    if(q && !id.includes(q) && !s.n.includes(q)) return false;
    return true;
  });
  document.getElementById('cnt').textContent=curList.length.toLocaleString();
  document.getElementById('pctTxt').textContent='('+(curList.length/DATA.total*100).toFixed(1)+'% / '+DATA.total+')';
  const box=document.getElementById('chips');
  box.innerHTML=curList.map(id=>{
    const s=DATA.stocks[id];
    return `<span class='chip ${s.b}' data-id='${id}'>${id} ${s.n}</span>`;
  }).join('');
}
document.querySelectorAll('input[data-flag],#bsii,#botc').forEach(e=>e.addEventListener('change',refresh));
document.getElementById('q').addEventListener('input',refresh);
document.getElementById('clear').onclick=()=>{
  document.querySelectorAll('input[data-flag]').forEach(e=>e.checked=false);
  document.getElementById('q').value=''; refresh();
};
document.getElementById('chips').addEventListener('click',e=>{
  const c=e.target.closest('.chip'); if(!c)return;
  curIdx=curList.indexOf(c.dataset.id); openChart();
});
// 五日表分類點擊 -> 套用單一條件
document.querySelectorAll('td.clickable').forEach(td=>{
  td.onclick=()=>{
    document.querySelectorAll('input[data-flag]').forEach(e=>e.checked=false);
    const cb=document.querySelector(`input[data-flag='${td.dataset.key}']`);
    if(cb){cb.checked=true;}
    document.querySelector('details.sec[open]')||null;
    refresh();
    document.getElementById('chips').scrollIntoView({behavior:'smooth',block:'center'});
  };
});

// ---------- chart ----------
const modal=document.getElementById('modal');
function openChart(){
  if(curIdx<0||curIdx>=curList.length)return;
  const id=curList[curIdx], s=DATA.stocks[id];
  document.getElementById('mt').innerHTML=`${id} ${s.n} (${s.b==='sii'?'上市':'上櫃'}) <span style='font-size:11px;background:#12414d;color:#5fd0c8;border:1px solid #5fd0c8;border-radius:4px;padding:1px 6px;vertical-align:middle'>還原價</span> ${curIdx+1}/${curList.length}`;
  const c=s.c, last=c[c.length-1], prev=c[c.length-2];
  let chg='';
  if(last!=null&&prev!=null){
    const p=(last/prev-1)*100;
    chg=`收 ${last}  ${p>=0?'+':''}${p.toFixed(2)}%`;
    document.getElementById('mchg').style.color=p>=0?'var(--red)':'var(--green)';
  }
  document.getElementById('mchg').textContent=chg;
  document.getElementById('mlegend').innerHTML=
    MAS.map(n=>`<span><i style='background:${MACOL[n]}'></i>MA${n}</span>`).join('')+
    `<span style='margin-left:auto'>近 <b id='vlabel'>${Math.min(viewN,s.c.length)}</b> 交易日（滾輪縮放 ${VIEW_MIN}~${VIEW_MAX}）· 還原價 · 紅漲綠跌 · 下方為成交量(張)</span>`;
  draw(s);
  modal.classList.add('show');
}
function draw(s){
  const cv=document.getElementById('cv'),x=cv.getContext('2d');
  const W=cv.width,H=cv.height,PL=90,PR=14,PT=20,PB=44,VH=170,GAP=26;
  const PH=H-PT-PB-VH-GAP; // price area height
  x.clearRect(0,0,W,H);
  // 取最右邊 viewN 天
  const n=Math.min(viewN,s.c.length), off=s.c.length-n;
  const C=s.c.slice(off),O=s.o.slice(off),Hh=s.h.slice(off),L=s.l.slice(off),V=s.v.slice(off);
  const M={}; for(const m of MAS)M[m]=s.m[m].slice(off);
  const DT=DATA.dates30.slice(off);
  const cw=(W-PL-PR)/n;
  // scale
  let lo=1e18,hi=-1e18;
  for(let i=0;i<n;i++){
    if(L[i]!=null){lo=Math.min(lo,L[i]);hi=Math.max(hi,Hh[i]);}
    for(const m of MAS){const v=M[m][i];if(v!=null){lo=Math.min(lo,v);hi=Math.max(hi,v);}}
  }
  const pad=(hi-lo)*0.06||1; lo-=pad;hi+=pad;
  const Y=v=>PT+(hi-v)/(hi-lo)*PH;
  const X=i=>PL+i*cw+cw/2;
  // grid + y labels
  x.strokeStyle='#1c2230';x.fillStyle='#6b7688';x.font='20px sans-serif';x.textAlign='right';
  for(let g=0;g<=4;g++){
    const v=lo+(hi-lo)*g/4, y=Y(v);
    x.beginPath();x.moveTo(PL,y);x.lineTo(W-PR,y);x.stroke();
    x.fillText(v.toFixed(v>=100?0:2),PL-8,y+6);
  }
  // volume scale
  let vmax=Math.max(...V.filter(v=>v!=null),1);
  const VY0=PT+PH+GAP+VH;
  // x labels (約 6 個)
  const step=Math.max(1,Math.ceil(n/6));
  x.textAlign='center';
  for(let i=0;i<n;i+=step){x.fillText(DT[i],X(i),H-12);}
  // volume bars
  const bw=Math.max(cw*0.64,1);
  for(let i=0;i<n;i++){
    if(V[i]==null)continue;
    const up=C[i]!=null&&O[i]!=null&&C[i]>=O[i];
    x.fillStyle=up?'rgba(244,91,105,.55)':'rgba(62,207,142,.55)';
    const bh=V[i]/vmax*VH;
    x.fillRect(X(i)-bw/2,VY0-bh,bw,bh);
  }
  x.fillStyle='#6b7688';x.textAlign='right';
  x.fillText((vmax>=10000?(vmax/1000).toFixed(0)+'k':vmax)+'',PL-8,VY0-VH+16);
  // candles
  const wick=Math.max(1,Math.min(2,cw*0.18));
  for(let i=0;i<n;i++){
    if(C[i]==null||O[i]==null)continue;
    const up=C[i]>=O[i];
    x.strokeStyle=x.fillStyle=up?'#f45b69':'#3ecf8e';
    x.lineWidth=wick;
    x.beginPath();x.moveTo(X(i),Y(Hh[i]));x.lineTo(X(i),Y(L[i]));x.stroke();
    const y1=Y(Math.max(O[i],C[i])),y2=Y(Math.min(O[i],C[i]));
    x.fillRect(X(i)-bw/2,y1,bw,Math.max(y2-y1,1.5));
  }
  // MA lines
  const mlw=n>150?1.8:2.5;
  for(const m of MAS){
    x.strokeStyle=MACOL[m];x.lineWidth=mlw;x.beginPath();let started=false;
    for(let i=0;i<n;i++){
      const v=M[m][i];if(v==null){continue;}
      if(!started){x.moveTo(X(i),Y(v));started=true;}else{x.lineTo(X(i),Y(v));}
    }
    x.stroke();
  }
}
// 滾輪縮放: 往上滾=放大(天數變少), 往下滾=縮小(天數變多), 貼齊最新日
document.getElementById('cv').addEventListener('wheel',e=>{
  e.preventDefault();
  const step=Math.max(4,Math.round(viewN*0.15));
  viewN=Math.min(VIEW_MAX,Math.max(VIEW_MIN,viewN+(e.deltaY>0?step:-step)));
  if(curIdx>=0&&curIdx<curList.length){
    const s=DATA.stocks[curList[curIdx]];
    const vl=document.getElementById('vlabel');
    if(vl)vl.textContent=Math.min(viewN,s.c.length);
    draw(s);
  }
},{passive:false});
function nav(d){
  if(!curList.length)return;
  curIdx=(curIdx+d+curList.length)%curList.length; openChart();
}
document.getElementById('prev').onclick=()=>nav(-1);
document.getElementById('next').onclick=()=>nav(1);
document.getElementById('close').onclick=()=>modal.classList.remove('show');
modal.addEventListener('click',e=>{if(e.target===modal)modal.classList.remove('show');});
document.addEventListener('keydown',e=>{
  if(!modal.classList.contains('show'))return;
  if(e.key==='Escape')modal.classList.remove('show');
  if(e.key==='ArrowLeft')nav(-1);
  if(e.key==='ArrowRight')nav(1);
});
refresh();
</script></body></html>"""

html = (HTML
        .replace("__CHARTN__", str(CHART_N))
        .replace("__DATE__", payload["date"])
        .replace("__TOTAL__", f"{totals[latest]:,}")
        .replace("__TABLE__", tbl_html)
        .replace("__FILTERS__", filter_html)
        .replace("__DATA__", data_json))

os.makedirs('dist', exist_ok=True)
out = os.path.join('dist', 'index.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"OK -> {out}  ({os.path.getsize(out)/1e6:.1f} MB, {len(stocks)} stocks)")
