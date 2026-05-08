from flask import Flask, render_template, request, redirect, session, jsonify
from database import init_db
import sqlite3
from datetime import datetime
from datetime import date, timedelta
import os
import uuid
import requests
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.environ.get("DB_PATH", "padel.db")
init_db()

TELEGRAM_TOKEN = "8789475526:AAGutuZ0izEkKi8kcSTHW8_JFjHBOPs6pms"
TELEGRAM_CHAT_ID = "7828571382"

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje
    }
    try:
        requests.post(url, data=data, timeout=10)
    except:
        pass

MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN") or ""
print("TOKEN MP:", MP_ACCESS_TOKEN[:12])
BASE_URL = (os.getenv("BASE_URL") or "http://127.0.0.1:5000").rstrip("/")

app = Flask(__name__)
app.secret_key = "clave_secreta_37"

# ===== FIX BASE DE DATOS =====
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Reservas
cursor.execute("PRAGMA table_info(reservas)")
columnas_reservas = [col[1] for col in cursor.fetchall()]

if "whatsapp_enviado" not in columnas_reservas:
   

if "descuento" not in columnas_reservas:
    cursor.execute("ALTER TABLE reservas ADD COLUMN descuento REAL DEFAULT 0")

if "motivo_descuento" not in columnas_reservas:
    cursor.execute("ALTER TABLE reservas ADD COLUMN motivo_descuento TEXT DEFAULT ''")

if "precio_final" not in columnas_reservas:
    cursor.execute("ALTER TABLE reservas ADD COLUMN precio_final REAL DEFAULT 0")
if "es_fijo" not in columnas_reservas:
    cursor.execute("ALTER TABLE reservas ADD COLUMN es_fijo INTEGER DEFAULT 0")

if "grupo_fijo" not in columnas_reservas:
    cursor.execute("ALTER TABLE reservas ADD COLUMN grupo_fijo TEXT DEFAULT ''")

cursor.execute("""
    UPDATE reservas
    SET precio_final = precio
    WHERE precio_final IS NULL OR precio_final = 0
""")

# Configuración
cursor.execute("PRAGMA table_info(configuracion)")
columnas_config = [col[1] for col in cursor.fetchall()]

if "precio_120_dia" not in columnas_config:
    cursor.execute("ALTER TABLE configuracion ADD COLUMN precio_120_dia REAL DEFAULT 45000")

if "precio_120_noche" not in columnas_config:
    cursor.execute("ALTER TABLE configuracion ADD COLUMN precio_120_noche REAL DEFAULT 55000")

conn.commit()
conn.close()

init_db()


def obtener_config(cursor):
    cursor.execute("""
        SELECT precio_60_dia, precio_60_noche,
               precio_90_dia, precio_90_noche,
               COALESCE(precio_120_dia, 45000),
               COALESCE(precio_120_noche, 55000)
        FROM configuracion
        WHERE id = 1
    """)
    return cursor.fetchone()


def generar_horarios():
    horarios = []
    for h in range(8, 24):
        horarios.append(f"{h:02d}:00")
        horarios.append(f"{h:02d}:30")
    return horarios


def calcular_precio(cursor, duracion, horario):
    config = obtener_config(cursor)
    if not config:
        return 0

    precio_60_dia = config[0]
    precio_60_noche = config[1]
    precio_90_dia = config[2]
    precio_90_noche = config[3]
    precio_120_dia = config[4] if len(config) > 4 else 45000
    precio_120_noche = config[5] if len(config) > 5 else 55000

    hora, minuto = map(int, horario.split(":"))

    # calcular hora de fin
    duracion_min = 60 if duracion == "60 minutos" else 90 if duracion == "90 minutos" else 120
    fin = hora * 60 + minuto + duracion_min

    # si en algún momento pasa de las 17:00 (1020 minutos)
    if fin > (17 * 60):
        es_noche = True
    else:
        es_noche = False

    if duracion == "60 minutos":
        return precio_60_noche if es_noche else precio_60_dia
    elif duracion == "90 minutos":
        return precio_90_noche if es_noche else precio_90_dia
    elif duracion == "120 minutos":
        return precio_120_noche if es_noche else precio_120_dia

    return 0

