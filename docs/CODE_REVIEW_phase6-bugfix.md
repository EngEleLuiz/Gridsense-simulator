# Code Review — Fase 6 pós-bugfix (`main` @ `0063514`)

> **Data:** 06/10/2026 · **Escopo:** todo o diff `49fb13b..0063514` (42 arquivos) + os arquivos que ele toca indiretamente (loader, Silver, Grafana, compose).
> **Método:** leitura linha a linha **e** execução. Nenhum achado abaixo é só opinião de leitura: cada um tem um comando ou número medido que o reproduz.
> **Ambiente de verificação:** Python 3.12 e 3.10, pandapower 3.5.5, numpy 2.4, dbt-core 1.12.5, PostgreSQL 16 real.

---

## 0. Resumo executivo

O núcleo científico do bugfix está **correto e bem feito**. As 12 correções C/N do `CHANGES_phase6-bugfix.md` estão de fato no código, os números-ouro do doc 06 se reproduzem, a premissa de monotonicidade da bisseção foi verificada empiricamente, e a cadeia simulador → Bronze → TimescaleDB → dbt → API funciona de ponta a ponta (dbt 53/53, API servindo dados reais).

O que está quebrado é **a entrega**, não a física: o merge foi feito pela metade.

| Severidade | Qtde | Resumo |
|---|---|---|
| 🔴 Bloqueador | 3 | README com conflitos commitados; Makefile deletado; teste legado deixa o CI vermelho |
| 🟠 Alta | 4 | Barra isolada (NaN) passa como "sem violação"; estocástico sem IC marcado como comparável; dashboard mistura pontos de operação; Silver e Python julgam barras diferentes |
| 🟡 Média | 7 | Runner perde resultados quando um método falha; validação tardia; efeitos colaterais do `TimeSeries`; custo do QSTS; parâmetros de busca divergentes; loader sem testes; porta do Grafana |
| 🔵 Baixa | 6 | Schema errado no README, deprecação do dbt, proveniência, seed sem `unique`, scripts soltos, histórico git |

**Estado do CI hoje:** o workflow que está no `main` (`.github/workflows/tests.yml`) **falharia** no job `simulator` (1 falha, R03). O CI novo entregue junto com este review também falha hoje, de propósito, nos itens R01–R03, e passa assim que eles forem corrigidos (ver §7).

---

## 1. O que foi verificado e está correto

Antes dos problemas, o que **não** precisa ser refeito. Isso importa para a banca: cada item tem evidência.

| Item do changelog | Verificação | Status |
|---|---|---|
| C1 ponto crítico (`load_scale`) | `test_golden_numbers_match_doc06`: λ 2,289 (×1,0) → 1,586 (×0,25), EN 50160 | ✅ |
| C2 frameworks nomeados | `limits.py`: `FRAMEWORKS` + `CriterionKind`; default da `cigre_lv` preservado | ✅ |
| C3 gate de baseline | PRODIST ×1,0 → `BaselineInfeasibleError`, barra 35 = 0,912 pu | ✅ |
| C4 novo estimador estocástico | λ* por cenário, HC = F⁻¹(α); p10 PRODIST medido = **0,4375** (n = 40), IC [0,281; 0,531] | ✅ (bate com os 0,45–0,46 do changelog) |
| C5 janela noturna | `NoDaylightError` em [0, 24) a 288 passos/dia | ✅ |
| C6 série amostrada uma vez | `TimeSeries` imutável; duas execuções idênticas | ✅ |
| C10 ranking por excedência relativa | 1,12 pu supera trafo a 100,5% | ✅ |
| C11 escopo de tensão | slack, geradores e barras MT fora (no **Python**; ver R07) | ✅ parcial |
| C12 Q escalado junto com P | `_set_step` | ✅ |
| N1 busca sem limite sinalizada | `bounded=False`, `lambda_fail=None` | ✅ |
| N2 sem mutação do `net` | `deepcopy` nos 3 métodos | ✅ |
| N3 pipeline só comparável | `is_comparable`, API com `comparable_only=True` | ✅ parcial (ver R05/R06) |
| N4 proveniência | commit, dirty, versões em cada linha | ✅ |
| Matemática do IC de quantil | Conferi as duas desigualdades binomiais e a cobertura exata contra a derivação; o caso n = 20, α = 0,10 sem limite inferior está correto (0,9²⁰ = 0,122 > 0,025) | ✅ |
| **Premissa de monotonicidade** | Varredura λ ∈ [0; 3] em passos de 0,02, PRODIST ×0,25, determinístico + 6 cenários estocásticos: **exatamente 1 transição** viável→inviável em todos | ✅ |
| JSON seguro | `allow_nan=False` + `_finite()` impedem `Infinity` no jsonb | ✅ |
| SQL da API | parâmetros nomeados; testado contra banco real | ✅ |
| Python 3.10 | suíte roda igual ao 3.12 (o `pyproject` declara `>=3.10`) | ✅ |
| Cobertura | pacote `hosting_capacity`: 90–100% por módulo; total 90% | ✅ |

