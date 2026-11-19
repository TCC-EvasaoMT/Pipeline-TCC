# Pipeline-TCC-2

Pipeline para o Trabalho de Conclusão de Curso II, referente ao titulo de "Desenvolvimento de uma Arquitetura de Dados em Nuvem para Apoio à Análise da Evasão no Ensino Superior Público em Mato Grosso". 
O projeto envia consultas agregadas ao SEDAP+ e grava as respostas na camada **bronze**. No Postgres, padroniza tudo na **silver** e monta a **gold**, um modelo dimensional para a análise da evasão.

## Estrutura

```text
Pipeline-TCC-2/
├── infra/docker-compose.yml   Postgres (aceita conexões só desta máquina)
├── pipeline/                  código Python
│   ├── consultas.py           catálogo das consultas SQL ao SEDAP+ (só monta o SQL)
│   ├── extracao.py            envia as consultas e grava as respostas na bronze
│   ├── carga.py               carrega a bronze no Postgres e roda os arquivos de sql/
│   ├── config.py              caminhos e variáveis do .env
│   └── __main__.py            linha de comando
├── sql/                       bronze.sql, silver.sql e gold.sql
├── referencias/               códigos do catálogo, fatores de perfil, IBGE e CINE Brasil
├── tests/                     testes com dados sintéticos
└── dados/                     criada ao rodar: respostas do SEDAP+ (confidencial, fora do git)
```

## Camadas

| Camada | Onde fica | O que tem |
|---|---|---|
| bronze | `dados/bronze/sedap/<ano>/*.json` e schema `bronze` | cada resposta do SEDAP+ como veio, com o SQL, a data e hora e o ano. As referências também ficam como vieram. |
| silver | schema `silver` | códigos como texto, anos unidos, códigos decodificados, consultas juntas por curso e IES, nomes do IBGE e da CINE Brasil, e marcas de grupo pequeno e de valor negativo |
| gold | schema `gold` | as 7 dimensões e as 4 fatos do modelo aprovado. As taxas são calculadas na visualização. |

O pipeline nunca altera os arquivos da bronze. A silver e a gold são reconstruídas a cada `transformar`, numa única transação: se algo falhar, o banco fica como estava. O ruído do SEDAP+ (valor negativo ou não inteiro) fica como veio, com a marca.

## Preparar (uma vez)

1. Instale as dependências (Python 3.10 ou mais novo):
   ```powershell
   cd Pipeline-TCC-2
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. Crie um arquivo `.env` nesta pasta, com uma linha `CHAVE=valor` para cada variável. Se a senha tiver `$`, coloque-a entre aspas simples.
   - Obrigatórias: `SEDAP_API_URL`, `SEDAP_TOKEN`, `SEDAP_PROFILE_ID`, `POSTGRES_DB`, `POSTGRES_USER` e `POSTGRES_PASSWORD`.
   - Opcionais: `SEDAP_TABELA` (padrão `raw.{tabela}_{ano}_SEDAP`), `SEDAP_TIMEOUT_SEGUNDOS` (padrão 300), `SEDAP_PAUSA_SEGUNDOS` (padrão 2), `POSTGRES_HOST` (padrão `localhost`) e `POSTGRES_PORT` (padrão 5432).
3. Suba o Postgres:
   ```powershell
   docker compose --env-file .env -f infra/docker-compose.yml up -d
   ```
   Para parar: troque `up -d` por `down`. Os dados ficam no volume do Docker.
4. Coloque as referências públicas em `referencias/`:
   - IBGE: `python -m pipeline ibge`. Baixa a lista de municípios, com meso e microrregião, da API de localidades do IBGE.
   - CINE Brasil: salve a tabela do INEP como `referencias/cine_brasil.csv`. Use as colunas da aba `AUX_CINE_BRASIL` do catálogo do INEP/SEDAP: `CO_CINE_ROTULO`, `NO_CINE_ROTULO`, `CO_CINE_AREA_GERAL`, `NO_CINE_AREA_GERAL`, `CO_CINE_AREA_ESPECIFICA`, `NO_CINE_AREA_ESPECIFICA`, `CO_CINE_AREA_DETALHADA` e `NO_CINE_AREA_DETALHADA`. Mantenha os códigos como texto, com os zeros à esquerda.

   Sem esses dois arquivos, o pipeline roda, mas os nomes de município, meso e microrregião e área ficam vazios.

## Rodar

| Comando | O que faz |
|---|---|
| `python -m pipeline consultas` | grava o SQL das 265 consultas em `dados/consultas/`, para revisão. Não chama a API. |
| `python -m pipeline extrair --prioridade 1` | envia ao SEDAP+ as 90 consultas de prioridade 1 |
| `python -m pipeline extrair --ano 2023 --consulta alunos_curso` | envia só as consultas escolhidas |
| `python -m pipeline transformar` | carrega a bronze no Postgres e reconstrói a silver e a gold |

Regras da extração:
- Envia uma consulta por vez, com pausa entre elas (`SEDAP_PAUSA_SEGUNDOS`) e um tempo máximo de espera (`SEDAP_TIMEOUT_SEGUNDOS`).
- Não reenvia uma consulta que já tem resposta na bronze com o mesmo SQL, mesmo que ela tenha sido recusada. Com `--refazer`, envia de novo e grava num arquivo novo; o antigo fica.
- Só repete o envio quando a consulta não chegou ao provedor ou quando ele pede (HTTP 429 ou 503). Se der erro de credencial ou de endereço, para tudo.
- A recusa fica registrada na bronze, com a mensagem do provedor. A silver usa a resposta válida mais recente de cada consulta e ano.
- O token e os dados nunca vão para o log.

## Testes

```powershell
python -m unittest discover -s tests
```

O teste da silver e da gold precisa de um Postgres de teste. Defina `PIPELINE_TESTE_POSTGRES` (ex.: `postgresql://usuario:senha@localhost:5432/postgres`). O teste cria um banco temporário e o apaga no fim. Sem essa variável, ele é pulado.

## Segurança

- O `.env` e a pasta `dados/` estão no `.gitignore` e não devem ser compartilhados.
- O Postgres só aceita conexões desta máquina (`127.0.0.1`).
- Os resultados do SEDAP+ só são cruzados com o IBGE e a CINE Brasil localmente.
