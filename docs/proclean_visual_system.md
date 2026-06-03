# ProClean Premium Clean UI — Sistema visual

Documentación interna para construir pantallas nuevas sin rediseñar desde cero.  
**Alcance:** solo HTML/CSS visual. No sustituye reglas de negocio ni motor de cálculo.

---

## 1. Nombre y objetivo

**ProClean Premium Clean UI** — interfaz blanca/gris clara, acentos azul y verde ProClean, tipografía Inter, componentes pill y tablas densas pero legibles.

Objetivo: que un módulo nuevo cargue `static/style.css` y use clases **opt-in** `.pc-*` (Fase D.20), sin copiar 400+ líneas de CSS por módulo.

---

## 2. Principios

| Hacer | Evitar |
|--------|--------|
| Base blanca (`--pc-surface`) y fondo `--pc-bg-page` | Gradientes agresivos en contenido |
| Azul ProClean (`--pc-blue`) para primario / acentos | Botones grises nativos del navegador |
| Verde (`--pc-green`) para éxito / confirmación | Emojis como iconos |
| Botones pill (`.btn-primary`, `.btn-secondary`) | Ilustraciones grandes / caricaturas |
| Cards con borde + sombra suave | Gráficas o KPIs inventados |
| Tablas con scroll horizontal cuando son anchas | Cambiar `href`, `name`, `action`, IDs funcionales |
| Iconos Lucide (`data-lucide`) en acciones D.9 | Tocar Python/JS/DB para “arreglar” UI |

---

## 3. Tokens (definidos en `static/style.css`)

```css
var(--pc-blue)           /* primario */
var(--pc-blue-deep)      /* hover / texto acento */
var(--pc-green)          /* éxito */
var(--pc-green-deep)
var(--pc-surface)        /* blanco tarjeta */
var(--pc-surface-soft)   /* gris muy claro */
var(--pc-surface-blue)   /* hover fila / fondo info */
var(--pc-surface-green)
var(--pc-border)
var(--pc-text)
var(--pc-text-muted)
var(--pc-radius-lg)
var(--pc-radius-pill)
var(--pc-shadow-soft)
var(--pc-transition)
```

Los alias legacy (`--primary`, `--panel`, `--muted`, etc.) apuntan a los mismos valores.

---

## 4. Clases base recomendadas (Fase D.20)

### Page head

```html
<section class="page-head pc-page-head">
  <div class="pc-page-head__main">
    <span class="pc-page-eyebrow">Módulo</span>
    <h2 class="pc-page-title">Título de pantalla</h2>
    <p class="pc-page-subtitle">Descripción existente — no inventar texto funcional.</p>
  </div>
  <div class="pc-page-actions page-head-actions">
    <a class="btn btn-secondary" href="...">Volver</a>
    <button type="submit" class="btn btn-primary">Acción</button>
  </div>
</section>
```

Puedes combinar `page-head` + `pc-page-head`. Los módulos ya migrados usan prefijos propios (`calc-page-head`, `param-page-head`, etc.); **no es obligatorio migrarlos** a `.pc-page-head`.

### Cards / panels

| Clase | Uso |
|--------|-----|
| `.pc-card` | Bloque principal |
| `.pc-card--blue` / `--green` | Acento superior |
| `.pc-panel` | Sección interna |
| `.pc-panel--soft` | Fondo gris suave |
| `.pc-panel--dashed` | Zona de ayuda / vacío |
| `.pc-panel-title` | Título de sección |

Alias legacy: `.panel`, `.form-card`, `.card`, `.module-card`.

### KPIs

```html
<div class="pc-kpi-grid">
  <div class="pc-kpi"><span>Etiqueta</span><strong>123</strong></div>
  <div class="pc-kpi pc-kpi--warn">...</div>
</div>
```

Variantes: `--warn`, `--danger`, `--success`.

### Formularios

| Clase | Uso |
|--------|-----|
| `.pc-form-grid` | Campos en rejilla responsive |
| `.pc-form-panel` | Card de formulario |
| `.pc-form-row` | Fila horizontal de campos |
| `.pc-field` | label + input |
| `.pc-field-hint` | Texto ayuda |
| `.pc-file-panel` | Zona de carga de archivo |

