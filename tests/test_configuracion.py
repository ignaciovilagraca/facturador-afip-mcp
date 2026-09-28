"""Alta guiada desde la conversación, con certificados de prueba firmados por una CA falsa."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from mcp.client.client import Client

from facturador_afip.datos import Datos

from facturador_afip_mcp import server

CUIT = "20111111112"  # ficticio
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def datos(tmp_path, monkeypatch):
    d = Datos(tmp_path / "datos")  # la carpeta todavía no existe, como en una instalación nueva
    monkeypatch.setattr(server, "datos", d)
    return d


async def llamar(client, herramienta, **args):
    r = await client.call_tool(herramienta, args)
    texto = r.content[0].text if r.content else ""
    if r.is_error:
        return True, texto
    return False, r.structured_content if r.structured_content is not None else json.loads(texto)


def certificado_de_arca(datos, entorno, emisor=None, clave=None, vence_en_dias=730):
    """Un certificado como el que da ARCA, para la clave de esta carpeta (o para otra, si se pasa)."""
    if clave is None:
        archivo = datos.certs / ("afip.key" if entorno == "homo" else "afip_prod.key")
        clave = serialization.load_pem_private_key(archivo.read_bytes(), password=None)
    ca = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ahora = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "prueba1a2b"),
                                     x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {CUIT}")]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                                       emisor or ("Computadores Test" if entorno == "homo" else "Computadores"))]))
            .public_key(clave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(ahora - timedelta(days=1)).not_valid_after(ahora + timedelta(days=vence_en_dias))
            .sign(ca, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


async def iniciar(c):
    return await llamar(c, "iniciar_configuracion", cuit="20-11111111-2", nombre="Persona de Prueba",
                        alias="prueba1a2b", condicion_iva="Responsable Monotributo",
                        domicilio_comercial="Calle 123, CABA", ingresos_brutos="Exento",
                        inicio_actividades="2020-01-01")


async def test_alta_completa(datos, tmp_path):
    async with Client(server.mcp, mode="legacy") as c:
        error, e = await llamar(c, "estado_configuracion")
        assert e["configuracion"]["etapa"] == "sin_configurar"
        assert e["configuracion"]["herramienta"] == "iniciar_configuracion"

        # Sin configurar, las herramientas explican qué hacer en lugar de fallar con un error técnico
        error, r = await llamar(c, "probar_conexion", entorno="homo", servicio="wsfe")
        assert error and "estado_configuracion" in r

        error, r = await iniciar(c)
        assert not error, r
        assert sorted(r["creados"]) == ["afip.csr", "afip.key", "afip_prod.csr", "afip_prod.key"]
        assert r["csr"]["homo"]["texto"].startswith("-----BEGIN CERTIFICATE REQUEST-----")
        assert r["csr"]["homo"]["alias"] == "prueba1a2b"
        assert "PRIVATE KEY" not in json.dumps(r)  # la clave nunca sale
        assert r["siguiente"]["etapa"] == "falta_certificado_homo"
        env = (datos.raiz / ".env").read_text()
        assert f"AFIP_CUIT={CUIT}" in env and "AFIP_CONDICION_IVA=Responsable Monotributo" in env
        assert oct((datos.certs / "afip_prod.key").stat().st_mode)[-3:] == "600"

        # Repetir no pisa las claves
        clave = (datos.certs / "afip_prod.key").read_bytes()
        error, r = await iniciar(c)
        assert not error and r["creados"] == [] and (datos.certs / "afip_prod.key").read_bytes() == clave

        # Homologación: el texto que muestra WSASS
        error, r = await llamar(c, "guardar_certificado", entorno="homo", certificado=certificado_de_arca(datos, "homo"))
        assert not error, r
        assert r["alias"] == "prueba1a2b" and r["siguiente"]["etapa"] == "falta_certificado_prod"

        # Producción: la ruta del .crt descargado
        descargado = tmp_path / "Downloads" / "prueba1a2b.crt"
        descargado.parent.mkdir()
        descargado.write_text(certificado_de_arca(datos, "prod"))
        error, r = await llamar(c, "guardar_certificado", entorno="prod", ruta=str(descargado))
        assert not error, r
        assert r["siguiente"]["etapa"] == "sin_perfil"

        error, r = await llamar(c, "guardar_perfil", perfil={
            "nombre": "Persona", "condicion_iva_emisor": "monotributo", "punto_venta_prod_comunes": 5,
            "formato": {"descripcion": "Consultoría"}})
        assert not error, r
        p = r["perfil"]
        assert p["punto_venta_prod_comunes"] == 5 and p["alias_certificado"] == "prueba1a2b"
        assert p["formato"]["descripcion"] == "Consultoría" and p["formato"]["idioma"] == 1  # mezcla con la plantilla
        assert p["cliente_por_defecto"] is None  # la plantilla no deja datos de ejemplo
        assert r["siguiente"]["etapa"] == "listo_para_verificar"


async def test_certificados_equivocados(datos, tmp_path):
    async with Client(server.mcp, mode="legacy") as c:
        await iniciar(c)
        csr = (datos.certs / "afip.csr").read_text()
        casos = [
            ({"entorno": "homo", "certificado": csr}, "Eso es el CSR"),
            ({"entorno": "homo", "certificado": (datos.certs / "afip.key").read_text()}, "clave privada"),
            ({"entorno": "homo", "certificado": certificado_de_arca(datos, "prod")}, "es de producción"),
            ({"entorno": "prod", "certificado": certificado_de_arca(datos, "homo")}, "es de homologación"),
            ({"entorno": "homo", "certificado": certificado_de_arca(
                datos, "homo", clave=rsa.generate_private_key(public_exponent=65537, key_size=2048))},
             "no corresponde a la clave"),
            ({"entorno": "homo", "certificado": "hola"}, "No es un certificado válido"),
            ({"entorno": "prod", "ruta": str(tmp_path / "no-existe.crt")}, "No existe"),
            ({"entorno": "prod", "ruta": str(datos.raiz / ".env")}, "tiene que ser el certificado"),
            ({"entorno": "homo"}, "una de las dos"),
        ]
        for args, esperado in casos:
            error, r = await llamar(c, "guardar_certificado", **args)
            assert error and esperado in r, (args, r)
        assert not (datos.certs / "afip_homo.crt").exists()


async def test_validaciones_de_inicio_y_perfil(datos):
    async with Client(server.mcp, mode="legacy") as c:
        for args, esperado in [({"alias": "con-guion"}, "solo con letras"), ({"cuit": "123"}, "11 dígitos"),
                               ({"inicio_actividades": "01/01/2020"}, "AAAA-MM-DD")]:
            base = {"cuit": CUIT, "nombre": "X", "alias": "abc123"}
            error, r = await llamar(c, "iniciar_configuracion", **(base | args))
            assert error and esperado in r
        await iniciar(c)
        error, r = await llamar(c, "iniciar_configuracion", cuit="20222222223", nombre="Otra", alias="otro1")
        assert error and "otro CUIT" in r
        for perfil, esperado in [({"inventado": 1}, "no válidos"), ({"condicion_iva_emisor": "rico"}, "monotributo"),
                                 ({"punto_venta_prod": "cuatro"}, "número de punto de venta")]:
            error, r = await llamar(c, "guardar_perfil", perfil=perfil)
            assert error and esperado in r


async def test_guia_y_prompt(datos):
    async with Client(server.mcp, mode="legacy") as c:
        error, r = await llamar(c, "guia_alta_arca", seccion="homologacion")
        assert not error
        texto = r["result"] if isinstance(r, dict) else r
        assert "WSASS" in texto and "guardar_certificado" in texto and "<!--" not in texto
        error, r = await llamar(c, "guia_alta_arca", seccion="verificar_y_errores")
        texto = r["result"] if isinstance(r, dict) else r
        assert "El servicio debe ser delegable" in texto
        prompts = (await c.list_prompts()).prompts
        assert [p.name for p in prompts] == ["configurar"]
