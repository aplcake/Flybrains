"""Local community curation aggregates. No wallet authentication or permanent votes.
CLI: python community.py serve [--port 7831], import MANIFEST, activate BATCH,
     report BATCH, allowlist --minimum 10 (private local export).
"""
import os,sqlite3,json,time,secrets,re,hashlib,argparse,http.server,threading,urllib.parse
from pathlib import Path
from contextlib import contextmanager
ROOT=Path(__file__).resolve().parent;DB=Path(os.environ.get('BRAINSORT_COMMUNITY_DB',str(ROOT/'data/community.sqlite')));RETRY_SECONDS=900
@contextmanager
def database():
 DB.parent.mkdir(exist_ok=True,parents=True);c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row
 c.executescript('''PRAGMA synchronous=FULL;PRAGMA secure_delete=ON;
 CREATE TABLE IF NOT EXISTS accounts(address TEXT PRIMARY KEY,nickname TEXT DEFAULT '',points INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,manifest TEXT);
 CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT);
 CREATE TABLE IF NOT EXISTS candidates(batch TEXT,id TEXT,recipe TEXT,preview TEXT,poster TEXT,served INTEGER DEFAULT 0,exposures INTEGER DEFAULT 0,wins INTEGER DEFAULT 0,losses INTEGER DEFAULT 0,PRIMARY KEY(batch,id));
 CREATE TABLE IF NOT EXISTS contrasts(batch TEXT,category TEXT,value TEXT,exposures INTEGER DEFAULT 0,wins INTEGER DEFAULT 0,PRIMARY KEY(batch,category,value));
 CREATE TABLE IF NOT EXISTS pairs(batch TEXT,a TEXT,b TEXT,a_wins INTEGER DEFAULT 0,b_wins INTEGER DEFAULT 0,PRIMARY KEY(batch,a,b));
 CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,address TEXT,batch TEXT,left_id TEXT,right_id TEXT,expires REAL,used INTEGER DEFAULT 0,result TEXT);
 ''')
 try:
  with c:
   c.execute('DELETE FROM receipts WHERE expires<?',(time.time(),));c.commit();yield c
 finally:c.close()
def address(value):
 if not isinstance(value,str) or not re.fullmatch('0x[0-9a-fA-F]{40}',value):raise ValueError('Invalid address')
 return value.lower()
def account(value,nickname=None):
 a=address(value)
 with database() as c:
  c.execute('INSERT OR IGNORE INTO accounts(address) VALUES(?)',(a,))
  if nickname is not None:
   if not isinstance(nickname,str) or len(nickname)>32:raise ValueError('Nickname must be at most 32 characters')
   c.execute('UPDATE accounts SET nickname=? WHERE address=?',(nickname.strip(),a))
  r=c.execute('SELECT * FROM accounts WHERE address=?',(a,)).fetchone();return {'display':r['nickname'] or a[:5]+'…'+a[-4:],'nickname':r['nickname'],'points':r['points']}
def import_batch(manifest):
 m=json.loads(Path(manifest).read_text(encoding='utf-8'));raw=json.dumps(m,sort_keys=True,separators=(',',':'));bid=hashlib.sha256(raw.encode()).hexdigest()[:20]
 with database() as c:
  c.execute('INSERT OR IGNORE INTO batches VALUES(?,?)',(bid,raw))
  for x in m['candidates']:
   for k in ['preview','poster']:
    p=ROOT/x[k]
    if not p.is_file() or not p.resolve().is_relative_to((ROOT/'exports').resolve()):raise ValueError('Missing or unsafe media path')
    if hashlib.sha256(p.read_bytes()).hexdigest()!=x[k+'SHA256']:raise ValueError('Media checksum mismatch')
   if x['recipe'].get('dataset')!=m['dataset'] or x['recipe'].get('scope')!=m['scope']:raise ValueError('Mixed dataset/scope batch')
   c.execute('INSERT OR IGNORE INTO candidates(batch,id,recipe,preview,poster) VALUES(?,?,?,?,?)',(bid,x['id'],json.dumps(x['recipe']),'/'+x['preview'].replace('\\','/'),'/'+x['poster'].replace('\\','/')))
 return bid
