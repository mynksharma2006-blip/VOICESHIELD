const countrySelect = document.querySelector("#news-country");
const sourceElement = document.querySelector("#news-source");
const updatedElement = document.querySelector("#news-updated");
const messageElement = document.querySelector("#news-message");
const itemsElement = document.querySelector("#news-items");
const refreshButton = document.querySelector("#news-refresh");

function t(text) {
  return window.VoiceShieldI18n?.t(text) || text;
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(window.VoiceShieldI18n?.locale || undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function addNewsCard(item) {
  const card = document.createElement("article");
  card.className = "news-card";

  const metadata = document.createElement("p");
  metadata.className = "news-card-meta";
  const date = formatDate(item.date);
  metadata.textContent = date ? `${date} · ${item.source}` : item.source;

  const headline = document.createElement("h3");
  const link = document.createElement("a");
  link.href = item.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = item.headline;
  headline.append(link);

  const summary = document.createElement("p");
  summary.className = "news-card-summary";
  summary.textContent = item.summary;

  const original = document.createElement("a");
  original.className = "news-original-link";
  original.href = item.url;
  original.target = "_blank";
  original.rel = "noopener noreferrer";
  original.textContent = "Read the original report";

  card.append(metadata, headline, summary, original);
  itemsElement.append(card);
}

async function loadCountries() {
  try {
    const response = await fetch("/api/news/countries");
    const data = await response.json();
    if (!response.ok) throw new Error(t(data.detail || "Could not load country options."));

    countrySelect.replaceChildren();
    for (const country of data) {
      const option = document.createElement("option");
      option.value = country.code;
      option.textContent = window.VoiceShieldI18n?.t(country.name) || country.name;
      countrySelect.append(option);
    }
    let savedCountry = "";
    try {
      savedCountry = localStorage.getItem("voiceshield-news-country") || "";
    } catch {
      savedCountry = "";
    }
    countrySelect.value = data.some((country) => country.code === savedCountry)
      ? savedCountry
      : "IN";
    await loadNews();
  } catch (error) {
    countrySelect.replaceChildren(new Option(t("News unavailable"), ""));
    countrySelect.disabled = true;
    messageElement.textContent = t(error.message);
    updatedElement.textContent = t("The country list could not be loaded.");
  }
}

async function loadNews() {
  if (!countrySelect.value) return;
  refreshButton.disabled = true;
  messageElement.textContent = t("Loading the selected country’s official feed…");
  sourceElement.textContent = "";
  updatedElement.textContent = "";
  itemsElement.replaceChildren();

  try {
    const response = await fetch(`/api/news?country=${encodeURIComponent(countrySelect.value)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(t(data.detail || "Could not load scam news."));

    sourceElement.replaceChildren();
    sourceElement.append(`${t("Official source:")} `);
    const sourceLink = document.createElement("a");
    sourceLink.href = data.source_url;
    sourceLink.target = "_blank";
    sourceLink.rel = "noopener noreferrer";
    sourceLink.textContent = data.source_name;
    sourceElement.append(sourceLink);

    const checkedAt = formatDate(data.checked_at);
    const feedUpdatedAt = formatDate(data.feed_updated_at);
    updatedElement.textContent = feedUpdatedAt
      ? `${t("Feed last updated")} ${feedUpdatedAt} · ${t("Checked")} ${checkedAt || t("just now")}`
      : `${t("Checked")} ${checkedAt || t("just now")} · ${t("Feed update time not provided")}`;

    const notConfigured = data.message.match(/^A live feed from (.+) is not configured yet\./);
    const unreachable = data.message.match(/^The live feed from (.+) could not be reached or read\./);
    messageElement.textContent = data.status === "available"
      ? `${t("Recent items from")} ${data.source_name}. ${t("Open each original report for full details.")}`
      : data.status === "empty"
        ? t(data.message)
        : notConfigured
          ? `${t("A live feed from")} ${notConfigured[1]} ${t("is not configured yet.")} ${t("No sample or cached alerts are being shown; visit the official source instead.")}`
          : unreachable
            ? `${t("The live feed from")} ${unreachable[1]} ${t("could not be reached or read.")} ${t("No sample or cached alerts are being shown; visit the official source instead.")} ${t("Try again later or visit the official source.")}`
            : t(data.message);
    messageElement.dataset.state = data.status;
    for (const item of data.items) addNewsCard(item);
  } catch (error) {
    messageElement.textContent = `${t(error.message)} ${t("No sample or cached alerts are being shown; visit the official source instead.")}`;
    messageElement.dataset.state = "unavailable";
    updatedElement.textContent = `${t("Checked")} ${formatDate(new Date().toISOString())}`;
  } finally {
    refreshButton.disabled = false;
  }
}

countrySelect.addEventListener("change", () => {
  try {
    localStorage.setItem("voiceshield-news-country", countrySelect.value);
  } catch {
    // Country selection still works for this visit when storage is unavailable.
  }
  void loadNews();
});
refreshButton.addEventListener("click", () => void loadNews());
document.addEventListener("voiceshield:languagechange", () => {
  for (const option of countrySelect.options) {
    const country = option.value;
    const names = { AU: "Australia", CA: "Canada", GB: "United Kingdom", IN: "India", US: "United States" };
    if (names[country]) option.textContent = window.VoiceShieldI18n?.t(names[country]) || names[country];
  }
  if (countrySelect.value) void loadNews();
});

void loadCountries();
