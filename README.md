# Monitor de Judicializacao de Medicamentos de Alto Custo

Sistema orientado a **municipio** que monitora, classifica e alerta sobre novas
acoes judiciais de medicamentos, com enquadramento automatico nas faixas de
ressarcimento do **Tema 1.234/STF** — identificando oportunidades de
ressarcimento pela Uniao para secretarias municipais e estaduais de saude.

> G3 Health Service Ltda (CNPJ 31.652.744/0001-14) — produto interno.

## O que faz

1. **Ingestao incremental** da API publica do **DataJud (CNJ)** por tribunal,
   filtrando por assuntos de saude (TPU/CNJ), com paginacao `search_after` e
   checkpoint por tribunal.
2. **Fronteira LGPD**: `sanitize()` descarta qualquer PII **antes** de persistir
   (whitelist de metadados publicos). Nada de nome/CPF/dado clinico e gravado.
3. **Classificacao Tema 1.234**: estima o custo anual, calcula a faixa, o % que
   a Uniao custeia/ressarce e o **valor potencial de ressarcimento**.
4. **Deduplicacao e historico** de movimentacoes; execucao **idempotente**.
5. **Alertas** (log estruturado JSON + e-mail SMTP opcional; Slack/WhatsApp stub).
6. **Relatorios** XLSX e PDF por municipio ("dinheiro na mesa"), formatacao sobria.

## Regras do Tema 1.234 (parametros, nao inferidas)

Transito em julgado 07/03/2025; Sumulas Vinculantes 60 e 61. Limiares derivados
de `SALARIO_MINIMO` (configuravel):

| Custo anual do tratamento | Competencia | Uniao |
|---|---|---|
| ≥ 210 salarios minimos | Justica Federal | custeia **100%** |
| 7 a 210 salarios minimos | Justica Estadual | ressarce **65%** (FNS→FES/FMS) |
| Medicamentos **oncologicos** | — | ressarce **80%** |
| < 7 salarios minimos | custeio local | **sem** ressarcimento federal |

Com `SALARIO_MINIMO = R$ 1.518,00`: limiar estadual = **R$ 10.626,00/ano**,
limiar federal = **R$ 318.780,00/ano**.

**Precedencia do oncologico** (confirmada com o cliente): os 80% sobrepoem a
faixa, mas nunca reduzem a parte da Uniao abaixo do percentual da propria faixa
(ex.: federal permanece 100%; estadual sobe de 65% para 80%). Ver
`src/classify/tema1234.py`.

## Requisitos

- Python 3.11+ recomendado (o codigo roda em 3.9+ para dev/teste).
- Dependencias em `pyproject.toml`.

## Instalacao

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"        # ou: pip install httpx pydantic pydantic-settings sqlmodel PyYAML APScheduler openpyxl reportlab python-dotenv pytest pytest-asyncio
cp .env.example .env           # preencha DATAJUD_API_KEY
```

## Chave publica do DataJud (CNJ)

A API publica do DataJud exige um header `Authorization: APIKey {CHAVE}`. A
**chave publica** e disponibilizada pelo CNJ na Wiki do DataJud
(<https://datajud-wiki.cnj.jus.br/api-publica/acesso>). Passos:

1. Acesse a Wiki do DataJud → secao **API Publica → Acesso**.
2. Copie a chave publica (`APIKey ...`) publicada pelo CNJ.
3. Cole **apenas o valor** (sem o prefixo `APIKey `) em `.env`:
   ```
   DATAJUD_API_KEY=coloque_a_chave_aqui
   ```

> A chave nunca deve ser versionada. `.env` esta no `.gitignore`.

## Escopo geografico

Edite `config/municipios.yaml` (nome, codigo IBGE, UF, tribunal). O piloto vem
com Sao Paulo, Campinas e Guarulhos (TJSP). Escale ate os 5.570 municipios
acrescentando entradas e os tribunais correspondentes (`tjsp`, `tjrj`, `trf3`…).

## Uso

```bash
# criar o banco
python -m src.cli init-db

# ingestao incremental (requer DATAJUD_API_KEY)
python -m src.cli run --tribunal tjsp

