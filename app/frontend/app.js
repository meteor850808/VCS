'use strict';
// ── CRS Maps ─────────────────────────────────────────────
const CRS_ALIAS = {
  'WGS84':'EPSG:4326','WGS 84':'EPSG:4326','WGS_1984':'EPSG:4326',
  '国家2000':'EPSG:4490','CGCS2000':'EPSG:4490','cgcs2000':'EPSG:4490',
  '北京54':'EPSG:4214','BJ54':'EPSG:4214','beijing54':'EPSG:4214',
  '西安80':'EPSG:4610','XA80':'EPSG:4610','xian80':'EPSG:4610',
  '墨卡托':'EPSG:3857','WebMercator':'EPSG:3857','webmercator':'EPSG:3857',
  'shanghaicity':'Custom:shanghaicity','ShanghaiCity':'Custom:shanghaicity',
  '上海城市坐标系':'Custom:shanghaicity','shanghai_local':'Custom:shanghai_local',
  '上海地方坐标系':'Custom:shanghai_local',
};
const CRS_LBL = {
  'EPSG:4326':'WGS84','EPSG:4490':'CGCS2000（国家2000）',
  'EPSG:4214':'北京1954','EPSG:4610':'西安1980',
  'EPSG:3857':'Web 墨卡托','EPSG:4533':'CGCS2000 3° GK 39','EPSG:4534':'CGCS2000 3° GK 40',
  'Custom:shanghaicity':'上海城市坐标系(Beijing54/Krasovsky)',
  'Custom:shanghai_local':'上海地方坐标系(CGCS2000)',
  'EPSG:4519':'CGCS2000 3° GK 31(上海)','EPSG:4520':'CGCS2000 3° GK 32(江浙)',
  'EPSG:2406':'Beijing1954 3° GK 31(上海)','EPSG:32651':'WGS84 UTM 51N(东亚)',
};

// ── State ─────────────────────────────────────────────────
const S = {
  key: (function(){try{return localStorage.getItem('vcs_key')||''}catch(e){return''}})(),
  base: window.location.origin||'',
  dmPage:1, dmSize:20, dmSearch:'', dmFmt:'', dmFrom:'', dmTo:'',
  sel: new Set(),
  upFile:null, stdFile:null,
  lastUpId:null,
  _pvMap:null,_pvGeo:null,_pvTile:null,
  _dMap:null,_dGeo:null,_dFileId:null,
  timer:null,
};

// ── Utils ─────────────────────────────────────────────────
const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
function toast(msg, t='info'){
  const w=document.getElementById('tc'), el=document.createElement('div');
  el.className=`toast ${t==='error'?'err':t==='success'?'ok':t==='warning'?'warn':'inf'}`;
  el.textContent=(t==='success'?'✓ ':t==='error'?'✗ ':t==='warning'?'⚠ ':'')+msg;
  w.appendChild(el); setTimeout(()=>el.remove(),3200);
}
function fsz(b){ if(!b)return'0 B'; const u=['B','KB','MB','GB'];let i=0; while(b>=1024&&i<3){b/=1024;i++;} return b.toFixed(i?1:0)+' '+u[i]; }
function ftm(ts){ if(!ts)return'-'; return new Date(ts).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}); }
/** 自定义 SRID 区段（与后端 custom_crs.CUSTOM_SRID_BASE=910000 对齐） */
const CUSTOM_SRID_MIN = 910000;
/** CRS 可读标签：SRID（区分标准 EPSG 与自定义）→ WKT 名称 → 别名 → 原文 */
function crsLabel(crs, srid, isCustom){
  const cust = !!isCustom || (srid && srid >= CUSTOM_SRID_MIN);
  if(srid && !cust) return 'EPSG:'+srid;
  if(cust){
    // 自定义坐标系：从 WKT/别名提取可读名称，避免误显示为 EPSG:913xxx
    const alias = crsAlias(crs) || crsFromWkt(crs) || '自定义坐标系';
    return alias;
  }
  return crsFromWkt(crs) || '';
}
/** 从 CRS 文本/别名得到可读名称（优先 CRS_LBL 标签，其次 WKT 名称） */
function crsAlias(crs){
  if(!crs) return '';
  const s = String(crs).trim();
  if(!s) return '';
  const nm = crsFromWkt(s);
  // Custom:code → 已知标签（如 Custom:shanghaicity）
  const cm = s.match(/^custom:(\S+)/i);
  if(cm && CRS_LBL['Custom:'+cm[1]]) return CRS_LBL['Custom:'+cm[1]];
  // WKT/EPSG 名称 → 已知自定义坐标系标签（如 ShanghaiCity → Custom:shanghaicity 的标签）
  for(const k in CRS_ALIAS){
    if(/^custom:/i.test(CRS_ALIAS[k]) && k.toLowerCase()===nm.toLowerCase()){
      const lbl=CRS_LBL[CRS_ALIAS[k]];
      if(lbl) return lbl;
    }
  }
  // 别名表反向：原值本身是 shanghaicity / EPSG 等
  for(const k in CRS_ALIAS){
    if(CRS_ALIAS[k].toLowerCase()===s.toLowerCase()){
      return CRS_LBL[k] || CRS_ALIAS[k];
    }
  }
  if(nm && CRS_LBL[nm]) return CRS_LBL[nm];
  return nm;
}
/** 从 WKT 提取坐标系名（PROJCS["name","GEOGCS["...）/ 规范化 */
function crsFromWkt(crs){
  if(!crs) return '';
  const s = String(crs).trim();
  if(!s) return '';
  if(/^EPSG:\d+$/i.test(s)) return s;
  // Custom:前缀 (后端 Custom:code)
  const cm = s.match(/^custom:(\S+)/i);
  if(cm) return 'Custom:'+cm[1];
  const m = s.match(/^(?:PROJCS|GEOGCS|VERTCRS|COMPD_CS)\["([^"]+)"/);
  if(m) return m[1];
  return s.length>60 ? s.substring(0,57)+'...' : s;
}
/** 生成后端可解析的 CRS 输入串（用于自动回填，非展示标签）。
    已知自定义码 → Custom:code；否则回退原始 WKT/EPSG 原文，保证后端可解析。 */
function crsResolvable(crs){
  if(!crs) return '';
  const s = String(crs).trim();
  if(!s) return s;
  const nm = crsFromWkt(s);
  // 已知自定义坐标系名 → Custom:code（后端/pyproj 均可解析）
  if(/^shanghaicity$/i.test(nm)||/^ShanghaiCity$/i.test(nm)) return 'Custom:shanghaicity';
  if(/^shanghai_local$/i.test(nm)) return 'Custom:shanghai_local';
  if(/^shanghai$/i.test(nm)) return 'Custom:shanghai_local';
  // 已是可解析形式（EPSG:xxx / Custom:xxx）原样返回
  if(/^(epsg:|custom:)/i.test(s)) return s;
  // 其余（WKT/proj4 原文）原样返回，后端 resolve_crs/pyproj 可解析
  return s;
}
function resCRS(v){ const t=String(v||'').trim(); return CRS_ALIAS[t]||t; }
function resCRS(v){ const t=String(v||'').trim(); return CRS_ALIAS[t]||t; }

function fmtBadge(n){
  if(!n)return'<span class="badge bother">未知</span>';
  const x=n.toLowerCase();
  if(x.includes('geojson')||x.includes('json'))return'<span class="badge bjson">GeoJSON</span>';
  if(x.includes('shp')||x.includes('shapefile'))return'<span class="badge bshp">SHP</span>';
  if(x.includes('kml')||x.includes('kmz'))return'<span class="badge bkml">KML</span>';
  if(x.includes('gpkg')||x.includes('geopackage'))return'<span class="badge bgpkg">GPKG</span>';
  if(x.includes('dxf'))return'<span class="badge bdxf">DXF</span>';
  if(x.includes('gml'))return'<span class="badge bgml">GML</span>';
  return`<span class="badge bother">${esc(n.split('/').pop()||n).slice(0,8)}</span>`;
}
function stBadge(s){
  const m={pending:'bpend',running:'brun',success:'bok',failed:'bfail',cancelled:'bcan'};
  const l={pending:'等待中',running:'运行中',success:'已完成',failed:'失败',cancelled:'已取消'};
  return`<span class="badge ${m[s]||'bpend'}">${l[s]||s}</span>`;
}

