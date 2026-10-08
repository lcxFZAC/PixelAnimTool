const $ = id => document.getElementById(id);
const clone = value => structuredClone(value);
const dirs = {s:'正面',e:'向右',n:'背面',w:'向左'};
const toolNames = {pencil:'铅笔 · B',eraser:'橡皮 · E',fill:'填充 · G',picker:'吸色 · I',line:'直线 · L',rect:'矩形 · R',ellipse:'椭圆 · O',select:'选区 · M',move:'移动 · V'};
const palette = ['#0b0e14','#26352f','#536b55','#829570','#b7c8a2','#e4e7c6','#344454','#647d92','#9bafc1','#d8dfdf','#624d43','#91745c','#bea184','#e3c6a1','#563d4c','#9d6578','#cf99a0','#eccebf'];
let info, csrf, token = '', project, clipIndex=0, frameIndex=0, layerIndex=0, tool='pencil', playing=false, lastFrameTime=0;
let cache=[], currentJob=null, polling=false, mutation=0, savedMutation=0, saving=null, saveTimer, undoStack=[], redoStack=[], selection=null, clipboard=null;
let zoom=3, pan={x:0,y:0}, transform={x:0,y:0,w:0,h:0}, gesture=null, renderGeneration=0;
const canvas=$('glCanvas'), stage=$('stage');
const clip=()=>project?.clips[clipIndex];
const frame=()=>clip()?.frames[frameIndex];
const layer=()=>frame()?.layers[layerIndex];
const activeCanvas=()=>cache[frameIndex]?.[layerIndex];
const dims=()=>[project?.settings.width||96,project?.settings.height||128];

function toast(message, error=false){$('toast').textContent=message;$('toast').style.background=error?'#823e32':'#304036';$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,error?12000:5500);}
function guarded(fn){return async e=>{try{await fn(e);}catch(error){toast(error.message||String(error),true);}};}
function on(id,event,fn){$(id).addEventListener(event,guarded(fn));}
async function request(path, body, binary=false){
  const headers={'X-Workshop':csrf||''};if(token)headers.Authorization=`Bearer ${token}`;
  if(body!==undefined)headers['Content-Type']='application/json';
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok){const value=await response.json().catch(()=>({error:`服务响应 ${response.status}`}));throw new Error(value.error);}
  return binary?response.blob():response.json();
}
function makeCanvas(w,h){const c=document.createElement('canvas');c.width=w;c.height=h;return c;}
function emptyData(){return makeCanvas(...dims()).toDataURL();}
async function imageCanvas(data){const image=new Image();image.src=data;await image.decode();const c=makeCanvas(image.width,image.height);c.getContext('2d',{willReadFrequently:true}).drawImage(image,0,0);return c;}
function composite(index=frameIndex,onion=false){
  const out=makeCanvas(...dims()),ctx=out.getContext('2d');
  if(!clip())return out;
  if(onion&&index>0){ctx.globalAlpha=.2;ctx.drawImage(composite(index-1),0,0);ctx.globalAlpha=1;}
  for(let i=0;i<clip().frames[index].layers.length;i++)if(clip().frames[index].layers[i].visible&&cache[index]?.[i])ctx.drawImage(cache[index][i],0,0);
  return out;
}