def crear_preferencia_mp(data):
    import os

    base_url = os.getenv("BASE_URL")
    url = "https://api.mercadopago.com/checkout/preferences"

    payload = {
        "items": [{
            "title": f"Reserva {data['cancha']} {data['fecha']} {data['horario']}",
            "quantity": 1,
            "currency_id": "ARS",
            "unit_price": float(data["monto"])
        }],
        "external_reference": data["id"],
        "back_urls": {
            "success": f"{base_url}/mp/success",
            "failure": f"{base_url}/mp/failure",
            "pending": f"{base_url}/mp/pending"
        },
        "notification_url": f"{base_url}/webhook/mercadopago",
        "auto_return": "approved"
    }

    headers = {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    r = requests.post(url, json=payload, headers=headers)

    if r.status_code not in (200, 201):
        print("ERROR MP:", r.text)
        return None

    data_resp = r.json()
    print("INIT POINT:", data_resp.get("init_point") or data_resp.get("sandbox_init_point"))
    return data_resp.get("init_point") or data_resp.get("sandbox_init_point")

def slots_reserva(horario, duracion):
    horarios = generar_horarios()
    slots = []

    if horario in horarios:
        idx = horarios.index(horario)

        if duracion == "60 minutos":
            slots = horarios[idx:idx + 2]
        elif duracion == "90 minutos":
            slots = horarios[idx:idx + 3]
        elif duracion == "120 minutos":
            slots = horarios[idx:idx + 4]
    return slots


def hay_conflicto(cursor, fecha, cancha, horario, duracion, excluir_id=None):
    nuevos_slots = slots_reserva(horario, duracion)

    if excluir_id is None:
        cursor.execute("""
            SELECT id, horario, duracion
            FROM reservas
            WHERE fecha = ? AND cancha = ?
        """, (fecha, cancha))
    else:
        cursor.execute("""
            SELECT id, horario, duracion
            FROM reservas
            WHERE fecha = ? AND cancha = ? AND id != ?
        """, (fecha, cancha, excluir_id))

    reservas_existentes = cursor.fetchall()

    for reserva in reservas_existentes:
        _, horario_existente, duracion_existente = reserva
        slots_existentes = slots_reserva(horario_existente, duracion_existente)

        if set(nuevos_slots) & set(slots_existentes):
            return True

    return False


def limpiar_numero(valor):
    try:
        n = float(valor)
    except (TypeError, ValueError):
        n = 0.0
    return max(0.0, n)


def calcular_pagado_inicial(precio, opcion_pago):
    if opcion_pago == "Reserva":
        return round(precio * 0.30, 2)
    return precio


def calcular_estado_desde_pagado(precio, pagado):
    return "Pagado" if pagado >= precio else "Reserva"


def obtener_total_por_metodo(cursor, fecha):
    cursor.execute("""
        SELECT COALESCE(SUM(pagado), 0)
        FROM reservas
        WHERE fecha = ? AND metodo_pago = 'Efectivo'
    """, (fecha,))
    efectivo = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(pagado), 0)
        FROM reservas
        WHERE fecha = ? AND metodo_pago = 'Transferencia'
    """, (fecha,))
    transferencia = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(pagado), 0)
        FROM reservas
        WHERE fecha = ? AND metodo_pago = 'Mercado Pago'
    """, (fecha,))
    mercado_pago = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(pagado), 0)
        FROM reservas
        WHERE fecha = ? AND metodo_pago = 'QR'
    """, (fecha,))
    qr = cursor.fetchone()[0]

    return efectivo, transferencia, mercado_pago, qr


def generar_dias_cliente(cantidad=7):
    nombres_dia = ["LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM"]
    nombres_mes = [
        "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
        "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"
    ]

    dias = []
    hoy = date.today()

    for i in range(cantidad):
        d = hoy + timedelta(days=i)

        if i == 0:
            titulo = "HOY"
        elif i == 1:
            titulo = "MAÑ"
        else:
            titulo = nombres_dia[d.weekday()]

        dias.append({
            "iso": d.strftime("%Y-%m-%d"),
            "titulo": titulo,
            "numero": d.strftime("%d"),
            "mes": nombres_mes[d.month - 1]
        })

    return dias


@app.route("/")
def home():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    config = obtener_config(cursor)
    if not config:
        config = [0, 0, 0, 0]

    cursor.execute("""
        SELECT fecha, cancha, horario, duracion
        FROM reservas
    """)
    reservas = cursor.fetchall()

    conn.close()

    reservas_ocupadas = []

    for fecha, cancha, horario, duracion in reservas:
        slots = slots_reserva(horario, duracion)
        for slot in slots:
            reservas_ocupadas.append({
                "fecha": fecha,
                "cancha": cancha,
                "horario": slot
            })

    dias_cliente = generar_dias_cliente(7)

    return render_template(
        "index.html",
        config=config,
        reservas_ocupadas=reservas_ocupadas,
        dias_cliente=dias_cliente
    )


@app.route("/reservar", methods=["POST"])
def reservar():
    nombre = request.form.get("nombre", "").strip()
    telefono = request.form.get("telefono", "").strip()
    fecha = request.form.get("fecha", "").strip()
    cancha = request.form.get("cancha", "").strip()
    duracion = request.form.get("duracion", "").strip()
    horario = request.form.get("horario", "").strip()
    metodo_pago = request.form.get("metodo_pago", "").strip()
    opcion_pago = request.form.get("opcion_pago", "").strip()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

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
    conn.commit()

    if hay_conflicto(cursor, fecha, cancha, horario, duracion):
        conn.close()
        return "Ese horario ya está ocupado o bloqueado. Volvé atrás y elegí otro."

    precio = calcular_precio(cursor, duracion, horario)

    if metodo_pago == "Transferencia":
        pagado = 0
        if opcion_pago == "Reserva":
            estado_pago = "Pendiente reserva"
        else:
            estado_pago = "Pendiente pago total"
    else:
        pagado = calcular_pagado_inicial(precio, opcion_pago)
        estado_pago = calcular_estado_desde_pagado(precio, pagado)

    cursor.execute("""
        INSERT INTO reservas (
            nombre, telefono, fecha, cancha, duracion, horario,
            precio, metodo_pago, estado_pago, pagado
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        nombre,
        telefono,
        fecha,
        cancha,
        duracion,
        horario,
        precio,
        metodo_pago,
        estado_pago,
        pagado
    ))

    reserva_id = cursor.lastrowid

    # Si el pago entra en el momento, registrar ingreso en caja
    if metodo_pago not in ["Mercado Pago", "Transferencia"] and float(pagado) > 0:
        if opcion_pago == "Reserva":
            descripcion = f"Seña reserva #{reserva_id} - {nombre} - {cancha} - {horario}"
        else:
            descripcion = f"Pago total reserva #{reserva_id} - {nombre} - {cancha} - {horario}"

        fecha_mov = date.today().strftime("%Y-%m-%d")

        cursor.execute("""
            INSERT INTO movimientos (fecha, tipo, descripcion, monto, metodo_pago)
            VALUES (?, ?, ?, ?, ?)
        """, (
            fecha_mov,
            "ingreso",
            descripcion,
            pagado,
            metodo_pago
        ))

    # Aviso a Telegram SIEMPRE que entra una reserva
    mensaje = f"""📲 Nueva reserva

👤 {nombre}
📞 {telefono}
📅 {fecha}
⏰ {horario}
🏟️ {cancha}
⏱️ {duracion}
💰 ${precio}
💳 {metodo_pago}
📌 {estado_pago}"""

    enviar_telegram(mensaje)

    # Si paga con Mercado Pago, generar link
    if metodo_pago == "Mercado Pago":
        external_id = str(reserva_id)

        url_pago = crear_preferencia_mp({
            "id": external_id,
            "nombre": nombre,
            "telefono": telefono,
            "fecha": fecha,
            "cancha": cancha,
            "duracion": duracion,
            "horario": horario,
            "monto": pagado
        })

        conn.commit()
        conn.close()

        if not url_pago:
            return "No se pudo generar el link de Mercado Pago."

        return redirect(url_pago)

    conn.commit()
    conn.close()
    return redirect("/?ok=1")

