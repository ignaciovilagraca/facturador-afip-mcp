"""Servidor MCP: herramientas para preparar, validar y emitir facturas electrónicas de ARCA."""
import functools
import json
import logging
import sys
from typing import Any, Literal

import anyio.to_thread
from importlib import resources

from mcp.server.apps import Apps
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from . import confirmacion, emision, flujo
from .arca import (ErrorArca, punto_de_venta_activo, punto_de_venta_activo_fe, puntos_de_venta, puntos_de_venta_fe,
                   tabla_parametro, wsfe, wsfex)
from .datos import Datos

INSTRUCCIONES = """\
Emite facturas electrónicas de ARCA (ex AFIP): Facturas A, B y C (WSFE), notas de crédito de A, B y C, y Factura E de \
exportación (WSFEX). Una factura emitida en producción es un comprobante fiscal real: no se borra, solo se anula con \
una nota de crédito.

Flujo, siempre en este orden:
1. Juntá los datos. Leé el perfil con ver_perfil: trae el cliente por defecto, el formato (un solo ítem, descripción, \
idioma, forma de pago) y las reglas de fechas. Para un cliente que ya se facturó, buscá con listar_comprobantes y \
ver_comprobante y reutilizá sus datos. Si el usuario pasa un invoice (PDF o imagen), armá la factura equivalente: \
controlá que la suma de las líneas dé el total y que el Tax ID del vendedor sea el CUIT del usuario.
2. Mostrale al usuario los datos (tipo, cliente, ítems, total, fechas) y esperá su OK. Si corrige algo, mostrá la \
versión completa de nuevo.
3. Con el OK, llamá a validar_en_homologacion. No tiene valor fiscal; aclaráselo al usuario al contarle el resultado.
4. Preguntale expresamente si la emite en producción. Confirmar los datos NO es aprobar la emisión: hace falta un \
"emitila" o equivalente para ESA factura. Cada factura necesita su propia aprobación, y otra vez si cambió algún dato.
5. Llamá a preparar_emision: devuelve el número de comprobante, el receptor, el total y la cotización reales de \
producción. Mostráselos al usuario y pedile la aprobación con esos datos.
6. Solo con esa aprobación, llamá a emitir_en_produccion con borrador_id, numero, receptor y total exactamente como \
los devolvió preparar_emision. Según el cliente, la persona confirma en una tarjeta dentro del chat (tipeando el \
número), en un formulario, en el diálogo de permiso o en un diálogo del sistema. Vos no podés ni debés responder esa \
confirmación. Si la respuesta dice que falta la confirmación en la tarjeta, no reintentes: esperá a que la persona \
confirme y después revisá el resultado con listar_borradores. Si la persona cancela, no reintentes por tu cuenta.
7. Contale el número, el CAE, el vencimiento del CAE y dónde quedó el PDF. Si hay observaciones, mostráselas: \
algunas obligan a anular la factura con una nota de crédito.
Si el usuario no aprueba la emisión, descartá el borrador con descartar_borrador.

Tipo de comprobante: cliente del exterior, Factura E. En Argentina, según perfil.condicion_iva_emisor: monotributo \
emite C; responsable inscripto emite A a responsables inscriptos y B al resto.

Factura E (sin "tipo" o "tipo": "E"): {"tipo_expo": 2, "pais_destino": 212, "cliente": {"nombre", "cuit_pais", \
"id_impositivo", "domicilio"}, "moneda": "DOL", "idioma": 1, "forma_pago", "fecha": "AAAA-MM-DD", "fecha_pago": \
"AAAA-MM-DD", "items": [{"codigo": "SERV", "descripcion", "cantidad": 1, "unidad": 7, "precio"}]}. tipo_expo 2 \
servicios, 1 bienes, 4 otros. Moneda: DOL (dólar), 060 (euro), PES. Países comunes: Estados Unidos 212 (CUIT país \
55000002126 empresa, 50000002124 persona física); Reino Unido 426 (55000004269 / 50000004267); España 410. Para \
otros usá buscar_codigo. El domicilio del cliente es obligatorio.

Facturas A, B y C: {"tipo": "C", "concepto": 2, "receptor": {"nombre", "doc_tipo": "CUIT|CUIL|DNI|CF", "doc_nro", \
"condicion_iva", "domicilio"}, "moneda": "PES", "fecha", "servicio_desde", "servicio_hasta", "fecha_vto_pago", \
"condicion_venta": "Contado", "items": [{"descripcion", "cantidad", "precio", "iva": 21}]}. concepto 1 productos, 2 \
servicios, 3 ambos. condicion_iva del receptor: 1 responsable inscripto, 4 exento, 5 consumidor final, 6 monotributo. \
En C el precio es final; en A y B el precio es el neto y cada ítem lleva "iva" (0, 2.5, 5, 10.5, 21, 27). La A exige \
CUIT. Nota de crédito: la misma factura con "nota_credito_de": {"punto_venta", "numero", "fecha"} de la original, \
de la misma letra; para anularla entera se repiten receptor, concepto, período e ítems. No hay notas de crédito de \
Factura E.

Fechas (perfil.fechas, para la Factura E): emision "ultimo_dia_mes_trabajado" o "hoy"; pago \
"primer_dia_habil_mes_siguiente" (saltea sábados, domingos, 1 de enero y 1 de mayo) o "igual_emision". Poné siempre \
fecha y fecha_pago explícitas. ARCA acepta fecha hasta 5 días antes o después de hoy (10 con servicios en A, B y C) \
y no deja fechas anteriores a la última factura del punto de venta. Si la regla da una fecha fuera de rango, no la \
cambies sola: proponé al usuario la más cercana permitida. fecha_pago no puede ser anterior a fecha.

Errores comunes en homologación: "fecha anterior a la última del punto de venta" es un resto de pruebas, repetí con \
otro punto_venta_homo (3, 4...). 1500: fecha fuera de rango. 1674: fecha de pago anterior a la emisión. 2053: \
cotización; el mensaje trae la que espera ARCA, ponela en "cotizacion" y avisale al usuario. Las cotizaciones de \
homologación son de prueba.

Configuración: estado_configuracion muestra qué falta (CUIT, certificados, perfil). El alta en ARCA (clave, CSR, \
certificados, autorizaciones, puntos de venta) está explicada en el README del proyecto; nunca le pidas al usuario \
su clave privada.
"""

