#!/usr/bin/env python3
"""Sequential, round-robin repeated validation for every implementation."""
import argparse,datetime,json,os,pathlib,signal,subprocess,sys,time
ROOT=pathlib.Path(__file__).resolve().parents[2]
def main():
 p=argparse.ArgumentParser();p.add_argument('--config',default='benchmark/campaigns/validation-5reps.json');p.add_argument('--budget-seconds',type=int,default=1800);p.add_argument('--resume',action='store_true');a=p.parse_args()
 cfg=json.loads((ROOT/a.config).read_text());directory=ROOT/'results'/cfg['campaign_id'];directory.mkdir(parents=True,exist_ok=True)
 previous=[json.loads(f.read_text()) for f in directory.glob('*/manifest.json')]
 started=min([datetime.datetime.fromisoformat(m['started_at_utc']).timestamp() for m in previous],default=time.time()) if a.resume else time.time()
 deadline=started+a.budget_seconds
 slots={(m['implementation'],m.get('repetition')) for m in previous if m.get('status')=='complete'}
 for rep in range(1,cfg['repetitions']+1):
  for impl in cfg['implementations']:
   if (impl,rep) in slots:continue
   remaining=deadline-time.time()
   if remaining<=0:break
   command=[sys.executable,'load-tests/scripts/run_campaign.py','--campaign','full','--config',a.config,'--implementation',impl,'--scenario',cfg['scenarios'][0],'--payload-bytes',str(cfg['payload_bytes'][0]),'--model','open','--load',str(cfg['open_rates_per_second'][0]),'--repetition',str(rep)]
   with (directory/f'{impl}-rep{rep}-orchestrator.log').open('w') as log:
    child=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    try:child.wait(timeout=max(1,remaining-5))
    except subprocess.TimeoutExpired:
     os.killpg(child.pid,signal.SIGTERM)
     try:child.wait(timeout=3)
     except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
   subprocess.run([sys.executable,'analysis/report.py','--config',a.config,'--campaign',cfg['campaign_id']],cwd=ROOT,check=True)
   print(f'{impl} rep {rep}/{cfg["repetitions"]}: exit={child.returncode}, elapsed={time.time()-started:.1f}s',flush=True)
  if time.time()>=deadline:break
 subprocess.run([sys.executable,'analysis/report.py','--config',a.config,'--campaign',cfg['campaign_id']],cwd=ROOT,check=True)
 (directory/'validation-session.json').write_text(json.dumps({'started_at_utc':datetime.datetime.fromtimestamp(started,datetime.timezone.utc).isoformat(),'finished_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'elapsed_seconds':time.time()-started,'budget_seconds':a.budget_seconds,'runs_complete':len(slots)},indent=2)+'\n')
if __name__=='__main__':main()