@app.route("/webhook/mercadopago", methods=["POST"])
def webhook_mercadopago():
    try:
        data = request.get_json(silent=True) or {}
        print("WEBHOOK MP:", data)

        # Solo procesar pagos
        if data.get("type") != "payment":
            return jsonify({"ok": True}), 200

        payment_id = data.get("data", {}).get("id")
        if not payment_id:
            return jsonify({"ok": True}), 200

        access_token = os.getenv("MP_ACCESS_TOKEN")
        if not access_token:
            print("❌ Falta MP_ACCESS_TOKEN")
            return jsonify({"ok": False}), 200

        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        resp = requests.get(
            f"https://api.mercadopago.com/v1/payments/{payment_id}",
            headers=headers
        )

        pago = resp.json()
        print("DETALLE PAGO:", pago)

        # Solo si está aprobado
        if pago.get("status") != "approved":
            return jsonify({"ok": True}), 200

        ref = pago.get("external_reference")
        if not ref:
            print("❌ No hay external_reference")
            return jsonify({"ok": True}), 200

        monto_pagado = float(pago.get("transaction_amount") or 0)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Crear tabla movimientos si no existe
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
        conn.commit()

        # Buscar la reserva ya creada en /reservar
        cursor.execute("""
            SELECT id, nombre, telefono, fecha, cancha, duracion, horario, precio, metodo_pago, estado_pago, pagado
            FROM reservas
            WHERE id = ?
        """, (ref,))
        reserva = cursor.fetchone()

        if not reserva:
            print(f"❌ No se encontró la reserva #{ref}")
            conn.close()
            return jsonify({"ok": True}), 200

        reserva_id = reserva[0]
        nombre = reserva[1]
        fecha = reserva[3]
        cancha = reserva[4]
        horario = reserva[6]
        precio_total = float(reserva[7] or 0)

        # Definir nuevo estado según cuánto pagó realmente
        nuevo_pagado = monto_pagado
        nuevo_estado = "Pagado" if nuevo_pagado >= precio_total else "Reserva"

        # Actualizar reserva con estado real de MP
        cursor.execute("""
            UPDATE reservas
            SET metodo_pago = ?, estado_pago = ?, pagado = ?
            WHERE id = ?
        """, (
            "Mercado Pago",
            nuevo_estado,
            nuevo_pagado,
            reserva_id
        ))

        # Evitar duplicar caja si Mercado Pago manda el webhook más de una vez
        descripcion_mov = f"Pago Mercado Pago reserva #{reserva_id} - {nombre} - {cancha} - {horario}"

        cursor.execute("""
            SELECT COUNT(*)
            FROM movimientos
            WHERE tipo = 'ingreso' AND descripcion = ?
        """, (descripcion_mov,))
        ya_existe = cursor.fetchone()[0]

        if ya_existe == 0 and nuevo_pagado > 0:
            cursor.execute("""
                INSERT INTO movimientos (fecha, tipo, descripcion, monto, metodo_pago)
                VALUES (?, ?, ?, ?, ?)
            """, (
                fecha,
                "ingreso",
                descripcion_mov,
                nuevo_pagado,
                "Mercado Pago"
            ))

        conn.commit()
        conn.close()

        print("✅ Reserva actualizada y caja impactada automáticamente")
        return jsonify({"ok": True}), 200

    except Exception as e:
        print("❌ ERROR WEBHOOK:", e)
        return jsonify({"ok": False}), 200

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        usuario = request.form["usuario"]
        clave = request.form["clave"]

        if usuario == "admin" and clave == "1234":
            session["admin"] = True
            return redirect("/admin")
        else:
            error = "Usuario o contraseña incorrectos"

    return render_template("login.html", error=error)


