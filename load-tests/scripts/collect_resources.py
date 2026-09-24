#!/usr/bin/env python3
"""Sample Docker Engine raw cumulative stats; works with rootless Docker.
memory_stats.usage is total cgroup usage (not Docker CLI's cache-subtracted display).
"""
import argparse,csv,datetime,http.client,json,pathlib,socket,subprocess,time
FIELDS='timestamp_utc phase container service cpu_usage_seconds memory_current_bytes memory_limit_bytes cpu_throttled_seconds restarts oom_killed'.split()
class Docker:
    def __init__(self):
        host=json.loads(subprocess.check_output(['docker','context','inspect','--format','{{json .Endpoints.docker.Host}}'],text=True))
        if not host.startswith('unix://'):raise RuntimeError('collector requires a local Unix Docker socket')
        self.path=host[7:]
    def get(self,path):
        c=http.client.HTTPConnection('localhost',timeout=5)
        c.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);c.sock.settimeout(5);c.sock.connect(self.path)
        try:
            c.request('GET',path);r=c.getresponse();data=r.read()
            if r.status==404:return None
            if r.status!=200:raise RuntimeError(f'Docker API {r.status}: {data[:100]}')
            return json.loads(data)
        finally:c.close()
    def sample(self,name,phase):
        info=self.get(f'/containers/{name}/json')
        if not info or not info['State']['Running']:return None
        raw=self.get(f'/containers/{name}/stats?stream=false&one-shot=true')
        cpu=raw.get('cpu_stats',{});memory=raw.get('memory_stats',{});throttle=cpu.get('throttling_data',{}).get('throttled_time')
        usage=cpu.get('cpu_usage',{}).get('total_usage')
        return dict(timestamp_utc=raw.get('read') or datetime.datetime.now(datetime.timezone.utc).isoformat(),phase=phase,container=info['Id'],service=info['Config'].get('Labels',{}).get('com.docker.compose.service','gatling' if 'gatling' in name else name),cpu_usage_seconds=usage/1e9 if usage is not None else '',memory_current_bytes=memory.get('usage',''),memory_limit_bytes=memory.get('limit',''),cpu_throttled_seconds=throttle/1e9 if throttle is not None else '',restarts=info['RestartCount'],oom_killed=info['State']['OOMKilled'])
def main():
    p=argparse.ArgumentParser();p.add_argument('--containers',nargs='+',required=True);p.add_argument('--out',required=True);p.add_argument('--phase',default='measure');p.add_argument('--duration',type=float,default=120);p.add_argument('--interval',type=float,default=1);p.add_argument('--stop-file');a=p.parse_args()
    docker=Docker();end=time.monotonic()+a.duration
    with open(a.out,'w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader()
        while time.monotonic()<end and not (a.stop_file and pathlib.Path(a.stop_file).exists()):
            tick=time.monotonic()
            for name in a.containers:
                row=docker.sample(name,a.phase)
                if row:w.writerow(row)
            f.flush();time.sleep(max(.01,a.interval-(time.monotonic()-tick)))
if __name__=='__main__':main()
