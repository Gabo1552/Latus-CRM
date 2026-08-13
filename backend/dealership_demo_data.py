"""Escenario comercial demostrativo para una concesionaria argentina."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


TZ_NAME = "America/Argentina/Buenos_Aires"
try:
    LOCAL_TZ = ZoneInfo(TZ_NAME)
except Exception:  # pragma: no cover - Windows sin tzdata
    LOCAL_TZ = timezone(timedelta(hours=-3), name="Argentina")


def _weekly(start: str = "09:00", end: str = "18:30", *, saturday: bool = True):
    return {
        str(day): ([{"start": start, "end": end}] if day < 5 else
                   ([{"start": "09:00", "end": "13:00"}] if day == 5 and saturday else []))
        for day in range(7)
    }


def build_dealership_demo_dataset(now: datetime | None = None) -> dict:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    now_local = now_utc.astimezone(LOCAL_TZ)

    def iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    def ago(days: int = 0, hours: int = 0, minutes: int = 0) -> datetime:
        return now_utc - timedelta(days=days, hours=hours, minutes=minutes)

    def future_business_day(days: int, hour: int, minute: int = 0, duration: int = 45):
        candidate = now_local.date()
        remaining = days
        while remaining:
            candidate += timedelta(days=1)
            if candidate.weekday() < 6:
                remaining -= 1
        start = datetime(candidate.year, candidate.month, candidate.day, hour, minute, tzinfo=LOCAL_TZ)
        return iso(start), iso(start + timedelta(minutes=duration))

    created = iso(ago(180))
    schedule = _weekly()

    work_areas = [
        {"id": "dealer_area_ventas", "name": "Ventas 0 km", "description": "Consultas, financiación y cierre de vehículos nuevos.", "routing_rules": "Derivar consultas sobre modelos 0 km, financiación, patentamiento y toma de usados.", "created_at": created, "is_demo": True},
        {"id": "dealer_area_usados", "name": "Usados seleccionados", "description": "Tasaciones, permutas y vehículos usados.", "routing_rules": "Derivar tasaciones, permutas y consultas por unidades usadas.", "created_at": created, "is_demo": True},
        {"id": "dealer_area_postventa", "name": "Entregas y postventa", "description": "Entrega de unidades, documentación y servicios.", "routing_rules": "Derivar consultas posteriores a la reserva o compra.", "created_at": created, "is_demo": True},
    ]

    users = [
        {"user_id": "dealer_user_manager", "email": "martin@autonorte.com.ar", "name": "Martín Acosta", "role": "supervisor", "work_areas": ["dealer_area_ventas", "dealer_area_usados", "dealer_area_postventa"]},
        {"user_id": "dealer_user_sales_1", "email": "sofia@autonorte.com.ar", "name": "Sofía Benítez", "role": "agent", "work_areas": ["dealer_area_ventas"]},
        {"user_id": "dealer_user_sales_2", "email": "lucas@autonorte.com.ar", "name": "Lucas Ferrero", "role": "agent", "work_areas": ["dealer_area_usados"]},
        {"user_id": "dealer_user_delivery", "email": "valeria@autonorte.com.ar", "name": "Valeria Quiroga", "role": "agent", "work_areas": ["dealer_area_postventa"]},
    ]
    for user in users:
        user.update({
            "active": True, "is_demo": True, "auth_provider": "local",
            "created_at": created, "updated_at": iso(now_utc),
            "calendar_settings": {
                "enabled": True, "timezone": TZ_NAME, "default_duration_minutes": 45,
                "buffer_minutes": 15, "weekly_schedule": schedule,
            },
        })

    tags = [
        {"id": "dealer_tag_testdrive", "name": "Test drive", "color": "#2563EB", "is_demo": True},
        {"id": "dealer_tag_financing", "name": "Financiación", "color": "#7C3AED", "is_demo": True},
        {"id": "dealer_tag_tradein", "name": "Entrega usado", "color": "#D97706", "is_demo": True},
        {"id": "dealer_tag_hot", "name": "Alta intención", "color": "#DC2626", "is_demo": True},
        {"id": "dealer_tag_followup", "name": "Seguimiento", "color": "#059669", "is_demo": True},
        {"id": "dealer_tag_fleet", "name": "Venta corporativa", "color": "#0F766E", "is_demo": True},
    ]

    vehicle_rows = [
        ("veh_corolla_cross", "Toyota Corolla Cross XEI CVT 2025", "AN-0KM-CCXEI", "0 km", 46800000, 1, "SUV automática, motor 2.0, Toyota Safety Sense y pantalla multimedia.", ["SUV", "Automático", "Entrega inmediata"], "photo-1503376780353-7e6692767b70"),
        ("veh_cronos", "Fiat Cronos Precision CVT 2025", "AN-0KM-CRPRE", "0 km", 29800000, 2, "Sedán nacional automático con cámara de retroceso y climatizador.", ["Sedán", "Automático", "Nacional"], "photo-1549317661-bd32c8ce0db2"),
        ("veh_tracker", "Chevrolet Tracker Premier 2025", "AN-0KM-TRPRE", "0 km", 45200000, 1, "SUV turbo automática con asistencias de conducción y techo panorámico.", ["SUV", "Turbo", "Automático"], "photo-1552519507-da3b142c6e3d"),
        ("veh_peugeot_208", "Peugeot 208 GT T200 2025", "AN-0KM-208GT", "0 km", 36500000, 1, "Hatchback turbo automático con i-Cockpit 3D.", ["Hatchback", "Turbo", "Automático"], "photo-1492144534655-ae79c964c9d7"),
        ("veh_hilux", "Toyota Hilux SRX 4x4 AT 2024", "AN-US-HILUX24", "Usados seleccionados", 61200000, 1, "34.000 km, services oficiales, cubiertas nuevas y garantía de seis meses.", ["Pickup", "4x4", "Usado certificado"], "photo-1550355291-bbee04a92027"),
        ("veh_taos", "Volkswagen Taos Comfortline 2023", "AN-US-TAOS23", "Usados seleccionados", 39800000, 1, "42.500 km, DSG, cuero y mantenimiento documentado.", ["SUV", "Automático", "Usado certificado"], "photo-1533473359331-0135ef1b58bf"),
        ("veh_amarok", "Volkswagen Amarok V6 Highline 2022", "AN-US-AMV622", "Usados seleccionados", 54800000, 1, "68.000 km, 4Motion, caja automática y lona marítima.", ["Pickup", "V6", "4x4"], "photo-1511919884226-fd3cad34687c"),
        ("veh_yaris", "Toyota Yaris XLS CVT 2023", "AN-US-YARIS23", "Usados seleccionados", 27800000, 1, "29.000 km, único dueño, services oficiales y excelente estado.", ["Hatchback", "Automático", "Bajo kilometraje"], "photo-1583121274602-3e2820c69888"),
        ("veh_partner", "Peugeot Partner Confort 1.6 2025", "AN-0KM-PART", "Utilitarios", 32100000, 3, "Utilitario para reparto urbano con financiación especial para empresas.", ["Utilitario", "Trabajo", "Plan empresa"], "photo-1590362891991-f776e747a588"),
    ]
    products = []
    for pid, name, sku, category, price, stock, description, vehicle_tags, image in vehicle_rows:
        products.append({
            "product_id": pid, "name": name, "sku": sku, "category": category,
            "description": description, "price": price, "currency": "ARS",
            "stock_status": "disponible", "track_stock": True, "stock_quantity": stock,
            "active": True, "tags": vehicle_tags,
            "image_url": f"https://images.unsplash.com/{image}?fit=crop&w=1200&h=800&q=82",
            "promo_price": None, "promo_limit_type": "none", "promo_start_at": None,
            "promo_end_at": None, "promo_unit_limit": None, "promo_units_used": 0,
            "commercial_conditions": "Precio de lista sujeto a disponibilidad. Gastos de patentamiento o transferencia no incluidos.",
            "external_link": None, "created_at": created, "updated_at": iso(now_utc),
            "deleted_at": None, "is_demo": True,
        })
    product_by_id = {item["product_id"]: item for item in products}
    product_by_id["veh_cronos"].update({
        "promo_price": 28450000, "promo_limit_type": "date",
        "promo_start_at": iso(ago(15)), "promo_end_at": iso(now_utc + timedelta(days=12)),
        "commercial_conditions": "Bonificación de demostración vigente hasta la fecha indicada. Consultar financiación.",
    })
    product_by_id["veh_partner"].update({
        "promo_price": 30900000, "promo_limit_type": "units", "promo_unit_limit": 3,
        "promo_units_used": 1, "commercial_conditions": "Precio promocional para las primeras tres unidades del plan empresas.",
    })

    prospects = [
        ("Carolina Méndez", "+54 9 11 6123-8042", "carolina.mendez@gmail.com", "Palermo, CABA", "Instagram", ["Test drive", "Alta intención"]),
        ("Federico Roldán", "+54 9 11 4871-2290", "federico.roldan@gmail.com", "Vicente López, Buenos Aires", "WhatsApp", ["Financiación", "Entrega usado"]),
        ("Mariana López", "+54 9 11 3560-1178", "mariana.lopez@gmail.com", "Caballito, CABA", "Sitio web", ["Seguimiento"]),
        ("Diego Suárez", "+54 9 221 510-7782", "diego.suarez@gmail.com", "La Plata, Buenos Aires", "Mercado Libre", ["Entrega usado", "Alta intención"]),
        ("Paula Fernández", "+54 9 341 601-4409", "paula.fernandez@gmail.com", "Rosario, Santa Fe", "Instagram", ["Financiación"]),
        ("Nicolás Gaitán", "+54 9 351 702-9190", "nicolas.gaitan@gmail.com", "Córdoba Capital", "WhatsApp", ["Test drive"]),
        ("Transporte Andino SRL", "+54 9 11 4440-8891", "compras@transporteandino.com.ar", "Barracas, CABA", "Referido", ["Venta corporativa", "Alta intención"]),
        ("Laura Villalba", "+54 9 11 5092-3338", "laura.villalba@gmail.com", "Belgrano, CABA", "Sitio web", ["Test drive", "Seguimiento"]),
        ("Gonzalo Romero", "+54 9 261 612-0455", "gonzalo.romero@gmail.com", "Mendoza Capital", "Mercado Libre", ["Entrega usado"]),
        ("Esteban Pereyra", "+54 9 11 6810-2574", "esteban.pereyra@gmail.com", "San Isidro, Buenos Aires", "WhatsApp", ["Financiación", "Seguimiento"]),
    ]
    scenarios = [
        ("Corolla Cross XEI con entrega inmediata", "proposal", "high", 46800000, "dealer_user_sales_1", "dealer_area_ventas", "veh_corolla_cross", 0, "open", False, [("contact", "Hola, vi la Corolla Cross en Instagram. ¿La tienen para entrega inmediata?"), ("bot", "¡Hola, Carolina! Tenemos una Corolla Cross XEI disponible. ¿Querés coordinar un test drive o recibir opciones de financiación?"), ("contact", "Quiero probarla mañana y tengo un Etios para entregar."), ("agent", "Soy Sofía. Te reservo un test drive a las 16:30 y podemos tasar el Etios en el momento."), ("contact", "Perfecto, agendame.")]),
        ("Cronos Precision financiado", "qualified", "high", 28450000, "dealer_user_sales_1", "dealer_area_ventas", "veh_cronos", 1, "open", True, [("contact", "¿Cuánto tengo que entregar para financiar un Cronos automático?"), ("bot", "El Cronos Precision CVT tiene una bonificación vigente. Podemos simular anticipo y cuotas según tu DNI."), ("contact", "Puedo entregar 12 millones y mi auto usado."), ("bot", "Perfecto. Para una cotización final necesitamos tasar tu usado y validar la financiación. Te comunico con una asesora.")]),
        ("Tracker Premier para uso familiar", "contacted", "medium", 45200000, "dealer_user_sales_1", "dealer_area_ventas", "veh_tracker", 2, "pending", True, [("contact", "Busco una SUV familiar automática. ¿Qué diferencia hay entre Tracker Premier y Corolla Cross?"), ("bot", "Ambas son automáticas y cuentan con asistencias de seguridad. La Tracker Premier se destaca por el motor turbo y el techo panorámico."), ("contact", "Mandame las fichas y lo veo con mi pareja.")]),
        ("Permuta por Hilux SRX", "won", "high", 61200000, "dealer_user_sales_2", "dealer_area_usados", "veh_hilux", 14, "resolved", False, [("contact", "Tengo una Ranger 2020 y quiero pasarme a la Hilux SRX publicada."), ("agent", "Hola Diego, soy Lucas. Podemos recibir tu Ranger. ¿Tenés fotos y kilometraje?"), ("contact", "Sí, tiene 91.000 km. Te mando todo ahora."), ("agent", "Tasación aprobada. Reservamos la Hilux con tu seña."), ("contact", "Seña transferida, gracias.")]),
        ("Peugeot 208 GT con financiación", "new", "medium", 36500000, None, "dealer_area_ventas", "veh_peugeot_208", 0, "open", True, [("contact", "Hola, ¿el 208 GT viene con caja automática?"), ("bot", "Sí, el 208 GT T200 es automático y tiene motor turbo. Hay una unidad disponible."), ("contact", "¿Qué financiación ofrecen entregando 15 millones?")]),
        ("Taos usada con test drive", "qualified", "medium", 39800000, "dealer_user_sales_2", "dealer_area_usados", "veh_taos", 3, "open", True, [("contact", "¿Sigue disponible la Taos 2023? ¿Tiene garantía?"), ("bot", "Sí, está disponible y cuenta con garantía de seis meses de la concesionaria."), ("contact", "Estoy en Córdoba pero viajo el viernes. ¿Puedo verla?"), ("agent", "Te reservé un test drive para el viernes a las 11:00.")]),
        ("Flota de tres Peugeot Partner", "proposal", "high", 92700000, "dealer_user_manager", "dealer_area_ventas", "veh_partner", 1, "pending", False, [("contact", "Necesitamos tres Partner para reparto. ¿Tienen condición para empresa?"), ("bot", "Sí, contamos con un plan especial para empresas y stock de tres unidades."), ("agent", "Soy Martín, gerente comercial. Preparé una propuesta por las tres unidades con bonificación y patentamiento bonificado."), ("contact", "Enviame la propuesta formal para presentarla a administración.")]),
        ("Yaris XLS para primer auto", "won", "medium", 27800000, "dealer_user_sales_2", "dealer_area_usados", "veh_yaris", 28, "resolved", True, [("contact", "Busco mi primer auto automático y vi el Yaris usado."), ("bot", "El Yaris XLS es automático, tiene 29.000 km y services oficiales."), ("contact", "¿Puedo reservarlo hasta mañana?"), ("agent", "Sí, Laura. Con una seña queda reservado por 48 horas."), ("contact", "Listo, ya pagué la seña.")]),
        ("Amarok V6 con envío a Mendoza", "lost", "medium", 54800000, "dealer_user_sales_2", "dealer_area_usados", "veh_amarok", 35, "resolved", False, [("contact", "Estoy en Mendoza. ¿La Amarok puede enviarse?"), ("agent", "Sí, podemos coordinar transporte y documentación a distancia."), ("contact", "Finalmente compré una unidad acá. Gracias.")]),
        ("Corolla Cross con plan de cuotas", "contacted", "medium", 46800000, "dealer_user_sales_1", "dealer_area_ventas", "veh_corolla_cross", 4, "open", True, [("contact", "¿Se puede financiar la Corolla Cross sin entregar usado?"), ("bot", "Sí, podemos armar una propuesta con anticipo y saldo financiado."), ("contact", "Mandame una simulación con 20 millones de anticipo.")]),
    ]

    contacts, leads, conversations, messages, notes, tasks = [], [], [], [], [], []
    agent_names = {item["user_id"]: item["name"] for item in users}
    for index, (person, scenario) in enumerate(zip(prospects, scenarios), 1):
        name, phone, email, location, source, person_tags = person
        title, status, priority, value, assigned, area, product_id, days, conv_status, bot_enabled, chat = scenario
        contact_id = f"dealer_contact_{index:02d}"
        lead_id = f"dealer_lead_{index:02d}"
        conversation_id = f"dealer_conv_{index:02d}"
        opened = ago(days + 2, 2)
        contacts.append({
            "id": contact_id, "name": name, "phone": phone,
            "whatsapp_id": "".join(char for char in phone if char.isdigit()),
            "email": email, "company": location, "avatar": None, "tags": person_tags,
            "notes": f"Contacto demostrativo. Origen: {source}.", "lead_source": source,
            "created_at": iso(opened), "is_demo": True,
        })
        product = product_by_id[product_id]
        lead = {
            "id": lead_id, "contact_id": contact_id, "title": title, "status": status,
            "priority": priority, "value": value, "assigned_to": assigned, "source": source,
            "tags": person_tags,
            "products": [{"id": product_id, "name": product["name"], "price": product.get("promo_price") or product["price"], "quantity": 1, "currency": "ARS", "list_price": product["price"], "promotion_applied": bool(product.get("promo_price"))}],
            "created_at": iso(opened), "updated_at": iso(ago(days)), "is_demo": True,
        }
        if status in {"won", "lost"}:
            lead.update({"closed_at": iso(ago(days)), "closed_by": assigned, "closed_value": value})
        leads.append(lead)
        conv_messages = []
        base = ago(days, 3)
        for msg_index, (sender_type, body) in enumerate(chat, 1):
            created_at = iso(base + timedelta(minutes=msg_index * 7))
            sender_name = name if sender_type == "contact" else ("Asistente de AutoNorte" if sender_type == "bot" else agent_names.get(assigned, "Equipo AutoNorte"))
            conv_messages.append({
                "id": f"dealer_msg_{index:02d}_{msg_index:02d}", "conversation_id": conversation_id,
                "sender_type": sender_type, "sender_name": sender_name, "body": body,
                "created_at": created_at, "direction": "inbound" if sender_type == "contact" else "outbound",
                "delivery_status": "received" if sender_type == "contact" else "read",
                "message_type": "text", "channel": "whatsapp", "is_demo": True,
            })
        messages.extend(conv_messages)
        last = conv_messages[-1]
        conversations.append({
            "id": conversation_id, "contact_id": contact_id, "lead_id": lead_id,
            "status": conv_status, "priority": priority, "bot_enabled": bot_enabled,
            "assigned_to": assigned, "assigned_work_area": area,
            "last_message": last["body"], "last_message_at": last["created_at"],
            "unread": 1 if last["sender_type"] == "contact" and conv_status != "resolved" else 0,
            "bot_status": "bot_activo" if bot_enabled else "en_atencion_humana",
            "channel": "whatsapp", "channel_external_id": f"demo:{contacts[-1]['whatsapp_id']}",
            "created_at": iso(opened), "is_demo": True,
        })
        if index in {1, 2, 4, 7, 8}:
            notes.append({
                "id": f"dealer_note_{index:02d}", "lead_id": lead_id,
                "body": {
                    1: "Tiene un Etios 2019 para tasar. Prioriza seguridad y entrega inmediata.",
                    2: "Solicitar fotos y documentación del usado antes de cotizar la permuta.",
                    4: "Reserva confirmada. Coordinar transferencia y entrega para el jueves.",
                    7: "Cuenta corporativa. Requiere factura A y propuesta con mantenimiento.",
                    8: "Primera compra. Explicar costos de transferencia y seguro antes de la entrega.",
                }[index],
                "author_id": "dealer_user_manager", "author_name": "Martín Acosta",
                "created_at": iso(ago(max(days - 1, 0))), "is_demo": True,
            })

    task_rows = [
        ("Coordinar test drive de Corolla Cross", "dealer_lead_01", 1, "todo", "high", "dealer_user_sales_1"),
        ("Solicitar documentación del usado", "dealer_lead_02", 0, "in_progress", "high", "dealer_user_sales_1"),
        ("Enviar comparativa Tracker vs Corolla Cross", "dealer_lead_03", 1, "todo", "medium", "dealer_user_sales_1"),
        ("Preparar documentación de entrega Hilux", "dealer_lead_04", 2, "todo", "high", "dealer_user_delivery"),
        ("Responder simulación de financiación", "dealer_lead_05", 0, "todo", "high", None),
        ("Enviar propuesta corporativa formal", "dealer_lead_07", 1, "in_progress", "high", "dealer_user_manager"),
        ("Coordinar entrega del Yaris", "dealer_lead_08", 3, "todo", "medium", "dealer_user_delivery"),
        ("Revisar publicaciones de usados", None, -2, "done", "low", "dealer_user_sales_2"),
    ]
    for index, (title, lead_id, due, status, priority, assigned) in enumerate(task_rows, 1):
        tasks.append({
            "id": f"dealer_task_{index:02d}", "title": title,
            "description": "Tarea de demostración para el seguimiento comercial de la concesionaria.",
            "lead_id": lead_id, "due_date": (now_local.date() + timedelta(days=due)).isoformat(),
            "status": status, "priority": priority, "assigned_to": assigned,
            "created_at": iso(ago(5)), "is_demo": True,
        })

    services = [
        {"id": "dealer_service_testdrive", "name": "Test drive", "description": "Prueba de manejo acompañada por un asesor.", "active": True, "duration_minutes": 45, "max_concurrent": 3, "timezone": TZ_NAME, "weekly_schedule": schedule, "sort_order": 0},
        {"id": "dealer_service_tradein", "name": "Tasación de usado", "description": "Inspección y tasación para permuta.", "active": True, "duration_minutes": 60, "max_concurrent": 2, "timezone": TZ_NAME, "weekly_schedule": schedule, "sort_order": 1},
        {"id": "dealer_service_delivery", "name": "Entrega de vehículo", "description": "Firma de documentación y entrega técnica de la unidad.", "active": True, "duration_minutes": 90, "max_concurrent": 2, "timezone": TZ_NAME, "weekly_schedule": schedule, "sort_order": 2},
        {"id": "dealer_service_financing", "name": "Asesoría de financiación", "description": "Simulación y revisión de alternativas de pago.", "active": True, "duration_minutes": 30, "max_concurrent": 3, "timezone": TZ_NAME, "weekly_schedule": schedule, "sort_order": 3},
    ]
    appointment_rows = [
        ("Test drive Corolla Cross", "dealer_service_testdrive", "dealer_user_sales_1", 1, 16, 30, 45, "dealer_contact_01", "dealer_lead_01", "dealer_conv_01"),
        ("Tasación de vehículo usado", "dealer_service_tradein", "dealer_user_sales_1", 2, 10, 0, 60, "dealer_contact_02", "dealer_lead_02", "dealer_conv_02"),
        ("Test drive Taos Comfortline", "dealer_service_testdrive", "dealer_user_sales_2", 3, 11, 0, 45, "dealer_contact_06", "dealer_lead_06", "dealer_conv_06"),
        ("Entrega técnica Toyota Hilux", "dealer_service_delivery", "dealer_user_delivery", 4, 15, 0, 90, "dealer_contact_04", "dealer_lead_04", "dealer_conv_04"),
        ("Asesoría de financiación Peugeot 208", "dealer_service_financing", "dealer_user_sales_1", 1, 12, 0, 30, "dealer_contact_05", "dealer_lead_05", "dealer_conv_05"),
        ("Entrega Volkswagen Yaris", "dealer_service_delivery", "dealer_user_delivery", 5, 10, 30, 90, "dealer_contact_08", "dealer_lead_08", "dealer_conv_08"),
    ]
    appointments = []
    for index, row in enumerate(appointment_rows, 1):
        title, service_id, assigned, day, hour, minute, duration, contact_id, lead_id, conversation_id = row
        start, end = future_business_day(day, hour, minute, duration)
        appointments.append({
            "id": f"dealer_appt_{index:02d}", "contact_id": contact_id, "lead_id": lead_id,
            "conversation_id": conversation_id, "title": title,
            "description": "Cita demostrativa creada para recorrer la agenda comercial.",
            "location": "AutoNorte · Av. del Libertador 7850, CABA", "event_type": "appointment",
            "start_time": start, "end_time": end, "status": "scheduled", "assigned_to": assigned,
            "scheduling_mode": "business", "service_id": service_id,
            "service_name": next(item["name"] for item in services if item["id"] == service_id),
            "reminder_enabled": True, "reminder_minutes_before": 1440,
            "reminder_template_id": "dealer_template_appointment",
            "reminder_due_at": iso(datetime.fromisoformat(start) - timedelta(days=1)),
            "reminder_status": "pending", "confirmation_status": "pending",
            "created_by_bot": index in {1, 3, 5}, "created_by": "bot" if index in {1, 3, 5} else assigned,
            "created_by_name": "Asistente de AutoNorte" if index in {1, 3, 5} else agent_names[assigned],
            "created_at": iso(ago(3)), "updated_at": iso(ago(1)), "is_demo": True,
        })

    sales = []
    inventory_movements = []
    sold = [("dealer_sale_hilux", 4, "veh_hilux", 61200000, 14, 12000000, "transfer"), ("dealer_sale_yaris", 8, "veh_yaris", 27800000, 28, 5000000, "mercadopago")]
    for sale_id, contact_no, product_id, total, days, paid, method in sold:
        product = product_by_id[product_id]
        confirmed_at = iso(ago(days))
        sales.append({
            "sale_id": sale_id, "status": "confirmed", "payment_status": "partial",
            "contact_id": f"dealer_contact_{contact_no:02d}", "lead_id": f"dealer_lead_{contact_no:02d}",
            "customer_name": prospects[contact_no - 1][0], "currency": "ARS",
            "lines": [{"product_id": product_id, "name": product["name"], "sku": product["sku"], "quantity": 1, "unit_price": total, "list_price": product["price"], "line_total": total, "currency": "ARS", "promotion_applied": False, "stock_tracked": True}],
            "subtotal": total, "discount_total": 0, "total": total,
            "payments": [{"payment_id": f"pay_{sale_id}", "amount": paid, "method": method, "reference": f"DEMO-{contact_no:04d}", "created_at": confirmed_at, "created_by": "dealer_user_manager"}],
            "amount_paid": paid, "balance_due": total - paid,
            "notes": "Venta demostrativa con precio histórico inmóvil.",
            "created_at": iso(ago(days + 2)), "created_by": "dealer_user_manager",
            "updated_at": confirmed_at, "updated_by": "dealer_user_manager",
            "confirmed_at": confirmed_at, "confirmed_by": "dealer_user_manager", "is_demo": True,
        })
        inventory_movements.append({
            "movement_id": f"dealer_mov_{product_id}", "product_id": product_id,
            "product_name": product["name"], "sku": product["sku"], "quantity_delta": -1,
            "movement_type": "sale", "sale_id": sale_id, "reason": None,
            "stock_before": 1, "stock_after": 0, "created_at": confirmed_at,
            "created_by": "dealer_user_manager", "is_demo": True,
        })
        product["stock_quantity"] = 0
        product["stock_status"] = "sin_stock"

    bot_events = [
        {"event_id": "dealer_bot_event_01", "conversation_id": "dealer_conv_01", "event_type": "handoff", "reason": "Tasación de usado y coordinación de test drive", "created_at": iso(ago(0, 1)), "is_demo": True},
        {"event_id": "dealer_bot_event_02", "conversation_id": "dealer_conv_07", "event_type": "handoff", "reason": "Cotización corporativa especial", "created_at": iso(ago(1)), "is_demo": True},
    ]

    ai_usage_logs = []
    for index in range(32):
        prompt = 540 + (index * 53) % 1250
        completion = 90 + (index * 31) % 360
        ai_usage_logs.append({
            "log_id": f"dealer_usage_{index + 1:03d}", "created_at": iso(ago(index % 14, (index * 2) % 20)),
            "provider": "built_in", "model": "gpt-4o-mini", "prompt_tokens": prompt,
            "completion_tokens": completion, "total_tokens": prompt + completion,
            "estimated_cost_usd": round((prompt * 0.15 + completion * 0.60) / 1_000_000, 6),
            "provider_cost_usd": None, "cost_source": "estimated", "token_source": "provider_response",
            "provider_request_id": f"dealer_req_{index + 1:03d}", "latency_ms": 650 + (index * 79) % 1700,
            "status": "success", "error_message": None,
            "conversation_id": f"dealer_conv_{(index % 10) + 1:02d}", "message_id": None,
            "user_id": None, "purpose": "bot_pipeline" if index % 4 else "suggest_reply", "is_demo": True,
        })

    bot_settings = {
        "_id": "default", "bot_enabled_default": True, "confidence_threshold": 0.76,
        "recent_messages_context_max": 16, "provider": "built_in", "model": "gpt-4o-mini",
        "bot_name": "Asistente de AutoNorte", "tone": "profesional, claro, comercial y cercano",
        "include_client_info": True, "default_handoff_user_id": "dealer_user_manager",
        "company_context": "AutoNorte es una concesionaria argentina de vehículos 0 km y usados seleccionados ubicada en CABA. Ofrece financiación, toma de usados, test drives, ventas corporativas y gestión de entrega.",
        "business_instructions": "Informar precios en pesos argentinos. No prometer stock, aprobación crediticia ni tasaciones sin verificación humana. Derivar negociaciones, reservas, señas y datos financieros.",
        "response_instructions": "Responder en español rioplatense, con mensajes breves. Antes de agendar confirmar modelo, nombre, teléfono y horario. Para financiar, consultar anticipo estimado y si entrega usado.",
        "handoff_rules": "Derivar cuando haya una negociación de precio, tasación, financiación, reserva, reclamo, venta corporativa o datos sensibles.",
        "faqs": [
            {"question": "¿Dónde están?", "answer": "Estamos en Av. del Libertador 7850, CABA."},
            {"question": "¿Toman usados?", "answer": "Sí. La tasación final requiere inspección presencial y documentación del vehículo."},
            {"question": "¿Ofrecen financiación?", "answer": "Sí, contamos con alternativas según el modelo y perfil crediticio. La aprobación está sujeta a evaluación."},
            {"question": "¿Puedo hacer un test drive?", "answer": "Sí, coordinamos test drives de lunes a sábado según disponibilidad."},
        ],
        "catalog_reading_enabled": True, "bot_inactive_close_hours": 12,
        "appointment_scheduling_enabled": True, "appointment_available_days": [1, 2, 3, 4, 5, 6],
        "appointment_business_hours": "09:00-18:30", "appointment_duration_minutes": 45,
        "appointment_mode": "business", "appointment_timezone": TZ_NAME,
        "appointment_services": services,
        "whatsapp_recontact_templates": [
            {"id": "dealer_template_recontact", "purpose": "recontact", "label": "Retomar consulta de vehículo", "name": "retomar_consulta_vehiculo", "language": "es_AR", "body_preview": "Hola {{client_name}}, somos AutoNorte. ¿Querés que retomemos tu consulta y revisemos disponibilidad o financiación?", "parameter_keys": ["client_name"], "active": True, "sort_order": 0},
            {"id": "dealer_template_purchase", "purpose": "recontact", "label": "Consultar decisión de compra", "name": "consultar_compra_vehiculo", "language": "es_AR", "body_preview": "Hola {{client_name}}, ¿pudiste revisar la propuesta por el vehículo? Estamos disponibles para ayudarte con los próximos pasos.", "parameter_keys": ["client_name"], "active": True, "sort_order": 1},
        ],
        "appointment_reminders_enabled": True, "appointment_reminder_minutes_before": 1440,
        "appointment_reminder_template_id": "dealer_template_appointment",
        "appointment_reminder_templates": [
            {"id": "dealer_template_appointment", "purpose": "appointment_reminder", "label": "Recordatorio de cita", "name": "recordatorio_cita_autonorte", "language": "es_AR", "body_preview": "Hola {{client_name}}, te recordamos tu cita de {{service_name}} para el {{appointment_date}} a las {{appointment_time}} en {{appointment_location}}. Respondé para confirmar.", "parameter_keys": ["client_name", "service_name", "appointment_date", "appointment_time", "appointment_location"], "active": True, "sort_order": 0},
        ],
        "appointment_rescheduling_enabled": True, "updated_at": iso(now_utc),
        "updated_by": "dealer_user_manager", "is_demo": True,
    }

    app_settings = {
        "key": "app", "lead_no_response_enabled": True, "lead_no_response_threshold_hours": 2,
        "lead_no_response_business_hours_only": True, "business_hours_start": "09:00",
        "business_hours_end": "18:30", "business_days": [0, 1, 2, 3, 4, 5],
        "business_timezone": TZ_NAME,
        "task_statuses": [{"key": "todo", "label": "Pendiente", "is_done": False}, {"key": "in_progress", "label": "En progreso", "is_done": False}, {"key": "done", "label": "Completada", "is_done": True}],
        "catalog_categories": ["0 km", "Usados seleccionados", "Utilitarios"],
        "catalog_category_colors": {"0 km": "#DDECF3", "Usados seleccionados": "#FFF0C7", "Utilitarios": "#DDF2E8"},
        "email_notif_unattended_enabled": True, "email_report_daily_enabled": True,
        "email_report_weekly_enabled": True, "email_report_monthly_enabled": True,
        "updated_at": iso(now_utc), "updated_by": "dealer_user_manager", "is_demo": True,
    }

    return {
        "users": users, "work_areas": work_areas, "tags": tags, "products": products,
        "contacts": contacts, "leads": leads, "conversations": conversations,
        "messages": messages, "notes": notes, "tasks": tasks, "appointments": appointments,
        "sales": sales, "inventory_movements": inventory_movements, "bot_events": bot_events,
        "ai_usage_logs": ai_usage_logs, "bot_settings": bot_settings,
        "app_settings": app_settings,
    }
