# נחמה – גיוס מנחי משחקי תפקידים לאייקון

שרת Django שמתכתב בוואטסאפ (דרך Twilio) עם מנחים, עונה בעזרת OpenAI, ומציג לצוות דשבורד של כל השיחות.

## מה יש כאן
- **webhook מ-Twilio** (`/webhooks/twilio/inbound/`): כל הודעה נכנסת נשמרת, ונחמה עונה תוך כמה שניות. כמה הודעות ברצף מקבלות תשובה אחת.
- **הסוכן** (`recruit/agent.py`, `recruit/prompts.py`): OpenAI Responses API עם JSON Schema קשיח. מחזיר תשובה, סטטוס, העדפות, סיכום, שריון משחק ודגל למענה אנושי.
- **פנייה יזומה ותזכורות** (`python manage.py tick`, רץ כ-cron): כבויה עד שמדליקים אותה בהגדרות. שולחת רק בימים א'–ה' בשעות שהוגדרו.
- **דשבורד** (`/dashboard/`): שיחות בתצוגת צ'אט, מענה ידני, השתקת נחמה מול מנחה, ייבוא מנחים, ייצוא CSV.
- **ניהול** (`/admin/`): משחקים, הגדרות, עריכה מלאה של מנחים.
- **מסמך ההקשר** נקרא מ-Google Docs (משותף בקישור לצפייה), עם גיבוי במסד הנתונים.

## משתני סביבה
| משתנה | מה זה |
|---|---|
| `DJANGO_SECRET_KEY` | מפתח סודי של Django |
| `DATABASE_URL` | Postgres (ב-Railway: `${{Postgres.DATABASE_URL}}`) |
| `PUBLIC_URL` | הכתובת הציבורית, למשל `https://nechama.up.railway.app` |
| `DJANGO_ADMIN_USERNAME` / `DJANGO_ADMIN_PASSWORD` | משתמש הצוות הראשון (נוצר פעם אחת) |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | פרטי Twilio |
| `TWILIO_WHATSAPP_FROM` | מספר השולח, למשל `+15553582140` |
| `TWILIO_MESSAGING_SERVICE_SID` | לא חובה. אם מוגדר, שולחים דרך שירות ההודעות |
| `OPENAI_API_KEY` | מפתח OpenAI |
| `OPENAI_MODEL` | ברירת מחדל `gpt-5.6-terra` |

## פקודות
```bash
python manage.py migrate
python manage.py seed            # הגדרות ברירת מחדל
python manage.py ensure_admin    # משתמש צוות מהמשתנים
python manage.py setup_twilio    # מכוון את ה-webhook של השולח לשרת, ומגיש תבניות לאישור
python manage.py tick            # פניות, תפוגת שריונים, הודעות שנשארו בלי מענה
python manage.py test recruit
```

## פריסה ב-Railway
- שירות `web`: start `gunicorn config.wsgi --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120`,
  pre-deploy `python manage.py migrate && python manage.py seed && python manage.py ensure_admin && python manage.py setup_twilio`.
- שירות `cron`: אותו ריפו, start `python manage.py tick`, cron `*/15 * * * *`.
