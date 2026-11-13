import base64, json, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hsr.protocol.gen import StarRail_pb2 as pb
base='http://127.0.0.1:21000'
def get(path, method='GET'):
    req=urllib.request.Request(base+path, method=method)
    with urllib.request.urlopen(req, timeout=5) as r: return r.status, r.read()
def post(path, body=b'{}'):
    req=urllib.request.Request(base+path, data=body, method='POST', headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r: return r.status, r.read()
s,d=get('/query_dispatch'); m=pb.Dispatch(); m.ParseFromString(base64.b64decode(d)); print('dispatch', s, len(d), m.msg, m.region_list[0].dispatch_url)
ver = sys.argv[1] if len(sys.argv) > 1 else 'OSBETAWin4.7.51'
s,g=get('/query_gateway?version=' + ver); m=pb.GateServer(); m.ParseFromString(base64.b64decode(g)); print('gateway', s, len(g), m.ip, m.port, bool(m.asset_bundle_url), m.mdk_res_version, m.unk1, m.unk8)
for p in ['/hkrpg_cn/mdk/shield/api/login','/hkrpg_cn/combo/granter/api/getConfig','/hkrpg_cn/combo/granter/api/compareProtocolVersion','/hkrpg_cn/combo/granter/login/v2/login','/account/risky/api/check','/account/ma-cn-passport/app/loginByPassword','/account/ma-cn-session/app/verify','/account/ma-cn-session/app/getTokenByGameToken','/account/ma-cn-session/app/exchange','/hkrpg_cn/combo/red_dot/list']:
    try:
        s,b=(get(p) if 'getConfig' in p else post(p))
        print(p, s, len(b), b[:50])
    except Exception as e:
        print(p, 'ERR', e)
