import base64
import hashlib
import hmac
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from recruit import services, twilio_client
from recruit.models import Game, GameStatus, Mentor, MentorStatus, Message, SiteConfig
from recruit.phone import normalize_phone

TEST_SETTINGS = dict(
    NECHAMA_PROCESS_INLINE=True,
    TWILIO_ACCOUNT_SID="AC123",
    TWILIO_AUTH_TOKEN="secret",
    TWILIO_WHATSAPP_FROM="+15550000000",
    TWILIO_MESSAGING_SERVICE_SID="",
    TWILIO_VALIDATE_SIGNATURE=False,
    OPENAI_API_KEY="sk-test",
    PUBLIC_URL="https://nechama.test",
)


def ai_reply(**overrides):
    data = {
        "reply": "סבבה! רוצה קישור להרשמה?",
        "status": "interested",
        "needs_human": False,
        "opt_out": False,
        "preferences": "אוהב אימה",
        "summary": "מתעניין בהנחיה",
        "reserve_game_id": "",
    }
    data.update(overrides)
    return data


class PhoneTests(TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_phone("050-123-4567"), "+972501234567")
        self.assertEqual(normalize_phone("whatsapp:+972501234567"), "+972501234567")
        self.assertEqual(normalize_phone("972501234567"), "+972501234567")
        self.assertEqual(normalize_phone("+1 555 358 2140"), "+15553582140")
        self.assertIsNone(normalize_phone("hello"))
        self.assertIsNone(normalize_phone("12"))


@override_settings(**TEST_SETTINGS)
class InboundFlowTests(TestCase):
    def setUp(self):
        SiteConfig.get()
        self.mentor = Mentor.objects.create(name="ראובן", phone="+972501234567",
                                            status=MentorStatus.CONTACTED)
        self.send = mock.patch.object(twilio_client, "send_whatsapp", return_value="SMout1").start()
        self.ask = mock.patch("recruit.agent.ask_nechama", return_value=ai_reply()).start()
        mock.patch("recruit.services.get_context_text", return_value="מידע על אייקון").start()
        self.addCleanup(mock.patch.stopall)

    def post(self, body, sid="SM1", **extra):
        data = {"From": "whatsapp:+972501234567", "Body": body, "MessageSid": sid,
                "ProfileName": "Reuven", **extra}
        return self.client.post(reverse("twilio_inbound"), data)

    def test_reply_updates_mentor_and_logs(self):
        resp = self.post("היי נחמה! מתי אייקון?")
        self.assertEqual(resp.status_code, 200)
        self.mentor.refresh_from_db()
        self.assertEqual(self.mentor.status, MentorStatus.INTERESTED)
        self.assertEqual(self.mentor.preferences, "אוהב אימה")
        self.assertIsNotNone(self.mentor.last_inbound_at)
        bodies = list(self.mentor.messages.values_list("author", "body"))
        self.assertEqual(bodies[0], ("mentor", "היי נחמה! מתי אייקון?"))
        self.assertEqual(bodies[1], ("nechama", "סבבה! רוצה קישור להרשמה?"))
        self.send.assert_called_once()
        prompt = self.ask.call_args.args[0]
        self.assertIn("מידע על אייקון", prompt)
        self.assertIn("היי נחמה! מתי אייקון?", prompt)

    def test_duplicate_webhook_is_ignored(self):
        self.post("שלום", sid="SMdup")
        self.post("שלום", sid="SMdup")
        self.assertEqual(self.mentor.messages.filter(direction="in").count(), 1)
        self.assertEqual(self.ask.call_count, 1)

    def test_opt_out_keyword_skips_ai(self):
        self.post("הסר")
        self.mentor.refresh_from_db()
        self.assertTrue(self.mentor.opted_out)
        self.ask.assert_not_called()
        self.assertEqual(self.send.call_count, 1)

    def test_unknown_sender_creates_mentor(self):
        self.client.post(reverse("twilio_inbound"), {"From": "whatsapp:+972541111111", "Body": "היי",
                                                     "MessageSid": "SMnew", "ProfileName": "דנה"})
        m = Mentor.objects.get(phone="+972541111111")
        self.assertEqual(m.name, "דנה")
        self.assertEqual(m.source, "inbound")

    def test_paused_bot_does_not_answer(self):
        self.mentor.bot_paused = True
        self.mentor.save()
        self.post("שאלה")
        self.ask.assert_not_called()
        self.send.assert_not_called()

    def test_ai_error_flags_human(self):
        from recruit.agent import AgentError
        self.ask.side_effect = AgentError("boom")
        self.post("שאלה")
        self.mentor.refresh_from_db()
        self.assertTrue(self.mentor.needs_human)
        self.send.assert_not_called()
        self.assertTrue(self.mentor.messages.filter(author="system").exists())

    def test_verified_status_is_not_overwritten(self):
        self.mentor.status = MentorStatus.VERIFIED
        self.mentor.save()
        self.post("תודה")
        self.mentor.refresh_from_db()
        self.assertEqual(self.mentor.status, MentorStatus.VERIFIED)

    def test_reserve_free_game(self):
        Game.objects.create(code="G01", name="שודבש")
        self.ask.return_value = ai_reply(reserve_game_id="G01", reply="שריינתי לך את שודבש!")
        self.post("כן, שודבש")
        game = Game.objects.get(code="G01")
        self.assertEqual(game.status, GameStatus.RESERVED)
        self.assertEqual(game.reserved_by, self.mentor)

    def test_reserve_taken_game_changes_reply(self):
        other = Mentor.objects.create(name="אחר", phone="+972509999999")
        Game.objects.create(code="G01", name="שודבש", status=GameStatus.RESERVED, reserved_by=other)
        self.ask.return_value = ai_reply(reserve_game_id="G01", reply="שריינתי לך!")
        self.post("כן")
        last = self.mentor.messages.filter(author="nechama").last()
        self.assertIn("נתפס", last.body)

    def test_status_callback_updates_message(self):
        self.post("היי")
        self.client.post(reverse("twilio_status"), {"MessageSid": "SMout1", "MessageStatus": "read"})
        msg = Message.objects.get(twilio_sid="SMout1")
        self.assertEqual(msg.delivery_status, "read")


