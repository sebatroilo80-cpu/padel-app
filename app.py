from flask import Flask, render_template, request, redirect, session, jsonify
from database import init_db
import sqlite3
from datetime import date, timedelta
import os
import uuid
import requests
from dotenv import load_dotenv

load_dotenv()

MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN") or ""
print("TOKEN MP:", MP_ACCESS_TOKEN[:12])
BASE_URL = (os.getenv("BASE_URL") or "http://127.0.0.1:5000").rstrip("/")

app = Flask(__name__)
app.secret_key = "clave_secreta_37"

init_db()


def obtener_config(cursor):
    cursor.execute("""
        SELECT precio_60_dia, precio_60_noche, precio_90_dia, precio_90_noche
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

    hora = int(horario.split(":")[0])

    if hora < 18:
        return precio_60_dia if duracion == "60 minutos" else precio_90_dia
    else:
        return precio_60_noche if duracion == "60 minutos" else precio_90_noche

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
    conn = sqlite3.connect("padel.db")
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

    conn = sqlite3.connect("padel.db")
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

    if hay_conflicto(cursor, fecha, cancha, horario, duracion):
        conn.close()
        return "Ese horario ya está ocupado o bloqueado. Volvé atrás y elegí otro."

    precio = calcular_precio(cursor, duracion, horario)
    pagado = calcular_pagado_inicial(precio, opcion_pago)
    estado_pago = calcular_estado_desde_pagado(precio, pagado)

    # Reserva del cliente
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

    # Si NO es Mercado Pago y ya pagó algo, impacta en caja acá
    # Si es Mercado Pago, NO lo cargamos acá para no duplicar:
    # eso lo hace el webhook cuando MP aprueba el pago.
    if metodo_pago != "Mercado Pago" and float(pagado) > 0:
        if opcion_pago == "Reserva":
            descripcion = f"Seña reserva #{reserva_id} - {nombre} - {cancha} - {horario}"
        else:
            descripcion = f"Pago total reserva #{reserva_id} - {nombre} - {cancha} - {horario}"

        cursor.execute("""
            INSERT INTO movimientos (fecha, tipo, descripcion, monto, metodo_pago)
            VALUES (?, ?, ?, ?, ?)
        """, (
            fecha,
            "ingreso",
            descripcion,
            pagado,
            metodo_pago
        ))

        conn.commit()
        conn.close()
        return redirect(f"/?ok=1")

    # Si es Mercado Pago, crear preferencia y redirigir
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
    return redirect(f"/?ok=1")

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

        conn = sqlite3.connect("padel.db")
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

    conn = sqlite3.connect("padel.db")
    cursor = conn.cursor()

    # Crear tabla movimientos si no existe
    try:
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
    except:
        pass

    fecha_admin = request.args.get("fecha")
    if not fecha_admin:
        fecha_admin = date.today().strftime("%Y-%m-%d")

    # =========================
    # TODAS las reservas
    # =========================
    cursor.execute("""
        SELECT * FROM reservas
        ORDER BY fecha DESC, horario ASC
    """)
    reservas = cursor.fetchall()

    # =========================
    # Reservas del día elegido
    # =========================
    cursor.execute("""
        SELECT * FROM reservas
        WHERE fecha = ?
        ORDER BY horario ASC
    """, (fecha_admin,))
    reservas_del_dia = cursor.fetchall()

    # =========================
    # Egresos cargados
    # =========================
    cursor.execute("""
        SELECT * FROM movimientos
        WHERE tipo = 'egreso'
        ORDER BY fecha DESC, id DESC
    """)
    egresos = cursor.fetchall()

    # =========================
    # Caja del día seleccionado
    # =========================
    cursor.execute("""
        SELECT COALESCE(SUM(monto), 0)
        FROM movimientos
        WHERE fecha = ? AND tipo = 'ingreso'
    """, (fecha_admin,))
    ingresos_hoy = cursor.fetchone()[0] or 0

    cursor.execute("""
        SELECT COALESCE(SUM(monto), 0)
        FROM movimientos
        WHERE fecha = ? AND tipo = 'egreso'
    """, (fecha_admin,))
    egresos_hoy = cursor.fetchone()[0] or 0

    total_hoy = ingresos_hoy - egresos_hoy

    # =========================
    # Métodos de pago del día seleccionado
    # =========================
    cursor.execute("""
        SELECT COALESCE(SUM(monto), 0)
        FROM movimientos
        WHERE fecha = ? AND tipo = 'ingreso' AND metodo_pago = 'Efectivo'
    """, (fecha_admin,))
    efectivo_hoy = cursor.fetchone()[0] or 0

    cursor.execute("""
        SELECT COALESCE(SUM(monto), 0)
        FROM movimientos
        WHERE fecha = ? AND tipo = 'ingreso' AND metodo_pago = 'Transferencia'
    """, (fecha_admin,))
    transferencia_hoy = cursor.fetchone()[0] or 0

    cursor.execute("""
        SELECT COALESCE(SUM(monto), 0)
        FROM movimientos
        WHERE fecha = ? AND tipo = 'ingreso' AND metodo_pago = 'Mercado Pago'
    """, (fecha_admin,))
    mercado_pago_hoy = cursor.fetchone()[0] or 0

    cursor.execute("""
        SELECT COALESCE(SUM(monto), 0)
        FROM movimientos
        WHERE fecha = ? AND tipo = 'ingreso' AND metodo_pago = 'QR'
    """, (fecha_admin,))
    qr_hoy = cursor.fetchone()[0] or 0

    # =========================
    # Total del mes
    # =========================
    cursor.execute("""
        SELECT COALESCE(SUM(CASE WHEN tipo='ingreso' THEN monto ELSE 0 END), 0) -
               COALESCE(SUM(CASE WHEN tipo='egreso' THEN monto ELSE 0 END), 0)
        FROM movimientos
        WHERE substr(fecha, 1, 7) = ?
    """, (fecha_admin[:7],))
    total_mes = cursor.fetchone()[0] or 0

    # =========================
    # Total del año
    # =========================
    cursor.execute("""
        SELECT COALESCE(SUM(CASE WHEN tipo='ingreso' THEN monto ELSE 0 END), 0) -
               COALESCE(SUM(CASE WHEN tipo='egreso' THEN monto ELSE 0 END), 0)
        FROM movimientos
        WHERE substr(fecha, 1, 4) = ?
    """, (fecha_admin[:4],))
    total_anio = cursor.fetchone()[0] or 0

    # =========================
    # Reporte mensual
    # =========================
    meses_nombres = {
        "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
        "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
        "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre"
    }

    reporte_mensual = []
    anio_actual = fecha_admin[:4]

    for mes_num in range(1, 13):
        mes_str = f"{mes_num:02d}"
        prefijo = f"{anio_actual}-{mes_str}"

        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'ingreso' AND substr(fecha, 1, 7) = ?
        """, (prefijo,))
        ingresos_mes = cursor.fetchone()[0] or 0

        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM movimientos
            WHERE tipo = 'egreso' AND substr(fecha, 1, 7) = ?
        """, (prefijo,))
        egresos_mes = cursor.fetchone()[0] or 0

        reporte_mensual.append({
            "mes": meses_nombres[mes_str],
            "ingresos": ingresos_mes,
            "egresos": egresos_mes,
            "saldo": ingresos_mes - egresos_mes
        })

    # =========================
    # Configuración de precios
    # =========================
    cursor.execute("""
        SELECT precio_60_dia, precio_60_noche, precio_90_dia, precio_90_noche
        FROM configuracion
        WHERE id = 1
    """)
    config = cursor.fetchone()

    # =========================
    # Agenda / grilla
    # =========================
    canchas = ["Cancha 1", "Cancha 2", "Cancha 3"]

    horarios = [
        "08:00","08:30","09:00","09:30","10:00","10:30","11:00","11:30",
        "12:00","12:30","13:00","13:30","14:00","14:30","15:00","15:30",
        "16:00","16:30","17:00","17:30","18:00","18:30","19:00","19:30",
        "20:00","20:30","21:00","21:30","22:00","22:30","23:00","23:30"
    ]

    grilla = {}
    for horario in horarios:
        grilla[horario] = {}
        for cancha in canchas:
            grilla[horario][cancha] = {"estado": "libre", "reserva": None}

    def slots_reserva(hora_inicio, duracion):
        try:
            idx = horarios.index(hora_inicio)
        except ValueError:
            return []
        if duracion == "90 minutos":
            return horarios[idx:idx+3]
        return horarios[idx:idx+2]

    for r in reservas_del_dia:
        cancha = r[4]
        duracion = r[5]
        hora_inicio = r[6]

        bloques = slots_reserva(hora_inicio, duracion)
        for i, h in enumerate(bloques):
            if h in grilla and cancha in grilla[h]:
                grilla[h][cancha]["estado"] = "inicio" if i == 0 else "continuacion"
                grilla[h][cancha]["reserva"] = r

    conn.close()

    return render_template(
        "admin.html",
        total_hoy=total_hoy,
        total_mes=total_mes,
        total_anio=total_anio,
        ingresos_hoy=ingresos_hoy,
        egresos_hoy=egresos_hoy,
        efectivo_hoy=efectivo_hoy,
        transferencia_hoy=transferencia_hoy,
        mercado_pago_hoy=mercado_pago_hoy,
        qr_hoy=qr_hoy,
        fecha_admin=fecha_admin,
        canchas=canchas,
        horarios=horarios,
        grilla=grilla,
        reporte_mensual=reporte_mensual,
        reservas=reservas,
        egresos=egresos,
        config=config
    )


@app.route("/configuracion", methods=["GET", "POST"])
def configuracion():
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect("padel.db")
    cursor = conn.cursor()

    if request.method == "POST":
        precio_60_dia = request.form["precio_60_dia"]
        precio_60_noche = request.form["precio_60_noche"]
        precio_90_dia = request.form["precio_90_dia"]
        precio_90_noche = request.form["precio_90_noche"]

        cursor.execute("""
            UPDATE configuracion
            SET precio_60_dia = ?, precio_60_noche = ?, precio_90_dia = ?, precio_90_noche = ?
            WHERE id = 1
        """, (precio_60_dia, precio_60_noche, precio_90_dia, precio_90_noche))

        conn.commit()

    config = obtener_config(cursor)
    if not config:
        config = [0, 0, 0, 0]

    conn.close()

    return render_template("configuracion.html", config=config)


@app.route("/admin-reservar", methods=["POST"])
def admin_reservar():
    if not session.get("admin"):
        return redirect("/login")

    nombre = request.form["nombre"]
    fecha = request.form["fecha"]
    cancha = request.form["cancha"]
    duracion = request.form["duracion"]
    horario = request.form["horario"]
    metodo_pago = request.form["metodo_pago"]
    opcion_pago = request.form["opcion_pago"]

    conn = sqlite3.connect("padel.db")
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

    if hay_conflicto(cursor, fecha, cancha, horario, duracion):
        conn.close()
        return "Ese horario ya está ocupado o bloqueado. Volvé atrás y elegí otro."

    precio = calcular_precio(cursor, duracion, horario)
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
        "",
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

    # Si pagó algo, registrar ingreso en caja
    if pagado and float(pagado) > 0:
        if opcion_pago == "Reserva":
            descripcion = f"Seña reserva #{reserva_id} - {nombre} - {cancha} - {horario}"
        else:
            descripcion = f"Pago total reserva #{reserva_id} - {nombre} - {cancha} - {horario}"

        cursor.execute("""
            INSERT INTO movimientos (fecha, tipo, descripcion, monto, metodo_pago)
            VALUES (?, ?, ?, ?, ?)
        """, (
            fecha,
            "ingreso",
            descripcion,
            pagado,
            metodo_pago
        ))

    conn.commit()
    conn.close()

    return redirect(f"/admin?fecha={fecha}")


@app.route("/editar/<int:id>", methods=["GET", "POST"])
def editar(id):
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect("padel.db")
    cursor = conn.cursor()

    if request.method == "POST":
        nombre = request.form["nombre"]
        fecha = request.form["fecha"]
        cancha = request.form["cancha"]
        duracion = request.form["duracion"]
        horario = request.form["horario"]
        metodo_pago = request.form["metodo_pago"]
        pagado = limpiar_numero(request.form.get("pagado"))

        if hay_conflicto(cursor, fecha, cancha, horario, duracion, excluir_id=id):
            conn.close()
            return "Ese horario ya está ocupado o bloqueado. Volvé atrás y elegí otro."

        precio = calcular_precio(cursor, duracion, horario)

        if pagado > precio:
            pagado = precio

        estado_pago = calcular_estado_desde_pagado(precio, pagado)

        cursor.execute("""
            UPDATE reservas
            SET nombre = ?, fecha = ?, cancha = ?, duracion = ?, horario = ?, metodo_pago = ?, estado_pago = ?, precio = ?, pagado = ?
            WHERE id = ?
        """, (nombre, fecha, cancha, duracion, horario, metodo_pago, estado_pago, precio, pagado, id))

        conn.commit()
        conn.close()

        return redirect(f"/admin?fecha={fecha}")

    cursor.execute("SELECT * FROM reservas WHERE id = ?", (id,))
    reserva = cursor.fetchone()
    conn.close()

    return render_template("editar.html", reserva=reserva)


@app.route("/agregar-egreso", methods=["POST"])
def agregar_egreso():
    if not session.get("admin"):
        return redirect("/login")

    fecha = request.form["fecha"]
    descripcion = request.form["descripcion"]
    monto = request.form["monto"]

    conn = sqlite3.connect("padel.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO egresos (fecha, descripcion, monto)
        VALUES (?, ?, ?)
    """, (fecha, descripcion, monto))

    conn.commit()
    conn.close()

    return redirect("/admin")


