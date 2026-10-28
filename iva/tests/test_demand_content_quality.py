from pathlib import Path
import unittest

from bs4 import BeautifulSoup
from iva_integration.demand_content_quality import extract_html_body, insufficient_content


FIXTURES = Path(__file__).parent / 'fixtures/demand_content'


class ContentQualityTests(unittest.TestCase):
    def soup(self, source):
        return BeautifulSoup(source, 'html.parser')

    def fixture(self, name):
        return self.soup((FIXTURES / name).read_text(encoding='utf-8'))

    def test_menu_articles_do_not_hide_documented_case_body(self):
        soup = self.fixture('docs_sidebar_case.html')
        before = str(soup)
        self.assertIn('Подключение интеграций', soup.article.get_text(' ', strip=True))
        text, error = extract_html_body(soup)
        self.assertIsNone(error)
        self.assertIn('Если передача заказов не настроена, остатки после продажи не изменятся.', text)
        self.assertIn('Обновлено 23.09.2026', text)
        self.assertNotIn('Как подключить первую площадку', text)
        self.assertNotIn('Как добавить пользователей', text)
        self.assertNotIn('Реклама и служебные', text)
        self.assertEqual(str(soup), before)

    def test_long_url_path_is_not_read_document_content(self):
        text, error = extract_html_body(self.fixture('path_shell.html'))
        self.assertGreaterEqual(len(text), 80)
        self.assertEqual(error, 'insufficient_content')

    def test_short_old_forum_complaint_and_date_survive(self):
        text, error = extract_html_body(self.fixture('short_forum_comment.html'))
        self.assertIsNone(error)
        self.assertLess(len(text), 220)
        self.assertIn('03.01.2022', text)
        self.assertIn('Почему не получается выгрузить Excel', text)

    def test_multiple_real_forum_articles_preserve_both_replies(self):
        soup = self.soup('<body><article><p>Первый автор описывает ручной перенос заказов.</p></article>'
                         '<article><p>Второй автор сообщает об успешной настройке обмена.</p></article></body>')
        text, error = extract_html_body(soup)
        self.assertIsNone(error)
        self.assertIn('Первый автор', text)
        self.assertIn('Второй автор', text)

    def test_genuine_main_takes_priority_over_unrelated_article(self):
        text, error = extract_html_body(self.soup('<body><article>Анонс другого материала.</article>'
                         '<main><p>Полный текст исследуемой инструкции с проверяемым описанием.</p></main></body>'))
        self.assertIsNone(error)
        self.assertIn('Полный текст', text)
        self.assertNotIn('Анонс', text)

    def test_empty_main_does_not_hide_article(self):
        text, error = extract_html_body(self.soup('<body><main></main><div><p>Доступный текст вне пустой оболочки.</p></div></body>'))
        self.assertIsNone(error)
        self.assertIn('Доступный текст', text)

    def test_navigation_only_document_reports_insufficient_content(self):
        text, error = extract_html_body(self.soup('<html><head><title>Документация</title></head><body>'
                         '<article><h2>Разделы</h2><a href="/a">Первый раздел документации</a>'
                         '<a href="/b">Второй раздел документации</a><a href="/c">Третий раздел документации</a></article></body></html>'))
        self.assertEqual(error, 'insufficient_content')
        self.assertEqual(text, '')

    def test_main_or_body_link_indexes_are_not_read_documents(self):
        links = ('<h2>Разделы</h2><a href="/a">Первая длинная инструкция</a>'
                 '<a href="/b">Вторая длинная инструкция</a><a href="/c">Третья длинная инструкция</a>')
        for source in ('<body>' + links + '</body>', '<body><main>' + links + '</main></body>'):
            with self.subTest(source=source):
                text, error = extract_html_body(self.soup(source))
                self.assertEqual(error, 'insufficient_content')
                self.assertEqual(text, '')

    def test_short_comment_with_several_links_still_keeps_its_actual_statement(self):
        text, error = extract_html_body(self.soup('<article><p>Все эти инструкции уже проверил, но возвраты по-прежнему приходится сверять вручную каждую неделю.</p>'
                         '<a href="/a">Один</a><a href="/b">Два</a><a href="/c">Три</a></article>'))
        self.assertIsNone(error)
        self.assertIn('возвраты по-прежнему', text)

    def test_two_word_complaint_with_three_long_links_is_not_navigation(self):
        text, error = extract_html_body(self.soup('<article><p>Не работает.</p>'
                         '<a href="/a">Очень длинная инструкция по настройке первого способа обмена</a>'
                         '<a href="/b">Очень длинная инструкция по настройке второго способа обмена</a>'
                         '<a href="/c">Очень длинная инструкция по настройке третьего способа обмена</a></article>'))
        self.assertIsNone(error)
        self.assertIn('Не работает.', text)

    def test_nonarticle_boilerplate_is_ignored_without_rewriting_quote_text(self):
        text, error = extract_html_body(self.soup('<body><div role="navigation">Меню</div>'
                         '<main><p>Exact English quote stays exactly as written.</p></main>'
                         '<div aria-hidden="true">Hidden</div><script>token=private</script></body>'))
        self.assertEqual(text, 'Exact English quote stays exactly as written.')
        self.assertIsNone(error)

    def test_no_generic_minimum_or_keyword_rule_rejects_short_real_content(self):
        self.assertFalse(insufficient_content('Ошибка синхронизации заказов повторяется.'))
        self.assertFalse(insufficient_content('Использую /api/orders для ручной выгрузки.'))
        self.assertTrue(insufficient_content('https://example.org/a/b'))

    def test_html_comments_templates_and_hidden_styles_cannot_be_evidence(self):
        soup = self.soup('<!doctype html><body><!-- Hidden instructions -->'
                         '<template>Inactive content</template><p style="display: none !important">Secret</p>'
                         '<p style="visibility: hidden; color:red">Not visible</p>'
                         '<p>Actual customer statement remains visible.</p></body>')
        text, error = extract_html_body(soup)
        self.assertEqual(text, 'Actual customer statement remains visible.')
        self.assertIsNone(error)

    def test_article_header_preserves_the_problem_title_and_original_date(self):
        text, error = extract_html_body(self.soup('<body><header>Global site banner</header>'
            '<article><header><h1>Stock sync is broken.</h1><time datetime="2022-01-03">03.01.2022</time></header>'
            '<p>Anyone else?</p></article></body>'))
        self.assertIsNone(error)
        self.assertIn('Stock sync is broken.', text)
        self.assertIn('03.01.2022', text)
        self.assertNotIn('Global site banner', text)

    def test_main_header_keeps_context_but_explicit_banner_stays_ignored(self):
        text, error = extract_html_body(self.soup('<main><header><h1>Problem statement</h1></header>'
            '<header role="banner">Advertisement</header><p>Concrete user experience.</p></main>'))
        self.assertIsNone(error)
        self.assertIn('Problem statement', text)
        self.assertNotIn('Advertisement', text)


if __name__ == '__main__':
    unittest.main()