def activate(bid):
 with database() as c:
  if c.execute('SELECT COUNT(*) FROM candidates WHERE batch=?',(bid,)).fetchone()[0]<2:raise ValueError('Batch needs at least two verified previews')
  c.execute('INSERT OR REPLACE INTO config VALUES("active",?)',(bid,))
def serve_pair(value):
 a=address(value)
 with database() as c:
  c.execute('BEGIN IMMEDIATE')
  row=c.execute('SELECT value FROM config WHERE key="active"').fetchone()
  if not row:raise ValueError('No active local batch')
  bid=row[0];pool=c.execute('SELECT * FROM candidates WHERE batch=? ORDER BY served,random() LIMIT 32',(bid,)).fetchall()
  if len(pool)<2:raise ValueError('Batch unavailable')
  left=pool[0];r=json.loads(left['recipe'])
  # Occasionally favor a controlled single-category contrast when available.
  controlled=[x for x in pool[1:] if sum(json.loads(x['recipe'])['traits'][k]!=r['traits'][k] for k in r['traits'])==1 and json.loads(x['recipe'])['stats']==r['stats']]
  right=secrets.choice(controlled) if controlled and secrets.randbelow(4)==0 else pool[1]
  if secrets.randbelow(2):left,right=right,left
  receipt=secrets.token_urlsafe(24);c.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,0,NULL)',(receipt,a,bid,left['id'],right['id'],time.time()+RETRY_SECONDS))
  for x in [left,right]:c.execute('UPDATE candidates SET served=served+1 WHERE batch=? AND id=?',(bid,x['id']))
  return {'receipt':receipt,'batch':bid,'left':{k:left[k] for k in ['id','preview','poster']},'right':{k:right[k] for k in ['id','preview','poster']}}
