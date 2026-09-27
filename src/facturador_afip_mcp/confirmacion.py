"""Cómo se le pide a una persona que confirme la emisión.

La confirmación la arma el servidor con los datos reales de producción y la responde una persona, no el
modelo: hay que tipear el número de comprobante que se va a emitir. Hay tres formas:

- "dialogo": un diálogo nativo del sistema operativo (macOS con osascript; Linux con zenity). Aparece fuera
  del cliente de MCP, así que funciona con cualquier cliente, incluidos los que no soportan elicitation.
- "elicitation": el formulario del propio cliente de MCP (por ejemplo, Claude Code en la terminal).
- "terminal": para el comando `facturador-afip-mcp emitir`.

Con FACTURADOR_CONFIRMACION=auto (por defecto) se usa el diálogo si está disponible y, si no, elicitation.
"""
import platform
import shutil
import subprocess

import anyio.to_thread
from pydantic import BaseModel, Field

from .arca import ErrorArca

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


async def por_terminal(resumen: str, numero: str) -> bool:
    print(_pedido(resumen, numero))
    return _coincide(await anyio.to_thread.run_sync(lambda: input("> ")), numero)


def elegir(modo: str, ctx):
    """Devuelve el confirmador según FACTURADOR_CONFIRMACION, o un error que explica qué hacer."""
    modo = (modo or "auto").lower()
    if modo not in ("auto", "dialogo", "elicitation"):
        raise ErrorArca(f"FACTURADOR_CONFIRMACION no válido: {modo} (auto, dialogo o elicitation)")
    if modo in ("auto", "dialogo") and dialogo_disponible():
        return por_dialogo
    if modo in ("auto", "elicitation") and cliente_soporta_elicitation(ctx):
        return por_elicitation(ctx)
    raise ErrorArca(
        "No hay forma de pedirle confirmación a una persona: este cliente de MCP no soporta elicitation y no hay "
        "diálogo del sistema disponible. No se emitió nada. Para emitir, que el usuario corra en su terminal: "
        "facturador-afip-mcp emitir <borrador_id>")
