# Sportswear

Sistema web modular para la gestión de una tienda de ropa deportiva. La implementación sigue el plan incremental del archivo `prompt`; las **Fases 1 a 8** cuentan con implementación inicial y pruebas locales.

## Arquitectura inicial

- `config`: configuración Django, URLs y puntos de entrada WSGI/ASGI.
- `apps/core`: dashboard, selección segura de sucursal y elementos comunes.
- `apps/users`: usuario personalizado, roles iniciales y asignación de sucursales autorizadas.
- `apps/branches`: modelo, permisos y gestión de sucursales.
- `apps/products`: categorías, marcas, tallas, colores, productos y variantes.
- `apps/inventory`: existencias por sucursal y variante, operaciones trazables y transferencias con recepción parcial.
- `apps/customers`: información e historial visible de clientes.
- `apps/sales`: punto de venta, pagos configurables, comprobantes, anulaciones y devoluciones.
- `apps/cash`: recursos de caja, turnos, cobros vinculados y arqueos inmutables.
- `apps/finance`: categorías, cuentas, ingresos/egresos con autorización, contabilización en caja y reversiones compensatorias.
- `apps/reports`: dashboard y reportes derivados de ventas, inventario, caja y finanzas, con exportación CSV/XLSX/PDF.
- `apps/audit`: historial de cambios de catálogo, sucursales, clientes, permisos de usuarios y accesos, e inicios de sesión.
- `templates/` y `static/`: plantilla compartida, autenticación, estilos y JavaScript.

El usuario personalizado se configura antes de la primera migración. Los roles describen el perfil inicial; las vistas de administración del catálogo y sucursales están protegidas con permisos y grupos de Django. Los módulos restantes se añadirán de forma incremental, sin crear registros comerciales de demostración.

## Requisitos y ejecución local

Se requiere Python 3.10 o superior.

1. Crear/activar un entorno virtual e instalar dependencias:

	```sh
	python -m venv .venv
	source .venv/bin/activate
	pip install -r requirements.txt
	```

2. Copiar `.env.example` a `.env`. SQLite está seleccionado para desarrollo. Cambiar `DJANGO_SECRET_KEY` antes de exponer el sistema.
3. Crear el esquema y una cuenta administrativa:

	```sh
	python manage.py makemigrations
	python manage.py migrate
	python manage.py createsuperuser
	```

4. Ejecutar comprobaciones y pruebas, y levantar el servidor:

	```sh
	python manage.py check
	python manage.py test
	python manage.py runserver
	```

Abrir `http://127.0.0.1:8000/` para iniciar sesión y `/admin/` para la administración inicial. La recuperación de contraseña usa la consola local por defecto; para producción hay que configurar un backend de correo y credenciales mediante variables de entorno.

## Base de datos en producción: SQLite

La instalación actual utiliza SQLite también en producción. Configura `DB_ENGINE=sqlite`, `DB_NAME` con una ruta persistente del servidor y permisos de lectura/escritura solo para el proceso de Django. `SQLITE_TIMEOUT=30` permite esperar por un bloqueo breve; `SQLITE_TRANSACTION_MODE=IMMEDIATE` adquiere el bloqueo de escritura al comenzar una transacción y ayuda a evitar carreras de lectura-a-escritura. SQLite admite un escritor a la vez: limita workers y tareas de escritura simultáneos, usa disco local (no NFS/SMB), monitorea `database is locked` y conserva copias de seguridad consistentes fuera del host. No ejecutar varios servidores independientes contra el mismo archivo.

La lógica con `select_for_update()` no proporciona bloqueos de fila en SQLite. Por ello, el control de inventario se apoya en transacciones IMMEDIATE y restricciones de base de datos, pero no equivale a PostgreSQL con concurrencia de escritura; una prueba de carga/concurrencia de la instancia SQLite de producción es requisito antes de aceptación.

## Convenciones

- Código y nombres técnicos en inglés; interfaz en español.
- Vistas basadas en funciones, formularios Django y validación en servidor.
- Importes monetarios con `DecimalField`.
	- Las ventas, inventario, caja y finanzas usan servicios de negocio y transacciones para sus operaciones críticas.

## Fase 2: sucursales y catálogo de productos

