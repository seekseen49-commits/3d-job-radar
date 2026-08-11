import unittest

from recipients import parse_recipient_chat_ids, send_to_recipients


class RecipientTests(unittest.IsolatedAsyncioTestCase):
    def test_owner_only(self):
        self.assertEqual(parse_recipient_chat_ids(1, None), (1,))

    def test_one_and_multiple_additional_recipients(self):
        self.assertEqual(parse_recipient_chat_ids(1, "2"), (1, 2))
        self.assertEqual(parse_recipient_chat_ids(1, "2, 3,4"), (1, 2, 3, 4))

    def test_duplicate_invalid_and_empty_values_are_ignored(self):
        self.assertEqual(parse_recipient_chat_ids(1, "1, 2, nope, , 2,  "), (1, 2))

    async def test_one_failed_delivery_does_not_stop_other_recipients(self):
        class Bot:
            def __init__(self): self.sent = []
            async def send_message(self, chat_id, text):
                if chat_id == 2: raise RuntimeError("blocked")
                self.sent.append((chat_id, text))
        bot = Bot()
        self.assertTrue(await send_to_recipients(bot, (1, 2, 3), "card"))
        self.assertEqual(bot.sent, [(1, "card"), (3, "card")])
