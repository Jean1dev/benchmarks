#!/usr/bin/env python3
"""Run a single audited benchmark combination in an isolated Compose project."""
import argparse,csv,datetime,fcntl,hashlib,json,os,pathlib,platform,subprocess,sys,time,uuid
from parse_gatling_log import export
from collect_resources import Docker,FIELDS
ROOT=pathlib.Path(__file__).resolve().parents[2]
PROJECT='benchmark-measurement'

def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def run(cmd,**kw):return subprocess.run(cmd,cwd=ROOT,check=True,text=True,**kw)
def compose(*args,**kw):
    env=dict(os.environ,API_PORT='18080')
    return run(['docker','compose','-p',PROJECT,*args],env=env,**kw)
def output(cmd):return run(cmd,capture_output=True).stdout.strip()
def save(path,obj):path.write_text(json.dumps(obj,indent=2)+'\n')
def inspect(name):return json.loads(output(['docker','inspect',name]))[0]
def ready():
    import urllib.request,urllib.error
    for _ in range(120):
        try:urllib.request.urlopen('http://127.0.0.1:18080/messages/00000000-0000-0000-0000-000000000000',timeout=1)
        except urllib.error.HTTPError as e:
            if e.code==404:return
        except OSError:pass
        time.sleep(.5)
    raise RuntimeError('API not ready')