La segunda fase agrega:

- Gestión de sucursales con estado activo/inactivo, código, dirección, contacto y responsable.
- Catálogos administrables de categorías jerárquicas, marcas, tallas y colores.
- Productos con código interno, nombre, modelo, proveedor opcional, categoría, marca, costos y precios `DecimalField`, promoción opcional, impuesto e imagen.
- Variantes vendibles con SKU, código de barras, talla/color y precios/costos opcionales que heredan los valores base del producto.
- Búsqueda por nombre, código interno, SKU y código de barras; filtros por categoría, marca y estado; paginación.
- Administración web y Django Admin. Los registros históricos se desactivan en vez de borrarse.
- Validación contra ciclos en categorías y contra cambios de precio base que dejarían inválida una promoción heredada de una variante.

### Permisos

Las páginas web exigen permisos Django del modelo (por ejemplo `branches.view_branch`, `products.add_product` y `products.change_productvariant`); el superusuario tiene acceso implícito. Asigna permisos directamente o mediante grupos desde `/admin/auth/group/`. Los roles del perfil no conceden automáticamente acceso a la administración, evitando elevar permisos solo por una etiqueta de rol. El selector de sucursal continúa limitando las opciones a sucursales activas autorizadas (o todas para Administradores/superusuarios).

### Validación adicional

Después de los cambios de modelos, generar y aplicar migraciones. Ejecutar `python manage.py check` y `python manage.py test`. Las imágenes cargadas localmente se sirven desde `media/` en modo DEBUG; producción debe configurar el almacenamiento de media apropiado. La Fase 2 no implementó existencias, movimientos, importaciones CSV/Excel ni ventas; esos datos se incorporan de forma incremental.

## Fase 3: inventario y movimientos de mercadería

- Existencias independientes por sucursal y variante, con stock mínimo y valorización por costo promedio ponderado.
- Entradas, salidas y conteos físicos convertidos en ajustes positivos/negativos dentro de transacciones atómicas. Cada operación registra responsable, motivo, fecha, saldo posterior y clave idempotente.
- Transferencias con estados borrador, pendiente, en tránsito, recepción parcial, recibida o cancelada. La recepción final registra faltantes sin sumar esas unidades al destino.
- Las unidades se descuentan del origen al despachar y se agregan al destino al confirmar cada recepción. Las transferencias no crean ventas ni ingresos financieros.
- Las vistas limitan sucursales en servidor según permisos y asignaciones. El Admin conserva el historial en modo solo lectura para evitar ajustes sin movimiento asociado.

Las transacciones IMMEDIATE de SQLite serializan los escritores y las restricciones protegen la integridad; SQLite no ofrece los bloqueos de fila de `select_for_update()`. La prueba automatizada valida reglas funcionales, pero la concurrencia de escritura debe probarse con la configuración SQLite productiva y su volumen real.

## Fase 4: clientes y ventas

- Registro y búsqueda de clientes con documento opcional, contacto, observaciones e historial de ventas limitado por sucursales autorizadas.
- Punto de venta con búsqueda por nombre, código interno, SKU o código de barras, precios efectivos, existencia por sucursal, carrito rápido, cliente opcional y pagos mixtos.
- Medios de pago configurables desde Django Admin; se cargan Efectivo, Transferencia bancaria, QR y Tarjeta como configuración inicial. Solo Efectivo se marca como efectivo físico; su enlace con turnos de caja se realizará en Fase 5.
- El servidor vuelve a calcular precios, descuentos autorizados e impuestos; registra copias de precios, costos y descripción en el comprobante. Una venta confirmada actualiza pagos e inventario en la misma transacción y no puede editarse directamente.
- Las reversiones y devoluciones crean registros compensatorios y movimientos positivos de inventario. Se admiten devoluciones parciales, con reembolso calculado desde los importes históricos. El formato de comprobante actual es interno e imprimible, no fiscal.
- La clave de idempotencia previene reintentos secuenciales. En SQLite, las transacciones IMMEDIATE serializan escritores; el motor no bloquea filas individuales y, en caso de concurrencia alta, el segundo escritor podría esperar o recibir `database is locked`. Probar este límite en el servidor y con los workers de producción.

