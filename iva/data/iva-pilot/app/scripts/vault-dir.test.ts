/* eslint-disable @typescript-eslint/no-floating-promises -- Node's test runner owns registrations. */
// Канонический резолвер каталога вольта: одна формула на все боевые места.
// Отсутствующая переменная — дефолт установки «vault»; пустое значение и пробелы по
// краям — явная ошибка с именем переменной (T24 v2, решение владельца: молчаливая
// подмена каталога в любую сторону запрещена).
import assert from "node:assert/strict";
import test from "node:test";
import {
  resolveVaultDir,
  VaultDirError,
  vaultDirSetting,
} from "@iva/vault-dir";

test("T24 v2: отсутствующая переменная даёт дефолт установки", () => {
  assert.equal(vaultDirSetting(undefined), "vault");
  assert.equal(resolveVaultDir("/srv/iva", undefined), "/srv/iva/vault");
});

test("T24 v2: пустое значение — явная ошибка с именем переменной", () => {
  for (const value of ["", " ", "\t\n"]) {
    assert.throws(
      () => vaultDirSetting(value),
      (error: unknown) => {
        assert.ok(error instanceof VaultDirError);
        assert.match(error.message, /ASSISTANT_VAULT_DIR/);
        assert.match(error.message, /unset it for the default/);
        return true;
      },
      `пустое значение ${JSON.stringify(value)} обязано бросать`,
    );
    assert.throws(
      () => resolveVaultDir("/srv/iva", value),
      VaultDirError,
      `resolveVaultDir(${JSON.stringify(value)}) обязан бросать`,
    );
  }
});

test("T24 v2: пробелы по краям — та же явная ошибка, не молчаливый trim", () => {
  assert.throws(
    () => vaultDirSetting(" my vault "),
    (error: unknown) => {
      assert.ok(error instanceof VaultDirError);
      assert.match(error.message, /ASSISTANT_VAULT_DIR/);
      assert.match(error.message, /whitespace/);
      return true;
    },
  );
  assert.throws(() => resolveVaultDir("/srv/iva", " vault"), VaultDirError);
});

test("T24 v2: относительный и абсолютный путь берутся как заданы", () => {
  assert.equal(resolveVaultDir("/srv/iva", "my vault"), "/srv/iva/my vault");
  assert.equal(resolveVaultDir("/srv/iva", "/srv/memory"), "/srv/memory");
});

test("T24 v2: явный undefined не подхватывает ASSISTANT_VAULT_DIR процесса", () => {
  const previous = process.env.ASSISTANT_VAULT_DIR;
  process.env.ASSISTANT_VAULT_DIR = "stale-process-state";
  try {
    assert.equal(resolveVaultDir("/srv/iva", undefined), "/srv/iva/vault");
    assert.equal(resolveVaultDir("/srv/iva"), "/srv/iva/stale-process-state");
  } finally {
    if (previous === undefined) delete process.env.ASSISTANT_VAULT_DIR;
    else process.env.ASSISTANT_VAULT_DIR = previous;
  }
});
