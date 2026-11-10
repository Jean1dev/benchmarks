#!/usr/bin/env python3
"""Strict Gatling 3.14.9 binary decoder (official v3.14.9 writer format).
Source: https://github.com/gatling/gatling/blob/v3.14.9/gatling-core/src/main/scala/io/gatling/core/stats/writer/LogFileDataWriter.scala
No instrumentation is added to the request path. HTTP status is not in this format.
"""
import argparse,csv,datetime,json,pathlib,struct
FIELDS='request_id endpoint phase started_at_utc finished_at_utc duration_ms success status_http error_class'.split()
def iso(ms): return datetime.datetime.fromtimestamp(ms/1000,datetime.timezone.utc).isoformat()
class Decoder:
    def __init__(self,data): self.data=data;self.pos=0;self.cache={}
    def take(self,n):
        if n<0 or self.pos+n>len(self.data): raise ValueError(f'truncated/invalid log at byte {self.pos}, need {n}')
        b=self.data[self.pos:self.pos+n];self.pos+=n;return b
    def integer(self): return struct.unpack('>i',self.take(4))[0]
    def long(self): return struct.unpack('>q',self.take(8))[0]
    def boolean(self):
        b=self.take(1)[0]
        if b not in (0,1): raise ValueError('invalid boolean')
        return bool(b)
    def string(self):
        size=self.integer()
        if not size:return ''
        raw=self.take(size);coder=self.take(1)[0]
        if coder not in (0,1):raise ValueError('invalid string coder')
        return raw.decode('latin1' if coder==0 else 'utf-16-le')
    def cached(self):
        key=self.integer()
        if key>=0:self.cache[key]=self.string();return self.cache[key]
        if -key not in self.cache:raise ValueError('missing cached string')
        return self.cache[-key]
    def count(self):
        n=self.integer()
        if not 0<=n<=1000000:raise ValueError('invalid count')
        return n
    def parse(self):
        if self.take(1)!=b'\0':raise ValueError('not a Gatling binary log')
        version=self.string()
        if version!='3.14.9':raise ValueError(f'unsupported Gatling {version}')
        meta={'gatling_version':version,'simulation':self.string(),'run_start_ms':self.long(),'description':self.string()}
        meta['scenarios']=[self.string() for _ in range(self.count())]
        for _ in range(self.count()):self.take(self.integer())
        rows=[];starts=[];ends=[];errors=[]
        while self.pos<len(self.data):
            kind=self.take(1)[0]
            if kind==1:
                for _ in range(self.count()):self.cached()
                name=self.cached();a=self.integer()+meta['run_start_ms'];b=self.integer()+meta['run_start_ms'];ok=self.boolean();error=self.cached()
                if b<a:raise ValueError('negative request duration')
                rows.append(dict(request_id=str(len(rows)+1),endpoint=name,started_at_utc=iso(a),finished_at_utc=iso(b),duration_ms=b-a,success=str(ok).lower(),status_http='',error_class=error,_start=a,_end=b))
            elif kind==2:
                scenario=self.integer();start=self.boolean();t=self.integer()+meta['run_start_ms']
                if not 0<=scenario<len(meta['scenarios']):raise ValueError('invalid scenario')
                (starts if start else ends).append(t)
            elif kind==3:
                for _ in range(self.count()):self.cached()
                self.integer();self.integer();self.integer();self.boolean()
            elif kind==4:errors.append({'message':self.cached(),'timestamp_ms':self.integer()+meta['run_start_ms']})
            else:raise ValueError(f'unknown record {kind}')
        meta.update(users_started=len(starts),users_ended=len(ends),first_user_ms=min(starts) if starts else None,last_user_end_ms=max(ends) if ends else None,request_count=len(rows),errors=errors,byte_count=self.pos)
        return rows,meta

def export(log,out,duration,phase='measure'):
    rows,meta=Decoder(pathlib.Path(log).read_bytes()).parse()
    start=meta['first_user_ms']
    if start is None:raise ValueError('no injected users')
    end=start+duration*1000
    meta['measurement_window']={'started_at_utc':iso(start),'ended_at_utc':iso(end),'source':'generator'}
    with open(out,'w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader()
        for r in rows:
            a=r.pop('_start');r.pop('_end')
            r['phase']=phase if start<=a<end else ('warmup' if a<start else 'drain')
            w.writerow(r)
    return meta

def main():
    p=argparse.ArgumentParser();p.add_argument('log');p.add_argument('--out',required=True);p.add_argument('--duration',type=float,required=True);p.add_argument('--phase',default='measure');p.add_argument('--metadata');a=p.parse_args()
    meta=export(a.log,a.out,a.duration,a.phase)
    if a.metadata:pathlib.Path(a.metadata).write_text(json.dumps(meta,indent=2)+'\n')
    print(json.dumps(meta))
if __name__=='__main__':main()
