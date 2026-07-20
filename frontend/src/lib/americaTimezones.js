export const DEFAULT_AMERICA_TIMEZONE = "America/Argentina/Buenos_Aires";

export const AMERICA_TIMEZONES = [
  { value: "America/Argentina/Buenos_Aires", label: "Buenos Aires, Argentina" },
  { value: "America/Argentina/Cordoba", label: "Córdoba, Argentina" },
  { value: "America/Argentina/Mendoza", label: "Mendoza, Argentina" },
  { value: "America/Argentina/Salta", label: "Salta, Argentina" },
  { value: "America/Argentina/Ushuaia", label: "Ushuaia, Argentina" },
  { value: "America/Montevideo", label: "Montevideo, Uruguay" },
  { value: "America/Asuncion", label: "Asunción, Paraguay" },
  { value: "America/Santiago", label: "Santiago, Chile" },
  { value: "America/Sao_Paulo", label: "São Paulo, Brasil" },
  { value: "America/La_Paz", label: "La Paz, Bolivia" },
  { value: "America/Lima", label: "Lima, Perú" },
  { value: "America/Bogota", label: "Bogotá, Colombia" },
  { value: "America/Guayaquil", label: "Quito y Guayaquil, Ecuador" },
  { value: "America/Caracas", label: "Caracas, Venezuela" },
  { value: "America/Guyana", label: "Georgetown, Guyana" },
  { value: "America/Paramaribo", label: "Paramaribo, Surinam" },
  { value: "America/Panama", label: "Ciudad de Panamá, Panamá" },
  { value: "America/Costa_Rica", label: "San José, Costa Rica" },
  { value: "America/Guatemala", label: "Ciudad de Guatemala, Guatemala" },
  { value: "America/El_Salvador", label: "San Salvador, El Salvador" },
  { value: "America/Tegucigalpa", label: "Tegucigalpa, Honduras" },
  { value: "America/Managua", label: "Managua, Nicaragua" },
  { value: "America/Mexico_City", label: "Ciudad de México, México" },
  { value: "America/Cancun", label: "Cancún, México" },
  { value: "America/Havana", label: "La Habana, Cuba" },
  { value: "America/Santo_Domingo", label: "Santo Domingo, República Dominicana" },
  { value: "America/Puerto_Rico", label: "San Juan, Puerto Rico" },
  { value: "America/New_York", label: "Nueva York, Estados Unidos" },
  { value: "America/Chicago", label: "Chicago, Estados Unidos" },
  { value: "America/Denver", label: "Denver, Estados Unidos" },
  { value: "America/Phoenix", label: "Phoenix, Estados Unidos" },
  { value: "America/Los_Angeles", label: "Los Ángeles, Estados Unidos" },
  { value: "America/Anchorage", label: "Anchorage, Estados Unidos" },
  { value: "America/Toronto", label: "Toronto, Canadá" },
  { value: "America/Halifax", label: "Halifax, Canadá" },
  { value: "America/Vancouver", label: "Vancouver, Canadá" },
];

const AMERICA_TIMEZONE_VALUES = new Set(AMERICA_TIMEZONES.map((timezone) => timezone.value));

export const normalizeAmericaTimezone = (value, fallback = DEFAULT_AMERICA_TIMEZONE) => (
  AMERICA_TIMEZONE_VALUES.has(value)
    ? value
    : (AMERICA_TIMEZONE_VALUES.has(fallback) ? fallback : DEFAULT_AMERICA_TIMEZONE)
);
