# Facturador ARCA

Plugin para Claude que guía la facturación electrónica de ARCA (ex AFIP): Facturas A, B y C, notas de crédito y Factura E de exportación. Trae una skill que le enseña a Claude el flujo completo: juntar los datos, validarlos en homologación, pedir tu aprobación y emitir en producción solo cuando vos lo confirmás. También te guía paso a paso en el alta en ARCA (clave y CSR, WSASS, certificados, Administrador de Relaciones y puntos de venta) y te explica los errores de ARCA.

## Instalación

En Claude Code:

```bash
/plugin install facturador-arca --marketplace ignaciovilagraca/facturador-afip-mcp
```

En Claude Desktop y Cowork se instala desde el directorio de plugins.

## Requisito: el servidor facturador-afip

El plugin no habla con ARCA por sí mismo. La conexión la hace el servidor MCP [facturador-afip-mcp](https://github.com/ignaciovilagraca/facturador-afip-mcp), de código abierto, que corre en tu computadora y guarda tus credenciales en una carpeta local. Si no lo tenés instalado, Claude te explica cómo hacerlo:

- **Claude Desktop:** bajá `facturador-afip.mcpb` de la [última release](https://github.com/ignaciovilagraca/facturador-afip-mcp/releases/latest) y abrilo con doble clic.
- **Claude Code:** con [uv](https://docs.astral.sh/uv/getting-started/installation/) instalado, `claude mcp add facturador-afip --scope user -e FACTURADOR_AFIP_DIR=~/.facturador-afip -- uvx facturador-afip-mcp`.

En claude.ai web y en el celular no funciona, porque el servidor tiene que correr en tu computadora.

## Qué hace y qué no

- La skill es solo texto: no corre programas, no lee variables de entorno y no manda datos a ningún lado.
- Las facturas, los certificados y la clave quedan en la carpeta de datos del servidor, en tu computadora. El servidor solo se conecta con los web services de ARCA.
- Ninguna factura se emite en producción sin tu aprobación expresa y tu confirmación en el chat.

## Privacidad

Ver la [sección de privacidad](https://github.com/ignaciovilagraca/facturador-afip-mcp#privacidad) del servidor.

## Licencia

MIT. Ver [LICENSE](LICENSE).
