) PRECIOS
Tabla base
Está en solicitar-revision.html:842-850, en un <script> inline (no en assets/js/):


const BASE_SERVICE_PRICES = Object.freeze({
    'auto-pequeno': 140000,
    'suv':          150000,
    'utilitario':   150000,
    'camioneta':    150000,
    '4x4':          150000,
    'deportivo':    150000,
    'clasico':      150000
});
Cada tipo con su nombre exacto y precio
Los nombres visibles están en NOMBRES_TIPO, solicitar-revision.html:852-860:

value	Nombre exacto	Precio base
auto-pequeno	Auto pequeño o mediano	140.000
suv	SUV	150.000
utilitario	Utilitario	150.000
camioneta	Camioneta	150.000
4x4	4x4	150.000
deportivo	Deportivo	150.000
clasico	Clásico o Especial	150.000
Los mismos 7 valores están en el <select id="tipo-vehiculo"> de solicitar-revision.html:600 aprox. El orden del <select> difiere del de la tabla (ahí camioneta y 4x4 van antes que utilitario), pero los 7 value coinciden exactamente con las claves de BASE_SERVICE_PRICES y de NOMBRES_TIPO.

Lógica de recargos, descuentos o precios especiales
No hay descuentos, cupones, promos ni multiplicadores. Busqué en todos los HTML y JS vivos.

El único modificador es el viático por localidad. La fórmula completa está en getPresupuestoActual(), solicitar-revision.html:1018-1027:


const precioBase = BASE_SERVICE_PRICES[presupuestoState.tipoVehiculo] || 0;
const viatico = presupuestoState.localidadValida ? presupuestoState.viatico : 0;
return { precioBase, viatico, total: precioBase + viatico };
Suma simple, sin porcentajes. Dos comportamientos condicionales:

Si el tipo de vehículo no está en la tabla → precioBase = 0.
Si la localidad tipeada no está en la lista (localidadValida === false) → el viático se fuerza a 0, y en la UI la fila del viático se oculta con style.display = 'none' (solicitar-revision.html:1053-1062). En el mensaje de WhatsApp ese caso se manda como precioBase + ' + viático a confirmar' (solicitar-revision.html:1213).
2) LOCALIDADES
Archivo: assets/js/zones-viaticos.js, array ZONES_VIATICOS_DATA, líneas 3-206. Cargado solo por solicitar-revision.html:839.

Totales
204 entradas. Sin duplicados (ningún nombre repetido dentro ni entre grupos).

Grupo	Localidades
CABA	51
Norte	40
Oeste	58
Sur	55
Total	204
76 de las 204 tienen viático 0. Valores distintos de viático que existen: 0, 20.000, 30.000, 40.000, 50.000, 80.000, 90.000, 100.000, 110.000, 160.000, 170.000, 180.000, 190.000, 200.000, 250.000.

Lista completa
CABA — 51 (todas viático 0)

CABA, Agronomía, 0
CABA, Almagro, 0
CABA, Balvanera, 0
CABA, Barracas, 0
CABA, Belgrano, 0
CABA, Boedo, 0
CABA, Caballito, 0
CABA, Chacarita, 0
CABA, Coghlan, 0
CABA, Colegiales, 0
CABA, Constitución, 0
CABA, Flores, 0
CABA, Floresta, 0
CABA, La Boca, 0
CABA, La Paternal, 0
CABA, Liniers, 0
CABA, Mataderos, 0
CABA, Monte Castro, 0
CABA, Monserrat, 0
CABA, Nueva Pompeya, 0
CABA, Núñez, 0
CABA, Palermo, 0
CABA, Parque Avellaneda, 0
CABA, Parque Chacabuco, 0
CABA, Parque Chas, 0
CABA, Parque Patricios, 0
CABA, Puerto Madero, 0
CABA, Recoleta, 0
CABA, Retiro, 0
CABA, Saavedra, 0
CABA, San Cristóbal, 0
CABA, San Nicolás, 0
CABA, San Telmo, 0
CABA, Versalles, 0
CABA, Villa Crespo, 0
CABA, Villa del Parque, 0
CABA, Villa Devoto, 0
CABA, Villa General Mitre, 0
CABA, Villa Lugano, 0
CABA, Villa Luro, 0
CABA, Villa Ortúzar, 0
CABA, Villa Pueyrredón, 0
CABA, Villa Real, 0
CABA, Villa Riachuelo, 0
CABA, Villa Santa Rita, 0
CABA, Villa Soldati, 0
CABA, Villa Urquiza, 0
CABA, Vélez Sarsfield, 0
CABA, CABA, 0
CABA, Capital Federal, 0
CABA, Capital, 0
Las últimas tres (CABA, Capital Federal, Capital) no son barrios: son alias de la ciudad entera, presumiblemente para que el autocompletado matchee si alguien tipea eso.