# relatorio de um municipio (XLSX + PDF)
python -m src.cli report --ibge 3550308
```

### Online (em producao)

- **Site:** <https://g3healthservice.github.io/monitor-judicializacao/> (acesso por convite)
- **Automacao:** GitHub Actions a cada 4h (`.github/workflows/atualizar.yml`)
- **Escopo:** 27 TJs (Brasil inteiro) em `config/tribunais.yaml`; agrupa por municipio
  via tabela IBGE embutida.

**Passo unico para ativar os dados** — cadastrar a chave publica do DataJud como
Secret (nunca vai para o site):

```bash
# via CLI (pede o valor de forma segura, sem eco):
gh secret set DATAJUD_API_KEY --repo g3healthservice/monitor-judicializacao
# depois, dispara o primeiro processamento (senao aguarda o cron de 4h):
gh workflow run atualizar.yml --repo g3healthservice/monitor-judicializacao
```

Ou pela UI: **Settings -> Secrets and variables -> Actions -> New repository secret**.

### Dashboard online (padrao Raio-X)

Gera um HTML **autocontido** (dados embutidos, sem back-end) com trava de acesso
por convite (G3 Access Gate, tool code `jd`), pronto para GitHub Pages:

```bash
python -m src.cli dashboard        # gera out/dashboard.html
```

Publicacao automatica (recomendado): o workflow `.github/workflows/atualizar.yml`
roda em cron, ingere o DataJud (chave em **GitHub Secret** `DATAJUD_API_KEY`,
nunca no site), regenera o dashboard e faz commit em `docs/` (GitHub Pages).
Publicacao local alternativa: `./publicar.sh`.

> A chave do DataJud jamais entra no HTML publicado — o site so recebe dados
> agregados e sem PII. Links de acesso sao gerados pelo mesmo gerador de convites
> dos demais dashboards G3.

### Demonstracao sem chave de API

Roda o pipeline completo com uma resposta simulada do DataJud e gera relatorio
real de Sao Paulo **e o dashboard** em `out/`:

```bash
python -m scripts.demo
```

## Testes

```bash
pytest -q
```

Cobrem: `sanitize()`/LGPD, classificacao Tema 1.234 (todas as faixas + oncologico),
query builder, mapper (CID/oncologico/municipio) e pipeline end-to-end
(idempotencia + nao-persistencia de PII), com DataJud mockado via `httpx.MockTransport`.

## LGPD por design

- O sistema trabalha apenas com **metadados processuais publicos** e dados
  **agregados**. A base publica do DataJud e, por construcao, metadata-only.
- `src/privacy/sanitize.py` aplica uma **whitelist**: apenas campos publicos
  passam; `detectar_pii()` sinaliza (e o pipeline descarta) qualquer campo
  sensivel que a API venha a devolver, para falharmos de forma visivel em vez de
  vazar. Testado em `tests/test_sanitize.py`.
- Nenhuma coluna do banco (`src/store/models.py`) armazena nome, CPF ou dado
  clinico de pessoa identificavel.

## Arquitetura

```
src/
  config/     # settings (.env) + constants (Tema 1.234, ASSUNTOS_SAUDE) + logging JSON
  ingest/     # cliente DataJud (async, retry/backoff, search_after), query, mapper
  privacy/    # sanitize() + deteccao de PII (fronteira LGPD)
  classify/   # regras Tema 1.234 (faixa, %, valor ressarcivel)
  enrich/     # conectores CMED / e-NatJus / JudSaude (stubs plugaveis)
  store/      # modelos SQLModel + repositorios (upsert idempotente, checkpoints)
  alert/      # log estruturado + e-mail SMTP; Slack/WhatsApp stubs
  report/     # agregacao + XLSX (openpyxl) + PDF (reportlab)
  scheduler/  # jobs periodicos (APScheduler)
  pipeline.py # orquestracao end-to-end
  cli.py      # init-db | run | report
tests/
```

## Gotchas conhecidos

- **DataJud publico e metadata-only**: nao traz partes/CPF (otimo p/ LGPD) mas
  tambem **nao traz valor da causa de forma confiavel**. Por isso a estimativa de
  custo em producao depende sobretudo do conector **CMED/PMVG** (stub na v1);
  `valor_causa` e usado como proxy quando presente (`origem_estimativa`).
- **Rate limit** do DataJud: cliente ja faz retry com backoff + `Retry-After`.
- **IBGE 6 vs 7 digitos**: o mapper tolera ambos ao casar municipio.

## Roadmap

- Ativar conector CMED/PMVG (custo real via posologia × preco).
- `scheduler/` com APScheduler (execucao periodica).
- Dashboard fase 2: HTML estatico autocontido com mapa municipal (padrao Raio-X).
- Caminho PostgreSQL (trocar `DATABASE_URL`).
