import { expandPermissions, getModuleAccess, hasPermission, setModuleAccess } from "./permissions";

describe("modelo de permisos por módulo", () => {
  it("mantiene compatibilidad con permisos anteriores", () => {
    expect(hasPermission(["write_crm"], "crm_view")).toBe(true);
    expect(hasPermission(["configure_ai"], "calendar_admin")).toBe(true);
    expect(hasPermission(["write_catalog"], "catalog_admin")).toBe(true);
  });

  it("un administrador también puede utilizar y visualizar", () => {
    const expanded = expandPermissions(["inbox_admin"]);
    expect(expanded).toEqual(expect.arrayContaining(["inbox_admin", "inbox_use", "inbox_view"]));
  });

  it("guarda un único nivel claro por módulo sin perder los demás", () => {
    const next = setModuleAccess(["configure_ai", "crm_use"], "ai", "view");
    expect(getModuleAccess(next, "ai")).toBe("view");
    expect(getModuleAccess(next, "calendar")).toBe("admin");
    expect(getModuleAccess(next, "crm")).toBe("use");
    expect(next).not.toContain("configure_ai");
  });
});