def bins(r):return {**r['traits'],**{k+':numeric-bins-v1':str(min(9,int(v)//10)*10)+'–'+str(min(100,(min(9,int(v)//10)+1)*10)) for k,v in r['stats'].items()}}
def vote(value):
 a=address(value['address']);choice=value['choice']
 if choice not in ['left','right']:raise ValueError('Choose left or right')
 with database() as c:
  c.execute('BEGIN IMMEDIATE');receipt=c.execute('SELECT * FROM receipts WHERE id=?',(value['receipt'],)).fetchone()
  if not receipt or receipt['address']!=a or receipt['expires']<time.time():raise ValueError('Pair expired or belongs to a different account')
  if receipt['used']:return json.loads(receipt['result'])
  bid=receipt['batch'];left,right=receipt['left_id'],receipt['right_id'];winner=left if choice=='left' else right
  c.execute('INSERT OR IGNORE INTO accounts(address) VALUES(?)',(a,));c.execute('UPDATE accounts SET points=points+1 WHERE address=?',(a,))
  recipes={}
  for id in [left,right]:
   c.execute('UPDATE candidates SET exposures=exposures+1,wins=wins+?,losses=losses+? WHERE batch=? AND id=?',(int(id==winner),int(id!=winner),bid,id));recipes[id]=json.loads(c.execute('SELECT recipe FROM candidates WHERE batch=? AND id=?',(bid,id)).fetchone()[0])
  lb,rb=bins(recipes[left]),bins(recipes[right])
  for cat in lb:
   if lb[cat]==rb[cat]:continue
   for id,v in [(left,lb[cat]),(right,rb[cat])]:c.execute('INSERT INTO contrasts VALUES(?,?,?,1,?) ON CONFLICT(batch,category,value) DO UPDATE SET exposures=exposures+1,wins=wins+excluded.wins',(bid,cat,str(v),int(id==winner)))
  lo,hi=sorted([left,right]);c.execute('INSERT INTO pairs VALUES(?,?,?,?,?) ON CONFLICT(batch,a,b) DO UPDATE SET a_wins=a_wins+excluded.a_wins,b_wins=b_wins+excluded.b_wins',(bid,lo,hi,int(winner==lo),int(winner==hi)))
  result={'accepted':True,'points':c.execute('SELECT points FROM accounts WHERE address=?',(a,)).fetchone()[0]};c.execute('UPDATE receipts SET used=1,result=? WHERE id=?',(json.dumps(result),receipt['id']));return result
def leaderboard():
 with database() as c:return [{'display':r['nickname'] or r['address'][:5]+'…'+r['address'][-4:],'points':r['points']} for r in c.execute('SELECT * FROM accounts ORDER BY points DESC,address LIMIT 100')]
def report(bid):
 import math
 def interval(w,n):
  if not n:return [0,1]
  p=w/n;d=1+3.8416/n;middle=(p+1.9208/n)/d;spread=1.96*math.sqrt(p*(1-p)/n+.9604/n**2)/d;return [middle-spread,middle+spread]
 with database() as c:
  rows=[dict(r) for r in c.execute('SELECT * FROM contrasts WHERE batch=?',(bid,))]
  for r in rows:r['rate']=r['wins']/r['exposures'];r['wilson95']=interval(r['wins'],r['exposures'])
  return {'batch':bid,'contrasts':rows,'candidates':[dict(r) for r in c.execute('SELECT id,exposures,wins,losses FROM candidates WHERE batch=?',(bid,))],'notice':'Associations within displayed combinations, not independent trait causality. Identical traits excluded. Exploration weights and final quotas are separate artist decisions.'}
class Handler(http.server.SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT),**kw)
 def log_message(self,*args):pass # Never log address-bearing payloads.
 def reply(self,value,status=200):
  b=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  if self.path=='/api/leaderboard':return self.reply(leaderboard())
  if self.path=='/':self.path='/web/community.html'
  decoded=urllib.parse.unquote(urllib.parse.urlsplit(self.path).path);target=(ROOT/decoded.lstrip('/')).resolve()
  allowed_web=target.parent==ROOT/'web' and target.name.startswith('community') and target.suffix in ['.html','.js']
  allowed_media=target.is_relative_to(ROOT/'exports') and len(target.relative_to(ROOT/'exports').parts)==2 and target.relative_to(ROOT/'exports').parts[0].startswith('community-') and target.suffix in ['.png','.webm']
  if not (allowed_web or allowed_media):return self.send_error(404)
  return super().do_GET()
 def do_POST(self):
  if self.headers.get('Origin') not in [None,f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}']:return self.send_error(403)
  try:
   n=int(self.headers.get('Content-Length',0))
   if n>8000:raise ValueError('Request too large')
   v=json.loads(self.rfile.read(n));actions={'/api/account':lambda:account(v['address'],v.get('nickname')),'/api/pair':lambda:serve_pair(v['address']),'/api/vote':lambda:vote(v)}
   if self.path not in actions:return self.send_error(404)
   self.reply(actions[self.path]())
  except Exception as e:self.reply({'error':str(e)},400)
def expire_loop():
 while True:
  time.sleep(60)
  with database():pass
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['serve','import','activate','report','allowlist']);p.add_argument('value',nargs='?');p.add_argument('--port',type=int,default=7831);p.add_argument('--minimum',type=int,default=10);a=p.parse_args()
 if a.action=='import':print(import_batch(a.value))
 elif a.action=='activate':activate(a.value)
 elif a.action=='report':print(json.dumps(report(a.value),indent=2))
 elif a.action=='allowlist':
  with database() as c:rows=[r[0] for r in c.execute('SELECT address FROM accounts WHERE points>=? ORDER BY address',(a.minimum,))]
  p=ROOT/'data/private-allowlist.json';p.write_text(json.dumps({'draftRule':a.minimum,'selfAssertedAddresses':rows},indent=2));print(p)
 else:threading.Thread(target=expire_loop,daemon=True).start();print(f'Local preview only: http://127.0.0.1:{a.port}',flush=True);http.server.ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
