# Monitor de licitações de medicamentos sem registro

Projeto separado do monitor de judicialização. Implementa as **etapas 1 (monitor e
filtro) e 2 (resultados por item)** da especificação "Especificação do agente de
monitoramento de licitações de medicamentos sem registro".

Todo dia ele:

1. busca no **PNCP** as contratações da **UASG 250005 (DLOG/Ministério da Saúde)**
   publicadas ou atualizadas;
2. separa as que são de medicamento (NCM do capítulo 30 ou descrição CATMAT de
   medicamento);
3. baixa o edital, o termo de referência e os anexos (PDF, ZIP ou RAR), lê o texto e
   procura os **sinais** de que a demanda aceita produto sem registro na Anvisa;
4. soma a pontuação e marca as **candidatas**, guardando para cada sinal a frase
   exata, o documento, a página, o endereço de origem e a data da leitura.

Ele não decide participar, não fixa preço e não envia nada a órgão público.

## Banco de dados

Usa o **mesmo `monitor.db`** do monitor de judicialização, em tabelas próprias com
prefixo `lic_`:

| Tabela | Conteúdo |
|---|---|
| `lic_compra` | uma linha por contratação: número da compra (ex.: 709), número do pregão (ex.: 90114/2026), objeto, prazos, pontuação, candidata |
| `lic_item` | itens: descrição, quantidade, unidade, NCM, valor estimado (quando não sigiloso) |
| `lic_arquivo` | documentos lidos: título, URL, páginas, hash, erro de leitura |
| `lic_sinal` | cada sinal achado, com trecho exato, página, URL e data |
| `lic_checkpoint` | até que data a coleta incremental já foi |
| `lic_resultado` | etapa 2: vencedor de cada item, valor e quantidade homologados, país |
| `lic_ata` | etapa 2: atas de registro de preços e vigência |
| `lic_evento` | etapa 2: mudanças detectadas (situação do item, vencedor novo, ata nova) |

**Número da compra e número do pregão.** O PNCP registra a contratação pelo número
do Compras.gov.br (ex.: `250005-709-2026`), e o pregão (ex.: `90114/2026`) aparece
no objeto e no nome do edital. O PE 709/2026 e o Pregão 90114/2026 são a mesma
contratação no PNCP. O agente guarda os dois números, e `ver 709` e `ver 90114`
devolvem o mesmo resultado.

## Regras de sinal

Ficam em [`licitacoes/sinais.yaml`](licitacoes/sinais.yaml) e podem ser mudadas sem
mexer no código. Cada sinal conta uma vez por contratação:

| Sinal | Peso |
|---|---|
| Admite expressamente produto sem registro na Anvisa | alto (3) |
| Cita importação excepcional, RDC 203/2017 ou RDC 488/2021 | alto (3) |
| Exige registro em país ICH ou pré-qualificação OMS | médio (2) |
| Permite empresa estrangeira ou nacional | médio (2) |
| Cita licença de importação, DUIMP ou desembaraço | médio (2) |
| Item anterior fracassado, deserto ou cancelado (etapa 2) | alto (3) |
| Medicamento de demanda judicial (CGLJUD) | baixo (1) |

Uma contratação vira **candidata** com `pontuacao >= 6` **e** pelo menos um sinal
alto. Sem a exigência de um sinal alto, quase todo edital da DLOG passaria do
mínimo, porque as cláusulas de empresa estrangeira e de importação são padrão.
Depois de mudar pesos ou limiar, `reavaliar` recalcula tudo sem baixar nada de novo.

O sinal "item anterior fracassado, deserto ou cancelado" não vem do texto: o agente
compara o **princípio ativo** de cada item com os itens de contratações anteriores já
coletadas. O CATMAT não vem preenchido no PNCP para esses itens, então a comparação
usa o nome do princípio ativo tirado da descrição, sem sal e sem concentração
("Trazodona cloridrato" e "TRAZODONA CONCENTRAÇÃO: 50" viram `trazodona`). Isso
significa que um fracasso da dabigatrana 75 mg conta para uma compra de dabigatrana
110 mg. Nomes genéricos ("Concentrado de Fator de Coagulação", "Insulina") só entram
quando a descrição traz o tipo (fator IX, NPH), para não juntar produtos diferentes.
O histórico cobre só o que já foi coletado.

