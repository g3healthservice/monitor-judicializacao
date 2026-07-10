#!/bin/bash
# Publicacao LOCAL do dashboard no GitHub Pages (alternativa ao GitHub Actions).
# Uso:
#   ./publicar.sh            # ingere DataJud, regenera o dashboard e publica
#   ./publicar.sh --skip-gen # so republica o out/dashboard.html ja gerado
#
# Pre-requisitos:
#   - .env com DATAJUD_API_KEY preenchido
#   - repo do site clonado em $SITE (GitHub Pages), com Pages apontando p/ a raiz
set -e
cd "$(dirname "$0")"

SITE="${G3_JUD_SITE:-$HOME/Downloads/judicializacao-site}"

if [ "$1" != "--skip-gen" ]; then
  echo "==> Ingestao incremental (DataJud)..."
  ./.venv/bin/python -m src.cli init-db
  ./.venv/bin/python -m src.cli run
fi

echo "==> Gerando dashboard..."
./.venv/bin/python -m src.cli dashboard

if [ ! -d "$SITE/.git" ]; then
  echo "ERRO: $SITE nao e um repositorio git. Clone o repo do GitHub Pages ali,"
  echo "      ou defina G3_JUD_SITE apontando para o clone."
  exit 1
fi

cp out/dashboard.html "$SITE/index.html"
cd "$SITE"
git add -A
if git diff --cached --quiet; then echo "Nada mudou."; exit 0; fi
git commit -q -m "Publicacao $(date '+%Y-%m-%d %H:%M')"
git push
echo "==> Publicado."
