import json
p=r'E:\SiFu\HSRSiFu\freesr-data.json'
d=json.load(open(p,encoding='utf-8'))
print('keys', list(d.keys()))
print('avatars', len(d.get('avatars',{})), 'relics', len(d.get('relics',[])), 'lightcones', len(d.get('lightcones',[])))
k=next(iter(d['avatars'])); print('avatar key', k); print(json.dumps(d['avatars'][k], ensure_ascii=False)[:600])
print('relic sample', json.dumps(d['relics'][0], ensure_ascii=False)[:400] if d.get('relics') else None)
print('lc sample', json.dumps(d['lightcones'][0], ensure_ascii=False)[:300] if d.get('lightcones') else None)
print('battle', json.dumps(d['battle_config'], ensure_ascii=False)[:400])
