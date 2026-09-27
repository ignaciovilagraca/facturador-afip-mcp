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

1. **Primero homologación.** Solo se emite un borrador que `validar_en_homologacion` ya emitió con éxito en homologación. Se compara un hash: producción emite exactamente la factura validada.
2. **Los datos reales a la vista.** `preparar_emision` arma la emisión sin emitir y devuelve el número de comprobante, el receptor, el total y la cotización de producción. `emitir_en_produccion` recibe esos mismos datos y el servidor verifica que coincidan con los reales; si no, no emite. Así el diálogo de permiso del cliente muestra qué se va a emitir.
3. **La confirmación de una persona.** Antes de enviar a ARCA, una persona confirma por la primera de estas vías que el cliente soporte (`FACTURADOR_CONFIRMACION`, en este orden por defecto):

   | Vía | Dónde | Cómo |
   |---|---|---|
   | `apps` | Clientes con MCP Apps (Claude Desktop) | Una tarjeta dentro del chat con el resumen: hay que tipear el número de comprobante y apretar Emitir. El botón llama a `confirmar_emision`, una herramienta que el cliente no le muestra al modelo, con un token de un solo uso que vence a los 10 minutos y que no va en el texto que lee el modelo |
   | `elicitation` | Clientes con elicitation (Claude Code en la terminal) | Un formulario del cliente: hay que tipear el número |
   | `permiso` | Cualquier cliente | El diálogo de permiso del cliente, que muestra número, receptor y total. Solo sirve si `emitir_en_produccion` está en `ask`: el servidor no puede saber si aprobó una persona. Sacalo de la lista si no es tu caso |
   | `dialogo` | macOS (`osascript`) y Linux (`zenity`) | Un diálogo del sistema, fuera del cliente: hay que tipear el número |

   Como `permiso` siempre está disponible, con el orden por defecto el diálogo del sistema solo se usa si sacás `permiso` de la lista. `estado_configuracion` muestra qué vía se va a usar con el cliente conectado.
4. **Una sola vez.** Un borrador emitido no se puede volver a emitir. Si la conexión se corta a mitad de la emisión, el próximo intento primero consulta a ARCA si el comprobante ya existe y solo reintenta si no.
5. **Tope opcional** por comprobante, en pesos (`FACTURADOR_TOTAL_MAXIMO_ARS`).

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
      "mcp__facturador-afip__validar_en_homologacion",
      "mcp__facturador-afip__preparar_emision"
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

Descargá `facturador-afip.mcpb` de la última [release](https://github.com/ignaciovilagraca/facturador-afip-mcp/releases) y abrilo con doble clic (o Configuración → Extensiones → Instalar extensión). Claude Desktop pide la carpeta de datos e instala las dependencias con uv.

En Claude Desktop, cada emisión se confirma en una tarjeta dentro del chat (MCP Apps). Funciona en macOS y Windows.

Sin el `.mcpb`, también se puede configurar a mano en `claude_desktop_config.json`:

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
| `preparar_emision` | Número, receptor, total y cotización reales de producción, sin emitir | Solo lectura |
| `listar_borradores`, `descartar_borrador` | Borradores y su estado | No |
| `emitir_en_produccion` | Emite el borrador, con confirmación de una persona | **Producción** |
| `confirmar_emision` | Solo para la tarjeta: el modelo no la ve | **Producción** |

El servidor le pasa al modelo instrucciones con el flujo: juntar los datos, confirmarlos con el usuario, validar en homologación, preparar la emisión, pedir aprobación expresa con los datos reales y recién ahí emitir. El formato del JSON de cada tipo de comprobante está en esas instrucciones (`INSTRUCCIONES` en `server.py`) y en [`ejemplos/`](ejemplos).

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

Los tests usan un cliente MCP en memoria y ARCA simulada: no salen a la red.

Para armar la extensión de Claude Desktop (la versión de `manifest.json` tiene que coincidir con la de `pyproject.toml`):

```bash
npx @anthropic-ai/mcpb validate manifest.json
npx @anthropic-ai/mcpb pack . dist/facturador-afip.mcpb
``` Para registrar la versión local en Claude Code:

```bash
claude mcp add facturador-afip --scope user -e FACTURADOR_AFIP_DIR=$HOME/.facturador-afip -- uv run --directory $PWD facturador-afip-mcp
```

## Limitaciones

- No hay notas de crédito de Factura E.
- Elicitation funciona con clientes que negocian el protocolo con el handshake `initialize` (el caso de Claude Code hoy). Con un cliente que use solo el protocolo 2026-07-28, la elicitation falla antes de emitir, así que no se emite nada.
- La tarjeta depende de que el cliente cumpla la especificación de MCP Apps: que no le muestre `confirmar_emision` al modelo y que no le pase el `structuredContent` (donde va el token). Aun si no lo cumpliera, el modelo necesitaría el token de un solo uso y el número exacto.
- El diálogo del sistema aparece en la computadora donde corre el servidor. Si le diste a Claude control de la pantalla (computer use), podría responderlo: no le des acceso a `osascript` ni a los diálogos del sistema.
