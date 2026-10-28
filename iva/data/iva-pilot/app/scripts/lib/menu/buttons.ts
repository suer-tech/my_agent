// Экраны меню импортируют помощник rich-кнопок отсюда; сам синтаксис тега живёт в одном
// месте — scripts/lib/telegram-buttons.ts (мост, меню, визарды).
export {
  button,
  buttonRow,
  escapeRichText,
  type RichButtonStyle,
} from "../telegram-buttons.ts";
