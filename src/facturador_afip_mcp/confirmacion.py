"""Cómo se le pide a una persona que confirme la emisión en producción.

Formas, en el orden en que se prueban por defecto (FACTURADOR_CONFIRMACION, lista separada por comas):

- "apps": una tarjeta dentro del chat (MCP Apps), en los clientes que la soportan (Claude Desktop). La persona
  tipea el número de comprobante y aprieta Emitir; el botón llama a una herramienta que el modelo no ve.
- "elicitation": el formulario del cliente de MCP (Claude Code en la terminal). Hay que tipear el número.
- "permiso": el diálogo de permiso del cliente. emitir_en_produccion recibe número, receptor y total, así el
  diálogo los muestra, y el servidor verifica que sean los reales. Solo es una confirmación si la herramienta
  está en "ask": el servidor no puede saber si la aprobó una persona.
- "dialogo": un diálogo nativo del sistema (macOS con osascript; Linux con zenity), fuera del cliente.

El comando `facturador-afip-mcp emitir` confirma en la terminal.
"""
import platform
import shutil
import subprocess

import anyio.to_thread
from mcp.server.apps import client_supports_apps
from pydantic import BaseModel, Field

from facturador_afip.arca import ErrorArca

ESPERA_SEGUNDOS = 300
TITULO = "Facturador ARCA: emitir en PRODUCCIÓN"


def _pedido(resumen, numero):
    return (f"{resumen}\n\nEsto emite un comprobante fiscal REAL ante ARCA. No se puede borrar: solo se anula con una "
            f"nota de crédito.\n\nPara emitirlo, escribí el número de comprobante: {numero}")


def _coincide(respuesta, numero):
    return (respuesta or "").strip() == numero


def dialogo_disponible() -> bool:
    if platform.system() == "Darwin":
        return shutil.which("osascript") is not None
    return platform.system() == "Linux" and shutil.which("zenity") is not None


def _dialogo_macos(mensaje) -> str | None:
    # El texto va como argumento, no dentro del script: así no hay forma de inyectar AppleScript
    script = [
        "on run argv",
        f'set r to display dialog (item 1 of argv) default answer "" with title "{TITULO}" '
        f'buttons {{"Cancelar", "Emitir"}} default button "Cancelar" cancel button "Cancelar" '
        f"with icon caution giving up after {ESPERA_SEGUNDOS}",
        "if gave up of r then return \"\"",
        "return text returned of r",
        "end run",
    ]
    args = ["osascript"] + [a for linea in script for a in ("-e", linea)] + [mensaje]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=ESPERA_SEGUNDOS + 30)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _dialogo_zenity(mensaje) -> str | None:
    proc = subprocess.run(["zenity", "--entry", f"--title={TITULO}", f"--text={mensaje}",
                           f"--timeout={ESPERA_SEGUNDOS}"], capture_output=True, text=True,
                          timeout=ESPERA_SEGUNDOS + 30)
    return proc.stdout.strip() if proc.returncode == 0 else None


async def por_dialogo(resumen: str, numero: str) -> bool:
    mostrar = _dialogo_macos if platform.system() == "Darwin" else _dialogo_zenity
    respuesta = await anyio.to_thread.run_sync(lambda: mostrar(_pedido(resumen, numero)))
    return _coincide(respuesta, numero)


class Confirmacion(BaseModel):
    numero: str = Field(title="Número de comprobante",
                        description="Escribí el número de comprobante que figura arriba para emitirlo")


def cliente_soporta_elicitation(ctx) -> bool:
    capacidades = ctx.request_context.session.client_capabilities
    return bool(capacidades and capacidades.elicitation is not None)


def por_elicitation(ctx):
    async def confirmar(resumen: str, numero: str) -> bool:
        r = await ctx.elicit(_pedido(resumen, numero), Confirmacion)
        return r.action == "accept" and _coincide(r.data.numero, numero)
    return confirmar


async def por_permiso(resumen: str, numero: str) -> bool:
    """La confirmación fue el diálogo de permiso del cliente, que mostró número, receptor y total."""
    return True


async def por_terminal(resumen: str, numero: str) -> bool:
    print(_pedido(resumen, numero))
    return _coincide(await anyio.to_thread.run_sync(lambda: input("> ")), numero)


ORDEN_POR_DEFECTO = "apps,elicitation,permiso,dialogo"
FORMAS = ("apps", "elicitation", "permiso", "dialogo")


def cliente_soporta_apps(ctx) -> bool:
    return client_supports_apps(ctx)


def disponibles(ctx) -> dict:
    return {"apps": cliente_soporta_apps(ctx), "elicitation": cliente_soporta_elicitation(ctx), "permiso": True,
            "dialogo": dialogo_disponible()}


def elegir(orden: str | None, ctx) -> str:
    """La primera forma de confirmar de la lista que esté disponible con este cliente."""
    formas = [f.strip().lower() for f in (orden or ORDEN_POR_DEFECTO).split(",") if f.strip()]
    if "auto" in formas:  # compatibilidad con la versión anterior
        formas = ORDEN_POR_DEFECTO.split(",")
    invalidas = [f for f in formas if f not in FORMAS]
    if invalidas:
        raise ErrorArca(f"FACTURADOR_CONFIRMACION no válido: {', '.join(invalidas)} (válidas: {', '.join(FORMAS)})")
    hay = disponibles(ctx)
    for forma in formas:
        if hay[forma]:
            return forma
    raise ErrorArca(
        f"No hay forma de pedirle confirmación a una persona con este cliente (FACTURADOR_CONFIRMACION={orden}). "
        "No se emitió nada. Para emitir, que el usuario corra en su terminal: facturador-afip-mcp emitir <borrador_id>")
