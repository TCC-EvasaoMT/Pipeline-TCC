import csv
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from pipeline import carga, config, consultas, extracao

# Sem esta variável, o teste da transformação completa é pulado (ver README)
CONNINFO = os.environ.get("PIPELINE_TESTE_POSTGRES")


class Resposta:
    def __init__(self, status_code, corpo=None, texto=""):
        self.status_code, self._corpo, self.text = status_code, corpo, texto

    def json(self):
        return self._corpo


def _temporaria(caso):
    pasta = tempfile.TemporaryDirectory()
    caso.addCleanup(pasta.cleanup)
    return Path(pasta.name)


class TestReferencias(unittest.TestCase):
    def setUp(self):
        with open(config.PASTA_REFERENCIAS / "fatores_perfil.csv", encoding="utf-8", newline="") as arquivo:
            self.fatores = list(csv.DictReader(arquivo, delimiter=";"))

    def test_catalogo_de_fatores_da_modelagem(self):
        prioridade = {linha["variavel"]: linha["prioridade"] for linha in self.fatores}
        self.assertEqual(sum(p == "1" for p in prioridade.values()), 12)
        self.assertEqual(sum(p == "2" for p in prioridade.values()), 35)

    def test_categorias_sem_repeticao_e_fator_consistente(self):
        chaves = [(linha["variavel"], linha["codigo_categoria"]) for linha in self.fatores]
        self.assertEqual(len(chaves), len(set(chaves)))
        por_variavel = {}
        for linha in self.fatores:
            atributos = (linha["prioridade"], linha["grupo_fator"], linha["fator"], linha["variavel_origem"])
            self.assertEqual(por_variavel.setdefault(linha["variavel"], atributos), atributos)
        fatores = [atributos[2] for atributos in por_variavel.values()]
        self.assertEqual(len(fatores), len(set(fatores)), "nome de fator repetido")

    def test_codigos_cobrem_as_variaveis_decodificadas(self):
        linhas = carga.ler_csv(config.PASTA_REFERENCIAS / "codigos.csv", carga.REFERENCIAS["codigos.csv"])
        variaveis = {(linha["tabela"], linha["variavel"]) for linha in linhas}
        for tabela, variavel in (("SUP_IES", "TP_CATEGORIA_ADMINISTRATIVA"), ("SUP_IES", "TP_ORGANIZACAO_ACADEMICA"),
                                 ("SUP_CURSO", "TP_SITUACAO"),
                                 ("SUP_CURSO", "TP_GRAU_ACADEMICO"), ("SUP_CURSO", "TP_NIVEL_ACADEMICO"),
                                 ("SUP_CURSO", "TP_MODALIDADE_ENSINO")):
            self.assertIn((tabela, variavel), variaveis)


class TestLeitura(unittest.TestCase):
    def test_csv_com_ponto_e_virgula_ou_virgula(self):
        pasta = _temporaria(self)
        (pasta / "a.csv").write_text("x;y\nÁrea;2\n", encoding="utf-8")
        (pasta / "b.csv").write_bytes("x,y\nÁrea,2\n".encode("latin-1"))
        for nome in ("a.csv", "b.csv"):
            self.assertEqual(carga.ler_csv(pasta / nome, {"x", "y"}), [{"x": "Área", "y": "2"}])
        with self.assertRaises(ValueError):
            carga.ler_csv(pasta / "a.csv", {"x", "z"})

    def test_resposta_ok_fora_do_formato_para_a_carga(self):
        pasta = _temporaria(self)
        consulta = consultas.catalogo([2020])[0]
        boa = extracao.registro(consulta, Resposta(200, {"rows": [dict.fromkeys(consulta.colunas, 1)]}),
                                datetime(2026, 1, 1).astimezone())
        extracao.gravar(boa, pasta)
        recusada = extracao.registro(consulta, Resposta(400, texto="recusada"), datetime(2026, 1, 2).astimezone())
        extracao.gravar(recusada, pasta)
        self.assertEqual(len(list(carga.ler_resultados(pasta))), 2)
        ruim = dict(boa, executado_em=datetime(2026, 1, 3).astimezone().isoformat(), resposta={"rows": [{"x": 1}]})
        extracao.gravar(ruim, pasta)
        with self.assertRaises(ValueError):
            list(carga.ler_resultados(pasta))

    def test_referencias_obrigatorias_e_opcionais(self):
        pasta = _temporaria(self)
        with self.assertRaises(FileNotFoundError):
            list(carga.ler_referencias(pasta))
        for nome in carga.OBRIGATORIAS:
            (pasta / nome).write_bytes((config.PASTA_REFERENCIAS / nome).read_bytes())
        with self.assertLogs("pipeline.carga", level="WARNING"):
            nomes = [nome for nome, _ in carga.ler_referencias(pasta)]
        self.assertEqual(sorted(nomes), sorted(carga.OBRIGATORIAS))
        (pasta / "ibge_municipios.json").write_text('{"erro": 1}', encoding="utf-8")
        with self.assertRaises(ValueError), self.assertLogs("pipeline.carga", level="WARNING"):
            list(carga.ler_referencias(pasta))


