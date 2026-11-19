import unittest
from unittest.mock import patch

from bench_whatsapp import account_routes, open_route, open_whatsapp


class WhatsAppRouting(unittest.TestCase):
    def setUp(self):
        self.local = {'host': 'local', 'bench': 'cofre'}
        self.remote = {'host': 'lol', 'bench': 'cofre'}
        self.config = {
            'accounts': {'principal': [self.local, self.remote], 'loja': [self.remote]},
            'projects': {'loja': 'loja'},
        }

    def test_project_mapping_and_explicit_account(self):
        self.assertEqual(account_routes(self.config, None, 'loja'), ('loja', [self.remote]))
        self.assertEqual(account_routes(self.config, 'principal', 'loja'),
                         ('principal', [self.local, self.remote]))
        with self.assertRaisesRegex(ValueError, 'whatsapp_account_required'):
            account_routes(self.config, None, 'sem-conta-associada')

    def test_uses_remote_paired_session_after_local_pairing_screen(self):
        answers = [{'state': 'pairing_required', **self.local},
                   {'state': 'paired', **self.remote, 'tab': 'tab', 'mission': 'pedido'}]
        with patch('bench_whatsapp.open_route', side_effect=answers) as route:
            self.assertEqual(open_whatsapp(self.config, 'pedido', account='principal'),
                             {'account': 'principal', **answers[1]})
            self.assertEqual([call.args[0] for call in route.call_args_list], [self.local, self.remote])

    def test_cleans_only_probe_tab_when_pairing_required(self):
        answers = [{'status': 'conectado', 'lives_in': 'cofre'}, {}, {'tab': 'mine'},
                   {'state': 'pairing_required'}, {}, {}]
        with patch('bench_whatsapp.bench_command', side_effect=answers) as command:
            self.assertEqual(open_route(self.remote, 'pedido'), {'state': 'pairing_required', **self.remote})
            self.assertEqual(command.call_args_list[-2].args[-3:], ('close', '--tab', 'mine'))

    def test_occupied_tab_is_never_replaced(self):
        answers = [{'status': 'conectado', 'lives_in': 'cofre'}, {},
                   RuntimeError('whatsapp_busy: o WhatsApp Web está aberto fora desta missão.')]
        with patch('bench_whatsapp.bench_command', side_effect=answers) as command:
            self.assertEqual(open_route(self.remote, 'pedido'), {'state': 'busy', **self.remote})
            self.assertEqual(command.call_count, 3)