log = logging.getLogger("facturador_afip_mcp")
datos = Datos.desde_entorno()
TARJETA = "ui://facturador-afip/confirmar-emision.html"
apps = Apps()
apps.add_html_resource(TARJETA, (resources.files("facturador_afip_mcp") / "tarjeta.html").read_text(),
                       name="Confirmar emisión", prefers_border=False)

LECTURA = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _hilo(funcion, *args):
    return anyio.to_thread.run_sync(lambda: funcion(*args))


def _errores_como_herramienta(funcion):
    """ErrorArca es un error esperado: el modelo recibe el mensaje, sin traceback."""
    @functools.wraps(funcion)
    async def envuelta(*args, **kwargs):
        try:
            return await funcion(*args, **kwargs)
        except ErrorArca as e:
            raise ToolError(str(e)) from e
    return envuelta


def _resultado(datos_resultado: dict, texto: str | None = None) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=texto or json.dumps(datos_resultado, ensure_ascii=False,
                                                                                     indent=2))],
                          structured_content=datos_resultado)


@apps.tool(resource_uri=TARJETA, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                                              idempotentHint=False, openWorldHint=True))
@_errores_como_herramienta
async def emitir_en_produccion(borrador_id: str, numero: str, receptor: str, total: str, ctx: Context) -> CallToolResult:
    """EMITE UN COMPROBANTE FISCAL REAL en ARCA producción. No se puede deshacer: solo se anula con una nota de
    crédito. Llamala únicamente si el usuario aprobó de forma expresa la emisión de ESTE borrador en este momento,
    con numero, receptor y total exactamente como los devolvió preparar_emision: si no coinciden con los reales,
    no se emite. Antes de enviar, una persona confirma (tarjeta en el chat, formulario, permiso o diálogo del
    sistema); si no confirma, no se emite nada."""
    forma = confirmacion.elegir(datos.env.get("FACTURADOR_CONFIRMACION"), ctx)
    esperado = {"numero": numero, "receptor": receptor, "total": total}
    if forma == "apps":
        em = await flujo.preparar_emision(datos, borrador_id)
        flujo.verificar_esperado(em, **esperado)
        token = flujo.crear_pendiente(datos, em)
        # El token va solo en structuredContent (para la tarjeta), no en el texto que lee el modelo
        return CallToolResult(
            content=[TextContent(type="text", text=(
                f"Todavía no se emitió nada. Falta que la persona confirme en la tarjeta: tiene que escribir {em.numero} "
                "y apretar Emitir. No vuelvas a llamar a esta herramienta: el resultado aparece en la tarjeta y se "
                f"puede ver con listar_borradores. La confirmación vence en {int(flujo.VIGENCIA_PENDIENTE.total_seconds() // 60)} minutos."))],
            structured_content={"borrador_id": borrador_id, "numero": em.numero, "resumen": em.resumen, "token": token})
    confirmar = {"elicitation": confirmacion.por_elicitation(ctx), "permiso": confirmacion.por_permiso,
                 "dialogo": confirmacion.por_dialogo}[forma]
    return _resultado(await flujo.emitir_borrador(datos, borrador_id, confirmar, esperado))


