"""Production WSGI API; public vote core shared with the local application.
Single instance on a persistent SQLite volume. No desktop authoring endpoints.
"""
import hashlib,hmac,json,os,re,sqlite3,threading,time
from contextlib import closing
from pathlib import Path
import community as core

def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def check_hash(v):
    if not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v):raise ValueError('Invalid SHA256')

def migrate():
    if core.DB.exists():
        with closing(sqlite3.connect(core.DB)) as old:version=old.execute('PRAGMA user_version').fetchone()[0]
        if version<1:
            from community_backup import backup
            backup(core.DB,core.DB.parent/('before-host-schema-'+str(time.time_ns())+'.sqlite'))
    with core.database() as c:
        c.executescript('''
CREATE TABLE IF NOT EXISTS remote_batches(id TEXT PRIMARY KEY,descriptor TEXT NOT NULL,reviewed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS remote_records(batch TEXT,ordinal INTEGER,record TEXT NOT NULL,PRIMARY KEY(batch,ordinal));
CREATE TABLE IF NOT EXISTS remote_objects(key TEXT PRIMARY KEY,record TEXT NOT NULL,etag TEXT);
CREATE TABLE IF NOT EXISTS remote_members(batch TEXT,key TEXT,PRIMARY KEY(batch,key));
CREATE TABLE IF NOT EXISTS activation_history(n INTEGER PRIMARY KEY,previous TEXT,current TEXT,created REAL);
CREATE INDEX IF NOT EXISTS candidate_balance ON candidates(batch,served);
CREATE INDEX IF NOT EXISTS receipt_expiry ON receipts(expires);
PRAGMA user_version=1;
''')

