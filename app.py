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

    data = r.json()
    return data.get("init_point") or data.get("sandbox_init_point")

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
    nombre = request.form.get("nombre")
    telefono = request.form.get("telefono")
    fecha = request.form.get("fecha")
    cancha = request.form.get("cancha")
    duracion = request.form.get("duracion")
    horario = request.form.get("horario")
    metodo_pago = request.form.get("metodo_pago")
    precio = request.form.get("precio") or request.form.get("monto")

    if not nombre or not fecha or not cancha or not duracion or not horario:
        return "Faltan datos obligatorios para la reserva"

    if not precio:
        return "No llegó el precio de la reserva. Revisá el formulario."

    try:
        precio = float(precio)
    except ValueError:
        return "El precio es inválido"

    pagado = precio
    estado_pago = "pendiente"

    # Si existe la función hay_conflicto en tu sistema, la usamos
    try:
        conn = sqlite3.connect("padel.db")
        cursor = conn.cursor()

        if hay_conflicto(cursor, fecha, cancha, horario, duracion):
            conn.close()
            return "Ese horario ya está ocupado o bloqueado."
    except NameError:
        # Si no existe hay_conflicto, seguimos sin validar conflicto
        conn = sqlite3.connect("padel.db")
        cursor = conn.cursor()

    # MERCADO PAGO
    if metodo_pago == "Mercado Pago":
        external_id = str(uuid.uuid4())

        session["reserva_mp"] = {
            "id": external_id,
            "nombre": nombre,
            "telefono": telefono,
            "fecha": fecha,
            "cancha": cancha,
            "duracion": duracion,
            "horario": horario,
            "precio": precio,
            "pagado": pagado,
            "estado": estado_pago
        }

        url_pago = crear_preferencia_mp({
            "id": external_id,
            "cancha": cancha,
            "fecha": fecha,
            "horario": horario,
            "monto": precio
        })

        conn.close()

        if not url_pago:
            return "No se pudo crear el pago de Mercado Pago"

        return redirect(url_pago)

    # RESERVA NORMAL
    cursor.execute("""
        INSERT INTO reservas
        (nombre, telefono, fecha, cancha, duracion, horario, precio, metodo_pago, estado_pago, pagado)
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

    conn.commit()
    conn.close()

    return redirect("/")

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

        import requests
        import os

        access_token = os.getenv("MP_ACCESS_TOKEN")

        # Consultar el pago en MercadoPago
        url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        resp = requests.get(url, headers=headers)
        pago = resp.json()

        print("DETALLE PAGO:", pago)

        # Solo si está aprobado
        if pago.get("status") != "approved":
            return jsonify({"ok": True}), 200

        ref = pago.get("external_reference")

        if not ref:
            print("❌ No hay external_reference")
            return jsonify({"ok": True}), 200

        print("📦 REF:", ref)

        # Conexión a DB
        import sqlite3
        conn = sqlite3.connect("padel.db")
        cursor = conn.cursor()

        # Buscar el turno
        cursor.execute("""
            SELECT cliente, fecha, hora, cancha, precio
            FROM turnos
            WHERE id = ?
        """, (ref,))

        turno = cursor.fetchone()

        if turno:
            cliente, fecha, hora, cancha, precio = turno

            # Marcar como pagado
            cursor.execute("""
                UPDATE turnos
                SET pagado = 1
                WHERE id = ?
            """, (ref,))

            # Registrar ingreso en caja
            cursor.execute("""
                INSERT INTO movimientos (fecha, tipo, concepto, monto, metodo_pago)
                VALUES (?, 'ingreso', ?, ?, 'mercadopago')
            """, (
                fecha,
                f"Turno {cliente} - Cancha {cancha} {hora}",
                precio
            ))

            conn.commit()
            conn.close()

            print("✅ RESERVA GUARDADA AUTOMÁTICAMENTE")

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

    fecha_admin = request.args.get("fecha")
    if not fecha_admin:
        fecha_admin = date.today().strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT * FROM reservas
        ORDER BY fecha DESC, horario ASC
    """)
    reservas = cursor.fetchall()

    cursor.execute("""
        SELECT * FROM reservas
        WHERE fecha = ?
        ORDER BY horario ASC
    """, (fecha_admin,))
    reservas_dia = cursor.fetchall()

    hoy = date.today().strftime("%Y-%m-%d")
    mes_actual = date.today().strftime("%Y-%m")
    anio_actual = date.today().strftime("%Y")

    cursor.execute("SELECT COALESCE(SUM(pagado), 0) FROM reservas WHERE fecha = ?", (hoy,))
    ingresos_hoy = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(monto), 0) FROM egresos WHERE fecha = ?", (hoy,))
    egresos_hoy = cursor.fetchone()[0]

    total_hoy = ingresos_hoy - egresos_hoy

    cursor.execute("""
        SELECT COALESCE(SUM(pagado), 0)
        FROM reservas
        WHERE substr(fecha, 1, 7) = ?
    """, (mes_actual,))
    ingresos_mes = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(monto), 0)
        FROM egresos
        WHERE substr(fecha, 1, 7) = ?
    """, (mes_actual,))
    egresos_mes = cursor.fetchone()[0]

    total_mes = ingresos_mes - egresos_mes

    cursor.execute("""
        SELECT COALESCE(SUM(pagado), 0)
        FROM reservas
        WHERE substr(fecha, 1, 4) = ?
    """, (anio_actual,))
    ingresos_anio = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(monto), 0)
        FROM egresos
        WHERE substr(fecha, 1, 4) = ?
    """, (anio_actual,))
    egresos_anio = cursor.fetchone()[0]

    total_anio = ingresos_anio - egresos_anio

    efectivo_hoy, transferencia_hoy, mercado_pago_hoy, qr_hoy = obtener_total_por_metodo(cursor, hoy)

    reporte_mensual = []
    nombres_meses = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ]

    for i in range(1, 13):
        clave_mes = f"{anio_actual}-{i:02d}"

        cursor.execute("""
            SELECT COALESCE(SUM(pagado), 0)
            FROM reservas
            WHERE substr(fecha, 1, 7) = ?
        """, (clave_mes,))
        ingresos_mes_item = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COALESCE(SUM(monto), 0)
            FROM egresos
            WHERE substr(fecha, 1, 7) = ?
        """, (clave_mes,))
        egresos_mes_item = cursor.fetchone()[0]

        reporte_mensual.append({
            "mes": nombres_meses[i - 1],
            "ingresos": ingresos_mes_item,
            "egresos": egresos_mes_item,
            "saldo": ingresos_mes_item - egresos_mes_item
        })

    cursor.execute("""
        SELECT * FROM egresos
        ORDER BY fecha DESC, id DESC
    """)
    egresos = cursor.fetchall()

    config = obtener_config(cursor)
    if not config:
        config = [0, 0, 0, 0]

    conn.close()

    horarios = generar_horarios()
    canchas = ["Cancha 1", "Cancha 2", "Cancha 3"]

    grilla = {}
    for horario in horarios:
        grilla[horario] = {}
        for cancha in canchas:
            grilla[horario][cancha] = {
                "estado": "libre",
                "reserva": None
            }

    for r in reservas_dia:
        horario_reserva = r[6]
        cancha_reserva = r[4]
        duracion_reserva = r[5]

        slots = slots_reserva(horario_reserva, duracion_reserva)
        if not slots:
            continue

        primero = True
        for slot in slots:
            if slot in grilla and cancha_reserva in grilla[slot]:
                grilla[slot][cancha_reserva] = {
                    "estado": "inicio" if primero else "continuacion",
                    "reserva": r
                }
                primero = False

    return render_template(
        "admin.html",
        reservas=reservas,
        egresos=egresos,
        total_hoy=total_hoy,
        total_mes=total_mes,
        total_anio=total_anio,
        ingresos_hoy=ingresos_hoy,
        egresos_hoy=egresos_hoy,
        efectivo_hoy=efectivo_hoy,
        transferencia_hoy=transferencia_hoy,
        mercado_pago_hoy=mercado_pago_hoy,
        qr_hoy=qr_hoy,
        reporte_mensual=reporte_mensual,
        fecha_admin=fecha_admin,
        horarios=horarios,
        canchas=canchas,
        grilla=grilla,
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

    if hay_conflicto(cursor, fecha, cancha, horario, duracion):
        conn.close()
        return "Ese horario ya está ocupado o bloqueado. Volvé atrás y elegí otro."

    precio = calcular_precio(cursor, duracion, horario)
    pagado = calcular_pagado_inicial(precio, opcion_pago)
    estado_pago = calcular_estado_desde_pagado(precio, pagado)

    cursor.execute("""
        INSERT INTO reservas (nombre, telefono, fecha, cancha, duracion, horario, precio, metodo_pago, estado_pago, pagado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (nombre, "", fecha, cancha, duracion, horario, precio, metodo_pago, estado_pago, pagado))

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

if __name__ == "__main__":
    app.run(debug=True)