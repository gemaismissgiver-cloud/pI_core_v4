#!/usr/bin/env python3
"""PI v4 — Android-App: Chat + Vorlesen via Android-TTS."""
from kivy.app import App
from kivy.lang import Builder
from pi_core_v4 import ProgrammierteIntelligenz, PITool, DB_PATH
import sqlite3, datetime, re, uuid

GEMINI_BASE = "https://generativelanguage.googleapis.com"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1"
GROQ_ASR = "whisper-large-v3"


class TextZuSpracheTool(PITool):
    name = "text_zu_sprache"
    description = "Spricht Text aus (Android-TTS, offline und kostenlos)"
    requires_api = False

    def fuehre_aus(self, text="", stimme="de-DE", **_):
        out = {"tool": self.name, "verfügbar": True, "gesprochen": False, "anbieter": None, "fehler": None}
        if not text.strip():
            out["fehler"] = "Kein Text."
            return out
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Locale = autoclass("java.util.Locale")
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            tts = TextToSpeech(PythonActivity.mActivity, None)
            tts.setLanguage(Locale(stimme))
            tts.speak(text, TextToSpeech.QUEUE_FLUSH, None)
            out["gesprochen"] = True
            out["anbieter"] = "android-tts (offline)"
            return out
        except Exception as e:
            out["fehler"] = "Android-TTS: " + str(e)
        out["hinweis"] = "TTS nur in der fertigen App verfügbar."
        return out


class AnonymisierungTool(PITool):
    name = "anonymisieren"
    description = "Ersetzt sensible Daten (Mail, Telefon, IBAN) durch Platzhalter"
    requires_api = False

    def fuehre_aus(self, text="", **_):
        patterns = {"EMAIL": r"[\w\.-]+@[\w\.-]+\.\w+",
                    "PHONE": r"(\+?\d[\d \-/]{7,}\d)",
                    "IBAN": r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"}
        red = text
        counts = {}
        for name, pat in patterns.items():
            counts[name] = len(re.findall(pat, red))
            red = re.sub(pat, "[" + name + "]", red)
        return {"tool": self.name, "verfügbar": True, "original_len": len(text),
                "anonymisiert": red, "ersetzt": counts}


class ReminderTool(PITool):
    name = "erinnerung_setzen"
    description = "Setzt eine Erinnerung (lokal gespeichert)"
    requires_api = False

    def fuehre_aus(self, zeit="", nachricht="", user_id="", **_):
        if not nachricht.strip():
            return {"tool": self.name, "fehler": "Keine Nachricht."}
        try:
            due = datetime.datetime.fromisoformat(zeit) if zeit else datetime.datetime.now() + datetime.timedelta(hours=1)
        except Exception:
            due = datetime.datetime.now() + datetime.timedelta(hours=1)
        with sqlite3.connect(DB_PATH) as c:
            c.execute("CREATE TABLE IF NOT EXISTS reminders (rid TEXT PRIMARY KEY, user_id TEXT, due TEXT, message TEXT, done INTEGER DEFAULT 0)")
            c.execute("INSERT INTO reminders VALUES (?,?,?,?,0)", (str(uuid.uuid4()), user_id or "anon", due.isoformat(), nachricht))
        return {"tool": self.name, "verfügbar": True, "fällig": due.isoformat(), "nachricht": nachricht}


class AudioTranskriptionTool(PITool):
    name = "audio_transkribieren"
    description = "Transkribiert Audio zu Text (Groq Whisper, braucht Key)"
    requires_api = True
    api_key_env = "GROQ_API_KEY"

    def fuehre_aus(self, audio_path="", audio_data=None, sprache="de", **_):
        out = {"tool": self.name, "verfügbar": self.verfuegbar(), "transkript": None, "anbieter": None, "fehler": None}
        gk = self._key("GROQ_API_KEY")
        if gk:
            try:
                import os, json, urllib.request
                url = GROQ_ENDPOINT + "/audio/transcriptions"
                if not audio_data:
                    with open(audio_path, "rb") as f:
                        audio_data = f.read()
                boundary = "----pi" + uuid.uuid4().hex
                body = self._multipart(boundary, {"model": GROQ_ASR, "language": sprache},
                                      filename=os.path.basename(audio_path) or "audio.mp3", filedata=audio_data)
                req = urllib.request.Request(url, data=body, method="POST",
                    headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                             "Authorization": "Bearer " + gk})
                with urllib.request.urlopen(req, timeout=120) as r:
                    out["transkript"] = json.loads(r.read()).get("text")
                    out["anbieter"] = "groq/whisper-large-v3"
                return out
            except Exception as e:
                out["fehler"] = "Groq: " + str(e)
        out["hinweis"] = "GROQ_API_KEY fehlt (optional)."
        return out

    @staticmethod
    def _multipart(boundary, fields, filename, filedata):
        parts = []
        for k, v in fields.items():
            parts.append(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + k +
                           "\"\r\n\r\n" + str(v) + "\r\n").encode())
        parts.append(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" +
                      filename + "\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + filedata + b"\r\n")
        parts.append(("--" + boundary + "--\r\n").encode())
        return b"".join(parts)


class BildSehenTool(PITool):
    name = "bild_sehen"
    description = "Liest Bild-Daten lokal (Beschreibung nur mit Gemini-Key)"
    requires_api = False

    def fuehre_aus(self, image_path="", image_data=None, frage="Was ist auf diesem Bild?", **_):
        out = {"tool": self.name, "verfügbar": True, "lokal": {}, "beschreibung": None, "fehler": None}
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_data) if image_data else image_path)
            out["lokal"] = {"format": img.format, "größe": img.size, "modus": img.mode}
        except Exception as e:
            out["fehler"] = "Lokal: " + str(e)
        gmk = self._key("GEMINI_API_KEY")
        if gmk:
            try:
                import base64, json, urllib.request
                if not image_data:
                    with open(image_path, "rb") as f:
                        image_data = f.read()
                b64 = base64.b64encode(image_data).decode()
                mime = "image/png" if str(image_path).lower().endswith(".png") else "image/jpeg"
                url = GEMINI_BASE + "/v1beta/models/gemini-2.0-flash:generateContent?key=" + gmk
                payload = {"contents": [{"parts": [{"text": frage},
                           {"inline_data": {"mime_type": mime, "data": b64}}]}]}
                req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=90) as r:
                    out["beschreibung"] = json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as e:
                out["fehler"] = "Gemini Vision: " + str(e)
        else:
            out["hinweis"] = "GEMINI_API_KEY fehlt (optional) — nur lokale Analyse."
        return out


