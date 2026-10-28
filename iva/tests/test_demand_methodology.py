import unittest

from iva_integration import demand_methodology as methodology


class Methodology(unittest.TestCase):
    def test_software_queries_keep_selected_subject_and_include_habr_without_goods(self):
        topic = 'Сверка остатков маркетплейса со складом'
        queries = methodology.query_suggestions('software', topic)
        self.assertEqual(len(queries), 4)
        self.assertTrue(all(query.startswith(topic + ' ') for query in queries))
        self.assertTrue(any('site:qna.habr.com' in query for query in queries))
        self.assertTrue(any('site:habr.com inurl:comments' in query for query in queries))
        self.assertTrue(all('хозблок' not in query and 'септик' not in query for query in queries))

    def test_foreign_and_critic_have_distinct_counter_searches(self):
        topic = 'workforce scheduling'
        foreign = methodology.query_suggestions('software', topic, role='foreign')
        critic = methodology.query_suggestions('software', topic, role='critic')
        self.assertTrue(any('inurl:issues' in query for query in foreign))
        self.assertTrue(any('решено' in query for query in critic))
        self.assertNotEqual(foreign, critic)
        text = methodology.instructions('software', role='foreign')
        self.assertIn('ту же задачу', text)
        self.assertIn('перевод точной цитаты', text)

    def test_goods_queries_do_not_introduce_software_research(self):
        queries = methodology.query_suggestions('b2c', 'Садовый полив')
        self.assertTrue(all(query.startswith('Садовый полив ') for query in queries))
        self.assertFalse(any('qna.habr' in query or 'inurl:issues' in query for query in queries))
        text = methodology.instructions('hozbloki', phase='analyze')
        self.assertIn('не превращай в новую гипотезу разработки ПО', text)
        self.assertNotIn('Покрытие ПО:', text)

    def test_methodology_never_treats_access_or_trends_as_market_proof(self):
        text = methodology.instructions('software')
        self.assertIn('не доказывает географическую блокировку', text)
        self.assertIn('нет автоматически подключённых API', text)
        self.assertIn('не является числом клиентов/покупок', text)
        self.assertIn('Площадка выбирается по теме, а не ради квоты', text)

    def test_analysis_separates_claim_strength_and_search_bias(self):
        text = methodology.instructions('software', phase='analyze')
        self.assertIn('объявленный бюджет, реальную оплату и выручку', text)
        self.assertIn('Перепечатки', text)
        self.assertIn('публичные жалобы смещены', text)
        self.assertIn('Неизвестные поля оставь неизвестными', text)

    def test_all_supported_phase_role_combinations_are_bounded(self):
        for track in methodology.TRACKS:
            for phase in methodology.PHASES:
                for role in [None, *sorted(methodology.ROLES)]:
                    with self.subTest(track=track, phase=phase, role=role):
                        text = methodology.instructions(track, phase=phase, role=role)
                        self.assertLess(len(text), 6500)
                        self.assertGreater(len(text), 1000)

    def test_unknown_scope_and_empty_topic_fail_instead_of_broad_search(self):
        for track in ('', 'everything', 'software and goods'):
            with self.assertRaises(ValueError):
                methodology.instructions(track)
        for topic in ('', '  ', 'x' * 601, None, 'bad\x00topic'):
            with self.assertRaises(ValueError):
                methodology.query_suggestions('software', topic)
        with self.assertRaises(ValueError):
            methodology.instructions('software', phase='execute-purchase')
        with self.assertRaises(ValueError):
            methodology.query_suggestions('software', 'topic', role='seller')

    def test_habr_source_location_distinguishes_comments_and_marketing(self):
        self.assertEqual(methodology.source_family('https://habr.com/ru/companies/vendor/articles/123/'), 'company_publication')
        self.assertEqual(methodology.source_family('https://habr.com/ru/companies/vendor/articles/123/comments/'), 'habr_comment_thread')
        self.assertEqual(methodology.source_family('https://qna.habr.com/q/123'), 'user_question_and_answers')
        self.assertEqual(methodology.source_family('https://habr.com/ru/articles/123/'), 'habr_publication')

    def test_domain_suffixes_do_not_inherit_a_source_category(self):
        self.assertEqual(methodology.source_family('https://habr.com.evil.test/ru/articles/1/'), 'unclassified_public_source')
        self.assertEqual(methodology.source_family('https://github.com/owner/project/issues/2'), 'public_issue_or_discussion')
        self.assertEqual(methodology.source_family('https://github.com/owner/project'), 'unclassified_public_source')
        self.assertEqual(methodology.source_family('javascript:alert(1)'), 'unknown')


if __name__ == '__main__':
    unittest.main()
