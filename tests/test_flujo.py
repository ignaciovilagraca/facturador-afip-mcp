"""Flujo completo con un cliente MCP en memoria y ARCA simulada: nada sale a la red."""
import json

import pytest
from mcp.client.client import Client
from mcp.client.extension import ClientExtension
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


async def para_emitir(client, borrador):
    """preparar_emision y los argumentos de emitir_en_produccion tal cual los devuelve."""
    error, r = await llamar(client, "preparar_emision", borrador_id=borrador)
    assert not error, r
    return {k: r[k] for k in ("borrador_id", "numero", "receptor", "total")}


async def emitir(client, borrador):
    return await llamar(client, "emitir_en_produccion", **await para_emitir(client, borrador))


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
        error, r = await emitir(c, borrador)
        assert not error, r
        assert r["emitida"] and r["comprobante"] == "Factura C 00004-00000042"
        assert arca.envios[-1] == ("prod", 4, 42)
        # El resumen lo arma el servidor con los datos de producción
        assert "Factura C 00004-00000042" in cb.mensajes[0] and "150000" in cb.mensajes[0]
        assert "comprobante fiscal REAL" in cb.mensajes[0]
        assert (datos.facturas / "prod" / "C-00004-00000042.json").exists()
        assert datos.leer_borrador(borrador)["estado"] == "emitido"

        error, r = await llamar(c, "preparar_emision", borrador_id=borrador)
        assert error and "ya se emitió" in r
    assert len([e for e in arca.envios if e[0] == "prod"]) == 1


async def test_sin_elicitation_no_emite(datos, arca):
    async with Client(server.mcp, mode="legacy") as c:
        borrador = (await validar(c))["borrador_id"]
        error, r = await emitir(c, borrador)
    assert error and "No se emitió nada" in r
    assert all(e[0] == "homo" for e in arca.envios)


@pytest.mark.parametrize("cb", [responder("00004-00000099"), responder("00004-00000001 "[:-2]), responder(""), responder("si"),
                                responder(accion="decline"), responder(accion="cancel")])
async def test_confirmacion_incorrecta_no_emite(datos, arca, cb):
    async with Client(server.mcp, mode="legacy", elicitation_callback=cb) as c:
        borrador = (await validar(c))["borrador_id"]
        error, r = await emitir(c, borrador)
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
        error, r = await llamar(c, "preparar_emision", borrador_id=borrador)
    assert error and "cambió" in r
    assert cb.mensajes == []


async def test_tope_en_pesos(datos, arca):
    (datos.raiz / ".env").write_text(datos.raiz.joinpath(".env").read_text() + "FACTURADOR_TOTAL_MAXIMO_ARS=100000\n")
    cb = responder()
    async with Client(server.mcp, mode="legacy", elicitation_callback=cb) as c:
        borrador = (await validar(c))["borrador_id"]
        error, r = await llamar(c, "preparar_emision", borrador_id=borrador)
    assert error and "tope" in r
    assert cb.mensajes == []


async def test_corte_de_red_y_reintento(datos, arca):
    cb = responder()
    async with Client(server.mcp, mode="legacy", elicitation_callback=cb) as c:
        borrador = (await validar(c))["borrador_id"]
        arca.falla_red = True
        error, r = await emitir(c, borrador)
        assert error and "No se sabe si ARCA emitió" in r
        assert datos.leer_borrador(borrador)["estado"] == "emitiendo"
        error, r = await llamar(c, "descartar_borrador", borrador_id=borrador)
        assert error

        # ARCA no lo tiene: se puede reintentar
        arca.falla_red = False
        error, r = await emitir(c, borrador)
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
        error, r = await emitir(c, borrador)
        assert error
        envios = len(arca.envios)
        error, r = await llamar(c, "preparar_emision", borrador_id=borrador)
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
        error, r = await emitir(c, borrador)
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


class ClienteConApps(ClientExtension):
    """Un cliente que declara MCP Apps, como Claude Desktop."""
    identifier = "io.modelcontextprotocol/ui"

    def settings(self):
        return {"mimeTypes": ["text/html;profile=mcp-app"]}


async def abrir(c, borrador):
    """Lo que hace la tarjeta al abrirse: pide el resumen y un token para esta apertura."""
    error, e = await llamar(c, "estado_confirmacion", borrador_id=borrador)
    assert not error and e["pendiente"], e
    return e["token"]


def orden(datos, valor):
    env = datos.raiz / ".env"
    env.write_text(env.read_text().replace("FACTURADOR_CONFIRMACION=elicitation", f"FACTURADOR_CONFIRMACION={valor}"))


async def test_tarjeta_mcp_apps(datos, arca):
    orden(datos, "apps,elicitation,permiso,dialogo")
    cb = responder()
    async with Client(server.mcp, mode="legacy", elicitation_callback=cb, extensions=[ClienteConApps()]) as c:
        # El modelo no ve confirmar_emision
        herramientas = {t.name: t for t in (await c.list_tools()).tools}
        assert herramientas["confirmar_emision"].meta["ui"]["visibility"] == ["app"]
        assert herramientas["emitir_en_produccion"].meta["ui"]["resourceUri"].startswith("ui://")

        borrador = (await validar(c))["borrador_id"]
        r = await c.call_tool("emitir_en_produccion", await para_emitir(c, borrador))
        assert not r.is_error
        # Nada secreto en la respuesta que puede leer el modelo, ni en el texto ni en structuredContent
        assert r.structured_content == {"confirmacion": "tarjeta", "borrador_id": borrador, "numero": "00004-00000001"}
        assert "Todavía no se emitió nada" in r.content[0].text
        token = await abrir(c, borrador)
        assert token not in json.dumps(datos.leer_borrador(borrador))  # solo se guarda el hash
        assert cb.mensajes == [] and all(e[0] == "homo" for e in arca.envios)

        # Token o número equivocados: no emite
        for args in ({"token": "otro", "numero": "00004-00000001"}, {"token": token, "numero": "00004-00000002"}):
            error, r = await llamar(c, "confirmar_emision", borrador_id=borrador, **args)
            assert error and "No se emitió nada" in r

        error, r = await llamar(c, "confirmar_emision", borrador_id=borrador, token=token, numero="00004-00000001")
        assert not error and r["emitida"], r
        assert arca.envios[-1] == ("prod", 4, 1)

        # Un solo uso
        error, r = await llamar(c, "confirmar_emision", borrador_id=borrador, token=token, numero="00004-00000001")
        assert error
    assert len([e for e in arca.envios if e[0] == "prod"]) == 1


