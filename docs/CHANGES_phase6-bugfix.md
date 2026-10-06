# Correção dos bugs da Fase 6 (P0) — registro de mudanças

> Base: `main` @ `49fb13b`. Nada foi commitado nem enviado ao GitHub.
> Validação: 57 testes do simulador, 22 da API, 7 da ingestão, e 15 testes dbt
> executados contra Postgres 16 real, alimentados pelo runner e pelo loader reais.

## 1. Achado → correção → teste que trava a regressão

| ID | Problema | Correção | Teste |
|---|---|---|---|
| C1 | Determinístico em carga nominal | `load_scale` (padrão 0,25 = ponto crítico), registrado em `StudyConditions` | `test_golden_numbers_match_doc06`, `test_critical_point_lowers_hosting_capacity` |
| C2 | ±10% EN 50160 usado sem nome nem natureza | `FRAMEWORKS` nomeados (`ansi_c84_range_a`, `prodist_m8_bt`, `en50160_envelope`) + `CriterionKind`. **Padrão da `cigre_lv` mantido** (decisão com a orientadora) | `test_hc_limits_scope.py` |
| C3 | Sem checagem de baseline → bisseção inválida | `require_feasible_baseline` em todos os métodos → `BaselineInfeasibleError` | `test_prodist_at_nominal_load_raises_baseline_infeasible`, `test_prodist_baseline_violation_is_detected` |
| C4 | Monte Carlo media o orçamento de amostragem | Novo `estimate_hosting_capacity_stochastic`: λ* por cenário de adoção, HC = F⁻¹(α), IC por estatística de ordem binomial. Antigo `run_monte_carlo` emite `DeprecationWarning` | `test_hc_stochastic.py` |
| C5 | Teste QSTS falso positivo (janela noturna, λ≈10⁶) | `NoDaylightError` | `test_night_only_window_raises_no_daylight` |
| C6 | QSTS reamostrava ruído a cada candidato λ | `TimeSeries` imutável, amostrada uma vez | `test_series_is_sampled_once_so_runs_are_identical` |
| C10 | Ranking misturava unidades | Excedência relativa ao próprio limite | `test_ranking_uses_relative_exceedance_not_mixed_units` |
| C11 | Slack, geradores e barras MT checados em tensão | `voltage_scope_buses` (+ `voltage_levels_kv=(0.4,)` na `cigre_lv`) | `test_cigre_scope_is_lv_only`, `test_case14_slack_is_never_reported_as_a_violation` |
| C12 | QSTS escalava P mas não Q | P e Q escalados juntos | (coberto pelo QSTS) |
| N1 | Teto de expansões devolvido como HC | `bounded=False`, `lambda_fail=None` | `test_unbounded_search_is_flagged*` |
| N2 | Funções mutavam a `net` do chamador | `deepcopy` interno em todos os métodos | `test_caller_network_is_not_mutated` (det. e QSTS) |
| N3 | Mart, API e Grafana exibiam o estimador inválido | Payload v2, `is_comparable`, `total_pv_mw_comparable` NULL para linhas inválidas/legadas, API com `comparable_only=True` por padrão | `api/tests/test_hosting_capacity.py`, 3 testes dbt novos |
| N4 | Resultado sem procedência | `provenance.py`: commit, dirty, versões de bibliotecas, em cada linha | `test_payload_v2_envelope_and_provenance` |
| N5 | Sem CI | `.github/workflows/tests.yml` (simulador, API, ingestão, dbt contra Postgres) | — |
| — | Limites Python × seed dbt podiam divergir | Teste de consistência entre camadas | `test_dbt_seed_matches_python_default_limits` |

Melhoria de custo, sem mudar a resposta: o QSTS pula os passos noturnos durante a busca. A justificativa está no docstring: com o baseline limpo e PV = 0, o estado noturno é igual ao baseline.

## 2. Números reproduzidos (`cigre_lv`, determinístico)

| Critério | Carga | λ | MW | Restrição |
|---|---|---|---|---|
| EN 50160 envelope | ×1,0 | 2,289 | 1,572 | trafo_0 (número antigo, reproduzido) |
| EN 50160 envelope | ×0,25 | 1,586 | 1,089 | trafo_0 |
| PRODIST M8 BT | ×0,25 | 1,164 | 0,799 | sobretensão, barra 16 |
| PRODIST M8 BT | ×1,0 | — | — | `baseline_infeasible` (barra 35 = 0,912 pu) |

Os quatro números batem com o doc 06.