@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect("/login")

    active_tab = request.args.get("tab", "grilla")
    fecha_admin = request.args.get("fecha")

    if not fecha_admin:
        fecha_admin = date.today().strftime("%Y-%m-%d")

    reserva_fecha = request.args.get("reserva_fecha", "").strip()
    busqueda = request.args.get("busqueda", "").strip()
    solo_pendientes = request.args.get("solo_pendientes", "0")

    mes_actual = fecha_admin[:7]
    anio_actual = fecha_admin[:4]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # =========================
    # TABLAS EXISTENTES
    # =========================
    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
    """)
    tablas = [t[0] for t in cursor.fetchall()]

    tiene_movimientos = "movimientos" in tablas
    tiene_egresos = "egresos" in tablas
    tiene_reservas = "reservas" in tablas
    tiene_config = "configuracion" in tablas

    # =========================
    # CONFIGURACION
    # =========================
    config = [25000, 30000, 35000, 40000, 45000, 55000]

    if tiene_config:
        cursor.execute("""
            SELECT precio_60_dia, precio_60_noche,
                   precio_90_dia, precio_90_noche,
                   precio_120_dia, precio_120_noche
            FROM configuracion
            ORDER BY id DESC
            LIMIT 1
        """)
        fila_config = cursor.fetchone()
        if fila_config:
            config = list(fila_config)

    # =========================
    # GRILLA
    # =========================
    canchas = ["Cancha 1", "Cancha 2", "Cancha 3"]

    horarios = [
        "08:00","08:30","09:00","09:30",
        "10:00","10:30","11:00","11:30",
        "12:00","12:30","13:00","13:30",
        "14:00","14:30","15:00","15:30",
        "16:00","16:30","17:00","17:30",
        "18:00","18:30","19:00","19:30",
        "20:00","20:30","21:00","21:30",
        "22:00","22:30","23:00","23:30"
    ]

    grilla = {}
    for h in horarios:
        grilla[h] = {}
        for cancha in canchas:
            grilla[h][cancha] = {
                "estado": "libre",
                "reserva": None
            }

    reservas_dia = []
    reservas = []

    if tiene_reservas:
        cursor.execute("""
            SELECT id, nombre, telefono, fecha, cancha, duracion, horario, precio, metodo_pago, estado_pago, pagado, whatsapp_enviado
            FROM reservas
            WHERE fecha = ?
            ORDER BY horario ASC
        """, (fecha_admin,))
        reservas_dia = cursor.fetchall()

        def bloques_reserva(hora_inicio, duracion):
            if hora_inicio not in horarios:
                return []

            idx = horarios.index(hora_inicio)

            if duracion == "60 minutos":
                return horarios[idx:idx+2]
            elif duracion == "90 minutos":
                return horarios[idx:idx+3]
            elif duracion == "120 minutos":
                return horarios[idx:idx+4]
            else:
                return [hora_inicio]

        for r in reservas_dia:
            cancha = r[4]
            duracion = r[5]
            hora_inicio = r[6]

            bloques = bloques_reserva(hora_inicio, duracion)

            for i, h in enumerate(bloques):
                if h in grilla and cancha in grilla[h]:
                    grilla[h][cancha]["estado"] = "inicio" if i == 0 else "continuacion"
                    grilla[h][cancha]["reserva"] = r

        # =========================
        # LISTADO RESERVAS CON FILTROS
        # =========================
        query_reservas = """
            SELECT id, nombre, telefono, fecha, cancha, duracion, horario, precio, metodo_pago, estado_pago, pagado, whatsapp_enviado
            FROM reservas
            WHERE 1=1
        """
        params_reservas = []

        if reserva_fecha:
            query_reservas += " AND fecha = ?"
            params_reservas.append(reserva_fecha)

        if busqueda:
            like = f"%{busqueda.lower()}%"
            query_reservas += " AND (LOWER(nombre) LIKE ? OR LOWER(COALESCE(telefono, '')) LIKE ?)"
            params_reservas.extend([like, like])

        if solo_pendientes == "1":
            query_reservas += " AND estado_pago IN ('Pendiente reserva', 'Pendiente pago total')"

        query_reservas += " ORDER BY fecha DESC, horario ASC"

        cursor.execute(query_reservas, params_reservas)
        reservas = cursor.fetchall()

    # =========================
    # EGRESOS / MOVIMIENTOS UNIFICADOS
    # =========================
    egresos = []

    if tiene_movimientos:
        cursor.execute("PRAGMA table_info(movimientos)")
        columnas_mov = [col[1] for col in cursor.fetchall()]
        campo_texto = "descripcion" if "descripcion" in columnas_mov else "concepto"

        cursor.execute(f"""
            SELECT id, fecha, tipo, {campo_texto} as descripcion, monto, metodo_pago
            FROM movimientos
            WHERE tipo = 'egreso'
        """)
        egresos.extend(cursor.fetchall())

    if tiene_egresos:
        cursor.execute("""
            SELECT id, fecha, 'egreso' as tipo, descripcion, monto, 'Egreso' as metodo_pago
            FROM egresos
        """)
        egresos.extend(cursor.fetchall())

    egresos.sort(key=lambda x: (x[1], x[0]), reverse=True)

    # =========================
    # CAJA DIARIA
    # =========================
    ingresos_hoy = 0.0
    egresos_hoy = 0.0
    descuentos_hoy = 0.0

    efectivo_hoy = 0.0
    transferencia_hoy = 0.0
    mercado_pago_hoy = 0.0
    qr_hoy = 0.0

    if tiene_movimientos:
        # Ingresos del día
        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'ingreso' AND fecha = ?
        """, (fecha_admin,))
        ingresos_hoy = float(cursor.fetchone()[0] or 0)

        # Egresos reales del día (sin descuentos)
        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'egreso'
              AND fecha = ?
              AND descripcion NOT LIKE 'Descuento reserva #%'
              AND descripcion NOT LIKE 'Ajuste descuento reserva #%'
              AND descripcion NOT LIKE 'Ajuste negativo reserva #%'
        """, (fecha_admin,))
        egresos_hoy = float(cursor.fetchone()[0] or 0)

        # Descuentos del día
        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'egreso'
              AND fecha = ?
              AND (
                    descripcion LIKE 'Descuento reserva #%'
                 OR descripcion LIKE 'Ajuste descuento reserva #%'
                 OR descripcion LIKE 'Ajuste negativo reserva #%'
              )
        """, (fecha_admin,))
        descuentos_hoy = float(cursor.fetchone()[0] or 0)

        # Medios de pago (solo ingresos)
        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'ingreso' AND fecha = ? AND metodo_pago = 'Efectivo'
        """, (fecha_admin,))
        efectivo_hoy = float(cursor.fetchone()[0] or 0)

        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'ingreso' AND fecha = ? AND metodo_pago = 'Transferencia'
        """, (fecha_admin,))
        transferencia_hoy = float(cursor.fetchone()[0] or 0)

        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'ingreso' AND fecha = ? AND metodo_pago = 'Mercado Pago'
        """, (fecha_admin,))
        mercado_pago_hoy = float(cursor.fetchone()[0] or 0)

        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'ingreso' AND fecha = ? AND metodo_pago = 'QR'
        """, (fecha_admin,))
        qr_hoy = float(cursor.fetchone()[0] or 0)

    if tiene_egresos:
        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM egresos
            WHERE fecha = ?
        """, (fecha_admin,))
        egresos_hoy += float(cursor.fetchone()[0] or 0)

    total_hoy = ingresos_hoy - egresos_hoy - descuentos_hoy

    # =========================
    # CAJA MENSUAL
    # =========================
    ingresos_mes = 0.0
    egresos_mes = 0.0
    descuentos_mes = 0.0

    if tiene_movimientos:
        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'ingreso' AND substr(fecha, 1, 7) = ?
        """, (mes_actual,))
        ingresos_mes = float(cursor.fetchone()[0] or 0)

        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'egreso'
              AND substr(fecha, 1, 7) = ?
              AND descripcion NOT LIKE 'Descuento reserva #%'
              AND descripcion NOT LIKE 'Ajuste descuento reserva #%'
              AND descripcion NOT LIKE 'Ajuste negativo reserva #%'
        """, (mes_actual,))
        egresos_mes = float(cursor.fetchone()[0] or 0)

        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'egreso'
              AND substr(fecha, 1, 7) = ?
              AND (
                    descripcion LIKE 'Descuento reserva #%'
                 OR descripcion LIKE 'Ajuste descuento reserva #%'
                 OR descripcion LIKE 'Ajuste negativo reserva #%'
              )
        """, (mes_actual,))
        descuentos_mes = float(cursor.fetchone()[0] or 0)

    if tiene_egresos:
        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM egresos
            WHERE substr(fecha, 1, 7) = ?
        """, (mes_actual,))
        egresos_mes += float(cursor.fetchone()[0] or 0)

    total_mes = ingresos_mes - egresos_mes - descuentos_mes

    # =========================
    # CAJA ANUAL
    # =========================
    ingresos_anio = 0.0
    egresos_anio = 0.0
    descuentos_anio = 0.0

    if tiene_movimientos:
        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'ingreso' AND substr(fecha, 1, 4) = ?
        """, (anio_actual,))
        ingresos_anio = float(cursor.fetchone()[0] or 0)

        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'egreso'
              AND substr(fecha, 1, 4) = ?
              AND descripcion NOT LIKE 'Descuento reserva #%'
              AND descripcion NOT LIKE 'Ajuste descuento reserva #%'
              AND descripcion NOT LIKE 'Ajuste negativo reserva #%'
        """, (anio_actual,))
        egresos_anio = float(cursor.fetchone()[0] or 0)

        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'egreso'
              AND substr(fecha, 1, 4) = ?
              AND (
                    descripcion LIKE 'Descuento reserva #%'
                 OR descripcion LIKE 'Ajuste descuento reserva #%'
                 OR descripcion LIKE 'Ajuste negativo reserva #%'
              )
        """, (anio_actual,))
        descuentos_anio = float(cursor.fetchone()[0] or 0)

    if tiene_egresos:
        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM egresos
            WHERE substr(fecha, 1, 4) = ?
        """, (anio_actual,))
        egresos_anio += float(cursor.fetchone()[0] or 0)

    total_anio = ingresos_anio - egresos_anio - descuentos_anio

    # =========================
    # REPORTE MENSUAL
    # =========================
    nombres_meses = {
        "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
        "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
        "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre"
    }

    reporte_mensual = []

    for mes in range(1, 13):
        mes_num = str(mes).zfill(2)
        clave_mes = f"{anio_actual}-{mes_num}"

        ingresos = 0.0
        egresos_mes_item = 0.0
        descuentos_mes_item = 0.0

        if tiene_movimientos:
            cursor.execute("""
                SELECT COALESCE(SUM(monto), 0)
                FROM movimientos
                WHERE tipo = 'ingreso' AND substr(fecha, 1, 7) = ?
            """, (clave_mes,))
            ingresos = float(cursor.fetchone()[0] or 0)

            cursor.execute("""
                SELECT COALESCE(SUM(monto), 0)
                FROM movimientos
                WHERE tipo = 'egreso'
                  AND substr(fecha, 1, 7) = ?
                  AND descripcion NOT LIKE 'Descuento reserva #%'
                  AND descripcion NOT LIKE 'Ajuste descuento reserva #%'
                  AND descripcion NOT LIKE 'Ajuste negativo reserva #%'
            """, (clave_mes,))
            egresos_mes_item = float(cursor.fetchone()[0] or 0)

            cursor.execute("""
                SELECT COALESCE(SUM(monto), 0)
                FROM movimientos
                WHERE tipo = 'egreso'
                  AND substr(fecha, 1, 7) = ?
                  AND (
                        descripcion LIKE 'Descuento reserva #%'
                     OR descripcion LIKE 'Ajuste descuento reserva #%'
                     OR descripcion LIKE 'Ajuste negativo reserva #%'
                  )
            """, (clave_mes,))
            descuentos_mes_item = float(cursor.fetchone()[0] or 0)

        if tiene_egresos:
            cursor.execute("""
                SELECT COALESCE(SUM(monto), 0)
                FROM egresos
                WHERE substr(fecha, 1, 7) = ?
            """, (clave_mes,))
            egresos_mes_item += float(cursor.fetchone()[0] or 0)

        saldo = ingresos - egresos_mes_item - descuentos_mes_item

        reporte_mensual.append({
            "mes": nombres_meses[mes_num],
            "ingresos": ingresos,
            "egresos": egresos_mes_item,
            "descuentos": descuentos_mes_item,
            "saldo": saldo
        })

    # =========================
    # ESTADISTICAS RESERVAS
    # =========================
    horarios_mas_usados = []
    max_horarios = 0
    dias_mas_usados = []
    max_dias = 0
    total_reservas = 0

    if tiene_reservas:
        cursor.execute("""
            SELECT horario, COUNT(*) as cantidad
            FROM reservas
            GROUP BY horario
            ORDER BY cantidad DESC, horario ASC
        """)
        horarios_raw = cursor.fetchall()

        for fila in horarios_raw:
            cantidad = int(fila[1])
            horarios_mas_usados.append({
                "label": fila[0],
                "cantidad": cantidad
            })
            if cantidad > max_horarios:
                max_horarios = cantidad

        cursor.execute("""
            SELECT strftime('%w', fecha) as dia_num, COUNT(*) as cantidad
            FROM reservas
            GROUP BY dia_num
            ORDER BY cantidad DESC
        """)
        dias_raw = cursor.fetchall()

        nombres_dias = {
            "0": "Domingo",
            "1": "Lunes",
            "2": "Martes",
            "3": "Miércoles",
            "4": "Jueves",
            "5": "Viernes",
            "6": "Sábado"
        }

        for fila in dias_raw:
            dia_num = str(fila[0])
            cantidad = int(fila[1])
            dias_mas_usados.append({
                "label": nombres_dias.get(dia_num, dia_num),
                "cantidad": cantidad
            })
            if cantidad > max_dias:
                max_dias = cantidad

        cursor.execute("SELECT COUNT(*) FROM reservas")
        total_reservas = int(cursor.fetchone()[0] or 0)

    # =========================
    # INGRESOS POR DIA DEL MES
    # =========================
    ingresos_por_dia = []
    max_ingreso_dia = 0

    if tiene_movimientos:
        cursor.execute("""
            SELECT fecha, COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'ingreso' AND substr(fecha, 1, 7) = ?
            GROUP BY fecha
            ORDER BY fecha ASC
        """, (mes_actual,))
        ingresos_raw = cursor.fetchall()

        for fecha_item, monto_item in ingresos_raw:
            monto_item = float(monto_item or 0)
            ingresos_por_dia.append({
                "label": fecha_item[-2:],
                "monto": monto_item
            })
            if monto_item > max_ingreso_dia:
                max_ingreso_dia = monto_item

    conn.close()

    return render_template(
        "admin_v2.html",
        active_tab=active_tab,
        fecha_admin=fecha_admin,
        reserva_fecha=reserva_fecha,
        busqueda=busqueda,
        solo_pendientes=solo_pendientes,
        canchas=canchas,
        horarios=horarios,
        grilla=grilla,
        reservas=reservas,
        egresos=egresos,
        config=config,
        ingresos_hoy=ingresos_hoy,
        egresos_hoy=egresos_hoy,
        descuentos_hoy=descuentos_hoy,
        efectivo_hoy=efectivo_hoy,
        transferencia_hoy=transferencia_hoy,
        mercado_pago_hoy=mercado_pago_hoy,
        qr_hoy=qr_hoy,
        total_hoy=total_hoy,
        total_mes=total_mes,
        total_anio=total_anio,
        descuentos_mes=descuentos_mes,
        descuentos_anio=descuentos_anio,
        reporte_mensual=reporte_mensual,
        horarios_mas_usados=horarios_mas_usados,
        dias_mas_usados=dias_mas_usados,
        max_horarios=max_horarios,
        max_dias=max_dias,
        total_reservas=total_reservas,
        ingresos_por_dia=ingresos_por_dia,
        max_ingreso_dia=max_ingreso_dia
    )    


@app.route("/configuracion", methods=["GET", "POST"])
def configuracion():
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if request.method == "POST":
        precio_60_dia = request.form["precio_60_dia"]
        precio_60_noche = request.form["precio_60_noche"]
        precio_90_dia = request.form["precio_90_dia"]
        precio_90_noche = request.form["precio_90_noche"]
        precio_120_dia = request.form["precio_120_dia"]
        precio_120_noche = request.form["precio_120_noche"]

        cursor.execute("""
            UPDATE configuracion
            SET precio_60_dia = ?, precio_60_noche = ?,
                precio_90_dia = ?, precio_90_noche = ?,
                precio_120_dia = ?, precio_120_noche = ?
            WHERE id = 1
        """, (
            precio_60_dia, precio_60_noche,
            precio_90_dia, precio_90_noche,
            precio_120_dia, precio_120_noche
        ))

        conn.commit()

    config = obtener_config(cursor)
    if not config:
        config = [25000, 30000, 35000, 40000, 45000, 55000]

    conn.close()

    return render_template("configuracion.html", config=config)


@app.route("/admin-reservar", methods=["POST"])
def admin_reservar():
    if not session.get("admin"):
        return redirect("/login")

    nombre = request.form.get("nombre", "").strip()
    telefono = request.form.get("telefono", "").strip()
    fecha = request.form.get("fecha", "").strip()
    cancha = request.form.get("cancha", "").strip()
    duracion = request.form.get("duracion", "").strip()
    horario = request.form.get("horario", "").strip()
    metodo_pago = request.form.get("metodo_pago", "").strip()
    opcion_pago = request.form.get("opcion_pago", "").strip()

    descuento = limpiar_numero(request.form.get("descuento"))
    motivo_descuento = request.form.get("motivo_descuento", "").strip()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if hay_conflicto(cursor, fecha, cancha, horario, duracion):
        conn.close()
        return "Ese horario ya está ocupado o bloqueado. Volvé atrás y elegí otro."

    precio_original = float(calcular_precio(cursor, duracion, horario) or 0)

    if descuento > precio_original:
        descuento = precio_original

    # Si es reserva, NO aplicamos descuento sobre la seña
    if opcion_pago == "Reserva":
        precio_final = precio_original
        pagado = round(precio_original * 0.30, 2)
        estado_pago = "Reserva"
        descuento_guardado = 0
        motivo_guardado = ""
    else:
        precio_final = max(0.0, precio_original - descuento)
        pagado = precio_final
        estado_pago = "Pagado"
        descuento_guardado = descuento
        motivo_guardado = motivo_descuento

    cursor.execute("""
        INSERT INTO reservas (
            nombre, telefono, fecha, cancha, duracion, horario,
            precio, metodo_pago, estado_pago, pagado,
            descuento, motivo_descuento, precio_final,
            es_fijo, grupo_fijo
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        nombre,
        telefono,
        fecha,
        cancha,
        duracion,
        horario,
        precio_original,
        metodo_pago,
        estado_pago,
        pagado,
        descuento_guardado,
        motivo_guardado,
        precio_final,
        0,
        ""
    ))

    reserva_id = cursor.lastrowid

    if pagado > 0:
        cursor.execute("""
            INSERT INTO movimientos (fecha, tipo, descripcion, monto, metodo_pago)
            VALUES (?, ?, ?, ?, ?)
        """, (
            fecha,
            "ingreso",
            f"Reserva #{reserva_id} - {nombre} - {cancha} - {horario}",
            pagado,
            metodo_pago
        ))

    conn.commit()
    conn.close()

    return redirect(f"/admin?fecha={fecha}")

