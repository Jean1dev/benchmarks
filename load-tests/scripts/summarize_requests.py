#!/usr/bin/env python3
import argparse,csv,json,math,statistics
def percentile(values,p):
    if not values: return None
    values=sorted(values); index=(len(values)-1)*p; lower=math.floor(index); upper=math.ceil(index)
    return values[lower] if lower==upper else values[lower]+(values[upper]-values[lower])*(index-lower)
def main():
    p=argparse.ArgumentParser(); p.add_argument('--requests',required=True); p.add_argument('--out',required=True); p.add_argument('--duration',type=float,required=True); p.add_argument('--run-id',required=True); a=p.parse_args()
    rows=list(csv.DictReader(open(a.requests))); successes=[r for r in rows if r.get('success')=='true']; failures=[r for r in rows if r.get('success')!='true']; lat=[float(r['duration_ms']) for r in successes if r.get('duration_ms')]
    by={}
    for r in rows: by.setdefault(r.get('endpoint',''),[]).append(r)
    endpoints={}
    for name,items in by.items():
        good=[x for x in items if x.get('success')=='true']; values=[float(x['duration_ms']) for x in good if x.get('duration_ms')]
        endpoints[name]={'requests_started':len(items),'successes':len(good),'failures':len(items)-len(good),'failure_rate_percent':(100*(len(items)-len(good))/len(items) if items else None),'latency_ms':{'p50':percentile(values,.5),'p95':percentile(values,.95),'p99':percentile(values,.99)},'throughput_success_per_second':len(good)/a.duration}
    result={'schema_version':1,'status':'complete' if rows else 'invalid','run_id':a.run_id,'measurement_duration_seconds':a.duration,'requests_started':len(rows) if rows else None,'successes':len(successes) if rows else None,'failures':len(failures) if rows else None,'failure_rate_percent':(100*len(failures)/len(rows) if rows else None),'throughput_success_per_second':len(successes)/a.duration if rows else None,'latency_ms':{'p50':percentile(lat,.5),'p95':percentile(lat,.95),'p99':percentile(lat,.99)},'endpoints':endpoints,'notes':['Throughput counts successes completed in the measured window divided by configured measurement duration.','Status HTTP is nullable because Gatling simulation.log does not expose it for every event.'] if rows else ['Gatling 3.14.9 simulation.log is binary; request-level rows are unavailable to the current parser and this run must not be ranked.']}
    open(a.out,'w').write(json.dumps(result,indent=2)+'\n')
if __name__=='__main__': main()
