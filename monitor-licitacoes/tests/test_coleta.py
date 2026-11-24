"""Coleta ponta a ponta com um cliente PNCP falso (sem rede)."""
from pathlib import Path

from licitacoes import db, relatorio
from licitacoes.coleta import Coletor
from licitacoes.config import Config
from licitacoes.sinais import carregar_regras

from .test_sinais import EDITAL_90114, OBJETO_709

REGRAS = carregar_regras(Path(__file__).resolve().parents[1] / "licitacoes" / "sinais.yaml")


def _pdf(texto: str) -> bytes:
    """PDF minimo de uma pagina com o texto (sem dependencias externas)."""
    linhas = [l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for l in texto.splitlines()]
    corpo = "BT /F1 8 Tf 20 800 Td 10 TL " + " ".join(f"({l}) '" for l in linhas) + " ET"
    stream = corpo.encode("latin-1", errors="replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 850] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


REG_709 = {
    "numeroControlePNCP": "00394544000185-1-002092/2026",
    "orgaoEntidade": {"cnpj": "00394544000185"},
    "unidadeOrgao": {"codigoUnidade": "250005", "nomeUnidade": "DEPARTAMENTO DE LOGÍSTICA EM SAUDE"},
    "anoCompra": 2026, "sequencialCompra": 2092, "numeroCompra": "709",
    "modalidadeNome": "Pregão - Eletrônico", "objetoCompra": OBJETO_709,
    "situacaoCompraNome": "Divulgada no PNCP", "processo": "25000.154587/2025-63", "srp": True,
    "dataPublicacaoPncp": "2026-09-28T04:00:07", "dataAberturaProposta": "2026-09-30T08:00:00",
    "dataEncerramentoProposta": "2026-10-15T10:00:00", "dataAtualizacaoGlobal": "2026-09-30T04:04:15",
}
REG_VEICULO = dict(REG_709, numeroControlePNCP="00394544000185-1-000591/2026", sequencialCompra=591,
                   numeroCompra="90032", objetoCompra="Registro de Preço para aquisição de VEÍCULO")


class ClienteFalso:
    def __init__(self):
        self.downloads = 0

    def contratacoes(self, ini, fim, mod, uasg=None, por="atualizacao"):
        return iter([REG_709, REG_VEICULO])

    def itens(self, cnpj, ano, seq):
        if seq == 2092:
            return [{"numeroItem": 1, "descricao": "TOLVAPTANA CONCENTRAÇÃO: 15", "quantidade": 20340,
                     "unidadeMedida": "Comprimido", "ncmNbsCodigo": "30049099", "orcamentoSigiloso": True}]
        return [{"numeroItem": 1, "descricao": "VEÍCULO", "quantidade": 3, "unidadeMedida": "Unidade",
                 "ncmNbsCodigo": "87032100"}]

    def arquivos(self, cnpj, ano, seq):
        return [{"sequencialDocumento": 2, "titulo": "EDITAL PE SRP 90114_2026 Rep..pdf",
                 "tipoDocumentoNome": "Edital", "url": "https://pncp.gov.br/x/arquivos/2",
                 "dataPublicacaoPncp": "2026-09-30T04:01:09", "statusAtivo": True}]

    def resultados(self, cnpj, ano, seq, item):
        return []

    def atas(self, cnpj, ano, seq):
        return []

    def baixar(self, url):
        self.downloads += 1
        return _pdf(EDITAL_90114)


def test_coleta_marca_709_e_ignora_nao_medicamento(tmp_path):
    cfg = Config(db_path=str(tmp_path / "m.db"), saida_dir=tmp_path / "out")
    con = db.conectar(cfg.db_path)
    cli = ClienteFalso()
    stats = Coletor(cfg, REGRAS, con, cli).coletar()
    assert stats["medicamentos"] == 1 and stats["candidatas_novas"] == 1
    assert cli.downloads == 1  # o veiculo nao teve documento baixado

    achado = relatorio.buscar(con, "90114")
    assert len(achado) == 1 and achado == relatorio.buscar(con, "709")
    c = achado[0]
    assert c["candidata"] == 1 and c["numero_pregao"] == "90114/2026"
    assert c["identificador"] == "250005-709-2026"
    sinais = {s["sinal_id"] for s in c["sinais"]}
    assert {"sem_registro_anvisa", "importacao_excepcional", "ich_ou_oms"} <= sinais
    assert all(s["fonte_url"] and s["lido_em"] for s in c["sinais"])

    # Segunda execucao: nada mudou no PNCP, nada e baixado de novo.
    Coletor(cfg, REGRAS, con, cli).coletar()
    assert cli.downloads == 1

    arquivos = relatorio.gravar(relatorio.candidatas(con), REGRAS, cfg.saida_dir)
    assert "90114/2026" in arquivos["md"].read_text(encoding="utf-8")
    assert "sem registro na ANVISA" in arquivos["csv"].read_text(encoding="utf-8-sig")


def test_zip_com_rar_dentro(tmp_path):
    """ZIP com RAR dentro (formato comum na DLOG) e lido ate os PDFs."""
    import io, shutil, subprocess, zipfile
    import pytest
    from licitacoes.coleta import _pdfs_de
    if not shutil.which("bsdtar"):
        pytest.skip("bsdtar ausente")
    pasta = tmp_path / "Completo"
    pasta.mkdir()
    (pasta / "Edital.pdf").write_bytes(_pdf(EDITAL_90114))
    rar = tmp_path / "Completo.7z"  # bsdtar nao grava RAR; 7z usa o mesmo caminho de leitura
    subprocess.run(["bsdtar", "-a", "-cf", str(rar), "-C", str(tmp_path), "Completo"], check=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Aviso.pdf", _pdf("Aviso curto"))
        z.writestr("Edital BUPROPIONA.7z", rar.read_bytes())
    nomes = [n for n, _ in _pdfs_de(buf.getvalue())]
    assert "Aviso.pdf" in nomes
    assert any(n.endswith("Completo/Edital.pdf") for n in nomes)