// ── API ───────────────────────────────────────────────────
function ah(){ if(!S.key)throw new Error('API Key 未设置'); return{'X-API-Key':S.key}; }
// 带超时的 fetch（默认 30s）：避免请求无限挂起导致界面"卡死/偶发失效"
async function _t(url,opt,ms=30000){
  const c=new AbortController();const t=setTimeout(()=>c.abort(),ms);
  try{return await fetch(url,{...opt,signal:c.signal});}
  catch(e){if(e.name==='AbortError')throw new Error('请求超时，请重试');throw e;}
  finally{clearTimeout(t);}
}
async function GET(p){
  const r=await _t(S.base+p,{headers:ah()});
  if(r.status===401){
    if(S.timer){clearInterval(S.timer);S.timer=null;}
    throw new Error('401 Unauthorized — API Key 无效或未设置');
  }
  if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.detail||`HTTP ${r.status}`);}
  return r.json().catch(()=>({}));
}
async function POST(p,body){
  const r=await _t(S.base+p,{method:'POST',headers:{...ah(),'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.detail||`HTTP ${r.status}`);}
  if(r.status===204||r.headers.get('content-length')==='0')return null;
  return r.json();
}
async function POSTF(p,fd){
  const r=await _t(S.base+p,{method:'POST',headers:ah(),body:fd});
  if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.detail||`HTTP ${r.status}`);}
  return r.json();
}
async function PUT(p,body){
  const r=await _t(S.base+p,{method:'PUT',headers:{...ah(),'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.detail||`HTTP ${r.status}`);}
  return r.json();
}
async function dlAuth(url,fn){
  try{
    const r=await _t(url,{headers:ah()});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const blob=await r.blob();
    const cd=r.headers.get('content-disposition')||'';
    let name=fn;
    // RFC 5987 编码优先：filename*=UTF-8''xxx
    const star=cd.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
    if(star){try{name=decodeURIComponent(star[1]);}catch(e){name=star[1];}}
    else{const plain=cd.match(/filename\s*=\s*"?([^";]+)"?/i);if(plain)name=plain[1].trim();}
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);
    a.download=name;document.body.appendChild(a);a.click();document.body.removeChild(a);
    toast('下载成功','success');
  }catch(e){toast('下载失败: '+e.message,'error');}
}

// ── Navigation ────────────────────────────────────────────
function goPage(p){
  document.querySelectorAll('.sb-nav a[data-page]').forEach(a=>a.classList.remove('active'));
  const lk=document.querySelector(`.sb-nav a[data-page="${p}"]`);
  if(lk)lk.classList.add('active');
  document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
  const el=document.getElementById('page-'+p);
  if(el)el.classList.add('active');
  if(p==='dashboard'&&S.key)loadDash();
  if(p==='upload'&&S.key)loadRecent();
  if(p==='data'&&S.key){cleanupThenLoadFiles();loadStats();}
  if(p==='quality'&&S.key)initQcPage();
  if(p==='transform'&&S.key){loadTxFiles();loadTxResults();}
  if(p==='preview'&&S.key)loadPvFiles();
  if(p==='standards'&&S.key)v2LoadStandards();
  if(p==='admin')loadAdminKeys();
}

// ── API Key ───────────────────────────────────────────────
function initKey(){
  const inp=document.getElementById('apiKeyInput'),btn=document.getElementById('setKeyBtn'),ks=document.getElementById('keyStatus');
  if(!inp||!btn)return console.error('initKey: DOM 元素未找到');
  // 从 localStorage 恢复
  if(S.key){inp.value=S.key;setKS(true);}
  // 输入时自动保存（双重保障）
  inp.addEventListener('input',()=>{
    const k=inp.value.trim();
    if(!k)return;
    S.key=k;try{localStorage.setItem('vcs_key',k)}catch(e){}
    setKS(true);
  });
  // 点击按钮保存
  btn.addEventListener('click',()=>{
    const k=inp.value.trim();
    if(!k)return toast('请输入 API Key','error');
    S.key=k;try{localStorage.setItem('vcs_key',k)}catch(e){}
    setKS(true);loadDash();loadRecent();
    if(S.timer)clearInterval(S.timer);
    S.timer=setInterval(loadDash,6000);
    btn.textContent='✓ 已设置';setTimeout(()=>btn.textContent='设置密钥',2000);
    toast('API Key 已保存','success');
  });
}
function setKS(ok){
  const el=document.getElementById('keyStatus');
  el.textContent=ok?'已设置':'未设置';
  el.className='ks '+(ok?'ok':'no');
}

// ── Dashboard ─────────────────────────────────────────────
// 状态 → 标签/样式/描述映射
const KB_COLS=[
  {key:'pending',  label:'待处理', icon:'📥', cls:'pending'},
  {key:'running',  label:'处理中', icon:'⏳', cls:'running'},
  {key:'success',  label:'已完成', icon:'✅', cls:'success'},
  {key:'failed',   label:'失败',   icon:'❌', cls:'failed'},
  {key:'cancelled',label:'已取消', icon:'🕓', cls:'cancelled'},
];
const DASH={
  jobs:[],
  statusFilter:'all',     // all | 某状态列聚焦
  fmtFilter:'all',        // all | geo/shp/gpkg/kml/dxf/gml/other
  sel:new Set(),          // 勾选的任务 id
  chartFocus:null,        // 图表点击聚焦状态/格式（null=全部）
};
function dashTab(tab,btn){
  document.querySelectorAll('.dash-tab').forEach(b=>b.classList.toggle('active',b===btn));
  document.getElementById('dashOverview').style.display=tab==='overview'?'':'none';
  document.getElementById('dashManage').style.display=tab==='manage'?'':'none';
  if(tab==='manage')kbRender();
}
// 任务是否命中当前搜索/格式/状态筛选
function kbMatch(j){
  if(DASH.statusFilter!=='all' && j.status!==DASH.statusFilter)return false;
  if(DASH.fmtFilter!=='all'){
    const f=DASH.fmtFilter;
    const fmt=(j.output_format||'').toLowerCase();
    const hit=f==='other'?!/geojson|json|shp|shapefile|gpkg|geopackage|kml|kmz|dxf|gml/.test(fmt):fmt.includes(f);
    if(!hit)return false;
  }
  const q=(document.getElementById('kbSearch').value||'').trim().toLowerCase();
  if(q && !((j.original_filename||'').toLowerCase().includes(q)))return false;
  return true;
}
async function loadDash(){
  try{
    // 并行拉取：任务 + 数据统计 + 标准统计（各自独立容错）
    const [jr,dr,sr]=await Promise.allSettled([
      GET('/api/v1/jobs?page_size=100'),
      GET('/api/v1/data/stats'),
      GET('/api/v1/data-standards-v2/stats'),
    ]);
    const jobs=jr.status==='fulfilled'&&Array.isArray(jr.value)?jr.value:[];
    DASH.jobs=jobs;
    // 统计卡
    const st={total:jobs.length,running:0,success:0,failed:0,cancelled:0,pending:0};
    jobs.forEach(j=>{if(j.status in st)st[j.status]++;});
    document.getElementById('st-total').textContent=st.total;
    document.getElementById('st-running').textContent=st.running;
    document.getElementById('st-success').textContent=st.success;
    document.getElementById('st-failed').textContent=st.failed;
    // 汇总图表 + 数据/标准统计 + 管理页
    renderDashCharts();
    if(dr.status==='fulfilled'&&dr.value)renderDataStats(dr.value);
    if(sr.status==='fulfilled'&&sr.value)renderStdStats(sr.value);
    const cnt=jobs.length;
    document.getElementById('dashMngCnt').textContent=cnt;
    document.getElementById('dashMngCnt').style.display=cnt?'':'none';
    if(document.getElementById('dashManage').style.display!=='none')kbRender();
  }catch(e){}
}
// 数据统计渲染（/data/stats）
function fmtSize(n){
  if(n==null||isNaN(n))return '0 B';
  const u=['B','KB','MB','GB','TB'];let i=0;let v=n;
  while(v>=1024&&i<u.length-1){v/=1024;i++;}
  return v.toFixed(v>=100||i===0?0:1)+' '+u[i];
}
function toBarsHtml(map,order,colors){
  const max=Math.max(1,...Object.values(map));
  return order.map(k=>{
    const v=map[k]||0;if(!v)return '';
    const pct=(v/max*100).toFixed(0);
    return `<div class="dash-bar-row"><span class="dash-bar-label">${k}</span>
      <div class="dash-bar-track"><div class="dash-bar-fill" style="width:${pct}%;background:${(colors[k]||'#8b949e')}"></div></div>
      <span class="dash-bar-count">${v}</span></div>`;
  }).join('');
}
function renderDataStats(d){
  const doSet=(id,v)=>{const el=document.getElementById(id);if(el)el.textContent=v;};
  doSet('ds-files',d.total_files??0);
  doSet('ds-size',fmtSize(d.total_size||0));
  const ft=d.feature_totals||{};
  doSet('ds-feats',(ft.total??0).toLocaleString());
  doSet('ds-withstd',d.with_standard??0);
  // 数据格式分布
  const bf=d.by_format||{};
  document.getElementById('dashDataFmt').innerHTML=
    Object.keys(bf).length?toBarsHtml(bf,Object.keys(bf).sort((a,b)=>bf[b]-bf[a]),{}):
    '<div class="empty" style="padding:20px 0;"><div class="ei">📭</div><p>暂无数据</p></div>';
  // 图层要素分布（点/线/面）
  const kinds=[['点要素','point','#3fb950'],['线要素','line','#79c0ff'],['面要素','polygon','#bc8cff']];
  const fmap={};kinds.forEach(([label,key])=>{fmap[label]=ft[key]||0;});
  const total=ft.total||0;
  document.getElementById('dashLayerFeats').innerHTML=
    total?toBarsHtml(fmap,['点要素','线要素','面要素'],{add:''})+
    `<div class="dash-bar-row"><span class="dash-bar-label">合计</span>
      <div class="dash-bar-track"><div class="dash-bar-fill" style="width:100%;background:linear-gradient(90deg,#3fb950,#79c0ff,#bc8cff)"></div></div>
      <span class="dash-bar-count">${total.toLocaleString()}</span></div>`
    :'<div class="empty" style="padding:20px 0;"><div class="ei">🗺</div><p>暂无图层要素</p></div>';
}
// 标准统计渲染（/data-standards-v2/stats）
function renderStdStats(s){
  const doSet=(id,v)=>{const el=document.getElementById(id);if(el)el.textContent=v;};
  doSet('sd-total',s.standards_total??0);
  doSet('sd-layers',s.layers_total??0);
  doSet('sd-tables',s.tables_total??0);
  doSet('sd-fields',s.fields_total??0);
  // 分类分布（national/industry/custom → 国标/行标/自定义）
  const cat=s.by_category||{};
  const catName={'national':'国标','industry':'行标','custom':'自定义'};
  const cmap={};Object.entries(cat).forEach(([k,v])=>{cmap[catName[k]||k]=v;});
  document.getElementById('dashStdCat').innerHTML=
    Object.keys(cmap).length?toBarsHtml(cmap,Object.keys(cmap),{}):
    '<div class="empty" style="padding:20px 0;"><div class="ei">🏷</div><p>暂无标准</p></div>';
  // 状态分布（draft/published/archived）
  const st=s.by_status||{};
  const stName={'draft':'草稿','published':'已发布','archived':'已归档'};
  const stCol={'draft':'#8b949e','published':'#3fb950','archived':'#d29922'};
  const smap={};Object.entries(st).forEach(([k,v])=>{smap[stName[k]||k]=v;});
  const sBody=Object.keys(smap).length?toBarsHtml(smap,Object.keys(smap),stCol):
    '<div class="empty" style="padding:20px 0;"><div class="ei">🔢</div><p>暂无标准</p></div>';
  const sFoot=`<div style="margin-top:8px;font-size:11px;color:var(--t2);display:flex;gap:14px;flex-wrap:wrap;">
      <span>🗂 字典 <b>${(s.standard_dicts||0)+(s.global_dicts||0)}</b>（全局 ${s.global_dicts||0} / 标准级 ${s.standard_dicts||0}）</span>
      <span>📦 字典项 <b>${s.dict_items_total||0}</b></span>
      <span>🔖 版本快照 <b>${s.versions_total||0}</b></span></div>`;
  document.getElementById('dashStdStatus').innerHTML=sBody+sFoot;
}
// 按状态统计（用于饼图/柱状图）
function statusCounts(){const c={pending:0,running:0,success:0,failed:0,cancelled:0};DASH.jobs.forEach(j=>{if(j.status in c)c[j.status]++;});return c;}
function fmtCounts(){
  const c={};DASH.jobs.forEach(j=>{const f=fmtKey(j.output_format);c[f]=(c[f]||0)+1;});
  return c;
}
function fmtKey(f){
  const x=(f||'').toLowerCase();
  if(x.includes('geojson')||x.includes('json'))return 'GeoJSON';
  if(x.includes('shp')||x.includes('shapefile'))return 'SHP';
  if(x.includes('gpkg')||x.includes('geopackage'))return 'GPKG';
  if(x.includes('kml')||x.includes('kmz'))return 'KML';
  if(x.includes('dxf'))return 'DXF';
  if(x.includes('gml'))return 'GML';
  return '其他';
}
function fmtFilterKey(f){
  const x=(f||'').toLowerCase();
  if(x.includes('geojson')||x.includes('json'))return 'geo';
  if(x.includes('shp')||x.includes('shapefile'))return 'shp';
  if(x.includes('gpkg')||x.includes('geopackage'))return 'gpkg';
  if(x.includes('kml')||x.includes('kmz'))return 'kml';
  if(x.includes('dxf'))return 'dxf';
  if(x.includes('gml'))return 'gml';
  return 'other';
}
const KB_STATUS_COLOR={pending:'#8b949e',running:'#388bfd',success:'#3fb950',failed:'#f85149',cancelled:'#d29922'};
function renderDashCharts(){
  // 1. 状态饼图（SVG donut）
  const sc=statusCounts();
  const se=document.getElementById('dashStatusDonut');
  const total=Object.values(sc).reduce((a,b)=>a+b,0);
  if(total===0){
    se.innerHTML='<div class="empty" style="padding:20px 0;"><div class="ei">📭</div><p>暂无任务数据</p></div>';
  }else{
    const R=42,C=2*Math.PI*R,r=80,size=r*2,cx=r,cy=r;
    let off=0;let segs=[];let i=0;
    KB_COLS.forEach(col=>{const v=sc[col.key];if(!v)return;const frac=v/total;
      const segLen=frac*C,gapLen=C-frac*C;
      segs.push(`<circle cx="${cx}" cy="${cy}" r="${R}" fill="none" stroke="${KB_STATUS_COLOR[col.key]}" stroke-width="14"
        stroke-dasharray="${segLen.toFixed(2)} ${gapLen.toFixed(2)}" stroke-dashoffset="${(-off*C).toFixed(2)}" stroke-linecap="round"
        onclick="dashChartFocus('${col.key}',null,this)" style="cursor:pointer;opacity:${DASH.chartFocus&&DASH.chartFocus!==col.key?'.25':'1'}"></circle>`);
      off+=frac;i++;
    });
    se.innerHTML=`
      <div class="donut-svg">
        <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
          <circle cx="${cx}" cy="${cy}" r="${R}" fill="none" stroke="var(--bg-3)" stroke-width="14"/>
          ${segs.join('')}
        </svg>
      </div>
      <div class="dash-legend">
        ${KB_COLS.map(col=>`<div class="legend-item ${DASH.chartFocus&&DASH.chartFocus!==col.key?'dim':''}"
          onclick="dashChartFocus('${col.key}',null,this)">
          <span class="legend-dot" style="background:${KB_STATUS_COLOR[col.key]}"></span>
          <span>${col.icon} ${col.label}</span>
          <span class="lv">${sc[col.key]}</span>
        </div>`).join('')}
      </div>`;
  }
  // 2. 格式柱状图
  const fc=fmtCounts();
  const order=['GeoJSON','SHP','GPKG','KML','DXF','GML','其他'];
  const max=Math.max(1,...Object.values(fc));
  const be=document.getElementById('dashFormatBars');
  if(total===0){
    be.innerHTML='<div class="empty" style="padding:20px 0;"><div class="ei">📦</div><p>暂无格式数据</p></div>';
  }else{
    be.innerHTML=order.map(k=>{
      const v=fc[k]||0;if(!v)return '';
      const pct=(v/max*100).toFixed(0);
      const color={GeoJSON:'#3fb950',SHP:'#79c0ff',GPKG:'#bc8cff',KML:'#d29922',DXF:'#ffa657',GML:'#56d364',其他:'#8b949e'}[k]||'#8b949e';
      return `<div class="dash-bar-row ${DASH.chartFocus==='fmt:'+fmtFilterKey(k)?'':'dim'}" onclick="dashChartFocus(null,'${fmtFilterKey(k)}',this)" title="点击筛选 ${k}">
        <span class="dash-bar-label">${k}</span>
        <div class="dash-bar-track"><div class="dash-bar-fill" style="width:${pct}%;background:${color}"></div></div>
        <span class="dash-bar-count">${v}</span>
      </div>`;
    }).join('');
  }
}
// 图表点击聚焦：一种状态或一种格式；再次点击/点击空白取消
function dashChartFocus(statusKey,fmtKey,el){
  if(statusKey){DASH.chartFocus = DASH.chartFocus===statusKey?null:statusKey;}
  else if(fmtKey){DASH.chartFocus = DASH.chartFocus==='fmt:'+fmtKey?null:'fmt:'+fmtKey;}
  renderDashCharts();
}
function kbCardHtml(j){
  const fmtBadgeHtml=fmtBadge(j.output_format);
  const prog=Math.round((j.progress||0)*100);
  const sub=[];
  if(j.status==='running'&&j.current_step)sub.push(esc(j.current_step));
  sub.push(ftm(j.created_at));
  const isCancelable=(j.status==='pending'||j.status==='running');
  const isDeletable=(j.status==='success'||j.status==='failed'||j.status==='cancelled');
  const sel=DASH.sel.has(j.id)?'sel':'';
  return `<div class="kb-card s-${j.status} ${sel}" onclick="openJobModal('${esc(j.id)}')" title="点击查看详情">
    <div class="kb-card-top">
      <input type="checkbox" class="kb-check" ${DASH.sel.has(j.id)?'checked':''}
        onclick="event.stopPropagation();kbToggleSel('${esc(j.id)}',this.checked)">
      ${fmtBadgeHtml}
      ${stBadge(j.status)}
    </div>
    <div class="kb-card-meta">
      <div class="kb-card-name">${esc(j.original_filename)}</div>
      <div class="kb-card-sub">${sub.join(' · ')||'-'}</div>
    </div>
    <div class="prog" style="height:6px;"><div class="pf ${j.status==='success'?'g':(j.status==='failed'||j.status==='cancelled')?'y':'b'}" style="width:${prog}%"></div></div>
    <div class="kb-card-foot">
      <span style="font-size:11px;color:var(--t2);">${prog}%</span>
      <span style="flex:1;"></span>
      ${isCancelable?`<button class="kb-act cancel" onclick="event.stopPropagation();cancelJob('${esc(j.id)}')" title="取消任务">✕ 取消</button>`:''}
      ${isDeletable?`<button class="kb-act del" onclick="event.stopPropagation();deleteJob('${esc(j.id)}',this)" title="删除任务（清理磁盘产物）">🗑 删除</button>`:''}
    </div>
  </div>`;
}
function kbRender(){
  const board=document.getElementById('kbBoard');
  // 清理已删除任务的勾选残留
  const ids=new Set(DASH.jobs.map(j=>j.id));
  DASH.sel=new Set([...DASH.sel].filter(id=>ids.has(id)));
  if(DASH.jobs.length===0){
    board.innerHTML=`<div class="kb-col" style="grid-column:1/-1;"><div class="kb-col-body"><div class="kb-empty"><div class="ei">📭</div><p>暂无任务，前往「数据上传」提交转换任务</p></div></div></div>`;
    kbUpdateBatchBar();return;
  }
  board.innerHTML=KB_COLS.map(col=>{
    const list=DASH.jobs.filter(j=>j.status===col.key&&kbMatch(j));
    const body=list.length
      ? list.map(kbCardHtml).join('')
      : `<div class="kb-empty"><div class="ei">–</div><p>暂无</p></div>`;
    const dim=DASH.statusFilter!=='all'&&DASH.statusFilter!==col.key;
    return `<div class="kb-col ${dim?'dim':''}" onclick="kbFocusCol('${col.key}')" title="${DASH.statusFilter===col.key?'再次点击取消聚焦':'点击聚焦该状态'}">
      <div class="kb-col-h ${col.cls}" onclick="event.stopPropagation();kbFocusCol('${col.key}')">
        <input type="checkbox" class="kb-selcol" data-col="${col.key}"
          onclick="event.stopPropagation();kbSelCol('${col.key}',this.checked)"
          title="全选该列（当前筛选结果）">
        <span class="kb-dot ${col.cls}"></span><span>${col.icon} ${col.label}</span>
        <span class="kb-col-cnt">${list.length}</span>
      </div>
      <div class="kb-col-body" onclick="event.stopPropagation()">${body}</div>
    </div>`;
  }).join('');
  kbSyncColSel();
  kbUpdateBatchBar();
}
// 列头全选同步（选中/半选/未选）
function kbSyncColSel(){
  const board=document.getElementById('kbBoard');
  KB_COLS.forEach(col=>{
    const cb=board.querySelector(`.kb-selcol[data-col="${col.key}"]`);
    if(!cb)return;
    const ids=new Set(DASH.jobs.filter(j=>j.status===col.key&&kbMatch(j)).map(j=>j.id));
    const sel=[...DASH.sel].filter(id=>ids.has(id)).length;
    cb.checked=sel>0&&sel===ids.size;
    cb.indeterminate=sel>0&&sel<ids.size;
  });
}
// 列头全选：勾选/取消该列当前筛选结果
function kbSelCol(key,on){
  DASH.jobs.forEach(j=>{
    if(j.status===key&&kbMatch(j)){if(on)DASH.sel.add(j.id);else DASH.sel.delete(j.id);}
  });
  kbRender();
}
// 点击列聚焦该状态（再点取消）；列头可聚焦，列体交互阻止冒泡避免误聚焦
function kbFocusCol(key){
  DASH.statusFilter=(DASH.statusFilter===key)?'all':key;
  kbRender();
}
function kbFilter(f,el){
  DASH.fmtFilter=f;
  document.querySelectorAll('.kb-filter').forEach(x=>x.classList.toggle('active',x===el));
  kbRender();
}
function kbToggleSel(id,on){
  if(on)DASH.sel.add(id);else DASH.sel.delete(id);
  kbUpdateBatchBar();
  kbRender();
}
function kbUpdateBatchBar(){
  const bar=document.getElementById('kbBatchBar');
  const n=DASH.sel.size;
  bar.style.display=n?'':'none';
  document.getElementById('kbSelCount').textContent=n;
}
function kbClearSel(){DASH.sel.clear();kbRender();}
function kbBatchCancel(){
  const ids=[...DASH.sel].filter(id=>{const j=DASH.jobs.find(x=>x.id===id);return j&&(j.status==='pending'||j.status==='running');});
  if(!ids.length){toast('请选择待处理或运行中的任务','error');return;}
  if(!confirm(`确定取消选中的 ${ids.length} 个任务？`))return;
  (async()=>{
    let ok=0,fail=0;
    for(const id of ids){
      try{await fetch(S.base+'/api/v1/jobs/'+id,{method:'DELETE',headers:ah()});ok++;}
      catch(e){fail++;}
    }
    toast(`批量取消完成：${ok} 成功，${fail} 失败`,ok>0?'success':'error');
    DASH.sel.clear();loadDash();
  })();
}
async function kbBatchDelete(){
  const ids=[...DASH.sel].filter(id=>{const j=DASH.jobs.find(x=>x.id===id);return j&&(j.status==='success'||j.status==='failed'||j.status==='cancelled');});
  if(!ids.length){toast('请选择已完成/失败/已取消的任务','error');return;}
  if(!confirm(`确定删除选中的 ${ids.length} 个任务？将清理任务记录与磁盘产物，不可恢复！`))return;
  try{
    const r=await POST('/api/v1/jobs/delete',{job_ids:ids});
    toast(`删除完成：${r.deleted_count} 个任务已删除`,r.deleted_count>0?'success':'error');
  }catch(e){
    toast('删除失败: '+e.message,'error');
  }
  DASH.sel.clear();loadDash();
}
function cancelJob(id){
  if(!DASH.sel.size && !confirm('确定取消此任务？'))return;
  (async()=>{
    try{await fetch(S.base+'/api/v1/jobs/'+id,{method:'DELETE',headers:ah()});toast('已取消','success');loadDash();}
    catch(e){toast('取消失败: '+e.message,'error');}
  })();
}
async function deleteJob(id,btn){
  if(!confirm('确定删除此任务？将清理任务记录与磁盘产物，不可恢复！'))return;
  if(btn){btn.disabled=true;btn.textContent='删除中...';}
  try{
    const r=await POST('/api/v1/jobs/delete',{job_ids:[id]});
    toast(r.deleted_count?'已删除':'删除失败','success');
    loadDash();
  }catch(e){
    toast('删除失败: '+e.message,'error');
    if(btn){btn.disabled=false;btn.textContent='🗑 删除';}
  }
}
async function openJobModal(id){
  document.getElementById('jobModal').style.display='block';
  document.getElementById('jmContent').innerHTML='<div class="empty"><p>加载中...</p></div>';
  try{
    const j=await GET('/api/v1/jobs/'+id);
    document.getElementById('jmTitle').textContent=j.original_filename;
    const steps=[
      {l:'格式识别',done:!!j.detected_format},
      {l:'坐标系解析',done:!!j.detected_crs},
      {l:'属性处理',done:j.feature_count_input!=null},
      {l:'几何修复',done:j.feature_fixed!=null},
      {l:'脚本生成与执行',done:j.status==='success'},
    ];
    document.getElementById('jmContent').innerHTML=`
      <div style="display:flex;gap:8px;margin-bottom:20px;">${stBadge(j.status)}<span style="font-size:12px;color:var(--t1);">${j.output_format||''}</span></div>
      <div class="pipeline">${steps.map((s,i)=>{
        const cls=s.done?'done':(j.current_step&&i===steps.findIndex(x=>!x.done)?'active':'');
        return`<div class="ps ${cls}"><div class="pd">${s.done?'✓':'·'}</div><div><div class="pt">${s.l}</div><div class="pm">${cls==='done'?'完成':cls==='active'?'进行中...':'等待中'}</div></div></div>`;
      }).join('')}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:20px;">
        <div style="background:var(--bg-3);border-radius:var(--r8);padding:16px;">
          <div style="font-size:11px;color:var(--t2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;">文件信息</div>
          ${[['源CRS',crsLabel(j.detected_crs,j.detected_crs_srid,j.crs_is_custom)||j.src_crs||'-'],['目标CRS',j.dst_crs||'-'],['编码',j.detected_encoding||'-'],['格式',j.detected_format||'-']].map(([k,v])=>`<div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px;"><span style="color:var(--t1);">${k}</span><span class="mono">${esc(v)}</span></div>`).join('')}
        </div>
        <div style="background:var(--bg-3);border-radius:var(--r8);padding:16px;">
          <div style="font-size:11px;color:var(--t2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;">统计信息</div>
          ${[['输入要素',j.feature_count_input??'-'],['输出要素',j.feature_count_output??'-'],['修复要素',j.feature_fixed??'-'],['耗时',(j.duration_seconds?j.duration_seconds.toFixed(1)+'s':'-')]].map(([k,v])=>`<div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px;"><span style="color:var(--t1);">${k}</span><span class="mono">${esc(String(v))}</span></div>`).join('')}
        </div>
      </div>
      ${j.generated_script?`<div style="margin-top:16px;"><div style="font-size:12px;color:var(--t2);margin-bottom:6px;">生成脚本</div><div style="background:var(--bg-0);border-radius:var(--r8);padding:12px;font-family:var(--mono);font-size:11px;color:#c9d1d9;word-break:break-all;max-height:120px;overflow-y:auto;">${esc(j.generated_script)}</div></div>`:''}
      ${j.error_message?`<div style="margin-top:12px;padding:12px;background:var(--rdim);border-radius:var(--r8);font-size:12px;color:var(--red);">✗ ${esc(j.error_message)}</div>`:''}`;
  }catch(e){document.getElementById('jmContent').innerHTML=`<div class="empty"><p style="color:var(--red);">加载失败: ${esc(e.message)}</p></div>`;}
}

// ── Upload ────────────────────────────────────────────────
function initUpload(){
  const zone=document.getElementById('uploadZone'),inp=document.getElementById('uploadInput');
  // 全局拦截拖放默认行为：防止文件拖到页面其他区域时浏览器直接打开/导航
  // 但放行 .uz 区域的拖拽（数据标准导入弹窗等）
  window.addEventListener('dragover',e=>e.preventDefault());
  window.addEventListener('drop',e=>{
    // 如果目标在 .uz 区域内，不阻止（由 .uz 自己的 handler 处理）
    if(e.target.closest&&e.target.closest('.uz'))return;
    e.preventDefault();
  });
  zone.addEventListener('click',e=>{if(e.target.tagName!=='BUTTON'&&e.target.tagName!=='INPUT')inp.click();});
  zone.addEventListener('dragover',e=>{e.preventDefault();zone.classList.add('drag');});
  zone.addEventListener('dragleave',()=>zone.classList.remove('drag'));
  zone.addEventListener('drop',e=>{e.preventDefault();zone.classList.remove('drag');if(e.dataTransfer.files[0])pickUpFile(e.dataTransfer.files[0]);});
  inp.addEventListener('change',()=>{if(inp.files[0])pickUpFile(inp.files[0]);inp.value='';});
  document.getElementById('uploadBtn').addEventListener('click',doUpload);
  document.querySelectorAll('input[name="upMode"]').forEach(r=>r.addEventListener('change',onUpModeChange));
  // 关联标准：选标准 → 加载图层组
  document.getElementById('upLinkStdSel').addEventListener('change',onLinkStdChange);
  document.getElementById('upLinkLayerSel').addEventListener('change',onLinkLayerChange);
}

let _stdCache=null;
async function _loadStandards(){
  if(_stdCache)return _stdCache;
  try{const r=await GET('/api/v1/data-standards-v2?page_size=50');_stdCache=r.items||[];}
  catch(e){_stdCache=[];}
  return _stdCache;
}

function pickUpFile(f){
  S.upFile=f;
  S._detectedGeomType=null; S._detectedFields=null;
  const zone=document.getElementById('uploadZone');
  zone.querySelector('h3').textContent='📄 '+f.name;
  zone.querySelector('p').textContent=fsz(f.size)+' — 准备就绪';
  document.getElementById('uploadBtn').disabled=false;
  document.getElementById('uploadConfig').classList.add('show');
  document.querySelector('input[name="upMode"][value=""]').checked=true;
  onUpModeChange();
  // 用文件名自动填充图层组名和数据表名
  const stem=f.name.replace(/\.(zip|tar|gz|7z|rar)$/i,'');
  document.getElementById('upNewLayerName').value=stem;
  document.getElementById('upNewTableName').value=stem;
  _loadStandards().then(stds=>{
    const sel=document.getElementById('upLinkStdSel');
    sel.innerHTML=stds.length?'<option value="">请选择标准...</option>':'<option value="">暂无可用标准，请先创建</option>';
    stds.forEach(s=>{const o=document.createElement('option');o.value=s.id;o.textContent=s.name;sel.appendChild(o);});
    const sel2=document.getElementById('upNewStdSel');
    sel2.innerHTML=stds.length?'<option value="">请选择标准...</option>':'<option value="">暂无可用标准，请先创建</option>';
    stds.forEach(s=>{const o=document.createElement('option');o.value=s.id;o.textContent=s.name;sel2.appendChild(o);});
  });
  _showFileFields();
  // 延迟后端预览：上传文件获取字段结构（不入库，仅分析）
  _previewFileFields(f);
}

/* ── 入库模式切换 ── */
function onUpModeChange(){
  const mode=document.querySelector('input[name="upMode"]:checked').value;
  document.querySelectorAll('.uradio').forEach(el=>el.classList.remove('selected'));
  document.getElementById('upLinkPanel').style.display='none';
  document.getElementById('upNewPanel').style.display='none';
  if(mode===''){
    document.getElementById('upOpt0').classList.add('selected');
  }else if(mode==='link'){
    document.getElementById('upOpt1').classList.add('selected');
    document.getElementById('upLinkPanel').style.display='block';
  }else{
    document.getElementById('upOpt2').classList.add('selected');
    document.getElementById('upNewPanel').style.display='block';
    _showFileFields();
  }
}

/* ── 关联标准：标准 → 图层组 → 字段 ── */
async function onLinkStdChange(){
  const stdId=document.getElementById('upLinkStdSel').value;
  const layerSel=document.getElementById('upLinkLayerSel');
  const fieldsDiv=document.getElementById('upLinkFields');
  const geomRow=document.getElementById('upLinkGeomRow');
  layerSel.innerHTML='<option value="">加载中...</option>';layerSel.disabled=true;
  fieldsDiv.style.display='none';geomRow.style.display='none';
  if(!stdId){
    layerSel.innerHTML='<option value="">请先选择标准</option>';
    return;
  }
  try{
    const layers=await GET('/api/v1/data-standards-v2/'+stdId+'/layers');
    if(!layers||!layers.length){
      layerSel.innerHTML='<option value="">该标准下无图层组</option>';
      return;
    }
    layerSel.innerHTML='<option value="">请选择图层组...</option>';
    layerSel.disabled=false;
    layers.forEach(lg=>{
      const o=document.createElement('option');o.value=lg.id;o.textContent=lg.group_name;
      layerSel.appendChild(o);
    });
  }catch(e){layerSel.innerHTML='<option value="">加载失败</option>';}
}

async function onLinkLayerChange(){
  const layerId=document.getElementById('upLinkLayerSel').value;
  const fieldsDiv=document.getElementById('upLinkFields');
  const geomRow=document.getElementById('upLinkGeomRow');
  if(!layerId){fieldsDiv.style.display='none';geomRow.style.display='none';return;}
  fieldsDiv.style.display='block';geomRow.style.display='flex';
  const body=document.getElementById('upLinkFieldsBody');
  const title=document.getElementById('upLinkFieldsTitle');
  const geomBadge=document.getElementById('upLinkGeomBadge');
  body.innerHTML='<div style="font-size:12px;color:var(--t2);">加载字段...</div>';
  geomBadge.textContent='检测中...';
  try{
    const layers=await GET('/api/v1/data-standards-v2/'+document.getElementById('upLinkStdSel').value+'/layers');
    const lg=(layers||[]).find(l=>l.id===layerId);
    if(!lg||!lg.tables||!lg.tables.length){body.innerHTML='<div style="font-size:12px;color:var(--t2);">该图层组下无数据表</div>';return;}
    let allFields=[];
    const geomTypes=new Set();
    lg.tables.forEach(t=>{
      if(t.geometry_type)geomTypes.add(t.geometry_type);
      (t.fields||[]).forEach(f=>allFields.push({table:t.table_name,field:f.field_name,type:f.data_type}));
    });
    if(!allFields.length){body.innerHTML='<div style="font-size:12px;color:var(--t2);">暂未定义字段</div>';return;}
    title.textContent=`图层字段（共 ${allFields.length} 个字段）`;
    body.innerHTML=allFields.map(f=>`<div class="ufmap-row"><span class="ufmap-sn">${esc(f.table)}.${esc(f.field)}</span><span style="color:var(--t2);font-size:11px;margin-left:auto;">${esc(f.type||'')}</span></div>`).join('');
    geomBadge.textContent=geomTypes.size?Array.from(geomTypes).join(' / '):'未指定（表单）';
  }catch(e){body.innerHTML='<div style="color:var(--red);font-size:12px;">加载失败</div>';}
}

/* ── 新增标准：标准选择 + 自动检测类型 ── */
function _previewFileFields(f){
  // 异步分析文件类型（通过 slice 读取前几个字节判断格式）
  const ext=(f.name||'').split('.').pop().toLowerCase();
  const GEOM_MAP={geojson:'GeoJSON',shp:'SHP',gpkg:'GPKG',kml:'KML',kmz:'KML',dxf:'DXF',gml:'GML',gdb:'GDB'};
  const fmt=GEOM_MAP[ext]||ext.toUpperCase();
  S._detectedFmt=fmt;
  // 显示格式信息到两个 badge
  const linkBadge=document.getElementById('upLinkGeomBadge');
  const newBadge=document.getElementById('upNewGeomBadge');
  const txt=`${fmt} 格式 — 入库后自动检测几何类型`;
  if(linkBadge)linkBadge.textContent=txt;
  if(newBadge)newBadge.textContent=txt;
}

function _showFileFields(){
  const fieldsDiv=document.getElementById('upNewFields');
  const mode=document.querySelector('input[name="upMode"]:checked');
  if(!mode||mode.value!=='new'){fieldsDiv.style.display='none';return;}
  if(!S.upFile){fieldsDiv.style.display='none';return;}
  fieldsDiv.style.display='block';
  const body=document.getElementById('upNewFieldsBody');
  const title=document.getElementById('upNewFieldsTitle');
  const geomBadge=document.getElementById('upNewGeomBadge');
  // 显示文件字段预览
  if(S._detectedFields&&S._detectedFields.length){
    const geomLabel=S._detectedGeomType||'自动检测';
    title.textContent=`数据字段（${geomLabel} · ${S._detectedFields.length} 个字段）`;
    body.innerHTML=S._detectedFields.map(f=>`<div class="ufmap-row"><span class="ufmap-sn">${esc(f.name)}</span><span style="color:var(--t2);font-size:11px;margin-left:auto;">${esc(f.type||'string')}</span></div>`).join('');
    if(geomBadge&&S._detectedGeomType)geomBadge.textContent=S._detectedGeomType;
  }else{
    title.textContent='数据字段预览';
    body.innerHTML='<div style="font-size:12px;color:var(--t2);">上传后将根据数据结构自动建立字段</div>';
  }
}

/* ── 上传 ── */
async function doUpload(){
  if(!S.upFile)return;
  const btn=document.getElementById('uploadBtn'),prog=document.getElementById('uploadProgress');
  const bar=document.getElementById('upBar'),txt=document.getElementById('upTxt'),pct=document.getElementById('upPct');
  btn.disabled=true;prog.style.display='block';bar.style.width='20%';txt.textContent='上传中...';pct.textContent='20%';
  const fd=new FormData();fd.append('file',S.upFile);
  const desc=document.getElementById('uploadDesc').value.trim();
  if(desc)fd.append('description',desc);
  const mode=document.querySelector('input[name="upMode"]:checked').value;
  if(mode==='link'){
    // 关联标准：standard_id + layer_group_id
    const stdId=document.getElementById('upLinkStdSel').value;
    const layerId=document.getElementById('upLinkLayerSel').value;
    if(!stdId){toast('请选择标准','error');btn.disabled=false;prog.style.display='none';return;}
    if(!layerId){toast('请选择图层组','error');btn.disabled=false;prog.style.display='none';return;}
    fd.append('standard_id',stdId);
    fd.append('layer_group_id',layerId);
  }else if(mode==='new'){
    // 新增标准 → 自动创建 数据标准→数据层组→数据表
    const stdSelVal=document.getElementById('upNewStdSel').value;
    const layerName=document.getElementById('upNewLayerName').value.trim();
    const tableName=document.getElementById('upNewTableName').value.trim();
    if(!stdSelVal){toast('请选择所属标准','error');btn.disabled=false;prog.style.display='none';return;}
    if(!layerName){toast('请输入图层组名','error');btn.disabled=false;prog.style.display='none';return;}
    fd.append('standard_id',stdSelVal);
    fd.append('layer_group_name',layerName);
    fd.append('table_name',tableName||layerName);
    fd.append('auto_create_standard','true');
    // geometry_type 不再由前端手动选择，后端自动检测
  }
  try{
    bar.style.width='60%';pct.textContent='60%';
    const r=await POSTF('/api/v1/data/upload',fd);
    bar.style.width='100%';pct.textContent='100%';
    S.lastUpId=r.id;
    showUpResult(r);
    S.upFile=null;
    document.getElementById('uploadDesc').value='';
    const zone=document.getElementById('uploadZone');
    zone.querySelector('h3').textContent='拖拽矢量文件到此处，或点击选择';
    zone.querySelector('p').innerHTML='支持 GeoJSON / SHP / GPKG / KML / KMZ / DXF / GML / GDB，最大 <span id="uploadSizeHint">500</span> MB，压缩包自动解压';
    _resetUploadConfig();
    prog.style.display='none';loadRecent();
    if(r.warning){toast(r.warning,'warning');}else{toast('上传成功','success');}
  }catch(e){bar.style.width='0%';prog.style.display='none';toast('上传失败: '+e.message,'error');}
  btn.disabled=false;
}

function _resetUploadConfig(){
  document.getElementById('uploadConfig').classList.remove('show');
  document.querySelector('input[name="upMode"][value=""]').checked=true;
  onUpModeChange();
  document.getElementById('upNewLayerName').value='';
  document.getElementById('upNewTableName').value='';
  document.getElementById('upLinkStdSel').value='';
  document.getElementById('upLinkLayerSel').innerHTML='<option value="">请先选择标准</option>';
  document.getElementById('upLinkFields').style.display='none';
  document.getElementById('upLinkGeomRow').style.display='none';
  document.getElementById('upNewFields').style.display='none';
  const lb=document.getElementById('upLinkGeomBadge');if(lb)lb.textContent='检测中...';
  const nb=document.getElementById('upNewGeomBadge');if(nb)nb.textContent='选择文件后自动检测';
  S._detectedGeomType=null;S._detectedFields=null;
}
function showUpResult(r){
  const rc=document.getElementById('uploadResult');
  const stdInfo=r.data_standard_name?` · 关联标准: ${r.data_standard_name}`:'';
  document.getElementById('ur-name').textContent=r.original_name||r.file_name||'文件';
  document.getElementById('ur-meta').textContent=`上传成功 · ${fsz(r.file_size)}${r.extracted_count>0?' · 解压 '+r.extracted_count+' 个文件':''}${stdInfo}`;
  document.getElementById('ur-stats').innerHTML=`
    <div class="urs"><div class="v">${fsz(r.file_size)}</div><div class="l">文件大小</div></div>
    <div class="urs"><div class="v">${r.feature_count||0}</div><div class="l">要素数</div></div>
    <div class="urs"><div class="v">${crsLabel(r.detected_crs,r.detected_crs_srid,r.crs_is_custom)||'—'}</div><div class="l">坐标系</div></div>
    <div class="urs"><div class="v">${r.geometry_type||r.data_type||'—'}</div><div class="l">数据类型</div></div>`;
  // 更新自动检测 badge（上传后实际检测到了类型）
  if(r.geometry_type){
    S._detectedGeomType=r.geometry_type;
    const nb=document.getElementById('upNewGeomBadge');
    if(nb)nb.textContent=r.geometry_type+'（已检测）';
    const lb=document.getElementById('upLinkGeomBadge');
    if(lb)lb.textContent=r.geometry_type+'（已检测）';
  }
  document.getElementById('urQCBtn').onclick=()=>{
    goPage('quality');
    setTimeout(()=>{
      // 上传后自动勾选该文件
      if(r.id){
        qcSelFiles.clear();qcSelFiles.add(r.id);
        renderFileList();updateFileCnt();
      }
    },500);
  };
  document.getElementById('urTxBtn').onclick=()=>{
    goPage('transform');
    setTimeout(()=>{const s=document.getElementById('txFile');if(s&&r.id)s.value=r.id;},500);
  };
  // ── Extraction details ──
  const exDiv=document.getElementById('urExtraction');
  const exList=document.getElementById('urExtractionList');
  if(r.extraction_details&&r.extraction_details.length){
    exDiv.style.display='block';
    exList.innerHTML=r.extraction_details.map((d,i)=>{
      const typeColor={GDB:'var(--accent)',SHP:'var(--green)',GEOJSON:'var(--yellow)',CSV:'#8b5cf6','XLSX':'#8b5cf6'}[d.type]||'var(--t1)';
      const statusIcon=d.status==='ok'||d.status==='table_only'?'✅':d.status.startsWith('skipped')?'⏭':'⚠️';
      const fid=d.file_id||'';
      return `<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:var(--bg-3);border-radius:var(--r8);margin-bottom:4px;font-size:12px;">
        <span>${statusIcon}</span>
        <span style="color:${typeColor};font-weight:600;min-width:50px;">${d.type}</span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(d.name)}">${esc(d.name)}</span>
        <span style="color:var(--t2);">${d.features!=null?d.features+'要素':'—'}</span>
        ${fid?`<select class="upFileStdSel" data-fid="${fid}" style="padding:2px 6px;background:var(--bg-1);border:1px solid var(--border-hi);color:var(--t0);border-radius:var(--r6);font-size:11px;max-width:120px;">
          <option value="">无标准</option>
        </select>`:''}
      </div>`;
    }).join('');
    // 填充每个 per-file 标准下拉 + change 事件自动调 batch-classify
    _loadStandards().then(stds=>{
      exList.querySelectorAll('.upFileStdSel').forEach(sel=>{
        stds.forEach(s=>{const o=document.createElement('option');o.value=s.id;o.textContent=s.name;sel.appendChild(o);});
        sel.addEventListener('change',async function(){
          const fid=this.dataset.fid;
          const sid=this.value||null;
          if(!fid)return;
          try{
            await POST('/api/v1/data/batch-classify',{file_ids:[fid],standard_id:sid});
            toast(sid?'已关联标准':'已取消关联','success');
          }catch(e){toast('关联失败: '+e.message,'error');}
        });
      });
    });
  }else{
    exDiv.style.display='none';
    exList.innerHTML='';
  }
  rc.classList.add('show');
}
async function loadRecent(){
  const el=document.getElementById('recentList');
  try{
    const resp=await GET('/api/v1/data?page_size=8');
    const files=(resp?.items||resp||[]);
    if(!files||!files.length){el.innerHTML='<div class="empty"><div class="ei">📂</div><p>暂无上传记录</p></div>';return;}
    el.innerHTML=files.map(f=>`
      <div class="rit">
        <span style="font-size:18px;">${f.is_archive?'📦':'📄'}</span>
        <div class="rif">
          <div class="rin">${esc(f.original_name)}</div>
          <div class="rim">${fsz(f.file_size)} · ${ftm(f.created_at)}</div>
        </div>
        <div style="display:flex;gap:4px;">
          <button class="btn btn-g btn-sm" onclick="openDrawer('${esc(f.id)}','${esc(f.original_name)}')">详情</button>
          ${f.is_archive
            ?'<span style="font-size:11px;color:var(--t2);" title="压缩包无独立特征数据，请展开后对内部文件导出">ZIP</span>'
            :`<div style="display:inline-flex;align-items:center;gap:2px;"><button class="btn btn-g btn-sm" onclick="dlFile('${esc(f.id)}')" title="下载原始文件" style="padding:2px 6px;">⬇</button>${dlFmtWrap(`dlFile('${esc(f.id)}',this.value)`)}</div>`}
        </div>
        </div>
      </div>`).join('');
  }catch(e){el.innerHTML='<div class="empty"><p style="color:var(--t2);">加载失败</p></div>';}
}

// ── Data Management ───────────────────────────────────────
function initData(){
  document.getElementById('dmSearchBtn').addEventListener('click',()=>{S.dmPage=1;loadFiles();});
  document.getElementById('dmSearch').addEventListener('keydown',e=>{if(e.key==='Enter'){S.dmPage=1;loadFiles();}});
  document.getElementById('dmFmt').addEventListener('change',()=>{S.dmPage=1;loadFiles();});
  document.getElementById('dmFrom').addEventListener('change',()=>{S.dmPage=1;loadFiles();});
  document.getElementById('dmTo').addEventListener('change',()=>{S.dmPage=1;loadFiles();});
  document.getElementById('dmSizeSel').addEventListener('change',function(){
    S.dmSize=parseInt(this.value);S.dmPage=1;loadFiles();
  });
  document.getElementById('dmPrev').addEventListener('click',()=>{if(S.dmPage>1){S.dmPage--;loadFiles();}});
  document.getElementById('dmNext').addEventListener('click',()=>{S.dmPage++;loadFiles();});
  document.getElementById('dmSelAll').addEventListener('change',e=>{
    document.querySelectorAll('#dmTbody input[type="checkbox"]').forEach(cb=>{
      cb.checked=e.target.checked;
      if(e.target.checked)S.sel.add(cb.value);else S.sel.delete(cb.value);
    });updBatch();
  });
  document.getElementById('batchDelBtn').addEventListener('click',batchDel);
}
function updBatch(){
  const btn=document.getElementById('batchDelBtn');
  btn.disabled=S.sel.size===0;
  btn.textContent=S.sel.size>0?`🗑 批量删除 (${S.sel.size})`:'🗑 批量删除';
}
// 进入数据管理页时先清理旧压缩包，再加载列表
async function cleanupThenLoadFiles(){
  // 静默清理 1 天前的压缩包（临时数据），不阻塞页面加载
  fetch(S.base+'/api/v1/data/cleanup-archives?days=1',{method:'POST',headers:ah()}).catch(()=>{});
  loadFiles();
}
async function loadFiles(){
  S.dmSearch=document.getElementById('dmSearch').value.trim();
  S.dmFmt=document.getElementById('dmFmt').value;
  S.dmFrom=document.getElementById('dmFrom').value;
  S.dmTo=document.getElementById('dmTo').value;
  S.dmDataType=document.getElementById('dmDataType')?.value||'';
  S.dmStdFilter=document.getElementById('dmStdFilter')?.value||'';
  S.dmSize=parseInt(document.getElementById('dmSizeSel').value);
  const tb=document.getElementById('dmTbody');
  tb.innerHTML='<tr><td colspan="9"><div class="empty"><p style="color:var(--t2);">⏳ 加载中...</p></div></td></tr>';
  try{
    const p=new URLSearchParams({page:S.dmPage,page_size:S.dmSize});
    if(S.dmSearch)p.set('search',S.dmSearch);
    if(S.dmFmt)p.set('file_format',S.dmFmt);
    // 处理复合过滤: "geometry:Point" → data_type=geometry + geometry_type=Point
    if(S.dmDataType){
      if(S.dmDataType.includes(':')){
        const [dt,gt]=S.dmDataType.split(':');
        p.set('data_type',dt);p.set('geometry_type',gt);
      }else{
        p.set('data_type',S.dmDataType);
      }
    }
    if(S.dmStdFilter)p.set('standard_id',S.dmStdFilter);
    if(S.dmFrom)p.set('from',S.dmFrom);
    if(S.dmTo)p.set('to',S.dmTo);
    const resp=await GET('/api/v1/data?'+p);
    const raw=resp.items||resp;
    // 压缩包父记录是解压后的空壳容器，不展示（需求：压缩包解压后不保留原包）
    const items=raw.filter(f=>!f.is_archive);
    const total=Math.max(0,(resp.total!=null?resp.total:raw.length)-(raw.length-items.length));
    S.sel.clear();document.getElementById('dmSelAll').checked=false;updBatch();
    if(!items||!items.length){
      tb.innerHTML='<tr><td colspan="9"><div class="empty"><div class="ei">📭</div><p>暂无数据</p></div></td></tr>';
      document.getElementById('dmPrev').disabled=true;
      document.getElementById('dmNext').disabled=true;
      document.getElementById('dmPN').textContent='第 1 页 / 共 0 条';
      document.getElementById('dmPI').textContent='';return;
    }
    tb.innerHTML=items.map(f=>{
      const gt=f.geometry_type;
      const typeIcon=f.data_type==='geometry'?(gt==='Point'?'📍':gt==='LineString'?'〰️':gt==='Polygon'?'⬜':gt==='3d'?'🧊':'📐'):f.data_type==='table'?'📊':f.data_type==='archive'?'📦':'📄';
      const typeBadge=gt?'<span style="color:var(--cyan);font-size:11px;">'+esc(gt)+'</span>':
                       f.data_type==='table'?'<span style="color:var(--green);font-size:11px;">表单</span>':
                       f.data_type==='archive'?'<span style="color:var(--yellow);font-size:11px;">压缩包</span>':
                       '<span style="color:var(--t2);font-size:11px;">'+esc(f.data_type||'未知')+'</span>';
      // 【v2.10.5】类型计数标签组（●点 ─线 ◆面）+ 图层数角标
      const cntChips=[];
      if(f.point_count)cntChips.push(`<span title="点要素 ${f.point_count}" style="font-size:10px;color:#388bfd;background:rgba(56,139,253,.12);padding:1px 6px;border-radius:8px;white-space:nowrap;">●${f.point_count>999?(f.point_count/1000).toFixed(1)+'k':f.point_count}</span>`);
      if(f.line_count)cntChips.push(`<span title="线要素 ${f.line_count}" style="font-size:10px;color:#d29922;background:rgba(210,153,34,.12);padding:1px 6px;border-radius:8px;white-space:nowrap;">─${f.line_count>999?(f.line_count/1000).toFixed(1)+'k':f.line_count}</span>`);
      if(f.polygon_count)cntChips.push(`<span title="面要素 ${f.polygon_count}" style="font-size:10px;color:#3fb950;background:rgba(63,185,80,.12);padding:1px 6px;border-radius:8px;white-space:nowrap;">◆${f.polygon_count>999?(f.polygon_count/1000).toFixed(1)+'k':f.polygon_count}</span>`);
      const layerTag=(f.layer_count&&f.layer_count>1)?`<span title="${f.layer_count} 个图层" style="font-size:10px;color:var(--purple);background:var(--pdim);padding:1px 6px;border-radius:8px;white-space:nowrap;">▤ ${f.layer_count} 图层</span>`:'';
      const cntHtml=cntChips.length?`<div style="display:flex;gap:3px;margin-top:3px;flex-wrap:wrap;">${cntChips.join('')}${layerTag}</div>`:(layerTag?`<div style="margin-top:3px;">${layerTag}</div>`:'');
      const stdBadge=f.data_standard_name?`<span style="color:var(--purple);font-size:11px;" title="${esc(f.data_standard_name)}">🔗 ${esc(f.data_standard_name).substring(0,8)}</span>`:'<span style="color:var(--t2);font-size:11px;">—</span>';
      return `
      <tr>
        <td><input type="checkbox" value="${esc(f.id)}" onchange="toggleSel('${esc(f.id)}',this.checked)"></td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer;" onclick="openDrawer('${esc(f.id)}','${esc(f.original_name)}')" title="${esc(f.original_name)}">
          ${typeIcon} ${esc(f.original_name)}
        </td>
        <td><span style="font-size:11px;color:var(--t1);">${esc(f.file_format||'—')}</span></td>
        <td>${typeBadge}${cntHtml}</td>
        <td class="mono">${fsz(f.file_size)}</td>
        <td class="mono">${f.feature_count?f.feature_count.toLocaleString():'—'}</td>
        <td>${stdBadge}</td>
        <td style="font-size:12px;color:var(--t2);">${ftm(f.created_at)}</td>
        <td>
          <div style="display:flex;gap:3px;">
            <button class="btn btn-sm bi" onclick="rowQC('${esc(f.id)}',this)" title="质量检查" style="background:var(--cdim);color:var(--cyan);">📊</button>
            <button class="btn btn-sm bi" onclick="rowTx('${esc(f.id)}')" title="坐标转换" style="background:var(--pdim);color:var(--purple);">🔄</button>
            ${f.is_archive
              ?'<span title="压缩包无独立特征数据，请展开后对内部文件导出" style="font-size:11px;color:var(--t2);">ZIP</span>'
              :`<div style="display:inline-flex;align-items:center;gap:2px;"><button class="btn btn-sm bi" onclick="dlFile('${esc(f.id)}')" title="下载原始文件" style="background:var(--gdim);color:var(--green);">⬇</button>${dlFmtWrap(`dlFile('${esc(f.id)}',this.value)`)}</div>`}
            <button class="btn btn-sm bi" onclick="delFile('${esc(f.id)}')" title="删除" style="background:var(--rdim);color:var(--red);">🗑</button>
          </div>
          <div id="op-${esc(f.id)}" style="font-size:10px;color:var(--t1);margin-top:2px;"></div>
        </td>
      </tr>`;
    }).join('');
    const totalPages=Math.ceil(total/S.dmSize);
    document.getElementById('dmPrev').disabled=S.dmPage===1;
    document.getElementById('dmNext').disabled=S.dmPage>=totalPages||totalPages===0;
    document.getElementById('dmPN').textContent=`第 ${S.dmPage} 页 / 共 ${totalPages} 页`;
    document.getElementById('dmPI').textContent=`共 ${total} 条 · 每页 ${S.dmSize} 条`;
  }catch(e){tb.innerHTML=`<tr><td colspan="9"><div class="empty"><p style="color:var(--red);">加载失败: ${esc(e.message)}</p></div></td></tr>`;}
}
async function loadStats(){
  try{
    const s=await GET('/api/v1/data/stats');
    document.getElementById('dm-total').textContent=s.total_files||0;
    document.getElementById('dm-size').textContent=fsz(s.total_size||0);
    // 几何子类型统计
    // 【v2.10.5】优先用 feature_totals（要素总量，计数驱动，mixed 文件不漏计）
    const ft=s.feature_totals||{};
    const g=s.by_geometry_type||{};
    document.getElementById('dm-pt').textContent=ft.point??g.Point??0;
    document.getElementById('dm-ln').textContent=ft.line??g.LineString??0;
    document.getElementById('dm-pg').textContent=ft.polygon??g.Polygon??0;
    document.getElementById('dm-3d').textContent=g['3d']||0;
    document.getElementById('dm-tb').textContent=s.by_data_type?.table||0;
    document.getElementById('dm-arc').textContent=s.by_data_type?.archive||0;
  }catch(e){}
}
function toggleSel(id,v){if(v)S.sel.add(id);else S.sel.delete(id);updBatch();}
async function batchDel(){
  if(!S.sel.size)return;
  if(!confirm(`确定删除选中的 ${S.sel.size} 个文件？`))return;
  try{
    await fetch(S.base+'/api/v1/data/batch-delete',{method:'POST',headers:{...ah(),'Content-Type':'application/json'},body:JSON.stringify(Array.from(S.sel))});
    toast(`已删除 ${S.sel.size} 个文件`,'success');S.sel.clear();loadFiles();
  }catch(e){toast('批量删除失败: '+e.message,'error');}
}
async function delFile(id){
  if(!confirm('确定删除此文件？'))return;
  try{
    await fetch(S.base+'/api/v1/data/'+id,{method:'DELETE',headers:ah()});
    toast('文件已删除','success');loadFiles();
  }catch(e){toast('删除失败: '+e.message,'error');}
}
async function dlFile(id,fmt){ // v2.19.0: fmt ∈ geojson/shp/gpkg/csv/mdb；省略时后端回退原始字节下载
  fmt=fmt||'';
  try{
    const qs=fmt?('?format='+encodeURIComponent(fmt)):'';
    const r=await fetch(S.base+'/api/v1/data/'+id+'/download'+qs,{headers:ah()});
    if(!r.ok){
      const e=await r.json().catch(()=>({}));
      throw new Error(e.detail||'HTTP '+r.status);
    }
    const blob=await r.blob();
    const cd=r.headers.get('content-disposition')||'';
    const fn=cd.match(/filename\*?=(?:UTF-8'')?([^;]+)/)?.[1]?.replace(/"/g,'')||'download';
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=decodeURIComponent(fn);
    document.body.appendChild(a);a.click();document.body.removeChild(a);toast('导出成功','success');
  }catch(e){toast('导出失败: '+e.message,'error');}
}
function dlFmtWrap(onchange){ // 复用格式下拉：<select> 触发即导出并复位
  return `<select onchange="${onchange};this.value='geojson';" style="padding:2px 2px;font-size:10px;background:var(--bg1);color:var(--t1);border:1px solid var(--bd);border-radius:4px;max-width:74px;" title="选择导出格式">
    <option value="geojson">GeoJSON</option><option value="shp">SHP(ZIP)</option>
    <option value="gpkg">GPKG</option><option value="mdb">MDB</option><option value="csv">CSV</option>
  </select>`;
}
async function rowQC(id,btn){
  const div=document.getElementById('op-'+id);
  btn.disabled=true;if(div)div.innerHTML='<span style="color:var(--accent);">⏳ 质检中...</span>';
  try{
    const body=new URLSearchParams();
    const topo=buildQcTopoConfig();if(topo)body.append('topology_config',JSON.stringify(topo));
    const r=await fetch(S.base+'/api/v1/data/'+id+'/quality-check',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded',...ah()},body});
    if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.detail||'HTTP '+r.status);}
    const d=await r.json();
    // v2.20: 综合分由后端按 WEIGHTS 计算返回 score_overall，前端不再硬编码权重
    let score=d.score_overall!==undefined?d.score_overall:(d.completeness_score||0);
    const sc=(score*100).toFixed(1);
    const col=score>=.85?'var(--green)':score>=.6?'var(--yellow)':'var(--red)';
    const el=document.getElementById('qs-'+id);
    if(el)el.innerHTML=`<span style="font-family:var(--mono);font-size:12px;color:${col};">${sc}%</span>`;
    if(div)div.innerHTML=`<span style="color:var(--green);">✓ 质检完成</span>`;
  }catch(e){if(div)div.innerHTML=`<span style="color:var(--red);">✗ ${esc(e.message)}</span>`;}
  btn.disabled=false;
}
function rowTx(id){goPage('transform');setTimeout(()=>{const s=document.getElementById('txFile');if(s)s.value=id;},400);}
async function rowRepair(id,btn){
  const div=document.getElementById('op-'+id);btn.disabled=true;
  if(div)div.innerHTML='<span style="color:var(--accent);">🔧 修复中...</span>';
  try{
    const r=await fetch(S.base+'/api/v1/spatial/'+id+'/repair',{method:'POST',headers:ah()});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();
    if(div)div.innerHTML=`<span style="color:var(--green);">✓ ${d.repaired}/${d.total} 已修复</span>`;
    toast('几何修复完成','success');
  }catch(e){if(div)div.innerHTML=`<span style="color:var(--red);">✗ ${esc(e.message)}</span>`;}
  btn.disabled=false;
}

// ── File Drawer ───────────────────────────────────────────
let _dMap=null,_dGeo=null,_dFileId=null;
function openDrawer(id,name){
  _dFileId=id;
  document.getElementById('drawerTitle').textContent=name||'文件详情';
  document.getElementById('dmask').style.display='block';
  document.getElementById('drawer').classList.add('open');
  swDTab('info');
}
function closeDrawer(){
  document.getElementById('dmask').style.display='none';
  document.getElementById('drawer').classList.remove('open');
}
function swDTab(tab){
  document.querySelectorAll('.dtab').forEach(b=>b.classList.remove('active'));
  document.querySelector(`.dtab[data-tab="${tab}"]`).classList.add('active');
  ['info','map','qc','cad','ops'].forEach(t=>document.getElementById('dtab-'+t).style.display=t===tab?'block':'none');
  if(tab==='info')loadDInfo();
  if(tab==='map')loadDMap();
  if(tab==='qc')loadDQC();
  if(tab==='cad')loadDCAD();
  if(tab==='ops')loadDOps();
}
async function loadDInfo(){
  const el=document.getElementById('dtab-info');el.innerHTML='<div class="empty"><p>加载中...</p></div>';
  try{
    const m=await GET('/api/v1/data/'+_dFileId);
    const isCad=['dxf','dwg'].includes((m.file_format||'').toLowerCase());
    const cadBtn=document.getElementById('dtabCadBtn');if(cadBtn)cadBtn.style.display=isCad?'block':'none';
    const typeIcon=m.data_type==='geometry'?(m.geometry_type==='Point'?'📍':m.geometry_type==='LineString'?'〰️':m.geometry_type==='Polygon'?'⬜':m.geometry_type==='3d'?'🧊':'📐'):m.data_type==='table'?'📊':m.data_type==='archive'?'📦':'📄';
    const typeLabel=m.geometry_type||(m.data_type==='geometry'?'图形数据':m.data_type==='table'?'表单数据':m.data_type==='archive'?'压缩包':'未知');
    const stdLabel=m.data_standard_name||'未关联';
    const crsText=crsLabel(m.detected_crs,m.detected_crs_srid,m.crs_is_custom)||'未检测';
    const fields=m.field_schema||[];
    // 【v2.10.5】要素构成条（点/线/面占比，纯 CSS 堆叠条）
    const pc=m.point_count||0,lc=m.line_count||0,gc=m.polygon_count||0;
    const tot=pc+lc+gc;
    let compBar='';
    if(tot>0){
      const seg=(n,c,label)=>n?`<div title="${label} ${n} (${(n/tot*100).toFixed(1)}%)" style="width:${(n/tot*100).toFixed(2)}%;background:${c};min-width:2px;"></div>`:'';
      compBar=`<div style="margin-top:14px;background:var(--bg-3);padding:14px;border-radius:var(--r8);">
        <div style="font-size:12px;font-weight:600;color:var(--t1);margin-bottom:8px;">📊 要素构成（${tot.toLocaleString()} 条）</div>
        <div style="display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--bg-2);">
          ${seg(pc,'#388bfd','📍 点')}${seg(lc,'#d29922','〰️ 线')}${seg(gc,'#3fb950','⬜ 面')}
        </div>
        <div style="display:flex;gap:14px;margin-top:6px;font-size:11px;color:var(--t2);">
          ${pc?`<span style="color:#388bfd;">● 点 ${pc.toLocaleString()}</span>`:''}
          ${lc?`<span style="color:#d29922;">─ 线 ${lc.toLocaleString()}</span>`:''}
          ${gc?`<span style="color:#3fb950;">◆ 面 ${gc.toLocaleString()}</span>`:''}
        </div>
      </div>`;
    }
    el.innerHTML=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      ${[['文件名',m.original_name||m.file_name],['格式',m.file_format||'—'],['类型',typeIcon+' '+typeLabel],['大小',fsz(m.file_size)],['CRS',crsText],['图层数',m.layer_count||0],['要素数',m.feature_count||0],['关联标准',stdLabel],['描述',m.description||'—'],['来源',m.source_archive?'压缩包: '+m.source_archive:'直接上传'],['上传时间',ftm(m.created_at)]].map(([k,v])=>`
        <div style="background:var(--bg-3);padding:12px;border-radius:var(--r8);">
          <div style="font-size:11px;color:var(--t2);margin-bottom:4px;">${esc(k)}</div>
          <div style="font-size:13px;word-break:break-all;">${esc(String(v))}</div>
        </div>`).join('')}
    </div>
    ${compBar}
    <div id="dinfo-layers" style="margin-top:14px;"></div>
    ${fields.length?`<div style="margin-top:14px;background:var(--bg-3);padding:14px;border-radius:var(--r8);">
      <div style="font-size:12px;font-weight:600;color:var(--t1);margin-bottom:8px;">📋 字段结构（${fields.length} 个字段）</div>
      <div style="max-height:200px;overflow-y:auto;">
        <table style="width:100%;font-size:12px;border-collapse:collapse;">
          <thead><tr style="border-bottom:1px solid var(--border);"><th style="text-align:left;padding:4px 8px;color:var(--t2);">字段名</th><th style="text-align:left;padding:4px 8px;color:var(--t2);">类型</th><th style="text-align:left;padding:4px 8px;color:var(--t2);">采样值</th></tr></thead>
          <tbody>${fields.map(f=>`<tr style="border-bottom:1px solid var(--border);"><td style="padding:4px 8px;font-family:var(--mono);">${esc(f.name)}</td><td style="padding:4px 8px;color:var(--t1);">${esc(f.type)}</td><td style="padding:4px 8px;color:var(--t2);">${esc(String(f.sample||'—')).substring(0,30)}</td></tr>`).join('')}</tbody>
        </table>
      </div>
    </div>`:''}
    <div style="margin-top:14px;display:flex;gap:8px;align-items:center;">
      <button class="btn btn-p btn-sm" onclick="dlFile('${_dFileId}')">⬇ 下载</button>
      ${dlFmtWrap(`dlFile('${_dFileId}',this.value)`)}
      <button class="btn btn-d btn-sm" onclick="delFile('${_dFileId}');closeDrawer()">🗑 删除</button>
    </div>`;
    _loadDLayerTable(_dFileId);
  }catch(e){el.innerHTML=`<div class="empty"><p style="color:var(--red);">加载失败</p></div>`;}
}
// 【v2.10.5】图层分组表（来自 /structure 真实图层清单）
async function _loadDLayerTable(fid){
  const box=document.getElementById('dinfo-layers');if(!box)return;
  try{
    const st=await GET('/api/v1/data/'+fid+'/structure');
    const layers=(st.layers||[]).filter(l=>l.feature_count>0||l.name);
    if(!layers.length){box.innerHTML='';return;}
    const gtBadge=t=>{
      const up=(t||'').toUpperCase();
      if(up==='POINT')return '<span style="color:#388bfd;font-size:11px;">● 点</span>';
      if(up==='LINESTRING')return '<span style="color:#d29922;font-size:11px;">─ 线</span>';
      if(up==='POLYGON')return '<span style="color:#3fb950;font-size:11px;">◆ 面</span>';
      if(up==='MIXED')return '<span style="color:var(--purple);font-size:11px;">⬡ 混合</span>';
      return '<span style="color:var(--t2);font-size:11px;">'+esc(t||'—')+'</span>';
    };
    const cntChip=(n,c,sym)=>n?`<span style="font-size:10px;color:${c};background:${c.replace(')',',.12)').replace('rgb','rgba')};padding:1px 6px;border-radius:8px;">${sym}${n}</span>`:'';
    // 构成条（每图层行内嵌迷你堆叠条）
    const miniBar=l=>{
      const t=l.feature_count;if(!t)return '';
      const seg=(n,c)=>n?`<div style="width:${(n/t*100).toFixed(1)}%;background:${c};"></div>`:'';
      return `<div style="display:flex;width:90px;height:6px;border-radius:3px;overflow:hidden;background:var(--bg-2);">${seg(l.point_count,'#388bfd')}${seg(l.line_count,'#d29922')}${seg(l.polygon_count,'#3fb950')}</div>`;
    };
    box.innerHTML=`<div style="background:var(--bg-3);padding:14px;border-radius:var(--r8);">
      <div style="font-size:12px;font-weight:600;color:var(--t1);margin-bottom:8px;">🗂 图层清单（${layers.length} 个图层）</div>
      <table style="width:100%;font-size:12px;border-collapse:collapse;">
        <thead><tr style="border-bottom:1px solid var(--border);">
          <th style="text-align:left;padding:4px 8px;color:var(--t2);">图层名</th>
          <th style="text-align:left;padding:4px 8px;color:var(--t2);">类型</th>
          <th style="text-align:left;padding:4px 8px;color:var(--t2);">构成</th>
          <th style="text-align:right;padding:4px 8px;color:var(--t2);">要素数</th>
        </tr></thead>
        <tbody>${layers.map(l=>`<tr style="border-bottom:1px solid var(--border);">
          <td style="padding:5px 8px;font-family:var(--mono);max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(l.name)}">${esc(l.name)}</td>
          <td style="padding:5px 8px;">${gtBadge(l.geometry_type)}</td>
          <td style="padding:5px 8px;"><div style="display:flex;align-items:center;gap:8px;">${miniBar(l)}
            <span style="white-space:nowrap;color:#388bfd;font-size:10px;">${l.point_count||''}</span>
            <span style="white-space:nowrap;color:#d29922;font-size:10px;">${l.line_count||''}</span>
            <span style="white-space:nowrap;color:#3fb950;font-size:10px;">${l.polygon_count||''}</span>
          </div></td>
          <td style="padding:5px 8px;text-align:right;font-family:var(--mono);">${(l.feature_count||0).toLocaleString()}</td>
        </tr>`).join('')}</tbody>
      </table>
    </div>`;
  }catch(e){box.innerHTML='';}
}
async function loadDMap(){
  if(!_dMap){
      // v2.18.3 空间数据浏览展示配置：DWG/DXF 是模型平面坐标（数量级 1e4~1e5，
      // 如 31463,-24833，y 向上），Leaflet 默认地理 CRS 会误当经纬度 → 地图框只见
      // 一条直线/乱线。改为 L.CRS.Simple 平面坐标系 + y 翻转（DXF y↑ / Leaflet y↓），
      // 配合下方已存在的 fitBounds 按 feature 实际范围收紧展示。
      const _ModelCRS=L.Util.extend({},L.CRS.Simple,{
        transformation:new L.Transformation(1,0,-1,0)
      });
      _dMap=L.map('drawerMap',{crs:_ModelCRS}).fitWorld();
      setTimeout(()=>_dMap.invalidateSize(),100);
  }
  if(_dGeo){_dMap.removeLayer(_dGeo);_dGeo=null;}
  try{
    const geo=await GET('/api/v1/data/'+_dFileId+'/features?page_size=2000');
    if(geo.features&&geo.features.length){
      _dGeo=L.geoJSON(geo,{
        pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:5,fillColor:'#388bfd',color:'#1f6feb',weight:2,fillOpacity:.7}),
        style:f=>{const t=(f.geometry||{}).type||'';return t.includes('Polygon')?{color:'#3fb950',weight:2,fillOpacity:.1}:t.includes('Line')?{color:'#d29922',weight:2}:{};},
      }).addTo(_dMap);
      _dMap.fitBounds(_dGeo.getBounds(),{padding:[20,20]});
    }
  }catch(e){}
  setTimeout(()=>_dMap.invalidateSize(),150);
}
async function loadDInfoTypes(fid){
  // 【v2.10.5】元素可能不存在（旧版遗留锚点），全部判空防炸
  const el=document.getElementById('dinfo-type-cnt');
  const totEl=document.getElementById('dinfo-total');
  if(!el&&!totEl)return;
  try{
    const geo=await GET('/api/v1/data/'+fid+'/features?page_size=5000');
    const feats=geo.features||[];
    const cnt={Point:0,MultiPoint:0,LineString:0,MultiLineString:0,Polygon:0,MultiPolygon:0};
    feats.forEach(f=>{
      const t=(f.properties||{}).geometry_type||(f.geometry||{}).type||'';
      if(cnt[t]!==undefined)cnt[t]++;else cnt.GeometryCollection=(cnt.GeometryCollection||0)+1;
    });
    const items=[];
    if(cnt.Point||cnt.MultiPoint)items.push(`<span style="color:#388bfd;">● 点: ${cnt.Point+cnt.MultiPoint}</span>`);
    if(cnt.LineString||cnt.MultiLineString)items.push(`<span style="color:#d29922;">━ 线: ${cnt.LineString+cnt.MultiLineString}</span>`);
    if(cnt.Polygon||cnt.MultiPolygon)items.push(`<span style="color:#3fb950;">▣ 面: ${cnt.Polygon+cnt.MultiPolygon}</span>`);
    if(totEl)totEl.textContent=feats.length;
    if(el)el.innerHTML=items.join('')||'<span style="color:var(--t2);">无空间要素</span>';
  }catch(e){if(el)el.innerHTML='<span style="color:var(--t2);">加载失败</span>';}
}
async function loadDQC(){
  const el=document.getElementById('dtab-qc');el.innerHTML='<div class="empty"><p>加载中...</p></div>';
  try{
    // 【v2.10.5】质检范围面板：图层多选（数据源 = structure 真实图层清单）
    let scopePanel='';
    try{
      const st=await GET('/api/v1/data/'+_dFileId+'/structure');
      const layers=(st.layers||[]).filter(l=>l.feature_count>0);
      if(layers.length>1){
        scopePanel=`<div style="background:var(--bg-3);padding:12px;border-radius:var(--r8);margin-bottom:14px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <div style="font-size:12px;font-weight:600;color:var(--t1);">🎯 检查范围（${layers.length} 个图层）</div>
            <span style="font-size:11px;color:var(--cyan);cursor:pointer;" onclick="_qcToggleAll(this)">全选 / 清空</span>
          </div>
          <div id="dqc-layers" style="display:flex;flex-direction:column;gap:5px;max-height:180px;overflow-y:auto;">
            ${layers.map(l=>`<label style="display:flex;align-items:center;gap:8px;font-size:12px;cursor:pointer;padding:3px 6px;border-radius:6px;background:var(--bg-2);">
              <input type="checkbox" class="dqc-lyr" value="${esc(l.name)}" checked style="accent-color:var(--accent);">
              <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(l.name)}</span>
              <span style="color:var(--t2);font-size:11px;white-space:nowrap;">${(l.geometry_type==='Polygon')?'◆':l.geometry_type==='LineString'?'─':l.geometry_type==='Point'?'●':'⬡'} ${(l.feature_count||0).toLocaleString()}</span>
            </label>`).join('')}
          </div>
          <button class="btn btn-p btn-sm" onclick="runDQCScoped()" style="margin-top:10px;width:100%;">▶ 按圈定范围质检</button>
          <span id="dqc-st" style="margin-left:8px;font-size:12px;"></span>
        </div>`;
      }
    }catch(e){}
    const reports=await GET('/api/v1/quality?page_size=50');
    if(!reports||!reports.length){el.innerHTML=scopePanel+'<div class="empty"><div class="ei">📋</div><p>暂无质检报告，请先执行质检</p></div>';return;}
    const r=reports[0];
    const cls=r.completeness_score>=.85?'hi':r.completeness_score>=.6?'mid':'lo';
    // 【v2.10.5】报告圈定范围标签
    const clTag=r.checked_layers&&r.checked_layers.length
      ?`<div style="font-size:11px;color:var(--purple);margin-top:4px;">🎯 已检图层: ${r.checked_layers.map(esc).join('、')}</div>`:'';
    el.innerHTML=`${scopePanel}<div class="qsr"><div class="qsb ${cls}">${(r.completeness_score*100).toFixed(1)}%</div>
      <div><div style="font-weight:600;">${esc(r.file_name)}</div><div style="font-size:12px;color:var(--t1);">${r.total_features} 要素 · ${r.total_fields} 字段</div>${clTag}</div></div>
      <div style="font-size:12px;color:${(r.issues_count||0)>0?'var(--yellow)':'var(--green)'};">${(r.issues_count||0)>0?`⚠ ${r.issues_count} 个问题`:'✓ 未发现问题'}</div>
      <div style="margin-top:14px;display:flex;gap:8px;">
        <button class="btn btn-p btn-sm" onclick="dlAuth('${S.base}/api/v1/quality/${r.id}/html','report.html')">HTML</button>
        <button class="btn btn-g btn-sm" onclick="dlAuth('${S.base}/api/v1/quality/${r.id}/json','report.json')">JSON</button>
      </div>`;
  }catch(e){el.innerHTML=`<div class="empty"><p style="color:var(--red);">加载失败</p></div>`;}
  loadDInfoTypes(_dFileId);
}
function _qcToggleAll(tg){
  const cbs=[...document.querySelectorAll('#dqc-layers .dqc-lyr')];
  const anyOff=cbs.some(c=>!c.checked);
  cbs.forEach(c=>c.checked=anyOff);
}
async function runDQCScoped(){
  const st=document.getElementById('dqc-st');
  const sel=[...document.querySelectorAll('#dqc-layers .dqc-lyr:checked')].map(c=>c.value);
  if(!sel.length){st.textContent='请至少选择一个图层';st.style.color='var(--red)';return;}
  st.textContent='质检中...';st.style.color='var(--accent)';
  try{
    const body=new URLSearchParams();
    // 数据标准：优先文件关联标准（无标准时后端会拒绝属性检查，仅拓扑场景由用户自行关联）
    try{
      const meta=await GET('/api/v1/data/'+_dFileId);
      const sid=(meta&&meta.standard_id)||'';
      if(sid)body.append('data_standard_id',sid);
    }catch(e){}
    const topo=buildQcTopoConfig();if(topo)body.append('topology_config',JSON.stringify(topo));
    body.append('layers',JSON.stringify(sel));
    const r=await fetch(S.base+'/api/v1/data/'+_dFileId+'/quality-check',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded',...ah()},body});
    if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.detail||'HTTP '+r.status);}
    const d=await r.json();
    const score=(d.score_overall||0)*100;  // v2.20: 使用后端综合分（WEIGHTS）
    st.textContent=`✓ 完成 ${score.toFixed(1)}%（${sel.length} 图层）`;st.style.color='var(--green)';
    toast(`范围质检完成（已检图层: ${sel.join('、')}）`,'success');
    loadDQC();
  }catch(e){st.textContent='✗ '+e.message;st.style.color='var(--red)';}
}
// ── CAD 检查面板（v2.18） ──
let _cadLayerFilter=null;
async function loadDCAD(){
  const el=document.getElementById('dtab-cad');el.innerHTML='<div class="empty"><p>加载 CAD 信息...</p></div>';
  try{
    const info=await GET('/api/v1/cad/'+_dFileId+'/info');
    const layerData=await GET('/api/v1/cad/'+_dFileId+'/layers');
    const layers=layerData.layers||[];
    // 加载预览
    const prevBlob=await _t(S.base+'/api/v1/cad/'+_dFileId+'/preview?format=svg&width=480&height=360',{headers:ah()});
    let prevUrl='';
    if(prevBlob.ok){const b=await prevBlob.blob();prevUrl=URL.createObjectURL(b);}

    const rgbStr=c=>c?`rgb(${c[0]},${c[1]},${c[2]})`:'#888';
    const layerHtml=layers.map((l,i)=>{
      const clr=rgbStr(l.color_rgb);
      return`<div class="cad-layer-item${_cadLayerFilter===l.name?' active':''}"
        onclick="toggleCADLayer('${esc(l.name)}')"
        style="display:flex;align-items:center;gap:6px;padding:5px 8px;cursor:pointer;border-radius:4px;font-size:12px;
        ${_cadLayerFilter===l.name?'background:rgba(56,139,253,.12);':'color:var(--t0);'}">
        <span style="width:10px;height:10px;border-radius:2px;flex-shrink:0;background:${clr};"></span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(l.name)}">${esc(l.name)}</span>
        <span style="color:var(--t2);font-size:11px;white-space:nowrap;">${l.entity_count}</span>
      </div>`;
    }).join('');

    // 元数据概览
    const verBadge=info.version?`<span class="badge bok" style="margin-right:6px;">${esc(info.version)}</span>`:'';
    const metaHtml=`
      <div style="background:var(--bg-3);padding:12px;border-radius:var(--r8);margin-bottom:12px;">
        <div style="font-size:12px;color:var(--t2);margin-bottom:4px;">📐 CAD 元数据</div>
        <div style="font-size:13px;">${verBadge}<b>${esc(info.format)}</b> — ${esc(info.units||'未知单位')}</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px;font-size:11px;color:var(--t2);">
          <span>实体: <b style="color:var(--t0);">${info.entity_total.toLocaleString()}</b></span>
          <span>图层: <b style="color:var(--t0);">${info.layer_count}</b></span>
          <span>文本: ${info.has_text?'✅':'—'}</span>
          <span>标注: ${info.has_dimensions?'✅':'—'}</span>
          <span>图块: ${info.has_blocks?'✅':'—'}</span>
          <span>填充: ${info.has_hatches?'✅':'—'}</span>
        </div>
      </div>`;

    el.innerHTML=`
      ${prevUrl?`<div style="text-align:center;background:var(--bg-3);padding:8px;border-radius:var(--r8);margin-bottom:12px;">
        <img src="${prevUrl}" style="max-width:100%;border-radius:4px;border:1px solid var(--border);" alt="CAD Preview">
      </div>`:''}
      ${metaHtml}
      <div style="display:grid;grid-template-columns:160px 1fr;gap:12px;min-height:260px;">
        <div style="background:var(--bg-3);padding:10px;border-radius:var(--r8);overflow-y:auto;max-height:400px;">
          <div style="font-size:12px;font-weight:600;margin-bottom:8px;color:var(--t1);">🗂 图层</div>
          ${layerHtml||'<div style="color:var(--t2);font-size:12px;">无图层</div>'}
        </div>
        <div style="background:var(--bg-3);border-radius:var(--r8);overflow-y:auto;max-height:400px;">
          <div style="font-size:12px;font-weight:600;padding:10px 10px 6px;color:var(--t1);display:flex;align-items:center;justify-content:space-between;">
            <span>📦 实体列表</span>
            <span id="cadEntCount" style="font-size:11px;color:var(--t2);"></span>
          </div>
          <div id="cadEntTable" style="padding:0 10px 10px;"><div style="color:var(--t2);font-size:12px;padding:8px;">加载中...</div></div>
          <div id="cadEntPag" style="padding:0 10px 10px;"></div>
        </div>
      </div>`;
    _loadCADEntities(0);
  }catch(e){
    el.innerHTML=`<div class="empty"><p style="color:var(--red);">加载失败: ${esc(e.message)}</p></div>`;
  }
}