---

## 2. 🔴 Bloqueadores (consertar antes de qualquer outra coisa)

### R01 — README commitado com 5 blocos de conflito de merge

**Onde:** `README.md`, linhas 224–235, 251–267, 303–306, 313–317, 322–327.
**Evidência:** `git grep -nE '^(<<<<<<<|>>>>>>>)( |$)'` acha os marcadores. É o README que aparece na página do repositório no GitHub.
**Causa:** o `git merge phase-6-hosting-capacity` parou em conflito; depois, `git add .` + commit gravou o arquivo com os marcadores.
**Correção:** substituir pelo `README.md` entregue junto com este review (que resolve os conflitos a favor do lado `phase-6-hosting-capacity` e atualiza o conteúdo).
**Prevenção:** o job `hygiene` do CI novo bloqueia qualquer commit com marcadores.

### R02 — `Makefile` deletado

**Onde:** commit `faa0a3b` ("phase 6 bugfixes") registra `delete mode 100644 Makefile`.
**Impacto:** `README.md` (linhas 150–157, 302–307, 333, 343), `transform/README.md` (85–91), `api/README.md` (54) e o próprio `docs/CHANGES_phase6-bugfix.md` (74–78) mandam rodar `make ...`. Nenhum desses comandos funciona. Além disso, `make test-hc` e `make hc-study`, citados no changelog, **nunca existiram** nem no Makefile antigo.
**Correção:** `Makefile` restaurado e entregue, com os alvos antigos mais `test-hc`, `hc-study`, `hc-qsts`, `hc-load`, `dbt-seed`, `dbt-build`, `lint` e `help`. Também corrige um bug antigo: `dbt-run` não rodava `dbt seed`, então um banco novo ficava com `network_v_min_pu` NULL em toda a Silver. Verificado: `make test-hc` → 47 passam.

### R03 — Teste legado não removido deixa o CI vermelho

**Onde:** `simulator/tests/test_hosting_capacity.py`.
**Evidência:**
```
FAILED simulator/tests/test_hosting_capacity.py::test_qsts_hosting_capacity_short_horizon_finds_a_positive_lambda
NoDaylightError: Time window [0, 24) at 288 steps/day has no daylight
1 failed, 64 passed
```
O changelog diz que esse arquivo "foi substituído por `test_hc_*.py`", mas ele continua no repositório. O teste que falha é exatamente o falso positivo C5: a correção funciona, e o teste antigo que dependia do bug agora quebra. O arquivo também tem um import não usado (`NetworkLimits`, F401).
**Correção:** `git rm simulator/tests/test_hosting_capacity.py`. Os outros 7 testes dele já têm equivalentes nos `test_hc_*.py`. Depois disso, a suíte fica com **57 testes**, exatamente o número do changelog.

---

## 3. 🟠 Alta prioridade (o resultado científico pode sair errado sem aviso)

### R04 — Barra isolada com tensão `NaN` é julgada como "sem violação"