Configura permisos Django mediante grupos, incluidos `sales.apply_discount`, `sales.cancel_sale`, `sales.process_salereturn` y `sales.view_all_sales`. Los vendedores sin el último permiso solo consultan sus propias operaciones; los permisos no sustituyen la comprobación de sucursales autorizadas.

## Fase 5: caja y turnos

- Cada recurso de caja pertenece a una sucursal y se configura desde Django Admin. Una restricción de base de datos impide turnos abiertos duplicados en el mismo recurso.
- Aperturas registran saldo inicial, responsable, fecha y observaciones. Los retiros manuales requieren un turno abierto; ingresos y egresos categorizados se registran en Finanzas para evitar duplicados y aplicar autorizaciones.
- Cada cobro de venta, incluido QR, tarjeta o transferencia, queda vinculado al turno. La clasificación de efectivo/no efectivo se guarda como una copia histórica para que cambios futuros del catálogo de medios de pago no alteren cierres anteriores.
- El cierre guarda efectivo contado, efectivo esperado, cobros, ingresos, egresos, retiros, reembolsos, diferencia, usuario y fecha; no se puede editar desde la interfaz ni desde Django Admin.
- Ventas requieren un turno abierto; las devoluciones y anulaciones seleccionan un turno abierto para registrar sus reembolsos. Esto mantiene trazabilidad, pero no permite reembolsar desde una caja cerrada; se debe abrir el turno operativo correspondiente.
- El efectivo esperado se calcula como saldo inicial + cobros de ventas en efectivo + ingresos adicionales − egresos − retiros − reembolsos en efectivo. Los cobros electrónicos se informan aparte y no afectan el efectivo físico.

Asigna permisos Django `cash.view_cashsession`, `cash.open_cashsession`, `cash.close_cashsession`, `cash.add_cashmovement` y `cash.view_cashmovement` mediante grupos. Las pruebas locales usan SQLite; validar apertura simultánea de turnos en el mismo archivo persistente de producción y revisar errores de espera de bloqueo.

## Fase 6: ingresos y egresos

- El nuevo módulo `apps/finance` registra ingresos y egresos categorizados, cuentas por sucursal o generales, fecha, concepto, importe y comprobante adjunto opcional.
- Las cuentas bancarias mantienen un saldo inicial contable y muestran un saldo calculado a partir de operaciones contabilizadas. Las cuentas de efectivo se vinculan a una caja física; el saldo inicial de efectivo pertenece a cada turno.
- Los pagos de ventas y sus reembolsos no se vuelven a registrar como operaciones financieras. Las operaciones de caja ingresadas desde Finanzas crean su movimiento categorizado y vinculado; el formulario de caja queda para retiros manuales, evitando ingresos/egresos sin categoría.
- Los límites pueden definirse por usuario o rol, con alcance por sucursal o general. Si el importe supera el límite del creador, la operación queda pendiente y no afecta caja ni cuenta. La autoriza otra persona con límite suficiente y permiso; no se permite autoaprobación.
- Los movimientos de efectivo solo se contabilizan al confirmar la operación y deben apuntar a un turno abierto de la caja de la cuenta. Operaciones bancarias no afectan turnos de caja.
- Las operaciones confirmadas no se editan ni eliminan. Reversiones crean una operación opuesta relacionada con la original, dejan historial de eventos y registran un efecto compensatorio en caja cuando corresponde.

Configura categorías, cuentas, límites de autorización y permisos desde Django Admin. Los permisos relevantes son `finance.add_financeoperation`, `finance.view_financeoperation`, `finance.approve_financeoperation`, `finance.reject_financeoperation`, `finance.reverse_financeoperation` y `finance.view_all_financeoperations`; para contabilizar en efectivo también se requiere `cash.add_cashmovement`. Los permisos `finance.use_general_finance_accounts` y `finance.view_general_finance_accounts` controlan por separado el uso y consulta de cuentas bancarias generales. Los saldos bancarios son calculados desde el saldo inicial más operaciones contabilizadas, no un libro bancario conciliado.

## Fase 7: reportes

