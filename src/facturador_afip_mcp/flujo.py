"""Validar en homologación y emitir en producción, con la confirmación de una persona antes de emitir.

Reglas que este módulo hace cumplir, sin depender de lo que decida el modelo:
- Producción solo emite un borrador ya validado en homologación, y exactamente esa factura (se compara el hash).
- Antes de enviar a ARCA, una persona tiene que tipear el número de comprobante en una confirmación que
  arma el servidor (no el modelo), con los datos reales de producción.
- Un borrador se emite una sola vez. Si la emisión se corta a la mitad, antes de reintentar se consulta a ARCA
  si el comprobante ya existe.
- Opcional: un tope por comprobante en pesos.
"""
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal

import anyio.to_thread

from . import emision
from .arca import ErrorArca, ultimo_comprobante, ultimo_comprobante_fe
from .datos import Datos, huella

# Entorno de emisión real. Los tests lo cambian a "homo" para probar el flujo completo sin valor fiscal.
ENTORNO_EMISION = "prod"

# (texto del resumen, número que hay que tipear) -> si la persona confirmó
Confirmador = Callable[[str, str], Awaitable[bool]]


def _en_hilo(funcion, *args):
    return anyio.to_thread.run_sync(lambda: funcion(*args))


def _ultimo(env, auth, servicio, pto, cbte_tipo):
    if servicio == "wsfe":
        return ultimo_comprobante_fe(env, auth, pto, cbte_tipo)
    return ultimo_comprobante(env, auth, pto, cbte_tipo)


def resumen_texto(prep: emision.Preparada, emisor: dict) -> str:
    r = prep.resumen
    lineas = [r["titulo"], ""]
    if r.get("asociado"):
        lineas.append(f"Anula o ajusta: {r['asociado']}")
    lineas.append(f"Fecha:      {r['fecha']}")
    lineas.append(f"Receptor:   {r['receptor']}")
    if r.get("concepto"):
        lineas.append(f"Concepto:   {r['concepto']}")
    if r.get("periodo"):
        p = r["periodo"]
        lineas.append(f"Período:    {p['desde']} a {p['hasta']}, vence {p['vto_pago']}")
    for desc, cantidad, precio, iva in r["items"]:
        lineas.append(f"  - {desc}: {cantidad} x {precio}" + (f" + IVA {iva}%" if iva is not None else ""))
    if r.get("neto"):
        lineas.append(f"Neto:       {r['moneda']} {r['neto']}   IVA: {r['iva']}")
    lineas.append(f"Total:      {r['moneda']} {r['total']}" + (f" (cotización {r['cotizacion']})" if r["moneda"] != "PES" else ""))
    if r.get("fecha_pago"):
        lineas.append(f"Fecha pago: {r['fecha_pago']}")
    lineas.append(f"Emisor:     CUIT {emisor.get('cuit')}")
    return "\n".join(lineas)


def _pto_produccion(datos: Datos, factura: dict):
    perfil = datos.perfil() or {}
    clave = "punto_venta_prod" if emision.tipo_de(factura) == "E" else "punto_venta_prod_comunes"
    return factura.get("punto_venta") or perfil.get(clave)


def _resultado(salida, registro, pdf_archivo):
    return {
        "comprobante": f"{emision.nombre_comprobante(salida['factura'])} {salida['punto_venta']:05d}-{salida['numero']:08d}",
        "entorno": salida["entorno"],
        "cae": salida["cae"],
        "vencimiento_cae": salida["vencimiento_cae"],
        "total": f"{salida['moneda']} {salida['total']}",
        "observaciones": salida.get("observaciones") or [],
        # El evento 103 es un aviso de mantenimiento que ARCA repite en cada respuesta
        "eventos": [e for e in salida.get("eventos") or [] if not e.startswith("103:")],
        "archivo": registro.name,
        "pdf": str(pdf_archivo) if pdf_archivo else None,
    }


async def validar_en_homologacion(datos: Datos, factura: dict, punto_venta_homo: int | None = None) -> dict:
    """Emite la factura en homologación (sin valor fiscal) y, si ARCA la aprueba, crea el borrador."""
    servicio = emision.servicio_de(factura)
    auth = await _en_hilo(datos.login, "homo", servicio)
    prep = await _en_hilo(emision.preparar, factura, "homo", auth, punto_venta_homo)
    salida = await _en_hilo(emision.enviar, prep, auth, datos.emisor())
    registro, pdf_archivo = await _en_hilo(datos.guardar_comprobante, salida, emision.nombre_registro(salida))
    resultado = _resultado(salida, registro, pdf_archivo)
    resultado["aviso"] = "Homologación: no tiene valor fiscal y no se emitió nada real."
    if salida.get("observaciones") and servicio == "wsfe":
        # ARCA a veces aprueba y a la vez pide anular (por ejemplo, CUIT receptora inexistente)
        resultado["borrador_id"] = None
        resultado["aviso"] += (" ARCA la aprobó CON OBSERVACIONES: no se creó el borrador. Mostráselas al usuario "
                               "y corregí los datos antes de validar de nuevo.")
        return resultado
    borrador = datos.crear_borrador(dict(factura), {
        "punto_venta": salida["punto_venta"], "numero": salida["numero"], "cae": salida["cae"],
        "archivo": registro.name, "resumen": prep.resumen,
    })
    resultado["borrador_id"] = borrador["id"]
    return resultado