@override_settings(**{**TEST_SETTINGS, "TWILIO_VALIDATE_SIGNATURE": True})
class SignatureTests(TestCase):
    def test_bad_signature_rejected(self):
        resp = self.client.post(reverse("twilio_inbound"), {"From": "whatsapp:+972501234567", "Body": "x"},
                                HTTP_X_TWILIO_SIGNATURE="nope")
        self.assertEqual(resp.status_code, 403)

    def test_valid_signature(self):
        url = "https://nechama.test/webhooks/twilio/inbound/"
        params = {"Body": "x", "From": "whatsapp:+972501234567"}
        payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
        sig = base64.b64encode(hmac.new(b"secret", payload.encode(), hashlib.sha1).digest()).decode()
        self.assertTrue(twilio_client.valid_signature(url, params, sig))


@override_settings(**TEST_SETTINGS)
class OutreachTests(TestCase):
    def setUp(self):
        self.config = SiteConfig.get()
        self.config.outreach_enabled = True
        self.config.first_template_sid = "HXfirst"
        self.config.reminder_template_sid = "HXrem"
        self.config.first_template_text = "היי [שם]!"
        self.config.save()
        sids = iter(f"SMt{i}" for i in range(100))
        self.send = mock.patch.object(twilio_client, "send_whatsapp", side_effect=lambda *a, **k: next(sids)).start()
        self.addCleanup(mock.patch.stopall)

    def test_disabled_sends_nothing(self):
        self.config.outreach_enabled = False
        self.config.save()
        Mentor.objects.create(name="א", phone="+972501111111")
        self.assertEqual(services.run_outreach(force_window=True), {"skipped": "outreach disabled"})
        self.send.assert_not_called()

    def test_first_contact_then_reminder_then_stop(self):
        m = Mentor.objects.create(name="דנה לוי", phone="+972501111111")
        services.run_outreach(force_window=True)
        m.refresh_from_db()
        self.assertEqual((m.status, m.outreach_count), (MentorStatus.CONTACTED, 1))
        self.assertEqual(self.send.call_args.kwargs["content_sid"], "HXfirst")
        self.assertEqual(self.send.call_args.kwargs["variables"], {"1": "דנה"})
        self.assertEqual(m.messages.last().body, "היי דנה!")
        # Too soon for a reminder
        services.run_outreach(force_window=True)
        self.assertEqual(self.send.call_count, 1)
        Mentor.objects.filter(pk=m.pk).update(last_outreach_at=timezone.now() - timedelta(days=4))
        services.run_outreach(force_window=True)
        self.assertEqual(self.send.call_args.kwargs["content_sid"], "HXrem")
        Mentor.objects.filter(pk=m.pk).update(outreach_count=3, last_outreach_at=timezone.now() - timedelta(days=9))
        services.run_outreach(force_window=True)
        self.assertEqual(self.send.call_count, 2)

    def test_opted_out_and_replied_are_skipped(self):
        Mentor.objects.create(name="א", phone="+972501111111", opted_out=True)
        Mentor.objects.create(name="ב", phone="+972502222222", status=MentorStatus.REPLIED)
        services.run_outreach(force_window=True)
        self.send.assert_not_called()

    def test_expire_reservations(self):
        m = Mentor.objects.create(name="א", phone="+972501111111", status=MentorStatus.INTERESTED)
        Game.objects.create(code="G1", name="x", status=GameStatus.RESERVED, reserved_by=m,
                            reserved_at=timezone.now() - timedelta(days=10))
        self.assertEqual(services.expire_reservations(), 1)
        self.assertEqual(Game.objects.get(code="G1").status, GameStatus.FREE)


