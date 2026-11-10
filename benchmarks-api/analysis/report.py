#!/usr/bin/env python3
"""Offline, read-only benchmark analysis. No third-party dependencies."""
import argparse
import collections
import csv
import datetime as dt
import hashlib
import html
import itertools
import json
import math
from pathlib import Path
import subprocess

REQUEST_FIELDS = 'request_id endpoint phase started_at_utc finished_at_utc duration_ms success status_http error_class'.split()
RESOURCE_FIELDS = 'timestamp_utc phase container service cpu_usage_seconds memory_current_bytes memory_limit_bytes cpu_throttled_seconds restarts oom_killed'.split()
COLORS = {'go': '#007e87', 'rust': '#a34312', 'python': '#7451a8', 'node': '#28782d', 'java': '#bd334a', 'api': '#007e87', 'postgres': '#7451a8', 'gatling': '#a34312'}
CRITERIA = {
    'invalid': 'Schema/tipos, integridade, janela/coorte incompleta, infraestrutura, duplicatas ou divergências impedem comparação.',
    'incomparable': 'Sem evidência histórica de equivalência (dataset, pool, durabilidade, limites, PostgreSQL, host ou configuração).',
    'smoke': 'Smoke nunca participa de rankings.',
    'api_failure': 'Erros/timeout/OOM/reinício da API são resultados, não exclusões automáticas, se a observação estiver completa.',
    'outliers': 'Nenhum outlier é removido automaticamente.',
}
EVIDENCE_FIELDS = {
    'dataset': ('initial_records', 'seed', 'sha256'),
    'pool': ('max_connections',),
    'durability': ('fsync', 'synchronous_commit', 'full_page_writes'),
    'effective_limits': ('api', 'postgres', 'gatling'),
    'postgresql': ('version', 'image_id', 'configuration'),
    'environment': ('host_id', 'os', 'kernel', 'hardware', 'docker', 'isolation'),
    'http': ('timeout_seconds', 'keep_alive', 'compression', 'retries'),
    'warmup_policy': ('requests', 'cache_policy'),
    'implementation_config': ('runtime', 'http_server', 'driver', 'crypto', 'flags'),
}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def inventory(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def percentile(values, p):
    if not values:
        return None
    a = sorted(values)
    i = (len(a) - 1) * p
    return a[math.floor(i)] + (a[math.ceil(i)] - a[math.floor(i)]) * (i % 1)


def relative(value, baseline):
    return None if value is None or baseline in (None, 0) else 100 * (value - baseline) / baseline


def number(value, integer=False):
    if isinstance(value, bool):
        raise ValueError('booleano não é número')
    n = float(value)
    if not math.isfinite(n) or n < 0 or (integer and n != int(n)):
        raise ValueError(f'número inválido: {value!r}')
    return int(n) if integer else n


def stamp(value):
    t = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.utcoffset() != dt.timedelta(0):
        raise ValueError('timestamp deve ter timezone UTC')
    return t.timestamp()


def issue(run, code, detail, exclude=False):
    run['issues'].append({'code': code, 'detail': detail, 'excludes': exclude})


def read_json(path):
    def pairs(items):
        d = {}
        for key, value in items:
            if key in d:
                raise ValueError(f'chave JSON duplicada: {key}')
            d[key] = value
        return d
    value = json.loads(path.read_text(), object_pairs_hook=pairs,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    if not isinstance(value, dict):
        raise ValueError('objeto JSON esperado')
    return value


def read_csv(path, fields):
    with path.open(newline='') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames) or not set(fields) <= set(reader.fieldnames):
            raise ValueError('cabeçalho CSV ausente, duplicado ou incompleto')
        rows = list(reader)
        if any(None in r or any(v is None for v in r.values()) for r in rows):
            raise ValueError('linha CSV truncada ou com campos extras')
        return rows


def request_metrics(rows, start, end):
    cohort = [r for r in rows if start <= r['start'] < end]
    completed = [r for r in rows if start <= r['end'] < end]
    good = [r for r in cohort if r['ok']]
    bad = [r for r in cohort if not r['ok']]
    result = {'requests_started': len(cohort), 'successes': len(good), 'failures': len(bad),
              'completed': len(completed), 'successes_completed_in_window': sum(r['ok'] for r in completed),
              'throughput_success_per_second': sum(r['ok'] for r in completed) / (end-start),
              'started_per_second': len(cohort)/(end-start), 'completed_per_second': len(completed)/(end-start),
              'failure_rate_percent': 100*len(bad)/len(cohort) if cohort else None,
              'timeouts': sum('timeout' in r['error_class'].lower() or 'timed out' in r['error_class'].lower() for r in bad),
              'http_status_counts': dict(collections.Counter(r['status_http'] or 'unknown' for r in cohort)),
              'error_class_counts': dict(collections.Counter(r['error_class'] or 'unknown' for r in bad))}
    result['timeout_rate_percent'] = 100*result['timeouts']/len(cohort) if cohort else None
    for name, items in [('success', good), ('failure', bad)]:
        for p in [50, 95, 99]:
            result[f'{name}_p{p}_ms'] = percentile([r['duration'] for r in items], p/100)
    return result


def resources(run, rows, start, end):
    series = collections.defaultdict(list)
    seen = set()
    for r in rows:
        try:
            t = stamp(r['timestamp_utc'])
            if r['phase'] not in ('rest', 'warmup', 'measure', 'drain') or not r['service'] or not r['container']:
                raise ValueError('fase/serviço/container inválido')
            key = (r['container'], t)
            if key in seen:
                raise ValueError('amostra duplicada')
            seen.add(key)
            v = {'time': t, 'phase': r['phase'], 'container': r['container']}
            for field in RESOURCE_FIELDS[4:-1]:
                v[field] = number(r[field], field in ('memory_current_bytes', 'memory_limit_bytes', 'restarts')) if r[field] else None
            if r['oom_killed'].lower() not in ('true', 'false', ''):
                raise ValueError('oom_killed inválido')
            v['oom_killed'] = None if not r['oom_killed'] else r['oom_killed'].lower() == 'true'
            series[r['service']].append(v)
        except (ValueError, TypeError) as e:
            issue(run, 'resource_row', str(e), True)
    output = {}
    for service, samples in sorted(series.items()):
        if any(b['time'] <= a['time'] for a, b in zip(samples, samples[1:])):
            issue(run, 'resource_order', service, True)
        samples.sort(key=lambda r: r['time'])
        role = 'api' if service.startswith('api') else service
        load = [r for r in samples if start is not None and start <= r['time'] < end and r['phase'] == 'measure']
        idle = [r['memory_current_bytes'] for r in samples if r['phase'] == 'rest' and r['memory_current_bytes'] is not None]
        mem = [r['memory_current_bytes'] for r in load if r['memory_current_bytes'] is not None]
        oom = [r['oom_killed'] for r in samples if r['oom_killed'] is not None]
        restarts = [r['restarts'] for r in samples if r['restarts'] is not None]
        result = {'memory_definition': 'cgroup memory.current (bytes), não RSS/working set',
                  'idle_memory_bytes': percentile(idle, .5), 'memory_median_bytes': percentile(mem, .5),
                  'memory_p95_bytes': percentile(mem, .95), 'memory_sampled_peak_bytes': max(mem) if mem else None,
                  'memory_kernel_peak_bytes': None, 'oom_killed': any(oom) if oom else None,
                  'restarts_observed_max': max(restarts) if restarts else None,
                  'cpu_cores': None, 'cpu_percent_effective_limit': None,
                  'cpu_seconds_per_success': None, 'cpu_throttled_seconds': None}
        if result['oom_killed'] or (result['restarts_observed_max'] or 0) > 0:
            issue(run, 'api_failure' if role == 'api' else 'infrastructure_failure', service, role != 'api')
        if not mem or not any(r['cpu_usage_seconds'] is not None for r in load):
            issue(run, 'resource_values_missing', service)
        if not idle and role != 'gatling':
            issue(run, 'idle_missing', service)
        # Only integrate complete adjacent intervals; never interpolate across missing counters/restarts.
        cpu_delta = observed = throttled = 0.0
        throttle_known = False
        for a, b in zip(samples, samples[1:]):
            if start is None or not (start <= a['time'] < b['time'] <= end):
                continue
            if a['container'] != b['container'] or a['restarts'] != b['restarts']:
                continue
            if a['cpu_usage_seconds'] is None or b['cpu_usage_seconds'] is None:
                continue
            delta = b['cpu_usage_seconds'] - a['cpu_usage_seconds']
            if delta < 0:
                issue(run, 'cpu_counter_reset', service)
                continue
            cpu_delta += delta
            observed += b['time'] - a['time']
            if a['cpu_throttled_seconds'] is not None and b['cpu_throttled_seconds'] is not None:
                td = b['cpu_throttled_seconds'] - a['cpu_throttled_seconds']
                if td >= 0:
                    throttled += td
                    throttle_known = True
        if observed:
            result['cpu_cores'] = cpu_delta / observed
            result['cpu_observed_seconds'] = observed
            result['cpu_throttled_seconds'] = throttled if throttle_known else None
            limits = run['manifest'].get('effective_limits', {}).get(role, {})
            limit = limits.get('cpus')
            if isinstance(limit, (int, float)) and limit > 0:
                result['cpu_percent_effective_limit'] = 100 * result['cpu_cores'] / limit
            successes = run.get('metrics', {}).get('successes_completed_in_window', 0)
            if abs(observed - (end-start)) < .000001 and successes >= 30:
                result['cpu_seconds_per_success'] = cpu_delta / successes
            if role == 'gatling' and result['cpu_percent_effective_limit'] is not None and result['cpu_percent_effective_limit'] >= 95:
                issue(run, 'generator_pressure', 'CPU do gerador >=95%; possível saturação, verificar telemetria.')
        output[service] = result
    for role in ('api', 'postgres', 'gatling'):
        if not any((s.startswith('api') if role == 'api' else s == role) for s in series):
            issue(run, 'service_missing', role)
    run['resource_metrics'] = output
    run['resource_series'] = dict(series)


def analyze_run(path, config):
    run = {'run_id': path.name, 'source': str(path), 'issues': [], 'manifest': {}, 'metrics': {}, 'endpoints': {}, 'resource_metrics': {}, 'resource_series': {}}
    try:
        m = read_json(path/'manifest.json')
        run['manifest'] = m
        for key in ('run_id', 'campaign_id', 'implementation', 'scenario', 'model', 'git_revision', 'image_id', 'config_sha256', 'seed'):
            if not isinstance(m.get(key), str) or not m[key]:
                issue(run, 'manifest_field', key, True)
        if m.get('run_id') != path.name:
            issue(run, 'run_id_path', 'run_id difere do diretório', True)
        if m.get('schema_version') != 1 or isinstance(m.get('schema_version'), bool):
            issue(run, 'schema_version', 'manifest deve ter schema_version=1', True)
        for key in ('payload_bytes', 'repetition', 'warmup_seconds', 'measurement_seconds', 'drain_seconds', 'load'):
            number(m.get(key), key in ('payload_bytes', 'repetition'))
        if m['measurement_seconds'] <= 0 or m['repetition'] < 1 or m['load'] <= 0:
            raise ValueError('duração/repetição/carga deve ser positiva')
        if not isinstance(m.get('smoke'), bool):
            issue(run, 'manifest_field', 'smoke deve ser booleano', True)
        if m.get('model') not in ('open', 'closed') or m.get('scenario') not in config['scenarios'] or m.get('implementation') not in config['implementations']:
            issue(run, 'dimensions', 'modelo/cenário/implementação desconhecido', True)
        if m.get('status') not in ('complete', 'failed', 'invalid', 'running'):
            issue(run, 'status', 'status desconhecido', True)
        elif m['status'] in ('invalid', 'running'):
            issue(run, 'status', f"{m['status']}: {m.get('failure_reason', '')}", True)
        elif m['status'] == 'failed':
            issue(run, 'failed_status', m.get('failure_reason', 'falha sem classificação'), m.get('failure_kind') != 'api')
        if stamp(m['finished_at_utc']) <= stamp(m['started_at_utc']):
            issue(run, 'run_interval', 'intervalo da execução inválido', True)
        if m.get('smoke'):
            issue(run, 'smoke', 'Smoke não qualifica para ranking.')
    except (OSError, ValueError, TypeError, KeyError) as e:
        issue(run, 'manifest_invalid', str(e), True)
        return run
    start = end = None
    for name, expected_hash in m.get('artifact_sha256', {}).items():
        artifact = (path/name).resolve()
        if path.resolve() not in artifact.parents or not artifact.is_file():
            issue(run, 'artifact_integrity', f'{name}: ausente ou caminho inválido', True)
        elif hashlib.sha256(artifact.read_bytes()).hexdigest() != expected_hash:
            issue(run, 'artifact_integrity', f'{name}: SHA-256 divergente', True)
    try:
        # Legacy orchestration timestamps include Maven startup: never infer an injection window from them.
        w = m['measurement_window']
        start, end = stamp(w['started_at_utc']), stamp(w['ended_at_utc'])
        if w['source'] != 'generator' or end <= start or abs(end-start-m['measurement_seconds']) > .001:
            raise ValueError('janela precisa ter origem generator e duração configurada')
        if not (stamp(m['started_at_utc']) <= start < end <= stamp(m['finished_at_utc'])):
            raise ValueError('janela fora da execução')
        if m.get('request_export_complete') is not True:
            issue(run, 'cohort_unverified', 'request_export_complete=true ausente; completude da drenagem não comprovada', True)
    except (KeyError, ValueError, TypeError) as e:
        issue(run, 'measurement_window', f'Janela real de injeção não comprovada: {e}', True)
        start = end = None
    if m.get('measurement_wall_seconds') is not None and m['measurement_wall_seconds'] > m['measurement_seconds'] + m['drain_seconds']:
        issue(run, 'orchestration_duration', f"Parede={m['measurement_wall_seconds']:.3f}s; medição={m['measurement_seconds']}s; drenagem={m['drain_seconds']}s. Inclui overhead; não usar como janela.")
    evidence = ('dataset', 'pool', 'durability', 'effective_limits', 'postgresql', 'environment', 'http', 'warmup_policy', 'implementation_config')
    missing = [key for key in evidence if not isinstance(m.get(key), dict) or not m[key]]
    for key, fields in EVIDENCE_FIELDS.items():
        obj = m.get(key)
        if isinstance(obj, dict):
            missing += [f'{key}.{field}' for field in fields if field not in obj or obj[field] is None]
    if isinstance(m.get('effective_limits'), dict):
        for role in ('api', 'postgres', 'gatling'):
            limits = m['effective_limits'].get(role)
            if isinstance(limits, dict):
                missing += [f'effective_limits.{role}.{f}' for f in ('cpus', 'memory_bytes', 'swap_bytes') if f not in limits]
            else:
                missing.append(f'effective_limits.{role}: objeto de limites ausente')
    if missing:
        issue(run, 'equivalence_missing', ', '.join(missing))
    comparable = {k: m.get(k) for k in ('scenario', 'payload_bytes', 'model', 'load', 'warmup_seconds', 'measurement_seconds', 'drain_seconds', 'seed', 'resources', 'slo', 'config_sha256', *evidence[:-1])}
    run['dimensions'] = comparable
    run['equivalence_id'] = digest(comparable)[:16]
    run['variant_id'] = digest({k: m.get(k) for k in ('implementation', 'git_revision', 'image_id', 'implementation_config')})[:16]
    run['window'] = [start, end]
    parsed = []
    try:
        rows = read_csv(path/'requests.csv', REQUEST_FIELDS)
        if not rows:
            issue(run, 'requests_empty', 'CSV sem requisições; contagens e throughput ficam ausentes, não zero.', True)
        seen = set()
        for r in rows:
            try:
                if not r['request_id'] or r['request_id'] in seen:
                    raise ValueError('request_id ausente/duplicado')
                seen.add(r['request_id'])
                if r['phase'] not in ('warmup', 'measure', 'drain') or not r['endpoint']:
                    raise ValueError('fase/endpoint inválido')
                a, b = stamp(r['started_at_utc']), stamp(r['finished_at_utc'])
                duration = number(r['duration_ms'], True)
                if b < a or abs((b-a)*1000-duration) > 1.01:
                    raise ValueError('latência diverge dos timestamps')
                if r['success'] not in ('true', 'false'):
                    raise ValueError('success deve ser true/false')
                if r['status_http'] and not 100 <= number(r['status_http'], True) <= 599:
                    raise ValueError('status HTTP inválido')
                if start is not None:
                    if r['phase'] == 'measure' and not start <= a < end:
                        raise ValueError('requisição measure fora da coorte')
                    if start <= a < end and r['phase'] != 'measure':
                        raise ValueError('fase incompatível com coorte iniciada na janela')
                    if start <= a < end and b > end + m['drain_seconds']:
                        raise ValueError('requisição da coorte excede drenagem')
                parsed.append(dict(r, start=a, end=b, duration=duration, ok=r['success']=='true'))
            except (ValueError, TypeError) as e:
                issue(run, 'request_row', f"{r.get('request_id')}: {e}", True)
        if parsed and start is not None:
            # Warmup belongs to another simulation and never contributes, even if it ends in this window.
            measured = [r for r in parsed if r['phase'] == 'measure']
            if not measured:
                issue(run, 'measurement_empty', 'Nenhuma requisição da fase medida foi exportada.', True)
            run['metrics'] = request_metrics(measured, start, end)
            for endpoint in sorted({r['endpoint'] for r in measured}):
                run['endpoints'][endpoint] = request_metrics([r for r in measured if r['endpoint']==endpoint], start, end)
            run['metrics']['offered_per_second'] = m['load'] if m['model']=='open' else None
            run['metrics']['concurrent_users'] = m['load'] if m['model']=='closed' else None
            if m['model']=='open':
                deviation = abs(run['metrics']['started_per_second']/m['load']-1)*100
                run['metrics']['started_offered_deviation_percent'] = deviation
                if deviation > config.get('tolerances', {}).get('started_rate_percent', 5):
                    issue(run, 'offered_rate_mismatch', f'{deviation:.3f}%: possível limitação do gerador/host; capacidade não confirmada.')
    except (OSError, ValueError) as e:
        issue(run, 'requests_file', str(e), True)
    try:
        summary = read_json(path/'summary.json')
        if summary.get('schema_version') != 1 or summary.get('run_id') != m['run_id'] or summary.get('status') not in ('complete', 'failed', 'invalid'):
            issue(run, 'summary_schema', 'schema/run_id/status inválido', True)
        if summary.get('status') == 'invalid':
            issue(run, 'summary_invalid', str(summary.get('notes', [])), True)
        expected = dict(run['metrics'], measurement_duration_seconds=m['measurement_seconds'])
        for field in ('requests_started', 'successes', 'failures', 'failure_rate_percent', 'throughput_success_per_second', 'measurement_duration_seconds'):
            actual = summary.get(field)
            raw = expected.get(field)
            if field not in summary:
                issue(run, 'summary_field_missing', field)
            elif actual is not None and (not isinstance(actual, (int, float)) or isinstance(actual, bool) or not math.isfinite(actual) or raw is None or abs(actual-raw) > .000001):
                issue(run, 'summary_divergence', f'{field}: summary={actual}, bruto={raw}', True)
        for p in (50, 95, 99):
            actual = summary.get('latency_ms', {}).get(f'p{p}')
            raw = run['metrics'].get(f'success_p{p}_ms')
            if actual is not None and (not isinstance(actual, (int, float)) or raw is None or abs(actual-raw) > .000001):
                issue(run, 'summary_divergence', f'p{p}: summary={actual}, bruto={raw}', True)
        for name, values in summary.get('endpoints', {}).items():
            raw = run['endpoints'].get(name, {})
            for key in ('requests_started', 'successes', 'failures', 'failure_rate_percent', 'throughput_success_per_second'):
                actual = values.get(key)
                if actual is not None and (not isinstance(actual, (int, float)) or raw.get(key) is None or abs(actual-raw[key]) > .000001):
                    issue(run, 'summary_divergence', f'endpoint {name}, {key}: summary={actual}, bruto={raw.get(key)}', True)
    except (OSError, ValueError, TypeError, AttributeError) as e:
        issue(run, 'summary_file', str(e), True)
    try:
        resources(run, read_csv(path/'resources.csv', RESOURCE_FIELDS), start, end)
    except (OSError, ValueError, TypeError) as e:
        issue(run, 'resources_file', str(e))
    logs = sorted(path.rglob('simulation.log'))
    run['gatling_artifacts'] = [str(p.relative_to(path)) for p in path.rglob('*') if p.is_file() and ('gatling' in p.parts or p.name == 'simulation.log')]
    if not logs:
        issue(run, 'gatling_missing', 'Nenhum simulation.log preservado; não é possível cruzar eventos com Gatling.')
    for log in logs:
        if b'\0' in log.read_bytes()[:4096]:
            if m.get('request_export_status') == 'decoded_binary_3.14.9':
                if 'measure' in log.relative_to(path).parts:
                    try:
                        meta = read_json(path/'decoder.json')
                        if meta['byte_count'] != log.stat().st_size or meta['request_count'] != len(parsed) or meta['users_started'] != meta['users_ended']:
                            issue(run, 'gatling_export_divergence', 'Metadados do decoder divergem do log/CSV ou usuários não drenados.', True)
                        else:
                            issue(run, 'gatling_export_verified', f"Gatling 3.14.9: {meta['request_count']} eventos exportados; tamanho/hash do log e drenagem verificados.")
                    except (OSError, ValueError, KeyError) as e:
                        issue(run, 'gatling_export_divergence', str(e), True)
            else:
                issue(run, 'gatling_binary', f'{log.name}: binário; sem exportação verificada disponível.')
    for key in ('resource_collector_timeout', 'infrastructure_failure', 'generator_saturated'):
        if m.get(key):
            issue(run, key, str(m[key]), key != 'resource_collector_timeout')
    return run


def classify(run):
    if any(i['excludes'] for i in run['issues']):
        return 'invalid'
    if run['manifest'].get('smoke'):
        return 'smoke'
    if any(i['code']=='equivalence_missing' for i in run['issues']):
        return 'incomparable'
    if any(i['code']=='historical_config_missing' for i in run['issues']):
        return 'incomparable'
    return 'valid'


def flatten(value, prefix=''):
    result = {}
    for key, item in value.items():
        name = f'{prefix}.{key}' if prefix else key
        if isinstance(item, dict):
            result.update(flatten(item, name))
        else:
            result[name] = item
    return result


def write_csv(path, rows, defaults):
    fields = list(dict.fromkeys(defaults + sorted({k for r in rows for k in r})))
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: canonical(v) if isinstance(v, (dict, list)) else v for k, v in r.items()})