Norte — 40

Norte, Tigre, 0
Norte, San Fernando, 0
Norte, San Isidro, 0
Norte, Vicente Lopez, 0
Norte, Villa Adelina, 0
Norte, Boulogne Sur Mer, 0
Norte, Benavídez, 20000
Norte, Escobar, 100000
Norte, Pilar, 90000
Norte, Campana, 190000
Norte, Zárate, 250000
Norte, Don Torcuato, 0
Norte, Tortuguitas, 80000
Norte, Acassuso, 0
Norte, Beccar, 0
Norte, Belén de Escobar, 100000
Norte, Boulogne, 0
Norte, Carapachay, 0
Norte, Del Viso, 90000
Norte, El Talar, 0
Norte, Florida, 0
Norte, Garín, 100000
Norte, General Pacheco, 0
Norte, Ingeniero Maschwitz, 100000
Norte, La Lucila, 0
Norte, Manuel Alberti, 90000
Norte, Maquinista Savio, 100000
Norte, Martínez, 0
Norte, Munro, 0
Norte, Nordelta, 0
Norte, Olivos, 0
Norte, Pacheco, 0
Norte, Presidente Derqui, 90000
Norte, Ricardo Rojas, 0
Norte, Rincón de Milberg, 0
Norte, Santa Catalina, 20000
Norte, Victoria, 0
Norte, Villa Martelli, 0
Norte, Villa Rosa, 90000
Norte, Virreyes, 0
Oeste — 58

Oeste, San Martin, 30000
Oeste, 3 de Febrero, 40000
Oeste, Hurlingham, 40000
Oeste, Ituzaingó, 50000
Oeste, Morón, 50000
Oeste, La Matanza Oeste, 50000
Oeste, Moreno, 90000
Oeste, General Rodriguez, 160000
Oeste, Marcos Paz, 160000
Oeste, Merlo, 100000
Oeste, Cañuelas, 170000
Oeste, General Las Heras, 190000
Oeste, Luján, 200000
Oeste, Exaltación de la Cruz, 200000
Oeste, Castelar, 50000
Oeste, Padua, 90000
Oeste, San Justo, 40000
Oeste, Ciudad Jardín, 40000
Oeste, Bella Vista, 50000
Oeste, Caseros, 40000
Oeste, Ciudadela, 40000
Oeste, Cuartel V, 90000
Oeste, El Palomar, 50000
Oeste, Francisco Álvarez, 90000
Oeste, González Catán, 90000
Oeste, Grand Bourg, 90000
Oeste, Gregorio de Laferrère, 90000
Oeste, Haedo, 50000
Oeste, Isidro Casanova, 90000
Oeste, José C. Paz, 90000
Oeste, José León Suárez, 30000
Oeste, La Reja, 90000
Oeste, La Tablada, 50000
Oeste, Libertad, 100000
Oeste, Lomas del Mirador, 50000
Oeste, Los Polvorines, 90000
Oeste, Mariano Acosta, 100000
Oeste, Martín Coronado, 40000
Oeste, Muñiz, 50000
Oeste, Pablo Nogués, 90000
Oeste, Paso del Rey, 90000
Oeste, Pontevedra, 100000
Oeste, Rafael Castillo, 90000
Oeste, Ramos Mejía, 50000
Oeste, Sáenz Peña, 40000
Oeste, San Andrés, 30000
Oeste, San Antonio de Padua, 90000
Oeste, San Miguel, 50000
Oeste, Santos Lugares, 40000
Oeste, Trujui, 90000
Oeste, Villa Ballester, 30000
Oeste, Villa Bosch, 40000
Oeste, Villa Luzuriaga, 50000
Oeste, Villa Lynch, 30000
Oeste, Villa Sarmiento, 50000
Oeste, Villa Tesei, 40000
Oeste, Villa Udaondo, 50000
Oeste, William Morris, 40000
Sur — 55