Los `input`/`select`/`textarea` ya tienen estilo global (Fase B). Para archivos, envolver en `.pc-file-panel`.

### Tablas

```html
<div class="pc-table-wrap pc-scroll-x">
  <div class="pc-table-toolbar">
    <h3 class="pc-table-title">Listado</h3>
    <div class="pc-cluster">...</div>
  </div>
  <table class="pc-table">
    <thead>...</thead>
    <tbody>...</tbody>
  </table>
</div>
```

Alias: `.table-wrap` (global). Estado vacío: `.pc-empty-state`.

**No** uses selectores globales nuevos tipo `table { }` en módulos; usa `.pc-table` opt-in.

### Badges

| Clase | Equivalente legacy |
|--------|---------------------|
| `.pc-badge.pc-badge--success` | `.badge-ok` |
| `.pc-badge--warning` | `.badge-warning` |
| `.pc-badge--danger` | `.badge-error` |
| `.pc-badge--info` | `.badge-info` |
| `.pc-badge--neutral` | `.badge-neutral` |

### Acciones (D.9)

```html
<td class="actions-cell">
  <a class="btn-icon" href="..." title="Ver" aria-label="Ver">
    <i data-lucide="eye"></i>
  </a>
  <form class="inline-form" method="post" action="...">
    <button type="submit" class="btn-icon btn-icon--danger" title="Eliminar" aria-label="Eliminar">
      <i data-lucide="trash-2"></i>
    </button>
  </form>
</td>
```

Clases reconocidas globalmente: `.btn-icon`, `.actions-cell`, `.table-actions`, `.icon-link`, `.action-btn`, variantes `--danger`, `--success`, `--muted`.

### Layout

`.pc-stack`, `.pc-cluster`, `.pc-grid-2|3|4`, `.pc-scroll-x`.

---

## 5. CSS por módulo (cuando hace falta)

Si la pantalla es muy densa (p. ej. cálculo de nómina, tabla >2000px):

1. Crear `static/nomina/<modulo>.css` (o ruta del dominio).
2. En template: `{% block extra_head %}<link rel="stylesheet" href="{{ url_for('static', filename='nomina/....css') }}">{% endblock %}`.
3. Usar **solo** tokens `var(--pc-*)` — no duplicar paleta.
4. Prefijo de módulo (`calc-`, `param-`, `vac-`) para reglas específicas; reutilizar `.pc-*` para lo genérico.

---

## 6. Ejemplo mínimo de módulo futuro

```html
{% extends "base.html" %}
{% block extra_head %}
<link rel="stylesheet" href="{{ url_for('static', filename='nomina/ejemplo.css') }}">
{% endblock %}
{% block content %}
<section class="page-head pc-page-head">
  <div class="pc-page-head__main">
    <span class="pc-page-eyebrow">Nóminas</span>
    <h2 class="pc-page-title">Ejemplo</h2>
    <p class="pc-page-subtitle">Subtítulo del módulo.</p>
  </div>
  <div class="pc-page-actions">
    <a class="btn btn-secondary" href="{{ url_for('nomina.index') }}">Volver</a>
  </div>
</section>

<div class="pc-stack">
  <form class="pc-form-panel pc-form-grid" method="get" action="...">
    <div class="pc-field">
      <label for="f1">Cliente</label>
      <select id="f1" name="cliente">...</select>
    </div>
    <div class="pc-field">
      <button type="submit" class="btn btn-primary">Filtrar</button>
    </div>
  </form>

  <section class="pc-card">
    <h3 class="pc-panel-title">Resultados</h3>
    <div class="pc-table-wrap pc-scroll-x">
      <table class="pc-table">
        <thead>...</thead>
        <tbody>
          <tr>
            <td>...</td>
            <td class="actions-cell">
              <a class="btn-icon" href="..." title="Detalle" aria-label="Detalle">
                <i data-lucide="file-text"></i>
              </a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</div>
{% endblock %}
```

---

## 7. Reglas de seguridad (obligatorias)

En migraciones visuales **no modificar**:

- `href`, `url_for`, rutas, endpoints
- `name`, `value`, `action`, `method`, `enctype` de formularios
- IDs usados por JS o backend
- Variables Jinja y `{% if %}` de negocio
- Permisos / roles
- Columnas, datos, historial, importaciones, exportaciones
- Motor de cálculo, ISR, subsidio, documentos DOCX/PDF/PNG