function toggleCADLayer(name){
  _cadLayerFilter=_cadLayerFilter===name?null:name;
  _loadCADEntities(0);
}

async function _loadCADEntities(offset){
  const lim=50;
  let url='/api/v1/cad/'+_dFileId+'/entities?offset='+offset+'&limit='+lim;
  if(_cadLayerFilter)url+='&layer='+encodeURIComponent(_cadLayerFilter);
  try{
    const pg=await GET(url);
    const tc=document.getElementById('cadEntCount');
    const tb=document.getElementById('cadEntTable');
    const pgDiv=document.getElementById('cadEntPag');
    if(tc)tc.textContent=`${pg.total} 条`;
    if(!tb)return;
    if(!pg.entities.length){
      tb.innerHTML='<div style="color:var(--t2);font-size:12px;padding:8px;">无实体</div>';
      if(pgDiv)pgDiv.innerHTML='';
      return;
    }
    const typeIcon=t=>{
      const u=(t||'').toUpperCase();
      if(u==='LINE'||u==='LWPOLYLINE'||u==='ARC'||u==='CIRCLE'||u==='SPLINE')return '〰️';
      if(u==='TEXT'||u==='MTEXT'||u==='DIMENSION'||u==='LEADER')return '📝';
      if(u==='HATCH'||u==='SOLID')return '⬜';
      if(u==='INSERT')return '📦';
      return '•';
    };
    tb.innerHTML=`<table style="width:100%;font-size:11px;border-collapse:collapse;">
      <thead><tr style="border-bottom:1px solid var(--border);text-align:left;">
        <th style="padding:4px 6px;color:var(--t2);"></th>
        <th style="padding:4px 6px;color:var(--t2);">类型</th>
        <th style="padding:4px 6px;color:var(--t2);">图层</th>
        <th style="padding:4px 6px;color:var(--t2);">长度</th>
        <th style="padding:4px 6px;color:var(--t2);">文本</th>
      </tr></thead>
      <tbody>${pg.entities.map(e=>{
        const sp=e.start_point?`(${e.start_point[0].toFixed(1)},${e.start_point[1].toFixed(1)})`:'';
        const ep=e.end_point?`(${e.end_point[0].toFixed(1)},${e.end_point[1].toFixed(1)})`:'';
        return`<tr style="border-bottom:1px solid var(--border);cursor:pointer;"
          title="句柄 ${esc(e.handle)}\n起点${sp} 终点${ep}">
          <td style="padding:4px 6px;">${typeIcon(e.entity_type)}</td>
          <td style="padding:4px 6px;font-family:var(--mono);">${esc(e.entity_type)}</td>
          <td style="padding:4px 6px;">${esc(e.layer)}</td>
          <td style="padding:4px 6px;color:var(--t2);">${e.length!=null?e.length.toFixed(2):'—'}</td>
          <td style="padding:4px 6px;color:var(--t2);max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(e.text_content||'')}</td>
        </tr>`;
      }).join('')}</tbody></table>`;
    if(pgDiv){
      const tp=Math.ceil(pg.total/lim),cp=Math.floor(pg.offset/lim)+1;
      pgDiv.innerHTML=`<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--t2);">
        <button class="btn btn-sm" ${pg.offset===0?'disabled':''} onclick="_loadCADEntities(${pg.offset-lim})">‹</button>
        <span>${cp}/${tp}</span>
        <button class="btn btn-sm" ${pg.offset+lim>=pg.total?'disabled':''} onclick="_loadCADEntities(${pg.offset+lim})">›</button>
      </div>`;
    }
  }catch(e){
    const tb=document.getElementById('cadEntTable');
    if(tb)tb.innerHTML=`<div style="color:var(--red);font-size:12px;padding:8px;">${esc(e.message)}</div>`;
  }
}

function loadDOps(){
  const el=document.getElementById('dtab-ops');
  el.innerHTML=`
    <div style="margin-bottom:20px;">
      <div style="font-size:13px;font-weight:600;margin-bottom:12px;color:var(--t1);">🔄 坐标转换</div>
      <div class="frow" style="margin-bottom:10px;">
        <div class="fg"><label>源坐标系</label>
          <div class="crs-combo">
            <select id="dop-src-sel" class="crs-sel" onchange="onDopCRSSel('src')"><option value="">-- 选择 --</option></select>
            <input type="text" id="dop-src" value="EPSG:4326" placeholder="或手动输入" oninput="onDopCRSInput('src')">
          </div>
        </div>
        <div class="fg"><label>目标坐标系</label>
          <div class="crs-combo">
            <select id="dop-dst-sel" class="crs-sel" onchange="onDopCRSSel('dst')"><option value="">-- 选择 --</option></select>
            <input type="text" id="dop-dst" value="EPSG:4490" placeholder="或手动输入" oninput="onDopCRSInput('dst')">
          </div>
        </div>
      </div>
      <button class="btn btn-p btn-sm" onclick="runDTx()">执行坐标转换</button>
      <span id="dop-tx-st" style="margin-left:10px;font-size:12px;"></span>
    </div>
    <div style="border-top:1px solid var(--border);padding-top:16px;">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px;color:var(--t1);">🔧 几何修复</div>
      <p style="font-size:12px;color:var(--t2);margin-bottom:10px;">执行 buffer(0) + make_valid 自动修复无效几何</p>
      <button class="btn btn-p btn-sm" onclick="runDRep()">执行几何修复</button>
      <span id="dop-rep-st" style="margin-left:10px;font-size:12px;"></span>
    </div>`;
  // 填充下载面板 CRS 下拉
  setTimeout(()=>{
    ['dop-src-sel','dop-dst-sel'].forEach(selId=>{
      const sel=document.getElementById(selId);
      if(!sel||!_crsCatalog) return;
      while(sel.options.length>1) sel.remove(1);
      _crsCatalog.forEach(grp=>{
        const og=document.createElement('optgroup');
        og.label=grp.group;
        grp.items.forEach(it=>og.appendChild(new Option(it.name,it.code)));
        sel.appendChild(og);
      });
    });
  },100);
}
function onDopCRSSel(which){
  const pre=which==='src'?'dop-src':'dop-dst';
  const sel=document.getElementById(pre+'-sel');
  const inp=document.getElementById(pre);
  if(sel.value){inp.value=sel.value;}
}
function onDopCRSInput(which){
  const pre=which==='src'?'dop-src':'dop-dst';
  const sel=document.getElementById(pre+'-sel');
  const inp=document.getElementById(pre);
  const v=inp.value.trim();
  let matched=false;
  for(let i=0;i<sel.options.length;i++){
    if(sel.options[i].value===v){sel.selectedIndex=i;matched=true;break;}
  }
  if(!matched) sel.selectedIndex=0;
}
async function runDTx(){
  const src=resCRS(document.getElementById('dop-src').value);
  const dst=resCRS(document.getElementById('dop-dst').value);
  const st=document.getElementById('dop-tx-st');
  st.textContent='转换中...';st.style.color='var(--accent)';
  try{
    const r=await fetch(`${S.base}/api/v1/spatial/${_dFileId}/transform?source_crs=${encodeURIComponent(src)}&target_crs=${encodeURIComponent(dst)}`,{method:'POST',headers:ah()});
    if(!r.ok)throw new Error((await r.json().catch(()=>({}))).detail||'HTTP '+r.status);
    const d=await r.json();
    st.textContent=`✓ ${d.transformed}/${d.total} → ${d.target_crs}`;st.style.color='var(--green)';
    addTxHist(src,dst,d);
  }catch(e){st.textContent='✗ '+e.message;st.style.color='var(--red)';}
}
async function runDRep(){
  const st=document.getElementById('dop-rep-st');st.textContent='修复中...';st.style.color='var(--accent)';
  try{
    const r=await fetch(`${S.base}/api/v1/spatial/${_dFileId}/repair`,{method:'POST',headers:ah()});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();
    st.textContent=`✓ ${d.repaired}/${d.total} 已修复`;st.style.color='var(--green)';
  }catch(e){st.textContent='✗ '+e.message;st.style.color='var(--red)';}
}

// ── Quality Check v3（紧凑布局 · 真实接口）────────────────────
// 拓扑规则（与后端 topology_checker.ALL_RULES 对齐，tolerances 默认与 TopologyConfig 一致）
const TOPO_RULES=[
  {id:'t01',name:'empty_geometry',label:'空几何',desc:'检测几何为空的要素',on:true},
  {id:'t02',name:'self_intersection',label:'自相交',desc:'检测线/面自相交',on:true},
  {id:'t03',name:'max_area',label:'极大面',desc:'检测面积超大的异常面',on:true,tol:{k:'max_area',v:'10000',u:'km²',mult:1e6,d:1e10}},
  {id:'t04',name:'min_area',label:'极小面',desc:'检测面积过小的面',on:true,tol:{k:'min_area',v:'0.01',u:'m²',mult:1,d:0.01}},
  {id:'t05',name:'short_line',label:'微短线',desc:'检测过短的线段',on:true,tol:{k:'min_length',v:'0.001',u:'m',mult:1,d:0.001}},
  {id:'t06',name:'polygon_overlap',label:'面重叠',desc:'检测面之间的重叠',on:true,tol:{k:'overlap_tolerance',v:'0.0001',u:'m',mult:1,d:0.0001}},
  {id:'t07',name:'point_overlap',label:'点重叠',desc:'检测点的重叠',on:true,tol:{k:'point_distance',v:'0.001',u:'m',mult:1,d:0.001}},
  {id:'t08',name:'line_overlap',label:'线重叠',desc:'检测线的重叠',on:true,tol:{k:'overlap_tolerance',v:'0.0001',u:'m',mult:1,d:0.0001}},
  {id:'t09',name:'polygon_gap',label:'面裂隙',desc:'检测面之间的裂隙',on:false,tol:{k:'gap_threshold',v:'0.001',u:'m',mult:1,d:0.001}},
  {id:'t10',name:'line_connectivity',label:'线连通',desc:'检测线的连通性',on:false},
  {id:'t11',name:'irregular_polygon',label:'不规则面',desc:'检测不规则多边形',on:false,tol:{k:'convexity_threshold',v:'0.3',u:'',mult:1,d:0.3}},
  {id:'t12',name:'ring_direction',label:'环方向',desc:'检测面环方向',on:true},
  {id:'t13',name:'multipart_geometry',label:'多部件',desc:'检测多部件几何',on:true},
];
// 属性结构检查（标准驱动，A1-A9 与 attribute_checker 对齐）
const ATTR_CHECKS=[
  {code:'31010001',name:'必要图层检查'},
  {code:'31010002',name:'要素类型正确性'},
  {code:'31010003',name:'属性结构正确性'},
  {code:'31010004',name:'字段必填性'},
  {code:'31010005',name:'属性值域控制'},
  {code:'31010006',name:'字段唯一值'},
  {code:'31010007',name:'必要图层不为空'},
  {code:'31010008',name:'字段值小数位'},
  {code:'31010009',name:'字段长度限制'},
];
// 规则类型标签映射（quality-rules）
const QC_RULE_TYPE_LBL={NOT_NULL:'必填',VALUE_IN_SET:'值域',TYPE_MATCH:'类型',LENGTH_LIMIT:'长度',UNIQUE:'唯一',RANGE:'范围',FORMAT:'格式'};

