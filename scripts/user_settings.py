import json
import os
from messages import Messages

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), 'user_settings.json')
SUPPORTED_LANGS = ['ru', 'en', 'kk']
DEFAULT_LANG = 'ru'

# Map language codes to translation target languages
LANG_TO_TRANSLATION = {
    'ru': 'ru',
    'en': 'en',
    'kk': 'kk',
}

LANG_LABELS = {
    'ru': '🇷🇺 Русский',
    'en': '🇬🇧 English',
    'kk': '🇰🇿 Қазақша',
}

LANG_CONFIRM = {
    'ru': 'Язык: Русский',
    'en': 'Language: English',
    'kk': 'Тіл: Қазақша',
}


class UserSettings:
    def __init__(self):
        self._data = {}
        self._messages_cache = {}
        self._load()

    def _load(self):
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = {}

    def _save(self):
        tmp_path = SETTINGS_PATH + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, SETTINGS_PATH)

    def get_lang(self, chat_id):
        return self._data.get(str(chat_id), {}).get('lang', DEFAULT_LANG)

    def set_lang(self, chat_id, lang):
        if lang not in SUPPORTED_LANGS:
            lang = DEFAULT_LANG
        key = str(chat_id)
        if key not in self._data:
            self._data[key] = {}
        self._data[key]['lang'] = lang
        self._save()

    def get_messages(self, chat_id):
        lang = self.get_lang(chat_id)
        if lang not in self._messages_cache:
            self._messages_cache[lang] = Messages(lang)
        return self._messages_cache[lang]

    def get_translation_lang(self, chat_id):
        lang = self.get_lang(chat_id)
        return LANG_TO_TRANSLATION.get(lang, DEFAULT_LANG)
