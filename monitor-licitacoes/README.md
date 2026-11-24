# Monitor de licitações de medicamentos sem registro

Projeto separado do monitor de judicialização. Implementa as **etapas 1 (monitor e
filtro), 2 (resultados por item), 3 (ficha e viabilidade) e 4 (simulador e
alertas)** da especificação "Especificação do agente de
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

## Ficha e nota de viabilidade (etapa 3)

Cada item das candidatas, e cada item que você cotar, ganha uma ficha com
identificação, quantidade, valor de referência, regras de elegibilidade (os sinais
do edital), resultado, prazos, **custo desembarcado** e o **FOB máximo em rupias** que
cabe na referência. A fórmula é a da planilha "Prévia de custos: importação (Índia)
× nacional":

```
custo   = FOB × câmbio × (1 + frete) × (1 + II + desembaraço) × (1 − desconto)
FOB máx = referência / (câmbio × (1 + frete) × (1 + II + desembaraço) × (1 − desconto))
```

As premissas (câmbio 0,055 R$/₹, frete 6%, II 0%, desembaraço 2%, desconto 0%) e os
pesos da nota ficam em [`licitacoes/premissas.yaml`](licitacoes/premissas.yaml).

**Cotações.** O PNCP não traz preço indiano nem fabricante, e o valor estimado da
DLOG costuma ser sigiloso. Esses dados ficam em [`cotacoes.csv`](cotacoes.csv), uma
linha por item (`contratacao` = identificador como `250005-709-2026`, `item` = número
do item). Colunas: `preco_inr`, `referencia_brl` e `referencia_tipo` (edital ou
mercado), `menor_oferta_brl`, `fabricantes_ich_oms`, `capacidade_fornecedor`,
`prazo_manual` (ex.: prazo de diligência, `2026-11-23T12:00`), `fonte`, `data` e
`observacao`. O arquivo já vem com as linhas da planilha.

**Nota de 0 a 100**, com os pesos ajustáveis:

| Critério | Peso | Como pontua |
|---|---|---|
| Admissão de produto sem registro | 30 | 100 com cláusula expressa; 60 só ICH/OMS; 40 só histórico de fracasso |
| Margem estimada | 30 | 0 com margem ≤ −10%, 100 com margem ≥ 30%, linear entre os dois |
| Disponibilidade de fornecedor | 15 | 0, 1, 2 ou 3+ fabricantes ICH/OMS cotados = 0, 50, 80, 100 |
| Prazo | 15 | dias até o fim das propostas ou o prazo informado; 100 a partir de 15 dias |
| Escala | 10 | 100 até 1× a capacidade típica; 0 a partir de 4× |

Critério sem dado conta como neutro (50), e a ficha mostra a **cobertura**: quanto do
peso total veio de dado real. A lista sai ordenada, com o motivo de cada nota. A
decisão de participar continua sendo sua.

## Simulador de cenários (etapa 4)

`simular` calcula, para cada item com preço indiano em `cotacoes.csv`, o custo
desembarcado, a margem e o FOB máximo em cada cenário de `premissas.yaml`, e marca
os cenários em que o custo fica acima da referência:

| Cenário | O que muda |
|---|---|
| Base | premissas atuais |
| Câmbio +10% | câmbio × 1,10 |
| Imposto de importação 10% / 18% | II = 10% ou 18% |
| Frete refrigerado 12% | frete + seguro = 12% |
| Desconto de 5% por volume | desconto do fabricante = 5% |

Os cenários são editáveis: acrescente um bloco em `cenarios:` com o que muda.

## Alertas (etapa 4)

A cada execução, `alertas` junta numa única mensagem o que é novo desde a anterior:

1. **edital novo** com sinais de produto sem registro (candidata com propostas
   abertas ou publicada nos últimos 7 dias);
2. **item acompanhado fracassado, deserto ou cancelado**, e as demais mudanças de
   situação, vencedor e ata;
3. **prazo a menos de 48 horas**: fim das propostas, ou o `prazo_manual` de
   `cotacoes.csv` (diligência);
4. **mudança no preço de referência** de um item no PNCP.

"Acompanhados" são as contratações candidatas e as que têm item em `cotacoes.csv`.
Cada alerta sai uma vez só. O e-mail mostra prazos e itens sem sucesso em vermelho
e traz a tabela de cenários dos itens cotados citados.

**Canais.** E-mail pelas mesmas variáveis do monitor de judicialização (`SMTP_HOST`,
`SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `ALERT_EMAIL_TO`) e Telegram
opcional (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). No GitHub, o workflow já usa o
Gmail (`smtp.gmail.com`, porta 587) e só precisa de três Secrets em *Settings → Secrets
and variables → Actions*: `SMTP_USER` (a conta Gmail), `SMTP_PASSWORD` (a senha de app de
https://myaccount.google.com/apppasswords) e `ALERT_EMAIL_TO` (quem recebe). Sem nenhum canal configurado, os alertas
ficam pendentes no banco e saem no primeiro envio depois da configuração (prazos já
vencidos são descartados). Veja [`.env.example`](.env.example).

## Painel

O workflow publica todo dia um painel em
https://g3healthservice.github.io/monitor-judicializacao/licitacoes/ com prazos abertos,
candidatas, trechos do edital, situação por item, vencedores e mudanças dos últimos 30
dias. A página é pública e por isso traz **só dados do PNCP**: preços indianos, custos,
margens, cenários e observações de `cotacoes.csv` ficam de fora.

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
python -m licitacoes.cli viabilidade                   # etapa 3: lista ordenada + CSV em out/
python -m licitacoes.cli viabilidade --abertas         # só itens com prazo aberto
python -m licitacoes.cli ficha 709 1                   # ficha completa de um item
python -m licitacoes.cli simular                       # etapa 4: cenários + CSV em out/
python -m licitacoes.cli simular 709 1                 # cenários de um item
python -m licitacoes.cli painel                        # painel HTML público em docs/licitacoes/
python -m licitacoes.cli alertas --previa              # o que seria enviado, sem enviar
python -m licitacoes.cli alertas --teste               # mensagem de teste pelos canais
python -m licitacoes.cli alertas                       # gera e envia os alertas novos
python -m licitacoes.cli reavaliar                     # após mudar sinais.yaml (sem rede)
```

Variáveis de ambiente (todas opcionais): `LIC_DB_PATH` (padrão `monitor.db`),
`LIC_UASGS` (padrão `250005`, aceita lista separada por vírgula), `LIC_CNPJ_ORGAO`,
`LIC_MODALIDADES` (padrão `6`, pregão eletrônico), `LIC_LOOKBACK_DIAS`,
`LIC_SAIDA_DIR`, `LIC_SINAIS_PATH`, `LIC_PREMISSAS_PATH`, `LIC_COTACOES_PATH`.

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