**Archivos típicos permitidos:** `static/style.css`, `static/<modulo>/*.css`, templates del módulo (clases/HTML), `docs/proclean_visual_system.md`.

**Prohibido** para UI sola: `.py`, `.js`, migraciones DB, plantillas documentales.

---

## 8. Checklist visual antes de commit

- [ ] `git status` solo muestra archivos CSS/docs/templates del alcance
- [ ] Page head legible; acciones con `.btn` pill, no gris nativo
- [ ] Tablas anchas con scroll (`.pc-table-wrap` / `.pc-scroll-x`)
- [ ] Acciones de tabla usan `.btn-icon` + Lucide (D.9)
- [ ] Badges para estados, no emojis
- [ ] Sin gradientes fuertes en cards de datos
- [ ] Contraste texto/fondo OK en warnings
- [ ] Responsive: grids colapsan en &lt;900px
- [ ] No se tocó Python/JS/DB/rutas/names

---

## 9. Mapa de fases en `style.css`

| Bloque | Contenido |
|--------|-----------|
| `:root` + Fase B | Tokens `--pc-*`, botones, inputs, tablas globales |
| D.1 | Shell, fondo decorativo |
| D.2 | `.card`, `.module-card`, badges refinados |
| D.4–D.5 | Sidebar iconografía + active state |
| D.9 | Acciones tabla / erradicar botones grises en celdas |
| **D.20** | Biblioteca `.pc-*` opt-in + ajustes legacy seguros |
| **E.1** | Enriquecimiento visual global (hero compacto, icon chip, variantes suaves, KPIs vivos, empty states) |
| **E.4** | Formularios, capturas, importación y preflight (`.pc-form-*`, `.pc-import-panel`, action bars) |
| **E.5** | Historiales, tablas, toolbars, filtros, acciones por fila (`.pc-table-*`, `.pc-history-*`) |
| **E.6** | Auditoría visual, regresiones, puentes CSS legacy (sin borrado masivo) |
| **E.6.1** | Tablas neutral-first (blanco/gris; color solo en badges/acciones) |

---

## 10. Visual enrichment patterns (Fase E.1)

Capa **opt-in** en `static/style.css` (bloque E.1). Complementa D.20 sin sustituir estilos de módulos ya pulidos (p. ej. `nd-*` en Dashboard Nómina).

| Patrón | Clases | Cuándo usar |
|--------|--------|-------------|
| **Hero compacto** | `.pc-hero-compact`, `__main`, `__deco` | Cabecera de módulo con badge, título y subtítulo; sustituye o envuelve `.page-head` plano. Incluye gradiente suave y blobs. No inventar texto funcional. |
| **Icon chip** | `.pc-section-head` + `.pc-icon-chip` (`--blue`, `--green`, `--neutral`, `--warn`) | Título de sección o card con icono SVG inline (24×24 stroke). Un chip por bloque; el `h3` va en `.pc-panel-title` dentro de `.pc-section-head`. |
| **Decorative background** | `.pc-surface-decor`, `--green`, `.pc-layout-decor` | Panel o layout con círculos radiales muy tenues. No en tablas densas ni sobre inputs. |
| **Soft card variants** | `.pc-panel--soft-blue`, `--soft-green`, `--neutral`, `--warn-subtle`, `--gradient-result` | Variar bloques sin repetir `border-left` en todo. Azul = captura/datos; verde = resultado/éxito; neutral = formulario denso; warn = avisos; gradient-result = panel de salida/cálculo. |
| **KPI tiles** | `.pc-kpi--vivid`, `.pc-kpi-grid--auto` | Métricas derivadas del cálculo o resumen. `--auto` estiliza hijos `div` sin cambiar JS (usa `:empty` para ocultar). Para KPIs estáticos en HTML, preferir `.pc-kpi-grid` + `.pc-kpi`. |
| **Empty states** | `.pc-empty-state`, `--rich`, `__icon`, `__title`, `__text` | Listas o paneles sin datos. `--rich` solo si el mensaje es fijo en HTML; si JS asigna `textContent` al nodo, usar `.pc-empty-state` simple en ese `id`. |

