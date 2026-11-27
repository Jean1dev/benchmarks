"""Synthetic fixtures only. Never copied to results/."""
import csv
import json
import hashlib
import shutil
from pathlib import Path
import tempfile
import unittest

import report


class MetricsTest(unittest.TestCase):
    def test_boundaries_and_errors(self):
        def row(a, b, ok=True, error=''):
            return dict(start=a, end=b, ok=ok, duration=(b-a)*1000, error_class=error, status_http='200' if ok else '')
        rows = [row(10,11), row(19,21), row(15,16,False,'timeout'), row(20,22)]
        m = report.request_metrics(rows,10,20)
        self.assertEqual(m['requests_started'],3)
        self.assertEqual(m['successes'],2)
        self.assertEqual(m['throughput_success_per_second'],.1)
        self.assertEqual(m['timeouts'],1)
        self.assertEqual(m['success_p95_ms'],1950)
        self.assertEqual(m['failure_p99_ms'],1000)

    def test_baseline_zero_and_quantiles(self):
        self.assertIsNone(report.relative(1,0))
        self.assertEqual(report.relative(12,10),20)
        self.assertEqual(report.percentile([1,2,3,4],.25),1.75)
        self.assertIsNone(report.percentile([],.95))

    def test_repetitions_and_slo(self):
        def run(rep,throughput):
            return dict(validity='valid',equivalence_id='e',variant_id='v',run_id=str(rep),
                        manifest=dict(implementation='go',repetition=rep,model='open'),dimensions={},endpoints={},resource_metrics={},
                        metrics=dict(throughput_success_per_second=throughput,success_p95_ms=100,failure_rate_percent=0,started_offered_deviation_percent=0))
        c=report.aggregate([run(1,10),run(2,20),run(3,30)],dict(repetitions=3))[0]
        self.assertEqual(c['metrics']['overall.throughput_success_per_second']['median'],20)
        self.assertEqual(c['metrics']['overall.throughput_success_per_second']['q1'],15)
        self.assertTrue(c['rate_passed'])
        self.assertFalse(report.aggregate([run(1,10)],dict(repetitions=3))[0]['rate_passed'])
        r=run(1,10);r['metrics']['failure_rate_percent']=2
        self.assertFalse(report.aggregate([r],dict(repetitions=1))[0]['rate_passed'])


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.path=self.root/'fixture';self.path.mkdir()
        self.config=dict(implementations=['go'],scenarios=['post'],repetitions=1)
        self.m=dict(schema_version=1,campaign_id='test',run_id='fixture',implementation='go',scenario='post',model='open',
                    git_revision='test',image_id='sha256:test',config_sha256='test',seed='fixture',payload_bytes=128,repetition=1,
                    warmup_seconds=2,measurement_seconds=10,drain_seconds=2,load=.3,smoke=False,status='complete',
                    started_at_utc='2026-01-01T00:00:00Z',finished_at_utc='2026-01-01T00:00:30Z',
                    measurement_window=dict(started_at_utc='2026-01-01T00:00:10Z',ended_at_utc='2026-01-01T00:00:20Z',source='generator'),
                    request_export_complete=True)
        for field in ('dataset','pool','durability','effective_limits','postgresql','environment','http','warmup_policy','implementation_config'):
            self.m[field]={key: 'fixture' for key in report.EVIDENCE_FIELDS[field]}
        self.m['effective_limits']={role: {'cpus': 1, 'memory_bytes': 1024, 'swap_bytes': 0} for role in ('api','postgres','gatling')}
        self.write_manifest()
        self.rows=[self.row('warmup',8,9),self.row('measure',10,11),self.row('measure',19,21),self.row('measure',15,16,False),self.row('drain',20,21)]
        self.write_rows()
        self.summary=dict(schema_version=1,run_id='fixture',status='complete',requests_started=3,successes=2,failures=1,
                          failure_rate_percent=100/3,throughput_success_per_second=.1,measurement_duration_seconds=10,
                          latency_ms={'p50':1500,'p95':1950,'p99':1990})
        (self.path/'summary.json').write_text(json.dumps(self.summary))
        (self.path/'resources.csv').write_text(','.join(report.RESOURCE_FIELDS)+'\n')

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self):
        (self.path/'manifest.json').write_text(json.dumps(self.m))

    def row(self,phase,a,b,ok=True):
        return dict(request_id=f'{phase}-{a}',endpoint='POST /messages',phase=phase,
                    started_at_utc=f'2026-01-01T00:00:{a:02d}Z',finished_at_utc=f'2026-01-01T00:00:{b:02d}Z',
                    duration_ms=(b-a)*1000,success=str(ok).lower(),status_http='201' if ok else '500',error_class='' if ok else 'timeout')

    def write_rows(self):
        with (self.path/'requests.csv').open('w') as f:
            w=csv.DictWriter(f,fieldnames=report.REQUEST_FIELDS);w.writeheader();w.writerows(self.rows)

    def analyze(self):
        return report.analyze_run(self.path,self.config)

    def test_full_run_and_input_preserved(self):
        before=report.inventory(self.root)
        r=self.analyze()
        self.assertEqual(report.classify(r),'valid',r['issues'])
        self.assertEqual(r['metrics']['throughput_success_per_second'],.1)
        self.assertEqual(r['metrics']['requests_started'],3)
        self.assertEqual(report.inventory(self.root),before)

    def test_missing_and_empty(self):
        (self.path/'requests.csv').unlink()
        r=self.analyze();self.assertEqual(report.classify(r),'invalid');self.assertEqual(r['metrics'],{})
        self.rows=[];self.write_rows()
        self.assertIn('requests_empty',[i['code'] for i in self.analyze()['issues']])

    def test_duplicate_and_bad_phase(self):
        self.rows.append(self.rows[1]);self.write_rows()
        self.assertEqual(report.classify(self.analyze()),'invalid')
        self.rows=[self.row('measure',20,21)];self.write_rows()
        self.assertIn('request_row',[i['code'] for i in self.analyze()['issues']])

    def test_drain_and_summary_disagreement(self):
        self.rows[2]['finished_at_utc']='2026-01-01T00:00:23Z';self.rows[2]['duration_ms']=4000;self.write_rows()
        self.assertEqual(report.classify(self.analyze()),'invalid')
        self.rows[2]=self.row('measure',19,21);self.write_rows()
        self.summary['throughput_success_per_second']=.2
        (self.path/'summary.json').write_text(json.dumps(self.summary))
        self.assertIn('summary_divergence',[i['code'] for i in self.analyze()['issues']])

    def test_missing_window_and_smoke(self):
        self.m.pop('measurement_window');self.write_manifest()
        self.assertEqual(self.analyze()['metrics'],{})
        self.m['smoke']=True;self.write_manifest()
        self.assertNotEqual(report.classify(self.analyze()),'valid')

    def test_resource_cpu_and_missing(self):
        r=self.analyze()
        rows=[]
        for t,cpu,mem in [(10,2,100),(15,4,200),(20,7,300)]:
            rows.append(dict(timestamp_utc=f'2026-01-01T00:00:{t}Z',phase='measure',container='api',service='api-go',cpu_usage_seconds=str(cpu),memory_current_bytes=str(mem),memory_limit_bytes='1000',cpu_throttled_seconds='0',restarts='0',oom_killed='false'))
        r['manifest']['effective_limits']={'api':{'cpus':1}}
        a,b=r['window'];report.resources(r,rows,a,b)
        m=r['resource_metrics']['api-go']
        self.assertEqual(m['cpu_cores'],.5)
        self.assertEqual(m['cpu_percent_effective_limit'],50)
        self.assertEqual(m['memory_median_bytes'],150)
        self.assertEqual(m['memory_sampled_peak_bytes'],200)
        self.assertIsNone(m['cpu_seconds_per_success'])
        rows[1]['cpu_usage_seconds']=''
        report.resources(r,rows,a,b)
        self.assertIsNone(r['resource_metrics']['api-go']['cpu_cores'])

    def test_bad_types_and_truncated_csv(self):
        self.rows[1]['success']='yes';self.write_rows()
        self.assertEqual(report.classify(self.analyze()),'invalid')

        (self.path/'requests.csv').write_text(','.join(report.REQUEST_FIELDS)+'\nonly-one-field\n')
        self.assertEqual(report.classify(self.analyze()),'invalid')

    def test_integrity_and_api_failure(self):
        self.m['artifact_sha256']={'requests.csv':'wrong'};self.write_manifest()
        self.assertIn('artifact_integrity',[i['code'] for i in self.analyze()['issues']])
        self.m.pop('artifact_sha256');self.m['status']='failed';self.m['failure_kind']='api';self.write_manifest()
        self.assertEqual(report.classify(self.analyze()),'valid')
        self.m['failure_kind']='infrastructure';self.write_manifest()
        self.assertEqual(report.classify(self.analyze()),'invalid')

    def test_cli_outputs_and_duplicate_id(self):
        config=dict(self.config,campaign_id='test',payload_bytes=[128],open_rates_per_second=[.3],closed_users=[1])
        benchmark=self.root/'benchmark';benchmark.mkdir();(benchmark/'implementations').mkdir()
        (benchmark/'config.json').write_text(json.dumps(config))
        (benchmark/'contract.md').write_text('synthetic fixture')
        self.m['config_sha256']=hashlib.sha256((benchmark/'config.json').read_bytes()).hexdigest();self.write_manifest()
        results=self.root/'results';(results/'test').mkdir(parents=True)
        shutil.copytree(self.path,results/'test'/'fixture')
        out=self.root/'reports'
        before=report.inventory(results)
        args=['--results',str(results),'--benchmark',str(benchmark),'--reports',str(out)]
        report.main(args)
        comparison=json.loads((out/'test'/'comparison.json').read_text())
        self.assertEqual(len(comparison),1)
        self.assertEqual(comparison[0]['metrics']['overall.throughput_success_per_second']['median'],.1)
        self.assertIn('<table>',(out/'test'/'report.html').read_text())
        self.assertEqual(report.inventory(results),before)
        shutil.copytree(self.path,results/'test'/'duplicate')
        report.main(args)
        self.assertEqual(json.loads((out/'test'/'comparison.json').read_text()),[])
        val=json.loads((out/'test'/'validation.json').read_text())
        self.assertTrue(all(any(i['code']=='duplicate_run_id' for i in r['issues']) for r in val['runs']))

    def test_no_measurements(self):
        benchmark=self.root/'benchmark';benchmark.mkdir();(benchmark/'implementations').mkdir()
        config=dict(self.config,campaign_id='test',payload_bytes=[128],open_rates_per_second=[1],closed_users=[1])
        (benchmark/'config.json').write_text(json.dumps(config))
        results=self.root/'results';results.mkdir()
        out=self.root/'reports'
        report.main(['--results',str(results),'--benchmark',str(benchmark),'--reports',str(out)])
        self.assertEqual(json.loads((out/'test'/'comparison.json').read_text()),[])
        self.assertIn('Não há medições válidas',(out/'test'/'report.md').read_text())


if __name__=='__main__':
    unittest.main()
