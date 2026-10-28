// Свойства парсеров process-lock отделены от сценариев с настоящими spawn/kill: свойства
// держат только разбор строк и не должны делить файл с жизнью дочерних процессов и их
// сроками (flake-каталог: сроки в общем файле плыли под нагрузкой).
import assert from "node:assert/strict";
import test from "node:test";
import fc from "fast-check";
import {
  parseTelegramGuardHolderMarker,
  parseTelegramProcessOwner,
} from "./process-lock.ts";

const SEED = 18_702;

/**
 * Заведомо валидные документы: на одних произвольных байтах парсер их всегда отвергает
 * (0 приёма на 2 000 вариантов), ветка успеха не исполняется, и проверки полей внутри try
 * не могли покраснеть ни на какой мутации (слепой QA T28, M6). Половина прогонов теперь
 * проходит обе ветки: валидный документ обязан вернуться целой схемой, байты — бросить.
 */
const VALID_OWNER = JSON.stringify({
  schema: "iva-telegram-poll-owner/v2",
  pid: 4_242,
  processStart: "Mon Jan 01 00:00:00 2001",
  nonce: "0123456789abcdef0123456789abcdef",
});
const VALID_HOLDER = `iva-telegram-poll-holder-v2=${Buffer.from(
  JSON.stringify({
    schema: "iva-telegram-poll-holder/v2",
    resource: "telegram:4242",
    pid: 4_242,
    processStart: "Mon Jan 01 00:00:00 2001",
    nonce: "fedcba9876543210fedcba9876543210",
  }),
).toString("base64url")}`;

void test("property: arbitrary owner bytes either fail or satisfy the full identity schema", () => {
  fc.assert(
    fc.property(fc.oneof(fc.string(), fc.constant(VALID_OWNER)), (raw) => {
      try {
        const owner = parseTelegramProcessOwner(raw);
        assert.ok(Number.isSafeInteger(owner.pid) && owner.pid > 0);
        assert.match(owner.nonce, /^[0-9a-f]{32}$/u);
        assert.deepEqual(Object.keys(owner).sort(), [
          "nonce",
          "pid",
          "processStart",
          "schema",
        ]);
      } catch (error) {
        // AssertionError — провал проверки полей, а не отказ парсера: без проброса
        // свойство зелено на любом разрушенном поле (слепой QA T28, M6).
        if (error instanceof assert.AssertionError) throw error;
        assert.ok(error instanceof Error);
      }
    }),
    { seed: SEED, numRuns: 2_000 },
  );
});

void test("property: arbitrary holder markers fail or satisfy the global schema", () => {
  fc.assert(
    fc.property(fc.oneof(fc.string(), fc.constant(VALID_HOLDER)), (raw) => {
      try {
        const holder = parseTelegramGuardHolderMarker(raw);
        assert.match(holder.resource, /^(?:telegram:[0-9]+|test:[a-z0-9-]+)$/u);
        assert.ok(Number.isSafeInteger(holder.pid) && holder.pid > 0);
        assert.match(holder.nonce, /^[0-9a-f]{32}$/u);
        assert.deepEqual(Object.keys(holder).sort(), [
          "nonce",
          "pid",
          "processStart",
          "resource",
          "schema",
        ]);
      } catch (error) {
        if (error instanceof assert.AssertionError) throw error;
        assert.ok(error instanceof Error);
      }
    }),
    { seed: SEED, numRuns: 2_000 },
  );
});