**También E.1:** `.pc-chip` (etiquetas inline), `.pc-divider` (separador interno), `.pc-result-actions` (barra de botones en panel resultado).

**Ejemplo — sección con chip y variante suave:**

```html
<section class="fin-sec pc-panel pc-panel--soft-blue pc-surface-decor">
  <div class="pc-section-head">
    <span class="pc-icon-chip pc-icon-chip--blue" aria-hidden="true">
      <svg viewBox="0 0 24 24">...</svg>
    </span>
    <h3 class="pc-panel-title">Datos generales</h3>
  </div>
  <!-- campos sin cambiar name/id -->
</section>
```

**Regla:** no cambiar `name`, `id`, `value`, `action`, `method` ni rutas al aplicar E.1.

### Composición narrativa (Fase E.2 — Finiquitos piloto)

| Patrón | Clases (módulo) | Uso |
|--------|-----------------|-----|
| Pasos numerados | `.fin-step-head`, `.fin-step-num`, `.fin-narrative-step` | Jerarquía 01–04 solo visual; no altera orden funcional de campos. |
| Barra Excel | `.fin-excel-bar` | Acción secundaria premium; mismos `#btn_excel` y `#excel_msg`. |
| Panel resultado | `.fin-result-panel`, `.fin-result-body`, `.fin-calc-empty` | Empty state con CSS `:has(#preview:not([hidden]))`; sticky en desktop. |
| Tarjetas internas | `.fin-inner-card` | Agrupar toggles sin `border-left`. |

---

## 11. Módulos con CSS local (referencia)

| Módulo | CSS |
|--------|-----|
| Dashboard Nómina | inline en `dashboard.html` + `style.css` |
| Check ID | `static/checkid/checkid.css` |
| Exámenes médicos | `static/examenes_medicos/examenes_medicos.css` |
| IMSS | `static/movimientos_imss/imss.css` |
| Headcount | `static/headcount/headcount.css` |
| Vitroflex | `static/vitroflex_docs/vitroflex.css` |
| Carrier/Cursos | `static/carrier/carrier.css` |
| Facturación | `static/facturacion/facturacion.css` |
| Finiquitos | `static/finiquitos/finiquitos.css` + patrones E.1 (`style.css`) |
| Hub asistencia | `static/nomina/asistencia_hub.css` |
| Vacaciones | `static/nomina/vacaciones.css` |
| INFONAVIT | `static/nomina/infonavit.css` |
| Parámetros | `static/nomina/parametros.css` |
| Cálculo | `static/nomina/calculo.css` |

**Pendientes de migración visual** (siguen con estilos inline o legacy): Comparativo, Login (parcial). **Consolidación CSS local → Fase E.6.**

---

## 12. Fase E.4 — Form and capture enrichment

Capa **opt-in** en `static/style.css` (bloque E.4). Objetivo: formularios, importaciones y preflight con composición narrativa (pasos 01–04 **solo visual**), sin tocar motor de cálculo, rutas, `name`/`id`/`value`, ni JS funcional.

### Cuándo usar cada patrón

| Patrón | Clases | Cuándo usar |
|--------|--------|-------------|
| **Shell de formulario** | `.pc-form-shell`, `.pc-form-shell--flat` | Envolver un `<form>` o panel de captura densa (IMSS movimiento, Check ID consulta, Exámenes, Vitroflex MEMO/CR, workspace Carrier). Aporta padding, sombra suave y gap entre bloques. `--flat` en embeds o paneles que ya tienen borde propio. |
| **Hero de pantalla** | `.pc-hub-hero`, `.pc-form-hero`, `__main`, `__actions` | Cabecera de módulo con eyebrow, título y acciones (Volver, Importar). Compatible con `.page-head` existente. |
| **Pasos narrativos** | `.pc-form-step-head`, `.pc-form-step-number` (`--green`, `--amber`), `.pc-form-step-icon`, `.pc-form-step-title`, `.pc-form-step-desc` | Secciones 01 Datos → 02 Archivo → 03 Validación → 04 Acciones. **No reordenar campos** si el backend depende del orden. |
| **Panel de importación** | `.pc-import-panel`, `.pc-form-section` | Zona de carga Excel/PDF: borde dashed azul, fondo suave. Usar con `.pc-form-actionbar` para el submit. |
| **Preflight / preview** | `.pc-preflight-panel`, `.pc-warning-soft` | Pantallas previas al cálculo o preview de importación (staging). Avisos sin sustituir lógica de validación. |
| **Resumen lateral** | `.pc-side-summary` | KPIs o resumen de importación vigente (INFONAVIT paso 01). |
| **Action bar** | `.pc-form-actionbar`, `--primary`, `.pc-sticky-actions` | Botones finales agrupados (Guardar, Importar, Calcular). Misma `type`, `name` y `action` en los botones. |
| **Zona segura** | `.pc-safe-zone` | Contenedor de página que agrupa cards sin alterar hijos funcionales. |
| **Ayudas** | `.pc-form-help`, `.pc-inline-hint`, `.pc-field-note` | Texto de apoyo; no sustituir mensajes de error del backend. |