def aggregate(runs, config):
    groups = collections.defaultdict(list)
    for run in runs:
        if run['validity']=='valid':
            groups[(run['equivalence_id'], run['manifest']['implementation'], run['variant_id'])].append(run)
    out = []
    for (eq, impl, variant), items in sorted(groups.items()):
        m = items[0]['manifest']
        n = config['repetitions']
        repetitions = sorted(r['manifest']['repetition'] for r in items)
        combined = []
        for r in items:
            services = {'api' if k.startswith('api') else k: v for k, v in r['resource_metrics'].items()}
            combined.append(flatten({'overall': r['metrics'], 'endpoints': r['endpoints'], 'services': services}))
        metrics = {}
        for key in sorted({k for row in combined for k in row}):
            pairs = [{'run_id': r['run_id'], 'value': row.get(key)} for r, row in zip(items, combined)]
            values = [p['value'] for p in pairs if isinstance(p['value'], (int, float)) and not isinstance(p['value'], bool)]
            if values:
                metrics[key] = {'median': percentile(values, .5), 'q1': percentile(values, .25), 'q3': percentile(values, .75), 'n': len(values), 'individual': pairs}
        slo = m.get('slo') or config.get('slo', {})
        p95 = slo.get('p95_success_latency_ms_lte', 200)
        failure = slo.get('failure_rate_percent_lte', 1)
        tolerance = min(5, config.get('tolerances', {}).get('started_rate_percent', 5))
        passed = all(r['metrics'].get('success_p95_ms') is not None and r['metrics']['success_p95_ms'] <= p95 and r['metrics']['failure_rate_percent'] <= failure and r['metrics'].get('started_offered_deviation_percent', math.inf) <= tolerance for r in items)
        out.append({'equivalence_id': eq, 'implementation': impl, 'variant_id': variant, 'dimensions': items[0]['dimensions'],
                    'run_ids': [r['run_id'] for r in items], 'valid_repetitions': len(items), 'expected_repetitions': n,
                    'missing_repetitions': sorted(set(range(1,n+1))-set(repetitions)), 'metrics': metrics,
                    'all_repetitions_pass_slo': passed if m['model']=='open' else None,
                    'rate_passed': m['model']=='open' and repetitions==list(range(1,n+1)) and passed})
    return out