KV = """
BoxLayout:
    orientation: "vertical"
    padding: 8
    spacing: 6
    Label:
        text: "PI v4 — offline auf deinem Handy"
        size_hint_y: None
        height: 26
        font_size: 15
        bold: True
        color: 0.62, 0.31, 0.87, 1
    ScrollView:
        Label:
            id: chat
            size_hint_y: None
            height: self.texture_size[1]
            text_size: self.width - 16, None
            text: "PI: Hallo! Ich bin PI.\\nBeispiele:\\n - wie spät ist es\\n - erinnere mich in 5 minuten an Wasser\\n - merke dir: mein Name ist Gema\\n"
            font_size: 15
    BoxLayout:
        size_hint_y: None
        height: 44
        spacing: 6
        TextInput:
            id: eingabe
            multiline: False
            font_size: 15
            hint_text: "Nachricht an PI…"
        Button:
            text: "Senden"
            size_hint_x: None
            width: 90
            on_release: app.senden()
    BoxLayout:
        size_hint_y: None
        height: 36
        spacing: 6
        CheckBox:
            id: vorlesen
            active: True
        Label:
            text: "Vorlesen"
            font_size: 13
            size_hint_x: None
            width: 90
            text_size: self.size
            halign: "left"
"""


class PIApp(App):
    title = "PI v4"

    def build(self):
        self.pi = ProgrammierteIntelligenz()
        for t in (TextZuSpracheTool(), AnonymisierungTool(), ReminderTool(),
                  AudioTranskriptionTool(), BildSehenTool()):
            self.pi.tools.registriere(t)
        return Builder.load_string(KV)

    def _zeige(self, text):
        self.root.ids.chat.text += text + "\n"

    def senden(self):
        e = self.root.ids.eingabe
        text = e.text.strip()
        if not text:
            return
        e.text = ""
        self._zeige("Du: " + text)
        erg = self.pi.verarbeite("gema", text)
        antw = erg.get("antwort", "")
        quelle = erg.get("quelle", "?")
        self._zeige("PI [" + quelle + "]: " + antw)
        if self.root.ids.vorlesen.active and antw:
            erg2 = self.pi.tool_ausfuehren("gema", "text_zu_sprache", text=antw, stimme="de-DE")
            if not erg2.get("gesprochen"):
                self._zeige("  (Vorlesen hier nicht möglich — in der App ja.)")


if __name__ == "__main__":
    PIApp().run()