def main():
    p=argparse.ArgumentParser();p.add_argument('--campaign',choices=['smoke','full'],required=True);p.add_argument('--config',type=pathlib.Path,default=ROOT/'benchmark/config.json');p.add_argument('--implementation',required=True);p.add_argument('--scenario',default='get');p.add_argument('--payload-bytes',type=int,default=128);p.add_argument('--model',choices=['open','closed'],default='closed');p.add_argument('--load',type=float);p.add_argument('--repetition',type=int,default=1);a=p.parse_args()
    cfg=json.loads(a.config.read_text());smoke=a.campaign=='smoke';profile=cfg['smoke'] if smoke else cfg
    if a.implementation not in cfg['implementations'] or a.scenario not in cfg['scenarios']:p.error('implementation/scenario outside config')
    if a.payload_bytes not in cfg['payload_bytes']:p.error('payload outside config')
    load=a.load if a.load is not None else profile['closed_users' if a.model=='closed' else 'open_rates_per_second'][0]
    if a.model=='closed' and (load<1 or int(load)!=load):p.error('closed users must be a positive integer')
    warm=profile['warmup_seconds'];duration=profile['measurement_seconds'];drain=cfg['drain_seconds']
    (ROOT/'.benchmark-data').mkdir(exist_ok=True)
    lock=(ROOT/'.benchmark-data/measurement.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    rid=f'{a.implementation}-{a.scenario}-{a.model}-{a.payload_bytes}-{a.repetition}-{uuid.uuid4().hex[:8]}'
    campaign=cfg['campaign_id']+('-smoke' if smoke else '')
    out=ROOT/'results'/campaign/rid;out.mkdir(parents=True);(out/'gatling').mkdir()
    save(out/'config.json',cfg)
    image=output(['docker','image','inspect',f'benchmark-api-{a.implementation}:local','--format','{{.Id}}'])
    m=dict(schema_version=1,campaign_id=campaign,run_id=rid,status='running',started_at_utc=utc(),implementation=a.implementation,scenario=a.scenario,payload_bytes=a.payload_bytes,model=a.model,load=load,repetition=a.repetition,seed=cfg['seed'],warmup_seconds=warm,measurement_seconds=duration,drain_seconds=drain,smoke=smoke,git_revision=output(['git','rev-parse','HEAD']),config_sha256=sha(a.config),image=f'benchmark-api-{a.implementation}:local',image_id=image,resources=cfg['resources'],slo=cfg.get('slo',{}))
    manifest=out/'manifest.json';save(manifest,m)
    collector=None;stop=out/'collector.stop';api=f'{PROJECT}-api-{a.implementation}-1';pg=f'{PROJECT}-postgres-1';gen=f'benchmark-gatling-{rid}'
    def log(message):
        with (out/'execution.log').open('a') as f:f.write(f'{utc()} {message}\n')
    def stop_apis():
        compose('stop',*['api-'+i for i in cfg['implementations']],stdout=subprocess.DEVNULL)
    try:
        stop_apis();compose('up','-d','postgres',stdout=subprocess.DEVNULL)
        count=10 if smoke else cfg['database']['initial_records']
        # Cache encrypted seed artifacts for this campaign/payload; plaintext/key never logged.
        data=ROOT/'.benchmark-data'/campaign/f'baseline-{a.payload_bytes}-{count}'
        if not (data/'metadata.json').exists():
            data.mkdir(parents=True,exist_ok=True)
            run(['docker','run','--rm','--user','0','--cpus','1','--memory','256m','--memory-swap','256m','--network','none','--env-file',str(ROOT/'.env'),'-v',f'{ROOT/"load-tests/scripts/seed_dataset.py"}:/seed.py:ro','-v',f'{data}:/data','--entrypoint','python','benchmark-api-python:local','/seed.py','--out','/data','--size',str(a.payload_bytes),'--count',str(count),'--seed',cfg['seed']])
        # TRUNCATE is restricted to this dedicated benchmark project and precedes every run.
        compose('exec','-T','postgres','psql','-U','benchmark','-d','benchmark','-v','ON_ERROR_STOP=1','-c','TRUNCATE messages;',stdout=subprocess.DEVNULL)
        with (data/'seed.tsv').open() as f:
            compose('exec','-T','postgres','psql','-U','benchmark','-d','benchmark','-v','ON_ERROR_STOP=1','-c',"COPY messages (id,nonce,ciphertext,tag) FROM STDIN",stdin=f,stdout=subprocess.DEVNULL)
        compose('exec','-T','postgres','psql','-U','benchmark','-d','benchmark','-c','VACUUM (ANALYZE) messages;',stdout=subprocess.DEVNULL)
        compose('--profile',a.implementation,'up','-d',f'api-{a.implementation}',stdout=subprocess.DEVNULL);ready()
        pginfo=inspect(pg);apiinfo=inspect(api)
        def limits(info):
            h=info['HostConfig'];return {'cpus':h['NanoCpus']/1e9,'memory_bytes':h['Memory'],'swap_bytes':h['MemorySwap']-h['Memory']}
        dbsettings=compose('exec','-T','postgres','psql','-U','benchmark','-d','benchmark','-At','-c',"SELECT name||'='||setting FROM pg_settings WHERE name IN ('server_version','fsync','synchronous_commit','full_page_writes','shared_buffers','max_connections','statement_timeout') ORDER BY name",capture_output=True).stdout.strip()
        implementation=json.loads((ROOT/f'benchmark/implementations/{a.implementation}.json').read_text())
        m.update(dataset=json.loads((data/'metadata.json').read_text()),pool={'max_connections':10},durability={'fsync':True,'synchronous_commit':True,'full_page_writes':True},effective_limits={'api':limits(apiinfo),'postgres':limits(pginfo),'gatling':{'cpus':2,'memory_bytes':2147483648,'swap_bytes':0}},postgresql={'version':'17.11','image_id':pginfo['Image'],'configuration':dbsettings},environment={'host_id':hashlib.sha256(platform.node().encode()).hexdigest(),'os':platform.system(),'kernel':platform.release(),'hardware':{'machine':platform.machine(),'logical_cpus':os.cpu_count()},'docker':output(['docker','version','--format','{{.Server.Version}}']),'isolation':{'project':PROJECT,'one_api_at_a_time':True,'cache':'not cleared'}},http=cfg['http'],warmup_policy={'requests':int(warm*10),'cache_policy':'not cleared','model':'open','rate':10,'scenario':'get' if a.scenario=='get' else 'post'},implementation_config={'runtime':implementation['runtime'],'http_server':implementation.get('http',implementation.get('libraries',{})),'driver':implementation.get('libraries',{}),'crypto':implementation.get('crypto',implementation.get('libraries',{})),'flags':implementation,'source_sha256':{str(f.relative_to(ROOT)):sha(f) for f in sorted((ROOT/'apis'/a.implementation).glob('*')) if f.is_file() and f.suffix not in ('.jar','.class')}},generator_image_id=output(['docker','image','inspect','benchmark-gatling:local','--format','{{.Id}}']))
        docker=Docker()
        with (out/'rest.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader()
            for _ in range(3):
                for name in (api,pg):
                    row=docker.sample(name,'rest')
                    if row:w.writerow(row)
                time.sleep(1)
        def gatling(phase,seconds,model,level,scenario):
            phaseout=out/'gatling'/phase;phaseout.mkdir()
            props=[f'-DbaseUrl=http://api-{a.implementation}:8080',f'-Dscenario={scenario}',f'-DpayloadBytes={a.payload_bytes}',f'-Dseed={cfg["seed"]}','-DidsFile=/data/ids.csv',f'-Dphase={phase}',f'-DdurationSeconds={seconds}',f'-Dgatling.simulationClass=com.orca.benchmark.{"Open" if model=="open" else "Closed"}WorkloadSimulation',f'-D{"ratePerSecond" if model=="open" else "users"}={level if model=="open" else int(level)}','-Dgatling.noReports=false']
            with (out/f'gatling-{phase}.log').open('w') as f:
                compose('--profile',a.implementation,'--profile','load-test','run','--rm','-T','--interactive=false','--name',gen,'-v',f'{data}:/data:ro','-v',f'{phaseout}:/workspace/results','gatling',*props,stdout=f,stderr=subprocess.STDOUT)
            logs=list(phaseout.rglob('simulation.log'))
            if len(logs)!=1:raise RuntimeError(f'expected one preserved {phase} log, found {len(logs)}')
            return logs[0]
        log('warmup: fixed offered request count across implementations')
        warm_log=gatling('warmup',warm,'open',10,m['warmup_policy']['scenario'])
        warmmeta=export(warm_log,out/'warmup-requests.csv',warm,'warmup')
        if warmmeta['request_count']!=warm*10 or warmmeta['users_started']!=warmmeta['users_ended']:raise RuntimeError('incomplete warmup')
        save(out/'warmup-decoder.json',warmmeta)
        collector=subprocess.Popen([sys.executable,str(ROOT/'load-tests/scripts/collect_resources.py'),'--containers',api,pg,gen,'--out',str(out/'load-resources.csv'),'--duration',str(duration+drain+180),'--stop-file',str(stop)],cwd=ROOT,stdout=(out/'collector.log').open('w'),stderr=subprocess.STDOUT)
        log('measure')
        measured_log=gatling('measure',duration,a.model,load,a.scenario)
        meta=export(measured_log,out/'requests.csv',duration)
        save(out/'decoder.json',meta)
        stop.touch();collector.wait(timeout=15)
        if collector.returncode:raise RuntimeError('resource collector failed')
        m['measurement_window']=meta['measurement_window']
        m['request_export_complete']=meta['users_started']==meta['users_ended'] and not meta['errors']
        if not m['request_export_complete']:raise RuntimeError('Gatling users did not drain or unassociated errors occurred')
        start=datetime.datetime.fromisoformat(meta['measurement_window']['started_at_utc']).timestamp();end=start+duration
        with (out/'resources.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader()
            for filename in ('rest.csv','load-resources.csv'):
                for row in csv.DictReader((out/filename).open()):
                    if filename=='load-resources.csv':
                        t=datetime.datetime.fromisoformat(row['timestamp_utc'].replace('Z','+00:00')).timestamp()
                        row['phase']='warmup' if t<start else 'measure' if t<end else 'drain'
                    w.writerow(row)
        # Use the same window-based definitions as the report, not the old all-row counts.
        sys.path.insert(0,str(ROOT/'analysis'));from report import request_metrics,stamp
        rows=[]
        for row in csv.DictReader((out/'requests.csv').open()):
            if row['phase']=='measure':rows.append(dict(row,start=stamp(row['started_at_utc']),end=stamp(row['finished_at_utc']),duration=float(row['duration_ms']),ok=row['success']=='true'))
        metrics=request_metrics(rows,start,end)
        summary=dict(schema_version=1,status='complete',run_id=rid,measurement_duration_seconds=duration,**metrics)
        summary['latency_ms']={f'p{n}':metrics[f'success_p{n}_ms'] for n in (50,95,99)}
        summary['endpoints']={name:request_metrics([r for r in rows if r['endpoint']==name],start,end) for name in {r['endpoint'] for r in rows}}
        save(out/'summary.json',summary)
        if not rows:raise RuntimeError('empty measured export')
        samples=list(csv.DictReader((out/'resources.csv').open()))
        for role in (f'api-{a.implementation}','postgres','gatling'):
            points=[r for r in samples if r['service']==role and r['phase']=='measure']
            if len(points)<2 or any(not r['cpu_usage_seconds'] or not r['memory_current_bytes'] for r in points):raise RuntimeError(f'missing resource measurements for {role}')
        m['status']='complete';m['request_export_status']='decoded_binary_3.14.9'
        log(f'complete: {len(rows)} requests; throughput={metrics["throughput_success_per_second"]}')
    except Exception as e:
        m['status']='invalid';m['failure_reason']=str(e);log(f'FAILED {e}');raise
    finally:
        stop.touch()
        if collector and collector.poll() is None:
            try:collector.wait(timeout=15)
            except subprocess.TimeoutExpired:collector.terminate();collector.wait()
        m['finished_at_utc']=utc()
        m['artifact_sha256']={str(f.relative_to(out)):sha(f) for f in out.rglob('*') if f.is_file() and f!=manifest and f.name!='collector.stop'}
        save(manifest,m);stop_apis()
        print(str(out),flush=True)
if __name__=='__main__':main()
