export const MODULE_PERMISSIONS = [
  { key: "crm", label: "CRM y ventas", description: "Clientes, leads, pipeline y tareas" },
  { key: "inbox", label: "Bandeja", description: "Conversaciones y mensajes con clientes" },
  { key: "calendar", label: "Agenda", description: "Citas, eventos, horarios y disponibilidad" },
  { key: "catalog", label: "Catálogo", description: "Productos, servicios, precios y promociones" },
  { key: "ai", label: "IA y automatización", description: "Bot, proveedores, consumo y automatizaciones" },
  { key: "users", label: "Usuarios y equipo", description: "Personas, roles, áreas y asignaciones" },
  { key: "whatsapp", label: "WhatsApp", description: "Conexión, plantillas, webhook y credenciales" },
  { key: "settings", label: "Configuración general", description: "Opciones transversales del sistema" },
];

export const ACCESS_LEVELS = [
  { value: "none", label: "Sin acceso", description: "El módulo no se muestra y sus rutas quedan bloqueadas." },
  { value: "view", label: "Sólo visualizar", description: "Puede consultar información, sin crear ni modificar." },
  { value: "use", label: "Utilizar", description: "Puede trabajar en el módulo y modificar lo que tiene asignado." },
  { value: "admin", label: "Administrar", description: "Puede configurar el módulo y gestionar todos sus registros." },
];

const LEGACY_PERMISSION_MAP = {
  write_crm: ["crm_use", "inbox_use", "calendar_use"],
  write_catalog: ["catalog_admin"],
  message_any: ["inbox_admin"],
  trigger_bot_any: ["ai_use"],
  manage_users: ["users_admin"],
  configure_whatsapp: ["whatsapp_admin"],
  configure_ai: ["ai_admin", "calendar_admin"],
  manage_settings: ["settings_admin"],
};

const LEVEL_SCORE = { none: 0, view: 1, use: 2, admin: 3 };

export function expandPermissions(input = []) {
  const permissions = Array.isArray(input) ? input : input?.permissions || [];
  const expanded = new Set(permissions);

  permissions.forEach((permission) => {
    (LEGACY_PERMISSION_MAP[permission] || []).forEach((mapped) => expanded.add(mapped));
  });

  MODULE_PERMISSIONS.forEach(({ key }) => {
    if (expanded.has(`${key}_admin`)) {
      expanded.add(`${key}_use`);
      expanded.add(`${key}_view`);
    } else if (expanded.has(`${key}_use`)) {
      expanded.add(`${key}_view`);
    }
  });

  return [...expanded];
}

export function hasPermission(input, required) {
  return expandPermissions(input).includes(required);
}

export function getModuleAccess(input, module) {
  const permissions = expandPermissions(input);
  if (permissions.includes(`${module}_admin`)) return "admin";
  if (permissions.includes(`${module}_use`)) return "use";
  if (permissions.includes(`${module}_view`)) return "view";
  return "none";
}

export function setModuleAccess(input, module, level) {
  const expanded = expandPermissions(input);
  const legacyKeys = new Set(Object.keys(LEGACY_PERMISSION_MAP));
  const canonical = [];

  MODULE_PERMISSIONS.forEach(({ key }) => {
    const current = key === module ? level : getModuleAccess(expanded, key);
    if (LEVEL_SCORE[current] > 0) canonical.push(`${key}_${current}`);
  });

  expanded.forEach((permission) => {
    const isModulePermission = MODULE_PERMISSIONS.some(({ key }) => permission.startsWith(`${key}_`));
    if (!isModulePermission && !legacyKeys.has(permission)) canonical.push(permission);
  });

  return [...new Set(canonical)];
}

export function hasConfigurationAccess(input) {
  return ["calendar", "ai", "users", "whatsapp", "settings"].some(
    (module) => getModuleAccess(input, module) === "admin"
  );
}

export function firstAllowedPath(input) {
  if (input?.subscription_access === false && !input?.is_platform_admin) return "/suscripcion";
  const candidates = [
    ["crm_view", "/dashboard"],
    ["inbox_view", "/inbox"],
    ["calendar_view", "/calendario"],
    ["catalog_view", "/catalogo"],
    ["ai_view", "/consumo-ia"],
    ["users_view", "/admin"],
    ["settings_view", "/admin"],
    ["whatsapp_view", "/admin"],
  ];
  return candidates.find(([permission]) => hasPermission(input, permission))?.[1] || "/sin-acceso";
}
