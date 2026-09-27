"""Flujo completo con un cliente MCP en memoria y ARCA simulada: nada sale a la red."""
import json

import pytest
from mcp.client.client import Client
from mcp.types import ElicitResult

from facturador_afip_mcp import confirmacion, emision, flujo, server
from facturador_afip_mcp.arca import Auth, ErrorArca
from facturador_afip_mcp.datos import Datos

CUIT = "20111111112"  # ficticio
AUTH = Auth("token", "sign", CUIT)
FACTURA_C = {
    "tipo": "C", "concepto": 2, "fecha": "2026-09-30",
    "receptor": {"nombre": "Cliente de prueba", "doc_tipo": "DNI", "doc_nro": "30123456", "condicion_iva": 5},
    "items": [{"descripcion": "Consultoría", "cantidad": 1, "precio": 150000}],
}

EMISOR = (f"AFIP_CUIT={CUIT}\nAFIP_RAZON_SOCIAL=Emisor de prueba\nAFIP_DOMICILIO_COMERCIAL=Calle 123, CABA\n"
          "AFIP_CONDICION_IVA=Responsable Monotributo\nAFIP_INGRESOS_BRUTOS=Exento\nAFIP_INICIO_ACTIVIDADES=2020-01-01\n")

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Arca:
    """ARCA simulada: numeración por (entorno, punto de venta, tipo) y registro de envíos."""

    def __init__(self):
        self.ultimos = {}
        self.envios = []
        self.falla_red = False
        self.observaciones = []

    def ultimo(self, env, auth, pto, tipo):
        return self.ultimos.get((env, int(pto), tipo), 0)

    def enviar(self, prep, auth, emisor):
        if self.falla_red:
            raise TimeoutError("se cortó la conexión")
        self.envios.append((prep.env, prep.punto_venta, prep.numero))
        self.ultimos[(prep.env, prep.punto_venta, prep.cbte_tipo)] = prep.numero
        return {**prep.salida, "cae": "76123456789012", "vencimiento_cae": "20261010",
                "observaciones": list(self.observaciones), "eventos": [], "emisor": emisor, "factura": prep.factura}


@pytest.fixture
def arca(monkeypatch):
    a = Arca()
    monkeypatch.setattr(emision, "ultimo_comprobante_fe", a.ultimo)
    monkeypatch.setattr(emision, "ultimo_comprobante", a.ultimo)
    monkeypatch.setattr(emision, "enviar", a.enviar)
    monkeypatch.setattr(flujo, "_ultimo", lambda env, auth, servicio, pto, tipo: a.ultimo(env, auth, pto, tipo))
    return a


