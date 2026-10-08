import copy,io,json,subprocess,tempfile,unittest,zipfile
from pathlib import Path
from PIL import Image
from test_workshop import project,frame,engine,server

class HD2DTests(unittest.TestCase):
    def test_normal_alpha_direction_and_color_independence(self):
        im=engine.flatten(frame(),(32,32));normal=engine.normal_map(im)
        self.assertEqual(normal.getchannel('A').tobytes(),im.getchannel('A').tobytes())
        other=engine.flatten(frame(color=(255,0,0,255)),(32,32))
        self.assertEqual(normal.tobytes(),engine.normal_map(other).tobytes())
        self.assertLess(normal.getpixel((8,16))[0],128)
        self.assertGreater(normal.getpixel((16,16))[0],128)
        self.assertGreater(normal.getpixel((12,5))[1],128)
        self.assertNotEqual(normal.tobytes(),engine.normal_map(im,100).tobytes())

    def test_frontend_backend_normals_identical(self):
        im=engine.flatten(frame(),(32,32));im.putpixel((9,6),(10,20,30,128))
        script="import {buildNormalMap} from './static/normal-map.mjs';let input='';for await(const c of process.stdin)input+=c;const p=JSON.parse(input);process.stdout.write(JSON.stringify(Array.from(buildNormalMap(p.rgba,32,32,p.strength))));"
        for strength in (1,65,100):
            proc=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(dict(rgba=list(im.tobytes()),strength=strength)),text=True,capture_output=True,check=True,cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(bytes(json.loads(proc.stdout)),engine.normal_map(im,strength).tobytes())

    def test_export_normals_aligned_and_optional(self):
        p=project()
        with zipfile.ZipFile(io.BytesIO(engine.export_project(engine.validate_project(p)))) as z:
            self.assertNotIn('walk_e/atlas_normal.png',z.namelist())
        p['settings']['hd2d']=True
        with zipfile.ZipFile(io.BytesIO(engine.export_project(engine.validate_project(p)))) as z:
            color=Image.open(io.BytesIO(z.read('walk_e/atlas.png')));normal=Image.open(io.BytesIO(z.read('walk_e/atlas_normal.png')))
            self.assertEqual(color.size,normal.size)
            self.assertEqual(color.getchannel('A').tobytes(),normal.getchannel('A').tobytes())
            self.assertEqual(normal.crop((32,0,64,32)).tobytes(),engine.normal_map(engine.flatten(p['clips'][0]['frames'][1],(32,32))).tobytes())
            self.assertEqual(json.loads(z.read('walk_e/atlas.json'))['normalMap']['colorSpace'],'linear')

    def test_hd2d_change_invalidates_visual_review(self):
        p=engine.validate_project(project());p['clips'][0]['review']['state']='accepted';p['settings']['hd2d']=True
        p=engine.validate_project(p);self.assertEqual(p['clips'][0]['review']['state'],'candidate')
        p['clips'][0]['review']['state']='accepted';p['settings']['normalStrength']=99
        self.assertEqual(engine.validate_project(p)['clips'][0]['review']['state'],'candidate')

    def test_generation_prompt_opt_in(self):
        cfg=engine.settings({})
        self.assertNotIn('HD2D relightable',engine.generation_prompt(cfg,'walk','e')[0])
        cfg['hd2d']=True
        prompt=engine.generation_prompt(cfg,'walk','e')[0]
        self.assertIn('HD2D relightable',prompt);self.assertIn('output ONLY the albedo',prompt)

    def test_hd2d_validation(self):
        for extra in ({'hd2d':'false'},{'normalStrength':0},{'normalStrength':101}):
            with self.assertRaises(ValueError):engine.settings(extra)

    def test_delete_restore_preserves_data_blocks_stale_save(self):
        with tempfile.TemporaryDirectory() as root:
            store=server.Store(root);p=store.save(project());source=store.folder(p['id'])/'original.png';source.write_bytes(b'original')
            with self.assertRaises(server.Conflict):store.set_deleted(p['id'],0,True)
            deleted=store.set_deleted(p['id'],p['version'],True)
            self.assertEqual(store.listing(),[]);self.assertEqual(len(store.listing(True)),1)
            with self.assertRaises(FileNotFoundError):store.read(p['id'])
            with self.assertRaises(FileNotFoundError):store.save(p,p['id'],p['version'])
            store.set_deleted(p['id'],deleted['version'],False)
            self.assertEqual(store.read(p['id'])['clips'],json.loads(json.dumps(p['clips'])));self.assertEqual(source.read_bytes(),b'original')
            self.assertEqual(len(store.listing()),1);self.assertEqual(store.listing(True),[])