def svg_chart(title, points, x_label, y_label):
    # Points only: no interpolation across absent observations or repetitions.
    width, height = 900, 390
    esc = html.escape
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}"><rect width="100%" height="100%" fill="white"/><g font-family="sans-serif" fill="#222"><text x="30" y="25">{esc(title)}</text>']
    if not points:
        parts.append('<text x="80" y="160">Sem observações válidas disponíveis; nenhum valor foi imputado.</text>')
    else:
        xmin = min(0, min(p[0] for p in points)); xmax = max(p[0] for p in points) or 1
        ymax = max(p[1] for p in points) or 1
        for i in range(6):
            x = 80+i*650/5; y = 300-i*240/5
            parts.append(f'<path d="M{x} 300v5 M75 {y}h655" stroke="#ddd"/><text x="{x}" y="322" font-size="11">{xmin+(xmax-xmin)*i/5:.3g}</text><text x="5" y="{y}" font-size="11">{ymax*i/5:.3g}</text>')
        for x, y, series, label in points:
            color = COLORS.get(series.split('/')[0], '#444')
            parts.append(f'<circle cx="{80+(x-xmin)/(xmax-xmin)*650}" cy="{300-y/ymax*240}" r="4" fill="{color}"><title>{esc(label)}</title></circle>')
        for i, series in enumerate(sorted({p[2] for p in points})):
            parts.append(f'<text x="745" y="{60+i*17}" font-size="10" fill="{COLORS.get(series.split("/")[0], "#444")}">{esc(series)}</text>')
    parts.append(f'<text x="260" y="365">{esc(x_label)}</text><text x="80" y="48" font-size="12">{esc(y_label)}</text></g></svg>')
    return ''.join(parts)