const qcTopoState={};
// 按规则默认值初始化（t09/t10/t11 默认关闭，其余开启）
TOPO_RULES.forEach(r=>{qcTopoState[r.id]=r.on!==false;});
let qcAllReports=[];
let qcStdMap={};
let qcFiles=[];
let qcSelFiles=new Set();   // 多选文件 ID 集合
let qcRules=[];
let qcAttrOn=true;
let qcPlanName='默认';
let qcCurrentPlanId='';     // 当前加载的方案 ID

function qcCntOn(){
  return TOPO_RULES.filter(r=>qcTopoState[r.id]!==false).length+(qcAttrOn?ATTR_CHECKS.length:0);
}

// ═══ 文件多选列表 ═══
async function loadStoredSel(){
  try{
    const files=await GET('/api/v1/data?page_size=200');
    qcFiles=(files?.items||files||[]).filter(f=>!f.is_archive);
    renderFileList();
    updateFileCnt();
  }catch(e){}
}
function renderFileList(){
  const el=document.getElementById('qcFileList');
  if(!el)return;
  if(!qcFiles.length){el.innerHTML='<div class="qc-empty">暂无已入库文件</div>';return;}
  el.innerHTML=qcFiles.map(f=>{
    const typeIcon=f.data_type==='geometry'?'📐':f.data_type==='table'?'📊':'📄';
    const isOn=qcSelFiles.has(f.id);
    return`<div class="qc-fi${isOn?' sl':''}" onclick="qcToggleFile('${f.id}',this)" title="${esc(f.original_name)}">
      <span class="ck${isOn?' on':''}">${isOn?'✓':''}</span>
      <span class="nm">${esc(f.layer_name||f.original_name)}</span>
      <span class="ty">${typeIcon} ${fsz(f.file_size)}</span>
    </div>`;
  }).join('');
  // 更新全选 checkbox 状态
  const allCb=document.getElementById('qcSelAll');
  if(allCb)allCb.checked=qcSelFiles.size===qcFiles.length&&qcFiles.length>0;
}
function qcToggleFile(id,el){
  if(qcSelFiles.has(id))qcSelFiles.delete(id);else qcSelFiles.add(id);
  updateFileCnt();
  renderFileList();
  applyFilesStandard();
}
function qcToggleAllFiles(){
  const allCb=document.getElementById('qcSelAll');
  if(allCb&&allCb.checked){qcFiles.forEach(f=>qcSelFiles.add(f.id));}
  else{qcSelFiles.clear();}
  updateFileCnt();
  renderFileList();
  applyFilesStandard();
}
function updateFileCnt(){
  const fc=document.getElementById('qcFileCnt');if(fc)fc.textContent=qcSelFiles.size;
  const sfc=document.getElementById('qcSelFileCnt');if(sfc)sfc.textContent=qcSelFiles.size;
  const si=document.getElementById('qcSelInfo');if(si)si.textContent='已选 '+qcSelFiles.size;
  const cnt2=document.getElementById('qcCnt2');if(cnt2)cnt2.textContent=qcCntOn();
}
// 多文件时自动回填关联标准（取交集或首个）
function applyFilesStandard(){
  if(qcSelFiles.size===0)return;
  const selIds=[...qcSelFiles];
  const stdSel=document.getElementById('qcStd2');
  if(!stdSel)return;
  // 如果已有关联标准，跳过
  if(stdSel.value)return;
  // 取首个文件的标准
  const first=qcFiles.find(f=>f.id===selIds[0]);
  if(first&&first.standard_id){
    stdSel.value=first.standard_id;
    S._qcStd2=first.standard_id;
    toast('已按文件关联标准自动选择：'+(qcStdMap[first.standard_id]||'标准'),'info');
    onQcStdChange2();
  }
}

// ═══ 配置渲染（Tab 布局） ═══
let qcCfgActiveTab='attr';
function qcCfgTab(tab,el){
  qcCfgActiveTab=tab;
  document.querySelectorAll('.qc-cfg-tab').forEach(t=>t.classList.remove('on'));
  if(el)el.classList.add('on');
  document.querySelectorAll('.qc-tab-panel').forEach(p=>p.classList.remove('on'));
  const panel=document.getElementById('tab-'+tab);
  if(panel)panel.classList.add('on');
}
function qcRenderCfg(){
  // 属性检查项
  const attrEl=document.getElementById('qcAttrItems');
  const attrCnt=document.getElementById('qcAttrCnt');
  if(attrEl){
    const hasStd=!!(document.getElementById('qcStd2')?.value);
    attrEl.innerHTML=ATTR_CHECKS.map(c=>{
      const on=qcAttrOn;
      const ck=on?'on':'';
      return`<span class="qc-ri${hasStd?'':' ro'}" title="${esc(c.name)}">
        <span class="ck${ck}" onclick="${hasStd?'qcTogAttr()':''}">${ck?'✓':''}</span>
        <span class="lb" onclick="${hasStd?'qcTogAttr()':''}">${esc(c.name)}${hasStd?'':' <span class="qc-sl">需选标准</span>'}</span>
      </span>`;
    }).join('');
    if(attrCnt)attrCnt.textContent=qcAttrOn?'9/9':'0/9';
  }
  // 拓扑检查项
  const topoEl=document.getElementById('qcTopoItems');
  const topoCnt=document.getElementById('qcTopoCnt');
  if(topoEl){
    topoEl.innerHTML=TOPO_RULES.map(r=>{
      const on=qcTopoState[r.id]!==false;
      const ck=on?'on':'';
      let pr='';
      if(r.tol){
        pr=`<div class="pr"><input type="text" id="qc-tol-${r.id}" value="${r.tol.v}" onclick="event.stopPropagation()"><span class="qc-u">${r.tol.u}</span></div>`;
      }
      return`<span class="qc-ri" title="${esc(r.desc||r.label)}">
        <span class="ck${ck}" onclick="qcTogTopo('${r.id}')">${ck?'✓':''}</span>
        <span class="lb" onclick="qcTogTopo('${r.id}')">${esc(r.label)}</span>${pr}
      </span>`;
    }).join('');
    const topoOn=TOPO_RULES.filter(r=>qcTopoState[r.id]!==false).length;
    if(topoCnt)topoCnt.textContent=topoOn+'/'+TOPO_RULES.length;
  }
  // 规则库
  const ruleEl=document.getElementById('qcRuleItems');
  const ruleCnt=document.getElementById('qcRuleCnt');
  const genBtn=document.getElementById('qcGenBtn');
  const hasStd=!!(document.getElementById('qcStd2')?.value);
  if(ruleEl){
    if(!hasStd){
      ruleEl.innerHTML='<span class="qc-cfg-note">未关联标准，规则库为空</span>';
    }else if(!qcRules.length){
      ruleEl.innerHTML='<span class="qc-cfg-note">暂无字段规则，点击「生成」由标准字段自动生成</span>';
    }else{
      ruleEl.innerHTML=qcRules.map(ru=>{
        const t=QC_RULE_TYPE_LBL[ru.rule_type]||ru.rule_type;
        const sev=ru.severity==='error'?'<span style="color:var(--red);font-size:10px;">⚠</span>':'';
        return`<span class="qc-ri ro" title="${esc(ru.rule_name)}">${sev}<span class="lb">${esc(ru.field_pattern||ru.rule_name)}<span class="qc-sl">${esc(t)}</span></span></span>`;
      }).join('');
    }
    if(ruleCnt)ruleCnt.textContent=qcRules.length?'('+qcRules.length+')':'';
  }
  if(genBtn)genBtn.style.display=hasStd?'inline-flex':'none';
  // 计数
  const total=qcCntOn();
  const c2=document.getElementById('qcCnt2');if(c2)c2.textContent=total;
}
function qcTogAttr(){
  const hasStd=!!(document.getElementById('qcStd2')?.value);
  if(!hasStd){toast('属性检查由标准驱动，请先选择数据标准','warning');return;}
  qcAttrOn=!qcAttrOn;
  qcRenderCfg();
}
function qcTogTopo(id){qcTopoState[id]=!qcTopoState[id];qcRenderCfg();}
// 属性全选/取消
function qcTogAttrAll(v){
  const hasStd=!!(document.getElementById('qcStd2')?.value);
  if(!hasStd){toast('属性检查由标准驱动，请先选择数据标准','warning');return;}
  qcAttrOn=v;
  qcRenderCfg();
}
// 拓扑全选/取消
function qcTogTopoAll(v){
  TOPO_RULES.forEach(r=>{qcTopoState[r.id]=v;});
  qcRenderCfg();
}
// 全部（属性+拓扑）选/取消
function qcRtAll(v){
  qcTogAttrAll(v);
  qcTogTopoAll(v);
}
// ═══ 标准规则库（quality-rules） ═══
async function loadQcRules(){
  try{
    const list=await GET('/api/v1/quality-rules?page_size=200');
    qcRules=Array.isArray(list)?list:[];
  }catch(e){qcRules=[];}
  qcRenderCfg();
}
async function qcGenRules(){
  const std=document.getElementById('qcStd2')?.value;
  if(!std){toast('请先选择数据标准','error');return;}
  try{
    await POST('/api/v1/quality-rules/generate/'+std,{});
    toast('规则生成成功','success');
    loadQcRules();
  }catch(e){toast('生成失败: '+e.message,'error');}
}

// ═══ 标准联动 ═══
async function onQcStdChange2(){
  const std=document.getElementById('qcStd2')?.value||'';
  const tag=document.getElementById('qcStdTag');
  if(tag){
    if(std&&qcStdMap[std]){tag.textContent='📏 '+qcStdMap[std];tag.classList.add('on');}
    else{tag.textContent='';tag.classList.remove('on');}
  }
  await Promise.all([loadQcRules(), loadQcStdOverview(std)]);
  qcRenderCfg();
}

// ═══ 标准概览（给质检页面展示字段/字典） ═══
let qcStdOverviewData=null;
async function loadQcStdOverview(stdId){
  const body=document.getElementById('qcStdOverviewBody');
  if(!body)return;
  if(!stdId){
    body.innerHTML='<span class="qc-cfg-note">选择标准后显示概览</span>';
    qcStdOverviewData=null;
    return;
  }
  try{
    const data=await GET('/api/v1/data-standards-v2/'+stdId+'/qc-summary');
    qcStdOverviewData=data;
    body.innerHTML=renderQcStdOverview(data);
  }catch(e){
    body.innerHTML='<span class="qc-cfg-note">加载失败</span>';
    qcStdOverviewData=null;
  }
}
function renderQcStdOverview(d){
  let h='';
  // 字典列表
  const allDicts=[...(d.standard_dictionaries||[]),...(d.global_dictionaries||[])];
  if(allDicts.length){
    h+='<div class="qc-std-dicts"><div class="qc-std-dicts-h">字典 ('+allDicts.length+')</div>';
    allDicts.forEach(dic=>{
      const glb=dic.scope==='global'?'<span class="glb">全局</span>':'';
      h+='<span class="qc-std-dict-chip">'+esc(dic.dict_name)+' <span class="cnt">'+dic.item_count+'项</span>'+glb+'</span>';
    });
    h+='</div>';
  }
  // 表→字段
  if(d.tables&&d.tables.length){
    h+='<table class="qc-std-tbl"><thead><tr><th>表</th><th>字段</th><th>类型</th><th>约束</th><th>字典</th><th></th></tr></thead><tbody>';
    // 构造可用字典 options
    const dictOpts=allDicts.map(dic=>'<option value="'+dic.dict_id+'">'+esc(dic.dict_name)+' ('+dic.item_count+'项)</option>').join('');
    d.tables.forEach(t=>{
      t.fields.forEach((f,i)=>{
        const typeTag=f.data_type||'string';
        const lenTag=f.length?'<span class="qc-td-tag" style="background:rgba(156,163,175,.1);color:#9ca3af;">'+f.length+'</span>':'';
        const reqTag=f.is_required?'<span class="qc-td-tag req">必填</span>':(f.nullable===false?'<span class="qc-td-tag req">NOT NULL</span>':'');
        let dictCell='';
        if(f.dict_links&&f.dict_links.length){
          dictCell=f.dict_links.map(dl=>'<span class="qc-td-tag dict">'+esc(dl.dict_name)+'<span class="cnt">('+dl.item_count+')</span></span>').join('');
        }else{
          dictCell='<span class="qc-td-tag nolink">无关联</span>';
        }
        // 关联按钮：显示下拉选择字典
        const linkBtn=allDicts.length
          ?'<select class="qc-dict-sel" id="qcdict-'+f.field_id+'" onchange="qcLinkDict(\''+f.field_id+'\',this.value)" style="font-size:10px;padding:1px 4px;border-radius:3px;border:1px solid var(--border-hi);background:var(--bg-0);color:var(--t1);"><option value="">+ 关联</option>'+dictOpts+'</select>'
          :'';
        if(i===0){
          h+='<tr><td rowspan="'+t.fields.length+'" style="font-weight:600;vertical-align:top;">'+esc(t.table_name)+'</td>';
        }else{h+='<tr>';}
        h+='<td>'+esc(f.field_name)+'</td>';
        h+='<td>'+typeTag+' '+lenTag+'</td>';
        h+='<td>'+reqTag+'</td>';
        h+='<td>'+dictCell+'</td>';
        h+='<td style="white-space:nowrap;">'+linkBtn+'</td></tr>';
      });
    });
    h+='</tbody></table>';
  }else{
    h+='<div class="qc-cfg-note">标准暂无表定义</div>';
  }
  return h;
}

// ═══ 快捷关联字段↔字典 ═══
async function qcLinkDict(fieldId, dictId){
  if(!dictId)return;
  try{
    await POST('/api/v1/data-standards-v2/fields/'+fieldId+'/dict-links',{dict_id:dictId, is_primary:true});
    toast('关联成功','success');
    // 刷新概览
    const std=document.getElementById('qcStd2')?.value;
    if(std)await loadQcStdOverview(std);
  }catch(e){toast('关联失败: '+e.message,'error');}
}

// 构造拓扑配置（读阈值输入框）
function buildQcTopoConfig(){
  const enabled=TOPO_RULES.filter(r=>qcTopoState[r.id]!==false).map(r=>r.name);
  if(!enabled.length)return null;
  const tolerances={};
  TOPO_RULES.forEach(r=>{
    if(r.tol){
      const inp=document.getElementById('qc-tol-'+r.id);
      const v=inp?parseFloat(inp.value):NaN;
      const mult=r.tol.mult||1;            // 显示单位 → 米制（km²→×1e6，m/无单位→×1）
      const raw=isNaN(v)?NaN:v*mult;       // 发送给后端的值统一为米制
      const dRaw=r.tol.d;                  // d 已是米制默认
      if(!isNaN(raw)&&Math.abs(raw-dRaw)>1e-12)tolerances[r.tol.k]=raw;
    }
  });
  return{enabled_rules:enabled, tolerances};
}

// ═══ 对已入库文件质检（支持多文件批量） ═══
async function doQcStored(){
  const fileIds=[...qcSelFiles];
  if(!fileIds.length){toast('请先勾选至少一个已入库文件','error');return;}
  const std=(qcAttrOn)?(document.getElementById('qcStd2')?.value||''):'';
  const topo=buildQcTopoConfig();
  if(!std&&!topo){toast('未选择标准且未启用任何检查项','warning');return;}
  const btn=document.getElementById('qcExecBtn')||document.querySelector('.qc-go');
  const origText=btn?.textContent;
  if(btn){btn.textContent='⏳ 检查中...';btn.disabled=true;}
  let ok=0,fail=0;
  try{
    for(const fileId of fileIds){
      try{
        const body={};
        if(std)body.standard_id=std;
        if(topo)body.topology_config=topo;
        const f=(qcFiles||[]).find(x=>x.id===fileId);
        if(f&&f.standard_table_id&&(!std||std===f.standard_id))body.standard_table_id=f.standard_table_id;
        const r=await fetch(S.base+'/api/v1/quality/check?file_id='+encodeURIComponent(fileId),{method:'POST',headers:{'Content-Type':'application/json',...ah()},body:JSON.stringify(body)});
        if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(e.detail||'HTTP '+r.status);}
        const d=await r.json();
        ok++;
        toast(`✓ ${f?.original_name||fileId}：${d.total_violations||0} 个问题`,'success');
      }catch(e){
        fail++;
        const m=e.message||'';
        if(m.includes('feature_count=0')){
          toast('✗ 该文件无图形要素','error');
        }else{
          toast('✗ 检查失败: '+m,'error');
        }
      }
    }
    if(fileIds.length>1)toast(`批量完成：${ok} 成功，${fail} 失败`,ok>0?'success':'error');
    await loadQCReports();
  }finally{
    if(btn){btn.textContent=origText;btn.disabled=false;}
  }
}

// ═══ 方案 CRUD（后端数据库） ═══
// 加载方案列表到下拉框
async function loadPlans(){
  try{
    const resp=await GET('/api/v1/quality-plans');
    const plans=resp?.items||[];
    const sel=document.getElementById('qcPlanSel');
    if(sel){
      sel.innerHTML='<option value="">默认方案</option>';
      plans.forEach(p=>{
        const opt=new Option(p.name,p.id);
        if(p.id===qcCurrentPlanId)opt.selected=true;
        sel.appendChild(opt);
      });
    }
  }catch(e){}
}
// 选择方案后加载
async function qcLoadPlanById(){
  const sel=document.getElementById('qcPlanSel');
  const planId=sel?.value||'';
  if(!planId){
    // 加载默认
    qcPlanName='默认';qcCurrentPlanId='';
    qcAttrOn=true;
    TOPO_RULES.forEach(r=>{qcTopoState[r.id]=r.on!==false;});
    qcRenderCfg();
    return;
  }
  try{
    const plan=await GET('/api/v1/quality-plans/'+planId);
    qcPlanName=plan.name;qcCurrentPlanId=plan.id;
    qcAttrOn=plan.attr_enabled!==false;
    if(plan.topo_config?.enabled_rules){
      const enabled=new Set(plan.topo_config.enabled_rules);
      TOPO_RULES.forEach(r=>{qcTopoState[r.id]=enabled.has(r.name);});
    }
    if(plan.topo_config?.tolerances){
      TOPO_RULES.forEach(r=>{
        if(r.tol&&plan.topo_config.tolerances[r.tol.k]!==undefined){
          const inp=document.getElementById('qc-tol-'+r.id);
          const mult=r.tol.mult||1;
          // 方案存储的是米制值，按显示单位还原（如 m²→km²）
          if(inp)inp.value=plan.topo_config.tolerances[r.tol.k]/mult;
        }
      });
    }
    qcRenderCfg();
    toast('已加载方案: '+plan.name,'info');
  }catch(e){toast('加载方案失败','error');}
}
// 显示保存弹窗
function qcShowSaveDialog(){
  const modal=document.getElementById('qcSaveModal');
  const nameInput=document.getElementById('qcPlanNameInput');
  const descInput=document.getElementById('qcPlanDescInput');
  const result=document.getElementById('qcSaveResult');
  if(nameInput)nameInput.value=qcPlanName==='默认'?'':qcPlanName;
  if(descInput)descInput.value='';
  if(result)result.innerHTML='';
  if(modal)modal.style.display='block';
  if(nameInput)nameInput.focus();
}
function qcHideSaveDialog(){
  const modal=document.getElementById('qcSaveModal');
  if(modal)modal.style.display='none';
}
// 保存方案到后端
async function qcSavePlan(){
  const nameInput=document.getElementById('qcPlanNameInput');
  const descInput=document.getElementById('qcPlanDescInput');
  const result=document.getElementById('qcSaveResult');
  const name=(nameInput?.value||'').trim();
  if(!name){if(result)result.innerHTML='<span style="color:var(--red);font-size:11px;">请输入方案名称</span>';return;}
  const topoConfig=buildQcTopoConfig()||{enabled_rules:[],tolerances:{}};
  const std=document.getElementById('qcStd2')?.value||null;
  try{
    if(qcCurrentPlanId){
      // 更新已有方案
      await PUT('/api/v1/quality-plans/'+qcCurrentPlanId,{
        name,description:descInput?.value||null,
        attr_enabled:qcAttrOn,topo_config:topoConfig,standard_id:std,
      });
      qcPlanName=name;
    }else{
      // 创建新方案
      const resp=await POST('/api/v1/quality-plans',{
        name,description:descInput?.value||null,
        attr_enabled:qcAttrOn,topo_config:topoConfig,standard_id:std,
      });
      qcCurrentPlanId=resp.id;qcPlanName=name;
    }
    if(result)result.innerHTML='<span style="color:var(--green);font-size:11px;">✓ 已保存</span>';
    toast('方案「'+name+'」已保存','success');
    await loadPlans();
    setTimeout(()=>qcHideSaveDialog(),800);
  }catch(e){
    const msg=e.message||'保存失败';
    if(result)result.innerHTML='<span style="color:var(--red);font-size:11px;">✗ '+esc(msg)+'</span>';
  }
}
// 导出配置（文本文件）
function qcExportCfg(){
  const rows=['# 数据检查配置导出','标准: '+((document.getElementById('qcStd2')?.value&&qcStdMap[document.getElementById('qcStd2').value])||'不关联')];
  rows.push('属性结构检查: '+(qcAttrOn?'启用':'跳过'));
  rows.push('拓扑规则:');
  TOPO_RULES.forEach(r=>{
    if(qcTopoState[r.id]!==false){
      const inp=document.getElementById('qc-tol-'+r.id);
      rows.push('  - '+r.name+(inp?' = '+inp.value+(r.tol?r.tol.u:''):''));
    }
  });
  const blob=new Blob([rows.join('\n')],{type:'text/plain;charset=utf-8'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='qc_config.txt';
  document.body.appendChild(a);a.click();document.body.removeChild(a);
  toast('配置已导出','success');
}

// 初始化（被 goPage('quality') 调用）
function initQcPage(){
  loadStoredSel();loadStdsSel();loadQCReports();loadQcRules();loadPlans();
  // 确保默认方案存在（幂等）
  POST('/api/v1/quality-plans/default',{}).catch(()=>{});
}

// 加载报告列表
async function loadQCReports(){
  try{
    try{
      const sr=await GET('/api/v1/data-standards-v2?page_size=200');
      const sl=sr?.items||sr||[];
      qcStdMap={};
      (Array.isArray(sl)?sl:[]).forEach(s=>{qcStdMap[s.id]=s.name;});
    }catch(_){}
    const r=await GET('/api/v1/quality?page_size=200');
    qcAllReports=r?.items||r||[];
    const cnt=document.getElementById('qcReportCount');
    if(cnt)cnt.textContent=qcAllReports.length;
    filterQcReports();
  }catch(e){
    document.getElementById('qcReportBody').innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--t2);padding:20px;">加载失败</td></tr>';
  }
}

// 报告表格 — 6 列实用布局
function renderQcTable(reports){
  const body=document.getElementById('qcReportBody');
  if(!reports||!reports.length){
    body.innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--t2);padding:30px;">暂无检查报告<br><span style="font-size:11px;margin-top:6px;display:inline-block;">选择文件与检查项，点击「执行质检」开始</span></td></tr>';
    return;
  }
  body.innerHTML=reports.map(r=>{
    // 属性检查统计
    const aSum=r.attribute_summary||{};
    const aTotal=aSum.total||r.constraint_violations_count||0;
    const aFail=aSum.failed||(r.constraint_violations||[]).reduce((s,v)=>(typeof v==='object'&&v.status!=='pass')?s+1:s,0);
    const aPassed=aTotal-aFail;
    const aHtml=aTotal>0
      ? (aFail===0
        ? `<span style="color:var(--green);">✓ ${aPassed}/${aTotal} 通过</span>`
        : `<span style="color:var(--red);">✗ ${aFail} 条违规</span>`)
      : '<span style="color:var(--t2);">—</span>';

    // 拓扑检查统计
    const tSum=r.topology_summary||{};
    const tTotal=tSum.total||r.topology_violations_count||0;
    const tFail=tSum.failed||(r.topology_violations||[]).reduce((s,v)=>(typeof v==='object'&&v.status!=='pass')?s+1:s,0);
    const tPassed=tTotal-tFail;
    const tHtml=tTotal>0
      ? (tFail===0
        ? `<span style="color:var(--green);">✓ ${tPassed}/${tTotal} 通过</span>`
        : `<span style="color:var(--red);">✗ ${tFail} 条违规</span>`)
      : '<span style="color:var(--t2);">—</span>';

    // 问题列表摘要（给悬浮窗用）
    const issues=[];
    (r.constraint_violations||[]).forEach(v=>{
      if(typeof v==='object'&&v.status!=='pass'){
        issues.push({type:'属性',rule:v.rule_name||v.rule_code,msg:v.message||'',count:v.failed_count||0});
      }
    });
    (r.topology_violations||[]).forEach(v=>{
      if(typeof v==='object'&&v.status!=='pass'){
        issues.push({type:'拓扑',rule:v.rule_name||v.rule_code,msg:v.message||'',count:v.failed_count||0});
      }
    });
    const tipData=esc(JSON.stringify(issues));

    return`<tr onclick="toggleQcDetail('${esc(r.id)}')" style="cursor:pointer;" data-tip='${tipData}' onmouseenter="showQcTip(event,this)" onmouseleave="hideQcTip()" onmousemove="moveQcTip(event)">
      <td><span class="file-name">${esc(r.file_name)}</span></td>
      <td style="font-size:11px;text-align:center;">${r.total_features||0}</td>
      <td style="font-size:11px;">${aHtml}</td>
      <td style="font-size:11px;">${tHtml}</td>
      <td style="font-size:10px;color:var(--t2);">${ftm(r.created_at)}</td>
      <td onclick="event.stopPropagation();" style="white-space:nowrap;">
        <button class="btn btn-g btn-sm" onclick="dlAuth('${S.base}/api/v1/quality/${esc(r.id)}/html','report.html')" style="font-size:10px;padding:2px 6px;">HTML</button>
        <button class="btn btn-g btn-sm" onclick="dlAuth('${S.base}/api/v1/quality/${esc(r.id)}/export?format=excel','violations.xlsx')" style="font-size:10px;padding:2px 6px;">Excel</button>
        <button class="btn btn-g btn-sm" onclick="dlAuth('${S.base}/api/v1/quality/${esc(r.id)}/export?format=geojson','violations.geojson')" style="font-size:10px;padding:2px 6px;">GeoJSON</button>
        <button class="btn btn-sm" onclick="qcDeleteReport('${esc(r.id)}')" style="font-size:10px;padding:2px 6px;color:var(--red);" title="删除此报告">🗑</button>
      </td>
    </tr>`;
  }).join('');
}

// 悬浮提示框
function showQcTip(e,row){
  const tip=document.getElementById('qcFloatTip');
  const data=row.dataset.tip;
  if(!data||data==='[]'){tip.style.display='none';return;}
  let issues;
  try{issues=JSON.parse(data);}catch(_){tip.style.display='none';return;}
  if(!issues.length){tip.style.display='none';return;}
  let html='<div style="font-weight:600;margin-bottom:6px;color:var(--t0);">检查结果摘要</div>';
  issues.slice(0,8).forEach(i=>{
    const color=i.type==='属性'?'var(--accent)':'var(--yellow)';
    html+=`<div style="margin-bottom:4px;"><span style="color:${color};font-size:10px;">[${esc(i.type)}]</span> <span style="font-size:11px;">${esc(i.rule)}</span><span style="font-size:10px;color:var(--red);margin-left:4px;">${i.count} 条</span></div>`;
  });
  if(issues.length>8)html+=`<div style="font-size:10px;color:var(--t2);margin-top:4px;">... 等 ${issues.length} 条问题</div>`;
  tip.innerHTML=html;
  tip.style.display='block';
  moveQcTip(e);
}
function moveQcTip(e){
  const tip=document.getElementById('qcFloatTip');
  if(tip.style.display==='none')return;
  let x=e.clientX+16, y=e.clientY+10;
  const w=tip.offsetWidth, h=tip.offsetHeight;
  if(x+w>window.innerWidth-10)x=e.clientX-w-10;
  if(y+h>window.innerHeight-10)y=e.clientY-h-10;
  tip.style.left=x+'px';tip.style.top=y+'px';
}
function hideQcTip(){
  document.getElementById('qcFloatTip').style.display='none';
}

// 搜索过滤
function filterQcReports(){
  const search=(document.getElementById('qcSearch')?.value||'').toLowerCase();
  let filtered=qcAllReports;
  if(search)filtered=filtered.filter(r=>(r.file_name||'').toLowerCase().includes(search));
  renderQcTable(filtered);
}

// ── 删除质检报告 ──────────────────────────────────────────────────────────────
async function qcDeleteReport(reportId){
  if(!confirm('确定删除此条质检报告？'))return;
  try{
    const r=await fetch(S.base+'/api/v1/quality/'+reportId,{method:'DELETE',headers:ah()});
    if(!r.ok&&r.status!==204){const d=await r.json().catch(()=>({}));throw new Error(d.detail||'HTTP '+r.status);}
    toast('已删除','success');
    qcAllReports=qcAllReports.filter(r=>r.id!==reportId);
    filterQcReports();
    updateReportCount();
  }catch(e){toast('删除失败: '+e.message,'error');}
}
async function qcDeleteAllReports(){
  if(!confirm('确定删除全部质检报告？此操作不可恢复！'))return;
  try{
    const r=await fetch(S.base+'/api/v1/quality/delete-all',{method:'POST',headers:ah()});
    if(!r.ok&&r.status!==204){const d=await r.json().catch(()=>({}));throw new Error(d.detail||'HTTP '+r.status);}
    toast('已全部删除','success');
    qcAllReports=[];
    filterQcReports();
    updateReportCount();
  }catch(e){toast('删除失败: '+e.message,'error');}
}
function updateReportCount(){
  const c=document.getElementById('qcReportCount');if(c)c.textContent=qcAllReports.length;
}

