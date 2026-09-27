"""Arranca el servidor MCP desde el código de este repositorio. Lo usa el plugin de Claude Code
(.claude-plugin/plugin.json), que corre el código con las dependencias exactas de uv.lock."""
from facturador_afip_mcp.cli import main

main()