@app.route("/crear-turno-fijo", methods=["POST"])
def crear_turno_fijo():
    if not session.get("admin"):
        return redirect("/login")

    nombre = request.form.get("nombre", "").strip()
    telefono = request.form.get("telefono", "").strip()
    cancha = request.form.get("cancha", "").strip()
    horario = request.form.get("horario", "").strip()
    duracion = request.form.get("duracion", "").strip()

    fecha_inicio = request.form.get("fecha_desde") or request.form.get("fecha_inicio")
    fecha_fin = request.form.get("fecha_hasta") or request.form.get("fecha_fin")
    dia_semana = int(request.form.get("dia_semana", 0))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    inicio = datetime.strptime(fecha_inicio, "%Y-%m-%d").date()
    fin = datetime.strptime(fecha_fin, "%Y-%m-%d").date()

    grupo_fijo = str(uuid.uuid4())
    fecha_actual = inicio

    while fecha_actual <= fin:
        if fecha_actual.weekday() == dia_semana:
            fecha_str = fecha_actual.strftime("%Y-%m-%d")

            if not hay_conflicto(cursor, fecha_str, cancha, horario, duracion):
                precio = float(calcular_precio(cursor, duracion, horario) or 0)

                cursor.execute("""
                    INSERT INTO reservas (
                        nombre, telefono, fecha, cancha, duracion, horario,
                        precio, metodo_pago, estado_pago, pagado,
                        descuento, motivo_descuento, precio_final,
                        es_fijo, grupo_fijo
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    nombre, telefono, fecha_str, cancha, duracion, horario,
                    precio, "", "Pendiente pago total", 0,
                    0, "", precio,
                    1, grupo_fijo
                ))

        fecha_actual += timedelta(days=1)

    conn.commit()
    conn.close()

    return redirect("/admin?tab=reservas")

@app.route("/editar/<int:id>", methods=["GET", "POST"])
def editar(id):
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if request.method == "POST":
        cursor.execute("""
            SELECT nombre, telefono, fecha, cancha, duracion, horario,
                   precio, metodo_pago, estado_pago, pagado,
                   descuento, motivo_descuento, precio_final
            FROM reservas
            WHERE id = ?
        """, (id,))
        reserva_actual = cursor.fetchone()

        if not reserva_actual:
            conn.close()
            return "Reserva no encontrada"

        nombre = request.form["nombre"]
        telefono = request.form["telefono"]
        fecha = request.form["fecha"]
        cancha = request.form["cancha"]
        duracion = request.form["duracion"]
        horario = request.form["horario"]
        metodo_pago = request.form["metodo_pago"]
        estado_pago_nuevo = request.form["estado_pago"]
        descuento = limpiar_numero(request.form.get("descuento"))
        motivo_descuento = request.form.get("motivo_descuento", "").strip()

        if hay_conflicto(cursor, fecha, cancha, horario, duracion, excluir_id=id):
            conn.close()
            return "Ese horario ya está ocupado o bloqueado."

        # Datos anteriores
        estado_anterior = (reserva_actual[8] or "").strip()
        pagado_anterior = float(reserva_actual[9] or 0)

        precio_original = float(calcular_precio(cursor, duracion, horario) or 0)

        # Limitar descuento
        if descuento > precio_original:
            descuento = precio_original

        # Nuevo total real del turno
        total_final = max(0.0, precio_original - descuento)

        sena_base = round(precio_original * 0.30, 2)

        # =========================
        # LOGICA DE PAGOS
        # =========================
        if estado_pago_nuevo == "Reserva":
            pagado = sena_base
            estado_pago = "Reserva"

        elif estado_pago_nuevo == "Pendiente reserva":
            pagado = 0.0
            estado_pago = "Pendiente reserva"

        elif estado_pago_nuevo == "Pendiente pago total":
            pagado = pagado_anterior
            estado_pago = "Pendiente pago total"

        elif estado_pago_nuevo == "Pagado":
            # Total pagado es el valor final del turno
            pagado = total_final
            estado_pago = "Pagado"

        else:
            pagado = pagado_anterior
            estado_pago = estado_pago_nuevo

        # =========================
        # GUARDAR RESERVA
        # =========================
        cursor.execute("""
            UPDATE reservas
            SET nombre = ?, telefono = ?, fecha = ?, cancha = ?, duracion = ?, horario = ?,
                metodo_pago = ?, estado_pago = ?, precio = ?, pagado = ?, descuento = ?,
                motivo_descuento = ?, precio_final = ?
            WHERE id = ?
        """, (
            nombre,
            telefono,
            fecha,
            cancha,
            duracion,
            horario,
            metodo_pago,
            estado_pago,
            precio_original,
            pagado,
            descuento,
            motivo_descuento,
            total_final,
            id
        ))

        # =========================
        # IMPACTO EN CAJA
        # =========================
        fecha_mov = date.today().strftime("%Y-%m-%d")

        # Diferencia real de dinero cobrado
        diferencia = round(pagado - pagado_anterior, 2)

        if diferencia > 0:
            cursor.execute("""
                INSERT INTO movimientos (fecha, tipo, descripcion, monto, metodo_pago)
                VALUES (?, 'ingreso', ?, ?, ?)
            """, (
                fecha_mov,
                f"Pago reserva #{id} - {nombre} - {cancha} - {horario}",
                diferencia,
                metodo_pago
            ))

        elif diferencia < 0:
            cursor.execute("""
                INSERT INTO movimientos (fecha, tipo, descripcion, monto, metodo_pago)
                VALUES (?, 'egreso', ?, ?, 'Ajuste')
            """, (
                fecha_mov,
                f"Ajuste negativo reserva #{id} - {nombre} - {cancha} - {horario}",
                abs(diferencia)
            ))

        conn.commit()
        conn.close()

        return redirect(f"/editar/{id}")

    cursor.execute("SELECT * FROM reservas WHERE id = ?", (id,))
    reserva = cursor.fetchone()
    conn.close()

    return render_template("editar.html", reserva=reserva)

@app.route("/whatsapp_enviado/<int:id>")
def whatsapp_enviado(id):
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE reservas
        SET whatsapp_enviado = 1
        WHERE id = ?
    """, (id,))

    conn.commit()
    conn.close()

    return redirect(f"/editar/{id}")