def charts(out, comparisons, runs):
    result = []
    panels = collections.defaultdict(list)
    for c in comparisons:
        d = dict(c['dimensions']); d.pop('load', None)
        panels[digest(d)[:12]].append(c)
    if not panels:
        panels['no-data'] = []
    definitions = [('throughput', ['overall.throughput_success_per_second'], 'sucessos/s'), ('latency', ['overall.success_p95_ms', 'overall.success_p99_ms'], 'ms (sucessos)'), ('errors', ['overall.failure_rate_percent'], '% da coorte'), ('memory', [], 'bytes cgroup'), ('cpu', [], 'cores')]
    for panel, items in panels.items():
        for name, keys, unit in definitions:
            points = []
            for c in items:
                selected = keys or [k for k in c['metrics'] if k.startswith('services.') and k.endswith('.memory_median_bytes' if name=='memory' else '.cpu_cores')]
                for key in selected:
                    metric = c['metrics'].get(key)
                    if metric:
                        series = c['implementation']+'/'+key.replace('overall.', '').replace('services.', '')
                        points.append((c['dimensions']['load'], metric['median'], series, f'{series}: {metric["median"]}; runs={c["run_ids"]}'))
            title = f'{name} — {panel}'
            xlabel = 'taxa oferecida (requisições/s)' if items and items[0]['dimensions']['model']=='open' else 'usuários concorrentes (perfil fechado)'
            path = f'charts/{panel}-{name}.svg'
            (out/path).write_text(svg_chart(title, points, xlabel, unit))
            result.append(path)
    for r in runs:
        for metric, unit in [('memory_current_bytes', 'bytes cgroup'), ('cpu_usage_seconds', 'CPU acumulada (s)')]:
            points = []
            series = r['resource_series']
            times = [p['time'] for samples in series.values() for p in samples]
            origin = min(times) if times else 0
            for service, samples in series.items():
                role = 'api' if service.startswith('api') else service
                for p in samples:
                    if p[metric] is not None:
                        points.append((p['time']-origin, p[metric], role, f'{r["run_id"]}: {service}, {p["phase"]}, {p[metric]}'))
            path = f'charts/{digest(r["source"])[:12]}-{metric}.svg'
            (out/path).write_text(svg_chart(f'{r["run_id"]} — API / PostgreSQL / Gatling', points, 'segundos desde primeira amostra', unit))
            result.append(path)
    return result


