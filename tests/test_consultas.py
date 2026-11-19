import unittest
from unittest import mock

from pipeline import config, consultas


def _partes(sql):
    select, resto = sql.split("\nFROM ", 1)
    itens = select.removeprefix("SELECT\n  ").split(",\n  ")
    grupo = resto.split("\nGROUP BY\n  ", 1)[1].split(",\n  ")
    return itens, grupo


class TestCatalogo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lista = consultas.catalogo()
        cls.por_chave = {(c.nome, c.ano): c for c in cls.lista}

    def test_quantidade_por_prioridade(self):
        # Por ano: 6 consultas fixas + 12 fatores de prioridade 1.
        self.assertEqual(sum(c.prioridade == 1 for c in self.lista), 18 * len(config.ANOS))
        self.assertEqual(sum(c.prioridade == 2 for c in self.lista), 35 * len(config.ANOS))
        self.assertEqual(len(self.por_chave), len(self.lista), "nome repetido no mesmo ano")

    def test_ordem_de_execucao_comeca_pela_prioridade_1(self):
        prioridades = [c.prioridade for c in self.lista]
        self.assertEqual(prioridades, sorted(prioridades))

    def test_toda_consulta_agrega_e_filtra_o_escopo(self):
        for c in self.lista:
            with self.subTest(consulta=c.nome, ano=c.ano):
                self.assertIn("\nGROUP BY\n", c.sql)
                self.assertIn("i.CO_UF = '51'", c.sql)
                self.assertIn("i.TP_CATEGORIA_ADMINISTRATIVA IN ('1', '2', '3')", c.sql)
                self.assertNotIn("SELECT *", c.sql)

    def test_colunas_nao_agregadas_estao_todas_no_group_by(self):
        for c in self.lista:
            with self.subTest(consulta=c.nome, ano=c.ano):
                itens, grupo = _partes(c.sql)
                nao_agregados = [item.rsplit(" AS ", 1)[0] for item in itens
                                 if not item.startswith(("COUNT(", "SUM("))]
                self.assertEqual(nao_agregados, grupo)
                self.assertEqual(len(itens), len(c.colunas))

    def test_colunas_do_resultado_sem_repeticao(self):
        for c in self.lista:
            with self.subTest(consulta=c.nome, ano=c.ano):
                minusculas = [coluna.lower() for coluna in c.colunas]
                self.assertEqual(len(minusculas), len(set(minusculas)))

    def test_tipos_de_sup_aluno_mudam_em_2020(self):
        de_2019 = self.por_chave[("alunos_curso", 2019)].sql
        de_2020 = self.por_chave[("alunos_curso", 2020)].sql
        self.assertIn("a.TP_SITUACAO = '4'", de_2019)
        self.assertIn("ON a.CO_IES = i.CO_IES", de_2019)
        self.assertIn("a.TP_NIVEL_ACADEMICO = '1'", de_2019)
        self.assertIn("a.TP_SITUACAO = 4", de_2020)
        self.assertIn("ON CAST(a.CO_IES AS STRING) = i.CO_IES", de_2020)
        self.assertIn("ON CAST(a.CO_CURSO AS STRING) = c.CO_CURSO", de_2020)
        self.assertIn("a.TP_NIVEL_ACADEMICO = 1", de_2020)

    def test_medidas_do_modelo(self):
        curso = self.por_chave[("alunos_curso", 2021)]
        for medida in ("vinculos_total", "alunos_cursando", "alunos_matricula_trancada", "alunos_desvinculados",
                       "alunos_transferidos", "concluintes_total", "alunos_falecidos",
                       "ingressantes_total", "matriculas_total"):
            self.assertIn(medida, curso.colunas)
        self.assertIn("SUM(CASE WHEN a.TP_SITUACAO = 7 THEN 1 ELSE 0 END) AS alunos_falecidos", curso.sql)
        coorte = self.por_chave[("alunos_coorte", 2021)]
        self.assertIn("NU_ANO_INGRESSO", coorte.colunas)
        self.assertNotIn("matriculas_total", coorte.colunas)
        docentes = self.por_chave[("docentes_curso", 2021)].sql
        self.assertIn("d.TP_ESCOLARIDADE = '5'", docentes)
        self.assertIn("d.TP_REGIME_TRABALHO IN ('1', '2')", docentes)
        self.assertNotIn("TP_SITUACAO", docentes)  # todos os docentes, em exercício ou afastados

    def test_um_fator_por_consulta_de_perfil(self):
        perfis = [c for c in self.lista if c.fato == "evasao_perfil"]
        for c in perfis:
            with self.subTest(consulta=c.nome, ano=c.ano):
                self.assertEqual(c.colunas.count("CATEGORIA_PERFIL"), 1)
                self.assertEqual(c.nome, f"perfil_{c.variavel.lower()}")

    def test_faixa_etaria_usa_a_coluna_de_cada_ano(self):
        de_2019 = consultas.expressao_fator("FAIXA_ETARIA", 2019)
        de_2022 = consultas.expressao_fator("FAIXA_ETARIA", 2022)
        self.assertIn("a.NU_IDADE", de_2019)
        self.assertNotIn("DT_NASCIMENTO", de_2019)
        self.assertIn("SUBSTR(a.DT_NASCIMENTO, 7, 4)", de_2022)
        for expressao in (de_2019, de_2022, consultas.expressao_fator("NATURALIDADE", 2019),
                          consultas.expressao_fator("CARGA_HORARIA_CUMPRIDA", 2021)):
            self.assertNotIn("ELSE", expressao)  # sem dado, a categoria fica nula
        self.assertIn("= '51'", consultas.expressao_fator("NATURALIDADE", 2019))
        self.assertIn("= 51 ", consultas.expressao_fator("NATURALIDADE", 2020))

    def test_nome_da_tabela_segue_a_configuracao(self):
        with mock.patch.object(config, "TABELA_SEDAP", "raw.{tabela}_{ano}_SEDAP"):
            self.assertEqual(consultas.tabela("SUP_ALUNO", 2021), "`raw.SUP_ALUNO_2021_SEDAP`")
        with mock.patch.object(config, "TABELA_SEDAP", "outro.{tabela}{ano}"):
            self.assertIn("`outro.SUP_IES2019`", consultas.catalogo([2019])[0].sql)


if __name__ == "__main__":
    unittest.main()
