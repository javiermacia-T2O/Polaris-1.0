# Medición Ágil Light

Esta edición funciona completamente en local y no incluye la pestaña Agente ni componentes de IA.

Aplicación local de escritorio para cargar CSV, Excel y Parquet, explorar datos,
filtrar, construir tablas, unir datasets, ejecutar análisis y exportar resultados.
Conserva el motor DuckDB/Arrow, las protecciones de memoria y las tareas seguras
de la aplicación original. Los análisis se cargan cuando se usan, para reducir
el tiempo de apertura.

## Uso en otro equipo Windows

1. Extrae **todo** el ZIP en una carpeta.
2. Abre `Iniciar.cmd` dentro de la carpeta extraída o
   `MedicionAgil\MedicionAgil.exe`.

No requiere instalar Python, dependencias, servicios ni modificar el PATH.
Mantén la carpeta `_internal` junto al ejecutable. Los archivos generados se
guardan en la carpeta `output` junto al ejecutable. La primera apertura puede
tardar unos segundos mientras Windows comprueba los archivos extraídos; no se
realiza ninguna instalación al iniciar.

## Código y pruebas

La entrada es `mmm_app\app_desktop.py`. El código y las pruebas están incluidos
en esta carpeta para que la variante se pueda mantener de forma independiente.
La aplicación se ha diseñado para portátiles de 16 GB: los datos grandes siguen
en DuckDB/Parquet y se muestran por páginas; las operaciones que necesitan
Pandas comprueban el presupuesto de memoria.

El presupuesto conjunto se ajusta a la RAM libre: deja al menos 1,5 GiB o un
cuarto de la memoria disponible para Windows y otras aplicaciones, y nunca
supera el 40 % de la RAM física ni 8 GiB. El único worker de tablas puede usar
hasta 3 GiB de ese presupuesto cuando hay margen. Si queda poca memoria libre,
reduce automáticamente el límite y puede pedir cerrar otras aplicaciones.

El constructor inicia con **No pivotar**; cada métrica conserva su propia
opción de pivotado. Al elegir una variable como columna, sus categorías se
muestran como encabezados aunque todavía no hayas añadido métricas. GeoX
incluye un diálogo compacto para elegir regiones de control, exclusiones y
parámetros del diseño. Causal Impact recuerda la última configuración por dataset en
`%LOCALAPPDATA%\MedicionAgil\causal_impact`; **Limpiar sesión** la restablece.
Los resultados se guardan en la carpeta de fecha y cliente seleccionada. Si
un nombre completo supera el límite de rutas de Windows, se acorta de forma
identificable conservando la extensión del archivo.

Para desarrollo, usa Python 3.12 de 64 bits con Tcl/Tk y las dependencias del
repositorio padre. Para reconstruir el ZIP, el script de build requiere
`meridian-geox==1.0.1` en `.geox-deps` junto al proyecto. El paquete para
usuarios ya contiene esas dependencias.