@apps.tool(resource_uri=TARJETA, visibility=["app"],
           annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                                       openWorldHint=True))
@_errores_como_herramienta
async def confirmar_emision(borrador_id: str, token: str, numero: str) -> CallToolResult:
    """Solo para la tarjeta de confirmación: emite el borrador con el token de un solo uso y el número tipeado."""
    return _resultado(await flujo.confirmar_pendiente(datos, borrador_id, token, numero))


mcp = MCPServer("facturador-afip", instructions=INSTRUCCIONES, extensions=[apps])


@mcp.tool(annotations=LOCAL)
@_errores_como_herramienta
async def estado_configuracion(ctx: Context) -> dict:
    """Muestra la configuración: carpeta de datos, CUIT, certificados (y cuándo vencen), perfil y cómo se va a
    pedir la confirmación de emisión. Usala primero si algo falla o si el usuario está configurando."""
    env = datos.env
    perfil = datos.perfil()
    orden = env.get("FACTURADOR_CONFIRMACION") or confirmacion.ORDEN_POR_DEFECTO
    try:
        confirmar = {"se_usa": confirmacion.elegir(orden, ctx)}
    except ErrorArca as e:
        confirmar = {"se_usa": None, "error": str(e)}
    confirmar |= {"orden": orden, "disponibles_con_este_cliente": confirmacion.disponibles(ctx)}
    faltan = [c for c in ("AFIP_RAZON_SOCIAL", "AFIP_DOMICILIO_COMERCIAL", "AFIP_CONDICION_IVA",
                          "AFIP_INGRESOS_BRUTOS", "AFIP_INICIO_ACTIVIDADES") if not env.get(c)]
    return {
        "carpeta": str(datos.raiz),
        "cuit": datos.cuit or None,
        "datos_emisor_faltantes": faltan,
        "certificados": {"homo": datos.certificado("homo"), "prod": datos.certificado("prod")},
        "perfil": "cargado" if perfil else f"falta {datos.raiz / 'perfil.json'}",
        "total_maximo_ars": str(datos.total_maximo_ars() or "sin tope"),
        "confirmacion_de_emision": confirmar,
    }


