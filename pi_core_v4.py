#!/usr/bin/env python3
"""PI v4 Core — offline Regel-Engine + optionale LLM-Anbindung.
Stufen: 1) Regeln (immer offline)  2) Groq (nur wenn Key in pi_config.json)."""
import os, re, json, time, sqlite3, urllib.request, datetime

PI_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PI_DIR, "pi_kortex.db")
CONFIG_PATH = os.path.join(PI_DIR, "pi_config.json")

ALLOWED_DOMAINS = [
    "generativelanguage.googleapis.com",
    "api.groq.com",
    "openrouter.ai",
]

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_LLM = "llama-3.3-70b-versatile"


def _load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _key(name):
    return os.environ.get(name, "") or _load_config().get(name, "")


class _SecurityManager:
    """Prueft jede ausgehende URL gegen ALLOWED_DOMAINS."""
    def sandbox_check(self, url):
        try:
            m = re.match(r"https?://([^/:]+)", url)
            host = m.group(1).split(":")[0] if m else ""
            return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)
        except Exception:
            return False


_security_mgr = _SecurityManager()


class PITool:
    name = "tool"
    description = ""
    requires_api = False
    api_key_env = ""
    _key = staticmethod(_key)

    def verfuegbar(self):
        return (not self.requires_api) or bool(_key(self.api_key_env))

    def fuehre_aus(self, **kwargs):
        raise NotImplementedError


class ToolKasten:
    def __init__(self):
        self.tools = {}

    def registriere(self, tool):
        self.tools[tool.name] = tool

    def liste(self):
        return [{"name": t.name, "beschreibung": t.description,
                 "verfuegbar": t.verfuegbar()} for t in self.tools.values()]


class Kortex:
    """Lokales Gedaechtnis: alles in SQLite auf dem Geraet."""
    def __init__(self):
        with sqlite3.connect(DB_PATH) as c:
            c.execute("CREATE TABLE IF NOT EXISTS kortex (ts TEXT, user_id TEXT, rolle TEXT, text TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS reminders (rid TEXT PRIMARY KEY, user_id TEXT, due TEXT, message TEXT, done INTEGER DEFAULT 0)")
            c.execute("CREATE TABLE IF NOT EXISTS facts (key TEXT PRIMARY KEY, wert TEXT)")

    def merke(self, user_id, rolle, text):
        with sqlite3.connect(DB_PATH) as c:
            c.execute("INSERT INTO kortex VALUES (?,?,?,?)",
                      (datetime.datetime.now().isoformat(), user_id, rolle, text[:2000]))

    def verlauf(self, user_id, n=8):
        with sqlite3.connect(DB_PATH) as c:
            r = c.execute("SELECT rolle, text FROM kortex WHERE user_id=? ORDER BY ts DESC LIMIT ?",
                          (user_id, n)).fetchall()
        return list(reversed(r))

    def lerne_fakt(self, key, wert):
        with sqlite3.connect(DB_PATH) as c:
            c.execute("INSERT OR REPLACE INTO facts VALUES (?,?)", (key.lower(), wert))

    def fakt(self, key):
        with sqlite3.connect(DB_PATH) as c:
            r = c.execute("SELECT wert FROM facts WHERE key=?", (key.lower(),)).fetchone()
        return r[0] if r else None

    def faellige_reminders(self, user_id=""):
        jetzt = datetime.datetime.now().isoformat()
        with sqlite3.connect(DB_PATH) as c:
            q = "SELECT rid, message FROM reminders WHERE done=0 AND due<=?"
            args = [jetzt]
            if user_id:
                q += " AND user_id=?"
                args.append(user_id)
            rows = c.execute(q, args).fetchall()
            for rid, _ in rows:
                c.execute("UPDATE reminders SET done=1 WHERE rid=?", (rid,))
        return [m for _, m in rows]


