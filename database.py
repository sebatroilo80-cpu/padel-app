import sqlite3

def init_db():
    conn = sqlite3.connect("padel.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reservas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        telefono TEXT,
        fecha TEXT NOT NULL,
        cancha TEXT NOT NULL,
        duracion TEXT NOT NULL,
        horario TEXT NOT NULL,
        precio REAL NOT NULL,
        metodo_pago TEXT NOT NULL,
        estado_pago TEXT NOT NULL DEFAULT 'Reserva',
        pagado REAL NOT NULL DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS configuracion (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        precio_60_dia REAL NOT NULL,
        precio_60_noche REAL NOT NULL,
        precio_90_dia REAL NOT NULL,
        precio_90_noche REAL NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS egresos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        descripcion TEXT NOT NULL,
        monto REAL NOT NULL
    )
    """)

    cursor.execute("SELECT COUNT(*) FROM configuracion")
    cantidad = cursor.fetchone()[0]

    if cantidad == 0:
        cursor.execute("""
        INSERT INTO configuracion (
            precio_60_dia, precio_60_noche, precio_90_dia, precio_90_noche
        ) VALUES (?, ?, ?, ?)
        """, (25000, 30000, 35000, 40000))

    conn.commit()
    conn.close()