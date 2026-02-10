let local = {};
try {
  // Local-only overrides (not committed):
  // module.exports = { DEV_API_BASE_URL, TRIAL_API_BASE_URL, PROD_API_BASE_URL, SUBSCRIBE_TEMPLATE_ID }
  // eslint-disable-next-line global-require
  local = require("./config.local");
} catch (_) {
  local = {};
}

const DEV_API_BASE_URL = local.DEV_API_BASE_URL || "http://127.0.0.1:8000";
const TRIAL_API_BASE_URL = local.TRIAL_API_BASE_URL || DEV_API_BASE_URL;
const PROD_API_BASE_URL = local.PROD_API_BASE_URL || "https://api.example.com";

function resolveEnvVersion() {
  const override = String(local.ENV_VERSION || "").trim().toLowerCase();
  if (override === "develop" || override === "trial" || override === "release") {
    return override;
  }
  try {
    // Avoid getAccountInfoSync() path in some devtools environments.
    if (typeof __wxConfig !== "undefined" && __wxConfig && __wxConfig.envVersion) {
      const runtimeEnv = String(__wxConfig.envVersion).toLowerCase();
      if (runtimeEnv === "develop" || runtimeEnv === "trial" || runtimeEnv === "release") {
        return runtimeEnv;
      }
    }
  } catch (_) {
    // ignore
  }
  return "develop";
}
const envVersion = resolveEnvVersion();

module.exports = {
  // develop -> local, trial -> test/staging, release -> production
  API_BASE_URL:
    envVersion === "release"
      ? PROD_API_BASE_URL
      : envVersion === "trial"
        ? TRIAL_API_BASE_URL
        : DEV_API_BASE_URL,
  // Optional: subscription template ID for reminder push.
  SUBSCRIBE_TEMPLATE_ID: local.SUBSCRIBE_TEMPLATE_ID || ""
};
