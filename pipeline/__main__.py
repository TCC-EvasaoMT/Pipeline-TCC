import argparse
import logging
import sys
from collections import Counter

from . import carga, config, consultas, extracao


def _consultas(_args) -> None:
    lista = consultas.catalogo()
    for consulta in lista:
        destino = config.PASTA_CONSULTAS / str(consulta.ano) / f"{consulta.nome}.sql"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(consulta.sql + "\n", encoding="utf-8")
    por_prioridade = Counter(consulta.prioridade for consulta in lista)
    print(f"{len(lista)} consultas gravadas em {config.PASTA_CONSULTAS}")
    for prioridade in sorted(por_prioridade):
        print(f"  prioridade {prioridade}: {por_prioridade[prioridade]}")


def _extrair(args) -> None:
    lista = consultas.catalogo(args.ano or config.ANOS)
    if args.prioridade:
        lista = [c for c in lista if c.prioridade <= args.prioridade]
    if args.consulta:
        desconhecidas = set(args.consulta) - {c.nome for c in lista}
        if desconhecidas:
            raise SystemExit(f"Consultas desconhecidas: {sorted(desconhecidas)} (ver o comando 'consultas').")
        lista = [c for c in lista if c.nome in args.consulta]
    print(f"{len(lista)} consultas selecionadas.")
    total = extracao.extrair(lista, refazer=args.refazer)
    print("Resumo:", dict(total))


def _ibge(_args) -> None:
    print("Gravado:", extracao.baixar_ibge())


def _transformar(_args) -> None:
    for tabela, linhas in carga.transformar().items():
        print(f"gold.{tabela}: {linhas} linhas")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline",
                                     description="Pipeline da evasão: SEDAP+ -> bronze -> silver -> gold.")
    comandos = parser.add_subparsers(dest="comando", required=True)

    comandos.add_parser("consultas", help="grava o SQL das consultas em dados/consultas/ (não chama a API)"
                        ).set_defaults(executar=_consultas)

    extrair = comandos.add_parser("extrair", help="envia as consultas ao SEDAP+ e grava as respostas na bronze")
    extrair.add_argument("--ano", type=int, nargs="+", choices=config.ANOS, help="anos (padrão: todos)")
    extrair.add_argument("--prioridade", type=int, choices=(1, 2), help="só até esta prioridade")
    extrair.add_argument("--consulta", nargs="+", help="só estas consultas (nomes do comando 'consultas')")
    extrair.add_argument("--refazer", action="store_true",
                         help="envia de novo mesmo se a bronze já tiver resposta (a antiga é mantida)")
    extrair.set_defaults(executar=_extrair)

    comandos.add_parser("ibge", help="baixa a tabela pública de municípios do IBGE para referencias/"
                        ).set_defaults(executar=_ibge)
    comandos.add_parser("transformar", help="carrega a bronze no Postgres e reconstrói silver e gold"
                        ).set_defaults(executar=_transformar)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        args.executar(args)
    except extracao.ErroExtracao as erro:
        sys.exit(f"Extração interrompida: {erro}")


if __name__ == "__main__":
    main()