async def test_tarjeta_vencida_o_numero_cambiado(datos, arca, monkeypatch):
    orden(datos, "apps")
    async with Client(server.mcp, mode="legacy", extensions=[ClienteConApps()]) as c:
        borrador = (await validar(c))["borrador_id"]
        await c.call_tool("emitir_en_produccion", await para_emitir(c, borrador))
        token = await abrir(c, borrador)
        arca.ultimos[("prod", 4, 11)] = 1  # alguien emitió otro comprobante en el medio
        error, r = await llamar(c, "confirmar_emision", borrador_id=borrador, token=token, numero="00004-00000001")
        assert error and "alguien emitió otro" in r

        await c.call_tool("emitir_en_produccion", await para_emitir(c, borrador))
        token = await abrir(c, borrador)
        b = datos.leer_borrador(borrador)
        b["pendiente"]["expira"] = "2000-01-01T00:00:00+00:00"
        datos.guardar_borrador(b)
        error, r = await llamar(c, "confirmar_emision", borrador_id=borrador, token=token, numero="00004-00000002")
        assert error and "venció" in r
    assert all(e[0] == "homo" for e in arca.envios)


async def test_permiso_emite_con_los_datos_aprobados(datos, arca):
    orden(datos, "apps,elicitation,permiso,dialogo")
    async with Client(server.mcp, mode="legacy") as c:  # sin Apps ni elicitation
        borrador = (await validar(c))["borrador_id"]
        error, estado = await llamar(c, "estado_configuracion")
        assert estado["confirmacion_de_emision"]["se_usa"] == "permiso"
        error, r = await emitir(c, borrador)
    assert not error and r["emitida"], r


@pytest.mark.parametrize("campo,valor", [("numero", "00004-00000009"), ("total", "PES 1.00"),
                                         ("receptor", "Otro (1)")])
async def test_datos_aprobados_distintos_no_emite(datos, arca, campo, valor):
    orden(datos, "permiso")
    async with Client(server.mcp, mode="legacy") as c:
        borrador = (await validar(c))["borrador_id"]
        args = await para_emitir(c, borrador)
        error, r = await llamar(c, "emitir_en_produccion", **{**args, campo: valor})
    assert error and "no coinciden" in r
    assert all(e[0] == "homo" for e in arca.envios)


async def test_tarjeta_cancelar_y_estado(datos, arca):
    orden(datos, "apps")
    async with Client(server.mcp, mode="legacy", extensions=[ClienteConApps()]) as c:
        borrador = (await validar(c))["borrador_id"]
        await c.call_tool("emitir_en_produccion", await para_emitir(c, borrador))
        token = await abrir(c, borrador)

        # Cada apertura rota el token: el de la apertura anterior ya no sirve
        otro = await abrir(c, borrador)
        error, r = await llamar(c, "confirmar_emision", borrador_id=borrador, token=token, numero="00004-00000001")
        assert error and "Token" in r
        token = otro
        error, e = await llamar(c, "cancelar_emision", borrador_id=borrador, token="falso")
        assert e["estado"] == "validado"
        assert await abrir(c, borrador)  # un token falso no cancela
        token = await abrir(c, borrador)

        # Al volver a abrir la conversación, la tarjeta pregunta y ya no está pendiente
        error, e = await llamar(c, "cancelar_emision", borrador_id=borrador, token=token)
        assert not error and not e["pendiente"]
        error, e = await llamar(c, "estado_confirmacion", borrador_id=borrador)
        assert not e["pendiente"] and e["estado"] == "validado" and "token" not in e
        error, r = await llamar(c, "confirmar_emision", borrador_id=borrador, token=token, numero="00004-00000001")
        assert error and "se canceló" in r

        # Emitida desde una tarjeta nueva: la vieja muestra el comprobante
        await c.call_tool("emitir_en_produccion", await para_emitir(c, borrador))
        nuevo = await abrir(c, borrador)
        error, r = await llamar(c, "confirmar_emision", borrador_id=borrador, token=nuevo, numero="00004-00000001")
        assert not error
        error, e = await llamar(c, "estado_confirmacion", borrador_id=borrador)
        assert e["estado"] == "emitido" and e["comprobante"] == "Factura C 00004-00000001" and "token" not in e
        error, lista = await llamar(c, "listar_borradores")
        lista = lista["result"] if isinstance(lista, dict) else lista
        assert lista[0]["emision"]["entorno"] == "prod"

        error, e = await llamar(c, "estado_confirmacion", borrador_id="C-2026-01-01-noexiste")
        assert e["estado"] == "descartado"
    assert len([e for e in arca.envios if e[0] == "prod"]) == 1


async def test_item_sin_precio_da_error_claro(datos, arca):
    async with Client(server.mcp, mode="legacy") as c:
        error, r = await llamar(c, "validar_en_homologacion",
                                factura={**FACTURA_C, "items": [{"descripcion": "x", "importe": 10}]})
    assert error and "tiene que tener descripcion y precio" in r