**Onde:** `violations.py`, `assess_violations()`, linhas 121–126.
**Evidência:**
```python
n.line.at[last_line, "in_service"] = False      # isola o fim de um ramal
check_violations(n, limits_for("cigre_lv"))
# NaN buses: [43]  in scope: [43]  has_violation: False
# barra 43 alimenta 'Load C20' (7,2 kW) — carga não atendida, julgada "ok"
```
**Por quê:** `nan < v_min` e `nan > v_max` são ambos `False`. O mesmo vale para `loading_percent` de linhas fora de serviço (o pandapower 3.x devolve `NaN`).
**Impacto hoje:** latente, porque os estimadores não abrem linhas. Mas `scenarios.py` já faz N-1/N-2, e qualquer estudo de HC sob contingência (pauta natural da Fase 8/9) passaria a aprovar cargas desenergizadas em silêncio. É exatamente o tipo de "número errado sem aviso" que o pacote de erros tipados foi criado para impedir.
**Correção sugerida:**
```python
import math
vm = net.res_bus["vm_pu"].reindex(list(scope))
isolated = [int(b) for b, v in vm.items() if not math.isfinite(v)]
voltage_violations = {
    int(b): float(v) for b, v in vm.items()
    if math.isfinite(v) and (v < limits.v_min_pu or v > limits.v_max_pu)
}
# ViolationReport ganha `isolated_buses: list[int]`;
# has_violation inclui `bool(isolated)`;
# ranked_violations() põe isolated no topo: (math.inf, f"isolated@bus_{b}")
```
**Teste de regressão:** abrir `net.line.index[-1]` na `cigre_lv` e exigir `has_violation is True` e `binding_constraint().startswith("isolated@bus_43")`.

### R05 — Estocástico sem intervalo de confiança aparece como "comparável"

**Onde:** `stg_hosting_capacity_results.sql`, linhas 87–90.
**Evidência:** depois do job dbt do CI antigo (`--mc-n-scenarios 8`), o mart contém:
```
 method     | is_comparable | lam   | n_censored | ci_lo
 stochastic | t             | 0.281 | 0          | (null)
```
Com n = 8, o p10 é simplesmente o **menor** dos 8 valores, e o IC inferior não existe. O changelog avisa (n ≥ 36), mas nada no pipeline impõe isso. O número vai para a API e para o dashboard com o mesmo selo de um estudo com n = 60.
**Correção sugerida** (staging):
```sql
select
    *,
    (schema_version >= 2 and status = 'ok' and is_bounded
     and (method <> 'stochastic'
          or (hc_lambda_ci_low is not null and hc_lambda_ci_high is not null))
    ) as is_comparable
from typed
```
(e `total_pv_mw_comparable` usando o mesmo predicado; vale extrair um CTE `gated`). Acrescentar o teste singular `assert_stochastic_comparable_rows_have_ci.sql`.
**Observação:** o CI novo já roda o estocástico com n = 40 (14 s medidos) para continuar verde depois dessa correção.

### R06 — Dashboard "latest run" pode pôr pontos de operação diferentes lado a lado

**Onde:** `grafana/dashboards/gridsense-hosting-capacity.json`, os 3 painéis.
**Problema:** `DISTINCT ON (method, criterion_framework)` ignora `load_scale` e `hc_alpha`. Se o último determinístico rodou com `--load-scale 1.0` e o último estocástico com 0,25, o painel "Comparable Number" mostra os dois como comparáveis. Ele também mistura `run_id` diferentes, e o QSTS (carga variável, sem `load_scale`) aparece ao lado de snapshots a ×0,25.
**Correção mínima:** `DISTINCT ON (method, criterion_framework, load_scale, hc_alpha)` e `load_scale`/`hc_alpha` como colunas visíveis.
**Correção melhor:** uma variável de dashboard `$run_id` (default = o mais recente) e filtrar `run_id = '$run_id'`, de modo que uma tela mostre **um** estudo.

### R07 — Silver e Python julgam conjuntos de barras diferentes

