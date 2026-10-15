"""Offline upstream adapter. Restore the whole sheet once; never rescale to fit."""
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from PIL import Image
import engine

ROOT=Path(__file__).resolve().parent
GATE=threading.BoundedSemaphore(1)


def restore(source, force_step=None):
    if min(source.size)<16 or source.width*source.height>4_000_000:
        raise ValueError('Pixel Art Fixer 要求边长至少 16，整图不超过 400 万像素')
    if not GATE.acquire(blocking=False):
        raise ValueError('Pixel Art Fixer 正在处理另一张图，请完成后再试')
    try:
        with tempfile.TemporaryDirectory(prefix='pixel-fixer-') as folder:
            source_path=Path(folder)/'input.png';target=Path(folder)/'native.png'
            source_path.write_bytes(engine.png_bytes(source))
            try:
                result=subprocess.run([sys.executable,str(ROOT/'pixel_fixer_worker.py'),str(source_path),str(target),str(force_step or 0)],capture_output=True,timeout=180)
            except subprocess.TimeoutExpired as exc:
                raise ValueError('像素网格检测超过 180 秒；请拆分较小动作图集，原稿已保留') from exc
            if result.returncode:
                raise ValueError('Pixel Art Fixer 处理失败；请重新运行启动器安装依赖，或检查输入网格。原稿已保留')
            image=Image.open(target).convert('RGBA')
            report=json.loads(target.with_suffix('.json').read_text(encoding='utf-8'))
            return image,report
    finally:
        GATE.release()


def pack_native(native,cfg,columns,rows,count):
    columns=engine.integer(columns,1,16,'列数');rows=engine.integer(rows,1,16,'行数')
    count=engine.integer(count,1,min(32,columns*rows),'帧数')
    # Round shared cuts once. A cell can differ by one empty column; never stretch it.
    xs=[round(i*native.width/columns) for i in range(columns+1)]
    ys=[round(i*native.height/rows) for i in range(rows+1)]
    frames=[];boxes=[]
    for i in range(count):
        x,y=i%columns,i//columns
        tile=native.crop((xs[x],ys[y],xs[x+1],ys[y+1]));box=tile.getbbox()
        if not box:raise ValueError(f'恢复后第 {i+1} 帧为空，请核对帧数和网格')
        if box[0]==0 or box[1]==0 or box[2]==tile.width or box[3]==tile.height:
            raise ValueError(f'恢复后第 {i+1} 帧触及分格边界；请检查原生恢复图并调整留白，不自动裁切')
        frames.append(tile);boxes.append(box)
    union=(min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes))
    w,h=cfg['width'],cfg['height'];uw,uh=union[2]-union[0],union[3]-union[1]
    if uw>w-4 or uh>h-4:
        raise ValueError(f'恢复后动作范围 {uw}×{uh} 放不进 {w}×{h}（含边距）；请选择更大画布。未二次缩放')
    dx=(w-uw)//2-union[0];dy=h-2-union[3]
    packed=[]
    for tile in frames:
        out=Image.new('RGBA',(w,h));out.paste(tile,(dx,dy))
        before=[p for p in engine.pixels(tile) if p[3]];after=[p for p in engine.pixels(out) if p[3]]
        if before!=after:raise ValueError('注册时可见像素发生损失，已停止')
        packed.append(dict(layers=[dict(name='Pixel Art Fixer 候选',visible=True,data=engine.encode_image(out))]))
    return packed,dict(sharedScale=1,sharedOffset=[dx,dy],resampling='none',columnCuts=xs,rowCuts=ys,visiblePixelsPreserved=True)


def process(source,cfg,columns,rows,count,background,folder,restorer=None,force_step=None):
    cfg=engine.settings(cfg);folder=Path(folder);folder.mkdir(exist_ok=True)
    if force_step is not None:engine.integer(force_step,1,64,'手动像素步长')
    engine.integer(columns,1,16,'列数');engine.integer(rows,1,16,'行数');engine.integer(count,1,min(32,columns*rows),'帧数')
    sid=uuid.uuid4().hex;nid=uuid.uuid4().hex
    (folder/f'{sid}.png').write_bytes(engine.png_bytes(source))
    prepared=engine.remove_background(source,background)
    native,report=(restorer or restore)(prepared,force_step=force_step) if force_step is not None else (restorer or restore)(prepared)
    (folder/f'{nid}.png').write_bytes(engine.png_bytes(native))
    report.update(sourceId=sid,nativeId=nid,sourceSize=list(source.size),nativeSize=list(native.size),sourceSha256=hashlib.sha256(engine.png_bytes(source)).hexdigest(),nativeSha256=hashlib.sha256(engine.png_bytes(native)).hexdigest(),userAccepted=False)
    engine_report=folder/f'{nid}.json'
    engine_report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        frames,transform=pack_native(native,cfg,columns,rows,count)
    except ValueError as exc:
        report['registrationError']=str(exc);engine_report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        raise ValueError(f'{exc}。源稿 {sid}、原生恢复图 {nid} 已保存在 AI / 修复原稿，可下载复核') from exc
    report['registration']=transform;engine_report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return frames,dict(sourceId=sid,sourceHash=report['sourceSha256'],pixelFixer=report,transform=transform,notes=[f"Pixel Art Fixer {'手动校准' if report.get('forced') else '自动检测'}网格：{native.width}×{native.height}，置信度 {report.get('confidence','unknown')}；未二次缩放，需视觉验收"])
