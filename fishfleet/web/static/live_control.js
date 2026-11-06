/* Shared, bounded realtime control transport. Video connections are never touched. */
(()=>{
  'use strict';
  const HIGH_WATER=64*1024, MAX_PENDING=32;
  let heartbeat=null;
  let wanted=true, configured=false, available=false, socket=null, epoch=0, retry=null;
  let active=null, counter=0; const pending=new Map();
  try{wanted=localStorage.getItem('ff_live_control')!=='0';}catch(_){}
  const notice=text=>{if(typeof window.toast==='function')window.toast(text);else console.warn('[live-control]',text);};
  const enabled=()=>configured&&available&&wanted;
  const ready=()=>enabled()&&socket?.readyState===1;
  function render(){const b=document.getElementById('live-control-mode');if(!b)return;
    b.textContent=enabled()?'实时控制':'旧版控制';b.setAttribute('aria-pressed',String(enabled()));
    b.title=enabled()?(ready()?'实时控制已连接；点击切回旧版':'实时控制连接中；当前操作会拒绝，不会重播'):(available?'点击启用实时控制':'后端实时控制不可用，当前使用旧版控制；点击重新检查');}
  function forget(id){const g=pending.get(id);if(g)clearTimeout(g.timer);pending.delete(id);}
  function clearPending(){for(const id of pending.keys())forget(id);}
  function armTimeout(g){clearTimeout(g.timer);g.timer=setTimeout(()=>{if(!pending.has(g.id))return;notice('实时控制响应超时，已断开清理；不会重播');stopSocket();scheduleReconnect();},5000);}
  function stopSocket(){clearInterval(heartbeat);heartbeat=null;clearTimeout(retry);retry=null;const old=socket;socket=null;epoch++;clearPending();active=null;releasePointer();try{old?.close();}catch(_){}render();}
  function transmit(message){
    if(!ready())return false;
    if(socket.bufferedAmount>HIGH_WATER){notice('实时控制拥塞，已中止手势；不会重播');stopSocket();scheduleReconnect();return false;}
    try{socket.send(JSON.stringify(message));return true;}catch(_){stopSocket();scheduleReconnect();return false;}
  }
  function scheduleReconnect(){if(!enabled()||retry)return;retry=setTimeout(()=>{retry=null;connect();},1000);}
  function connect(){
    if(!enabled()||socket)return;const own=++epoch;
    try{socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/ws/control`);}catch(_){socket=null;scheduleReconnect();return;}
    const ws=socket;
    ws.onopen=()=>{if(own!==epoch)return;clearInterval(heartbeat);heartbeat=setInterval(()=>{if(active)transmit({type:'ping',gesture_id:active.id});},2000);render();};
    ws.onmessage=e=>{if(own!==epoch)return;let m;try{m=JSON.parse(e.data);}catch(_){return;}
      const id=m.gesture_id||m.gid;
      if(m.type==='error'){
        if(id&&pending.has(id)){if(active?.id===id)cancel(id,'server-error');forget(id);}
        notice('实时操作失败'+(m.serial?' ['+m.serial+']':'')+'：'+(m.error||m.reason||m.code||'未知错误'));
        return;
      }
      if(!id||!pending.has(id))return;
      if(m.type==='accepted')clearTimeout(pending.get(id).timer);
      else if(m.type==='ended')forget(id);
    };
    ws.onerror=()=>{};
    ws.onclose=()=>{if(own!==epoch)return;const had=!!active;clearInterval(heartbeat);heartbeat=null;socket=null;active=null;clearPending();releasePointer();render();if(had)notice('实时控制断开，当前手势已停止；不会重播');scheduleReconnect();};
  }
  function getTargets(source){
    const m=window.S?.multi, ds=window.S?.devices||[];
    if(!m?.size||!m.has(source))return [source];
    return [source,...m].filter((s,i,a)=>a.indexOf(s)===i&&ds.some(d=>d.serial===s&&d.state==='online'));
  }
  function begin({targets,source_width,source_height,x,y}){
    if(!ready()){notice('实时控制尚未就绪，本次操作未发送；请稍后重试或切换旧版控制');return null;}
    if(active){notice('已有进行中的实时手势');return null;}
    if(pending.size>=MAX_PENDING){notice('实时控制响应积压，已断开清理；请重试');stopSocket();scheduleReconnect();return null;}
    if(!targets?.length||![source_width,source_height].every(v=>Number.isInteger(v)&&v>0)||![x,y].every(Number.isInteger))return null;
    const id=`g-${Date.now().toString(36)}-${++counter}-${epoch}`;
    const g={id,seq:0,x,y};active=g;pending.set(id,g);
    if(!transmit({type:'begin',gesture_id:id,seq:0,targets:[...targets],source_width,source_height,x,y})){active=null;forget(id);return null;}
    armTimeout(g);return id;
  }
  function move(id,x,y){const g=active;if(!g||g.id!==id)return false;g.x=x;g.y=y;return transmit({type:'move',gesture_id:id,seq:++g.seq,x,y});}
  function finish(type,id,x,y){const g=active;if(!g||g.id!==id)return false;active=null;
    const ok=transmit({type,gesture_id:id,seq:++g.seq,x:Number.isInteger(x)?x:g.x,y:Number.isInteger(y)?y:g.y});
    if(!ok)forget(id);else armTimeout(g);return ok;
  }
  function end(id,x,y){return finish('end',id,x,y);}
  function cancel(id=active?.id,reason){return finish('cancel',id);}
  function key(targets,key){if(!ready()){notice('实时控制尚未就绪，按键未发送');return false;}return transmit({type:'key',targets:[...targets],key});}
  function scroll({targets,source_width,source_height,x,y,h,v}){if(!ready()){notice('实时控制尚未就绪，滚动未发送');return false;}return transmit({type:'scroll',targets:[...targets],source_width,source_height,x,y,h,v});}
  async function refreshConfig(){
    const before=enabled();
    try{const response=await fetch('/api/control/config');if(!response.ok)throw Error('config');const c=await response.json();if(c.protocol!==1||typeof c.enabled!=='boolean')throw Error('protocol');
      available=c.enabled;configured=true;if(!enabled()){cancel();stopSocket();}else connect();
      if(!available)notice('后端实时控制已禁用，当前使用旧版控制');
    }catch(_){available=false;configured=true;cancel();stopSocket();notice('实时控制配置不可用，当前使用旧版控制');}render();if(before!==enabled())window.dispatchEvent(new Event('livecontrolmodechange'));return enabled();
  }
  function setEnabled(value){if(active||pending.size)notice('正在释放实时控制；旧版若提示忙请稍后重试，已发送操作不会重播');cancel();wanted=!!value;try{localStorage.setItem('ff_live_control',wanted?'1':'0');}catch(_){}
    stopSocket();if(wanted){if(!available)refreshConfig();else connect();}render();
    window.dispatchEvent(new Event('livecontrolmodechange'));
  }
  // One pointer session, with captured identity. No promises/acks gate MOVE or UP.
  let pointer=null;
  function releasePointer(){const p=pointer;pointer=null;if(!p)return;cancelAnimationFrame(p.frame);try{if(p.element.hasPointerCapture?.(p.pointerId))p.element.releasePointerCapture(p.pointerId);}catch(_){} }
  function cancelPointer(){if(!pointer)return;cancel(pointer.id);releasePointer();}
  function watch(){const p=pointer;if(!p)return;if(!enabled()||!active||active.id!==p.id||!p.valid()){cancelPointer();return;}p.frame=requestAnimationFrame(watch);}
  function startPointer(e,{element,point,valid,targets,source_width,source_height}){
    if(!enabled())return false;
    e.preventDefault();e.stopImmediatePropagation();
    if(pointer)return true;
    const q=point(e);if(!q||!valid())return true;
    try{element.setPointerCapture(e.pointerId);}catch(_){notice('无法捕获指针，已取消实时操作');return true;}
    const id=begin({targets,source_width,source_height,...q});
    if(!id){try{element.releasePointerCapture(e.pointerId);}catch(_){}return true;}
    pointer={id,element,point,valid,pointerId:e.pointerId,frame:0};pointer.frame=requestAnimationFrame(watch);return true;
  }
  document.addEventListener('pointermove',e=>{const p=pointer;if(!p||p.pointerId!==e.pointerId)return;e.preventDefault();e.stopImmediatePropagation();
    if(e.buttons===0||!p.valid()){cancelPointer();return;}const q=p.point(e);if(q)move(p.id,q.x,q.y);
  },true);
  document.addEventListener('pointerup',e=>{const p=pointer;if(!p||p.pointerId!==e.pointerId)return;e.preventDefault();e.stopImmediatePropagation();
    const q=p.valid()?p.point(e):null;if(q)end(p.id,q.x,q.y);else cancel(p.id);releasePointer();
  },true);
  for(const name of ['pointercancel','lostpointercapture'])document.addEventListener(name,e=>{if(pointer?.pointerId===e.pointerId)cancelPointer();},true);
  window.addEventListener('blur',()=>{cancelPointer();cancel();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelPointer();cancel();}});
  window.addEventListener('pagehide',()=>{cancelPointer();cancel();stopSocket();});
  window.addEventListener('livecontrolmodechange',cancelPointer);
  const api={get enabled(){return enabled();},get ready(){return ready();},begin,move,end,cancel,key,scroll,getTargets,startPointer,cancelPointer,setEnabled,refreshConfig};
  window.__liveControl=api;
  function init(){document.getElementById('live-control-mode')?.addEventListener('click',()=>setEnabled(!enabled()));render();refreshConfig();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
