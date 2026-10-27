"""Read-only inventory comparison for a frozen full delivery copy.

No app imports, no DB migrations and no file deletion. Output contains filenames,
file sizes, hashes and DB integrity only; file contents/credentials are not dumped.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys


def inventory(root):
    files={};dirs=set();links=[]
    for current,children,names in os.walk(root,followlinks=False):
        base=Path(current)
        for name in list(children):
            path=base/name
            if path.is_symlink() or path.is_junction():
                links.append(path.relative_to(root).as_posix());children.remove(name)
            else:dirs.add(path.relative_to(root).as_posix())
        for name in names:
            path=base/name
            files[path.relative_to(root).as_posix()]=path.stat().st_size
    return files,dirs,links


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def hash_scope(relative):
    parts=Path(relative).parts
    if any(p in {'.git','.venv','venv','__pycache__','.idea','.codex','.claude'} for p in parts):return None
    if len(parts)>3 and parts[0]=='enterprises' and parts[2]=='data':
        tail=parts[3:]
        if tail[0] in {'reports','preset_images','project_archives'} or len(tail)==1 and tail[0] in {'carbon_account.db','project_presets_shared.json','project_presets_global.json','site_projects_xinzuan.json'}:
            return 'enterprise_runtime_critical'
        return None
    if parts[0]=='enterprises' and len(parts)>2:return 'enterprise_adaptation'
    return 'common_working_tree'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();source=args.source.resolve();destination=args.destination.resolve()
    if not source.is_dir() or not destination.is_dir() or source==destination:raise ValueError('Separate existing source and destination directories are required')
    original,source_dirs,links=inventory(source);copied,destination_dirs,dest_links=inventory(destination)
    differences={'missing_files':sorted(original.keys()-copied.keys()),'extra_files':sorted(copied.keys()-original.keys()),
                 'size_mismatches':[name for name in original.keys()&copied.keys() if original[name]!=copied[name]],
                 'missing_directories':sorted(source_dirs-destination_dirs),'extra_directories':sorted(destination_dirs-source_dirs)}
    print(json.dumps({'phase':'inventory','source_files':len(original),'destination_files':len(copied),'bytes':sum(original.values())},ensure_ascii=False),flush=True)
    hashes=[]
    for name in sorted(original.keys()&copied.keys()):
        scope=hash_scope(name)
        if scope:
            a,b=digest(source/name),digest(destination/name)
            hashes.append({'path':name,'scope':scope,'source_sha256':a,'destination_sha256':b,'matches':a==b})
    integrity=[]
    for folder in (destination/'enterprises').iterdir():
        db=folder/'data/carbon_account.db'
        if not folder.is_dir() or not db.is_file():continue
        with closing(sqlite3.connect(str(db))) as conn:
            conn.execute('PRAGMA query_only=ON')
            checks=[row[0] for row in conn.execute('PRAGMA integrity_check')]
        integrity.append({'database':db.relative_to(destination).as_posix(),'result':checks,'ok':checks==['ok']})
    success=not any(differences.values()) and all(item['matches'] for item in hashes) and all(item['ok'] for item in integrity)
    result={'client_date':'2026-10-26','source':str(source),'destination':str(destination),'passed':success,
            'source_files':len(original),'copied_files':len(copied),'source_bytes':sum(original.values()),'copied_bytes':sum(copied.values()),
            'source_junctions_not_followed':links,'destination_junctions_not_followed':dest_links,
            'differences':differences,'checked_hashes':hashes,'database_integrity':integrity,
            'verification_scope':'All regular file names/sizes and directories compared; common code, adaptation assets and critical current runtime files SHA-256 compared. Developer environments, Git internals and old data backups have size/inventory checks only.',
            'inventory':[{'path':name,'bytes':size} for name,size in sorted(original.items())]}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':success,'files':len(copied),'bytes':sum(copied.values()),'hashed_files':len(hashes),'databases':integrity,'differences':differences,'output':str(args.output)},ensure_ascii=False),flush=True)
    return 0 if success else 1

if __name__=='__main__':sys.exit(main())
