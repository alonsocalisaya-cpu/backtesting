# Nexo 2 · Plataforma de backtesting

Aplicación local en español para investigar estrategias con velas diarias. Escucha exclusivamente en **127.0.0.1:8000**. Tiene entorno Python propio y no requiere instalar servicios de base de datos. No conecta con brókeres.

La interfaz principal ahora está en **React + TypeScript**, con gráficos Lightweight Charts, editor Monaco y API **FastAPI**. `app.py` conserva la interfaz Streamlit anterior como referencia; el iniciador usa `server.py` y sirve React y API desde el mismo puerto.

## Abrir

Abre `http://localhost:8000` si ya está ejecutándose. Para arrancarla de nuevo, haz doble clic en **iniciar.bat**, o ejecuta `./iniciar.ps1` en PowerShell desde esta carpeta. Si el puerto está ocupado, el iniciador se detiene sin cerrar otros procesos. Ctrl+C detiene solo esta aplicación en su terminal.

## Instalación en otra computadora

Con Python 3.12 o superior, Node.js y pnpm 11 instalados:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd frontend
pnpm install
pnpm run build
cd ..
.\iniciar.ps1
```

`requirements.lock.txt`, generado en esta instalación, permite reproducir las versiones exactas mediante `pip install -r requirements.lock.txt`. Todo se instala en `.venv`, sin cambiar el Python de otros proyectos.

## Flujo de trabajo

### Espacio de trabajo y estrategias por código

El modo predeterminado es **Código Python**. Abre el editor, modifica `on_bar(data, params)` y ejecuta el backtest. La función devuelve `(comprar, vender)` como booleanos. Recibe un DataFrame con el histórico hasta el cierre actual y los indicadores causales precalculados. El motor ejecuta las señales en la apertura siguiente. El código se conserva en la configuración de la prueba y puede descargarse como `.py`.

Las estrategias se ejecutan en un proceso separado con límite de 45 segundos por activo. No es un sandbox de seguridad: usa código propio o de confianza, pues conserva permisos locales. El usuario debe evitar consultas externas a información futura. Pine Script no está soportado directamente; debe traducirse a Python. Por ahora el código define señales; el tamaño de posición, stops y objetivos se configuran en **Parámetros**. No hay estado de posición dentro de `on_bar`.

El gráfico principal permite zoom con rueda, desplazamiento, escala logarítmica y niveles horizontales. Los niveles son visuales, se mantienen durante la sesión de la página y no cambian las órdenes. La barra inferior contiene editor, probador, operaciones y replay; el panel derecho contiene seguimiento y procedencia. El menú izquierdo abre Mercado, Estrategias, Backtesting, Cartera e Historial. Los paneles se pueden ocultar y el inferior se puede redimensionar. No reproduce todas las funciones de TradingView.

Arrastra la barra con el tirador central encima de las pestañas inferiores para ampliar o reducir el probador/editor. El doble clic restaura su altura inicial; al enfocarla con el teclado, las flechas arriba/abajo ajustan la altura. El probador incluye Resumen, Rendimiento, Análisis de operaciones, Por mes y Configuración usada. Las ganancias, pérdidas y rachas se calculan sobre operaciones netas de costos en orden de cierre. El botón Indicadores cambia la visibilidad de las EMA sin reconstruir el gráfico ni restablecer sus rangos.

Los botones 1W y 1M agregan velas solo para visualización; las simulaciones siguen usando 1D. La lista de seguimiento muestra el último cierre del histórico seleccionado y su variación frente a la sesión anterior; no es una cotización en tiempo real. El replay solo oculta el futuro dentro del gráfico central durante la práctica; otras vistas contienen el histórico completo.

1. Pulsa **Parámetros** e introduce símbolos de Yahoo separados por comas (hasta 20) y fechas. La fecha final está incluida. Se excluye el día en curso para no usar una vela sin terminar.
2. Escribe tu código Python en el editor. **Comprobar** verifica sintaxis; **Guardar** lo añade a la biblioteca de Estrategias. Solo compras y acciones enteras, sin apalancamiento.
3. Configura capital compartido, posiciones, riesgo por operación, stop, objetivo, comisión y deslizamiento.
4. Ejecuta. Se guardan pruebas en `data/research.sqlite`, precios en `data/prices` y operaciones/capital en `data/runs`.
5. Consulta resultados, gráfico de operaciones y metadatos. Exporta el ZIP para conservar precios y configuración. Los backtests nuevos conservan una copia de los precios en su carpeta; las pruebas anteriores reutilizan la caché de la versión previa.
6. En Backtesting, usa la validación temporal para elegir objetivo R solo en entrenamiento y evaluar después. Replay permite practicar avanzando una vela cada vez.
7. Compara las pruebas guardadas en Historial. Los cambios de controles no cambian resultados previos hasta ejecutar de nuevo.

## Datos reales y procedencia

La aplicación descarga precios de **Yahoo Finance mediante yfinance**, con `auto_adjust=True` (OHLC ajustados por dividendos y splits). Son series históricas del proveedor, no precios nominales de ejecución. El modelo opera en unidades de precio ajustado y no agrega dividendos otra vez. Yahoo/yfinance puede limitar peticiones o no cubrir ciertos símbolos; los errores se muestran sin sustituir precios por datos ficticios. No se garantiza una fuente libre de errores.

Cada descarga conserva hora UTC, fuente, cobertura, número de filas, tipo de ajuste y huella SHA-256. Reutiliza la caché para el mismo intervalo; **Volver a descargar** actualiza los datos. Se descargan 1500 días naturales previos para calentar indicadores. El inicio efectivo depende de la cobertura del símbolo. No incluye automáticamente empresas retiradas ni componentes históricos de índices.

CSV aceptado: `Date,Open,High,Low,Close,Volume` (también Fecha/Apertura/Cierre/Volumen). Fechas ISO `AAAA-MM-DD`; punto decimal. Un símbolo por archivo. Su autenticidad no se verifica externamente; debe ser un histórico real y coherentemente ajustado si hay splits o dividendos.

## Contabilidad y límites

- Señal al cierre, orden a la apertura siguiente. No se opera durante el calentamiento ni antes de la primera señal del periodo.
- Capital de cartera compartido; prioridad alfabética si faltan fondos o posiciones. Máximo de capital por activo y riesgo objetivo con costos estimados. Un gap puede superar el riesgo objetivo.
- Stops y objetivos fijos. Si la vela toca ambos, stop primero. Un gap bajo stop se ejecuta a apertura; las salidas por señal también a apertura. La asignación se hace antes de liquidaciones intradía, para no reutilizar dinero del futuro.
- Slippage adverso al comprar/vender y comisión en ambas piernas. Sin modelar profundidad de mercado, impuestos, spread adicional o impacto por volumen. Los límites de beneficio se aproximan con ejecución conservadora y slippage.
- Cierre forzoso de posiciones al último cierre disponible de cada activo, con costos. Los huecos no se convierten en velas ficticias; la valoración arrastra el último cierre conocido.
- SPY se incluye como activo de referencia en la lista de seguimiento. La comparación de pruebas en Historial muestra curvas separadas en porcentaje. El CAGR usa días naturales; Sharpe 252 sesiones y tasa libre de riesgo 0. Profit factor no definido si no hay pérdidas. Exposición medida al cierre.
- La validación es una separación temporal simple, no un análisis walk-forward completo. Consultar repetidamente el tramo posterior para ajustar reglas provoca sobreajuste.
- Replay oculta futuras velas dentro de esa pestaña; no evita que el usuario mire el histórico completo en otras pestañas. Los botones de compra/venta avanzan una sesión y gestionan stops/objetivos en ella.
- Rentabilidades históricas no garantizan resultados futuros.

## Comprobación

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/smoke_real.py
.\.venv\Scripts\python.exe scripts/check_platform.py
```

Las pruebas unitarias usan precios artificiales únicamente para verificar contabilidad y ausencia de anticipación. La aplicación no incluye datos de demostración. `smoke_real.py` sí descarga históricos reales y genera una prueba de verificación identificada como tal.

`check_platform.py` comprueba la API del servidor iniciado: carga de históricos reales, conservación del historial, guardado de código, ejecución de backtest, replay y exportación. Crea una prueba y una estrategia EMA de plantilla. `scripts/check_ui.py` verifica la interfaz Streamlit anterior, no la versión React.

## Referencias

- https://ranaroussi.github.io/yfinance/
- https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html
- https://tradingview.github.io/lightweight-charts/
- https://fastapi.tiangolo.com/

Lightweight Charts™ es un producto de TradingView, Inc. https://www.tradingview.com/ . Nexo es una aplicación independiente. La atribución se muestra en el gráfico y en la barra inferior de la plataforma.