Sur, Lanús, 30000
Sur, Avellaneda, 30000
Sur, Lomas de Zamora, 80000
Sur, Almirante Brown, 80000
Sur, Quilmes, 50000
Sur, La Matanza Este, 90000
Sur, Ezeiza, 100000
Sur, Esteban Echeverría, 90000
Sur, Presidente Perón, 110000
Sur, Florencio Varela, 100000
Sur, Berazategui, 90000
Sur, Coronel Brandsen, 200000
Sur, La Plata, 180000
Sur, Berisso, 190000
Sur, Ensenada, 190000
Sur, Gonnet, 170000
Sur, Bernal, 80000
Sur, Villa Dominico, 30000
Sur, Adrogué, 80000
Sur, Banfield, 80000
Sur, Bosques, 100000
Sur, Burzaco, 80000
Sur, Canning, 100000
Sur, City Bell, 180000
Sur, Claypole, 80000
Sur, Dock Sud, 30000
Sur, Don Bosco, 50000
Sur, El Jagüel, 90000
Sur, Ezpeleta, 50000
Sur, Gerli, 30000
Sur, Glew, 80000
Sur, Guernica, 110000
Sur, Hudson, 90000
Sur, José Mármol, 80000
Sur, Llavallol, 80000
Sur, Longchamps, 80000
Sur, Los Hornos, 180000
Sur, Luis Guillón, 90000
Sur, Monte Chingolo, 30000
Sur, Monte Grande, 90000
Sur, Piñeyro, 30000
Sur, Plátanos, 90000
Sur, Rafael Calzada, 80000
Sur, Ranelagh, 90000
Sur, Remedios de Escalada, 30000
Sur, Ringuelet, 180000
Sur, San Francisco Solano, 50000
Sur, Sarandí, 30000
Sur, Temperley, 80000
Sur, Tolosa, 180000
Sur, Tristán Suárez, 100000
Sur, Turdera, 80000
Sur, Valentín Alsina, 30000
Sur, Villa Elisa, 180000
Sur, Wilde, 30000
zones-data.js — sí existe
assets/js/zones-data.js, 113 líneas. Contiene ZONES_DATA: un array de 4 grupos, cada uno con code, label y un array areas de objetos { name, slug }.

Diferencias con zones-viaticos.js:

zones-data.js	zones-viaticos.js
Estructura	anidada por grupo	plana, un objeto por localidad
Campos	name, slug	grupo, zona, viatico
Precio	no tiene	tiene viatico
Slug para URL	sí	no
CABA	48	51
Norte	8	40
Oeste	14	58
Sur	15	55
Total	85	204
Códigos de grupo	CABA, NORTE, OESTE, SUR (mayúsculas) + label	CABA, Norte, Oeste, Sur
Cargado por	cobertura.html:473	solicitar-revision.html:839
Usado para	acordeón de cobertura (cobertura.html:478, 559)	autocompletado + cálculo del cotizador
Las 85 localidades de zones-data.js son un subconjunto exacto de las 204: ninguna aparece en zones-data.js que no esté también en zones-viaticos.js. Las 119 restantes existen solo en el de viáticos. Los tres alias de CABA (CABA, Capital Federal, Capital) explican la diferencia 48 vs 51 en ese grupo.

Son dos fuentes de verdad separadas, sin sincronización automática entre ellas.

3) CÓDIGOS DE CANAL (ref)
Archivo: assets/js/tracking.js, función detectRef() líneas 224-267.

Valores posibles
Son 6:

ref	Origen
ga	Google Ads (pago)
ig	Instagram
fb	Facebook / Meta
org	Buscador orgánico
dir	Directo
otro	No determinado
Cómo se decide
detectRef() evalúa en cascada y devuelve en el primer match. El orden importa:

ga — si existe gclid, gbraid o wbraid en la URL, o utm_source=google + utm_medium=cpc. (tracking.js:236)
ig — si utm_source es instagram o ig. (tracking.js:240)
fb — si utm_source es facebook o fb. (tracking.js:241)
fb — si hay fbclid en la URL, sin utm_source más específico. (tracking.js:244)
ig — si el hostname del document.referrer contiene instagram.com. (tracking.js:252)
fb — si el referrer contiene facebook.com, fb.com o fb.watch. (tracking.js:253)
org — si el referrer contiene google., bing.com, yahoo.com o search.yahoo. (tracking.js:255-258)
dir — si no hay referrer ni UTM, o el referrer es del mismo hostname y no hay UTM. (tracking.js:261)
otro — todo lo demás. (tracking.js:264)
utm_medium no altera la clasificación de ig/fb: paid_social, ads o cpc dan el mismo resultado.

