"""Datos demostrativos coherentes para una empresa argentina de estetica."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


TZ_NAME = "America/Argentina/Buenos_Aires"
try:
    LOCAL_TZ = ZoneInfo(TZ_NAME)
except Exception:  # Windows puede no incluir la base IANA en instalaciones mínimas.
    LOCAL_TZ = timezone(timedelta(hours=-3), name="Argentina")


def _weekly(start: str = "09:00", end: str = "20:00", *, saturday: bool = True):
    return {
        str(day): ([{"start": start, "end": end}] if day < 5 else
                   ([{"start": "09:00", "end": "14:00"}] if day == 5 and saturday else []))
        for day in range(7)
    }


def build_demo_dataset(now: datetime | None = None) -> dict:
    """Devuelve un escenario completo y enlazado sin tocar la base de datos."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    now_local = now_utc.astimezone(LOCAL_TZ)

    def iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    def ago(*, days: int = 0, hours: int = 0, minutes: int = 0) -> datetime:
        return now_utc - timedelta(days=days, hours=hours, minutes=minutes)

    def business_slot(number: int, hour: int, minute: int = 0, duration: int = 60) -> tuple[str, str]:
        """Numero positivo=futuro, cero=hoy, negativo=dia habil anterior."""
        direction = 1 if number >= 0 else -1
        remaining = abs(number)
        candidate = now_local.date()
        if remaining == 0 and candidate.weekday() == 6:
            candidate += timedelta(days=1)
        while remaining:
            candidate += timedelta(days=direction)
            if candidate.weekday() < 5:
                remaining -= 1
        start = datetime(candidate.year, candidate.month, candidate.day, hour, minute, tzinfo=LOCAL_TZ)
        return iso(start), iso(start + timedelta(minutes=duration))

    full_week = _weekly()
    short_week = _weekly("10:00", "18:00")
    afternoon_week = _weekly("12:00", "20:00", saturday=False)
    created = iso(ago(days=160))

    work_areas = [
        {"id": "area_recepcion", "name": "Recepción y turnos", "description": "Consultas, reservas, cambios y confirmaciones.", "routing_rules": "Derivar aquí pedidos de turnos, horarios, precios de servicios y reprogramaciones.", "created_at": created, "is_demo": True},
        {"id": "area_cabinas", "name": "Tratamientos en cabina", "description": "Seguimiento de tratamientos faciales y corporales.", "routing_rules": "Derivar consultas técnicas sobre tratamientos, contraindicaciones o planes personalizados.", "created_at": created, "is_demo": True},
        {"id": "area_ventas", "name": "Venta de productos", "description": "Asesoramiento, promociones, pagos y entregas.", "routing_rules": "Derivar consultas de catálogo, disponibilidad, promociones, envíos y compras.", "created_at": created, "is_demo": True},
    ]

    users = [
        {"user_id": "user_demo_sup", "email": "valentina@auraestetica.com.ar", "name": "Valentina Ríos", "role": "supervisor", "picture": "https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?crop=faces&fit=crop&w=200&h=200", "work_areas": ["area_recepcion", "area_cabinas", "area_ventas"], "calendar_settings": {"enabled": True, "timezone": TZ_NAME, "default_duration_minutes": 45, "buffer_minutes": 15, "weekly_schedule": full_week}},
        {"user_id": "user_demo_a1", "email": "camila@auraestetica.com.ar", "name": "Camila Fernández", "role": "sales_agent", "picture": "https://images.unsplash.com/photo-1494790108377-be9c29b29330?crop=faces&fit=crop&w=200&h=200", "work_areas": ["area_recepcion", "area_ventas"], "calendar_settings": {"enabled": True, "timezone": TZ_NAME, "default_duration_minutes": 45, "buffer_minutes": 10, "weekly_schedule": full_week}},
        {"user_id": "user_demo_a2", "email": "luciana@auraestetica.com.ar", "name": "Luciana Gómez", "role": "sales_agent", "picture": "https://images.unsplash.com/photo-1580489944761-15a19d654956?crop=faces&fit=crop&w=200&h=200", "work_areas": ["area_cabinas"], "calendar_settings": {"enabled": True, "timezone": TZ_NAME, "default_duration_minutes": 60, "buffer_minutes": 15, "weekly_schedule": short_week}},
        {"user_id": "user_demo_a3", "email": "sofia@auraestetica.com.ar", "name": "Sofía Martínez", "role": "sales_agent", "picture": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?crop=faces&fit=crop&w=200&h=200", "work_areas": ["area_cabinas", "area_ventas"], "calendar_settings": {"enabled": True, "timezone": TZ_NAME, "default_duration_minutes": 60, "buffer_minutes": 15, "weekly_schedule": afternoon_week}},
    ]
    for user in users:
        user.update({"active": True, "is_demo": True, "auth_provider": "local", "created_at": created, "updated_at": iso(now_utc)})

    tags = [
        {"id": "tag_turno", "name": "Turno solicitado", "color": "#0E8DDB", "is_demo": True},
        {"id": "tag_frecuente", "name": "Cliente frecuente", "color": "#7C3AED", "is_demo": True},
        {"id": "tag_productos", "name": "Venta de productos", "color": "#059669", "is_demo": True},
        {"id": "tag_depilacion", "name": "Consulta depilación", "color": "#DB2777", "is_demo": True},
        {"id": "tag_seguimiento", "name": "Seguimiento", "color": "#D97706", "is_demo": True},
        {"id": "tag_prioridad", "name": "Prioridad alta", "color": "#DC2626", "is_demo": True},
    ]

    product_rows = [
        ("prod_serum_c", "Sérum facial con vitamina C 30 ml", "AUR-SER-C30", "Cuidado facial", 28900, "Fórmula antioxidante para iluminar y unificar el tono.", "disponible", ["Vitamina C", "Rostro"]),
        ("prod_crema_hialuronico", "Crema hidratante con ácido hialurónico", "AUR-CRE-AH50", "Cuidado facial", 24500, "Hidratación diaria de textura liviana, apta para todo tipo de piel.", "disponible", ["Hidratación", "Rostro"]),
        ("prod_protector_50", "Protector solar facial FPS 50", "AUR-PS-F50", "Protección solar", 21900, "Protección de amplio espectro con acabado seco.", "disponible", ["Protección", "Rostro"]),
        ("prod_gel_limpieza", "Gel de limpieza facial suave", "AUR-GEL-L250", "Cuidado facial", 15900, "Limpia sin resecar y ayuda a mantener la barrera natural.", "disponible", ["Limpieza", "Rostro"]),
        ("prod_agua_micelar", "Agua micelar 400 ml", "AUR-AGU-M400", "Cuidado facial", 13500, "Desmaquilla y limpia rostro, ojos y labios.", "disponible", ["Limpieza", "Desmaquillante"]),
        ("prod_mascara", "Máscara facial descongestiva", "AUR-MAS-D100", "Tratamientos en casa", 9800, "Calma e hidrata pieles sensibles o expuestas al sol.", "disponible", ["Calmante", "Rostro"]),
        ("prod_cuticulas", "Aceite nutritivo para cutículas", "AUR-ACE-C15", "Manos y uñas", 8600, "Nutre cutículas y mejora el aspecto de uñas secas.", "disponible", ["Manos", "Uñas"]),
        ("prod_esmalte_nude", "Esmalte semipermanente tono nude", "AUR-ESM-N15", "Manos y uñas", 12400, "Color neutro de larga duración para uso profesional.", "disponible", ["Manos", "Color"]),
        ("prod_kit_facial", "Kit de rutina facial completa", "AUR-KIT-RF", "Combos", 74900, "Gel de limpieza, sérum con vitamina C, hidratante y protector solar.", "disponible", ["Combo", "Rutina facial"]),
        ("prod_kit_manos", "Kit de cuidado de manos", "AUR-KIT-MA", "Combos", 26500, "Crema de manos, aceite para cutículas y lima profesional.", "disponible", ["Combo", "Manos"]),
        ("prod_bruma", "Bruma facial hidratante", "AUR-BRU-H120", "Cuidado facial", 18700, "Refresca e hidrata durante el día sin alterar el maquillaje.", "consultar", ["Hidratación", "Rostro"]),
        ("prod_ampollas", "Ampollas reafirmantes por 5 unidades", "AUR-AMP-R5", "Tratamientos en casa", 32000, "Tratamiento concentrado para complementar la rutina semanal.", "sin_stock", ["Reafirmante", "Rostro"]),
    ]
    product_images = [
        "photo-1620916566398-39f1143ab7be", "photo-1556228578-8c89e6adf883", "photo-1556229010-6c3f2c9ca5f8",
        "photo-1608248543803-ba4f8c70ae0b", "photo-1571781926291-c477ebfd024b", "photo-1598440947619-2c35fc9aa908",
        "photo-1612817288484-6f916006741a", "photo-1632345031435-8727f6897d53", "photo-1522335789203-aabd1fc54bc9",
        "photo-1596462502278-27bfdc403348", "photo-1611930022073-b7a4ba5fcccd", "photo-1570194065650-d99fb4bedf0a",
    ]
    products = []
    for row, image_id in zip(product_rows, product_images):
        pid, name, sku, category, price, description, stock, product_tags = row
        products.append({
            "product_id": pid, "name": name, "sku": sku, "category": category,
            "description": description, "price": price, "currency": "ARS",
            "stock_status": stock, "active": True, "tags": product_tags,
            "image_url": f"https://images.unsplash.com/{image_id}?fit=crop&w=900&h=900&q=80",
            "promo_price": None, "promo_limit_type": "none", "promo_start_at": None,
            "promo_end_at": None, "promo_unit_limit": None, "promo_units_used": 0,
            "commercial_conditions": "Precio final. Hasta 3 cuotas sin interés. Envíos en CABA y retiro por el local.",
            "external_link": None, "created_at": created, "updated_at": iso(now_utc),
            "deleted_at": None, "is_demo": True,
        })
    by_product = {item["product_id"]: item for item in products}
    by_product["prod_kit_facial"].update({
        "promo_price": 64900, "promo_limit_type": "date",
        "promo_start_at": iso(ago(days=30)), "promo_end_at": iso(now_utc + timedelta(days=12)),
        "commercial_conditions": "Promoción vigente hasta la fecha indicada o hasta agotar existencias.",
    })
    by_product["prod_kit_manos"].update({
        "promo_price": 22900, "promo_limit_type": "units", "promo_unit_limit": 40,
        "promo_units_used": 11, "commercial_conditions": "Precio promocional para las primeras 40 unidades.",
    })

    people = [
        ("Martina López", "+54 9 11 3842-7710", "martina.lopez@gmail.com", "Palermo, CABA", "Instagram", ["Turno solicitado", "Cliente frecuente"]),
        ("Julieta Ferraro", "+54 9 11 6124-9035", "julieta.ferraro@gmail.com", "Belgrano, CABA", "WhatsApp", ["Consulta depilación"]),
        ("Rocío Benítez", "+54 9 11 4755-1182", "rocio.benitez@gmail.com", "Villa Urquiza, CABA", "Recomendación", ["Turno solicitado", "Prioridad alta"]),
        ("Agustina Pereyra", "+54 9 11 3390-6641", "aguspereyra@gmail.com", "Caballito, CABA", "Instagram", ["Venta de productos"]),
        ("Florencia Acosta", "+54 9 11 5901-2478", "flor.acosta@gmail.com", "Almagro, CABA", "Sitio web", ["Turno solicitado"]),
        ("Natalia Suárez", "+54 9 11 4418-0932", "natalia.suarez@gmail.com", "Recoleta, CABA", "WhatsApp", ["Prioridad alta", "Seguimiento"]),
        ("Carolina Méndez", "+54 9 341 612-4470", "caro.mendez@gmail.com", "Rosario, Santa Fe", "Instagram", ["Venta de productos", "Cliente frecuente"]),
        ("Belén Romero", "+54 9 351 703-8821", "belen.romero@gmail.com", "Nueva Córdoba, Córdoba", "Sitio web", ["Seguimiento"]),
        ("Paula Sosa", "+54 9 11 3766-5214", "paula.sosa@gmail.com", "San Telmo, CABA", "Recomendación", ["Turno solicitado"]),
        ("Micaela Torres", "+54 9 11 6812-3349", "mica.torres@gmail.com", "Núñez, CABA", "Instagram", ["Venta de productos", "Prioridad alta"]),
        ("Verónica Ruiz", "+54 9 221 507-1196", "veronica.ruiz@gmail.com", "La Plata, Buenos Aires", "WhatsApp", ["Cliente frecuente"]),
        ("Daniela Cabrera", "+54 9 11 4087-6502", "daniela.cabrera@gmail.com", "Boedo, CABA", "Instagram", ["Consulta depilación", "Turno solicitado"]),
    ]

    scenarios = [
        {"title": "Limpieza facial y rutina para el hogar", "status": "proposal", "priority": "high", "value": 109900, "assigned": "user_demo_a1", "area": "area_recepcion", "conv": "open", "bot": False, "days": 0, "hours": 2, "products": [("prod_kit_facial", 1)], "messages": [("contact", "Hola, vi la limpieza facial profunda en Instagram. ¿Tienen lugar esta semana?"), ("bot", "¡Hola, Martina! Sí, puedo ayudarte con el turno. ¿Preferís por la mañana o por la tarde?"), ("contact", "Por la tarde. También quería llevarme algo para seguir la rutina en casa."), ("agent", "Hola, soy Camila. Te recomiendo el kit de rutina facial; esta semana está a $64.900. Tengo un turno el martes a las 17:00."), ("contact", "Buenísimo, reservámelo. ¿Puedo pagar con tarjeta?")]},
        {"title": "Plan de depilación definitiva", "status": "contacted", "priority": "medium", "value": 55000, "assigned": "user_demo_a2", "area": "area_cabinas", "conv": "pending", "bot": True, "days": 0, "hours": 5, "products": [], "messages": [("contact", "Quería consultar cuánto sale depilación definitiva de axilas y cavado."), ("bot", "¡Hola, Julieta! La sesión combinada tiene un valor de $55.000. Antes de reservar te hacemos unas preguntas breves para confirmar que el tratamiento sea adecuado."), ("contact", "Perfecto, nunca me hice láser. ¿Duele?"), ("bot", "La sensación suele ser tolerable y regulamos la intensidad según tu piel. Una profesional puede explicarte todo antes de confirmar."), ("contact", "Dale, lo pienso y mañana les escribo.")]},
        {"title": "Preparación para casamiento", "status": "qualified", "priority": "high", "value": 98500, "assigned": "user_demo_sup", "area": "area_recepcion", "conv": "open", "bot": False, "days": 1, "hours": 1, "products": [("prod_kit_manos", 1)], "messages": [("contact", "Me caso el mes que viene y quiero organizar limpieza facial, lifting de pestañas y manos."), ("bot", "¡Felicitaciones, Rocío! Podemos armarte un plan para llegar con tiempo a cada servicio."), ("agent", "Hola, soy Valentina. Te propongo una limpieza dos semanas antes y pestañas más manos durante la semana del evento."), ("contact", "Me encanta. ¿Me mandás el detalle con los valores?")]},
        {"title": "Compra de sérum y protector solar", "status": "won", "priority": "medium", "value": 50800, "assigned": "user_demo_a3", "area": "area_ventas", "conv": "resolved", "bot": True, "days": 8, "hours": 2, "products": [("prod_serum_c", 1), ("prod_protector_50", 1)], "messages": [("contact", "¿Tienen sérum con vitamina C y protector para piel mixta?"), ("bot", "Sí, tenemos ambos disponibles. El sérum cuesta $28.900 y el protector FPS 50 cuesta $21.900."), ("contact", "Los llevo. ¿Puedo retirar hoy?"), ("bot", "Sí, podés retirar de 9:00 a 20:00. Te reservamos el pedido a tu nombre."), ("contact", "Ya retiré, muchas gracias.")]},
        {"title": "Consulta por piel con acné", "status": "new", "priority": "medium", "value": 45000, "assigned": None, "area": "area_cabinas", "conv": "open", "bot": True, "days": 0, "hours": 3, "products": [], "messages": [("contact", "Hola, tengo brotes de acné y quería saber qué tratamiento me recomiendan."), ("bot", "¡Hola, Florencia! Para recomendarte de forma responsable primero conviene hacer una evaluación de la piel."), ("contact", "¿Cuánto cuesta y cuánto dura la consulta?")]},
        {"title": "Turno urgente antes de un evento", "status": "qualified", "priority": "high", "value": 72500, "assigned": None, "area": "area_recepcion", "conv": "pending", "bot": False, "days": 0, "hours": 4, "products": [], "messages": [("contact", "Tengo un evento el sábado. ¿Hay lugar para limpieza facial y lifting de pestañas?"), ("bot", "Voy a revisar la disponibilidad de ambos servicios para darte una opción que llegue bien al evento."), ("contact", "Si puede ser después de las 18 sería ideal."), ("bot", "Necesito que una persona del equipo confirme la combinación y los tiempos. Ya derivé tu consulta.")]},
        {"title": "Kit facial con envío a Rosario", "status": "won", "priority": "medium", "value": 78400, "assigned": "user_demo_a3", "area": "area_ventas", "conv": "resolved", "bot": True, "days": 21, "hours": 2, "products": [("prod_kit_facial", 1), ("prod_agua_micelar", 1)], "messages": [("contact", "Quiero reponer el kit facial y sumar agua micelar. ¿Hacen envío a Rosario?"), ("bot", "Sí, enviamos a Rosario. El total de los productos es $78.400 y el despacho se realiza dentro de las 48 horas hábiles."), ("contact", "Perfecto, ya hice la transferencia."), ("agent", "Pago recibido, Carolina. Mañana te enviamos el código de seguimiento.")]},
        {"title": "Consulta por masaje descontracturante", "status": "lost", "priority": "low", "value": 42000, "assigned": "user_demo_a1", "area": "area_recepcion", "conv": "resolved", "bot": False, "days": 35, "hours": 1, "products": [], "messages": [("contact", "¿Tienen masajes los domingos?"), ("agent", "Hola, Belén. Atendemos de lunes a sábado; el sábado tenemos horarios hasta las 14:00."), ("contact", "Gracias, necesitaba sí o sí domingo. Conseguí otro lugar.")]},
        {"title": "Voucher para masaje", "status": "qualified", "priority": "medium", "value": 48000, "assigned": "user_demo_a1", "area": "area_ventas", "conv": "open", "bot": True, "days": 2, "hours": 4, "products": [], "messages": [("contact", "Quiero regalarle un masaje a mi mamá. ¿Tienen voucher digital?"), ("bot", "Sí, preparamos un voucher digital personalizado. El masaje descontracturante de 60 minutos cuesta $48.000."), ("contact", "Buenísimo. ¿Tiene vencimiento?"), ("bot", "Tiene una vigencia de 90 días desde la compra."), ("contact", "Dale, esta tarde confirmo el pago.")]},
        {"title": "Productos para estudio de maquillaje", "status": "proposal", "priority": "high", "value": 249600, "assigned": "user_demo_sup", "area": "area_ventas", "conv": "pending", "bot": False, "days": 1, "hours": 6, "products": [("prod_kit_manos", 6), ("prod_bruma", 6)], "messages": [("contact", "Tengo un estudio de maquillaje y necesito seis kits de manos y seis brumas. ¿Hacen precio por cantidad?"), ("bot", "Tenemos disponibilidad para preparar una cotización por volumen. Te comunico con ventas."), ("agent", "Hola, Micaela. Podemos respetarte la promoción de los kits y hacer la entrega sin cargo en CABA. El total queda en $249.600."), ("contact", "Me sirve. Mandame la cotización y la reviso con mi socia.")]},
        {"title": "Reposición de hidratante y limpieza", "status": "won", "priority": "low", "value": 40400, "assigned": "user_demo_a3", "area": "area_ventas", "conv": "resolved", "bot": True, "days": 52, "hours": 1, "products": [("prod_crema_hialuronico", 1), ("prod_gel_limpieza", 1)], "messages": [("contact", "Hola, quiero volver a pedir la crema hidratante y el gel de limpieza."), ("bot", "¡Hola, Verónica! Ambos están disponibles. El total es $40.400."), ("contact", "Perfecto, los retiro mañana."), ("agent", "Quedaron reservados a tu nombre. ¡Te esperamos!")]},
        {"title": "Depilación definitiva de piernas", "status": "contacted", "priority": "medium", "value": 68000, "assigned": "user_demo_a2", "area": "area_cabinas", "conv": "open", "bot": True, "days": 2, "hours": 1, "products": [], "messages": [("contact", "Vi el anuncio de depilación definitiva. ¿Cuánto está piernas completas?"), ("bot", "¡Hola, Daniela! La sesión de piernas completas cuesta $68.000. Tenemos turnos de lunes a sábado."), ("contact", "¿Puedo hacerlo si estoy tomando medicación?"), ("bot", "Para cuidarte, ese punto debe revisarlo una profesional antes de agendar. Te derivo con Luciana.")]},
    ]

    contacts, leads, conversations, messages, notes = [], [], [], [], []
    agents = {user["user_id"]: user["name"] for user in users}

    def sale_lines(items):
        lines = []
        for product_id, quantity in items:
            product = by_product[product_id]
            unit = product.get("promo_price") if product_id == "prod_kit_facial" else product["price"]
            lines.append({"id": product_id, "name": product["name"], "price": unit, "unit_price": unit,
                          "quantity": quantity, "line_total": unit * quantity, "currency": "ARS",
                          "list_price": product["price"], "promotion_applied": unit != product["price"],
                          "promotion_limit_type": product.get("promo_limit_type") or "none"})
        return lines

    for index, (person, scenario) in enumerate(zip(people, scenarios), 1):
        name, phone, email, location, source, person_tags = person
        cid, lid, conv_id = f"contact_demo_{index:02d}", f"lead_demo_{index:02d}", f"conv_demo_{index:02d}"
        opened = ago(days=scenario["days"] + 2, hours=scenario["hours"])
        contacts.append({
            "id": cid, "name": name, "phone": phone, "whatsapp_id": "".join(ch for ch in phone if ch.isdigit()),
            "email": email, "company": location, "avatar": None, "tags": person_tags,
            "notes": f"Contacto de demostración. Origen: {source}.", "lead_source": source,
            "created_at": iso(opened), "is_demo": True,
            **({"meta_ad_id": f"anuncio_estetica_{index}", "meta_source_type": "anuncio de WhatsApp",
                "meta_ad_title": "Conocé nuestros tratamientos de estética",
                "meta_ad_body": "Reservá una evaluación personalizada en Aura Estética y Belleza.",
                "first_message_from_ad": scenario["messages"][0][1], "first_ad_message_at": iso(opened)}
               if source == "Instagram" else {}),
        })
        lead_products = [{"id": pid, "name": by_product[pid]["name"],
                          "price": by_product[pid].get("promo_price") or by_product[pid]["price"],
                          "quantity": qty, "currency": "ARS", "list_price": by_product[pid]["price"],
                          "promotion_applied": by_product[pid].get("promo_price") is not None}
                         for pid, qty in scenario["products"]]
        lead = {"id": lid, "contact_id": cid, "title": scenario["title"], "status": scenario["status"],
                "priority": scenario["priority"], "value": scenario["value"], "assigned_to": scenario["assigned"],
                "source": source, "tags": person_tags, "products": lead_products,
                "created_at": iso(opened), "updated_at": iso(ago(days=scenario["days"])), "is_demo": True}
        if scenario["status"] in ("won", "lost"):
            closed_at = ago(days=scenario["days"])
            lead.update({"closed_at": iso(closed_at), "closed_by": scenario["assigned"], "closed_value": scenario["value"]})
            if scenario["status"] == "won":
                lines = sale_lines(scenario["products"])
                lead["sale_snapshot"] = {"closed_at": iso(closed_at), "closed_by": scenario["assigned"],
                                         "products": lines, "total": scenario["value"], "currency": "ARS"}
        leads.append(lead)

        base = ago(days=scenario["days"], hours=scenario["hours"])
        conversation_messages = []
        for msg_index, (sender_type, body) in enumerate(scenario["messages"], 1):
            msg_time = base + timedelta(minutes=(msg_index - 1) * 8)
            sender_name = name if sender_type == "contact" else ("Asistente virtual de Aura" if sender_type == "bot" else agents.get(scenario["assigned"], "Equipo de Aura"))
            message = {"id": f"msg_demo_{index:02d}_{msg_index:02d}", "conversation_id": conv_id,
                       "sender_type": sender_type, "sender_name": sender_name, "body": body,
                       "created_at": iso(msg_time), "direction": "inbound" if sender_type == "contact" else "outbound",
                       "delivery_status": "read" if sender_type != "contact" else "received",
                       "message_type": "text", "channel": "whatsapp", "is_demo": True}
            conversation_messages.append(message)
        messages.extend(conversation_messages)
        last = conversation_messages[-1]
        conversations.append({
            "id": conv_id, "contact_id": cid, "lead_id": lid, "status": scenario["conv"],
            "priority": scenario["priority"], "bot_enabled": scenario["bot"], "assigned_to": scenario["assigned"],
            "assigned_work_area": scenario["area"], "last_message": last["body"], "last_message_at": last["created_at"],
            "unread": 1 if last["sender_type"] == "contact" and scenario["conv"] != "resolved" else 0,
            "bot_status": "bot_activo" if scenario["bot"] else ("requiere_humano" if scenario["assigned"] is None else "en_atencion_humana"),
            "channel": "whatsapp", "channel_external_id": f"demo:{contacts[-1]['whatsapp_id']}",
            "created_at": iso(opened), "is_demo": True,
        })
        if index in (1, 3, 5, 6, 10, 12):
            note_bodies = {
                1: "Prefiere turnos después de las 16:00. Interesada en sostener una rutina facial simple.",
                3: "Evento importante. Preparar propuesta con fechas recomendadas y reserva coordinada.",
                5: "No recomendar productos agresivos sin evaluación previa de la profesional.",
                6: "Consulta urgente. Confirmar primero disponibilidad conjunta de ambos servicios.",
                10: "Potencial cuenta profesional. Ofrecer condiciones por volumen y recompra mensual.",
                12: "Revisar medicación y antecedentes antes de confirmar la sesión.",
            }
            notes.append({"id": f"note_demo_{index:02d}", "lead_id": lid, "body": note_bodies[index],
                          "author_id": "user_demo_sup", "author_name": "Valentina Ríos",
                          "created_at": iso(ago(days=max(0, scenario["days"] - 1))), "is_demo": True})

    tasks = [
        {"id": "task_demo_01", "title": "Enviar propuesta de preparación para casamiento", "description": "Detallar servicios, fechas recomendadas y formas de pago.", "lead_id": "lead_demo_03", "due": 1, "status": "todo", "priority": "high", "assigned_to": "user_demo_sup"},
        {"id": "task_demo_02", "title": "Confirmar disponibilidad para Natalia", "description": "Coordinar limpieza facial y lifting antes del evento.", "lead_id": "lead_demo_06", "due": 0, "status": "in_progress", "priority": "high", "assigned_to": None},
        {"id": "task_demo_03", "title": "Enviar cotización por cantidad", "description": "Aplicar descuento profesional y detallar la entrega en CABA.", "lead_id": "lead_demo_10", "due": 1, "status": "todo", "priority": "high", "assigned_to": "user_demo_sup"},
        {"id": "task_demo_04", "title": "Contactar a Florencia para evaluación", "description": "Ofrecer horarios para una evaluación de piel.", "lead_id": "lead_demo_05", "due": -1, "status": "todo", "priority": "medium", "assigned_to": None},
        {"id": "task_demo_05", "title": "Verificar pago del voucher", "description": "Si se acredita, emitir el voucher personalizado.", "lead_id": "lead_demo_09", "due": 2, "status": "todo", "priority": "medium", "assigned_to": "user_demo_a1"},
        {"id": "task_demo_06", "title": "Controlar existencias de bruma facial", "description": "Confirmar reposición antes de cerrar la cotización profesional.", "lead_id": "lead_demo_10", "due": 0, "status": "in_progress", "priority": "medium", "assigned_to": "user_demo_a3"},
        {"id": "task_demo_07", "title": "Revisión semanal de oportunidades", "description": "Revisar consultas abiertas, turnos pendientes y ventas sin respuesta.", "lead_id": None, "due": 3, "status": "todo", "priority": "medium", "assigned_to": "user_demo_sup"},
        {"id": "task_demo_08", "title": "Actualizar fotos del catálogo", "description": "Revisar imágenes de los productos con mayor rotación.", "lead_id": None, "due": -3, "status": "done", "priority": "low", "assigned_to": "user_demo_a3"},
    ]
    for task in tasks:
        task["due_date"] = (now_local.date() + timedelta(days=task.pop("due"))).isoformat()
        task.update({"created_at": iso(ago(days=5)), "is_demo": True})

    appointment_rows = [
        ("appt_demo_01", 1, "Limpieza facial profunda", "limpieza_facial", "user_demo_a2", 17, 0, 75, "scheduled", True, "pending"),
        ("appt_demo_02", 2, "Depilación definitiva · axilas y cavado", "depilacion_definitiva", "user_demo_a2", 11, 0, 45, "scheduled", True, "confirmed"),
        ("appt_demo_03", 3, "Lifting de pestañas", "lifting_pestanas", "user_demo_a3", 16, 0, 60, "scheduled", False, "pending"),
        ("appt_demo_04", 5, "Evaluación personalizada de la piel", "evaluacion_piel", "user_demo_a2", 10, 0, 30, "scheduled", True, "pending"),
        ("appt_demo_05", 12, "Evaluación para depilación definitiva", "evaluacion_piel", "user_demo_a2", 18, 0, 30, "scheduled", True, "pending"),
        ("appt_demo_06", -1, "Masaje descontracturante", "masaje_descontracturante", "user_demo_a3", 13, 0, 60, "completed", False, "confirmed"),
        ("appt_demo_07", -3, "Manicura semipermanente", "manicura_semipermanente", "user_demo_a3", 15, 30, 75, "completed", False, "confirmed"),
        ("appt_demo_08", 4, "Capacitación interna de productos", None, "user_demo_sup", 9, 0, 60, "scheduled", False, None),
    ]
    services_by_id = {
        "limpieza_facial": "Limpieza facial profunda", "depilacion_definitiva": "Depilación definitiva",
        "lifting_pestanas": "Perfilado y lifting de pestañas", "evaluacion_piel": "Evaluación personalizada de la piel",
        "masaje_descontracturante": "Masaje descontracturante", "manicura_semipermanente": "Manicura semipermanente",
    }
    appointments = []
    for pos, row in enumerate(appointment_rows):
        appt_id, day_no, title, service_id, assigned, hour, minute, duration, status, by_bot, confirmation = row
        start_time, end_time = business_slot(day_no, hour, minute, duration)
        contact_index = [1, 2, 3, 5, 12, 11, 4, None][pos]
        contact_id = f"contact_demo_{contact_index:02d}" if contact_index else None
        lead_id = f"lead_demo_{contact_index:02d}" if contact_index else None
        conv_id = f"conv_demo_{contact_index:02d}" if contact_index else None
        start_dt = datetime.fromisoformat(start_time)
        is_event = service_id is None
        appointments.append({
            "id": appt_id, "contact_id": contact_id, "lead_id": lead_id, "conversation_id": conv_id,
            "title": title, "description": ("Reunión del equipo para repasar lanzamientos y recomendaciones." if is_event else "Turno de demostración cargado para recorrer la agenda."),
            "location": "Aura Estética y Belleza · Av. Santa Fe 3253, Palermo",
            "event_type": "event" if is_event else "appointment", "start_time": start_time, "end_time": end_time,
            "status": status, "assigned_to": assigned, "scheduling_mode": "people" if is_event else "business",
            "service_id": service_id, "service_name": services_by_id.get(service_id),
            "reminder_enabled": bool(service_id and status == "scheduled"), "reminder_minutes_before": 1440 if service_id else None,
            "reminder_template_id": "recordatorio_turno_aura" if service_id else None,
            "reminder_due_at": iso(start_dt - timedelta(days=1)) if service_id and status == "scheduled" else None,
            "reminder_status": "pending" if service_id and status == "scheduled" else None,
            "confirmation_status": confirmation, "created_by_bot": by_bot,
            "created_by": "bot" if by_bot else assigned, "created_by_name": "Asistente virtual de Aura" if by_bot else agents.get(assigned),
            "created_at": iso(ago(days=4)), "updated_at": iso(ago(days=1)), "is_demo": True,
        })

    services = [
        {"id": "evaluacion_piel", "name": "Evaluación personalizada de la piel", "description": "Diagnóstico inicial y recomendación de rutina o tratamiento.", "active": True, "duration_minutes": 30, "max_concurrent": 1, "timezone": TZ_NAME, "weekly_schedule": full_week, "sort_order": 0},
        {"id": "limpieza_facial", "name": "Limpieza facial profunda", "description": "Higiene, exfoliación, extracciones y máscara según el tipo de piel.", "active": True, "duration_minutes": 75, "max_concurrent": 2, "timezone": TZ_NAME, "weekly_schedule": full_week, "sort_order": 1},
        {"id": "depilacion_definitiva", "name": "Depilación definitiva", "description": "Sesión láser con evaluación previa y parámetros personalizados.", "active": True, "duration_minutes": 45, "max_concurrent": 2, "timezone": TZ_NAME, "weekly_schedule": full_week, "sort_order": 2},
        {"id": "lifting_pestanas", "name": "Perfilado y lifting de pestañas", "description": "Diseño de cejas y realce de pestañas naturales.", "active": True, "duration_minutes": 60, "max_concurrent": 1, "timezone": TZ_NAME, "weekly_schedule": full_week, "sort_order": 3},
        {"id": "masaje_descontracturante", "name": "Masaje descontracturante", "description": "Sesión corporal de 60 minutos.", "active": True, "duration_minutes": 60, "max_concurrent": 1, "timezone": TZ_NAME, "weekly_schedule": short_week, "sort_order": 4},
        {"id": "manicura_semipermanente", "name": "Manicura semipermanente", "description": "Preparación, esmaltado y cuidado de cutículas.", "active": True, "duration_minutes": 75, "max_concurrent": 2, "timezone": TZ_NAME, "weekly_schedule": full_week, "sort_order": 5},
    ]

    bot_settings = {
        "_id": "default", "bot_enabled_default": True, "confidence_threshold": 0.72,
        "recent_messages_context_max": 14, "provider": "built_in", "model": "gpt-4o-mini",
        "bot_name": "Asistente de Aura", "tone": "profesional, cálido, claro y cercano",
        "include_client_info": True, "default_handoff_user_id": "user_demo_sup",
        "company_context": "Aura Estética y Belleza es un centro de estética integral y tienda de productos de belleza ubicado en Palermo, CABA. Atiende de lunes a viernes de 9:00 a 20:00 y los sábados de 9:00 a 14:00. Ofrece tratamientos faciales, depilación definitiva, pestañas, manicura, masajes y asesoramiento de cuidado personal.",
        "business_instructions": "Informar siempre precios en pesos argentinos. Consultar disponibilidad real antes de prometer un turno. No dar diagnósticos médicos. Derivar a una profesional ante medicación, embarazo, alergias, contraindicaciones o reacciones adversas.",
        "response_instructions": "Responder en español rioplatense, con mensajes breves y amables. No inventar existencias, promociones ni horarios. Para una compra, confirmar producto, cantidad, modalidad de entrega y forma de pago.",
        "handoff_rules": "Derivar a una persona cuando exista una contraindicación, una queja, una solicitud de devolución, una negociación especial, datos sensibles de pago o cuando la confianza sea baja.",
        "faqs": [
            {"question": "¿Dónde están?", "answer": "Estamos en Av. Santa Fe 3253, Palermo, CABA."},
            {"question": "¿Qué medios de pago aceptan?", "answer": "Efectivo, transferencia, débito y crédito. Hay hasta 3 cuotas sin interés en productos seleccionados."},
            {"question": "¿Hacen envíos?", "answer": "Sí, realizamos envíos en CABA y al resto del país. También podés retirar por el local."},
            {"question": "¿Puedo reprogramar un turno?", "answer": "Sí, podés solicitar el cambio por WhatsApp. Está sujeto a disponibilidad."},
        ],
        "catalog_reading_enabled": True, "bot_inactive_close_hours": 12,
        "appointment_scheduling_enabled": True, "appointment_available_days": [1, 2, 3, 4, 5, 6],
        "appointment_business_hours": "09:00-20:00", "appointment_duration_minutes": 60,
        "appointment_mode": "business", "appointment_timezone": TZ_NAME, "appointment_services": services,
        "whatsapp_recontact_templates": [
            {"id": "recontacto_aura", "purpose": "recontact", "label": "Retomar consulta", "name": "retomar_consulta_aura", "language": "es_AR", "body_preview": "Hola {{client_name}}, somos Aura Estética y Belleza. Queríamos saber si necesitás ayuda para continuar con tu consulta.", "parameter_keys": ["client_name"], "active": True, "sort_order": 0},
            {"id": "consulta_compra_producto", "purpose": "recontact", "label": "Consultar compra de producto", "name": "consulta_compra_producto_aura", "language": "es_AR", "body_preview": "Hola {{client_name}}, ¿pudiste revisar la información del producto? Si querés, te ayudamos a confirmar disponibilidad y entrega.", "parameter_keys": ["client_name"], "active": True, "sort_order": 1},
        ],
        "appointment_reminders_enabled": True, "appointment_reminder_minutes_before": 1440,
        "appointment_reminder_template_id": "recordatorio_turno_aura",
        "appointment_reminder_templates": [
            {"id": "recordatorio_turno_aura", "purpose": "appointment_reminder", "label": "Recordatorio de turno", "name": "recordatorio_turno_aura", "language": "es_AR", "body_preview": "Hola {{client_name}}, te recordamos tu turno de {{service_name}} para el {{appointment_date}} a las {{appointment_time}} en {{appointment_location}}. Por favor, respondé para confirmar.", "parameter_keys": ["client_name", "service_name", "appointment_date", "appointment_time", "appointment_location"], "active": True, "sort_order": 0},
        ],
        "appointment_rescheduling_enabled": True, "updated_at": iso(now_utc), "updated_by": "user_demo_sup", "is_demo": True,
    }

    app_settings = {
        "key": "app", "lead_no_response_enabled": True, "lead_no_response_threshold_hours": 2,
        "lead_no_response_business_hours_only": True, "business_hours_start": "09:00",
        "business_hours_end": "20:00", "business_days": [0, 1, 2, 3, 4, 5],
        "business_timezone": TZ_NAME,
        "task_statuses": [{"key": "todo", "label": "Pendiente", "is_done": False}, {"key": "in_progress", "label": "En progreso", "is_done": False}, {"key": "done", "label": "Completada", "is_done": True}],
        "catalog_categories": ["Cuidado facial", "Protección solar", "Tratamientos en casa", "Manos y uñas", "Combos"],
        "catalog_category_colors": {"Cuidado facial": "#DDECF3", "Protección solar": "#FFF0C7", "Tratamientos en casa": "#E9E0F5", "Manos y uñas": "#F7DDE7", "Combos": "#DDF2E8"},
        "email_notif_unattended_enabled": True, "email_report_daily_enabled": True,
        "email_report_weekly_enabled": True, "email_report_monthly_enabled": True,
        "updated_at": iso(now_utc), "updated_by": "user_demo_sup", "is_demo": True,
    }

    ai_usage_logs = []
    purposes = ["bot_pipeline", "bot_pipeline", "bot_pipeline", "suggest_reply", "summary_regen"]
    for index in range(36):
        prompt_tokens = 620 + (index * 47) % 1100
        completion_tokens = 95 + (index * 29) % 310
        status = "error" if index in (17, 31) else "success"
        conversation_id = f"conv_demo_{(index % 12) + 1:02d}"
        ai_usage_logs.append({
            "log_id": f"usage_demo_{index + 1:03d}", "created_at": iso(ago(days=index % 15, hours=(index * 3) % 20)),
            "provider": "built_in", "model": "gpt-4o-mini",
            "prompt_tokens": prompt_tokens if status == "success" else 0,
            "completion_tokens": completion_tokens if status == "success" else 0,
            "total_tokens": prompt_tokens + completion_tokens if status == "success" else 0,
            "estimated_cost_usd": round((prompt_tokens * 0.15 + completion_tokens * 0.60) / 1_000_000, 6) if status == "success" else 0,
            "provider_cost_usd": None, "cost_source": "estimated", "token_source": "provider_response" if status == "success" else "unavailable",
            "provider_request_id": f"solicitud_demo_{index + 1:03d}" if status == "success" else None,
            "latency_ms": 780 + (index * 83) % 1800, "status": status,
            "error_message": "Tiempo de espera agotado; el mensaje quedó disponible para reintentar." if status == "error" else None,
            "conversation_id": conversation_id, "message_id": None,
            "user_id": None, "purpose": purposes[index % len(purposes)], "is_demo": True,
        })

    return {
        "work_areas": work_areas, "users": users, "tags": tags, "products": products,
        "contacts": contacts, "leads": leads, "conversations": conversations,
        "messages": messages, "notes": notes, "tasks": tasks, "appointments": appointments,
        "bot_settings": bot_settings, "app_settings": app_settings, "ai_usage_logs": ai_usage_logs,
    }