### Ejemplo mínimo (importación)

```html
<section class="page-head pc-hub-hero pc-form-hero">...</section>
<div class="ni-wrap pc-safe-zone">
  <section class="ni-card pc-import-panel pc-form-section">
    <header class="pc-form-step-head">
      <span class="pc-form-step-number" aria-hidden="true">02</span>
      <div class="pc-form-step-text">
        <h3 class="pc-form-step-title">Importar archivo</h3>
        <p class="pc-form-step-desc">Descripción existente.</p>
      </div>
    </header>
    <form class="pc-form-actionbar" method="post" action="..." enctype="multipart/form-data">
      <input type="file" name="excel_file" required>
      <button type="submit" class="btn btn-primary">Importar</button>
    </form>
  </section>
</div>
```

### Formularios sensibles — prohibido

En pantallas con cálculo, exportación DOCX/PDF, vinculación Headcount o muchos `data-*` controlados por JS:

| Permitido | Prohibido |
|-----------|-----------|
| Hero, `.pc-safe-zone`, headers de paso | Cambiar `name`, `id`, `value`, `data-*` |
| `.pc-form-shell` envolviendo sin mover inputs | Reordenar campos o filas de tabla |
| `.pc-import-panel` / action bar visual | Cambiar `action`, `method`, `href` funcional, `onclick` |
| Icon chips SVG inline | Tocar `.py`, rutas Flask, SQLite, JS funcional |
| Spacing y jerarquía tipográfica | Consolidar CSS local de módulo (salvo mínimo necesario) |
| Empty states ya presentes en HTML | Historiales profundos, filtros avanzados, acciones por fila (**E.5**) |

### Fuera de alcance E.4 (reservado E.5)

- Historiales con tablas anchas y paginación operativa.
- Filtros GET complejos sobre listados (solo se permitió encabezado visual en import/revisión, no rediseño de tabla).
- Acciones por fila, detalles expandibles, toolbars de historial.
- Limpieza agresiva de CSS por módulo.

### Pantallas referencia E.4

Movimiento/constancia IMSS (`_movimiento_constancia_form.html`), Exportación IMSS index (captura), Check ID (consulta), Exámenes médicos, Parámetros (importaciones), Cálculo index/preflight, INFONAVIT import/revisión (cabeceras), Vacaciones import/preview, Facturación import/form, Carrier paquete/workspace, Vitroflex MEMO/CR.

---

## 13. Fase E.5 — History, table and listing enrichment

Capa **opt-in** en `static/style.css` (bloque E.5). Aplica a historiales, listados operativos y tablas densas **sin** alterar columnas, query params, POST/GET ni JS funcional.

### Cuándo usar cada patrón