class Preview {
  constructor(canvas){
    this.canvas=canvas;
    const gl=canvas.getContext('webgl',{alpha:false,antialias:false,preserveDrawingBuffer:true});this.gl=gl;
    if(!gl){this.ctx=canvas.getContext('2d');$('rendererBadge').textContent='Canvas 兼容模式';toast('浏览器未提供 WebGL，已启用清晰像素兼容预览。');return;}
    const shader=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;};
    const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,'attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}'));
    gl.attachShader(program,shader(gl.FRAGMENT_SHADER,`precision mediump float;
      uniform sampler2D tex;uniform vec2 resolution;uniform vec2 origin;uniform vec2 size;uniform float zoom;uniform float ratio;uniform float grid;
      void main(){vec2 p=vec2(gl_FragCoord.x/ratio,resolution.y-gl_FragCoord.y/ratio);float ck=mod(floor(p.x/12.)+floor(p.y/12.),2.);vec3 bg=mix(vec3(.933,.941,.914),vec3(.960,.965,.945),ck);vec2 uv=(p-origin)/(size*zoom);if(uv.x>=0.&&uv.y>=0.&&uv.x<1.&&uv.y<1.){vec4 c=texture2D(tex,uv);bg=mix(bg,c.rgb,c.a);if(grid>.5&&zoom>=6.){vec2 cell=mod(p-origin,zoom);if(cell.x<.65||cell.y<.65)bg=mix(bg,vec3(.2,.3,.2),.12);}}gl_FragColor=vec4(bg,1.);}`));
    gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));gl.useProgram(program);this.program=program;
    const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);const attr=gl.getAttribLocation(program,'p');gl.enableVertexAttribArray(attr);gl.vertexAttribPointer(attr,2,gl.FLOAT,false,0,0);
    this.texture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,this.texture);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
    this.loc=Object.fromEntries(['resolution','origin','size','zoom','ratio','grid'].map(k=>[k,gl.getUniformLocation(program,k)]));
    canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();playing=false;toast('WebGL 上下文丢失。项目仍可保存；刷新页面恢复预览。',true);});
    canvas.addEventListener('webglcontextrestored',()=>location.reload());
  }
  draw(image){
    const r=stage.getBoundingClientRect(),dpr=window.devicePixelRatio||1,w=Math.max(1,r.width),h=Math.max(1,r.height);
    const cw=Math.round(w*dpr),ch=Math.round(h*dpr);if(this.canvas.width!==cw||this.canvas.height!==ch){this.canvas.width=cw;this.canvas.height=ch;}
    transform={x:Math.round((w-image.width*zoom)/2+pan.x),y:Math.round((h-image.height*zoom)/2+pan.y),w:image.width,h:image.height};
    if(this.gl){const gl=this.gl;gl.viewport(0,0,cw,ch);gl.useProgram(this.program);gl.bindTexture(gl.TEXTURE_2D,this.texture);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,image);gl.uniform2f(this.loc.resolution,w,h);gl.uniform2f(this.loc.origin,transform.x,transform.y);gl.uniform2f(this.loc.size,image.width,image.height);gl.uniform1f(this.loc.zoom,zoom);gl.uniform1f(this.loc.ratio,dpr);gl.uniform1f(this.loc.grid,$('gridToggle').checked?1:0);gl.drawArrays(gl.TRIANGLE_STRIP,0,4);}
    else{const ctx=this.ctx;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle='#f0f2eb';ctx.fillRect(0,0,w,h);ctx.imageSmoothingEnabled=false;ctx.drawImage(image,transform.x,transform.y,image.width*zoom,image.height*zoom);}
    if(selection){Object.assign($('selectionBox').style,{left:`${transform.x+selection.x*zoom}px`,top:`${transform.y+selection.y*zoom}px`,width:`${selection.w*zoom}px`,height:`${selection.h*zoom}px`});$('selectionBox').hidden=false;}else $('selectionBox').hidden=true;
  }
}
let preview;
function draw(){if(preview)preview.draw(composite(frameIndex,$('onionToggle').checked&&!playing));$('pixelPosition').textContent=gesture?`${gesture.last.x}, ${gesture.last.y}`:$('pixelPosition').textContent;}
async function refresh(){
  const gen=++renderGeneration;
  const newCache=clip()?await Promise.all(clip().frames.map(f=>Promise.all(f.layers.map(l=>imageCanvas(l.data))))):[];
  if(gen!==renderGeneration)return;
  cache=newCache;frameIndex=Math.min(frameIndex,Math.max(0,(clip()?.frames.length||1)-1));layerIndex=Math.min(layerIndex,Math.max(0,(frame()?.layers.length||1)-1));
  renderUI();draw();
}
function option(value,label){const o=document.createElement('option');o.value=value;o.textContent=label;return o;}
function renderUI(){
  $('projectName').value=project?.name||'未命名角色';$('workspaceTitle').textContent=project?.name&&project.name!=='未命名角色'?project.name:'让每一帧，都恰到好处。';
  $('workspaceSubtitle').textContent=clip()?`${info.presets.actions[clip().action]?.name||clip().id} · ${dirs[clip().direction]||'自定义方向'} · ${clip().frames.length} 帧`:'从角色立绘到可用图集，在一个空间里完成。';
  $('referencePreview').hidden=!project?.reference;$('uploadHint').hidden=!!project?.reference;if(project?.reference)$('referencePreview').src=project.reference;else $('referencePreview').removeAttribute('src');
  const cfg=project?.settings||readSettings();$('styleSelect').value=cfg.style;$('sizeSelect').value=`${cfg.width},${cfg.height}`;if(!$('sizeSelect').value)$('sizeSelect').append(option(`${cfg.width},${cfg.height}`,`${cfg.width} × ${cfg.height}`));$('sizeSelect').value=`${cfg.width},${cfg.height}`;$('ppuSelect').value=cfg.ppu;if(!$('ppuSelect').value){$('ppuSelect').append(option(cfg.ppu,cfg.ppu));$('ppuSelect').value=cfg.ppu;}$('colorsInput').value=cfg.colors;if(cfg.outline!=='none')$('outlineInput').value=cfg.outline;
  $('sizeSelect').disabled=!!project?.clips.length;$('worldSize').textContent=`${cfg.width} × ${cfg.height} px · 画布 ${(cfg.width/cfg.ppu).toFixed(2)} × ${(cfg.height/cfg.ppu).toFixed(2)} 世界单位`;
  $('masterInfo').textContent=Object.keys(project?.masters||{}).length?`已固定：${Object.keys(project.masters).map(d=>dirs[d]).join('、')}`:'未固定方向母版';
  $('emptyState').hidden=!!clip();$('canvasLabel').textContent=clip()?`${cfg.width} × ${cfg.height} · ${cfg.ppu} PPU`:'还没有动作';
  $('clipTabs').replaceChildren(...(project?.clips||[]).map((c,i)=>{const b=document.createElement('button');b.textContent=c.id;b.className=i===clipIndex?'active':'';b.onclick=guarded(async()=>{if(currentJob)return;commitGesture();playing=false;clipIndex=i;frameIndex=0;layerIndex=0;selection=null;await refresh();});return b;}));
  $('layerList').replaceChildren(...(frame()?.layers||[]).map((l,i)=>{const row=document.createElement('div');row.className=`layer ${i===layerIndex?'active':''}`;const eye=document.createElement('input');eye.type='checkbox';eye.checked=l.visible;eye.setAttribute('aria-label',`显示图层 ${l.name}`);eye.onclick=e=>e.stopPropagation();eye.onchange=guarded(async()=>{ensureEditable();remember();l.visible=eye.checked;changed();draw();renderTimeline();renderReport();});const thumb=makeCanvas(...dims());if(cache[frameIndex]?.[i])thumb.getContext('2d').drawImage(cache[frameIndex][i],0,0);const name=document.createElement('span');name.textContent=l.name;row.append(eye,thumb,name);row.onclick=()=>{if(currentJob)return;layerIndex=i;renderUI();};return row;}).reverse());
  $('frameCounter').textContent=`${clip()?frameIndex+1:0} / ${clip()?.frames.length||0}`;$('fpsInput').value=clip()?.fps||10;$('loopToggle').checked=clip()?.loop??true;$('playBtn').textContent=playing?'Ⅱ':'▶';
  renderTimeline();renderReport();
  for(const id of ['saveBtn','exportBtn','blankBtn','deleteClip','importBtn','addLayer','layerUp','layerDown','renameLayer','deleteLayer','newFrame','duplicateFrame','deleteFrame','frameLeft','frameRight','pinMaster','reviewSelect'])$(id).disabled=!!currentJob;
  $('newBtn').disabled=!!currentJob;$('projectSelect').disabled=!!currentJob;
  $('generateBtn').disabled=!$('geminiEnabled').checked||!!currentJob;
  $('undoBtn').disabled=!undoStack.length||!!currentJob;$('redoBtn').disabled=!redoStack.length||!!currentJob;
}
function renderTimeline(){
  if(!clip()){$('frameStrip').replaceChildren();return;}
  $('frameStrip').replaceChildren(...clip().frames.map((f,i)=>{const b=document.createElement('button');b.className=`frame-tile ${i===frameIndex?'active':''}`;b.setAttribute('aria-label',`第 ${i+1} 帧`);const img=composite(i),label=document.createElement('span');label.textContent=String(i+1).padStart(2,'0');b.append(img,label);b.onclick=guarded(async()=>{commitGesture();playing=false;frameIndex=i;layerIndex=Math.min(layerIndex,f.layers.length-1);selection=null;renderUI();draw();});return b;}));
}
function renderReport(){
  const c=clip(),root=$('qualityReport');root.replaceChildren();
  if(!c){root.textContent='完成一个动作后，在这里检查像素结构与验收状态。';$('reviewBadge').textContent='待制作';return;}
  const state=c.review?.state||'candidate';$('reviewBadge').textContent={candidate:'待验收',accepted:'已验收',revision:'需返修'}[state];$('reviewBadge').className=`badge ${state}`;$('reviewSelect').value=state;
  const add=(s,cls='')=>{const p=document.createElement('p');p.textContent=s;p.className=cls;root.append(p);};
  if(mutation!==savedMutation){add('编辑中；保存后更新技术检查。');return;}
  if(c.report){add(`${c.report.uniqueFrames} 个不同帧 · ${c.report.colors} 种颜色`);for(const b of c.report.blockers)add(b,'error');for(const w of c.report.warnings)add(w,'warn');if(c.report.technicalPass)add('结构检查通过 · 仍需检查身份、手形、配饰与动作。');}
  else add('保存后检查像素结构。');
}
function readSettings(){const [width,height]=($('sizeSelect').value||'96,128').split(',').map(Number);return {width,height,ppu:Number($('ppuSelect').value||48),style:$('styleSelect').value||'jiuzhen',colors:Number($('colorsInput').value||32),outline:$('outlineInput').value||'#0b0e14'};}
function ensureProject(){if(!project){project={name:$('projectName').value||'未命名角色',settings:readSettings(),reference:null,masters:{},clips:[]};mutation=0;savedMutation=0;}return project;}
function ensureEditable(){if(currentJob)throw Error('生成期间暂不能修改项目，请等待完成或取消任务。');ensureProject();}
function remember(){ensureEditable();undoStack.push({project:clone(project),clipIndex,frameIndex,layerIndex});if(undoStack.length>24)undoStack.shift();redoStack=[];}
function changed(invalidate=true){mutation++;if(invalidate&&clip())clip().review={state:'candidate'};$('saveStatus').textContent='等待保存…';clearTimeout(saveTimer);saveTimer=setTimeout(()=>save().catch(e=>toast(e.message,true)),1000);}
async function save(){
  clearTimeout(saveTimer);if(!project||mutation===savedMutation&&project.id)return project;if(gesture)commitGesture();
  if(saving){await saving;if(mutation!==savedMutation)return save();return project;}
  if(currentJob)throw Error('任务运行中，项目由后台保存。');
  const generation=mutation, snapshot=clone(project);$('saveStatus').textContent='保存中…';
  saving=request('/api/save',{id:project.id,version:project.version,project:snapshot}).then(async saved=>{
    project.id=saved.id;project.version=saved.version;project.updatedAt=saved.updatedAt;savedMutation=generation;
    if(mutation===generation){project=saved;renderUI();$('saveStatus').textContent='已保存';}else $('saveStatus').textContent='等待保存…';
    await listProjects();return project;
  }).catch(error=>{$('saveStatus').textContent='保存失败 · 请重试';throw error;}).finally(()=>saving=null);
  await saving;if(mutation!==savedMutation)return save();return project;
}
async function listProjects(){const items=await request('/api/projects');$('projectSelect').replaceChildren(option('','选择已保存项目'),...items.map(p=>option(p.id,`${p.name} · ${p.clips} 个动作`)));$('projectSelect').value=project?.id||'';}
async function loadProject(id){await save();project=await request(`/api/projects/${id}`);clipIndex=0;frameIndex=0;layerIndex=0;undoStack=[];redoStack=[];mutation=0;savedMutation=0;selection=null;pan={x:0,y:0};$('saveStatus').textContent='已保存';await refresh();}
async function askName(title,initial){$('textTitle').textContent=title;$('textInput').value=initial;$('textDialog').showModal();$('textInput').select();return new Promise(resolve=>{const done=value=>{$('textDialog').close();$('textForm').onsubmit=null;$('textCancel').onclick=null;$('textDialog').oncancel=null;resolve(value);};$('textForm').onsubmit=e=>{e.preventDefault();done($('textInput').value.trim());};$('textCancel').onclick=()=>done(null);$('textDialog').oncancel=e=>{e.preventDefault();done(null);};});}
async function newProject(){ensureEditable();await save();const name=await askName('新建角色项目','新角色');if(!name)return;project={name,settings:readSettings(),reference:null,masters:{},clips:[]};mutation=1;savedMutation=0;clipIndex=frameIndex=layerIndex=0;undoStack=[];redoStack=[];selection=null;pan={x:0,y:0};await save();await refresh();}
async function blankClip(){ensureEditable();const name=await askName('动作 ID（小写字母、数字、下划线）',`custom_${project.clips.length+1}`);if(!name)return;if(!/^[a-z][a-z0-9_]{0,63}$/.test(name))throw Error('请使用 snake_case，如 walk_e');if(project.clips.some(c=>c.id===name))throw Error('动作名称已存在');remember();project.clips.push({id:name,action:'custom',direction:'s',fps:10,loop:true,frames:[{layers:[{name:'底稿',visible:true,data:emptyData()}]}],review:{state:'candidate'}});clipIndex=project.clips.length-1;frameIndex=layerIndex=0;selection=null;changed();await refresh();}

