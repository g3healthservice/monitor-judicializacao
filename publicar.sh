#!/bin/bash
# Publicacao LOCAL do dashboard no GitHub Pages.
# O site e servido pela pasta docs/ DESTE repo (mesma origem do GitHub Actions).
# Uso:
#   ./publicar.sh            # ingere DataJud, coleta oncologia SUS, regenera e publica
#   ./publicar.sh --skip-gen # so republica o out/dashboard.html ja gerado
#
# Pre-requisitos:
#   - .env com DATAJUD_API_KEY preenchido (para a ingestao; sem ele o painel fica vazio)
set -e
cd "$(dirname "$0")"

if [ "$1" != "--skip-gen" ]; then
  echo "==> Ingestao incremental (DataJud)..."
  ./.venv/bin/python -m src.cli init-db
  ./.venv/bin/python -m src.cli run
  echo "==> Coleta oncologia SUS (CONASEMS) p/ o cruzamento..."
  ./.venv/bin/python -m src.enrich.conasems_onco || echo "   (coleta CONASEMS pulada — segue sem cruzamento)"
fi

echo "==> Gerando dashboard..."
./.venv/bin/python -m src.cli dashboard

echo "==> Publicando em docs/ (GitHub Pages)..."
cp out/dashboard.html docs/index.html
git add -A docs/index.html
if git diff --cached --quiet; then echo "Nada mudou."; exit 0; fi
git commit -q -m "Publicacao dashboard $(date '+%Y-%m-%d %H:%M')"
git push
echo "==> Publicado. Site: https://g3healthservice.github.io/monitor-judicializacao/"