**Onde:** `transform/models/silver/fct_bus_voltage.sql` vs `hosting_capacity/scope.py`.
**Evidência** (Postgres real, telemetria do simulador):
```
 network | bus_id | v      | flagged
 case14  | 0      | 1.0600 | t   ← slack: o Python exclui (C11)
 case14  | 5      | 1.0700 | t   ← gerador: o Python exclui
 case14  | 7      | 1.0900 | t   ← gerador: o Python exclui
```
A Silver aplica a faixa a **todas** as barras; o Python exclui slack, geradores e (na `cigre_lv`) as barras de 20 kV. O dashboard "GridSense Overview" e o `mart_voltage_quality_hourly` mostram taxa de violação inflada para o case14, que é exatamente o bug que a Issue #1 queria eliminar, só que agora no eixo do escopo e não da faixa.
**Por que o teste de consistência não pegou:** `test_hc_cross_layer.py` compara só `v_min`/`v_max`.
**Correção sugerida:** um seed `network_voltage_scope.csv` (`network, bus_id`), **gerado** a partir de `voltage_scope_buses()` por um script, unido na Silver para setar `is_in_scope`. Estender `test_hc_cross_layer.py` para regenerar o seed em memória e compará-lo com o arquivo commitado.

---

## 4. 🟡 Média prioridade

### R08 — Runner descarta resultados já calculados quando um método falha

**Onde:** `scripts/run_hosting_capacity_study.py`, `build_records()`: só `HostingCapacityError` é capturada, e o Parquet só é escrito no fim de `main()`.
**Evidência:**
```
build_records(..., methods=["deterministic","qsts"], qsts_kwargs={"total_steps": 0})
→ ValueError: total_steps must be >= 1.   (o determinístico, já calculado, é perdido)
```
Num estudo real, um QSTS que falha depois de 1 h leva junto o determinístico e o estocástico.
**Correção:** capturar `Exception` como segundo `except`, gravar `status="error"` (já aceito pelo dbt) com `f"{type(exc).__name__}: {exc}"`, chamar `logger.exception(...)` e fazer `main()` sair com código 1 se alguma linha tiver `status == "error"`. Assim o erro fica visível e nada se perde.

### R09 — Argumentos inválidos só falham depois do trabalho caro

**Evidência:** `--mc-alpha 1.5` → `ValueError: q must be in (0, 1)` **depois** de rodar o Monte Carlo inteiro.
**Correção:** validar em `parse_args()` (`0 < mc_alpha < 1`, `mc_n_scenarios ≥ 1`, `0 < adoption_min ≤ adoption_max ≤ 1`, `qsts_total_steps ≥ 1`, `load_scale > 0`) com `parser.error(...)`. Bônus: avisar quando `mc_n_scenarios < ceil(log(0.025)/log(1-alpha))`, o n mínimo para existir o limite inferior do IC.

### R10 — `TimeSeries` tem dois efeitos colaterais

**Onde:** `timeseries.py`, linhas 23, 40–53.
**Evidência:**
```
a = np.ones(4); TimeSeries(a, b, 4)
a.flags.writeable  → False          # o array do CHAMADOR virou somente-leitura
TimeSeries(...) == TimeSeries(...)  → ValueError: truth value of an array is ambiguous
hash(TimeSeries(...))               → TypeError: unhashable type: 'numpy.ndarray'
```
`np.asarray` não copia arrays que já são `float64`, e `@dataclass(frozen=True)` gera `__eq__`/`__hash__` que não funcionam com ndarray.
**Correção:** `np.array(self.load_mult, dtype=float, copy=True)` (idem `pv_mult`) e `@dataclass(frozen=True, eq=False)`.

### R11 — QSTS padrão custa ~1,5 h e o README subestima de outro jeito

**Medido:** fluxo de potência na `cigre_lv` = 36 ms (sem numba). Série padrão de 60 dias = 17.280 passos de baseline + ~8.700 passos diurnos × ~14 candidatos ≈ **139 mil fluxos ≈ 87 min**. O README fala em "~350k". A Issue #10 (rodar os 60 dias) continua aberta por causa disso.
**Otimização que não muda a resposta:** dentro de `_violates`, percorrer os passos diurnos em ordem decrescente de "risco" (maior `pv_mult`, menor `load_mult`) em vez da ordem cronológica. Um candidato inviável sai no primeiro ou segundo passo; só os viáveis pagam a série inteira. O critério "algum passo viola" não depende da ordem; o `first_violating_step` reportado precisaria ser recalculado cronologicamente uma única vez no `lambda_fail`.