@app.route("/agregar-egreso", methods=["POST"])
def agregar_egreso():
    if not session.get("admin"):
        return redirect("/login")

    fecha = request.form.get("fecha")
    descripcion = request.form.get("descripcion")
    monto = limpiar_numero(request.form.get("monto"))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Ver tablas existentes
    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
    """)
    tablas = [t[0] for t in cursor.fetchall()]

    if "movimientos" in tablas:
        cursor.execute("PRAGMA table_info(movimientos)")
        columnas = [col[1] for col in cursor.fetchall()]
        campo_texto = "descripcion" if "descripcion" in columnas else "concepto"

        cursor.execute(f"""
            INSERT INTO movimientos (fecha, tipo, {campo_texto}, monto, metodo_pago)
            VALUES (?, ?, ?, ?, ?)
        """, (
            fecha,
            "egreso",
            descripcion,
            monto,
            "Egreso"
        ))

    elif "egresos" in tablas:
        cursor.execute("""
            INSERT INTO egresos (fecha, descripcion, monto)
            VALUES (?, ?, ?)
        """, (fecha, descripcion, monto))

    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS egresos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                descripcion TEXT NOT NULL,
                monto REAL NOT NULL
            )
        """)
        cursor.execute("""
            INSERT INTO egresos (fecha, descripcion, monto)
            VALUES (?, ?, ?)
        """, (fecha, descripcion, monto))

    conn.commit()
    conn.close()

    return redirect("/admin?tab=caja")


