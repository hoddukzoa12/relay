#!/usr/bin/env node

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { ShopifyAdminClient } from "../services/commerce/src/shopify-client.ts";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..");
const expectedThemeNumber = "204473499934";
const liveThemeNumber = "204459704606";
const expectedThemeName = "Horizon + Relay Agent (preview)";
const themeId = `gid://shopify/OnlineStoreTheme/${expectedThemeNumber}`;
const sectionFilename = "sections/relay-agent-chat.liquid";
const templateFilename = "templates/index.json";
const expectedFiles = new Map([
  [sectionFilename, readFileSync(resolve(repoRoot, sectionFilename), "utf8")],
  [templateFilename, readFileSync(resolve(repoRoot, templateFilename), "utf8")],
]);

function loadEnv(path) {
  for (const raw of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf("=");
    if (separator < 1) continue;
    const key = line.slice(0, separator).trim();
    let value = line.slice(separator + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    process.env[key] ??= value;
  }
}

function stripJsonComments(value) {
  return value.replace(/\/\*[\s\S]*?\*\//g, "");
}

function validateTemplate(value, label) {
  try {
    const parsed = JSON.parse(stripJsonComments(value));
    if (!parsed.sections || !Array.isArray(parsed.order)) {
      throw new Error("expected sections and order");
    }
    return parsed;
  } catch (error) {
    throw new Error(`${label} is not a valid Shopify JSON template: ${String(error)}`);
  }
}

function templateFingerprint(value, label) {
  return JSON.stringify(validateTemplate(value, label));
}

loadEnv(resolve(repoRoot, ".env"));
validateTemplate(expectedFiles.get(templateFilename), templateFilename);

const domain = process.env.SHOPIFY_STORE_DOMAIN;
const adminAccessToken = process.env.SHOPIFY_ADMIN_ACCESS_TOKEN;
const clientId = process.env.SHOPIFY_CLIENT_ID;
const clientSecret = process.env.SHOPIFY_CLIENT_SECRET;
const apiVersion = process.env.SHOPIFY_API_VERSION ?? "2025-01";
if (!domain || (!adminAccessToken && !(clientId && clientSecret))) {
  throw new Error(
    "SHOPIFY_STORE_DOMAIN and either SHOPIFY_ADMIN_ACCESS_TOKEN or both SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET are required in .env",
  );
}
if (expectedThemeNumber === liveThemeNumber) {
  throw new Error("Refusing to target the live Horizon theme");
}

const shopifyAdmin = new ShopifyAdminClient({
  domain,
  apiVersion,
  adminAccessToken,
  clientId,
  clientSecret,
});

const THEME_FILES = [
  "query RelayChatThemeFiles($themeId: ID!, $filenames: [String!]!) {",
  "  theme(id: $themeId) {",
  "    id",
  "    name",
  "    role",
  "    files(filenames: $filenames, first: 50) {",
  "      nodes {",
  "        filename",
  "        checksumMd5",
  "        body {",
  "          ... on OnlineStoreThemeFileBodyText { content }",
  "        }",
  "      }",
  "    }",
  "  }",
  "}",
].join("\n");

const THEME_FILE_UPSERT = [
  "mutation RelayChatThemeFileUpsert(",
  "  $themeId: ID!",
  "  $files: [OnlineStoreThemeFilesUpsertFileInput!]!",
  ") {",
  "  themeFilesUpsert(themeId: $themeId, files: $files) {",
  "    upsertedThemeFiles { filename }",
  "    job { id }",
  "    userErrors { field message }",
  "  }",
  "}",
].join("\n");

async function fetchTheme() {
  const data = await shopifyAdmin.graphql(THEME_FILES, {
    themeId,
    filenames: [...expectedFiles.keys()],
  });
  if (!data.theme) throw new Error(`Theme ${themeId} was not found`);
  return data.theme;
}

function assertSafeTheme(theme) {
  if (theme.id !== themeId) {
    throw new Error(`Theme id mismatch: expected ${themeId}, received ${theme.id}`);
  }
  if (theme.name !== expectedThemeName) {
    throw new Error(
      `Refusing theme named "${theme.name}"; expected "${expectedThemeName}"`,
    );
  }
  if (theme.role === "MAIN") {
    throw new Error("Refusing to modify a published MAIN theme");
  }
}

function remoteContent(theme, filename) {
  return theme.files.nodes.find((file) => file.filename === filename)?.body?.content;
}

async function upsertFile(filename, content) {
  const data = await shopifyAdmin.graphql(THEME_FILE_UPSERT, {
    themeId,
    files: [{ filename, body: { type: "TEXT", value: content } }],
  });
  const result = data.themeFilesUpsert;
  if (result.userErrors?.length) {
    throw new Error(
      `${filename} upsert failed: ${JSON.stringify(result.userErrors)}`,
    );
  }
  const upserted = result.upsertedThemeFiles?.map((file) => file.filename) ?? [];
  if (!upserted.includes(filename) && !result.job?.id) {
    throw new Error(
      `${filename} was not accepted for upsert: ${JSON.stringify(result)}`,
    );
  }
  console.log(
    `Queued ${filename}${result.job?.id ? ` (job ${result.job.id})` : ""}`,
  );
}

function fileMatches(filename, actual, expected) {
  if (typeof actual !== "string") return false;
  if (filename === templateFilename) {
    return (
      templateFingerprint(actual, `remote ${filename}`) ===
      templateFingerprint(expected, filename)
    );
  }
  return actual === expected;
}

async function waitForFile(filename, content) {
  for (let attempt = 1; attempt <= 15; attempt += 1) {
    const theme = await fetchTheme();
    assertSafeTheme(theme);
    if (fileMatches(filename, remoteContent(theme, filename), content)) return theme;
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 1_000));
  }
  throw new Error(`Timed out waiting for Shopify to persist ${filename}`);
}

async function verifyRemote() {
  const theme = await fetchTheme();
  assertSafeTheme(theme);
  const currentTemplate = remoteContent(theme, templateFilename);
  if (currentTemplate) validateTemplate(currentTemplate, `remote ${templateFilename}`);

  const mismatches = [];
  for (const [filename, content] of expectedFiles) {
    if (!fileMatches(filename, remoteContent(theme, filename), content)) {
      mismatches.push(filename);
    }
  }
  if (mismatches.length) {
    throw new Error(`Remote files differ from this checkout: ${mismatches.join(", ")}`);
  }
  return theme;
}

const upload = process.argv.includes("--upload");
const initialTheme = await fetchTheme();
assertSafeTheme(initialTheme);
const initialTemplate = remoteContent(initialTheme, templateFilename);
if (initialTemplate) validateTemplate(initialTemplate, `remote ${templateFilename}`);

if (upload) {
  // Shopify rejects a JSON template that references a section file not yet present.
  await upsertFile(sectionFilename, expectedFiles.get(sectionFilename));
  await waitForFile(sectionFilename, expectedFiles.get(sectionFilename));
  await upsertFile(templateFilename, expectedFiles.get(templateFilename));
  await waitForFile(templateFilename, expectedFiles.get(templateFilename));
}

const verifiedTheme = await verifyRemote();
console.log(
  JSON.stringify(
    {
      ok: true,
      uploaded: upload,
      theme: {
        id: verifiedTheme.id,
        name: verifiedTheme.name,
        role: verifiedTheme.role,
      },
      files: [...expectedFiles.keys()],
      previewUrl: `https://${domain}/?preview_theme_id=${expectedThemeNumber}`,
    },
    null,
    2,
  ),
);
