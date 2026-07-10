"""Relatorio PDF por municipio. Formatacao sobria (apreciacao por TCE/TCU)."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..config.constants import (
    FAIXA_ESTADUAL,
    FAIXA_FEDERAL,
    FAIXA_LOCAL,
    FAIXA_ONCOLOGICO,
)

_AZUL = colors.HexColor("#1F3864")

_FAIXA_LABEL = {
    FAIXA_FEDERAL: "Federal - Uniao 100%",
    FAIXA_ESTADUAL: "Estadual - Uniao 65%",
    FAIXA_ONCOLOGICO: "Oncologico - Uniao 80%",
    FAIXA_LOCAL: "Local - sem ressarcimento",
}


def _brl(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def gerar_pdf(metricas: Dict, destino: Path, municipio_nome: str = "") -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], textColor=_AZUL, fontSize=15)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=_AZUL, fontSize=12)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, textColor=colors.grey)

    doc = SimpleDocTemplate(
        str(destino), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title="Monitoramento de Judicializacao de Medicamentos",
    )
    el = []
    el.append(Paragraph("Monitoramento de Judicializacao de Medicamentos", h1))
    el.append(Paragraph(
        f"Municipio: <b>{municipio_nome or metricas['municipio_ibge']}</b> "
        f"({metricas.get('uf') or '-'}) &nbsp;|&nbsp; Comarca: {metricas.get('comarca') or '-'}",
        styles["Normal"],
    ))
    el.append(Paragraph("Enquadramento: Tema 1.234/STF (Sumulas Vinculantes 60 e 61)", small))
    el.append(Spacer(1, 0.5 * cm))

    resumo = [
        ["Indicador", "Valor"],
        ["Numero de processos", str(metricas["n_processos"])],
        ["Valor total (causa)", _brl(metricas["valor_total_causa"])],
        ["Dinheiro na mesa (ressarcivel pela Uniao)", _brl(metricas["valor_total_ressarcivel"])],
        ["Tempo medio de tramitacao (dias)",
         str(metricas["tempo_medio_tramitacao_dias"] or "-")],
    ]
    t = Table(resumo, colWidths=[10 * cm, 6 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
        ("TEXTCOLOR", (1, 3), (1, 3), colors.HexColor("#006100")),
        ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"),
    ]))
    el.append(t)
    el.append(Spacer(1, 0.6 * cm))

    el.append(Paragraph("Distribuicao por faixa (Tema 1.234)", h2))
    faixa_rows = [["Faixa", "Qtd"]]
    for faixa, label in _FAIXA_LABEL.items():
        faixa_rows.append([label, str(metricas["distribuicao_faixa"].get(faixa, 0))])
    tf = Table(faixa_rows, colWidths=[12 * cm, 4 * cm])
    tf.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
    ]))
    el.append(tf)
    el.append(Spacer(1, 0.6 * cm))

    if metricas["top_cids"]:
        el.append(Paragraph("Top CIDs", h2))
        cid_rows = [["CID", "Ocorrencias"]] + [[c, str(n)] for c, n in metricas["top_cids"]]
        tc = Table(cid_rows, colWidths=[12 * cm, 4 * cm])
        tc.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _AZUL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ]))
        el.append(tc)

    el.append(Spacer(1, 0.8 * cm))
    el.append(Paragraph(
        "Estimativas geradas a partir de metadados processuais publicos (DataJud/CNJ). "
        "Nao contem dados pessoais identificaveis (LGPD).", small,
    ))

    doc.build(el)
    return destino