@app.route("/eliminar/<int:id>")
def eliminar_reserva(id):
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Buscar la reserva antes de borrarla
    cursor.execute("""
        SELECT id, nombre, fecha, cancha, horario
        FROM reservas
        WHERE id = ?
    """, (id,))
    reserva = cursor.fetchone()

    # Ver qué tablas existen
    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
    """)
    tablas = [t[0] for t in cursor.fetchall()]

    # Si existe movimientos, borra movimientos relacionados
    if reserva and "movimientos" in tablas:
        reserva_id = reserva[0]

        cursor.execute("PRAGMA table_info(movimientos)")
        columnas = [col[1] for col in cursor.fetchall()]

        campo_texto = "descripcion" if "descripcion" in columnas else "concepto"

        cursor.execute(f"""
            DELETE FROM movimientos
            WHERE {campo_texto} LIKE ?
        """, (f"%reserva #{reserva_id}%",))

    # Borrar la reserva
    cursor.execute("""
        DELETE FROM reservas
        WHERE id = ?
    """, (id,))

    conn.commit()
    conn.close()

    return redirect("/admin?tab=reservas")

@app.route("/confirmar_transferencia/<int:id>")
def confirmar_transferencia(id):
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT fecha, nombre, cancha, horario, precio, metodo_pago, estado_pago, pagado
        FROM reservas
        WHERE id = ?
    """, (id,))
    reserva = cursor.fetchone()

    if not reserva:
        conn.close()
        return "Reserva no encontrada"

    fecha_turno, nombre, cancha, horario, precio, metodo_pago, estado_pago, pagado_actual = reserva

    estado_texto = (estado_pago or "").strip().lower()
    pagado_actual = float(pagado_actual or 0)
    precio = float(precio or 0)

    if estado_texto == "pendiente reserva":
        monto_confirmado = round(precio * 0.30, 2)
        nuevo_pagado = monto_confirmado
        nuevo_estado = "Reserva"
        descripcion = f"Seña confirmada reserva #{id} - {nombre} - {cancha} - {horario}"
    elif estado_texto == "pendiente pago total":
        monto_confirmado = precio - pagado_actual
        if monto_confirmado < 0:
            monto_confirmado = 0
        nuevo_pagado = precio
        nuevo_estado = "Pagado"
        descripcion = f"Pago total confirmado reserva #{id} - {nombre} - {cancha} - {horario}"
    else:
        conn.close()
        return redirect(f"/editar/{id}")

    cursor.execute("""
        UPDATE reservas
        SET estado_pago = ?, pagado = ?
        WHERE id = ?
    """, (
        nuevo_estado,
        nuevo_pagado,
        id
    ))

    # Fecha real del cobro
    fecha_mov = date.today().strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
    """)
    tablas = [t[0] for t in cursor.fetchall()]

    if "movimientos" in tablas and monto_confirmado > 0:
        cursor.execute("PRAGMA table_info(movimientos)")
        columnas = [col[1] for col in cursor.fetchall()]
        campo_texto = "descripcion" if "descripcion" in columnas else "concepto"

        cursor.execute(f"""
            INSERT INTO movimientos (fecha, tipo, {campo_texto}, monto, metodo_pago)
            VALUES (?, ?, ?, ?, ?)
        """, (
            fecha_mov,
            "ingreso",
            descripcion,
            monto_confirmado,
            metodo_pago
        ))

    conn.commit()
    conn.close()

    return redirect(f"/editar/{id}")

@app.route("/eliminar-egreso/<int:id>")
def eliminar_egreso(id):
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
    """)
    tablas = [t[0] for t in cursor.fetchall()]

    if "egresos" in tablas:
        cursor.execute("DELETE FROM egresos WHERE id = ?", (id,))
    elif "movimientos" in tablas:
        cursor.execute("DELETE FROM movimientos WHERE id = ? AND tipo = 'egreso'", (id,))

    conn.commit()
    conn.close()

    return redirect("/admin?tab=caja")