@pytest.fixture
def datos(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(EMISOR + "FACTURADOR_CONFIRMACION=elicitation\n")
    (tmp_path / "perfil.json").write_text(json.dumps({"punto_venta_prod_comunes": 4, "punto_venta_prod": 5}))
    d = Datos(tmp_path)
    monkeypatch.setattr(d, "login", lambda env, servicio: AUTH)
    monkeypatch.setattr(server, "datos", d)
    return d


def responder(texto=None, accion="accept"):
    """Elicitation que responde siempre lo mismo y guarda los mensajes que recibió."""
    mensajes = []

    async def callback(context, params):
        mensajes.append(params.message)
        numero = texto if texto is not None else params.message.rsplit(": ", 1)[1]
        return ElicitResult(action=accion, content={"numero": numero} if accion == "accept" else None)
    callback.mensajes = mensajes
    return callback


async def llamar(client, herramienta, **args):
    r = await client.call_tool(herramienta, args)
    texto = r.content[0].text if r.content else ""
    if r.is_error:
        return True, texto
    return False, r.structured_content if r.structured_content is not None else json.loads(texto)


async def validar(client, factura=FACTURA_C):
    error, r = await llamar(client, "validar_en_homologacion", factura=factura)
    assert not error, r
    return r


async def test_validar_crea_borrador(datos, arca):
    async with Client(server.mcp, mode="legacy") as c:
        r = await validar(c)
    assert r["borrador_id"].startswith("C-2026-09-30-")
    assert r["entorno"] == "homo" and "no tiene valor fiscal" in r["aviso"]
    assert arca.envios == [("homo", 1, 1)]
    assert datos.leer_borrador(r["borrador_id"])["estado"] == "validado"
    assert (datos.facturas / "homo" / "C-00001-00000001.json").exists()
    assert r["pdf"].endswith(f"{CUIT}_011_00001_00000001.pdf")


async def test_emitir_con_confirmacion(datos, arca):
    cb = responder()
    async with Client(server.mcp, mode="legacy", elicitation_callback=cb) as c:
        borrador = (await validar(c))["borrador_id"]
        arca.ultimos[("prod", 4, 11)] = 41
        error, r = await llamar(c, "emitir_en_produccion", borrador_id=borrador)
        assert not error, r
        assert r["emitida"] and r["comprobante"] == "Factura C 00004-00000042"
        assert arca.envios[-1] == ("prod", 4, 42)
        # El resumen lo arma el servidor con los datos de producción
        assert "Factura C 00004-00000042" in cb.mensajes[0] and "150000" in cb.mensajes[0]
        assert "comprobante fiscal REAL" in cb.mensajes[0]
        assert (datos.facturas / "prod" / "C-00004-00000042.json").exists()
        assert datos.leer_borrador(borrador)["estado"] == "emitido"

        error, r = await llamar(c, "emitir_en_produccion", borrador_id=borrador)
        assert error and "ya se emitió" in r
    assert len([e for e in arca.envios if e[0] == "prod"]) == 1


async def test_sin_elicitation_no_emite(datos, arca):
    async with Client(server.mcp, mode="legacy") as c:
        borrador = (await validar(c))["borrador_id"]
        error, r = await llamar(c, "emitir_en_produccion", borrador_id=borrador)
    assert error and "No se emitió nada" in r
    assert all(e[0] == "homo" for e in arca.envios)


@pytest.mark.parametrize("cb", [responder("00004-00000099"), responder("00004-00000001 "[:-2]), responder(""), responder("si"),
                                responder(accion="decline"), responder(accion="cancel")])
async def test_confirmacion_incorrecta_no_emite(datos, arca, cb):
    async with Client(server.mcp, mode="legacy", elicitation_callback=cb) as c:
        borrador = (await validar(c))["borrador_id"]
        error, r = await llamar(c, "emitir_en_produccion", borrador_id=borrador)
    assert not error and r["emitida"] is False
    assert all(e[0] == "homo" for e in arca.envios)
    assert datos.leer_borrador(borrador)["estado"] == "validado"


async def test_borrador_modificado_no_emite(datos, arca):
    cb = responder()
    async with Client(server.mcp, mode="legacy", elicitation_callback=cb) as c:
        borrador = (await validar(c))["borrador_id"]
        b = datos.leer_borrador(borrador)
        b["factura"]["items"][0]["precio"] = 999999
        datos.guardar_borrador(b)
        error, r = await llamar(c, "emitir_en_produccion", borrador_id=borrador)
    assert error and "cambió" in r
    assert cb.mensajes == []


async def test_tope_en_pesos(datos, arca):
    (datos.raiz / ".env").write_text(datos.raiz.joinpath(".env").read_text() + "FACTURADOR_TOTAL_MAXIMO_ARS=100000\n")
    cb = responder()
    async with Client(server.mcp, mode="legacy", elicitation_callback=cb) as c:
        borrador = (await validar(c))["borrador_id"]
        error, r = await llamar(c, "emitir_en_produccion", borrador_id=borrador)
    assert error and "tope" in r
    assert cb.mensajes == []


async def test_corte_de_red_y_reintento(datos, arca):
    cb = responder()
    async with Client(server.mcp, mode="legacy", elicitation_callback=cb) as c:
        borrador = (await validar(c))["borrador_id"]
        arca.falla_red = True
        error, r = await llamar(c, "emitir_en_produccion", borrador_id=borrador)
        assert error and "No se sabe si ARCA emitió" in r
        assert datos.leer_borrador(borrador)["estado"] == "emitiendo"
        error, r = await llamar(c, "descartar_borrador", borrador_id=borrador)
        assert error

        # ARCA no lo tiene: se puede reintentar
        arca.falla_red = False
        error, r = await llamar(c, "emitir_en_produccion", borrador_id=borrador)
        assert not error and r["emitida"], r


async def test_corte_de_red_pero_arca_lo_emitio(datos, arca, monkeypatch):
    cb = responder()
    async with Client(server.mcp, mode="legacy", elicitation_callback=cb) as c:
        borrador = (await validar(c))["borrador_id"]
        enviar_real = arca.enviar

        def emite_y_se_corta(prep, auth, emisor):
            enviar_real(prep, auth, emisor)
            raise TimeoutError("se cortó después de emitir")
        monkeypatch.setattr(emision, "enviar", emite_y_se_corta)
        error, r = await llamar(c, "emitir_en_produccion", borrador_id=borrador)
        assert error
        envios = len(arca.envios)
        error, r = await llamar(c, "emitir_en_produccion", borrador_id=borrador)
    assert error and "ARCA ya tiene el comprobante" in r
    assert len(arca.envios) == envios


async def test_observaciones_en_homologacion_no_crean_borrador(datos, arca):
    arca.observaciones = ["10013: CUIT receptora inexistente"]
    async with Client(server.mcp, mode="legacy") as c:
        r = await validar(c)
    assert r["borrador_id"] is None and "OBSERVACIONES" in r["aviso"]
    assert datos.listar_borradores() == []


async def test_dialogo_del_sistema(datos, arca, monkeypatch):
    (datos.raiz / ".env").write_text(EMISOR + "FACTURADOR_CONFIRMACION=dialogo\n")
    vistos = []

    def dialogo(mensaje):
        vistos.append(mensaje)
        return mensaje.rsplit(": ", 1)[1]
    monkeypatch.setattr(confirmacion, "dialogo_disponible", lambda: True)
    monkeypatch.setattr(confirmacion, "_dialogo_macos", dialogo)
    monkeypatch.setattr(confirmacion, "_dialogo_zenity", dialogo)
    async with Client(server.mcp, mode="legacy") as c:  # sin elicitation
        borrador = (await validar(c))["borrador_id"]
        error, r = await llamar(c, "emitir_en_produccion", borrador_id=borrador)
    assert not error and r["emitida"], r
    assert "00004-00000001" in vistos[0]


def test_nota_de_credito_informa_comprobante_asociado(arca):
    nc = {**FACTURA_C, "nota_credito_de": {"punto_venta": 4, "numero": 42, "fecha": "2026-09-30"}}
    prep = emision.preparar(nc, "homo", AUTH, 4)
    assert prep.cbte_tipo == 13
    assert "<CbteTipo>13</CbteTipo>" in prep.cuerpo
    assert f"<CbtesAsoc><CbteAsoc><Tipo>11</Tipo><PtoVta>4</PtoVta><Nro>42</Nro><Cuit>{CUIT}</Cuit>" in prep.cuerpo
    assert prep.resumen["titulo"].startswith("Nota de crédito C")
    assert emision.nombre_registro({**prep.salida, "cae": "1"}) == "NC-C-00004-00000001.json"


def test_ids_y_archivos_no_escapan_de_la_carpeta(tmp_path):
    d = Datos(tmp_path)
    for malo in ("../x", "a/b", "", "x" * 200):
        with pytest.raises(ErrorArca):
            d.leer_borrador(malo)
    with pytest.raises(ErrorArca):
        d.registro("prod", "../.env")