function colorRGBA(){const c=$('paintColor').value;return [parseInt(c.slice(1,3),16),parseInt(c.slice(3,5),16),parseInt(c.slice(5,7),16),255];}
function point(event){const r=canvas.getBoundingClientRect();return {x:Math.floor((event.clientX-r.left-transform.x)/zoom),y:Math.floor((event.clientY-r.top-transform.y)/zoom)};}
function inside(p){const [w,h]=dims();return p.x>=0&&p.y>=0&&p.x<w&&p.y<h;}
function brush(p,erase=false){const ctx=activeCanvas().getContext('2d');const size=Math.max(1,Math.min(8,Number($('brushSize').value)||1));const x=p.x-Math.floor((size-1)/2),y=p.y-Math.floor((size-1)/2);if(erase)ctx.clearRect(x,y,size,size);else{ctx.fillStyle=$('paintColor').value;ctx.fillRect(x,y,size,size);}}
function line(a,b,erase=false){let x=a.x,y=a.y;const dx=Math.abs(b.x-a.x),sx=a.x<b.x?1:-1,dy=-Math.abs(b.y-a.y),sy=a.y<b.y?1:-1;let err=dx+dy;for(let guard=0;guard<20000;guard++){brush({x,y},erase);if(x===b.x&&y===b.y)break;const e=2*err;if(e>=dy){err+=dy;x+=sx;}if(e<=dx){err+=dx;y+=sy;}}}
function fill(p){const ctx=activeCanvas().getContext('2d'),[w,h]=dims(),im=ctx.getImageData(0,0,w,h),data=im.data,start=(p.y*w+p.x)*4,old=Array.from(data.slice(start,start+4)),next=colorRGBA();if(old.every((x,i)=>x===next[i]))return;const stack=[p.y*w+p.x];while(stack.length){const index=stack.pop(),offset=index*4;if(!old.every((x,i)=>data[offset+i]===x))continue;data.set(next,offset);const x=index%w,y=Math.floor(index/w);if(x)stack.push(index-1);if(x<w-1)stack.push(index+1);if(y)stack.push(index-w);if(y<h-1)stack.push(index+w);}ctx.putImageData(im,0,0);}
function rectBetween(a,b){const [w,h]=dims(),x=Math.max(0,Math.min(a.x,b.x)),y=Math.max(0,Math.min(a.y,b.y));return {x,y,w:Math.min(w,Math.max(a.x,b.x)+1)-x,h:Math.min(h,Math.max(a.y,b.y)+1)-y};}
function applyGesture(p){
  const g=gesture;if(!g)return;
  const c=activeCanvas(),ctx=c.getContext('2d');
  if(tool==='pencil'||tool==='eraser')line(g.last,p,tool==='eraser');
  else if(tool==='select')selection=rectBetween(g.start,p);
  else if(['line','rect','ellipse'].includes(tool)){
    ctx.putImageData(g.before,0,0);
    if(tool==='line')line(g.start,p);
    else if(tool==='rect'){line(g.start,{x:p.x,y:g.start.y});line({x:p.x,y:g.start.y},p);line(p,{x:g.start.x,y:p.y});line({x:g.start.x,y:p.y},g.start);}
    else{const cx=(g.start.x+p.x)/2,cy=(g.start.y+p.y)/2,rx=Math.abs(p.x-g.start.x)/2,ry=Math.abs(p.y-g.start.y)/2;let previous=null;const steps=Math.max(16,Math.ceil(Math.max(rx,ry)*10));for(let i=0;i<=steps;i++){const t=i/steps*Math.PI*2,pt={x:Math.round(cx+Math.cos(t)*rx),y:Math.round(cy+Math.sin(t)*ry)};if(previous)line(previous,pt);previous=pt;}}
  }else if(tool==='move'){
    ctx.putImageData(g.before,0,0);const r=g.region,dx=p.x-g.start.x,dy=p.y-g.start.y;ctx.clearRect(r.x,r.y,r.w,r.h);ctx.drawImage(g.cut,r.x+dx,r.y+dy);if(g.hadSelection)selection={...r,x:r.x+dx,y:r.y+dy};
  }
  g.last=p;draw();
}
function commitGesture(){if(!gesture)return;const editing=gesture.editing;gesture=null;if(editing&&layer()){layer().data=activeCanvas().toDataURL();changed();renderTimeline();renderReport();}renderUI();draw();}
on('glCanvas','pointerdown',e=>{
  if(e.button!==0||!clip()||currentJob)return;playing=false;renderUI();const p=point(e);if(!inside(p))return;canvas.focus();canvas.setPointerCapture(e.pointerId);
  if(tool==='picker'){const rgba=composite().getContext('2d').getImageData(p.x,p.y,1,1).data;if(rgba[3]){$('paintColor').value='#'+Array.from(rgba.slice(0,3)).map(n=>n.toString(16).padStart(2,'0')).join('');renderPalette();}return;}
  if(!layer().visible)throw Error('请先显示当前图层再绘制');
  const editing=tool!=='select';if(editing){clearTimeout(saveTimer);remember();}
  const c=activeCanvas();gesture={start:p,last:p,editing,before:c.getContext('2d').getImageData(0,0,c.width,c.height)};
  if(tool==='move'){const r=selection?{...selection}:{x:0,y:0,w:c.width,h:c.height};const cut=makeCanvas(r.w,r.h);cut.getContext('2d').drawImage(c,r.x,r.y,r.w,r.h,0,0,r.w,r.h);Object.assign(gesture,{region:r,cut,hadSelection:!!selection});}
  if(tool==='fill'){fill(p);commitGesture();}else applyGesture(p);
});
on('glCanvas','pointermove',e=>{const p=point(e);$('pixelPosition').textContent=inside(p)?`${p.x}, ${p.y} px`:'—';if(gesture){const [w,h]=dims();applyGesture({x:Math.max(-w,Math.min(w*2,p.x)),y:Math.max(-h,Math.min(h*2,p.y))});}});
on('glCanvas','pointerup',()=>commitGesture());on('glCanvas','pointercancel',()=>commitGesture());
canvas.addEventListener('wheel',e=>{e.preventDefault();if(e.shiftKey)pan.x-=e.deltaY;else pan.y-=e.deltaY;draw();},{passive:false});
function setTool(value){commitGesture();tool=value;$('toolLabel').textContent=toolNames[value];document.querySelectorAll('[data-tool]').forEach(b=>{b.classList.toggle('selected',b.dataset.tool===value);b.setAttribute('aria-pressed',String(b.dataset.tool===value));});}
document.querySelectorAll('[data-tool]').forEach(b=>b.onclick=()=>setTool(b.dataset.tool));
async function history(redo=false){ensureEditable();commitGesture();const from=redo?redoStack:undoStack,to=redo?undoStack:redoStack;if(!from.length)return;to.push({project:clone(project),clipIndex,frameIndex,layerIndex});const s=from.pop(),id=project.id,version=project.version;project=s.project;project.id=id;project.version=version;clipIndex=s.clipIndex;frameIndex=s.frameIndex;layerIndex=s.layerIndex;selection=null;changed();await refresh();}
function renderPalette(colors=palette){$('palette').replaceChildren(...colors.map(color=>{const b=document.createElement('button');b.className='swatch'+($('paintColor').value===color?' active':'');b.style.background=color;b.title=color;b.setAttribute('aria-label',`颜色 ${color}`);b.onclick=()=>{$('paintColor').value=color;renderPalette(colors);};return b;}));}
async function editFrames(kind){ensureEditable();if(!clip())return;remember();const c=clip();if(kind==='new'||kind==='duplicate'){if(c.frames.length>=32)throw Error('单个动作最多 32 帧');const f=kind==='new'?{layers:[{name:'底稿',visible:true,data:emptyData()}]}:clone(frame());c.frames.splice(frameIndex+1,0,f);frameIndex++;}
  else if(kind==='delete'){if(c.frames.length===1)throw Error('至少保留一帧');c.frames.splice(frameIndex,1);frameIndex=Math.min(frameIndex,c.frames.length-1);}
  else{const other=frameIndex+(kind==='left'?-1:1);if(other<0||other>=c.frames.length)return;[c.frames[frameIndex],c.frames[other]]=[c.frames[other],c.frames[frameIndex]];frameIndex=other;}
  layerIndex=0;selection=null;playing=false;changed();await refresh();}
