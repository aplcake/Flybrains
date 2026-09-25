"""Offline/online SQLite snapshot with temporary vote receipts removed."""
import argparse,os,sqlite3
from contextlib import closing
from pathlib import Path
def backup(source,target):
    target=Path(target)
    if target.exists():raise ValueError('Refusing to overwrite backup')
    target.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(source)) as s,closing(sqlite3.connect(target)) as d:
        s.backup(d);d.execute('PRAGMA secure_delete=ON')
        if d.execute("SELECT 1 FROM sqlite_master WHERE name='receipts'").fetchone():d.execute('DELETE FROM receipts')
        d.commit();d.execute('VACUUM')
        if d.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('target');p.add_argument('--source',default=os.environ.get('BRAINSORT_COMMUNITY_DB'));a=p.parse_args();backup(a.source,a.target);print('Verified snapshot without temporary receipts:',a.target)
