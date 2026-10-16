# Sportswear

Sistema web modular para la gestión de una tienda de ropa deportiva. La implementación sigue el plan incremental del archivo `prompt`; las **Fases 1 y 2** están implementadas.

## Arquitectura inicial

- `config`: configuración Django, URLs y puntos de entrada WSGI/ASGI.
- `apps/core`: dashboard, selección segura de sucursal y elementos comunes.
- `apps/users`: usuario personalizado, roles iniciales y asignación de sucursales autorizadas.
- `apps/branches`: modelo, permisos y gestión de sucursales.
- `apps/products`: categorías, marcas, tallas, colores, productos y variantes.
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
- Las ventas, inventario y caja se implementarán con servicios de negocio y transacciones en sus fases correspondientes.

## Fase 2: sucursales y catálogo de productos

La segunda fase agrega:

- Gestión de sucursales con estado activo/inactivo, código, dirección, contacto y responsable.
- Catálogos administrables de categorías jerárquicas, marcas, tallas y colores.
- Productos con código interno, categoría, marca, costos y precios `DecimalField`, promoción opcional, impuesto e imagen.
- Variantes vendibles con SKU, código de barras, talla/color y precios/costos opcionales que heredan los valores base del producto.
- Búsqueda por nombre, código interno, SKU y código de barras; filtros por categoría, marca y estado; paginación.
- Administración web y Django Admin. Los registros históricos se desactivan en vez de borrarse.

### Permisos

Las páginas web exigen permisos Django del modelo (por ejemplo `branches.view_branch`, `products.add_product` y `products.change_productvariant`); el superusuario tiene acceso implícito. Asigna permisos directamente o mediante grupos desde `/admin/auth/group/`. Los roles del perfil no conceden automáticamente acceso a la administración, evitando elevar permisos solo por una etiqueta de rol. El selector de sucursal continúa limitando las opciones a sucursales activas autorizadas (o todas para Administradores/superusuarios).

### Validación adicional

Después de los cambios de modelos, generar y aplicar migraciones. Ejecutar `python manage.py check` y `python manage.py test`. Las imágenes cargadas localmente se sirven desde `media/` en modo DEBUG; producción debe configurar el almacenamiento de media apropiado. Esta fase **no** implementa existencias, movimientos, importaciones CSV/Excel ni ventas; esos datos no se simulan y se desarrollarán en las siguientes fases.
