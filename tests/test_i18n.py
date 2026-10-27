"""Langues de l'interface : traductions complètes, langue de chaque compte, langue par défaut du serveur.

python3 -m unittest tests.test_i18n
"""
import ast
import glob
import json
import os
import re
import shutil
import tempfile
import threading
import unittest

from tests.test_api import STATIC, Client

from seance import i18n
from seance.api import Ctx, build_app
from seance.lang_en import EN
from seance.web import Server, make_handler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_STRING = re.compile(r"'((?:[^'\\\n]|\\.)*)'")
PLACEHOLDER = re.compile(r"\{\w*\}")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def js_unescape(s):
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def call_args(src, start):
    """Arguments (texte) de l'appel dont la parenthèse ouvrante est à start, séparés au premier niveau."""
    depth, quote, args, cur, i = 0, None, [], "", start + 1
    while i < len(src):
        c = src[i]
        if quote:
            cur += c
            if c == "\\":
                cur += src[i + 1]
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
            cur += c
        elif c in "([{":
            depth += 1
            cur += c
        elif c in ")]}":
            if depth == 0:
                args.append(cur)
                return args
            depth -= 1
            cur += c
        elif c == "," and depth == 0:
            args.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    return args


def web_keys():
    """Textes passés à tr() et plural() dans le code de l'interface."""
    keys = {}
    for path in glob.glob(os.path.join(STATIC, "js", "**", "*.js"), recursive=True):
        if "/i18n" in path.replace(os.sep, "/"):
            continue
        src = read(path)
        for m in re.finditer(r"(?<![\w.$])(tr|plural)\(", src):
            args = call_args(src, m.end() - 1)
            texts = args[:1] if m.group(1) == "tr" else args[1:3]
            for a in texts:
                for lit in JS_STRING.finditer(a):
                    if re.search(r"[!=]==?\s*$", a[:lit.start()]):
                        continue  # valeur comparée dans une condition (tr(x === 'a' ? '…' : '…'))
                    keys.setdefault(js_unescape(lit.group(1)), os.path.relpath(path, ROOT))
    return keys


def web_dict(lang):
    src = read(os.path.join(STATIC, "js", "i18n", f"{lang}.js"))
    body = src[src.index("export default {") + len("export default "):src.rindex("}") + 1]
    return json.loads(re.sub(r",\s*}$", "}", body))


class TranslationsTest(unittest.TestCase):
    def test_web_dictionary_is_complete(self):
        en = web_dict("en")
        keys = web_keys()
        self.assertGreater(len(keys), 1000)
        missing = sorted(k for k in keys if k not in en)
        self.assertEqual(missing, [], "textes de l'interface sans traduction anglaise")
        for k, v in en.items():
            self.assertEqual(sorted(PLACEHOLDER.findall(k)), sorted(PLACEHOLDER.findall(v)), k)
            self.assertTrue(v.strip(), k)

    def test_server_messages_are_translated(self):
        """Chaque message d'erreur écrit en dur dans le code du serveur a sa traduction anglaise."""
        missing = []
        for path in glob.glob(os.path.join(ROOT, "seance", "**", "*.py"), recursive=True):
            tree = ast.parse(read(path))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "HTTPError"
                        and len(node.args) >= 2):
                    continue
                msg = node.args[1]
                if isinstance(msg, ast.Constant) and isinstance(msg.value, str):
                    text = msg.value
                elif isinstance(msg, ast.JoinedStr):
                    text = "".join(v.value if isinstance(v, ast.Constant) else "X" for v in msg.values)
                else:
                    continue
                if i18n.tr(text, "en") == text:
                    missing.append(f"{os.path.relpath(path, ROOT)}:{node.lineno} {text}")
        self.assertEqual(missing, [], "\n".join(missing))
        for k, v in EN.items():
            self.assertEqual(k.count("{}"), v.count("{}"), k)

    def test_scheduled_tasks_are_translated(self):
        from seance.tasks import TASKS
        for task in TASKS:
            for text in (task.name, task.description, *[o["label"] for o in task.options],
                         *[o["hint"] for o in task.options if o.get("hint")]):
                self.assertNotEqual(i18n.tr(text, "en"), text, text)

    def test_templates(self):
        self.assertEqual(i18n.tr("Dossier introuvable depuis le serveur : /mnt/x", "en"), "Folder not found from the server: /mnt/x")
        self.assertEqual(i18n.tr("Préparation impossible : ffmpeg s'est arrêté", "en"), "Preparation failed: ffmpeg stopped")
        self.assertEqual(i18n.tr("Un titre de film", "en"), "Un titre de film")  # texte inconnu : inchangé
        self.assertEqual(i18n.tr("Introuvable", "fr"), "Introuvable")


class LanguageApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.server = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.admin = Client(cls.base)
        st, r = cls.admin.call("POST", "/api/setup", {"username": "admin", "password": "motdepasse1", "language": "fr"},
                               headers={"X-Setup-Code": cls.ctx.setup_code})
        assert st == 200, r

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        i18n.set_server_default("fr")
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_error_follows_interface_header(self):
        st, r = Client(self.base).call("POST", "/api/login", {"username": "x", "password": "y"})
        self.assertEqual((st, r["error"]), (401, "Identifiant ou mot de passe incorrect"))
        st, r = Client(self.base).call("POST", "/api/login", {"username": "x", "password": "y"}, headers={"X-Lang": "en"})
        self.assertEqual((st, r["error"]), (401, "Incorrect username or password"))

    def test_user_language_and_server_default(self):
        st, me = self.admin.call("GET", "/api/me")
        self.assertEqual(me["user"]["language"], "fr")
        # l'administrateur choisit l'anglais pour les nouveaux comptes
        st, s = self.admin.call("PUT", "/api/admin/settings", {"default_language": "en"})
        self.assertEqual((st, s["default_language"]), (200, "en"))
        st, status = Client(self.base).call("GET", "/api/status")
        self.assertEqual(status["language"], "en")
        st, html, _ = Client(self.base).call("GET", "/", raw=True)
        self.assertIn(b'data-default-lang="en"', html)
        st, u = self.admin.call("POST", "/api/admin/users", {"username": "alice", "password": "motdepasse1"})
        self.assertEqual((st, u["language"]), (200, "en"))
        alice = Client(self.base)
        alice.call("POST", "/api/login", {"username": "alice", "password": "motdepasse1"})
        # sans en-tête de l'interface : la langue du compte
        st, r = alice.call("PUT", "/api/me/password", {"current": "faux", "new": "motdepasse2"})
        self.assertEqual(r["error"], "Incorrect current password")
        # chacun choisit sa langue
        st, r = alice.call("PUT", "/api/me/language", {"language": "fr"})
        self.assertEqual((st, r["language"]), (200, "fr"))
        st, me = alice.call("GET", "/api/me")
        self.assertEqual(me["user"]["language"], "fr")
        st, r = alice.call("PUT", "/api/me/language", {"language": "xx"})
        self.assertEqual(st, 400)
        # un compte existant garde sa langue quand la langue par défaut change
        st, me = self.admin.call("GET", "/api/me")
        self.assertEqual(me["user"]["language"], "en")  # l'administrateur n'a pas choisi : langue par défaut
        st, r = self.admin.call("PUT", "/api/admin/settings", {"default_language": "zz"})
        self.assertEqual(st, 400)
        self.admin.call("PUT", "/api/admin/settings", {"default_language": "fr"})
        st, me = alice.call("GET", "/api/me")
        self.assertEqual(me["user"]["language"], "fr")

    def test_admin_sets_user_language(self):
        st, u = self.admin.call("POST", "/api/admin/users", {"username": "bob", "password": "motdepasse1", "language": "en"})
        self.assertEqual(u["language"], "en")
        st, u = self.admin.call("PUT", f"/api/admin/users/{u['id']}", {"language": "fr"})
        self.assertEqual((st, u.get("ok", True)), (200, True))
        st, users = self.admin.call("GET", "/api/admin/users")
        self.assertEqual(next(x for x in users if x["username"] == "bob")["language"], "fr")
        st, r = self.admin.call("PUT", f"/api/admin/users/{u.get('id', 0) or next(x for x in users if x['username'] == 'bob')['id']}",
                                {"language": "de"})
        self.assertEqual(st, 400)

    def test_tasks_in_english(self):
        st, tasks = self.admin.call("GET", "/api/admin/tasks", headers={"X-Lang": "en"})
        scan = next(t for t in tasks["tasks"] if t["id"] == "scan")
        self.assertEqual(scan["name"], "Scan libraries")
        self.assertEqual(scan["category"], "Bibliothèques")  # clé : traduite par l'interface


if __name__ == "__main__":
    unittest.main()