@mcp.tool(annotations=LOCAL)
@_errores_como_herramienta
async def ver_perfil() -> dict:
    """Devuelve perfil.json: nombre, puntos de venta de producción, condición frente al IVA, cliente por defecto,
    formato y reglas de fechas."""
    perfil = datos.perfil()
    if perfil is None:
        raise ErrorArca(f"No existe {datos.raiz / 'perfil.json'}. Copiá el perfil.example.json del proyecto y completalo.")
    return perfil


@mcp.tool(annotations=LECTURA)
@_errores_como_herramienta
async def probar_conexion(entorno: Literal["homo", "prod"], servicio: Literal["wsfe", "wsfex"]) -> dict:
    """Prueba de solo lectura: estado del servicio, login y puntos de venta. wsfe: A, B y C; wsfex: Factura E."""
    def probar():
        if servicio == "wsfex":
            estado = {c.tag.split("}")[1]: c.text for c in wsfex(entorno, "FEXDummy", None)}
            ptos, err = puntos_de_venta(entorno, datos.login(entorno, "wsfex"))
            ptos = [{"numero": p[0], "bloqueado": p[1], "baja": p[2]} for p in ptos]
        else:
            estado = {c.tag.split("}")[1]: c.text for c in wsfe(entorno, "FEDummy", None)}
            ptos, err = puntos_de_venta_fe(entorno, datos.login(entorno, "wsfe"))
            ptos = [{"numero": p[0], "tipo": p[1], "bloqueado": p[2], "baja": p[3]} for p in ptos]
        return {"servidores": estado, "login": "OK", "puntos_de_venta": ptos, "errores": err or None}
    return await _hilo(probar)


@mcp.tool(annotations=LECTURA)
@_errores_como_herramienta
async def ultimo_comprobante(entorno: Literal["homo", "prod"], tipo: Literal["A", "B", "C", "E"],
                             nota_credito: bool = False, punto_venta: int | None = None) -> dict:
    """Último número autorizado por ARCA para un tipo de comprobante y punto de venta (por defecto, el del perfil
    en producción o el primero activo)."""
    factura = {"tipo": tipo, "nota_credito_de": nota_credito}
    servicio, cbte_tipo = emision.servicio_de(factura), emision.cbte_tipo_de(factura)

    def consultar():
        auth = datos.login(entorno, servicio)
        pto = punto_venta or (flujo._pto_produccion(datos, {"tipo": tipo}) if entorno == "prod" else None) or (
            punto_de_venta_activo_fe(entorno, auth) if servicio == "wsfe" else punto_de_venta_activo(entorno, auth))
        if not pto:
            raise ErrorArca("No hay punto de venta activo para ese tipo de comprobante")
        return {"punto_venta": int(pto), "ultimo": flujo._ultimo(entorno, auth, servicio, pto, cbte_tipo)}
    return await _hilo(consultar)


TABLAS = {
    "paises": ("FEXGetPARAM_DST_pais", "ClsFEXResponse_DST_pais", "DST_Codigo", "DST_Ds"),
    "cuit_pais": ("FEXGetPARAM_DST_CUIT", "ClsFEXResponse_DST_cuit", "DST_CUIT", "DST_Ds"),
    "monedas": ("FEXGetPARAM_MON", "ClsFEXResponse_Mon", "Mon_Id", "Mon_Ds"),
}


@mcp.tool(annotations=LECTURA)
@_errores_como_herramienta
async def buscar_codigo(tabla: Literal["paises", "cuit_pais", "monedas"], texto: str) -> list[dict]:
    """Busca en las tablas de ARCA: código de país destino, CUIT genérico del país del cliente (uno para personas
    jurídicas y otro para físicas) o código de moneda. Consulta homologación, que tiene las mismas tablas."""
    def buscar():
        filas = tabla_parametro("homo", datos.login("homo", "wsfex"), *TABLAS[tabla])
        return [{"codigo": c, "descripcion": d} for c, d in filas if texto.upper() in d.upper()]
    return await _hilo(buscar)


