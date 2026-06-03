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

**Pendientes de migración visual** (siguen con estilos inline o legacy): Comparativo, Exportación IMSS, Users, History global, Login (parcialmente cubierto por auth).

---

*Última actualización: Fase E.1 — enriquecimiento visual global + Finiquitos piloto.*