@app.route("/logout")
def logout():
    session.pop("admin", None)
    return redirect("/login")

@app.route("/mp/success")
def mp_success():
    return """
    <html>
    <head>
        <title>Pago confirmado</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                text-align: center;
                padding-top: 80px;
                background: #ffffff;
            }
            h1 {
                color: #111;
                font-size: 48px;
                margin-bottom: 20px;
            }
            p {
                font-size: 28px;
                color: #444;
                margin-bottom: 30px;
            }
            a {
                font-size: 28px;
                color: #444;
                text-decoration: underline;
            }
        </style>
    </head>
    <body>
        <h1>✅ Reserva confirmada</h1>
        <p>El pago fue registrado correctamente.</p>
        <a href="/">Volver al inicio</a>
    </body>
    </html>
    """


@app.route("/confirmar_pago")
def confirmar_pago():
    return """
    <html>
    <head>
        <title>Pago confirmado</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                text-align: center;
                padding-top: 80px;
                background: #ffffff;
            }
            h1 {
                color: #111;
                font-size: 48px;
                margin-bottom: 20px;
            }
            p {
                font-size: 28px;
                color: #444;
                margin-bottom: 30px;
            }
            a {
                font-size: 28px;
                color: #444;
                text-decoration: underline;
            }
        </style>
    </head>
    <body>
        <h1>✅ Reserva confirmada</h1>
        <p>El pago fue registrado correctamente.</p>
        <a href="/">Volver al inicio</a>
    </body>
    </html>
    """

@app.route("/finalizar_pago")
def finalizar_pago():
    data = session.get("reserva_mp")

    if not data:
        return """
        <html>
        <body style="font-family: Arial; padding: 40px; text-align: center;">
            <h1>No hay reserva pendiente</h1>
            <a href="/">Volver al inicio</a>
        </body>
        </html>
        """

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO reservas
        (nombre, telefono, fecha, cancha, duracion, horario, precio, metodo_pago, estado_pago, pagado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["nombre"],
        data["telefono"],
        data["fecha"],
        data["cancha"],
        data["duracion"],
        data["horario"],
        data["precio"],
        "Mercado Pago",
        data["estado"],
        data["pagado"]
    ))

    conn.commit()
    conn.close()

    session.pop("reserva_mp", None)

    return """
    <html>
    <body style="font-family: Arial; padding: 40px; text-align: center;">
        <h1>✅ Reserva confirmada</h1>
        <p>El pago fue registrado correctamente.</p>
        <a href="/">Volver al inicio</a>
    </body>
    </html>
    """

@app.route("/mp/failure")
def mp_failure():
    session.pop("reserva_mp", None)
    return "Pago cancelado"


@app.route("/mp/pending")
def mp_pending():
    return "Pago pendiente"

@app.route("/reset-db")
def reset_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM reservas")
    cursor.execute("DELETE FROM movimientos")
    cursor.execute("DELETE FROM egresos")

    conn.commit()
    conn.close()

    return "Base de datos reseteada"

@app.route("/reservas")
def ver_reservas():
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM reservas
        ORDER BY fecha DESC, horario ASC
    """)
    reservas = cursor.fetchall()

    conn.close()

    return render_template("reservas.html", reservas=reservas)

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)