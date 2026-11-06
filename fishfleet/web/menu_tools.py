"""Bounded system-settings shortcuts and real screenshot downloads."""
import asyncio,subprocess,time
from fastapi import APIRouter,HTTPException,UploadFile,File
from fastapi.responses import Response
from pydantic import BaseModel
router=APIRouter(prefix='/api/menu',tags=['menu'])
SETTINGS={'settings':'android.settings.SETTINGS','connections':'android.settings.WIRELESS_SETTINGS','wifi':'android.settings.WIFI_SETTINGS','ime':'android.settings.INPUT_METHOD_SETTINGS','language':'android.settings.LOCALE_SETTINGS','accessibility':'android.settings.ACCESSIBILITY_SETTINGS'}
class Shortcut(BaseModel):
 serial:str
 target:str
@router.post('/settings')
async def open_settings(req:Shortcut):
 if req.target not in SETTINGS:raise HTTPException(400,'不支持的设置入口')
 from .server import _adb_path,get_manager
 if not get_manager().get(req.serial):raise HTTPException(404,'设备不存在')
 r=await asyncio.to_thread(subprocess.run,[_adb_path(),'-s',req.serial,'shell','am','start','-W','-a',SETTINGS[req.target]],capture_output=True,text=True,timeout=20,encoding='utf-8',errors='replace')
 output=(r.stdout or '')+'\n'+(r.stderr or '')
 ok=r.returncode==0 and 'Error:' not in output and ('Status: ok' in output or 'Activity not started' in output)
 return {'ok':ok,'output':output[-600:],**({} if ok else {'error':'设置入口未成功启动'})}
EXPORT_DIRS={'相册照片':['/sdcard/DCIM/Camera'],'截图':['/sdcard/Pictures/Screenshots','/sdcard/DCIM/Screenshots','/sdcard/Screenshots'],'下载':['/sdcard/Download'],'视频':['/sdcard/Movies','/sdcard/DCIM/Camera'],'文档':['/sdcard/Documents']}
@router.get('/{serial}/files')
async def list_files(serial:str,kind:str):
 if kind not in EXPORT_DIRS:raise HTTPException(400,'不支持的位置')
 from .server import _adb_path,get_manager
 if not get_manager().get(serial):raise HTTPException(404,'设备不存在')
 adb=_adb_path();seen=set();rows=[];existing=[]
 for path in EXPORT_DIRS[kind]:
  r=await asyncio.to_thread(subprocess.run,[adb,'-s',serial,'shell','ls','-lt',path],capture_output=True,text=True,timeout=20,encoding='utf-8',errors='replace')
  if r.returncode:continue
  existing.append(path)
  for line in (r.stdout or '').splitlines()[1:]:
   parts=line.split()
   if len(parts)<7:continue
   name=' '.join(parts[6:])
   if name in ('.','..') or not parts[4].isdigit() or name in seen:continue
   seen.add(name);rows.append({'name':name,'bytes':int(parts[4]),'path':path+'/'+name})
 if not existing:raise HTTPException(404,'手机上没有找到该类型的目录：'+kind)
 rows.sort(key=lambda x:x['bytes'],reverse=True)
 return {'ok':True,'files':rows[:200],'more':len(rows)>200,'dirs':existing}
@router.get('/{serial}/file')
async def get_file(serial:str,kind:str,name:str,path:str=''):
 from urllib.parse import quote
 if kind not in EXPORT_DIRS:raise HTTPException(400,'不支持的位置')
 if not name or '/' in name or '\\' in name or name in ('.','..') or '\x00' in name:raise HTTPException(400,'文件名无效')
 base=path if path in EXPORT_DIRS[kind] else EXPORT_DIRS[kind][0]
 from .server import _adb_path,get_manager
 if not get_manager().get(serial):raise HTTPException(404,'设备不存在')
 r=await asyncio.to_thread(subprocess.run,[_adb_path(),'-s',serial,'exec-out','cat',base+'/'+name],capture_output=True,timeout=120)
 if r.returncode or not r.stdout:raise HTTPException(502,'手机文件读取失败，未下载空文件')
 return Response(r.stdout,media_type='application/octet-stream',headers={'Content-Disposition':f"attachment; filename*=UTF-8''{quote(name)}",'Cache-Control':'no-store'})

@router.post('/{serial}/wallpaper')
async def set_wallpaper(serial:str,file:UploadFile=File(...)):
 import io,tempfile,os,uuid
 from .server import _adb_path,get_manager
 if not get_manager().get(serial):raise HTTPException(404,'设备不存在')
 raw=await file.read(8*1024*1024+1)
 await file.close()
 if len(raw)>8*1024*1024:raise HTTPException(413,'壁纸图片不能超过8MiB')
 try:
  from PIL import Image
  image=Image.open(io.BytesIO(raw));image.verify()
  image=Image.open(io.BytesIO(raw)).convert('RGB')
 except Exception:raise HTTPException(400,'不是有效的图片文件')
 path='/sdcard/Pictures/FishFleet/';remote=path+'wallpaper_'+uuid.uuid4().hex[:8]+'.jpg'
 fd,tmp=tempfile.mkstemp(prefix='fishfleet_wallpaper_',suffix='.jpg')
 try:
  with os.fdopen(fd,'wb') as out:image.save(out,format='JPEG',quality=92)
  adb=_adb_path()
  await asyncio.to_thread(subprocess.run,[adb,'-s',serial,'shell','mkdir','-p',path],capture_output=True,text=True,timeout=20)
  pushed=await asyncio.to_thread(subprocess.run,[adb,'-s',serial,'push',tmp,remote],capture_output=True,text=True,timeout=120)
  if pushed.returncode:raise HTTPException(502,'图片推送到手机失败')
  # Android has no public CLI wallpaper setter on this ROM: open the system chooser.
  await asyncio.to_thread(subprocess.run,[adb,'-s',serial,'shell','am','start','-a','android.intent.action.ATTACH_DATA','-d','file://'+remote,'-t','image/jpeg','-e','mimeType','image/jpeg'],capture_output=True,text=True,timeout=20)
  confirmed=await asyncio.to_thread(_pick_wallpaper_option,serial)
  return {'ok':True,'remote':remote,'confirmed':confirmed,'note':None if confirmed else '已打开系统“设为壁纸”选择界面，请在手机画面点选壁纸目标'}
 finally:
  try:os.remove(tmp)
  except OSError:pass

def _pick_wallpaper_option(serial):
 time.sleep(2)
 try:
  import uiautomator2 as u
  device=u.connect(serial)
  for label in ('壁纸','主屏幕','主屏幕和锁屏','锁定屏幕','锁屏','Wallpaper','Home screen','Home and lock screen','Lock screen'):
   if device(text=label).exists:
    device(text=label).click();time.sleep(1.2);return True
  return False
 except Exception:return False
@router.get('/{serial}/screenshot')
async def screenshot(serial:str):
 from .server import _adb_path,get_manager
 if not get_manager().get(serial):raise HTTPException(404,'设备不存在')
 r=await asyncio.to_thread(subprocess.run,[_adb_path(),'-s',serial,'exec-out','screencap','-p'],capture_output=True,timeout=20)
 if r.returncode or not r.stdout.startswith(b'\x89PNG\r\n\x1a\n'):raise HTTPException(502,'手机截图失败，未导出空文件')
 import datetime,re
 name=re.sub(r'[^A-Za-z0-9_-]','_',serial)+'_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S')+'.png'
 return Response(r.stdout,media_type='image/png',headers={'Content-Disposition':f'attachment; filename="{name}"','Cache-Control':'no-store'})
