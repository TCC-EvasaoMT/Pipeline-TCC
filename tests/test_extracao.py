import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import requests

from pipeline import consultas, extracao

AMBIENTE = {"SEDAP_API_URL": "https://exemplo.invalido/api", "SEDAP_TOKEN": "token-sintetico",
            "SEDAP_PROFILE_ID": "perfil-sintetico"}


class Resposta:
    def __init__(self, status_code, corpo=None, texto=None, headers=None):
        self.status_code = status_code
        self._corpo = corpo
        self.text = texto if texto is not None else json.dumps(corpo)
        self.headers = headers or {}

    def json(self):
        if self._corpo is None:
            raise ValueError("não é JSON")
        return self._corpo


class Sessao:
    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def post(self, url, json, headers, timeout):
        self.chamadas.append({"url": url, "json": json, "headers": headers})
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def linhas_validas(consulta, n=2):
    return {"rows": [{coluna: i for coluna in consulta.colunas} for i in range(n)]}


@mock.patch.dict(os.environ, AMBIENTE)
@mock.patch("pipeline.extracao.time.sleep")
class TestExtracao(unittest.TestCase):
    def setUp(self):
        temporaria = tempfile.TemporaryDirectory()
        self.addCleanup(temporaria.cleanup)
        self.pasta = Path(temporaria.name)
        self.consulta = next(c for c in consultas.catalogo([2021]) if c.nome == "alunos_curso")
        self.outra = next(c for c in consultas.catalogo([2021]) if c.nome == "ies")

    def arquivos(self):
        return sorted(self.pasta.glob("*/*.json"))

    def test_envia_como_no_instrucoes_e_grava_a_resposta_como_veio(self, _sleep):
        corpo = linhas_validas(self.consulta)
        sessao = Sessao(Resposta(200, corpo))
        total = extracao.extrair([self.consulta], sessao=sessao, pasta=self.pasta)
        chamada = sessao.chamadas[0]
        self.assertEqual(chamada["json"], {"content": self.consulta.sql})
        self.assertEqual(chamada["headers"]["Authorization"], "Bearer token-sintetico")
        self.assertEqual(chamada["headers"]["profile-id"], "perfil-sintetico")
        self.assertEqual(total["ok"], 1)
        [arquivo] = self.arquivos()
        doc = json.loads(arquivo.read_text(encoding="utf-8"))
        self.assertEqual(arquivo.parent.name, "2021")
        self.assertEqual((doc["status"], doc["ano"], doc["sql"]), ("ok", 2021, self.consulta.sql))
        self.assertEqual(doc["resposta"], corpo)
        self.assertTrue(doc["executado_em"])

    def test_nao_reenvia_o_que_ja_esta_na_bronze(self, _sleep):
        extracao.extrair([self.consulta], sessao=Sessao(Resposta(200, linhas_validas(self.consulta))),
                         pasta=self.pasta)
        sessao = Sessao()
        total = extracao.extrair([self.consulta], sessao=sessao, pasta=self.pasta)
        self.assertEqual(sessao.chamadas, [])
        self.assertEqual(total["já na bronze"], 1)

    def test_refazer_grava_arquivo_novo_sem_apagar_o_antigo(self, _sleep):
        extracao.extrair([self.consulta], sessao=Sessao(Resposta(200, linhas_validas(self.consulta))),
                         pasta=self.pasta)
        antigo = self.arquivos()[0]
        conteudo = antigo.read_bytes()
        with mock.patch("pipeline.extracao.datetime") as relogio:  # outro horário = outro arquivo
            relogio.now.return_value = datetime(2030, 1, 1, 12, 0, 0).astimezone()
            relogio.fromisoformat = datetime.fromisoformat
            extracao.extrair([self.consulta], refazer=True,
                             sessao=Sessao(Resposta(200, linhas_validas(self.consulta))), pasta=self.pasta)
        self.assertEqual(len(self.arquivos()), 2)
        self.assertEqual(antigo.read_bytes(), conteudo)

    def test_recusa_fica_registrada_e_a_extracao_continua(self, _sleep):
        sessao = Sessao(Resposta(400, texto="consulta recusada"), Resposta(200, linhas_validas(self.outra)))
        total = extracao.extrair([self.consulta, self.outra], sessao=sessao, pasta=self.pasta)
        self.assertEqual((total["recusada"], total["ok"]), (1, 1))
        docs = [json.loads(a.read_text(encoding="utf-8")) for a in self.arquivos()]
        recusada = next(d for d in docs if d["consulta"] == "alunos_curso")
        self.assertEqual((recusada["status_http"], recusada["erro"]), (400, "consulta recusada"))
        # Uma consulta recusada não é reenviada sozinha (evita requisições repetidas).
        sessao = Sessao()
        extracao.extrair([self.consulta], sessao=sessao, pasta=self.pasta)
        self.assertEqual(sessao.chamadas, [])

    def test_credencial_invalida_interrompe_sem_gravar(self, _sleep):
        with self.assertRaises(extracao.ErroExtracao):
            extracao.extrair([self.consulta, self.outra], sessao=Sessao(Resposta(401, texto="não autorizado")),
                             pasta=self.pasta)
        self.assertEqual(self.arquivos(), [])

    def test_repete_quando_o_provedor_pede(self, sleep):
        sessao = Sessao(Resposta(429, texto="", headers={"Retry-After": "7"}),
                        Resposta(200, linhas_validas(self.consulta)))
        total = extracao.extrair([self.consulta], sessao=sessao, pasta=self.pasta)
        self.assertEqual((len(sessao.chamadas), total["ok"]), (2, 1))
        sleep.assert_any_call(7.0)

    def test_429_persistente_interrompe(self, _sleep):
        sessao = Sessao(*(Resposta(429, texto="") for _ in range(extracao.TENTATIVAS)))
        with self.assertRaises(extracao.ErroExtracao):
            extracao.extrair([self.consulta], sessao=sessao, pasta=self.pasta)
        self.assertEqual(self.arquivos(), [])

    def test_sem_conexao_repete_mas_tempo_esgotado_na_resposta_nao(self, _sleep):
        sessao = Sessao(requests.exceptions.ConnectTimeout(), Resposta(200, linhas_validas(self.consulta)))
        self.assertEqual(extracao.extrair([self.consulta], sessao=sessao, pasta=self.pasta)["ok"], 1)
        sessao = Sessao(requests.exceptions.ReadTimeout(), Resposta(200, linhas_validas(self.outra)))
        with self.assertRaises(extracao.ErroExtracao):
            extracao.extrair([self.outra], sessao=sessao, pasta=self.pasta)
        self.assertEqual(len(sessao.chamadas), 1)  # a consulta pode ter sido processada: não repete

    def test_resposta_fora_do_formato_e_marcada(self, _sleep):
        corpo = {"rows": [{"NU_ANO": 2021}]}
        extracao.extrair([self.consulta], sessao=Sessao(Resposta(200, corpo)), pasta=self.pasta)
        doc = json.loads(self.arquivos()[0].read_text(encoding="utf-8"))
        self.assertEqual(doc["status"], "resposta_invalida")
        self.assertIn("faltam", doc["erro"])
        self.assertEqual(doc["resposta"], corpo)

    def test_aceita_nomes_de_coluna_em_outra_caixa(self, _sleep):
        corpo = {"rows": [{coluna.lower(): 1 for coluna in self.consulta.colunas}]}
        self.assertIsNone(extracao.problema_na_resposta(corpo, self.consulta.colunas))

    def test_token_e_dados_nao_vao_para_o_log(self, _sleep):
        corpo = {"rows": [{coluna: "VALOR-SENSIVEL" for coluna in self.consulta.colunas}]}
        with self.assertLogs("pipeline.extracao", level="DEBUG") as logs:
            extracao.extrair([self.consulta], sessao=Sessao(Resposta(200, corpo)), pasta=self.pasta)
        texto = "\n".join(logs.output)
        self.assertNotIn("token-sintetico", texto)
        self.assertNotIn("VALOR-SENSIVEL", texto)
        self.assertNotIn(AMBIENTE["SEDAP_API_URL"], texto)

    def test_ibge_nao_sobrescreve_arquivo_existente(self, _sleep):
        destino = self.pasta / "ibge_municipios.json"
        destino.write_text("[]", encoding="utf-8")
        with self.assertRaises(extracao.ErroExtracao):
            extracao.baixar_ibge(destino, sessao=mock.Mock())
        self.assertEqual(destino.read_text(encoding="utf-8"), "[]")


if __name__ == "__main__":
    unittest.main()