# Sem CO_IES, nome e sigla pois o SEDAP+ não deixa identificar a IES
IES_A = {"CO_MANTENEDORA": "9", "TP_CATEGORIA_ADMINISTRATIVA": "1", "TP_ORGANIZACAO_ACADEMICA": "1",
         "CO_REGIAO_IES": "5", "CO_UF_IES": "51", "CO_MUNICIPIO_IES": "5103403"}
IES_B = {"CO_MANTENEDORA": "8", "TP_CATEGORIA_ADMINISTRATIVA": "2", "TP_ORGANIZACAO_ACADEMICA": "4",
         "CO_REGIAO_IES": "5", "CO_UF_IES": "51", "CO_MUNICIPIO_IES": "5101837"}
# Só aparece na consulta de docentes da IES.
IES_C = {"CO_MANTENEDORA": "7", "TP_CATEGORIA_ADMINISTRATIVA": "3", "TP_ORGANIZACAO_ACADEMICA": "3",
         "CO_REGIAO_IES": "5", "CO_UF_IES": "51", "CO_MUNICIPIO_IES": "5101837"}
# Curso presencial, de manhã e à noite, na capital
CURSO_A = {"NO_CURSO": "Curso Sintético A", "TP_SITUACAO_CURSO": "1", "CO_CINE_ROTULO": "0114E04",
           "TP_GRAU_ACADEMICO": "2", "TP_NIVEL_ACADEMICO": "1", "TP_MODALIDADE_ENSINO": "1", "IN_GRATUITO": 1,
           "NU_CARGA_HORARIA": 3200, "DT_INICIO_FUNCIONAMENTO": "1990-03-01", "CO_MUNICIPIO_CURSO": "5103403",
           "IN_CAPITAL_CURSO": 1.0, "IN_INTEGRAL": 0, "IN_MATUTINO": 1, "IN_VESPERTINO": 0, "IN_NOTURNO": 1,
           "NU_INTEGRALIZACAO_MATUTINO": 4, "NU_INTEGRALIZACAO_NOTURNO": 5, "IN_OFERECE_DISC_SEMI_PRES": 0}
# Curso a distância, sem local nem turnos
CURSO_B = {"NO_CURSO": "Curso Sintético B", "TP_SITUACAO_CURSO": "1", "CO_CINE_ROTULO": "0413A01",
           "TP_GRAU_ACADEMICO": "3", "TP_NIVEL_ACADEMICO": "1", "TP_MODALIDADE_ENSINO": "2", "IN_GRATUITO": 1,
           "NU_CARGA_HORARIA": 1600, "DT_INICIO_FUNCIONAMENTO": "2015-08-01", "NU_INTEGRALIZACAO_EAD": 2.5,
           "IN_OFERECE_DISC_SEMI_PRES": 0}
# Curso integral num município criado depois de 2017 (sem meso/microrregião no IBGE)
CURSO_C = {"NO_CURSO": "Curso Sintético C", "TP_SITUACAO_CURSO": "3", "CO_CINE_ROTULO": "0114E04",
           "TP_GRAU_ACADEMICO": "1", "TP_NIVEL_ACADEMICO": "1", "TP_MODALIDADE_ENSINO": "1", "IN_GRATUITO": 1,
           "NU_CARGA_HORARIA": 4000, "DT_INICIO_FUNCIONAMENTO": "2019-02-01", "CO_MUNICIPIO_CURSO": "5101837",
           "IN_CAPITAL_CURSO": 0, "IN_INTEGRAL": 1, "IN_MATUTINO": 0, "IN_VESPERTINO": 0, "IN_NOTURNO": 0,
           "NU_INTEGRALIZACAO_INTEGRAL": 5, "IN_OFERECE_DISC_SEMI_PRES": 1, "NU_PERC_CARGA_SEMI_PRES": 20}

