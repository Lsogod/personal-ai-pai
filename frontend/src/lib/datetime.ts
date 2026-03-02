export function parseServerDate(value: string): Date {
  const raw = String(value || "").trim();
  if (!raw) return new Date("");

  // Tolerate malformed "...+00:00Z" by removing the extra trailing Z.
  const cleaned = raw.replace(/([+-]\d{2}:\d{2})Z$/, "$1");
  const isoLike = cleaned.includes("T") ? cleaned : cleaned.replace(" ", "T");
  const hasTimezone = /Z$/i.test(isoLike) || /[+-]\d{2}:\d{2}$/.test(isoLike);

  // Backend timestamps are UTC; if timezone info is missing, treat it as UTC.
  const primary = hasTimezone ? isoLike : `${isoLike}Z`;
  let dt = new Date(primary);
  if (!Number.isNaN(dt.getTime())) return dt;

  // Fallback for non-standard date separators.
  const relaxed = primary.replace(/-/g, "/").replace("T", " ");
  dt = new Date(relaxed);
  return dt;
}

const DISPLAY_TIMEZONE = "Asia/Shanghai";

function getDatePartsInDisplayTimezone(dt: Date): {
  year: string;
  month: string;
  day: string;
  hour: string;
  minute: string;
  second: string;
} {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: DISPLAY_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(dt);

  const lookup = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return {
    year: lookup("year"),
    month: lookup("month"),
    day: lookup("day"),
    hour: lookup("hour"),
    minute: lookup("minute"),
    second: lookup("second"),
  };
}

export function formatHmLocal(value: string): string {
  const dt = parseServerDate(value);
  if (Number.isNaN(dt.getTime())) return "--:--";
  const { hour: hh, minute: mm } = getDatePartsInDisplayTimezone(dt);
  return `${hh}:${mm}`;
}

export function formatMdHmLocal(value: string): string {
  const dt = parseServerDate(value);
  if (Number.isNaN(dt.getTime())) return "--:--";
  const { month, day, hour: hh, minute: mm } = getDatePartsInDisplayTimezone(dt);
  return `${month}-${day} ${hh}:${mm}`;
}

export function formatYmdHmLocal(value: string): string {
  const dt = parseServerDate(value);
  if (Number.isNaN(dt.getTime())) return "-";
  const { year, month, day, hour: hh, minute: mm } = getDatePartsInDisplayTimezone(dt);
  return `${year}-${month}-${day} ${hh}:${mm}`;
}
