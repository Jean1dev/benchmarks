#!/usr/bin/env python3
"""Create an encrypted, deterministic logical baseline outside measured windows.
Runs inside the existing Python API image for its pinned cryptography dependency.
"""
import argparse,base64,csv,hashlib,json,os,pathlib,uuid
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def payload(size,seed):
    unit=('ação-日本語-😀|'+seed).encode()
    data=(unit*((size//len(unit))+1))[:size]
    while True:
        try:return data.decode()+'x'*(size-len(data))
        except UnicodeDecodeError:data=data[:-1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--size',type=int,required=True);p.add_argument('--count',type=int,required=True);p.add_argument('--seed',required=True);a=p.parse_args()
    out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    aes=AESGCM(base64.b64decode(os.environ['ENCRYPTION_KEY_BASE64'],validate=True));logical=hashlib.sha256()
    with (out/'seed.tsv').open('w') as f,(out/'ids.csv').open('w') as ids:
        w=csv.writer(ids);w.writerow(['id'])
        for i in range(a.count):
            uid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'{a.seed}:{a.size}:{i}'))
            msg=payload(a.size,f'{a.seed}:{i}').encode();logical.update(uid.encode()+msg)
            nonce=os.urandom(12);encrypted=aes.encrypt(nonce,msg,None)
            # PostgreSQL COPY text needs an escaped backslash before bytea's x marker.
            f.write(uid+'\t'+ '\t'.join('\\\\x'+v.hex() for v in (nonce,encrypted[:-16],encrypted[-16:]))+'\n');w.writerow([uid])
    (out/'metadata.json').write_text(json.dumps(dict(initial_records=a.count,seed=a.seed,sha256=logical.hexdigest(),payload_bytes=a.size,selection='uniform_with_replacement'),indent=2))
if __name__=='__main__':main()
