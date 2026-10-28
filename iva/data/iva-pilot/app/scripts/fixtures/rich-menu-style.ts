// Тестовая фикстура: свежий каталог данных с menuStyle=rich, чтобы тесты отправки экранов
// проверяли rich-путь (по умолчанию у пользователя classic). Импортировать ПЕРВОЙ строкой:
// каталог фиксируется модулем settings на его импорте.
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

if (!process.env.ASSISTANT_DATA_DIR) {
  process.env.ASSISTANT_DATA_DIR = mkdtempSync(
    join(tmpdir(), "iva-rich-menu-"),
  );
}
writeFileSync(
  join(process.env.ASSISTANT_DATA_DIR, "settings.json"),
  JSON.stringify({ menuStyle: "rich" }),
  { mode: 0o600 },
);
