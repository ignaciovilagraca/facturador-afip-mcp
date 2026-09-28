# facturador-afip-mcp

Servidor MCP para emitir facturas electrónicas de ARCA (ex AFIP) desde Claude u otro cliente de MCP:

- **Facturas A, B y C** y sus **notas de crédito**, por WSFE.
- **Factura E** (exportación), por WSFEX.

Le pedís a Claude la factura en lenguaje natural ("haceme la factura de septiembre", o le pasás el invoice de tu cliente), la valida en homologación y, cuando la aprobás, la emite: vos confirmás cada emisión tipeando el número de comprobante en una tarjeta dentro del chat. Arma el PDF con el diseño de "Comprobantes en línea", lleva la numeración y guarda cada comprobante.

## Índice

- [Instalación](#instalación)
- [Primeros pasos](#primeros-pasos)
- [Cómo se usa](#cómo-se-usa)
- [Cómo protege la emisión](#cómo-protege-la-emisión)
- [Credenciales y carpeta de datos](#credenciales-y-carpeta-de-datos)
- [Herramientas](#herramientas)
- [Comandos](#comandos)
- [Alta en ARCA paso a paso](#alta-en-arca-paso-a-paso)
- [Desarrollo](#desarrollo)
- [Limitaciones](#limitaciones)
- [Privacidad](#privacidad)
- [Licencia](#licencia)

## Instalación

Requiere [uv](https://docs.astral.sh/uv/getting-started/installation/).

### Claude Desktop

1. Descargá `facturador-afip.mcpb` de la [última release](https://github.com/ignaciovilagraca/facturador-afip-mcp/releases/latest).
2. Abrilo con doble clic, o en Configuración → Extensiones → Instalar extensión.
3. Elegí tu carpeta de datos (por defecto `~/.facturador-afip`).

Cada emisión se confirma en una tarjeta dentro del chat. Funciona en macOS y Windows.

### Claude Code, como plugin

El plugin instala el servidor y te pide la carpeta de datos al habilitarlo:

```bash
claude plugin marketplace add ignaciovilagraca/facturador-afip-mcp
```
```bash
claude plugin install facturador-afip@facturador-afip-mcp
```

Este repositorio es el plugin ([`.claude-plugin/plugin.json`](.claude-plugin/plugin.json)) y también su marketplace ([`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json)). El plugin corre el código de este repositorio con las dependencias exactas de `uv.lock`.

El plugin funciona en Claude Code y Cowork. En claude.ai web no, porque el servidor corre en tu computadora; en Claude Desktop usá el `.mcpb`.

### Claude Code, como servidor MCP

```bash
claude mcp add facturador-afip --scope user -e FACTURADOR_AFIP_DIR=~/.facturador-afip -- uvx facturador-afip-mcp
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

### Otros clientes de MCP

El paquete está en [PyPI](https://pypi.org/project/facturador-afip-mcp/). La configuración típica:

```json
{
  "mcpServers": {
    "facturador-afip": {
      "command": "uvx",
      "args": ["facturador-afip-mcp"],
      "env": { "FACTURADOR_AFIP_DIR": "/Users/<usuario>/.facturador-afip" }
    }
  }
}
```

## Primeros pasos

Pedile a Claude **"configurá el facturador"** (en Claude Code también está el comando `/configurar` del plugin). Claude revisa en qué paso estás y te guía hasta poder emitir:

1. Te pide tu CUIT, tu nombre o razón social y los datos que van impresos en las facturas, y genera en tu computadora tu clave privada y el pedido de certificado (CSR). La clave nunca sale de tu computadora ni pasa por Claude.
2. Te guía pantalla por pantalla en ARCA para obtener los certificados (WSASS en homologación, Administración de Certificados Digitales en producción). Le pasás el certificado que te da ARCA y lo guarda después de verificar que corresponda a tu clave y al entorno correcto.
3. Te guía para autorizar el certificado y dar de alta el punto de venta, y te pregunta tus preferencias (cliente habitual, formato de la factura).
4. Prueba la conexión con ARCA.

Los pasos dentro de ARCA los hacés vos con tu clave fiscal. Si te trabás, pasale a Claude una captura o el mensaje de error.

Si preferís la terminal, `uvx facturador-afip-mcp init --cuit ... --nombre ... --alias ...` hace el paso 1. Si ya usás [facturador-afip](https://github.com/ignaciovilagraca/facturador-afip), no hace falta nada de esto: apuntá la carpeta de datos a la de ese proyecto.

## Cómo se usa

Pedíselo a Claude como se lo pedirías a una persona: "haceme la factura del mes", "facturale 1500 dólares a Acme por septiembre", o pasale el PDF del invoice. Claude:

1. Arma la factura con tu perfil y te muestra los datos para que los confirmes.
2. La valida en homologación, sin valor fiscal.
3. Te muestra el número, el receptor, el total y la cotización reales de producción, y te pide la aprobación.
4. Con tu aprobación, pide la emisión. Vos confirmás en la tarjeta tipeando el número de comprobante; si cancelás, no se emite nada.
5. Te pasa el número, el CAE y dónde quedó el PDF.

## Cómo protege la emisión

Una factura emitida en producción es un comprobante fiscal real: no se borra, solo se anula con una nota de crédito. Por eso emitir pasa por varias barreras, y las del servidor no dependen de lo que decida el modelo:

1. **Primero homologación.** Solo se emite un borrador que `validar_en_homologacion` ya emitió con éxito en homologación. Se compara un hash: producción emite exactamente la factura validada.
2. **Los datos reales a la vista.** `preparar_emision` arma la emisión sin emitir y devuelve el número de comprobante, el receptor, el total y la cotización de producción. `emitir_en_produccion` recibe esos mismos datos y el servidor verifica que coincidan con los reales; si no, no emite. Así el diálogo de permiso del cliente muestra qué se va a emitir.
3. **La confirmación de una persona.** Antes de enviar a ARCA, una persona confirma por la primera de estas vías que el cliente soporte (`FACTURADOR_CONFIRMACION`, en este orden por defecto):

   | Vía | Dónde | Cómo |
   |---|---|---|
   | `apps` | Clientes con MCP Apps (Claude Desktop) | Una tarjeta dentro del chat con el resumen: hay que tipear el número de comprobante y apretar Emitir. El botón llama a `confirmar_emision`, una herramienta que el cliente no le muestra al modelo, con un token de un solo uso que la tarjeta pide a otra herramienta oculta, así que nunca pasa por el modelo. La confirmación vence a los 10 minutos |
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

Si arrancás de cero, `init` la crea: ver [Primeros pasos](#primeros-pasos).

## Herramientas

| Herramienta | Qué hace | Toca ARCA |
|---|---|---|
| `estado_configuracion` | En qué paso del alta estás, carpeta, CUIT, certificados y vencimiento, perfil, forma de confirmar | No |
| `guia_alta_arca` | Guía del alta en ARCA y errores frecuentes, por sección | No |
| `iniciar_configuracion` | Crea la carpeta de datos, guarda el CUIT y los datos del emisor, y genera la clave y los CSR | No |
| `ver_csr` | El pedido de certificado de un entorno, para llevar a ARCA | No |
| `guardar_certificado` | Verifica y guarda el certificado que da ARCA (texto de WSASS o ruta del `.crt`) | No |
| `guardar_perfil` | Guarda las preferencias en `perfil.json` | No |
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
| `estado_confirmacion`, `cancelar_emision`, `confirmar_emision` | Solo para la tarjeta: el modelo no las ve | `confirmar_emision` emite en **producción** |

El servidor le pasa al modelo instrucciones con el flujo: juntar los datos, confirmarlos con el usuario, validar en homologación, preparar la emisión, pedir aprobación expresa con los datos reales y recién ahí emitir. El formato del JSON de cada tipo de comprobante está en esas instrucciones (`INSTRUCCIONES` en `server.py`) y en [`ejemplos/`](ejemplos).

## Comandos

```bash
uvx facturador-afip-mcp init ...            # carpeta de datos, clave y CSR
uvx facturador-afip-mcp borradores          # lista los borradores
uvx facturador-afip-mcp emitir <borrador>   # emite un borrador, con confirmación en la terminal
```

`emitir` sirve para clientes sin tarjeta ni elicitation, o para quien prefiera emitir siempre desde la terminal.

## Alta en ARCA paso a paso

Lo más simple es hacerlo con Claude: pedile "configurá el facturador" y te guía paso a paso con esta misma guía, que viene dentro del servidor (`guia_alta_arca`).


Para usar los web services de facturación hay que: generar una clave y un pedido de certificado, obtener el certificado en ARCA, autorizarlo para el servicio y, en producción, dar de alta un punto de venta para web services. Se hace una vez por entorno.

Los pasos son los mismos para las Facturas A, B y C (servicio `wsfe`) y para la Factura E (servicio `wsfex`); cambian el servicio que se autoriza y el tipo de punto de venta. Si vas a emitir los dos tipos, autorizá los dos servicios.

| | Facturas A, B o C | Factura E (exportación) |
|---|---|---|
| Servicio web | `wsfe` | `wsfex` |
| Nombre en el Administrador de Relaciones (ARCA → WebServices) | Facturación Electrónica | Facturación Electrónica de Exportación |
| Sistema del punto de venta | Factura Electrónica - Monotributo - Web Services (monotributistas) o RECE para aplicativo y web services (responsables inscriptos) | Comprobantes de Exportación - Web Services |

Dos cosas que confunden la primera vez:

- **Lo que se autoriza es un "Computador Fiscal", no una persona.** El certificado representa a tu programa; en ARCA se identifica por el alias que le pusiste. Por eso en las relaciones el representante es el Computador Fiscal con ese alias, nunca tu CUIT.
- **Hay dos ramas parecidas en el buscador de servicios de ARCA: "Servicios interactivos" (los que usás desde la web de ARCA) y "WebServices" (los que usa un programa).** Para autorizar el certificado siempre es **WebServices**. Si elegís el interactivo, ARCA responde "El servicio debe ser delegable".

ARCA cambia seguido los nombres y la ubicación de los menús. Si alguno no coincide exactamente, buscalo por palabras clave ("certificados", "relaciones", "puntos de venta").

### Requisitos

- Clave fiscal nivel 3 o superior.

### Paso 1: clave privada y pedido de certificado (CSR)

Lo hace el servidor con `iniciar_configuracion`: genera en la computadora de la persona una clave privada y un CSR por entorno, y escribe el CUIT y los datos del emisor. La clave privada nunca sale de la computadora ni se muestra. Los CSR son públicos: la herramienta los devuelve para pegarlos o subirlos en ARCA.

- El alias va **solo con letras y números** (sin guiones, espacios ni acentos); si no, ARCA lo rechaza con "El Nombre simbólico del DN sólo puede contener números y/o letras". Ejemplo: `facturador1a2b3c`.
- Los datos del emisor (razón social, domicilio comercial, condición frente al IVA, ingresos brutos, inicio de actividades) van impresos en el PDF de cada factura.

### Paso 2: homologación (entorno de pruebas)

1. Entrá a [arca.gob.ar](https://www.arca.gob.ar) con clave fiscal.
2. Si no tenés el servicio "WSASS - Autogestión Certificados Homologación", adherilo:
   1. Entrá a "Administrador de Relaciones de Clave Fiscal".
   2. Elegí "Adherir servicio".
   3. Buscá ARCA → Servicios interactivos → "WSASS - Autogestión Certificados Homologación" y confirmá.
   4. Cerrá sesión y volvé a entrar para que aparezca.
3. En WSASS, elegí "Nuevo Certificado":
   1. En "Nombre simbólico del DN" poné el alias (el mismo `ALIAS` del CSR).
   2. En "Solicitud de certificado en formato PKCS#10" pegá el CSR de homologación que devolvió `iniciar_configuracion` (o `ver_csr`), incluidas las líneas `-----BEGIN CERTIFICATE REQUEST-----` y `-----END CERTIFICATE REQUEST-----`.
   3. Elegí "Crear DN y obtener certificado".
   4. WSASS no da un archivo: muestra el certificado en la pantalla. Copiá el texto, desde `-----BEGIN CERTIFICATE-----` hasta `-----END CERTIFICATE-----`, y pasáselo a Claude: lo guarda `guardar_certificado` con entorno `homo`, que verifica que corresponda a la clave. Ojo con no confundirlo con el CSR, que empieza con `-----BEGIN CERTIFICATE REQUEST-----`.
4. En WSASS, elegí "Crear autorización a servicio":
   1. Nombre simbólico del DN: tu alias.
   2. CUIT representada: tu CUIT.
   3. Servicio: `wsfe - Facturación Electrónica` para las Facturas A, B y C. Si también facturás al exterior, creá otra autorización con `wsfex - Facturación Electrónica de Exportación`.
   4. Confirmá con "Crear autorización de acceso".

En homologación no hace falta dar de alta puntos de venta: acepta cualquier número.

### 3.1 Obtener el certificado

1. Con clave fiscal, entrá a "Administración de Certificados Digitales". Si no aparece, adherilo como en el paso 2.2, buscando ARCA → Servicios interactivos → "Administración de Certificados Digitales".
2. Elegí tu CUIT y después "Agregar alias".
3. Poné el alias (solo letras y números), subí el archivo del CSR de producción (`certs/afip_prod.csr` en la carpeta de datos; `ver_csr` muestra la ruta) y confirmá con "Agregar alias".
4. En la lista de alias, tocá **"Ver"** en la fila de tu alias.
5. En la pantalla siguiente, tocá **"Descargar"** en el certificado. Baja un archivo `.crt`.
6. Decile a Claude dónde quedó el archivo (por ejemplo, en Descargas): lo guarda `guardar_certificado` con entorno `prod` y la ruta, y verifica que corresponda a la clave.

El certificado de producción lo emite "Computadores" de AFIP (el de homologación, "Computadores Test"). ARCA usa como CN el alias que escribiste en la pantalla, aunque el CSR tenga otro.

### 3.2 Autorizar el certificado para el servicio

1. Entrá a "Administrador de Relaciones de Clave Fiscal".
2. Elegí "Nueva Relación".
3. En "Servicio", tocá "Buscar" y elegí ARCA → **WebServices** → "Facturación Electrónica" (Facturas A, B y C). No uses la rama "Servicios interactivos": da el error "El servicio debe ser delegable".
4. En "Representante", tocá "Buscar" (no escribas un CUIT en el campo), marcá **"Computador Fiscal"** y elegí tu alias en el desplegable.
5. Confirmá. Si te lo pide, generá e imprimí el formulario F.3283.

Si aparece "El dador de la autorización no debe ser igual al autorizado", en "Representante" quedó tu propio CUIT como persona: tiene que ser el Computador Fiscal (el certificado).

Si también facturás al exterior, creá otra relación igual en Administrador de Relaciones de Clave Fiscal → Nueva Relación, con ARCA → WebServices → "Facturación Electrónica de Exportación" y el mismo Computador Fiscal. ARCA puede tardar unos minutos en aplicar una relación nueva.

### 3.3 Dar de alta el punto de venta

Se necesita uno por tipo de factura: uno para las Facturas A, B y C y, si facturás al exterior, otro para la Factura E.

1. Con clave fiscal, entrá a "Administración de puntos de venta y domicilios". Si no aparece, adherilo como en el paso 2.2, buscando ARCA → Servicios interactivos → "Administración de puntos de venta y domicilios".
2. Se abre "PVE - Gestión de puntos de venta". Elegí tu CUIT y, en el menú principal, "A/B/M de Puntos de venta / emisión".
3. Vas a ver el listado con las columnas Número, Nombre de Fantasía, Sistema y Baja. Revisá qué números ya usás y cuáles son de web services (el sistema termina en "Web Services").
4. Tocá "Agregar..." y completá:
   1. **Número**: uno que no uses.
   2. **Nombre de Fantasía**: opcional; es el nombre comercial que aparece en tus facturas.
   3. **Sistema**:
      - Facturas A, B y C, si sos monotributista: "Factura Electrónica - Monotributo - Web Services".
      - Facturas A, B y C, si sos responsable inscripto: "RECE para aplicativo y web services".
      - Factura E: "Comprobantes de Exportación - Web Services".
   4. **Domicilio**: elegí uno de los domicilios que tenés declarados en ARCA.
5. Confirmá. El punto de venta nuevo aparece en el listado.

Tené en cuenta:
- Los sistemas "Factura en Línea" (por ejemplo "Factura en Línea - Monotributo" o "Comprobantes de Exportación - Factura en Línea") son de "Comprobantes en línea" y **no sirven para web services**.
- El sistema de un punto de venta no se puede cambiar: si elegiste mal, dalo de baja con "Baja" y creá otro.
- Cada punto de venta tiene su propia numeración, que empieza en 1.
- El alta puede tardar unos minutos en verse desde el web service.

Con el ejemplo de la tabla de arriba, un monotributista que factura al exterior y en Argentina termina con algo así:

| Número | Sistema | Uso |
|---|---|---|
| 5 | Factura Electrónica - Monotributo - Web Services | Factura C |
| 4 | Comprobantes de Exportación - Web Services | Factura E |

### 3.4 Lista de control

Para cada servicio que vayas a usar (`wsfe`, `wsfex` o los dos):

- [ ] Certificado de producción guardado con `guardar_certificado` (paso 3.1).
- [ ] Relación del alias como Computador Fiscal con el servicio (paso 3.2).
- [ ] Punto de venta del sistema "... - Web Services" que corresponde (paso 3.3).
- [ ] `probar_conexion` en producción muestra el punto de venta y el último número (paso 4).

### Paso 4: verificar

Llamá a `estado_configuracion` (muestra qué falta: CUIT, certificados, perfil) y a `probar_conexion` para cada entorno (`homo` y `prod`) y servicio (`wsfe` para A, B y C; `wsfex` para la E).

Son consultas de solo lectura: no emiten nada. Tiene que mostrar el servicio OK, el login en WSAA y, en producción, tu punto de venta con `N` (no bloqueado) y el último número emitido (0 si es nuevo). Errores frecuentes:

**Durante el alta en ARCA:**

| Mensaje | Qué pasó y qué hacer |
|---|---|
| El Nombre simbólico del DN sólo puede contener números y/o letras | El alias tiene guiones, espacios o acentos. Usá solo letras y números, y generá el CSR con ese mismo alias. |
| El dador de la autorización no debe ser igual al autorizado | En "Representante" quedó tu CUIT como persona. Tocá "Buscar", marcá "Computador Fiscal" y elegí el alias. |
| El servicio debe ser delegable | Elegiste el servicio en "Servicios interactivos". Buscalo en ARCA → **WebServices**. |
| Pegaste algo que empieza con `BEGIN CERTIFICATE REQUEST` como certificado | Eso es el CSR (el pedido). El certificado lo da ARCA: en homologación, el texto que muestra WSASS; en producción, el archivo que bajás con Ver → Descargar. |

**Al conectarse o emitir:**

| Error | Qué pasó y qué hacer |
|---|---|
| Computador no autorizado a acceder al servicio | Falta la autorización del paso 2.4 (homologación) o la relación del paso 3.2 (producción) para ese servicio: `wsfe` para A, B y C, `wsfex` para la E. |
| El CEE ya posee un TA valido para el acceso al WSN solicitado | ARCA ya entregó un ticket de acceso para ese certificado y servicio, y dura 12 horas; no da otro hasta que venza. Pasa si se borra `certs/ta_*.json` o si otro programa usa el mismo certificado con otra carpeta de datos. Esperá a que venza; no borres esos archivos. |
| 1607: Campo Pto_venta no es valido | El punto de venta no es del sistema "Comprobantes de Exportación - Web Services". |
| Sin puntos de venta en `probar_conexion` de producción con `wsfe` | No hay punto de venta de web services para A, B y C, o todavía no se propagó el alta. |
| 1500 (fecha) | La fecha de emisión está fuera de lo que acepta ARCA: 5 días antes o después de hoy (10 para servicios en A, B y C). |
| 1535 (Factura E) o 10016 (A, B y C): fecha anterior al último comprobante | En cada punto de venta las fechas no pueden retroceder. En homologación suele ser por pruebas viejas: repetí con otro `punto_venta_homo`. |
| 1674 / 10036: fecha de pago anterior a la emisión | La fecha de pago (o de vencimiento) tiene que ser igual o posterior a la de emisión. |
| 2053: cotización no válida | La cotización tiene que ser la del día anterior a la fecha del comprobante; el servidor ya la pide así. |
| Aprobada con la observación 10238 (CUIT receptora inexistente) | ARCA emitió la factura igual. En producción hay que anularla con nota de crédito. Revisá el CUIT del receptor antes de emitir. |
| DH_KEY_TOO_SMALL | El servidor de producción usa una clave Diffie-Hellman de 1024 bits; el servidor ya lo resuelve. |

### Renovación

Los certificados vencen a los 2 años (`estado_configuracion` muestra la fecha y cuántos días faltan). Para renovarlos alcanza con pedir un certificado nuevo para **el mismo alias y la misma clave**: las autorizaciones y relaciones son del alias, así que siguen valiendo.

- **Homologación:** en WSASS → "Nuevo Certificado", poné el mismo alias como nombre simbólico del DN, pegá el mismo CSR (`ver_csr`) y guardá el certificado nuevo con `guardar_certificado`.
- **Producción:** en "Administración de Certificados Digitales", elegí tu CUIT, tocá "Ver" en la fila del alias y después "Agregar certificado"; subí el mismo CSR de producción. En la pantalla del alias vas a ver dos certificados: tocá "Descargar" en el nuevo (el de vencimiento más lejano) y guardalo con `guardar_certificado`.

Si creés que la clave privada quedó expuesta, no renueves: generá una clave nueva con otro alias y hacé todo el alta de nuevo (certificado, autorizaciones y relaciones).

## Desarrollo

```bash
uv sync
uv run pytest
```

El núcleo (login y web services de ARCA, emisión, PDF, carpeta de datos y alta guiada) es la biblioteca [facturador-afip](https://github.com/ignaciovilagraca/facturador-afip), que se instala como dependencia desde PyPI. Este repositorio tiene solo el servidor MCP: las herramientas, el flujo de borradores y la confirmación.

Los tests usan un cliente MCP en memoria y ARCA simulada: no salen a la red. Para probar la versión local en Claude Code:

```bash
claude mcp add facturador-afip --scope user -e FACTURADOR_AFIP_DIR=~/.facturador-afip -- uv run --directory ~/ruta/al/facturador-afip-mcp facturador-afip-mcp
```

### Publicar una versión

1. Subí la versión en `pyproject.toml`, `manifest.json` y `.claude-plugin/plugin.json` (tienen que coincidir). La URI de la tarjeta la incluye, así los clientes no muestran una tarjeta vieja desde su caché.
2. PyPI:
   ```bash
   uv build && uv publish dist/facturador_afip_mcp-<versión>*
   ```
3. Extensión de Claude Desktop y release:
   ```bash
   npx @anthropic-ai/mcpb validate manifest.json && npx @anthropic-ai/mcpb pack . dist/facturador-afip.mcpb
   gh release create v<versión> dist/facturador-afip.mcpb --title "v<versión>" --notes "..."
   ```
4. El plugin sale del commit: el marketplace de este repositorio y el directorio de Claude toman la rama `main`.

## Limitaciones

- No hay notas de crédito de Factura E.
- Elicitation funciona con clientes que negocian el protocolo con el handshake `initialize` (el caso de Claude Code hoy). Con un cliente que use solo el protocolo 2026-07-28, la elicitation falla antes de emitir, así que no se emite nada.
- La tarjeta depende de que el cliente cumpla la especificación de MCP Apps y no le muestre al modelo las herramientas de la tarjeta. Claude Desktop lo cumple.
- El diálogo del sistema aparece en la computadora donde corre el servidor. Si le diste a Claude control de la pantalla (computer use), podría responderlo: no le des acceso a `osascript` ni a los diálogos del sistema.

## Privacidad

- **Qué datos usa:** tu CUIT y los datos del emisor (`.env`), tu clave privada y tus certificados de ARCA (`certs/`), tus preferencias (`perfil.json`) y los datos de las facturas que emitís, incluidos los de tus clientes.
- **Dónde quedan:** todo se guarda en tu carpeta de datos, en tu computadora. El servidor no tiene base de datos propia ni servidores del autor, y no manda telemetría.
- **Con quién se comparte:** el servidor solo se conecta con los web services de ARCA (`afip.gov.ar`) para autenticarse, consultar y emitir comprobantes. La clave privada nunca sale de tu computadora: se usa localmente para firmar el login. Los datos de las facturas que le das a Claude o que devuelven las herramientas pasan por Claude, según la [política de privacidad de Anthropic](https://www.anthropic.com/legal/privacy).
- **Cuánto tiempo:** los comprobantes, borradores y tickets de acceso quedan en tu carpeta de datos hasta que los borres. Los tickets de ARCA vencen a las 12 horas.
- **Contacto:** [issues del repositorio](https://github.com/ignaciovilagraca/facturador-afip-mcp/issues).

## Licencia

[MIT](LICENSE). El software se ofrece tal cual, sin garantías: revisá cada factura antes de confirmar su emisión en producción.

El logo de ARCA que usa el PDF es de ARCA y no está cubierto por esta licencia; se usa solo para que el PDF replique el diseño de "Comprobantes en línea".