async function editLayer(kind){ensureEditable();if(!frame())return;remember();const layers=frame().layers;
  if(kind==='add'){if(layers.length>=8)throw Error('每帧最多 8 个图层');layers.push({name:`图层 ${layers.length+1}`,visible:true,data:emptyData()});layerIndex=layers.length-1;}
  else if(kind==='delete'){if(layers.length===1)throw Error('至少保留一个图层');layers.splice(layerIndex,1);layerIndex=Math.max(0,layerIndex-1);}
  else if(kind==='rename'){const name=await askName('图层名称',layer().name);if(!name)return;layer().name=name;}
  else{const other=layerIndex+(kind==='up'?1:-1);if(other<0||other>=layers.length)return;[layers[other],layers[layerIndex]]=[layers[layerIndex],layers[other]];layerIndex=other;}
  changed();await refresh();}
function togglePlay(){if(!clip())return;commitGesture();playing=!playing;lastFrameTime=0;selection=null;renderUI();draw();}
function animate(now){if(playing&&clip()&&now-lastFrameTime>=1000/clip().fps){lastFrameTime=now;if(frameIndex+1>=clip().frames.length){if(clip().loop)frameIndex=0;else playing=false;}else frameIndex++;layerIndex=Math.min(layerIndex,frame().layers.length-1);$('frameCounter').textContent=`${frameIndex+1} / ${clip().frames.length}`;$('playBtn').textContent=playing?'Ⅱ':'▶';document.querySelectorAll('.frame-tile').forEach((b,i)=>b.classList.toggle('active',i===frameIndex));draw();}requestAnimationFrame(animate);}