@override_settings(**TEST_SETTINGS)
class DashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("staff", password="pw-long-enough", is_staff=True)
        self.client.login(username="staff", password="pw-long-enough")
        self.mentor = Mentor.objects.create(name="ראובן", phone="+972501234567")
        Message.objects.create(mentor=self.mentor, direction="in", author="mentor", body="שלום")

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)

    def test_dashboard_and_detail_render(self):
        self.assertContains(self.client.get(reverse("dashboard")), "ראובן")
        self.assertContains(self.client.get(reverse("mentor_detail", args=[self.mentor.pk])), "שלום")

    def test_staff_reply_pauses_bot(self):
        Mentor.objects.filter(pk=self.mentor.pk).update(last_inbound_at=timezone.now())
        with mock.patch.object(twilio_client, "send_whatsapp", return_value="SMs"):
            self.client.post(reverse("mentor_detail", args=[self.mentor.pk]),
                             {"action": "reply", "text": "היי, כאן ראובן מהצוות"})
        self.mentor.refresh_from_db()
        self.assertTrue(self.mentor.bot_paused)
        self.assertTrue(self.mentor.messages.filter(author="staff", staff_user="staff").exists())

    def test_staff_reply_blocked_outside_window(self):
        with mock.patch.object(twilio_client, "send_whatsapp") as send:
            self.client.post(reverse("mentor_detail", args=[self.mentor.pk]),
                             {"action": "reply", "text": "היי"})
        send.assert_not_called()

    def test_import(self):
        self.client.post(reverse("import_mentors"),
                         {"rows": "שם,טלפון\nדנה לוי, 054-7654321\nיוסי\t+972521112222\nבלי טלפון"})
        self.assertTrue(Mentor.objects.filter(phone="+972547654321", name="דנה לוי").exists())
        self.assertTrue(Mentor.objects.filter(phone="+972521112222").exists())

    def test_export(self):
        resp = self.client.get(reverse("export_csv", args=["messages"]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("שלום", resp.content.decode("utf-8"))