def expected_missing(runs, config):
    observed = {(m.get('implementation'), m.get('scenario'), m.get('payload_bytes'), m.get('model'), m.get('load'), m.get('repetition'))
                for r in runs if r['validity']=='valid' for m in [r['manifest']]}
    missing = []
    for impl, scenario, payload, model in itertools.product(config['implementations'], config['scenarios'], config['payload_bytes'], ('open', 'closed')):
        levels = config['open_rates_per_second'] if model=='open' else config['closed_users']
        for level, rep in itertools.product(levels, range(1, config['repetitions']+1)):
            key = (impl, scenario, payload, model, level, rep)
            if key not in observed:
                missing.append(dict(zip(('implementation', 'scenario', 'payload_bytes', 'model', 'load', 'repetition'), key)))
    return missing


def conclusions(comparisons):
    lines = []
    groups = collections.defaultdict(list)
    for c in comparisons:
        groups[c['equivalence_id']].append(c)
    for eq, items in sorted(groups.items()):
        metrics = [('overall.throughput_success_per_second', True), ('overall.success_p95_ms', False), ('overall.success_p99_ms', False)]
        metrics += [(k, False) for k in sorted({k for c in items for k in c['metrics'] if k.endswith(('.idle_memory_bytes', '.memory_median_bytes', '.cpu_cores'))})]
        for metric, higher in metrics:
            candidates = [c for c in items if metric in c['metrics']]
            if len({c['implementation'] for c in candidates}) < 2:
                continue
            ranked = sorted(candidates, key=lambda c: c['metrics'][metric]['median'], reverse=higher)
            lead, baseline = ranked[0], ranked[-1]
            value, base = lead['metrics'][metric]['median'], baseline['metrics'][metric]['median']
            change = relative(value, base)
            errors = [f"{c['implementation']}: {c['metrics'].get('overall.failure_rate_percent', {}).get('median')}% falhas" for c in (lead, baseline)]
            lines.append(f"Grupo {eq}, {metric}: liderança observada {lead['implementation']}/{lead['variant_id']} ({value:.4g}); baseline {baseline['implementation']}/{baseline['variant_id']} ({base:.4g}); diferença relativa {change if change is not None else 'indefinida (baseline zero)'}%. Runs: {lead['run_ids']} versus {baseline['run_ids']}. {'; '.join(errors)}. Ver IQR antes de interpretar.")
    return lines