class ProgrammierteIntelligenz:
    def __init__(self):
        self.kortex = Kortex()
        self.tools = ToolKasten()

    # ---------- Groq (optional, nur mit Key) ----------
    def _groq_antwort(self, frage, verlauf):
        gk = _key("GROQ_API_KEY")
        if not gk:
            return None, None
        try:
            url = GROQ_ENDPOINT
            if not _security_mgr.sandbox_check(url):
                raise PermissionError("Sandbox blockiert Groq.")
            msgs = [{"role": r, "content": t} for r, t in verlauf] + [{"role": "user", "content": frage}]
            payload = {"model": GROQ_LLM, "messages": msgs, "temperature": 0.7, "max_tokens": 500}
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": "Bearer " + gk}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            return data["choices"][0]["message"]["content"].strip(), "groq/" + GROQ_LLM
        except Exception as e:
            print("[PI] Groq-Fehler:", e)
            return None, None

    # ---------- 0-Logik-Regel-Engine (immer offline) ----------
    def _regel_antwort(self, user_id, text):
        low = re.sub(r"\s+", " ", text.lower()).strip()

        # Fakten lernen: "merk dir mein X ist Y"
        m = re.match(r".*(merke? dir(?:,)? (?:dass )?)?(?:mein|meine) ([\w äöüß-]+?) (?:ist|heißt|heisst|sind) (.+)", low)
        if m and ("merke" in low or " mein" in low or " meine" in low):
            self.kortex.lerne_fakt(m.group(2), m.group(3))
            return "Gespeichert: " + m.group(2).strip() + " = " + m.group(3).strip()

        m = re.search(r"(?:was ist|was heißt|wie ist) (?:mein|meine) ([\w äöüß-]+)", low)
        if m:
            wert = self.kortex.fakt(m.group(1).strip())
            if wert:
                return "Dein/e " + m.group(1).strip() + " ist " + wert
            return "Das habe ich noch nicht gespeichert. Sag: 'Merke dir: mein X ist Y'."

        if re.search(r"\b(uhrzeit|wie spät|wie spaet)\b", low):
            return "Es ist " + datetime.datetime.now().strftime("%H:%M") + " Uhr."
        if re.search(r"\b(datum|welcher tag|heute)\b", low):
            return "Heute ist " + datetime.datetime.now().strftime("%A, %d.%m.%Y") + "."

        # Rechnen: nur Ziffern und Grundrechenarten
        m = re.search(r"([-+*/().\d ]{3,})", text)
        if m and re.search(r"\d", m.group(1)) and re.search(r"[+\-*/]", m.group(1)):
            try:
                erg = eval(m.group(1), {"__builtins__": {}}, {})
                if isinstance(erg, (int, float)):
                    return "Ergebnis: " + str(erg)
            except Exception:
                pass

        if re.match(r"(hallo|hi|hey|moin|servus|guten (tag|morgen|abend))\b", low):
            erinn = self.kortex.faellige_reminders(user_id)
            antw = "Hallo! Ich bin PI, offline auf deinem Handy."
            if erinn:
                antw += " Erinnerung" + ("en" if len(erinn) > 1 else "") + ": " + "; ".join(erinn)
            return antw
        if re.search(r"\b(danke|dankeschön)\b", low):
            return "Gern geschehen."
        return None  # keine Regel gepasst -> Groq versuchen

    # ---------- Haupteinstieg ----------
    def verarbeite(self, user_id, text):
        text = (text or "").strip()
        if not text:
            return {"antwort": "Leere Eingabe.", "quelle": "regeln", "kohärenz": 1.0}

        # Erinnerung: "erinnere mich in 20 minuten an X"
        m = re.search(r"erinner(?:e)? mich in (\d+) ?(min|minute|minuten|stunde|stunden|h|std) an (.+)",
                      text, re.IGNORECASE)
        if m:
            zahl, einheit, nachricht = int(m.group(1)), m.group(2).lower(), m.group(3)
            minuten = zahl * (60 if einheit.startswith(("std", "h")) else 1)
            due = datetime.datetime.now() + datetime.timedelta(minutes=minuten)
            with sqlite3.connect(DB_PATH) as c:
                c.execute("INSERT INTO reminders VALUES (?,?,?,?,0)",
                          (str(int(time.time() * 1000)), user_id, due.isoformat(), nachricht))
            return {"antwort": "Erinnerung gesetzt für " + due.strftime("%H:%M") + " Uhr: " + nachricht,
                    "quelle": "regeln", "kohärenz": 1.0}

        antwort = self._regel_antwort(user_id, text)
        quelle = "regeln"
        if antwort is None:
            antwort, quelle = self._groq_antwort(text, self.kortex.verlauf(user_id, n=6))
            if not antwort:
                antwort = ("Ich bin offline ohne Key und habe '" + text[:80] +
                           "' im Kortex notiert. Frag mich nach Uhrzeit, Datum, Rechnungen, "
                           "Erinnerungen oder gespeicherten Fakten — das geht immer offline.")
                quelle = "regeln (fallback)"
        self.kortex.merke(user_id, "user", text)
        self.kortex.merke(user_id, "pi", antwort)
        return {"antwort": antwort, "quelle": quelle, "kohärenz": round(min(1.0, 0.6 + len(antwort) / 300), 2)}

    def tool_ausfuehren(self, user_id, name, **kwargs):
        t = self.tools.tools.get(name)
        if not t:
            return {"tool": name, "fehler": "Tool unbekannt."}
        if t.requires_api and not t.verfuegbar():
            return {"tool": name, "verfügbar": False, "hinweis": "Key fehlt: " + t.api_key_env}
        try:
            return t.fuehre_aus(**kwargs)
        except Exception as e:
            return {"tool": name, "fehler": str(e)}

    def tools_liste(self):
        return self.tools.liste()
