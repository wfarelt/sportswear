# Sportswear

Sistema web modular para la gestión de una tienda de ropa deportiva. La implementación sigue el plan incremental del archivo `prompt`; las **Fases 1 a 5** están implementadas.

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

## PostgreSQL

Configurar `DB_ENGINE=postgresql` y completar `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` y `DB_PORT` en `.env`. SQLite permanece como base de datos local predeterminada.

## Convenciones

- Código y nombres técnicos en inglés; interfaz en español.
- Vistas basadas en funciones, formularios Django y validación en servidor.
- Importes monetarios con `DecimalField`.
- Ventas y caja se implementarán con servicios de negocio y transacciones en sus fases correspondientes; el inventario ya usa operaciones de servicio transaccionales.

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

PostgreSQL utiliza bloqueos de filas durante las actualizaciones de existencias. SQLite sigue disponible para desarrollo; la prueba automatizada valida reglas de integridad, pero no simula concurrencia real de PostgreSQL.

## Fase 4: clientes y ventas

- Registro y búsqueda de clientes con documento opcional, contacto, observaciones e historial de ventas limitado por sucursales autorizadas.
- Punto de venta con búsqueda por nombre, código interno, SKU o código de barras, precios efectivos, existencia por sucursal, carrito rápido, cliente opcional y pagos mixtos.
- Medios de pago configurables desde Django Admin; se cargan Efectivo, Transferencia bancaria, QR y Tarjeta como configuración inicial. Solo Efectivo se marca como efectivo físico; su enlace con turnos de caja se realizará en Fase 5.
- El servidor vuelve a calcular precios, descuentos autorizados e impuestos; registra copias de precios, costos y descripción en el comprobante. Una venta confirmada actualiza pagos e inventario en la misma transacción y no puede editarse directamente.
- Las reversiones y devoluciones crean registros compensatorios y movimientos positivos de inventario. Se admiten devoluciones parciales, con reembolso calculado desde los importes históricos. El formato de comprobante actual es interno e imprimible, no fiscal.
- La clave de idempotencia previene ventas duplicadas ante reintentos. El inventario se bloquea por variante en PostgreSQL; la concurrencia de producción debe probarse en un servidor PostgreSQL real.

Configura permisos Django mediante grupos, incluidos `sales.apply_discount`, `sales.cancel_sale`, `sales.process_salereturn` y `sales.view_all_sales`. Los vendedores sin el último permiso solo consultan sus propias operaciones; los permisos no sustituyen la comprobación de sucursales autorizadas.

## Fase 5: caja y turnos

- Cada recurso de caja pertenece a una sucursal y se configura desde Django Admin. Una restricción de base de datos impide turnos abiertos duplicados en el mismo recurso.
- Aperturas registran saldo inicial, responsable, fecha y observaciones. Los movimientos manuales (ingreso adicional, egreso y retiro) solo se permiten dentro de un turno abierto.
- Cada cobro de venta, incluido QR, tarjeta o transferencia, queda vinculado al turno. La clasificación de efectivo/no efectivo se guarda como una copia histórica para que cambios futuros del catálogo de medios de pago no alteren cierres anteriores.
- El cierre guarda efectivo contado, efectivo esperado, cobros, ingresos, egresos, retiros, reembolsos, diferencia, usuario y fecha; no se puede editar desde la interfaz ni desde Django Admin.
- Ventas requieren un turno abierto; las devoluciones y anulaciones seleccionan un turno abierto para registrar sus reembolsos. Esto mantiene trazabilidad, pero no permite reembolsar desde una caja cerrada; se debe abrir el turno operativo correspondiente.
- El efectivo esperado se calcula como saldo inicial + cobros de ventas en efectivo + ingresos adicionales − egresos − retiros − reembolsos en efectivo. Los cobros electrónicos se informan aparte y no afectan el efectivo físico.

Asigna permisos Django `cash.view_cashsession`, `cash.open_cashsession`, `cash.close_cashsession`, `cash.add_cashmovement` y `cash.view_cashmovement` mediante grupos. Las pruebas locales se ejecutan sobre SQLite; las garantías de bloqueo y carrera entre aperturas deben confirmarse además en PostgreSQL.
