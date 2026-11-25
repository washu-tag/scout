const PLACEHOLDER = 'REPLACE_WITH_YOUR_GATEWAY_KEY';

function isKey(value) {
  return typeof value === 'string' && value !== '' && value !== PLACEHOLDER;
}

function fillKey(value, key) {
  if (value === PLACEHOLDER) {
    return key;
  }
  if (Array.isArray(value)) {
    return value.map((item) => fillKey(item, key));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, fillKey(v, key)]));
  }
  return value;
}

function chatModelKeys(providers) {
  const keys = [];
  for (const provider of Array.isArray(providers) ? providers : []) {
    if (isKey(provider?.apiKey)) {
      keys.push(provider.apiKey);
    }
    for (const model of provider?.models || []) {
      for (const [name, value] of Object.entries(model?.requestHeaders || {})) {
        if (name.toLowerCase() === 'x-api-key' && isKey(value)) {
          keys.push(value);
        }
      }
    }
  }
  return keys;
}

function isObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

// Per-model settings such as reasoningEffort are the user's to change, so theirs win
// over the site's defaults, model by model.
function mergeSettings(siteSettings, userSettings) {
  if (!isObject(userSettings)) {
    return siteSettings;
  }
  const merged = { ...(isObject(siteSettings) ? siteSettings : {}) };
  for (const [model, values] of Object.entries(userSettings)) {
    merged[model] = isObject(values) ? { ...(merged[model] || {}), ...values } : values;
  }
  return merged;
}

// Site providers replace the user's providers of the same name and come first, keeping
// the user's per-model settings; the user's other providers are kept. The placeholder
// is replaced with the key when there is one.
function mergeChatModels(site, current, key) {
  const users = Array.isArray(current) ? current : [];
  const byName = new Map(users.map((provider) => [provider?.name, provider]));
  const names = new Set(site.map((provider) => provider?.name));
  const own = users.filter((provider) => !names.has(provider?.name));
  const fromSite = (isKey(key) ? fillKey(site, key) : site).map((provider) => {
    const settings = mergeSettings(provider.settings, byName.get(provider.name)?.settings);
    return settings === undefined ? provider : { ...provider, settings };
  });
  const merged = [...fromSite, ...own];
  return { providers: merged, changed: JSON.stringify(merged) !== JSON.stringify(current) };
}

module.exports = { PLACEHOLDER, isKey, chatModelKeys, mergeChatModels };