## 3. Achados novos durante a implementação

1. **O case14 é inviável no baseline sob ANSI Range A**, mesmo com a slack excluída. As barras de carga 6, 8, 9, 11 e 12 ficam em cerca de 1,055–1,062 pu por causa dos setpoints dos geradores do caso. Qualquer estudo de HC no case14 agora falha explicitamente, quando antes devolvia um número sem sentido.
2. **O estocástico difere do doc 06.** O p10 sob PRODIST deu λ ≈ 0,45 a 0,46, contra 0,562 no doc 06. A diferença vem do modelo de adoção: eu não tinha o código do zip, e a dispersão de tamanho de 0,5 é uma premissa minha. A direção do achado se mantém (o estocástico fica abaixo do determinístico), mas **o número depende do `AdoptionModel` e exige análise de sensibilidade antes de ser citado**.
3. **O IC do quantil exige n suficiente.** Com n = 20 e α = 0,10, o limite inferior não existe (0,9²⁰ > 0,025). O código devolve `None` e não inventa um limite. Para p10 com 95% de confiança, use n ≥ 36; recomendo n ≥ 60.
4. **O QSTS sob PRODIST é inviável no baseline** no pico de carga ao meio-dia do perfil sintético. É uma consequência direta do C9 e fica resolvida pelo S2 (dados reais).
5. **dbt-postgres e pandapower exigem versões incompatíveis de `deepdiff`.** Eles precisam de ambientes separados, e o CI já faz isso.

## 4. Mudanças de interface (breaking)

- **Argumentos keyword-only.** `find_hosting_capacity_*` aceita `framework=` e `load_scale=` só por nome. Os resultados ganharam os campos `lambda_fail`, `bounded` e `conditions`.
- **Default de carga do determinístico.** Ele agora roda em `load_scale=0.25`. Para obter o número antigo, passe `load_scale=1.0`.
- **Estimador estocástico.** O runner não escreve mais o estimador antigo, e as flags `--mc-max-pv-mw-per-bus`, `--mc-n-trials` e `--mc-include-trials` foram removidas.
- **Payload v2.** O staging lê v1 e v2, e as linhas v1 viram `legacy_invalid`.
- **Nome dos Parquet.** Os arquivos ganharam um sufixo aleatório, para evitar que duas escritas no mesmo milissegundo se sobrescrevam.
- **API.** `/compare` retorna só linhas comparáveis por padrão. Para auditoria, use `comparable_only=false`.
- **Testes.** `simulator/tests/test_hosting_capacity.py` foi substituído por `test_hc_*.py`.

## 5. Ainda aberto (fora do P0, por decisão)

| Item | Onde será resolvido |
|---|---|
| C7: critério de duração τ̄ no QSTS | S1 |
| C8: controle de tap | S1 |
| C9: perfil de carga com pico ao meio-dia | S2 |
| C13: métricas VVSI/OSI/RPFI | S1 (o fluxo reverso já é reportado) |
| `DEFAULT_CRITICAL_LOAD_SCALE = 0,25` | Premissa até a Fase 8 |
| Faixa PRODIST 0,92–1,05 | Verificar contra a revisão vigente do Módulo 8 |
| N7: scripts soltos na raiz e doc 05 desatualizado | Higiene; a Issue #1 do doc 05 já estava resolvida na `main` |

## 6. Como validar localmente

```bash
pip install -e "simulator[dev,fast]"
make test-hc                       # testes de HC (~40 s)
make test                          # simulador + ingestão + API
make up && make hc-study           # estudo PRODIST
python ingestion/load_hosting_capacity_to_timescale.py --results-dir data/hosting_capacity -v
make dbt-run && make dbt-test
```

## 7. Sugestão de commits

1. `fix(hc): named voltage frameworks, voltage scope, relative-exceedance ranking` (limits, scope, violations)
2. `fix(hc): critical operating point, baseline gate, shared bisection, no input mutation` (conditions, baseline, search, errors, allocation, deterministic)
3. `fix(hc): stochastic estimator as distribution of critical penetration` (stochastic, quantiles)
4. `fix(hc): QSTS fixed series, daylight guard, P+Q scaling` (timeseries, qsts)
5. `feat(hc): payload v2 with provenance; status rows` (runner, provenance)
6. `fix(pipeline): comparable-only mart/API/dashboard` (dbt, api, grafana)
7. `ci: GitHub Actions for simulator, api, ingestion, dbt`
8. `docs: changelog and README`