### R12 — Parâmetros de busca divergem entre os métodos

| | `max_expansions` | `max_bisections` |
|---|---|---|
| determinístico | 20 | 40 |
| estocástico | 12 | 60 (default de `bisect_max_feasible`) |
| QSTS | 20 | 20 |

Com `tolerance = 0,01` isso não muda nenhum número medido. Mas o limiar de censura difere (2¹² vs 2¹⁹), e o `__init__.py` afirma que a bisseção é "a mesma" nos três, o que é a base do argumento de comparação **controlada**. Recomendo uma constante única `SEARCH_DEFAULTS` em `search.py`. Também vale expor `resolved` nos resultados: hoje um colchete não resolvido (que bateu no `max_bisections`) não é sinalizado em nenhum lugar.

### R13 — Loader de HC sem testes e com URL hardcoded

`ingestion/load_hosting_capacity_to_timescale.py` não mudou neste merge e não tem nenhum teste (os 7 testes da ingestão são todos do `bronze_consumer`). O default de `--db-url` é literal, ao contrário do padrão por variável de ambiente adotado no resto do projeto (mesmo padrão da Issue #13). A docstring chama a URL de "SQLAlchemy-style", mas `psycopg2` espera uma URI libpq. O job `e2e` do CI novo cobre o loader contra banco real; um teste unitário com `psycopg2` mockado ainda vale a pena.

### R14 — Porta do Grafana mudou sem documentação

O merge `0063514` trocou `"3000:3000"` por `"3001:3000"` no `docker-compose.yml`, mas README, Makefile antigo e `grafana/README.md` dizem `localhost:3000`. Parece um ajuste local (porta 3000 ocupada no Windows) que entrou no commit. O README e o Makefile novos usam 3001; decida qual vale e alinhe o `grafana/README.md`. Uma alternativa é `"${GRAFANA_PORT:-3000}:3000"` com `GRAFANA_PORT` no `.env.example`.

---

## 5. 🔵 Baixa prioridade / higiene

| # | Achado | Correção |
|---|---|---|
| R15 | README, seção Verify: `SELECT * FROM gold.mart_hosting_capacity`. O schema real é **`public_gold`** (profile `public` + `+schema: gold`); a API já usa `public_gold`. | Corrigido no README novo |
| R16 | dbt 1.10+ emite `MissingArgumentsPropertyInGenericTestDeprecation`: `accepted_values: values:` deve ir para dentro de `arguments:` em `_staging.yml`. Vai virar erro numa versão futura. | Mover `values` para `arguments` |
| R17 | `provenance.py` usa `git status --porcelain`, que conta arquivos **não rastreados** como "dirty" (um `.coverage` qualquer marca o estudo como sujo). | Trade-off: `--untracked-files=no` reduz falso positivo, mas deixa passar um módulo novo não commitado. Documente a escolha. |
| R18 | Seed `network_voltage_limits.csv` sem teste `unique` em `network`: uma linha duplicada duplicaria silenciosamente a Silver inteira. | `seeds/_seeds.yml` com `unique` + `not_null` |
| R19 | `qsts.py` importa `numpy` sem usar (F401); scripts soltos na raiz (`check*.py`, `ingestion_query.py`, `query_bronze.py`; já registrado como N7). | `ruff check --fix`; mover os scripts para `scripts/debug/` |
| R20 | Histórico: o bugfix entrou como um commit monolítico (`faa0a3b`, 42 arquivos), não nos 8 commits sugeridos no changelog, e o merge `0063514` tem mensagem de feature ("add baseline, conditions...") em vez de "Merge ...". | Não reescreva o `main` agora. Daqui para frente: branch + PR, e o CI novo como check obrigatório (§7). |

---

## 6. Limitações científicas que continuam abertas (não são bugs)

Ficam aqui para que o Cap. 5 não as descubra depois:

- **C7** QSTS sem critério de duração τ̄ (Eq. 3.20–3.21). Enquanto isso não existir, o EN 50160 aplicado instantaneamente continua sendo um erro de categoria assumido.
- **C8** sem controle de tap.
- **C9** perfil de carga com pico ao meio-dia. Consequência prática medida: **não existe hoje nenhum número de QSTS sob PRODIST** (baseline inviável em todos os passos de meio-dia). O único QSTS comparável é sob EN 50160, ou seja, `mart_hosting_capacity` compara QSTS(EN 50160) com determinístico/estocástico(PRODIST) dentro do mesmo `run_id` a menos que o consumidor filtre por framework.
- **C13** VVSI/OSI/RPFI não implementados.
- `DEFAULT_CRITICAL_LOAD_SCALE = 0,25` e o `AdoptionModel` (30–100%, dispersão 0,5) são premissas; o p10 estocástico é sensível a elas.
- `is_comparable` não olha `criterion_kind`. Uma linha `statistical` aplicada instantaneamente passa como comparável. É uma decisão consciente (default da `cigre_lv` preservado), mas vale um aviso explícito no dashboard.

---

## 7. Como aplicar (ordem recomendada)

**Passo 1 — destravar o CI (R01, R02, R03, R19).** No `cmd` do Windows, dentro da pasta do repositório:
```bat
git checkout main
git pull origin main
git checkout -b fix/post-merge-cleanup

:: copie para dentro do repositório os arquivos entregues:
::   README.md, Makefile, .github\workflows\tests.yml, .github\dependabot.yml,
::   scripts\ci\jsonl_to_bronze.py, scripts\ci\assert_e2e.py,
::   docs\CODE_REVIEW_phase6-bugfix.md

git rm simulator/tests/test_hosting_capacity.py
python -m ruff check --select F401 --fix simulator/gridsense_sim/hosting_capacity/qsts.py
git add -A
git status
git commit -m "fix: resolve README merge conflict, restore Makefile, drop legacy HC test, add full CI"
git push -u origin fix/post-merge-cleanup
```
Abra o PR no GitHub e confira se o check **ci-ok** fica verde. Só então faça o merge.

**Passo 2 — proteger o `main`.** GitHub → Settings → Branches → Add rule para `main`: "Require a pull request before merging" e "Require status checks to pass" com o check `ci-ok`. Isso impede que R01–R03 se repitam.

**Passo 3 — um PR por grupo de correção:** (a) R04 + R10; (b) R05 + R06; (c) R07; (d) R08 + R09; (e) R11 + R12. Cada um com o teste de regressão descrito acima, para o CI travar o bug.

---

## 8. Como cada achado foi reproduzido

| Achado | Comando / evidência |
|---|---|
| R01 | `git grep -nE '^(<<<<<<<\|>>>>>>>)( \|$)'` |
| R02 | `git show --stat faa0a3b \| grep Makefile` |
| R03 | `python -m pytest simulator/tests -q` → 1 failed, 64 passed |
| R04 | abrir `net.line.index[-1]` na `cigre_lv` → barra 43 NaN, `has_violation=False` |
| R05 | runner com `--mc-n-scenarios 8` → loader → `dbt build` → `select ... from public_gold.mart_hosting_capacity` |
| R07 | telemetria `case14` → Bronze → dbt → `public_silver.fct_bus_voltage` barras 0/5/7 |
| R08–R09 | `build_records(...)` com `total_steps=0` e com `stochastic_alpha=1.5` |
| R10 | `TimeSeries(a, b, 4); a.flags.writeable` |
| R11 | `pp.runpp` médio de 30 execuções; contagem de passos de `build_synthetic_series(17280, 288)` |
| Monotonicidade | varredura λ ∈ [0; 3], Δλ = 0,02, 7 alocações, contagem de transições |
| Ponta a ponta | simulador (case14 + cigre_lv) → `scripts/ci/jsonl_to_bronze.py` → loader → runner HC → loader HC → `dbt build` (53/53) → `scripts/ci/assert_e2e.py` (7/7) → API real |