def report(out, campaign, runs, comparisons, config, provenance, missing):
    partial = bool(missing) or config.get('scope', {}).get('partial', False)
    chart_paths = charts(out, comparisons, runs)
    lines = [f'# Relatório {campaign} — {"PARCIAL" if partial else "completo"}', '',
             f'{len(runs)} execuções encontradas; {sum(r["validity"]=="valid" for r in runs)} válidas para comparação. {len(missing)} combinações/repetições previstas sem resultado válido.', '']
    if not comparisons:
        lines += ['**Não há medições válidas para ranking, liderança de throughput/latência/memória ou capacidade sustentada.** Os arquivos disponíveis não permitem concluir saturação nem identificar gargalos. Ausência de dados não representa desempenho zero.', '']
    if config.get('scope'):
        lines += [config['scope'].get('purpose', ''), '']
        repetitions = min((c['valid_repetitions'] for c in comparisons), default=0)
        lines += [f'Agregação desta campanha: {repetitions} repetições válidas por implementação. Os quartis descrevem a dispersão observada nessas repetições; não demonstram significância estatística nem representam outros cenários, cargas ou payloads.', '']
    lines += conclusions(comparisons)
    lines += ['', '## Evidências por execução', '', '| run_id | implementação | classificação | motivos e evidência |', '|---|---|---|---|']
    for r in runs:
        source = Path(r['source']).resolve()
        import os
        link = os.path.relpath(source, out.resolve())
        details = '; '.join(f"{i['code']}: {i['detail']}" for i in r['issues']).replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| [{r['run_id']}]({link}/manifest.json) | {r['manifest'].get('implementation', 'ausente')} | {r['validity']} | {details} |")
    lines += ['', 'Condições declaradas por execução (não equivalem a verificação do ambiente):', '', '| run_id | cenário | payload (bytes) | modelo / carga | warmup / medida / drenagem (s) |', '|---|---|---|---|---|']
    for r in runs:
        m = r['manifest']
        lines.append(f"| {r['run_id']} | {m.get('scenario', 'ausente')} | {m.get('payload_bytes', 'ausente')} | {m.get('model', 'ausente')} / {m.get('load', 'ausente')} | {m.get('warmup_seconds', 'ausente')} / {m.get('measurement_seconds', 'ausente')} / {m.get('drain_seconds', 'ausente')} |")
    lines += ['', '## Metodologia e regras', '',
              'Comparação do conjunto linguagem, runtime, servidor HTTP, driver, criptografia e configuração. Não se atribui causalidade apenas à linguagem nem se extrapola para outros ambientes.',
              'Janela de medição [início, fim), atestada pelo gerador. Throughput = sucessos concluídos na janela / duração. Latência e erros usam requisições iniciadas na janela, incluindo suas conclusões até fim + drenagem. Warmup nunca entra; requisições iniciadas na drenagem ficam fora. Exportação completa é requisito; timestamps do processo Maven não provam a janela.',
              'Percentis por interpolação linear (n−1)p. Repetições: mediana, Q1 e Q3 das métricas individuais, com n e valores rastreáveis. A mediana dos p95 por execução não é um p95 global. Não há combinação de amostras nem remoção de outliers.',
              'Memória: memory.current do cgroup, em bytes; repouso = mediana das amostras rest; carga = mediana/p95/máximo amostrado na janela. RSS, working set e pico do kernel não são inferidos. Serviços separados. CPU em cores = ΔCPU-segundos/Δtempo observado; percentual somente com limite efetivo registrado. CPU-segundos/sucesso exige cobertura exata da janela e pelo menos 30 sucessos.',
              f"SLO configurado: p95 sucessos ≤ {config.get('slo', {}).get('p95_success_latency_ms_lte', 200)} ms; falhas ≤ {config.get('slo', {}).get('failure_rate_percent_lte', 1)}%. Tolerância iniciada/oferecida ≤ {min(5, config.get('tolerances', {}).get('started_rate_percent', 5))}%. Cada grupo usa SLO histórico do manifesto quando presente; SLO diferente separa grupos.",
              'Taxa aberta passa somente com todas as repetições previstas válidas e aprovadas no SLO e na taxa iniciada. Campanha incompleta impede capacidade confirmada. Se todas as taxas passarem, apenas a maior testada passou; o ponto de saturação permanece desconhecido. Usuários concorrentes nunca são convertidos em RPS oferecida.',
              'Diferença relativa (%) = 100 × (valor − baseline) / baseline; baseline explicitado em cada comparação; zero resulta em indefinido. Lideranças empatadas não comprovam vantagem. Poucas repetições e pequenas diferenças não sustentam significância estatística.', '',
              '## Condições e variantes', '',
              'A configuração corrente é uma referência de planejamento, não prova do ambiente histórico. Os manifests existentes não registram todas as condições necessárias. Hashes de imagem/código/configuração diferentes são mantidos em variantes/grupos distintos. Sobreposição temporal sem isolamento comprovado é excluída conservadoramente.',
              'Configuração analisada: [analysis-config.json](analysis-config.json). Metadados declarados das implementações: [implementations.json](implementations.json). Revisão e SHA-256 do código, contrato, schema, configuração e cada evidência: [validation.json](validation.json).', '',
              '## Resultados agregados', '', '| grupo | implementação/variante | repetições | throughput mediano (sucessos/s) | p95 mediano (ms) | falhas medianas (%) |', '|---|---|---|---|---|---|']
    for c in comparisons:
        vals = [c['metrics'].get(k, {}).get('median', 'ausente') for k in ('overall.throughput_success_per_second','overall.success_p95_ms','overall.failure_rate_percent')]
        lines.append(f"| {c['equivalence_id']} | {c['implementation']}/{c['variant_id']} | {c['valid_repetitions']}/{c['expected_repetitions']} | {' | '.join(map(str, vals))} |")
    lines += ['', 'IQR, resultados por endpoint, memória/CPU por serviço e valores individuais: [comparison.json](comparison.json), [comparison.csv](comparison.csv), [runs.csv](runs.csv).', '', '## Capacidade, limites e gargalos', '']
    open_groups = [c for c in comparisons if c['dimensions']['model']=='open']
    if not open_groups:
        lines.append('Nenhuma taxa aberta tem evidência comparável. Não há maior taxa sustentada demonstrada.')
    for c in open_groups:
        lines.append(f"- {c['implementation']}/{c['variant_id']}, grupo {c['equivalence_id']}, {c['dimensions']['load']} req/s: {'passou todas as repetições previstas' if c['rate_passed'] else 'não demonstrada (SLO/taxa/repetições)'}. Runs: {c['run_ids']}.")
    rate_groups = collections.defaultdict(list)
    for c in open_groups:
        dims = dict(c['dimensions']); dims.pop('load', None)
        rate_groups[(c['implementation'], c['variant_id'], digest(dims)[:12])].append(c)
    for (impl, variant, group), items in rate_groups.items():
        passed = [c['dimensions']['load'] for c in items if c['rate_passed']]
        text = f'maior taxa testada aprovada: {max(passed)} req/s' if passed else 'nenhuma taxa passou todas as repetições'
        lines.append(f'{impl}/{variant}, condições {group}: {text}. Ponto de saturação não inferido.')
    if partial:
        lines.append('Campanha incompleta: nenhuma capacidade é declarada confirmada.')
    lines += ['CPU alta, throttling, erros e OOM são sinais observáveis quando disponíveis; atribuir limitação ao banco, gerador ou host exige evidência adicional. Não há vencedor geral automático. As tabelas permitem avaliar trocas de memória/CPU/latência/throughput nas mesmas condições.', '', '## Validação e limitações', '']
    lines += [f'- {key}: {value}' for key, value in CRITERIA.items()]
    lines += ['- CSV vazio não demonstra zero requisições. Resumos sem suporte bruto não são usados como medição.', '- Artefatos Gatling binários são inventariados, sem decodificação especulativa. Artefatos ausentes e campos faltantes estão listados por execução.', '- Amostras ausentes não são interpoladas; gráficos mostram pontos e unidades. Séries temporais são observações diagnósticas, inclusive de runs excluídas.', '- Fixtures de teste não entram na busca em results/.', '', '## Gráficos', '']
    for path in chart_paths:
        lines += [f'![{path}]({path})', '']
    lines += ['## Reprodução', '', '```sh', 'docker compose -f analysis/compose.yaml run --build --rm analysis', 'python3 -m unittest discover -s analysis -p "test_*.py" -v', '```', '', f"Revisão de análise: {provenance['git_revision']}; código identificado também por SHA-256, pois alterações locais podem não estar commitadas."]
    markdown = '\n'.join(lines)+'\n'
    (out/'report.md').write_text(markdown)
    # Self-contained HTML: text plus embedded SVG, no JS/fonts/CDN or external assets required.
    import re
    def inline(text):
        safe = html.escape(text)
        safe = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', safe)
        return re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', safe)
    body = []
    table = code = False
    for index, line in enumerate(lines):
        if table and not line.startswith('|'):
            body.append('</tbody></table>'); table = False
        if line.startswith('```'):
            body.append('</pre>' if code else '<pre>'); code = not code
        elif code:
            body.append(html.escape(line)+'\n')
        elif line.startswith('|'):
            if re.fullmatch(r'[| :\-]+', line):
                continue
            cells = re.split(r'(?<!\\)\|', line)[1:-1]
            tag = 'td' if table else 'th'
            if not table:
                body.append('<table><tbody>'); table = True
            body.append('<tr>'+''.join(f'<{tag}>{inline(c.strip())}</{tag}>' for c in cells)+'</tr>')
        elif line.startswith('!['):
            path = line.split('](', 1)[1][:-1]
            body.append((out/path).read_text())
        elif line.startswith('#'):
            level = len(line)-len(line.lstrip('#'))
            body.append(f'<h{level} id="s{index}">{inline(line[level:].strip())}</h{level}>')
        elif line:
            body.append(f'<p>{inline(line)}</p>')
    nav = ' · '.join(f'<a href="#s{i}">{html.escape(line[3:])}</a>' for i, line in enumerate(lines) if line.startswith('## '))
    (out/'report.html').write_text('<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Relatório '+html.escape(campaign)+'</title><style>body{font:16px system-ui;max-width:1200px;margin:2em auto;padding:0 1em;color:#222}p,td{overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #ddd;padding:.5em;text-align:left}svg{width:100%;border:1px solid #ddd;margin:1em 0}nav{position:sticky;top:0;background:white;padding:1em}a{color:#07569b}pre{overflow:auto;background:#eee;padding:1em}</style><nav>'+nav+'</nav>'+''.join(body)+'</html>')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results', type=Path, default=Path('results'))
    p.add_argument('--benchmark', type=Path, default=Path('benchmark'))
    p.add_argument('--reports', type=Path, default=Path('reports'))
    p.add_argument('--config', type=Path, help='Configuração específica da campanha')
    p.add_argument('--campaign', help='Analisar somente este diretório de campanha')
    args = p.parse_args(argv)
    if args.reports.resolve() == args.results.resolve() or args.results.resolve() in args.reports.resolve().parents or args.reports.resolve() in args.results.resolve().parents:
        p.error('relatórios e resultados precisam de diretórios separados não sobrepostos')
    config_path = args.config or args.benchmark/'config.json'
    config = read_json(config_path)
    before = inventory(args.results)
    source_hashes = inventory(Path(__file__).parent)
    source_hashes = {k: v for k, v in source_hashes.items() if '__pycache__' not in k}
    try:
        rev = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        rev = 'unavailable (see analysis_sha256)'
    provenance = {'git_revision': rev, 'analysis_sha256': source_hashes, 'benchmark_sha256': inventory(args.benchmark), 'input_sha256': before}
    campaigns = collections.defaultdict(list)
    campaigns[config['campaign_id']] = []
    for folder in sorted(args.results.iterdir()):
        if args.campaign and folder.name != args.campaign:
            continue
        if folder.is_dir():
            for path in sorted(folder.iterdir()):
                if path.is_dir():
                    run = analyze_run(path, config)
                    campaign = run['manifest'].get('campaign_id', folder.name)
                    if not isinstance(campaign, str) or not campaign or '/' in campaign or campaign in ('.', '..'):
                        campaign = folder.name
                        issue(run, 'campaign_id', 'identificador inseguro/inválido', True)
                    campaigns[campaign].append(run)
                    if run['manifest'].get('config_sha256') and run['manifest']['config_sha256'] != hashlib.sha256(config_path.read_bytes()).hexdigest():
                        issue(run, 'historical_config_missing', 'Hash da configuração histórica difere da disponível; matriz/repetições históricas não comprovadas.')
                    if folder.name != campaign:
                        issue(run, 'campaign_directory', f'diretório={folder.name}; manifest={campaign}; agrupado pelo manifesto')
    all_runs = [r for runs in campaigns.values() for r in runs]
    for r in all_runs:
        if sum(x['manifest'].get('run_id', x['run_id']) == r['manifest'].get('run_id', r['run_id']) for x in all_runs) > 1:
            issue(r, 'duplicate_run_id', r['run_id'], True)
        for other in all_runs:
            if other is r:
                continue
            try:
                a,b = r['manifest'],other['manifest']
                if max(stamp(a['started_at_utc']), stamp(b['started_at_utc'])) < min(stamp(a['finished_at_utc']), stamp(b['finished_at_utc'])):
                    host_a = a.get('environment', {}).get('host_id')
                    host_b = b.get('environment', {}).get('host_id')
                    if not host_a or not host_b or host_a == host_b:
                        issue(r, 'concurrent_run', other['run_id'], True)
            except (KeyError, ValueError, TypeError):
                pass
        r['validity'] = classify(r)
    for campaign, runs in campaigns.items():
        # Duplicate repetition slots within a variant are not extra independent repetitions.
        slots = collections.defaultdict(list)
        for r in runs:
            if r['validity']=='valid':
                slots[(r.get('equivalence_id'), r.get('variant_id'), r['manifest']['repetition'])].append(r)
        for items in slots.values():
            if len(items)>1:
                for r in items:
                    issue(r, 'duplicate_repetition', 'mais de uma execução para o mesmo slot/variante', True)
                    r['validity'] = classify(r)
        comparisons = aggregate(runs, config)
        missing = expected_missing(runs, config)
        out = args.reports/campaign
        (out/'charts').mkdir(parents=True, exist_ok=True)
        for stale in (out/'charts').glob('*.svg'):
            stale.unlink()
        validation = {'campaign_id': campaign, 'partial': bool(missing) or config.get('scope', {}).get('partial', False), 'criteria': CRITERIA, 'provenance': provenance,
                      'missing_repetitions': missing, 'runs': [{k: v for k, v in r.items() if k!='resource_series'} for r in runs],
                      'input_unchanged': before==inventory(args.results),
                      'variants': {impl: sorted({r.get('variant_id', 'unknown') for r in runs if r['manifest'].get('implementation')==impl}) for impl in config['implementations']}}
        (out/'comparison.json').write_text(json.dumps(comparisons, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
        write_csv(out/'comparison.csv', [flatten(c) for c in comparisons], ['implementation','equivalence_id','variant_id','valid_repetitions'])
        write_csv(out/'runs.csv', [flatten({k:v for k,v in r.items() if k!='resource_series'}) for r in runs], ['run_id','validity','source'])
        (out/'analysis-config.json').write_text(json.dumps(config, indent=2)+'\n')
        impls = {f.stem: read_json(f) for f in sorted((args.benchmark/'implementations').glob('*.json'))}
        (out/'implementations.json').write_text(json.dumps(impls, indent=2, ensure_ascii=False)+'\n')
        report(out, campaign, runs, comparisons, config, provenance, missing)
        (out/'validation.json').write_text(json.dumps(validation, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
        print(f'{out}: {len(runs)} runs, {len({c["equivalence_id"] for c in comparisons})} grupos equivalentes, {len(comparisons)} variantes/agregados, {len(missing)} repetições faltantes')
    if before != inventory(args.results):
        raise RuntimeError('dados de entrada mudaram durante a análise')


if __name__ == '__main__':
    main()