SITUACAO = ("vinculos_total", "alunos_cursando", "alunos_matricula_trancada", "alunos_desvinculados",
            "alunos_transferidos", "concluintes_total", "alunos_falecidos")


def _situacao(*valores):
    return dict(zip(SITUACAO, valores))


UF_MT = {"id": 51, "sigla": "MT", "nome": "Mato Grosso", "regiao": {"id": 5, "sigla": "CO", "nome": "Centro-Oeste"}}
IBGE = [
    {"id": 5103403, "nome": "Município Capital Sintético",
     "microrregiao": {"id": 51017, "nome": "Micro Sintética",
                      "mesorregiao": {"id": 5104, "nome": "Meso Sintética", "UF": UF_MT}},
     "regiao-imediata": {"id": 510001, "nome": "Imediata", "regiao-intermediaria": {"id": 5101, "nome": "Inter",
                                                                                    "UF": UF_MT}}},
    {"id": 5101837, "nome": "Município Novo Sintético", "microrregiao": None,
     "regiao-imediata": {"id": 510002, "nome": "Imediata 2", "regiao-intermediaria": {"id": 5102, "nome": "Inter 2",
                                                                                      "UF": UF_MT}}},
]
CINE = ("CO_CINE_AREA_GERAL;NO_CINE_AREA_GERAL;CO_CINE_AREA_ESPECIFICA;NO_CINE_AREA_ESPECIFICA;"
        "CO_CINE_AREA_DETALHADA;NO_CINE_AREA_DETALHADA;CO_CINE_ROTULO;NO_CINE_ROTULO\n"
        "01;Geral 01;011;Específica 011;0114;Detalhada 0114;0114E04;Rótulo 0114E04\n"
        "04;Geral 04;041;Específica 041;0413;Detalhada 0413;0413A01;Rótulo 0413A01\n")


