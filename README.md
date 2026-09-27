# facturador-afip-mcp

Servidor MCP para emitir facturas electrónicas de ARCA (ex AFIP) desde Claude u otro cliente de MCP:

- **Facturas A, B y C** y sus **notas de crédito**, por WSFE.
- **Factura E** (exportación), por WSFEX.

Arma el PDF con el diseño de "Comprobantes en línea", lleva la numeración y guarda cada comprobante. Está basado en [facturador-afip](https://github.com/ignaciovilagraca/facturador-afip) y usa la misma carpeta de datos.

## Índice

- [Cómo protege la emisión](#cómo-protege-la-emisión)
- [Credenciales y carpeta de datos](#credenciales-y-carpeta-de-datos)
- [Instalación](#instalación)
- [Uso con Claude Code](#uso-con-claude-code)
- [Uso con Claude Desktop](#uso-con-claude-desktop)
- [Herramientas](#herramientas)
- [Comandos](#comandos)
- [Desarrollo](#desarrollo)
- [Limitaciones](#limitaciones)

## Cómo protege la emisión

Una factura emitida en producción es un comprobante fiscal real: no se borra, solo se anula con una nota de crédito. Por eso emitir pasa por varias barreras, y las del servidor no dependen de lo que decida el modelo:

1. **Primero homologación.** `emitir_en_produccion` no recibe datos, solo el id de un borrador que `validar_en_homologacion` ya emitió con éxito en homologación. Se compara un hash: producción emite exactamente la factura validada.
2. **El permiso del cliente.** En Claude Code, `emitir_en_produccion` va en `ask` (ver [más abajo](#uso-con-claude-code)): cada llamada pide aprobación.
3. **La confirmación de una persona.** Antes de enviar a ARCA, el servidor arma un resumen con los datos reales de producción (número, cotización, total) y pide **tipear el número de comprobante**. La confirmación no pasa por el modelo:
   - **Diálogo del sistema** (macOS con `osascript`, Linux con `zenity`). Funciona con cualquier cliente.
   - **Elicitation**: el formulario del cliente de MCP, en los clientes que lo soportan (Claude Code en la terminal).
4. **Una sola vez.** Un borrador emitido no se puede volver a emitir. Si la conexión se corta a mitad de la emisión, el próximo intento primero consulta a ARCA si el comprobante ya existe y solo reintenta si no.
5. **Tope opcional** por comprobante, en pesos (`FACTURADOR_TOTAL_MAXIMO_ARS`).

`FACTURADOR_CONFIRMACION` elige la forma de confirmar: `auto` (por defecto: el diálogo si está disponible y, si no, elicitation), `dialogo` o `elicitation`. Si no hay ninguna disponible, el servidor no emite y explica cómo hacerlo desde la terminal.

## Credenciales y carpeta de datos

El servidor corre en tu computadora. La clave privada y el certificado se leen de disco al firmar el login con ARCA: nunca pasan por el modelo ni por los argumentos de una herramienta.

Todo vive en una carpeta de datos, `$FACTURADOR_AFIP_DIR` o `~/.facturador-afip`:

```
.env            AFIP_CUIT, datos del emisor para el PDF, FACTURADOR_TOTAL_MAXIMO_ARS, FACTURADOR_CONFIRMACION
perfil.json     puntos de venta, condición frente al IVA, cliente por defecto, formato, reglas de fechas
certs/          afip.key y afip_homo.crt (homologación); afip_prod.key y afip_prod.crt (producción); tickets ta_*.json
facturas/homo/  validaciones en homologación
facturas/prod/  comprobantes emitidos: JSON y PDF
facturas/mcp/   borradores del servidor
```

Es la misma estructura que la de facturador-afip. **Si ya lo usás, apuntá `FACTURADOR_AFIP_DIR` a esa carpeta.** Además de reutilizar certificados, perfil y facturas, comparten la caché de tickets de WSAA: ARCA no da un ticket nuevo mientras el anterior siga vigente, así que dos carpetas con el mismo certificado se bloquearían entre sí.

Si arrancás de cero:

```bash
facturador-afip-mcp init --cuit 20XXXXXXXXX --nombre "Tu nombre o razón social" --alias facturador1a2b3c
```

Crea la carpeta, `.env` y `perfil.json` a partir de plantillas, y genera las claves privadas y los pedidos de certificado (CSR). Nunca pisa una clave existente. El alias va solo con letras y números. Después hay que pedir los certificados y autorizarlos en ARCA: el paso a paso está en el [README de facturador-afip](https://github.com/ignaciovilagraca/facturador-afip#alta-en-arca-paso-a-paso), desde el paso 2. La herramienta `estado_configuracion` muestra qué falta.

## Instalación

Requiere [uv](https://docs.astral.sh/uv/). Mientras el repositorio sea privado, se instala por SSH con una cuenta con acceso:

```bash
uv tool install git+ssh://git@github.com/ignaciovilagraca/facturador-afip-mcp
```

## Uso con Claude Code

```bash
claude mcp add facturador-afip --scope user -e FACTURADOR_AFIP_DIR=$HOME/.facturador-afip -- facturador-afip-mcp
```

Permisos recomendados en `~/.claude/settings.json`: lectura sin preguntar, y `ask` explícito para emitir y descartar:

```json
{
  "permissions": {
    "allow": [
      "mcp__facturador-afip__estado_configuracion",
      "mcp__facturador-afip__ver_perfil",
      "mcp__facturador-afip__probar_conexion",
      "mcp__facturador-afip__ultimo_comprobante",
      "mcp__facturador-afip__buscar_codigo",
      "mcp__facturador-afip__listar_comprobantes",
      "mcp__facturador-afip__ver_comprobante",
      "mcp__facturador-afip__listar_borradores",
      "mcp__facturador-afip__generar_pdf",
      "mcp__facturador-afip__validar_en_homologacion"
    ],
    "ask": [
      "mcp__facturador-afip__emitir_en_produccion",
      "mcp__facturador-afip__descartar_borrador"
    ]
  }
}
```

Nunca pongas `emitir_en_produccion` en `allow`.

## Uso con Claude Desktop

En `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "facturador-afip": {
      "command": "facturador-afip-mcp",
      "env": { "FACTURADOR_AFIP_DIR": "/Users/<usuario>/.facturador-afip" }
    }
  }
}
```

Si Claude Desktop no encuentra el comando, usá la ruta completa (`which facturador-afip-mcp`). Claude Desktop no soporta elicitation, así que la confirmación usa el diálogo del sistema.

## Herramientas

| Herramienta | Qué hace | Toca ARCA |
|---|---|---|
| `estado_configuracion` | Carpeta, CUIT, certificados y vencimiento, perfil, forma de confirmar | No |
| `ver_perfil` | `perfil.json` | No |
| `probar_conexion` | Estado del servicio, login y puntos de venta | Solo lectura |
| `ultimo_comprobante` | Último número autorizado | Solo lectura |
| `buscar_codigo` | Códigos de país, CUIT genérico por país y monedas | Solo lectura (homologación) |
| `listar_comprobantes`, `ver_comprobante` | Comprobantes guardados | No |
| `generar_pdf` | Regenera el PDF de un comprobante guardado | No (salvo JSON viejos de Factura E) |
| `validar_en_homologacion` | Emite en homologación y crea el borrador | Homologación, sin valor fiscal |
| `listar_borradores`, `descartar_borrador` | Borradores y su estado | No |
| `emitir_en_produccion` | Emite el borrador, con confirmación de una persona | **Producción** |

El servidor le pasa al modelo instrucciones con el flujo: juntar los datos, confirmarlos con el usuario, validar en homologación, pedir aprobación expresa y recién ahí emitir. El formato del JSON de cada tipo de comprobante está en esas instrucciones (`INSTRUCCIONES` en `server.py`) y en [`ejemplos/`](ejemplos).

## Comandos

```bash
facturador-afip-mcp                     # servidor MCP por stdio (lo ejecuta el cliente)
facturador-afip-mcp init ...            # carpeta de datos, clave y CSR
facturador-afip-mcp borradores          # lista los borradores
facturador-afip-mcp emitir <borrador>   # emite un borrador con confirmación en la terminal
```

`emitir` sirve para clientes sin elicitation en máquinas sin diálogo del sistema, o para quien prefiera emitir siempre desde la terminal.

## Desarrollo

```bash
uv sync
git config core.hooksPath .githooks   # bloquea commits con claves, certificados o el CUIT
uv run pytest
```

Los tests usan un cliente MCP en memoria y ARCA simulada: no salen a la red. Para registrar la versión local en Claude Code:

```bash
claude mcp add facturador-afip --scope user -e FACTURADOR_AFIP_DIR=$HOME/.facturador-afip -- uv run --directory $PWD facturador-afip-mcp
```

## Limitaciones

- No hay notas de crédito de Factura E.
- Elicitation funciona con clientes que negocian el protocolo con el handshake `initialize` (el caso de Claude Code hoy). Con un cliente que use solo el protocolo 2026-07-28, la elicitation falla antes de emitir, así que no se emite nada; el diálogo del sistema sí funciona.
- El diálogo del sistema aparece en la computadora donde corre el servidor. Si le diste a Claude control de la pantalla (computer use), podría responderlo: no le des acceso a `osascript` ni a los diálogos del sistema.
