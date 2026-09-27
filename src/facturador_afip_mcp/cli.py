"""Comando facturador-afip-mcp.

    facturador-afip-mcp                      servidor MCP por stdio (lo que ejecuta el cliente de MCP)
    facturador-afip-mcp init                 crea la carpeta de datos, la clave privada y el pedido de certificado
    facturador-afip-mcp borradores           lista los borradores validados en homologación
    facturador-afip-mcp emitir <borrador>    emite un borrador en producción, con confirmación en la terminal

La carpeta de datos es $FACTURADOR_AFIP_DIR, o ~/.facturador-afip si no está definida.
"""
import argparse
import re
import sys
from importlib import resources
from pathlib import Path

import anyio


def _init(args):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from .datos import Datos

    datos = Datos(Path(args.carpeta).expanduser()) if args.carpeta else Datos.desde_entorno()
    cuit = re.sub(r"\D", "", args.cuit)
    if len(cuit) != 11:
        raise SystemExit("El CUIT tiene que tener 11 dígitos")
    if not re.fullmatch(r"[A-Za-z0-9]{1,50}", args.alias):
        raise SystemExit("El alias va solo con letras y números (ARCA rechaza guiones, espacios y acentos)")

    datos.certs.mkdir(parents=True, exist_ok=True)
    datos.certs.chmod(0o700)
    (datos.facturas / "prod").mkdir(parents=True, exist_ok=True)
    plantillas = resources.files("facturador_afip_mcp") / "plantillas"
    env = datos.raiz / ".env"
    if not env.exists():
        texto = (plantillas / "env").read_text().replace("AFIP_CUIT=", f"AFIP_CUIT={cuit}", 1)
        env.write_text(texto.replace("AFIP_RAZON_SOCIAL=", f"AFIP_RAZON_SOCIAL={args.nombre}", 1))
        env.chmod(0o600)
        print(f"Creado {env}: completá los datos del emisor")
    perfil = datos.raiz / "perfil.json"
    if not perfil.exists():
        perfil.write_text((plantillas / "perfil.json").read_text())
        print(f"Creado {perfil}: completalo con tus puntos de venta, cliente por defecto y formato")

    sujeto = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, args.nombre),
        x509.NameAttribute(NameOID.COMMON_NAME, args.alias),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit}"),
    ])
    for clave_nombre, csr_nombre in (("afip.key", "afip.csr"), ("afip_prod.key", "afip_prod.csr")):
        clave_archivo, csr_archivo = datos.certs / clave_nombre, datos.certs / csr_nombre
        if clave_archivo.exists():
            # Nunca se pisa una clave: las autorizaciones de ARCA dependen de ella
            print(f"Ya existe {clave_archivo}, no se toca")
            clave = serialization.load_pem_private_key(clave_archivo.read_bytes(), password=None)
        else:
            clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            clave_archivo.write_bytes(clave.private_bytes(serialization.Encoding.PEM,
                                                          serialization.PrivateFormat.TraditionalOpenSSL,
                                                          serialization.NoEncryption()))
            clave_archivo.chmod(0o600)
            print(f"Creada {clave_archivo} (no la compartas nunca)")
        if not csr_archivo.exists():
            csr = x509.CertificateSigningRequestBuilder().subject_name(sujeto).sign(clave, hashes.SHA256())
            csr_archivo.write_bytes(csr.public_bytes(serialization.Encoding.PEM))
            print(f"Creado {csr_archivo}")
    print("\nSiguiente paso: pedir los certificados en ARCA con esos CSR (README, 'Alta en ARCA'). "
          f"Guardalos como {datos.certs / 'afip_homo.crt'} y {datos.certs / 'afip_prod.crt'}.")


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