| Patrón | Clases | Cuándo usar |
|--------|--------|-------------|
| **Shell de historial** | `.pc-history-shell`, `.pc-list-panel` | Página o sección dedicada a historial (Movimientos IMSS, Finiquitos, Check ID recientes, Carrier Cursos, Exámenes). Envuelve toolbar + tabla + empty state. |
| **Hero de historial** | `.pc-history-hero`, `.pc-hub-hero` | Cabecera con eyebrow, título y acciones «Volver». |
| **Shell de tabla** | `.pc-table-shell` | Card/sección que contiene una tabla operativa (Parámetros, INFONAVIT avisos, Vacaciones, Cálculo, Facturación). |
| **Toolbar / filtros** | `.pc-table-toolbar`, `.pc-filter-bar` | Barra de búsqueda client-side (`.history-toolbar`) o formulario GET de filtros (`.param-filters`, `.vac-filters-grid`, `.fx-filters-panel`, `.hc-filters`). **No** añadir campos ni cambiar `name`. |
| **Búsqueda** | `.pc-search-box` (clase extra en `input`, sin cambiar `id`) | Inputs `#hist-search`, `.history-search-input` cuando el `id` lo exige el JS. |
| **Scroll** | `.pc-table-scroll` | Contenedor con overflow horizontal (`.table-wrap`, `.param-table-wrap`, `.fx-table-wrap`, `.ni-table-wrap`, etc.). |
| **Densidad** | `.pc-density-compact` | Tablas con muchas columnas (Check ID, Parámetros, Vacaciones). |
| **Caption / meta** | `.pc-table-caption`, `.pc-table-meta` | Título de bloque tabla y nota secundaria. |
| **Acciones por fila** | `.pc-row-actions` en `.actions-cell` | Agrupa `.btn-icon` y formularios inline sin cambiar `action` ni botones. |
| **Badges de estado** | `.pc-status-badge`, `--success`, `--warning`, `--danger`, `--neutral` | Texto de estado ya presente (p. ej. badges INFONAVIT, `.imss-badge`). Complementa, no sustituye clases módulo si el JS las usa. |
| **Empty state** | `.pc-table-empty` | Mensaje sin filas en HTML o `#fin_hist_empty`. Filas `colspan` vacías se estilan vía `.pc-table-shell tbody td[colspan]`. |
| **Nota auditoría** | `.pc-audit-note` | Avisos bajo tabla o en bandejas de revisión. |

### Ejemplo — historial con búsqueda

```html
<section class="page-head pc-hub-hero pc-history-hero">...</section>
<section class="panel pc-history-shell pc-list-panel">
  <div class="history-toolbar pc-table-toolbar pc-filter-bar">
    <label>Buscar <input type="search" class="history-search-input" id="history-search"></label>
  </div>
  <div class="table-wrap pc-table-scroll pc-table-shell pc-density-compact">
    <table>...</table>
  </div>
  <p class="empty-state pc-table-empty" hidden>Sin registros.</p>
</section>
```

### Tablas sensibles — prohibido

| Permitido | Prohibido |
|-----------|-----------|
| Wrappers, toolbar visual, scroll, badges decorativos | Cambiar columnas u orden |
| `.pc-row-actions` en celdas existentes | Cambiar `href`, `action`, `method`, query params |
| Clases en contenedor scroll | Reordenar filas o celdas |
| Empty state en nodos ya usados por Jinja/JS | Tocar JS funcional, paginación, filtros nuevos |
| `pc-search-box` además de `id` requerido | Cambiar permisos `{% if %}` por rol |

### Fuera de alcance E.5 (reservado E.6)

- Consolidación agresiva de CSS por módulo.
- Refactor de nombres de clases legacy.
- Comparativo / visualizadores con JS muy acoplado (solo si no hay riesgo).
- `_asistencia_hub_visualizer.html` (solo wrapper mínimo en contenedor si aplica).

### Pantallas referencia E.5

`history.html`, `finiquitos/historial.html`, `checkid.html` (recientes), `carrier/cursos_historial.html`, `examenes_medicos_historial.html`, `headcount/historial_sua.html`, `_headcount_cliente_table.html`, `_sua_historial.html`, `nomina/parametros_index.html`, `parametros_conciliacion.html`, `infonavit_index.html`, `vacaciones_index.html`, `calculo_index.html`, `calculo_view.html`, `dashboard.html` (historial imports), `exportacion_imss/index.html` (historial exportaciones), `facturacion_*` listados, `users.html`, Vitroflex tablas trabajadores (wrapper).

---

## 14. Fase E.6 — Visual audit and safe consolidation

Fase de **corrección fina**, no de expansión. Objetivo: consistencia entre capas E.1–E.5 y CSS local de módulos, sin refactor global ni borrado de clases legacy.