**Atenção:** um sinal é uma frase encontrada no texto e não interpreta o contexto.
Por exemplo, "não será aceito produto sem registro" também marca o sinal. Por isso
cada sinal vem com o trecho, para você conferir sem reler o edital.

## Resultado por item (etapa 2)

A cada execução, `acompanhar` relê no PNCP os itens das contratações de medicamento
que ainda não têm resultado final, sem reler os editais. Para cada item ele grava:

| Fase | Quando |
|---|---|
| Propostas abertas | situação "Em andamento" e prazo de propostas ainda aberto |
| Em julgamento (aceitação, habilitação ou diligência) | "Em andamento" depois do fim do prazo |
| Homologado | com vencedor, valor unitário, quantidade e ata |
| Fracassado / Deserto / Anulado-revogado-cancelado | situação oficial do PNCP |

**Limite:** o PNCP não publica em que ponto da sessão o item está. A diligência
fica dentro de "Em julgamento" e não aparece separada.

Mudanças entre uma leitura e a seguinte viram eventos em `lic_evento`, a base dos
alertas da etapa 4. Na primeira leitura de uma contratação o agente só registra o
estado atual, sem gerar eventos, para a carga inicial não virar uma enxurrada de avisos.

## Uso

```bash
pip install ./monitor-licitacoes          # na raiz do repositório
sudo apt-get install poppler-utils libarchive-tools   # opcional: pdftotext e .rar

python -m licitacoes.cli init-db
python -m licitacoes.cli coletar                       # incremental (checkpoint)
python -m licitacoes.cli coletar --desde 2026-01-01 --por publicacao   # carga inicial
python -m licitacoes.cli candidatas                    # lista + CSV/Markdown em out/
python -m licitacoes.cli candidatas --abertas          # só com prazo de proposta aberto
python -m licitacoes.cli ver 90114                     # uma contratação (709, 90114, 90114/2026)
python -m licitacoes.cli acompanhar                    # etapa 2: situação, vencedor, ata
python -m licitacoes.cli resultados                    # itens das candidatas + CSV em out/
python -m licitacoes.cli resultados 90114              # itens de uma contratação
python -m licitacoes.cli eventos --desde 2026-11-01    # mudanças detectadas
python -m licitacoes.cli reavaliar                     # após mudar sinais.yaml (sem rede)
```

Variáveis de ambiente (todas opcionais): `LIC_DB_PATH` (padrão `monitor.db`),
`LIC_UASGS` (padrão `250005`, aceita lista separada por vírgula), `LIC_CNPJ_ORGAO`,
`LIC_MODALIDADES` (padrão `6`, pregão eletrônico), `LIC_LOOKBACK_DIAS`,
`LIC_SAIDA_DIR`, `LIC_SINAIS_PATH`.

## Agendamento

O workflow [`.github/workflows/licitacoes.yml`](../.github/workflows/licitacoes.yml)
roda todo dia às 06:17 (Brasília): coleta, acompanha os resultados, grava o
`monitor.db` e publica em `monitor-licitacoes/saida/` a lista de candidatas, a situação
por item e as mudanças das últimas 24 horas, que também aparecem na página da execução. Usa o mesmo
grupo de concorrência do workflow de judicialização, para que os dois nunca gravem
o banco ao mesmo tempo.

## Testes

```bash
cd monitor-licitacoes && python -m pytest -q
```

Os testes usam o texto real do Edital PE SRP 90114/2026 (item 9.27) e simulam o
PNCP, então rodam sem rede.

## Próximas etapas

3. Ficha por item e nota de viabilidade.
4. Simulador de cenários e alertas.
