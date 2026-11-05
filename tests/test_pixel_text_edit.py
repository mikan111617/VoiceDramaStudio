import base64
import io
import sys
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pixel_workbench'))
import app as pixel


def png(w=64,h=64):
    buf=io.BytesIO(); Image.new('RGBA',(w,h),(255,0,0,255)).save(buf,'PNG'); return buf.getvalue()

class Immediate:
    def submit(self, fn, *args): fn(*args)

@pytest.fixture
def client(monkeypatch, tmp_path):
    for name in ('OUTPUT','ASSETS','EXPORTS'):
        d=tmp_path/name.lower();d.mkdir();monkeypatch.setattr(pixel,name,d)
    monkeypatch.setattr(pixel,'STATE_FILE',tmp_path/'state.json')
    monkeypatch.setattr(pixel,'_state',pixel.initial_state())
    monkeypatch.setattr(pixel,'_executor',Immediate())
    monkeypatch.setattr(pixel.time,'sleep',lambda n:None)
    with TestClient(pixel.app) as c: yield c


def args(**overrides):
    d={'mode':'edit','provider':'pixellab','model':'pro','size':'128','frames':'8','prompt':'王冠を大きく','demo':'true'}
    d.update(overrides);return d


def test_edit_demo_keeps_source_and_result(client):
    src=png();r=client.post('/api/jobs',data=args(),files={'image':('source.png',src,'image/png')}); assert r.status_code==200,r.text
    job=client.get('/api/jobs/'+r.json()['id']).json();assert job['status']=='completed';assert {'reference.png','image_00.png'} <= set(job['files'])
    for name in ('reference.png','image_00.png'):
        img=Image.open(io.BytesIO(client.get('/api/jobs/'+job['id']+'/files/'+name).content));assert img.size==(64,64) and img.getpixel((0,0))==(255,0,0,255)


def test_edit_validation(client):
    assert client.post('/api/jobs',data=args()).status_code==422
    assert client.post('/api/jobs',data=args(prompt=''),files={'image':('x.png',png(),'image/png')}).status_code==422
    assert client.post('/api/jobs',data=args(),files={'image':('x.png',png(16,16),'image/png')}).status_code==422
    assert client.post('/api/jobs',data=args(model='standard'),files={'image':('x.png',png(),'image/png')}).status_code==422


def test_edit_provider_request_and_poll(client,monkeypatch):
    monkeypatch.setattr(pixel,'_token','test-token'); calls=[]
    monkeypatch.setattr(pixel,'balance_data',lambda:{})
    result_png=png()
    def fake_request(method,route,payload=None,timeout=75):
        calls.append((method,route,payload))
        if method=='POST': return {'background_job_id':'job123'}
        return {'status':'completed','last_response':{'images':[{'base64':base64.b64encode(result_png).decode()}]},'usage':{'type':'usd','usd':0.12}}
    monkeypatch.setattr(pixel,'pixel_request',fake_request)
    r=client.post('/api/jobs',data=args(demo='false'),files={'image':('src.png',result_png,'image/png')});assert r.status_code==200,r.text
    job=client.get('/api/jobs/'+r.json()['id']).json();assert job['status']=='completed',job
    post=calls[0];assert post[1]=='/edit-images-v2';assert post[2]['description']=='王冠を大きく';assert post[2]['image_size']=={'width':64,'height':64};assert post[2]['edit_images'][0]['width']==64
    assert calls[1][1]=='/background-jobs/job123';assert job['usd_charged']==0.12
