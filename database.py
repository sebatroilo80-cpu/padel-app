import os
import sqlite3


def init_db():
    db_path = os.environ.get("DB_PATH", "padel.db")

    conn = sqlite3.connect(db_path)
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
        metodo_pago TEXT NOT NULL DEFAULT '',
        estado_pago TEXT NOT NULL DEFAULT 'Reserva',
        pagado REAL NOT NULL DEFAULT 0,
        whatsapp_enviado INTEGER DEFAULT 0,
        descuento REAL DEFAULT 0,
        motivo_descuento TEXT DEFAULT '',
        precio_final REAL DEFAULT 0,
        es_fijo INTEGER DEFAULT 0,
        grupo_fijo TEXT DEFAULT ''
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS configuracion (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        precio_60_dia REAL NOT NULL,
        precio_60_noche REAL NOT NULL,
        precio_90_dia REAL NOT NULL,
        precio_90_noche REAL NOT NULL,
        precio_120_dia REAL NOT NULL DEFAULT 45000,
        precio_120_noche REAL NOT NULL DEFAULT 55000
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movimientos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        tipo TEXT,
        descripcion TEXT,
        monto REAL,
        metodo_pago TEXT
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
            precio_60_dia,
            precio_60_noche,
            precio_90_dia,
            precio_90_noche,
            precio_120_dia,
            precio_120_noche
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """, (25000, 30000, 35000, 40000, 45000, 55000))

    conn.commit()
    conn.close()