async function readFile(file){if(!file)throw Error('请先选择文件');if(file.size>18*1024*1024)throw Error('图片请控制在 18 MB 以内');return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('文件读取失败'));reader.readAsDataURL(file);});}
async function setReference(file){ensureEditable();const data=await readFile(file);await imageCanvas(data);remember();project.reference=data;changed(false);await refresh();}
async function importFile(){
  ensureEditable();const file=$('atlasInput').files[0];if(!file)throw Error('请选择图集或项目文件');
  if(file.name.endsWith('.json')){if(file.size>48*1024*1024)throw Error('项目超过 48 MB');const p=JSON.parse(await file.text());await save();const saved=await request('/api/save',{project:p});project=saved;clipIndex=frameIndex=layerIndex=0;mutation=savedMutation=0;undoStack=[];redoStack=[];selection=null;$('importDialog').close();await listProjects();await refresh();toast('项目已作为新副本导入。');return;}
  const id=$('importName').value.trim();if(!/^[a-z][a-z0-9_]{0,63}$/.test(id))throw Error('动作 ID 请使用小写 snake_case');if(project.clips.some(c=>c.id===id))throw Error('此动作已存在，请换一个名称保留原稿');
  $('confirmImport').disabled=true;
  try{const result=await request('/api/process',{image:await readFile(file),settings:project.settings,columns:Number($('columnsInput').value),rows:Number($('rowsInput').value),count:Number($('countInput').value),background:$('backgroundSelect').value,fit:$('fitInput').checked,quantize:$('quantizeInput').checked,outline:$('fixedOutlineInput').checked});remember();const direction=id.split('_').at(-1),action=id.replace(/_[senw]$/,'');project.clips.push({id,action,direction:dirs[direction]?direction:'s',fps:info.presets.actions[action]?.fps||10,loop:!['death','attack','cast','hit'].includes(action),frames:result.frames,provenance:result.provenance,review:{state:'candidate'}});clipIndex=project.clips.length-1;frameIndex=layerIndex=0;selection=null;changed();await save();await refresh();$('importDialog').close();toast('已导入。请检查切片边缘、动作连贯性与原生像素。');}finally{$('confirmImport').disabled=false;}
}
async function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
async function generate(){ensureEditable();await save();if(!project?.reference)throw Error('先上传并保存一张正面立绘');const body={id:project.id,enabled:$('geminiEnabled').checked,key:$('apiKey').value,model:$('modelInput').value.trim(),pack:$('packSelect').value,action:$('actionSelect').value,direction:$('directionSelect').value,keyword:$('keyword').value,repair:$('repairText').value,replace:$('replaceClip').checked};const job=await request('/api/generate',body);currentJob=job.id;sessionStorage.setItem('pixel-workshop-job',JSON.stringify({id:job.id,projectId:project.id}));playing=false;renderUI();await pollJob();}
async function pollJob(){
  if(polling||!currentJob)return;polling=true;
  try{const job=await request(`/api/jobs/${currentJob}`);$('jobPanel').hidden=false;$('jobTitle').textContent={queued:'排队中',running:`正在制作 ${job.current}`,cancelling:'正在停止',completed:'生成结束 · 等待验收',cancelled:'已停止后续生成',failed:'需要处理后续做'}[job.state];$('jobCounts').textContent=`${job.done} / ${job.total} 个动作`;$('jobProgress').max=job.total;$('jobProgress').value=job.done;$('jobMessage').textContent=job.message;$('cancelJob').disabled=job.state!=='running'&&job.state!=='queued';
    if(!['queued','running','cancelling'].includes(job.state)){currentJob=null;sessionStorage.removeItem('pixel-workshop-job');project=await request(`/api/projects/${job.projectId}`);mutation=savedMutation=0;undoStack=[];redoStack=[];clipIndex=Math.max(0,project.clips.length-1);frameIndex=layerIndex=0;await refresh();await listProjects();$('saveStatus').textContent='已保存';if(job.state==='failed')toast(job.message,true);return;}
  }catch(error){toast(error.message,true);currentJob=null;sessionStorage.removeItem('pixel-workshop-job');renderUI();}
  finally{polling=false;if(currentJob)setTimeout(pollJob,2000);}
}
async function copyPrompt(){const a=$('actionSelect').value,d=$('directionSelect').value,cfg=readSettings(),spec=info.presets.actions[a],sty=info.presets.styles.find(s=>s.id===cfg.style),n=spec.frames,grid=n===8?'4 列 2 行':n===6?'3 列 2 行':'2 列 2 行';const prompt=`以我上传的正面立绘锁定身份，制作 ${a} / ${dirs[d]} 的独立动作图集。风格：${sty.description}\n精确 ${n} 帧，${grid}，每格相同，预留空白，纯洋红 #ff00ff 背景，无文字与网格线。目标原生格 ${cfg.width}×${cfg.height}，固定描边 ${cfg.outline}，共享 ${cfg.colors} 色。\n相位：${spec.phases.join(' → ')}\n${spec.rule}\n禁止复制、镜像或整体平移冒充动画。服饰、配饰侧别和头脸比例保持稳定。\n用户补充：${$('keyword').value}\n输出实际图稿，未经过精确像素处理和逐帧验收前标记为候选。`;await navigator.clipboard.writeText(prompt);toast('当前单个动作的生成要求已复制，可粘贴到 Gem 对话。');}