- Dashboard y reportes de ventas, inventario, caja y finanzas consultan sus módulos de origen; no almacenan agregados duplicados.
- Los filtros usan sucursales activas autorizadas y rangos de fecha limitados a 366 días. Los permisos de reportes se combinan con los permisos de lectura de cada módulo; la información financiera y los métodos de pago requieren permisos separados.
- Las ventas netas restan devoluciones parciales y completas y excluyen ventas anuladas. Las agrupaciones de producto, categoría, marca y variante usan las etiquetas históricas guardadas en cada línea.
- Exportaciones disponibles en CSV (UTF-8), Excel (.xlsx) y PDF. Los límites de exportación evitan generar archivos excesivos; los archivos tabulares escapan valores para impedir fórmulas inyectadas.

Asigna `reports.view_reports`, `reports.view_sales_reports`, `reports.view_inventory_reports`, `reports.view_finance_reports` y `reports.view_payment_reports` mediante grupos. También se requiere el permiso subyacente de lectura: por ejemplo `sales.view_sale`, `inventory.view_stocklevel`, `finance.view_financeoperation` o `cash.view_cashsession`.

## Fase 8: auditoría y seguridad

- `apps/audit` registra actor, acción, fecha, entidad y datos antes/después para el mantenimiento web de sucursales, clientes, productos, variantes y catálogos; registra también inicios de sesión, administración de usuarios, permisos y asignaciones de sucursales. El historial se expone en Django Admin en modo de solo lectura. Los snapshots omiten contraseñas y último inicio de sesión.
- El admin audita asimismo cambios de medios de pago, recursos de caja, categorías/cuentas/límites financieros. Ventas, caja, inventario y operaciones financieras conservan sus historiales de dominio existentes.
- Los clientes ahora registran quién los creó y solo son visibles a usuarios con ventas en sucursales autorizadas o al creador. Las consultas de compras siguen filtradas por permisos de venta y sucursal.
- Los comprobantes financieros se guardan bajo `private_media/`, fuera del `MEDIA_ROOT` público, no generan una URL pública y se descargan por una vista que valida permiso y visibilidad de la operación. Solo admite extensiones PDF/JPG/PNG y archivos de hasta 10 MB. Configura `DJANGO_PRIVATE_MEDIA_ROOT` en un volumen privado persistente para producción.
- Los límites de aprobación financiera tienen unicidad separada para los ámbitos de usuario/rol y sucursal/global, incluyendo alcances con `NULL`.
- Las operaciones de inventario verifican permiso y sucursal autorizada dentro de los servicios, además de la validación en las vistas. Mantener el rol `admin` con acceso global es una decisión explícita actual: `User.has_global_branch_access` otorga acceso a usuarios con ese rol o a superusuarios.
- Desarrollo conserva `DEBUG=True` por defecto. En producción establece explícitamente `DJANGO_DEBUG=False`, una clave aleatoria de al menos 50 caracteres y hosts reales en `DJANGO_ALLOWED_HOSTS`; se habilitan redirección HTTPS, cookies seguras, HSTS y cabeceras defensivas. `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` debe habilitarse solo si todos los subdominios usan HTTPS. `DJANGO_SECURE_HSTS_PRELOAD` permanece apagado; actívalo solo tras verificar el dominio y HTTPS integral. Si TLS termina en un proxy, configura correctamente el proxy y `DJANGO_CSRF_TRUSTED_ORIGINS`.
- Las migraciones aplican `apps.audit`, `customers.0002`, los límites `finance.0004` y el almacenamiento privado `finance.0005`.

### Validación y límites pendientes

Las pruebas y `manage.py check` validan reglas funcionales; no sustituyen pruebas concurrentes de escritura en el SQLite persistente desplegado, que serializa escrituras y no implementa bloqueos de fila. Antes de aceptación, probar ventas simultáneas de las últimas unidades, claves idempotentes, transferencias y aperturas de caja usando el mismo archivo y número de workers de producción; comprobar recuperación ante `database is locked`, latencia y restauración de copias. Ejecutar además `manage.py check --deploy` con la configuración real de proxy/HTTPS. La auditoría cubre los flujos documentados, pero operaciones de mantenimiento manual por SQL o procesos fuera de la aplicación requieren controles operativos y copias de seguridad inmutables.
