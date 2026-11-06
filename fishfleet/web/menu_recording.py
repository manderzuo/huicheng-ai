"""Record accepted UI input into the existing action script library."""
import threading,time
from ..core.recorder import get_script_manager,Action
LOCK=threading.RLock();ACTIVE={}
def start(serial):
 with LOCK:
  if serial in ACTIVE:return {'ok':False,'error':'此设备正在录制，请先停止保存'}
  script=get_script_manager().create('录制 '+serial[-6:]+' '+time.strftime('%H:%M:%S'),'用户通过大小屏操作录制；回放前请核对设备与页面')
  ACTIVE[serial]={'script':script,'last':time.monotonic()}
  return {'ok':True,'script_id':script.id,'name':script.name}
def capture(serial,kind,params):
 with LOCK:
  row=ACTIVE.get(serial)
  if not row:return
  now=time.monotonic();delay=min(30,max(0,now-row['last']));row['last']=now
  script=row['script'];script.actions.append(Action(type=kind,params=params,delay=delay));get_script_manager()._save(script)
def stop(serial):
 with LOCK:
  row=ACTIVE.pop(serial,None)
  if not row:return {'ok':False,'error':'当前设备未在录制'}
  script=row['script'];get_script_manager()._save(script)
  return {'ok':True,'script_id':script.id,'name':script.name,'steps':len(script.actions)}
def status(serial):
 with LOCK:
  row=ACTIVE.get(serial)
  return {'ok':True,'recording':bool(row),'steps':len(row['script'].actions) if row else 0}
