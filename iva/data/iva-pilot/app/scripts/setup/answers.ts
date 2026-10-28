// Один разбор ответов мастера setup. Мастер спрашивает по-русски, когда язык русский,
// поэтому разбор не имеет права знать только «yes»: «да» и «д» работают наравне с
// «y»/«yes», а хвостовая пунктуация пункта меню («2.», «2)» - так пишут по пункту «2)»)
// не уводит выбор в значение по умолчанию. То же правило, что у answer_token в install.sh.

/** Ответ без пробелов по краям и хвостовой пунктуации пункта меню, в нижнем регистре. */
export function answerToken(value: string): string {
  return value
    .trim()
    .replace(/[.)\s]+$/u, "")
    .toLowerCase();
}

/** «да»: y/yes и д/да, без регистра; всё остальное - нет. */
export function isYesAnswer(value: string): boolean {
  return ["y", "yes", "д", "да"].includes(answerToken(value));
}

/** Выбор пункта меню: «2», «2.», «2)» → 2; не число → null. */
export function menuChoice(value: string): number | null {
  const token = answerToken(value);
  return /^\d+$/u.test(token) ? Number(token) : null;
}
