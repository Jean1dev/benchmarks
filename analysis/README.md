# Análise reproduzível

Execute na raiz, sem subir APIs ou carga:

```sh
mkdir -p reports
docker compose -f analysis/compose.yaml run --build --rm analysis
python3 -m unittest discover -s analysis -p 'test_*.py' -v
```

Compose independente evita interpolar credenciais ou iniciar serviços da carga.
Docker rootless usa UID/GID 0 no namespace do container (padrão). Em Docker
rootful, use `ANALYSIS_UID=$(id -u) ANALYSIS_GID=$(id -g)` antes do comando
para gerar arquivos com seu usuário.
Limites configuráveis: `ANALYSIS_CPUS=2.0`, `ANALYSIS_MEMORY=2g`; swap desabilitada.
Sem dependências externas: biblioteca padrão, runtime Python 3.14.7 fixado na imagem.
Alternativa local: `python3 analysis/report.py`. Argumentos: `--results`, `--benchmark`,
`--reports`. Não escreva relatórios dentro de results. Configuração, contrato,
implementações e schema são inventariados com SHA-256; entrada é conferida novamente
após processamento. A imagem não inclui Git; o hash do código é a identificação
exata da análise, inclusive quando a revisão Git não está disponível.

Saída: `reports/<campaign_id>/`. A identidade da campanha vem do manifesto;
divergências com o diretório são informadas. A campanha configurada recebe relatório
mesmo sem nenhuma execução. Fixtures só são criadas em diretórios temporários de teste.
JSON usa null e CSV usa campo vazio para ausência. Relatório HTML inclui SVG e CSS,
sem rede; links para evidências exigem manter reports e results no mesmo layout.

## Evidência necessária para comparar futuras execuções

Os manifestos legados registram a duração do subprocesso Gatling, que inclui
inicialização. O analisador não inventa a janela de injeção. Além dos campos v1,
reconhece as seguintes evidências opcionais; sua ausência impede comparação,
mas não impede a geração do diagnóstico:

- `measurement_window`: objeto com `started_at_utc`, `ended_at_utc`, `source: "generator"`.
  Janela UTC real, duração igual a `measurement_seconds`.
- `request_export_complete: true`: atestado de que todas as requisições iniciadas
  foram exportadas, inclusive falhas e conclusões até o fim da drenagem.
  Fase `measure` designa a coorte iniciada na janela, inclusive conclusões na drenagem.
- Objetos históricos não vazios: `dataset` (tamanho, seed, identidade/hash e estado
  inicial), `pool`, `durability`, `effective_limits` (por api/postgres/gatling,
  `cpus`, memória e swap), `postgresql` (versão/imagem/configuração), `environment`
  (host_id, SO/kernel, hardware, Docker, isolamento), `http`, `warmup_policy`
  (incluindo volume/contagem e estado de cache), `implementation_config`
  (runtime/servidor/driver/cripto/flags). Igualdade exata dos objetos comuns define
  grupos comparáveis; implementação/configuração e imagem definem variantes.
- `failure_kind: "api"` permite manter status failed como resultado observado,
  quando os dados estão completos. `infrastructure_failure` ou
  `generator_saturated` invalidam rankings; OOM/reinícios da API são reportados,
  preservando resultados quando a exportação estiver completa.
- `artifact_sha256` pode mapear caminhos relativos de artefatos para hashes esperados.

A configuração corrente define a matriz esperada e repetições; mudanças de hash
são apontadas e não justificam preencher fatos históricos ausentes. Limitações:
não decodifica Gatling binário, não infere picos kernel/RSS, não faz teste de
significância e não confirma gargalos por utilização isolada. Quantidades de timeout
incluem apenas falhas cuja classe identifica timeout; demais classes permanecem
explicitadas. CPU/sucesso requer cobertura integral e 30 sucessos (regra conservadora).

Os nomes obrigatórios de cada objeto de evidência estão em `EVIDENCE_FIELDS`
em report.py. `effective_limits` usa `cpus`, `memory_bytes`, `swap_bytes`
por serviço. Uma configuração histórica com hash diferente da disponível
permanece incomparável até disponibilizar a configuração correspondente.