on('newBtn','click',newProject);on('blankBtn','click',blankClip);on('emptyBlank','click',blankClip);on('saveBtn','click',async()=>{await save();toast('项目已保存。');});
on('deleteClip','click',async()=>{ensureEditable();if(!clip())return;remember();project.clips.splice(clipIndex,1);clipIndex=Math.min(clipIndex,Math.max(0,project.clips.length-1));frameIndex=layerIndex=0;selection=null;playing=false;changed(false);await refresh();toast('动作已移除，可用撤销恢复。');});
on('projectSelect','change',async()=>{const id=$('projectSelect').value;if(id)await loadProject(id);});
on('projectName','input',()=>{ensureEditable();project.name=$('projectName').value.trim()||'未命名角色';changed(false);$('workspaceTitle').textContent=project.name;});
on('referenceInput','change',()=>setReference($('referenceInput').files[0]));on('clearReference','click',async()=>{ensureEditable();remember();project.reference=null;changed(false);await refresh();});
$('dropzone').addEventListener('dragover',e=>{e.preventDefault();$('dropzone').classList.add('drag');});$('dropzone').addEventListener('dragleave',()=>$('dropzone').classList.remove('drag'));on('dropzone','drop',async e=>{e.preventDefault();$('dropzone').classList.remove('drag');await setReference(e.dataTransfer.files[0]);});on('dropzone','keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('referenceInput').click();}});
for(const id of ['styleSelect','sizeSelect','ppuSelect','colorsInput','outlineInput'])on(id,'change',async()=>{ensureEditable();if(id==='styleSelect'){const style=info.presets.styles.find(s=>s.id===$('styleSelect').value);$('colorsInput').value=style.colors;$('outlineInput').value=style.outline;}remember();project.settings=readSettings();changed(false);await refresh();});
on('geminiEnabled','change',()=>{const enabled=$('geminiEnabled').checked;$('geminiFields').hidden=!enabled;if(!enabled)$('apiKey').value='';$('generateBtn').textContent=enabled?'生成缺失动作 →':'开启 Gemini 后生成';$('generationHint').textContent=enabled?'一次制作一个动作与方向；已有动作会跳过。不会自动反复请求消耗额度。':'AI 默认关闭。你可以先导入图集或从空白画布开始。';renderUI();});
on('packSelect','change',()=>{$('singleFields').hidden=$('packSelect').value!=='single';if($('packSelect').value!=='single')$('replaceClip').checked=false;});
on('generateBtn','click',generate);on('promptBtn','click',copyPrompt);on('cancelJob','click',async()=>{if(currentJob){await request('/api/cancel',{id:currentJob});await pollJob();}});
on('pinMaster','click',async()=>{ensureEditable();if(!clip())throw Error('先选择一帧已核对的角色图');remember();project.masters[$('masterDirection').value]=composite().toDataURL();changed(false);renderUI();toast('已固定方向母版，后续该方向生成会引用这张图。');});
on('undoBtn','click',()=>history(false));on('redoBtn','click',()=>history(true));on('paintColor','input',()=>renderPalette());on('samplePalette','click',()=>{if(!clip())return;const im=composite().getContext('2d').getImageData(0,0,...dims()).data,counts=new Map();for(let i=0;i<im.length;i+=4)if(im[i+3]){const hex='#'+Array.from(im.slice(i,i+3)).map(n=>n.toString(16).padStart(2,'0')).join('');counts.set(hex,(counts.get(hex)||0)+1);}renderPalette([...counts.entries()].sort((a,b)=>b[1]-a[1]).slice(0,36).map(x=>x[0]));});
for(const [id,kind] of Object.entries({newFrame:'new',duplicateFrame:'duplicate',deleteFrame:'delete',frameLeft:'left',frameRight:'right'}))on(id,'click',()=>editFrames(kind));
for(const [id,kind] of Object.entries({addLayer:'add',deleteLayer:'delete',renameLayer:'rename',layerUp:'up',layerDown:'down'}))on(id,'click',()=>editLayer(kind));
on('playBtn','click',togglePlay);on('fpsInput','change',()=>{ensureEditable();if(!clip())return;const fps=Number($('fpsInput').value);if(!Number.isInteger(fps)||fps<1||fps>60)throw Error('FPS 必须在 1～60 之间');remember();clip().fps=fps;changed(false);});on('loopToggle','change',()=>{ensureEditable();if(!clip())return;remember();clip().loop=$('loopToggle').checked;changed(false);});
on('zoomSelect','change',()=>{zoom=Number($('zoomSelect').value);pan={x:0,y:0};draw();});on('gridToggle','change',draw);on('onionToggle','change',draw);
on('reviewSelect','change',async()=>{if(!clip())return;const state=$('reviewSelect').value,cid=clip().id;await save();project=await request('/api/review',{id:project.id,version:project.version,clipId:cid,state});renderUI();toast(state==='accepted'?'验收已绑定当前图集。后续改图会重新待验收。':'验收状态已更新。');});
for(const id of ['importBtn','emptyImport'])on(id,'click',()=>{ensureEditable();$('importDialog').showModal();});on('confirmImport','click',importFile);on('atlasInput','change',async()=>{const file=$('atlasInput').files[0];if(!file)return;if(file.name.endsWith('.json')){$('importFileInfo').textContent='将导入为新项目副本，保留图层与帧。';return;}const c=await imageCanvas(await readFile(file));$('importFileInfo').textContent=`${file.name} · ${c.width} × ${c.height} px`;});
on('exportBtn','click',async()=>{await save();if(!project?.clips.length)throw Error('先创建或导入一个动作');const blob=await request('/api/export',{id:project.id},true);await download(blob,`${project.name.replace(/[<>:"/\\|?*]/g,'_')}-素材包.zip`);toast('导出包包含全部候选与验收状态，请按 manifest 区分。');});
on('sourcesBtn','click',async()=>{if(!project?.id)throw Error('先保存项目');const ids=await request(`/api/sources/${project.id}`);$('sourcesList').replaceChildren(...ids.map(id=>{const b=document.createElement('button');b.textContent=`下载 ${id.slice(0,12)}…`;b.onclick=guarded(async()=>download(await request(`/api/sources/${project.id}/${id}`,undefined,true),`${id}.png`));return b;}));if(!ids.length)$('sourcesList').textContent='暂无 AI 源稿';});
for(const id of ['helpBtn'])on(id,'click',()=>$('helpDialog').showModal());for(const id of ['closeHelp','closeHelpBottom'])on(id,'click',()=>$('helpDialog').close());on('workspaceTab','click',()=>$('helpDialog').close());
on('shutdownBtn','click',async()=>{await save();await request('/api/shutdown',{});toast('服务已退出，可以关闭页面。');$('connection').textContent='服务已退出';});
document.addEventListener('keydown',guarded(async e=>{
  if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)||document.querySelector('dialog[open]'))return;
  if(e.ctrlKey||e.metaKey){const key=e.key.toLowerCase();if(['z','y','s','c','v'].includes(key))e.preventDefault();if(key==='z')return history(e.shiftKey);if(key==='y')return history(true);if(key==='s')return save();
    if(key==='c'&&selection&&activeCanvas()){clipboard=makeCanvas(selection.w,selection.h);clipboard.getContext('2d').drawImage(activeCanvas(),selection.x,selection.y,selection.w,selection.h,0,0,selection.w,selection.h);toast('已复制选区。');}
    if(key==='v'&&clipboard&&activeCanvas()){ensureEditable();remember();const x=selection?.x||0,y=selection?.y||0;activeCanvas().getContext('2d').drawImage(clipboard,x,y);layer().data=activeCanvas().toDataURL();selection={x,y,w:clipboard.width,h:clipboard.height};changed();renderTimeline();draw();}return;
  }
  if(e.key===' '){e.preventDefault();togglePlay();return;}
  if(e.key==='Escape'){selection=null;draw();return;}
  if(e.key==='Delete'&&selection&&activeCanvas()){ensureEditable();remember();activeCanvas().getContext('2d').clearRect(selection.x,selection.y,selection.w,selection.h);layer().data=activeCanvas().toDataURL();changed();renderTimeline();draw();return;}
  if(['ArrowLeft','ArrowRight'].includes(e.key)&&clip()){e.preventDefault();playing=false;frameIndex=(frameIndex+(e.key==='ArrowRight'?1:-1)+clip().frames.length)%clip().frames.length;layerIndex=0;selection=null;renderUI();draw();return;}
  const keys={b:'pencil',e:'eraser',g:'fill',i:'picker',l:'line',r:'rect',o:'ellipse',m:'select',v:'move'};if(keys[e.key.toLowerCase()])setTool(keys[e.key.toLowerCase()]);
}));
window.addEventListener('beforeunload',e=>{if(mutation!==savedMutation){e.preventDefault();e.returnValue='';}});
async function init(){
  info=await request('/api/info');csrf=info.csrf;
  for(const s of info.presets.styles)$('styleSelect').append(option(s.id,s.name));for(const [w,h] of info.presets.sizes)$('sizeSelect').append(option(`${w},${h}`,`${w} × ${h}`));for(const p of info.presets.ppu)$('ppuSelect').append(option(p,p));$('ppuSelect').value=48;for(const [key,value] of Object.entries(info.presets.actions))$('actionSelect').append(option(key,value.name));$('actionSelect').value='walk';
  preview=new Preview(canvas);renderPalette();new ResizeObserver(draw).observe(stage);requestAnimationFrame(animate);
  if(info.cloud){$('connection').textContent='在线工作空间';$('shutdownBtn').hidden=true;$('loginDialog').showModal();await new Promise(resolve=>{$('loginForm').onsubmit=guarded(async e=>{e.preventDefault();token=$('accessToken').value;try{await listProjects();$('accessToken').value='';$('loginDialog').close();resolve();}catch(error){$('loginError').textContent=error.message;token='';}});});}else await listProjects();
  const stored=sessionStorage.getItem('pixel-workshop-job');if(stored){try{const j=JSON.parse(stored);project=await request(`/api/projects/${j.projectId}`);currentJob=j.id;await refresh();await pollJob();}catch{sessionStorage.removeItem('pixel-workshop-job');currentJob=null;}}
  renderUI();draw();
}
init().catch(e=>toast(`启动失败：${e.message}`,true));