@unittest.skipUnless(CONNINFO, "defina PIPELINE_TESTE_POSTGRES para testar a transformação num Postgres de teste")
class TestTransformacao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.banco = f"pipeline_teste_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(CONNINFO, autocommit=True) as conexao:
            conexao.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.banco)))
        cls.conninfo = make_conninfo(CONNINFO, dbname=cls.banco)
        cls._pasta = tempfile.TemporaryDirectory()
        cls.bronze = Path(cls._pasta.name) / "bronze"
        cls.referencias = Path(cls._pasta.name) / "referencias"
        cls.referencias.mkdir()
        for nome in carga.OBRIGATORIAS:
            (cls.referencias / nome).write_bytes((config.PASTA_REFERENCIAS / nome).read_bytes())
        (cls.referencias / "ibge_municipios.json").write_text(json.dumps(IBGE), encoding="utf-8")
        (cls.referencias / "cine_brasil.csv").write_text(CINE, encoding="utf-8")
        cls._gravar_bronze()
        cls.resumo = carga.transformar(cls.conninfo, cls.bronze, cls.referencias)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(CONNINFO, autocommit=True) as conexao:
            conexao.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(cls.banco)))
        cls._pasta.cleanup()

    @classmethod
    def _resultado(cls, nome, ano, linhas, minuto=0, status=200):
        consulta = next(c for c in consultas.catalogo([ano]) if c.nome == nome)
        rows = []
        for valores in linhas:
            assert set(valores) <= set(consulta.colunas), set(valores) - set(consulta.colunas)
            rows.append({**dict.fromkeys(consulta.colunas), **valores})
        quando = datetime(2026, 11, 10, 10, 0).astimezone() + timedelta(minutes=minuto)
        resposta = Resposta(200, {"rows": rows}) if status == 200 else Resposta(status, texto="recusada")
        extracao.gravar(extracao.registro(consulta, resposta, quando), cls.bronze)

    @classmethod
    def _gravar_bronze(cls):
        a_2019 = {"NU_ANO": 2019, "CO_CURSO": "1001", **IES_A, **CURSO_A}
        b_2019 = {"NU_ANO": 2019, "CO_CURSO": "1002", **IES_A, **CURSO_B}
        # 2020: SUP_ALUNO devolve códigos numéricos (1001.0 deve virar 1001)
        a_2020 = {"NU_ANO": 2020, "CO_CURSO": 1001.0, **IES_A, **CURSO_A}
        b_2020 = {"NU_ANO": 2020, "CO_CURSO": 1002, **IES_A, **CURSO_B}
        # Resposta antiga de 2019, substituída pela mais recente.
        cls._resultado("alunos_curso", 2019, [{**a_2019, **_situacao(999, 0, 0, 0, 0, 0, 0)}], minuto=0)
        cls._resultado("alunos_curso", 2019, [
            {**a_2019, **_situacao(100.5, 60, 5, 20.25, 4, 11, 0.25), "ingressantes_total": 30,
             "matriculas_total": 71},
            {**b_2019, **_situacao(12.3, 8, 1, 2, 0, 1.3, 0), "ingressantes_total": 4, "matriculas_total": 9},
        ], minuto=1)
        cls._resultado("alunos_curso", 2020, [
            {**a_2020, **_situacao(120.4, 70, 6, 25, 5, 15.1, -0.7), "ingressantes_total": 35,
             "matriculas_total": 85},
            {**b_2020, **_situacao(40, 30, 2, 5, 0, 3, 0), "ingressantes_total": 10, "matriculas_total": 33},
            {"NU_ANO": 2020, "CO_CURSO": 2001, **IES_B, **CURSO_C, **_situacao(30, 25, 1, 3, 0, 1, 0),
             "ingressantes_total": 8, "matriculas_total": 26},
        ])
        cls._resultado("alunos_coorte", 2020, [
            {**a_2020, "NU_ANO_INGRESSO": 2019, **_situacao(50, 40, 2, 6, 2, 0, 0), "ingressantes_total": 0},
            {**a_2020, "NU_ANO_INGRESSO": 2020, **_situacao(60, 55, 1, 4, 0, 0, 0), "ingressantes_total": 35},
            {**a_2020, "NU_ANO_INGRESSO": None, **_situacao(10.4, 5, 3, 1, 1, 0.4, 0), "ingressantes_total": 0},
        ])
        cls._resultado("perfil_tp_sexo", 2020, [
            {**a_2020, "CATEGORIA_PERFIL": 1, **_situacao(70, 40, 4, 15, 3, 8, 0)},
            {**a_2020, "CATEGORIA_PERFIL": 2, **_situacao(45, 27, 2, 9, 2, 5, 0)},
            {**a_2020, "CATEGORIA_PERFIL": None, **_situacao(3, 2, 0, 1, 0, 0, 0)},
            {**a_2020, "CATEGORIA_PERFIL": 3, **_situacao(2.4, 1, 0, 0, 0, 1.4, 0)},
        ])
        cls._resultado("perfil_in_apoio_alimentacao", 2020, [
            {**a_2020, "CATEGORIA_PERFIL": 1, **_situacao(10, 9, 0, 1, 0, 0, 0)},
            {**a_2020, "CATEGORIA_PERFIL": 0, **_situacao(5, 4, 0, 1, 0, 0, 0)},
            {**a_2020, "CATEGORIA_PERFIL": None, **_situacao(105, 57, 6, 23, 5, 14, 0)},
        ])
        cls._resultado("perfil_faixa_etaria", 2019, [
            {**b_2019, "CATEGORIA_PERFIL": "ATE_19", **_situacao(5, 4, 0, 1, 0, 0, 0)},
            {**b_2019, "CATEGORIA_PERFIL": "40_MAIS", **_situacao(6, 3, 1, 1, 0, 1, 0)},
            {**b_2019, "CATEGORIA_PERFIL": None, **_situacao(1.3, 1, 0, 0, 0, 0.3, 0)},
        ])
        cls._resultado("perfil_tp_cor_raca", 2020, [], status=400)
        cls._resultado("docentes_curso", 2020, [
            {"NU_ANO": 2020, "CO_CURSO": "1001", "docentes_total": 30, "docentes_doutores": 20,
             "docentes_tempo_integral": 25},
            {"NU_ANO": 2020, "CO_CURSO": "3001", "docentes_total": 5, "docentes_doutores": 1,
             "docentes_tempo_integral": -0.5},
        ])
        cls._resultado("docentes_ies", 2020, [
            {"NU_ANO": 2020, **IES_A, "docentes_total": 900, "docentes_doutores": 700,
             "docentes_tempo_integral": 850},
            {"NU_ANO": 2020, **IES_C, "docentes_total": 15, "docentes_doutores": 2, "docentes_tempo_integral": -1},
        ])

    def consultar(self, comando):
        with psycopg.connect(self.conninfo) as conexao:
            return conexao.execute(comando).fetchall()

    def test_resumo_da_gold(self):
        self.assertEqual(self.resumo["fato_evasao_curso"], 6)
        self.assertEqual(self.resumo["fato_evasao_coorte"], 3)
        self.assertEqual(self.resumo["fato_evasao_perfil"], 10)
        self.assertEqual(self.resumo["fato_contexto_ies"], 2)
        self.assertEqual(self.resumo["dim_instituicao"], 4)   # A, B, C e o membro 0
        self.assertEqual(self.resumo["dim_curso"], 5)         # 1001, 1002, 2001, 3001 e o membro 0
        self.assertEqual(self.resumo["dim_tempo"], 3)         # 2019, 2020 e o membro 0

    def test_bronze_guarda_todas_as_respostas(self):
        [(total, recusadas)] = self.consultar(
            "SELECT count(*), count(*) FILTER (WHERE status = 'recusada') FROM bronze.sedap_resultado")
        self.assertEqual((total, recusadas), (10, 1))

    def test_valores_ficam_como_vieram_e_resposta_mais_recente_vale(self):
        linhas = dict(self.consultar(
            "SELECT t.ano_censo, f.vinculos_total FROM gold.fato_evasao_curso f "
            "JOIN gold.dim_tempo t USING (id_tempo) WHERE f.codigo_curso = '1001'"))
        self.assertEqual(linhas, {2019: Decimal("100.5"), 2020: Decimal("120.4")})
        [(falecidos, negativo)] = self.consultar(
            "SELECT alunos_falecidos, flag_valor_negativo FROM gold.fato_evasao_curso f "
            "JOIN gold.dim_tempo t USING (id_tempo) WHERE f.codigo_curso = '1001' AND t.ano_censo = 2020")
        self.assertEqual((falecidos, negativo), (Decimal("-0.7"), True))

    def test_codigos_de_2019_e_2020_viram_o_mesmo_membro(self):
        [(cursos,)] = self.consultar("SELECT count(DISTINCT id_curso) FROM gold.fato_evasao_curso "
                                     "WHERE codigo_curso IN ('1001', '1002')")
        self.assertEqual(cursos, 2)

    def test_grupo_pequeno_marcado(self):
        [(pequeno,)] = self.consultar(
            "SELECT flag_grupo_pequeno FROM gold.fato_evasao_curso f JOIN gold.dim_tempo t USING (id_tempo) "
            "WHERE f.codigo_curso = '1002' AND t.ano_censo = 2019")
        self.assertTrue(pequeno)

    def test_atributos_decodificados_e_nomes_das_referencias(self):
        [ies] = self.consultar("SELECT categoria_administrativa, organizacao_academica, codigo_municipio_sede, "
                               "municipio_sede, uf_sede, regiao_sede, mesorregiao_sede "
                               "FROM gold.dim_instituicao WHERE codigo_mantenedora = '9'")
        self.assertEqual(ies, ("Pública federal", "Universidade", "5103403",
                               "Município Capital Sintético", "MT", "Centro-Oeste", "Meso Sintética"))
        instituicoes = dict(self.consultar(
            "SELECT DISTINCT f.codigo_curso, i.codigo_mantenedora FROM gold.fato_evasao_curso f "
            "JOIN gold.dim_instituicao i USING (id_instituicao)"))
        self.assertEqual(instituicoes, {"1001": "9", "1002": "9", "2001": "8", "3001": None})
        [curso] = self.consultar("SELECT nome_curso, grau_academico, situacao_curso, prazo_integralizacao_anos, "
                                 "ano_inicio_funcionamento FROM gold.dim_curso WHERE codigo_curso = '1001'")
        self.assertEqual(curso, ("Curso Sintético A", "Licenciatura", "Em atividade", Decimal("4"), 1990))
        [local] = self.consultar("SELECT uf, mesorregiao, eh_capital FROM gold.dim_localidade "
                                 "WHERE codigo_municipio = '5101837'")
        self.assertEqual(local, ("MT", None, False))
        [area] = self.consultar("SELECT nome_curso_cine, area_geral FROM gold.dim_area_conhecimento "
                                "WHERE codigo_rotulo_cine = '0114E04'")
        self.assertEqual(area, ("Rótulo 0114E04", "Geral 01"))

    def test_turno_da_oferta(self):
        turnos = dict(self.consultar(
            "SELECT DISTINCT f.codigo_curso, o.turno FROM gold.fato_evasao_curso f JOIN gold.dim_oferta o "
            "USING (id_oferta)"))
        self.assertEqual(turnos, {"1001": "Mais de um turno", "1002": "Não se aplica (EaD)", "2001": "Integral",
                                  "3001": "Não informado"})

    def test_curso_sem_atributos_aponta_para_o_membro_0(self):
        [chaves] = self.consultar("SELECT id_instituicao, id_area_conhecimento, id_localidade, id_oferta, "
                                  "flag_valor_negativo FROM gold.fato_evasao_curso WHERE codigo_curso = '3001'")
        self.assertEqual(chaves, (0, 0, 0, 0, True))
        [(ead,)] = self.consultar("SELECT DISTINCT id_localidade FROM gold.fato_evasao_curso "
                                  "WHERE codigo_curso = '1002'")
        self.assertEqual(ead, 0)

    def test_coorte_com_ano_de_ingresso_vazio(self):
        anos = dict(self.consultar(
            "SELECT f.vinculos_total, t.ano_censo FROM gold.fato_evasao_coorte f "
            "JOIN gold.dim_tempo t ON t.id_tempo = f.id_tempo_ingresso"))
        self.assertEqual(anos, {Decimal("50"): 2019, Decimal("60"): 2020, Decimal("10.4"): None})

    def test_perfil_mantem_vazios_e_codigos_fora_do_catalogo(self):
        categorias = dict(self.consultar(
            "SELECT p.categoria, f.vinculos_total FROM gold.fato_evasao_perfil f JOIN gold.dim_perfil p "
            "USING (id_perfil) WHERE p.fator = 'Sexo'"))
        self.assertEqual(categorias, {"Feminino": Decimal("70"), "Masculino": Decimal("45"),
                                      "Vazio (sem significado no catálogo)": Decimal("3"),
                                      "Código 3 não previsto no catálogo": Decimal("2.4")})
        self.assertEqual(sum(categorias.values()), Decimal("120.4"))  # = total do curso no ano
        vazios = dict(self.consultar(
            "SELECT p.fator, p.categoria FROM gold.fato_evasao_perfil f JOIN gold.dim_perfil p USING (id_perfil) "
            "WHERE p.codigo_categoria IS NULL AND p.fator <> 'Sexo'"))
        self.assertEqual(vazios, {"Tipo de apoio: alimentação": "Não recebe nenhum apoio social",
                                  "Faixa etária": "Não informado"})
        [(sem_perfil,)] = self.consultar("SELECT count(*) FROM gold.fato_evasao_perfil WHERE id_perfil = 0")
        self.assertEqual(sem_perfil, 0)

    def test_contexto_da_ies(self):
        linhas = {mantenedora: resto for mantenedora, *resto in self.consultar(
            "SELECT i.codigo_mantenedora, f.docentes_total, f.docentes_doutores, f.flag_valor_negativo "
            "FROM gold.fato_contexto_ies f JOIN gold.dim_instituicao i USING (id_instituicao)")}
        self.assertEqual(linhas, {"9": [Decimal("900"), Decimal("700"), False],
                                  "7": [Decimal("15"), Decimal("2"), True]})

    def test_reexecutar_da_o_mesmo_resultado(self):
        self.assertEqual(carga.transformar(self.conninfo, self.bronze, self.referencias), self.resumo)

    def test_grao_repetido_para_a_carga_sem_mexer_no_banco(self):
        repetida = {"NU_ANO": 2019, "CO_CURSO": "1001", **IES_A, **CURSO_A, **_situacao(1, 1, 0, 0, 0, 0, 0)}
        self._resultado("alunos_curso", 2019, [repetida, {**repetida, "NO_CURSO": "Outro nome"}], minuto=30)
        try:
            with self.assertRaises(psycopg.Error):
                carga.transformar(self.conninfo, self.bronze, self.referencias)
            [(linhas,)] = self.consultar("SELECT count(*) FROM gold.fato_evasao_curso")
            self.assertEqual(linhas, 6)
        finally:
            for arquivo in (self.bronze / "2019").glob("alunos_curso__*T103000.json"):
                arquivo.unlink()


if __name__ == "__main__":
    unittest.main()
