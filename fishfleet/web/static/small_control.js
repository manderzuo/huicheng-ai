/* Small-screen controls: delegated, serial-bound, no big-screen activation. */
(()=>{
  const gestures=new Map();let controlEpoch=0;window.__smallControlEvents=[];
  window.setSmallControlEnabled=(enabled)=>{
    controlEpoch++;window.__ctrlSmall=!!enabled;
    window.__liveControl?.cancelPointer();
    gestures.clear(); // 关闭再开启也不能提交上一次未完成的手势。
    const button=document.querySelector('#right-panel [data-op="ctrl-small"]');
    button?.classList.toggle('on',window.__ctrlSmall);
    button?.setAttribute('aria-pressed',String(window.__ctrlSmall));
    button?.setAttribute('title',window.__ctrlSmall?'已开启：可点击、滚动、滑动；关闭后可拖拽排序':'已关闭：仅查看画面和拖拽排序');
  };
  window.setSmallControlEnabled(false);
  function notify(message){if(typeof window.toast==='function')window.toast(message);else console.warn('[small-control]',message);}
  function target(serial){if(window.S)window.S.selected=serial;window.__ctxSerial=serial;document.querySelectorAll('.phone').forEach(e=>e.classList.toggle('active',e.dataset.serial===serial));}
  // 系统导航栏/状态栏区域仍正常触控，只是不切换当前设备高亮。
  // point() 已逆变换回竖屏设备坐标：顶部约 80px 为状态栏，底部约 130px 为导航栏。
  function isSystemChrome(p,device){const w=Number(device?.width)||0,h=Number(device?.height)||0;if(!w||!h||!p)return false;return p.y<80||p.y>h-130;}
  function point(canvas,e,serial){
    const r=canvas.getBoundingClientRect(),d=window.S?.devices?.find(d=>d.serial===serial);
    const w=Number(d?.width),h=Number(d?.height),fw=canvas.width,fh=canvas.height;
    // Only map a rendered frame with known, orientation-consistent device dimensions.
    if(!window.__jpegLive?.hasFrame(serial,canvas)||
       !Number.isInteger(w)||!Number.isInteger(h)||w<=0||h<=0||
       !fw||!fh||!r.width||!r.height||((w>h)!==(fw>fh)))return null;
    const angle=Number(window.__smallRotate)||0,odd=angle===90||angle===270;
    if(![0,90,180,270].includes(angle))return null;
    const bw=odd?r.height:r.width,bh=odd?r.width:r.height;
    const px=e.clientX-r.left,py=e.clientY-r.top;
    let ux=px,uy=py;
    if(angle===90){ux=py;uy=bh-px;}
    else if(angle===180){ux=bw-px;uy=bh-py;}
    else if(angle===270){ux=bw-py;uy=px;}
    const scale=Math.min(bw/fw,bh/fh),drawW=fw*scale,drawH=fh*scale;
    const x=ux-(bw-drawW)/2,y=uy-(bh-drawH)/2;
    // Black letterbox pixels do not represent the device screen.
    if(!Number.isFinite(x)||!Number.isFinite(y)||x<0||y<0||x>=drawW||y>=drawH)return null;
    return {x:Math.min(w-1,Math.floor(x/drawW*w)),y:Math.min(h-1,Math.floor(y/drawH*h))};
  }
  async function send(url,body){
    try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const result=await r.json();window.__smallControlEvents.push({time:Date.now(),url,serial:body.serial,ok:r.ok&&!!result.ok});if(window.__smallControlEvents.length>100)window.__smallControlEvents.shift();if(!r.ok||!result.ok)notify('手机操作失败：'+(result.error||result.detail||'设备是否收到操作未知'));return result;}catch(e){notify('手机操作请求失败');return {ok:false};}
  }
  // ★ 多选扇出：若存在多选集合，把同一操作按比例坐标广播到每台选中手机。
  function fanoutTargets(serial){const m=window.S?.multi;if(!m||!m.size)return [serial];const targets=[...m];if(!targets.includes(serial))targets.unshift(serial);return targets.filter(s=>{const d=window.S?.devices?.find(d=>d.serial===s);return d&&d.state==='online';});}
  function scalePoint(p,fromW,fromH,toW,toH){return {x:Math.min(toW-1,Math.max(0,Math.round(p.x/fromW*toW))),y:Math.min(toH-1,Math.max(0,Math.round(p.y/fromH*toH)))};}
  async function broadcast(kind,serial,makeBody){
    const ownEpoch=controlEpoch,targets=fanoutTargets(serial);
    const src=window.S?.devices?.find(d=>d.serial===serial);
    const sw=Number(src?.width)||1080,sh=Number(src?.height)||1920;
    let ok=0;const bad=[];
    for(const s of targets){
      if(ownEpoch!==controlEpoch){notify('控制模式已变化，后续旧版操作已取消；在途操作不重发');return;}
      const d=window.S?.devices?.find(d=>d.serial===s);
      const tw=Number(d?.width)||1080,th=Number(d?.height)||1920;
      const r=await send(kind,makeBody(s,d,scalePoint,sw,sh,tw,th));
      if(r&&r.ok)ok++;else bad.push(d?.index||s);
    }
    notify(bad.length?`已下发 ${ok}/${targets.length} 台，失败：${bad.join('、')}`:`已同步操作 ${targets.length} 台`);
  }
  document.addEventListener('pointerdown',e=>{
    const cv=e.target.closest?.('canvas.phone-live');if(!cv||!window.__ctrlSmall||e.button!==0||e.ctrlKey||e.metaKey)return;
    const serial=cv.closest('.phone')?.dataset.serial;if(!serial)return;
    // 当前协议仅支持单点触摸，禁止同设备并发手势互相覆盖。
    if([...gestures.values()].some(g=>g.serial===serial)){notify('该手机已有进行中的手势');e.preventDefault();e.stopPropagation();return;}
    const p=point(cv,e,serial);if(!p){notify('画面或设备尺寸未就绪，已阻止操作');e.preventDefault();e.stopPropagation();return;}
    const device0=window.S?.devices?.find(d=>d.serial===serial);
    if(!isSystemChrome(p,device0))target(serial);
    const live=window.__liveControl;
    if(live?.enabled){
      const angle=window.__smallRotate||0,fw=cv.width,fh=cv.height,dw=device0?.width,dh=device0?.height;
      const valid=()=>{const d=window.S?.devices?.find(d=>d.serial===serial);return !!(window.__ctrlSmall&&cv.isConnected&&cv.closest('.phone')?.dataset.serial===serial&&(window.__smallRotate||0)===angle&&cv.width===fw&&cv.height===fh&&d?.width===dw&&d?.height===dh);};
      live.startPointer(e,{element:cv,point:event=>point(cv,event,serial),valid,targets:live.getTargets(serial),source_width:dw,source_height:dh});return;
    }
    try{cv.setPointerCapture(e.pointerId);}catch(err){notify('无法捕获指针，已取消操作');e.preventDefault();e.stopPropagation();return;}
    gestures.set(e.pointerId,{cv,serial,start:p,x:e.clientX,y:e.clientY,at:performance.now(),angle:window.__smallRotate||0,frameWidth:cv.width,frameHeight:cv.height,deviceWidth:device0?.width,deviceHeight:device0?.height});cv.style.touchAction='none';e.preventDefault();e.stopPropagation();
  },true);
  document.addEventListener('pointerup',e=>{
    const g=gestures.get(e.pointerId);if(!g)return;gestures.delete(e.pointerId);e.preventDefault();e.stopPropagation();const device=window.S?.devices?.find(d=>d.serial===g.serial);if(!window.__ctrlSmall||!g.cv.isConnected||g.cv.closest('.phone')?.dataset.serial!==g.serial||(window.__smallRotate||0)!==g.angle||g.cv.width!==g.frameWidth||g.cv.height!==g.frameHeight||!device||device.width!==g.deviceWidth||device.height!==g.deviceHeight)return;const end=point(g.cv,e,g.serial);if(!end)return;
    const distance=Math.hypot(e.clientX-g.x,e.clientY-g.y);
    const duration=Math.max(.12,Math.min(2,(performance.now()-g.at)/1000));
    if(distance<6)broadcast('/api/tap',g.serial,(s,d,sc,sw,sh,tw,th)=>{const q=(sw===tw&&sh===th)?end:sc(end,sw,sh,tw,th);return {serial:s,...q};});
    else broadcast('/api/swipe-points',g.serial,(s,d,sc,sw,sh,tw,th)=>{const a=(sw===tw&&sh===th)?g.start:sc(g.start,sw,sh,tw,th),b=(sw===tw&&sh===th)?end:sc(end,sw,sh,tw,th);return {serial:s,x1:a.x,y1:a.y,x2:b.x,y2:b.y,duration};});
  },true);
  document.addEventListener('pointercancel',e=>gestures.delete(e.pointerId),true);
  // 失去焦点或浏览器取消捕获时丢弃手势，不能在返回后误触。
  const invalidateLegacy=()=>{controlEpoch++;gestures.clear();};
  window.addEventListener('blur',invalidateLegacy);
  window.addEventListener('livecontrolmodechange',invalidateLegacy);
  document.addEventListener('lostpointercapture',e=>gestures.delete(e.pointerId),true);
  document.addEventListener('dragstart',e=>{if(window.__ctrlSmall&&e.target.closest?.('.phone-live'))e.preventDefault();},true);
  document.addEventListener('wheel',e=>{
    const cv=e.target.closest?.('canvas.phone-live');if(!cv||!window.__ctrlSmall)return;const serial=cv.closest('.phone')?.dataset.serial,p=point(cv,e,serial);if(!p)return;
    if([...gestures.values()].some(g=>g.serial===serial)){e.preventDefault();return;}
    const dev0=window.S?.devices?.find(d=>d.serial===serial);
    if(!isSystemChrome(p,dev0))target(serial);
    e.preventDefault();
    const delta=e.deltaY;if(!delta)return;
    if(window.__liveControl?.enabled){e.stopImmediatePropagation();window.__liveControl.scroll({targets:window.__liveControl.getTargets(serial),source_width:dev0.width,source_height:dev0.height,...p,h:0,v:delta>0?-1:1});return;}
    broadcast('/api/scroll',serial,(s,d,sc,sw,sh,tw,th)=>{const q=(sw===tw&&sh===th)?p:sc(p,sw,sh,tw,th);return {serial:s,...q,h:0,v:delta>0?-1:1};});
  },{passive:false,capture:true});
  document.addEventListener('click',e=>{
    const button=e.target.closest?.('.fm-keys [data-key], .card-keys [data-key]');if(!button)return;e.preventDefault();e.stopPropagation();const serial=button.closest('.phone')?.dataset.serial || button.closest('#float-menu')?.dataset.serial;
    if(!serial){notify('请先选择手机');return;}
    if(window.__liveControl?.enabled){e.stopImmediatePropagation();window.__liveControl.key(window.__liveControl.getTargets(serial),button.dataset.key);return;}
    broadcast('/api/key',serial,(s)=>({serial:s,key:button.dataset.key}));
  },true);
})();
