# Facturador ARCA

Plugin para emitir facturas electrónicas de ARCA (ex AFIP) desde Claude: Facturas A, B y C, sus notas de crédito y Factura E de exportación. Le pedís la factura en lenguaje natural o le pasás el invoice de tu cliente; Claude la arma con tu perfil, la valida en homologación sin valor fiscal y, cuando la aprobás, la emite. Cada emisión en producción la confirma una persona: en Claude Desktop, tipeando el número de comprobante en una tarjeta dentro del chat; en Claude Code, en un formulario. Genera el PDF con el diseño de "Comprobantes en línea".

Instala el servidor MCP [`facturador-afip-mcp`](https://pypi.org/project/facturador-afip-mcp/), que corre en tu computadora con [uv](https://docs.astral.sh/uv/getting-started/installation/). La documentación completa, con la guía de alta en ARCA, está en el [repositorio](https://github.com/ignaciovilagraca/facturador-afip-mcp).

## Antes de usarlo

1. Instalá [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Creá tu carpeta de datos, con tu clave privada y los pedidos de certificado:
   ```bash
   uvx facturador-afip-mcp init --cuit 20XXXXXXXXX --nombre "Tu nombre o razón social" --alias facturador1a2b3c
   ```
3. Hacé el alta en ARCA (certificados, autorizaciones y puntos de venta) siguiendo la [guía paso a paso](https://github.com/ignaciovilagraca/facturador-afip-mcp#alta-en-arca-paso-a-paso).
4. Al habilitar el plugin, indicá esa carpeta. Después pedile a Claude "revisá la configuración del facturador".

## Ejemplos

- "Haceme la factura de septiembre."
- "Facturale 1500 dólares a Acme Inc por consultoría de septiembre."
- "Hacé la nota de crédito que anula la factura C 00005-00000012."

## Qué ejecuta y a dónde se conecta

- Ejecuta `uvx facturador-afip-mcp` con una versión fija, que descarga el paquete de PyPI y lo corre en tu computadora.
- El servidor solo se conecta con los web services de ARCA (`afip.gov.ar`).
- Lee y escribe únicamente en tu carpeta de datos.

## Privacidad

- **Qué datos usa:** tu CUIT y los datos del emisor, tu clave privada y tus certificados de ARCA, tus preferencias y los datos de las facturas que emitís, incluidos los de tus clientes.
- **Dónde quedan:** todo se guarda en tu carpeta de datos, en tu computadora. No hay servidores del autor ni telemetría.
- **Con quién se comparte:** solo con ARCA, para autenticarse, consultar y emitir. La clave privada nunca sale de tu computadora. Los datos de las facturas que le das a Claude o que devuelven las herramientas pasan por Claude, según la [política de privacidad de Anthropic](https://www.anthropic.com/legal/privacy).
- **Cuánto tiempo:** hasta que los borres de tu carpeta de datos. Los tickets de acceso de ARCA vencen a las 12 horas.
- **Contacto:** [issues del repositorio](https://github.com/ignaciovilagraca/facturador-afip-mcp/issues).

## Licencia

[MIT](LICENSE). Se ofrece tal cual, sin garantías: revisá cada factura antes de confirmar su emisión en producción. El logo de ARCA que usa el PDF es de ARCA y no está cubierto por esta licencia.