async def _reconciliar(datos: Datos, borrador: dict, auth) -> None:
    """Un borrador quedó en "emitiendo" (se cortó la conexión, se cerró el cliente): ¿ARCA lo emitió?"""
    e = borrador["emision"]
    ultimo = await _en_hilo(_ultimo, ENTORNO_EMISION, auth, e["servicio"], e["punto_venta"], e["cbte_tipo"])
    if ultimo >= e["numero"]:
        raise ErrorArca(
            f"La emisión anterior de este borrador se cortó y ARCA ya tiene el comprobante "
            f"{e['punto_venta']:05d}-{e['numero']:08d} (último autorizado: {ultimo}). Es muy probable que se haya "
            "emitido: revisalo en ARCA (Comprobantes en línea > Consultas) antes de hacer nada. No se reintenta.")
    borrador["estado"] = "validado"
    borrador.setdefault("historial", []).append(
        {"cuando": _ahora(), "evento": f"emisión cortada; ARCA no tiene el {e['numero']}, se puede reintentar"})
    borrador["emision"] = None
    datos.guardar_borrador(borrador)


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def emitir_borrador(datos: Datos, borrador_id: str, confirmar: Confirmador) -> dict:
    env = ENTORNO_EMISION
    borrador = datos.leer_borrador(borrador_id)
    if borrador["estado"] == "emitido":
        e = borrador["emision"]
        raise ErrorArca(f"Este borrador ya se emitió: {e['comprobante']}, CAE {e['cae']}. No se emite dos veces.")
    factura = borrador["factura"]
    if huella(factura) != borrador["huella"]:
        raise ErrorArca("La factura del borrador cambió después de validarla en homologación. Validala de nuevo.")

    servicio = emision.servicio_de(factura)
    auth = await _en_hilo(datos.login, env, servicio)
    if borrador["estado"] == "emitiendo":
        await _reconciliar(datos, borrador, auth)

    prep = await _en_hilo(emision.preparar, factura, env, auth, _pto_produccion(datos, factura))
    total_ars = Decimal(prep.salida["total"]) * Decimal(prep.salida["cotizacion"])
    tope = datos.total_maximo_ars()
    if tope is not None and total_ars > tope:
        raise ErrorArca(f"El total equivale a ARS {total_ars:.2f}, más que el tope de ARS {tope} "
                        "(FACTURADOR_TOTAL_MAXIMO_ARS en .env). No se emitió nada.")

    numero = f"{prep.punto_venta:05d}-{prep.numero:08d}"
    if not await confirmar(resumen_texto(prep, datos.emisor()), numero):
        return {"emitida": False, "mensaje": "La persona no confirmó la emisión. No se emitió nada."}

    # Entre la confirmación y el envío pudo pasar tiempo: se relee el borrador por si cambió
    borrador = datos.leer_borrador(borrador_id)
    if borrador["estado"] != "validado" or borrador["huella"] != huella(factura):
        raise ErrorArca("El borrador cambió mientras se esperaba la confirmación. No se emitió nada.")
    borrador["estado"] = "emitiendo"
    borrador["emision"] = {"inicio": _ahora(), "servicio": servicio, "cbte_tipo": prep.cbte_tipo,
                           "punto_venta": prep.punto_venta, "numero": prep.numero}
    datos.guardar_borrador(borrador)

    try:
        salida = await _en_hilo(emision.enviar, prep, auth, datos.emisor())
    except emision.Rechazada as e:
        borrador["estado"] = "validado"
        borrador["emision"] = None
        borrador.setdefault("historial", []).append({"cuando": _ahora(), "evento": str(e)})
        datos.guardar_borrador(borrador)
        raise
    except Exception as e:
        # No se sabe si ARCA la emitió: el borrador queda en "emitiendo" y el próximo intento lo verifica
        raise ErrorArca(f"No se sabe si ARCA emitió {numero}: {e}. Volvé a llamar a emitir_en_produccion con el "
                        "mismo borrador: primero consulta a ARCA si se emitió y solo reintenta si no.") from e

    registro, pdf_archivo = await _en_hilo(datos.guardar_comprobante, salida, emision.nombre_registro(salida))
    resultado = _resultado(salida, registro, pdf_archivo)
    borrador["estado"] = "emitido"
    borrador["emision"] = {**borrador["emision"], "fin": _ahora(), "comprobante": resultado["comprobante"],
                           "cae": salida["cae"], "archivo": registro.name}
    datos.guardar_borrador(borrador)
    resultado["emitida"] = True
    if salida.get("observaciones"):
        resultado["aviso"] = ("ARCA la aprobó CON OBSERVACIONES. Algunas indican que hay que anularla con una nota "
                              "de crédito: mostráselas al usuario.")
    return resultado