// 详情面板（行内展开）
async function toggleQcDetail(id){
  const panel=document.getElementById('qcDetailPanel');
  if(panel.dataset.currentId===id&&panel.classList.contains('show')){
    panel.classList.remove('show');panel.dataset.currentId='';return;
  }
  panel.dataset.currentId=id;
  panel.classList.add('show');
  panel.innerHTML='<div style="text-align:center;color:var(--t2);padding:20px;">加载中...</div>';
  try{
    const r=await GET('/api/v1/quality/'+id);
    const overall=(r.score_overall||0)*100;  // v2.20: 使用后端综合分（WEIGHTS）
    const overallColor=overall>=85?'var(--green)':overall>=60?'var(--yellow)':'var(--red)';

    // 【S5】五维评分卡片
    const dims=[['完整性',r.score_completeness],['准确性',r.score_accuracy],['一致性',r.score_consistency],['唯一性',r.score_uniqueness],['拓扑',r.score_topology]];
    const dimCards=dims.map(([l,v])=>{
      const p=Math.round((v||0)*100);
      const c=p>=85?'var(--green)':p>=60?'var(--yellow)':'var(--red)';
      return`<div class="dp-dim" style="border-top-color:${c};"><div class="dp-dim-v" style="color:${c};">${p}%</div><div class="dp-dim-l">${l}</div></div>`;
    }).join('');

    // 【S5】严重度分布
    const sd=r.severity_dist||{};
    const sevHtml=`<div class="dp-sev">
      <span style="color:var(--red);font-weight:600;">● 错误 ${sd.error||0}</span>
      <span style="color:var(--yellow);font-weight:600;">● 警告 ${sd.warning||0}</span>
      <span style="color:var(--t2);">违规总数 ${r.total_violations||0}</span>
    </div>`;

    // 【S5】违规样本明细
    let samHtml='';
    const sams=r.violation_samples||[];
    if(sams.length){
      const rows=sams.map(s=>{
        const _idx=s.feature_index;
        const cell=_idx!=null
          ?(r.stored_file_id
            ?`<span class="qc-idx"><a href="javascript:;" class="qc-locate" onclick="event.stopPropagation();locateReportSample('${r.stored_file_id}',${_idx})" title="切换到预览页定位该要素">要素 #${esc(_idx)} 📌</a></span>`
            :`<span class="qc-idx">要素 #${esc(_idx)}</span>`)
          :`<span class="qc-val">${esc(String(s.sample==null?'':s.sample).slice(0,60))}</span>`;
        return`<tr><td>${esc(s.rule_code||'')}</td><td>${esc(s.rule_name||'')}</td><td>${esc(s.layer||'—')}</td><td>${esc(s.field||'—')}</td><td>${cell}</td><td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(s.message||'')}</td></tr>`;
      }).join('');
      samHtml=`<div class="dp-section" style="grid-column:1/-1;"><div class="dp-section-title">🔎 违规样本 <span style="font-size:10px;color:var(--t2);font-weight:400;">${sams.length} 条</span></div>
        <table class="ck-table"><thead><tr><th>规则</th><th>名称</th><th>图层</th><th>字段</th><th>样本</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    // 属性检查表 — 从 constraint_violations 数组渲染
    let attrHtml='';
    const cvList=r.constraint_violations||[];
    const aSum=r.attribute_summary||{passed:0,total:cvList.length};
    if(cvList.length){
      const rows=cvList.map(v=>{
        if(typeof v!=='object')return'';
        const cls=v.status==='pass'?'ck-pass':'ck-fail';
        const st=v.status==='pass'?'✓ 通过':'✗ 不通过';
        const msg=v.message||v.expect||v.field_name||'';
        return`<tr><td>${esc(v.rule_code||'')}</td><td>${esc(v.rule_name||'')}</td><td class="${cls}">${st}</td><td>${v.total_checked||0}</td><td>${v.failed_count||0}</td><td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(msg)}</td></tr>`;
      }).join('');
      attrHtml=`<div class="dp-section"><div class="dp-section-title">📋 属性检查 <span style="font-size:10px;color:var(--t2);font-weight:400;">${aSum.passed||0}/${aSum.total||cvList.length} 通过</span></div>
        <table class="ck-table"><thead><tr><th>规则</th><th>名称</th><th>状态</th><th>检查</th><th>失败</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    // 拓扑检查表 — 从 topology_violations 数组渲染
    let topoHtml='';
    const tvList=r.topology_violations||[];
    const tSum=r.topology_summary||{passed:0,total:tvList.length};
    if(tvList.length){
      const rows=tvList.map(v=>{
        if(typeof v!=='object')return'';
        const cls=v.status==='pass'?'ck-pass':'ck-fail';
        const st=v.status==='pass'?'✓ 通过':'✗ 不通过';
        const msg=v.message||'';
        return`<tr><td>${esc(v.rule_code||'')}</td><td>${esc(v.rule_name||'')}</td><td class="${cls}">${st}</td><td>${v.total_checked||0}</td><td>${v.failed_count||0}</td><td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(msg)}</td></tr>`;
      }).join('');
      topoHtml=`<div class="dp-section"><div class="dp-section-title">🗺 拓扑检查 <span style="font-size:10px;color:var(--t2);font-weight:400;">${tSum.passed||0}/${tSum.total||tvList.length} 通过</span></div>
        <table class="ck-table"><thead><tr><th>规则</th><th>名称</th><th>状态</th><th>检查</th><th>失败</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    panel.innerHTML=`
      <div class="dp-header">
        <div class="dp-score" style="background:${overallColor}15;color:${overallColor};">${Math.round(overall)}</div>
        <div class="dp-info">
          <div class="dp-name">${esc(r.file_name)}</div>
          <div class="dp-meta">${r.total_features||0} 要素 · ${ftm(r.created_at)}</div>
        </div>
        <div style="display:flex;gap:6px;">
          <button class="btn btn-g btn-sm" onclick="dlAuth('${S.base}/api/v1/quality/${id}/html','report.html')">📄 HTML</button>
          <button class="btn btn-g btn-sm" onclick="dlAuth('${S.base}/api/v1/quality/${id}/export?format=excel','violations.xlsx')">📊 Excel</button>
          <button class="btn btn-g btn-sm" onclick="dlAuth('${S.base}/api/v1/quality/${id}/export?format=geojson','violations.geojson')">🗺 GeoJSON</button>
        </div>
      </div>
      <div class="dp-dims">${dimCards}</div>
      ${sevHtml}
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
        ${attrHtml||'<div class="dp-section"><div class="dp-section-title">📋 属性检查 <span style="font-size:10px;color:var(--t2);font-weight:400;">无数据</span></div><div style="color:var(--t2);font-size:12px;padding:8px 0;">未执行属性检查</div></div>'}
        ${topoHtml||'<div class="dp-section"><div class="dp-section-title">🗺 拓扑检查 <span style="font-size:10px;color:var(--t2);font-weight:400;">无数据</span></div><div style="color:var(--t2);font-size:12px;padding:8px 0;">未执行拓扑检查</div></div>'}
      </div>
      ${samHtml}`;
  }catch(e){panel.innerHTML=`<div style="color:var(--red);font-size:12px;text-align:center;padding:16px;">加载失败: ${esc(e.message)}</div>`;}
}

// 加载标准选择器
async function loadStdsSel(){
  try{
    const resp=await GET('/api/v1/data-standards-v2?page_size=50'); // 【S4】放宽上限，避免大库标准被截断
    const list=resp?.items||resp||[];
    const sel=document.getElementById('qcStd2');
    if(sel){
      // "不关联标准"置顶为空值选项
      sel.innerHTML='<option value="">— 不关联标准（仅拓扑检查）</option>';
      // 按 category 分组（optgroup）
      const cats={};
      (Array.isArray(list)?list:[]).forEach(s=>{
        const c=s.category||'未分类';
        (cats[c]=cats[c]||[]).push(s);
      });
      Object.entries(cats).forEach(([cat,arr])=>{
        const g=document.createElement('optgroup');
        g.label='📁 '+cat;
        arr.forEach(s=>g.appendChild(new Option(s.name,s.id)));
        sel.appendChild(g);
      });
      qcStdMap={};
      (Array.isArray(list)?list:[]).forEach(s=>{qcStdMap[s.id]=s.name;});
      // 若之前选中的标准仍在列表中，恢复选中
      if(S._qcStd2&&qcStdMap[S._qcStd2])sel.value=S._qcStd2;
    }
    const filterSel=document.getElementById('dmStdFilter');
    if(filterSel){
      while(filterSel.options.length>2)filterSel.remove(2);
      (Array.isArray(list)?list:[]).forEach(s=>filterSel.appendChild(new Option(s.name,s.id)));
    }
  }catch(e){}
}

// 保存当前选中的标准（切页后恢复）
document.addEventListener('change',function(e){
  if(e.target&&e.target.id==='qcStd2'){S._qcStd2=e.target.value;}
});

// ── Coordinate Transform ──────────────────────────────────
let _crsCatalog = null; // 缓存 CRS 目录
function initTx(){
  document.getElementById('txRunBtn').addEventListener('click',doTx);
  document.getElementById('txFile').addEventListener('change',updateTxPrev);
  loadCRSCatalog();
}
// 下拉选中 → 填入文本框
function onCRSSel(which){
  const sel=document.getElementById('tx'+(which==='src'?'Src':'Dst')+'Sel');
  const inp=document.getElementById('tx'+(which==='src'?'Src':'Dst'));
  if(sel.value){
    inp.value=sel.value;
    chkAlias(which);
  }
}
// 文本框输入 → 尝试反向选中下拉
function onCRSInput(which){
  const sel=document.getElementById('tx'+(which==='src'?'Src':'Dst')+'Sel');
  const inp=document.getElementById('tx'+(which==='src'?'Src':'Dst'));
  const v=inp.value.trim();
  // 尝试精确匹配下拉选项
  let matched=false;
  for(let i=0;i<sel.options.length;i++){
    if(sel.options[i].value===v){sel.selectedIndex=i;matched=true;break;}
  }
  if(!matched) sel.selectedIndex=0; // 未匹配则回到"从列表选择"
  chkAlias(which);
}
// 兼容旧 setCRS 调用（如 updateTxPrev 自动填充）
function setCRS(which,val){
  document.getElementById('tx'+(which==='src'?'Src':'Dst')).value=val;
  // 同步下拉
  const sel=document.getElementById('tx'+(which==='src'?'Src':'Dst')+'Sel');
  let matched=false;
  for(let i=0;i<sel.options.length;i++){
    if(sel.options[i].value===val){sel.selectedIndex=i;matched=true;break;}
  }
  if(!matched) sel.selectedIndex=0;
  chkAlias(which);
}
function chkAlias(which){
  const inp=document.getElementById('tx'+(which==='src'?'Src':'Dst'));
  const hint=document.getElementById('tx'+(which==='src'?'SrcH':'DstH'));
  const v=(inp.value||'').trim();
  const res=CRS_ALIAS[v];
  if(res&&res!==v){hint.textContent=`→ 识别为 ${res} (${CRS_LBL[res]||res})`;hint.style.display='block';return;}
  // 识别自定义坐标系 code（shanghaicity / shanghai_local 等，引擎内置定义）
  const customMatch=(document.querySelector('#tx'+(which==='src'?'Src':'Dst')+'Sel')||{}).value;
  // 手动输入的自定义 code（不含 EPSG:/WKT 前缀时）
  if(!/^EPSG:/i.test(v) && (v==='shanghaicity'||v==='shanghai_local'||v==='Custom:shanghaicity'||v==='Custom:shanghai_local')){
    const pretty=(v.indexOf(':')>=0?v.split(':')[1]:v);
    const prettyName=pretty==='shanghaicity'?'上海城市坐标系':'上海地方坐标系(新)';
    hint.textContent=`→ 自定义坐标系 ${prettyName}，将以注册 SRID 参与变换`;
    hint.style.display='block';return;
  }
  // 纯数字（如 4326）→ 提示按 EPSG 编码处理
  if(/^\d{4,6}$/.test(v)){
    hint.textContent=`→ 按 EPSG 编码处理为 EPSG:${v}（${CRS_LBL['EPSG:'+v]||'请在目录中确认'}）`;
    hint.style.display='block';return;
  }
  hint.style.display='none';
}
// 从 API 加载 CRS 目录并填充下拉（路由免鉴权，直接 fetch 绕过 ah()）
async function loadCRSCatalog(){
  try{
    const r=await _t(S.base+'/api/v1/spatial/crs/catalog');
    if(!r.ok) throw new Error('HTTP '+r.status);
    const data=await r.json();
    _crsCatalog=data.catalog||[];
    ['txSrcSel','txDstSel'].forEach(selId=>{
      const sel=document.getElementById(selId);
      if(!sel) return;
      // 保留第一个 option
      while(sel.options.length>1) sel.remove(1);
      _crsCatalog.forEach(grp=>{
        const og=document.createElement('optgroup');
        og.label=grp.group;
        grp.items.forEach(it=>{
          og.appendChild(new Option(it.name,it.code));
        });
        sel.appendChild(og);
      });
    });
  }catch(e){console.warn('CRS catalog load failed:',e);}
}
async function loadTxFiles(){
  try{
    const resp=await GET('/api/v1/data?page_size=200');
    const files=(resp?.items||resp||[]).filter(f=>!f.is_archive);
    const sel=document.getElementById('txFile');
    while(sel.options.length>1)sel.remove(1);
    files.forEach(f=>{
      const crs=crsLabel(f.detected_crs,f.detected_crs_srid,f.crs_is_custom);
      const crsDisp=crs?' ['+crs+']':'';
      const o=new Option(f.original_name+crsDisp+' ('+fsz(f.file_size)+')',f.id);
      o.dataset.crs=f.detected_crs||'';
      o.dataset.srid=String(f.detected_crs_srid||'');
      o.dataset.cust=String(!!f.crs_is_custom);
      o.dataset.name=f.original_name;
      sel.appendChild(o);
    });
  }catch(e){console.error('loadTxFiles failed:',e);}
}
async function updateTxPrev(){
  const fid=document.getElementById('txFile').value;
  if(!fid){document.getElementById('txPreview').style.display='none';return;}
  // 自动填充源坐标系（优先 SRID，其次 WKT/EPSG）
  const sel=document.getElementById('txFile');
  const opt=sel.options[sel.selectedIndex];
  if(opt){
    const detectedCrs=opt.dataset.crs||'';
    const srid=opt.dataset.srid;
    const isCustom=opt.dataset.cust==='true'||(srid&&parseInt(srid)>=CUSTOM_SRID_MIN);
    // 自定义 SRID（91xxxxx）：位点是 VCS 自建，不能以 EPSG:913xxx 回填（pyproj 不识别），
    // 回填其可解析的字典名（Custom:shanghaicity 等）或 WKT 原文
    const autoFill=isCustom
      ? ((opt.dataset.crs&&crsResolvable(opt.dataset.crs))|| '')
      : (srid?'EPSG:'+srid:detectedCrs);
    const srcInput=document.getElementById('txSrc');
    // 仅在源坐标系为空或为默认值时自动填充，避免覆盖用户手动输入
    if(autoFill&&(!srcInput.value||srcInput.value==='EPSG:4326'||srcInput.value==='WGS84')){
      srcInput.value=autoFill;
      chkAlias('src');
    }
    // 在预览区显示文件 CRS 信息
    const crsDisplay=crsLabel(detectedCrs,parseInt(srid)||null,isCustom)||'未知';
    const crsInfo=document.getElementById('txPreviewCrs');
    if(crsInfo)crsInfo.innerHTML=`<span style="color:var(--cyan);font-size:12px;">源 CRS: <strong>${esc(crsDisplay)}</strong></span>`;
  }
  try{
    const geo=await GET('/api/v1/data/'+fid+'/features?page_size=3');
    const feats=(geo.features||[]).slice(0,3);
    if(!feats.length){document.getElementById('txPreview').style.display='none';return;}
    document.getElementById('txPreview').style.display='block';
    document.getElementById('txPreviewRows').innerHTML=feats.map(f=>{
      const g=f.geometry||{};let coord='—';
      if(g.coordinates){
        const c=g.coordinates;
        const pt=Array.isArray(c[0])?Array.isArray(c[0][0])?c[0][0]:c[0]:c;
        const x=pt[0],y=pt[1];
        coord=`${Number(x).toFixed(6)}, ${Number(y).toFixed(6)}`;
      }
      return`<div class="tp-row"><span class="tpl">原始</span><span class="tpc">${coord}</span></div>`;
    }).join('');
    // 坐标域提示：判断数据是经纬度还是投影米制，提示源坐标系选择
    probeCoordDomain(fid).then(p=>{
      const tip=document.getElementById('txPreviewCrs');
      if(!tip) return;
      const domainTip=(p.isProjected)
        ?'<span style="color:var(--amber,#e6a23c);font-size:11px;margin-left:6px;">·坐标疑似<strong>投影米制</strong>，源坐标系请选投影系(如 Custom:shanghaicity / EPSG:4547)，勿选 WGS84</span>'
        :'<span style="color:var(--t2);font-size:11px;margin-left:6px;">·坐标疑似<strong>经纬度</strong></span>';
      tip.innerHTML=tip.innerHTML+domainTip;
    });
  }catch(e){document.getElementById('txPreview').style.display='none';}
}
// 判断坐标域：多数坐标绝对值远超 ±180/±92 → 判定投影米制（非经纬度）
function probeCoordDomain(fid){
  return new Promise(resolve=>{
    GET('/api/v1/data/'+fid+'/features?page_size=5').then(geo=>{
      const feats=(geo.features||[]).slice(0,5);
      let big=0, small=0;
      feats.forEach(f=>{
        const c=(f.geometry||{}).coordinates;
        if(!c) return;
        (function walk(o){
          if(typeof o[0]==='number'&&o.length>=2){
            const x=Math.abs(o[0]), y=Math.abs(o[1]);
            if(!isFinite(x)||!isFinite(y)) return;
            if(x>200||y>92) big++; else small++;
            return;
          }
          if(Array.isArray(o)) for(const v of o){ walk(v); }
        })(c);
      });
      const total=big+small;
      resolve({big, small, total, isProjected: total>0 && big/total>0.9});
    }).catch(()=>resolve({big:0,small:0,total:0,isProjected:false}));
  });
}
// 预检文案：文件坐标是投影米制但源/目标选了地理坐标系 → 返回建议
function domainMatchAdvice(srcCrs, dstCrs, isProjected){
  if(!isProjected) return '';
  const G=/^(EPSG:4326|EPSG:4490|EPSG:4214|EPSG:4610)$/i;
  const srcGeo=G.test(srcCrs), dstGeo=G.test(dstCrs);
  const parts=[];
  if(srcGeo&&!dstGeo) parts.push('源坐标系选的是地理(度)，但文件坐标疑似投影米制');
  if(!srcGeo&&dstGeo) parts.push('目标选的是地理(度)，源需选投影(米)坐标系');
  if(srcGeo&&dstGeo) parts.push('源文件坐标疑似投影米制，但源/目标都选了地理(度)坐标系');
  return parts.join('；');
}
async function doTx(){
  const fid=document.getElementById('txFile').value;
  if(!fid)return toast('请选择文件','error');
  const src=resCRS(document.getElementById('txSrc').value)||'EPSG:4326';
  const dst=resCRS(document.getElementById('txDst').value)||'EPSG:4490';
  const btn=document.getElementById('txRunBtn'),prog=document.getElementById('txProg'),res=document.getElementById('txResult');
  btn.disabled=true;prog.style.display='block';res.style.display='none';
  // 预检（异步完成，不阻塞请求）：坐标域疑似投影米制时给出提前警告
  let preWarn='';
  try{
    const probe=await probeCoordDomain(fid);
    preWarn=(probe.isProjected)?domainMatchAdvice(src,dst,true):'';
  }catch(e){}
  try{
    const r=await fetch(`${S.base}/api/v1/spatial/${fid}/transform?source_crs=${encodeURIComponent(src)}&target_crs=${encodeURIComponent(dst)}`,{method:'POST',headers:ah()});
    if(!r.ok){
      const j=await r.json().catch(()=>({}));
      // 解析后端诊断 detail（含域不匹配提示）
      let detail=j.detail||'HTTP '+r.status;
      res.className='cr err';res.style.display='block';
      let html='✗ 转换失败: '+esc(detail);
      // 若失败且坐标疑似投影米制 → 强提示检查源坐标系
      if(!preWarn){
        try{const probe=await probeCoordDomain(fid);if(probe.isProjected)preWarn=domainMatchAdvice(src,dst,true);}catch(e){}
      }
      if(preWarn) html+=`<div style="margin-top:8px;padding:8px 10px;background:rgba(230,162,60,.12);border-left:3px solid var(--amber,#e6a23c);border-radius:4px;font-size:12px;color:var(--amber,#e6a23c);">💡 ${esc(preWarn)}，请调整源/目标坐标系后重试</div>`;
      res.innerHTML=html;
      return;
    }
    const d=await r.json();
    res.className='cr';res.style.display='block';
    // 统计（含跳过/失败）与校验告警
    const warnHtml=(d.warnings&&d.warnings.length)?`<div style="margin-top:6px;font-size:11px;color:var(--amber,#e6a23c);">⚠ ${d.warnings.map(esc).join('<br>⚠ ')}</div>`:'';
    const hasFail=d.failed>0;
    const statNote=(hasFail||d.skipped>0)?`<span style="font-size:11px;color:${hasFail?'var(--red,#e74c3c)':'var(--t2)'};">（跳过 ${d.skipped||0}，失败 ${d.failed||0}）</span>`:'';
    let inner=`✓ 已转换 <strong>${d.transformed}</strong> / ${d.total} 个要素 ${statNote}<br>目标坐标系: <span class="mono">${esc(d.target_crs)}</span> (${CRS_LBL[d.target_crs]||d.target_crs})<br><span style="font-size:11px;color:var(--t2);">结果已保存，可在下方表格下载</span>${warnHtml}`;
    // 若有失败且疑似投影米制源 → 提示核对源坐标系
    if(hasFail){
      if(!preWarn){
        try{const probe=await probeCoordDomain(fid);preWarn=probe.isProjected?domainMatchAdvice(src,dst,true):'';}catch(e){}
      }
      if(preWarn) inner+=`<div style="margin-top:8px;padding:8px 10px;background:rgba(230,162,60,.12);border-left:3px solid var(--amber,#e6a23c);border-radius:4px;font-size:12px;color:var(--amber,#e6a23c);">💡 ${esc(preWarn)}，部分要素可能因坐标系域不匹配而未转换</div>`;
    }
    res.innerHTML=inner;
    toast(hasFail?'坐标转换完成（部分要素失败）':'坐标转换完成',hasFail?'warning':'success');
    loadTxResults();
  }catch(e){
    res.className='cr err';res.style.display='block';
    let html='✗ 转换失败: '+esc(e.message);
    if(preWarn) html+=`<div style="margin-top:8px;padding:8px 10px;background:rgba(230,162,60,.12);border-left:3px solid var(--amber,#e6a23c);border-radius:4px;font-size:12px;color:var(--amber,#e6a23c);">💡 ${esc(preWarn)}，请调整源/目标坐标系后重试</div>`;
    res.innerHTML=html;
  }
  btn.disabled=false;prog.style.display='none';
}
async function loadTxResults(){
  try{
    const r=await fetch(`${S.base}/api/v1/spatial/transform-results?page_size=50`,{headers:ah()});
    if(!r.ok)return;
    const d=await r.json();
    const tb=document.getElementById('txHistTbody');
    if(!d.items||!d.items.length){tb.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--t2);padding:24px;">暂无转换结果</td></tr>';return;}
    tb.innerHTML=d.items.map(i=>`<tr>
      <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(i.source_file_name)}">${esc(i.source_file_name)}</td>
      <td><span class="mono" style="color:var(--cyan);font-size:11px;">${esc(i.source_crs)}</span></td>
      <td><span class="mono" style="color:var(--green);font-size:11px;">${esc(i.target_crs)}</span></td>
      <td>${i.feature_count}</td>
      <td>${fsz(i.output_size)}</td>
      <td style="font-size:11px;color:var(--t2);">${ftm(i.created_at)}</td>
      <td style="white-space:nowrap;">
        <div style="position:relative;display:inline-flex;align-items:center;">
          <button class="btn btn-s btn-p" onclick="downloadTxResult('${i.id}','geojson')" style="padding:3px 8px;font-size:11px;">⬇ 下载</button>
          <select onchange="downloadTxResult('${i.id}',this.value);this.value='geojson';" style="margin-left:4px;padding:2px 2px;font-size:10px;background:var(--bg1);color:var(--t1);border:1px solid var(--bd);border-radius:4px;max-width:74px;" title="选择导出格式">
            <option value="geojson">GeoJSON</option>
            <option value="shp">SHP(ZIP)</option>
            <option value="gpkg">GPKG</option>
          </select>
          <button class="btn btn-s" onclick="deleteTxResult('${i.id}')" style="padding:3px 8px;font-size:11px;color:var(--red);margin-left:4px;">🗑</button>
        </div>
      </td>
    </tr>`).join('');
  }catch(e){console.warn('loadTxResults failed:',e);}
}
async function downloadTxResult(id,fmt){
  fmt=fmt||'geojson';
  try{
    const r=await fetch(`${S.base}/api/v1/spatial/transform-results/${id}/download?format=${encodeURIComponent(fmt)}`,{headers:ah()});
    if(!r.ok)throw new Error(r.status===401?'授权失败':'下载失败');
    const blob=await r.blob();
    const cd=r.headers.get('content-disposition')||'';
    const fn=cd.match(/filename\*=UTF-8''(.+)/)?decodeURIComponent(RegExp.$1):'transform_result.'+(fmt==='shp'?'zip':fmt);
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=url;a.download=fn;
    document.body.appendChild(a);a.click();document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }catch(e){toast('下载失败: '+e.message,'error');}
}
async function deleteTxResult(id){
  if(!confirm('确定删除此转换结果？'))return;
  try{
    const r=await fetch(`${S.base}/api/v1/spatial/transform-results/${id}`,{method:'DELETE',headers:ah()});
    if(!r.ok)throw new Error('删除失败');
    toast('已删除','success');loadTxResults();
  }catch(e){toast('删除失败: '+e.message,'error');}
}

// ── Preview ───────────────────────────────────────────────
let _pvMap=null,_pvGeo=null,_pvTile=null,_pvIsCad=false;
/** 预览页属性表状态：已加载要素 + 客户端分页 + 选中定位 */
const PV={feats:[],page:1,size:50,total:0,truncated:false,selIdx:null,_hl:null,_hlOrig:null,_hlT:null};
function initPreview(){
  document.getElementById('pvLoadBtn').addEventListener('click',loadPvMap);
  document.getElementById('pvBasemap').addEventListener('change',e=>swPvBase(e.target.value));
  document.querySelectorAll('#pvTypeFilter input').forEach(cb=>{
    cb.addEventListener('change',()=>{if(_pvGeo)loadPvMap();});
  });
  document.getElementById('pvPrev').addEventListener('click',()=>pvGo(-1));
  document.getElementById('pvNext').addEventListener('click',()=>pvGo(1));
  document.getElementById('pvSizeSel').addEventListener('change',function(){
    PV.size=parseInt(this.value)||50;PV.page=1;renderPvAttr();
  });
}
async function loadPvFiles(){
  try{
    const resp=await GET('/api/v1/data?page_size=200');
    const files=(resp?.items||resp||[]).filter(f=>!f.is_archive);
    const sel=document.getElementById('pvFile');
    while(sel.options.length>1)sel.remove(1);
    files.forEach(f=>{
      const o=new Option(f.original_name+' ('+fsz(f.file_size)+')',f.id);
      // v2.18.3 文件格式标记：供 loadPvMap 判断 CAD 平面坐标（dxf/dwg）以选用 CRS.Simple
      o.dataset.format=(f.file_format||'').toLowerCase();
      sel.appendChild(o);
    });
  }catch(e){console.error('loadPvFiles failed:',e);}
}
function _gtColor(t){
  const up=(t||'').toUpperCase();
  if(up==='POINT'||up==='MULTIPOINT')return '#388bfd';
  if(up.includes('LINE'))return '#d29922';
  if(up.includes('POLYGON'))return '#3fb950';
  return '#8b949e';
}
function _gtFilter(feat){
  const t=(feat.geometry||{}).type||'';
  const up=t.toUpperCase();
  let cat='other';
  if(up==='POINT'||up==='MULTIPOINT')cat='point';
  else if(up.includes('LINE'))cat='line';
  else if(up.includes('POLYGON'))cat='polygon';
  const cb=document.querySelector(`#pvTypeFilter input[data-gtype="${cat}"]`);
  return cb?cb.checked:true;
}
async function loadPvMap(){
  const fid=document.getElementById('pvFile').value;if(!fid)return;
  const _sel=document.getElementById('pvFile');
  const _fmt=(_sel.selectedOptions[0]&&_sel.selectedOptions[0].dataset.format)||'';
  // v2.18.3 空间数据浏览 CAD 平面坐标适配（与详情抽屉 loadDMap 同语义）：
  // DWG/DXF 图纸是模型平面坐标（数量级 1e4~1e5，y 向上），Leaflet 默认地理
  // CRS 会误当经纬度 → Web Mercator 投影溢出 → 地图框只见一条直线/乱线。
  // CAD 文件改用 L.CRS.Simple 平面坐标系 + y 翻转；Leaflet 不支持运行时切 CRS，
  // 故文件类型变化时重建地图（地理数据 → 默认 CRS + OSM 底图）。
  const _isCad=(_fmt==='dxf'||_fmt==='dwg');
  if(!_pvMap||_pvIsCad!==_isCad){
    if(_pvMap){_pvMap.remove();_pvMap=null;_pvGeo=null;_pvTile=null;}
    _pvIsCad=_isCad;
    if(_pvIsCad){
      const _ModelCRS=L.Util.extend({},L.CRS.Simple,{
        transformation:new L.Transformation(1,0,-1,0)
      });
      _pvMap=L.map('pvMap',{crs:_ModelCRS}).fitWorld();
    }else{
      _pvMap=L.map('pvMap').setView([35,110],4);
      _pvTile=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(_pvMap);
    }
    setTimeout(()=>_pvMap.invalidateSize(),100);
  }
  if(_pvGeo){_pvMap.removeLayer(_pvGeo);_pvGeo=null;}
  // Remove old legend
  document.querySelectorAll('.pv-legend').forEach(el=>el.remove());
  try{
    // 拉取上限按服务端允许的最大值 10000（超过部分在 pvPI 提示截断）
    const geo=await GET('/api/v1/data/'+fid+'/features?page_size=10000');
    const allFeats=(geo.features||[]).filter(_gtFilter);
    // 初始化属性表状态
    PV.feats=allFeats;
    PV.total=(geo.metadata&&geo.metadata.total!=null)?geo.metadata.total:allFeats.length;
    PV.truncated=PV.total>allFeats.length;
    PV.page=1;PV.selIdx=null;
    if(PV._hl){try{PV._hl.setStyle(PV._hlOrig)}catch(e){}}PV._hl=null;
    document.getElementById('pv-feat').textContent=PV.truncated?(allFeats.length.toLocaleString()+'+'):allFeats.length.toLocaleString();
    const layers=new Set(allFeats.map(f=>(f.properties||{}).layer_name||'').filter(Boolean));
    document.getElementById('pv-lay').textContent=layers.size||1;
    // 先渲染属性表（空结果时清空表格并隐藏分页，避免残留）
    renderPvAttr();
    if(!allFeats.length)return;
    // Count by geometry type
    const typeCnt={};
    allFeats.forEach(f=>{
      const t=_gtColor((f.properties||{}).geometry_type||(f.geometry||{}).type||'');
      const key=t==='#388bfd'?'点':t==='#d29922'?'线':t==='#3fb950'?'面':'其他';
      typeCnt[key]=(typeCnt[key]||0)+1;
    });
    document.getElementById('pv-sz').textContent=Object.entries(typeCnt).map(([k,v])=>`${k}:${v}`).join(' ');
    // Filtered GeoJSON
    const filtered={type:'FeatureCollection',features:allFeats};
    _pvGeo=L.geoJSON(filtered,{
      pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:6,fillColor:_gtColor((f.properties||{}).geometry_type||(f.geometry||{}).type||''),color:'#1f6feb',weight:2,fillOpacity:.7}),
      style:f=>{
        const t=_gtColor((f.properties||{}).geometry_type||(f.geometry||{}).type||'');
        const up=(f.geometry||{}).type||'';
        if(up.includes('POLYGON')||up==='MULTIPOLYGON')return{color:t,weight:2,fillOpacity:.1};
        if(up.includes('LINE'))return{color:t,weight:2};
        return{color:t,weight:1};
      },
      onEachFeature:(f,layer)=>{
        const p=f.properties||{};
        const gt=p.geometry_type||(f.geometry||{}).type||'';
        let html=`<b>#${p.feature_index||''}</b> <span style="color:${_gtColor(gt)};">●</span> ${gt}`;
        if(p.source_name)html+=`<br><span style="color:var(--t2);font-size:11px;">来源: ${esc(p.source_name)}</span>`;
        for(const[k,v] of Object.entries(p)){
          if(['feature_index','layer_name','source_name','geometry_type'].includes(k))continue;
          html+=`<br>${esc(k)}: ${esc(String(v).slice(0,40))}`;
        }
        layer.bindPopup(html);
      }
    }).addTo(_pvMap);
    _pvMap.fitBounds(_pvGeo.getBounds(),{padding:[30,30]});
    populateLayers(allFeats);
    // Legend
    const legend=document.createElement('div');
    legend.className='pv-legend';
    legend.style.cssText='position:absolute;bottom:20px;right:20px;z-index:1000;background:rgba(13,17,23,.9);border:1px solid #30363d;border-radius:8px;padding:10px 14px;font-size:12px;';
    legend.innerHTML='<div style="font-weight:600;margin-bottom:4px;color:#e6edf3;">图例</div>'+
      '<div><span style="color:#388bfd;">●</span> 点</div>'+
      '<div><span style="color:#d29922;">━</span> 线</div>'+
      '<div><span style="color:#3fb950;">▣</span> 面</div>';
    document.getElementById('pvMap').appendChild(legend);
    // 报告样本联动（v2.21）：文件加载完成后消费待定位项
    if(_pvPendingLocate){
      const pl=_pvPendingLocate;_pvPendingLocate=null;
      if(pl.featureIndex!=null)setTimeout(()=>locatePvByFeatureIndex(pl.featureIndex),50);
    }
  }catch(e){
    _pvPendingLocate=null;
    toast('加载失败: '+e.message,'error');
  }
}
function swPvBase(val){
  if(!_pvMap)return;
  // CAD 平面坐标系下无地理底图可对齐（瓦片按经纬度切分），忽略底图切换
  if(_pvIsCad){return;}
  if(_pvTile)_pvMap.removeLayer(_pvTile);
  const urls={osm:'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',sat:'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',dark:'https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png'};
  _pvTile=L.tileLayer(urls[val]||urls.osm,{maxZoom:19}).addTo(_pvMap);
}
function renderPvAttr(){
  const el=document.getElementById('pvAttr');
  const paging=document.getElementById('pvPaging');
  const feats=PV.feats;
  if(!feats.length){
    el.innerHTML='';
    if(paging)paging.style.display='none';
    document.getElementById('pvAC').textContent='';
    return;
  }
  // 字段全集（去重；feature_index 置首，其余按出现顺序）
  const fields=new Set();
  feats.forEach(f=>{if(f.properties)Object.keys(f.properties).forEach(k=>fields.add(k));});
  const fl=['feature_index',...Array.from(fields).filter(k=>k!=='feature_index')];
  const total=feats.length,size=PV.size;
  const pages=Math.max(1,Math.ceil(total/size));
  if(PV.page>pages)PV.page=pages;
  const start=(PV.page-1)*size,end=Math.min(start+size,total);
  const slice=feats.slice(start,end);
  // 全字段渲染 + 横向滚动（容器 overflow-x:auto）
  let t='<table class="dt" style="font-size:11px;min-width:100%;white-space:nowrap;"><thead><tr>';
  t+='<th style="width:40px;">#</th>';
  fl.forEach(k=>t+=`<th>${esc(k)}</th>`);
  t+='</tr></thead><tbody>';
  slice.forEach((f,i)=>{
    const gi=start+i;
    const sel=PV.selIdx!=null&&f.properties&&f.properties.feature_index===PV.selIdx?' pv-sel-row':'';
    t+=`<tr class="pv-row${sel}" data-gi="${gi}" onclick="locatePvFeature(${gi})" title="点击定位到地图">`;
    t+=`<td style="color:var(--t2);width:40px;">${gi+1}</td>`;
    fl.forEach(k=>{
      const v=(f.properties||{})[k];
      const s=v!=null?String(v):'';
      t+=`<td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;" title="${esc(s)}">${esc(s)}</td>`;
    });
    t+='</tr>';
  });
  t+='</tbody></table>';
  document.getElementById('pvAC').textContent=`显示第 ${start+1}-${end} 行 / 共 ${total.toLocaleString()} 行`;
  if(paging){
    paging.style.display='flex';
    document.getElementById('pvPI').textContent=`共 ${total.toLocaleString()} 行 · ${fl.length} 个字段${PV.truncated?`（⚠ 文件共 ${PV.total.toLocaleString()} 个要素，仅加载前 ${total.toLocaleString()} 个）`:''}`;
    document.getElementById('pvPN').textContent=`第 ${PV.page} 页 / 共 ${pages} 页`;
    document.getElementById('pvPrev').disabled=PV.page===1;
    document.getElementById('pvNext').disabled=PV.page>=pages;
  }
  el.innerHTML=t;
}
/** 属性表翻页：delta=-1 上一页 / +1 下一页 */
function pvGo(delta){
  const pages=Math.max(1,Math.ceil(PV.feats.length/PV.size));
  PV.page=Math.min(pages,Math.max(1,PV.page+delta));
  renderPvAttr();
}
/** 报告样本 → 预览定位待办（v2.21 联动）：goPage 后由 loadPvMap 成功后消费 */
let _pvPendingLocate=null;   // {fileId, featureIndex} | null
/** 定位核心：flyTo/flyToBounds + 高亮闪烁 + 行选中（gi=PV.feats 数组下标, idx=feature_index） */
function _pvLocateCore(f,gi,idx){
  // 已选中的行再次点击 → 取消选中（保留地图要素闪烁高亮）
  if(PV.selIdx!=null&&idx!=null&&PV.selIdx===idx){
    PV.selIdx=null;
    const rows1=document.querySelectorAll('#pvAttr tr[data-gi]');
    rows1.forEach(r=>r.classList.remove('pv-sel-row'));
    return;
  }
  let target=null;
  _pvGeo.eachLayer(l=>{if(!target&&l.feature===f)target=l;});
  if(!target)return;
  // 恢复上一个高亮
  if(PV._hl){try{PV._hl.setStyle(PV._hlOrig)}catch(e){}}
  // 定位：点 → flyTo；线/面/多点 → flyToBounds
  const geom=(f.geometry||{}).type||'';
  if(geom==='Point'){
    const ll=target.getLatLng&&target.getLatLng();
    if(ll)_pvMap.flyTo(ll,Math.max(_pvMap.getZoom(),16));
  }else{
    const b=target.getBounds&&target.getBounds();
    if(b&&b.isValid())_pvMap.flyToBounds(b,{padding:[40,40],maxZoom:16});
  }
  // 高亮闪烁（1.6s 后还原）
  PV._hl=target;
  PV._hlOrig={};
  ['radius','weight','color','fillColor','fillOpacity','opacity'].forEach(k=>{
    if(k in target.options)PV._hlOrig[k]=target.options[k];
  });
  target.setStyle({weight:5,color:'#ffa657',fillColor:'#ffa657',radius:Math.max(target.options.radius||6,11),fillOpacity:.85,opacity:1});
  try{target.openPopup&&target.openPopup();}catch(e){}
  clearTimeout(PV._hlT);
  PV._hlT=setTimeout(()=>{
    if(PV._hl){try{PV._hl.setStyle(PV._hlOrig)}catch(e){}PV._hl=null;}
  },1600);
  // 行选中高亮（不重渲染表格，避免滚动位置复位）
  PV.selIdx=idx;
  const rows=document.querySelectorAll('#pvAttr tr[data-gi]');
  rows.forEach(r=>r.classList.toggle('pv-sel-row',r.dataset.gi===String(gi)));
}
/** 点击属性行 → 地图定位到对应要素，并高亮闪烁 + 行选中 */
function locatePvFeature(gi){
  const f=PV.feats[gi];if(!f||!_pvGeo)return;
  _pvLocateCore(f,gi,f.properties&&f.properties.feature_index);
}
/** 按 feature_index 定位（跨页自动翻页；供报告样本联动复用） */
function locatePvByFeatureIndex(featureIndex){
  if(featureIndex==null)return;
  const gi=PV.feats.findIndex(f=>(f.properties||{}).feature_index===featureIndex);
  if(gi<0){toast('要素 #'+featureIndex+' 不在已加载范围（可能超过预览上限或被图层过滤）','error');return;}
  const f=PV.feats[gi];
  // 跨页：先翻到所在页再定位（重渲染后行内 onclick 已是新 gi）
  const page=Math.floor(gi/PV.size)+1;
  if(PV.page!==page){
    PV.page=page;
    renderPvAttr();
    setTimeout(()=>_pvLocateCore(f,gi,featureIndex),30);
  }else{
    _pvLocateCore(f,gi,featureIndex);
  }
}
/** 报告违规样本 → 预览页定位（v2.21 联动） */
async function locateReportSample(fileId,featureIndex){
  if(!fileId||featureIndex==null){toast('该样本缺少文件关联，无法定位','error');return;}
  const sel=document.getElementById('pvFile');
  const cur=sel.value;
  goPage('preview');
  if(cur!==fileId){
    _pvPendingLocate={fileId,featureIndex};
    if([...sel.options].find(o=>o.value===fileId)){
      sel.value=fileId;
      loadPvMap();
    }else{
      toast('正在加载文件列表...','info');
      await loadPvFiles();
      if([...sel.options].find(o=>o.value===fileId)){
        sel.value=fileId;
        loadPvMap();
      }else{
        toast('找不到关联文件（可能已删除）','error');
        _pvPendingLocate=null;
      }
    }
  }else{
    if(!PV.feats.length)toast('请先点击「加载地图」','error');
    else locatePvByFeatureIndex(featureIndex);
  }
}
function populateLayers(feats){
  const sel=document.getElementById('pvLayer');
  while(sel.options.length>1)sel.remove(1);
  const layers=new Set(feats.map(f=>(f.properties||{}).layer_name||'').filter(Boolean));
  layers.forEach(l=>{const o=new Option(l,l);sel.appendChild(o);});
  sel.onchange=function(){
    const layer=this.value;if(!_pvGeo)return;
    _pvGeo.eachLayer(l=>{
      const p=(l.feature&&l.feature.properties)||{};
      const ok=!layer||p.layer_name===layer;
      if(l.setStyle)l.setStyle({opacity:ok?1:.1,fillOpacity:ok?.12:.01});
    });
  };
}

