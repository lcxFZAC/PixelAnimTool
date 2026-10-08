import base64
import copy
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
import server

CFG = dict(width=32, height=32, ppu=48, style="jiuzhen", colors=16, outline="#0b0e14")


def frame(x=8, color=(130, 160, 120, 255)):
    image = Image.new("RGBA", (32, 32))
    ImageDraw.Draw(image).rectangle((x, 5, x + 8, 27), fill=color)
    return dict(layers=[dict(name="底稿", visible=True, data=engine.encode_image(image))])


def project():
    return dict(name="回归样本", settings=CFG.copy(), reference=frame()["layers"][0]["data"], masters={}, clips=[dict(id="walk_e", action="walk", direction="e", fps=10, loop=True, frames=[frame(), frame(9)], review={"state":"candidate"})])


def generated_sheet():
    sheet = Image.new("RGBA", (128, 64), (255, 0, 255, 255))
    for i in range(8):
        x, y = (i % 4) * 32, (i // 4) * 32
        draw = ImageDraw.Draw(sheet)
        draw.rectangle((x+7+i%3,y+5,x+20,y+27),fill=(100,150,100,255))
        draw.point((x+9+i,y+10),fill=(230,220,130,255))
    return sheet


class ProcessingTests(unittest.TestCase):
    def test_native_import_is_pixel_exact(self):
        src=engine.decode_image(frame()["layers"][0]["data"])
        result, _ = engine.pixel_process(src, CFG, fit=False)
        self.assertEqual(src.tobytes(), engine.flatten(result[0], (32,32)).tobytes())

    def test_shared_transform_preserves_relative_motion(self):
        sheet=Image.new("RGBA",(64,32))
        sheet.paste(engine.decode_image(frame(4)["layers"][0]["data"]),(0,0))
        sheet.paste(engine.decode_image(frame(12)["layers"][0]["data"]),(32,0))
        frames, provenance=engine.pixel_process(sheet, CFG,2,1,2,fit=True)
        a,b=[engine.flatten(f,(32,32)).getbbox() for f in frames]
        self.assertGreater(b[0],a[0])
        self.assertEqual(a[3],b[3])
        self.assertIn("sharedScale",provenance["transform"])

    def test_magenta_flood_does_not_delete_enclosed_detail(self):
        im=Image.new("RGBA",(8,8),(255,0,255,255))
        ImageDraw.Draw(im).rectangle((1,1,6,6),fill=(20,30,40,255))
        im.putpixel((3,3),(255,0,255,255))
        out=engine.remove_background(im,"magenta")
        self.assertEqual(out.getpixel((0,0))[3],0)
        self.assertEqual(out.getpixel((3,3)),(255,0,255,255))

    def test_fractional_grid_rejected(self):
        with self.assertRaisesRegex(ValueError,"整除"):
            engine.pixel_process(Image.new("RGBA",(101,100)),CFG,4,2,8)

    def test_fixed_outline_does_not_change_alpha(self):
        sheet=generated_sheet()
        a,_=engine.pixel_process(sheet,CFG,4,2,8,"magenta",True,False)
        b,_=engine.pixel_process(sheet,CFG,4,2,8,"magenta",True,True)
        for fa,fb in zip(a,b):
            ia,ib=engine.flatten(fa,(32,32)),engine.flatten(fb,(32,32))
            self.assertEqual(ia.getchannel("A").tobytes(),ib.getchannel("A").tobytes())
            bbox=ib.getbbox()
            self.assertEqual(ib.getpixel((bbox[0],bbox[1]))[:3],(11,14,20))

    def test_empty_and_duplicate_animation_blocked(self):
        p=project();p["clips"][0]["frames"]=[frame(),frame()]
        clean=engine.validate_project(p)
        self.assertFalse(clean["clips"][0]["report"]["technicalPass"])
        self.assertTrue(any("完全相同" in x for x in clean["clips"][0]["report"]["blockers"]))

    def test_hash_review_resets_on_pixel_edit_but_not_fps(self):
        p=engine.validate_project(project());c=p["clips"][0];c["review"]["state"]="accepted"
        c["fps"]=12
        self.assertEqual(engine.validate_project(p)["clips"][0]["review"]["state"],"accepted")
        c["frames"][0]=frame(12)
        self.assertEqual(engine.validate_project(p)["clips"][0]["review"]["state"],"candidate")

    def test_export_coordinates_and_apng_exact(self):
        p=engine.validate_project(project())
        with zipfile.ZipFile(io.BytesIO(engine.export_project(p))) as z:
            meta=json.loads(z.read("walk_e/atlas.json"))
            self.assertEqual(meta["frames"][1]["x"],32)
            self.assertEqual(meta["ppu"],48)
            anim=Image.open(io.BytesIO(z.read("walk_e/preview.apng")))
            for i,f in enumerate(p["clips"][0]["frames"]):
                anim.seek(i)
                self.assertEqual(anim.convert("RGBA").tobytes(),engine.flatten(f,(32,32)).tobytes())
            self.assertIn("Unity/Editor/PixelWorkshopImporter.cs",z.namelist())

    def test_layer_visibility_compositing(self):
        f=frame();hidden=frame(8,(255,0,0,255))["layers"][0];hidden["visible"]=False;f["layers"].append(hidden)
        self.assertEqual(engine.flatten(f,(32,32)).getpixel((9,10)),(130,160,120,255))

    def test_size_limits_and_metadata_strip(self):
        p=project();p["apiKey"]="secret";p["settings"]["width"]=20000
        with self.assertRaises(ValueError):engine.validate_project(p)
        p["settings"]=CFG.copy();p["settings"]["width"]=32
        self.assertNotIn("apiKey",engine.validate_project(p))


class StoreAndJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="pixel-workshop-tests-")
        self.store=server.Store(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def test_path_traversal_rejected(self):
        for bad in ("../secret","C:/tmp","x"*32):
            with self.assertRaises(ValueError):self.store.folder(bad)

    def test_revision_conflict_prevents_lost_updates(self):
        p=self.store.save(project());self.store.save(p,p["id"],p["version"])
        with self.assertRaises(server.Conflict):self.store.save(p,p["id"],p["version"])

    def test_no_generation_when_disabled(self):
        jobs=server.Jobs(self.store,provider=lambda *a:self.fail("provider called"))
        with self.assertRaisesRegex(ValueError,"关闭"):jobs.start({"enabled":False,"key":"fake"})

    def test_generation_retains_sources_never_persists_key(self):
        p=project();p["clips"]=[];p=self.store.save(p)
        jobs=server.Jobs(self.store,provider=lambda *a:(generated_sheet(),"mock response"))
        j=jobs.start(dict(id=p["id"],enabled=True,key="FAKE_TEST_SECRET",pack="single",action="walk",direction="e"))
        deadline=time.monotonic()+5
        while jobs.busy() and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(jobs.snapshot(j["id"])["state"],"completed")
        saved=self.store.read(p["id"])
        self.assertEqual(saved["clips"][0]["review"]["state"],"candidate")
        self.assertEqual(len(list((self.store.folder(p["id"])/"sources").glob("*.png"))),1)
        for file in Path(self.temp.name).rglob("*.json"):
            self.assertNotIn("FAKE_TEST_SECRET",file.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError,"均已存在"):
            jobs.start(dict(id=p["id"],enabled=True,key="fake",pack="single",action="walk",direction="e"))

    def test_cancel_retains_inflight_result_and_stops_followups(self):
        p=project();p["clips"]=[];p=self.store.save(p)
        started,release=threading.Event(),threading.Event();calls=[]
        def provider(*args):
            calls.append(1);started.set();release.wait(3)
            # First movement task is idle: 4 frames, 2x2.
            return generated_sheet().crop((0,0,64,64)),""
        jobs=server.Jobs(self.store,provider);j=jobs.start(dict(id=p["id"],enabled=True,key="fake",pack="movement"))
        self.assertTrue(started.wait(2));jobs.cancel(j["id"]);release.set()
        deadline=time.monotonic()+5
        while jobs.busy() and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(len(calls),1);self.assertEqual(jobs.snapshot(j["id"])["state"],"cancelled")
        self.assertEqual(len(self.store.read(p["id"])["clips"]),1)

    def test_provider_handles_text_only_and_thought_image(self):
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        response={"candidates":[{"content":{"parts":[{"text":"no image"}]}}]}
        with patch.object(server.urllib.request,"urlopen",return_value=Response(json.dumps(response).encode())):
            with self.assertRaisesRegex(ValueError,"0 张图"):server.call_gemini("fake","model","test",[])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="pixel-workshop-http-")
        self.store=server.Store(self.temp.name);self.jobs=server.Jobs(self.store)
        self.http=server.ThreadingHTTPServer(("127.0.0.1",0),server.make_handler(self.store,self.jobs))
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.url=f"http://127.0.0.1:{self.http.server_port}"
        self.csrf=self.get("/api/info")["csrf"]

    def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.temp.cleanup()

    def get(self,path):
        with urllib.request.urlopen(self.url+path) as r:return json.load(r)

    def post(self,path,body,headers=None):
        req=urllib.request.Request(self.url+path,json.dumps(body).encode(),headers={"Content-Type":"application/json","X-Workshop":self.csrf}|(headers or {}))
        with urllib.request.urlopen(req) as r:return json.load(r)

    def test_delete_restore_http_guards(self):
        p=self.post('/api/save',{'project':project()});body=dict(id=p['id'],version=p['version'])
        with patch.object(self.jobs,'busy',return_value=True):
            with self.assertRaises(urllib.error.HTTPError) as c:self.post('/api/delete-project',body)
            self.assertEqual(c.exception.code,409)
        with self.assertRaises(urllib.error.HTTPError) as c:self.post('/api/delete-project',body,{'X-Workshop':''})
        self.assertEqual(c.exception.code,403)
        deleted=self.post('/api/delete-project',body)
        self.assertEqual(self.get('/api/projects'),[]);self.assertEqual(len(self.get('/api/trash')),1)
        with self.assertRaises(urllib.error.HTTPError) as c:self.get('/api/projects/'+p['id'])
        self.assertEqual(c.exception.code,404)
        self.post('/api/restore-project',dict(id=p['id'],version=deleted['version']))
        self.assertEqual(self.get('/api/projects/'+p['id'])['name'],p['name'])

    def test_save_review_export_and_reopen(self):
        p=self.post("/api/save",{"project":project()})
        p=self.post("/api/review",dict(id=p["id"],version=p["version"],clipId="walk_e",state="accepted"))
        self.assertEqual(self.get('/api/projects/'+p['id'])["clips"][0]["review"]["state"],"accepted")

    def test_foreign_origin_and_missing_csrf_rejected(self):
        for headers in ({"Origin":"https://evil.example"},{"X-Workshop":""},{"Host":"evil.example"}):
            with self.assertRaises(urllib.error.HTTPError) as c:self.post("/api/save",{"project":project()},headers)
            self.assertEqual(c.exception.code,403)

    def test_acceptance_blocked_on_empty_frame(self):
        p=project();p["clips"][0]["frames"]=[dict(layers=[dict(name="empty",visible=True,data=engine.encode_image(Image.new("RGBA",(32,32))))])]
        p=self.post("/api/save",{"project":p})
        with self.assertRaises(urllib.error.HTTPError) as c:self.post("/api/review",dict(id=p["id"],version=p["version"],clipId="walk_e",state="accepted"))
        self.assertEqual(c.exception.code,400)

    def test_cloud_requires_access_token(self):
        cloud=server.ThreadingHTTPServer(("127.0.0.1",0),server.make_handler(self.store,self.jobs,True,"personal-test-token-16"))
        t=threading.Thread(target=cloud.serve_forever,daemon=True);t.start()
        try:
            url=f"http://127.0.0.1:{cloud.server_port}/api/projects"
            with self.assertRaises(urllib.error.HTTPError) as c:urllib.request.urlopen(url)
            self.assertEqual(c.exception.code,403)
            req=urllib.request.Request(url,headers={"Authorization":"Bearer personal-test-token-16"})
            with urllib.request.urlopen(req) as response:self.assertEqual(json.load(response),[])
        finally:cloud.shutdown();cloud.server_close();t.join()


if __name__ == "__main__":unittest.main(verbosity=2)
