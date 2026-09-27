"""Comando facturador-afip-mcp.

    facturador-afip-mcp                      servidor MCP por stdio (lo que ejecuta el cliente de MCP)
    facturador-afip-mcp init                 crea la carpeta de datos, la clave privada y el pedido de certificado
    facturador-afip-mcp borradores           lista los borradores validados en homologación
    facturador-afip-mcp emitir <borrador>    emite un borrador en producción, con confirmación en la terminal

La carpeta de datos es $FACTURADOR_AFIP_DIR, o ~/.facturador-afip si no está definida.
"""
import argparse
import sys
from pathlib import Path

import anyio


def _init(args):
    from . import configuracion
    from .arca import ErrorArca
    from .datos import Datos

    datos = Datos(Path(args.carpeta).expanduser()) if args.carpeta else Datos.desde_entorno()
    try:
        r = configuracion.iniciar(datos, args.cuit, args.nombre, args.alias)
    except ErrorArca as e:
        raise SystemExit(str(e)) from e
    for archivo in r["creados"]:
        print(f"Creado {datos.certs / archivo}")
    print(f"\nCarpeta de datos: {r['carpeta']}")
    print("Siguiente paso: pedir los certificados en ARCA con esos CSR. La forma más simple es pedirle a Claude "
          "\"configurá el facturador\": te guía paso a paso.")


def _borradores(_args):
    from .datos import Datos
    for b in Datos.desde_entorno().listar_borradores():
        titulo = b["homologacion"].get("resumen", {}).get("titulo", "")
        print(f"{b['id']}\t{b['estado']}\t{titulo} (homologación)\t{(b.get('emision') or {}).get('comprobante', '')}")


def _emitir(args):
    from . import confirmacion, flujo
    from .arca import ErrorArca
    from .datos import Datos
    try:
        r = anyio.run(flujo.emitir_borrador, Datos.desde_entorno(), args.borrador, confirmacion.por_terminal)
    except ErrorArca as e:
        raise SystemExit(str(e)) from e
    if not r.get("emitida"):
        raise SystemExit(r["mensaje"])
    print(f"\nAPROBADA. {r['comprobante']}, CAE {r['cae']}, vence {r['vencimiento_cae']}")
    for o in r["observaciones"]:
        print(f"OBSERVACIÓN: {o}")
    print(f"PDF: {r['pdf']}")


def main():
    if len(sys.argv) == 1:
        from .server import main as servidor
        return servidor()
    ap = argparse.ArgumentParser(prog="facturador-afip-mcp", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="comando", required=True)
    p = sub.add_parser("init", help="crea la carpeta de datos, la clave y el CSR")
    p.add_argument("--cuit", required=True)
    p.add_argument("--nombre", required=True, help="nombre o razón social, como figura en ARCA")
    p.add_argument("--alias", required=True, help="nombre del certificado: solo letras y números")
    p.add_argument("--carpeta", help="por defecto $FACTURADOR_AFIP_DIR o ~/.facturador-afip")
    p.set_defaults(funcion=_init)
    sub.add_parser("borradores", help="lista los borradores").set_defaults(funcion=_borradores)
    p = sub.add_parser("emitir", help="emite un borrador en producción, con confirmación en la terminal")
    p.add_argument("borrador")
    p.set_defaults(funcion=_emitir)
    sub.add_parser("serve", help="servidor MCP por stdio").set_defaults(funcion=lambda _: __import__(
        "facturador_afip_mcp.server", fromlist=["main"]).main())
    args = ap.parse_args()
    args.funcion(args)


if __name__ == "__main__":
    main()