// ── Standards v2.2 ───────────────────────────────────────
const V2 = {
  stdId: null,   // 当前选中的标准 ID
  layerId: null, // 当前选中的层组 ID
  tableId: null, // 当前选中的表 ID
  dictId: null,  // 当前选中的字典 ID
  dictTab: 'global', // 字典面板当前 tab: global(全局字典) / standard(标准字典)
  _stdFile: null,
};

function initStds() {
  v2LoadStandards();
  v2LoadDicts(); // 默认加载全局字典（独立维度，不依赖选中标准/表）
}

// ── Standards List ──────────────────────────────────────
async function v2LoadStandards() {
  const el = document.getElementById('v2StdList');
  el.innerHTML = '<div style="padding:12px;text-align:center;color:var(--t2);">加载中...</div>';
  try {
    const r = await GET('/api/v1/data-standards-v2?page_size=50');
    const list = r.items || r;
    document.getElementById('v2StdCount').textContent = `共 ${Array.isArray(list) ? list.length : 0} 个标准`;
    if (!Array.isArray(list) || !list.length) {
      el.innerHTML = '<div class="empty" style="padding:20px;"><p>暂无数据标准</p></div>';
      return;
    }
    el.innerHTML = list.map(s => `
      <div class="v2-std-item" onclick="v2SelectStd('${esc(s.id)}')" style="padding:10px 12px;border-radius:var(--r8);cursor:pointer;margin-bottom:2px;transition:var(--tr);border:1px solid transparent;${V2.stdId === s.id ? 'background:var(--aglow);border-color:var(--accent);' : ''}">
        <div style="display:flex;align-items:center;gap:6px;">
          <div style="font-size:13px;font-weight:600;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${V2.stdId === s.id ? 'color:var(--accent);' : ''}">${esc(s.name)}</div>
          <div style="display:flex;gap:2px;flex-shrink:0;" onclick="event.stopPropagation();">
            <button class="btn btn-g btn-sm" onclick="v2OpenGraph('${esc(s.id)}','${esc(s.name)}')" style="padding:2px 5px;font-size:10px;opacity:.6;" title="知识图谱（纯视图）">🕸</button>
            <button class="btn btn-g btn-sm" onclick="v2EditStd('${esc(s.id)}','${esc(s.name)}','${esc(s.description||'')}','${esc(s.category)}','${esc(s.status)}')" style="padding:2px 5px;font-size:10px;opacity:.6;" title="编辑">✏️</button>
            <button class="btn btn-d btn-sm" onclick="v2DelStd('${esc(s.id)}','${esc(s.name)}')" style="padding:2px 5px;font-size:10px;opacity:.6;" title="删除">🗑️</button>
          </div>
        </div>
        <div style="font-size:11px;color:var(--t2);margin-top:2px;display:flex;gap:8px;align-items:center;">
          <span>${esc(s.category || 'custom')}</span>
          <span>${s.status === 'published' ? '🟢 已发布' : s.status === 'archived' ? '🔴 已归档' : '⚪ 草稿'}</span>
          <span style="margin-left:auto;font-size:10px;color:var(--t2);">v${s.version||1}</span>
        </div>
        ${s.description ? `<div style="font-size:11px;color:var(--t2);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(s.description).slice(0, 40)}</div>` : ''}
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = `<div class="empty" style="padding:20px;"><p style="color:var(--red);">加载失败: ${esc(e.message)}</p></div>`;
  }
}

function v2SelectStd(id) {
  V2.stdId = id;
  V2.layerId = null;
  V2.tableId = null;
  v2LoadStandards(); // 刷新选中状态
  v2LoadLayers();
  v2LoadDicts();
  v2LoadVersions();
  // 清空右侧
  document.getElementById('v2TableInfo').innerHTML = '<div class="empty" style="padding:16px;"><p>选择一个层组查看数据表</p></div>';
  document.getElementById('v2FieldsArea').innerHTML = '<div class="empty" style="padding:20px;"><p>选择一个数据表查看字段</p></div>';
  document.getElementById('v2TableTitle').textContent = '🗂 数据表';
  const ownEl = document.getElementById('v2TableOwnership');
  if (ownEl) ownEl.style.display = 'none';
}

async function v2CreateStd() {
  const name = prompt('输入标准名称:');
  if (!name) return;
  const desc = prompt('输入描述（可选）:');
  const cat = prompt('分类 (national/industry/custom):', 'custom') || 'custom';
  try {
    const r = await POST('/api/v1/data-standards-v2', { name, description: desc || null, category: cat });
    toast('标准创建成功', 'success');
    V2.stdId = r.id;
    v2LoadStandards();
    v2LoadLayers();
    v2LoadDicts();
    v2LoadVersions();
  } catch (e) { toast('创建失败: ' + e.message, 'error'); }
}

async function v2DelStd(id, name) {
  if (!confirm(`确定删除标准"${name}"？此操作不可恢复。`)) return;
  try {
    await fetch(S.base + '/api/v1/data-standards-v2/' + id, { method: 'DELETE', headers: ah() });
    toast('已删除', 'success');
    if (V2.stdId === id) { V2.stdId = null; V2.layerId = null; V2.tableId = null; }
    v2LoadStandards();
    v2LoadDicts(); // 标准删除后刷新字典面板（standard tab 恢复"请选择数据标准"提示）
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

function v2EditStd(id, name, desc, category, status) {
  // 创建编辑弹窗
  const old = document.getElementById('v2EditModal');
  if (old) old.remove();
  const cats = ['national','industry','custom'];
  const stats = ['draft','published','archived'];
  const catOpts = cats.map(c => `<option value="${c}" ${c===category?'selected':''}>${c}</option>`).join('');
  const statOpts = stats.map(s => `<option value="${s}" ${s===status?'selected':''}>${s==='draft'?'草稿':s==='published'?'已发布':'已归档'}</option>`).join('');
  const modal = document.createElement('div');
  modal.id = 'v2EditModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:300;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(3px);';
  modal.innerHTML = `
    <div style="background:var(--bg-2);border:1px solid var(--border);border-radius:var(--r12);width:420px;padding:24px;position:relative;">
      <div style="font-size:15px;font-weight:600;margin-bottom:16px;">✏️ 编辑标准</div>
      <div style="margin-bottom:12px;">
        <label style="font-size:12px;color:var(--t1);display:block;margin-bottom:4px;">名称</label>
        <input type="text" id="v2EditName" value="${esc(name)}" style="width:100%;padding:8px 12px;background:var(--bg-1);border:1px solid var(--border-hi);color:var(--t0);border-radius:var(--r8);font-size:13px;box-sizing:border-box;">
      </div>
      <div style="margin-bottom:12px;">
        <label style="font-size:12px;color:var(--t1);display:block;margin-bottom:4px;">描述</label>
        <textarea id="v2EditDesc" rows="3" style="width:100%;padding:8px 12px;background:var(--bg-1);border:1px solid var(--border-hi);color:var(--t0);border-radius:var(--r8);font-size:13px;resize:vertical;box-sizing:border-box;">${esc(desc)}</textarea>
      </div>
      <div style="display:flex;gap:12px;margin-bottom:16px;">
        <div style="flex:1;">
          <label style="font-size:12px;color:var(--t1);display:block;margin-bottom:4px;">分类</label>
          <select id="v2EditCat" style="width:100%;padding:8px 12px;background:var(--bg-1);border:1px solid var(--border-hi);color:var(--t0);border-radius:var(--r8);font-size:13px;">${catOpts}</select>
        </div>
        <div style="flex:1;">
          <label style="font-size:12px;color:var(--t1);display:block;margin-bottom:4px;">状态</label>
          <select id="v2EditStatus" style="width:100%;padding:8px 12px;background:var(--bg-1);border:1px solid var(--border-hi);color:var(--t0);border-radius:var(--r8);font-size:13px;">${statOpts}</select>
        </div>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;">
        <button class="btn btn-g btn-sm" onclick="document.getElementById('v2EditModal').remove()">取消</button>
        <button class="btn btn-p btn-sm" onclick="v2SaveStd('${esc(id)}')">💾 保存</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.getElementById('v2EditName').focus();
}

async function v2SaveStd(id) {
  const name = document.getElementById('v2EditName').value.trim();
  if (!name) { toast('名称不能为空', 'error'); return; }
  const desc = document.getElementById('v2EditDesc').value.trim();
  const category = document.getElementById('v2EditCat').value;
  const status = document.getElementById('v2EditStatus').value;
  try {
    await PUT('/api/v1/data-standards-v2/' + id, { name, description: desc || null, category, status });
    toast('保存成功', 'success');
    document.getElementById('v2EditModal').remove();
    v2LoadStandards();
  } catch (e) { toast('保存失败: ' + e.message, 'error'); }
}

// ── Layer Group Tree ────────────────────────────────────
async function v2LoadLayers() {
  const el = document.getElementById('v2LayerTree');
  if (!V2.stdId) { el.innerHTML = '<div class="empty" style="padding:20px;"><p>选择一个标准查看层组</p></div>'; return; }
  el.innerHTML = '<div style="padding:12px;text-align:center;color:var(--t2);">加载中...</div>';
  try {
    const list = await GET('/api/v1/data-standards-v2/' + V2.stdId + '/layers');
    if (!list || !list.length) {
      el.innerHTML = '<div class="empty" style="padding:20px;"><p>暂无层组，点击 ➕ 新建</p></div>';
      return;
    }
    el.innerHTML = _renderLayerTree(list, 0);
  } catch (e) {
    el.innerHTML = `<div class="empty" style="padding:20px;"><p style="color:var(--red);">加载失败: ${esc(e.message)}</p></div>`;
  }
}

function _renderLayerTree(layers, depth) {
  return layers.map(l => {
    const indent = depth * 16;
    const hasChildren = l.children && l.children.length;
    const isActive = V2.layerId === l.id;
    const tableCount = l.table_count || l.tables_count || 0;
    let html = `<div class="v2-layer-item" onclick="v2SelectLayer('${esc(l.id)}')" style="padding:8px 10px 8px ${12 + indent}px;border-radius:var(--r6);cursor:pointer;margin-bottom:1px;transition:var(--tr);display:flex;align-items:center;gap:8px;${isActive ? 'background:var(--aglow);border:1px solid var(--accent);' : 'border:1px solid transparent;'}">
      <span style="font-size:14px;${hasChildren ? '' : 'opacity:.4;'}">${hasChildren ? '📂' : '📁'}</span>
      <div style="flex:1;min-width:0;">
        <div style="font-size:12px;font-weight:${isActive ? '600' : '500'};${isActive ? 'color:var(--accent);' : ''}">${esc(l.group_name)}</div>
        <div style="font-size:10px;color:var(--t2);">${esc(l.group_code || '')} · ${tableCount} 个表</div>
      </div>
      <div style="display:flex;gap:2px;" onclick="event.stopPropagation();">
        <button class="btn btn-g btn-sm" onclick="v2ShowLayerOp('move','${esc(l.id)}','${esc(l.group_name)}')" style="padding:2px 5px;font-size:10px;opacity:.5;" title="移动">↕</button>
        <button class="btn btn-g btn-sm" onclick="v2ShowLayerOp('copy','${esc(l.id)}','${esc(l.group_name)}')" style="padding:2px 5px;font-size:10px;opacity:.5;" title="复制">⧉</button>
        <button class="btn btn-d btn-sm" onclick="v2DelLayer('${esc(l.id)}','${esc(l.group_name)}')" style="padding:2px 5px;font-size:10px;opacity:.5;" title="删除">🗑</button>
      </div>
    </div>`;
    if (hasChildren) {
      html += _renderLayerTree(l.children, depth + 1);
    }
    return html;
  }).join('');
}

function v2SelectLayer(id) {
  V2.layerId = id;
  V2.tableId = null;
  v2LoadLayers(); // 刷新选中
  v2LoadLayerTables(id);
  document.getElementById('v2FieldsArea').innerHTML = '<div class="empty" style="padding:20px;"><p>选择一个数据表查看字段</p></div>';
  const ownEl = document.getElementById('v2TableOwnership');
  if (ownEl) ownEl.style.display = 'none';
}

// 递归展开层组树为扁平列表（带缩进）
function _flatLayers(layers, depth, out) {
  out = out || [];
  (layers || []).forEach(l => {
    out.push({ id: l.id, name: '　'.repeat(depth) + l.group_name });
    if (l.children && l.children.length) _flatLayers(l.children, depth + 1, out);
  });
  return out;
}

async function v2AddLayer() {
  if (!V2.stdId) { toast('请先选择一个标准', 'error'); return; }
  const name = prompt('输入层组名称:');
  if (!name) return;
  const code = prompt('层组编码（可选）:');
  // 父级用下拉选择（替代手输 UUID），避免输错 UUID 导致关联失败
  let layers = [];
  try { layers = (await GET('/api/v1/data-standards-v2/' + V2.stdId + '/layers')) || []; } catch (e) {}
  const flat = _flatLayers(layers, 0);
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:1200;display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = `<div style="background:var(--bg-1);border:1px solid var(--border);border-radius:var(--r12);padding:24px;min-width:360px;max-width:480px;">
    <h4 style="margin-bottom:12px;">新建层组：${esc(name)}</h4>
    <label style="display:block;margin-bottom:6px;font-size:13px;color:var(--t1);">父级层组（默认顶层）</label>
    <select id="v2AddLayerParent" style="width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:var(--r6);background:var(--bg-1);color:var(--t0);font-size:14px;">
      <option value="">— 顶层 —</option>
      ${flat.map(l => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join('')}
    </select>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">
      <button class="btn" onclick="this.closest('div').parentElement.remove()">取消</button>
      <button class="btn btn-p" id="v2AddLayerGo">创建</button>
    </div></div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('#v2AddLayerGo').onclick = async () => {
    const parentId = overlay.querySelector('#v2AddLayerParent').value || null;
    try {
      await POST('/api/v1/data-standards-v2/' + V2.stdId + '/layers', {
        group_name: name,
        group_code: code || null,
        parent_id: parentId,
      });
      toast('层组创建成功', 'success');
      overlay.remove();
      v2LoadLayers();
    } catch (e) { toast('创建失败: ' + e.message, 'error'); }
  };
}

async function v2DelLayer(id, name) {
  if (!confirm(`确定删除层组"${name}"？`)) return;
  try {
    await fetch(S.base + '/api/v1/layers/' + id, { method: 'DELETE', headers: ah() });
    toast('已删除', 'success');
    if (V2.layerId === id) { V2.layerId = null; V2.tableId = null; }
    v2LoadLayers();
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

// ── Layer Move/Copy ─────────────────────────────────────
function v2ShowLayerOp(op, layerId, layerName) {
  document.getElementById('v2LayerOpId').value = layerId;
  document.getElementById('v2LayerOpType').value = op;
  document.getElementById('v2LayerOpTitle').textContent = (op === 'move' ? '↕ 移动层组 — ' : '⧉ 复制层组 — ') + layerName;
  document.getElementById('v2LayerOpConfirm').textContent = op === 'move' ? '确认移动' : '确认复制';

  const body = document.getElementById('v2LayerOpBody');
  if (op === 'move') {
    // S9: 移动仅限当前标准内（后端 B3 拦截跨标准）；跨标准请用复制
    body.innerHTML = `
      <div style="padding:8px 10px;margin-bottom:12px;font-size:12px;color:var(--t2);background:rgba(255,170,60,.08);border:1px solid rgba(255,170,60,.25);border-radius:var(--r8);">
        移动仅限当前标准内。跨标准放置请使用「复制」后删除原层组。
      </div>
      <div class="fg" style="margin-bottom:12px;">
        <label>目标父层组（留空=移到顶层）</label>
        <select id="v2LoTargetParent" style="background:var(--bg-1);border:1px solid var(--border-hi);color:var(--t0);padding:9px 12px;border-radius:var(--r8);font-size:13px;width:100%;">
          <option value="">顶层</option>
        </select>
      </div>`;
    _loadMoveParentLayers();
  } else {
    body.innerHTML = `
      <div class="fg" style="margin-bottom:12px;">
        <label>新名称</label>
        <input type="text" id="v2LoCopyName" placeholder="复制后的名称" style="background:var(--bg-1);border:1px solid var(--border-hi);color:var(--t0);padding:9px 12px;border-radius:var(--r8);font-size:13px;width:100%;">
      </div>
      <div class="fg" style="margin-bottom:12px;">
        <label>目标标准（S9：支持跨标准复制）</label>
        <select id="v2LoCopyStd" style="background:var(--bg-1);border:1px solid var(--border-hi);color:var(--t0);padding:9px 12px;border-radius:var(--r8);font-size:13px;width:100%;"></select>
      </div>
      <div class="fg" style="margin-bottom:12px;">
        <label>目标父层组（留空=复制到顶层/同级）</label>
        <select id="v2LoTargetParent" style="background:var(--bg-1);border:1px solid var(--border-hi);color:var(--t0);padding:9px 12px;border-radius:var(--r8);font-size:13px;width:100%;">
          <option value="">顶层</option>
        </select>
      </div>`;
    _loadCopyTargetStdList();
  }
  document.getElementById('v2LayerOpModal').style.display = 'block';
}

async function _loadMoveParentLayers() {
  // 移动：仅加载当前标准的层组
  const sel = document.getElementById('v2LoTargetParent');
  sel.innerHTML = '<option value="">顶层</option>';
  try {
    const list = await GET('/api/v1/data-standards-v2/' + V2.stdId + '/layers');
    if (list && list.length) sel.innerHTML += _buildLayerOptions(list, 0);
  } catch (e) {}
}

async function _loadCopyTargetStdList() {
  const sel = document.getElementById('v2LoCopyStd');
  try {
    const r = await GET('/api/v1/data-standards-v2?page_size=50');
    const list = r.items || r;
    sel.innerHTML = (Array.isArray(list) ? list : []).map(s =>
      `<option value="${esc(s.id)}" ${s.id === V2.stdId ? 'selected' : ''}>${esc(s.name)}</option>`
    ).join('');
    sel.onchange = () => _loadCopyTargetParentList(sel.value);
    _loadCopyTargetParentList(sel.value);
  } catch (e) { sel.innerHTML = '<option value="">加载失败</option>'; }
}

async function _loadCopyTargetParentList(stdId) {
  const sel = document.getElementById('v2LoTargetParent');
  sel.innerHTML = '<option value="">顶层</option>';
  try {
    const list = await GET('/api/v1/data-standards-v2/' + stdId + '/layers');
    if (list && list.length) sel.innerHTML += _buildLayerOptions(list, 0);
  } catch (e) {}
}

function _buildLayerOptions(layers, depth) {
  return layers.map(l => {
    let opt = `<option value="${esc(l.id)}">${'—'.repeat(depth)} ${esc(l.group_name)}</option>`;
    if (l.children && l.children.length) opt += _buildLayerOptions(l.children, depth + 1);
    return opt;
  }).join('');
}

async function v2ConfirmLayerOp() {
  const id = document.getElementById('v2LayerOpId').value;
  const op = document.getElementById('v2LayerOpType').value;
  if (op === 'attach') {
    // 归属面板的内部按钮自行处理操作，确认键仅关闭
    document.getElementById('v2LayerOpModal').style.display = 'none';
    return;
  }
  try {
    if (op === 'move') {
      // S9: 移动仅在当前标准内，不传 new_standard_id（跨标准由后端拦截）
      const targetParent = document.getElementById('v2LoTargetParent').value;
      await POST('/api/v1/layers/' + id + '/move', {
        new_parent_id: targetParent || null,
      });
      toast('层组已移动', 'success');
    } else {
      const newName = document.getElementById('v2LoCopyName').value.trim();
      if (!newName) { toast('请输入新名称', 'error'); return; }
      const targetStd = document.getElementById('v2LoCopyStd').value;
      const targetParent = document.getElementById('v2LoTargetParent').value;
      await POST('/api/v1/layers/' + id + '/copy', {
        new_name: newName,
        new_standard_id: targetStd || undefined,
        new_parent_id: targetParent || null,
      });
      toast('层组已复制', 'success');
    }
    document.getElementById('v2LayerOpModal').style.display = 'none';
    v2LoadLayers();
  } catch (e) { toast('操作失败: ' + e.message, 'error'); }
}

// ── S9: 表归属管理（挂载/摘挂/主归属转移）──────────────
async function v2ShowTableAttach(tableId, tableName) {
  document.getElementById('v2LayerOpId').value = tableId;
  document.getElementById('v2LayerOpType').value = 'attach';
  document.getElementById('v2LayerOpTitle').textContent = '⛓ 表归属管理' + (tableName ? ' — ' + tableName : '');
  document.getElementById('v2LayerOpConfirm').textContent = '关闭';
  const body = document.getElementById('v2LayerOpBody');
  body.innerHTML = '<div style="padding:8px;color:var(--t2);">加载中...</div>';
  document.getElementById('v2LayerOpModal').style.display = 'block';
  try {
    const links = await GET('/api/v1/tables/' + tableId + '/layers');
    let rows = '';
    if (links && links.length) {
      rows = '<div style="font-size:12px;color:var(--t1);font-weight:600;margin-bottom:4px;">当前归属（主归属唯一）</div>' +
        links.map(l => `<div style="display:flex;align-items:center;gap:8px;padding:6px 2px;border-bottom:1px solid var(--border);font-size:13px;">
          <span style="flex:1;min-width:0;">${l.is_primary ? '★ ' : ''}${esc(l.layer_group.group_name)} <span style="font-size:10px;color:var(--t2);">${esc(l.layer_group.group_code || '')}</span></span>
          <span class="badge ${l.is_primary ? 'bjson' : 'bpend'}" style="font-size:10px;">${l.is_primary ? '主归属' : '次归属'}</span>
          ${l.is_primary ? '' : `<button class="btn btn-d btn-sm" onclick="v2DetachTable('${esc(l.layer_group.id)}','${esc(l.layer_group.group_name)}')" style="padding:2px 6px;font-size:10px;">摘挂</button>`}
        </div>`).join('');
    }
    let layers = [];
    try { layers = (await GET('/api/v1/data-standards-v2/' + V2.stdId + '/layers')) || []; } catch (e) {}
    const flat = _flatLayers(layers, 0);
    const opts = flat.filter(l => !(links || []).some(x => x.layer_group.id === l.id))
      .map(l => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join('');
    const attachForm = `<div style="margin-top:12px;">
      <div style="font-size:12px;color:var(--t1);font-weight:600;margin-bottom:6px;">挂载到其他层组</div>
      ${opts ? `
      <select id="v2AttachTarget" style="width:100%;padding:8px 12px;border:1px solid var(--border-hi);background:var(--bg-1);color:var(--t0);border-radius:var(--r8);font-size:13px;margin-bottom:8px;">
        <option value="">选择层组...</option>${opts}
      </select>
      <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--t1);margin-bottom:8px;cursor:pointer;">
        <input type="checkbox" id="v2AttachPrimary"> 设为目标层组的主归属（主归属将转移并同步该表的默认归属）
      </label>
      <button class="btn btn-p btn-sm" onclick="v2AttachTable()" style="padding:6px 14px;font-size:12px;">挂载</button>
      ` : '<div style="font-size:12px;color:var(--t2);">该表已归属所有层组</div>'}
    </div>`;
    body.innerHTML = rows + attachForm;
  } catch (e) {
    body.innerHTML = '<div class="empty" style="padding:16px;"><p style="color:var(--red);">加载失败: ' + esc(e.message) + '</p></div>';
  }
}

async function v2AttachTable() {
  const tableId = document.getElementById('v2LayerOpId').value;
  const layerId = document.getElementById('v2AttachTarget').value;
  const isPrimary = document.getElementById('v2AttachPrimary').checked;
  if (!layerId) { toast('请选择目标层组', 'error'); return; }
  try {
    await POST('/api/v1/layers/' + layerId + '/tables/' + tableId + '/attach', { is_primary: isPrimary });
    toast(isPrimary ? '主归属已转移' : '已挂载', 'success');
    v2ShowTableAttach(tableId, '');
    v2LoadLayerTables(V2.layerId);
  } catch (e) { toast('挂载失败: ' + e.message, 'error'); }
}

async function v2DetachTable(layerId, layerName) {
  if (!confirm(`从"${layerName}"摘挂该表？`)) return;
  const tableId = document.getElementById('v2LayerOpId').value;
  try {
    await fetch(S.base + '/api/v1/layers/' + layerId + '/tables/' + tableId + '/attach', { method: 'DELETE', headers: ah() });
    toast('已摘挂', 'success');
    v2ShowTableAttach(tableId, '');
    v2LoadLayerTables(V2.layerId);
  } catch (e) { toast('摘挂失败: ' + e.message, 'error'); }
}

// ── Tables ──────────────────────────────────────────────
async function v2LoadLayerTables(layerId) {
  const el = document.getElementById('v2TableInfo');
  el.innerHTML = '<div style="padding:12px;text-align:center;color:var(--t2);">加载中...</div>';
  try {
    const list = await GET('/api/v1/layers/' + layerId + '/tables');
    if (!list || !list.length) {
      el.innerHTML = '<div class="empty" style="padding:16px;"><p>暂无数据表</p></div>';
      return;
    }
    document.getElementById('v2TableTitle').textContent = `🗂 数据表 (${list.length})`;
    el.innerHTML = '<div style="display:flex;flex-direction:column;gap:6px;">' + list.map(t => `
      <div onclick="v2SelectTable('${esc(t.id)}','${esc(t.table_name)}')" style="padding:10px 12px;background:var(--bg-1);border:1px solid var(--border);border-radius:var(--r8);cursor:pointer;transition:var(--tr);${V2.tableId === t.id ? 'border-color:var(--accent);background:var(--aglow);' : ''}">
        <div style="display:flex;align-items:center;justify-content:space-between;">
          <span style="font-size:13px;font-weight:500;display:flex;align-items:center;gap:6px;">📋 ${esc(t.table_name)}
            ${t.is_primary
              ? '<span style="font-size:10px;padding:1px 6px;border-radius:var(--r4);background:rgba(80,200,120,.16);color:#7ddb9a;border:1px solid rgba(80,200,120,.35);" title="主归属层组">主</span>'
              : '<span style="font-size:10px;padding:1px 6px;border-radius:var(--r4);background:rgba(255,170,60,.14);color:#f2b25c;border:1px solid rgba(255,170,60,.3);" title="次归属层组（主归属在别处）">次</span>'}
          </span>
          <button class="btn btn-g btn-sm" onclick="event.stopPropagation();v2ShowTableAttach('${esc(t.id)}','${esc(t.table_name)}')" style="padding:2px 6px;font-size:10px;opacity:.7;" title="挂载到其他层组">⛓</button>
          <button class="btn btn-d btn-sm" onclick="event.stopPropagation();v2DelTable('${esc(t.id)}','${esc(t.table_name)}')" style="padding:2px 6px;font-size:10px;">🗑</button>
        </div>
        <div style="font-size:11px;color:var(--t2);margin-top:3px;display:flex;gap:8px;">
          <span class="mono" style="color:var(--accent);">${esc(t.table_code || '')}</span>
          <span>${_v2GeomBadge(t.geometry_type)}</span>
          <span>${t.field_count || 0} 字段</span>
        </div>
      </div>
    `).join('') + '</div>';
  } catch (e) {
    el.innerHTML = `<div class="empty" style="padding:16px;"><p style="color:var(--red);">加载失败: ${esc(e.message)}</p></div>`;
  }
}

function _v2GeomBadge(g) {
  if (!g) return '<span style="font-size:10px;color:var(--t2);">属性表</span>';
  const m = {Point:'📍点', MultiPoint:'📍多点', LineString:'📏线', MultiLineString:'📏多线', Polygon:'🔲面', MultiPolygon:'🔲多面'};
  return `<span style="font-size:10px;color:var(--cyan);">${m[g] || g}</span>`;
}

function v2SelectTable(id, name) {
  V2.tableId = id;
  document.getElementById('v2TableTitle').textContent = `🗂 ${name}`;
  v2LoadLayerTables(V2.layerId); // 刷新选中
  v2LoadTableOwnership(id);
  v2LoadFields(id);
}

// ── S9: 表归属（多分类）面板 ──────────────────────────
async function v2LoadTableOwnership(tableId) {
  const el = document.getElementById('v2TableOwnership');
  if (!el) return;
  try {
    const links = await GET('/api/v1/tables/' + tableId + '/layers');
    if (!links || !links.length) { el.style.display = 'none'; return; }
    el.style.display = 'block';
    el.innerHTML = '<span style="font-weight:600;margin-right:6px;">🏷 归属层组</span>' + links.map(l =>
      `<span onclick="v2SelectLayer('${esc(l.layer_group.id)}')" style="display:inline-flex;align-items:center;gap:4px;margin-right:8px;padding:2px 8px;background:var(--bg-1);border:1px solid ${l.is_primary ? 'rgba(80,200,120,.4)' : 'var(--border)'};border-radius:var(--r8);font-size:11px;cursor:pointer;" title="点击跳转到该层组">
        ${esc(l.layer_group.group_name)}
        <span style="color:${l.is_primary ? '#7ddb9a' : 'var(--t2)'};">${l.is_primary ? '主' : '次'}</span>
      </span>`).join('');
  } catch (e) { el.style.display = 'none'; }
}

async function v2AddTable() {
  if (!V2.layerId) { toast('请先选择一个层组', 'error'); return; }
  const name = prompt('输入表名:');
  if (!name) return;
  const code = prompt('表编码（可选）:');
  const geom = prompt('几何类型（Point/LineString/Polygon/留空=属性表）:') || null;
  try {
    await POST('/api/v1/layers/' + V2.layerId + '/tables', {
      table_name: name,
      table_code: code || null,
      table_type: geom ? 'spatial' : 'attribute',
      geometry_type: geom || null,
    });
    toast('表创建成功', 'success');
    v2LoadLayerTables(V2.layerId);
  } catch (e) { toast('创建失败: ' + e.message, 'error'); }
}

async function v2DelTable(id, name) {
  if (!confirm(`确定删除表"${name}"？`)) return;
  try {
    await fetch(S.base + '/api/v1/tables/' + id, { method: 'DELETE', headers: ah() });
    toast('已删除', 'success');
    if (V2.tableId === id) { V2.tableId = null; }
    v2LoadLayerTables(V2.layerId);
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

// ── Fields ──────────────────────────────────────────────
async function v2LoadFields(tableId) {
  const el = document.getElementById('v2FieldsArea');
  el.innerHTML = '<div style="padding:12px;text-align:center;color:var(--t2);">加载中...</div>';
  try {
    const list = await GET('/api/v1/tables/' + tableId + '/fields');
    if (!list || !list.length) {
      el.innerHTML = '<div class="empty" style="padding:20px;"><p>暂无字段</p></div>';
      return;
    }
    el.innerHTML = '<table class="dt"><thead><tr><th>字段名</th><th>中文名</th><th>类型</th><th>长度</th><th>必填</th><th>主键</th><th>字典</th><th>操作</th></tr></thead><tbody>' +
      list.map(f => `<tr>
        <td class="mono" style="font-weight:600;font-size:12px;">${esc(f.field_name)}</td>
        <td style="font-size:12px;">${esc(f.field_alias || '')}</td>
        <td><span class="badge bjson" style="font-size:10px;">${esc(f.data_type)}</span></td>
        <td class="mono" style="font-size:12px;">${f.length || ''}</td>
        <td>${f.is_required ? '✅' : ''}</td>
        <td>${f.is_primary_key ? '🔑' : ''}</td>
        <td style="font-size:11px;">${(f.dict_links || []).length}</td>
        <td>
          <button class="btn btn-g btn-sm" onclick="v2EditField('${esc(f.id)}')" style="padding:2px 6px;font-size:10px;">✏</button>
          <button class="btn btn-d btn-sm" onclick="v2DelField('${esc(f.id)}','${esc(f.field_name)}')" style="padding:2px 6px;font-size:10px;">🗑</button>
        </td>
      </tr>`).join('') +
      '</tbody></table>';
  } catch (e) {
    el.innerHTML = `<div class="empty" style="padding:20px;"><p style="color:var(--red);">加载失败: ${esc(e.message)}</p></div>`;
  }
}

async function v2AddField() {
  if (!V2.tableId) { toast('请先选择一个数据表', 'error'); return; }
  const name = prompt('字段名:');
  if (!name) return;
  const alias = prompt('中文名（可选）:');
  const dtype = prompt('数据类型 (string/int/float/date):', 'string') || 'string';
  const len = prompt('长度（可选）:');
  try {
    await POST('/api/v1/tables/' + V2.tableId + '/fields', {
      field_name: name,
      field_alias: alias || null,
      data_type: dtype,
      length: len ? parseInt(len) : null,
    });
    toast('字段创建成功', 'success');
    v2LoadFields(V2.tableId);
  } catch (e) { toast('创建失败: ' + e.message, 'error'); }
}

async function v2DelField(id, name) {
  if (!confirm(`确定删除字段"${name}"？`)) return;
  try {
    await fetch(S.base + '/api/v1/fields/' + id, { method: 'DELETE', headers: ah() });
    toast('已删除', 'success');
    v2LoadFields(V2.tableId);
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

// ── Field Edit ──────────────────────────────────────────
let _v2FieldDictLinks = [];

async function v2EditField(id) {
  try {
    // 获取字段详情
    const fields = await GET('/api/v1/tables/' + V2.tableId + '/fields');
    const f = fields.find(x => x.id === id);
    if (!f) { toast('字段不存在', 'error'); return; }

    document.getElementById('v2FieldEditId').value = id;
    document.getElementById('v2FieldModalTitle').textContent = '✏ 编辑字段 — ' + f.field_name;
    document.getElementById('v2FeName').value = f.field_name || '';
    document.getElementById('v2FeAlias').value = f.field_alias || '';
    document.getElementById('v2FeType').value = f.data_type || 'string';
    document.getElementById('v2FeLength').value = f.length || '';
    document.getElementById('v2FePrecision').value = f.precision || '';
    document.getElementById('v2FeDesc').value = f.description || '';
    document.getElementById('v2FeRequired').checked = !!f.is_required;
    document.getElementById('v2FePK').checked = !!f.is_primary_key;
    document.getElementById('v2FeNullable').checked = f.nullable !== false;

    // 加载字典关联
    _v2RemovedLinkIds = [];
    _v2FieldDictLinks = f.dict_links || [];
    v2RenderFieldDictLinks();
    document.getElementById('v2FieldModal').style.display = 'block';
  } catch (e) { toast('加载字段信息失败: ' + e.message, 'error'); }
}

function v2RenderFieldDictLinks() {
  const el = document.getElementById('v2FeDictLinks');
  if (!_v2FieldDictLinks.length) {
    el.innerHTML = '<div style="font-size:12px;color:var(--t2);padding:8px 0;">暂无字典关联</div>';
    return;
  }
  el.innerHTML = _v2FieldDictLinks.map((link, i) => `
    <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:var(--bg-1);border:1px solid var(--border);border-radius:var(--r6);margin-bottom:6px;">
      <span style="font-size:13px;">📖 ${esc(link.dict_name || link.dict_id)}</span>
      <span class="badge ${link.is_primary ? 'bjson' : 'bpend'}" style="font-size:10px;">${link.is_primary ? '严格' : '建议'}</span>
      <div style="flex:1;"></div>
      <button class="btn btn-d btn-sm" onclick="v2FeRemoveDictLink(${i})" style="padding:2px 6px;font-size:10px;">🗑</button>
    </div>
  `).join('');
}

let _v2RemovedLinkIds = [];

function v2FeRemoveDictLink(index) {
  const link = _v2FieldDictLinks[index];
  if (link.id) {
    _v2RemovedLinkIds.push(link.id);
  }
  _v2FieldDictLinks.splice(index, 1);
  v2RenderFieldDictLinks();
}

function v2FeAddDictLink() {
  // 加载可用字典列表
  const sel = document.getElementById('v2DlDictId');
  sel.innerHTML = '<option value="">加载中...</option>';
  document.getElementById('v2DictLinkModal').style.display = 'block';

  // 加载全局字典 + 当前标准字典
  Promise.all([
    GET('/api/v1/dictionaries').catch(() => []),
    V2.stdId ? GET('/api/v1/data-standards-v2/' + V2.stdId + '/dictionaries').catch(() => []) : Promise.resolve([]),
  ]).then(([globalDicts, stdDicts]) => {
    const all = [];
    (stdDicts || []).forEach(d => all.push({ ...d, _label: '📋 ' + d.dict_name + ' (标准)' }));
    (globalDicts || []).forEach(d => all.push({ ...d, _label: '🌐 ' + d.dict_name + ' (全局)' }));
    // 去重
    const seen = new Set();
    const unique = all.filter(d => { if (seen.has(d.id)) return false; seen.add(d.id); return true; });
    sel.innerHTML = unique.length
      ? unique.map(d => `<option value="${esc(d.id)}">${esc(d._label)}</option>`).join('')
      : '<option value="">无可用字典</option>';
  });
}

function v2ConfirmDictLink() {
  const dictId = document.getElementById('v2DlDictId').value;
  const mode = document.getElementById('v2DlMode').value;
  if (!dictId) { toast('请选择字典', 'error'); return; }
  // 检查重复
  if (_v2FieldDictLinks.some(l => l.dict_id === dictId)) { toast('该字典已关联', 'error'); return; }
  _v2FieldDictLinks.push({ dict_id: dictId, is_primary: mode === 'strict' });
  v2RenderFieldDictLinks();
  document.getElementById('v2DictLinkModal').style.display = 'none';
  toast('已添加关联，保存时生效', 'info');
}

async function v2SaveField() {
  const id = document.getElementById('v2FieldEditId').value;
  const name = document.getElementById('v2FeName').value.trim();
  if (!name) { toast('字段名不能为空', 'error'); return; }
  const body = {
    field_name: name,
    field_alias: document.getElementById('v2FeAlias').value.trim() || null,
    data_type: document.getElementById('v2FeType').value,
    length: document.getElementById('v2FeLength').value ? parseInt(document.getElementById('v2FeLength').value) : null,
    precision: document.getElementById('v2FePrecision').value ? parseInt(document.getElementById('v2FePrecision').value) : null,
    description: document.getElementById('v2FeDesc').value.trim() || null,
    is_required: document.getElementById('v2FeRequired').checked,
    is_primary_key: document.getElementById('v2FePK').checked,
    nullable: document.getElementById('v2FeNullable').checked,
  };
  try {
    await PUT('/api/v1/fields/' + id, body);
    // 删除被移除的字典关联
    for (const linkId of _v2RemovedLinkIds) {
      await fetch(S.base + '/api/v1/field-dict-links/' + linkId, { method: 'DELETE', headers: ah() }).catch(() => {});
    }
    // 保存新添加的字典关联
    for (const link of _v2FieldDictLinks) {
      if (!link.id) {
        await POST('/api/v1/fields/' + id + '/dict-links', { dict_id: link.dict_id, is_primary: link.is_primary }).catch(() => {});
      }
    }
    document.getElementById('v2FieldModal').style.display = 'none';
    _v2RemovedLinkIds = [];
    toast('字段保存成功', 'success');
    v2LoadFields(V2.tableId);
  } catch (e) { toast('保存失败: ' + e.message, 'error'); }
}

// ── Dictionaries ────────────────────────────────────────
function v2DictTab(tab) {
  V2.dictTab = tab;
  document.getElementById('v2DictTabGlobal').classList.toggle('active', tab === 'global');
  document.getElementById('v2DictTabStd').classList.toggle('active', tab === 'standard');
  v2LoadDicts();
}

function _v2RenderDictList(list) {
  const el = document.getElementById('v2DictList');
  if (!list || !list.length) {
    el.innerHTML = '<div class="empty" style="padding:20px;"><p>暂无字典</p></div>';
    return;
  }
  const scopeLabel = V2.dictTab === 'global' ? '共享' : '标准';
  const scopeBadge = V2.dictTab === 'global' ? 'bgjson' : 'bpend';
  el.innerHTML = list.map(d => `
    <div class="v2-dict-item" style="padding:10px 12px;border-radius:var(--r8);margin-bottom:2px;transition:var(--tr);border:1px solid var(--border);cursor:pointer;" onclick="v2ShowDictItems('${esc(d.id)}','${esc(d.dict_name)}')">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <span style="font-size:13px;font-weight:500;">📖 ${esc(d.dict_name)}</span>
        <span class="badge ${scopeBadge}" style="font-size:10px;">${scopeLabel}</span>
      </div>
      <div style="font-size:11px;color:var(--t2);margin-top:2px;display:flex;gap:8px;align-items:center;">
        <span class="mono" style="color:var(--accent);">${esc(d.dict_code || '')}</span>
        <span>${d.item_count || 0} 条</span>
        <span style="flex:1;"></span>
        <button class="btn btn-sm bi" onclick="event.stopPropagation();v2RenameDict('${esc(d.id)}','${esc(d.dict_name)}')" title="重命名" style="padding:1px 6px;font-size:10px;background:var(--pdim);color:var(--purple);">✏️</button>
        <button class="btn btn-sm bi" onclick="event.stopPropagation();v2DelDict('${esc(d.id)}','${esc(d.dict_name)}')" title="删除字典" style="padding:1px 6px;font-size:10px;background:var(--rdim);color:var(--red);">🗑</button>
      </div>
    </div>
  `).join('');
}

async function v2LoadDicts() {
  const el = document.getElementById('v2DictList');
  if (V2.dictTab === 'global') {
    // 全局字典：独立维度，不依赖选中标准/表/字段
    el.innerHTML = '<div style="padding:12px;text-align:center;color:var(--t2);">加载中...</div>';
    try {
      const list = await GET('/api/v1/dictionaries?scope=global');
      _v2RenderDictList(list);
    } catch (e) {
      el.innerHTML = `<div class="empty" style="padding:20px;"><p style="color:var(--red);">加载失败: ${esc(e.message)}</p></div>`;
    }
    return;
  }
  // 标准字典：仅依赖当前选中的数据标准（不依赖层组/表/字段）
  if (!V2.stdId) { el.innerHTML = '<div class="empty" style="padding:20px;"><p>请先在左侧选择数据标准</p></div>'; return; }
  el.innerHTML = '<div style="padding:12px;text-align:center;color:var(--t2);">加载中...</div>';
  try {
    const list = await GET('/api/v1/data-standards-v2/' + V2.stdId + '/dictionaries');
    _v2RenderDictList(list);
  } catch (e) {
    el.innerHTML = `<div class="empty" style="padding:20px;"><p style="color:var(--red);">加载失败: ${esc(e.message)}</p></div>`;
  }
}

async function v2AddDict() {
  if (V2.dictTab === 'global') {
    // 创建全局字典：不依赖任何标准
    const name = prompt('全局字典名称:');
    if (!name) return;
    const code = prompt('字典编码（可选）:');
    try {
      await POST('/api/v1/dictionaries', { dict_name: name, dict_code: code || null, scope: 'global' });
      toast('全局字典创建成功', 'success');
      v2LoadDicts();
    } catch (e) { toast('创建失败: ' + e.message, 'error'); }
    return;
  }
  // 创建标准字典：归属当前选中的数据标准
  if (!V2.stdId) { toast('请先选择数据标准', 'error'); return; }
  const name = prompt('字典名称:');
  if (!name) return;
  const code = prompt('字典编码（可选）:');
  try {
    await POST('/api/v1/data-standards-v2/' + V2.stdId + '/dictionaries', {
      dict_name: name,
      dict_code: code || null,
      scope: 'standard',
    });
    toast('字典创建成功', 'success');
    v2LoadDicts();
  } catch (e) { toast('创建失败: ' + e.message, 'error'); }
}

async function v2RenameDict(id, current) {
  const name = prompt('新字典名称:', current);
  if (!name || name === current) return;
  try {
    await PUT('/api/v1/dictionaries/' + id, { dict_name: name });
    toast('字典已重命名', 'success');
    v2LoadDicts();
  } catch (e) { toast('重命名失败: ' + e.message, 'error'); }
}

async function v2DelDict(id, name) {
  if (!confirm('删除字典 "' + name + '"？\n删除后关联字段将解除字典约束，条目一并删除。')) return;
  try {
    const r = await fetch(S.base + '/api/v1/dictionaries/' + id, { method: 'DELETE', headers: ah() });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || 'HTTP ' + r.status); }
    toast('字典已删除', 'success');
    v2LoadDicts();
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

async function v2EditDictItem(itemId, code, currentName) {
  if (!_v2CurDictId) return;
  const name = prompt('编辑条目「' + code + '」名称:', currentName);
  if (!name || name === currentName) return;
  try {
    await PUT('/api/v1/dictionaries/' + _v2CurDictId + '/items', [{ id: itemId, item_name: name }]);
    const title = document.getElementById('v2DictModalTitle').textContent.replace('📖 ', '').replace(' — 条目管理', '');
    v2ShowDictItems(_v2CurDictId, title);
    v2LoadDicts();
    toast('条目已更新', 'success');
  } catch (e) { toast('更新失败: ' + e.message, 'error'); }
}

let _v2CurDictId = null;
async function v2ShowDictItems(dictId, dictName) {
  _v2CurDictId = dictId;
  document.getElementById('v2DictModalTitle').textContent = '📖 ' + dictName + ' — 条目管理';
  document.getElementById('v2DictModal').style.display = 'block';
  const el = document.getElementById('v2DictItemsArea');
  el.innerHTML = '<div style="padding:12px;text-align:center;color:var(--t2);">加载中...</div>';
  try {
    const d = await GET('/api/v1/dictionaries/' + dictId);
    if (!d.items || !d.items.length) {
      el.innerHTML = '<div class="empty" style="padding:16px;"><p>暂无条目</p></div>';
      return;
    }
    el.innerHTML = '<table class="dt"><thead><tr><th>码值</th><th>名称</th><th>父级</th><th>操作</th></tr></thead><tbody>' +
      d.items.map(item => `<tr>
        <td class="mono" style="font-weight:600;font-size:12px;">${esc(item.item_code)}</td>
        <td style="font-size:12px;">${esc(item.item_name)}</td>
        <td class="mono" style="font-size:11px;color:var(--t2);">${esc(item.parent_code || '')}</td>
        <td style="display:flex;gap:4px;">
          <button class="btn btn-sm bi" onclick="v2EditDictItem('${esc(item.id)}','${esc(item.item_code)}','${esc(item.item_name)}')" title="编辑条目" style="padding:1px 6px;font-size:10px;background:var(--pdim);color:var(--purple);">✏️</button>
          <button class="btn btn-sm bi" onclick="v2DelDictItem('${esc(item.id)}')" title="删除条目" style="padding:1px 6px;font-size:10px;background:var(--rdim);color:var(--red);">🗑</button>
        </td>
      </tr>`).join('') +
      '</tbody></table>';
  } catch (e) {
    el.innerHTML = `<div class="empty" style="padding:16px;"><p style="color:var(--red);">加载失败: ${esc(e.message)}</p></div>`;
  }
}

async function v2AddDictItem() {
  if (!_v2CurDictId) return;
  const code = document.getElementById('v2NewItemCode').value.trim();
  const name = document.getElementById('v2NewItemName').value.trim();
  const parentCode = document.getElementById('v2NewItemParent').value.trim() || null;
  if (!code || !name) { toast('请输入码值和名称', 'error'); return; }
  try {
    await POST('/api/v1/dictionaries/' + _v2CurDictId + '/items', [
      { item_code: code, item_name: name, parent_code: parentCode }
    ]);
    document.getElementById('v2NewItemCode').value = '';
    document.getElementById('v2NewItemName').value = '';
    document.getElementById('v2NewItemParent').value = '';
    v2ShowDictItems(_v2CurDictId, document.getElementById('v2DictModalTitle').textContent.replace('📖 ', '').replace(' — 条目管理', ''));
    v2LoadDicts();
    toast('条目添加成功', 'success');
  } catch (e) { toast('添加失败: ' + e.message, 'error'); }
}

async function v2DelDictItem(itemId) {
  if (!confirm('确定删除此条目？')) return;
  if (!_v2CurDictId) return;
  try {
    await fetch(S.base + '/api/v1/dictionaries/' + _v2CurDictId + '/items/' + itemId, {
      method: 'DELETE', headers: ah()
    });
    const title = document.getElementById('v2DictModalTitle').textContent.replace('📖 ', '').replace(' — 条目管理', '');
    v2ShowDictItems(_v2CurDictId, title);
    v2LoadDicts();
    toast('已删除', 'success');
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

// ── Versions ────────────────────────────────────────────
async function v2LoadVersions() {
  const el = document.getElementById('v2VersionList');
  if (!V2.stdId) { el.innerHTML = '<div class="empty" style="padding:20px;"><p>选择一个标准查看版本</p></div>'; return; }
  el.innerHTML = '<div style="padding:12px;text-align:center;color:var(--t2);">加载中...</div>';
  try {
    // 获取版本列表和标准详情（取当前版本号）
    const [list, std] = await Promise.all([
      GET('/api/v1/data-standards-v2/' + V2.stdId + '/versions'),
      GET('/api/v1/data-standards-v2/' + V2.stdId),
    ]);
    const curVer = std.version || 1;
    if (!list || !list.length) {
      el.innerHTML = '<div class="empty" style="padding:20px;"><p>暂无版本记录</p><p style="font-size:11px;color:var(--t2);margin-top:4px;">点击「发布版本」创建第一个版本快照</p></div>';
      return;
    }
    // 版本选择区域
    let html = `<div style="padding:8px 12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;">
      <span style="font-size:11px;color:var(--t2);">选择2个版本进行对比</span>
      <div style="flex:1;"></div>
      <button class="btn btn-g btn-sm" id="v2DiffBtn" onclick="v2ShowDiff()" disabled style="padding:2px 8px;font-size:11px;">📊 对比</button>
    </div>`;
    // 版本列表 — 最新在前
    html += '<div style="padding:6px;">' + list.map(v => {
      const isLatest = v.version_number === Math.max(...list.map(x => x.version_number));
      return `
      <div class="v2-ver-item" data-vn="${v.version_number}" style="padding:8px 12px;border-radius:var(--r8);margin-bottom:4px;border:1px solid ${isLatest ? 'var(--accent)' : 'var(--border)'};cursor:pointer;transition:var(--tr);${isLatest ? 'background:var(--accent-dim);' : ''}" onclick="v2ToggleVerSelect(this, ${v.version_number})">
        <div style="display:flex;align-items:center;gap:6px;">
          <input type="checkbox" class="v2-ver-cb" data-vn="${v.version_number}" style="accent-color:var(--accent);width:13px;height:13px;" onclick="event.stopPropagation();v2UpdateDiffBtn()">
          <span style="font-size:12px;font-weight:600;">v${esc(v.version_number)}</span>
          ${isLatest ? '<span class="badge bok" style="font-size:10px;">当前</span>' : ''}
          ${v.is_rollback ? '<span class="badge bcan" style="font-size:10px;">回滚</span>' : ''}
        </div>
        <div style="font-size:11px;color:var(--t2);margin-top:2px;">${ftm(v.created_at)}</div>
        <div style="font-size:11px;color:var(--t1);margin-top:2px;">${esc(v.change_log || '').slice(0, 80) || '<i style="color:var(--t2);">无说明</i>'}</div>
        ${!isLatest ? `<div style="margin-top:4px;">
          <button class="btn btn-sm" onclick="event.stopPropagation();v2Rollback(${v.version_number})" style="padding:2px 8px;font-size:10px;color:var(--yellow);border:1px solid var(--yellow);background:transparent;border-radius:var(--r4);cursor:pointer;">⏪ 回滚到此版本</button>
        </div>` : ''}
      </div>`;
    }).join('') + '</div>';
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<div class="empty" style="padding:20px;"><p style="color:var(--red);">加载失败: ${esc(e.message)}</p></div>`;
  }
}

function v2ToggleVerSelect(el, vn) {
  const cb = el.querySelector('.v2-ver-cb');
  cb.checked = !cb.checked;
  v2UpdateDiffBtn();
}

function v2UpdateDiffBtn() {
  const cbs = document.querySelectorAll('.v2-ver-cb:checked');
  const btn = document.getElementById('v2DiffBtn');
  if (btn) btn.disabled = cbs.length !== 2;
}

async function v2ShowDiff() {
  const cbs = document.querySelectorAll('.v2-ver-cb:checked');
  if (cbs.length !== 2) { toast('请选择2个版本', 'error'); return; }
  const versions = Array.from(cbs).map(cb => parseInt(cb.dataset.vn)).sort((a, b) => a - b);
  const v1 = versions[0], v2 = versions[1];

  document.getElementById('v2DiffTitle').textContent = `📊 版本对比: v${v1} → v${v2}`;
  document.getElementById('v2DiffContent').innerHTML = '<div style="padding:20px;text-align:center;color:var(--t2);">加载中...</div>';
  document.getElementById('v2DiffModal').style.display = 'block';

  try {
    const diff = await GET(`/api/v1/data-standards-v2/${V2.stdId}/versions/${v1}/diff/${v2}`);
    let html = '';

    // 实体级统计（后端 stats 口径，非叶子路径数）
    const st = diff.stats || {};
    const sumOp = (op) => ['layer_groups', 'tables', 'fields', 'dictionaries', 'items'].reduce((a, t) => a + ((st[t] || {})[op] || 0), 0);
    const addCount = sumOp('added'), removeCount = sumOp('removed'), modCount = sumOp('modified');
    html += `<div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap;">
      <div style="padding:8px 14px;background:var(--gdim);border-radius:var(--r6);font-size:12px;"><span style="color:var(--green);font-weight:600;">+${addCount}</span> 新增</div>
      <div style="padding:8px 14px;background:var(--rdim);border-radius:var(--r6);font-size:12px;"><span style="color:var(--red);font-weight:600;">-${removeCount}</span> 删除</div>
      <div style="padding:8px 14px;background:var(--ydim);border-radius:var(--r6);font-size:12px;"><span style="color:var(--yellow);font-weight:600;">~${modCount}</span> 修改</div>
    </div>`;

    // 中文变更摘要行（实体级）
    if (diff.summary && diff.summary.length) {
      const colored = diff.summary.map(s => esc(s)
        .replace('新增', '<span style="color:var(--green);font-weight:600;">新增</span>')
        .replace('修改', '<span style="color:var(--yellow);font-weight:600;">修改</span>')
        .replace('删除', '<span style="color:var(--red);font-weight:600;">删除</span>')
      ).join('　·　');
      html += `<div style="margin-bottom:14px;padding:10px 14px;background:var(--bg2);border-radius:var(--r6);font-size:12px;line-height:1.8;">${colored}</div>`;
    }

    if (!addCount && !removeCount && !modCount) {
      html += '<div class="empty" style="padding:30px;"><p>两个版本完全一致，无差异</p></div>';
    } else {
      // 新增项 — 层级折叠树（层组→表→字段 分组，中文名）
      if (addCount) {
        html += '<h4 style="font-size:13px;color:var(--green);margin:12px 0 8px;">➕ 新增</h4>';
        const tree = _renderDiffTree(diff.added, 'green', diff.labels);
        html += tree || '<div class="empty">无实体级新增（元信息变更）</div>';
      }
      // 删除项
      if (removeCount) {
        html += '<h4 style="font-size:13px;color:var(--red);margin:12px 0 8px;">➖ 删除</h4>';
        const tree = _renderDiffTree(diff.removed, 'red', diff.labels);
        html += tree || '<div class="empty">无实体级删除（元信息变更）</div>';
      }
      // 修改项 — 层级树（旧值 → 新值）
      if (modCount) {
        html += '<h4 style="font-size:13px;color:var(--yellow);margin:12px 0 8px;">✏️ 修改</h4>';
        html += _renderDiffTree(diff.modified, 'yellow', diff.labels) || '<div class="empty">无实体级修改</div>';
      }
    }
    document.getElementById('v2DiffContent').innerHTML = html;
  } catch (e) {
    document.getElementById('v2DiffContent').innerHTML = `<div class="empty" style="padding:30px;"><p style="color:var(--red);">对比失败: ${esc(e.message)}</p></div>`;
  }
}

// 实体类型 → 图标/中文名（用于 diff 层级树分组）
const _DT_ICON = { layer_groups: '🗂', tables: '📄', fields: '🔤', dictionaries: '📖', items: '🔢' };
const _DT_LABEL = { layer_groups: '层组', tables: '表', fields: '字段', dictionaries: '字典', items: '字典项' };

function _renderDiffTree(entries, color, labels) {
  // entries: {path: value}，path 形如 layer_groups/BG/tables/CTRL_PT/fields/pt_id
  // 实体类型段+名称段 → 折叠节点（中文名优先，编码灰显）；属性段 → 子节点；路径终点挂值
  // labels: {实体类型: {自然键: 中文名}}（后端 _collect_diff_labels，可选）
  if (!entries || !Object.keys(entries).length) return '';
  labels = labels || {};
  const root = { children: {} };
  for (const [path, value] of Object.entries(entries)) {
    const segs = path.split('/');
    let cur = root;
    for (let i = 0; i < segs.length; i++) {
      const s = segs[i];
      let key, node;
      if (_DT_ICON[s]) {
        const name = (i + 1 < segs.length) ? segs[++i] : '';
        key = s + '|' + name;
        const disp = (labels[s] && labels[s][name]) ? labels[s][name] : name;
        node = cur.children[key] || (cur.children[key] = { icon: _DT_ICON[s], label: _DT_LABEL[s], name, disp, children: {} });
      } else {
        key = '~' + s;
        node = cur.children[key] || (cur.children[key] = { name: s, children: {} });
      }
      cur = node;
    }
    cur.val = value;
  }

  function render(nodes) {
    let html = '';
    for (const [k, n] of Object.entries(nodes)) {
      const kids = render(n.children);
      let title;
      if (n.icon) {
        const dispHtml = n.disp !== n.name
          ? `<b style="color:var(--${color});">${esc(n.disp)}</b> <span class="mono" style="color:var(--t3);font-size:10px;">${esc(n.name)}</span>`
          : `<b style="color:var(--${color});">${esc(n.disp)}</b>`;
        title = `<span>${n.icon} ${n.label} ${dispHtml}</span>`;
      } else {
        title = `<span class="mono" style="font-size:11px;">${esc(n.name)}</span>`;
      }
      let valHtml = '';
      if (n.val !== undefined) {
        const v = n.val;
        if (v && typeof v === 'object' && 'old' in v && 'new' in v) {
          valHtml = `<div style="padding:1px 0 1px 16px;font-size:11px;"><span style="color:var(--red);">${esc(JSON.stringify(v.old)).slice(0, 60)}</span> → <span style="color:var(--green);">${esc(JSON.stringify(v.new)).slice(0, 60)}</span></div>`;
        } else {
          valHtml = `<div style="padding:1px 0 1px 16px;font-size:11px;color:var(--${color});">→ ${esc(JSON.stringify(v)).slice(0, 140)}</div>`;
        }
      }
      const kidCount = Object.keys(n.children || {}).length;
      if (kidCount) {
        html += `<details style="margin:1px 0;"${kidCount <= 6 ? ' open' : ''}><summary style="cursor:pointer;padding:2px 5px;border-radius:var(--r4);background:var(--bg2);font-size:12px;">${title} <span style="color:var(--t3);font-size:10px;">(${kidCount})</span></summary><div style="padding-left:10px;margin-left:7px;border-left:1px solid var(--border);">${kids}${valHtml}</div></details>`;
      } else {
        html += `<div style="padding:2px 5px;font-size:12px;">${title}${valHtml}</div>`;
      }
    }
    return html;
  }
  return render(root.children);
}

async function v2Publish() {
  if (!V2.stdId) { toast('请先选择一个标准', 'error'); return; }
  const summary = prompt('版本说明（可选）:');
  try {
    const r = await POST('/api/v1/data-standards-v2/' + V2.stdId + '/publish', {
      change_log: summary || '手动发布',
    });
    toast(`版本 v${r.version_number} 发布成功`, 'success');
    v2LoadVersions();
    v2LoadStandards(); // 刷新状态标签
    v2LoadLayers();    // 刷新结构树
  } catch (e) { toast('发布失败: ' + e.message, 'error'); }
}

async function v2Rollback(versionNumber) {
  if (!V2.stdId) { toast('请先选择一个标准', 'error'); return; }
  if (!confirm(`确定要回滚到 v${versionNumber} 吗？\n当前标准的所有数据将被恢复到该版本的状态，\n此操作会创建一个新的版本记录。`)) return;
  try {
    const r = await POST('/api/v1/data-standards-v2/' + V2.stdId + '/rollback/' + versionNumber, {});
    toast(`已回滚到 v${versionNumber}，新版本 v${r ? r.version_number : '?'} 已创建`, 'success');
    v2LoadVersions();
    v2LoadStandards();
    v2LoadLayers();
    v2LoadDicts();
  } catch (e) { toast('回滚失败: ' + e.message, 'error'); }
}

// ── Import ──────────────────────────────────────────────
// ── Import/Export（v2.22.2 单框三段：唯一格式选择器 + 导入/导出/模板同框） ──
// 原实现是 3 主 Tab（导入|导出|模板）+ 导入下 3 子 Tab（JSON|Excel|CSV）= 6 个切换按钮、
// 2 个 dropzone；现压成 1 组格式选择器（3 按钮）+ 3 个动作段。
// 唯一格式选择器驱动：file input 的 accept/multiple、dropzone 提示、导出/模板按钮文案、导入端点。
// 附带修复：mode 选择器暴露（原 UI 从不下发 mode，update / upsert 路径不可达）。
const V2_IE_FMT = {
  json:  { label: 'JSON',  accept: '.json', multiple: false, tpl: '/api/v1/data-standards-v2/template/json'  },
  excel: { label: 'Excel', accept: '.xlsx', multiple: false, tpl: '/api/v1/data-standards-v2/template/excel' },
  // v2.22.3 §六·补七：导出产出 ZIP 归档，导入须原样收得回来（后端自动解包）
  csv:   { label: 'CSV',   accept: '.csv,.zip', multiple: true,  tpl: '/api/v1/data-standards-v2/template/csv',
           hint: '可多选散装 CSV，或直接拖入导出的 ZIP 归档' },
};

function v2ShowImpExp() {
  document.getElementById('v2ImportModal').style.display = 'block';
  document.getElementById('v2ImpResult').innerHTML = '';
  v2IeFmt('excel'); // 默认 Excel（v2 五 Sheet 模板，字典导入走这条）

  const zone = document.getElementById('v2IeDropZone');
  const inp = document.getElementById('v2IeFile');
  zone.onclick = () => inp.click();
  inp.onchange = () => v2IeDropHint();
  zone.ondragover = (e) => { e.preventDefault(); zone.classList.add('drag'); };
  zone.ondragleave = () => zone.classList.remove('drag');
  zone.ondrop = (e) => {
    e.preventDefault(); zone.classList.remove('drag');
    const accept = (inp.accept || '').split(',').map(s => s.trim()).filter(Boolean);
    const picked = [...e.dataTransfer.files].filter(f =>
      !accept.length || accept.some(a => f.name.toLowerCase().endsWith(a)));
    if (!picked.length) { toast('请拖入 ' + (inp.accept || '所需') + ' 文件', 'warning'); return; }
    const dt = new DataTransfer();
    picked.slice(0, inp.multiple ? picked.length : 1).forEach(f => dt.items.add(f));
    inp.files = dt.files;
    v2IeDropHint();
  };

  v2IeExportInfo();
}

// 唯一格式选择器：切格式并同步三段 UI 状态
function v2IeFmt(fmt) {
  const cfg = V2_IE_FMT[fmt] || V2_IE_FMT.excel;
  const isJson = fmt === 'json';
  V2.ieFmt = fmt;

  document.querySelectorAll('#v2IeFmtBar .v2-ie-fmt').forEach(b => b.classList.toggle('active', b.dataset.fmt === fmt));

  const inp = document.getElementById('v2IeFile');
  inp.accept = cfg.accept;
  inp.multiple = cfg.multiple;
  inp.value = ''; // 换格式清空已选文件，避免 accept 不符的残留被提交

  document.getElementById('v2IeJsonBox').style.display = isJson ? 'block' : 'none';
  document.getElementById('v2IeDropZone').style.display = isJson ? 'none' : 'block';
  document.getElementById('v2IeDropTitle').textContent = isJson ? '' : '点击或拖拽文件到此处';
  document.getElementById('v2IeDropHint').textContent = isJson ? '' : v2IeDropText();

  // 导出：v2.22.3 起 json / excel / csv 三格式后端均已实现且均可无损往返；
  // CSV 产出 ZIP 归档（manifest + 6 个角色 CSV，含字典与字段-字典绑定）
  const expBtn = document.getElementById('v2IeExportBtn');
  const isCsv = fmt === 'csv';
  expBtn.textContent = '⬇ 导出为 ' + cfg.label + (isCsv ? ' (ZIP)' : '');
  expBtn.disabled = false;
  expBtn.style.opacity = '1';
  expBtn.style.cursor = 'pointer';
  expBtn.title = isCsv
    ? 'ZIP 归档：_manifest.json + 标准信息/层组/数据表/字段/字典/字典项 六个 CSV，含字典与字段-字典绑定，可无损往返'
    : '';

  document.getElementById('v2IeTplBtn').textContent =
    '📥 下载 ' + cfg.label + ' 模板' + (fmt === 'csv' ? ' (ZIP)' : '');
}

function v2IeDropText() {
  const cfg = V2_IE_FMT[V2.ieFmt] || V2_IE_FMT.excel;
  if (cfg.hint) return cfg.hint;
  return cfg.accept + ' 格式，' + (cfg.multiple ? '可多选，每个文件对应一个数据表' : '单个文件');
}

function v2IeDropHint() {
  const inp = document.getElementById('v2IeFile');
  const n = inp.files.length;
  document.getElementById('v2IeDropTitle').textContent =
    n ? (n > 1 ? '📄 ' + n + ' 个文件' : '📄 ' + inp.files[0].name) : '点击或拖拽文件到此处';
}

function v2IeExportInfo() {
  const el = document.getElementById('v2IeExpInfo');
  if (!el) return;
  if (!V2.stdId) { el.textContent = '⚠ 请先在左侧选择一个标准'; return; }
  const sel = document.querySelector('.v2-std-item[onclick*="' + V2.stdId + '"]');
  el.textContent = '当前选中: ' + (sel ? sel.querySelector('div')?.textContent?.trim() : V2.stdId);
}

async function v2IeImport() {
  const fmt = V2.ieFmt || 'excel';
  const name = document.getElementById('v2IeName').value.trim();
  const mode = document.getElementById('v2IeMode').value;
  const box = document.getElementById('v2ImpResult');
  if (!name) { toast('请输入标准名称', 'error'); return; }
  try {
    let r;
    if (fmt === 'json') {
      const data = document.getElementById('v2IeJsonData').value.trim();
      if (!data) { toast('请输入 JSON 数据', 'error'); return; }
      const parsed = JSON.parse(data);
      parsed.name = name;
      r = await POST('/api/v1/data-standards-v2/import/json?mode=' + mode, parsed); // mode 走 Query
    } else {
      const inp = document.getElementById('v2IeFile');
      const files = fmt === 'csv' ? [...inp.files] : (inp.files[0] ? [inp.files[0]] : []);
      if (!files.length) { toast('请选择要导入的文件', 'error'); return; }
      const fd = new FormData();
      if (fmt === 'csv') files.forEach(f => fd.append('files', f)); // CSV 端点形参是 files(list)
      else fd.append('file', files[0]);
      fd.append('name', name);
      fd.append('mode', mode); // Excel/CSV 的 mode 走 Form
      r = await POSTF('/api/v1/data-standards-v2/import/' + fmt, fd);
    }
    box.innerHTML = '<div style="padding:12px;background:var(--gdim);border:1px solid var(--green);border-radius:var(--r8);font-size:13px;color:var(--green);">✓ ' + esc(r.message) + '</div>';
    v2LoadStandards();
  } catch (e) {
    box.innerHTML = '<div style="padding:12px;background:var(--rdim);border:1px solid var(--red);border-radius:var(--r8);font-size:13px;color:var(--red);">✗ ' + esc(e.message) + '</div>';
  }
}

function v2IeExport() {
  if (!V2.stdId) { toast('请先选择一个标准', 'error'); return; }
  const fmt = V2.ieFmt || 'json';
  // csv 产出的是 ZIP（后端按表拆文件），故扩展名不能沿用 fmt
  const ext = fmt === 'csv' ? 'zip' : fmt;
  dlAuth(S.base + '/api/v1/data-standards-v2/' + V2.stdId + '/export?format=' + fmt, '标准导出.' + ext);
}

function v2IeTemplate() {
  const cfg = V2_IE_FMT[V2.ieFmt] || V2_IE_FMT.json;
  const a = document.createElement('a');
  a.href = S.base + cfg.tpl;
  a.download = '';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

// ── 知识图谱（A 档纯视图，不依赖图数据库）────────────────────
// 后端契约：GET /{id}/graph 给摘要（无字段/条目明细），展开走
// GET /{id}/graph/nodes/{id} 懒加载 —— 真实标准（SHGT_SURVEY）有
// 3288 字段 + 2031 条目，一次全给约 1.5MB。
const V2G = { stdId: null, data: null, expanded: {}, details: {} };

function v2CloseGraph() {
  document.getElementById('v2GraphModal').style.display = 'none';
}

async function v2OpenGraph(stdId, stdName) {
  V2G.stdId = stdId; V2G.data = null; V2G.expanded = {}; V2G.details = {};
  document.getElementById('v2GraphTitle').textContent = '🕸 知识图谱: ' + (stdName || '');
  document.getElementById('v2GraphStats').textContent = '加载中...';
  document.getElementById('v2GraphBody').innerHTML = '';
  document.getElementById('v2GraphModal').style.display = 'block';
  try {
    V2G.data = await GET('/api/v1/data-standards-v2/' + stdId + '/graph');
    v2RenderGraph();
  } catch (e) {
    document.getElementById('v2GraphStats').textContent = '';
    document.getElementById('v2GraphBody').innerHTML =
      '<div class="empty" style="padding:30px;"><p style="color:var(--red);">加载失败: ' + esc(e.message) + '</p></div>';
  }
}

async function v2ToggleGraphNode(nodeId) {
  if (V2G.expanded[nodeId]) { delete V2G.expanded[nodeId]; v2RenderGraph(); return; }
  if (!V2G.details[nodeId]) {
    try {
      V2G.details[nodeId] = await GET('/api/v1/data-standards-v2/' + V2G.stdId + '/graph/nodes/' + nodeId);
    } catch (e) { toast('展开失败: ' + e.message, 'error'); return; }
  }
  V2G.expanded[nodeId] = true;
  v2RenderGraph();
}

function v2GraphBez(x1, y1, x2, y2) {
  const dx = Math.max(24, (x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

function v2RenderGraph() {
  const g = V2G.data;
  const st = g.stats || {};
  document.getElementById('v2GraphStats').textContent =
    `${st.group_count || 0} 层组 · ${st.table_count || 0} 表 · ${st.field_count || 0} 字段 · ` +
    `${st.dict_count || 0} 字典 · ${st.linked_field_count || 0} 绑定字段 · ${st.binds_edge_count || 0} 绑定边`;

  const nodes = g.nodes || [];
  const structural = nodes.filter(n => n.kind !== 'standard');
  const body = document.getElementById('v2GraphBody');
  if (!structural.length) {
    body.innerHTML = '<div class="empty" style="padding:30px;"><p>空标准 —— 暂无图谱内容</p></div>';
    return;
  }

  const byId = {}; nodes.forEach(n => { byId[n.id] = n; });
  const childrenOf = {};
  nodes.filter(n => n.kind === 'group' && n.parent_id)
       .forEach(n => { (childrenOf[n.parent_id] = childrenOf[n.parent_id] || []).push(n); });
  const roots = nodes.filter(n => n.kind === 'group' && !n.parent_id);
  const groupOrder = [];
  (function walk(gs) { gs.forEach(gr => { groupOrder.push(gr); walk(childrenOf[gr.id] || []); }); })(roots);
  const tables = nodes.filter(n => n.kind === 'table');
  const dicts = nodes.filter(n => n.kind === 'dict');
  const dictIdByCode = {};
  dicts.forEach(d => { if (d.code) dictIdByCode[d.code] = d.id; });

  // 三列布局：层组 | 数据表 | 字典（参照物层组深度实测只有 2）
  const COLS = [
    { x: 16, w: 226, label: '层组' },
    { x: 286, w: 266, label: '数据表' },
    { x: 592, w: 306, label: '字典' },
  ];
  const W = COLS[2].x + COLS[2].w + 16;
  const RH = { group: 34, table: 38, dict: 38, field: 22, item: 24 };

  const pos = {};    // 节点锚点（画边用）
  let yG = 48, yT = 48, yD = 48;
  const fieldRows = [];   // {field, x, y, w}

  // svg 分三层拼装：表头 → 边（垫底）→ 节点（上层）
  let header = '';
  COLS.forEach(c => {
    header += `<text x="${c.x + 2}" y="30" fill="var(--t2)" font-size="12">${c.label}</text>`;
    header += `<line x1="${c.x}" y1="38" x2="${c.x + c.w}" y2="38" stroke="var(--border)" stroke-width="1"/>`;
  });
  let nodesSvg = '';

  // ── 层组列（缩进表达层级）──
  groupOrder.forEach(gr => {
    const depth = gr.depth || 1;
    const x = COLS[0].x + (depth - 1) * 16;
    const w = COLS[0].w - (depth - 1) * 16;
    const y = yG;
    pos[gr.id] = { x, y, w, h: RH.group };
    nodesSvg += `<g><title>${esc(gr.name)}${gr.code ? ' · ' + esc(gr.code) : ''}</title>` +
      `<rect x="${x}" y="${y}" width="${w}" height="${RH.group}" rx="6" fill="var(--bg-3)" stroke="var(--border-hi)"/>` +
      `<text x="${x + 10}" y="${y + 21}" fill="var(--t0)" font-size="12">📂 ${esc((gr.name || '').slice(0, 16))}</text>` +
      (gr.code ? `<text x="${x + w - 8}" y="${y + 21}" fill="var(--t2)" font-size="10" text-anchor="end">${esc(gr.code)}</text>` : '') +
      `</g>`;
    yG += RH.group + 6;
  });

  // ── 表列（可展开字段）──
  tables.forEach(t => {
    const x = COLS[1].x, w = COLS[1].w, y = yT;
    pos[t.id] = { x, y, w, h: RH.table };
    const open = !!V2G.expanded[t.id];
    nodesSvg += `<g style="cursor:pointer" onclick="v2ToggleGraphNode('${esc(t.id)}')">` +
      `<title>${esc(t.name)} · ${t.field_count || 0} 字段（点击${open ? '收起' : '展开'}）</title>` +
      `<rect x="${x}" y="${y}" width="${w}" height="${RH.table}" rx="6" fill="var(--bg-2)" stroke="var(--accent)" stroke-opacity=".45"/>` +
      `<text x="${x + 10}" y="${y + 24}" fill="var(--t0)" font-size="12">${open ? '▾' : '▸'} 🗃 ${esc((t.name || '').slice(0, 15))}</text>` +
      `<text x="${x + w - 8}" y="${y + 24}" fill="var(--accent)" font-size="10" text-anchor="end">${t.field_count || 0} 字段</text>` +
      `</g>`;
    yT += RH.table + 6;
    const det = V2G.details[t.id];
    if (open && det && det.fields) {
      det.fields.forEach(f => {
        const fx = x + 20, fy = yT, fw = w - 20;
        fieldRows.push({ field: f, x: fx, y: fy, w: fw });
        const bound = (f.dict_codes || []).length > 0;
        nodesSvg += `<g><title>${esc(f.field_name)}${bound ? ' → ' + (f.dict_codes || []).join(', ') : ''}</title>` +
          `<rect x="${fx}" y="${fy}" width="${fw}" height="${RH.field}" rx="4" fill="var(--bg-1)" stroke="var(--border)"${bound ? '' : ' stroke-opacity=".5"'}/>` +
          `<text x="${fx + 8}" y="${fy + 15}" fill="var(--t1)" font-size="11">${bound ? '🔗 ' : ''}${esc((f.field_name || '').slice(0, 18))}</text>` +
          `<text x="${fx + fw - 6}" y="${fy + 15}" fill="var(--t2)" font-size="9" text-anchor="end">${esc(f.data_type || '')}</text>` +
          `</g>`;
        yT += RH.field + 2;
      });
      yT += 10;
    }
  });

  // ── 字典列（可展开条目）──
  dicts.forEach(d => {
    const x = COLS[2].x, w = COLS[2].w, y = yD;
    pos[d.id] = { x, y, w, h: RH.dict };
    const open = !!V2G.expanded[d.id];
    const globe = d.scope === 'global';
    nodesSvg += `<g style="cursor:pointer" onclick="v2ToggleGraphNode('${esc(d.id)}')">` +
      `<title>${esc(d.name)}${d.code ? ' · ' + esc(d.code) : ''} · ${d.item_count || 0} 条目（点击${open ? '收起' : '展开'}）</title>` +
      `<rect x="${x}" y="${y}" width="${w}" height="${RH.dict}" rx="6" fill="var(--bg-2)" stroke="${globe ? 'var(--purple)' : 'var(--green)'}" stroke-opacity=".5"${globe ? ' stroke-dasharray="5 3"' : ''}/>` +
      `<text x="${x + 10}" y="${y + 24}" fill="var(--t0)" font-size="12">${open ? '▾' : '▸'} ${globe ? '🌐' : '📕'} ${esc((d.name || '').slice(0, 14))}</text>` +
      `<text x="${x + w - 8}" y="${y + 24}" fill="var(--t1)" font-size="10" text-anchor="end">${d.item_count || 0} 项</text>` +
      `</g>`;
    yD += RH.dict + 6;
    const det = V2G.details[d.id];
    if (open && det && det.items) {
      det.items.forEach(it => {
        nodesSvg += `<g><title>${esc(it.item_code)} ${esc(it.item_name)}${it.parent_code ? ' · 父级 ' + esc(it.parent_code) : ''}</title>` +
          `<rect x="${x + 20}" y="${yD}" width="${w - 20}" height="${RH.item}" rx="4" fill="var(--bg-1)" stroke="var(--border)"/>` +
          `<text x="${x + 28}" y="${yD + 16}" fill="var(--t1)" font-size="11">${it.parent_code ? '↳ ' : ''}${esc(it.item_code)} ${esc((it.item_name || '').slice(0, 12))}</text>` +
          `</g>`;
        yD += RH.item + 2;
      });
      yD += 10;
    }
  });

  const H = Math.max(yG, yT, yD) + 12;

  // ── 边（垫在节点下层）──
  let edgesSvg = '';
  (g.edges || []).forEach(e => {
    if (e.kind === 'contains') {
      // standard→根层组 的边不画（弹层标题已表达）；层组间画肘形，层组→表画贝塞尔
      if (!byId[e.source] || byId[e.source].kind === 'standard') return;
      const a = pos[e.source], b = pos[e.target];
      if (!a || !b) return;
      if (byId[e.target].kind === 'group') {
        edgesSvg += `<path d="M ${a.x + 14} ${a.y + a.h} V ${b.y + b.h / 2} H ${b.x}" fill="none" stroke="var(--border-hi)" stroke-width="1.2"/>`;
      } else {
        // S9 次归属用虚线区分主归属
        const dashed = e.primary === false ? ' stroke-dasharray="4 3"' : '';
        edgesSvg += `<path d="${v2GraphBez(a.x + a.w, a.y + a.h / 2, b.x, b.y + b.h / 2)}" fill="none" stroke="var(--border-hi)" stroke-width="1.2"${dashed}/>`;
      }
    } else if (e.kind === 'binds') {
      const a = pos[e.source], b = pos[e.target];
      if (!a || !b) return;
      const wpx = (1 + Math.min(e.field_count || 1, 6) * 0.55).toFixed(2);
      edgesSvg += `<path d="${v2GraphBez(a.x + a.w, a.y + a.h / 2, b.x, b.y + b.h / 2)}" fill="none" stroke="var(--accent)" stroke-width="${wpx}" stroke-opacity=".5">` +
        `<title>${esc(byId[e.source] ? byId[e.source].name : '')} → ${esc(byId[e.target] ? byId[e.target].name : '')}：${e.field_count || 0} 个字段绑定</title></path>`;
    }
  });
  // 展开的字段 → 字典 细边（按 dict_code 映射到字典节点）
  fieldRows.forEach(fr => {
    (fr.field.dict_codes || []).forEach(code => {
      const did = dictIdByCode[code];
      const b = did && pos[did];
      if (!b) return;
      edgesSvg += `<path d="${v2GraphBez(fr.x + fr.w, fr.y + 11, b.x, b.y + b.h / 2)}" fill="none" stroke="var(--accent)" stroke-width="1" stroke-opacity=".22"/>`;
    });
  });

  body.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" font-family="inherit">` +
    header + edgesSvg + nodesSvg + '</svg>';
}

// ── Bootstrap ─────────────────────────────────────────────
document.addEventListener('DOMContentLoaded',()=>{
  // URL 参数 ?key=xxx 自动设置 API Key
  const urlKey=new URLSearchParams(window.location.search).get('key');
  if(urlKey&&!S.key){S.key=urlKey;try{localStorage.setItem('vcs_key',urlKey)}catch(e){};setKS(true);}
  initKey();initUpload();initData();initTx();initPreview();initStds();initAdmin();
  document.querySelectorAll('.sb-nav a[data-page]').forEach(a=>{
    a.addEventListener('click',e=>{e.preventDefault();goPage(a.dataset.page);});
  });
  if(S.key){
    loadDash();loadRecent();
    S.timer=setInterval(loadDash,6000);
  }
  goPage('dashboard');
});

// ── Admin: Tab switching ────────────────────────────────
function switchAdmTab(tab){
  document.querySelectorAll('.adm-tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===tab));
  document.querySelectorAll('.adm-tab-content').forEach(c=>c.style.display=c.id==='admTab'+tab.charAt(0).toUpperCase()+tab.slice(1)?'':'none');
  if(tab==='keys') loadAdminKeys();
  if(tab==='groups') loadGroups();
}

// ── Admin: Helper ───────────────────────────────────────
function initAdmin(){}
function admKey(){
  const el=document.getElementById('adminInitKey');
  const v=el.value.trim();
  const saved=(()=>{try{return localStorage.getItem('vcs_admin_key')||'';}catch(e){return '';}})();
  if(v&&v!==saved){try{localStorage.setItem('vcs_admin_key',v);}catch(e){}}
  else if(!v&&saved){el.value=saved;return saved;}
  return v;
}
function admHeaders(){return {'X-Admin-Init-Key':admKey()};}
function admFmt(d){if(!d)return'-';try{return new Date(d).toLocaleString('zh-CN');}catch(e){return d;}}

// ── Admin: API Keys ─────────────────────────────────────

async function loadAdminKeys(){
  const k=admKey();
  if(!k){toast('请先输入管理员密钥','error');return;}
  const tb=document.getElementById('adminKeyTbody');
  tb.innerHTML='<tr><td colspan="6"><div class="empty"><p>⏳ 加载中...</p></div></td></tr>';
  try{
    const r=await fetch(S.base+'/api/v1/admin/apikeys',{headers:admHeaders()});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||'HTTP '+r.status);
    // 同时刷新分组下拉
    fillGroupSelect();
    if(!d.length){
      tb.innerHTML='<tr><td colspan="6"><div class="empty"><div class="ei">🔑</div><p>暂无密钥</p></div></td></tr>';
      return;
    }
    tb.innerHTML=d.map(x=>'<tr>'+
      '<td style="font-weight:500;">'+esc(x.name)+'</td>'+
      '<td style="font-size:12px;color:var(--t1);">'+esc(x.description||'-')+'</td>'+
      '<td>'+(x.group_name?'<span class="badge bb" title="group: '+esc(x.group_id)+'">'+esc(x.group_name)+'</span>':'<span style="color:var(--t2);font-size:12px;">无</span>')+'</td>'+
      '<td><span class="badge '+(x.is_active?'bg':'br')+'">'+(x.is_active?'活跃':'已吊销')+'</span></td>'+
      '<td style="font-size:12px;color:var(--t1);">'+admFmt(x.created_at)+'</td>'+
      '<td style="display:flex;gap:4px;">'+
        (x.is_active
          ?'<button class="key-revoke-btn" data-id="'+x.id+'" data-name="'+esc(x.name)+'" title="吊销">🗑️</button>'
          :'<button class="key-activate-btn" data-id="'+x.id+'" data-name="'+esc(x.name)+'" title="重新启用（恢复可用）" style="color:var(--green);">▶️启用</button>')+
        '<button class="key-regen-btn" data-id="'+x.id+'" data-name="'+esc(x.name)+'" title="重新生成密钥（旧密钥立即失效）" style="color:var(--yellow);">🔁</button>'+
        (x.is_active
          ?''
          :'<button class="key-delete-btn" data-id="'+x.id+'" data-name="'+esc(x.name)+'" title="永久删除" style="color:var(--red);">❌删除</button>')+
        '<button class="key-group-btn" data-id="'+x.id+'" data-name="'+esc(x.name)+'" data-gid="'+(x.group_id||'')+'" title="修改分组">👥</button>'+
      '</td>'+
    '</tr>').join('');
  }catch(e){tb.innerHTML='<tr><td colspan="6"><div class="empty"><p style="color:var(--red);">加载失败: '+esc(e.message)+'</p></div></td></tr>';}
}

// 填充创建 Key 时的分组下拉
async function fillGroupSelect(){
  const sel=document.getElementById('adminKeyGroup');
  try{
    const r=await fetch(S.base+'/api/v1/admin/groups',{headers:admHeaders()});
    if(!r.ok)return;
    const groups=await r.json();
    sel.innerHTML='<option value="">无分组</option>'+groups.map(g=>'<option value="'+esc(g.id)+'">'+esc(g.name)+' ('+g.member_count+'人)</option>').join('');
  }catch(e){}
}

async function createAdminKey(){
  const el=document.getElementById('adminCreateResult');
  const k=admKey();
  if(!k){el.innerHTML='<span style="color:var(--red);">请先输入管理员密钥</span>';return;}
  const name=document.getElementById('adminKeyName').value.trim();
  if(!name){el.innerHTML='<span style="color:var(--red);">请输入密钥名称</span>';return;}
  const groupId=document.getElementById('adminKeyGroup').value||null;
  el.innerHTML='<span style="color:var(--t1);">⏳ 创建中...</span>';
  try{
    const desc=document.getElementById('adminKeyDesc').value.trim();
    const body={name,description:desc||null};
    if(groupId) body.group_id=groupId;
    const r=await fetch(S.base+'/api/v1/admin/apikeys',{
      method:'POST',
      headers:{'X-Admin-Init-Key':k,'Content-Type':'application/json'},
      body:JSON.stringify(body),
    });
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||'HTTP '+r.status);
    el.innerHTML='<div style="padding:12px;background:var(--gdim);border:1px solid var(--green);border-radius:var(--r8);font-size:13px;">'+
      '<div style="font-weight:600;color:var(--green);margin-bottom:6px;">✓ 创建成功</div>'+
      '<div style="margin-bottom:4px;"><span style="color:var(--t1);">密钥: </span><code style="font-family:var(--mono);color:var(--accent);word-break:break-all;">'+esc(d.api_key)+'</code></div>'+
      '<div style="font-size:11px;color:var(--yellow);">⚠ 请立即复制，关闭后不再显示</div></div>';
    document.getElementById('adminKeyName').value='';
    document.getElementById('adminKeyDesc').value='';
    loadAdminKeys();
  }catch(e){el.innerHTML='<span style="color:var(--red);">创建失败: '+esc(e.message)+'</span>';}
}

async function delAdminKey(id,name){
  if(!confirm('确定吊销密钥 "'+name+'"? 吊销后仍可查看，但不可使用。'))return;
  try{
    const r=await fetch(S.base+'/api/v1/admin/apikeys/'+id,{
      method:'DELETE',
      headers:admHeaders(),
    });
    if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.detail||'HTTP '+r.status);}
    toast('密钥 "'+name+'" 已吊销','success');
    loadAdminKeys();
  }catch(e){toast('吊销失败: '+e.message,'error');}
}

async function activateAdminKey(id,name){
  if(!confirm('重新启用密钥 "'+name+'"? 启用后该密钥立即可用。'))return;
  try{
    const r=await fetch(S.base+'/api/v1/admin/apikeys/'+id+'/activate',{
      method:'PUT',
      headers:admHeaders(),
    });
    if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.detail||'HTTP '+r.status);}
    toast('密钥 "'+name+'" 已重新启用','success');
    loadAdminKeys();
  }catch(e){toast('启用失败: '+e.message,'error');}
}

async function regenAdminKey(id,name){
  if(!confirm('重新生成密钥 "'+name+'"?\n\n旧密钥将立即失效。新密钥仅显示一次，请立即复制保存。'))return;
  try{
    const r=await fetch(S.base+'/api/v1/admin/apikeys/'+id+'/regenerate',{
      method:'POST',
      headers:admHeaders(),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.detail||'HTTP '+r.status);
    alert('新密钥（仅显示一次，请复制保存）：\n\n'+d.api_key+'\n\n'+d.message);
    loadAdminKeys();
  }catch(e){toast('重新生成失败: '+e.message,'error');}
}

async function delAdminKeyPermanent(id,name){
  if(!confirm('永久删除密钥 "'+name+'"?\n\n此操作不可恢复！删除后所有关联数据将变为「无归属」状态。'))return;
  if(!confirm('再次确认：真的要永久删除 "'+name+'" 吗？'))return;
  try{
    const r=await fetch(S.base+'/api/v1/admin/apikeys/'+id+'/hard',{
      method:'DELETE',
      headers:admHeaders(),
    });
    if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.detail||'HTTP '+r.status);}
    toast('密钥 "'+name+'" 已永久删除','success');
    loadAdminKeys();
  }catch(e){toast('删除失败: '+e.message,'error');}
}

// ── Admin: Key-Group assignment modal ───────────────────

function showGroupModal(keyId,keyName,currentGroupId){
  const overlay=document.createElement('div');
  overlay.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:1000;display:flex;align-items:center;justify-content:center;';
  const box=document.createElement('div');
  box.style.cssText='background:var(--bg-1);border:1px solid var(--border);border-radius:var(--r12);padding:24px;min-width:380px;max-width:500px;';
  box.innerHTML='<h4 style="margin-bottom:12px;">设置密钥分组: '+esc(keyName)+'</h4>'+
    '<div id="groupModalBody"><p style="color:var(--t1);">⏳ 加载分组列表...</p></div>'+
    '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">'+
    '<button class="btn" onclick="this.closest(\'div\').parentElement.parentElement.remove()">取消</button>'+
    '<button class="btn btn-p" id="groupModalSaveBtn" onclick="saveKeyGroup()">保存</button></div>';
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  overlay.dataset.keyId=keyId;
  overlay.dataset.keyName=keyName;
  overlay.dataset.currentGroup=currentGroupId||'';
  loadGroupModalOptions(overlay);
}

async function loadGroupModalOptions(overlay){
  const body=overlay.querySelector('#groupModalBody');
  const saveBtn=overlay.querySelector('#groupModalSaveBtn');
  if(saveBtn)saveBtn.disabled=true;  // 选项未就绪前禁止保存，避免误保存
  try{
    const r=await fetch(S.base+'/api/v1/admin/groups',{headers:admHeaders()});
    const groups=await r.json();
    if(!r.ok)throw new Error(groups.detail||'HTTP '+r.status);
    const cur=overlay.dataset.currentGroup;
    body.innerHTML='<label style="display:block;margin-bottom:6px;font-size:13px;color:var(--t1);">选择分组（选"无分组"则独立访问）</label>'+
      '<select id="groupModalSelect" style="width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:var(--r6);background:var(--bg-1);color:var(--t0);font-size:14px;">'+
      '<option value="">— 无分组 —</option>'+
      groups.map(g=>'<option value="'+esc(g.id)+'"'+(g.id===cur?' selected':'')+'>'+esc(g.name)+' ('+g.member_count+'人)</option>').join('')+
      '</select>';
    if(saveBtn)saveBtn.disabled=false;
  }catch(e){body.innerHTML='<p style="color:var(--red);">加载失败: '+esc(e.message)+'</p>';if(saveBtn)saveBtn.disabled=true;}
}

async function saveKeyGroup(){
  const overlay=document.querySelector('[data-key-id]');
  if(!overlay)return;
  const keyId=overlay.dataset.keyId;
  const groupId=document.getElementById('groupModalSelect').value||null;
  const saveBtn=document.getElementById('groupModalSaveBtn');
  saveBtn.disabled=true;saveBtn.textContent='保存中...';
  try{
    const r=await fetch(S.base+'/api/v1/admin/apikeys/'+keyId+'/group',{
      method:'PUT',
      headers:{'X-Admin-Init-Key':admKey(),'Content-Type':'application/json'},
      body:JSON.stringify({group_id:groupId}),
    });
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||'HTTP '+r.status);
    toast('分组已更新','success');
    overlay.remove();
    loadAdminKeys();
    loadGroups();
  }catch(e){toast('更新失败: '+e.message,'error');saveBtn.disabled=false;saveBtn.textContent='保存';}
}

// 事件委托：吊销 + 启用 + 重新生成 + 删除 + 分组按钮
document.addEventListener('click', function(e) {
  var btn = e.target.closest('.key-revoke-btn');
  if(btn) delAdminKey(btn.getAttribute('data-id'), btn.getAttribute('data-name'));
  var abtn = e.target.closest('.key-activate-btn');
  if(abtn) activateAdminKey(abtn.getAttribute('data-id'), abtn.getAttribute('data-name'));
  var rbtn = e.target.closest('.key-regen-btn');
  if(rbtn) regenAdminKey(rbtn.getAttribute('data-id'), rbtn.getAttribute('data-name'));
  var dbtn = e.target.closest('.key-delete-btn');
  if(dbtn) delAdminKeyPermanent(dbtn.getAttribute('data-id'), dbtn.getAttribute('data-name'));
  var gbtn = e.target.closest('.key-group-btn');
  if(gbtn) showGroupModal(gbtn.getAttribute('data-id'), gbtn.getAttribute('data-name'), gbtn.getAttribute('data-gid'));
});

// ── Admin: Groups ───────────────────────────────────────

async function loadGroups(){
  const k=admKey();
  if(!k)return;
  const container=document.getElementById('groupListContainer');
  container.innerHTML='<div class="empty"><p>⏳ 加载中...</p></div>';
  try{
    const r=await fetch(S.base+'/api/v1/admin/groups',{headers:admHeaders()});
    const groups=await r.json();
    if(!r.ok)throw new Error(groups.detail||'HTTP '+r.status);
    if(!groups.length){
      container.innerHTML='<div class="empty"><div class="ei">👥</div><p>暂无分组，在上方创建一个</p></div>';
      return;
    }
    let html='';
    for(const g of groups){
      // Load group detail with members
      const dr=await fetch(S.base+'/api/v1/admin/groups/'+g.id,{headers:admHeaders()});
      const detail=dr.ok?await dr.json():null;
      const members=detail?detail.api_keys||[]:[];
      html+='<div class="group-card" style="background:var(--bg-2);border:1px solid var(--border);border-radius:var(--r12);padding:20px;margin-bottom:16px;">'+
        '<div class="frow" style="align-items:start;margin-bottom:12px;">'+
          '<div style="flex:1;">'+
            '<h4 style="margin:0 0 4px 0;">'+esc(g.name)+'</h4>'+
            '<p style="margin:0;font-size:13px;color:var(--t1);">'+esc(g.description||'无描述')+'</p>'+
            '<p style="margin:4px 0 0 0;font-size:12px;color:var(--t2);">'+g.member_count+' 个成员 · 创建于 '+admFmt(g.created_at)+'</p>'+
          '</div>'+
          '<div style="display:flex;gap:4px;">'+
            '<button class="btn" onclick="editGroup(\''+esc(g.id)+'\',\''+esc(g.name)+'\',\''+esc(g.description||'')+'\')">✏️</button>'+
            '<button class="btn" onclick="deleteGroup(\''+esc(g.id)+'\',\''+esc(g.name)+'\')" style="color:var(--red);">🗑️</button>'+
          '</div>'+
        '</div>'+
        (members.length?'<div style="border-top:1px solid var(--border);padding-top:12px;"><label style="font-size:12px;color:var(--t2);display:block;margin-bottom:8px;">成员密钥:</label>'+
          '<div style="display:flex;flex-wrap:wrap;gap:6px;">'+
          members.map(m=>'<span class="badge '+(m.is_active?'bg':'br')+'" style="font-size:11px;padding:2px 8px;cursor:pointer;" title="'+esc(m.description||'')+'">'+esc(m.name)+'</span>').join('')+
          '</div></div>':'')+
      '</div>';
    }
    container.innerHTML=html;
  }catch(e){container.innerHTML='<div class="empty"><p style="color:var(--red);">加载失败: '+esc(e.message)+'</p></div>';}
}

async function createGroup(){
  const el=document.getElementById('groupCreateResult');
  const k=admKey();
  if(!k){el.innerHTML='<span style="color:var(--red);">请先输入管理员密钥</span>';return;}
  const name=document.getElementById('groupName').value.trim();
  if(!name){el.innerHTML='<span style="color:var(--red);">请输入分组名称</span>';return;}
  el.innerHTML='<span style="color:var(--t1);">⏳ 创建中...</span>';
  try{
    const desc=document.getElementById('groupDesc').value.trim();
    const r=await fetch(S.base+'/api/v1/admin/groups',{
      method:'POST',
      headers:{'X-Admin-Init-Key':k,'Content-Type':'application/json'},
      body:JSON.stringify({name,description:desc||null}),
    });
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||'HTTP '+r.status);
    el.innerHTML='<span style="color:var(--green);">✓ 分组 "'+esc(name)+'" 创建成功</span>';
    document.getElementById('groupName').value='';
    document.getElementById('groupDesc').value='';
    loadGroups();
    fillGroupSelect();
  }catch(e){el.innerHTML='<span style="color:var(--red);">创建失败: '+esc(e.message)+'</span>';}
}

function editGroup(id,name,desc){
  const newName=prompt('修改分组名称:',name);
  if(newName===null)return;
  const newDesc=prompt('修改描述:',desc);
  if(newDesc===null)return;
  (async()=>{
    try{
      const r=await fetch(S.base+'/api/v1/admin/groups/'+id,{
        method:'PUT',
        headers:{'X-Admin-Init-Key':admKey(),'Content-Type':'application/json'},
        body:JSON.stringify({name:newName.trim()||null,description:newDesc.trim()||null}),
      });
      if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.detail||'HTTP '+r.status);}
      toast('分组已更新','success');
      loadGroups();
      fillGroupSelect();
      loadAdminKeys();
    }catch(e){toast('更新失败: '+e.message,'error');}
  })();
}

async function deleteGroup(id,name){
  if(!confirm('确定删除分组 "'+name+'"?\n组内成员的密钥不会被删除，但会变为"无分组"状态。'))return;
  try{
    const r=await fetch(S.base+'/api/v1/admin/groups/'+id,{
      method:'DELETE',
      headers:admHeaders(),
    });
    if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.detail||'HTTP '+r.status);}
    toast('分组 "'+name+'" 已删除','success');
    loadGroups();
    fillGroupSelect();
    loadAdminKeys();
  }catch(e){toast('删除失败: '+e.message,'error');}
}