@app.route("/eliminar/<int:id>")
def eliminar_reserva(id):
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect("padel.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM reservas WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    return redirect("/admin")


@app.route("/eliminar-egreso/<int:id>")
def eliminar_egreso(id):
    if not session.get("admin"):
        return redirect("/login")

    conn = sqlite3.connect("padel.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM egresos WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    return redirect("/admin")


@app.route("/logout")
def logout():
    session.pop("admin", None)
    return redirect("/login")

@app.route("/mp/success")
def mp_success():
    return redirect("/confirmar_pago")

@app.route("/confirmar_pago")
def confirmar_pago():
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

    return """
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Pago recibido</title>
        <meta http-equiv="refresh" content="5;url=/finalizar_pago">
    </head>
    <body style="font-family: Arial; padding: 40px; text-align: center; background: #f6f7fb;">
        <div style="max-width: 520px; margin: 60px auto; background: white; padding: 30px; border-radius: 16px;">
            <h1 style="color:#16a34a;">✅ Pago recibido</h1>
            <p>Tu reserva se va a confirmar automáticamente.</p>
            <a href="/finalizar_pago" style="display:inline-block; margin-top:20px; padding:14px 24px; background:#16a34a; color:white; text-decoration:none; border-radius:10px;">
                Confirmar reserva
            </a>
        </div>
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

    conn = sqlite3.connect("padel.db")
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
    conn = sqlite3.connect("padel.db")
    cursor = conn.cursor()

    cursor.execute("DELETE FROM reservas")
    cursor.execute("DELETE FROM movimientos")
    cursor.execute("DELETE FROM egresos")

    conn.commit()
    conn.close()

    return "Base de datos reseteada"

if __name__ == "__main__":
    app.run(debug=True)