import json, pathlib
from typing import Dict

class I18N:
    def __init__(self, locales_dir: str = "locales"):
        self._texts: Dict[str, dict] = {}
        for code in ("uk","ru","en"):
            p = pathlib.Path(locales_dir) / f"{code}.json"
            self._texts[code] = json.loads(p.read_text(encoding="utf-8"))

    def t(self, lang: str, code: str, **kwargs) -> str:
        lang = lang if lang in self._texts else "en"
        s = self._texts[lang].get(code, code)
        if kwargs:
            try:
                s = s.format(**kwargs)
            except Exception:
                pass
        return s

i18n = I18N()