def _resumen_registro(archivo):
    s = json.loads(archivo.read_text())
    f = s.get("factura", {})
    receptor = (f.get("cliente") or f.get("receptor") or {}).get("nombre")
    return {"archivo": archivo.name, "fecha": s.get("fecha"), "receptor": receptor,
            "total": f"{s.get('moneda')} {s.get('total')}", "cae": s.get("cae")}


@mcp.tool(annotations=LOCAL)
@_errores_como_herramienta
async def listar_comprobantes(entorno: Literal["homo", "prod"] = "prod", buscar: str | None = None,
                              limite: int = 20) -> list[dict]:
    """Comprobantes guardados en la carpeta de datos, del más nuevo al más viejo. `buscar` filtra por texto
    (nombre del cliente, archivo, fecha)."""
    carpeta = datos.carpeta(entorno)
    registros = sorted(carpeta.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if carpeta.exists() else []
    resultado = []
    for archivo in registros:
        r = _resumen_registro(archivo)
        if not buscar or buscar.lower() in json.dumps(r, ensure_ascii=False).lower():
            resultado.append(r)
        if len(resultado) >= limite:
            break
    return resultado


@mcp.tool(annotations=LOCAL)
@_errores_como_herramienta
async def ver_comprobante(archivo: str, entorno: Literal["homo", "prod"] = "prod") -> dict:
    """JSON completo de un comprobante guardado (campo "factura": los datos originales, para reutilizarlos o para
    armar una nota de crédito)."""
    return json.loads(datos.registro(entorno, archivo).read_text())


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
@_errores_como_herramienta
async def generar_pdf(archivo: str, entorno: Literal["homo", "prod"] = "prod") -> dict:
    """Regenera el PDF de un comprobante guardado, con el diseño de "Comprobantes en línea". No toca ARCA."""
    registro = datos.registro(entorno, archivo)
    return {"pdf": str(await _hilo(datos.generar_pdf, json.loads(registro.read_text()), registro))}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                                      openWorldHint=True))
@_errores_como_herramienta
async def validar_en_homologacion(factura: dict[str, Any], punto_venta_homo: int | None = None) -> dict:
    """Valida la factura emitiéndola en homologación (sin valor fiscal) y, si ARCA la aprueba sin observaciones,
    crea un borrador listo para producción. Llamala solo después de que el usuario confirmó los datos.
    `factura` tiene el formato descripto en las instrucciones del servidor. `punto_venta_homo` sirve para esquivar
    el error de fechas de pruebas anteriores."""
    return await flujo.validar_en_homologacion(datos, factura, punto_venta_homo)


@mcp.tool(annotations=LECTURA)
@_errores_como_herramienta
async def preparar_emision(borrador_id: str) -> dict:
    """Arma la emisión en producción de un borrador SIN emitir: devuelve el número de comprobante, el receptor, el
    total y la cotización reales, para mostrárselos al usuario antes de pedirle la aprobación. Solo lee de ARCA."""
    em = await flujo.preparar_emision(datos, borrador_id)
    return {**em.para_confirmar(), "cotizacion": em.prep.resumen["cotizacion"], "resumen": em.resumen}


@mcp.tool(annotations=LOCAL)
@_errores_como_herramienta
async def listar_borradores() -> list[dict]:
    """Borradores validados en homologación y su estado: validado, emitiendo o emitido."""
    return [{"borrador_id": b["id"], "estado": b["estado"], "creado": b["creado"],
             "homologacion": b["homologacion"].get("resumen", {}).get("titulo"),
             "emision": (b.get("emision") or {}).get("comprobante")} for b in datos.listar_borradores()]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True,
                                      openWorldHint=False))
@_errores_como_herramienta
async def descartar_borrador(borrador_id: str) -> dict:
    """Borra un borrador que no se va a emitir. No afecta nada en ARCA."""
    b = datos.descartar_borrador(borrador_id)
    return {"descartado": b["id"], "estado": b["estado"]}


def main():
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    mcp.run("stdio")
