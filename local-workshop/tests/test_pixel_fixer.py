import io,json,tempfile,unittest,zipfile,hashlib
from pathlib import Path
from PIL import Image,ImageDraw
from unittest.mock import patch
from test_workshop import engine,server,project,frame
import pixel_fixer

class PixelFixerTests(unittest.TestCase):
    def test_vendored_sources_match_recorded_commit(self):
        root=Path(__file__).resolve().parents[1]/'vendor'
        for name,digest in json.loads((root/'upstream.json').read_text())['filesSha256'].items():
            self.assertEqual(hashlib.sha256((root/'pixelfixer'/name).read_bytes()).hexdigest(),digest)

    def test_actual_automatic_grid_recovery(self):
        src=Image.open(Path(__file__).resolve().parents[1]/'examples/lantern-exercise.png').convert('RGBA')
        result,report=pixel_fixer.restore(src.resize((src.width*4,src.height*4),Image.Resampling.NEAREST))
        self.assertEqual(result.size,src.size);self.assertFalse(report['forced']);self.assertEqual(report['step_x'],4)
        self.assertEqual(result.getchannel('A').tobytes(),src.getchannel('A').tobytes())
        self.assertEqual([p for p in engine.pixels(result) if p[3]],[p for p in engine.pixels(src) if p[3]])

    def test_explicit_native_step_does_not_destroy_sparse_art(self):
        src=Image.open(Path(__file__).resolve().parents[1]/'examples/lantern-exercise.png').convert('RGBA')
        result,report=pixel_fixer.restore(src,1)
        self.assertEqual(result.size,src.size);self.assertTrue(report['forced']);self.assertEqual(report['forceStep'],1)
        self.assertEqual([p for p in engine.pixels(result) if p[3]],[p for p in engine.pixels(src) if p[3]])

    def test_registration_preserves_pixels_and_motion(self):
        sheet=Image.new('RGBA',(64,32));sheet.paste(engine.flatten(frame(5),(32,32)),(0,0));sheet.paste(engine.flatten(frame(10),(32,32)),(32,0))
        frames,report=pixel_fixer.pack_native(sheet,dict(width=48,height=64),2,1,2)
        a,b=[engine.flatten(f,(48,64)) for f in frames]
        self.assertEqual(b.getbbox()[0]-a.getbbox()[0],5);self.assertEqual(report['sharedScale'],1)
        self.assertEqual([p for p in engine.pixels(a) if p[3]],[p for p in engine.pixels(sheet.crop((0,0,32,32))) if p[3]])

    def test_overflow_keeps_native_and_report(self):
        sheet=engine.flatten(frame(),(32,32))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'未二次缩放'):
                pixel_fixer.process(sheet,dict(width=16,height=16),1,1,1,'keep',folder,lambda im:(im,{'confidence':'high'}))
            self.assertEqual(len(list(Path(folder).glob('*.png'))),2)
            report=json.loads(next(Path(folder).glob('*.json')).read_text(encoding='utf-8'))
            self.assertIn('registrationError',report);self.assertFalse(report['userAccepted'])

    def test_boundary_and_empty_rejected(self):
        for im in (Image.new('RGBA',(32,32)),Image.new('RGBA',(32,32),'red')):
            with self.assertRaises(ValueError):pixel_fixer.pack_native(im,dict(width=96,height=96),1,1,1)

    def test_repaired_export_has_aligned_normals_and_report(self):
        p=project();p['settings'].update(hd2d=True,style='octopath',pixelFixer=True)
        with tempfile.TemporaryDirectory() as folder:
            f,report=pixel_fixer.process(engine.flatten(frame(),(32,32)),p['settings'],1,1,1,'keep',folder,lambda im:(im,{'confidence':'high','forced':False}))
            p['clips'][0].update(frames=f,provenance=report)
            clean=engine.validate_project(p)
            with zipfile.ZipFile(io.BytesIO(engine.export_project(clean))) as z:
                a=Image.open(io.BytesIO(z.read('walk_e/atlas.png')));n=Image.open(io.BytesIO(z.read('walk_e/atlas_normal.png')))
                self.assertEqual(a.getchannel('A').tobytes(),n.getchannel('A').tobytes())
                self.assertEqual(json.loads(z.read('walk_e/atlas.json'))['provenance']['pixelFixer']['registration']['sharedScale'],1)

    def test_octopath_prompt_and_optional_setting(self):
        self.assertFalse(engine.settings({})['pixelFixer'])
        with self.assertRaises(ValueError):engine.settings({'pixelFixer':'yes'})
        prompt=engine.generation_prompt(engine.settings({'style':'octopath','hd2d':True}),'walk','e')[0]
        self.assertIn('3-5 tone',prompt);self.assertIn('same native pixel grid',prompt)