### Qué se corrigió (patrones)

| Área | Acción |
|------|--------|
| Toolbars anidadas | CSS E.6 anula padding/borde del `<form>` hijo cuando el padre ya es `.pc-filter-bar` (Vacaciones, Conciliación). |
| Shell + card módulo | Reglas para `.panel.pc-history-shell`, `.fin-hist-card.pc-history-shell` — una sola sombra/borde dominante. |
| Scroll + shell | `.pc-table-scroll.pc-table-shell` en el mismo nodo: un solo borde visible. |
| Sub-hero interno | No usar `.pc-history-hero` en cabeceras dentro de cards (p. ej. Check ID recientes). |
| Badges legacy | Puentes en `style.css` para `.vac-badge`, `.calc-chip`, `.calc-status`, `.em-chip` dentro de `.pc-table-shell`. |
| Legibilidad | `-webkit-font-smoothing: antialiased` en bloques `.pc-*`; thead sin `backdrop-filter` borroso. |

### Consolidación conservadora

- **Se mantiene** CSS local: `finiquitos.css`, `nomina/*.css`, `carrier.css`, `facturacion.css`, inline Comparativo/Exportación IMSS.
- **Se añadió** puente en `finiquitos.css` para historial + clases `.pc-*`.
- **No se eliminaron** clases `.vac-badge`, `.nd-*`, `.fx-*`, `.nom-hub-*`, `.calc-*`, `.hc-*`.

### Excepciones permanentes (solo wrapper o sin tocar)

| Pantalla | Motivo |
|----------|--------|
| `nomina/_asistencia_hub_visualizer.html` | Tabla y filtros 100 % JS (`nomi-fbtn`, columnas dinámicas). Solo `.pc-table-scroll` en contenedor. |
| `exportacion_imss/index.html` (panel izquierdo + listas movimientos) | Estado y render JS; panel derecho: clase suave `.pc-list-panel-soft`. |
| `comparativo/index.html` | Layout propio inline + JS; hero + scroll en tablas/historial. |
| `comparativo/reporte_mensual.html` | No auditado en E.6 (mismo riesgo que comparativo). |
| Cálculo view / preflight motor | Formularios con inputs ocultos y POST sensibles — sin reestructura. |
| Login | `auth-card` legacy; botones ya usan `.btn-primary`. |

### Reglas para cambios futuros

1. Un **hero** por pantalla (`.pc-hub-hero` en `page-head`); subtítulos de sección con `.pc-table-caption`.
2. **Toolbar**: o el contenedor `.pc-filter-bar` o el `<form>`, no ambos con caja completa.
3. Antes de borrar CSS local, comprobar uso en templates y JS.
4. Preferir **puente** en `style.css` E.6 antes de renombrar HTML.

### Tablas neutral-first (Fase E.6.1)

**Tables should stay neutral-first: white surfaces, subtle headers, minimal row tint. Use color only for semantic badges/actions.**

| Elemento | Tratamiento |
|----------|-------------|
| Shell / scroll / list panel | Fondo `#ffffff`, borde `#e2e8f0`, sombra ligera o ninguna |
| Toolbar / filtros | `#f8fafc`, sin gradiente azul/verde |
| `thead th` | `#f8fafc`, borde inferior gris |
| Filas `tbody` | `#ffffff`; hover `#f8fafc` |
| Zebra | Desactivada en `.pc-table-*` |
| Badges, `.btn-icon`, alertas | Conservan color semántico |

Implementado en `style.css` (E.5/E.6 ajustados + bloque E.6.1). CSS local de módulos (`imss.css`, `parametros.css`, etc.) puede seguir existiendo; los puentes E.6.1 en contenedores `.pc-table-scroll` prevalecen en listados migrados.

### QA mínimo pre-deploy

Ver checklist al final de la entrega del agente o repetir recorrido sidebar: Dashboard admin, Home, Nómina hub/dashboard, Finiquitos + historial, IMSS + historial, Check ID, Headcount, Facturación, Carrier, Vitroflex, Exámenes, INFONAVIT, Vacaciones, Parámetros, Cálculo, Usuarios, Exportación IMSS.

---

*Última actualización: Fase E.6.1 — neutral-first tables.*
