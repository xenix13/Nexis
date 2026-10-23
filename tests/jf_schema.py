"""Vérifie les réponses de l'API compatible Jellyfin contre les modèles du SDK Kotlin officiel (1.8.12).

Ce SDK est celui de l'application Android TV : un champ obligatoire absent (ou nul), un GUID mal formé ou une
valeur d'énumération inconnue y font échouer la lecture de la réponse. tests/data/jellyfin_models.json est extrait
des sources générées du SDK (jellyfin-model/src/commonMain/kotlin-generated).
"""
import json
import os
import re
from datetime import datetime

MODELS = json.load(open(os.path.join(os.path.dirname(__file__), "data", "jellyfin_models.json")))
UUID = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")


def check(value, typ, path="$", errors=None):
    errors = [] if errors is None else errors
    typ = typ.rstrip("?")
    if value is None:
        return errors
    if typ.startswith("List<"):
        if not isinstance(value, list):
            errors.append(f"{path}: liste attendue ({typ})")
            return errors
        for i, v in enumerate(value):
            check(v, typ[5:-1], f"{path}[{i}]", errors)
        return errors
    if typ == "String":
        if not isinstance(value, str):
            errors.append(f"{path}: texte attendu, {value!r}")
    elif typ in ("Int", "Long"):
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: entier attendu, {value!r}")
    elif typ in ("Double", "Float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}: nombre attendu, {value!r}")
    elif typ == "Boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: booléen attendu, {value!r}")
    elif typ == "UUID":
        if not isinstance(value, str) or not UUID.match(value):
            errors.append(f"{path}: GUID attendu, {value!r}")
    elif typ == "DateTime":
        try:
            datetime.fromisoformat(re.sub(r"\.(\d{6})\d*", r".\1", value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            errors.append(f"{path}: date attendue, {value!r}")
    elif typ in MODELS:
        model = MODELS[typ]
        if "enum" in model:
            if value not in model["enum"]:
                errors.append(f"{path}: {value!r} n'est pas une valeur de {typ}")
        else:
            if not isinstance(value, dict):
                errors.append(f"{path}: objet {typ} attendu")
                return errors
            for f in model["fields"]:
                v = value.get(f["json"])
                if v is None:
                    if f["required"]:
                        errors.append(f"{path}.{f['json']}: champ obligatoire de {typ} absent")
                    continue
                check(v, f["type"], f"{path}.{f['json']}", errors)
    return errors


def assert_model(testcase, value, typ):
    errors = check(value, typ)
    testcase.assertFalse(errors, "\n".join(errors[:20]))