class HostedCommunity:
    def __init__(self,storage,admin_token,origins=(),project_id=''):
        if len(admin_token)<32 or admin_token.startswith('REPLACE_'):raise ValueError('Set a private random admin token with at least 32 characters')
        self.storage=storage;self.token=admin_token;self.origins=set(origins);self.project_id=project_id;migrate()
    def stage(self,d):
        if set(d)!={'version','count','dataset','scope','rendererHash','profileHash','contentRoot','manifest'} or d['version']!=1:raise ValueError('Invalid batch descriptor')
        if type(d['count']) is not int or not 2<=d['count']<=100000:raise ValueError('Batch count must be 2..100000')
        for k in ['rendererHash','profileHash','contentRoot']:check_hash(d[k])
        if any(not isinstance(d[k],str) or len(d[k])>100 for k in ['dataset','scope']):raise ValueError('Invalid provenance')
        bid=digest(d)[:32]
        obj=d['manifest']
        if obj.get('key')!='manifests/'+d['contentRoot']+'.jsonl' or obj.get('sha256')!=d['contentRoot'] or obj.get('mime')!='application/x-ndjson' or type(obj.get('bytes')) is not int or not 1<=obj['bytes']<=500_000_000 or not re.fullmatch('[A-Za-z0-9+/]{22}==',obj.get('md5','')):raise ValueError('Invalid manifest object')
        with core.database() as c:
            c.execute('INSERT OR IGNORE INTO remote_batches VALUES(?,?,0)',(bid,canonical(d)))
            c.execute('INSERT OR IGNORE INTO batches VALUES(?,?)',(bid,canonical(d)))
            prior=c.execute('SELECT record FROM remote_objects WHERE key=?',(obj['key'],)).fetchone()
            if prior and prior[0]!=canonical(obj):raise ValueError('Immutable manifest mismatch')
            c.execute('INSERT OR IGNORE INTO remote_objects VALUES(?,?,NULL)',(obj['key'],canonical(obj)))
            c.execute('INSERT OR IGNORE INTO remote_members VALUES(?,?)',(bid,obj['key']))
        return {'batch':bid}
    def records(self,bid,records):
        if not isinstance(records,list) or not 1<=len(records)<=50:raise ValueError('Send 1..50 candidates at a time')
        with core.database() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM remote_batches WHERE id=?',(bid,)).fetchone()
            if not row:raise ValueError('Unknown upload batch')
            d=json.loads(row['descriptor'])
            for r in records:
                ordinal=r['ordinal'];recipe=r['recipe'];cid=r['id']
                if type(ordinal) is not int or not 0<=ordinal<d['count'] or not re.fullmatch('[a-f0-9]{20,64}',cid):raise ValueError('Invalid candidate identity')
                if recipe.get('id')!=cid or recipe.get('dataset')!=d['dataset'] or recipe.get('scope')!=d['scope'] or recipe.get('runtimeHash')!=d['rendererHash']:raise ValueError('Candidate provenance mismatch')
                if set(recipe.get('stats',{}))!={'Braincells','IQ','ATP'} or any(type(v) not in (int,float) or not 0<=v<=100 for v in recipe['stats'].values()):raise ValueError('Invalid numeric stats')
                if set(recipe.get('traits',{}))!={'Palette','Mental State','Connection','Anatomy','Mutation'} or any(not isinstance(v,str) or not 1<=len(v)<=120 for v in recipe['traits'].values()):raise ValueError('Invalid traits')
                raw=canonical(r);prior=c.execute('SELECT record FROM remote_records WHERE batch=? AND ordinal=?',(bid,ordinal)).fetchone()
                if prior:
                    if prior[0]!=raw:raise ValueError('Immutable candidate mismatch')
                    continue
                if row['reviewed']:raise ValueError('Reviewed batch is immutable')
                if c.execute('SELECT 1 FROM candidates WHERE batch=? AND id=?',(bid,cid)).fetchone():raise ValueError('Duplicate candidate ID')
                for kind,ext,mime in [('preview','.webm','video/webm'),('poster','.png','image/png')]:
                    obj=r[kind];check_hash(obj['sha256'])
                    if obj['key']!='media/'+obj['sha256']+ext or obj['mime']!=mime or type(obj['bytes']) is not int or not 1<=obj['bytes']<=50_000_000:raise ValueError('Invalid media descriptor')
                    if not re.fullmatch('[A-Za-z0-9+/]{22}==',obj['md5']):raise ValueError('Invalid media MD5')
                    priorobj=c.execute('SELECT record FROM remote_objects WHERE key=?',(obj['key'],)).fetchone()
                    if priorobj and priorobj[0]!=canonical(obj):raise ValueError('Immutable object mismatch')
                    c.execute('INSERT OR IGNORE INTO remote_objects VALUES(?,?,NULL)',(obj['key'],canonical(obj)))
                    c.execute('INSERT OR IGNORE INTO remote_members VALUES(?,?)',(bid,obj['key']))
                c.execute('INSERT INTO remote_records VALUES(?,?,?)',(bid,ordinal,raw))
                c.execute('INSERT INTO candidates(batch,id,recipe,preview,poster) VALUES(?,?,?,?,?)',(bid,cid,canonical(recipe),r['preview']['key'],r['poster']['key']))
        return {'received':len(records)}
    def object(self,key):
        with core.database() as c:row=c.execute('SELECT * FROM remote_objects WHERE key=?',(key,)).fetchone()
        if not row:raise ValueError('Unknown media object')
        return row,json.loads(row['record'])
    def ticket(self,key):
        row,r=self.object(key);head=self.storage.head(key)
        if row['etag'] and head and head['ETag']==row['etag'] and head['ContentLength']==r['bytes']:return {'verified':True}
        return {'verified':False,**self.storage.upload_ticket(r)}
    def verify(self,key):
        row,r=self.object(key);etag=self.storage.verify(r)
        with core.database() as c:c.execute('UPDATE remote_objects SET etag=? WHERE key=?',(etag,key))
        return {'verified':True}
    def review(self,bid):
        with core.database() as c:
            row=c.execute('SELECT * FROM remote_batches WHERE id=?',(bid,)).fetchone()
            if not row:raise ValueError('Unknown batch')
            d=json.loads(row['descriptor']);h=hashlib.sha256();count=0
            for r in c.execute('SELECT ordinal,record FROM remote_records WHERE batch=? ORDER BY ordinal',(bid,)):
                if r['ordinal']!=count:raise ValueError('Incomplete candidate sequence')
                h.update((r['record']+'\n').encode());count+=1
            missing=c.execute('SELECT COUNT(*) FROM remote_members m JOIN remote_objects o ON m.key=o.key WHERE m.batch=? AND o.etag IS NULL',(bid,)).fetchone()[0]
            ready=count==d['count'] and h.hexdigest()==d['contentRoot'] and missing==0
            # Store the immutable manifest as ordered NDJSON outside the database
            # through the upload client; DB records remain the activation source.
            if ready:c.execute('UPDATE remote_batches SET reviewed=1 WHERE id=?',(bid,))
            previews=[dict(x) for x in c.execute('SELECT id,preview,poster FROM candidates WHERE batch=? ORDER BY id LIMIT 6',(bid,))]
        for x in previews:
            for k in ['preview','poster']:x[k]=self.storage.read_url(x[k])
        return {'batch':bid,'ready':ready,'received':count,'expected':d['count'],'unverifiedObjects':missing,'previews':previews,'manifest':self.storage.read_url(d['manifest']['key']) if ready else None}
    def activate(self,bid,expected):
        # Last HEAD pass confirms availability before the short atomic swap.
        with core.database() as c:
            row=c.execute('SELECT reviewed FROM remote_batches WHERE id=?',(bid,)).fetchone()
            if not row or not row[0]:raise ValueError('Review a complete verified batch before activation')
            objects=c.execute('SELECT o.record,o.etag FROM remote_members m JOIN remote_objects o ON o.key=m.key WHERE m.batch=?',(bid,)).fetchall()
        for row in objects:
            r=json.loads(row['record']);head=self.storage.head(r['key'])
            if not head or head['ETag']!=row['etag'] or head['ContentLength']!=r['bytes']:raise ValueError('Media unavailable or changed; active batch retained')
        with core.database() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT value FROM config WHERE key="active"').fetchone();current=row[0] if row else None
            if current!=expected:raise ValueError('Active batch changed; review status before retrying')
            if current==bid:return {'active':bid,'unchanged':True}
            c.execute('INSERT INTO activation_history(previous,current,created) VALUES(?,?,?)',(current,bid,time.time()))
            c.execute('INSERT OR REPLACE INTO config VALUES("previous",?)',(current or '',))
            c.execute('INSERT OR REPLACE INTO config VALUES("active",?)',(bid,))
        return {'active':bid,'previous':current}
    def status(self):
        with core.database() as c:return {r[0]:r[1] or None for r in c.execute('SELECT key,value FROM config WHERE key IN ("active","previous")')}
    def dispatch(self,path,v):
        if path=='/api/config':return {'projectId':self.project_id}
        if path=='/api/health':
            with core.database() as c:c.execute('SELECT 1')
            return {'ok':True,'storage':'sqlite-single-instance'}
        if path=='/api/leaderboard':return core.leaderboard()
        if path=='/api/account':
            if 'nickname' in v and (not isinstance(v['nickname'],str) or not v['nickname'].strip()):raise ValueError('Choose a nickname to continue')
            return core.account(v['address'],v.get('nickname'))
        if path in ['/api/pair','/api/vote'] and not core.account(v['address'])['nickname'].strip():raise ValueError('Choose a nickname before curating')
        if path=='/api/vote':return core.vote(v)
        if path=='/api/pair':
            pair=core.serve_pair(v['address'])
            for side in ['left','right']:
                for k in ['preview','poster']:pair[side][k]=self.storage.read_url(pair[side][k])
            return pair
        if path=='/admin/stage':return self.stage(v)
        if path=='/admin/records':return self.records(v['batch'],v['records'])
        if path=='/admin/ticket':return self.ticket(v['key'])
        if path=='/admin/verify':return self.verify(v['key'])
        if path=='/admin/review':return self.review(v['batch'])
        if path=='/admin/status':return self.status()
        if path=='/admin/activate':return self.activate(v['batch'],v.get('expectedActive'))
        if path=='/admin/rollback':
            previous=self.status().get('previous')
            if not previous:raise ValueError('No previous batch')
            return self.activate(previous,v.get('expectedActive'))
        if path=='/admin/report':return core.report(v['batch'])
        raise ValueError('Unknown endpoint')
    def __call__(self,environ,start_response):
        status='200 OK';headers=[('Content-Type','application/json'),('Cache-Control','no-store'),('CDN-Cache-Control','no-store'),('Vercel-CDN-Cache-Control','no-store'),('X-Content-Type-Options','nosniff')]
        try:
            path=environ.get('PATH_INFO','');method=environ.get('REQUEST_METHOD','GET');origin=environ.get('HTTP_ORIGIN')
            if origin and origin not in self.origins:raise PermissionError('Origin not allowed')
            if origin:headers.extend([('Access-Control-Allow-Origin',origin),('Vary','Origin')])
            if path.startswith('/admin/') and not hmac.compare_digest(environ.get('HTTP_AUTHORIZATION',''),'Bearer '+self.token):raise PermissionError('Admin authorization required')
            if method=='OPTIONS':
                headers.extend([('Access-Control-Allow-Methods','GET, POST, OPTIONS'),('Access-Control-Allow-Headers','Content-Type, Authorization')]);value={}
            else:
                if method!='POST' and path not in ['/api/config','/api/health','/api/leaderboard']:raise ValueError('POST required')
                length=int(environ.get('CONTENT_LENGTH') or 0);limit=2_000_000 if path.startswith('/admin/') else 8000
                if length<0 or length>limit:raise ValueError('Request too large')
                if method=='POST' and 'application/json' not in environ.get('CONTENT_TYPE',''):raise ValueError('JSON required')
                raw=environ['wsgi.input'].read(length);v=json.loads(raw) if raw else {}
                if not isinstance(v,dict):raise ValueError('JSON object required')
                value=self.dispatch(path,v)
        except PermissionError as e:status='403 Forbidden';value={'error':str(e)}
        except (ValueError,KeyError,TypeError) as e:status='400 Bad Request';value={'error':str(e)}
        except Exception:status='503 Service Unavailable';value={'error':'Service temporarily unavailable; retry safely'}
        body=json.dumps(value,allow_nan=False).encode();headers.append(('Content-Length',str(len(body))));start_response(status,headers);return [body]

def main():
    from waitress import serve
    from community_storage import S3Storage
    if not os.environ.get('BRAINSORT_COMMUNITY_DB'):raise RuntimeError('Set BRAINSORT_COMMUNITY_DB on the persistent volume')
    mount=os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
    if not mount and os.environ.get('ALLOW_LOCAL_STORAGE')!='1':raise RuntimeError('Attach the Railway persistent volume; local testing requires explicit ALLOW_LOCAL_STORAGE=1')
    if mount and not core.DB.resolve().is_relative_to(Path(mount).resolve()):raise RuntimeError('Database must be inside the Railway volume mount')
    app=HostedCommunity(S3Storage(),os.environ['ARTIST_ADMIN_TOKEN'],os.environ['ALLOWED_ORIGINS'].split(','),os.environ.get('REOWN_PROJECT_ID',''))
    threading.Thread(target=core.expire_loop,daemon=True).start()
    serve(app,host=os.environ.get('HOST','0.0.0.0'),port=int(os.environ.get('PORT','8080')),threads=8,max_request_body_size=2_000_000,channel_timeout=45,expose_tracebacks=False)

if __name__=='__main__':main()