Persistencia — first-touch. ensureRef() (tracking.js:267-275) guarda el valor en sessionStorage bajo la clave rc_ref. Si ya hay uno guardado en la sesión, lo devuelve sin recalcular. El primer origen detectado gana y no se pisa.

Cómo se genera el código RC-XXXX
Definiciones en tracking.js:282-286, generación en generateCodigo() tracking.js:306-315:

Prefijo fijo RC- + 4 caracteres.
Alfabeto: ABCDEFGHJKMNPQRSTUVWXYZ23456789 — 31 caracteres. Excluye O, 0, I, 1 y L (según el comentario del código, porque el código se lee de un chat y se tipea a mano en la planilla).
Fuente de aleatoriedad: window.crypto.getRandomValues con un Uint32Array, tomando buffer[i] % 31. Si crypto no está disponible, cae a Math.floor(Math.random() * 31) (tracking.js:288-303).
Validación al leer: /^RC-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}$/.
Espacio total: 31⁴ = 923.521 combinaciones.
Persistencia. ensureCodigo() (tracking.js:317-332) usa una caché en memoria (codigoCache) y sessionStorage con clave rc_codigo. Si el valor guardado no matchea el regex, se descarta y se genera uno nuevo. Sin sessionStorage, el código igual queda estable dentro de la carga de página.

Qué se manda al endpoint de LEADS
Endpoint (tracking.js:7):


https://script.google.com/macros/s/AKfycbzUsb68sDbs2ceYXjppEPVc0NUELWIuI9jxVbercBglS0KVEQW9OXmVeO19u6TF5jJR/exec
Es un Google Apps Script. Se exporta como window.RideCheckTracking.LEADS_ENDPOINT (tracking.js:621) y el formulario lo lee desde ahí, con el literal duplicado como fallback en solicitar-revision.html:1271.

Hay dos payloads distintos, con forma diferente:

A) Lead de WhatsApp — registerWhatsAppLead(), tracking.js:367-395

{
  "origen": "wa",
  "codigo": "RC-XXXX",
  "ref":    "ga|ig|fb|org|dir|otro",
  "gclid":  "",
  "tipoId": ""
}
5 campos. gclid y tipoId salen de getStoredGoogleClickId(); van vacíos si no hay nada guardado.

B) Lead del formulario — sendLeadToGoogleSheets(), solicitar-revision.html:1243-1283

{
  "nombre":    "<campo #nombre>",
  "telefono":  "<campo #telefono>",
  "auto":      "<campo #auto>",
  "tipo":      "<value del select, ej. auto-pequeno>",
  "localidad": "<texto tipeado en #localidad>",
  "total":     190000,
  "codigo":    "RC-XXXX",
  "ref":       "ga|ig|fb|org|dir|otro",
  "gclid":     "",
  "tipoId":    ""
}
10 campos. No lleva origen. total es numérico (base + viático). localidad es el texto crudo del input, no la zona normalizada. Si tracking.js no cargó, ref cae a 'otro' y codigo a ''.

Transporte
WhatsApp: intenta navigator.sendBeacon con un Blob de text/plain;charset=utf-8; si falla, fetch con mode: 'no-cors' y keepalive: true (tracking.js:345-364).
Formulario: fetch directo, mode: 'no-cors', Content-Type: text/plain;charset=utf-8.
text/plain es deliberado para evitar el preflight CORS, según el comentario en tracking.js:351.
Ambos silencian errores con .catch() vacío.
Sobre el GCLID
Se guarda aparte, en localStorage (no sessionStorage) bajo rc_google_click_id, con TTL de 90 días (90 * 24 * 60 * 60 * 1000, tracking.js:164). El objeto guardado es { gclid, tipoId, capturedAt }. tipoId puede ser gclid, gbraid o wbraid. Al leer, si está vencido o malformado se borra y devuelve null (tracking.js:167-188).

Dos comportamientos que condicionan el envío
whatsappLeadSent (tracking.js:335): flag de módulo. El lead de WhatsApp se manda una sola vez por carga de página; clicks posteriores no reenvían.
isLocalHost() (tracking.js:339-343): en localhost, 127.0.0.1 o ::1 el beacon de WhatsApp no se envía (solo loguea a consola). Esto corta únicamente el beacon de WhatsApp — el POST del formulario sale siempre, también en local.
DEBUG = true (tracking.js:3): está activo, así que el payload de WhatsApp se imprime en consola en producción.
También se anexa como texto al mensaje de WhatsApp, vía appendRef() (tracking.js:398-400):


ref: <ref> · cod: RC-XXXX